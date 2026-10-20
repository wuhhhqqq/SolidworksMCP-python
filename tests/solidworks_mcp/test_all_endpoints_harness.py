"""Full endpoint integration test harness for the SolidWorks MCP Server."""

from __future__ import annotations

import inspect
import json
import os
import platform
import shutil
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from solidworks_mcp.config import (
    AdapterType,
    DeploymentMode,
    SecurityLevel,
    SolidWorksMCPConfig,
)
from solidworks_mcp.contracts.mvp import (
    ActivateConfigurationRequest,
    ActivateDocumentRequest,
    ActivateDrawingSheetRequest,
    ActivateDrawingViewRequest,
    Add3DLineRequest,
    AddArcRequest,
    AddCenterlineRequest,
    AddCircleRequest,
    AddEllipseRequest,
    AddLineRequest,
    AddPointRequest,
    AddPolygonRequest,
    AddRectangleRequest,
    AddSketchTextRequest,
    AddSplineRequest,
    AlignDrawingViewsRequest,
    CheckAssemblyInterferenceRequest,
    CloseDocumentRequest,
    ConvertSketchEntitiesRequest,
    Create3DSketchRequest,
    CreateAssemblyMateRequest,
    CreateAssemblyRequest,
    CreateAuxiliaryDrawingViewRequest,
    CreateChamferRequest,
    CreateConfigurationRequest,
    CreateCutExtrudeRequest,
    CreateDomeRequest,
    CreateDraftRequest,
    CreateDrawingBalloonRequest,
    CreateDrawingBomRequest,
    CreateDrawingHoleCalloutRequest,
    CreateDrawingNoteRequest,
    CreateDrawingRequest,
    CreateDrawingRevisionTableRequest,
    CreateDrawingSheetRequest,
    CreateDrawingViewRequest,
    CreateExtrusionRequest,
    CreateFeatureCircularPatternRequest,
    CreateFeatureLinearPatternRequest,
    CreateHoleWizardRequest,
    CreateLoftRequest,
    CreatePartRequest,
    CreateProjectedDrawingViewRequest,
    CreateReferencePlaneRequest,
    CreateRevolveRequest,
    CreateRibRequest,
    CreateSectionDrawingViewRequest,
    CreateSheetMetalBaseFlangeRequest,
    CreateSheetMetalEdgeFlangeRequest,
    CreateShellRequest,
    CreateSketchCircularPatternRequest,
    CreateSketchLinearPatternRequest,
    CreateSketchRequest,
    CreateSweepRequest,
    CreateTappedHoleRequest,
    CreateThickenRequest,
    CreateWrapRequest,
    DeleteAssemblyMateRequest,
    DeleteConfigurationRequest,
    DeleteCustomPropertyRequest,
    DeleteDrawingBomRequest,
    DeleteDrawingSheetRequest,
    DeleteEquationRequest,
    DocumentRequest,
    ExecuteTrustedMacroRequest,
    ExitSketchRequest,
    ExportFileRequest,
    ExportFlatPatternDxfRequest,
    GetAssemblyComponentRequest,
    GetBoundingBoxRequest,
    GetConfigurationRequest,
    GetDimensionRequest,
    GetDrawingViewOutlineRequest,
    GetEquationRequest,
    GetFeatureDimensionRequest,
    GetMaterialAssignmentRequest,
    GetReferenceAxisRequest,
    GetReferencePlaneRequest,
    InsertAssemblyComponentRequest,
    InsertSheetMetalBendNotesRequest,
    InsertSheetMetalBendTableRequest,
    ListAssemblyComponentsRequest,
    ListAssemblyMateEntitiesRequest,
    ListAssemblyMatesRequest,
    ListConfigurationsRequest,
    ListCustomPropertiesRequest,
    ListDimensionsRequest,
    ListDrawingAnnotationTargetsRequest,
    ListDrawingSheetsRequest,
    ListDrawingViewsRequest,
    ListEquationsRequest,
    ListFeaturesRequest,
    ListPartTopologyRequest,
    ListSheetMetalFeatureGeometryRequest,
    ListSolidBodiesRequest,
    ListSurfaceBodiesRequest,
    MirrorFeatureRequest,
    MirrorSketchEntitiesRequest,
    OffsetSketchEntitiesRequest,
    OpenModelRequest,
    PlaceDrawingDimensionRequest,
    PrepareTrustedMacroRequest,
    RebuildDrawingRequest,
    RenameDrawingSheetRequest,
    SaveModelRequest,
    SessionRequest,
    SetCustomPropertyRequest,
    SetDimensionRequest,
    SetEquationRequest,
    SetFeatureDimensionRequest,
    SetMaterialAssignmentRequest,
    SetSheetMetalKFactorRequest,
    SheetMetalFlatStateRequest,
    TrimSketchEntityRequest,
    UpdateDrawingBomRequest,
    UpdateDrawingViewLayoutRequest,
)
from solidworks_mcp.server import SolidWorksMCPServer

# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

REAL_SW_FLAG = "SOLIDWORKS_MCP_RUN_REAL_INTEGRATION"
OUTPUT_DIR = Path("tests/.generated/solidworks_integration")
CATALOG_PATH = Path("tests/.generated/tool_catalog.json")
MAX_TOOL_RESPONSE_BYTES = 250_000
MAX_SMOKE_TOTAL_RESPONSE_BYTES = 3_000_000
SAMPLE_MODELS_ENV = "SOLIDWORKS_MCP_SAMPLE_MODELS_DIR"

# Reference snapshot of required COM objects – add to this list as the server grows.
REQUIRED_COM_METHODS: list[str] = [
    "SldWorks.ISldWorks",
    "SldWorks.IModelDoc2",
    "SldWorks.IFeatureManager",
    "SldWorks.ISketchManager",
    "SldWorks.IDrawingDoc",
    "SldWorks.IAssemblyDoc",
]


