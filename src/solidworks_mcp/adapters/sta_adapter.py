"""Keep native adapter lifetimes and calls on a single COM apartment."""

from __future__ import annotations

import asyncio
import inspect
from functools import wraps
from typing import Any

from .com_executor import ComExecutor


class StaAdapter:
    """Dispatch async native methods off the server event loop.

    Cancellation drains the running native call before releasing workflow locks.
    A timeout is therefore a soft deadline: it never authorizes overlapping COM
    work or silently cancels a mutation already submitted to SolidWorks.
    """

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self._executor = ComExecutor()
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        if name in {"swApp", "currentModel", "currentSketch", "currentSketchManager"}:
            raise AttributeError("Native COM objects are private to the STA worker")
        method = getattr(self.adapter, name)
        if not inspect.iscoroutinefunction(method):
            return method

        @wraps(method)
        async def invoke(*args: Any, **kwargs: Any) -> Any:
            return await self._invoke_callback(
                lambda: method(*args, **kwargs), stop=name == "disconnect"
            )

        return invoke

    async def run_native(self, callback: Any) -> Any:
        """Run a legacy compound operation without exposing COM to its caller."""
        async def call() -> Any:
            return callback(self.adapter)

        # Reuse the same scheduling/cancellation rules as native async methods.
        return await self._invoke_callback(call)

    async def _invoke_callback(self, callback: Any, *, stop: bool = False) -> Any:
        async with self._lock:
            self._executor.start()
            future = asyncio.wrap_future(self._executor.submit(lambda: asyncio.run(callback())))
            cancelled = False
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            try:
                if cancelled:
                    # Observe any exception before propagating cancellation.
                    if not future.cancelled():
                        future.exception()
                    raise asyncio.CancelledError
                return future.result()
            finally:
                if stop:
                    # The callback has drained, so shutdown has no native work to wait on.
                    self._executor.stop()
