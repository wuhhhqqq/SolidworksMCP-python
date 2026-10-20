"""Tests for SolidWorks IO mixin behaviors."""

from __future__ import annotations

import os
import base64
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from solidworks_mcp.adapters.base import (
    ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED,
    AdapterResult,
    AdapterResultStatus,
)
from solidworks_mcp.adapters.solidworks import io as _io_module
from solidworks_mcp.adapters.solidworks.io import (
    SolidWorksIOMixin,
    _get_sw_comtypes_lib,
)


class _IOHarness(SolidWorksIOMixin):
    """Minimal harness for IO mixin."""

    def __init__(self, current_model) -> None:
        self.currentModel = current_model

    def _attempt(self, callback, default=None):
        try:
            return callback()
        except Exception:
            return default

    def _handle_com_operation(self, _name, callback, *args):
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))


class _ActivationHarness(_IOHarness):
    """IO harness that supplies the dependencies required by activate_model."""

    def __init__(self, current_model, app) -> None:
        super().__init__(current_model)
        self.swApp = app
        self._document_routing = SimpleNamespace(
            document_identity=lambda document: (
                document.GetPathName(),
                document.GetTitle(),
            )
        )

    def is_connected(self) -> bool:
        return True


class _CreationHarness(_IOHarness):
    """IO harness for new-document tests."""

    def __init__(self, app) -> None:
        super().__init__(None)
        self.swApp = app
        self.tracked_documents = []

    def is_connected(self) -> bool:
        return True

    def _get_attr_or_call(self, obj, attr_name):
        value = getattr(obj, attr_name)
        return value() if callable(value) else value

    def _track_session_document(self, document) -> None:
        self.tracked_documents.append(document)


import pytest


@pytest.mark.asyncio
async def test_create_part_uses_verified_active_document_when_new_part_returns_none() -> None:
    """Legacy COM may create a part while returning a null IDispatch value."""
    model = SimpleNamespace(GetTitle=Mock(return_value="Part1"), GetType=Mock(return_value=1))
    previous_model = SimpleNamespace(
        GetTitle=Mock(return_value="ExistingPart"), GetType=Mock(return_value=1)
    )
    app = SimpleNamespace(ActiveDoc=previous_model)

    def create_part_without_dispatch():
        app.ActiveDoc = model
        return None

    app.NewPart = Mock(side_effect=create_part_without_dispatch)
    harness = _CreationHarness(app)

    result = await harness.create_part(name="IgnoredBySolidWorks")

    assert result.is_success
    assert result.data.name == "Part1"
    assert harness.currentModel is model
    assert harness.tracked_documents == [model]
    app.NewPart.assert_called_once_with()


@pytest.mark.asyncio
async def test_create_part_uses_document_delta_before_template_fallback() -> None:
    """A newly opened part can be identified even if ActiveDoc stays stale."""
    previous_model = SimpleNamespace(
        GetTitle=Mock(return_value="ExistingPart"), GetType=Mock(return_value=1)
    )
    new_model = SimpleNamespace(
        GetTitle=Mock(return_value="Part2"), GetType=Mock(return_value=1)
    )
    documents = [previous_model]
    app = SimpleNamespace(
        ActiveDoc=previous_model,
        GetDocuments=Mock(side_effect=lambda: tuple(documents)),
    )

    def create_part_without_dispatch():
        documents.append(new_model)
        return None

    app.NewPart = Mock(side_effect=create_part_without_dispatch)
    app.NewDocument = Mock(side_effect=AssertionError("must not create a duplicate"))
    harness = _CreationHarness(app)

    result = await harness.create_part()

    assert result.is_success
    assert result.data.name == "Part2"
    assert harness.currentModel is new_model
    app.NewDocument.assert_not_called()


@pytest.mark.asyncio
async def test_create_part_treats_null_document_list_as_empty() -> None:
    """A null SAFEARRAY means no open documents, not unsupported enumeration."""
    new_model = SimpleNamespace(
        GetTitle=Mock(return_value="Part1"), GetType=Mock(return_value=1)
    )
    documents = []
    app = SimpleNamespace(
        ActiveDoc=None,
        GetDocuments=Mock(side_effect=lambda: tuple(documents) if documents else None),
    )

    def create_part_without_dispatch():
        documents.append(new_model)
        return None

    app.NewPart = Mock(side_effect=create_part_without_dispatch)
    app.NewDocument = Mock(side_effect=AssertionError("must not create a duplicate"))
    harness = _CreationHarness(app)

    result = await harness.create_part()

    assert result.is_success
    assert result.data.name == "Part1"
    assert harness.currentModel is new_model
    assert harness.tracked_documents == [new_model]
    app.NewDocument.assert_not_called()


@pytest.mark.asyncio
async def test_create_part_fails_safely_when_null_new_part_cannot_be_identified(
    monkeypatch,
) -> None:
    """Do not risk a duplicate when NewPart may have created an unseen document."""
    previous_model = SimpleNamespace(
        GetTitle=Mock(return_value="ExistingPart"), GetType=Mock(return_value=1)
    )
    app = SimpleNamespace(
        ActiveDoc=previous_model,
        GetDocuments=Mock(return_value=(previous_model,)),
        NewPart=Mock(return_value=None),
        NewDocument=Mock(),
    )
    monkeypatch.setattr(
        SolidWorksIOMixin,
        "_resolve_template_path",
        Mock(side_effect=AssertionError("must not create a duplicate")),
    )
    harness = _CreationHarness(app)

    result = await harness.create_part()

    assert not result.is_success
    assert "refusing to create a duplicate" in result.error
    app.NewDocument.assert_not_called()


@pytest.mark.asyncio
async def test_create_part_preserves_localized_active_configuration_name() -> None:
    """Created documents must not assume the English ``Default`` name."""
    model = SimpleNamespace(
        GetTitle=Mock(return_value="Part1"),
        GetType=Mock(return_value=1),
        GetActiveConfiguration=Mock(
            return_value=SimpleNamespace(GetName=Mock(return_value="默认"))
        ),
    )
    app = SimpleNamespace(NewPart=Mock(return_value=model), ActiveDoc=None)
    harness = _CreationHarness(app)

    result = await harness.create_part(name="LocalizedPart")

    assert result.is_success
    assert result.data.configuration == "默认"


@pytest.mark.asyncio
async def test_list_dimensions_flags_model_and_returns_safe_rows(
    monkeypatch,
) -> None:
    """Driving dimensions are read from a standard feature traversal."""
    dimension = SimpleNamespace(
        GetNameForSelection=Mock(return_value="D1@Boss-Extrude1"),
        SystemValue=0.01,
    )
    display_dimension = SimpleNamespace(
        GetDimension=Mock(return_value=dimension),
    )
    feature = SimpleNamespace(
        GetFirstDisplayDimension=Mock(return_value=display_dimension),
        GetNextDisplayDimension=Mock(return_value=None),
        GetNextFeature=Mock(return_value=None),
    )
    model = SimpleNamespace(FirstFeature=Mock(return_value=feature))
    flag_methods = Mock()
    monkeypatch.setattr(_io_module._sw_type_info, "flag_methods", flag_methods)
    harness = _IOHarness(model)

    result = await harness.list_dimensions()

    assert result.is_success
    assert result.data == [{"name": "D1@Boss-Extrude1", "value_mm": 10.0}]
    model.FirstFeature.assert_called_once_with()
    flag_methods.assert_any_call(model, "IModelDoc2")


@pytest.mark.asyncio
async def test_list_dimensions_falls_back_to_reverse_feature_positions() -> None:
    """The list remains usable when dynamic COM cannot resolve FirstFeature."""
    dimension = SimpleNamespace(
        GetNameForSelection=Mock(return_value="D1@Boss-Extrude1"),
        SystemValue=0.01,
    )
    display_dimension = SimpleNamespace(
        GetDimension=Mock(return_value=dimension),
    )
    feature = SimpleNamespace(
        GetFirstDisplayDimension=Mock(return_value=display_dimension),
        GetNextDisplayDimension=Mock(return_value=None),
    )
    model = SimpleNamespace(
        FirstFeature=Mock(side_effect=RuntimeError("not available")),
        FeatureByPositionReverse=Mock(side_effect=[feature, None]),
    )
    harness = _IOHarness(model)

    result = await harness.list_dimensions()

    assert result.is_success
    assert result.data == [{"name": "D1@Boss-Extrude1", "value_mm": 10.0}]
    model.FeatureByPositionReverse.assert_has_calls([call(0), call(1)])


@pytest.mark.asyncio
async def test_list_dimensions_reads_feature_parameters_without_display_dimensions() -> (
    None
):
    """Saved-model dimensions are discovered even when no UI dimensions exist."""
    dimension = SimpleNamespace(
        GetNameForSelection="D1@Extrusion1",
        SystemValue=0.01,
    )
    feature = SimpleNamespace(
        GetTypeName2="Extrusion",
        Parameter=lambda name: dimension if name == "D1" else None,
    )
    model = SimpleNamespace(
        FirstFeature=Mock(side_effect=RuntimeError("not available")),
        FeatureByPositionReverse=Mock(side_effect=[feature, None]),
    )
    harness = _IOHarness(model)

    result = await harness.list_dimensions()

    assert result.is_success
    assert result.data == [{"name": "D1@Extrusion1", "value_mm": 10.0}]


@pytest.mark.asyncio
async def test_get_mass_properties_uses_callable_gmp() -> None:
    """Callable GetMassProperties should be used when CreateMassProperty is missing."""
    # Provide a callable GetMassProperties to cover the callable branch.
    current_model = SimpleNamespace(
        ForceRebuild3=lambda _flag: None,
        Extension=SimpleNamespace(CreateMassProperty=lambda: None),
        GetMassProperties=lambda: [1.0, 2.0, 3.0, 0.1, 0.2, 0.3],
    )
    harness = _IOHarness(current_model)
    result = await harness.get_mass_properties()
    assert result.is_success
    assert result.data.mass == 0.3


@pytest.mark.asyncio
async def test_get_mass_properties_missing_gmp_fails() -> None:
    """Missing GetMassProperties should surface a failure."""
    # Use a non-callable/non-list attribute to hit raw=None.
    current_model = SimpleNamespace(
        ForceRebuild3=lambda _flag: None,
        Extension=SimpleNamespace(CreateMassProperty=lambda: None),
        GetMassProperties=123,
    )
    harness = _IOHarness(current_model)
    result = await harness.get_mass_properties()
    assert result.status == AdapterResultStatus.ERROR
    assert "Failed to get mass properties" in (result.error or "")


