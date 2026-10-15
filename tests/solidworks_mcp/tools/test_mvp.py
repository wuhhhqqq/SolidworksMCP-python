"""Tests for the typed, prefixed MCP MVP tool surface."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from solidworks_mcp.tools.mvp import register_mvp_tools


@pytest.mark.asyncio
async def test_mvp_tools_are_prefixed_and_expose_structured_session_creation(
    tmp_path: Path,
) -> None:
    """The MVP adds discoverable tools without changing the legacy surface."""

    class _Adapter:
        def is_connected(self) -> bool:
            return True

    mcp_server = FastMCP("mvp-test-server")
    config = SimpleNamespace(data_dir=tmp_path)
    added = await register_mvp_tools(mcp_server, _Adapter(), config)

    assert added == 134
    tools = {tool.name: tool for tool in await mcp_server.list_tools()}
    expected = {
        "solidworks_create_session",
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
        "solidworks_reorder_drawing_sheets",
        "solidworks_split_sketch_entity",
        "solidworks_fillet_sketch_corner",
        "solidworks_chamfer_sketch_corner",
        "solidworks_list_drawing_revisions",
        "solidworks_add_drawing_revision",
        "solidworks_delete_drawing_revision",
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
        "solidworks_create_drawing_ordinate_dimension",
        "solidworks_create_drawing_surface_finish",
        "solidworks_create_drawing_weld_symbol",
        "solidworks_create_drawing_geometric_tolerance",
        "solidworks_create_drawing_center_mark",
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
        "solidworks_list_dimensions",
        "solidworks_get_dimension",
        "solidworks_set_dimension",
        "solidworks_get_feature_dimension",
        "solidworks_set_feature_dimension",
        "solidworks_list_equations",
        "solidworks_get_equation",
        "solidworks_set_equation",
        "solidworks_delete_equation",
        "solidworks_export_file",
        "solidworks_get_sheet_metal_info",
        "solidworks_set_sheet_metal_flat_state",
        "solidworks_export_flat_pattern_dxf",
        "solidworks_get_model_info",
        "solidworks_get_mass_properties",
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
        "solidworks_save_model",
        "solidworks_list_trusted_macros",
        "solidworks_prepare_trusted_macro",
        "solidworks_execute_trusted_macro",
    }
    assert expected <= tools.keys()

    result = await tools["solidworks_create_session"].fn()
    assert result.status.value == "succeeded"
    assert result.data["session"]["session_id"].startswith("sw_")
    assert tools["solidworks_get_model_info"].annotations.readOnlyHint is True
    assert tools["solidworks_create_part"].annotations.destructiveHint is False
    assert tools["solidworks_create_configuration"].annotations.idempotentHint is False
    assert tools["solidworks_execute_trusted_macro"].annotations.destructiveHint is True
