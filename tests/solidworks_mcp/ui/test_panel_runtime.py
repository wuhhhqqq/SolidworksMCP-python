"""Contract tests for the embedded Task Pane bridge."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from solidworks_mcp.ui.panel_runtime import PanelRuntime, serialize_tool


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "failed", "unsupported", "partial", "needs_confirmation", "succeeded"])
async def test_panel_evaluates_actual_fastmcp_business_result(status) -> None:
    mcp = FastMCP("panel-status-test")

    @mcp.tool()
    async def operation() -> dict:
        return {"status": status, "message": "profile result"}

    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True,
        mcp=mcp,
        state=SimpleNamespace(is_connected=True),
    )
    result = await runtime.execute("operation", {})
    assert result["ok"] is (status == "succeeded")
    assert result["status"] == status
    assert runtime.logs()[0]["ok"] is result["ok"]


@pytest.mark.asyncio
async def test_panel_reports_actual_fastmcp_exception() -> None:
    mcp = FastMCP("panel-error-test")

    @mcp.tool()
    async def operation() -> dict:
        raise ValueError("invalid profile")

    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True, mcp=mcp,
        state=SimpleNamespace(is_connected=True),
    )
    result = await runtime.execute("operation", {})
    assert result["ok"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_panel_health_clears_stale_connection_then_retries_attach() -> None:
    from unittest.mock import AsyncMock

    adapter = SimpleNamespace(connect=AsyncMock())
    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True, adapter=adapter,
        state=SimpleNamespace(is_connected=True, tool_count=0, startup_time=None),
        config=SimpleNamespace(adapter_type="pywin32", solidworks_year=2019),
        health_check=AsyncMock(return_value={
            "status": "healthy", "adapter": {"connection_status": "disconnected"},
        }),
    )
    health = await runtime.state()
    assert health["connected"] is False
    assert health["status"] == "warning"
    await runtime.ensure_ready()
    adapter.connect.assert_awaited_once()
    assert runtime.server.state.is_connected is True


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_status", ["pooled", "circuit_breaker_open"])
async def test_panel_health_does_not_reconnect_healthy_pool_or_open_breaker(connection_status) -> None:
    from unittest.mock import AsyncMock

    adapter = SimpleNamespace(connect=AsyncMock())
    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True, adapter=adapter,
        state=SimpleNamespace(is_connected=True, tool_count=0, startup_time=None),
        config=SimpleNamespace(adapter_type="pywin32", solidworks_year=2019),
        health_check=AsyncMock(return_value={
            "adapter": {"connection_status": connection_status, "healthy": True},
        }),
    )
    assert (await runtime.state())["connected"] is True
    await runtime.ensure_ready()
    adapter.connect.assert_not_awaited()


def _tool(name: str = "demo_tool") -> SimpleNamespace:
    async def fn(value: int = 1) -> dict[str, int]:
        return {"value": value}

    fn.__module__ = "solidworks_mcp.tools.modeling"
    return SimpleNamespace(
        name=name,
        title=None,
        description="Demo operation",
        tags=set(),
        parameters={
            "type": "object",
            "properties": {"value": {"type": "integer", "default": 1}},
            "required": [],
        },
        output_schema={"type": "object"},
        timeout=None,
        fn=fn,
    )


def test_serialize_tool_is_public_and_schema_driven() -> None:
    result = serialize_tool(_tool())

    assert result["name"] == "demo_tool"
    assert result["category"] == "modeling"
    assert result["parameters"]["properties"]["value"]["type"] == "integer"
    assert "fn" not in result


@pytest.mark.asyncio
async def test_panel_runtime_serializes_tools_and_logs_calls() -> None:
    tool = _tool()

    class FakeMcp:
        async def list_tools(self):
            return [tool]

        def get_tool(self, name):
            return tool if name == tool.name else None

        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                structured_content={"value": arguments["value"]}, content=[]
            )

    fake_server = SimpleNamespace(
        _setup_complete=True,
        mcp=FakeMcp(),
        state=SimpleNamespace(is_connected=True, tool_count=1, startup_time="now"),
        config=SimpleNamespace(adapter_type="mock", solidworks_year=2019),
        health_check=lambda: {"status": "healthy", "adapter": None},
    )
    runtime = PanelRuntime()
    runtime.server = fake_server

    catalog = await runtime.tools()
    result = await runtime.execute("demo_tool", {"value": 7})

    assert catalog[0]["name"] == "demo_tool"
    assert result["ok"] is True
    assert result["result"] == {"value": 7}
    assert runtime.logs()[0]["tool_name"] == "demo_tool"


@pytest.mark.asyncio
async def test_panel_runtime_requires_confirmation_for_destructive_tool() -> None:
    tool = _tool("delete_feature")

    class FakeMcp:
        async def list_tools(self):
            return [tool]

        def get_tool(self, name):
            return tool

        async def call_tool(self, name, arguments):
            raise AssertionError("must not execute before confirmation")

    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True,
        mcp=FakeMcp(),
        state=SimpleNamespace(is_connected=True, tool_count=1, startup_time="now"),
        config=SimpleNamespace(adapter_type="mock", solidworks_year=None),
    )

    result = await runtime.execute("delete_feature", {})

    assert result["ok"] is False
    assert result["requires_confirmation"] is True


@pytest.mark.asyncio
async def test_panel_runtime_marks_business_failure_as_not_ok() -> None:
    tool = _tool("failed_operation")

    class FakeMcp:
        def get_tool(self, name):
            return tool

        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                structured_content={"status": "failed", "message": "bad profile"},
                content=[],
                is_error=False,
            )

    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True,
        mcp=FakeMcp(),
        state=SimpleNamespace(is_connected=True, tool_count=1, startup_time="now"),
        config=SimpleNamespace(adapter_type="mock", solidworks_year=None),
    )

    result = await runtime.execute("failed_operation", {})

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "bad profile"


@pytest.mark.asyncio
async def test_panel_runtime_retries_a_later_solidworks_connection() -> None:
    connected = False

    class Adapter:
        async def connect(self):
            nonlocal connected
            connected = True

    class FakeMcp:
        async def list_tools(self):
            return []

    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True,
        adapter=Adapter(),
        mcp=FakeMcp(),
        state=SimpleNamespace(is_connected=False, tool_count=0, startup_time="now"),
        config=SimpleNamespace(adapter_type="mock", solidworks_year=None),
    )

    assert await runtime.tools() == []
    assert connected is True
    assert runtime.server.state.is_connected is True


@pytest.mark.asyncio
async def test_panel_runtime_explicit_start_updates_connection_state() -> None:
    started = []

    class Adapter:
        async def start_solidworks(self, timeout_seconds=None):
            started.append(timeout_seconds)
            return {"started": True, "pid": 1234}

    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True,
        adapter=Adapter(),
        mcp=SimpleNamespace(),
        state=SimpleNamespace(is_connected=False, tool_count=0, startup_time="now"),
        config=SimpleNamespace(adapter_type="pywin32", solidworks_year=2019),
    )

    result = await runtime.start_solidworks(30)

    assert result["ok"] is True
    assert result["connected"] is True
    assert result["result"]["pid"] == 1234
    assert started == [30]
    assert runtime.server.state.is_connected is True


@pytest.mark.asyncio
async def test_panel_runtime_reports_unsupported_explicit_start() -> None:
    runtime = PanelRuntime()
    runtime.server = SimpleNamespace(
        _setup_complete=True,
        adapter=SimpleNamespace(),
        mcp=SimpleNamespace(),
        state=SimpleNamespace(is_connected=False, tool_count=0, startup_time="now"),
        config=SimpleNamespace(adapter_type="mock", solidworks_year=None),
    )

    result = await runtime.start_solidworks()

    assert result["ok"] is False
    assert "does not support" in result["error"]
