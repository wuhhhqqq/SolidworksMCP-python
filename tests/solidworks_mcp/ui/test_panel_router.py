"""Tests for the local Task Pane HTTP contract."""

from __future__ import annotations

from fastapi.testclient import TestClient

from solidworks_mcp.ui import server


class _FakeRuntime:
    async def state(self):
        return {"status": "healthy", "connected": True, "tool_count": 1}

    async def tools(self):
        return [
            {
                "name": "demo_tool",
                "title": "Demo",
                "description": "Demo",
                "category": "modeling",
                "tags": [],
                "parameters": {"type": "object"},
                "output_schema": None,
                "requires_confirmation": False,
                "timeout_seconds": None,
            }
        ]

    async def tool(self, name):
        return (await self.tools())[0] if name == "demo_tool" else None

    async def execute(self, name, arguments, **kwargs):
        return {"ok": True, "tool_name": name, "result": arguments}

    def logs(self, limit=50):
        return []

    async def close(self):
        return None

    async def start_solidworks(self, timeout_seconds=None):
        return {
            "ok": True,
            "connected": True,
            "result": {"started": True, "timeout_seconds": timeout_seconds},
        }


def test_panel_routes_expose_catalog_and_execute(monkeypatch) -> None:
    fake = _FakeRuntime()
    monkeypatch.setattr(server.panel_router, "get_panel_runtime", lambda: fake)
    client = TestClient(server.app)

    page = client.get("/panel")
    catalog = client.get("/api/panel/tools")
    item = client.get("/api/panel/tools/demo_tool")
    executed = client.post(
        "/api/panel/tools/demo_tool/execute", json={"arguments": {"x": 2}}
    )

    assert page.status_code == 200
    assert "SolidWorks MCP" in page.text
    assert catalog.json()["count"] == 1
    assert item.json()["name"] == "demo_tool"
    assert executed.json()["ok"] is True


def test_panel_route_returns_404_for_unknown_tool(monkeypatch) -> None:
    fake = _FakeRuntime()
    monkeypatch.setattr(server.panel_router, "get_panel_runtime", lambda: fake)
    response = TestClient(server.app).get("/api/panel/tools/missing")

    assert response.status_code == 404


def test_panel_route_can_explicitly_start_solidworks(monkeypatch) -> None:
    fake = _FakeRuntime()
    monkeypatch.setattr(server.panel_router, "get_panel_runtime", lambda: fake)

    response = TestClient(server.app).post(
        "/api/panel/start-solidworks", json={"timeout_seconds": 45}
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["result"]["timeout_seconds"] == 45
