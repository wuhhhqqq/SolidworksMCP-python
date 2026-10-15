"""Runtime bridge used by the SolidWorks Task Pane.

The Task Pane is intentionally a thin local HTTP client.  This module keeps one
``SolidWorksMCPServer`` instance per UI process, exposes the real FastMCP tool
registry, and serializes calls so SolidWorks COM work is never run concurrently.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


def _jsonable(value: Any) -> Any:
    """Convert MCP/COM results into JSON-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (Path, datetime, date)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _category_for_tool(name: str, module: str = "") -> str:
    """Map a tool name to the same broad categories used by the MCP catalog."""
    prefixes = (
        "drawing_analysis",
        "file_management",
        "template_management",
        "docs_discovery",
        "macro_recording",
        "vba_generation",
        "import_cad",
        "modeling",
        "sketching",
        "drawing",
        "analysis",
        "export",
        "automation",
        "mvp",
    )
    module_name = module.rsplit(".", 1)[-1]
    if module_name in prefixes:
        return module_name
    for prefix in prefixes:
        if name.startswith(prefix + "_"):
            return prefix
    # Legacy tools are named by operation rather than module prefix.
    legacy = {
        "open_model": "file_management",
        "close_model": "file_management",
        "list_open_models": "file_management",
        "get_selection_state": "modeling",
        "get_dimension": "modeling",
        "set_dimension": "modeling",
        "create_sketch": "sketching",
        "add_line": "sketching",
        "add_circle": "sketching",
        "add_rectangle": "sketching",
        "add_arc": "sketching",
        "add_spline": "sketching",
    }
    return legacy.get(name, "general")


def _requires_confirmation(name: str, tool: Any) -> bool:
    """Identify operations that can delete, overwrite, or run code."""
    tags = {str(tag).lower() for tag in (getattr(tool, "tags", None) or set())}
    if tags.intersection({"dangerous", "destructive", "requires_confirmation"}):
        return True
    destructive_tokens = (
        "delete",
        "remove",
        "close",
        "cut",
        "overwrite",
        "save_as",
        "execute_macro",
        "run_macro",
        "quit",
        "exit",
    )
    lowered = name.lower()
    return any(token in lowered for token in destructive_tokens)


def serialize_tool(tool: Any) -> dict[str, Any]:
    """Return the public, schema-driven representation used by the panel."""
    return {
        "name": str(tool.name),
        "title": getattr(tool, "title", None) or str(tool.name),
        "description": getattr(tool, "description", None) or "",
        "category": _category_for_tool(
            str(tool.name), getattr(getattr(tool, "fn", None), "__module__", "")
        ),
        "tags": sorted(str(tag) for tag in (getattr(tool, "tags", None) or set())),
        "parameters": _jsonable(getattr(tool, "parameters", None) or {"type": "object"}),
        "output_schema": _jsonable(getattr(tool, "output_schema", None)),
        "requires_confirmation": _requires_confirmation(str(tool.name), tool),
        "timeout_seconds": getattr(tool, "timeout", None),
    }


