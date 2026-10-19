"""Real SolidWorks integration smoke tests."""

from __future__ import annotations

import json
import os
import platform
from collections import Counter
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio

from solidworks_mcp.adapters.base import ReferencePlaneParameters
from solidworks_mcp.config import (
    AdapterType,
    DeploymentMode,
    SecurityLevel,
    SolidWorksMCPConfig,
)
from solidworks_mcp.contracts.mvp import (
    ActivateConfigurationRequest,
    AddRectangleRequest,
    CreateAssemblyRequest,
    CreateDrawingRequest,
    CreateDrawingViewRequest,
    CreateExtrusionRequest,
    CreateFeatureCircularPatternRequest,
    CreateFeatureLinearPatternRequest,
    CreatePartRequest,
    CreateProjectedDrawingViewRequest,
    CreateReferencePlaneRequest,
    CreateSketchRequest,
    DocumentRequest,
    ExitSketchRequest,
    ExportFileRequest,
    GetReferenceAxisRequest,
    GetReferencePlaneRequest,
    InsertAssemblyComponentRequest,
    ListAssemblyComponentsRequest,
    ListConfigurationsRequest,
    ListCustomPropertiesRequest,
    ListDrawingViewsRequest,
    ListFeaturesRequest,
    MirrorFeatureRequest,
    SaveModelRequest,
    SessionRequest,
    SetCustomPropertyRequest,
)
from solidworks_mcp.server import SolidWorksMCPServer
from solidworks_mcp.tools.file_management import SaveAsInput, SaveFileInput
from solidworks_mcp.tools.import_cad import ImportDxfToPartInput
from solidworks_mcp.tools.modeling import (
    CloseModelInput,
    CreateAssemblyInput,
    CreatePartInput,
    OpenModelInput,
)

REAL_SW_ENV_FLAG = "SOLIDWORKS_MCP_RUN_REAL_INTEGRATION"


