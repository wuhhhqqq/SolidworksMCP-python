"""DXF normalization for CAXA 2015's basic-entity reader.

SolidWorks drawings commonly export nested blocks, dimensions and modern DXF
entities.  CAXA 2015 can render many of these objects, but its CRX API cannot
reliably enumerate them.  This module creates a deliberately conservative R12
derivative containing only entities the legacy reader can consume.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.addons.importer import Importer

CAXA_2015_PROFILE = "caxa_2015"
CAXA_2015_DXF_VERSION = "AC1009"  # AutoCAD R12

# These R12 entities are straightforward for CAXA 2015 / CRX to enumerate.
_SUPPORTED_TYPES = frozenset(
    {"ARC", "CIRCLE", "LINE", "POINT", "POLYLINE", "SOLID", "TEXT", "TRACE"}
)


class Caxa2015DxfError(RuntimeError):
    """Raised when a DXF cannot be made safe for CAXA 2015."""


def _explode_blocks(modelspace: Any) -> int:
    """Recursively replace INSERT entities with their transformed contents."""
    count = 0
    # Exploding a block can reveal nested INSERT objects.  A cyclic block
    # definition is invalid DXF; the bound prevents an invalid input looping.
    for _ in range(32):
        inserts = list(modelspace.query("INSERT"))
        if not inserts:
            return count
        for insert in inserts:
            try:
                insert.explode(target_layout=modelspace)
            except Exception as exc:  # ezdxf exposes several version-specific errors
                name = getattr(insert.dxf, "name", "<unnamed>")
                raise Caxa2015DxfError(f"Unable to explode DXF block '{name}': {exc}") from exc
            count += 1
    raise Caxa2015DxfError("DXF block nesting exceeds the CAXA 2015 safety limit")


def _convert_lwpolylines(modelspace: Any) -> int:
    """Replace post-R12 LWPOLYLINE objects with classic POLYLINE entities."""
    converted = 0
    for entity in list(modelspace.query("LWPOLYLINE")):
        points = list(entity.get_points("xyseb"))
        attributes = {
            key: getattr(entity.dxf, key)
            for key in ("layer", "color", "linetype", "lineweight")
            if entity.dxf.hasattr(key)
        }
        polyline = modelspace.add_polyline2d(
            [(point[0], point[1]) for point in points], dxfattribs=attributes
        )
        for vertex, point in zip(polyline.vertices, points, strict=True):
            if point[2]:
                vertex.dxf.start_width = point[2]
            if point[3]:
                vertex.dxf.end_width = point[3]
            if point[4]:
                vertex.dxf.bulge = point[4]
        polyline.close(entity.closed)
        modelspace.delete_entity(entity)
        converted += 1
    return converted


def normalize_for_caxa_2015(source_path: str, destination_path: str) -> dict[str, Any]:
    """Write a CAXA 2015-safe R12 DXF from a SolidWorks DXF export.

    Blocks are expanded into model-space entities and LWPOLYLINE is converted
    to R12 POLYLINE.  Dimensions, hatches, splines, proxy objects and other
    entities outside the small CRX-safe subset are intentionally removed.
    Their removal is reported to the caller, so a drawing's annotations are
    never silently represented as usable geometry.
    """
    source = Path(source_path)
    destination = Path(destination_path)
    if not source.is_file():
        raise Caxa2015DxfError(f"SolidWorks did not create the temporary DXF: {source}")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing CAXA DXF: {destination}")

    try:
        document = ezdxf.readfile(source)
    except Exception as exc:
        raise Caxa2015DxfError(f"Unable to read SolidWorks DXF: {exc}") from exc

    modelspace = document.modelspace()
    expanded_blocks = _explode_blocks(modelspace)
    converted_polylines = _convert_lwpolylines(modelspace)
    dropped = Counter[str]()
    for entity in list(modelspace):
        if entity.dxftype() not in _SUPPORTED_TYPES:
            dropped[entity.dxftype()] += 1
            modelspace.delete_entity(entity)

    # Writing a newer document after merely changing ``dxfversion`` can retain
    # post-R12 table/object records. Re-importing into a new R12 document makes
    # the compatibility guarantee structural instead of a header-only claim.
    r12_document = ezdxf.new(CAXA_2015_DXF_VERSION)
    importer = Importer(document, r12_document)
    importer.import_modelspace()
    importer.finalize()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        r12_document.saveas(destination)
    except Exception as exc:
        raise Caxa2015DxfError(f"Unable to write CAXA 2015 DXF: {exc}") from exc

    return {
        "profile": CAXA_2015_PROFILE,
        "dxf_version": "R12",
        "expanded_blocks": expanded_blocks,
        "converted_lwpolylines": converted_polylines,
        "dropped_entities": dict(sorted(dropped.items())),
        "remaining_entities": sum(1 for _ in r12_document.modelspace()),
    }


def inspect_caxa_2015_compatibility(file_path: str) -> dict[str, Any]:
    """Report entities that CAXA 2015's CRX reader cannot safely enumerate."""
    try:
        document = ezdxf.readfile(file_path)
    except Exception as exc:
        raise Caxa2015DxfError(f"Unable to read DXF for compatibility inspection: {exc}") from exc

    entities = Counter(entity.dxftype() for entity in document.modelspace())
    unsupported = {
        name: count for name, count in entities.items() if name not in _SUPPORTED_TYPES
    }
    return {
        "dxf_version": document.dxfversion,
        "entity_counts": dict(sorted(entities.items())),
        "unsupported_entities": dict(sorted(unsupported.items())),
        "compatible": not unsupported and document.dxfversion == CAXA_2015_DXF_VERSION,
    }