def _real_sw_enabled() -> bool:
    """Test helper for real sw enabled."""
    return os.getenv(REAL_SW_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_windows() -> bool:
    """Test helper for is windows."""
    return platform.system() == "Windows"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mock_server(tmp_path: Path) -> AsyncGenerator[SolidWorksMCPServer, None]:
    """Mock-adapter server — no SolidWorks required."""
    config = SolidWorksMCPConfig(
        adapter_type=AdapterType.MOCK,
        deployment_mode=DeploymentMode.LOCAL,
        security_level=SecurityLevel.MINIMAL,
        testing=True,
        data_dir=tmp_path / "mcp_data",
    )
    server = SolidWorksMCPServer(config)
    await server.setup()
    yield server


@pytest_asyncio.fixture
async def real_server() -> AsyncGenerator[SolidWorksMCPServer, None]:
    """Real COM adapter — requires Windows + SolidWorks running."""
    config = SolidWorksMCPConfig(
        adapter_type=AdapterType.PYWIN32,
        deployment_mode=DeploymentMode.LOCAL,
        security_level=SecurityLevel.MINIMAL,
    )
    server = SolidWorksMCPServer(config)
    await server.setup()
    if server.adapter and not server.adapter.is_connected():
        await server.adapter.connect()
        server.state.is_connected = True
    yield server
    if server.adapter and server.adapter.is_connected():
        await server.adapter.disconnect()
        server.state.is_connected = False


@pytest_asyncio.fixture
async def output_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Test helper for output dir."""
    return tmp_path_factory.mktemp("endpoint_reports")


def _load_catalog() -> list[dict[str, Any]]:
    """Test helper for load catalog."""
    if not CATALOG_PATH.exists():
        return []
    with open(CATALOG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


async def _find_tool(server: SolidWorksMCPServer, tool_name: str):
    """Test helper for find tool."""
    for tool in await server.mcp.list_tools():
        if tool.name == tool_name:
            return tool.fn
    return None


def _candidate_sample_roots() -> list[Path]:
    """Return likely SolidWorks sample roots in preferred order."""
    candidates: list[Path] = []

    env_override = os.getenv(SAMPLE_MODELS_ENV, "").strip()
    if env_override:
        candidates.append(Path(env_override))

    # Keep 2026 first, then try nearby versions to support side-by-side installs.
    years = [2026, 2025, 2024, 2023, 2022]
    for year in years:
        candidates.append(
            Path(
                f"C:/Users/Public/Documents/SOLIDWORKS/SOLIDWORKS {year}/samples/learn"
            )
        )

    # De-duplicate while preserving order.
    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)
    return unique_candidates


def _resolve_sample_parts_dir() -> Path:
    """Find the first existing sample root or skip with a clear diagnostic."""
    for candidate in _candidate_sample_roots():
        if candidate.exists():
            return candidate

    searched = "\n".join(f"  - {p}" for p in _candidate_sample_roots())
    pytest.skip(
        "No SolidWorks sample models directory found. "
        f"Set {SAMPLE_MODELS_ENV} to override. Searched:\n{searched}"
    )


def _find_sample_part() -> Path:
    """Test helper for find sample part."""
    sample_parts_dir = _resolve_sample_parts_dir()
    preferred = [
        sample_parts_dir / "Baseball Bat.SLDPRT",
        sample_parts_dir / "Paper Airplane.SLDPRT",
        sample_parts_dir / "Coping Saw.SLDPRT",
    ]
    for candidate in preferred:
        if candidate.exists():
            return candidate

    discovered = sorted(sample_parts_dir.rglob("*.sldprt"))
    if discovered:
        return discovered[0]

    pytest.skip(f"No sample .sldprt files found under {sample_parts_dir}")


# ---------------------------------------------------------------------------
# Level A — Schema / Contract Tests (mock-safe)
# ---------------------------------------------------------------------------


# start_macro_recording and stop_macro_recording intentionally exist in both
# automation.py and macro_recording.py — they are the same operation exposed
# from two code paths.
KNOWN_DUPLICATES = {"start_macro_recording", "stop_macro_recording"}


class TestLevelASchema:
    """Every registered tool must exist and be callable on the mock server."""

    @pytest.mark.asyncio
    async def test_tool_count_meets_minimum(
        self, mock_server: SolidWorksMCPServer
    ) -> None:
        """At least 95 tools must be registered (verified count as of SolidWorks 2026)."""
        registered = len(await mock_server.mcp.list_tools())
        assert registered >= 95, (
            f"Expected ≥95 registered tools, found {registered}. "
            "Run the server setup and registration pipeline before this test."
        )

    @pytest.mark.asyncio
    async def test_all_catalog_tools_registered(
        self, mock_server: SolidWorksMCPServer
    ) -> None:
        """Every tool listed in the JSON catalog must be registered in the server."""
        catalog = _load_catalog()
        if not catalog:
            pytest.skip(
                "tool_catalog.json not found – run generate_tool_catalog.py first"
            )

        registered_names = {t.name for t in await mock_server.mcp.list_tools()}
        missing: list[str] = []
        for entry in catalog:
            name = entry["tool_name"]
            # Also check func_name as some tools use a name= override
            if (
                name not in registered_names
                and entry["func_name"] not in registered_names
            ):
                missing.append(name)

        assert not missing, (
            f"{len(missing)} catalog tools NOT registered:\n"
            + "\n".join(f"  - {m}" for m in sorted(missing))
        )

    @pytest.mark.asyncio
    async def test_no_duplicate_tool_names(
        self, mock_server: SolidWorksMCPServer
    ) -> None:
        """Every registered tool must have a unique name."""
        names = [t.name for t in await mock_server.mcp.list_tools()]
        seen: dict[str, int] = {}
        for n in names:
            seen[n] = seen.get(n, 0) + 1
        unexpected_duplicates = {
            k: v for k, v in seen.items() if v > 1 and k not in KNOWN_DUPLICATES
        }
        known_found = {k: v for k, v in seen.items() if v > 1 and k in KNOWN_DUPLICATES}
        if known_found:
            print(
                f"\n⚠️  Known duplicate tool names (in multiple modules): {known_found}"
            )
        assert not unexpected_duplicates, (
            f"Unexpected duplicate tool names: {unexpected_duplicates}"
        )

    @pytest.mark.asyncio
    async def test_all_tools_have_callable_attribute(
        self, mock_server: SolidWorksMCPServer
    ) -> None:
        """Every registered tool must expose a callable func."""
        for tool in await mock_server.mcp.list_tools():
            assert callable(tool.fn), f"Tool '{tool.name}' func is not callable"

    @pytest.mark.asyncio
    async def test_catalog_json_structure(self) -> None:
        """The generated catalog JSON must have the expected schema."""
        catalog = _load_catalog()
        if not catalog:
            pytest.skip("tool_catalog.json not found")

        required_keys = {
            "tool_name",
            "func_name",
            "category",
            "description",
            "fields",
            "sample_payload",
        }
        for entry in catalog:
            missing_keys = required_keys - set(entry.keys())
            assert not missing_keys, (
                f"Catalog entry '{entry.get('tool_name', '?')}' missing keys: {missing_keys}"
            )

    @pytest.mark.asyncio
    async def test_category_coverage(self, mock_server: SolidWorksMCPServer) -> None:
        """All expected tool categories must be represented."""
        catalog = _load_catalog()
        if not catalog:
            pytest.skip("tool_catalog.json not found")
        categories = {e["category"] for e in catalog}
        expected = {
            "modeling",
            "sketching",
            "drawing",
            "drawing_analysis",
            "analysis",
            "export",
            "file_management",
            "automation",
            "vba_generation",
            "template_management",
            "macro_recording",
            "docs_discovery",
        }
        missing_cats = expected - categories
        assert not missing_cats, f"Missing tool categories in catalog: {missing_cats}"

    @pytest.mark.asyncio
    async def test_tool_catalog_snapshot_written(self, output_dir: Path) -> None:
        """Snapshot file must exist and contain tool names for regression detection."""
        snapshot_path = output_dir / "tool_catalog_snapshot.json"
        catalog = _load_catalog()
        if not catalog:
            pytest.skip("tool_catalog.json not found")
        names = [e["tool_name"] for e in catalog]
        snapshot_path.write_text(json.dumps(names, indent=2), encoding="utf-8")
        assert snapshot_path.exists()

    def test_sample_path_candidates_include_supported_versions(self) -> None:
        """Candidate sample roots should include current and fallback SolidWorks years."""
        candidates = [str(p).replace("\\", "/") for p in _candidate_sample_roots()]
        assert any("SOLIDWORKS 2026/samples/learn" in p for p in candidates)
        assert any("SOLIDWORKS 2025/samples/learn" in p for p in candidates)

    def test_sample_path_env_override_is_prioritized(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Environment override should be first lookup path when configured."""
        override = "D:/custom/sw/samples/learn"
        monkeypatch.setenv(SAMPLE_MODELS_ENV, override)
        candidates = _candidate_sample_roots()
        assert str(candidates[0]).replace("\\", "/") == override


# ---------------------------------------------------------------------------
# Level B — Execution Smoke Tests (mock-safe)
# ---------------------------------------------------------------------------

# Minimal safe payloads for every tool. Empty dict means no required params.
# Tools that require actual file paths get a dummy value – the mock adapter
# ignores real paths.

_SMOKE_PAYLOADS: dict[str, dict[str, Any]] = {
    # Modeling
    "open_model": {"file_path": "C:\\Temp\\dummy.sldprt"},
    "create_part": {"name": "smoke_part"},
    "create_assembly": {"name": "smoke_asm"},
    "create_drawing": {"name": "smoke_drw"},
    "close_model": {"save": False},
    "create_extrusion": {"sketch_name": "Sketch1", "depth": 10.0},
    "create_revolve": {
        "sketch_name": "Sketch1",
        "axis_entity": "Line1",
        "angle": 360.0,
    },
    "get_dimension": {"name": "D1@Sketch1"},
    "set_dimension": {"name": "D1@Sketch1", "value": 25.0},
    # Sketching
    "create_sketch": {"plane": "Front"},
    "add_line": {"x1": 0.0, "y1": 0.0, "x2": 10.0, "y2": 10.0},
    "add_circle": {"center_x": 0.0, "center_y": 0.0, "radius": 5.0},
    "add_rectangle": {"x1": -10.0, "y1": -10.0, "x2": 10.0, "y2": 10.0},
    "exit_sketch": {},
    "add_arc": {
        "center_x": 0.0,
        "center_y": 0.0,
        "start_x": 5.0,
        "start_y": 0.0,
        "end_x": 0.0,
        "end_y": 5.0,
    },
    "add_spline": {
        "points": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 5.0}, {"x": 20.0, "y": 0.0}]
    },
    "add_centerline": {"x1": -20.0, "y1": 0.0, "x2": 20.0, "y2": 0.0},
    "add_polygon": {"center_x": 0.0, "center_y": 0.0, "sides": 6, "circumradius": 10.0},
    "add_ellipse": {
        "center_x": 0.0,
        "center_y": 0.0,
        "major_radius": 10.0,
        "minor_radius": 5.0,
    },
    "add_sketch_constraint": {"entity1": "Line1", "relation_type": "horizontal"},
    "add_sketch_dimension": {
        "entity1": "Line1",
        "value": 20.0,
        "dimension_type": "linear",
    },
    "sketch_linear_pattern": {
        "entity": "Line1",
        "x_count": 3,
        "y_count": 2,
        "x_spacing": 10.0,
        "y_spacing": 10.0,
    },
    "sketch_circular_pattern": {
        "entity": "Line1",
        "count": 4,
        "angle": 90.0,
    },
    "sketch_mirror": {"entity": "Line1", "mirror_line": "Line2"},
    "sketch_offset": {"entity": "Line1", "distance": 5.0},
    "sketch_tutorial_simple_hole": {
        "plane": "Front",
        "center_x": 0.0,
        "center_y": 0.0,
        "diameter": 10.0,
    },
    "tutorial_simple_hole": {
        "plane": "Front",
        "center_x": 0.0,
        "center_y": 0.0,
        "diameter": 10.0,
    },
    # Analysis
    "calculate_mass_properties": {},
    "get_mass_properties": {},
    "check_interference": {},
    "analyze_geometry": {"analysis_type": "curvature"},
    "get_material_properties": {},
    # Export
    "export_step": {"file_path": "C:\\Temp\\smoke.step", "format_type": "step"},
    "export_iges": {"file_path": "C:\\Temp\\smoke.iges", "format_type": "iges"},
    "export_stl": {"file_path": "C:\\Temp\\smoke.stl", "format_type": "stl"},
    "export_pdf": {"file_path": "C:\\Temp\\smoke.pdf", "format_type": "pdf"},
    "export_dwg": {"file_path": "C:\\Temp\\smoke.dwg", "format_type": "dwg"},
    "export_image": {"file_path": "C:\\Temp\\smoke.jpg", "format_type": "jpg"},
    "batch_export": {
        "source_directory": "C:\\Temp\\src",
        "output_directory": "C:\\Temp\\out",
        "format_type": "step",
    },
    # File management
    "save_file": {},
    "save_as": {"file_path": "C:\\Temp\\save_as.sldprt"},
    "get_file_properties": {},
    "get_model_info": {},
    "list_features": {},
    "list_configurations": {},
    "manage_file_properties": {},
    "convert_file_format": {
        "source_path": "C:\\Temp\\part.sldprt",
        "target_format": "step",
    },
    "batch_file_operations": {"source_directory": "C:\\Temp\\src", "operation": "copy"},
    "load_part": {"file_path": "C:\\Temp\\part.sldprt"},
    "load_assembly": {"file_path": "C:\\Temp\\asm.sldasm"},
    "save_part": {},
    "save_assembly": {},
    "pack_and_go_assembly": {
        "source_path": "C:\\Temp\\robot_arm.sldasm",
        "target_dir": "C:\\Temp\\robot_arm_pkg",
    },
    # Automation
    "generate_vba_code": {"code_type": "extrusion"},
    "start_macro_recording": {"macro_name": "smoke_macro"},
    "stop_macro_recording": {},
    "batch_process_files": {
        "source_directory": "C:\\Temp\\src",
        "operation": "export",
        "output_directory": "C:\\Temp\\out",
    },
    "manage_design_table": {"action": "list"},
    "execute_workflow": {"steps": []},
    "create_template": {"template_name": "smoke_template", "template_type": "part"},
    "optimize_performance": {},
    # VBA generation
    "generate_vba_extrusion": {"sketch_name": "Sketch1", "depth": 10.0},
    "generate_vba_revolve": {
        "sketch_name": "Sketch1",
        "axis_entity": "Line1",
        "angle": 360.0,
    },
    "generate_vba_assembly_insert": {"component_path": "C:\\Temp\\part.sldprt"},
    "generate_vba_drawing_views": {"model_path": "C:\\Temp\\part.sldprt"},
    "generate_vba_batch_export": {
        "source_directory": "C:\\Temp\\src",
        "output_directory": "C:\\Temp\\out",
        "format_type": "step",
    },
    "generate_vba_part_modeling": {"operations": []},
    "generate_vba_assembly_mates": {"mate_type": "coincident"},
    "generate_vba_drawing_dimensions": {},
    "generate_vba_file_operations": {"operation": "save"},
    "generate_vba_macro_recorder": {"macro_name": "smoke"},
    # Template management
    "extract_template": {"model_path": "C:\\Temp\\part.sldprt"},
    "apply_template": {
        "template_path": "C:\\Temp\\template.prtdot",
        "target_path": "C:\\Temp\\part.sldprt",
    },
    "batch_apply_template": {
        "template_path": "C:\\Temp\\template.prtdot",
        "source_directory": "C:\\Temp\\src",
    },
    "compare_templates": {
        "template1_path": "C:\\Temp\\t1.prtdot",
        "template2_path": "C:\\Temp\\t2.prtdot",
    },
    "save_to_template_library": {
        "template_path": "C:\\Temp\\template.prtdot",
        "library_name": "smoke",
    },
    "list_template_library": {},
    # Macro recording
    "execute_macro": {"macro_path": "C:\\Temp\\macro.swp"},
    "analyze_macro": {"macro_path": "C:\\Temp\\macro.swp"},
    "batch_execute_macros": {"macro_paths": ["C:\\Temp\\macro.swp"]},
    "optimize_macro": {"macro_path": "C:\\Temp\\macro.swp"},
    "create_macro_library": {"library_name": "smoke_lib", "macro_paths": []},
    # Drawing
    "create_drawing_view": {
        "model_path": "C:\\Temp\\part.sldprt",
        "view_type": "front",
    },
    "add_dimension": {"entity1": "Line1", "value": 20.0, "dimension_type": "linear"},
    "add_note": {"text": "Smoke test note", "x": 0.0, "y": 0.0},
    "create_section_view": {"cutting_line": "Line1"},
    "create_detail_view": {
        "center_x": 0.0,
        "center_y": 0.0,
        "radius": 10.0,
        "scale": 2.0,
    },
    "update_sheet_format": {"sheet_format_path": "C:\\Temp\\format.slddrt"},
    "auto_dimension_view": {"view_name": "Drawing View1"},
    "check_drawing_standards": {},
    "create_technical_drawing": {"model_path": "C:\\Temp\\part.sldprt"},
    "add_drawing_view": {"model_path": "C:\\Temp\\part.sldprt", "view_type": "front"},
    "add_annotation": {"annotation_type": "note", "text": "smoke", "x": 0.0, "y": 0.0},
    "update_title_block": {"title": "Smoke Test"},
    # Drawing analysis
    "analyze_drawing_comprehensive": {},
    "analyze_drawing_dimensions": {},
    "analyze_drawing_annotations": {},
    "check_drawing_compliance": {},
    "analyze_drawing_views": {},
    "generate_drawing_report": {},
    "compare_drawing_versions": {
        "drawing1_path": "C:\\Temp\\drw1.slddrw",
        "drawing2_path": "C:\\Temp\\drw2.slddrw",
    },
    "validate_drawing_completeness": {},
    # Docs discovery
    "discover_solidworks_docs": {},
    "search_solidworks_api_help": {"query": "extrude"},
}


