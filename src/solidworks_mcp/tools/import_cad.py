"""DXF-to-SolidWorks part import workflow tools.

The importer intentionally accepts only DXF. DWG can contain application-specific
objects and proxy entities, so callers should convert it to a basic DXF first.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import ezdxf
from fastmcp import FastMCP
from loguru import logger
from pydantic import Field, field_validator

from ..adapters.base import ExtrusionParameters, SolidWorksAdapter
from .input_compat import CompatInput

_DXF_UNIT_TO_MM = {
    0: 1.0,  # Unitless DXF is treated as mm, matching CAXA 2015 R12 output.
    1: 25.4,  # Inches
    2: 304.8,  # Feet
    4: 1.0,  # Millimetres
    5: 10.0,  # Centimetres
    6: 1000.0,  # Metres
    7: 1_000_000.0,  # Kilometres
}
_SKETCH_ENTITY_TYPES = frozenset({"ARC", "CIRCLE", "LINE", "LWPOLYLINE", "POLYLINE"})
_IGNORED_ENTITY_TYPES = frozenset({"MTEXT", "POINT", "TEXT"})


def _validate_dxf_source_path(value: str) -> str:
    """Require a DXF source path and give DWG callers an actionable route."""
    suffix = Path(value).suffix.lower()
    if suffix == ".dwg":
        raise ValueError(
            "DWG is not imported directly. Convert it to a basic DXF first, "
            "preferably with export_caxa_2015_dxf."
        )
    if suffix != ".dxf":
        raise ValueError("source_path must use the .dxf extension")
    return value


class ImportDxfToPartInput(CompatInput):
    """Input for creating a SolidWorks part from basic DXF geometry."""

    source_path: str = Field(description="Existing .dxf file containing 2D geometry")
    output_path: str = Field(
        description="New .sldprt path; existing files are never overwritten"
    )
    plane: Literal["Front", "Top", "Right"] = Field(
        default="Front", description="Part reference plane for the imported sketch"
    )
    source_units: Literal["auto", "mm", "inch"] = Field(
        default="auto",
        description="DXF coordinate units; auto reads $INSUNITS and treats R12 as mm",
    )
    extrusion_depth_mm: float | None = Field(
        default=None,
        gt=0,
        description="Optional positive depth to create a boss extrusion; omit for sketch-only part",
    )
    strict: bool = Field(
        default=True,
        description="Reject unsupported DXF objects instead of silently omitting geometry",
    )
    validate_geometry: bool = Field(
        default=True,
        description="Run DXF overlap/profile preflight before creating the SolidWorks part",
    )
    validation_tolerance_mm: float = Field(
        default=0.01,
        gt=0,
        le=1.0,
        description="Tolerance used by the optional DXF geometry preflight",
    )
    verify_with_solidworks: bool = Field(
        default=True,
        description="Before extrusion, call SolidWorks ISketch.CheckFeatureUse on the imported sketch",
    )

    @field_validator("source_path")
    @classmethod
    def _validate_source_path(cls, value: str) -> str:
        return _validate_dxf_source_path(value)

    @field_validator("output_path")
    @classmethod
    def _validate_output_path(cls, value: str) -> str:
        """Require an explicit native SolidWorks part destination."""
        if Path(value).suffix.lower() != ".sldprt":
            raise ValueError("output_path must use the .sldprt extension")
        return value


class ValidateDxfGeometryInput(CompatInput):
    """Input for a read-only preflight of DXF geometry before SolidWorks import."""

    source_path: str = Field(description="Existing .dxf file to inspect without editing it")
    source_units: Literal["auto", "mm", "inch"] = Field(
        default="auto",
        description="DXF coordinate units; auto reads $INSUNITS and treats R12 as mm",
    )
    tolerance_mm: float = Field(
        default=0.01,
        gt=0,
        le=1.0,
        description="Geometric comparison tolerance in millimetres",
    )
    max_pair_checks: int = Field(
        default=20_000,
        ge=1,
        le=100_000,
        description="Maximum line-pair comparisons before reporting a partial intersection scan",
    )
    strict: bool = Field(
        default=False,
        description="Reject unsupported DXF objects instead of reporting them and continuing",
    )

    @field_validator("source_path")
    @classmethod
    def _validate_source_path(cls, value: str) -> str:
        return _validate_dxf_source_path(value)


def _coordinate_scale_mm(document: Any, source_units: str) -> tuple[float, str]:
    """Return the source-coordinate scale in millimetres and its resolved unit."""
    if source_units == "mm":
        return 1.0, "mm"
    if source_units == "inch":
        return 25.4, "inch"
    insunits = int(document.header.get("$INSUNITS", 0) or 0)
    return _DXF_UNIT_TO_MM.get(insunits, 1.0), {
        1: "inch",
        2: "foot",
        4: "mm",
        5: "cm",
        6: "m",
        7: "km",
    }.get(insunits, "mm (R12/unitless default)")


def _arc_from_bulge(
    start: tuple[float, float], end: tuple[float, float], bulge: float
) -> tuple[float, float, float, float, float, float]:
    """Convert one DXF polyline bulge segment into a SolidWorks arc definition."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(dx, dy)
    if chord <= 1e-9:
        raise ValueError("DXF polyline contains a zero-length bulge segment")
    angle = 4.0 * math.atan(bulge)
    tangent = math.tan(angle / 2.0)
    if abs(tangent) <= 1e-12:
        raise ValueError("DXF polyline contains an invalid bulge value")
    offset = chord / (2.0 * tangent)
    midpoint_x = (start[0] + end[0]) / 2.0
    midpoint_y = (start[1] + end[1]) / 2.0
    center_x = midpoint_x - (dy / chord) * offset
    center_y = midpoint_y + (dx / chord) * offset
    return center_x, center_y, start[0], start[1], end[0], end[1]


