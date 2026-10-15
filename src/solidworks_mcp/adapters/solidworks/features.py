"""Feature-domain mixin for PyWin32 SolidWorks operations."""

from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

from ..base import (
    ADAPTER_ERROR_CHAMFER_CREATION,
    ADAPTER_ERROR_CHAMFER_EDGE_SELECTION,
    ADAPTER_ERROR_CHAMFER_SELECTION_COUNT,
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    FeatureCircularPatternParameters,
    FeatureLinearPatternParameters,
    FeatureMirrorParameters,
    HoleWizardParameters,
    LoftParameters,
    OriginPlaneParameters,
    ReferenceAxisParameters,
    ReferencePlaneParameters,
    RevolveParameters,
    SolidWorksFeature,
    SweepParameters,
    ThreadedHoleParameters,
)

# SolidWorks.Interop.swconst values used by the deliberately narrow typed
# feature presets below. Keeping these named makes the COM argument lists
# auditable without making the optional interop assembly a Python dependency.
_SW_FEATURE_SCOPE_ALL_BODIES = 0
_SW_WZD_HOLE = 2
_SW_STANDARD_ANSI_METRIC = 1
_SW_ANSI_METRIC_SCREW_CLEARANCES = 40
_SW_END_COND_BLIND = 0
_SW_ANSI_METRIC_CLEARANCE_SEED_SIZE = "M6"
_SW_WZD_TAP = 4
_SW_ANSI_METRIC_TAPPED_HOLE = 43
_SW_TAP_BLIND = 31
_SW_TAP_THROUGH = 33


def _tapped_hole_seed_size(thread_size: str) -> str:
    """Return the nominal size expected by the Hole Wizard database."""
    return thread_size.split("x", 1)[0]
_SW_FM_BASE_FLANGE = 34
# swBendAllowanceTypes_e.swBendAllowanceKFactor.  Read from the local
# swconst.tlb rather than assuming that assigning KFactor switches modes.
_SW_BEND_ALLOWANCE_K_FACTOR = 2
# swSheetMetalOverrideDefaultParameters_e.BendAllowance.
_SW_SHEET_METAL_OVERRIDE_DEFAULT_BEND_ALLOWANCE = 1
_SW_DRAFT_TYPE_NEUTRAL_PLANE = 0
_SW_THICKEN_DIRECTIONS = {"side1": 0, "side2": 1, "both": 2}
_SW_WRAP_OPERATIONS = {"emboss": 0, "engrave": 1, "scribe": 2}
_SW_WRAP_METHODS = {"analytical": 0, "spline_surface": 1}


