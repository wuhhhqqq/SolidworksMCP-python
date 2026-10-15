"""Direct branch coverage tests for solidworks_mcp.adapters.solidworks.features."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    LoftParameters,
    SweepParameters,
)
from solidworks_mcp.adapters.solidworks import features


class _FakeFeatureAdapter:
    def __init__(self) -> None:
        self.currentModel = None
        self.constants = {
            "swEndCondBlind": 1,
            "swEndCondThroughAll": 2,
            "swStartSketchPlane": 0,
        }
        self._last_sketch_name = None
        self._sketch_count = 2

    def _handle_com_operation(self, _name, callback):
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))

    def _attempt(self, callback, default=None):
        try:
            return callback()
        except Exception:
            return default

    def _attempt_with_error(self, callback):
        try:
            return callback(), None
        except Exception as exc:
            return None, str(exc)

    def _get_feature_id(self, feature):
        return getattr(feature, "Name", "feature-id")


def test_create_cut_extrude_requires_model() -> None:
    adapter = _FakeFeatureAdapter()
    result = features._create_cut_extrude_impl(adapter, ExtrusionParameters(depth=5.0))
    assert result.status == AdapterResultStatus.ERROR
    assert result.error == "No active model"


def test_base_flange_resolves_the_latest_owned_sketch_from_reverse_tree() -> None:
    """A synthetic sketch name must not prevent the next feature operation."""
    adapter = _FakeFeatureAdapter()
    adapter._last_sketch_name = "Sketch_1"
    calls: list[tuple[object, ...]] = []
    sketch = SimpleNamespace(
        Name="Sketch1",
        GetTypeName2=lambda: "ProfileFeature",
        Select2=lambda *_args: True,
    )
    created = SimpleNamespace(Name="Base-Flange1")
    adapter.currentModel = SimpleNamespace(
        ClearSelection2=lambda *_args: True,
        FeatureByName=lambda _name: None,
        FeatureByPositionReverse=lambda position: sketch if position == 0 else None,
        FeatureManager=SimpleNamespace(
            InsertSheetMetalBaseFlange2=lambda *args: calls.append(args) or created
        ),
    )

    result = features._create_sheet_metal_base_flange_impl(
        adapter, "Sketch_1", thickness_mm=1.5, bend_radius_mm=1.5, k_factor=0.42
    )

    assert result.is_success
    assert result.data.name == "Base-Flange1"
    assert result.data.parameters["k_factor"] == 0.42
    assert len(calls) == 1
    assert len(calls[0]) == 19
    assert calls[0][:3] == (0.0015, False, 0.0015)


def test_base_flange_prefers_typed_feature_definition_api() -> None:
    """The definition path receives all requested sheet-metal values."""
    adapter = _FakeFeatureAdapter()
    sketch = SimpleNamespace(Name="Sketch1", Select2=lambda *_args: True)
    definition = SimpleNamespace()
    created = SimpleNamespace(Name="Base-Flange1")
    requested_types: list[int] = []
    adapter.currentModel = SimpleNamespace(
        ClearSelection2=lambda *_args: True,
        FeatureByName=lambda _name: sketch,
        FeatureManager=SimpleNamespace(
            CreateDefinition=lambda feature_type: requested_types.append(feature_type)
            or definition,
            CreateFeature=lambda feature_data: created if feature_data is definition else None,
        ),
    )

    result = features._create_sheet_metal_base_flange_impl(
        adapter, "Sketch1", thickness_mm=1.5, bend_radius_mm=2.0, k_factor=0.42
    )

    assert result.is_success
    assert requested_types == [34]
    assert definition.UseGaugeTable is False
    assert definition.OverrideThickness is True
    assert definition.OverrideRadius is True
    assert definition.OverrideKFactor is True
    assert definition.Thickness == 0.0015
    assert definition.BendRadius == 0.002
    assert definition.KFactor == 0.42


def test_sheet_metal_k_factor_finds_definition_from_reverse_tree() -> None:
    """K-factor editing works when IModelDoc2.FirstFeature is unavailable."""
    adapter = _FakeFeatureAdapter()
    accessed_models: list[object] = []
    custom_bend_allowance = SimpleNamespace(Type=1, KFactor=0.42)
    definition = SimpleNamespace(
        BendAllowanceType=1,
        KFactor=0.42,
        AccessSelections=lambda model, _component: accessed_models.append(model) or True,
        GetCustomBendAllowance=lambda: custom_bend_allowance,
        SetCustomBendAllowance=lambda allowance: allowance is custom_bend_allowance,
    )
    feature = SimpleNamespace(
        GetTypeName2=lambda: "SheetMetal",
        GetDefinition=lambda: definition,
        ModifyDefinition=lambda *_args: True,
    )
    adapter.currentModel = SimpleNamespace(
        FirstFeature=lambda: (_ for _ in ()).throw(RuntimeError("unsupported")),
        FeatureByPositionReverse=lambda position: feature if position == 0 else None,
        ForceRebuild3=lambda *_args: True,
    )

    result = features._set_sheet_metal_k_factor_impl(adapter, 0.4)

    assert result.is_success
    assert result.data == {"k_factor": 0.4}
    assert custom_bend_allowance.Type == 2
    assert custom_bend_allowance.KFactor == 0.4
    assert accessed_models == [adapter.currentModel]


def test_sheet_metal_k_factor_prefers_global_bend_allowance_definition() -> None:
    """The SheetMetal node owns the active bend-allowance override."""
    adapter = _FakeFeatureAdapter()
    override_calls: list[tuple[int, bool]] = []
    base_definition = SimpleNamespace(
        KFactor=0.42,
        UseGaugeTable=True,
        OverrideKFactor=False,
        AccessSelections=lambda *_args: True,
    )
    custom_bend_allowance = SimpleNamespace(Type=1, KFactor=0.5)
    sheet_definition = SimpleNamespace(
        KFactor=0.5,
        AccessSelections=lambda *_args: True,
        GetCustomBendAllowance=lambda: custom_bend_allowance,
        SetCustomBendAllowance=lambda allowance: allowance is custom_bend_allowance,
        SetOverrideDefaultParameter2=lambda parameter, override: override_calls.append(
            (parameter, override)
        )
        or True,
    )
    sheet_feature = SimpleNamespace(
        GetTypeName2=lambda: "SheetMetal",
        GetDefinition=lambda: sheet_definition,
        ModifyDefinition=lambda *_args: True,
    )
    base_feature = SimpleNamespace(
        GetTypeName2=lambda: "SMBaseFlange",
        GetDefinition=lambda: base_definition,
        ModifyDefinition=lambda *_args: True,
    )
    adapter.currentModel = SimpleNamespace(
        FirstFeature=lambda: sheet_feature,
        FeatureByPositionReverse=lambda position: {
            0: sheet_feature,
            1: base_feature,
        }.get(position),
        ForceRebuild3=lambda *_args: True,
    )

    result = features._set_sheet_metal_k_factor_impl(adapter, 0.4)

    assert result.is_success
    assert result.data == {"k_factor": 0.4}
    assert override_calls == [(1, True)]
    assert custom_bend_allowance.Type == 2
    assert custom_bend_allowance.KFactor == 0.4
    assert base_definition.KFactor == 0.42


def test_cut_sweep_and_cut_loft_call_cut_com_members() -> None:
    """Cut operations select the dedicated SolidWorks feature methods."""
    adapter = _FakeFeatureAdapter()
    calls: list[str] = []
    feature_manager = SimpleNamespace(
        InsertCutSwept4=lambda *_args: (
            calls.append(f"InsertCutSwept4:{len(_args)}")
            or SimpleNamespace(Name="Cut-Sweep1")
        ),
        InsertCutBlend=lambda *_args: (
            calls.append(f"InsertCutBlend:{len(_args)}")
            or SimpleNamespace(Name="Cut-Loft1")
        ),
    )
    sketch_two = SimpleNamespace(
        Name="PathSketch",
        GetTypeName2=lambda: "ProfileFeature",
        GetNextFeature=lambda: None,
        Select2=lambda *_args: True,
    )
    sketch_one = SimpleNamespace(
        Name="ProfileSketch",
        GetTypeName2=lambda: "ProfileFeature",
        GetNextFeature=lambda: sketch_two,
        Select2=lambda *_args: True,
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        ClearSelection2=lambda *_args: True,
        FirstFeature=sketch_one,
        FeatureByName=lambda name: {
            "ProfileSketch": sketch_one,
            "PathSketch": sketch_two,
        }.get(name),
        Extension=SimpleNamespace(SelectByID2=lambda *_args: True),
    )

    sweep = features._create_sweep_impl(
        adapter,
        SweepParameters(
            profile="ProfileSketch", path="PathSketch", is_cut=True
        ),
    )
    loft = features._create_loft_impl(
        adapter,
        LoftParameters(
            profiles=["ProfileSketch", "PathSketch"], is_cut=True
        ),
    )

    assert sweep.is_success
    assert sweep.data.type == "Cut-Sweep"
    assert loft.is_success
    assert loft.data.type == "Cut-Loft"
    assert calls == ["InsertCutSwept4:19", "InsertCutBlend:12"]


def test_create_cut_extrude_collects_all_fallback_errors() -> None:
    adapter = _FakeFeatureAdapter()

    feature_manager = SimpleNamespace(
        FeatureCut4=lambda *args: (_ for _ in ()).throw(RuntimeError("cut4 failed")),
        FeatureCut3=lambda *args: (_ for _ in ()).throw(RuntimeError("cut3 failed")),
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        ClearSelection2=lambda *_args: True,
        FirstFeature=None,
        Extension=SimpleNamespace(SelectByID2=lambda *args, **kwargs: False),
    )
    adapter._last_sketch_name = "Sketch2"

    result = features._create_cut_extrude_impl(
        adapter,
        ExtrusionParameters(depth=4.0, end_condition="ThroughAll", draft_angle=1.0),
    )

    assert result.status == AdapterResultStatus.ERROR
    assert "FeatureCut4: cut4 failed" in (result.error or "")
    assert "FeatureCut3 modern: cut3 failed" in (result.error or "")
    assert "FeatureCut3 legacy: cut3 failed" in (result.error or "")


def test_create_cut_extrude_uses_modern_fallback_when_cut4_returns_none() -> None:
    adapter = _FakeFeatureAdapter()

    feature = SimpleNamespace(Name="Cut-Extrude9")

    feature_manager = SimpleNamespace(
        FeatureCut4=lambda *args: None,
        FeatureCut3=lambda *args: feature,
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        ClearSelection2=lambda *_args: True,
        FirstFeature=None,
        Extension=SimpleNamespace(SelectByID2=lambda *args, **kwargs: True),
    )

    result = features._create_cut_extrude_impl(adapter, ExtrusionParameters(depth=6.0))
    assert result.is_success
    assert result.data.type == "Cut-Extrude"
    assert result.data.name == "Cut-Extrude9"


def test_create_cut_extrude_selects_tracked_sketch_before_cut4() -> None:
    """The tracked profile is selected before the first FeatureCut call."""
    adapter = _FakeFeatureAdapter()
    events: list[str] = []
    sketch = SimpleNamespace(
        Name="Sketch2",
        GetTypeName2=lambda: "ProfileFeature",
        Select2=lambda *_args: events.append("select") or True,
    )
    created = SimpleNamespace(Name="Cut-Extrude1", GetTypeName2=lambda: "Cut")
    feature_manager = SimpleNamespace(
        FeatureCut4=lambda *_args: events.append("cut") or created,
        FeatureCut3=lambda *_args: None,
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        FeatureByName=lambda name: sketch if name == "Sketch2" else None,
        ClearSelection2=lambda *_args: events.append("clear") or True,
        FeatureByPositionReverse=lambda _position: None,
        FirstFeature=None,
    )
    adapter._last_sketch_name = "Sketch2"

    result = features._create_cut_extrude_impl(adapter, ExtrusionParameters(depth=6.0))

    assert result.is_success
    assert events.index("select") < events.index("cut")


def test_create_cut_extrude_recovers_feature_when_cut4_returns_none() -> None:
    """A tree feature created by COM is success even when the return is empty."""
    adapter = _FakeFeatureAdapter()
    created = SimpleNamespace(Name="Cut-Extrude3", GetTypeName2=lambda: "Cut")
    state = {"created": False}

    def reverse_feature(position: int):
        return created if state["created"] and position == 0 else None

    def cut4(*_args):
        state["created"] = True
        return None

    feature_manager = SimpleNamespace(
        FeatureCut4=cut4,
        FeatureCut3=lambda *_args: (_ for _ in ()).throw(
            AssertionError("fallback would duplicate the cut")
        ),
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        ClearSelection2=lambda *_args: True,
        FeatureByPositionReverse=reverse_feature,
        FirstFeature=None,
    )

    result = features._create_cut_extrude_impl(adapter, ExtrusionParameters(depth=6.0))

    assert result.is_success
    assert result.data.name == "Cut-Extrude3"


def test_created_feature_recovers_new_extrusion_from_feature_tree() -> None:
    """The shared recovery path handles another feature family, not only cuts."""
    adapter = _FakeFeatureAdapter()
    created = SimpleNamespace(Name="Boss-Extrude1", GetTypeName2=lambda: "BossExtrude")
    state = {"created": False}
    adapter.currentModel = SimpleNamespace(
        FeatureByPositionReverse=lambda position: (
            created if state["created"] and position == 0 else None
        ),
        FirstFeature=None,
    )

    before = features._feature_tree_snapshot(adapter)
    state["created"] = True
    result = features._created_feature(
        adapter,
        None,
        feature_type="Extrusion",
        parameters={"depth": 10.0},
        before=before,
    )

    assert result.name == "Boss-Extrude1"
    assert result.type == "Extrusion"


def test_add_fillet_and_chamfer_selection_and_feature_failures() -> None:
    adapter = _FakeFeatureAdapter()

    feature_manager = SimpleNamespace(
        FeatureFillet3=lambda *args: None,
        FeatureChamfer=lambda *args: None,
    )
    extension = SimpleNamespace(SelectByID2=lambda edge, *_args: edge != "Edge<bad>")
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager, Extension=extension
    )

    fillet_select_error = features._add_fillet_impl(adapter, 2.0, ["Edge<bad>"])
    assert fillet_select_error.status == AdapterResultStatus.ERROR
    assert "Failed to select edge" in (fillet_select_error.error or "")

    fillet_feature_error = features._add_fillet_impl(adapter, 2.0, ["Edge<1>"])
    assert fillet_feature_error.status == AdapterResultStatus.ERROR
    assert "Failed to create fillet" in (fillet_feature_error.error or "")

    chamfer_select_error = features._add_chamfer_impl(adapter, 1.0, ["Edge<bad>"])
    assert chamfer_select_error.status == AdapterResultStatus.ERROR
    assert "Failed to select edge" in (chamfer_select_error.error or "")

    chamfer_feature_error = features._add_chamfer_impl(adapter, 1.0, ["Edge<2>"])
    assert chamfer_feature_error.status == AdapterResultStatus.ERROR
    assert "Failed to create chamfer" in (chamfer_feature_error.error or "")


def test_create_chamfer_resolves_persistent_edges_and_reports_diagnostics() -> None:
    """The safe chamfer path selects opaque edges and verifies the selection set."""
    adapter = _FakeFeatureAdapter()
    token = base64.b64encode(b"edge-ref").decode("ascii")
    selected: list[bool] = []
    edge = SimpleNamespace(Select4=lambda append, _callout: selected.append(append) or True)
    feature = SimpleNamespace(Name="Chamfer1")
    manager = SimpleNamespace(
        InsertFeatureChamfer=lambda *args: feature,
    )
    model = SimpleNamespace(
        FeatureManager=manager,
        Extension=SimpleNamespace(
            GetObjectByPersistReference3=lambda _raw: edge,
        ),
        SelectionManager=SimpleNamespace(GetSelectedObjectCount2=lambda _mark: 1),
        ForceRebuild3=lambda _top: True,
        ClearSelection2=lambda _all: True,
        FeatureByPositionReverse=lambda _position: None,
        FirstFeature=None,
    )
    adapter.currentModel = model

    result = features._create_chamfer_impl(
        adapter, [token], 2.0, 45.0, "equal_distance"
    )

    assert result.is_success
    assert result.data.name == "Chamfer1"
    assert result.metadata is not None
    assert result.metadata["diagnostics"]["selected_edge_count"] == 1
    assert selected == [False]


def test_create_chamfer_rejects_selection_count_mismatch() -> None:
    """A resolver/select mismatch must fail before the feature API is called."""
    adapter = _FakeFeatureAdapter()
    token = base64.b64encode(b"edge-ref").decode("ascii")
    manager = SimpleNamespace(
        InsertFeatureChamfer=lambda *_args: (_ for _ in ()).throw(
            AssertionError("feature API must not run")
        )
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=manager,
        Extension=SimpleNamespace(
            GetObjectByPersistReference3=lambda _raw: SimpleNamespace(
                Select4=lambda *_args: True
            )
        ),
        SelectionManager=SimpleNamespace(GetSelectedObjectCount2=lambda _mark: 0),
        ForceRebuild3=lambda _top: True,
        ClearSelection2=lambda _all: True,
    )

    result = features._create_chamfer_impl(
        adapter, [token], 2.0, 45.0, "equal_distance"
    )

    assert result.is_error
    assert "unexpected number of edges" in (result.error or "")
    assert result.metadata is not None
    assert result.metadata["error_code"] == "SW_CHAMFER_SELECTION_COUNT_MISMATCH"


def test_create_chamfer_falls_back_when_modern_api_dispatch_fails() -> None:
    """Late-bound unsupported FeatureChamferType must use the SW2019 API."""
    adapter = _FakeFeatureAdapter()
    token = base64.b64encode(b"edge-ref").decode("ascii")
    feature = SimpleNamespace(Name="Chamfer2")
    manager = SimpleNamespace(InsertFeatureChamfer=lambda *_args: feature)
    adapter.currentModel = SimpleNamespace(
        FeatureManager=manager,
        FeatureChamferType=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("member not supported")
        ),
        Extension=SimpleNamespace(
            GetObjectByPersistReference3=lambda _raw: SimpleNamespace(
                Select4=lambda *_args: True
            )
        ),
        SelectionManager=SimpleNamespace(GetSelectedObjectCount2=lambda _mark: 1),
        ForceRebuild3=lambda _top: True,
        ClearSelection2=lambda _all: True,
        FeatureByPositionReverse=lambda _position: None,
        FirstFeature=None,
    )

    result = features._create_chamfer_impl(
        adapter, [token], 1.0, 45.0, "equal_distance"
    )

    assert result.is_success
    assert result.data.name == "Chamfer2"


def test_create_chamfer_recovers_modern_void_feature_after_rebuild() -> None:
    """A void modern API result is accepted when the tree updates on rebuild."""
    adapter = _FakeFeatureAdapter()
    token = base64.b64encode(b"edge-ref").decode("ascii")
    created = SimpleNamespace(Name="Chamfer3", GetTypeName2=lambda: "Chamfer")
    state = {"rebuilds": 0}

    def rebuild(_top: bool) -> bool:
        state["rebuilds"] += 1
        return True

    def reverse_feature(position: int):
        return created if state["rebuilds"] >= 2 and position == 0 else None

    manager = SimpleNamespace(
        InsertFeatureChamfer=lambda *_args: (_ for _ in ()).throw(
            AssertionError("legacy chamfer API must not run")
        )
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=manager,
        FeatureChamferType=lambda *_args: None,
        Extension=SimpleNamespace(
            GetObjectByPersistReference3=lambda _raw: SimpleNamespace(
                Select4=lambda *_args: True
            )
        ),
        SelectionManager=SimpleNamespace(GetSelectedObjectCount2=lambda _mark: 1),
        ForceRebuild3=rebuild,
        ClearSelection2=lambda _all: True,
        FeatureByPositionReverse=reverse_feature,
        FirstFeature=None,
    )

    result = features._create_chamfer_impl(
        adapter, [token], 1.0, 45.0, "equal_distance"
    )

    assert result.is_success
    assert result.data.name == "Chamfer3"
    assert state["rebuilds"] >= 2


def test_create_cut_extrude_through_all_both_directions() -> None:
    """Through-all + both directions should use the combined end condition."""
    # Exercise the branch that uses swEndCondThroughAllBoth.
    adapter = _FakeFeatureAdapter()

    feature = SimpleNamespace(Name="Cut-Extrude1")
    feature_manager = SimpleNamespace(
        FeatureCut4=lambda *args: feature,
        FeatureCut3=lambda *args: None,
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        ClearSelection2=lambda *_args: True,
        FirstFeature=None,
        Extension=SimpleNamespace(SelectByID2=lambda *args, **kwargs: True),
    )
    adapter._last_sketch_name = "Sketch1"

    result = features._create_cut_extrude_impl(
        adapter,
        ExtrusionParameters(
            depth=5.0, end_condition="ThroughAll", both_directions=True
        ),
    )
    assert result.is_success
    assert result.data.type == "Cut-Extrude"


def test_create_cut_extrude_raises_when_no_feature_and_no_errors() -> None:
    """Missing cut feature should raise a generic error when no fallback errors exist."""
    # Force all cut methods to return None without errors to hit the final raise.
    adapter = _FakeFeatureAdapter()

    feature_manager = SimpleNamespace(
        FeatureCut4=lambda *args: None,
        FeatureCut3=lambda *args: None,
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        ClearSelection2=lambda *_args: True,
        FirstFeature=None,
        Extension=SimpleNamespace(SelectByID2=lambda *args, **kwargs: True),
    )
    adapter._last_sketch_name = "Sketch1"

    result = features._create_cut_extrude_impl(
        adapter,
        ExtrusionParameters(depth=4.0),
    )
    assert result.status == AdapterResultStatus.ERROR
    assert "Failed to create cut extrude feature" in (result.error or "")


def test_draft_thicken_and_wrap_use_persistent_face_selection() -> None:
    """Advanced surface features select only resolved server-issued geometry."""
    adapter = _FakeFeatureAdapter()
    calls: list[tuple[str, tuple[object, ...]]] = []
    face = SimpleNamespace(Select2=lambda append, mark: calls.append(("face", (append, mark))) or True)
    sketch = SimpleNamespace(
        Name="WrapSketch",
        Select2=lambda append, mark: calls.append(("sketch", (append, mark))) or True,
    )
    feature_manager = SimpleNamespace(
        InsertMultiFaceDraft=lambda *args: calls.append(("draft", args))
        or SimpleNamespace(Name="Draft1"),
        FeatureBossThicken=lambda *args: calls.append(("thicken", args))
        or SimpleNamespace(Name="Thicken1"),
        InsertWrapFeature2=lambda *args: calls.append(("wrap", args))
        or SimpleNamespace(Name="Wrap1"),
    )
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        FeatureByName=lambda name: sketch if name == "WrapSketch" else None,
        ClearSelection2=lambda *_args: calls.append(("clear", ())) or True,
        Extension=SimpleNamespace(GetObjectByPersistReference3=lambda _token: face),
    )
    token = base64.b64encode(b"persistent-face").decode("ascii")

    draft = features._create_draft_impl(adapter, [token], 3.0, False, False)
    thicken = features._create_thicken_impl(
        adapter, token, 1.5, "boss", "both", False, True
    )
    wrap = features._create_wrap_impl(
        adapter, "WrapSketch", token, "engrave", 0.5, False, "analytical"
    )

    assert draft.is_success
    assert draft.data.type == "Draft"
    assert thicken.is_success
    assert thicken.data.type == "Thicken"
    assert wrap.is_success
    assert wrap.data.type == "Wrap"
    assert ("draft", (features.math.radians(3.0), False, False, 0, False, False)) in calls
    assert ("thicken", (0.0015, 2, 0, False, True, False, False)) in calls
    assert ("wrap", (1, 0.0005, False, 0, 0)) in calls
    assert ("sketch", (False, 0)) in calls
    assert ("face", (True, 0)) in calls


# ---------------------------------------------------------------------------
# Fillet: SW 2025+ (major >= 33) code paths
# ---------------------------------------------------------------------------


class _FilletAdapterSW2026(_FakeFeatureAdapter):
    """Fake adapter that simulates SW 2026 (major=34) for fillet tests."""

    def _get_attr_or_call(self, obj, name, default=None):
        if obj is None:
            return default
        candidate = getattr(obj, name, None)
        if candidate is None:
            return default
        if callable(candidate):
            try:
                return candidate()
            except Exception:
                return default
        return candidate


def _make_sw2026_adapter(fillet3_return=1, last_feature=None):
    """Return a fake adapter reporting SW 2026 (major=34)."""
    adapter = _FilletAdapterSW2026()
    adapter.swApp = SimpleNamespace(RevisionNumber="34.0.0")

    feature_manager = SimpleNamespace(
        FeatureFillet3=lambda *args: None,  # old path — not used for major >= 33
        GetLastModifiedFeature=lambda: last_feature,
    )
    extension = SimpleNamespace(SelectByID2=lambda edge, *_args: edge != "Edge<bad>")

    # IModelDoc2.FeatureFillet3 on the model directly (SW 2025+ path)
    adapter.currentModel = SimpleNamespace(
        FeatureManager=feature_manager,
        Extension=extension,
        FeatureFillet3=lambda *args: fillet3_return,
    )
    return adapter


def test_add_fillet_sw2026_success_returns_default_name() -> None:
    """SW 2026 path: FeatureFillet3 returns non-zero; name defaults to 'Fillet'
    because IModelDoc2.FeatureFillet3 returns an int, not an IFeature."""
    adapter = _make_sw2026_adapter(fillet3_return=1)

    result = features._add_fillet_impl(adapter, 3.0, ["Edge<1>"])

    assert result.is_success
    assert result.data.type == "Fillet"
    assert result.data.name == "Fillet"


def test_add_fillet_sw2026_success_last_feature_none_uses_default_name() -> None:
    """SW 2026 path: GetLastModifiedFeature returns None — fall back to 'Fillet'."""
    adapter = _make_sw2026_adapter(fillet3_return=1, last_feature=None)

    result = features._add_fillet_impl(adapter, 2.0, ["Edge<1>"])

    assert result.is_success
    assert result.data.name == "Fillet"
    assert result.data.id == ""


def test_add_fillet_sw2026_failure_returns_zero() -> None:
    """SW 2026 path: FeatureFillet3 returns 0 — should raise and return error."""
    adapter = _make_sw2026_adapter(fillet3_return=0)

    result = features._add_fillet_impl(adapter, 2.0, ["Edge<1>"])

    assert result.status == AdapterResultStatus.ERROR
    assert "FeatureFillet3 returned 0" in (result.error or "")


def test_add_fillet_sw2026_edge_selection_failure() -> None:
    """SW 2026 path: edge selection failure is reported before fillet call."""
    adapter = _make_sw2026_adapter(fillet3_return=1)

    result = features._add_fillet_impl(adapter, 2.0, ["Edge<bad>"])

    assert result.status == AdapterResultStatus.ERROR
    assert "Failed to select edge" in (result.error or "")


def test_add_fillet_sw33_also_uses_new_path() -> None:
    """major=33 (SW 2025) hits the >= 33 branch; name defaults to 'Fillet'
    because IModelDoc2.FeatureFillet3 returns int, not IFeature."""
    adapter = _FilletAdapterSW2026()
    adapter.swApp = SimpleNamespace(RevisionNumber="33.2.1")
    adapter.currentModel = SimpleNamespace(
        FeatureManager=SimpleNamespace(
            FeatureFillet3=lambda *args: None,
        ),
        Extension=SimpleNamespace(SelectByID2=lambda *_args: True),
        FeatureFillet3=lambda *args: 1,
    )

    result = features._add_fillet_impl(adapter, 1.5, ["Edge<1>"])

    assert result.is_success
    assert result.data.name == "Fillet"