def _real_solidworks_enabled() -> bool:
    """Test helper for real solidworks enabled."""
    value = os.getenv(REAL_SW_ENV_FLAG, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


async def _find_tool(server: SolidWorksMCPServer, tool_name: str):
    """Test helper for find tool."""
    for tool in await server.mcp.list_tools():
        if tool.name == tool_name:
            return tool.fn
    raise AssertionError(f"Tool '{tool_name}' not found")


async def _tool_names(server: SolidWorksMCPServer) -> list[str]:
    """Test helper for tool names."""
    return sorted(tool.name for tool in await server.mcp.list_tools())


@pytest_asyncio.fixture
async def real_server(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncGenerator[SolidWorksMCPServer, None]:
    """Test helper for real server."""
    if platform.system() != "Windows":
        pytest.skip("Real SolidWorks integration tests require Windows")

    if not _real_solidworks_enabled():
        pytest.skip(
            f"Set {REAL_SW_ENV_FLAG}=true to run real SolidWorks integration tests"
        )

    config = SolidWorksMCPConfig(
        deployment_mode=DeploymentMode.LOCAL,
        security_level=SecurityLevel.MINIMAL,
        adapter_type=AdapterType.PYWIN32,
        mock_solidworks=False,
        testing=False,
        debug=True,
        enable_windows_validation=False,
        data_dir=tmp_path_factory.mktemp("solidworks_mcp_real_data"),
    )

    server = SolidWorksMCPServer(config)
    await server.setup()

    try:
        await server.adapter.connect()
    except Exception as exc:
        await server.stop()
        pytest.fail(f"Could not connect to local SolidWorks instance: {exc}")

    try:
        yield server
    finally:
        # Keep the public adapter wrappers intact: in particular, the STA
        # wrapper must marshal this cleanup call onto the COM-owning thread.
        if hasattr(server.adapter, "close_all_session_docs"):
            try:
                await server.adapter.close_all_session_docs()
            except Exception:
                pass
        else:
            # Fallback: close only the current active model
            close_tool = await _find_tool(server, "close_model")
            try:
                await close_tool(CloseModelInput(save=False))
            except Exception:
                pass
        await server.stop()


@pytest.fixture
def integration_output_dir(tmp_path: Path) -> Path:
    """Test helper for integration output dir."""
    output_dir = tmp_path / "solidworks_integration"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_dxf_import_preflight_and_boss_extrude(
    real_server: SolidWorksMCPServer,
) -> None:
    """Import a clean DXF profile, validate it natively, and create a part.

    The DXF and resulting part are placed in pytest's per-run data directory.
    The real-server fixture closes the created document without closing the
    manually started SolidWorks application.
    """
    import ezdxf

    import_tool = await _find_tool(real_server, "import_dxf_to_part")
    work_dir = real_server.config.data_dir / "dxf_import_smoke"
    work_dir.mkdir(parents=True, exist_ok=True)
    source_path = work_dir / "closed_rectangle_mm.dxf"
    output_path = work_dir / "closed_rectangle_mm.sldprt"

    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4  # Millimetres
    modelspace = document.modelspace()
    corners = ((0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0))
    for start, end in zip(corners, (*corners[1:], corners[0]), strict=True):
        modelspace.add_line(start, end)
    document.saveas(source_path)

    result = await import_tool(
        ImportDxfToPartInput(
            source_path=str(source_path),
            output_path=str(output_path),
            plane="Front",
            extrusion_depth_mm=12.0,
            verify_with_solidworks=True,
        )
    )

    assert result["status"] == "success", result
    assert result["imported_entities"] == {"LINE": 4}
    assert result["validation"]["is_ready_for_extrusion"] is True
    assert result["solidworks_validation"]["is_valid"] is True
    assert result["part"]["extrusion_depth_mm"] == 12.0
    assert output_path.is_file(), result


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_registered_tool_catalog_snapshot(
    real_server: SolidWorksMCPServer,
    integration_output_dir: Path,
) -> None:
    """Verify tool catalog coverage and persist a local snapshot for auditing."""
    tool_names = await _tool_names(real_server)
    duplicate_counts = {
        name: count for name, count in Counter(tool_names).items() if count > 1
    }
    allowed_duplicate_counts: dict[str, int] = {
        # No intentional duplicate tool names are expected.
    }

    assert duplicate_counts == allowed_duplicate_counts, (
        f"Unexpected duplicate tool names: {duplicate_counts}"
    )

    unique_tool_names = set(tool_names)
    assert len(tool_names) >= 77, f"Expected at least 77 tools, got {len(tool_names)}"

    expected_core_tools = {
        # Modeling / file lifecycle
        "create_part",
        "create_assembly",
        "create_drawing",
        "open_model",
        "close_model",
        "save_file",
        "save_as",
        # Sketching
        "create_sketch",
        "add_circle",
        "exit_sketch",
        # Analysis / export
        "get_mass_properties",
        "check_interference",
        "export_step",
        # Automation / templates / macros / drawing analysis
        "batch_process_files",
        "generate_vba_extrusion",
        "extract_template",
        "start_macro_recording",
        "analyze_drawing_comprehensive",
    }

    missing = sorted(expected_core_tools.difference(unique_tool_names))
    assert not missing, f"Missing expected tools: {missing}"

    snapshot_path = integration_output_dir / "tool_catalog_snapshot.json"
    snapshot = {
        "tool_count": len(tool_names),
        "tools": tool_names,
    }
    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    assert snapshot_path.exists(), f"Expected snapshot file at {snapshot_path}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_solidworks_connection_health(
    real_server: SolidWorksMCPServer,
) -> None:
    """Verify that we can connect to and query a real SolidWorks session."""
    assert real_server.adapter.is_connected()

    health = await real_server.health_check()
    assert health["status"] in {"healthy", "warning"}
    assert health["adapter"] is not None


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_reference_plane_adapter(
    real_server: SolidWorksMCPServer,
) -> None:
    """Create an offset plane through the adapter with a localized template."""
    tools = {
        tool.name: tool.fn
        for tool in await real_server.mcp.list_tools()
        if tool.name.startswith("solidworks_")
    }
    session = await tools["solidworks_create_session"]()
    session_id = session.data["session"]["session_id"]
    part = await tools["solidworks_create_part"](
        CreatePartRequest(session_id=session_id, name="MCP_ReferencePlane_Probe")
    )
    assert part.status.value == "succeeded", part

    adapter = real_server.adapter
    while hasattr(adapter, "adapter"):
        adapter = adapter.adapter
    result = await adapter.create_reference_plane(
        ReferencePlaneParameters(base_plane="Front", offset=10.0)
    )

    assert result.is_success, result.error


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_typed_mvp_part_to_step_workflow(
    real_server: SolidWorksMCPServer,
) -> None:
    """Build and export a part through the typed, session-scoped MCP surface."""
    tools = {
        tool.name: tool.fn
        for tool in await real_server.mcp.list_tools()
        if tool.name.startswith("solidworks_")
    }

    session = await tools["solidworks_create_session"]()
    assert session.status.value == "succeeded", session
    assert session.data["session"]["connected"] is True
    session_id = session.data["session"]["session_id"]

    part = await tools["solidworks_create_part"](
        CreatePartRequest(session_id=session_id, name="MCP_Typed_MVP_Bracket")
    )
    assert part.status.value == "succeeded", part
    document_id = part.data["document"]["document_id"]

    reference_plane = await tools["solidworks_create_reference_plane"](
        CreateReferencePlaneRequest(
            session_id=session_id,
            document_id=document_id,
            base_plane="Front",
            offset=10,
        )
    )
    assert reference_plane.status.value == "succeeded", reference_plane

    sketch = await tools["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id,
            document_id=document_id,
            reference_plane_id=reference_plane.data["reference_plane"]["persistent_id"],
        )
    )
    assert sketch.status.value == "succeeded", sketch
    sketch_id = sketch.data["sketch"]["persistent_id"]

    rectangle = await tools["solidworks_add_rectangle"](
        AddRectangleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=0,
            x2=30,
            y2=20,
        )
    )
    assert rectangle.status.value == "succeeded", rectangle

    exited = await tools["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
        )
    )
    assert exited.status.value == "succeeded", exited

    extrusion = await tools["solidworks_create_extrusion"](
        CreateExtrusionRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            depth=10,
        )
    )
    assert extrusion.status.value == "succeeded", extrusion

    linear_pattern = await tools["solidworks_create_feature_linear_pattern"](
        CreateFeatureLinearPatternRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=extrusion.data["feature"]["persistent_id"],
            direction="X",
            instances=2,
            spacing=40,
        )
    )
    assert linear_pattern.status.value == "succeeded", linear_pattern

    origin_axis = await tools["solidworks_get_reference_axis"](
        GetReferenceAxisRequest(
            session_id=session_id, document_id=document_id, axis="Z"
        )
    )
    assert origin_axis.status.value == "succeeded", origin_axis
    circular_pattern = await tools["solidworks_create_feature_circular_pattern"](
        CreateFeatureCircularPatternRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=linear_pattern.data["feature"]["persistent_id"],
            reference_axis_id=origin_axis.data["reference_axis"]["persistent_id"],
            instances=2,
            angle_degrees=180,
        )
    )
    assert circular_pattern.status.value == "succeeded", circular_pattern

    origin_plane = await tools["solidworks_get_reference_plane"](
        GetReferencePlaneRequest(
            session_id=session_id, document_id=document_id, plane="Right"
        )
    )
    assert origin_plane.status.value == "succeeded", origin_plane
    mirrored_feature = await tools["solidworks_mirror_feature"](
        MirrorFeatureRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=circular_pattern.data["feature"]["persistent_id"],
            reference_plane_id=origin_plane.data["reference_plane"]["persistent_id"],
        )
    )
    assert mirrored_feature.status.value == "succeeded", mirrored_feature

    features = await tools["solidworks_list_features"](
        ListFeaturesRequest(session_id=session_id, document_id=document_id)
    )
    assert features.status.value == "succeeded", features
    assert features.data["total"] >= 1

    configurations = await tools["solidworks_list_configurations"](
        ListConfigurationsRequest(session_id=session_id, document_id=document_id)
    )
    assert configurations.status.value == "succeeded", configurations
    assert configurations.data["total"] >= 1
    configuration_name = configurations.data["configurations"][0]["name"]
    activated_configuration = await tools["solidworks_activate_configuration"](
        ActivateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name=configuration_name,
        )
    )
    assert activated_configuration.status.value == "succeeded", activated_configuration
    assert (
        activated_configuration.data["document"]["configuration"] == configuration_name
    )

    set_property = await tools["solidworks_set_custom_property"](
        SetCustomPropertyRequest(
            session_id=session_id,
            document_id=document_id,
            name="McpIntegrationId",
            value="typed-mvp-bracket",
        )
    )
    assert set_property.status.value == "succeeded", set_property
    properties = await tools["solidworks_list_custom_properties"](
        ListCustomPropertiesRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )
    assert properties.status.value == "succeeded", properties
    assert any(
        property_["name"] == "McpIntegrationId"
        and property_["value"] == "typed-mvp-bracket"
        for property_ in properties.data["properties"]
    )

    model_info = await tools["solidworks_get_model_info"](
        DocumentRequest(session_id=session_id, document_id=document_id)
    )
    assert model_info.status.value == "succeeded", model_info

    mass_properties = await tools["solidworks_get_mass_properties"](
        DocumentRequest(session_id=session_id, document_id=document_id)
    )
    assert mass_properties.status.value == "succeeded", mass_properties

    exported = await tools["solidworks_export_file"](
        ExportFileRequest(
            session_id=session_id,
            document_id=document_id,
            artifact_name="mcp_typed_mvp_bracket",
            format_type="step",
        )
    )
    assert exported.status.value == "succeeded", exported
    artifact_path = Path(exported.data["artifact"]["path"])
    assert artifact_path.suffix.lower() == ".step"
    assert artifact_path.is_file()

    native_save = await tools["solidworks_save_model"](
        SaveModelRequest(
            session_id=session_id,
            document_id=document_id,
            artifact_name="mcp_typed_mvp_bracket_native",
        )
    )
    assert native_save.status.value == "succeeded", native_save
    native_path = Path(native_save.data["artifact"]["path"])
    assert native_path.suffix.lower() == ".sldprt"
    assert native_path.is_file()

    assembly = await tools["solidworks_create_assembly"](
        CreateAssemblyRequest(session_id=session_id, name="MCP_Typed_MVP_Assembly")
    )
    assert assembly.status.value == "succeeded", assembly
    assert assembly.data["document"]["document_type"] == "assembly"
    assembly_document_id = assembly.data["document"]["document_id"]

    inserted_component = await tools["solidworks_insert_assembly_component"](
        InsertAssemblyComponentRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            source_part_document_id=document_id,
            x=20,
        )
    )
    assert inserted_component.status.value == "succeeded", inserted_component
    assert inserted_component.data["component"]["document_type"] == "part"
    assert inserted_component.data["document"]["revision"] == 2

    components = await tools["solidworks_list_assembly_components"](
        ListAssemblyComponentsRequest(
            session_id=session_id,
            document_id=assembly_document_id,
        )
    )
    assert components.status.value == "succeeded", components
    assert components.data["total"] >= 1

    drawing = await tools["solidworks_create_drawing"](
        CreateDrawingRequest(session_id=session_id, name="MCP_Typed_MVP_Drawing")
    )
    assert drawing.status.value == "succeeded", drawing
    assert drawing.data["document"]["document_type"] == "drawing"
    drawing_document_id = drawing.data["document"]["document_id"]

    drawing_view = await tools["solidworks_create_drawing_view"](
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=document_id,
            orientation="isometric",
            x=100,
            y=100,
        )
    )
    assert drawing_view.status.value == "succeeded", drawing_view
    assert drawing_view.data["view"]["view_id"]
    assert drawing_view.data["document"]["revision"] == 2

    projected_drawing_view = await tools["solidworks_create_projected_drawing_view"](
        CreateProjectedDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_view_id=drawing_view.data["view"]["view_id"],
            x=150,
            y=100,
        )
    )
    if projected_drawing_view.status.value == "succeeded":
        assert projected_drawing_view.data["view"]["view_id"]
    else:
        assert projected_drawing_view.status.value == "failed", projected_drawing_view
        assert projected_drawing_view.error is not None
        assert projected_drawing_view.error.code == "SW_PROJECTED_VIEW_UNSUPPORTED"

    drawing_views = await tools["solidworks_list_drawing_views"](
        ListDrawingViewsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
        )
    )
    assert drawing_views.status.value == "succeeded", drawing_views
    assert drawing_views.data["total"] >= 1

    closed = await tools["solidworks_close_session"](
        SessionRequest(session_id=session_id)
    )
    assert closed.status.value == "succeeded", closed


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_part_create_save_open_close(
    real_server: SolidWorksMCPServer,
    integration_output_dir: Path,
) -> None:
    """Create a part, save it, reopen it, and save again via real tools."""
    create_part = await _find_tool(real_server, "create_part")
    save_as = await _find_tool(real_server, "save_as")
    open_model = await _find_tool(real_server, "open_model")
    save_file = await _find_tool(real_server, "save_file")
    close_model = await _find_tool(real_server, "close_model")

    part_result = await create_part(CreatePartInput(name="MCP_Integration_Part"))
    assert part_result["status"] == "success", part_result

    property_result = await real_server.adapter.set_custom_property(
        "MCP_Closeout_Verification", "created-modified-saved"
    )
    assert property_result.is_success, property_result

    part_path = integration_output_dir / "mcp_integration_part.sldprt"
    save_as_result = await save_as(
        SaveAsInput(
            file_path=str(part_path),
            format_type="solidworks",
            overwrite=True,
        )
    )
    assert save_as_result["status"] == "success", save_as_result
    assert part_path.exists(), f"Expected saved part at {part_path}"

    close_result = await close_model(CloseModelInput(save=False))
    assert close_result["status"] == "success", close_result

    open_result = await open_model(OpenModelInput(file_path=str(part_path)))
    assert open_result["status"] == "success", open_result

    properties = await real_server.adapter.list_custom_properties()
    assert properties.is_success, properties
    assert any(
        item["name"] == "MCP_Closeout_Verification"
        and item["value"] == "created-modified-saved"
        for item in properties.data or []
    ), properties

    save_result = await save_file(SaveFileInput(force_save=True))
    assert save_result["status"] == "success", save_result

    final_close = await close_model(CloseModelInput(save=True))
    assert final_close["status"] == "success", final_close


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_assembly_create_and_save(
    real_server: SolidWorksMCPServer,
    integration_output_dir: Path,
) -> None:
    """Create and save a real SolidWorks assembly document."""
    create_assembly = await _find_tool(real_server, "create_assembly")
    save_as = await _find_tool(real_server, "save_as")
    close_model = await _find_tool(real_server, "close_model")

    assembly_result = await create_assembly(
        CreateAssemblyInput(name="MCP_Integration_Assembly")
    )
    assert assembly_result["status"] == "success", assembly_result

    asm_path = integration_output_dir / "mcp_integration_assembly.sldasm"
    save_as_result = await save_as(
        SaveAsInput(
            file_path=str(asm_path),
            format_type="solidworks",
            overwrite=True,
        )
    )
    assert save_as_result["status"] == "success", save_as_result
    assert asm_path.exists(), f"Expected saved assembly at {asm_path}"

    close_result = await close_model(CloseModelInput(save=False))
    assert close_result["status"] in {"success", "error"}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_cross_category_minimal_smoke(
    real_server: SolidWorksMCPServer,
    integration_output_dir: Path,
) -> None:
    """Run a deterministic low-risk workflow touching multiple tool categories."""
    create_part = await _find_tool(real_server, "create_part")
    save_as = await _find_tool(real_server, "save_as")
    open_model = await _find_tool(real_server, "open_model")
    save_file = await _find_tool(real_server, "save_file")
    close_model = await _find_tool(real_server, "close_model")

    part_result = await create_part(CreatePartInput(name="MCP_CrossCategory_Smoke"))
    assert part_result["status"] == "success", part_result

    smoke_part_path = integration_output_dir / "mcp_cross_category_smoke.sldprt"
    save_as_result = await save_as(
        SaveAsInput(
            file_path=str(smoke_part_path),
            format_type="solidworks",
            overwrite=True,
        )
    )
    assert save_as_result["status"] == "success", save_as_result
    assert smoke_part_path.exists(), f"Expected saved part at {smoke_part_path}"

    close_intermediate = await close_model(CloseModelInput(save=False))
    assert close_intermediate["status"] in {"success", "error"}

    reopen_result = await open_model(OpenModelInput(file_path=str(smoke_part_path)))
    assert reopen_result["status"] == "success", reopen_result

    save_result = await save_file(SaveFileInput(force_save=True))
    assert save_result["status"] == "success", save_result

    close_result = await close_model(CloseModelInput(save=True))
    assert close_result["status"] in {"success", "error"}


