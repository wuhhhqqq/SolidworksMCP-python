"""Capability identity reporting must distinguish mock and runtime backends."""

from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.capabilities import register_capability_tools


@pytest.mark.asyncio
async def test_capabilities_report_mock_identity_without_claiming_validation() -> None:
    adapter = MockSolidWorksAdapter()
    await adapter.connect()
    server = FastMCP("capability-test")
    await register_capability_tools(server, adapter, SimpleNamespace(build_id="test-build"))
    try:
        async with Client(server) as client:
            result = await client.call_tool("solidworks_get_capabilities")
            assert not result.is_error
            data = result.structured_content
            assert data["backend"]["adapter_type"] == "mock"
            assert data["identity_status"] == "identity_only"
            assert data["validation_status"] == "unknown"
            assert "solidworks_get_capabilities" in data["registered_tools"]
    finally:
        await adapter.disconnect()