class PanelRuntime:
    """Own the MCP server instance used by one embedded panel process."""

    def __init__(self) -> None:
        self.server: Any | None = None
        self._owns_server = False
        self._setup_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._logs: deque[dict[str, Any]] = deque(maxlen=200)
        self._setup_error: str | None = None

    def attach_server(self, server: Any) -> None:
        """Attach an already-running MCP server when both services share a process."""
        if self.server is None:
            self.server = server
            self._owns_server = False

    async def ensure_ready(self, *, attach: bool = True) -> Any:
        """Create/register/connect the MCP server exactly once."""
        if self.server is not None and getattr(self.server, "_setup_complete", False):
            if attach:
                await self._connect_adapter_if_needed(self.server)
            return self.server
        async with self._setup_lock:
            if self.server is not None and getattr(self.server, "_setup_complete", False):
                if attach:
                    await self._connect_adapter_if_needed(self.server)
                return self.server
            try:
                from ..config import load_config
                from ..server import SolidWorksMCPServer

                owns_server = self.server is None
                server = self.server or SolidWorksMCPServer(load_config())
                await server.setup()
                if attach:
                    await self._connect_adapter_if_needed(server)
                self.server = server
                self._owns_server = owns_server
                return server
            except Exception as exc:
                self._setup_error = str(exc)
                logger.exception("[ui.panel] MCP runtime setup failed")
                raise

    async def _connect_adapter_if_needed(self, server: Any) -> None:
        """Connect to a SolidWorks instance when it becomes available later."""
        adapter = getattr(server, "adapter", None)
        if adapter is None or server.state.is_connected:
            return
        # Attach is also COM work. Serialize it with tool execution so two
        # simultaneous panel requests cannot create competing COM sessions.
        async with self._operation_lock:
            if server.state.is_connected:
                return
            try:
                await adapter.connect()
                server.state.is_connected = True
                self._setup_error = None
            except Exception as exc:
                # Keep the registry available so the panel can show a useful
                # connection error and retry after SolidWorks is started.
                self._setup_error = str(exc)
                logger.warning("[ui.panel] SolidWorks connection failed: {}", exc)

    async def close(self) -> None:
        """Disconnect the adapter without closing SolidWorks itself or user documents."""
        if self.server is not None and self._owns_server:
            await self.server.stop()
        self.server = None
        self._owns_server = False

    async def tools(self) -> list[dict[str, Any]]:
        server = await self.ensure_ready()
        registered = await server.mcp.list_tools()
        return [serialize_tool(tool) for tool in sorted(registered, key=lambda item: item.name)]

    async def tool(self, name: str) -> dict[str, Any] | None:
        server = await self.ensure_ready()
        registered = server.mcp.get_tool(name)
        if asyncio.iscoroutine(registered):
            registered = await registered
        return serialize_tool(registered) if registered is not None else None

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmed: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        server = await self.ensure_ready()
        definition = server.mcp.get_tool(name)
        if asyncio.iscoroutine(definition):
            definition = await definition
        if definition is None:
            raise KeyError(name)
        public_definition = serialize_tool(definition)
        if public_definition["requires_confirmation"] and not confirmed:
            return {
                "ok": False,
                "requires_confirmation": True,
                "tool_name": name,
                "error": "This operation requires explicit confirmation.",
            }

        started = time.perf_counter()
        record: dict[str, Any] = {
            "tool_name": name,
            "arguments": _jsonable(arguments),
            "started_at": datetime.now().isoformat(timespec="milliseconds"),
        }
        async with self._operation_lock:
            try:
                timeout = timeout_seconds or getattr(definition, "timeout", None) or 300.0
                result = await asyncio.wait_for(
                    server.mcp.call_tool(name, arguments), timeout=float(timeout)
                )
                structured = _jsonable(getattr(result, "structured_content", None))
                if structured is None:
                    for block in getattr(result, "content", None) or []:
                        if getattr(block, "type", None) != "text":
                            continue
                        try:
                            decoded = json.loads(block.text)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(decoded, dict):
                            structured = decoded
                            break
                is_error = bool(getattr(result, "is_error", False))
                # FastMCP transport success is not the same as operation success.
                # Legacy tools return a status field, while typed tools use an
                # OperationResult whose serialized status is ``succeeded`` or
                # ``failed``. Preserve both signals for panel clients.
                operation_status = (
                    structured.get("status")
                    if isinstance(structured, dict)
                    else None
                )
                incomplete_statuses = {
                    "error", "failed", "failure", "unsupported",
                    "partial", "needs_confirmation",
                }
                ok = not is_error and operation_status not in incomplete_statuses
                payload = {
                    "ok": ok,
                    "tool_name": name,
                    "status": operation_status,
                    "result": structured,
                    "content": _jsonable(getattr(result, "content", None)),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
                if not ok:
                    detail = structured.get("error") if isinstance(structured, dict) else None
                    if isinstance(detail, dict):
                        detail = detail.get("message")
                    payload["error"] = detail or (
                        structured.get("message") if isinstance(structured, dict) else None
                    ) or "MCP operation did not complete successfully"
                    payload["requires_confirmation"] = operation_status == "needs_confirmation"
                record.update({"ok": ok, "elapsed_ms": payload["elapsed_ms"]})
                if not ok:
                    record["error"] = payload["error"]
                return payload
            except Exception as exc:
                payload = {
                    "ok": False,
                    "tool_name": name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
                record.update({"ok": False, "error": str(exc), "elapsed_ms": payload["elapsed_ms"]})
                return payload
            finally:
                self._logs.appendleft(record)

    async def start_solidworks(self, timeout_seconds: float | None = None) -> dict[str, Any]:
        """Explicitly start SolidWorks through the configured adapter.

        Normal panel loading only attempts an attach.  This method is the
        separate user-triggered action that may launch the installed desktop
        application and then waits for its COM server.
        """
        # Starting is a recovery path. Do not attempt the normal attach first:
        # an unavailable application is expected here and must not consume a
        # circuit-breaker failure before the explicit launch is attempted.
        server = await self.ensure_ready(attach=False)
        adapter = getattr(server, "adapter", None)
        launcher = getattr(adapter, "start_solidworks", None)
        if not callable(launcher):
            return {
                "ok": False,
                "started": False,
                "connected": bool(server.state.is_connected),
                "error": "The configured SolidWorks adapter does not support explicit launch.",
            }

        async with self._operation_lock:
            started = time.perf_counter()
            try:
                kwargs = {}
                if timeout_seconds is not None:
                    kwargs["timeout_seconds"] = timeout_seconds
                result = await launcher(**kwargs)
                server.state.is_connected = True
                self._setup_error = None
                payload = {
                    "ok": True,
                    "connected": True,
                    "result": _jsonable(result),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
                self._logs.appendleft(
                    {
                        "tool_name": "start_solidworks",
                        "arguments": _jsonable(kwargs),
                        "started_at": datetime.now().isoformat(timespec="milliseconds"),
                        "ok": True,
                        "elapsed_ms": payload["elapsed_ms"],
                    }
                )
                return payload
            except Exception as exc:
                payload = {
                    "ok": False,
                    "connected": bool(server.state.is_connected),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
                self._logs.appendleft(
                    {
                        "tool_name": "start_solidworks",
                        "arguments": _jsonable(timeout_seconds),
                        "started_at": datetime.now().isoformat(timespec="milliseconds"),
                        "ok": False,
                        "error": str(exc),
                        "elapsed_ms": payload["elapsed_ms"],
                    }
                )
                return payload

    async def state(self) -> dict[str, Any]:
        server = await self.ensure_ready()
        async with self._operation_lock:
            try:
                health = await server.health_check()
                adapter_health = _jsonable(health.get("adapter"))
                if isinstance(adapter_health, dict):
                    if "connection_status" in adapter_health:
                        connection_status = adapter_health["connection_status"]
                        if connection_status == "connected":
                            server.state.is_connected = True
                        elif connection_status == "pooled":
                            server.state.is_connected = bool(adapter_health.get("healthy"))
                        elif connection_status in {"disconnected", "pool_not_initialized"}:
                            server.state.is_connected = False
                    elif "connected" in adapter_health:
                        server.state.is_connected = bool(adapter_health["connected"])
            except Exception as exc:
                server.state.is_connected = False
                self._setup_error = str(exc)
                health = {"status": "warning", "adapter": None}
        from ..version import __version__

        return {
            "status": "healthy" if server.state.is_connected else "warning",
            "version": __version__,
            "connected": bool(server.state.is_connected),
            "tool_count": int(server.state.tool_count),
            "startup_time": server.state.startup_time,
            "setup_error": self._setup_error,
            "config": {
                "adapter_type": str(server.config.adapter_type),
                "solidworks_year": server.config.solidworks_year,
                "launch_supported": callable(
                    getattr(getattr(server, "adapter", None), "start_solidworks", None)
                ),
            },
            "adapter": _jsonable(health.get("adapter")),
        }

    def logs(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._logs)[: max(1, min(limit, 200))]


_RUNTIME = PanelRuntime()


def get_panel_runtime() -> PanelRuntime:
    """Return the process-wide panel runtime."""
    return _RUNTIME


def attach_panel_server(server: Any) -> None:
    """Allow ``SolidWorksMCPServer`` to share its registry with the UI app."""
    _RUNTIME.attach_server(server)


def detach_panel_server(server: Any) -> None:
    """Detach a server that has just stopped, without touching a newer runtime."""
    if _RUNTIME.server is server:
        _RUNTIME.server = None


__all__ = [
    "PanelRuntime",
    "attach_panel_server",
    "detach_panel_server",
    "get_panel_runtime",
    "serialize_tool",
]