@pytest.mark.skipif(
    not _real_solidworks_enabled(),
    reason="Real SolidWorks integration disabled (set SOLIDWORKS_MCP_RUN_REAL_INTEGRATION=true)",
)
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_load_save_lifecycle(
    real_server: SolidWorksMCPServer,
    integration_output_dir: Path,
) -> None:
    """Test comprehensive load/save/open lifecycle with real SolidWorks."""
    create_part = await _find_tool(real_server, "create_part")
    save_part = await _find_tool(real_server, "save_part")
    load_part = await _find_tool(real_server, "load_part")
    close_model = await _find_tool(real_server, "close_model")

    # Step 1: Create a new part
    part_result = await create_part(CreatePartInput(name="MCP_LoadSave_Lifecycle"))
    assert part_result["status"] == "success", part_result

    # Step 2: Save the part using save_part convenience tool
    lifecycle_part_path = integration_output_dir / "mcp_load_save_lifecycle.sldprt"
    save_result = await save_part(
        {
            "file_path": str(lifecycle_part_path),
            "overwrite": True,
        }
    )
    assert save_result["status"] == "success", save_result
    assert lifecycle_part_path.exists(), f"Expected saved part at {lifecycle_part_path}"

    # Step 3: Close the part
    close_result = await close_model(CloseModelInput(save=False))
    assert close_result["status"] in {"success", "error"}

    # Step 4: Verify the file exists and load it using load_part convenience tool
    assert lifecycle_part_path.exists(), (
        f"Part file should exist at {lifecycle_part_path}"
    )
    load_result = await load_part({"file_path": str(lifecycle_part_path)})
    assert load_result["status"] == "success", load_result
    assert load_result["model"]["type"] == "Part", "Loaded model should be a Part"
    assert load_result["model"]["name"] is not None, "Model should have a name"

    # Step 5: Save again using save_part (without path, should save to current location)
    save_again_result = await save_part({})
    assert save_again_result["status"] == "success", save_again_result

    # Step 6: Close the reloaded part
    close_final_result = await close_model(CloseModelInput(save=True))
    assert close_final_result["status"] in {"success", "error"}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.windows_only
