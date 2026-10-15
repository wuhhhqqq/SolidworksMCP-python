"""Tests for the CAXA 2015 DXF normalization path."""

from pathlib import Path

import ezdxf

from solidworks_mcp.adapters.dxf_caxa import (
    inspect_caxa_2015_compatibility,
    normalize_for_caxa_2015,
)


def test_normalize_for_caxa_2015_flattens_blocks_and_removes_unsupported_entities(
    tmp_path: Path,
) -> None:
    """The generated derivative contains only basic R12 model-space entities."""
    source = tmp_path / "solidworks-source.dxf"
    destination = tmp_path / "caxa2015.dxf"
    document = ezdxf.new("R2010")
    block = document.blocks.new("VIEW_BLOCK")
    block.add_line((0, 0), (20, 0))
    modelspace = document.modelspace()
    modelspace.add_blockref("VIEW_BLOCK", (10, 10))
    modelspace.add_lwpolyline([(0, 0), (10, 0), (10, 10)])
    modelspace.add_spline([(0, 0), (5, 10), (10, 0)])
    document.saveas(source)

    result = normalize_for_caxa_2015(str(source), str(destination))
    inspection = inspect_caxa_2015_compatibility(str(destination))

    assert result["dxf_version"] == "R12"
    assert result["expanded_blocks"] == 1
    assert result["converted_lwpolylines"] == 1
    assert result["dropped_entities"] == {"SPLINE": 1}
    assert inspection["compatible"] is True
    assert inspection["entity_counts"] == {"LINE": 1, "POLYLINE": 1}