class SolidWorksFeaturesMixin:
    """Expose SolidWorks feature methods via mixin-local implementation helpers."""

    async def create_extrusion(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_extrusion_impl(self, params)

    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_revolve_impl(self, params)

    async def create_reference_plane(
        self, params: ReferencePlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_reference_plane_impl(self, params)

    async def get_reference_axis(
        self, params: ReferenceAxisParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _get_reference_axis_impl(self, params)

    async def get_reference_plane(
        self, params: OriginPlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _get_reference_plane_impl(self, params)

    async def create_feature_linear_pattern(
        self, params: FeatureLinearPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_feature_linear_pattern_impl(self, params)

    async def create_feature_circular_pattern(
        self, params: FeatureCircularPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_feature_circular_pattern_impl(self, params)

    async def mirror_feature(
        self, params: FeatureMirrorParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _mirror_feature_impl(self, params)

    async def create_hole_wizard(
        self, params: HoleWizardParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_hole_wizard_impl(self, params)

    async def create_tapped_hole(
        self, params: ThreadedHoleParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_tapped_hole_impl(self, params)

    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_sweep_impl(self, params)

    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_loft_impl(self, params)

    async def create_cut_extrude(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_cut_extrude_impl(self, params)

    async def create_sheet_metal_base_flange(
        self, sketch_name: str, thickness_mm: float, bend_radius_mm: float, k_factor: float
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_sheet_metal_base_flange_impl(
            self, sketch_name, thickness_mm, bend_radius_mm, k_factor
        )

    async def create_sheet_metal_edge_flange(
        self,
        edge_persistent_reference: str,
        length_mm: float,
        angle_degrees: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_sheet_metal_edge_flange_impl(
            self,
            edge_persistent_reference,
            length_mm,
            angle_degrees,
            bend_radius_mm,
            k_factor,
        )

    async def set_sheet_metal_k_factor(
        self, k_factor: float
    ) -> AdapterResult[dict[str, Any]]:
        return _set_sheet_metal_k_factor_impl(self, k_factor)

    async def create_shell(
        self, face_persistent_references: list[str], thickness_mm: float, outward: bool
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_shell_impl(self, face_persistent_references, thickness_mm, outward)

    async def create_rib(
        self,
        sketch_name: str,
        thickness_mm: float,
        draft_angle_degrees: float,
        direction: str,
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_rib_impl(
            self, sketch_name, thickness_mm, draft_angle_degrees, direction
        )

    async def create_dome(
        self, face_persistent_reference: str, height_mm: float, reverse_direction: bool
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_dome_impl(self, face_persistent_reference, height_mm, reverse_direction)

    async def create_draft(
        self,
        face_persistent_references: list[str],
        angle_degrees: float,
        reverse_direction: bool,
        edge_draft: bool,
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_draft_impl(
            self,
            face_persistent_references,
            angle_degrees,
            reverse_direction,
            edge_draft,
        )

    async def create_thicken(
        self,
        face_persistent_reference: str,
        thickness_mm: float,
        operation: str,
        direction: str,
        fill_volume: bool,
        merge_result: bool,
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_thicken_impl(
            self,
            face_persistent_reference,
            thickness_mm,
            operation,
            direction,
            fill_volume,
            merge_result,
        )

    async def create_wrap(
        self,
        sketch_name: str,
        face_persistent_reference: str,
        operation: str,
        thickness_mm: float,
        reverse_direction: bool,
        method: str,
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_wrap_impl(
            self,
            sketch_name,
            face_persistent_reference,
            operation,
            thickness_mm,
            reverse_direction,
            method,
        )

    async def add_fillet(
        self, radius: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        return _add_fillet_impl(self, radius, edge_names)

    async def add_chamfer(
        self, distance: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        return _add_chamfer_impl(self, distance, edge_names)

    async def create_chamfer(
        self,
        edge_persistent_references: list[str],
        distance_mm: float,
        angle_degrees: float,
        chamfer_type: str,
    ) -> AdapterResult[SolidWorksFeature]:
        return _create_chamfer_impl(
            self,
            edge_persistent_references,
            distance_mm,
            angle_degrees,
            chamfer_type,
        )


def _create_extrusion_impl(
    adapter: Any, params: ExtrusionParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create a boss-extrude feature from the active sketch profile.

    Attempts the modern ``FeatureExtrusion3`` COM call first; falls back to the
    legacy ``FeatureExtrusion2`` signature when the newer overload is absent.
    When ``params.thin_feature`` is truthy, the thin-wall variants
    (``FeatureExtrusionThin2`` / ``FeatureExtruThin2``) are used instead.

    All depth and thickness values are provided in millimetres and converted
    to metres internally.

    Args:
        adapter: A fully connected ``PyWin32Adapter`` instance.  Must have a
            non-``None`` ``currentModel`` and a valid ``FeatureManager``.
        params: Extrusion parameter bag.  Relevant fields:
            - ``depth`` (float): Extrude depth in mm.
            - ``draft_angle`` (float): Draft angle in degrees.  Default 0.
            - ``reverse_direction`` (bool): Flip the extrusion direction.
            - ``thin_feature`` (bool): Produce a thin-wall body.
            - ``thin_thickness`` (float | None): Wall thickness in mm when
              ``thin_feature`` is ``True``.
            - ``merge_result`` (bool): Merge with existing bodies.  Default
              ``True``.
            - ``both_directions`` (bool): Extrude symmetrically in both
              directions from the sketch plane.
            - ``auto_fillet_corners`` (bool): Round sharp thin-wall corners.
            - ``fillet_corners_radius`` (float): Corner fillet radius in mm.

    Returns:
        AdapterResult[SolidWorksFeature]: On success, ``data`` is a
        ``SolidWorksFeature`` whose ``type`` is ``"Extrusion"``.  On failure,
        ``status`` is ``ERROR`` and ``error`` contains a descriptive message.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when the COM
            call returns ``None`` for the created feature object.

    Example::

        from solidworks_mcp.adapters.base import ExtrusionParameters
        from solidworks_mcp.adapters import pywin32_feature_ops

        params = ExtrusionParameters(depth=25.0, draft_angle=2.0)
        result = pywin32_feature_ops.create_extrusion(adapter, params)
        print(result.data.name)  # e.g. "Boss-Extrude1"
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _extrusion_operation() -> SolidWorksFeature:  # pragma: no cover
        """Inner COM closure that builds and returns the extrusion feature.

        Normalises ``params`` into a ``SimpleNamespace`` so every attribute
        access is guaranteed safe regardless of the dataclass version.  Picks
        the thin-wall or solid branch, then tries the modern API first before
        falling back to the legacy one.

        Returns:
            SolidWorksFeature: Populated feature descriptor on success.

        Raises:
            Exception: If both API variants return ``None``.
        """
        normalized = SimpleNamespace(
            depth=float(getattr(params, "depth", 0.0)),
            draft_angle=float(getattr(params, "draft_angle", 0.0)),
            reverse_direction=bool(getattr(params, "reverse_direction", False)),
            thin_feature=bool(getattr(params, "thin_feature", False)),
            thin_thickness=getattr(params, "thin_thickness", None),
            merge_result=bool(getattr(params, "merge_result", True)),
            both_directions=bool(getattr(params, "both_directions", False)),
            auto_fillet_corners=bool(getattr(params, "auto_fillet_corners", False)),
            fillet_corners_radius=float(getattr(params, "fillet_corners_radius", 0.0)),
        )
        feature_manager = adapter.currentModel.FeatureManager
        # Boss extrusion has the same late-bound COM behavior as cut extrude:
        # select the tracked profile when available, then use the implicit
        # sketch context only for untracked legacy callers.
        _select_cut_profile(adapter)
        features_before = _feature_tree_snapshot(adapter)

        if normalized.thin_feature and normalized.thin_thickness:
            t0 = adapter.constants.get("swStartSketchPlane", 0)
            t1 = (
                adapter.constants["swEndCondMidPlane"]
                if normalized.both_directions
                else adapter.constants["swEndCondBlind"]
            )
            try:
                feature = feature_manager.FeatureExtrusionThin2(
                    True,
                    False,
                    normalized.reverse_direction,
                    t1,
                    adapter.constants["swEndCondBlind"],
                    normalized.depth / 1000.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.draft_angle * 3.14159 / 180.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.merge_result,
                    normalized.thin_thickness / 1000.0,
                    normalized.thin_thickness / 1000.0,
                    0.0,
                    0,
                    0,
                    normalized.auto_fillet_corners,
                    normalized.fillet_corners_radius / 1000.0,
                    False,
                    True,
                    t0,
                    0.0,
                    False,
                )
            except Exception:
                feature = feature_manager.FeatureExtruThin2(
                    normalized.depth / 1000.0,
                    0.0,
                    False,
                    normalized.draft_angle * 3.14159 / 180.0,
                    0.0,
                    False,
                    False,
                    normalized.merge_result,
                    False,
                    True,
                    normalized.thin_thickness / 1000.0,
                    normalized.thin_thickness / 1000.0,
                    False,
                    False,
                    False,
                    adapter.constants["swEndCondBlind"],
                    adapter.constants["swEndCondBlind"],
                )
        else:
            t0 = adapter.constants.get("swStartSketchPlane", 0)
            try:
                feature = feature_manager.FeatureExtrusion3(
                    True,
                    False,
                    normalized.reverse_direction,
                    adapter.constants["swEndCondBlind"],
                    adapter.constants["swEndCondBlind"],
                    normalized.depth / 1000.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.draft_angle * 3.14159 / 180.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.merge_result,
                    False,
                    True,
                    t0,
                    0.0,
                    False,
                )
            except Exception:
                feature = feature_manager.FeatureExtrusion2(
                    True,
                    False,
                    normalized.reverse_direction,
                    adapter.constants["swEndCondBlind"],
                    adapter.constants["swEndCondBlind"],
                    normalized.depth / 1000.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.draft_angle * 3.14159 / 180.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.merge_result,
                    False,
                    True,
                    t0,
                    0.0,
                    False,
                )

        if not feature:
            feature = _recover_new_feature(adapter, features_before, "Extrusion")
        if not feature:
            raise Exception("Failed to create extrusion feature")

        return SolidWorksFeature(
            name=feature.Name,
            type="Extrusion",
            id=adapter._get_feature_id(feature),
            parameters={
                "depth": normalized.depth,
                "draft_angle": normalized.draft_angle,
                "reverse_direction": normalized.reverse_direction,
                "thin_feature": normalized.thin_feature,
                "thin_thickness": normalized.thin_thickness,
            },
            properties={"created": datetime.now().isoformat()},
        )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_extrusion", _extrusion_operation),
    )


def _create_reference_plane_impl(
    adapter: Any, params: ReferencePlaneParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create a distance-offset plane from one standard reference plane.

    ``IFeatureManager::InsertRefPlane`` consumes the currently selected base
    plane. The typed workflow restricts that input to Front, Top, or Right;
    this helper resolves the corresponding native feature and always clears
    the COM selection set when the operation completes or fails.
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _reference_plane_operation() -> SolidWorksFeature:  # pragma: no cover
        adapter._attempt(
            lambda: adapter.currentModel.ClearSelection2(True), default=None
        )
        try:
            try:
                base_plane = _standard_origin_plane(adapter, params.base_plane)
            except Exception:
                # Keep the name-based selection path for English documents whose
                # origin planes cannot be enumerated through late-bound COM.
                base_plane = None
            plane_name = str(
                _read_member(base_plane, "Name") if base_plane else ""
            ).strip() or f"{params.base_plane} Plane"
            selected = bool(base_plane) and adapter._attempt(
                lambda: base_plane.Select2(False, 0), default=False
            )
            if not selected:
                selected = adapter._attempt(
                    lambda: adapter.currentModel.Extension.SelectByID2(
                        plane_name,
                        "PLANE",
                        0,
                        0,
                        0,
                        False,
                        0,
                        None,
                        0,
                    ),
                    default=False,
                )
            if not selected:
                raise Exception(f"Failed to select reference plane: {plane_name}")
            # The reference-plane API represents direction with the OptionFlip
            # bit, not a negative distance. Keep the physical distance positive
            # so COM applies the requested normal-direction inversion reliably.
            first_constraint = 8 | (256 if params.reverse_direction else 0)
            features_before = _feature_tree_snapshot(adapter)
            reference_plane = adapter.currentModel.FeatureManager.InsertRefPlane(
                first_constraint,
                params.offset / 1000.0,
                0,
                0.0,
                0,
                0.0,
            )
            if not reference_plane:
                reference_plane = _recover_new_feature(
                    adapter, features_before, "ReferencePlane"
                )
            if not reference_plane:
                raise Exception("Failed to create reference plane")
            name = str(_read_member(reference_plane, "Name") or "").strip()
            if not name:
                raise Exception("SolidWorks created a reference plane without a name")
            return SolidWorksFeature(
                name=name,
                type="ReferencePlane",
                id=adapter._get_feature_id(reference_plane),
                parameters={
                    "base_plane": params.base_plane,
                    "offset": params.offset,
                    "reverse_direction": params.reverse_direction,
                },
                properties={"created": datetime.now().isoformat()},
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation(
            "create_reference_plane", _reference_plane_operation
        ),
    )


def _get_reference_axis_impl(
    adapter: Any, params: ReferenceAxisParameters
) -> AdapterResult[SolidWorksFeature]:
    """Resolve one of the active part's built-in origin axes.

    The typed workflow exposes only X, Y, and Z origin axes. Localized
    templates occasionally do not expose their built-in axes through the COM
    feature tree; in that case the resolver materializes one clearly named
    reference-axis helper from the corresponding pair of origin planes.
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _axis_operation() -> SolidWorksFeature:  # pragma: no cover
        adapter._attempt(
            lambda: adapter.currentModel.ClearSelection2(True), default=None
        )
        try:
            axis = _standard_origin_axis(adapter, params.axis)
            if not axis:
                raise Exception(f"Standard origin axis is unavailable: {params.axis}")
            name = str(_read_member(axis, "Name") or "").strip()
            if not name:
                raise Exception("SolidWorks returned an origin axis without a name")
            return SolidWorksFeature(
                name=name,
                type="OriginAxis",
                id=adapter._get_feature_id(axis),
                parameters={"axis": params.axis},
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("get_reference_axis", _axis_operation),
    )


def _get_reference_plane_impl(
    adapter: Any, params: OriginPlaneParameters
) -> AdapterResult[SolidWorksFeature]:
    """Resolve one of the active part's built-in origin planes."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _plane_operation() -> SolidWorksFeature:  # pragma: no cover
        plane = _standard_origin_plane(adapter, params.plane)
        if not plane:
            raise Exception(f"Standard origin plane is unavailable: {params.plane}")
        name = str(_read_member(plane, "Name") or "").strip()
        if not name:
            raise Exception("SolidWorks returned an origin plane without a name")
        return SolidWorksFeature(
            name=name,
            type="OriginPlane",
            id=adapter._get_feature_id(plane),
            parameters={"plane": params.plane},
        )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("get_reference_plane", _plane_operation),
    )


def _standard_origin_plane(adapter: Any, plane: str) -> Any:
    """Resolve a standard plane without depending on the UI language.

    SolidWorks localizes the display names of Front/Top/Right planes, but keeps
    their ``RefPlane`` type and feature-tree order stable. Prefer the English
    name for existing English templates, then use the first three ``RefPlane``
    features for localized templates.
    """
    order = {"Front": 0, "Top": 1, "Right": 2}
    try:
        index = order[plane]
    except KeyError as exc:
        raise Exception(f"Unsupported standard origin plane: {plane}") from exc

    named_plane = adapter._attempt(
        lambda: adapter.currentModel.FeatureByName(f"{plane} Plane"), default=None
    )
    if named_plane:
        return named_plane

    _flag_feature_methods(adapter.currentModel, "IModelDoc2")
    feature = _read_member(adapter.currentModel, "FirstFeature")
    origin_planes: list[Any] = []
    seen: set[int] = set()
    for _ in range(256):
        if not feature or id(feature) in seen:
            break
        seen.add(id(feature))
        _flag_feature_methods(feature, "IFeature")
        feature_type = str(_read_member(feature, "GetTypeName2") or "")
        if feature_type.casefold() == "refplane":
            origin_planes.append(feature)
            if len(origin_planes) > index:
                return origin_planes[index]
        feature = _read_member(feature, "GetNextFeature")

    # Some late-bound pywin32 installations expose ``FirstFeature`` as an
    # ambiguous zero-argument member. ``FeatureByPositionReverse`` is stable
    # there: the origin appears first, followed by Right, Top, and Front.
    origin_seen = False
    reverse_order_planes: list[Any] = []
    for position in range(256):
        feature = adapter._attempt(
            lambda position=position: adapter.currentModel.FeatureByPositionReverse(
                position
            ),
            default=None,
        )
        if not feature:
            break
        _flag_feature_methods(feature, "IFeature")
        feature_type = str(_read_member(feature, "GetTypeName2") or "")
        if feature_type.casefold() == "originprofilefeature":
            origin_seen = True
            continue
        if origin_seen and feature_type.casefold() == "refplane":
            reverse_order_planes.append(feature)
            if len(reverse_order_planes) == 3:
                return reverse_order_planes[2 - index]

    raise Exception(
        f"Standard origin plane is unavailable: {plane} ({plane} Plane)"
    )


def _standard_origin_axis(adapter: Any, axis: str) -> Any:
    """Resolve a standard origin axis without depending on UI-localized names."""
    if axis not in {"X", "Y", "Z"}:
        raise Exception(f"Unsupported standard origin axis: {axis}")
    for candidate in (f"{axis} Axis", f"{axis} axis"):
        reference_axis = adapter._attempt(
            lambda name=candidate: adapter.currentModel.FeatureByName(name),
            default=None,
        )
        if reference_axis:
            return reference_axis
    helper_name = f"MCP_OriginAxis_{axis}"
    existing_helper = adapter._attempt(
        lambda: adapter.currentModel.FeatureByName(helper_name), default=None
    )
    if existing_helper and _is_reference_axis_feature(existing_helper):
        return existing_helper

    # A standard axis is the intersection of its two perpendicular origin
    # planes. This fallback creates a persistent, re-usable reference axis
    # only on templates where the native origin axis is not enumerable.
    plane_pairs = {
        "X": ("Top", "Front"),
        "Y": ("Front", "Right"),
        "Z": ("Right", "Top"),
    }
    first_plane, second_plane = (
        _standard_origin_plane(adapter, plane) for plane in plane_pairs[axis]
    )
    first_selected = adapter._attempt(
        lambda: first_plane.Select2(False, 0), default=False
    )
    second_selected = adapter._attempt(
        lambda: second_plane.Select2(True, 0), default=False
    )
    if not first_selected or not second_selected:
        raise Exception(f"Failed to select origin planes for {axis} axis")

    _flag_feature_methods(adapter.currentModel, "IModelDoc2")
    inserted = adapter._attempt(
        lambda: adapter.currentModel.InsertAxis2(True), default=False
    )
    if not inserted:
        raise Exception(f"Failed to create localized origin-axis helper: {axis}")

    created_axis = _latest_reference_axis_feature(adapter)
    if not created_axis:
        raise Exception("SolidWorks created an origin-axis helper without a feature")
    adapter._attempt(lambda: setattr(created_axis, "Name", helper_name), default=None)
    return created_axis


def _is_reference_axis_feature(feature: Any) -> bool:
    """Return whether a feature is a reference axis across late COM binding modes."""
    _flag_feature_methods(feature, "IFeature")
    return str(_read_member(feature, "GetTypeName2") or "").casefold() == "refaxis"


def _latest_reference_axis_feature(adapter: Any) -> Any:
    """Find the just-created reference axis in reverse feature-tree order."""
    for position in range(256):
        feature = adapter._attempt(
            lambda position=position: adapter.currentModel.FeatureByPositionReverse(
                position
            ),
            default=None,
        )
        if not feature:
            break
        if _is_reference_axis_feature(feature):
            return feature
    return None


def _select_trusted_feature(
    adapter: Any, name: str, *, append: bool, mark: int
) -> None:
    """Select a feature-tree item by a server-owned display name."""
    feature = adapter._attempt(
        lambda: adapter.currentModel.FeatureByName(name), default=None
    )
    selected = bool(feature) and adapter._attempt(
        lambda: feature.Select2(append, mark), default=False
    )
    if not selected:
        selected = adapter._attempt(
            lambda: adapter.currentModel.Extension.SelectByID2(
                name, "BODYFEATURE", 0, 0, 0, append, mark, None, 0
            ),
            default=False,
        )
    if not selected:
        raise Exception(f"Failed to select server-owned feature: {name}")


def _select_trusted_reference(
    adapter: Any, name: str, selection_type: str, *, append: bool, mark: int
) -> None:
    """Select a server-owned reference feature and preserve the requested mark."""
    feature = adapter._attempt(
        lambda: adapter.currentModel.FeatureByName(name), default=None
    )
    selected = bool(feature) and adapter._attempt(
        lambda: feature.Select2(append, mark), default=False
    )
    if not selected:
        selected = adapter._attempt(
            lambda: adapter.currentModel.Extension.SelectByID2(
                name, selection_type, 0, 0, 0, append, mark, None, 0
            ),
            default=False,
        )
    if not selected:
        raise Exception(f"Failed to select server-owned reference: {name}")


def _created_feature(
    adapter: Any,
    feature: Any,
    *,
    feature_type: str,
    parameters: dict[str, Any],
    before: dict[str, Any] | None = None,
) -> SolidWorksFeature:
    """Convert a non-null COM feature into the adapter's serializable result."""
    if not feature and before is not None:
        feature = _recover_new_feature(adapter, before, feature_type)
    if not feature:
        raise Exception(f"SolidWorks did not create {feature_type}")
    name = str(_read_member(feature, "Name") or "").strip()
    if not name:
        raise Exception(f"SolidWorks created {feature_type} without a name")
    return SolidWorksFeature(
        name=name,
        type=feature_type,
        id=adapter._get_feature_id(feature),
        parameters=parameters,
        properties={"created": datetime.now().isoformat()},
    )


def _create_feature_linear_pattern_impl(
    adapter: Any, params: FeatureLinearPatternParameters
) -> AdapterResult[SolidWorksFeature]:
    """Pattern one trusted feature along a fixed standard coordinate direction."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    direction_plane_by_axis = {"X": "Right", "Y": "Top", "Z": "Front"}
    direction_plane = direction_plane_by_axis.get(params.direction)
    if direction_plane is None:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Unsupported linear-pattern direction",
        )

    def _linear_pattern_operation() -> SolidWorksFeature:  # pragma: no cover
        adapter.currentModel.ClearSelection2(True)
        try:
            direction_reference = _standard_origin_plane(adapter, direction_plane)
            direction_plane_name = str(
                _read_member(direction_reference, "Name") or ""
            ).strip()
            if not direction_plane_name:
                raise Exception(
                    f"Standard origin plane is unavailable: {direction_plane}"
                )
            # FeatureLinearPattern4 consumes pattern seed features under mark 4
            # and the first direction reference under mark 1.
            _select_trusted_feature(adapter, params.feature_name, append=False, mark=4)
            _select_trusted_reference(
                adapter, direction_plane_name, "PLANE", append=True, mark=1
            )
            features_before = _feature_tree_snapshot(adapter)
            feature = adapter.currentModel.FeatureManager.FeatureLinearPattern4(
                params.instances,
                params.spacing / 1000.0,
                1,
                0.0,
                params.reverse_direction,
                False,
                "NULL",
                "NULL",
                False,
                False,
                False,
                False,
                False,
                False,
                True,
                True,
                False,
                False,
                0,
                0,
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="LinearPattern",
                parameters={
                    "instances": params.instances,
                    "spacing": params.spacing,
                    "direction": params.direction,
                    "reverse_direction": params.reverse_direction,
                },
                before=features_before,
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation(
            "create_feature_linear_pattern", _linear_pattern_operation
        ),
    )


def _create_feature_circular_pattern_impl(
    adapter: Any, params: FeatureCircularPatternParameters
) -> AdapterResult[SolidWorksFeature]:
    """Pattern one trusted feature around a trusted standard origin axis."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _circular_pattern_operation() -> SolidWorksFeature:  # pragma: no cover
        adapter.currentModel.ClearSelection2(True)
        try:
            _select_trusted_feature(adapter, params.feature_name, append=False, mark=4)
            _select_trusted_reference(
                adapter, params.axis_name, "AXIS", append=True, mark=1
            )
            features_before = _feature_tree_snapshot(adapter)
            feature = adapter.currentModel.FeatureManager.FeatureCircularPattern5(
                params.instances,
                params.angle_degrees * 3.141592653589793 / 180.0,
                params.reverse_direction,
                "NULL",
                False,
                True,
                False,
                False,
                False,
                False,
                1,
                0.0,
                "NULL",
                False,
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="CircularPattern",
                parameters={
                    "instances": params.instances,
                    "angle_degrees": params.angle_degrees,
                    "reverse_direction": params.reverse_direction,
                },
                before=features_before,
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation(
            "create_feature_circular_pattern", _circular_pattern_operation
        ),
    )


def _mirror_feature_impl(
    adapter: Any, params: FeatureMirrorParameters
) -> AdapterResult[SolidWorksFeature]:
    """Mirror one trusted feature about one trusted reference plane."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _mirror_operation() -> SolidWorksFeature:  # pragma: no cover
        adapter.currentModel.ClearSelection2(True)
        try:
            # InsertMirrorFeature2 consumes the source feature under mark 1
            # and the mirror plane under mark 2.
            _select_trusted_feature(adapter, params.feature_name, append=False, mark=1)
            _select_trusted_reference(
                adapter, params.plane_name, "PLANE", append=True, mark=2
            )
            features_before = _feature_tree_snapshot(adapter)
            feature = adapter.currentModel.FeatureManager.InsertMirrorFeature2(
                False,
                False,
                False,
                False,
                _SW_FEATURE_SCOPE_ALL_BODIES,
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="Mirror",
                parameters={"plane_name": params.plane_name},
                before=features_before,
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("mirror_feature", _mirror_operation),
    )


def _create_hole_wizard_impl(
    adapter: Any, params: HoleWizardParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create one ANSI Metric M6 clearance Hole Wizard from a trusted sketch.

    ``HoleWizard5`` is invoked with the only supported typed preset.  The
    position is defined entirely by the selected server-owned sketch, keeping
    raw selection strings, Wizard database paths, and arbitrary face handles
    outside the MCP interface.
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _hole_wizard_operation() -> SolidWorksFeature:  # pragma: no cover
        adapter.currentModel.ClearSelection2(True)
        try:
            _select_trusted_reference(
                adapter,
                params.location_sketch_name,
                "SKETCH",
                append=False,
                mark=0,
            )
            features_before = _feature_tree_snapshot(adapter)
            feature = adapter.currentModel.FeatureManager.HoleWizard5(
                _SW_WZD_HOLE,  # GenericHoleType: swWzdHole
                _SW_STANDARD_ANSI_METRIC,  # StandardIndex: swStandardAnsiMetric
                _SW_ANSI_METRIC_SCREW_CLEARANCES,
                _SW_ANSI_METRIC_CLEARANCE_SEED_SIZE,
                _SW_END_COND_BLIND,  # EndType: swEndCondBlind
                params.diameter / 1000.0,  # Diameter (metres)
                params.depth / 1000.0,  # Depth (metres)
                0.0,  # Length: unused for a circular hole
                -1,  # Value1: unused for this preset
                -1,  # Value2
                -1,  # Value3
                -1,  # Value4
                -1,  # Value5
                -1,  # Value6
                -1,  # Value7
                -1,  # Value8
                -1,  # Value9
                -1,  # Value10
                -1,  # Value11
                -1,  # Value12
                "",
                False,
                True,
                True,
                True,
                True,
                False,
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="HoleWizard",
                parameters={
                    "standard": "ansi_metric",
                    "hole_type": "simple",
                    "diameter": params.diameter,
                    "depth": params.depth,
                },
                before=features_before,
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_hole_wizard", _hole_wizard_operation),
    )


def _create_tapped_hole_impl(
    adapter: Any, params: ThreadedHoleParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create one standard ANSI Metric tapped hole from a trusted point sketch."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _tapped_hole_operation() -> SolidWorksFeature:  # pragma: no cover
        adapter.currentModel.ClearSelection2(True)
        try:
            _select_trusted_reference(
                adapter,
                params.location_sketch_name,
                "SKETCH",
                append=False,
                mark=0,
            )
            features_before = _feature_tree_snapshot(adapter)
            feature = adapter.currentModel.FeatureManager.HoleWizard5(
                _SW_WZD_TAP,
                _SW_STANDARD_ANSI_METRIC,
                _SW_ANSI_METRIC_TAPPED_HOLE,
                _tapped_hole_seed_size(params.thread_size),
                _SW_TAP_THROUGH if params.through_all else _SW_TAP_BLIND,
                0.0,
                params.depth / 1000.0,
                0.0,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                -1,
                "",
                False,
                True,
                True,
                True,
                True,
                False,
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="TappedHole",
                parameters={
                    "standard": "ansi_metric",
                    "hole_type": "tapped",
                    "thread_size": params.thread_size,
                    "depth": params.depth,
                    "through_all": params.through_all,
                },
                before=features_before,
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_tapped_hole", _tapped_hole_operation),
    )


def _create_revolve_impl(
    adapter: Any, params: RevolveParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create a revolve feature from the active sketch profile around a centre axis.

    Uses ``FeatureRevolve2`` from the SolidWorks COM API.  The sketch must
    already contain a centre-line that SolidWorks will use as the rotation axis.

    Args:
        adapter: A fully connected ``PyWin32Adapter`` with a non-``None``
            ``currentModel``.
        params: Revolve parameter bag.  Relevant fields:
            - ``angle`` (float): Revolve angle in degrees.  Use 360 for a
              full revolution.
            - ``reverse_direction`` (bool): Flip the revolve direction.
            - ``both_directions`` (bool): Revolve symmetrically in both
              directions.
            - ``thin_feature`` (bool): Produce a thin-wall body.
            - ``thin_thickness`` (float | None): Wall thickness in mm.
            - ``merge_result`` (bool): Merge with existing bodies.

    Returns:
        AdapterResult[SolidWorksFeature]: On success, ``data`` is a
        ``SolidWorksFeature`` whose ``type`` is ``"Revolve"``.  On failure,
        ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``FeatureRevolve2`` returns ``None``.

    Example::

        from solidworks_mcp.adapters.base import RevolveParameters
        from solidworks_mcp.adapters import pywin32_feature_ops

        params = RevolveParameters(angle=360.0, merge_result=True)
        result = pywin32_feature_ops.create_revolve(adapter, params)
        print(result.data.name)  # e.g. "Revolve1"
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _revolve_operation() -> SolidWorksFeature:  # pragma: no cover
        """Inner COM closure that builds and returns the revolve feature.

        Converts the degree angle to radians and invokes ``FeatureRevolve2``.

        Returns:
            SolidWorksFeature: Populated feature descriptor on success.

        Raises:
            Exception: If ``FeatureRevolve2`` returns ``None``.
        """
        _select_cut_profile(adapter)
        is_cut = bool(getattr(params, "is_cut", False))
        # Detect SW major version for FeatureRevolve2 API choice
        revolve_sw_major = 0
        if getattr(adapter, "swApp", None):
            rev = adapter._attempt(
                lambda: adapter._get_attr_or_call(adapter.swApp, "RevisionNumber"),
                default="0",
            )
            try:
                revolve_sw_major = int(str(rev).split(".")[0])
            except (ValueError, IndexError):
                revolve_sw_major = 0

        import math
        features_before = _feature_tree_snapshot(adapter)

        if revolve_sw_major == 33:  # pragma: no cover
            # IFeatureManager.FeatureRevolve2 (20 params) per gen_py SW 2025
            # SingleDir, IsSolid, IsThin, IsCut, ReverseDir, BothDirUpToSame,
            # Dir1Type, Dir2Type, Dir1Angle(rad), Dir2Angle(rad),
            # OffsetRev1/2, OffsetDist1/2, Merge, ThinThick1/2(m), AutoSelect, Propagate
            feature_manager = adapter.currentModel.FeatureManager
            feature = feature_manager.FeatureRevolve2(
                True,  # SingleDir
                True,  # IsSolid
                False,  # IsThin
                 is_cut,  # IsCut
                params.reverse_direction,  # ReverseDir
                params.both_directions,  # BothDirUpToSame
                0,
                0,  # Dir1Type, Dir2Type
                params.angle * math.pi / 180.0,  # Dir1Angle (rad)
                (params.angle * math.pi / 180.0)
                if params.both_directions
                else 0.0,  # Dir2Angle
                False,
                False,  # OffsetRev1/2
                0.0,
                0.0,  # OffsetDist1/2
                params.merge_result,  # Merge
                (params.thin_thickness or 0.0) / 1000.0,
                0.0,  # ThinThick1/2
                True,  # AutoSelect
                False,  # Propagate
            )
        else:
            feature_manager = adapter.currentModel.FeatureManager
            feature = feature_manager.FeatureRevolve2(
                not params.both_directions,
                True,
                params.thin_feature,
                 is_cut,
                params.reverse_direction,
                False,
                adapter.constants["swEndCondBlind"],
                adapter.constants["swEndCondBlind"],
                params.angle * 3.14159 / 180.0,
                (params.angle * 3.14159 / 180.0) if params.both_directions else 0.0,
                False,
                False,
                0.0,
                0.0,
                0,
                (params.thin_thickness or 0.0) / 1000.0,
                0.0,
                params.merge_result,
                False,
                True,
            )

        if not feature:
            feature = _recover_new_feature(
                adapter,
                features_before,
                "Cut-Revolve" if is_cut else "Revolve",
            )

        # IModelDoc2.FeatureRevolve2 returns None (void) on SW 2025
        if not feature and revolve_sw_major != 33:
            raise Exception("Failed to create revolve feature")

        return SolidWorksFeature(
            name=feature.Name if feature else (
                 "Cut-Revolve-Auto" if is_cut else "Revolve-Auto"
            ),
            type="Cut-Revolve" if is_cut else "Revolve",
            id=adapter._get_feature_id(feature) if feature else "revolve_auto",
            parameters={
                "angle": params.angle,
                "reverse_direction": params.reverse_direction,
                "both_directions": params.both_directions,
                "thin_feature": params.thin_feature,
                "thin_thickness": params.thin_thickness,
            },
            properties={"created": datetime.now().isoformat()},
        )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_revolve", _revolve_operation),
    )


def _select_named_feature(
    adapter: Any,
    name: str,
    mark: int,
    append: bool,
) -> bool:
    """Select a named feature under a specific selection mark via ``Select2``.

    Sweep and loft rely on selection marks to tell SolidWorks which selection
    is the profile (1), guide curve (2), or sweep path (4).  We resolve the
    feature with ``IModelDoc2::FeatureByName`` and select it with
    ``IFeature::Select2(append, mark)`` — the same proven path the rest of the
    adapter uses for plane/sketch selection.  ``IModelDocExtension::SelectByID2``
    is avoided deliberately: late-bound ``SelectByID2`` raises
    ``Type mismatch`` on some SolidWorks builds, whereas ``FeatureByName`` +
    ``Select2`` is reliable, works for sketches *and* reference curves such as
    a helix, and needs no entity-type string.

    Args:
        adapter: A connected adapter with a valid ``currentModel``.
        name: Exact feature name (e.g. ``"Sketch1"`` or ``"Helix/Spiral1"``).
        mark: Selection mark — 1=profile, 2=guide curve, 4=sweep path.
        append: ``True`` to add to the current selection set, ``False`` to
            replace it.

    Returns:
        bool: ``True`` when the feature was found and selected.
    """
    feature = adapter._attempt(
        lambda: adapter.currentModel.FeatureByName(name), default=None
    )
    if not feature:
        return False
    return bool(adapter._attempt(lambda: feature.Select2(append, mark), default=False))


def _flag_feature_methods(obj: Any, interface: str) -> None:  # pragma: no cover
    """Best-effort method flagging for a COM object via ``sw_type_info``.

    Flagging tells pywin32 late binding to resolve names like ``GetTypeName2``
    / ``GetNextFeature`` / ``FirstFeature`` as methods.  No-ops on plain test
    doubles (and any environment without the gen_py wrapper).

    Args:
        obj: The COM object (or test double) to flag.
        interface: SolidWorks interface name (e.g. ``"IFeature"``).
    """
    try:
        from solidworks_mcp.adapters import sw_type_info

        sw_type_info.flag_methods(obj, interface)
    except Exception:
        pass


def _read_member(obj: Any, name: str) -> Any:  # pragma: no cover
    """Read a COM member that pywin32 may expose as a property *or* a method.

    Late-bound pywin32 dispatches are inconsistent: an unflagged zero-arg
    accessor may come back as a bound method (needing a call) *or* as the
    already-resolved value — and when that value is itself a COM object it is
    also callable, so a naive "call if callable" check wrongly invokes its
    default dispatch (``Member not found``).  This helper calls the member and
    falls back to the raw member if the call raises, so it yields the value in
    every case (flagged method, unflagged method, property-returning-object,
    or plain test double).

    Args:
        obj: The COM object (or test double) to read from.
        name: Member name.

    Returns:
        Any: The member's value, or ``None`` when the attribute is absent.
    """
    member = getattr(obj, name, None)
    if not callable(member):
        return member
    try:
        return member()
    except Exception:
        return member


def _profile_feature_names(adapter: Any) -> list[str]:  # pragma: no cover
    """Return sketch (``ProfileFeature``) names in feature-tree order.

    Walks ``FirstFeature`` -> ``GetNextFeature`` reading ``GetTypeName2`` and
    collecting features whose type is ``"ProfileFeature"`` (a 2D/3D sketch).
    Mirrors the tree walk used by :func:`_create_cut_extrude_impl`, but flags
    each feature for ``IFeature`` and reads members through
    :func:`_read_member` so it is robust to pywin32's method-vs-property
    late-binding ambiguity.

    Args:
        adapter: A connected adapter with a valid ``currentModel``.

    Returns:
        list[str]: Bare sketch names, earliest first.  Empty when the walk
        finds no sketches or the tree is inaccessible.
    """
    names: list[str] = []
    try:
        _flag_feature_methods(adapter.currentModel, "IModelDoc2")
        feat = _read_member(adapter.currentModel, "FirstFeature")
        # Bound the walk so a misbehaving GetNextFeature can't spin forever.
        for _ in range(5000):
            if not feat:
                break
            _flag_feature_methods(feat, "IFeature")
            try:
                if _read_member(feat, "GetTypeName2") == "ProfileFeature":
                    names.append(str(_read_member(feat, "Name")))
            except Exception:
                pass
            try:
                feat = _read_member(feat, "GetNextFeature")
            except Exception:
                break
    except Exception:
        pass
    return names


def _create_sweep_impl(
    adapter: Any, params: SweepParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create a swept boss or cut from a profile sketch along a path sketch.

    Uses ``IFeatureManager::InsertProtrusionSwept4`` for bosses and
    ``InsertCutSwept4`` (falling back to ``InsertCutSwept3``) for cuts. Two sketches are
    required in the active part: a closed **profile** sketch and an open
    **path** sketch named by ``params.path``.  The path is selected under
    mark 4 and the profile under mark 1, per the SolidWorks selection-mark
    contract for sweeps.

    New callers should set ``params.profile`` to select an exact profile
    sketch.  Legacy callers that provide only a path retain the prior,
    best-effort feature-tree inference for compatibility.

    Args:
        adapter: A fully connected ``PyWin32Adapter`` with a non-``None``
            ``currentModel``.
        params: Sweep parameter bag.  Relevant fields:
            - ``path`` (str): Name of the path sketch (e.g. ``"Sketch2"``).
            - ``profile`` (str | None): Exact profile sketch name when known.
            - ``twist_along_path`` (bool): Apply a constant twist along the
              path.
            - ``twist_angle`` (float): Twist angle in **degrees** (used only
              when ``twist_along_path`` is true).
            - ``merge_result`` (bool): Merge with existing bodies.
            - ``is_cut`` (bool): Use SolidWorks' cut-sweep API instead of the
              boss-sweep API.

    Returns:
        AdapterResult[SolidWorksFeature]: On success, ``data`` is a
        ``SolidWorksFeature`` whose ``type`` is ``"Sweep"``.  On failure,
        ``status`` is ``ERROR`` with a descriptive message.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when the
            profile/path cannot be selected or the COM call returns ``None``.

    Example::

        from solidworks_mcp.adapters.base import SweepParameters

        params = SweepParameters(path="Sketch2", merge_result=True)
        result = await adapter.create_sweep(params)
        print(result.data.name)  # e.g. "Sweep1"
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    if not getattr(params, "path", None):
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Sweep requires a 'path' sketch name",
        )

    def _sweep_operation() -> SolidWorksFeature:  # pragma: no cover
        """Inner COM closure that selects profile + path and runs the sweep.

        Returns:
            SolidWorksFeature: Populated feature descriptor on success.

        Raises:
            Exception: When selections fail or ``InsertProtrusionSwept4``
                returns ``None``.
        """
        import math

        feature_manager = adapter.currentModel.FeatureManager

        # Resolve the path name against the actual tree sketches so the
        # profile/path comparison is on bare names, then pick the first
        # non-path sketch as the profile.
        sketch_names = _profile_feature_names(adapter)
        path_name = params.path
        for name in sketch_names:
            if name == params.path or name.lower() == params.path.lower():
                path_name = name
                break

        # Typed workflow callers provide an exact session-owned profile.  The
        # feature-tree fallback is retained only for the established legacy
        # tool surface, which has no profile argument.
        profile_name = getattr(params, "profile", None)
        if profile_name:
            profile_name = next(
                (
                    name
                    for name in sketch_names
                    if name.casefold() == str(profile_name).casefold()
                ),
                profile_name,
            )
        else:
            # Profile = the most recently created sketch that isn't the path.
            # Preferring the latest sketch handles both a sketch path (profile
            # is drawn first, so it's the only non-path sketch) and a
            # helix/curve path (the helix's base-circle sketch precedes the
            # profile in the tree, so "first non-path" would wrongly pick it).
            last = getattr(adapter, "_last_sketch_name", None)
            if last and last != path_name and last in sketch_names:
                profile_name = last
            if profile_name is None:
                profile_name = next(
                    (name for name in reversed(sketch_names) if name != path_name),
                    None,
                )
        if profile_name is None:
            raise Exception(
                "Sweep needs a profile sketch distinct from the path "
                f"'{params.path}'. Sketches found: {sketch_names or 'none'}"
            )

        adapter._attempt(
            lambda: adapter.currentModel.ClearSelection2(True), default=None
        )
        try:
            if not _select_named_feature(adapter, profile_name, 1, False):
                raise Exception(
                    f"Failed to select sweep profile sketch: {profile_name}"
                )
            if not _select_named_feature(adapter, path_name, 4, True):
                raise Exception(f"Failed to select sweep path: {path_name}")

            twist = bool(getattr(params, "twist_along_path", False))
            twist_angle_deg = float(getattr(params, "twist_angle", 0.0))
            # swTwistControlType_e: 0 = follow path, 8 = constant twist along path.
            twist_ctrl = 8 if twist else 0
            twist_angle_rad = math.radians(twist_angle_deg) if twist else 0.0

            sweep_args = (
                False,  # Propagate to next tangent edge
                False,  # Alignment (go through end faces)
                twist_ctrl,  # TwistCtrlOption (swTwistControlType_e)
                False,  # KeepTangency
                False,  # BAdvancedSmoothing
                0,  # StartMatchingType (swTangencyType_e)
                0,  # EndMatchingType
                False,  # IsThinBody
                0.0,  # Thickness1
                0.0,  # Thickness2
                0,  # ThinType (swThinWallType_e)
                0,  # PathAlign
                bool(getattr(params, "merge_result", True)),  # Merge
                True,  # UseFeatScope
                True,  # UseAutoSelect
                twist_angle_rad,  # TwistAngle (radians)
                True,  # BMergeSmoothFaces
                False,  # CircularProfile
                0.0,  # CircularProfileDiameter
                0,  # Direction
            )

            is_cut = bool(getattr(params, "is_cut", False))
            cut_sweep_common_args = (
                False,  # Propagate to next tangent edge
                False,  # Alignment (go through end faces)
                twist_ctrl,  # TwistCtrlOption (swTwistControlType_e)
                False,  # KeepTangency
                False,  # BAdvancedSmoothing
                0,  # StartMatchingType (swTangencyType_e)
                0,  # EndMatchingType
                False,  # IsThinBody
                0.0,  # Thickness1
                0.0,  # Thickness2
                0,  # ThinType (swThinWallType_e)
                0,  # PathAlign
                True,  # UseFeatScope
                True,  # UseAutoSelect
                twist_angle_rad,  # TwistAngle (radians)
                True,  # BMergeSmoothFaces
            )
            method_calls = (
                (
                    ("InsertCutSwept4", "InsertCutSwept3"),
                    (
                        (*cut_sweep_common_args, False, True, False),
                        cut_sweep_common_args,
                    ),
                )
                if is_cut
                else (("InsertProtrusionSwept4",), (sweep_args,))
            )
            features_before = _feature_tree_snapshot(adapter)
            feature: Any = None
            call_errors: list[str] = []
            method_names, method_args = method_calls
            for method_name, args in zip(method_names, method_args, strict=True):
                method = getattr(feature_manager, method_name, None)
                if not callable(method):
                    call_errors.append(f"{method_name} is unavailable")
                    continue
                try:
                    feature = method(*args)
                except Exception as exc:
                    call_errors.append(f"{method_name}: {exc}")
                    continue
                if feature:
                    break

            if not feature:
                feature = _recover_new_feature(
                    adapter,
                    features_before,
                    "Cut-Sweep" if is_cut else "Sweep",
                )
            if not feature:
                operation = "cut sweep" if is_cut else "boss sweep"
                detail = "; ".join(call_errors) or "SolidWorks returned no feature"
                raise Exception(f"Failed to create sweep feature ({operation}): {detail}")

            return SolidWorksFeature(
                name=feature.Name,
                type="Cut-Sweep" if is_cut else "Sweep",
                id=adapter._get_feature_id(feature),
                parameters={
                    "profile": profile_name,
                    "path": path_name,
                    "twist_along_path": twist,
                    "twist_angle": twist_angle_deg,
                    "merge_result": bool(getattr(params, "merge_result", True)),
                    "is_cut": is_cut,
                },
                properties={"created": datetime.now().isoformat()},
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_sweep", _sweep_operation),
    )


def _create_loft_impl(
    adapter: Any, params: LoftParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create a lofted boss or cut between two or more profile sketches.

    Uses ``IFeatureManager::InsertProtrusionBlend2`` for bosses and
    ``InsertCutBlend`` for cuts. Each profile named in
    ``params.profiles`` is selected under mark 1 (in order — the selection
    order determines the loft direction), and any ``params.guide_curves`` are
    selected under mark 2.  Because a solid is produced, every profile must be
    a closed contour.

    Args:
        adapter: A fully connected ``PyWin32Adapter`` with a non-``None``
            ``currentModel``.
        params: Loft parameter bag.  Relevant fields:
            - ``profiles`` (list[str]): Ordered profile sketch names; at least
              two are required.
            - ``guide_curves`` (list[str] | None): Optional guide curve names.
            - ``start_tangent`` / ``end_tangent`` (str | None): ``"normal"``
              tangency at the start/end profile, anything else / ``None`` ->
              no tangency.
            - ``merge_result`` (bool): Merge with existing bodies.
            - ``is_cut`` (bool): Use SolidWorks' cut-loft API instead of the
              boss-loft API.

    Returns:
        AdapterResult[SolidWorksFeature]: On success, ``data`` is a
        ``SolidWorksFeature`` whose ``type`` is ``"Loft"``.  On failure,
        ``status`` is ``ERROR`` with a descriptive message.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when a profile
            cannot be selected or the COM call returns ``None``.

    Example::

        from solidworks_mcp.adapters.base import LoftParameters

        params = LoftParameters(profiles=["Sketch1", "Sketch2"])
        result = await adapter.create_loft(params)
        print(result.data.name)  # e.g. "Loft1"
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    profiles = list(getattr(params, "profiles", None) or [])
    if len(profiles) < 2:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Loft requires at least 2 profile sketches",
        )

    def _loft_operation() -> SolidWorksFeature:  # pragma: no cover
        """Inner COM closure that selects profiles/guides and runs the loft.

        Returns:
            SolidWorksFeature: Populated feature descriptor on success.

        Raises:
            Exception: When a profile selection fails or
                ``InsertProtrusionBlend2`` returns ``None``.
        """
        guide_curves = list(getattr(params, "guide_curves", None) or [])

        adapter._attempt(
            lambda: adapter.currentModel.ClearSelection2(True), default=None
        )
        try:
            # Profiles under mark 1, in order. First replaces the selection set,
            # the rest append so SW sees them as an ordered profile group.
            for index, profile in enumerate(profiles):
                if not _select_named_feature(adapter, profile, 1, append=index > 0):
                    raise Exception(f"Failed to select loft profile sketch: {profile}")

            # Optional guide curves under mark 2 (a sketch or a reference curve).
            for guide in guide_curves:
                if not _select_named_feature(adapter, guide, 2, append=True):
                    raise Exception(f"Failed to select loft guide curve: {guide}")

            # swTangencyType_e: 0 = none, 1 = tangent to profile normal.
            def _tangency(value: str | None) -> int:  # pragma: no cover
                return 1 if str(value or "").strip().lower() == "normal" else 0

            start_match = _tangency(getattr(params, "start_tangent", None))
            end_match = _tangency(getattr(params, "end_tangent", None))

            loft_args = (
                False,  # Closed loft
                True,  # KeepTangency
                False,  # ForceNonRational
                1.0,  # TessToleranceFactor
                start_match,  # StartMatchingType (swTangencyType_e)
                end_match,  # EndMatchingType
                1.0,  # StartTangentLength
                1.0,  # EndTangentLength
                True,  # StartTangentDir
                True,  # EndTangentDir
                False,  # IsThinBody
                0.0,  # Thickness1
                0.0,  # Thickness2
                0,  # ThinType
                bool(getattr(params, "merge_result", True)),  # Merge
                True,  # UseFeatScope
                True,  # UseAutoSelect
                2,  # GuideCurveInfluence (swGuideCurveInfluenceNextEdge)
            )

            is_cut = bool(getattr(params, "is_cut", False))
            method_name = "InsertCutBlend" if is_cut else "InsertProtrusionBlend2"
            feature_manager = adapter.currentModel.FeatureManager
            method = getattr(feature_manager, method_name, None)
            if not callable(method):
                raise Exception(f"SolidWorks does not expose {method_name}")
            cut_loft_args = (
                False,  # Closed loft
                True,  # KeepTangency
                False,  # ForceNonRational
                1.0,  # TessToleranceFactor
                start_match,  # StartMatchingType (swTangencyType_e)
                end_match,  # EndMatchingType
                False,  # IsThinBody
                0.0,  # Thickness1
                0.0,  # Thickness2
                0,  # ThinType
                True,  # UseFeatScope
                True,  # UseAutoSelect
            )
            features_before = _feature_tree_snapshot(adapter)
            feature = method(*(cut_loft_args if is_cut else loft_args))

            if not feature:
                feature = _recover_new_feature(
                    adapter,
                    features_before,
                    "Cut-Loft" if is_cut else "Loft",
                )
            if not feature:
                operation = "cut loft" if is_cut else "boss loft"
                raise Exception(f"Failed to create loft feature ({operation})")

            return SolidWorksFeature(
                name=feature.Name,
                type="Cut-Loft" if is_cut else "Loft",
                id=adapter._get_feature_id(feature),
                parameters={
                    "profiles": profiles,
                    "guide_curves": guide_curves or None,
                    "start_tangent": getattr(params, "start_tangent", None),
                    "end_tangent": getattr(params, "end_tangent", None),
                    "merge_result": bool(getattr(params, "merge_result", True)),
                    "is_cut": is_cut,
                },
                properties={"created": datetime.now().isoformat()},
            )
        finally:
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_loft", _loft_operation),
    )


def _create_cut_extrude_impl(
    adapter: Any, params: ExtrusionParameters
) -> AdapterResult[SolidWorksFeature]:
    """Create a cut-extrude feature from the active sketch profile.

    The function first attempts to locate and select the sketch profile that
    should be cut.  It walks the feature tree looking for the most recent
    ``ProfileFeature``; if that fails, it falls back to the ``_last_sketch_name``
    tracker and then to an enumerated ``Sketch<N>`` name search.

    Three COM API variants are attempted in order of preference:

    1. ``FeatureCut4`` ΓÇö most modern (SolidWorks 2015+).
    2. ``FeatureCut3`` modern signature ΓÇö SolidWorks 2010ΓÇô2014.
    3. ``FeatureCut3`` legacy argument order ΓÇö older installs.

    All depth values are in millimetres and converted to metres internally.

    Args:
        adapter: A fully connected ``PyWin32Adapter`` with a non-``None``
            ``currentModel``.
        params: Extrusion parameter bag reused for cut parameters:
            - ``depth`` (float): Cut depth in mm.
            - ``draft_angle`` (float): Draft angle in degrees.
            - ``reverse_direction`` (bool): Flip the cut direction.
            - ``end_condition`` (str): ``"Blind"`` (default) or
              ``"ThroughAll"`` / ``"through_all"``.
            - ``feature_scope`` (bool): Limit cut to selected bodies.
            - ``auto_select`` (bool): Auto-select bodies in scope.

    Returns:
        AdapterResult[SolidWorksFeature]: On success, ``data`` is a
        ``SolidWorksFeature`` whose ``type`` is ``"Cut-Extrude"``.  On
        failure, ``status`` is ``ERROR`` and ``error`` lists every API
        variant that was tried.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when all
            three COM variants fail.

    Example::

        from solidworks_mcp.adapters.base import ExtrusionParameters
        from solidworks_mcp.adapters import pywin32_feature_ops

        params = ExtrusionParameters(depth=10.0, end_condition="ThroughAll")
        result = pywin32_feature_ops.create_cut_extrude(adapter, params)
        print(result.data.name)  # e.g. "Cut-Extrude1"
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _cut_operation() -> SolidWorksFeature:  # pragma: no cover
        """Inner COM closure that locates the active sketch and performs the cut.

        Normalises ``params``, resolves the end-condition constant, selects the
        sketch profile, then cascades through three ``FeatureCut`` overloads.

        Returns:
            SolidWorksFeature: Populated feature descriptor on success.

        Raises:
            Exception: When all COM cut variants return ``None``.
        """
        normalized = SimpleNamespace(
            depth=float(getattr(params, "depth", 0.0)),
            draft_angle=float(getattr(params, "draft_angle", 0.0)),
            reverse_direction=bool(getattr(params, "reverse_direction", False)),
            end_condition=str(getattr(params, "end_condition", "Blind")),
            feature_scope=bool(getattr(params, "feature_scope", False)),
            auto_select=bool(getattr(params, "auto_select", True)),
        )
        feature_manager = adapter.currentModel.FeatureManager

        end_condition = (normalized.end_condition or "Blind").strip().lower()
        t1 = adapter.constants["swEndCondBlind"]
        depth_m = normalized.depth / 1000.0
        if end_condition in {"throughall", "through all", "through_all"}:
            t1 = adapter.constants["swEndCondThroughAll"]
        elif end_condition in {
            "throughallboth",
            "through all both",
            "through_all_both",
        }:
            t1 = adapter.constants["swEndCondThroughAllBoth"]

        t0 = adapter.constants.get("swStartSketchPlane", 0)
        feature = None
        fallback_errors: list[str] = []
        # Capture the tree before the COM call.  Several SolidWorks releases
        # successfully add a Cut-Extrude but return ``None`` through late
        # binding; this lets us distinguish that case from a real failure.
        cut_features_before = _feature_tree_snapshot(adapter)
        sketch_selected = _select_cut_profile(adapter)
        is_through = end_condition in {
            "throughall",
            "through all",
            "through_all",
            "throughallboth",
            "through all both",
            "through_all_both",
        }

        # Detect SW major version for FeatureCut4 parameter count
        # SW 2025 (major=33) verified with 27 params; other versions use 28.
        sw_major = 0
        if getattr(adapter, "swApp", None):
            rev = adapter._attempt(
                lambda: adapter._get_attr_or_call(adapter.swApp, "RevisionNumber"),
                default="0",
            )
            try:
                sw_major = int(str(rev).split(".")[0])
            except (ValueError, IndexError):
                sw_major = 0

        # 1. FeatureCut4 (SW 2015+)
        # SW 2025 (major=33): 27 params (verified by VBA macro).
        # SW 2026+ (major>=34): 28 params — adds OptimizeGeometry + PFeat.
        if sw_major == 33:
            feature, cut4_error = adapter._attempt_with_error(
                lambda: feature_manager.FeatureCut4(
                    is_through,  # Sd
                    False,  # Flip
                    normalized.reverse_direction,  # Dir
                    t1,  # T1
                    adapter.constants["swEndCondBlind"],  # T2
                    depth_m,  # D1
                    0.0,  # D2
                    False,
                    False,
                    False,
                    False,  # Dchk1/2, Ddir1/2
                    normalized.draft_angle * 3.14159 / 180.0,  # Dang1
                    0.0,  # Dang2
                    False,
                    False,
                    False,
                    False,  # OffsetRev1/2, TranslateSurf1/2
                    False,  # NormalCut
                    normalized.feature_scope,  # UseFeatScope
                    normalized.auto_select,  # UseAutoSelect
                    False,  # AssemblyFeatureScope
                    False,  # AutoSelectComponents
                    False,  # PropagateFeatureToParts
                    t0,  # T0
                    0.0,  # StartOffset
                    False,  # FlipStartOffset
                )
            )
        elif sw_major >= 34:
            # SW 2026+ adds OptimizeGeometry as a 27th INPUT param.
            # PFeat is an OUT param (PARAMFLAG_FOUT|FRETVAL) — not passed by caller.
            feature, cut4_error = adapter._attempt_with_error(
                lambda: feature_manager.FeatureCut4(
                    is_through,  # Sd
                    False,  # Flip
                    normalized.reverse_direction,  # Dir
                    t1,  # T1
                    adapter.constants["swEndCondBlind"],  # T2
                    depth_m,  # D1
                    0.0,  # D2
                    False,
                    False,
                    False,
                    False,  # Dchk1/2, Ddir1/2
                    normalized.draft_angle * 3.14159 / 180.0,  # Dang1
                    0.0,  # Dang2
                    False,
                    False,
                    False,
                    False,  # OffsetRev1/2, TranslateSurf1/2
                    False,  # NormalCut
                    normalized.feature_scope,  # UseFeatScope
                    normalized.auto_select,  # UseAutoSelect
                    False,  # AssemblyFeatureScope
                    False,  # AutoSelectComponents
                    False,  # PropagateFeatureToParts
                    t0,  # T0
                    0.0,  # StartOffset
                    False,  # FlipStartOffset
                    False,  # OptimizeGeometry (added in SW 2026)
                )
            )
        else:
            feature, cut4_error = adapter._attempt_with_error(
                lambda: feature_manager.FeatureCut4(
                    True,
                    False,
                    normalized.reverse_direction,
                    t1,
                    adapter.constants["swEndCondBlind"],
                    depth_m,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.draft_angle * 3.14159 / 180.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    False,
                    normalized.feature_scope,
                    normalized.auto_select,
                    False,
                    False,
                    False,
                    t0,
                    0.0,
                    False,
                    False,
                )
            )
        if cut4_error is not None:
            fallback_errors.append(f"FeatureCut4: {cut4_error}")

        if not feature:
            # Do not retry a second cut if FeatureCut4 created the feature but
            # omitted its return value.  This is the most common source of
            # duplicate pockets and misleading "cut failed" messages.
            recovered = _recover_new_cut_feature(adapter, cut_features_before)
            if recovered is not None:
                feature = recovered

        if not feature and not sketch_selected:
            # Compatibility fallback for old clients whose sketch was not
            # created through the tracked sketch workflow.
            adapter._attempt(
                lambda: adapter.currentModel.ClearSelection2(True), default=None
            )
            for candidate in (
                [adapter._last_sketch_name] if adapter._last_sketch_name else []
            ) + [
                f"Sketch{n}"
                for n in range(getattr(adapter, "_sketch_count", 0), 0, -1)
            ]:
                sel_ok = bool(
                    adapter._attempt(
                        lambda c=candidate: adapter.currentModel.Extension.SelectByID2(
                            c, "SKETCH", 0.0, 0.0, 0.0, False, 0, None, 0
                        ),
                        default=False,
                    )
                )
                if sel_ok:
                    adapter._last_sketch_name = candidate
                    break

        if not feature:
            # 2. FeatureCut3 modern (SW 2010+, 26 params, corrected for SW 2022)
            # Signature: Sd, Flip, Dir, T1, T2, D1, D2, Dchk1, Dchk2, Ddir1, Ddir2,
            #   Dang1, Dang2, OffsetReverse1, OffsetReverse2, TranslateSurface1,
            #   TranslateSurface2, NormalCut, UseFeatScope, UseAutoSelect,
            #   AssemblyFeatureScope, AutoSelectComponents, PropagateFeatureToParts,
            #   T0, StartOffset, FlipStartOffset
            feature, cut3_modern_error = adapter._attempt_with_error(
                lambda: feature_manager.FeatureCut3(
                    is_through,
                    normalized.reverse_direction,
                    False,
                    t1,
                    0,
                    normalized.depth / 1000.0,
                    normalized.depth / 1000.0,
                    False,
                    False,
                    False,
                    False,
                    normalized.draft_angle * 3.14159 / 180.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    False,
                    normalized.feature_scope,
                    normalized.auto_select,
                    False,
                    False,
                    False,
                    t0,
                    0.0,
                    False,
                )
            )
            if cut3_modern_error is not None:
                fallback_errors.append(f"FeatureCut3 modern: {cut3_modern_error}")

        if not feature:
            # 3. FeatureCut3 legacy (older installs, alternate arg order)
            feature, cut3_legacy_error = adapter._attempt_with_error(
                lambda: feature_manager.FeatureCut3(
                    True,
                    False,
                    normalized.reverse_direction,
                    adapter.constants["swEndCondBlind"],
                    adapter.constants["swEndCondBlind"],
                    False,
                    False,
                    False,
                    False,
                    normalized.draft_angle * 3.14159 / 180.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    False,
                    normalized.feature_scope,
                    normalized.auto_select,
                    normalized.depth / 1000.0,
                    0.0,
                )
            )
            if cut3_legacy_error is not None:
                fallback_errors.append(f"FeatureCut3 legacy: {cut3_legacy_error}")

        if not feature:
            if fallback_errors:
                raise Exception(
                    "Failed to create cut extrude feature. "
                    + " | ".join(fallback_errors)
                )
            raise Exception("Failed to create cut extrude feature")

        return SolidWorksFeature(
            name=feature.Name,
            type="Cut-Extrude",
            id=adapter._get_feature_id(feature),
            parameters={
                "depth": normalized.depth,
                "draft_angle": normalized.draft_angle,
                "reverse_direction": normalized.reverse_direction,
            },
            properties={"created": datetime.now().isoformat()},
        )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_cut_extrude", _cut_operation),
    )


def _decode_persistent_reference(token: str, *, description: str) -> bytes:
    """Decode one workflow-only base64 persistent reference."""
    import base64

    try:
        value = base64.b64decode(token.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise RuntimeError(f"{description} reference is invalid") from exc
    if not value:
        raise RuntimeError(f"{description} reference is invalid")
    return value


def _select_persistent_entities(
    adapter: Any,
    tokens: list[str],
    *,
    description: str,
    clear_selection: bool = True,
    append_to_existing: bool = False,
) -> None:
    """Resolve and select only server-issued persistent geometry references."""
    extension = adapter.currentModel.Extension
    resolver = getattr(extension, "GetObjectByPersistReference3", None)
    if not callable(resolver):
        raise RuntimeError("Active model does not support persistent references")
    if clear_selection:
        adapter.currentModel.ClearSelection2(True)
    try:
        for index, token in enumerate(tokens):
            raw_resolved = resolver(_decode_persistent_reference(token, description=description))
            entity = raw_resolved
            resolve_error = 0
            if isinstance(raw_resolved, (tuple, list)):
                entity = raw_resolved[0] if raw_resolved else None
                if len(raw_resolved) > 1 and isinstance(raw_resolved[-1], (bool, int)):
                    resolve_error = int(raw_resolved[-1])
            if entity is None or resolve_error != 0:
                raise RuntimeError(f"{description} can no longer be resolved")
            selected = False
            selector = getattr(adapter, "select_entity", None)
            if callable(selector):
                selected = bool(selector(entity, append_to_existing or index > 0))
            if not selected:
                for method_name, arguments in (
                    ("Select4", (append_to_existing or index > 0, None)),
                    ("Select2", (append_to_existing or index > 0, 0)),
                    ("Select", (append_to_existing or index > 0,)),
                ):
                    method = getattr(entity, method_name, None)
                    if callable(method) and bool(method(*arguments)):
                        selected = True
                        break
            if not selected:
                raise RuntimeError(f"SolidWorks could not select the requested {description}")
    except Exception:
        adapter.currentModel.ClearSelection2(True)
        raise


def _call_feature_method(
    manager: Any,
    names: tuple[str, ...],
    candidates: tuple[tuple[Any, ...], ...],
    *,
    recover_none: Any = None,
) -> Any:
    """Call version-dependent FeatureManager overloads without UI selection names."""
    last_error: Exception | None = None
    failures: list[str] = []
    for method_name in names:
        method = getattr(manager, method_name, None)
        if not callable(method):
            continue
        for arguments in candidates:
            try:
                created = method(*arguments)
            except Exception as exc:  # COM dispatch reports overload mismatch as an exception.
                last_error = exc
                failures.append(f"{method_name}({len(arguments)} args): {exc}")
                continue
            if created is not None and created is not False:
                return created
            if created is None and callable(recover_none):
                recovered = recover_none()
                if recovered is not None and recovered is not False:
                    return recovered
    if last_error is not None:
        attempted = "; ".join(failures)
        raise RuntimeError(
            f"SolidWorks rejected the requested feature. Attempts: {attempted}"
        ) from last_error
    raise RuntimeError("This SolidWorks version does not expose the requested feature API")


def _resolve_trusted_sketch_feature(adapter: Any, sketch_name: str) -> Any:
    """Resolve a server-created sketch despite late-bound COM name gaps.

    ``GetActiveSketch2`` occasionally returns ``None`` after a valid sketch
    is opened.  The sketch adapter then records a synthetic ``Sketch_N`` name
    so the workflow can keep its state coherent.  That synthetic name cannot
    be resolved with ``FeatureByName`` after exiting the sketch.  Restrict the
    positional fallback to that exact most-recent adapter-owned sketch; callers
    cannot use it to select an arbitrary existing sketch.
    """
    feature = adapter._attempt(
        lambda: adapter.currentModel.FeatureByName(sketch_name), default=None
    )
    if feature:
        return feature

    if sketch_name != getattr(adapter, "_last_sketch_name", None):
        return None

    for position in range(4096):
        feature = adapter._attempt(
            lambda position=position: adapter.currentModel.FeatureByPositionReverse(
                position
            ),
            default=None,
        )
        if not feature:
            break
        _flag_feature_methods(feature, "IFeature")
        if str(_read_member(feature, "GetTypeName2") or "").casefold() == "profilefeature":
            return feature
    return None


def _resolve_cut_profile_feature(adapter: Any) -> Any:
    """Resolve the sketch that the legacy cut operation should consume.

    The legacy API has no ``sketch_name`` argument, so the adapter tracks the
    sketch created/opened most recently.  Prefer that trusted name and then
    use the reverse feature tree as a compatibility fallback.  The caller
    still treats a missing profile as an implicit-selection case for old
    clients, but never silently substitutes an arbitrary named sketch.
    """
    last_name = getattr(adapter, "_last_sketch_name", None)
    if last_name:
        feature = _resolve_trusted_sketch_feature(adapter, str(last_name))
        if feature is not None:
            return feature

    model = getattr(adapter, "currentModel", None)
    if model is None:
        return None
    for position in range(4096):
        feature = adapter._attempt(
            lambda position=position: model.FeatureByPositionReverse(position),
            default=None,
        )
        if feature is None:
            break
        _flag_feature_methods(feature, "IFeature")
        type_name = str(_read_member(feature, "GetTypeName2") or "").casefold()
        if type_name == "profilefeature":
            return feature
    return None


def _select_cut_profile(adapter: Any) -> bool:
    """Select the tracked cut sketch before calling ``FeatureCut*``.

    ``FeatureByName`` + ``IFeature.Select2`` is more reliable than
    ``SelectByID2`` on late-bound SOLIDWORKS 2019 COM.  The latter remains a
    narrowly scoped fallback for installations that expose only a name-based
    selector.  A return value of ``False`` means no trusted profile was found;
    it does not fail legacy callers that rely on SolidWorks' implicit sketch
    context.
    """
    model = getattr(adapter, "currentModel", None)
    if model is None:
        return False
    profile = _resolve_cut_profile_feature(adapter)
    if profile is None:
        return False
    adapter._attempt(lambda: model.ClearSelection2(True), default=None)
    selected = bool(
        adapter._attempt(lambda: profile.Select2(False, 0), default=False)
    )
    if not selected:
        name = str(_read_member(profile, "Name") or "")
        if name:
            selected = bool(
                adapter._attempt(
                    lambda: model.Extension.SelectByID2(
                        name, "SKETCH", 0.0, 0.0, 0.0, False, 0, None, 0
                    ),
                    default=False,
                )
            )
    if not selected:
        raise RuntimeError("The tracked sketch profile could not be selected for cut")
    return True


def _feature_tree_snapshot(adapter: Any) -> dict[str, Any]:
    """Return named features visible through the active model's feature tree."""
    model = getattr(adapter, "currentModel", None)
    if model is None:
        return {}
    snapshot: dict[str, Any] = {}
    seen: set[tuple[str, int]] = set()

    def add(feature: Any) -> None:
        if feature is None:
            return
        _flag_feature_methods(feature, "IFeature")
        raw_name = _read_member(feature, "Name")
        name = str(raw_name or "")
        if not name:
            return
        key = (name, id(feature))
        if key not in seen:
            seen.add(key)
            snapshot.setdefault(name, feature)

    for start in (0, 1):
        for position in range(start, 4096):
            feature = adapter._attempt(
                lambda position=position: model.FeatureByPositionReverse(position),
                default=None,
            )
            if feature is None:
                break
            add(feature)

    feature = adapter._attempt(lambda: _read_member(model, "FirstFeature"), default=None)
    visited: set[int] = set()
    for _ in range(4096):
        if feature is None or id(feature) in visited:
            break
        visited.add(id(feature))
        add(feature)
        feature = adapter._attempt(
            lambda feature=feature: _read_member(feature, "GetNextFeature"),
            default=None,
        )
    return snapshot


def _recover_new_cut_feature(adapter: Any, before: dict[str, Any]) -> Any:
    """Recover a cut feature created by COM when ``FeatureCut4`` returned None."""
    after = _feature_tree_snapshot(adapter)
    for name, feature in after.items():
        if name in before:
            continue
        lowered = name.casefold().replace("_", "-")
        type_name = str(_read_member(feature, "GetTypeName2") or "").casefold()
        if lowered.startswith("cut-extrude") or lowered.startswith("cutextrude"):
            return feature
        if type_name in {"cut", "cut-extrude", "cutextrude"}:
            return feature
    return None


def _recover_new_feature(
    adapter: Any, before: dict[str, Any], feature_type: str
) -> Any:
    """Recover a newly-created feature when late-bound COM returned no object.

    SolidWorks feature names and ``GetTypeName2`` values differ by release, so
    matching uses conservative aliases for the feature families exposed by the
    MCP.  The pre-call name snapshot prevents returning an older feature and
    therefore prevents a caller from treating an unchanged model as success.
    """
    aliases = {
        "extrusion": ("extrude", "boss"),
        "referenceplane": ("refplane", "referenceplane"),
        "linearpattern": ("linearpattern", "lpattern"),
        "circularpattern": ("circularpattern", "cpattern"),
        "mirror": ("mirror",),
        "holewizard": ("holewizard", "hole"),
        "revolve": ("revolve",),
        "cut-revolve": ("revolve",),
        "sweep": ("sweep",),
        "cut-sweep": ("sweep",),
        "loft": ("loft", "blend"),
        "cut-loft": ("loft", "blend"),
        "shell": ("shell",),
        "rib": ("rib",),
        "dome": ("dome",),
        "chamfer": ("chamfer",),
        "draft": ("draft",),
        "thicken": ("thicken",),
        "wrap": ("wrap",),
        "baseflange": ("baseflange", "sm-baseflange"),
        "edgeflange": ("edgeflange",),
    }
    normalized_type = feature_type.casefold().replace(" ", "")
    tokens = aliases.get(normalized_type, (normalized_type,))
    after = _feature_tree_snapshot(adapter)
    for name, feature in after.items():
        if name in before:
            continue
        name_key = name.casefold().replace("-", "").replace("_", "")
        type_name = str(_read_member(feature, "GetTypeName2") or "").casefold()
        if any(token.replace("-", "") in name_key for token in tokens):
            return feature
        if any(token.replace("-", "") in type_name for token in tokens):
            return feature
    return None


def _typed_feature_manager(manager: Any) -> Any:
    """Use the generated IFeatureManager wrapper when pywin32 provides it.

    The base-flange API has an optional ``IDispatch`` argument.  A dynamic
    ``CDispatch`` sends Python ``None`` as ``VT_EMPTY`` rather than a null
    ``VT_DISPATCH``, which SolidWorks rejects.  The generated wrapper performs
    the coercion declared by the installed SolidWorks type library.  Plain
    test doubles and installations without generated metadata retain dynamic
    dispatch.
    """
    try:  # pragma: no cover - needs a real Windows COM type library.
        from win32com.client import CastTo

        return CastTo(manager, "IFeatureManager")
    except Exception:
        return manager


def _typed_com_interface(dispatch: Any, interface_name: str) -> Any:
    """Use a generated interface wrapper when pywin32 can provide one."""
    try:  # pragma: no cover - requires a real Windows COM type library.
        from win32com.client import CastTo

        return CastTo(dispatch, interface_name)
    except Exception:
        return dispatch


def _null_com_dispatch() -> Any:
    """Return a typed null ``IDispatch`` when pywin32 is installed."""
    try:  # pragma: no cover - depends on Windows pywin32.
        import pythoncom
        from win32com.client import VARIANT

        return VARIANT(pythoncom.VT_DISPATCH, None)
    except Exception:
        return None


def _new_sheet_metal_feature(adapter: Any) -> Any:
    """Return a sheet-metal tree feature created by APIs that return ``None``.

    Some SolidWorks releases create the base-flange feature successfully but
    omit the COM return value when called through late binding.  The current
    MCP workflow creates base flanges only from a fresh session-owned part, so
    finding a sheet-metal feature here is evidence of that successful call.
    """
    for position in range(4096):
        feature = adapter._attempt(
            lambda position=position: adapter.currentModel.FeatureByPositionReverse(
                position
            ),
            default=None,
        )
        if feature is None:
            break
        _flag_feature_methods(feature, "IFeature")
        if str(_read_member(feature, "GetTypeName2") or "").casefold() == "sheetmetal":
            return feature
    return None


def _create_base_flange_from_definition(
    adapter: Any,
    feature_manager: Any,
    *,
    thickness_m: float,
    bend_radius_m: float,
    k_factor: float,
) -> Any:
    """Create a base flange through ``IBaseFlangeFeatureData``.

    ``swFmBaseFlange`` is the stable SolidWorks feature-definition enum value
    (34).  The definition owns the thickness, bend radius, and K factor, so
    this path avoids the version-sensitive optional arguments of the legacy
    base-flange method.
    """
    definition = feature_manager.CreateDefinition(_SW_FM_BASE_FLANGE)
    if definition is None:
        raise RuntimeError("SolidWorks did not create a base-flange definition")
    for property_name, value in (
        ("UseGaugeTable", False),
        ("OverrideThickness", True),
        ("OverrideRadius", True),
        ("OverrideKFactor", True),
        ("Thickness", thickness_m),
        ("BendRadius", bend_radius_m),
        ("KFactor", k_factor),
    ):
        setattr(definition, property_name, value)
    feature = feature_manager.CreateFeature(definition)
    if feature is None:
        feature = _new_sheet_metal_feature(adapter)
    if feature is None:
        raise RuntimeError("SolidWorks did not create a base-flange feature")
    return feature


def _create_sheet_metal_base_flange_impl(
    adapter: Any, sketch_name: str, thickness_mm: float, bend_radius_mm: float, k_factor: float
) -> AdapterResult[SolidWorksFeature]:
    """Create a base flange from a server-tracked sketch using compatible COM overloads."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _create() -> SolidWorksFeature:
        sketch = _resolve_trusted_sketch_feature(adapter, sketch_name)
        if not sketch or not sketch.Select2(False, 0):
            raise RuntimeError("The trusted base-flange sketch is no longer selectable")
        try:
            feature_manager = _typed_feature_manager(adapter.currentModel.FeatureManager)
            _flag_feature_methods(feature_manager, "IFeatureManager")
            thickness_m = thickness_mm / 1000.0
            bend_radius_m = bend_radius_mm / 1000.0
            extrude_distance_m = max(thickness_m, 0.01)
            relief_size_m = max(thickness_m, 0.001)
            pcba = _null_com_dispatch()
            try:
                definition_feature = _create_base_flange_from_definition(
                    adapter,
                    feature_manager,
                    thickness_m=thickness_m,
                    bend_radius_m=bend_radius_m,
                    k_factor=k_factor,
                )
            except Exception:
                definition_feature = None
            feature = definition_feature or _call_feature_method(
                feature_manager,
                (
                    "InsertSheetMetalBaseFlange2",
                    "InsertSheetMetalBaseFlange",
                    "InsertBaseFlange2",
                ),
                (
                    # IFeatureManager::InsertSheetMetalBaseFlange2. K factor
                    # is not an argument of this API; it belongs to the
                    # sheet-metal definition and is set through the dedicated
                    # verified operation after feature creation.
                    (
                        thickness_m,
                        False,
                        bend_radius_m,
                        extrude_distance_m,
                        extrude_distance_m,
                        False,
                        0,
                        0,
                        0,
                        pcba,
                        True,
                        0,
                        relief_size_m,
                        relief_size_m,
                        0.5,
                        False,
                        True,
                        True,
                        True,
                    ),
                    (
                        thickness_m,
                        True,
                        bend_radius_m,
                        extrude_distance_m,
                        extrude_distance_m,
                        False,
                        0,
                        0,
                        0,
                        pcba,
                        True,
                        0,
                        relief_size_m,
                        relief_size_m,
                        0.5,
                        False,
                        True,
                        True,
                        True,
                    ),
                    (thickness_m, False, bend_radius_m),
                    (thickness_m,),
                ),
                recover_none=lambda: _new_sheet_metal_feature(adapter),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="BaseFlange",
                parameters={
                    "sketch_name": sketch_name,
                    "thickness_mm": thickness_mm,
                    "bend_radius_mm": bend_radius_mm,
                    "k_factor": k_factor,
                },
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_sheet_metal_base_flange", _create),
    )


def _create_sheet_metal_edge_flange_impl(
    adapter: Any,
    edge_reference: str,
    length_mm: float,
    angle_degrees: float,
    bend_radius_mm: float,
    k_factor: float,
) -> AdapterResult[SolidWorksFeature]:
    """Create an edge flange from a persistent edge reference with selection cleanup."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _create() -> SolidWorksFeature:
        _select_persistent_entities(adapter, [edge_reference], description="Part edge")
        try:
            features_before = _feature_tree_snapshot(adapter)
            feature = _call_feature_method(
                adapter.currentModel.FeatureManager,
                ("InsertEdgeFlange2", "InsertEdgeFlange"),
                (
                    (
                        length_mm / 1000.0,
                        math.radians(angle_degrees),
                        bend_radius_mm / 1000.0,
                        k_factor,
                    ),
                    (length_mm / 1000.0, math.radians(angle_degrees)),
                ),
                recover_none=lambda: _recover_new_feature(
                    adapter, features_before, "EdgeFlange"
                ),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="EdgeFlange",
                parameters={
                    "length_mm": length_mm,
                    "angle_degrees": angle_degrees,
                    "bend_radius_mm": bend_radius_mm,
                    "k_factor": k_factor,
                },
                before=features_before,
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_sheet_metal_edge_flange", _create),
    )


def _find_sheet_metal_feature(adapter: Any) -> Any:
    """Find the active part's sheet-metal definition without client feature names."""
    _flag_feature_methods(adapter.currentModel, "IModelDoc2")
    best_feature: Any = None
    best_priority = 0

    def consider(feature: Any) -> None:
        """Prefer the global sheet-metal definition that owns bend allowance."""
        nonlocal best_feature, best_priority
        _flag_feature_methods(feature, "IFeature")
        type_name = str(_read_member(feature, "GetTypeName2") or "").casefold()
        priority = 2 if type_name == "sheetmetal" else int("baseflange" in type_name)
        if priority > best_priority:
            best_feature = feature
            best_priority = priority

    # ``FirstFeature`` is unavailable through IDispatch on the local
    # SolidWorks release, whereas this positional accessor is reliable.
    # Some SolidWorks releases use a zero-based feature position, while
    # others expose the same accessor as one-based through late-bound COM.
    for first_position in (0, 1):
        for position in range(first_position, 4096):
            feature = adapter._attempt(
                lambda position=position: adapter.currentModel.FeatureByPositionReverse(
                    position
                ),
                default=None,
            )
            if feature is None:
                break
            consider(feature)

    feature = adapter._attempt(lambda: adapter.currentModel.FirstFeature(), default=None)
    seen: set[int] = set()
    while feature is not None and len(seen) < 4096:
        if id(feature) in seen:
            break
        seen.add(id(feature))
        consider(feature)
        feature = adapter._attempt(
            lambda feature=feature: _read_member(feature, "GetNextFeature"),
            default=None,
        )
    if best_feature is not None:
        return best_feature
    raise RuntimeError("Active part does not contain a sheet-metal feature")


def _sheet_metal_definition_interface(feature: Any) -> str:
    """Return the generated feature-data interface matching the feature type."""
    _flag_feature_methods(feature, "IFeature")
    type_name = str(_read_member(feature, "GetTypeName2") or "").casefold()
    if "baseflange" in type_name:
        return "IBaseFlangeFeatureData"
    return "ISheetMetalFeatureData"


def _set_sheet_metal_k_factor_impl(
    adapter: Any, k_factor: float
) -> AdapterResult[dict[str, Any]]:
    """Set a sheet-metal definition K factor and verify the value after rebuilding."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _set() -> dict[str, Any]:
        feature = _find_sheet_metal_feature(adapter)
        _flag_feature_methods(feature, "IFeature")
        definition_interface = _sheet_metal_definition_interface(feature)
        raw_definition = _read_member(feature, "GetDefinition")
        definition = _typed_com_interface(raw_definition, definition_interface)
        if definition is None:
            raise RuntimeError("Active sheet-metal feature does not expose KFactor")

        # SolidWorks requires feature-data edits to occur while its selection
        # context is open.  Skipping AccessSelections can make a valid
        # KFactor property appear unavailable or reject ModifyDefinition.
        _flag_feature_methods(definition, "ISheetMetalFeatureData")
        _flag_feature_methods(definition, "IBaseFlangeFeatureData")
        access_selections = getattr(definition, "AccessSelections", None)
        selection_accessed = False
        component = _null_com_dispatch()
        if callable(access_selections):
            try:
                selection_accessed = access_selections(adapter.currentModel, component) is not False
            except Exception as exc:
                raise RuntimeError(
                    f"SolidWorks could not access the sheet-metal definition: {exc}"
                ) from exc
            if not selection_accessed:
                raise RuntimeError("SolidWorks rejected access to the sheet-metal definition")

        modifier = getattr(feature, "ModifyDefinition", None)
        try:
            try:
                custom_bend_allowance = adapter._attempt(
                    lambda: _read_member(definition, "GetCustomBendAllowance"), default=None
                )
                if custom_bend_allowance is None:
                    # IBaseFlangeFeatureData owns these values directly.
                    try:
                        definition.UseGaugeTable = False
                    except Exception:
                        pass
                    try:
                        definition.OverrideKFactor = True
                    except Exception:
                        pass
                    definition.KFactor = k_factor
                else:
                    custom_bend_allowance = _typed_com_interface(
                        custom_bend_allowance, "ICustomBendAllowance"
                    )
                    _flag_feature_methods(custom_bend_allowance, "ICustomBendAllowance")
                    override_default = getattr(definition, "SetOverrideDefaultParameter2", None)
                    if callable(override_default):
                        override_result = override_default(
                            _SW_SHEET_METAL_OVERRIDE_DEFAULT_BEND_ALLOWANCE, True
                        )
                    else:
                        override_default = getattr(definition, "SetOverrideDefaultParameter", None)
                        override_result = (
                            override_default(True) if callable(override_default) else None
                        )
                    if override_result is False:
                        raise RuntimeError("SolidWorks rejected the bend-allowance override")
                    custom_bend_allowance.Type = _SW_BEND_ALLOWANCE_K_FACTOR
                    custom_bend_allowance.KFactor = k_factor
                    setter = getattr(definition, "SetCustomBendAllowance", None)
                    if not callable(setter):
                        raise RuntimeError("SolidWorks does not expose SetCustomBendAllowance")
                    if setter(custom_bend_allowance) is False:
                        raise RuntimeError("SolidWorks rejected the custom bend allowance")
            except Exception as exc:
                raise RuntimeError(
                    "SolidWorks did not expose a writable sheet-metal bend allowance "
                    f"or KFactor: {exc}"
                ) from exc
            if callable(modifier) and modifier(definition, adapter.currentModel, component) is False:
                raise RuntimeError("SolidWorks rejected the sheet-metal K factor")
            selection_accessed = False  # ModifyDefinition consumes the access context.
            adapter.currentModel.ForceRebuild3(False)
            updated_definition = _typed_com_interface(
                _read_member(feature, "GetDefinition"), definition_interface
            )
            actual = _read_member(updated_definition, "KFactor")
            updated_custom_allowance = adapter._attempt(
                lambda: _read_member(updated_definition, "GetCustomBendAllowance"), default=None
            )
            if updated_custom_allowance is not None:
                updated_custom_allowance = _typed_com_interface(
                    updated_custom_allowance, "ICustomBendAllowance"
                )
                custom_k_factor = _read_member(updated_custom_allowance, "KFactor")
                if isinstance(custom_k_factor, (int, float)):
                    actual = custom_k_factor
            if not isinstance(actual, (int, float)) or not math.isclose(
                float(actual), k_factor, abs_tol=1e-9
            ):
                raise RuntimeError(
                    "SolidWorks did not retain the requested sheet-metal K factor "
                    f"(requested={k_factor!r}, actual={actual!r})"
                )
            return {"k_factor": float(actual)}
        finally:
            if selection_accessed:
                release_access = getattr(definition, "ReleaseSelectionAccess", None)
                if callable(release_access):
                    adapter._attempt(release_access, default=None)

    return cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation("set_sheet_metal_k_factor", _set),
    )


def _create_shell_impl(
    adapter: Any, face_references: list[str], thickness_mm: float, outward: bool
) -> AdapterResult[SolidWorksFeature]:
    """Create a shell after selecting only workflow-owned removable faces."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _create() -> SolidWorksFeature:
        _select_persistent_entities(adapter, face_references, description="Part face")
        try:
            features_before = _feature_tree_snapshot(adapter)
            feature = _call_feature_method(
                adapter.currentModel.FeatureManager,
                ("InsertShell", "InsertShell2"),
                (
                    (thickness_mm / 1000.0, outward),
                    (thickness_mm / 1000.0, outward, 0, 0, 0, 0, 0, 0),
                ),
                recover_none=lambda: _recover_new_feature(adapter, features_before, "Shell"),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="Shell",
                parameters={"thickness_mm": thickness_mm, "outward": outward},
                before=features_before,
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature], adapter._handle_com_operation("create_shell", _create)
    )


def _create_rib_impl(
    adapter: Any, sketch_name: str, thickness_mm: float, draft_angle_degrees: float, direction: str
) -> AdapterResult[SolidWorksFeature]:
    """Create a rib from a trusted sketch through compatible FeatureManager overloads."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _create() -> SolidWorksFeature:
        sketch = adapter.currentModel.FeatureByName(sketch_name)
        if not sketch or not sketch.Select2(False, 0):
            raise RuntimeError("The trusted rib sketch is no longer selectable")
        try:
            features_before = _feature_tree_snapshot(adapter)
            feature = _call_feature_method(
                adapter.currentModel.FeatureManager,
                ("InsertRib", "InsertRib2"),
                (
                    (direction == "normal", False, thickness_mm / 1000.0, math.radians(draft_angle_degrees)),
                    (thickness_mm / 1000.0,),
                ),
                recover_none=lambda: _recover_new_feature(adapter, features_before, "Rib"),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="Rib",
                parameters={
                    "sketch_name": sketch_name,
                    "thickness_mm": thickness_mm,
                    "draft_angle_degrees": draft_angle_degrees,
                    "direction": direction,
                },
                before=features_before,
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature], adapter._handle_com_operation("create_rib", _create)
    )


def _create_dome_impl(
    adapter: Any, face_reference: str, height_mm: float, reverse_direction: bool
) -> AdapterResult[SolidWorksFeature]:
    """Create a dome after selecting one workflow-owned target face."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _create() -> SolidWorksFeature:
        _select_persistent_entities(adapter, [face_reference], description="Part face")
        try:
            features_before = _feature_tree_snapshot(adapter)
            feature = _call_feature_method(
                adapter.currentModel.FeatureManager,
                ("InsertDome", "InsertDome2"),
                ((height_mm / 1000.0, reverse_direction), (height_mm / 1000.0,)),
                recover_none=lambda: _recover_new_feature(adapter, features_before, "Dome"),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="Dome",
                parameters={"height_mm": height_mm, "reverse_direction": reverse_direction},
                before=features_before,
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature], adapter._handle_com_operation("create_dome", _create)
    )


def _create_draft_impl(
    adapter: Any,
    face_references: list[str],
    angle_degrees: float,
    reverse_direction: bool,
    edge_draft: bool,
) -> AdapterResult[SolidWorksFeature]:
    """Create a neutral-plane multi-face draft from persistent part-face references."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _create() -> SolidWorksFeature:
        _select_persistent_entities(adapter, face_references, description="Part face")
        try:
            features_before = _feature_tree_snapshot(adapter)
            feature = _call_feature_method(
                adapter.currentModel.FeatureManager,
                ("InsertMultiFaceDraft",),
                (
                    (
                        math.radians(angle_degrees),
                        reverse_direction,
                        edge_draft,
                        _SW_DRAFT_TYPE_NEUTRAL_PLANE,
                        False,
                        False,
                    ),
                ),
                recover_none=lambda: _recover_new_feature(adapter, features_before, "Draft"),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="Draft",
                parameters={
                    "angle_degrees": angle_degrees,
                    "reverse_direction": reverse_direction,
                    "edge_draft": edge_draft,
                },
                before=features_before,
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature], adapter._handle_com_operation("create_draft", _create)
    )


def _create_thicken_impl(
    adapter: Any,
    face_reference: str,
    thickness_mm: float,
    operation: str,
    direction: str,
    fill_volume: bool,
    merge_result: bool,
) -> AdapterResult[SolidWorksFeature]:
    """Thicken one persistent surface face using the documented FeatureManager API."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
    direction_value = _SW_THICKEN_DIRECTIONS.get(direction)
    if direction_value is None:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Unsupported thicken direction",
        )
    if operation not in {"boss", "cut"}:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Unsupported thicken operation",
        )

    def _create() -> SolidWorksFeature:
        _select_persistent_entities(adapter, [face_reference], description="Part face")
        try:
            method_name = "FeatureBossThicken" if operation == "boss" else "FeatureCutThicken"
            arguments: tuple[Any, ...]
            if operation == "boss":
                arguments = (
                    thickness_mm / 1000.0,
                    direction_value,
                    0,
                    fill_volume,
                    merge_result,
                    False,
                    False,
                )
            else:
                arguments = (
                    thickness_mm / 1000.0,
                    direction_value,
                    0,
                    fill_volume,
                    False,
                    False,
                )
            features_before = _feature_tree_snapshot(adapter)
            feature = _call_feature_method(
                adapter.currentModel.FeatureManager,
                (method_name,),
                (arguments,),
                recover_none=lambda: _recover_new_feature(adapter, features_before, "Thicken"),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="Thicken",
                parameters={
                    "thickness_mm": thickness_mm,
                    "operation": operation,
                    "direction": direction,
                    "fill_volume": fill_volume,
                    "merge_result": merge_result,
                },
                before=features_before,
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature], adapter._handle_com_operation("create_thicken", _create)
    )


def _create_wrap_impl(
    adapter: Any,
    sketch_name: str,
    face_reference: str,
    operation: str,
    thickness_mm: float,
    reverse_direction: bool,
    method: str,
) -> AdapterResult[SolidWorksFeature]:
    """Wrap a trusted sketch on one persistent face without name-based face selection."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
    operation_value = _SW_WRAP_OPERATIONS.get(operation)
    method_value = _SW_WRAP_METHODS.get(method)
    if operation_value is None or method_value is None:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="Unsupported wrap parameters")

    def _create() -> SolidWorksFeature:
        sketch = adapter.currentModel.FeatureByName(sketch_name)
        if not sketch or not sketch.Select2(False, 0):
            raise RuntimeError("The trusted wrap sketch is no longer selectable")
        try:
            _select_persistent_entities(
                adapter,
                [face_reference],
                description="Part face",
                clear_selection=False,
                append_to_existing=True,
            )
            features_before = _feature_tree_snapshot(adapter)
            feature = _call_feature_method(
                adapter.currentModel.FeatureManager,
                ("InsertWrapFeature2", "InsertWrapFeature"),
                (
                    (
                        operation_value,
                        thickness_mm / 1000.0,
                        reverse_direction,
                        method_value,
                        0,
                    ),
                    (operation_value, thickness_mm / 1000.0, reverse_direction),
                ),
                recover_none=lambda: _recover_new_feature(adapter, features_before, "Wrap"),
            )
            return _created_feature(
                adapter,
                feature,
                feature_type="Wrap",
                parameters={
                    "sketch_name": sketch_name,
                    "operation": operation,
                    "thickness_mm": thickness_mm,
                    "reverse_direction": reverse_direction,
                    "method": method,
                },
                before=features_before,
            )
        finally:
            adapter.currentModel.ClearSelection2(True)

    return cast(
        AdapterResult[SolidWorksFeature], adapter._handle_com_operation("create_wrap", _create)
    )


def _parse_edge_spec(  # pragma: no cover
    edge_name: str,
) -> tuple[str, float, float, float]:
    """Parse an edge specification string into (SelectByID2 name, x, y, z).

    Supports two formats:
    - ``"Edge<1>"`` — name-based selection (x=y=z=0.0, SW looks up by topology name)
    - ``"x,y,z"`` — coordinate-based selection (name="", coordinate hint in metres)
    """
    parts = edge_name.split(",")
    if len(parts) == 3:
        try:
            x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
            return "", x, y, z
        except ValueError:
            pass
    return edge_name, 0.0, 0.0, 0.0


def _select_edge_by_coord(  # pragma: no cover
    adapter: Any,
    x: float,
    y: float,
    z: float,
    append: bool,
    mark: int = 0,
) -> bool:
    """Select the edge nearest to (x, y, z) in metres via ``SelectByID2``.

    Calls ``ForceRebuild3`` on the first edge in the selection set so that
    recent features are fully tessellated and their edges are selectable.
    Tries the primary coordinate and several small radial/Y offsets to
    improve hit probability on curved edges.

    The ``Callout`` parameter of ``SelectByID2`` requires a VT_DISPATCH null
    VARIANT — passing plain Python ``None`` triggers DISP_E_TYPEMISMATCH.

    Returns True if an edge was successfully selected; False otherwise.
    """
    import math

    model = adapter.currentModel

    # Rebuild to tessellate geometry from recent features before the first
    # edge in the selection set (when append=False this is the first edge).
    if not append:
        adapter._attempt(lambda: model.ForceRebuild3(True), default=None)

    r = math.sqrt(x**2 + z**2)
    if r > 0:
        # Candidates: exact point plus small radial scale-in/out and Y offsets.
        candidates = [
            (x, y, z),
            (x * 0.999, y, z * 0.999),
            (x * 1.001, y, z * 1.001),
            (x * 0.997, y, z * 0.997),
            (x * 1.003, y, z * 1.003),
            (x, y * 0.999, z),
            (x, y * 1.001, z),
        ]
    else:
        candidates = [
            (x, y, z),
            (x, y * 0.999, z),
            (x, y * 1.001, z),
        ]

    # VT_DISPATCH null pointer — required by SelectByID2's Callout parameter.
    # Plain Python None marshals as VT_NULL which SW rejects with DISP_E_TYPEMISMATCH.
    try:
        import pythoncom
        import win32com.client as _win32com

        null_callout = _win32com.VARIANT(pythoncom.VT_DISPATCH, None)
    except Exception:
        null_callout = None  # fallback: may fail on some SW versions

    for cx, cy, cz in candidates:
        try:
            selected = bool(
                model.Extension.SelectByID2(
                    "", "EDGE", cx, cy, cz, append, mark, null_callout, 0
                )
            )
        except Exception:
            selected = False
        if selected:
            return True

    return False


def _add_fillet_impl(
    adapter: Any, radius: float, edge_names: list[str]
) -> AdapterResult[SolidWorksFeature]:
    """Create a constant-radius fillet on one or more named edges.

    Each edge in ``edge_names`` is selected by name using
    ``Extension.SelectByID2`` with entity type ``"EDGE"``.  After all edges
    are in the selection set, ``FeatureFillet3`` is called to build the
    feature.

    Args:
        adapter: A fully connected ``PyWin32Adapter`` with a non-``None``
            ``currentModel``.
        radius: Fillet radius in **millimetres**.  Converted to metres
            internally before the COM call.
        edge_names: List of SolidWorks edge entity names to fillet, e.g.
            ``["Edge<1>", "Edge<2>"]``.

    Returns:
        AdapterResult[SolidWorksFeature]: On success, ``data`` is a
        ``SolidWorksFeature`` whose ``type`` is ``"Fillet"``.  On failure,
        ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when an
            edge cannot be selected or ``FeatureFillet3`` returns ``None``.

    Example::

        result = pywin32_feature_ops.add_fillet(
            adapter, radius=3.0, edge_names=["Edge<1>", "Edge<3>"]
        )
        print(result.data.name)  # e.g. "Fillet1"
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _fillet_operation() -> SolidWorksFeature:  # pragma: no cover
        """Inner COM closure that selects edges and invokes FeatureFillet3.

        Returns:
            SolidWorksFeature: Populated feature descriptor.

        Raises:
            Exception: If any edge selection fails or the feature is ``None``.
        """
        # Detect SW major version for FeatureFillet3 parameter count
        fillet_sw_major = 0
        if getattr(adapter, "swApp", None):
            rev = adapter._attempt(
                lambda: adapter._get_attr_or_call(adapter.swApp, "RevisionNumber"),
                default="0",
            )
            try:
                fillet_sw_major = int(str(rev).split(".")[0])
            except (ValueError, IndexError):
                fillet_sw_major = 0

        # Clear any prior selection so the edge set is clean.
        adapter._attempt(
            lambda: adapter.currentModel.ClearSelection2(True), default=None
        )

        for idx, edge_name in enumerate(edge_names):
            sel_name, ex, ey, ez = _parse_edge_spec(edge_name)
            append = idx > 0  # first edge starts fresh, subsequent ones append
            if sel_name == "":
                # Coordinate-based: traverse body edges and pick the closest one.
                selected = _select_edge_by_coord(adapter, ex, ey, ez, append=append)
            else:
                selected = adapter._attempt(
                    lambda sn=sel_name, _x=ex, _y=ey, _z=ez, _ap=append: (
                        adapter.currentModel.Extension.SelectByID2(
                            sn, "EDGE", _x, _y, _z, _ap, 0, None, 0
                        )
                    ),
                    default=False,
                )
            if not selected:
                raise Exception(f"Failed to select edge: {edge_name}")

        # SW 2025+ (major >= 33): IModelDoc2.FeatureFillet3 (9 params).
        # Returns a non-zero int on success — NOT an IFeature — so we look up
        # the last modified feature afterwards to get the name/id.
        # Older builds: IFeatureManager.FeatureFillet3 (15 params, returns IFeature).
        if fillet_sw_major >= 33:
            result_code = adapter.currentModel.FeatureFillet3(
                radius / 1000.0,  # R1 in meters
                True,  # Propagate (VT_BOOL)
                0,  # Ftyp (VT_I4)
                False,  # VarRadTyp (VT_BOOL — must be bool, not int)
                0,  # OverflowType (VT_I4)
                0,  # NRadii (VT_I4)
                None,  # Radii (VT_VARIANT)
                False,  # UseHelpPoint (VT_BOOL)
                False,  # UseTangentHoldLine (VT_BOOL)
            )
            if not result_code:
                raise Exception(
                    "Failed to create fillet (IModelDoc2.FeatureFillet3 returned 0)"
                )
            feature = None  # int return — retrieve feature object below
        else:
            feature_manager = adapter.currentModel.FeatureManager
            feature = feature_manager.FeatureFillet3(
                radius / 1000.0,
                0,
                0,
                0,
                0,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                0,
                False,
            )
            if not feature:
                raise Exception("Failed to create fillet")

        # Resolve the feature name — FeatureFillet3 on SW 2025+ returns an int,
        # not an IFeature, so we can only report a default name here.
        feature_name = "Fillet"
        if feature is not None and hasattr(feature, "Name"):
            try:
                feature_name = feature.Name or "Fillet"
            except Exception:
                pass

        return SolidWorksFeature(
            name=feature_name,
            type="Fillet",
            id=adapter._get_feature_id(feature) if feature else "",
            parameters={"radius": radius, "edges": edge_names},
            properties={"created": datetime.now().isoformat()},
        )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("add_fillet", _fillet_operation),
    )


def _create_chamfer_impl(
    adapter: Any,
    edge_references: list[str],
    distance_mm: float,
    angle_degrees: float,
    chamfer_type: str,
) -> AdapterResult[SolidWorksFeature]:
    """Create a validated 3D chamfer from persistent edge references.

    This is deliberately separate from the legacy name/coordinate API.  The
    workflow has already checked the opaque IDs and revision; this adapter
    resolves them through SolidWorks' persistent-reference API and verifies
    that SolidWorks selected exactly the requested number of edges before it
    invokes the version-specific chamfer method.
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
    if not edge_references:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="At least one edge is required")
    if chamfer_type not in {"equal_distance", "distance_angle"}:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="Unsupported chamfer type")

    def _create() -> SolidWorksFeature:
        model = adapter.currentModel
        _select_persistent_entities(adapter, edge_references, description="Part edge")
        try:
            selection_manager = getattr(model, "SelectionManager", None)
            count_method = getattr(selection_manager, "GetSelectedObjectCount2", None)
            selected_count = int(count_method(-1) or 0) if callable(count_method) else 0
            if selected_count != len(edge_references):
                raise RuntimeError(
                    "SolidWorks selected an unexpected number of edges "
                    f"(requested={len(edge_references)}, selected={selected_count})"
                )

            rebuild = getattr(model, "ForceRebuild3", None)
            if callable(rebuild) and rebuild(True) is False:
                raise RuntimeError("SolidWorks could not rebuild the part before chamfer creation")

            before = _feature_tree_snapshot(adapter)
            feature_manager = _typed_feature_manager(getattr(model, "FeatureManager", None))
            _flag_feature_methods(feature_manager, "IFeatureManager")
            feature: Any = None
            feature_type_code = 0 if chamfer_type == "equal_distance" else 1
            modern_error: Exception | None = None
            modern_succeeded = False
            if callable(getattr(model, "FeatureChamferType", None)):
                try:
                    modern_result = model.FeatureChamferType(
                        feature_type_code,
                        distance_mm / 1000.0,
                        math.radians(angle_degrees),
                        False,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                    )
                    if modern_result is not False and modern_result != 0:
                        modern_succeeded = True
                        # FeatureChamferType is commonly a void COM method;
                        # rebuild before looking for its newly-created tree item.
                        if callable(rebuild) and rebuild(True) is False:
                            raise RuntimeError(
                                "SolidWorks could not rebuild after FeatureChamferType"
                            )
                        feature = _recover_new_feature(adapter, before, "Chamfer")
                        if feature is None:
                            raise RuntimeError(
                                "SolidWorks created a chamfer but did not expose it in the feature tree"
                            )
                except Exception as exc:
                    modern_error = exc
                    if modern_succeeded:
                        raise
                    feature = None
            if feature is None:
                feature, insert_error = adapter._attempt_with_error(
                    lambda: feature_manager.InsertFeatureChamfer(
                        1,
                        1 if chamfer_type == "equal_distance" else 2,
                        distance_mm / 1000.0,
                        math.radians(angle_degrees),
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                    )
                )
                if not feature:
                    feature = _recover_new_feature(adapter, before, "Chamfer")
                if not feature:
                    raise RuntimeError(
                        "SolidWorks did not create a chamfer ("
                        f"{insert_error or modern_error or 'no feature returned'})"
                    )

            if feature is None:
                raise RuntimeError("SolidWorks did not expose the created chamfer feature")

            for member_name in ("IsSuppressed", "GetSuppression"):
                member = getattr(feature, member_name, None)
                if callable(member) and bool(member()):
                    raise RuntimeError("SolidWorks created the chamfer in a suppressed state")
            for member_name in ("GetErrorCode2", "GetErrorCode"):
                member = getattr(feature, member_name, None)
                if callable(member):
                    error_code = member()
                    if isinstance(error_code, (tuple, list)):
                        error_code = error_code[0] if error_code else 0
                    if isinstance(error_code, int) and error_code != 0:
                        raise RuntimeError(
                            f"SolidWorks reported chamfer feature error code {error_code}"
                        )

            rebuild_after = rebuild(True) if callable(rebuild) else True
            if rebuild_after is False:
                raise RuntimeError("SolidWorks could not rebuild the part after chamfer creation")
            feature = _recover_new_feature(adapter, before, "Chamfer") or feature
            return _created_feature(
                adapter,
                feature,
                feature_type="Chamfer",
                parameters={
                    "distance_mm": distance_mm,
                    "angle_degrees": angle_degrees,
                    "chamfer_type": chamfer_type,
                    "edge_count": len(edge_references),
                },
                before=before,
            )
        finally:
            adapter._attempt(lambda: model.ClearSelection2(True), default=None)

    result = cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("create_chamfer", _create),
    )
    if result.is_error:
        error_text = (result.error or "").lower()
        if "unexpected number of edges" in error_text:
            error_code = ADAPTER_ERROR_CHAMFER_SELECTION_COUNT
        elif any(
            marker in error_text
            for marker in (
                "could not select",
                "can no longer be resolved",
                "reference is invalid",
                "requested part edge",
            )
        ):
            error_code = ADAPTER_ERROR_CHAMFER_EDGE_SELECTION
        else:
            error_code = ADAPTER_ERROR_CHAMFER_CREATION
        result.metadata = {**(result.metadata or {}), "error_code": error_code}
    if result.is_success:
        result.metadata = {
            "diagnostics": {
                "requested_edge_count": len(edge_references),
                "selected_edge_count": len(edge_references),
                "rebuild_succeeded": True,
                "geometry_validated": False,
                "feature_state_validated": True,
                "validation_scope": "SolidWorks feature error/suppression and rebuild status; BREP validity not queried",
            }
        }
    return result


def _add_chamfer_impl(
    adapter: Any, distance: float, edge_names: list[str]
) -> AdapterResult[SolidWorksFeature]:
    """Create an equal-distance chamfer on one or more named edges.

    Each edge in ``edge_names`` is selected by name using
    ``Extension.SelectByID2`` with entity type ``"EDGE"``.  After all edges
    are in the selection set, ``FeatureChamfer`` is called in
    equal-distance mode (type ``1``).

    Args:
        adapter: A fully connected ``PyWin32Adapter`` with a non-``None``
            ``currentModel``.
        distance: Chamfer distance in **millimetres**.  Converted to metres
            internally.
        edge_names: List of SolidWorks edge entity names, e.g.
            ``["Edge<2>", "Edge<5>"]``.

    Returns:
        AdapterResult[SolidWorksFeature]: On success, ``data`` is a
        ``SolidWorksFeature`` whose ``type`` is ``"Chamfer"``.  On failure,
        ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when an
            edge cannot be selected or ``FeatureChamfer`` returns ``None``.

    Example::

        result = pywin32_feature_ops.add_chamfer(
            adapter, distance=2.0, edge_names=["Edge<2>"]
        )
        print(result.data.name)  # e.g. "Chamfer1"
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _chamfer_operation() -> SolidWorksFeature:  # pragma: no cover
        """Inner COM closure that selects edges and invokes the chamfer API.

        Returns:
            SolidWorksFeature: Populated feature descriptor.

        Raises:
            Exception: If any edge selection fails or the feature is not created.
        """
        import math

        # Detect SW major version (same pattern as fillet).
        chamfer_sw_major = 0
        if getattr(adapter, "swApp", None):
            rev = adapter._attempt(
                lambda: adapter._get_attr_or_call(adapter.swApp, "RevisionNumber"),
                default="0",
            )
            try:
                chamfer_sw_major = int(str(rev).split(".")[0])
            except (ValueError, IndexError):
                chamfer_sw_major = 0

        # Clear any prior selection so the edge set is clean.
        adapter._attempt(
            lambda: adapter.currentModel.ClearSelection2(True), default=None
        )

        for idx, edge_name in enumerate(edge_names):
            sel_name, cx, cy, cz = _parse_edge_spec(edge_name)
            append = idx > 0
            if sel_name == "":
                selected = _select_edge_by_coord(
                    adapter, cx, cy, cz, append=append, mark=0
                )
            else:
                selected = adapter._attempt(
                    lambda sn=sel_name, _x=cx, _y=cy, _z=cz, _ap=append: (
                        adapter.currentModel.Extension.SelectByID2(
                            sn, "EDGE", _x, _y, _z, _ap, 0, None, 0
                        )
                    ),
                    default=False,
                )
            if not selected:
                raise Exception(f"Failed to select edge: {edge_name}")

        selection_manager = getattr(adapter.currentModel, "SelectionManager", None)
        count_method = getattr(selection_manager, "GetSelectedObjectCount2", None)
        if callable(count_method):
            selected_count = int(count_method(-1) or 0)
            if selected_count != len(edge_names):
                raise Exception(
                    "SolidWorks selected an unexpected number of edges "
                    f"(requested={len(edge_names)}, selected={selected_count})"
                )
        rebuild = getattr(adapter.currentModel, "ForceRebuild3", None)
        if callable(rebuild) and rebuild(True) is False:
            raise Exception("SolidWorks could not rebuild the part before chamfer creation")

        fm = adapter.currentModel.FeatureManager
        _flag_feature_methods(fm, "IFeatureManager")
        features_before = _feature_tree_snapshot(adapter)
        count_before = adapter._attempt(
            lambda: int(fm.GetFeatureCount(True) or 0), default=0
        )

        feature_name = "Chamfer"
        feature = None

        if chamfer_sw_major >= 33:
            # SW 2025+ (major >= 33): IModelDoc2.FeatureChamferType (8 params,
            # VT_VOID). Detect success via feature count change.
            adapter.currentModel.FeatureChamferType(
                0,  # ChamferType: 0 = swChamferType_EqualDistance
                distance / 1000.0,  # Width in metres
                math.pi / 4,  # 45° angle
                False,  # Flip
                0.0,  # OtherDist
                0.0,  # VertexChamDist1
                0.0,  # VertexChamDist2
                0.0,  # VertexChamDist3
            )
            count_after = adapter._attempt(
                lambda: int(fm.GetFeatureCount(True) or 0), default=0
            )
            if count_after <= count_before:
                raise Exception(
                    "Failed to create chamfer (IModelDoc2.FeatureChamferType;"
                    " feature count unchanged)"
                )
        else:
            # Older SW: IFeatureManager.InsertFeatureChamfer returns IFeature.
            feature, insert_err = adapter._attempt_with_error(
                lambda: fm.InsertFeatureChamfer(
                    1,  # Options
                    1,  # ChamferType = equal distance
                    distance / 1000.0,  # Width in metres
                    math.pi / 4,  # 45° angle
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
            )
            if feature and hasattr(feature, "Name"):
                feature_name = feature.Name or "Chamfer"
            elif not feature:
                feature = _recover_new_feature(adapter, features_before, "Chamfer")
                if not feature:
                    raise Exception(
                        f"Failed to create chamfer (InsertFeatureChamfer: {insert_err})"
                    )
                feature_name = str(_read_member(feature, "Name") or "Chamfer")

        if callable(rebuild) and rebuild(True) is False:
            raise Exception("SolidWorks could not rebuild the part after chamfer creation")

        return SolidWorksFeature(
            name=feature_name,
            type="Chamfer",
            id=adapter._get_feature_id(feature) if feature else "",
            parameters={"distance": distance, "edges": edge_names},
            properties={"created": datetime.now().isoformat()},
        )

    return cast(
        AdapterResult[SolidWorksFeature],
        adapter._handle_com_operation("add_chamfer", _chamfer_operation),
    )
