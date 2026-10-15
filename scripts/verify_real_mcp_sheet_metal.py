"""Verify a real MCP sheet-metal base-flange and DXF workflow.

Run from the repository root:

    .venv\\Scripts\\python.exe scripts\\verify_real_mcp_sheet_metal.py

The workflow creates and saves only a new temporary part below ``.generated``.
It does not open or modify user-provided models.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from solidworks_mcp.config import (
    AdapterType,
    DeploymentMode,
    SecurityLevel,
    SolidWorksMCPConfig,
)
from solidworks_mcp.contracts.mvp import (
    AddRectangleRequest,
    CloseDocumentRequest,
    CreatePartRequest,
    CreateSheetMetalBaseFlangeRequest,
    CreateSketchRequest,
    DocumentRequest,
    ExitSketchRequest,
    ExportFlatPatternDxfRequest,
    SaveModelRequest,
    SessionRequest,
    SetSheetMetalKFactorRequest,
    SheetMetalFlatStateRequest,
)
from solidworks_mcp.server import SolidWorksMCPServer


class VerificationError(RuntimeError):
    """Raised when one required MCP operation does not succeed."""


def _require(result: object, operation: str) -> object:
    """Require one succeeded typed MCP operation result."""
    if getattr(getattr(result, "status", None), "value", None) != "succeeded":
        raise VerificationError(
            f"{operation} failed: {getattr(result, 'error', None) or result!r}"
        )
    return result


async def _tools(server: SolidWorksMCPServer) -> dict[str, object]:
    """Return MCP callables keyed by their public name."""
    return {tool.name: tool.fn for tool in await server.mcp.list_tools()}


def _tool(tools: dict[str, object], name: str):
    """Resolve one required registered MCP tool."""
    tool = tools.get(name)
    if not callable(tool):
        raise VerificationError(f"Required MCP tool is not registered: {name}")
    return tool


async def run() -> dict[str, object]:
    """Create, inspect, flatten, save, and export a temporary sheet-metal part."""
    repository_root = Path(__file__).resolve().parents[1]
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    data_dir = repository_root / ".generated" / "real_mcp_sheet_metal" / run_id
    config = SolidWorksMCPConfig(
        deployment_mode=DeploymentMode.LOCAL,
        security_level=SecurityLevel.MINIMAL,
        adapter_type=AdapterType.PYWIN32,
        mock_solidworks=False,
        testing=False,
        enable_windows_validation=False,
        enable_intelligent_routing=False,
        data_dir=data_dir,
    )
    server = SolidWorksMCPServer(config)
    session_id: str | None = None
    document_id: str | None = None
    evidence: dict[str, object] = {"run_id": run_id, "data_dir": str(data_dir)}
    try:
        await server.setup()
        if server.adapter is None:
            raise VerificationError("MCP server did not create a SolidWorks adapter")
        if not server.adapter.is_connected():
            await server.adapter.connect()
            server.state.is_connected = server.adapter.is_connected()
        if not server.state.is_connected:
            raise VerificationError("MCP adapter could not connect to SolidWorks")

        tools = await _tools(server)
        create_session = _tool(tools, "solidworks_create_session")
        create_part = _tool(tools, "solidworks_create_part")
        create_sketch = _tool(tools, "solidworks_create_sketch")
        add_rectangle = _tool(tools, "solidworks_add_rectangle")
        exit_sketch = _tool(tools, "solidworks_exit_sketch")
        create_base_flange = _tool(tools, "solidworks_create_sheet_metal_base_flange")
        get_sheet_metal_info = _tool(tools, "solidworks_get_sheet_metal_info")
        set_k_factor = _tool(tools, "solidworks_set_sheet_metal_k_factor")
        save_model = _tool(tools, "solidworks_save_model")
        set_flat_state = _tool(tools, "solidworks_set_sheet_metal_flat_state")
        export_dxf = _tool(tools, "solidworks_export_flat_pattern_dxf")

        session = _require(await create_session(), "solidworks_create_session")
        session_id = session.data["session"]["session_id"]
        part = _require(
            await create_part(
                CreatePartRequest(session_id=session_id, name="MCP_SheetMetal_E2E")
            ),
            "solidworks_create_part",
        )
        document_id = part.data["document"]["document_id"]
        sketch = _require(
            await create_sketch(
                CreateSketchRequest(
                    session_id=session_id,
                    document_id=document_id,
                    plane="Top",
                )
            ),
            "solidworks_create_sketch",
        )
        sketch_id = sketch.data["sketch"]["persistent_id"]
        _require(
            await add_rectangle(
                AddRectangleRequest(
                    session_id=session_id,
                    document_id=document_id,
                    sketch_id=sketch_id,
                    x1=0,
                    y1=0,
                    x2=50,
                    y2=30,
                )
            ),
            "solidworks_add_rectangle",
        )
        _require(
            await exit_sketch(
                ExitSketchRequest(
                    session_id=session_id,
                    document_id=document_id,
                    sketch_id=sketch_id,
                )
            ),
            "solidworks_exit_sketch",
        )
        base_flange = _require(
            await create_base_flange(
            CreateSheetMetalBaseFlangeRequest(
                session_id=session_id,
                document_id=document_id,
                sketch_id=sketch_id,
                thickness=1.5,
                bend_radius=1.5,
                k_factor=0.42,
            )
            ),
            "solidworks_create_sheet_metal_base_flange",
        )
        info = _require(
            await get_sheet_metal_info(
                DocumentRequest(session_id=session_id, document_id=document_id)
            ),
            "solidworks_get_sheet_metal_info",
        )
        if not info.data["sheet_metal"]["is_sheet_metal"]:
            raise VerificationError("Base flange did not produce a sheet-metal part")
        k_factor = _require(
            await set_k_factor(
                SetSheetMetalKFactorRequest(
                    session_id=session_id,
                    document_id=document_id,
                    k_factor=0.4,
                )
            ),
            "solidworks_set_sheet_metal_k_factor",
        )
        saved = _require(
            await save_model(
                SaveModelRequest(
                    session_id=session_id,
                    document_id=document_id,
                    artifact_name="sheet_metal_base_flange",
                )
            ),
            "solidworks_save_model",
        )
        model_path = Path(saved.data["artifact"]["path"])
        if not model_path.is_file():
            raise VerificationError("solidworks_save_model did not create the part")
        flattened = _require(
            await set_flat_state(
                SheetMetalFlatStateRequest(
                    session_id=session_id,
                    document_id=document_id,
                    flattened=True,
                )
            ),
            "solidworks_set_sheet_metal_flat_state",
        )
        dxf = _require(
            await export_dxf(
                ExportFlatPatternDxfRequest(
                    session_id=session_id,
                    document_id=document_id,
                    artifact_name="sheet_metal_flat_pattern.dxf",
                )
            ),
            "solidworks_export_flat_pattern_dxf",
        )
        dxf_path = Path(dxf.data["artifact"]["path"])
        if not dxf_path.is_file():
            raise VerificationError("solidworks_export_flat_pattern_dxf did not create DXF")
        evidence.update(
            {
                "base_flange": base_flange.data["feature"],
                "sheet_metal": info.data["sheet_metal"],
                "k_factor": k_factor.data["k_factor"],
                "flattened": flattened.data["sheet_metal"]["is_flattened"],
                "part_path": str(model_path),
                "dxf_path": str(dxf_path),
            }
        )
        return evidence
    finally:
        if session_id and document_id:
            tools = await _tools(server)
            close_document = _tool(tools, "solidworks_close_document")
            await close_document(
                CloseDocumentRequest(
                    session_id=session_id,
                    document_id=document_id,
                    save=False,
                )
            )
        if session_id:
            tools = await _tools(server)
            close_session = _tool(tools, "solidworks_close_session")
            await close_session(SessionRequest(session_id=session_id))
        if server.adapter is not None and server.adapter.is_connected():
            await server.adapter.disconnect()


if __name__ == "__main__":
    try:
        result = asyncio.run(run())
    except VerificationError as exc:
        print(f"VERIFICATION_FAILED: {exc}")
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
