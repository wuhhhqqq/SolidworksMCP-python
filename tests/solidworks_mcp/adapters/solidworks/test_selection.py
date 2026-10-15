"""Tests for the SolidWorksSelectionMixin."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks.selection import SolidWorksSelectionMixin


class _SelectionHarness(SolidWorksSelectionMixin):
    """Minimal harness to exercise the mixin methods."""

    def __init__(self, *, has_model: bool) -> None:
        self.currentModel = object() if has_model else None
        self._feature_selector = SimpleNamespace(
            list_features=lambda _include: [{"name": "Feat1"}],
            select_feature=lambda _name: {"selected": True},
        )

    def _handle_com_operation(self, _name, operation, *args):
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=operation(*args))

    @staticmethod
    def _attempt(operation, default=None):
        try:
            return operation()
        except Exception:
            return default


@pytest.mark.asyncio
async def test_list_features_requires_active_model() -> None:
    """list_features should error when no model is active."""
    # Validate the error branch when currentModel is missing.
    harness = _SelectionHarness(has_model=False)
    result = await harness.list_features()
    assert result.status == AdapterResultStatus.ERROR


@pytest.mark.asyncio
async def test_select_feature_requires_active_model() -> None:
    """select_feature should error when no model is active."""
    # Validate the error branch when currentModel is missing.
    harness = _SelectionHarness(has_model=False)
    result = await harness.select_feature("Feat1")
    assert result.status == AdapterResultStatus.ERROR


@pytest.mark.asyncio
async def test_list_and_select_feature_success() -> None:
    """list_features/select_feature should call the selector when model exists."""
    # Exercise the success path when currentModel is set.
    harness = _SelectionHarness(has_model=True)
    list_result = await harness.list_features()
    select_result = await harness.select_feature("Feat1")
    assert list_result.is_success
    assert list_result.data == [{"name": "Feat1"}]
    assert select_result.is_success
    assert select_result.data == {"selected": True}


@pytest.mark.asyncio
async def test_get_selection_state_returns_only_serializable_summary() -> None:
    """Selection state must not leak COM objects and must preserve order/marks."""
    harness = _SelectionHarness(has_model=True)
    selected = SimpleNamespace(Name="Edge<1>")
    component = SimpleNamespace(Name2="Bracket-1")
    manager = Mock(
        GetSelectedObjectCount2=Mock(return_value=1),
        GetSelectedObjectType3=Mock(return_value=21),
        GetSelectedObjectMark=Mock(return_value=4),
        GetSelectedObject6=Mock(return_value=selected),
        GetSelectedObjectsComponent3=Mock(return_value=component),
    )
    harness.currentModel = SimpleNamespace(SelectionManager=manager)

    result = await harness.get_selection_state()

    assert result.is_success
    assert result.data == {
        "count": 1,
        "truncated": False,
        "items": [
            {
                "index": 1,
                "object_type": 21,
                "mark": 4,
                "name": "Edge<1>",
                "component_name": "Bracket-1",
            }
        ],
    }


@pytest.mark.asyncio
async def test_get_selection_state_keeps_reading_when_one_com_accessor_fails() -> None:
    """A version-specific accessor failure should not abort an otherwise useful read."""
    harness = _SelectionHarness(has_model=True)
    manager = Mock(
        GetSelectedObjectCount2=Mock(return_value=1),
        GetSelectedObjectType3=Mock(side_effect=RuntimeError("unsupported")),
        GetSelectedObjectMark=Mock(side_effect=RuntimeError("unsupported")),
        GetSelectedObject6=Mock(return_value=None),
        GetSelectedObjectsComponent3=Mock(return_value=None),
    )
    harness.currentModel = SimpleNamespace(SelectionManager=manager)
    result = await harness.get_selection_state()

    assert result.is_success
    assert result.data["items"] == [
        {
            "index": 1,
            "object_type": 0,
            "mark": 0,
            "name": None,
            "component_name": None,
        }
    ]
