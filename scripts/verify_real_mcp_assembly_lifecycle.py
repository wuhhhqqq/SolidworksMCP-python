"""Run a real SolidWorks MCP part-to-assembly edit lifecycle verification.

The workflow deliberately uses registered MCP tool functions only for all
SolidWorks operations.  It creates a dimensioned component, saves it, inserts
it into an assembly, saves the assembly, reopens the component from the
configured trusted input directory, changes its driving dimension, saves, and
closes the document.

Run from the repository root:

    1. Start SolidWorks manually from its desktop shortcut and wait for it to
       finish loading.
    .venv\\Scripts\\python.exe scripts\\verify_real_mcp_assembly_lifecycle.py

The script attaches to that existing instance. Its cleanup closes only the
documents created during this run and intentionally leaves SolidWorks open.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.config import (
    AdapterType,
    DeploymentMode,
    SecurityLevel,
    SolidWorksMCPConfig,
)
from solidworks_mcp.contracts.mvp import (
    ActivateDocumentRequest,
    AddCircleRequest,
    CloseDocumentRequest,
    CreateAssemblyMateRequest,
    CreateAssemblyRequest,
    CreateDrawingBomRequest,
    CreateDrawingRequest,
    CreateDrawingViewRequest,
    CreateExtrusionRequest,
    CreatePartRequest,
    CreateSketchRequest,
    DeleteAssemblyMateRequest,
    DeleteDrawingBomRequest,
    ExitSketchRequest,
    GetDimensionRequest,
    InsertAssemblyComponentRequest,
    ListAssemblyComponentsRequest,
    ListAssemblyMateEntitiesRequest,
    ListAssemblyMatesRequest,
    ListDimensionsRequest,
    OpenModelRequest,
    SaveModelRequest,
    SessionRequest,
    SetDimensionRequest,
    UpdateDrawingBomRequest,
)
from solidworks_mcp.server import SolidWorksMCPServer
from solidworks_mcp.tools.modeling import (
    CloseModelInput,
    GetDimensionInput,
    SetDimensionInput,
)


class VerificationError(RuntimeError):
    """Raised when one real MCP operation does not produce its required result."""


def _require_typed(result: object, operation: str) -> object:
    """Require a succeeded OperationResult without exposing adapter internals."""
    status = getattr(getattr(result, "status", None), "value", None)
    if status != "succeeded":
        error = getattr(result, "error", None)
        raise VerificationError(f"{operation} failed: {error or result!r}")
    return result


def _require_legacy(result: dict[str, object], operation: str) -> dict[str, object]:
    """Require a legacy MCP tool's success response."""
    if result.get("status") != "success":
        raise VerificationError(
            f"{operation} failed: {result.get('message') or result}"
        )
    return result


async def _tools(server: SolidWorksMCPServer) -> dict[str, object]:
    """Return all registered MCP tool callables keyed by their public name."""
    return {tool.name: tool.fn for tool in await server.mcp.list_tools()}


def _tool(tools: dict[str, object], name: str):
    """Get one expected tool with a clear verification failure."""
    tool = tools.get(name)
    if not callable(tool):
        raise VerificationError(f"Required MCP tool is not registered: {name}")
    return tool