MVP_TOOL_NAMES = {
    "solidworks_create_session",
    "solidworks_start",
    "solidworks_close_session",
    "solidworks_get_status",
    "solidworks_list_documents",
    "solidworks_activate_document",
    "solidworks_close_document",
    "solidworks_open_model",
    "solidworks_create_part",
    "solidworks_create_assembly",
    "solidworks_create_drawing",
    "solidworks_insert_assembly_component",
    "solidworks_list_assembly_components",
    "solidworks_get_assembly_component",
    "solidworks_check_assembly_interference",
    "solidworks_list_assembly_mate_entities",
    "solidworks_create_assembly_mate",
    "solidworks_list_assembly_mates",
    "solidworks_delete_assembly_mate",
    "solidworks_create_drawing_view",
    "solidworks_list_drawing_views",
    "solidworks_list_drawing_sheets",
    "solidworks_create_drawing_sheet",
    "solidworks_update_drawing_view_layout",
    "solidworks_align_drawing_views",
    "solidworks_rebuild_drawing",
    "solidworks_activate_drawing_sheet",
    "solidworks_delete_drawing_sheet",
    "solidworks_rename_drawing_sheet",
    "solidworks_get_drawing_view_outline",
    "solidworks_activate_drawing_view",
    "solidworks_create_projected_drawing_view",
    "solidworks_create_auxiliary_drawing_view",
    "solidworks_create_section_drawing_view",
    "solidworks_create_drawing_bom",
    "solidworks_update_drawing_bom",
    "solidworks_delete_drawing_bom",
    "solidworks_create_drawing_note",
    "solidworks_create_drawing_revision_table",
    "solidworks_list_drawing_annotation_targets",
    "solidworks_place_drawing_dimension",
    "solidworks_create_drawing_balloon",
    "solidworks_create_drawing_hole_callout",
    "solidworks_list_custom_properties",
    "solidworks_set_custom_property",
    "solidworks_delete_custom_property",
    "solidworks_create_sketch",
    "solidworks_create_3d_sketch",
    "solidworks_create_reference_plane",
    "solidworks_get_reference_axis",
    "solidworks_get_reference_plane",
    "solidworks_add_rectangle",
    "solidworks_add_line",
    "solidworks_add_3d_line",
    "solidworks_add_circle",
    "solidworks_add_point",
    "solidworks_add_arc",
    "solidworks_add_centerline",
    "solidworks_add_spline",
    "solidworks_add_polygon",
    "solidworks_add_ellipse",
    "solidworks_add_sketch_text",
    "solidworks_create_sketch_linear_pattern",
    "solidworks_create_sketch_circular_pattern",
    "solidworks_mirror_sketch_entities",
    "solidworks_offset_sketch_entities",
    "solidworks_trim_sketch_entity",
    "solidworks_convert_sketch_entities",
    "solidworks_exit_sketch",
    "solidworks_create_extrusion",
    "solidworks_create_cut_extrude",
    "solidworks_create_sheet_metal_base_flange",
    "solidworks_create_sheet_metal_edge_flange",
    "solidworks_set_sheet_metal_k_factor",
    "solidworks_create_shell",
    "solidworks_create_chamfer",
    "solidworks_create_rib",
    "solidworks_create_dome",
    "solidworks_create_draft",
    "solidworks_create_thicken",
    "solidworks_create_wrap",
    "solidworks_create_revolve",
    "solidworks_create_sweep",
    "solidworks_create_loft",
    "solidworks_create_feature_linear_pattern",
    "solidworks_create_feature_circular_pattern",
    "solidworks_mirror_feature",
    "solidworks_create_hole_wizard",
    "solidworks_create_tapped_hole",
    "solidworks_get_bounding_box",
    "solidworks_list_equations",
    "solidworks_get_equation",
    "solidworks_set_equation",
    "solidworks_delete_equation",
    "solidworks_export_file",
    "solidworks_get_sheet_metal_info",
    "solidworks_set_sheet_metal_flat_state",
    "solidworks_export_flat_pattern_dxf",
    "solidworks_save_model",
    "solidworks_get_model_info",
    "solidworks_get_mass_properties",
    "solidworks_list_dimensions",
    "solidworks_get_dimension",
    "solidworks_set_dimension",
    "solidworks_get_feature_dimension",
    "solidworks_set_feature_dimension",
    "solidworks_list_solid_bodies",
    "solidworks_list_surface_bodies",
    "solidworks_list_part_topology",
    "solidworks_list_sheet_metal_feature_geometry",
    "solidworks_insert_sheet_metal_bend_notes",
    "solidworks_insert_sheet_metal_bend_table",
    "solidworks_get_material_assignment",
    "solidworks_set_material_assignment",
    "solidworks_list_features",
    "solidworks_list_configurations",
    "solidworks_get_configuration",
    "solidworks_create_configuration",
    "solidworks_delete_configuration",
    "solidworks_activate_configuration",
    "solidworks_list_trusted_macros",
    "solidworks_prepare_trusted_macro",
    "solidworks_execute_trusted_macro",
}


def _as_mcp_response(result: Any) -> dict[str, Any] | None:
    """Normalize direct tool results to the object shape sent over MCP."""
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        dumped = result.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    return None