@pytest.mark.asyncio
async def test_get_bounding_box_converts_part_box_metres_to_millimetres() -> None:
    """The part bounding box is returned as a typed millimetre-valued summary."""
    current_model = SimpleNamespace(
        GetPartBox=Mock(return_value=[-0.01, -0.005, 0.0, 0.09, 0.045, 0.025])
    )
    harness = _IOHarness(current_model)

    result = await harness.get_bounding_box()

    assert result.is_success
    assert result.data == {
        "min_point_mm": [-10.0, -5.0, 0.0],
        "max_point_mm": [90.0, 45.0, 25.0],
        "size_mm": [100.0, 50.0, 25.0],
    }
    current_model.GetPartBox.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_equation_operations_read_back_created_updated_and_deleted_rows() -> None:
    """Equation writes use the manager API and verify their post-operation state."""
    equations = ['"PlateWidth" = 100mm']

    def get_equation(index: int) -> str:
        return equations[index]

    def add_equation(_index: int, equation: str, _solve: bool) -> int:
        equations.append(equation)
        return len(equations) - 1

    def set_equation(index: int, equation: str, _scope: int, _names) -> int:
        equations[index] = equation
        return index

    def delete_equation(index: int) -> int:
        del equations[index]
        return 0

    manager = SimpleNamespace(
        GetCount=lambda: len(equations),
        Equation=get_equation,
        GlobalVariable=lambda index: "@" not in equations[index].split('"')[1],
        Add2=Mock(side_effect=add_equation),
        Delete=Mock(side_effect=delete_equation),
        SetEquationAndConfigurationOption=Mock(side_effect=set_equation),
    )
    current_model = SimpleNamespace(GetEquationMgr=lambda: manager)
    harness = _IOHarness(current_model)

    listed = await harness.list_equations()
    updated = await harness.set_equation("PlateWidth", "120mm")
    created = await harness.set_equation("Clearance", "0.25mm")
    deleted = await harness.delete_equation("Clearance")

    assert listed.is_success
    assert listed.data == [
        {
            "index": 0,
            "name": "PlateWidth",
            "expression": "100mm",
            "is_global_variable": True,
        }
    ]
    assert updated.is_success
    assert updated.data["expression"] == "120mm"
    assert created.is_success
    assert created.data == {
        "index": 1,
        "name": "Clearance",
        "expression": "0.25mm",
        "is_global_variable": True,
    }
    assert deleted.is_success
    assert deleted.data["name"] == "Clearance"
    manager.SetEquationAndConfigurationOption.assert_not_called()
    manager.Add2.assert_has_calls(
        [
            call(-1, '"PlateWidth" = 120mm', True),
            call(-1, '"Clearance" = 0.25mm', True),
        ]
    )
    manager.Delete.assert_has_calls([call(0), call(1)])


@pytest.mark.asyncio
async def test_set_equation_replaces_existing_row_with_delete_and_add2() -> None:
    """Existing equations update through the supported Delete/Add2 path."""
    equations = ['"PlateWidth" = 100mm']

    def add_equation(_index: int, equation: str, _solve: bool) -> int:
        equations.append(equation)
        return len(equations) - 1

    def delete_equation(index: int) -> int:
        del equations[index]
        return 0

    manager = SimpleNamespace(
        GetCount=lambda: len(equations),
        Equation=lambda index: equations[index],
        GlobalVariable=lambda _index: True,
        SetEquationAndConfigurationOption=Mock(return_value=1),
        Delete=Mock(side_effect=delete_equation),
        Add2=Mock(side_effect=add_equation),
    )
    current_model = SimpleNamespace(GetEquationMgr=lambda: manager)
    harness = _IOHarness(current_model)

    result = await harness.set_equation("PlateWidth", "120mm")

    assert result.is_success
    assert result.data["expression"] == "120mm"
    assert equations == ['"PlateWidth" = 120mm']
    manager.SetEquationAndConfigurationOption.assert_not_called()
    manager.Delete.assert_called_once_with(0)
    manager.Add2.assert_called_once_with(-1, '"PlateWidth" = 120mm', True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "rejected", "wrong_readback"])
async def test_failed_equation_update_restores_original_row(failure: str) -> None:
    equations = ['"PlateWidth" = 100mm', '"Height" = 50mm']

    def add_equation(index, equation, _solve):
        if equation == '"PlateWidth" = invalid':
            if failure == "exception":
                raise RuntimeError("COM failure")
            if failure == "rejected":
                return -1
            equations.append('"PlateWidth" = 0mm')
            return len(equations) - 1
        equations.insert(index, equation)
        return index

    manager = SimpleNamespace(
        GetCount=lambda: len(equations),
        Equation=lambda index: equations[index],
        GlobalVariable=lambda _index: True,
        Delete=lambda index: equations.pop(index),
        Add2=add_equation,
    )
    harness = _IOHarness(SimpleNamespace(GetEquationMgr=lambda: manager))

    result = await harness.set_equation("PlateWidth", "invalid")

    assert not result.is_success
    assert equations == ['"PlateWidth" = 100mm', '"Height" = 50mm']


@pytest.mark.asyncio
async def test_equation_manager_accepts_callable_dispatch_returned_by_getattr() -> None:
    """A callable COM manager property must not be called as a getter method."""

    class CallableManager(SimpleNamespace):
        def __call__(self):
            raise AssertionError("manager default member called")

    manager = CallableManager(GetCount=Mock(return_value=0), Equation=Mock())
    model = SimpleNamespace(GetEquationMgr=manager)
    harness = _IOHarness(model)

    result = await harness.list_equations()

    assert result.is_success
    assert result.data == []
    manager.GetCount.assert_called_once_with()


@pytest.mark.asyncio
async def test_activate_model_uses_activate_doc3_and_tracks_activated_document() -> (
    None
):
    """Stateful workflows must switch the COM active document by trusted identity."""
    activated_document = SimpleNamespace(
        GetPathName=lambda: r"C:\models\target.sldprt",
        GetTitle=lambda: "target.sldprt",
    )
    app = SimpleNamespace(
        ActivateDoc3=Mock(return_value=activated_document),
        ActiveDoc=None,
    )
    harness = _ActivationHarness(current_model=None, app=app)

    result = await harness.activate_model("target.sldprt", r"C:\models\target.sldprt")

    assert result.is_success
    assert harness.currentModel is activated_document
    app.ActivateDoc3.assert_called_once()


@pytest.mark.asyncio
async def test_activate_model_does_not_fall_back_to_title_when_path_is_tracked() -> (
    None
):
    """A same-titled document without the trusted path must not be activated."""
    wrong_document = SimpleNamespace(
        GetPathName=lambda: "",
        GetTitle=lambda: "target.sldprt",
    )
    app = SimpleNamespace(
        ActivateDoc3=Mock(return_value=wrong_document),
        ActiveDoc=None,
    )
    harness = _ActivationHarness(current_model=None, app=app)

    result = await harness.activate_model("target.sldprt", r"C:\models\target.sldprt")

    assert result.status is AdapterResultStatus.ERROR
    assert harness.currentModel is None


@pytest.mark.asyncio
async def test_activate_configuration_verifies_the_requested_name() -> None:
    """A configuration switch only succeeds after SolidWorks reports the same name."""
    active_configuration = SimpleNamespace(Name="Ä¬ÈÏ")
    current_model = SimpleNamespace(
        ShowConfiguration2=Mock(return_value=True),
        ConfigurationManager=SimpleNamespace(ActiveConfiguration=active_configuration),
    )
    harness = _IOHarness(current_model)

    result = await harness.activate_configuration("\u9ed8\u8ba4")

    assert result.is_success
    assert result.data == "\u9ed8\u8ba4"
    current_model.ShowConfiguration2.assert_called_once_with("\u9ed8\u8ba4")


@pytest.mark.asyncio
async def test_activate_configuration_accepts_a_no_change_com_result() -> None:
    """False is valid when SolidWorks reports the requested config already active."""
    active_configuration = SimpleNamespace(Name="Default")
    current_model = SimpleNamespace(
        ShowConfiguration2=Mock(return_value=False),
        ConfigurationManager=SimpleNamespace(ActiveConfiguration=active_configuration),
    )
    harness = _IOHarness(current_model)

    result = await harness.activate_configuration("Default")

    assert result.is_success
    assert result.data == "Default"


@pytest.mark.asyncio
async def test_create_configuration_uses_add_configuration3_and_verifies_activation() -> (
    None
):
    """Creating a configuration succeeds only after SolidWorks activates it."""
    active_configuration = SimpleNamespace(Name="Fabrication")
    current_model = SimpleNamespace(
        GetConfigurationNames=Mock(
            side_effect=[["Default"], ["Default", "Fabrication"]]
        ),
        AddConfiguration3=Mock(return_value=SimpleNamespace(Name="Fabrication")),
        ShowConfiguration2=Mock(return_value=True),
        ConfigurationManager=SimpleNamespace(ActiveConfiguration=active_configuration),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_configuration("Fabrication", "Welded construction")

    assert result.is_success
    assert result.data == "Fabrication"
    current_model.AddConfiguration3.assert_called_once_with(
        "Fabrication", "Welded construction", "", 0, "", ""
    )
    current_model.ShowConfiguration2.assert_called_once_with("Fabrication")


@pytest.mark.asyncio
async def test_create_configuration_rejects_existing_name_without_com_write() -> None:
    """Duplicate configuration names do not reach the mutating COM API."""
    current_model = SimpleNamespace(
        GetConfigurationNames=Mock(return_value=["Default"]),
        AddConfiguration3=Mock(),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_configuration("default")

    assert result.status is AdapterResultStatus.ERROR
    assert "already exists" in (result.error or "")
    current_model.AddConfiguration3.assert_not_called()


@pytest.mark.asyncio
async def test_delete_configuration_rejects_active_and_verifies_removal() -> None:
    """Configuration deletion never deletes the active config and reads back state."""
    active_configuration = SimpleNamespace(Name="Default")
    current_model = SimpleNamespace(
        GetConfigurationNames=Mock(
            side_effect=[["Default", "Fabrication"], ["Default"]]
        ),
        DeleteConfiguration2=Mock(return_value=True),
        ConfigurationManager=SimpleNamespace(ActiveConfiguration=active_configuration),
    )
    harness = _IOHarness(current_model)

    result = await harness.delete_configuration("Fabrication")

    assert result.is_success
    assert result.data == "Fabrication"
    current_model.DeleteConfiguration2.assert_called_once_with("Fabrication")


@pytest.mark.asyncio
async def test_list_configurations_repairs_chinese_com_mojibake() -> None:
    """Configuration discovery returns usable Unicode names to MCP clients."""
    current_model = SimpleNamespace(GetConfigurationNames=lambda: ["Ä¬ÈÏ"])
    harness = _IOHarness(current_model)

    result = await harness.list_configurations()

    assert result.is_success
    assert result.data == ["\u9ed8\u8ba4"]


@pytest.mark.asyncio
async def test_get_configuration_reads_name_comment_and_active_state() -> None:
    """Configuration detail lookup does not require activation or selection."""
    default = SimpleNamespace(Name="Default", Comment="Fabrication setup")
    current_model = SimpleNamespace(
        GetConfigurationByName=Mock(return_value=default),
        GetActiveConfiguration=Mock(return_value=default),
    )
    harness = _IOHarness(current_model)

    result = await harness.get_configuration("Default")

    assert result.is_success
    assert result.data == {
        "name": "Default",
        "description": "Fabrication setup",
        "is_active": True,
    }
    current_model.GetConfigurationByName.assert_called_once_with("Default")


@pytest.mark.asyncio
async def test_insert_assembly_component_uses_add_component5_in_system_units() -> None:
    """Assembly insertion delegates to AddComponent5 and returns no source path."""
    component = SimpleNamespace(
        GetID=lambda: 42,
        Name2="FixturePart-1",
        GetModelDoc2=lambda: SimpleNamespace(GetType=lambda: 1),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        AddComponent5=Mock(return_value=component),
    )
    harness = _IOHarness(current_model)

    result = await harness.insert_assembly_component(
        r"C:\controlled\fixture-part.sldprt", 0.01, 0.02, 0.03
    )

    assert result.is_success
    assert result.data == {
        "component_id": "42",
        "display_name": "FixturePart-1",
        "document_type": "part",
    }
    current_model.AddComponent5.assert_called_once_with(
        r"C:\controlled\fixture-part.sldprt", 0, "", False, "", 0.01, 0.02, 0.03
    )


@pytest.mark.asyncio
async def test_list_assembly_components_uses_get_components_and_safe_summaries() -> (
    None
):
    """Assembly listing never serializes component COM objects or model paths."""
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetModelDoc2=lambda: SimpleNamespace(GetType=lambda: 1),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
    )
    harness = _IOHarness(current_model)

    result = await harness.list_assembly_components()

    assert result.is_success
    assert result.data == [
        {
            "component_id": "9",
            "display_name": "FixturePart-1",
            "document_type": "part",
        }
    ]
    current_model.GetComponents.assert_called_once_with(False)


@pytest.mark.asyncio
async def test_list_assembly_components_uses_recorded_source_type_as_fallback() -> None:
    """A component inserted by this adapter stays typed without generated COM wrappers."""
    component = SimpleNamespace(
        GetID=lambda: 10,
        Name2="FixturePart-1",
        GetModelDoc2=lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
    )
    harness = _IOHarness(current_model)
    harness._assembly_component_types = {"10": "part"}

    result = await harness.list_assembly_components()

    assert result.is_success
    assert result.data[0]["document_type"] == "part"


@pytest.mark.asyncio
async def test_get_assembly_component_uses_name_lookup_without_selection() -> None:
    """Exact component lookup returns only the existing safe summary."""
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetModelDoc2=lambda: SimpleNamespace(GetType=lambda: 1),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponentByName=Mock(return_value=component),
    )
    harness = _IOHarness(current_model)

    result = await harness.get_assembly_component("FixturePart-1")

    assert result.is_success
    assert result.data == {
        "component_id": "9",
        "display_name": "FixturePart-1",
        "document_type": "part",
    }
    current_model.GetComponentByName.assert_called_once_with("FixturePart-1")


