"""Exercise MCP serialization and artifacts with an explicitly mock CAD backend."""

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.mvp import register_mvp_tools


@pytest.mark.asyncio
async def test_protocol_modeling_and_artifact_workflow(tmp_path: Path) -> None:
    """Validate protocol inputs, business failures, and persistent output metadata."""
    adapter = MockSolidWorksAdapter()
    await adapter.connect()
    server = FastMCP("mock-cad-protocol-test")
    await register_mvp_tools(server, adapter, SimpleNamespace(data_dir=tmp_path))
    try:
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert len(tools) == 134
            assert tools["solidworks_add_rectangle"].inputSchema

            async def call(name: str, payload: dict | None = None) -> dict:
                arguments = {} if payload is None else {"input_data": payload}
                result = await client.call_tool(name, arguments)
                assert not result.is_error
                assert result.structured_content is not None
                return result.structured_content

            session = await call("solidworks_create_session")
            assert session["status"] == "succeeded"
            scope = {"session_id": session["data"]["session"]["session_id"]}
            part = await call("solidworks_create_part", {**scope, "name": "ProtocolPart"})
            assert part["status"] == "succeeded"
            scope["document_id"] = part["data"]["document"]["document_id"]
            sketch = await call("solidworks_create_sketch", {**scope, "plane": "Front"})
            assert sketch["status"] == "succeeded"
            sketch_scope = {**scope, "sketch_id": sketch["data"]["sketch"]["persistent_id"]}
            rectangle = await call("solidworks_add_rectangle", {
                **sketch_scope, "x1": 0, "y1": 0, "x2": 40, "y2": 20,
            })
            assert rectangle["status"] == "succeeded"
            premature = await call("solidworks_create_extrusion", {**sketch_scope, "depth": 10})
            assert premature["status"] == "failed"
            assert premature["error"]["code"]
            assert (await call("solidworks_exit_sketch", sketch_scope))["status"] == "succeeded"
            extrusion = await call("solidworks_create_extrusion", {**sketch_scope, "depth": 10})
            assert extrusion["status"] == "succeeded"

            for name, extra in (
                ("solidworks_save_model", {"artifact_name": "ProtocolPart"}),
                ("solidworks_export_file", {"artifact_name": "ProtocolPart", "format_type": "stl"}),
            ):
                output = await call(name, {**scope, **extra})
                assert output["status"] == "succeeded", str(output.get("error"))
                assert len(output["artifacts"]) == 1
                artifact = output["artifacts"][0]
                path = Path(artifact["path"])
                assert path.is_relative_to(tmp_path)
                content = path.read_bytes()
                assert len(content) == artifact["size_bytes"] > 0
                assert sha256(content).hexdigest() == artifact["sha256"]
            missing_export = await call("solidworks_export_file", {
                **scope, "artifact_name": "MissingMockExport", "format_type": "step",
            })
            assert missing_export["status"] == "failed"
            assert missing_export["error"]["code"] == "SW_EXPORT_ARTIFACT_MISSING"
            closed = await call("solidworks_close_session", {"session_id": scope["session_id"]})
            assert closed["status"] == "succeeded"
            assert path.is_file()
    finally:
        await adapter.disconnect()