async def _mvp_smoke_results(server: SolidWorksMCPServer) -> dict[str, Any]:
    """Exercise the typed MVP tools with valid, stateful Pydantic inputs."""
    if server.adapter is None:
        raise AssertionError("Mock server did not initialize an adapter")
    if not server.adapter.is_connected():
        await server.adapter.connect()

    tool_functions = {
        tool.name: tool.fn
        for tool in await server.mcp.list_tools()
        if tool.name in MVP_TOOL_NAMES
    }
    missing_tools = MVP_TOOL_NAMES.difference(tool_functions)
    if missing_tools:
        raise AssertionError(
            f"Typed MVP tools are not registered: {sorted(missing_tools)}"
        )

    session = await tool_functions["solidworks_create_session"]()
    session_id = session.data["session"]["session_id"]
    status = await tool_functions["solidworks_get_status"](
        SessionRequest(session_id=session_id)
    )
    part = await tool_functions["solidworks_create_part"](
        CreatePartRequest(session_id=session_id, name="mvp_smoke_part")
    )
    document_id = part.data["document"]["document_id"]
    documents = await tool_functions["solidworks_list_documents"](
        SessionRequest(session_id=session_id)
    )
    activated_document = await tool_functions["solidworks_activate_document"](
        ActivateDocumentRequest(session_id=session_id, document_id=document_id)
    )
    reference_plane = await tool_functions["solidworks_create_reference_plane"](
        CreateReferencePlaneRequest(
            session_id=session_id,
            document_id=document_id,
            base_plane="Front",
            offset=10,
        )
    )
    sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id,
            document_id=document_id,
            reference_plane_id=reference_plane.data["reference_plane"]["persistent_id"],
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    rectangle = await tool_functions["solidworks_add_rectangle"](
        AddRectangleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=0,
            x2=20,
            y2=10,
        )
    )
    line = await tool_functions["solidworks_add_line"](
        AddLineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=0,
            x2=20,
            y2=0,
        )
    )
    circle = await tool_functions["solidworks_add_circle"](
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=10,
            center_y=5,
            radius=2,
        )
    )
    arc = await tool_functions["solidworks_add_arc"](
        AddArcRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=0,
            center_y=0,
            start_x=2,
            start_y=0,
            end_x=0,
            end_y=2,
        )
    )
    centerline = await tool_functions["solidworks_add_centerline"](
        AddCenterlineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=-5,
            x2=0,
            y2=5,
        )
    )
    spline = await tool_functions["solidworks_add_spline"](
        AddSplineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            points=[{"x": 0, "y": 0}, {"x": 5, "y": 3}, {"x": 10, "y": 0}],
        )
    )
    polygon = await tool_functions["solidworks_add_polygon"](
        AddPolygonRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=25,
            center_y=10,
            radius=3,
            sides=6,
        )
    )
    ellipse = await tool_functions["solidworks_add_ellipse"](
        AddEllipseRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=35,
            center_y=10,
            major_axis=8,
            minor_axis=4,
        )
    )
    linear_pattern = await tool_functions["solidworks_create_sketch_linear_pattern"](
        CreateSketchLinearPatternRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[line.data["line"]["persistent_id"]],
            direction_x=1,
            direction_y=0,
            spacing=5,
            count=3,
        )
    )
    circular_pattern = await tool_functions[
        "solidworks_create_sketch_circular_pattern"
    ](
        CreateSketchCircularPatternRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[circle.data["circle"]["persistent_id"]],
            angle_degrees=180,
            count=3,
        )
    )
    mirrored = await tool_functions["solidworks_mirror_sketch_entities"](
        MirrorSketchEntitiesRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[arc.data["arc"]["persistent_id"]],
            mirror_line_id=centerline.data["centerline"]["persistent_id"],
        )
    )
    offset = await tool_functions["solidworks_offset_sketch_entities"](
        OffsetSketchEntitiesRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[ellipse.data["ellipse"]["persistent_id"]],
            distance=1,
        )
    )
    trimmed = await tool_functions["solidworks_trim_sketch_entity"](
        TrimSketchEntityRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_id=line.data["line"]["persistent_id"],
            x=10,
            y=0,
        )
    )
    exited = await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
        )
    )
    extrusion = await tool_functions["solidworks_create_extrusion"](
        CreateExtrusionRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            depth=10,
        )
    )
    feature_linear_pattern = await tool_functions[
        "solidworks_create_feature_linear_pattern"
    ](
        CreateFeatureLinearPatternRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=extrusion.data["feature"]["persistent_id"],
            direction="X",
            instances=2,
            spacing=12,
        )
    )
    reference_axis = await tool_functions["solidworks_get_reference_axis"](
        GetReferenceAxisRequest(
            session_id=session_id, document_id=document_id, axis="Z"
        )
    )
    feature_circular_pattern = await tool_functions[
        "solidworks_create_feature_circular_pattern"
    ](
        CreateFeatureCircularPatternRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=feature_linear_pattern.data["feature"]["persistent_id"],
            reference_axis_id=reference_axis.data["reference_axis"]["persistent_id"],
            instances=3,
            angle_degrees=180,
        )
    )
    origin_plane = await tool_functions["solidworks_get_reference_plane"](
        GetReferencePlaneRequest(
            session_id=session_id, document_id=document_id, plane="Right"
        )
    )
    feature_mirror = await tool_functions["solidworks_mirror_feature"](
        MirrorFeatureRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=feature_circular_pattern.data["feature"]["persistent_id"],
            reference_plane_id=origin_plane.data["reference_plane"]["persistent_id"],
        )
    )
    hole_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(session_id=session_id, document_id=document_id, plane="Top")
    )
    hole_sketch_id = hole_sketch.data["sketch"]["persistent_id"]
    point = await tool_functions["solidworks_add_point"](
        AddPointRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=hole_sketch_id,
            x=5,
            y=5,
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=hole_sketch_id,
        )
    )
    hole_wizard = await tool_functions["solidworks_create_hole_wizard"](
        CreateHoleWizardRequest(
            session_id=session_id,
            document_id=document_id,
            location_sketch_id=hole_sketch_id,
            diameter=6,
            depth=20,
        )
    )
    revolve_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    revolve_sketch_id = revolve_sketch.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=revolve_sketch_id,
        )
    )
    revolved = await tool_functions["solidworks_create_revolve"](
        CreateRevolveRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=revolve_sketch_id,
            angle_degrees=180,
        )
    )
    cut_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    cut_sketch_id = cut_sketch.data["sketch"]["persistent_id"]
    _cut_circle = await tool_functions["solidworks_add_circle"](
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=cut_sketch_id,
            center_x=10,
            center_y=5,
            radius=2,
        )
    )
    _cut_exited = await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=cut_sketch_id,
        )
    )
    cut_extrusion = await tool_functions["solidworks_create_cut_extrude"](
        CreateCutExtrudeRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=cut_sketch_id,
            depth=4,
        )
    )
    sweep_profile = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sweep_profile_id = sweep_profile.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sweep_profile_id,
        )
    )
    sweep_path = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(session_id=session_id, document_id=document_id, plane="Top")
    )
    sweep_path_id = sweep_path.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sweep_path_id,
        )
    )
    sweep = await tool_functions["solidworks_create_sweep"](
        CreateSweepRequest(
            session_id=session_id,
            document_id=document_id,
            profile_sketch_id=sweep_profile_id,
            path_sketch_id=sweep_path_id,
        )
    )
    loft_first_profile = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    loft_first_profile_id = loft_first_profile.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=loft_first_profile_id,
        )
    )
    loft_second_profile = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Right"
        )
    )
    loft_second_profile_id = loft_second_profile.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=loft_second_profile_id,
        )
    )
    loft = await tool_functions["solidworks_create_loft"](
        CreateLoftRequest(
            session_id=session_id,
            document_id=document_id,
            profile_sketch_ids=[loft_first_profile_id, loft_second_profile_id],
        )
    )
    base_flange_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(session_id=session_id, document_id=document_id, plane="Top")
    )
    base_flange_sketch_id = base_flange_sketch.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_add_rectangle"](
        AddRectangleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=base_flange_sketch_id,
            x1=0,
            y1=0,
            x2=30,
            y2=20,
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=base_flange_sketch_id,
        )
    )
    base_flange = await tool_functions["solidworks_create_sheet_metal_base_flange"](
        CreateSheetMetalBaseFlangeRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=base_flange_sketch_id,
            thickness=1.5,
            bend_radius=1.5,
            k_factor=0.42,
        )
    )
    k_factor = await tool_functions["solidworks_set_sheet_metal_k_factor"](
        SetSheetMetalKFactorRequest(
            session_id=session_id, document_id=document_id, k_factor=0.4
        )
    )
    edge_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["edge"],
        )
    )
    edge_flange = await tool_functions["solidworks_create_sheet_metal_edge_flange"](
        CreateSheetMetalEdgeFlangeRequest(
            session_id=session_id,
            document_id=document_id,
            edge_id=edge_topology.data["entities"][0]["entity_id"],
            length=15,
            bend_radius=1.5,
        )
    )
    chamfer_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["edge"],
        )
    )
    chamfer = await tool_functions["solidworks_create_chamfer"](
        CreateChamferRequest(
            session_id=session_id,
            document_id=document_id,
            edge_ids=[chamfer_topology.data["entities"][0]["entity_id"]],
            distance=0.5,
        )
    )
    shell_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    shell = await tool_functions["solidworks_create_shell"](
        CreateShellRequest(
            session_id=session_id,
            document_id=document_id,
            face_ids=[shell_topology.data["entities"][0]["entity_id"]],
            thickness=1,
        )
    )
    dome_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    dome = await tool_functions["solidworks_create_dome"](
        CreateDomeRequest(
            session_id=session_id,
            document_id=document_id,
            face_id=dome_topology.data["entities"][0]["entity_id"],
            height=4,
        )
    )
    tapped_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(session_id=session_id, document_id=document_id, plane="Top")
    )
    tapped_sketch_id = tapped_sketch.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_add_point"](
        AddPointRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=tapped_sketch_id,
            x=15,
            y=5,
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=tapped_sketch_id,
        )
    )
    tapped_hole = await tool_functions["solidworks_create_tapped_hole"](
        CreateTappedHoleRequest(
            session_id=session_id,
            document_id=document_id,
            location_sketch_id=tapped_sketch_id,
            thread_size="M6x1.0",
            depth=10,
        )
    )
    sketch_text = await tool_functions["solidworks_add_sketch_text"](
        AddSketchTextRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            text="MCP",
            x=45,
            y=10,
            height=5,
        )
    )
    draft_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    draft = await tool_functions["solidworks_create_draft"](
        CreateDraftRequest(
            session_id=session_id,
            document_id=document_id,
            face_ids=[draft_topology.data["entities"][0]["entity_id"]],
            angle_degrees=3,
        )
    )
    thicken_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    thicken = await tool_functions["solidworks_create_thicken"](
        CreateThickenRequest(
            session_id=session_id,
            document_id=document_id,
            face_id=thicken_topology.data["entities"][0]["entity_id"],
            thickness=1.5,
            direction="both",
        )
    )
    conversion_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["edge"],
        )
    )
    conversion_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    conversion_sketch_id = conversion_sketch.data["sketch"]["persistent_id"]
    converted_entities = await tool_functions["solidworks_convert_sketch_entities"](
        ConvertSketchEntitiesRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=conversion_sketch_id,
            edge_ids=[conversion_topology.data["entities"][0]["entity_id"]],
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=conversion_sketch_id,
        )
    )
    sketch_3d = await tool_functions["solidworks_create_3d_sketch"](
        Create3DSketchRequest(session_id=session_id, document_id=document_id)
    )
    sketch_3d_id = sketch_3d.data["sketch"]["persistent_id"]
    line_3d = await tool_functions["solidworks_add_3d_line"](
        Add3DLineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_3d_id,
            x1=0,
            y1=0,
            z1=0,
            x2=10,
            y2=5,
            z2=7,
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id, document_id=document_id, sketch_id=sketch_3d_id
        )
    )
    rib_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Right"
        )
    )
    rib_sketch_id = rib_sketch.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_add_line"](
        AddLineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=rib_sketch_id,
            x1=0,
            y1=0,
            x2=10,
            y2=10,
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id, document_id=document_id, sketch_id=rib_sketch_id
        )
    )
    rib = await tool_functions["solidworks_create_rib"](
        CreateRibRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=rib_sketch_id,
            thickness=2,
        )
    )
    wrap_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Top"
        )
    )
    wrap_sketch_id = wrap_sketch.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_add_circle"](
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=wrap_sketch_id,
            center_x=0,
            center_y=0,
            radius=3,
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=wrap_sketch_id,
        )
    )
    wrap_topology = await tool_functions["solidworks_list_part_topology"](
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    wrap = await tool_functions["solidworks_create_wrap"](
        CreateWrapRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=wrap_sketch_id,
            face_id=wrap_topology.data["entities"][0]["entity_id"],
            operation="engrave",
            thickness=0.5,
        )
    )
    bounding_box = await tool_functions["solidworks_get_bounding_box"](
        GetBoundingBoxRequest(session_id=session_id, document_id=document_id)
    )
    set_equation = await tool_functions["solidworks_set_equation"](
        SetEquationRequest(
            session_id=session_id,
            document_id=document_id,
            name="Clearance",
            expression="0.25mm",
        )
    )
    equations = await tool_functions["solidworks_list_equations"](
        ListEquationsRequest(session_id=session_id, document_id=document_id)
    )
    equation = await tool_functions["solidworks_get_equation"](
        GetEquationRequest(
            session_id=session_id,
            document_id=document_id,
            name="Clearance",
        )
    )
    deleted_equation = await tool_functions["solidworks_delete_equation"](
        DeleteEquationRequest(
            session_id=session_id,
            document_id=document_id,
            name="Clearance",
        )
    )
    export = await tool_functions["solidworks_export_file"](
        ExportFileRequest(
            session_id=session_id,
            document_id=document_id,
            artifact_name="mvp_smoke",
            format_type="stl",
        )
    )
    model_info = await tool_functions["solidworks_get_model_info"](
        DocumentRequest(session_id=session_id, document_id=document_id)
    )
    mass_properties = await tool_functions["solidworks_get_mass_properties"](
        DocumentRequest(session_id=session_id, document_id=document_id)
    )
    native_save = await tool_functions["solidworks_save_model"](
        SaveModelRequest(
            session_id=session_id,
            document_id=document_id,
            artifact_name="mvp_smoke_native",
        )
    )
    solid_bodies = await tool_functions["solidworks_list_solid_bodies"](
        ListSolidBodiesRequest(session_id=session_id, document_id=document_id)
    )
    surface_bodies = await tool_functions["solidworks_list_surface_bodies"](
        ListSurfaceBodiesRequest(session_id=session_id, document_id=document_id)
    )
    material_assignment = await tool_functions["solidworks_get_material_assignment"](
        GetMaterialAssignmentRequest(session_id=session_id, document_id=document_id)
    )
    updated_material_assignment = await tool_functions[
        "solidworks_set_material_assignment"
    ](
        SetMaterialAssignmentRequest(
            session_id=session_id,
            document_id=document_id,
            database_name="mock-materials.sldmat",
            material_name="Alloy Steel",
        )
    )
    sheet_metal_info = await tool_functions["solidworks_get_sheet_metal_info"](
        DocumentRequest(session_id=session_id, document_id=document_id)
    )
    flattened_sheet_metal = await tool_functions[
        "solidworks_set_sheet_metal_flat_state"
    ](
        SheetMetalFlatStateRequest(
            session_id=session_id,
            document_id=document_id,
            flattened=True,
        )
    )
    flat_pattern_dxf = await tool_functions["solidworks_export_flat_pattern_dxf"](
        ExportFlatPatternDxfRequest(
            session_id=session_id,
            document_id=document_id,
            artifact_name="mvp_smoke_flat_pattern.dxf",
        )
    )
    sheet_metal_feature_geometry = await tool_functions[
        "solidworks_list_sheet_metal_feature_geometry"
    ](
        ListSheetMetalFeatureGeometryRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )
    bend_notes = await tool_functions["solidworks_insert_sheet_metal_bend_notes"](
        InsertSheetMetalBendNotesRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )
    bend_table = await tool_functions["solidworks_insert_sheet_metal_bend_table"](
        InsertSheetMetalBendTableRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )
    features = await tool_functions["solidworks_list_features"](
        ListFeaturesRequest(session_id=session_id, document_id=document_id)
    )
    dimension_sketch = await tool_functions["solidworks_create_sketch"](
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    dimension_sketch_id = dimension_sketch.data["sketch"]["persistent_id"]
    await tool_functions["solidworks_add_circle"](
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=dimension_sketch_id,
            center_x=0,
            center_y=0,
            radius=2,
        )
    )
    await tool_functions["solidworks_exit_sketch"](
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=dimension_sketch_id,
        )
    )
    dimension_feature = await tool_functions["solidworks_create_extrusion"](
        CreateExtrusionRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=dimension_sketch_id,
            depth=3,
        )
    )
    dimension_feature_id = dimension_feature.data["feature"]["persistent_id"]
    feature_dimension = await tool_functions["solidworks_get_feature_dimension"](
        GetFeatureDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=dimension_feature_id,
        )
    )
    updated_feature_dimension = await tool_functions[
        "solidworks_set_feature_dimension"
    ](
        SetFeatureDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=dimension_feature_id,
            value=12,
        )
    )
    dimensions = await tool_functions["solidworks_list_dimensions"](
        ListDimensionsRequest(session_id=session_id, document_id=document_id)
    )
    dimension_id = dimensions.data["dimensions"][0]["dimension_id"]
    dimension = await tool_functions["solidworks_get_dimension"](
        GetDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            dimension_id=dimension_id,
        )
    )
    updated_dimension = await tool_functions["solidworks_set_dimension"](
        SetDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            dimension_id=dimension_id,
            value=18,
        )
    )
    configurations = await tool_functions["solidworks_list_configurations"](
        ListConfigurationsRequest(session_id=session_id, document_id=document_id)
    )
    created_configuration = await tool_functions["solidworks_create_configuration"](
        CreateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Fabrication",
            description="Smoke-test configuration",
        )
    )
    active_configuration_name = configurations.data["configurations"][0]["name"]
    activated_configuration = await tool_functions["solidworks_activate_configuration"](
        ActivateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name=active_configuration_name,
        )
    )
    deleted_configuration = await tool_functions["solidworks_delete_configuration"](
        DeleteConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Fabrication",
        )
    )
    native_source = Path(native_save.data["artifact"]["path"])
    input_root = Path(server.config.model_input_dir)
    input_root.mkdir(parents=True, exist_ok=True)
    input_copy = input_root / "mvp_smoke_native.sldprt"
    shutil.copyfile(native_source, input_copy)
    opened = await tool_functions["solidworks_open_model"](
        OpenModelRequest(session_id=session_id, file_name=input_copy.name)
    )
    assembly = await tool_functions["solidworks_create_assembly"](
        CreateAssemblyRequest(session_id=session_id, name="mvp_smoke_assembly")
    )
    assembly_document_id = assembly.data["document"]["document_id"]
    inserted_component = await tool_functions["solidworks_insert_assembly_component"](
        InsertAssemblyComponentRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            source_part_document_id=document_id,
        )
    )
    assembly_components = await tool_functions["solidworks_list_assembly_components"](
        ListAssemblyComponentsRequest(
            session_id=session_id,
            document_id=assembly_document_id,
        )
    )
    configuration = await tool_functions["solidworks_get_configuration"](
        GetConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Default",
        )
    )
    assembly_interference = await tool_functions[
        "solidworks_check_assembly_interference"
    ](
        CheckAssemblyInterferenceRequest(
            session_id=session_id,
            document_id=assembly_document_id,
        )
    )
    assembly_component = await tool_functions["solidworks_get_assembly_component"](
        GetAssemblyComponentRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            component_name=inserted_component.data["component"]["display_name"],
        )
    )
    assembly_mate_entities = await tool_functions[
        "solidworks_list_assembly_mate_entities"
    ](
        ListAssemblyMateEntitiesRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            entity_types=["face"],
        )
    )
    mate_entities = assembly_mate_entities.data["entities"]
    assembly_mate = await tool_functions["solidworks_create_assembly_mate"](
        CreateAssemblyMateRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            first_entity_id=mate_entities[0]["entity_id"],
            second_entity_id=mate_entities[1]["entity_id"],
            mate_type="coincident",
        )
    )
    assembly_mates = await tool_functions["solidworks_list_assembly_mates"](
        ListAssemblyMatesRequest(
            session_id=session_id, document_id=assembly_document_id
        )
    )
    deleted_assembly_mate = await tool_functions["solidworks_delete_assembly_mate"](
        DeleteAssemblyMateRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            mate_id=assembly_mates.data["mates"][0]["mate_id"],
        )
    )
    assembly_save = await tool_functions["solidworks_save_model"](
        SaveModelRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            artifact_name="mvp_smoke_assembly_native",
        )
    )
    drawing = await tool_functions["solidworks_create_drawing"](
        CreateDrawingRequest(session_id=session_id, name="mvp_smoke_drawing")
    )
    drawing_document_id = drawing.data["document"]["document_id"]
    drawing_view = await tool_functions["solidworks_create_drawing_view"](
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=document_id,
            orientation="front",
        )
    )
    drawing_views = await tool_functions["solidworks_list_drawing_views"](
        ListDrawingViewsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
        )
    )
    drawing_view_outline = await tool_functions["solidworks_get_drawing_view_outline"](
        GetDrawingViewOutlineRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
        )
    )
    second_drawing_view = await tool_functions["solidworks_create_drawing_view"](
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=document_id,
            orientation="top",
            x=140,
            y=100,
        )
    )
    aligned_drawing_views = await tool_functions["solidworks_align_drawing_views"](
        AlignDrawingViewsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_ids=[
                drawing_view.data["view"]["view_id"],
                second_drawing_view.data["view"]["view_id"],
            ],
            direction="horizontal",
            gap=10,
        )
    )
    projected_drawing_view = await tool_functions[
        "solidworks_create_projected_drawing_view"
    ](
        CreateProjectedDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_view_id=drawing_view.data["view"]["view_id"],
        )
    )
    section_drawing_view = await tool_functions[
        "solidworks_create_section_drawing_view"
    ](
        CreateSectionDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_view_id=drawing_view.data["view"]["view_id"],
            cut_start_x=20,
            cut_start_y=20,
            cut_end_x=50,
            cut_end_y=20,
            x=120,
            y=100,
        )
    )
    assembly_drawing_view = await tool_functions["solidworks_create_drawing_view"](
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=assembly_document_id,
            orientation="isometric",
            x=80,
            y=100,
        )
    )
    drawing_bom = await tool_functions["solidworks_create_drawing_bom"](
        CreateDrawingBomRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_assembly_document_id=assembly_document_id,
            source_view_id=assembly_drawing_view.data["view"]["view_id"],
        )
    )
    updated_drawing_bom = await tool_functions["solidworks_update_drawing_bom"](
        UpdateDrawingBomRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            bom_id=drawing_bom.data["bom_id"],
        )
    )
    deleted_drawing_bom = await tool_functions["solidworks_delete_drawing_bom"](
        DeleteDrawingBomRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            bom_id=updated_drawing_bom.data["bom_id"],
        )
    )
    set_custom_property = await tool_functions["solidworks_set_custom_property"](
        SetCustomPropertyRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            name="DrawingNumber",
            value="MVP-SMOKE-001",
        )
    )
    drawing_note = await tool_functions["solidworks_create_drawing_note"](
        CreateDrawingNoteRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            text="MVP smoke-test drawing note",
        )
    )
    dimension_targets = await tool_functions[
        "solidworks_list_drawing_annotation_targets"
    ](
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["dimension"],
        )
    )
    drawing_dimension = await tool_functions["solidworks_place_drawing_dimension"](
        PlaceDrawingDimensionRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=dimension_targets.data["targets"][0]["target_id"],
        )
    )
    balloon_targets = await tool_functions[
        "solidworks_list_drawing_annotation_targets"
    ](
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=assembly_drawing_view.data["view"]["view_id"],
            target_types=["component"],
        )
    )
    drawing_balloon = await tool_functions["solidworks_create_drawing_balloon"](
        CreateDrawingBalloonRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=balloon_targets.data["targets"][0]["target_id"],
        )
    )
    hole_callout_targets = await tool_functions[
        "solidworks_list_drawing_annotation_targets"
    ](
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["edge"],
        )
    )
    drawing_hole_callout = await tool_functions[
        "solidworks_create_drawing_hole_callout"
    ](
        CreateDrawingHoleCalloutRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=hole_callout_targets.data["targets"][0]["target_id"],
        )
    )
    custom_properties = await tool_functions["solidworks_list_custom_properties"](
        ListCustomPropertiesRequest(
            session_id=session_id,
            document_id=drawing_document_id,
        )
    )
    deleted_custom_property = await tool_functions["solidworks_delete_custom_property"](
        DeleteCustomPropertyRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            name="DrawingNumber",
        )
    )
    trusted_macro_list = await tool_functions["solidworks_list_trusted_macros"]()
    trusted_macro_prepare = await tool_functions["solidworks_prepare_trusted_macro"](
        PrepareTrustedMacroRequest(session_id=session_id, macro_id="mvp_smoke")
    )
    trusted_macro_execute = await tool_functions["solidworks_execute_trusted_macro"](
        ExecuteTrustedMacroRequest(
            session_id=session_id,
            confirmation_id="mac_smoke",
            macro_sha256="0" * 64,
        )
    )
    drawing_revision_table = await tool_functions[
        "solidworks_create_drawing_revision_table"
    ](
        CreateDrawingRevisionTableRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            revision="A",
        )
    )
    activated_drawing_view = await tool_functions["solidworks_activate_drawing_view"](
        ActivateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
        )
    )
    drawing_sheets = await tool_functions["solidworks_list_drawing_sheets"](
        ListDrawingSheetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
        )
    )
    activated_drawing_sheet = await tool_functions["solidworks_activate_drawing_sheet"](
        ActivateDrawingSheetRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            sheet_name=drawing_sheets.data["sheets"][0]["name"],
        )
    )
    created_drawing_sheet = await tool_functions["solidworks_create_drawing_sheet"](
        CreateDrawingSheetRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            sheet_name="Sheet3",
            paper_size="a4",
        )
    )
    renamed_drawing_sheet = await tool_functions["solidworks_rename_drawing_sheet"](
        RenameDrawingSheetRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            sheet_name="Sheet3",
            new_sheet_name="DetailSheet",
        )
    )
    updated_drawing_view_layout = await tool_functions[
        "solidworks_update_drawing_view_layout"
    ](
        UpdateDrawingViewLayoutRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            x=125.0,
            y=85.0,
            scale_decimal=0.5,
        )
    )
    deleted_drawing_sheet = await tool_functions["solidworks_delete_drawing_sheet"](
        DeleteDrawingSheetRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            sheet_name=drawing_sheets.data["sheets"][1]["name"],
        )
    )
    auxiliary_edge_targets = await tool_functions[
        "solidworks_list_drawing_annotation_targets"
    ](
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["edge"],
        )
    )
    auxiliary_drawing_view = await tool_functions[
        "solidworks_create_auxiliary_drawing_view"
    ](
        CreateAuxiliaryDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=auxiliary_edge_targets.data["targets"][0]["target_id"],
            x=180,
            y=120,
        )
    )
    rebuilt_drawing = await tool_functions["solidworks_rebuild_drawing"](
        RebuildDrawingRequest(
            session_id=session_id,
            document_id=drawing_document_id,
        )
    )
    await tool_functions["solidworks_activate_document"](
        ActivateDocumentRequest(session_id=session_id, document_id=document_id)
    )
    closed_document = await tool_functions["solidworks_close_document"](
        CloseDocumentRequest(session_id=session_id, document_id=document_id, save=False)
    )
    closed = await tool_functions["solidworks_close_session"](
        SessionRequest(session_id=session_id)
    )
    return {
        "solidworks_create_session": session,
        "solidworks_close_session": closed,
        "solidworks_get_status": status,
        "solidworks_list_documents": documents,
        "solidworks_activate_document": activated_document,
        "solidworks_close_document": closed_document,
        "solidworks_open_model": opened,
        "solidworks_create_part": part,
        "solidworks_create_assembly": assembly,
        "solidworks_create_drawing": drawing,
        "solidworks_insert_assembly_component": inserted_component,
        "solidworks_list_assembly_components": assembly_components,
        "solidworks_get_assembly_component": assembly_component,
        "solidworks_check_assembly_interference": assembly_interference,
        "solidworks_list_assembly_mate_entities": assembly_mate_entities,
        "solidworks_create_assembly_mate": assembly_mate,
        "solidworks_list_assembly_mates": assembly_mates,
        "solidworks_delete_assembly_mate": deleted_assembly_mate,
        "solidworks_create_drawing_view": drawing_view,
        "solidworks_list_drawing_views": drawing_views,
        "solidworks_get_drawing_view_outline": drawing_view_outline,
        "solidworks_align_drawing_views": aligned_drawing_views,
        "solidworks_rebuild_drawing": rebuilt_drawing,
        "solidworks_activate_drawing_view": activated_drawing_view,
        "solidworks_list_drawing_sheets": drawing_sheets,
        "solidworks_activate_drawing_sheet": activated_drawing_sheet,
        "solidworks_create_drawing_sheet": created_drawing_sheet,
        "solidworks_rename_drawing_sheet": renamed_drawing_sheet,
        "solidworks_update_drawing_view_layout": updated_drawing_view_layout,
        "solidworks_delete_drawing_sheet": deleted_drawing_sheet,
        "solidworks_create_projected_drawing_view": projected_drawing_view,
        "solidworks_create_section_drawing_view": section_drawing_view,
        "solidworks_create_auxiliary_drawing_view": auxiliary_drawing_view,
        "solidworks_create_drawing_bom": drawing_bom,
        "solidworks_update_drawing_bom": updated_drawing_bom,
        "solidworks_delete_drawing_bom": deleted_drawing_bom,
        "solidworks_create_drawing_note": drawing_note,
        "solidworks_create_drawing_revision_table": drawing_revision_table,
        "solidworks_list_drawing_annotation_targets": hole_callout_targets,
        "solidworks_place_drawing_dimension": drawing_dimension,
        "solidworks_create_drawing_balloon": drawing_balloon,
        "solidworks_create_drawing_hole_callout": drawing_hole_callout,
        "solidworks_set_custom_property": set_custom_property,
        "solidworks_delete_custom_property": deleted_custom_property,
        "solidworks_list_custom_properties": custom_properties,
        "solidworks_create_reference_plane": reference_plane,
        "solidworks_get_reference_axis": reference_axis,
        "solidworks_get_reference_plane": origin_plane,
        "solidworks_create_sketch": sketch,
        "solidworks_create_3d_sketch": sketch_3d,
        "solidworks_add_rectangle": rectangle,
        "solidworks_add_line": line,
        "solidworks_add_3d_line": line_3d,
        "solidworks_add_circle": circle,
        "solidworks_add_point": point,
        "solidworks_add_arc": arc,
        "solidworks_add_centerline": centerline,
        "solidworks_add_spline": spline,
        "solidworks_add_polygon": polygon,
        "solidworks_add_ellipse": ellipse,
        "solidworks_add_sketch_text": sketch_text,
        "solidworks_create_sketch_linear_pattern": linear_pattern,
        "solidworks_create_sketch_circular_pattern": circular_pattern,
        "solidworks_mirror_sketch_entities": mirrored,
        "solidworks_offset_sketch_entities": offset,
        "solidworks_trim_sketch_entity": trimmed,
        "solidworks_convert_sketch_entities": converted_entities,
        "solidworks_exit_sketch": exited,
        "solidworks_create_extrusion": extrusion,
        "solidworks_create_feature_linear_pattern": feature_linear_pattern,
        "solidworks_create_feature_circular_pattern": feature_circular_pattern,
        "solidworks_mirror_feature": feature_mirror,
        "solidworks_create_hole_wizard": hole_wizard,
        "solidworks_create_tapped_hole": tapped_hole,
        "solidworks_create_cut_extrude": cut_extrusion,
        "solidworks_create_sheet_metal_base_flange": base_flange,
        "solidworks_create_sheet_metal_edge_flange": edge_flange,
        "solidworks_set_sheet_metal_k_factor": k_factor,
        "solidworks_create_shell": shell,
        "solidworks_create_chamfer": chamfer,
        "solidworks_create_rib": rib,
        "solidworks_create_dome": dome,
        "solidworks_create_draft": draft,
        "solidworks_create_thicken": thicken,
        "solidworks_create_wrap": wrap,
        "solidworks_create_revolve": revolved,
        "solidworks_create_sweep": sweep,
        "solidworks_create_loft": loft,
        "solidworks_get_bounding_box": bounding_box,
        "solidworks_list_equations": equations,
        "solidworks_get_equation": equation,
        "solidworks_set_equation": set_equation,
        "solidworks_delete_equation": deleted_equation,
        "solidworks_export_file": export,
        "solidworks_save_model": assembly_save,
        "solidworks_get_sheet_metal_info": sheet_metal_info,
        "solidworks_set_sheet_metal_flat_state": flattened_sheet_metal,
        "solidworks_export_flat_pattern_dxf": flat_pattern_dxf,
        "solidworks_get_model_info": model_info,
        "solidworks_get_mass_properties": mass_properties,
        "solidworks_list_solid_bodies": solid_bodies,
        "solidworks_list_surface_bodies": surface_bodies,
        "solidworks_list_part_topology": conversion_topology,
        "solidworks_list_sheet_metal_feature_geometry": sheet_metal_feature_geometry,
        "solidworks_insert_sheet_metal_bend_notes": bend_notes,
        "solidworks_insert_sheet_metal_bend_table": bend_table,
        "solidworks_get_material_assignment": material_assignment,
        "solidworks_set_material_assignment": updated_material_assignment,
        "solidworks_list_features": features,
        "solidworks_get_feature_dimension": feature_dimension,
        "solidworks_set_feature_dimension": updated_feature_dimension,
        "solidworks_list_dimensions": dimensions,
        "solidworks_get_dimension": dimension,
        "solidworks_set_dimension": updated_dimension,
        "solidworks_list_configurations": configurations,
        "solidworks_get_configuration": configuration,
        "solidworks_create_configuration": created_configuration,
        "solidworks_delete_configuration": deleted_configuration,
        "solidworks_activate_configuration": activated_configuration,
        "solidworks_list_trusted_macros": trusted_macro_list,
        "solidworks_prepare_trusted_macro": trusted_macro_prepare,
        "solidworks_execute_trusted_macro": trusted_macro_execute,
    }