@pytest.mark.asyncio
async def test_get_assembly_component_falls_back_to_component_enumeration() -> None:
    """Late-bound clients without GetComponentByName still support lookup."""
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetModelDoc2=lambda: SimpleNamespace(GetType=lambda: 1),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
    )
    harness = _IOHarness(current_model)

    result = await harness.get_assembly_component("FixturePart-1")

    assert result.is_success
    assert result.data["component_id"] == "9"
    current_model.GetComponents.assert_called_once_with(False)


@pytest.mark.asyncio
async def test_check_assembly_interference_returns_native_component_names_and_volume() -> (
    None
):
    """Interference detection uses the manager API and restores its user settings."""
    manager = SimpleNamespace(
        TreatCoincidenceAsInterference=False,
        IncludeMultibodyPartInterferences=False,
        GetInterferences=Mock(
            return_value=[
                SimpleNamespace(
                    Components=[
                        SimpleNamespace(Name2="Bracket-1"),
                        SimpleNamespace(Name2="Cover-1"),
                    ],
                    Volume=0.0000000125,
                    IsPossibleInterference=False,
                )
            ]
        ),
        Done=Mock(),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        InterferenceDetectionManager=manager,
    )
    harness = _IOHarness(current_model)

    result = await harness.check_assembly_interference(True, True)

    assert result.is_success
    assert result.data == [
        {
            "component_names": ["Bracket-1", "Cover-1"],
            "volume_mm3": 12.5,
            "is_possible": False,
        }
    ]
    assert manager.TreatCoincidenceAsInterference is False
    assert manager.IncludeMultibodyPartInterferences is False
    manager.GetInterferences.assert_called_once_with()
    manager.Done.assert_called_once_with()


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_uses_persist_references_not_selection_names() -> (
    None
):
    """Mate candidates expose server-only persistent references for faces and edges."""
    second_face = SimpleNamespace(GetNextFace=Mock(return_value=None))
    first_face = SimpleNamespace(GetNextFace=Mock(return_value=second_face))
    second_edge = SimpleNamespace(GetNext=Mock(return_value=None))
    first_edge = SimpleNamespace(GetNext=Mock(return_value=second_edge))
    body = SimpleNamespace(
        GetFirstFace=Mock(return_value=first_face),
        GetFirstEdge=Mock(return_value=first_edge),
    )
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetBodies3=Mock(return_value=[body]),
    )
    extension = SimpleNamespace(
        GetPersistReference3=Mock(side_effect=[b"\x01", b"\x02", b"\x03", b"\x04"])
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
        Extension=extension,
    )
    harness = _IOHarness(current_model)

    result = await harness.list_assembly_mate_entities()

    assert result.is_success
    assert result.data == [
        {
            "component_id": "9",
            "entity_type": "face",
            "display_name": "FixturePart-1 Face 1",
            "persistent_reference": "AQ==",
        },
        {
            "component_id": "9",
            "entity_type": "face",
            "display_name": "FixturePart-1 Face 2",
            "persistent_reference": "Ag==",
        },
        {
            "component_id": "9",
            "entity_type": "edge",
            "display_name": "FixturePart-1 Edge 1",
            "persistent_reference": "Aw==",
        },
        {
            "component_id": "9",
            "entity_type": "edge",
            "display_name": "FixturePart-1 Edge 2",
            "persistent_reference": "BA==",
        },
    ]
    get_bodies_arguments = component.GetBodies3.call_args.args
    assert get_bodies_arguments[0] == 0
    assert len(get_bodies_arguments) == 2
    assert not hasattr(current_model, "SelectByID2")


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_falls_back_to_component_locator() -> None:
    """Assembly topology stays actionable when SW returns no persistent edge ID."""
    edge = SimpleNamespace(GetNext=lambda: None)
    body = SimpleNamespace(GetFirstEdge=lambda: edge)
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetBodies3=Mock(return_value=[body]),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
        Extension=SimpleNamespace(GetPersistReference3=lambda _entity: None),
    )
    harness = _IOHarness(current_model)

    result = await harness.list_assembly_mate_entities(entity_types=("edge",))

    assert result.is_success
    token = result.data[0]["persistent_reference"]
    assert token.startswith("assembly_entity:")
    assert harness._decode_assembly_entity_locator(token) == ("9", "edge", 1)


@pytest.mark.asyncio
async def test_create_assembly_mate_resolves_persist_references_and_calls_add_mate5() -> (
    None
):
    """Mate creation selects resolved geometry and sends the complete AddMate5 contract."""
    first_entity = SimpleNamespace(Select4=Mock(return_value=True))
    second_entity = SimpleNamespace(Select4=Mock(return_value=True))
    mate_feature = SimpleNamespace(Name="Concentric1")
    extension = SimpleNamespace(
        GetObjectByPersistReference3=Mock(
            side_effect=[(first_entity, 0), (second_entity, 0)]
        )
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        Extension=extension,
        ClearSelection2=Mock(return_value=True),
        AddMate5=Mock(return_value=(mate_feature, 0)),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_assembly_mate(
        "AQ==",
        "Ag==",
        mate_type="concentric",
        alignment="aligned",
        flip=False,
        distance_m=0.0,
        angle_radians=0.0,
        for_positioning_only=False,
        lock_rotation=True,
    )

    assert result.is_success
    assert result.data == {
        "mate_type": "concentric",
        "display_name": "Concentric1",
    }
    extension.GetObjectByPersistReference3.assert_has_calls(
        [call(b"\x01"), call(b"\x02")]
    )
    first_entity.Select4.assert_called_once_with(False, None)
    second_entity.Select4.assert_called_once_with(True, None)
    add_mate_arguments = current_model.AddMate5.call_args.args
    assert add_mate_arguments[:-1] == (
        1,
        0,
        False,
        0.0,
        0.0,
        0.0,
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,
        False,
        True,
        0,
    )
    assert add_mate_arguments[-1].value == 0


@pytest.mark.asyncio
async def test_create_assembly_mate_resolves_component_locator_without_persist_reference() -> (
    None
):
    """The assembly-local locator restores an exact component edge on demand."""
    first_entity = SimpleNamespace(Select4=Mock(return_value=True), GetNext=lambda: None)
    second_entity = SimpleNamespace(Select4=Mock(return_value=True), GetNext=lambda: None)
    first_component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetBodies3=Mock(return_value=[SimpleNamespace(GetFirstEdge=lambda: first_entity)]),
    )
    second_component = SimpleNamespace(
        GetID=lambda: 10,
        Name2="FixturePart-2",
        GetBodies3=Mock(return_value=[SimpleNamespace(GetFirstEdge=lambda: second_entity)]),
    )
    mate_feature = SimpleNamespace(Name="Concentric1")
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[first_component, second_component]),
        Extension=SimpleNamespace(),
        ClearSelection2=Mock(return_value=True),
        AddMate5=Mock(return_value=(mate_feature, 0)),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_assembly_mate(
        harness._assembly_entity_locator("9", "edge", 1),
        harness._assembly_entity_locator("10", "edge", 1),
        mate_type="concentric",
        alignment="aligned",
        flip=False,
        distance_m=0.0,
        angle_radians=0.0,
        for_positioning_only=False,
        lock_rotation=True,
    )

    assert result.is_success
    first_entity.Select4.assert_called_once_with(False, None)
    second_entity.Select4.assert_called_once_with(True, None)
    current_model.AddMate5.assert_called_once()


@pytest.mark.asyncio
async def test_create_assembly_mate_uses_vtable_after_dispatch_type_mismatch(
    monkeypatch,
) -> None:
    """Legacy IDispatch falls back to IAssemblyDoc for AddMate5 output values."""
    first_entity = SimpleNamespace(Select4=Mock(return_value=True))
    second_entity = SimpleNamespace(Select4=Mock(return_value=True))
    mate_feature = SimpleNamespace(Name="Concentric1")
    extension = SimpleNamespace(
        GetObjectByPersistReference3=Mock(
            side_effect=[(first_entity, 0), (second_entity, 0)]
        )
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        Extension=extension,
        ClearSelection2=Mock(return_value=True),
        AddMate5=Mock(side_effect=RuntimeError(-2147352571, "type mismatch")),
    )
    assembly_interface = object()
    vtable_add_mate = Mock(return_value=(0, mate_feature))
    bridge = Mock(return_value=SimpleNamespace(AddMate5=vtable_add_mate))
    monkeypatch.setattr(
        _io_module,
        "_get_sw_comtypes_lib",
        lambda: SimpleNamespace(IAssemblyDoc=assembly_interface),
    )
    monkeypatch.setattr(_io_module, "_bridge_com_to_comtypes", bridge)
    harness = _IOHarness(current_model)

    result = await harness.create_assembly_mate(
        "AQ==",
        "Ag==",
        mate_type="concentric",
        alignment="aligned",
        flip=False,
        distance_m=0.0,
        angle_radians=0.0,
        for_positioning_only=False,
        lock_rotation=True,
    )

    assert result.is_success
    bridge.assert_called_once_with(current_model, assembly_interface)
    assert len(vtable_add_mate.call_args.args) == 14


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mate_type", "expected_type"),
    [("perpendicular", 2), ("parallel", 3)],
)
async def test_create_assembly_mate_uses_solidworks_parallel_and_perpendicular_enums(
    mate_type: str, expected_type: int
) -> None:
    """Parallel and perpendicular must use the values from the SolidWorks TLB."""
    first_entity = SimpleNamespace(Select4=Mock(return_value=True))
    second_entity = SimpleNamespace(Select4=Mock(return_value=True))
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        Extension=SimpleNamespace(
            GetObjectByPersistReference3=Mock(
                side_effect=[(first_entity, 0), (second_entity, 0)]
            )
        ),
        ClearSelection2=Mock(return_value=True),
        AddMate5=Mock(return_value=(SimpleNamespace(Name="Mate1"), 0)),
    )

    result = await _IOHarness(current_model).create_assembly_mate(
        "AQ==",
        "Ag==",
        mate_type=mate_type,
        alignment="aligned",
        flip=False,
        distance_m=0.0,
        angle_radians=0.0,
        for_positioning_only=False,
        lock_rotation=False,
    )

    assert result.is_success
    assert current_model.AddMate5.call_args.args[0] == expected_type