async def run() -> dict[str, object]:
    """Execute the complete real SolidWorks MCP lifecycle and return its evidence."""
    repository_root = Path(__file__).resolve().parents[1]
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    data_dir = repository_root / ".generated" / "real_mcp_lifecycle" / run_id
    data_dir.mkdir(parents=True, exist_ok=False)

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
    evidence: dict[str, object] = {"run_id": run_id, "data_dir": str(data_dir)}

    try:
        await server.setup()
        if server.adapter is None:
            raise VerificationError("MCP server did not create a SolidWorks adapter")
        underlying_adapter = server.adapter
        while hasattr(underlying_adapter, "adapter"):
            underlying_adapter = underlying_adapter.adapter
        if not isinstance(underlying_adapter, PyWin32Adapter):
            raise VerificationError(
                "Real validation requires PyWin32Adapter, got "
                f"{type(underlying_adapter).__name__}"
            )
        if not server.adapter.is_connected():
            await server.adapter.connect()
            if not server.adapter.is_connected():
                raise VerificationError("MCP adapter could not connect to SolidWorks")
            server.state.is_connected = True

        tools = await _tools(server)
        create_session = _tool(tools, "solidworks_create_session")
        create_part = _tool(tools, "solidworks_create_part")
        create_sketch = _tool(tools, "solidworks_create_sketch")
        add_circle = _tool(tools, "solidworks_add_circle")
        exit_sketch = _tool(tools, "solidworks_exit_sketch")
        create_extrusion = _tool(tools, "solidworks_create_extrusion")
        save_model = _tool(tools, "solidworks_save_model")
        create_assembly = _tool(tools, "solidworks_create_assembly")
        insert_component = _tool(tools, "solidworks_insert_assembly_component")
        list_components = _tool(tools, "solidworks_list_assembly_components")
        list_mate_entities = _tool(tools, "solidworks_list_assembly_mate_entities")
        create_mate = _tool(tools, "solidworks_create_assembly_mate")
        list_mates = _tool(tools, "solidworks_list_assembly_mates")
        delete_mate = _tool(tools, "solidworks_delete_assembly_mate")
        create_drawing = _tool(tools, "solidworks_create_drawing")
        create_drawing_view = _tool(tools, "solidworks_create_drawing_view")
        create_bom = _tool(tools, "solidworks_create_drawing_bom")
        update_bom = _tool(tools, "solidworks_update_drawing_bom")
        delete_bom = _tool(tools, "solidworks_delete_drawing_bom")
        activate_document = _tool(tools, "solidworks_activate_document")
        close_document = _tool(tools, "solidworks_close_document")
        open_model = _tool(tools, "solidworks_open_model")
        close_session = _tool(tools, "solidworks_close_session")
        list_dimensions = _tool(tools, "solidworks_list_dimensions")
        get_dimension = _tool(tools, "solidworks_get_dimension")
        set_dimension = _tool(tools, "solidworks_set_dimension")
        legacy_get_dimension = _tool(tools, "get_dimension")
        legacy_set_dimension = _tool(tools, "set_dimension")
        legacy_close_model = _tool(tools, "close_model")

        session = _require_typed(await create_session(), "solidworks_create_session")
        session_id = session.data["session"]["session_id"]
        evidence["create_session"] = {"session_id": session_id}

        part = _require_typed(
            await create_part(
                CreatePartRequest(session_id=session_id, name="MCP_E2E_Component")
            ),
            "solidworks_create_part",
        )
        part_document_id = part.data["document"]["document_id"]

        sketch = _require_typed(
            await create_sketch(
                CreateSketchRequest(
                    session_id=session_id,
                    document_id=part_document_id,
                    plane="Front",
                )
            ),
            "solidworks_create_sketch",
        )
        sketch_id = sketch.data["sketch"]["persistent_id"]

        circle = _require_typed(
            await add_circle(
                AddCircleRequest(
                    session_id=session_id,
                    document_id=part_document_id,
                    sketch_id=sketch_id,
                    center_x=0,
                    center_y=0,
                    radius=15,
                )
            ),
            "solidworks_add_circle",
        )
        circle_id = circle.data["circle"]["persistent_id"]
        _require_typed(
            await exit_sketch(
                ExitSketchRequest(
                    session_id=session_id,
                    document_id=part_document_id,
                    sketch_id=sketch_id,
                )
            ),
            "solidworks_exit_sketch",
        )
        extrusion = _require_typed(
            await create_extrusion(
                CreateExtrusionRequest(
                    session_id=session_id,
                    document_id=part_document_id,
                    sketch_id=sketch_id,
                    depth=10,
                )
            ),
            "solidworks_create_extrusion",
        )
        _ = extrusion

        part_save = _require_typed(
            await save_model(
                SaveModelRequest(
                    session_id=session_id,
                    document_id=part_document_id,
                    artifact_name="mcp_e2e_component",
                )
            ),
            "solidworks_save_model(part)",
        )
        part_path = Path(part_save.data["artifact"]["path"])
        if not part_path.is_file():
            raise VerificationError(f"MCP did not produce the part file: {part_path}")

        input_path = config.model_input_dir / "mcp_e2e_component_input.sldprt"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(part_path, input_path)
        if not input_path.is_file():
            raise VerificationError(
                "Could not stage the MCP-created part in trusted inputs"
            )

        assembly = _require_typed(
            await create_assembly(
                CreateAssemblyRequest(session_id=session_id, name="MCP_E2E_Assembly")
            ),
            "solidworks_create_assembly",
        )
        assembly_document_id = assembly.data["document"]["document_id"]
        first_inserted = _require_typed(
            await insert_component(
                InsertAssemblyComponentRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                    source_part_document_id=part_document_id,
                    x=25,
                )
            ),
            "solidworks_insert_assembly_component",
        )
        second_inserted = _require_typed(
            await insert_component(
                InsertAssemblyComponentRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                    source_part_document_id=part_document_id,
                    x=75,
                )
            ),
            "solidworks_insert_assembly_component(second instance)",
        )
        components = _require_typed(
            await list_components(
                ListAssemblyComponentsRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                )
            ),
            "solidworks_list_assembly_components",
        )
        if components.data["total"] != 2:
            raise VerificationError(
                "Assembly did not contain exactly two inserted components: "
                f"{components.data['total']}"
            )
        component_rows = components.data["components"]
        component_ids = {row["component_id"] for row in component_rows}
        first_component_id = first_inserted.data["component"]["component_id"]
        second_component_id = second_inserted.data["component"]["component_id"]
        if {first_component_id, second_component_id} != component_ids:
            raise VerificationError("Assembly component listing did not retain both instances")

        mate_entities = _require_typed(
            await list_mate_entities(
                ListAssemblyMateEntitiesRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                    entity_types=["edge"],
                    limit=100,
                )
            ),
            "solidworks_list_assembly_mate_entities",
        )
        edge_by_component: dict[str, str] = {}
        for entity in mate_entities.data["entities"]:
            edge_by_component.setdefault(entity["component_id"], entity["entity_id"])
        if first_component_id not in edge_by_component or second_component_id not in edge_by_component:
            raise VerificationError("Could not obtain one circular edge from each assembly component")
        created_mate = _require_typed(
            await create_mate(
                CreateAssemblyMateRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                    first_entity_id=edge_by_component[first_component_id],
                    second_entity_id=edge_by_component[second_component_id],
                    mate_type="concentric",
                    lock_rotation=True,
                )
            ),
            "solidworks_create_assembly_mate",
        )
        mates_after_create = _require_typed(
            await list_mates(
                ListAssemblyMatesRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                )
            ),
            "solidworks_list_assembly_mates",
        )
        if mates_after_create.data["total"] < 1:
            raise VerificationError("Assembly mate creation did not produce an enumerable mate")
        mate_to_delete = next(
            (
                mate
                for mate in mates_after_create.data["mates"]
                if mate["display_name"] == created_mate.data["mate"]["display_name"]
            ),
            mates_after_create.data["mates"][-1],
        )
        deleted_mate = _require_typed(
            await delete_mate(
                DeleteAssemblyMateRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                    mate_id=mate_to_delete["mate_id"],
                )
            ),
            "solidworks_delete_assembly_mate",
        )
        mates_after_delete = _require_typed(
            await list_mates(
                ListAssemblyMatesRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                )
            ),
            "solidworks_list_assembly_mates(after delete)",
        )
        if mates_after_delete.data["total"] != mates_after_create.data["total"] - 1:
            raise VerificationError("Assembly mate deletion did not reduce the mate count")
        assembly_save = _require_typed(
            await save_model(
                SaveModelRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                    artifact_name="mcp_e2e_assembly",
                )
            ),
            "solidworks_save_model(assembly)",
        )
        assembly_path = Path(assembly_save.data["artifact"]["path"])
        if not assembly_path.is_file():
            raise VerificationError(
                f"MCP did not produce the assembly file: {assembly_path}"
            )

        drawing = _require_typed(
            await create_drawing(
                CreateDrawingRequest(session_id=session_id, name="MCP_E2E_BOM_Drawing")
            ),
            "solidworks_create_drawing",
        )
        drawing_document_id = drawing.data["document"]["document_id"]
        drawing_view = _require_typed(
            await create_drawing_view(
                CreateDrawingViewRequest(
                    session_id=session_id,
                    document_id=drawing_document_id,
                    source_model_document_id=assembly_document_id,
                    orientation="isometric",
                    x=100,
                    y=100,
                )
            ),
            "solidworks_create_drawing_view(assembly)",
        )
        created_bom = _require_typed(
            await create_bom(
                CreateDrawingBomRequest(
                    session_id=session_id,
                    document_id=drawing_document_id,
                    source_assembly_document_id=assembly_document_id,
                    source_view_id=drawing_view.data["view"]["view_id"],
                )
            ),
            "solidworks_create_drawing_bom",
        )
        updated_bom = _require_typed(
            await update_bom(
                UpdateDrawingBomRequest(
                    session_id=session_id,
                    document_id=drawing_document_id,
                    bom_id=created_bom.data["bom_id"],
                )
            ),
            "solidworks_update_drawing_bom",
        )
        deleted_bom = _require_typed(
            await delete_bom(
                DeleteDrawingBomRequest(
                    session_id=session_id,
                    document_id=drawing_document_id,
                    bom_id=updated_bom.data["bom_id"],
                )
            ),
            "solidworks_delete_drawing_bom",
        )
        _require_typed(
            await close_document(
                CloseDocumentRequest(
                    session_id=session_id,
                    document_id=drawing_document_id,
                    save=False,
                )
            ),
            "solidworks_close_document(drawing)",
        )

        extrusion_name = extrusion.data["feature"]["display_name"]
        _require_typed(
            await activate_document(
                ActivateDocumentRequest(
                    session_id=session_id,
                    document_id=assembly_document_id,
                )
            ),
            "solidworks_activate_document(assembly)",
        )
        _require_legacy(
            await legacy_close_model(CloseModelInput(save=False)),
            "close_model(assembly)",
        )
        _require_typed(
            await activate_document(
                ActivateDocumentRequest(
                    session_id=session_id,
                    document_id=part_document_id,
                )
            ),
            "solidworks_activate_document(part)",
        )
        _require_legacy(
            await legacy_close_model(CloseModelInput(save=False)),
            "close_model(original part)",
        )
        _require_typed(
            await close_session(SessionRequest(session_id=session_id)),
            "solidworks_close_session(original)",
        )

        edit_session = _require_typed(
            await create_session(), "solidworks_create_session(edit)"
        )
        edit_session_id = edit_session.data["session"]["session_id"]
        opened = _require_typed(
            await open_model(
                OpenModelRequest(
                    session_id=edit_session_id,
                    file_name=input_path.name,
                )
            ),
            "solidworks_open_model",
        )
        edited_document_id = opened.data["document"]["document_id"]
        listed_dimensions = await list_dimensions(
            ListDimensionsRequest(
                session_id=edit_session_id,
                document_id=edited_document_id,
            )
        )
        use_opaque_dimensions = (
            getattr(listed_dimensions.status, "value", None) == "succeeded"
        )
        if not use_opaque_dimensions:
            dimension_name = f"D1@{extrusion_name}"
            before = _require_legacy(
                await legacy_get_dimension(GetDimensionInput(name=dimension_name)),
                "get_dimension(before)",
            )
            before_value = float(before["dimension"]["value"])
            target_value = before_value + 10.0
            _require_legacy(
                await legacy_set_dimension(
                    SetDimensionInput(name=dimension_name, value=target_value)
                ),
                "set_dimension",
            )
            after = _require_legacy(
                await legacy_get_dimension(GetDimensionInput(name=dimension_name)),
                "get_dimension(after)",
            )
            after_value = float(after["dimension"]["value"])
            dimension_id = "legacy-localized-dimension"
        else:
            listed_dimensions = _require_typed(
                listed_dimensions,
                "solidworks_list_dimensions",
            )
            dimensions = listed_dimensions.data["dimensions"]
            matching_dimensions = [
                dimension
                for dimension in dimensions
                if abs(float(dimension["value_mm"]) - 10.0) <= 0.001
            ]
            if len(matching_dimensions) != 1:
                raise VerificationError(
                    "Expected exactly one 10 mm driving dimension in the reopened part"
                )
            dimension_id = matching_dimensions[0]["dimension_id"]
            before = _require_typed(
                await get_dimension(
                    GetDimensionRequest(
                        session_id=edit_session_id,
                        document_id=edited_document_id,
                        dimension_id=dimension_id,
                    )
                ),
                "solidworks_get_dimension(before)",
            )
            before_value = float(before.data["dimension"]["value_mm"])
            target_value = before_value + 10.0
            changed = _require_typed(
                await set_dimension(
                    SetDimensionRequest(
                        session_id=edit_session_id,
                        document_id=edited_document_id,
                        dimension_id=dimension_id,
                        value=target_value,
                    )
                ),
                "solidworks_set_dimension",
            )
            updated_dimension_id = changed.data["dimension"]["dimension_id"]
            after = _require_typed(
                await get_dimension(
                    GetDimensionRequest(
                        session_id=edit_session_id,
                        document_id=edited_document_id,
                        dimension_id=updated_dimension_id,
                    )
                ),
                "solidworks_get_dimension(after)",
            )
            after_value = float(after.data["dimension"]["value_mm"])
        if before_value <= 0:
            raise VerificationError(
                f"Expected a positive driving dimension before editing, got {before_value}"
            )
        if abs(after_value - target_value) > 0.001:
            raise VerificationError(
                f"Expected the driving dimension to be {target_value} mm after editing, got {after_value}"
            )
        edited_save = _require_typed(
            await save_model(
                SaveModelRequest(
                    session_id=edit_session_id,
                    document_id=edited_document_id,
                    artifact_name="mcp_e2e_component_edited",
                )
            ),
            "solidworks_save_model(edited part)",
        )
        edited_part_path = Path(edited_save.data["artifact"]["path"])
        if not edited_part_path.is_file():
            raise VerificationError(
                f"MCP did not produce the edited part file: {edited_part_path}"
            )
        _require_typed(
            await close_document(
                CloseDocumentRequest(
                    session_id=edit_session_id,
                    document_id=edited_document_id,
                    save=False,
                )
            ),
            "solidworks_close_document(edited part)",
        )
        _require_typed(
            await close_session(SessionRequest(session_id=edit_session_id)),
            "solidworks_close_session(edit)",
        )

        persisted_input_path = (
            config.model_input_dir / "mcp_e2e_component_edited_input.sldprt"
        )
        shutil.copy2(edited_part_path, persisted_input_path)
        persisted_session = _require_typed(
            await create_session(), "solidworks_create_session(persisted)"
        )
        persisted_session_id = persisted_session.data["session"]["session_id"]
        persisted_opened = _require_typed(
            await open_model(
                OpenModelRequest(
                    session_id=persisted_session_id,
                    file_name=persisted_input_path.name,
                )
            ),
            "solidworks_open_model(persisted)",
        )
        persisted_document_id = persisted_opened.data["document"]["document_id"]
        if use_opaque_dimensions:
            persisted_dimensions = _require_typed(
                await list_dimensions(
                    ListDimensionsRequest(
                        session_id=persisted_session_id,
                        document_id=persisted_document_id,
                    )
                ),
                "solidworks_list_dimensions(persisted)",
            )
            persisted_matches = [
                dimension
                for dimension in persisted_dimensions.data["dimensions"]
                if abs(float(dimension["value_mm"]) - target_value) <= 0.001
            ]
            if len(persisted_matches) != 1:
                raise VerificationError(
                    "Expected exactly one persisted dimension with the edited value"
                )
            persisted = _require_typed(
                await get_dimension(
                    GetDimensionRequest(
                        session_id=persisted_session_id,
                        document_id=persisted_document_id,
                        dimension_id=persisted_matches[0]["dimension_id"],
                    )
                ),
                "solidworks_get_dimension(persisted)",
            )
            persisted_value = float(persisted.data["dimension"]["value_mm"])
        else:
            persisted = _require_legacy(
                await legacy_get_dimension(GetDimensionInput(name=dimension_name)),
                "get_dimension(persisted)",
            )
            persisted_value = float(persisted["dimension"]["value"])
        if abs(persisted_value - target_value) > 0.001:
            raise VerificationError(
                f"Expected the saved driving dimension to be {target_value} mm, got {persisted_value}"
            )
        _require_typed(
            await close_document(
                CloseDocumentRequest(
                    session_id=persisted_session_id,
                    document_id=persisted_document_id,
                    save=False,
                )
            ),
            "solidworks_close_document(persisted part)",
        )
        _require_typed(
            await close_session(SessionRequest(session_id=persisted_session_id)),
            "solidworks_close_session(persisted)",
        )

        evidence.update(
            {
                "component": {
                    "part_path": str(part_path),
                    "trusted_input_path": str(input_path),
                    "edited_part_path": str(edited_part_path),
                    "persisted_input_path": str(persisted_input_path),
                    "dimension_id": dimension_id,
                    "sketch_entity_id": circle_id,
                    "before_mm": before_value,
                    "after_mm": after_value,
                    "target_mm": target_value,
                    "persisted_mm": persisted_value,
                },
                "assembly": {
                    "assembly_path": str(assembly_path),
                    "components": components.data["components"],
                    "mate": {
                        "created": created_mate.data["mate"],
                        "deleted": deleted_mate.data["deleted_mate"],
                    },
                },
                "drawing": {
                    "view": drawing_view.data["view"],
                    "bom": {
                        "created": created_bom.data["bom"],
                        "updated": updated_bom.data["bom"],
                        "deleted": deleted_bom.data["deleted_bom"],
                    },
                },
                "result": "passed",
            }
        )
        return evidence
    finally:
        if server.adapter is not None:
            underlying_adapter = server.adapter
            while hasattr(underlying_adapter, "adapter"):
                underlying_adapter = underlying_adapter.adapter
            close_session_documents = getattr(
                underlying_adapter, "close_all_session_docs", None
            )
            if callable(close_session_documents):
                try:
                    await close_session_documents()
                except Exception:
                    pass
            await server.adapter.disconnect()


def main() -> None:
    """Run the verification and print its artifact and dimension evidence as JSON."""
    evidence = asyncio.run(run())
    print(json.dumps(evidence, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