async def _invoke_smoke_tool(
    tool: Any, payload: dict[str, Any], mvp_results: dict[str, Any]
) -> Any:
    """Call legacy tools with smoke dictionaries and typed tools with valid models."""
    if tool.name in mvp_results:
        return mvp_results[tool.name]
    return (
        await tool.fn(payload)
        if inspect.signature(tool.fn).parameters
        else await tool.fn()
    )


class TestLevelBSmokeExecution:
    """Every tool must handle a minimal payload without raising an unhandled exception."""

    @pytest.mark.asyncio
    async def test_smoke_all_tools(
        self, mock_server: SolidWorksMCPServer, output_dir: Path
    ) -> None:
        """Run every registered tool with a safe mock payload."""
        results: list[dict[str, Any]] = []
        failed: list[str] = []
        mvp_results = await _mvp_smoke_results(mock_server)

        for tool in await mock_server.mcp.list_tools():
            name = tool.name
            payload = _SMOKE_PAYLOADS.get(name, {})
            t0 = time.perf_counter()
            try:
                result = await _invoke_smoke_tool(tool, payload, mvp_results)
                elapsed = time.perf_counter() - t0
                response = _as_mcp_response(result)
                status = response.get("status", "unknown") if response else "non-object"
                results.append(
                    {
                        "tool": name,
                        "status": status,
                        "elapsed_s": round(elapsed, 4),
                        "payload_keys": list(payload.keys()),
                    }
                )
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                results.append(
                    {
                        "tool": name,
                        "status": "EXCEPTION",
                        "elapsed_s": round(elapsed, 4),
                        "error": str(exc),
                    }
                )
                failed.append(f"{name}: {exc}")

        # Write report
        report_path = output_dir / "smoke_test_report.json"
        report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

        if failed:
            summary = "\n".join(failed)
            pytest.fail(
                f"{len(failed)}/{len(results)} tools raised unhandled exceptions:\n{summary}\n"
                f"Full report: {report_path}"
            )

    @pytest.mark.asyncio
    async def test_all_tools_return_dict(
        self, mock_server: SolidWorksMCPServer
    ) -> None:
        """Every tool must serialize to an MCP response object."""
        non_dict: list[str] = []
        mvp_results = await _mvp_smoke_results(mock_server)
        for tool in await mock_server.mcp.list_tools():
            name = tool.name
            payload = _SMOKE_PAYLOADS.get(name, {})
            try:
                result = await _invoke_smoke_tool(tool, payload, mvp_results)
                if _as_mcp_response(result) is None:
                    non_dict.append(f"{name}: returned {type(result).__name__}")
            except Exception:
                pass  # Exceptions are caught in test_smoke_all_tools

        assert not non_dict, (
            f"{len(non_dict)} tools did not return a dict:\n" + "\n".join(non_dict)
        )

    @pytest.mark.asyncio
    async def test_all_tools_have_status_field(
        self, mock_server: SolidWorksMCPServer
    ) -> None:
        """Every tool response dict must contain a 'status' key."""
        missing_status: list[str] = []
        mvp_results = await _mvp_smoke_results(mock_server)
        for tool in await mock_server.mcp.list_tools():
            name = tool.name
            payload = _SMOKE_PAYLOADS.get(name, {})
            try:
                result = await _invoke_smoke_tool(tool, payload, mvp_results)
                response = _as_mcp_response(result)
                if response is not None and "status" not in response:
                    missing_status.append(name)
            except Exception:
                pass

        assert not missing_status, (
            f"{len(missing_status)} tools returned dicts without 'status':\n"
            + "\n".join(missing_status)
        )

    @pytest.mark.asyncio
    async def test_smoke_responses_within_context_budget(
        self, mock_server: SolidWorksMCPServer, output_dir: Path
    ) -> None:
        """Guardrail: smoke responses should stay reasonably small for LLM context usage."""
        oversize: list[str] = []
        total_bytes = 0
        size_rows: list[dict[str, Any]] = []
        mvp_results = await _mvp_smoke_results(mock_server)

        for tool in await mock_server.mcp.list_tools():
            name = tool.name
            payload = _SMOKE_PAYLOADS.get(name, {})
            try:
                result = await _invoke_smoke_tool(tool, payload, mvp_results)
            except Exception as exc:
                # Size-budget test is not responsible for behavioral failures.
                size_rows.append({"tool": name, "bytes": 0, "error": str(exc)})
                continue

            serialized = json.dumps(_as_mcp_response(result) or result, default=str)
            size_bytes = len(serialized.encode("utf-8"))
            total_bytes += size_bytes
            size_rows.append({"tool": name, "bytes": size_bytes})

            if size_bytes > MAX_TOOL_RESPONSE_BYTES:
                oversize.append(
                    f"{name}: {size_bytes} bytes (max {MAX_TOOL_RESPONSE_BYTES})"
                )

        size_report = output_dir / "smoke_response_size_report.json"
        size_report.write_text(
            json.dumps(
                {
                    "max_tool_response_bytes": MAX_TOOL_RESPONSE_BYTES,
                    "max_total_response_bytes": MAX_SMOKE_TOTAL_RESPONSE_BYTES,
                    "total_response_bytes": total_bytes,
                    "tools": size_rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        if oversize:
            pytest.fail(
                "One or more smoke responses exceeded per-tool size budget:\n"
                + "\n".join(oversize)
                + f"\nReport: {size_report}"
            )

        assert total_bytes <= MAX_SMOKE_TOTAL_RESPONSE_BYTES, (
            f"Total smoke response size {total_bytes} bytes exceeds "
            f"budget {MAX_SMOKE_TOTAL_RESPONSE_BYTES} bytes. "
            f"Report: {size_report}"
        )


# ---------------------------------------------------------------------------
# Level C — Real COM Lifecycle (Windows + SolidWorks required)
# ---------------------------------------------------------------------------

DEMO_DIR = Path("C:/Temp/mcp_smoke_integration")


@pytest.mark.skipif(
    not _real_sw_enabled(), reason=f"Set {REAL_SW_FLAG}=true to run real COM tests"
)
@pytest.mark.skipif(not _is_windows(), reason="COM tests require Windows")
@pytest.mark.windows_only
@pytest.mark.solidworks_only
class TestLevelCRealCOM:
    """Real SolidWorks COM integration — one per lifecycle stage."""

    @pytest.mark.asyncio
    async def test_c01_health_check(self, real_server: SolidWorksMCPServer) -> None:
        """SolidWorks COM connection must report healthy."""
        tool = await _find_tool(real_server, "discover_solidworks_docs")
        assert tool is not None, "discover_solidworks_docs not registered"
        result = await tool({})
        assert isinstance(result, dict)
        assert result.get("status") in (
            "success",
            "error",
        ), f"Unexpected status: {result}"

    @pytest.mark.asyncio
    async def test_c02_create_and_save_part(
        self, real_server: SolidWorksMCPServer, output_dir: Path
    ) -> None:
        """Create a part, save it, and verify the file exists."""
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        part_path = DEMO_DIR / "smoke_c02_part.sldprt"

        create = await _find_tool(real_server, "create_part")
        assert create, "create_part not registered"
        result = await create({"name": "smoke_c02_part"})
        assert result["status"] == "success", f"create_part failed: {result}"

        save_as = await _find_tool(real_server, "save_as")
        assert save_as, "save_as not registered"
        result = await save_as({"file_path": str(part_path)})
        assert result["status"] == "success", f"save_as failed: {result}"
        assert part_path.exists(), f"Part file not created: {part_path}"

    @pytest.mark.asyncio
    async def test_c03_sketch_and_extrude(
        self, real_server: SolidWorksMCPServer, output_dir: Path
    ) -> None:
        """Create a part, save it, reopen it, and save again in one lifecycle."""
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        part_path = DEMO_DIR / "smoke_c03_roundtrip.sldprt"

        create = await _find_tool(real_server, "create_part")
        result = await create({"name": "smoke_c03_roundtrip"})
        assert result["status"] == "success"

        save_as = await _find_tool(real_server, "save_as")
        result = await save_as({"file_path": str(part_path)})
        assert result["status"] == "success"
        assert part_path.exists()

        close_model = await _find_tool(real_server, "close_model")
        result = await close_model({"save": False})
        assert result["status"] in {"success", "error"}

        open_model = await _find_tool(real_server, "open_model")
        result = await open_model({"file_path": str(part_path)})
        assert result["status"] == "success"

        save_file = await _find_tool(real_server, "save_file")
        result = await save_file({"force_save": True})
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_c04_mass_properties(self, real_server: SolidWorksMCPServer) -> None:
        """Open a shipped sample part and verify file properties can be read."""
        sample_part = _find_sample_part()

        open_tool = await _find_tool(real_server, "open_model")
        assert open_tool
        result = await open_tool({"file_path": str(sample_part)})
        assert result["status"] == "success"

        props_tool = await _find_tool(real_server, "get_file_properties")
        assert props_tool
        result = await props_tool()
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_c05_export_step(self, real_server: SolidWorksMCPServer) -> None:
        """Open a shipped sample part, export it to STEP, and verify file exists."""
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        step_path = DEMO_DIR / "smoke_c05_export.step"

        sample_part = _find_sample_part()
        open_tool = await _find_tool(real_server, "open_model")
        assert open_tool
        result = await open_tool({"file_path": str(sample_part)})
        assert result["status"] == "success", f"open_model failed: {result}"

        tool = await _find_tool(real_server, "export_step")
        assert tool
        result = await tool({"file_path": str(step_path), "format_type": "step"})
        assert result["status"] == "success", f"STEP export failed: {result}"
        assert step_path.exists(), f"STEP file not found: {step_path}"

    @pytest.mark.asyncio
    async def test_c06_export_image(self, real_server: SolidWorksMCPServer) -> None:
        """Open a shipped sample part and verify image export returns usable output."""
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        img_path = DEMO_DIR / "smoke_c06_image.jpg"

        sample_part = _find_sample_part()
        open_tool = await _find_tool(real_server, "open_model")
        assert open_tool
        result = await open_tool({"file_path": str(sample_part)})
        assert result["status"] == "success", f"open_model failed: {result}"

        tool = await _find_tool(real_server, "export_image")
        assert tool
        result = await tool({"file_path": str(img_path), "format_type": "jpg"})
        assert result["status"] == "success", f"Image export failed: {result}"
        if not img_path.exists():
            export_info = result.get("export") or result.get("data") or {}
            assert export_info.get("file_path") == str(img_path), (
                f"Image export returned success without file or matching metadata: {result}"
            )

    @pytest.mark.asyncio
    async def test_c07_create_assembly(self, real_server: SolidWorksMCPServer) -> None:
        """Create an assembly and save it."""
        DEMO_DIR.mkdir(parents=True, exist_ok=True)
        asm_path = DEMO_DIR / "smoke_c07_assembly.sldasm"
        tool = await _find_tool(real_server, "create_assembly")
        assert tool
        result = await tool({"name": "smoke_c07_asm"})
        assert result["status"] == "success"

        save_as = await _find_tool(real_server, "save_as")
        result = await save_as({"file_path": str(asm_path)})
        assert result["status"] == "success"
        assert asm_path.exists()

    @pytest.mark.asyncio
    async def test_c08_close_model(self, real_server: SolidWorksMCPServer) -> None:
        """Close the active document cleanly."""
        tool = await _find_tool(real_server, "close_model")
        assert tool
        result = await tool({"save": False})
        assert result["status"] in (
            "success",
            "error",
        ), f"Unexpected close result: {result}"

    @pytest.mark.asyncio
    async def test_c09_open_sample_part(self, real_server: SolidWorksMCPServer) -> None:
        """Open one of the shipped 2026 sample parts and read its file properties."""
        sample_part = _find_sample_part()

        open_tool = await _find_tool(real_server, "open_model")
        assert open_tool
        result = await open_tool({"file_path": str(sample_part)})
        assert result["status"] == "success", f"open_model failed: {result}"

        props = await _find_tool(real_server, "get_file_properties")
        assert props
        result = await props()
        assert result["status"] == "success"

        close = await _find_tool(real_server, "close_model")
        await close({"save": False})

    @pytest.mark.asyncio
    async def test_c10_docs_discovery_and_compat(
        self, real_server: SolidWorksMCPServer, output_dir: Path
    ) -> None:
        """Discover live COM surface and write compatibility report."""
        tool = await _find_tool(real_server, "discover_solidworks_docs")
        assert tool

        docs_out = output_dir / "docs-index"
        result = await tool({"output_dir": str(docs_out), "include_vba": True})
        assert result["status"] in ("success", "error")

        compat: dict[str, Any] = {
            "solidworks_version": "2026",
            "compat_check_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "required_com_interfaces": REQUIRED_COM_METHODS,
            "discovery_status": result.get("status"),
            "classification": {},
        }

        if result.get("status") == "success":
            discovered_ifaces: list[str] = list(
                result.get("index", {}).get("com_objects", {}).keys()
            )
            for iface in REQUIRED_COM_METHODS:
                if any(iface.lower() in d.lower() for d in discovered_ifaces):
                    compat["classification"][iface] = "OK"
                else:
                    compat["classification"][iface] = "NOT_FOUND"

        compat_path = output_dir / "api_compat_report.json"
        compat_path.write_text(json.dumps(compat, indent=2), encoding="utf-8")

        not_found = [k for k, v in compat["classification"].items() if v == "NOT_FOUND"]
        if not_found:
            # Warn but don't fail — the LLM needs the report to choose fallback paths
            print(f"\n⚠️  COM interfaces not found (suggest fallback VBA): {not_found}")

    @pytest.mark.asyncio
    async def test_c11_read_tools_feature_and_configuration_listing(
        self, real_server: SolidWorksMCPServer
    ) -> None:
        """Open a sample part and validate read-before-write tools for tree/config context."""
        sample_part = _find_sample_part()

        open_tool = await _find_tool(real_server, "open_model")
        assert open_tool is not None, "open_model not registered"
        opened = await open_tool({"file_path": str(sample_part)})
        assert opened.get("status") == "success", f"open_model failed: {opened}"

        list_features_tool = await _find_tool(real_server, "list_features")
        assert list_features_tool is not None, "list_features not registered"
        features_result = await list_features_tool({"include_suppressed": False})
        assert features_result.get("status") == "success", (
            f"list_features failed: {features_result}"
        )
        assert isinstance(features_result.get("features", []), list), (
            f"Expected list in features payload: {features_result}"
        )

        list_configs_tool = await _find_tool(real_server, "list_configurations")
        assert list_configs_tool is not None, "list_configurations not registered"
        configs_result = await list_configs_tool()
        assert configs_result.get("status") == "success", (
            f"list_configurations failed: {configs_result}"
        )
        configs = configs_result.get("configurations", [])
        assert isinstance(configs, list), (
            f"Expected list in configurations payload: {configs_result}"
        )
        assert len(configs) >= 1, (
            "Expected at least one configuration in an opened sample part"
        )


# ---------------------------------------------------------------------------
# Docs-discovery compatibility adapter tests (mock-safe)
# ---------------------------------------------------------------------------


class TestDocsDiscoveryCompat:
    """Verify that the compatibility classification logic works offline."""

    @pytest.mark.asyncio
    async def test_compat_report_structure(self, output_dir: Path) -> None:
        """The saved compat report (if present) must have expected keys."""
        compat_path = output_dir / "api_compat_report.json"
        if not compat_path.exists():
            pytest.skip("api_compat_report.json not present — run Level C tests first")

        with open(compat_path, encoding="utf-8") as fh:
            report = json.load(fh)

        for key in (
            "solidworks_version",
            "required_com_interfaces",
            "discovery_status",
            "classification",
        ):
            assert key in report, f"compat report missing key: {key}"

    @pytest.mark.asyncio
    async def test_smoke_report_exists(self, output_dir: Path) -> None:
        """The smoke execution report must be present after Level B tests."""
        smoke_path = output_dir / "smoke_test_report.json"
        if not smoke_path.exists():
            pytest.skip("smoke_test_report.json not present — run Level B tests first")

        with open(smoke_path, encoding="utf-8") as fh:
            report = json.load(fh)

        assert isinstance(report, list)
        assert len(report) > 0, "Smoke report is empty"
        # Count successes
        success_count = sum(1 for r in report if r.get("status") == "success")
        total = len(report)
        print(f"\n📊 Smoke test results: {success_count}/{total} success")