@pytest.mark.asyncio
async def test_create_drawing_view_uses_standard_view_api_in_system_units() -> None:
    """Drawing view creation sends the trusted model and sheet coordinates to COM."""
    view = SimpleNamespace(Name="Drawing View1")
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        CreateDrawViewFromModelView3=Mock(return_value=view),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_drawing_view(
        r"C:\controlled\fixture.sldprt", "*Front", 0.1, 0.2
    )

    assert result.is_success
    assert result.data == {"view_id": "Drawing View1", "display_name": "Drawing View1"}
    current_model.CreateDrawViewFromModelView3.assert_called_once_with(
        r"C:\controlled\fixture.sldprt", "*Front", 0.1, 0.2, 0.0
    )


@pytest.mark.asyncio
async def test_create_drawing_view_retries_with_localized_source_view_name() -> None:
    """Localized installations use the standard-view name reported by the model."""
    source_model = SimpleNamespace(
        GetPathName=r"C:\controlled\fixture.sldprt",
        GetModelViewNames=(
            "*Normal To",
            "*Front",
            "*Back",
            "*Left",
            "*Right",
            "*Top",
            "*Bottom",
            "*\u7b49\u8f74\u6d4b",
            "*Top Dimetric",
            "*Right Dimetric",
        ),
    )
    view = SimpleNamespace(Name="Drawing View1")
    create_view = Mock(side_effect=[None, view])
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        CreateDrawViewFromModelView3=create_view,
    )
    harness = _IOHarness(current_model)
    harness.swApp = SimpleNamespace(GetDocuments=(source_model,))

    result = await harness.create_drawing_view(
        r"C:\controlled\fixture.sldprt", "*Isometric", 0.1, 0.2
    )

    assert result.is_success
    assert result.data == {"view_id": "Drawing View1", "display_name": "Drawing View1"}
    assert create_view.call_args_list == [
        call(r"C:\controlled\fixture.sldprt", "*Isometric", 0.1, 0.2, 0.0),
        call(r"C:\controlled\fixture.sldprt", "*\u7b49\u8f74\u6d4b", 0.1, 0.2, 0.0),
    ]


@pytest.mark.asyncio
async def test_create_drawing_view_uses_english_name_when_localization_probe_fails() -> None:
    """A failed localization probe cannot block the documented English call."""
    view = SimpleNamespace(Name="Drawing View1")
    create_view = Mock(return_value=view)
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        CreateDrawViewFromModelView3=create_view,
    )
    harness = _IOHarness(current_model)
    harness.swApp = SimpleNamespace(GetDocuments=Mock(side_effect=RuntimeError("COM")))

    result = await harness.create_drawing_view(
        r"C:\controlled\fixture.sldprt", "*Front", 0.1, 0.2
    )

    assert result.is_success
    create_view.assert_called_once_with(
        r"C:\controlled\fixture.sldprt", "*Front", 0.1, 0.2, 0.0
    )


@pytest.mark.asyncio
async def test_open_model_does_not_track_a_document_that_was_already_open() -> None:
    """Opening an existing user document must not make it eligible for cleanup."""
    target = os.path.abspath(r"C:\controlled\fixture.sldprt")
    model = SimpleNamespace(
        GetTitle="fixture.sldprt",
        GetPathName=target,
        GetActiveConfiguration=lambda: None,
    )
    app = SimpleNamespace(
        GetDocuments=(model,),
        OpenDoc6=Mock(return_value=model),
    )
    harness = _IOHarness(None)
    harness.swApp = app
    harness.constants = {"swDocPART": 1}
    harness.is_connected = lambda: True
    harness._get_attr_or_call = lambda obj, name: getattr(obj, name)
    tracked: list[object] = []
    harness._track_session_document = tracked.append

    result = await harness.open_model(target)

    assert result.is_success
    assert tracked == []
    app.OpenDoc6.assert_not_called()


@pytest.mark.asyncio
async def test_open_model_opens_when_matching_document_is_not_already_open() -> None:
    """A missing path still uses OpenDoc6 and tracks the new document."""
    target = os.path.abspath(r"C:\controlled\new_fixture.sldprt")
    existing = SimpleNamespace(
        GetTitle="other.sldprt",
        GetPathName=os.path.abspath(r"C:\controlled\other.sldprt"),
    )
    model = SimpleNamespace(
        GetTitle="new_fixture.sldprt",
        GetPathName=target,
        GetActiveConfiguration=lambda: None,
    )
    app = SimpleNamespace(
        GetDocuments=(existing,),
        OpenDoc6=Mock(return_value=model),
    )
    harness = _IOHarness(None)
    harness.swApp = app
    harness.constants = {"swDocPART": 1}
    harness.is_connected = lambda: True
    harness._get_attr_or_call = lambda obj, name: getattr(obj, name)
    tracked: list[object] = []
    harness._track_session_document = tracked.append

    result = await harness.open_model(target)

    assert result.is_success
    app.OpenDoc6.assert_called_once()
    assert tracked == [model]


@pytest.mark.asyncio
async def test_open_model_reuses_process_cache_while_document_enumeration_lags() -> None:
    """A second adapter must not OpenDoc6 while SolidWorks updates GetDocuments."""
    target = os.path.abspath(r"C:\controlled\lagging_fixture.sldprt")
    model = SimpleNamespace(
        GetTitle="lagging_fixture.sldprt",
        GetPathName=target,
        GetActiveConfiguration=lambda: None,
    )
    app = SimpleNamespace(GetDocuments=(), OpenDoc6=Mock(return_value=model))

    def _harness() -> _IOHarness:
        harness = _IOHarness(None)
        harness.swApp = app
        harness.constants = {"swDocPART": 1}
        harness.is_connected = lambda: True
        harness._get_attr_or_call = lambda obj, name: getattr(obj, name)
        return harness

    try:
        first = await _harness().open_model(target)
        second = await _harness().open_model(target)
    finally:
        _io_module._OPEN_DOCUMENT_CACHE.pop(
            os.path.normcase(os.path.abspath(target)), None
        )

    assert first.is_success and second.is_success
    app.OpenDoc6.assert_called_once()
    assert second.data.properties["reused_existing_document"] is True


@pytest.mark.asyncio
async def test_open_model_does_not_open_when_document_enumeration_fails() -> None:
    """A transient COM enumeration failure must not create a duplicate tab."""
    target = os.path.abspath(r"C:\controlled\busy_fixture.sldprt")
    wrong_model = SimpleNamespace(
        GetTitle="other.sldprt",
        GetPathName=os.path.abspath(r"C:\controlled\other.sldprt"),
    )
    app = SimpleNamespace(
        GetDocuments=Mock(side_effect=RuntimeError("COM busy")),
        ActiveDoc=wrong_model,
        OpenDoc6=Mock(),
    )
    harness = _IOHarness(None)
    harness.swApp = app
    harness.constants = {"swDocPART": 1}
    harness.is_connected = lambda: True
    harness._get_attr_or_call = lambda obj, name: getattr(obj, name)

    result = await harness.open_model(target)

    assert result.status is AdapterResultStatus.ERROR
    app.OpenDoc6.assert_not_called()


@pytest.mark.asyncio
async def test_pack_and_go_does_not_open_when_document_enumeration_fails(
    monkeypatch, tmp_path
) -> None:
    """Pack-and-Go must not duplicate an assembly during a COM busy window."""
    monkeypatch.setattr(_io_module, "_get_sw_comtypes_lib", lambda: object())
    source = tmp_path / "source.sldasm"
    source.write_bytes(b"assembly")
    app = SimpleNamespace(
        GetDocuments=Mock(side_effect=RuntimeError("COM busy")),
        OpenDoc6=Mock(),
    )
    harness = _IOHarness(None)
    harness.swApp = app

    result = await harness.pack_and_go_assembly(
        str(source), str(tmp_path / "packed")
    )

    assert result.status is AdapterResultStatus.ERROR
    app.OpenDoc6.assert_not_called()


@pytest.mark.asyncio
async def test_open_model_rejects_failed_activation_of_existing_document() -> None:
    """A failed activation must not route later operations to another document."""
    target = os.path.abspath(r"C:\controlled\fixture.sldprt")
    target_model = SimpleNamespace(
        GetTitle="fixture.sldprt",
        GetPathName=target,
        GetActiveConfiguration=lambda: None,
    )
    wrong_model = SimpleNamespace(
        GetTitle="other.sldprt",
        GetPathName=os.path.abspath(r"C:\controlled\other.sldprt"),
    )
    app = SimpleNamespace(
        GetDocuments=(target_model, wrong_model),
        ActiveDoc=wrong_model,
        ActivateDoc3=Mock(return_value=wrong_model),
        OpenDoc6=Mock(return_value=target_model),
    )
    harness = _IOHarness(None)
    harness.swApp = app
    harness.constants = {"swDocPART": 1}
    harness.is_connected = lambda: True
    harness._get_attr_or_call = lambda obj, name: getattr(obj, name)

    result = await harness.open_model(target)

    assert result.status is AdapterResultStatus.ERROR
    app.OpenDoc6.assert_not_called()


@pytest.mark.asyncio
async def test_open_model_rejects_existing_document_without_activation_api() -> None:
    """A non-active document cannot be reported active when activation is unavailable."""
    target = os.path.abspath(r"C:\controlled\fixture_no_activation.sldprt")
    target_model = SimpleNamespace(
        GetTitle="fixture_no_activation.sldprt",
        GetPathName=target,
        GetActiveConfiguration=lambda: None,
    )
    wrong_model = SimpleNamespace(
        GetTitle="other.sldprt",
        GetPathName=os.path.abspath(r"C:\controlled\other.sldprt"),
    )
    app = SimpleNamespace(
        GetDocuments=(target_model, wrong_model),
        ActiveDoc=wrong_model,
        OpenDoc6=Mock(return_value=target_model),
    )
    harness = _IOHarness(None)
    harness.swApp = app
    harness.constants = {"swDocPART": 1}
    harness.is_connected = lambda: True
    harness._get_attr_or_call = lambda obj, name: getattr(obj, name)

    result = await harness.open_model(target)

    assert result.status is AdapterResultStatus.ERROR
    app.OpenDoc6.assert_not_called()


@pytest.mark.asyncio
async def test_list_drawing_views_skips_sheet_and_returns_safe_summaries() -> None:
    """The sheet pseudo-view is skipped when traversing a SolidWorks drawing."""
    second_view = SimpleNamespace(
        Name="Drawing View2", GetNextView=Mock(return_value=None)
    )
    first_view = SimpleNamespace(
        Name="Drawing View1", GetNextView=Mock(return_value=second_view)
    )
    sheet = SimpleNamespace(GetNextView=Mock(return_value=first_view))
    current_model = SimpleNamespace(
        GetType=lambda: 3, GetFirstView=Mock(return_value=sheet)
    )
    harness = _IOHarness(current_model)

    result = await harness.list_drawing_views()

    assert result.is_success
    assert result.data == [
        {"view_id": "Drawing View1", "display_name": "Drawing View1"},
        {"view_id": "Drawing View2", "display_name": "Drawing View2"},
    ]
    current_model.GetFirstView.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_drawing_view_outline_resolves_current_view_and_returns_metres() -> None:
    """The adapter preserves native metres for workflow-level unit normalization."""
    view = SimpleNamespace(
        Name="Drawing View1",
        GetOutline=Mock(return_value=(0.02, 0.03, 0.08, 0.09)),
        GetNextView=Mock(return_value=None),
    )
    sheet_view = SimpleNamespace(GetNextView=Mock(return_value=view))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet_view),
    )
    harness = _IOHarness(current_model)

    result = await harness.get_drawing_view_outline("Drawing View1")

    assert result.is_success
    assert result.data == {
        "view_id": "Drawing View1",
        "left_m": 0.02,
        "bottom_m": 0.03,
        "right_m": 0.08,
        "top_m": 0.09,
    }
    view.GetOutline.assert_called_once_with()


