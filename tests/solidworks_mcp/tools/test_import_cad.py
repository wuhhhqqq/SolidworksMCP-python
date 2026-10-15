"""Tests for DXF-to-SolidWorks part import tools."""

from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.import_cad import (
    ImportDxfToPartInput,
    ValidateDxfGeometryInput,
    _read_dxf_sketch_geometry,
    _validate_dxf_geometry,
    register_import_cad_tools,
)


class _ImportAdapter:
    """Small adapter double that records the composed import workflow."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def create_part(self, *args: object) -> AdapterResult[dict[str, str]]:
        self.calls.append(("create_part", args))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data={"name": "part"})

    async def create_sketch(self, *args: object) -> AdapterResult[str]:
        self.calls.append(("create_sketch", args))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Sketch1")

    async def add_line(self, *args: object) -> AdapterResult[str]:
        self.calls.append(("add_line", args))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Line1")

    async def add_arc(self, *args: object) -> AdapterResult[str]:
        self.calls.append(("add_arc", args))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Arc1")

    async def add_circle(self, *args: object) -> AdapterResult[str]:
        self.calls.append(("add_circle", args))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Circle1")

    async def exit_sketch(self) -> AdapterResult[None]:
        self.calls.append(("exit_sketch", ()))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)

    async def create_extrusion(self, *args: object) -> AdapterResult[SimpleNamespace]:
        self.calls.append(("create_extrusion", args))
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS, data=SimpleNamespace(name="Boss-Extrude1")
        )

    async def save_file(self, *args: object) -> AdapterResult[None]:
        self.calls.append(("save_file", args))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)


class _FailingImportAdapter(_ImportAdapter):
    """Adapter double that fails one sketch-geometry insertion."""

    async def add_circle(self, *args: object) -> AdapterResult[str]:
        self.calls.append(("add_circle", args))
        return AdapterResult(status=AdapterResultStatus.ERROR, error="simulated circle failure")


class _FailingCleanupImportAdapter(_FailingImportAdapter):
    """Adapter double that cannot clean up a failed, partially created sketch."""

    async def exit_sketch(self) -> AdapterResult[None]:
        self.calls.append(("exit_sketch", ()))
        return AdapterResult(status=AdapterResultStatus.ERROR, error="simulated exit failure")


class _RejectedFeatureUseAdapter(_ImportAdapter):
    """Adapter double whose SolidWorks kernel rejects a boss-extrude profile."""

    async def check_sketch_feature_use(
        self, *args: object
    ) -> AdapterResult[dict[str, object]]:
        self.calls.append(("check_sketch_feature_use", args))
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "is_valid": False,
                "status": "self_intersection",
                "status_code": 2,
            },
        )


def _write_dxf(path: Path) -> None:
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (10, 0))
    modelspace.add_circle((5, 5), 2)
    modelspace.add_arc((5, 5), 3, 0, 90)
    modelspace.add_lwpolyline([(0, 0), (0, 10), (10, 10)], close=False)
    modelspace.add_text("100")
    document.saveas(path)


def _write_invalid_profile_dxf(path: Path) -> None:
    """Write a profile with duplicate, overlapping, and open line geometry."""
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    modelspace = document.modelspace()
    modelspace.add_line((0, 0), (10, 0))
    modelspace.add_line((10, 0), (10, 10))
    modelspace.add_line((10, 10), (0, 10))
    modelspace.add_line((0, 10), (0, 0))
    modelspace.add_line((0, 0), (10, 0))
    modelspace.add_line((20, 0), (25, 0))
    document.saveas(path)


def _write_closed_circle_dxf(path: Path) -> None:
    """Write a simple profile that passes the lightweight DXF preflight."""
    document = ezdxf.new("R2010")
    document.header["$INSUNITS"] = 4
    document.modelspace().add_circle((0, 0), 10)
    document.saveas(path)


def test_read_dxf_sketch_geometry_reads_basic_entities_in_mm(tmp_path: Path) -> None:
    """Basic CAXA-compatible geometry maps to SolidWorks sketch primitives."""
    source = tmp_path / "profile.dxf"
    _write_dxf(source)

    geometry, report = _read_dxf_sketch_geometry(str(source), "auto", True)

    assert [entity[0] for entity in geometry] == [
        "LINE",
        "CIRCLE",
        "ARC",
        "LINE",
        "LINE",
    ]
    assert geometry[0][1] == (0.0, 0.0, 10.0, 0.0)
    assert report["source_units"] == "mm"
    assert report["ignored_entities"] == {"TEXT": 1}


def test_validate_dxf_geometry_reports_overlap_and_open_profile(tmp_path: Path) -> None:
    """The read-only validator must flag geometry that is unsafe to extrude."""
    source = tmp_path / "invalid-profile.dxf"
    _write_invalid_profile_dxf(source)
    geometry, _report = _read_dxf_sketch_geometry(str(source), "auto", True)

    validation = _validate_dxf_geometry(geometry, tolerance_mm=0.01, max_pair_checks=100)

    codes = {issue["code"] for issue in validation["issues"]}
    assert {"duplicate_geometry", "overlapping_line_segments", "open_profile"} <= codes
    assert validation["is_valid_for_sketch"] is False
    assert validation["is_ready_for_extrusion"] is False
    assert validation["profiles"]["open"] == 1


def test_validate_dxf_geometry_does_not_clear_incomplete_analysis_for_extrusion() -> None:
    """Unanalysed arc crossings and truncated line scans require an explicit override."""
    arc_validation = _validate_dxf_geometry(
        [("ARC", (0.0, 0.0, 1.0, 0.0, -1.0, 0.0))],
        tolerance_mm=0.01,
        max_pair_checks=100,
    )
    truncated_validation = _validate_dxf_geometry(
        [
            ("LINE", (0.0, 0.0, 1.0, 0.0)),
            ("LINE", (2.0, 0.0, 3.0, 0.0)),
            ("LINE", (4.0, 0.0, 5.0, 0.0)),
        ],
        tolerance_mm=0.01,
        max_pair_checks=1,
    )

    assert arc_validation["is_valid_for_sketch"] is True
    assert arc_validation["is_ready_for_extrusion"] is False
    assert arc_validation["summary"]["analysis_complete_for_extrusion"] is False
    assert truncated_validation["is_ready_for_extrusion"] is False
    assert truncated_validation["summary"]["analysis_complete_for_extrusion"] is False


@pytest.mark.asyncio
async def test_validate_dxf_geometry_is_registered_and_read_only(tmp_path: Path) -> None:
    """Preflight is available through MCP and does not call the CAD adapter."""
    source = tmp_path / "invalid-profile.dxf"
    _write_invalid_profile_dxf(source)
    adapter = _ImportAdapter()
    mcp = FastMCP("test")

    assert await register_import_cad_tools(mcp, adapter, {}) == 2
    tool = next(item.fn for item in await mcp.list_tools() if item.name == "validate_dxf_geometry")
    result = await tool(ValidateDxfGeometryInput(source_path=str(source)))

    assert result["status"] == "success"
    assert result["validation"]["is_ready_for_extrusion"] is False
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_import_dxf_to_part_composes_part_sketch_extrude_and_save(
    tmp_path: Path,
) -> None:
    """The MCP tool creates a native part from parsed geometry without overwriting files."""
    source = tmp_path / "profile.dxf"
    output = tmp_path / "profile.sldprt"
    _write_dxf(source)
    adapter = _ImportAdapter()
    mcp = FastMCP("test")
    assert await register_import_cad_tools(mcp, adapter, {}) == 2
    tool = next(item.fn for item in await mcp.list_tools() if item.name == "import_dxf_to_part")

    result = await tool(
        ImportDxfToPartInput(
            source_path=str(source),
            output_path=str(output),
            extrusion_depth_mm=5.0,
            validate_geometry=False,
            verify_with_solidworks=False,
        )
    )

    assert result["status"] == "success"
    assert result["imported_entities"] == {"ARC": 1, "CIRCLE": 1, "LINE": 3}
    assert [name for name, _args in adapter.calls] == [
        "create_part",
        "create_sketch",
        "add_line",
        "add_circle",
        "add_arc",
        "add_line",
        "add_line",
        "exit_sketch",
        "create_extrusion",
        "save_file",
    ]


@pytest.mark.asyncio
async def test_import_dxf_to_part_blocks_invalid_preflight_before_creating_part(
    tmp_path: Path,
) -> None:
    """Overlap diagnostics must stop the default import before any CAD mutation."""
    source = tmp_path / "invalid-profile.dxf"
    _write_invalid_profile_dxf(source)
    adapter = _ImportAdapter()
    mcp = FastMCP("test")
    await register_import_cad_tools(mcp, adapter, {})
    tool = next(item.fn for item in await mcp.list_tools() if item.name == "import_dxf_to_part")

    result = await tool(
        ImportDxfToPartInput(
            source_path=str(source), output_path=str(tmp_path / "invalid.sldprt")
        )
    )

    assert result["status"] == "error"
    assert result["validation"]["is_valid_for_sketch"] is False
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_import_dxf_to_part_blocks_solidworks_kernel_rejection(
    tmp_path: Path,
) -> None:
    """A native CheckFeatureUse failure must prevent an otherwise valid extrusion."""
    source = tmp_path / "circle.dxf"
    _write_closed_circle_dxf(source)
    adapter = _RejectedFeatureUseAdapter()
    mcp = FastMCP("test")
    await register_import_cad_tools(mcp, adapter, {})
    tool = next(item.fn for item in await mcp.list_tools() if item.name == "import_dxf_to_part")

    result = await tool(
        ImportDxfToPartInput(
            source_path=str(source),
            output_path=str(tmp_path / "circle.sldprt"),
            extrusion_depth_mm=5.0,
        )
    )

    assert result["status"] == "error"
    assert result["solidworks_validation"]["status"] == "self_intersection"
    assert [name for name, _args in adapter.calls] == [
        "create_part",
        "create_sketch",
        "add_circle",
        "exit_sketch",
        "check_sketch_feature_use",
    ]


@pytest.mark.asyncio
async def test_import_dxf_to_part_exits_partial_sketch_after_entity_failure(
    tmp_path: Path,
) -> None:
    """A failed import must not leave SolidWorks editing a partially built sketch."""
    source = tmp_path / "profile.dxf"
    _write_dxf(source)
    adapter = _FailingImportAdapter()
    mcp = FastMCP("test")
    await register_import_cad_tools(mcp, adapter, {})
    tool = next(item.fn for item in await mcp.list_tools() if item.name == "import_dxf_to_part")

    result = await tool(
        ImportDxfToPartInput(
            source_path=str(source), output_path=str(tmp_path / "profile.sldprt")
        )
    )

    assert result["status"] == "error"
    assert [name for name, _args in adapter.calls][-1] == "exit_sketch"


@pytest.mark.asyncio
async def test_import_dxf_to_part_reports_failed_partial_sketch_cleanup(
    tmp_path: Path,
) -> None:
    """Cleanup failures must be visible to clients before another CAD operation."""
    source = tmp_path / "profile.dxf"
    _write_dxf(source)
    adapter = _FailingCleanupImportAdapter()
    mcp = FastMCP("test")
    await register_import_cad_tools(mcp, adapter, {})
    tool = next(item.fn for item in await mcp.list_tools() if item.name == "import_dxf_to_part")

    result = await tool(
        ImportDxfToPartInput(
            source_path=str(source), output_path=str(tmp_path / "profile.sldprt")
        )
    )

    assert result["status"] == "error"
    assert result["sketch_cleanup_error"] == "simulated exit failure"
    assert "partial sketch could not be exited" in result["message"]


def test_import_input_rejects_dwg() -> None:
    """The tool must guide callers away from unreliable DWG parsing."""
    with pytest.raises(ValueError, match="Convert it to a basic DXF"):
        ImportDxfToPartInput(source_path="input.dwg", output_path="output.sldprt")
