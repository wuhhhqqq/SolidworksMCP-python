"""Verify a real MCP opaque-dimension CRUD workflow against a part copy.

Usage from the repository root:

    .venv\\Scripts\\python.exe scripts\\verify_real_mcp_opaque_dimensions.py path\\to\\model.sldprt

The supplied model is copied into an isolated trusted-input directory. The
verification opens that copy through registered MCP tools, lists an opaque
dimension ID, reads and changes its value, saves an artifact, and reopens the
artifact to verify persistence. The source model remains unchanged.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
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
    CloseDocumentRequest,
    GetDimensionRequest,
    ListDimensionsRequest,
    OpenModelRequest,
    SaveModelRequest,
    SessionRequest,
    SetDimensionRequest,
)
from solidworks_mcp.server import SolidWorksMCPServer


class VerificationError(RuntimeError):
    """Raised when a required MCP operation does not succeed."""


def _require(result: object, operation: str) -> object:
    """Require one succeeded typed MCP result."""
    if getattr(getattr(result, "status", None), "value", None) != "succeeded":
        raise VerificationError(
            f"{operation} failed: {getattr(result, 'error', None) or result!r}"
        )
    return result


async def _tool_map(server: SolidWorksMCPServer) -> dict[str, object]:
    """Return registered MCP tool callables keyed by their public names."""
    return {tool.name: tool.fn for tool in await server.mcp.list_tools()}


def _tool(tools: dict[str, object], name: str):
    """Return one registered tool or fail with a verification-specific error."""
    tool = tools.get(name)
    if not callable(tool):
        raise VerificationError(f"Required MCP tool is not registered: {name}")
    return tool


async def run(source_model: Path) -> dict[str, object]:
    """Run the real opaque dimension MCP CRUD workflow."""
    if not source_model.is_file() or source_model.suffix.casefold() != ".sldprt":
        raise VerificationError("Expected an existing .sldprt source model")

    repository_root = Path(__file__).resolve().parents[1]
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    data_dir = repository_root / ".generated" / "real_mcp_opaque_dimensions" / run_id
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
    staged_name = f"opaque_dimensions_{uuid4().hex[:8]}.sldprt"
    staged_path = config.model_input_dir / staged_name
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_model, staged_path)

    server = SolidWorksMCPServer(config)
    evidence: dict[str, object] = {
        "run_id": run_id,
        "source_path": str(source_model),
        "staged_path": str(staged_path),
    }
    session_id: str | None = None
    document_id: str | None = None
    try:
        await server.setup()
        if server.adapter is None:
            raise VerificationError("MCP server did not create a SolidWorks adapter")
        if not server.adapter.is_connected():
            await server.adapter.connect()
            server.state.is_connected = server.adapter.is_connected()
        if not server.state.is_connected:
            raise VerificationError("MCP adapter could not connect to SolidWorks")

        tools = await _tool_map(server)
        create_session = _tool(tools, "solidworks_create_session")
        open_model = _tool(tools, "solidworks_open_model")
        list_dimensions = _tool(tools, "solidworks_list_dimensions")
        get_dimension = _tool(tools, "solidworks_get_dimension")
        set_dimension = _tool(tools, "solidworks_set_dimension")
        save_model = _tool(tools, "solidworks_save_model")
        close_document = _tool(tools, "solidworks_close_document")
        close_session = _tool(tools, "solidworks_close_session")

        session = _require(await create_session(), "solidworks_create_session")
        session_id = session.data["session"]["session_id"]
        opened = _require(
            await open_model(OpenModelRequest(session_id=session_id, file_name=staged_name)),
            "solidworks_open_model",
        )
        document_id = opened.data["document"]["document_id"]
        listed = _require(
            await list_dimensions(
                ListDimensionsRequest(session_id=session_id, document_id=document_id)
            ),
            "solidworks_list_dimensions",
        )
        dimensions = listed.data["dimensions"]
        if not dimensions:
            raise VerificationError("solidworks_list_dimensions returned no dimensions")
        dimension = dimensions[0]
        dimension_id = dimension["dimension_id"]
        read = _require(
            await get_dimension(
                GetDimensionRequest(
                    session_id=session_id,
                    document_id=document_id,
                    dimension_id=dimension_id,
                )
            ),
            "solidworks_get_dimension",
        )
        if read.data["dimension"]["dimension_id"] != dimension_id:
            raise VerificationError("Dimension read returned a different opaque ID")
        before_value_mm = float(read.data["dimension"]["value_mm"])
        target_value_mm = before_value_mm + 5.0
        changed = _require(
            await set_dimension(
                SetDimensionRequest(
                    session_id=session_id,
                    document_id=document_id,
                    dimension_id=dimension_id,
                    value=target_value_mm,
                )
            ),
            "solidworks_set_dimension",
        )
        updated_dimension_id = changed.data["dimension"]["dimension_id"]
        updated = _require(
            await get_dimension(
                GetDimensionRequest(
                    session_id=session_id,
                    document_id=document_id,
                    dimension_id=updated_dimension_id,
                )
            ),
            "solidworks_get_dimension(updated)",
        )
        updated_value_mm = float(updated.data["dimension"]["value_mm"])
        if abs(updated_value_mm - target_value_mm) > 0.001:
            raise VerificationError(
                "solidworks_set_dimension did not return the requested value"
            )
        saved = _require(
            await save_model(
                SaveModelRequest(
                    session_id=session_id,
                    document_id=document_id,
                    artifact_name="opaque_dimensions_edited",
                )
            ),
            "solidworks_save_model",
        )
        edited_path = Path(saved.data["artifact"]["path"])
        if not edited_path.is_file():
            raise VerificationError("solidworks_save_model did not create an artifact")
        _require(
            await close_document(
                CloseDocumentRequest(
                    session_id=session_id,
                    document_id=document_id,
                    save=False,
                )
            ),
            "solidworks_close_document(edited)",
        )
        _require(
            await close_session(SessionRequest(session_id=session_id)),
            "solidworks_close_session(edited)",
        )
        session_id = None
        document_id = None

        persisted_name = f"opaque_dimensions_persisted_{uuid4().hex[:8]}.sldprt"
        persisted_path = config.model_input_dir / persisted_name
        shutil.copy2(edited_path, persisted_path)
        persisted_session = _require(
            await create_session(), "solidworks_create_session(persisted)"
        )
        session_id = persisted_session.data["session"]["session_id"]
        persisted_opened = _require(
            await open_model(
                OpenModelRequest(session_id=session_id, file_name=persisted_name)
            ),
            "solidworks_open_model(persisted)",
        )
        document_id = persisted_opened.data["document"]["document_id"]
        persisted_list = _require(
            await list_dimensions(
                ListDimensionsRequest(session_id=session_id, document_id=document_id)
            ),
            "solidworks_list_dimensions(persisted)",
        )
        persisted_dimension = next(
            (
                candidate
                for candidate in persisted_list.data["dimensions"]
                if abs(float(candidate["value_mm"]) - target_value_mm) <= 0.001
            ),
            None,
        )
        if persisted_dimension is None:
            raise VerificationError("Saved artifact did not retain the changed dimension")
        persisted = _require(
            await get_dimension(
                GetDimensionRequest(
                    session_id=session_id,
                    document_id=document_id,
                    dimension_id=persisted_dimension["dimension_id"],
                )
            ),
            "solidworks_get_dimension(persisted)",
        )
        persisted_value_mm = float(persisted.data["dimension"]["value_mm"])
        if abs(persisted_value_mm - target_value_mm) > 0.001:
            raise VerificationError("Persisted dimension value does not match")
        evidence["dimension"] = {
            "dimension_id_before_write": dimension_id,
            "dimension_id_after_write": updated_dimension_id,
            "listed_value_mm": dimension["value_mm"],
            "read_value_mm": before_value_mm,
            "target_value_mm": target_value_mm,
            "updated_value_mm": updated_value_mm,
            "persisted_dimension_id": persisted_dimension["dimension_id"],
            "persisted_value_mm": persisted_value_mm,
        }
        evidence["edited_artifact_path"] = str(edited_path)
        evidence["persisted_input_path"] = str(persisted_path)
        return evidence
    finally:
        if session_id and document_id:
            tools = await _tool_map(server)
            close_document = _tool(tools, "solidworks_close_document")
            await close_document(
                CloseDocumentRequest(
                    session_id=session_id,
                    document_id=document_id,
                    save=False,
                )
            )
        if session_id:
            tools = await _tool_map(server)
            close_session = _tool(tools, "solidworks_close_session")
            await close_session(SessionRequest(session_id=session_id))
        if server.adapter is not None and server.adapter.is_connected():
            await server.adapter.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Expected one .sldprt source-model path", file=sys.stderr)
        raise SystemExit(2)
    try:
        result = asyncio.run(run(Path(sys.argv[1]).resolve()))
    except VerificationError as exc:
        print(f"VERIFICATION_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