@pytest.mark.asyncio
async def test_activate_drawing_view_resolves_current_view_before_com_call() -> None:
    """Activation never forwards an arbitrary view string without current resolution."""
    view = SimpleNamespace(
        Name="Drawing View1", GetNextView=Mock(return_value=None)
    )
    sheet_view = SimpleNamespace(GetNextView=Mock(return_value=view))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet_view),
        ActivateView=Mock(return_value=True),
    )
    harness = _IOHarness(current_model)

    result = await harness.activate_drawing_view("Drawing View1")

    assert result.is_success
    assert result.data == {
        "view_id": "Drawing View1",
        "display_name": "Drawing View1",
    }
    current_model.ActivateView.assert_called_once_with("Drawing View1")


@pytest.mark.asyncio
async def test_list_and_activate_drawing_sheets_use_current_sheet_api() -> None:
    """Sheet enumeration and activation stay within the active drawing document."""
    current_sheet = SimpleNamespace(
        GetName=Mock(side_effect=["Sheet2", "Sheet1"])
    )
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetSheetNames=Mock(return_value=("Sheet1", "Sheet2")),
        GetCurrentSheet=Mock(return_value=current_sheet),
        ActivateSheet=Mock(return_value=True),
    )
    harness = _IOHarness(current_model)

    listed = await harness.list_drawing_sheets()
    activated = await harness.activate_drawing_sheet("Sheet1")

    assert listed.is_success
    assert listed.data == [
        {"name": "Sheet1", "is_active": False},
        {"name": "Sheet2", "is_active": True},
    ]
    assert activated.is_success
    assert activated.data == {"name": "Sheet1", "is_active": True}
    current_model.ActivateSheet.assert_called_once_with("Sheet1")


@pytest.mark.asyncio
async def test_create_drawing_sheet_uses_new_sheet4_with_typed_parameters() -> None:
    """Sheet creation maps the public paper and layout fields to NewSheet4."""
    current_sheet = SimpleNamespace(GetName=Mock(return_value="Sheet3"))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetSheetNames=Mock(return_value=("Sheet1", "Sheet2")),
        GetCurrentSheet=Mock(return_value=current_sheet),
        NewSheet4=Mock(return_value=True),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_drawing_sheet(
        "Sheet3", "a4", 0.297, 0.210, False, 1, 2, 4, 5
    )

    assert result.is_success
    assert result.data == {"name": "Sheet3", "is_active": True, "created": True}
    current_model.NewSheet4.assert_called_once_with(
        "Sheet3", 4, 0, 1, 2, False, "", 0.297, 0.210, "",
        0.0, 0.0, 0.0, 0.0, 4, 5
    )


@pytest.mark.asyncio
async def test_update_drawing_view_layout_sets_position_and_scale_on_current_view() -> None:
    """Layout updates resolve a current view before mutating typed IView members."""
    view = SimpleNamespace(
        Name="Drawing View1",
        GetNextView=Mock(return_value=None),
        Position=(0.1, 0.1),
        ScaleDecimal=1.0,
    )
    sheet_view = SimpleNamespace(GetNextView=Mock(return_value=view))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet_view),
    )
    harness = _IOHarness(current_model)

    result = await harness.update_drawing_view_layout(
        "Drawing View1", 0.125, 0.085, 0.5
    )

    assert result.is_success
    assert result.data["view_id"] == "Drawing View1"
    assert view.Position == (0.125, 0.085)
    assert view.ScaleDecimal == 0.5


@pytest.mark.asyncio
async def test_align_drawing_views_uses_outlines_to_set_spaced_centres() -> None:
    """Alignment measures all views before applying ordered position changes."""
    first = SimpleNamespace(
        Name="Drawing View1",
        GetOutline=Mock(return_value=(0.02, 0.03, 0.08, 0.09)),
        GetNextView=Mock(),
        Position=(0.05, 0.06),
    )
    second = SimpleNamespace(
        Name="Drawing View2",
        GetOutline=Mock(return_value=(0.10, 0.02, 0.14, 0.10)),
        GetNextView=Mock(return_value=None),
        Position=(0.12, 0.06),
    )
    first.GetNextView.return_value = second
    sheet_view = SimpleNamespace(GetNextView=Mock(return_value=first))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet_view),
    )
    harness = _IOHarness(current_model)

    result = await harness.align_drawing_views(
        ["Drawing View1", "Drawing View2"], "horizontal", 0.01, 0.1, 0.2
    )

    assert result.is_success
    assert first.Position == pytest.approx((0.13, 0.2))
    assert second.Position == pytest.approx((0.19, 0.2))
    assert result.data["views"][1]["outline_m"] == pytest.approx(
        {"left": 0.17, "bottom": 0.16, "right": 0.21, "top": 0.24}
    )


@pytest.mark.asyncio
async def test_rebuild_drawing_calls_rebuild_and_save_on_the_active_drawing() -> None:
    """The typed adapter exposes the native drawing rebuild/save operation."""
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        RebuildAndSave=Mock(return_value=True),
    )
    harness = _IOHarness(current_model)

    result = await harness.rebuild_drawing()

    assert result.is_success
    assert result.data == {"rebuilt": True, "saved": True}
    current_model.RebuildAndSave.assert_called_once_with()


@pytest.mark.asyncio
async def test_rename_drawing_sheet_uses_set_name_and_verifies_sheet_names() -> None:
    """Sheet renaming uses a current sheet object rather than UI selection text."""
    sheet = SimpleNamespace(SetName=Mock(return_value=None))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetSheetNames=Mock(
            side_effect=[("Sheet1", "Sheet2"), ("DetailSheet", "Sheet2")]
        ),
        ActivateSheet=Mock(return_value=True),
        GetCurrentSheet=Mock(return_value=sheet),
    )
    harness = _IOHarness(current_model)

    result = await harness.rename_drawing_sheet("Sheet1", "DetailSheet")

    assert result.is_success
    assert result.data == {"name": "DetailSheet", "renamed_from": "Sheet1"}
    current_model.ActivateSheet.assert_called_once_with("Sheet1")
    sheet.SetName.assert_called_once_with("DetailSheet")


@pytest.mark.asyncio
async def test_create_projected_drawing_view_uses_callable_dynamic_proxy_without_type_info() -> (
    None
):
    """Projection uses the selected source view and clears temporary selection."""
    source_view = SimpleNamespace(
        Name="Drawing View1", GetNextView=Mock(return_value=None)
    )
    sheet = SimpleNamespace(GetNextView=Mock(return_value=source_view))
    projected_view = SimpleNamespace(Name="Drawing View2")
    extension = SimpleNamespace(SelectByID2=Mock(return_value=True))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet),
        Extension=extension,
        CreateProjectionView=Mock(return_value=projected_view),
        ClearSelection2=Mock(),
    )
    harness = _IOHarness(current_model)

    with patch.object(
        _io_module._sw_type_info,
        "flag_method",
        return_value=False,
    ):
        result = await harness.create_projected_drawing_view("Drawing View1", 0.15, 0.1)

    assert result.is_success
    assert result.data == {"view_id": "Drawing View2", "display_name": "Drawing View2"}
    extension.SelectByID2.assert_called_once_with(
        "Drawing View1", "DRAWINGVIEW", 0.0, 0.0, 0.0, False, 0, None, 0
    )
    current_model.CreateProjectionView.assert_called_once_with(0.15, 0.1, 0.0)
    current_model.ClearSelection2.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_create_projected_drawing_view_rejects_unknown_source() -> None:
    """Projection cannot select a view ID that is absent from the active drawing."""
    sheet = SimpleNamespace(GetNextView=Mock(return_value=None))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet),
        Extension=SimpleNamespace(SelectByID2=Mock(return_value=True)),
        CreateProjectionView=Mock(),
        ClearSelection2=Mock(),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_projected_drawing_view("Missing View", 0.15, 0.1)

    assert result.status == AdapterResultStatus.ERROR
    assert "no longer exists" in (result.error or "")
    current_model.CreateProjectionView.assert_not_called()


@pytest.mark.asyncio
async def test_create_projected_drawing_view_rejects_unsupported_legacy_api() -> None:
    """SolidWorks 2019 must not substitute an unrelated standard view."""
    source_view = SimpleNamespace(
        Name="Drawing View1",
        GetNextView=Mock(return_value=None),
        GetReferencedModelName=Mock(return_value="C:/controlled/fixture-part.sldprt"),
    )
    sheet = SimpleNamespace(GetNextView=Mock(return_value=source_view))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet),
        CreateDrawViewFromModelView3=Mock(),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_projected_drawing_view("Drawing View1", 0.15, 0.1)

    assert result.status == AdapterResultStatus.ERROR
    assert result.metadata == {
        "error_code": ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED
    }
    source_view.GetReferencedModelName.assert_not_called()
    current_model.CreateDrawViewFromModelView3.assert_not_called()


@pytest.mark.asyncio
async def test_create_projected_drawing_view_uses_dynamic_proxy_despite_stale_cache() -> (
    None
):
    """A stale type cache must not disable an available runtime method."""
    source_view = SimpleNamespace(
        Name="Drawing View1",
        GetNextView=Mock(return_value=None),
    )
    sheet = SimpleNamespace(GetNextView=Mock(return_value=source_view))
    projected_view = SimpleNamespace(Name="Drawing View2")
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet),
        Extension=SimpleNamespace(SelectByID2=Mock(return_value=True)),
        CreateProjectionView=Mock(return_value=projected_view),
        ClearSelection2=Mock(),
    )
    harness = _IOHarness(current_model)

    with patch.object(
        _io_module._sw_type_info,
        "flag_method",
        return_value=False,
    ):
        result = await harness.create_projected_drawing_view("Drawing View1", 0.15, 0.1)

    assert result.is_success
    current_model.CreateProjectionView.assert_called_once_with(0.15, 0.1, 0.0)