def _polyline_segments(
    points: list[tuple[float, float, float]], closed: bool
) -> list[tuple[str, tuple[float, ...]]]:
    """Convert classic or lightweight polyline vertices into line/arc segments."""
    segments: list[tuple[str, tuple[float, ...]]] = []
    upper_bound = len(points) if closed else len(points) - 1
    for index in range(max(upper_bound, 0)):
        start = points[index]
        end = points[(index + 1) % len(points)]
        if abs(start[2]) <= 1e-12:
            segments.append(("LINE", (start[0], start[1], end[0], end[1])))
        else:
            segments.append(("ARC", _arc_from_bulge(start[:2], end[:2], start[2])))
    return segments


def _read_dxf_sketch_geometry(
    source_path: str, source_units: str, strict: bool
) -> tuple[list[tuple[str, tuple[float, ...]]], dict[str, Any]]:
    """Read CAXA-compatible DXF entities and normalize coordinates to millimetres."""
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"DXF source file does not exist: {source}")
    try:
        document = ezdxf.readfile(source)
    except Exception as exc:
        raise ValueError(f"Unable to read DXF source: {exc}") from exc

    scale, resolved_units = _coordinate_scale_mm(document, source_units)
    geometry: list[tuple[str, tuple[float, ...]]] = []
    encountered = Counter[str]()
    ignored = Counter[str]()
    unsupported = Counter[str]()
    for entity in document.modelspace():
        entity_type = entity.dxftype()
        encountered[entity_type] += 1
        if entity_type == "LINE":
            start, end = entity.dxf.start, entity.dxf.end
            geometry.append(
                (
                    "LINE",
                    (start.x * scale, start.y * scale, end.x * scale, end.y * scale),
                )
            )
        elif entity_type == "CIRCLE":
            center = entity.dxf.center
            geometry.append(
                ("CIRCLE", (center.x * scale, center.y * scale, entity.dxf.radius * scale))
            )
        elif entity_type == "ARC":
            center = entity.dxf.center
            radius = entity.dxf.radius * scale
            start_angle = math.radians(entity.dxf.start_angle)
            end_angle = math.radians(entity.dxf.end_angle)
            geometry.append(
                (
                    "ARC",
                    (
                        center.x * scale,
                        center.y * scale,
                        center.x * scale + radius * math.cos(start_angle),
                        center.y * scale + radius * math.sin(start_angle),
                        center.x * scale + radius * math.cos(end_angle),
                        center.y * scale + radius * math.sin(end_angle),
                    ),
                )
            )
        elif entity_type == "LWPOLYLINE":
            points = [
                (point[0] * scale, point[1] * scale, point[2])
                for point in entity.get_points("xyb")
            ]
            geometry.extend(_polyline_segments(points, entity.closed))
        elif entity_type == "POLYLINE":
            points = [
                (
                    vertex.dxf.location.x * scale,
                    vertex.dxf.location.y * scale,
                    float(getattr(vertex.dxf, "bulge", 0.0)),
                )
                for vertex in entity.vertices
            ]
            geometry.extend(_polyline_segments(points, bool(entity.is_closed)))
        elif entity_type in _IGNORED_ENTITY_TYPES:
            ignored[entity_type] += 1
        else:
            unsupported[entity_type] += 1

    if unsupported and strict:
        details = ", ".join(
            f"{name}={count}" for name, count in sorted(unsupported.items())
        )
        raise ValueError(
            "DXF contains unsupported entities for a SolidWorks sketch: "
            f"{details}. Export a CAXA 2015 R12 DXF or set strict=false to ignore them."
        )
    if not geometry:
        raise ValueError("DXF has no importable LINE, ARC, CIRCLE, or POLYLINE geometry")
    return geometry, {
        "source_units": resolved_units,
        "scale_to_mm": scale,
        "encountered_entities": dict(sorted(encountered.items())),
        "ignored_entities": dict(sorted(ignored.items())),
        "unsupported_entities": dict(sorted(unsupported.items())),
    }