@pytest.mark.solidworks_only
async def test_real_arc_measurements_smoke(
    real_server: SolidWorksMCPServer,
    integration_output_dir: Path,
) -> None:
    """Smoke test: create a sketch with arcs/circles and dimension them without dialog.

    Covers radial dimensions on arcs and circles.  The preference toggle
    swInputDimValOnCreate (62) must remain False for the full dimension
    operation (including SetSystemValue3) so the Modify dialog never appears.

    This test intentionally relies on the popup-safe adapter path: direct COM
    dimension creation with minimal extra COM chatter.  Regressions here tend
    to come from reintroducing AddDimension2 probing or non-essential cleanup
    calls around dimension creation/teardown.
    """
    create_part = await _find_tool(real_server, "create_part")
    save_as = await _find_tool(real_server, "save_as")
    create_sketch = await _find_tool(real_server, "create_sketch")
    add_circle = await _find_tool(real_server, "add_circle")
    add_arc = await _find_tool(real_server, "add_arc")
    add_sketch_dimension = await _find_tool(real_server, "add_sketch_dimension")
    exit_sketch = await _find_tool(real_server, "exit_sketch")
    close_model = await _find_tool(real_server, "close_model")

    # Create a fresh part
    part_result = await create_part(CreatePartInput(name="MCP_ArcMeasurements_Smoke"))
    assert part_result["status"] == "success", part_result

    # Open a sketch on the Front plane
    sketch_result = await create_sketch({"plane": "Front"})
    assert sketch_result["status"] == "success", sketch_result

    # Add a circle (radius 20 mm, centred at origin)
    circle_result = await add_circle({"center_x": 0.0, "center_y": 0.0, "radius": 20.0})
    assert circle_result["status"] == "success", circle_result
    circle_entity = circle_result["circle"]["id"]

    # Add a 90-degree arc (quarter circle, radius 15 mm)
    # Start at (15, 0), end at (0, 15), centre at origin
    arc_result = await add_arc(
        {
            "center_x": 0.0,
            "center_y": 0.0,
            "start_x": 15.0,
            "start_y": 0.0,
            "end_x": 0.0,
            "end_y": 15.0,
        }
    )
    assert arc_result["status"] == "success", arc_result
    arc_entity = arc_result["arc"]["id"]

    # Dimension the circle radius (radial)
    circle_dim_result = await add_sketch_dimension(
        {
            "entity1": circle_entity,
            "dimension_type": "radial",
            "value": 20.0,
        }
    )
    assert circle_dim_result["status"] == "success", (
        f"Circle radial dimension failed: {circle_dim_result}"
    )
    assert circle_dim_result.get("dimension") is not None, (
        "Circle radial dimension returned no data"
    )

    # Dimension the arc radius (radial)
    arc_dim_result = await add_sketch_dimension(
        {
            "entity1": arc_entity,
            "dimension_type": "radial",
            "value": 15.0,
        }
    )
    assert arc_dim_result["status"] == "success", (
        f"Arc radial dimension failed: {arc_dim_result}"
    )
    assert arc_dim_result.get("dimension") is not None, (
        "Arc radial dimension returned no data"
    )

    # Exit sketch
    exit_result = await exit_sketch()
    assert exit_result["status"] == "success", exit_result

    # Save and close
    part_path = integration_output_dir / "mcp_arc_measurements_smoke.sldprt"
    save_as_result = await save_as(
        SaveAsInput(file_path=str(part_path), format_type="solidworks", overwrite=True)
    )
    assert save_as_result["status"] == "success", save_as_result

    close_result = await close_model(CloseModelInput(save=False))
    assert close_result["status"] in {"success", "error"}