@pytest.mark.asyncio
async def test_create_projected_drawing_view_maps_runtime_member_not_found() -> None:
    """A newer local type cache cannot make an SW2019 API appear supported."""

    class _MemberNotFoundError(Exception):
        hresult = -2147352573

    source_view = SimpleNamespace(
        Name="Drawing View1", GetNextView=Mock(return_value=None)
    )
    sheet = SimpleNamespace(GetNextView=Mock(return_value=source_view))
    extension = SimpleNamespace(SelectByID2=Mock(return_value=True))
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=Mock(return_value=sheet),
        Extension=extension,
        CreateProjectionView=Mock(side_effect=_MemberNotFoundError()),
        ClearSelection2=Mock(),
    )
    harness = _IOHarness(current_model)

    with patch.object(
        _io_module._sw_type_info,
        "flag_method",
        return_value=True,
    ):
        result = await harness.create_projected_drawing_view("Drawing View1", 0.15, 0.1)

    assert result.status == AdapterResultStatus.ERROR
    assert result.metadata == {
        "error_code": ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED
    }
    current_model.ClearSelection2.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_create_auxiliary_drawing_view_resolves_edge_and_clears_selection() -> None:
    """Auxiliary views use an opaque persistent edge reference and typed COM API."""
    edge = SimpleNamespace(Select2=Mock(return_value=True))
    extension = SimpleNamespace(
        GetObjectByPersistReference3=Mock(return_value=edge)
    )
    auxiliary_view = SimpleNamespace(Name="Auxiliary View1")
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        Extension=extension,
        CreateAuxiliaryViewAt=Mock(return_value=auxiliary_view),
        ClearSelection2=Mock(),
    )
    harness = _IOHarness(current_model)
    token = base64.b64encode(b"drawing-edge").decode("ascii")

    result = await harness.create_auxiliary_drawing_view(token, 0.15, 0.1)

    assert result.is_success
    assert result.data == {
        "view_id": "Auxiliary View1",
        "display_name": "Auxiliary View1",
    }
    extension.GetObjectByPersistReference3.assert_called_once_with(b"drawing-edge")
    edge.Select2.assert_called_once_with(False, 0)
    current_model.CreateAuxiliaryViewAt.assert_called_once_with(0.15, 0.1, 0.0, 0)
    current_model.ClearSelection2.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_sheet_metal_state_and_flat_pattern_dxf_use_dedicated_part_apis() -> None:
    """Sheet-metal inspection, unfold, and DXF export avoid generic SaveAs paths."""
    flat_pattern = SimpleNamespace(
        GetTypeName2=lambda: "FlatPattern",
        Name="Flat-Pattern1",
        GetNextFeature=lambda: None,
    )
    sheet_metal = SimpleNamespace(
        GetTypeName2=lambda: "SheetMetal",
        GetDefinition=lambda: SimpleNamespace(Thickness=0.002),
        GetNextFeature=lambda: flat_pattern,
    )
    current_model = SimpleNamespace(
        FirstFeature=lambda: sheet_metal,
        GetBendState=Mock(side_effect=[3, 2, 2]),
        SetBendState=Mock(return_value=True),
        ForceRebuild3=Mock(return_value=True),
        ExportToDWG2=Mock(return_value=True),
    )
    harness = _IOHarness(current_model)

    info = await harness.get_sheet_metal_info()
    flattened = await harness.set_sheet_metal_flat_state(True)
    exported = await harness.export_flat_pattern_dxf(
        r"C:\controlled\bracket.dxf",
        r"C:\controlled\bracket.sldprt",
        21,
    )

    assert info.is_success
    assert info.data == {
        "is_sheet_metal": True,
        "is_flattened": False,
        "thickness_mm": 2.0,
        "flat_pattern_feature_name": "Flat-Pattern1",
    }
    assert flattened.is_success
    assert flattened.data["is_flattened"] is True
    current_model.SetBendState.assert_called_once_with(2)
    current_model.ForceRebuild3.assert_called_once_with(False)
    assert exported.is_success
    current_model.ExportToDWG2.assert_called_once_with(
        r"C:\controlled\bracket.dxf",
        r"C:\controlled\bracket.sldprt",
        1,
        True,
        (0.0,) * 12,
        False,
        False,
        21,
        None,
    )


@pytest.mark.asyncio
async def test_sheet_metal_state_uses_reverse_feature_lookup_when_first_feature_fails() -> None:
    """COM builds without FirstFeature still expose sheet-metal state."""
    sheet_metal = SimpleNamespace(
        GetTypeName2=lambda: "SheetMetal",
        GetDefinition=lambda: SimpleNamespace(Thickness=0.0015),
    )
    flat_pattern = SimpleNamespace(
        GetTypeName2=lambda: "FlatPattern",
        Name="Flat-Pattern1",
    )
    current_model = SimpleNamespace(
        FirstFeature=lambda: (_ for _ in ()).throw(RuntimeError("unsupported")),
        FeatureByPositionReverse=lambda position: {
            0: flat_pattern,
            1: sheet_metal,
        }.get(position),
        GetBendState=lambda: 3,
    )
    harness = _IOHarness(current_model)

    info = await harness.get_sheet_metal_info()

    assert info.is_success
    assert info.data == {
        "is_sheet_metal": True,
        "is_flattened": False,
        "thickness_mm": 1.5,
        "flat_pattern_feature_name": "Flat-Pattern1",
    }


@pytest.mark.asyncio
async def test_drawing_bom_and_note_use_real_drawing_com_apis() -> None:
    """BOM and note creation use InsertBomTable4 and InsertNote with positions."""
    source_view = SimpleNamespace(
        Name="Drawing View1",
        GetNextView=lambda: None,
        Select4=Mock(return_value=True),
    )
    sheet = SimpleNamespace(GetNextView=lambda: source_view)
    bom_annotation = SimpleNamespace(GetName=lambda: "BOM1")
    bom_table = SimpleNamespace(
        GetAnnotation=lambda: bom_annotation,
        RowCount=4,
        ColumnCount=3,
    )
    note_annotation = SimpleNamespace(
        GetName=lambda: "Note1", SetPosition2=Mock(return_value=True)
    )
    note = SimpleNamespace(GetAnnotation=lambda: note_annotation)
    revision_table = SimpleNamespace(
        AddRevision=Mock(return_value=0), CurrentRevision="A"
    )
    source_view.InsertBomTable4 = Mock(return_value=bom_table)
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=lambda: sheet,
        InsertNote=Mock(return_value=note),
        InsertRevisionTable2=Mock(return_value=revision_table),
        Extension=SimpleNamespace(
            GetPersistReference3=lambda entity: b"bom-token" if entity is bom_table else b""
        ),
    )
    harness = _IOHarness(current_model)

    bom = await harness.create_drawing_bom(
        "Drawing View1", "parts_only", "Default", 0.18, 0.08, False
    )
    note_result = await harness.create_drawing_note("DEBURR ALL EDGES", 0.12, 0.06)
    revision_result = await harness.create_drawing_revision_table(
        0.2, 0.1, 0, "A"
    )

    assert bom.is_success
    assert bom.data == {
        "display_name": "BOM1",
        "bom_type": "parts_only",
        "row_count": 4,
        "column_count": 3,
        "persistent_reference": "Ym9tLXRva2Vu",
    }
    source_view.InsertBomTable4.assert_called_once_with(
        False, 0.18, 0.08, 0, 1, "Default", "", False, 0, False
    )
    assert note_result.is_success
    assert note_result.data == {"annotation_type": "note", "display_name": "Note1"}
    current_model.InsertNote.assert_called_once_with("DEBURR ALL EDGES")
    note_annotation.SetPosition2.assert_called_once_with(0.12, 0.06, 0.0)
    assert revision_result.is_success
    assert revision_result.data == {
        "display_name": "Revision Table",
        "revision": "A",
        "revision_id": 0,
        "current_revision": "A",
    }
    current_model.InsertRevisionTable2.assert_called_once_with(
        True, 0.2, 0.1, 0, "", 0, True
    )
    revision_table.AddRevision.assert_called_once_with("A")


@pytest.mark.asyncio
async def test_revision_table_rows_add_and_delete_use_persistent_reference() -> None:
    """Revision-table mutations resolve only the server-owned persistent token."""
    rows = {0: "A", 1: "B"}
    revision_table = SimpleNamespace(
        GetIdForRowNumber=lambda row: row if row in rows else (_ for _ in ()).throw(RuntimeError("end")),
        GetRevisionForId=lambda revision_id: rows[revision_id],
        GetRevisionSymbolCount=lambda _revision_id: 2,
        AddRevision=Mock(return_value=2),
        CurrentRevision="C",
        DeleteRevision=Mock(return_value=True),
    )
    extension = SimpleNamespace(
        GetObjectByPersistReference3=Mock(return_value=revision_table)
    )
    model = SimpleNamespace(GetType=lambda: 3, Extension=extension)
    harness = _IOHarness(model)

    listed = await harness.list_drawing_revisions("dGFibGU=")
    added = await harness.add_drawing_revision("dGFibGU=", "C")
    deleted = await harness.delete_drawing_revision("dGFibGU=", 1, True)

    assert listed.is_success
    assert listed.data == [
        {"row_number": 0, "revision_id": 0, "revision": "A", "symbol_count": 2},
        {"row_number": 1, "revision_id": 1, "revision": "B", "symbol_count": 2},
    ]
    assert added.is_success and added.data["revision_id"] == 2
    assert deleted.is_success and deleted.data == {"revision_id": 1, "deleted": True}
    assert extension.GetObjectByPersistReference3.call_count == 3
    revision_table.AddRevision.assert_called_once_with("C")
    revision_table.DeleteRevision.assert_called_once_with(1, True)


@pytest.mark.asyncio
async def test_drawing_annotation_targets_and_creators_use_persist_references() -> None:
    """Dimensions, balloons, and hole callouts never receive selection text from clients."""
    dimension = SimpleNamespace(GetNext5=lambda: None, Select4=Mock(return_value=True))
    component = SimpleNamespace(Name2="Bracket-1", Select4=Mock(return_value=True))
    edge = SimpleNamespace(Select4=Mock(return_value=True))
    view = SimpleNamespace(
        Name="Drawing View1",
        GetNextView=lambda: None,
        GetFirstDisplayDimension5=lambda: dimension,
        GetVisibleComponents=lambda: [component],
        GetVisibleEntities2=lambda _component, _entity_type: [edge],
    )
    sheet = SimpleNamespace(GetNextView=lambda: view)
    annotation = SimpleNamespace(GetName=lambda: "Annotation1", SetPosition2=Mock())
    extension = SimpleNamespace(
        GetPersistReference3=Mock(side_effect=[b"\x01", b"\x02", b"\x03"]),
        GetObjectByPersistReference3=Mock(
            side_effect=[
                (dimension, 0), (component, 0), (edge, 0),
                (dimension, 0), (edge, 0), (edge, 0), (dimension, 0),
                (edge, 0),
            ]
        ),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=lambda: sheet,
        Extension=extension,
        ClearSelection2=Mock(return_value=True),
        AddDimension2=Mock(
            return_value=SimpleNamespace(GetAnnotation=lambda: annotation)
        ),
        InsertBOMBalloon2=Mock(
            return_value=SimpleNamespace(GetAnnotation=lambda: annotation)
        ),
        AddHoleCallout2=Mock(
            return_value=SimpleNamespace(GetAnnotation=lambda: annotation)
        ),
        AddOrdinateDimension2=Mock(
            return_value=SimpleNamespace(GetAnnotation=lambda: annotation)
        ),
        InsertSurfaceFinishSymbol3=Mock(
            return_value=SimpleNamespace(GetAnnotation=lambda: annotation)
        ),
        InsertWeldSymbol3=Mock(
            return_value=SimpleNamespace(
                GetAnnotation=lambda: annotation,
                SetWeldSymbol=Mock(return_value=True),
            )
        ),
        InsertGtol=Mock(
            return_value=SimpleNamespace(
                GetAnnotation=lambda: annotation,
                SetFrameValues=Mock(return_value=True),
            )
        ),
        AddCenterMark=Mock(return_value=True),
    )
    harness = _IOHarness(current_model)

    targets = await harness.list_drawing_annotation_targets(
        "Drawing View1", ("dimension", "component", "edge")
    )
    placed = await harness.place_drawing_dimension("AQ==", 0.1, 0.2)
    balloon = await harness.create_drawing_balloon("Ag==", 0.11, 0.21, "circular", "")
    callout = await harness.create_drawing_hole_callout("Aw==", 0.12, 0.22)
    ordinate = await harness.create_drawing_ordinate_dimension("AQ==", 0.13, 0.23, "vertical")
    surface_finish = await harness.create_drawing_surface_finish(
        "Aw==", 0.14, 0.24, "machining_required", "1.6"
    )
    weld_symbol = await harness.create_drawing_weld_symbol(
        "Aw==", 0.15, 0.25, "Fillet", "5"
    )
    geometric_tolerance = await harness.create_drawing_geometric_tolerance(
        "AQ==", 0.16, 0.26, "0.05", "A"
    )
    center_mark = await harness.create_drawing_center_mark("Aw==", 0.005, True)

    assert targets.is_success
    assert [target["target_type"] for target in targets.data] == [
        "dimension",
        "component",
        "edge",
    ]
    assert all("persistent_reference" in target for target in targets.data)
    assert all(result.is_success for result in (
        placed, balloon, callout, ordinate, surface_finish, weld_symbol, geometric_tolerance, center_mark
    ))
    extension.GetObjectByPersistReference3.assert_has_calls(
        [
            call(b"\x01"), call(b"\x02"), call(b"\x03"), call(b"\x01"),
            call(b"\x03"), call(b"\x03"), call(b"\x01"), call(b"\x03"),
        ]
    )
    current_model.AddDimension2.assert_called_once_with(0.1, 0.2, 0.0)
    current_model.InsertBOMBalloon2.assert_called_once_with(1, 0, 0, "", 0, "")
    current_model.AddHoleCallout2.assert_called_once_with(0.12, 0.22, 0.0)
    current_model.AddOrdinateDimension2.assert_called_once_with(1, 0.13, 0.23, 0.0)
    current_model.InsertSurfaceFinishSymbol3.assert_called_once_with(
        1, 0, 0.14, 0.24, 0.0, 0, 0, "", "", "", "", "1.6", "", ""
    )
    current_model.InsertWeldSymbol3.assert_called_once_with()
    current_model.InsertGtol.assert_called_once_with()
    current_model.AddCenterMark.assert_called_once_with(0.005, True)
    current_model.InsertWeldSymbol3.return_value.SetWeldSymbol.assert_called_once_with(
        True, "Fillet", "5", "", "", ""
    )
    current_model.InsertGtol.return_value.SetFrameValues.assert_called_once_with(
        1, "0.05", "", "A", "", ""
    )


