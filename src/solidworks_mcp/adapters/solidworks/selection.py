"""Feature selection mixin for PyWin32 SolidWorks operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

from ..base import AdapterResult, AdapterResultStatus


class SolidWorksSelectionMixin:
    """Expose feature-selection and feature-list methods through a mixin."""

    if TYPE_CHECKING:  # pragma: no cover
        # Mixin protocol: declare attributes that must exist on the parent class
        currentModel: Any
        _feature_selector: Any

        def _handle_com_operation(
            self, operation_name: str, operation: Callable[..., Any], *args: Any
        ) -> AdapterResult[Any]: ...

    async def list_features(
        self, include_suppressed: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        if not self.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )
        return self._handle_com_operation(
            "list_features", self._feature_selector.list_features, include_suppressed
        )

    @staticmethod
    def _normalize_feature_name(raw_name: str | None) -> str:
        return str(raw_name or "").strip().strip('"').casefold()

    def _build_feature_candidate_names(
        self, feature_name: str, target_doc: Any
    ) -> list[str]:
        return cast(
            list[str],
            self._feature_selector.build_feature_candidate_names(
                feature_name, target_doc
            ),
        )

    def _try_select_by_extension(
        self,
        target_doc: Any,
        candidate_names: list[str],
        feature_name: str,
    ) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._feature_selector.try_select_by_extension(
                target_doc, candidate_names, feature_name
            ),
        )

    def _try_select_by_component(
        self,
        target_doc: Any,
        candidate_names: list[str],
        feature_name: str,
    ) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._feature_selector.try_select_by_component(
                target_doc, candidate_names, feature_name
            ),
        )

    def _try_select_by_feature_tree(
        self,
        target_doc: Any,
        feature_name: str,
        candidate_names: list[str],
    ) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._feature_selector.try_select_by_feature_tree(
                target_doc, feature_name, candidate_names
            ),
        )

    async def select_feature(self, feature_name: str) -> AdapterResult[dict[str, Any]]:
        if not self.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        return self._handle_com_operation(
            "select_feature", self._feature_selector.select_feature, feature_name
        )

    async def get_selection_state(self) -> AdapterResult[dict[str, Any]]:
        """Read the current selection without exposing COM objects to callers."""
        if not self.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _read() -> dict[str, Any]:
            manager = self.currentModel.SelectionManager
            count = int(
                self._attempt(
                    lambda: manager.GetSelectedObjectCount2(-1), default=0
                )
                or 0
            )
            entries: list[dict[str, Any]] = []
            for index in range(1, min(count, 100) + 1):
                object_type = int(
                    self._attempt(
                        lambda i=index: manager.GetSelectedObjectType3(i, -1),
                        default=0,
                    )
                    or 0
                )
                mark = int(
                    self._attempt(
                        lambda i=index: manager.GetSelectedObjectMark(i), default=0
                    )
                    or 0
                )
                selected = self._attempt(
                    lambda i=index: manager.GetSelectedObject6(i, -1), default=None
                )
                component = self._attempt(
                    lambda i=index: manager.GetSelectedObjectsComponent3(i, -1), default=None
                )
                name = self._attempt(
                    lambda: str(getattr(selected, "Name", "") or ""), default=""
                )
                component_name = self._attempt(
                    lambda: str(getattr(component, "Name2", "") or ""), default=""
                )
                entries.append(
                    {
                        "index": index,
                        "object_type": object_type,
                        "mark": mark,
                        "name": name or None,
                        "component_name": component_name or None,
                    }
                )
            return {
                "count": count,
                "truncated": count > len(entries),
                "items": entries,
            }

        return self._handle_com_operation("get_selection_state", _read)
