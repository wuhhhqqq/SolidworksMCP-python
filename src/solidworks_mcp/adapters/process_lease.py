"""Hold one CAD automation owner process for the current desktop user.

The lock lasts for connected adapter lifetimes, rather than individual COM
calls: sketch edit mode and active-document selection span multiple calls.
OS file locks disappear after a crash; the marker file is intentionally kept.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import BinaryIO

from ..exceptions import SolidWorksMCPError


class ProcessLease:
    """Reference-count an OS lock across adapters in one Python process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._owners: set[object] = set()
        self._file: BinaryIO | None = None

    def acquire(self, owner: object) -> None:
        with self._lock:
            if owner in self._owners:
                return
            if self._file is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.open("a+b")
                try:
                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    handle.close()
                    raise SolidWorksMCPError(
                        "SW_AUTOMATION_PROCESS_BUSY: another MCP process owns "
                        "the SolidWorks automation session. Use its existing "
                        "panel/API, or disconnect it before starting another "
                        "stdio/panel process. No SolidWorks instance was launched."
                    ) from exc
                self._file = handle
            self._owners.add(owner)

    def release(self, owner: object) -> None:
        with self._lock:
            self._owners.discard(owner)
            if not self._owners and self._file is not None:
                # Closing releases the OS lock, including on Windows. Never
                # remove the marker: unlinking would allow competing inodes.
                self._file.close()
                self._file = None


AUTOMATION_PROCESS_LEASE = ProcessLease(
    Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    / "SolidWorksMCP" / "runtime" / "automation.lock"
)