@pytest.mark.asyncio
async def test_custom_properties_use_document_property_manager_and_safe_values() -> (
    None
):
    """Property listing reads names and resolved values without COM objects."""
    manager = SimpleNamespace(
        GetNames=Mock(return_value=["PartNumber"]),
        Get6=Mock(return_value=(0, "BRKT-001", "BRKT-001", True, False)),
    )
    extension = SimpleNamespace(CustomPropertyManager=Mock(return_value=manager))
    harness = _IOHarness(SimpleNamespace(Extension=extension))

    result = await harness.list_custom_properties()

    assert result.is_success
    assert result.data == [
        {"name": "PartNumber", "value": "BRKT-001", "resolved_value": "BRKT-001"}
    ]
    extension.CustomPropertyManager.assert_called_once_with("")
    manager.Get6.assert_called_once_with("PartNumber", False)


@pytest.mark.asyncio
async def test_custom_properties_accept_pywin32_get_names_property() -> None:
    """Late-bound pywin32 can expose the zero-argument name getter as a tuple."""
    manager = SimpleNamespace(
        GetNames=("PartNumber",),
        Get6=Mock(return_value=(0, "BRKT-001", "BRKT-001", True, False)),
    )
    extension = SimpleNamespace(CustomPropertyManager=Mock(return_value=manager))
    harness = _IOHarness(SimpleNamespace(Extension=extension))

    result = await harness.list_custom_properties()

    assert result.is_success
    assert result.data == [
        {"name": "PartNumber", "value": "BRKT-001", "resolved_value": "BRKT-001"}
    ]
    manager.Get6.assert_called_once_with("PartNumber", False)


@pytest.mark.asyncio
async def test_custom_properties_use_byref_variants_without_generated_wrapper(
    monkeypatch,
) -> None:
    """Late-bound Get6 receives output values through explicit VARIANT references."""

    class _Variant:
        def __init__(self, _kind: int, value: object) -> None:
            self.value = value

    def get6(name: str, _cached: bool, *outputs: _Variant):
        if len(outputs) != 4:
            raise TypeError("late-bound Get6 needs explicit output parameters")
        assert name == "PartNumber"
        outputs[0].value = "BRKT-001"
        outputs[1].value = "BRKT-001"
        outputs[2].value = True
        outputs[3].value = False
        return 2

    monkeypatch.setattr(
        _io_module,
        "win32com",
        SimpleNamespace(client=SimpleNamespace(VARIANT=_Variant)),
    )
    monkeypatch.setattr(
        _io_module,
        "pythoncom",
        SimpleNamespace(VT_BYREF=0x4000, VT_BSTR=8, VT_BOOL=11),
    )
    manager = SimpleNamespace(GetNames=("PartNumber",), Get6=get6)
    extension = SimpleNamespace(CustomPropertyManager=Mock(return_value=manager))
    harness = _IOHarness(SimpleNamespace(Extension=extension))

    result = await harness.list_custom_properties()

    assert result.is_success
    assert result.data == [
        {"name": "PartNumber", "value": "BRKT-001", "resolved_value": "BRKT-001"}
    ]


@pytest.mark.asyncio
async def test_set_custom_property_uses_add3_replace_value() -> None:
    """Property writes use SolidWorks text/replace constants and return a safe summary."""
    manager = SimpleNamespace(
        Add3=Mock(return_value=0),
        Get6=Mock(return_value=(0, "BRKT-002", "BRKT-002", True, False)),
    )
    extension = SimpleNamespace(CustomPropertyManager=Mock(return_value=manager))
    harness = _IOHarness(SimpleNamespace(Extension=extension))

    result = await harness.set_custom_property("PartNumber", "BRKT-002")

    assert result.is_success
    assert result.data == {
        "name": "PartNumber",
        "value": "BRKT-002",
        "resolved_value": "BRKT-002",
    }
    manager.Add3.assert_called_once_with("PartNumber", 30, "BRKT-002", 2)


@pytest.mark.asyncio
async def test_set_custom_property_preserves_existing_property_metadata() -> None:
    """Repeated writes use ReplaceValue instead of DeleteAndAdd."""
    manager = SimpleNamespace(
        Add3=Mock(return_value=0),
        Get6=Mock(return_value=(0, "BRKT-003", "BRKT-003", True, False)),
    )
    extension = SimpleNamespace(CustomPropertyManager=Mock(return_value=manager))
    harness = _IOHarness(SimpleNamespace(Extension=extension))

    first = await harness.set_custom_property("PartNumber", "BRKT-002")
    second = await harness.set_custom_property("PartNumber", "BRKT-003")

    assert first.is_success
    assert second.is_success
    assert manager.Add3.call_args_list == [
        call("PartNumber", 30, "BRKT-002", 2),
        call("PartNumber", 30, "BRKT-003", 2),
    ]


@pytest.mark.asyncio
async def test_delete_custom_property_uses_delete2_and_verifies_removal() -> None:
    """Custom property deletion succeeds only when the name disappears on readback."""
    manager = SimpleNamespace(
        Delete2=Mock(return_value=0),
        GetNames=Mock(return_value=[]),
    )
    extension = SimpleNamespace(CustomPropertyManager=Mock(return_value=manager))
    harness = _IOHarness(SimpleNamespace(Extension=extension))

    result = await harness.delete_custom_property("PartNumber")

    assert result.is_success
    assert result.data == {"name": "PartNumber"}
    manager.Delete2.assert_called_once_with("PartNumber")
    manager.GetNames.assert_called_once_with()


@pytest.mark.asyncio
async def test_list_solid_bodies_uses_get_bodies2_and_returns_safe_summaries() -> None:
    """Part body inspection converts COM bodies to bounded, serializable rows."""
    bodies = [
        SimpleNamespace(Name="Base", Visible=True),
        SimpleNamespace(Name="Lug", Visible=False),
    ]
    current_model = SimpleNamespace(GetBodies2=Mock(return_value=bodies))
    harness = _IOHarness(current_model)

    result = await harness.list_solid_bodies(include_hidden=True)

    assert result.is_success
    assert result.data == [
        {"body_id": "body_1", "name": "Base", "visible": True},
        {"body_id": "body_2", "name": "Lug", "visible": False},
    ]
    current_model.GetBodies2.assert_called_once_with(0, False)


@pytest.mark.asyncio
async def test_list_surface_bodies_uses_sheet_body_type_and_safe_summaries() -> None:
    """Surface inspection requests the ``swSheetBody`` GetBodies2 branch."""
    body = SimpleNamespace(Name="Surface-Extrude1", Visible=True)
    current_model = SimpleNamespace(GetBodies2=Mock(return_value=[body]))
    harness = _IOHarness(current_model)

    result = await harness.list_surface_bodies(include_hidden=True)

    assert result.is_success
    assert result.data == [
        {
            "body_id": "surface_body_1",
            "name": "Surface-Extrude1",
            "visible": True,
        }
    ]
    current_model.GetBodies2.assert_called_once_with(1, False)


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_uses_active_doc_extension_fallback() -> None:
    """Assembly-specific dispatches can require the app's base active document view."""

    class _AssemblyDispatch:
        GetType = staticmethod(lambda: 2)
        GetComponents = staticmethod(lambda _top_level: [])

        @property
        def Extension(self) -> object:
            raise RuntimeError("Member not found")

    extension = SimpleNamespace(GetPersistReference3=Mock())
    harness = _IOHarness(_AssemblyDispatch())
    harness.swApp = SimpleNamespace(ActiveDoc=SimpleNamespace(Extension=extension))

    result = await harness.list_assembly_mate_entities(entity_types=("edge",))

    assert result.is_success
    assert result.data == []


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_uses_vtable_extension_fallback(
    monkeypatch,
) -> None:
    """The vtable bridge supports legacy dispatches without Extension properties."""

    class _AssemblyDispatch:
        GetType = staticmethod(lambda: 2)
        GetComponents = staticmethod(lambda _top_level: [])

        @property
        def Extension(self) -> object:
            raise RuntimeError("Member not found")

    extension = SimpleNamespace(GetPersistReference3=Mock())
    bridge = Mock(return_value=SimpleNamespace(Extension=extension))
    monkeypatch.setattr(
        _io_module,
        "_get_sw_comtypes_lib",
        lambda: SimpleNamespace(IModelDoc2=object()),
    )
    monkeypatch.setattr(_io_module, "_bridge_com_to_comtypes", bridge)
    harness = _IOHarness(_AssemblyDispatch())

    result = await harness.list_assembly_mate_entities(entity_types=("edge",))

    assert result.is_success
    assert result.data == []
    bridge.assert_called_once()


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_bridges_pywin32_entity_for_vtable_extension(
    monkeypatch,
) -> None:
    """Persistent references bridge pywin32 topology into a vtable extension."""
    edge = SimpleNamespace(GetNext=lambda: None)
    body = SimpleNamespace(GetFirstEdge=lambda: edge)
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetBodies3=Mock(return_value=[body]),
    )
    bridged_edge = object()

    def get_persist_reference(entity: object) -> bytes:
        if entity is edge:
            raise RuntimeError("GetEdges.QueryInterface")
        assert entity is bridged_edge
        return b"\x01"

    extension = SimpleNamespace(GetPersistReference3=get_persist_reference)
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
        Extension=extension,
    )
    bridge = Mock(return_value=bridged_edge)
    monkeypatch.setattr(_io_module, "_ComtypesIDispatch", object())
    monkeypatch.setattr(_io_module, "_bridge_com_to_comtypes", bridge)
    harness = _IOHarness(current_model)

    result = await harness.list_assembly_mate_entities(entity_types=("edge",))

    assert result.is_success
    assert result.data[0]["persistent_reference"] == "AQ=="
    bridge.assert_called_once_with(edge, _io_module._ComtypesIDispatch)


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_falls_back_to_single_component_body() -> None:
    """Single-body component dispatches need not expose GetBodies3."""

    class _LegacyComponent:
        GetID = staticmethod(lambda: 9)
        Name2 = "FixturePart-1"
        GetBody = Mock()

        def __getattr__(self, name: str) -> object:
            if name == "GetBodies3":
                raise RuntimeError("Member not found")
            raise AttributeError(name)

    edge = SimpleNamespace(GetNext=Mock(return_value=None))
    body = SimpleNamespace(GetEdges=Mock(return_value=[edge]))
    component = _LegacyComponent()
    component.GetBody.return_value = body
    extension = SimpleNamespace(GetPersistReference3=Mock(return_value=b"\x01"))
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
        Extension=extension,
    )
    harness = _IOHarness(current_model)

    result = await harness.list_assembly_mate_entities(entity_types=("edge",))

    assert result.is_success
    assert result.data == [
        {
            "component_id": "9",
            "entity_type": "edge",
            "display_name": "FixturePart-1 Edge 1",
            "persistent_reference": "AQ==",
        }
    ]


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_falls_back_to_component_part_bodies() -> None:
    """Component source parts provide topology when component body APIs fail."""
    edge = SimpleNamespace(GetNext=Mock(return_value=None))
    body = SimpleNamespace(GetEdges=Mock(return_value=[edge]))
    source_part = SimpleNamespace(GetBodies2=Mock(return_value=[body]))
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetBody=Mock(return_value=None),
        GetModelDoc2=Mock(return_value=source_part),
    )
    extension = SimpleNamespace(GetPersistReference3=Mock(return_value=b"\x01"))
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
        Extension=extension,
    )
    harness = _IOHarness(current_model)

    result = await harness.list_assembly_mate_entities(entity_types=("edge",))

    assert result.is_success
    assert result.data[0]["component_id"] == "9"
    source_part.GetBodies2.assert_called_once_with(0, False)


