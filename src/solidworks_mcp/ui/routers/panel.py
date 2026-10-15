"""Local API consumed by the embedded SolidWorks Task Pane."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..panel_runtime import get_panel_runtime

router = APIRouter()


def _require_local(request: Request) -> None:
    """Reject non-local callers; the panel bridge must never be internet-facing."""
    host = (request.client.host if request.client else "").lower()
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Panel API only accepts local callers")


class ExecuteToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    timeout_seconds: float | None = Field(default=None, gt=0, le=1800)


class StartSolidWorksRequest(BaseModel):
    """Optional override for the explicit SolidWorks startup wait."""

    timeout_seconds: float | None = Field(default=None, gt=0, le=300)


@router.get("/panel", response_class=HTMLResponse)
async def panel_page(request: Request) -> HTMLResponse:
    """Serve the self-contained panel frontend."""
    _require_local(request)
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "panel" / "index.html"
    if not path.exists():
        raise HTTPException(status_code=500, detail="Panel frontend is not installed")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.get("/api/panel/health")
async def panel_health(request: Request) -> dict[str, Any]:
    _require_local(request)
    return await get_panel_runtime().state()


@router.get("/api/panel/tools")
async def panel_tools(request: Request) -> dict[str, Any]:
    _require_local(request)
    tools = await get_panel_runtime().tools()
    categories: dict[str, int] = {}
    for item in tools:
        category = str(item["category"])
        categories[category] = categories.get(category, 0) + 1
    return {"tools": tools, "count": len(tools), "categories": categories}


@router.get("/api/panel/tools/{name}")
async def panel_tool(name: str, request: Request) -> dict[str, Any]:
    _require_local(request)
    item = await get_panel_runtime().tool(name)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Unknown MCP tool: {name}")
    return item


@router.post("/api/panel/tools/{name}/execute")
async def panel_execute(
    name: str, payload: ExecuteToolRequest, request: Request
) -> dict[str, Any]:
    _require_local(request)
    try:
        result = await get_panel_runtime().execute(
            name,
            payload.arguments,
            confirmed=payload.confirmed,
            timeout_seconds=payload.timeout_seconds,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown MCP tool: {name}") from exc
    return result


@router.get("/api/panel/state")
async def panel_state(request: Request) -> dict[str, Any]:
    _require_local(request)
    return await get_panel_runtime().state()


@router.get("/api/panel/logs")
async def panel_logs(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    _require_local(request)
    return {"logs": get_panel_runtime().logs(limit)}


@router.post("/api/panel/reconnect")
async def panel_reconnect(request: Request) -> dict[str, Any]:
    _require_local(request)
    runtime = get_panel_runtime()
    await runtime.close()
    return await runtime.state()


@router.post("/api/panel/start-solidworks")
async def panel_start_solidworks(
    payload: StartSolidWorksRequest, request: Request
) -> dict[str, Any]:
    """Launch SolidWorks only after an explicit local panel request."""
    _require_local(request)
    return await get_panel_runtime().start_solidworks(payload.timeout_seconds)


__all__ = ["router"]
