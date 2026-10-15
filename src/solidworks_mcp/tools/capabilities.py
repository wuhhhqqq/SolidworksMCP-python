"""Read-only capability and backend identity reporting for integration clients."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..version import __version__


def _unwrap_adapter(adapter: Any) -> Any:
    """Unwrap known safety wrappers without treating wrapper type as backend identity."""
    current = adapter
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "adapter", None)
        if nested is None or nested is current:
            break
        current = nested
    return current


async def register_capability_tools(mcp: FastMCP, adapter: Any, config: Any) -> int:
    """Register the integration identity endpoint and return the number added."""
    before = len(await mcp.list_tools())

    @mcp.tool(
        name="solidworks_get_capabilities",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_capabilities() -> dict[str, Any]:
        """Report registered tools and runtime backend identity for safe routing."""
        health = await adapter.health_check()
        metrics = dict(getattr(health, "metrics", None) or {})
        raw = _unwrap_adapter(adapter)
        adapter_type = metrics.get("adapter_type") or raw.__class__.__name__
        tool_names = sorted(
            tool.name for tool in await mcp.list_tools() if tool.name.startswith("solidworks_")
        )
        return {
            "server": {"version": __version__, "build_id": getattr(config, "build_id", None)},
            "backend": {
                "adapter_type": adapter_type,
                "connected": health.connection_status == "connected",
                "healthy": bool(health.healthy),
                "solidworks_version": metrics.get("sw_version"),
                "metrics": metrics,
            },
            "registered_tools": tool_names,
            "identity_status": "runtime_reported" if adapter_type == "pywin32" and metrics.get("sw_version") else "identity_only",
            "validation_status": "unknown",
        }

    return len(await mcp.list_tools()) - before