@pytest.mark.asyncio
async def test_list_assembly_mate_entities_falls_back_to_legacy_persist_reference() -> None:
    """Legacy extensions can issue persistent tokens without the v3 API."""

    class _LegacyExtension:
        @property
        def GetPersistReference3(self) -> object:
            raise RuntimeError(-2147352573, "Member not found")

        @staticmethod
        def GetPersistReference(_entity: object) -> bytes:
            return b"\x01"

    edge = SimpleNamespace(GetNext=Mock(return_value=None))
    body = SimpleNamespace(GetEdges=Mock(return_value=[edge]))
    component = SimpleNamespace(
        GetID=lambda: 9,
        Name2="FixturePart-1",
        GetBody=Mock(return_value=body),
    )
    current_model = SimpleNamespace(
        GetType=lambda: 2,
        GetComponents=Mock(return_value=[component]),
        Extension=_LegacyExtension(),
    )
    harness = _IOHarness(current_model)

    result = await harness.list_assembly_mate_entities(entity_types=("edge",))

    assert result.is_success
    assert result.data[0]["persistent_reference"] == "AQ=="


@pytest.mark.asyncio
async def test_list_part_topology_issues_persistent_tokens_without_com_objects() -> None:
    """Topology enumeration keeps face/edge COM objects behind base64 tokens."""
    face = SimpleNamespace(GetNextFace=lambda: None)
    edge = SimpleNamespace(GetNext=lambda: None)
    body = SimpleNamespace(GetFirstFace=lambda: face, GetFirstEdge=lambda: edge)
    extension = SimpleNamespace(
        GetPersistReference3=lambda entity: b"face-token" if entity is face else b"edge-token"
    )
    current_model = SimpleNamespace(
        GetBodies2=Mock(return_value=[body]), Extension=extension
    )
    harness = _IOHarness(current_model)

    result = await harness.list_part_topology(("face", "edge"))

    assert result.is_success
    assert result.data == [
        {
            "entity_type": "face",
            "display_name": "Body 1 Face 1",
            "persistent_reference": "ZmFjZS10b2tlbg==",
        },
        {
            "entity_type": "edge",
            "display_name": "Body 1 Edge 1",
            "persistent_reference": "ZWRnZS10b2tlbg==",
        },
    ]


@pytest.mark.asyncio
async def test_list_part_topology_includes_safe_face_edge_and_vertex_geometry() -> None:
    """Topology rows expose measured geometry while retaining only persistent tokens."""
    face = SimpleNamespace(
        GetNextFace=lambda: None,
        GetArea=lambda: 0.012,
        Normal=(0.0, 0.0, 1.0),
    )
    edge = SimpleNamespace(
        GetNext=lambda: None,
        GetLength=lambda: 0.025,
        GetCurve=lambda: SimpleNamespace(Identity=3001),
    )
    vertex = SimpleNamespace(GetPoint=lambda: (0.1, 0.2, 0.3))
    body = SimpleNamespace(
        GetFirstFace=lambda: face,
        GetFirstEdge=lambda: edge,
        GetVertices=lambda: [vertex],
    )
    extension = SimpleNamespace(
        GetPersistReference3=lambda entity: {
            id(face): b"face-token",
            id(edge): b"edge-token",
            id(vertex): b"vertex-token",
        }[id(entity)]
    )
    current_model = SimpleNamespace(
        GetBodies2=Mock(return_value=[body]), Extension=extension
    )
    harness = _IOHarness(current_model)

    result = await harness.list_part_topology(("face", "edge", "vertex"))

    assert result.is_success
    assert result.data == [
        {
            "entity_type": "face",
            "display_name": "Body 1 Face 1",
            "persistent_reference": "ZmFjZS10b2tlbg==",
            "area_mm2": 12000.0,
            "normal": [0.0, 0.0, 1.0],
        },
        {
            "entity_type": "edge",
            "display_name": "Body 1 Edge 1",
            "persistent_reference": "ZWRnZS10b2tlbg==",
            "length_mm": 25.0,
            "curve_identity": 3001,
        },
        {
            "entity_type": "vertex",
            "display_name": "Body 1 Vertex 1",
            "persistent_reference": "dmVydGV4LXRva2Vu",
            "point_mm": [100.0, 200.0, 300.0],
        },
    ]


@pytest.mark.asyncio
async def test_create_drawing_bom_returns_a_persistent_table_reference() -> None:
    """A newly inserted BOM is actionable only through a server-side table token."""
    source_view = SimpleNamespace(
        Name="Assembly View",
        InsertBomTable4=Mock(
            return_value=SimpleNamespace(
                GetAnnotation=lambda: SimpleNamespace(GetName=lambda: "BOM1"),
                RowCount=3,
                ColumnCount=4,
            )
        ),
    )
    sheet_view = SimpleNamespace(GetNextView=lambda: source_view)
    source_view.GetNextView = lambda: None
    table = source_view.InsertBomTable4.return_value
    current_model = SimpleNamespace(
        GetType=lambda: 3,
        GetFirstView=lambda: sheet_view,
        Extension=SimpleNamespace(
            GetPersistReference3=lambda entity: b"bom-token" if entity is table else b""
        ),
    )
    harness = _IOHarness(current_model)

    result = await harness.create_drawing_bom(
        "Assembly View", "parts_only", "Default", 0.1, 0.1, False
    )

    assert result.is_success
    assert result.data == {
        "display_name": "BOM1",
        "bom_type": "parts_only",
        "row_count": 3,
        "column_count": 4,
        "persistent_reference": "Ym9tLXRva2Vu",
    }


@pytest.mark.asyncio
async def test_material_assignment_reads_and_verifies_part_material_update() -> None:
    """Material APIs use the active configuration and return no COM objects."""
    material_getter = Mock(
        return_value=(
            True,
            r"C:\\materials\\solidworks materials.sldmat",
            "Alloy Steel",
        )
    )
    material_setter = Mock(return_value=True)
    current_model = SimpleNamespace(
        GetMaterialPropertyName2=material_getter,
        SetMaterialPropertyName2=material_setter,
    )
    harness = _IOHarness(current_model)

    read = await harness.get_material_assignment("Fabrication")
    written = await harness.set_material_assignment(
        "Fabrication",
        r"C:\\materials\\solidworks materials.sldmat",
        "Alloy Steel",
    )

    expected = {
        "database_name": r"C:\\materials\\solidworks materials.sldmat",
        "material_name": "Alloy Steel",
    }
    assert read.is_success and read.data == expected
    assert written.is_success and written.data == expected
    material_setter.assert_called_once_with(
        "Fabrication", r"C:\\materials\\solidworks materials.sldmat", "Alloy Steel"
    )
    assert material_getter.call_args_list == [call("Fabrication"), call("Fabrication")]


@pytest.mark.asyncio
async def test_material_assignment_retries_getter_with_one_out_parameter() -> None:
    """Late-bound SW 2019 requires the GetMaterialPropertyName2 out parameter."""
    calls = []

    class BadParameterCount(Exception):
        hresult = -2147352562

    def material_getter(configuration, *out_parameters):
        calls.append((configuration, out_parameters))
        if not out_parameters:
            raise BadParameterCount("invalid number of parameters")
        out_parameters[0].value = r"C:\\materials\\solidworks materials.sldmat"
        return "Alloy Steel"

    current_model = SimpleNamespace(GetMaterialPropertyName2=material_getter)
    harness = _IOHarness(current_model)

    result = await harness.get_material_assignment("Fabrication")

    assert result.is_success
    assert result.data == {
        "database_name": r"C:\\materials\\solidworks materials.sldmat",
        "material_name": "Alloy Steel",
    }
    assert [len(arguments) for _, arguments in calls] == [0, 1]


@pytest.mark.asyncio
async def test_material_assignment_maps_default_to_localized_configuration() -> None:
    """The English default alias is resolved before a material write."""
    material_getter = Mock(
        return_value=(True, r"C:\\materials\\solidworks materials.sldmat", "Alloy Steel")
    )
    material_setter = Mock(return_value=True)
    current_model = SimpleNamespace(
        GetConfigurationNames=Mock(return_value=["默认"]),
        GetMaterialPropertyName2=material_getter,
        SetMaterialPropertyName2=material_setter,
    )
    harness = _IOHarness(current_model)

    result = await harness.set_material_assignment(
        "Default",
        r"C:\\materials\\solidworks materials.sldmat",
        "Alloy Steel",
    )

    assert result.is_success
    material_setter.assert_called_once_with(
        "默认", r"C:\\materials\\solidworks materials.sldmat", "Alloy Steel"
    )
    material_getter.assert_called_once_with("默认")


def test_material_assignment_normalizes_generated_wrapper_return_order() -> None:
    """SW 2019 generated pywin32 returns ``(material, database)``."""
    result = SolidWorksIOMixin._material_assignment_from_com_result(
        ("Alloy Steel", r"C:\\materials\\solidworks materials.sldmat")
    )

    assert result == {
        "database_name": r"C:\\materials\\solidworks materials.sldmat",
        "material_name": "Alloy Steel",
    }


def test_get_sw_comtypes_lib_returns_cached_value() -> None:
    """Second call returns the cached module without re-querying the registry (io.py:46-47)."""
    sentinel = object()
    with patch.object(_io_module, "_sw_comtypes_lib", sentinel):
        result = _get_sw_comtypes_lib()
    assert result is sentinel


def test_get_sw_comtypes_lib_returns_none_when_comtypes_unavailable() -> None:
    """Returns None immediately when comtypes is not installed (io.py:48-49)."""
    with (
        patch.object(_io_module, "_sw_comtypes_lib", None),
        patch.object(_io_module, "_COMTYPES_AVAILABLE", False),
    ):
        result = _get_sw_comtypes_lib()
    assert result is None