def _point_key(point: tuple[float, float], tolerance_mm: float) -> tuple[int, int]:
    """Create a tolerance-aware point key suitable for a profile endpoint graph."""
    return round(point[0] / tolerance_mm), round(point[1] / tolerance_mm)


def _points_close(
    first: tuple[float, float], second: tuple[float, float], tolerance_mm: float
) -> bool:
    """Return whether two model-space points fall within the configured tolerance."""
    return math.dist(first, second) <= tolerance_mm


def _cross(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    """Return the two-dimensional cross product of two vectors."""
    return first[0] * second[1] - first[1] * second[0]


def _line_values(values: tuple[float, ...]) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return the endpoints of a normalized LINE geometry tuple."""
    return (values[0], values[1]), (values[2], values[3])


def _line_segments_overlap(
    first: tuple[float, ...], second: tuple[float, ...], tolerance_mm: float
) -> bool:
    """Detect a non-zero collinear overlap between two line segments."""
    start_a, end_a = _line_values(first)
    start_b, end_b = _line_values(second)
    direction_a = end_a[0] - start_a[0], end_a[1] - start_a[1]
    direction_b = end_b[0] - start_b[0], end_b[1] - start_b[1]
    length_a = math.hypot(*direction_a)
    length_b = math.hypot(*direction_b)
    if length_a <= tolerance_mm or length_b <= tolerance_mm:
        return False
    if abs(_cross(direction_a, direction_b)) > tolerance_mm * length_a:
        return False
    offset_b = start_b[0] - start_a[0], start_b[1] - start_a[1]
    if abs(_cross(offset_b, direction_a)) > tolerance_mm * length_a:
        return False
    unit = direction_a[0] / length_a, direction_a[1] / length_a
    first_range = 0.0, length_a
    second_start = offset_b[0] * unit[0] + offset_b[1] * unit[1]
    second_end = second_start + direction_b[0] * unit[0] + direction_b[1] * unit[1]
    overlap = min(max(first_range), max(second_start, second_end)) - max(
        min(first_range), min(second_start, second_end)
    )
    return overlap > tolerance_mm


def _non_endpoint_line_intersection(
    first: tuple[float, ...], second: tuple[float, ...], tolerance_mm: float
) -> tuple[float, float] | None:
    """Return an intersection that is not a shared endpoint of both line segments."""
    start_a, end_a = _line_values(first)
    start_b, end_b = _line_values(second)
    direction_a = end_a[0] - start_a[0], end_a[1] - start_a[1]
    direction_b = end_b[0] - start_b[0], end_b[1] - start_b[1]
    denominator = _cross(direction_a, direction_b)
    if abs(denominator) <= tolerance_mm:
        return None
    offset = start_b[0] - start_a[0], start_b[1] - start_a[1]
    first_factor = _cross(offset, direction_b) / denominator
    second_factor = _cross(offset, direction_a) / denominator
    if not (
        -tolerance_mm <= first_factor <= 1.0 + tolerance_mm
        and -tolerance_mm <= second_factor <= 1.0 + tolerance_mm
    ):
        return None
    intersection = (
        start_a[0] + first_factor * direction_a[0],
        start_a[1] + first_factor * direction_a[1],
    )
    first_endpoint = any(
        _points_close(intersection, point, tolerance_mm) for point in (start_a, end_a)
    )
    second_endpoint = any(
        _points_close(intersection, point, tolerance_mm) for point in (start_b, end_b)
    )
    return None if first_endpoint and second_endpoint else intersection


def _issue(
    severity: Literal["error", "warning"],
    code: str,
    message: str,
    entity_ids: list[int],
    **details: Any,
) -> dict[str, Any]:
    """Create one consistently shaped DXF preflight finding."""
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "entity_ids": entity_ids,
        **details,
    }


def _validate_dxf_geometry(
    geometry: list[tuple[str, tuple[float, ...]]],
    tolerance_mm: float,
    max_pair_checks: int,
) -> dict[str, Any]:
    """Analyze basic DXF sketch geometry without changing a CAD document.

    The preflight is deliberately conservative: it reports definite duplicate,
    overlap, endpoint, and straight-line crossing problems, while preserving
    arc-crossing analysis as an explicit limitation rather than approximating it.
    """
    issues: list[dict[str, Any]] = []
    signatures: dict[tuple[Any, ...], int] = {}
    endpoint_entities: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {}
    entities_by_node: dict[tuple[int, int], list[int]] = {}
    line_entities: list[tuple[int, tuple[float, ...]]] = []
    has_arc = False

    for entity_id, (entity_type, values) in enumerate(geometry, start=1):
        if entity_type == "LINE":
            start, end = _line_values(values)
            if _points_close(start, end, tolerance_mm):
                issues.append(
                    _issue(
                        "error",
                        "zero_length_line",
                        "Line has no usable length within the configured tolerance.",
                        [entity_id],
                    )
                )
            signature = ("LINE", *sorted((_point_key(start, tolerance_mm), _point_key(end, tolerance_mm))))
            endpoint_entities[entity_id] = (
                _point_key(start, tolerance_mm),
                _point_key(end, tolerance_mm),
            )
            line_entities.append((entity_id, values))
        elif entity_type == "CIRCLE":
            center = values[0], values[1]
            radius = values[2]
            if radius <= tolerance_mm:
                issues.append(
                    _issue(
                        "error",
                        "zero_radius_circle",
                        "Circle radius is zero or below the configured tolerance.",
                        [entity_id],
                    )
                )
            signature = ("CIRCLE", _point_key(center, tolerance_mm), round(radius / tolerance_mm))
        else:
            center = values[0], values[1]
            start = values[2], values[3]
            end = values[4], values[5]
            radius = math.dist(center, start)
            has_arc = True
            if radius <= tolerance_mm or _points_close(start, end, tolerance_mm):
                issues.append(
                    _issue(
                        "error",
                        "degenerate_arc",
                        "Arc has a zero radius or indistinguishable start and end points.",
                        [entity_id],
                    )
                )
            signature = (
                "ARC",
                _point_key(center, tolerance_mm),
                _point_key(start, tolerance_mm),
                _point_key(end, tolerance_mm),
            )
            endpoint_entities[entity_id] = (
                _point_key(start, tolerance_mm),
                _point_key(end, tolerance_mm),
            )

        original_entity = signatures.setdefault(signature, entity_id)
        if original_entity != entity_id:
            issues.append(
                _issue(
                    "error",
                    "duplicate_geometry",
                    "Geometry duplicates an earlier entity within the configured tolerance.",
                    [original_entity, entity_id],
                )
            )

    for entity_id, endpoints in endpoint_entities.items():
        for endpoint in endpoints:
            entities_by_node.setdefault(endpoint, []).append(entity_id)

    pair_checks = 0
    intersection_scan_truncated = False
    for left_index, (left_id, left_values) in enumerate(line_entities):
        for right_id, right_values in line_entities[left_index + 1 :]:
            pair_checks += 1
            if pair_checks > max_pair_checks:
                intersection_scan_truncated = True
                break
            if _line_segments_overlap(left_values, right_values, tolerance_mm):
                issues.append(
                    _issue(
                        "error",
                        "overlapping_line_segments",
                        "Collinear line segments overlap by more than the configured tolerance.",
                        [left_id, right_id],
                    )
                )
                continue
            intersection = _non_endpoint_line_intersection(
                left_values, right_values, tolerance_mm
            )
            if intersection is not None:
                issues.append(
                    _issue(
                        "warning",
                        "non_endpoint_line_intersection",
                        "Line segments cross or form a T-junction away from a shared endpoint.",
                        [left_id, right_id],
                        point_mm={"x": intersection[0], "y": intersection[1]},
                    )
                )
        if intersection_scan_truncated:
            break

    visited: set[int] = set()
    profiles = {"closed": 0, "open": 0, "branching": 0, "circles": 0}
    for entity_id, (first_node, second_node) in endpoint_entities.items():
        if entity_id in visited or first_node == second_node:
            continue
        pending = [entity_id]
        component_entities: set[int] = set()
        component_nodes: set[tuple[int, int]] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            component_entities.add(current)
            current_nodes = endpoint_entities[current]
            component_nodes.update(current_nodes)
            for node in current_nodes:
                pending.extend(entities_by_node[node])
        degrees = [
            sum(1 for entity in entities_by_node[node] if entity in component_entities)
            for node in component_nodes
        ]
        entity_ids = sorted(component_entities)
        if any(degree > 2 for degree in degrees):
            profiles["branching"] += 1
            issues.append(
                _issue(
                    "warning",
                    "branching_profile",
                    "Profile has a branch point and is ambiguous for a boss extrusion.",
                    entity_ids,
                )
            )
        elif all(degree == 2 for degree in degrees):
            profiles["closed"] += 1
        else:
            profiles["open"] += 1
            issues.append(
                _issue(
                    "warning",
                    "open_profile",
                    "Profile has unmatched endpoints and cannot form a closed boss contour.",
                    entity_ids,
                )
            )
    profiles["circles"] = sum(1 for entity_type, _ in geometry if entity_type == "CIRCLE")
    profiles["closed"] += profiles["circles"]

    error_count = sum(issue["severity"] == "error" for issue in issues)
    warning_count = len(issues) - error_count
    extrusion_blockers = {
        "branching_profile",
        "non_endpoint_line_intersection",
        "open_profile",
    }
    has_preflight_extrusion_blockers = any(
        issue["code"] in extrusion_blockers for issue in issues
    )
    analysis_complete_for_extrusion = not has_arc and not intersection_scan_truncated
    return {
        "is_valid_for_sketch": error_count == 0,
        "is_ready_for_extrusion": error_count == 0
        and not has_preflight_extrusion_blockers
        and analysis_complete_for_extrusion,
        "summary": {
            "importable_entity_count": len(geometry),
            "error_count": error_count,
            "warning_count": warning_count,
            "line_pair_checks": min(pair_checks, max_pair_checks),
            "analysis_complete_for_extrusion": analysis_complete_for_extrusion,
            "has_preflight_extrusion_blockers": has_preflight_extrusion_blockers,
        },
        "profiles": profiles,
        "issues": issues,
        "analysis_limitations": (
            [
                "Arc crossings are not approximated; arcs are checked for endpoint connectivity, "
                "degeneracy, and exact duplicates only."
            ]
            if has_arc
            else []
        )
        + (
            [
                "The line intersection scan reached max_pair_checks; increase it for a complete "
                "scan of large DXF files."
            ]
            if intersection_scan_truncated
            else []
        ),
    }


async def register_import_cad_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: dict[str, Any]
) -> int:
    """Register safe DXF-to-SolidWorks import workflows."""
    _ = config

    @mcp.tool()
    async def validate_dxf_geometry(
        input_data: ValidateDxfGeometryInput,
    ) -> dict[str, Any]:
        """Read and preflight basic DXF geometry without changing SolidWorks.

        Finds zero-length entities, duplicate geometry, collinear line overlap,
        open or branching line/arc profiles, and non-endpoint line crossings.
        Use it before `import_dxf_to_part`; inspect `is_ready_for_extrusion`
        before requesting an extrusion. Arc crossing analysis is explicitly
        reported as a limitation instead of using an unreliable approximation.
        """
        try:
            normalized = (
                input_data
                if isinstance(input_data, ValidateDxfGeometryInput)
                else ValidateDxfGeometryInput.model_validate(input_data)
            )
            geometry, source_report = _read_dxf_sketch_geometry(
                normalized.source_path, normalized.source_units, normalized.strict
            )
            validation = _validate_dxf_geometry(
                geometry, normalized.tolerance_mm, normalized.max_pair_checks
            )
            summary = validation["summary"]
            if summary["error_count"]:
                message = (
                    "DXF validation found errors. Correct them before importing or "
                    "creating an extrusion."
                )
            elif validation["is_ready_for_extrusion"]:
                message = "DXF validation found no detected blockers for a sketch or boss extrusion."
            else:
                message = (
                    "DXF can be imported as a sketch, but review warnings before requesting "
                    "an extrusion."
                )
            return {
                "status": "success",
                "message": message,
                "validation": validation,
                "source_report": source_report,
            }
        except Exception as exc:
            logger.error("Error in validate_dxf_geometry tool: {}", exc)
            return {
                "status": "error",
                "message": f"DXF validation failed: {exc}",
            }

    @mcp.tool()
    async def import_dxf_to_part(input_data: ImportDxfToPartInput) -> dict[str, Any]:
        """Create a new SolidWorks part from basic DXF geometry.

        Imports LINE, ARC, CIRCLE and POLYLINE entities into a new sketch on a
        reference plane. Optionally creates a boss extrusion. Text and points
        are reported but not added to the sketch; DWG input is rejected with a
        conversion recommendation. Geometry is preflighted by default, and the
        output path must be a new `.sldprt`.
        """
        sketch_is_open = False
        response: dict[str, Any] | None = None
        try:
            normalized = (
                input_data
                if isinstance(input_data, ImportDxfToPartInput)
                else ImportDxfToPartInput.model_validate(input_data)
            )
            output = Path(normalized.output_path)
            if output.exists():
                return {
                    "status": "error",
                    "message": f"Refusing to overwrite existing SolidWorks part: {output}",
                }
            if not output.parent.is_dir():
                return {
                    "status": "error",
                    "message": f"Output directory does not exist: {output.parent}",
                }

            geometry, report = _read_dxf_sketch_geometry(
                normalized.source_path, normalized.source_units, normalized.strict
            )
            validation: dict[str, Any] | None = None
            if normalized.validate_geometry:
                validation = _validate_dxf_geometry(
                    geometry, normalized.validation_tolerance_mm, max_pair_checks=20_000
                )
                if not validation["is_valid_for_sketch"]:
                    return {
                        "status": "error",
                        "message": (
                            "DXF preflight found geometry errors; correct duplicate, overlapping, "
                            "or degenerate entities before importing."
                        ),
                        "validation": validation,
                        "source_report": report,
                    }
                if (
                    normalized.extrusion_depth_mm is not None
                    and validation["summary"]["has_preflight_extrusion_blockers"]
                ):
                    return {
                        "status": "error",
                        "message": (
                            "DXF preflight found an open, branching, or intersecting profile. "
                            "Correct it before requesting an extrusion, or import a sketch without "
                            "extrusion_depth_mm."
                        ),
                        "validation": validation,
                        "source_report": report,
                    }
            created_part = await adapter.create_part(output.stem, "mm")
            if not created_part.is_success:
                return {
                    "status": "error",
                    "message": f"Failed to create SolidWorks part: {created_part.error}",
                }
            sketch = await adapter.create_sketch(normalized.plane)
            if not sketch.is_success:
                return {
                    "status": "error",
                    "message": f"Failed to create import sketch: {sketch.error}",
                }
            sketch_is_open = True
            sketch_name = sketch.data if isinstance(sketch.data, str) else "Imported DXF"
            created = Counter[str]()
            for entity_type, values in geometry:
                if entity_type == "LINE":
                    result = await adapter.add_line(*values)
                elif entity_type == "ARC":
                    result = await adapter.add_arc(*values)
                else:
                    result = await adapter.add_circle(*values)
                if not result.is_success:
                    response = {
                        "status": "error",
                        "message": (
                            f"Failed to import {entity_type} into SolidWorks sketch: "
                            f"{result.error}"
                        ),
                        "imported_before_failure": dict(created),
                    }
                    return response
                created[entity_type] += 1

            exited = await adapter.exit_sketch()
            if not exited.is_success:
                return {
                    "status": "error",
                    "message": f"Failed to exit imported sketch: {exited.error}",
                }
            sketch_is_open = False
            feature_name: str | None = None
            solidworks_validation: dict[str, Any] | None = None
            if normalized.extrusion_depth_mm is not None and normalized.verify_with_solidworks:
                native_validation = await adapter.check_sketch_feature_use(
                    sketch_name, "boss_extrude"
                )
                if not native_validation.is_success:
                    return {
                        "status": "error",
                        "message": (
                            "SolidWorks could not verify the imported sketch for a boss extrusion: "
                            f"{native_validation.error}"
                        ),
                        "sketch": sketch_name,
                        "validation": validation,
                    }
                solidworks_validation = native_validation.data or {}
                if not solidworks_validation.get("is_valid"):
                    return {
                        "status": "error",
                        "message": (
                            "SolidWorks reports the imported sketch is invalid for a boss extrusion: "
                            f"{solidworks_validation.get('status', 'unknown')}"
                        ),
                        "sketch": sketch_name,
                        "solidworks_validation": solidworks_validation,
                        "validation": validation,
                    }
            if normalized.extrusion_depth_mm is not None:
                extrusion = await adapter.create_extrusion(
                    ExtrusionParameters(depth=normalized.extrusion_depth_mm)
                )
                if not extrusion.is_success:
                    return {
                        "status": "error",
                        "message": (
                            "DXF sketch was created but SolidWorks could not extrude it. "
                            "Ensure the DXF contains a closed, non-self-intersecting profile: "
                            f"{extrusion.error}"
                        ),
                        "sketch": sketch_name,
                    }
                feature_name = getattr(extrusion.data, "name", None)

            saved = await adapter.save_file(str(output))
            if not saved.is_success:
                return {
                    "status": "error",
                    "message": f"Part was created but could not be saved: {saved.error}",
                    "sketch": sketch_name,
                }
            return {
                "status": "success",
                "message": f"Created SolidWorks part from DXF: {output}",
                "part": {
                    "file_path": str(output),
                    "sketch": sketch_name,
                    "extrusion_feature": feature_name,
                    "extrusion_depth_mm": normalized.extrusion_depth_mm,
                },
                "imported_entities": dict(sorted(created.items())),
                "source_report": report,
                "validation": validation,
                "solidworks_validation": solidworks_validation,
            }
        except Exception as exc:
            logger.error("Error in import_dxf_to_part tool: {}", exc)
            response = {"status": "error", "message": f"DXF import failed: {exc}"}
            return response
        finally:
            if sketch_is_open:
                try:
                    cleanup = await adapter.exit_sketch()
                    if not cleanup.is_success:
                        cleanup_error = cleanup.error or "SolidWorks returned an unknown error"
                        logger.warning(
                            "Failed to exit partially imported DXF sketch: {}", cleanup_error
                        )
                        if response is not None:
                            response["sketch_cleanup_error"] = cleanup_error
                            response["message"] = (
                                f"{response['message']} The partial sketch could not be exited: "
                                f"{cleanup_error}"
                            )
                except Exception as cleanup_error:
                    logger.warning(
                        "Failed to exit partially imported DXF sketch: {}", cleanup_error
                    )
                    if response is not None:
                        response["sketch_cleanup_error"] = str(cleanup_error)
                        response["message"] = (
                            f"{response['message']} The partial sketch could not be exited: "
                            f"{cleanup_error}"
                        )

    return 2
