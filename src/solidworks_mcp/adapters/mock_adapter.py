"""Mock SolidWorks adapter for testing and development.

This adapter simulates SolidWorks operations for testing on non-Windows platforms or
when SolidWorks is not available.
"""

from __future__ import annotations

import asyncio
import base64
import math
import random
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..exceptions import SolidWorksOperationError
from .base import (
    AdapterHealth,
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    FeatureCircularPatternParameters,
    FeatureLinearPatternParameters,
    FeatureMirrorParameters,
    HoleWizardParameters,
    LoftParameters,
    MassProperties,
    OriginPlaneParameters,
    ReferenceAxisParameters,
    ReferencePlaneParameters,
    RevolveParameters,
    SolidWorksAdapter,
    SolidWorksFeature,
    SolidWorksModel,
    SweepParameters,
    ThreadedHoleParameters,
)
from .solidworks.sketch import RELATION_NAME_MAP


class _BoolCallable:
    """Compatibility shim that behaves as both bool and callable.

    Args:
        getter (Callable[[], bool]): The getter value.

    Attributes:
        _getter (Any): The getter value.
    """

    def __init__(self, getter: Callable[[], bool]) -> None:
        """Initialize the bool callable.

        Args:
            getter (Callable[[], bool]): The getter value.

        Returns:
            None: None.
        """
        self._getter = getter

    def __call__(self) -> bool:
        """Build internal call.

        Returns:
            bool: True if call, otherwise False.
        """
        return bool(self._getter())

    def __bool__(self) -> bool:
        """Build internal bool.

        Returns:
            bool: True if bool, otherwise False.
        """
        return bool(self._getter())


class MockSolidWorksAdapter(SolidWorksAdapter):
    """Mock adapter that simulates SolidWorks operations.

    Args:
        config (object | None): Configuration values for the operation. Defaults to None.

    Attributes:
        _connected (Any): The connected value.
        _delays (Any): The delays value.
        _is_connected_proxy (Any): The is connected proxy value.
        _operation_count (Any): The operation count value.
        _simulate_errors (Any): The simulate errors value.
    """

    def __init__(self, config: object | None = None) -> None:
        """Initialize the mock solid works adapter.

        Args:
            config (object | None): Configuration values for the operation. Defaults to None.

        Returns:
            None: None.
        """
        super().__init__(config)
        cfg: dict[str, Any] = dict(self.config_dict)
        self._connected = False
        self._current_model: SolidWorksModel | None = None
        self._models: dict[str, SolidWorksModel] = {}
        self._assembly_components: dict[str, list[dict[str, str]]] = {}
        self._assembly_mates: dict[str, list[dict[str, str]]] = {}
        self._drawing_views: dict[str, list[dict[str, str]]] = {}
        self._drawing_sheets: dict[str, list[str]] = {}
        self._active_drawing_sheets: dict[str, str] = {}
        self._drawing_boms: dict[str, list[dict[str, Any]]] = {}
        self._drawing_revision_tables: dict[str, list[dict[str, Any]]] = {}
        self._drawing_annotations: dict[str, list[dict[str, str]]] = {}
        self._sheet_metal_flattened: dict[str, bool] = {}
        self._sheet_metal_k_factors: dict[str, float] = {}
        self._configurations: dict[str, list[str]] = {}
        self._configuration_descriptions: dict[tuple[str, str], str] = {}
        self._custom_properties: dict[str, dict[str, dict[str, str]]] = {}
        self._material_assignments: dict[tuple[str, str], dict[str, str]] = {}
        self._features: dict[str, SolidWorksFeature] = {}
        self._sketches: dict[str, str] = {}
        self._current_sketch: str | None = None
        # Tracks IDs returned by add_line/add_arc/add_circle/add_centerline/
        # add_rectangle/add_spline so add_sketch_constraint can validate
        # entity1/entity2 the same way the real adapter validates against
        # its sketch-entity registry.
        self._sketch_entity_ids: set[str] = set()
        self._dimensions: dict[str, float] = {}
        self._equations: dict[str, list[dict[str, Any]]] = {}
        self._operation_count = 0

        # Configurable simulation delays (in seconds)
        self._delays = {
            "connect": cfg.get("mock_connect_delay", 0.1),
            "model_operation": cfg.get("mock_model_delay", 0.02),
            "feature_operation": cfg.get("mock_feature_delay", 0.03) if cfg else 0.03,
            "sketch_operation": cfg.get("mock_sketch_delay", 0.1),
        }
        self._is_connected_proxy = _BoolCallable(lambda: self._connected)
        self._simulate_errors = bool(cfg.get("simulate_errors", False))

    def __getattribute__(self, name: str) -> Any:
        """Build internal getattribute.

        Args:
            name (str): The name value.

        Returns:
            Any: The result produced by the operation.
        """
        if name == "is_connected":
            return object.__getattribute__(self, "_is_connected_proxy")
        return object.__getattribute__(self, name)

    async def connect(self) -> None:
        """Mock connection to SolidWorks.

        Returns:
            None: None.
        """
        await asyncio.sleep(self._delays["connect"])
        self._connected = True

        # Initialize some sample data
        self._dimensions = {
            "D1@Sketch1": 10.0,
            "D2@Sketch1": 20.0,
            "D1@Boss-Extrude1": 50.0,
        }

    async def disconnect(self) -> None:
        """Mock disconnection from SolidWorks.

        Returns:
            None: None.
        """
        self._connected = False
        self._current_model = None
        self._models.clear()
        self._assembly_components.clear()
        self._drawing_views.clear()
        self._drawing_sheets.clear()
        self._active_drawing_sheets.clear()
        self._drawing_boms.clear()
        self._drawing_revision_tables.clear()
        self._drawing_annotations.clear()
        self._sheet_metal_flattened.clear()
        self._custom_properties.clear()
        self._equations.clear()
        self._features.clear()
        self._sketches.clear()
        self._current_sketch = None

    def is_connected(self) -> bool:
        """Check if mock connection is active.

        Returns:
            bool: True if connected, otherwise False.
        """
        return self._connected

    async def health_check(self) -> AdapterHealth:
        """Get mock health status.

        Returns:
            AdapterHealth: The result produced by the operation.
        """
        error_count = int(self._metrics["errors_count"])
        success_count = int(
            self._metrics["operations_count"] - self._metrics["errors_count"]
        )
        return AdapterHealth(
            healthy=self._connected,
            last_check=datetime.now(),
            error_count=error_count,
            success_count=success_count,
            average_response_time=self._metrics["average_response_time"],
            connection_status="connected" if self._connected else "disconnected",
            metrics={
                "adapter_type": "mock",
                "operations_count": self._operation_count,
                "models_count": len(self._models),
                "features_count": len(self._features),
                "sketches_count": len(self._sketches),
            },
        )

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        """Mock opening a SolidWorks model.

        Args:
            file_path (str): Path to the target file.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.

        Raises:
            SolidWorksOperationError: Simulated adapter failure.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        if self._simulate_errors:
            raise SolidWorksOperationError("Simulated adapter failure")

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        # Determine model type from file extension
        file_path_lower = file_path.lower()
        if file_path_lower.endswith((".sldprt", ".step", ".stp", ".iges", ".igs")):
            model_type = "Part"
        elif file_path_lower.endswith(".sldasm"):
            model_type = "Assembly"
        elif file_path_lower.endswith(".slddrw"):
            model_type = "Drawing"
        else:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Unsupported file type: {file_path}",
            )

        # Create mock model
        model_name = file_path.split("/")[-1].split("\\")[-1]
        model = SolidWorksModel(
            path=file_path,
            name=model_name,
            type=model_type,
            is_active=True,
            configuration="Default",
            properties={
                "created": datetime.now().isoformat(),
                "model_id": model_name or f"Part{len(self._models) + 1}",
                "file_size": random.randint(100000, 5000000),
            },
        )

        self._current_model = model
        self._models[file_path] = model

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def activate_model(
        self, name: str, path: str | None = None
    ) -> AdapterResult[None]:
        """Activate one previously opened mock model by its trusted identity."""
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        normalized_path = path.casefold() if path else None
        if normalized_path:
            for model in self._models.values():
                if model.path.casefold() == normalized_path:
                    self._current_model = model
                    return AdapterResult(status=AdapterResultStatus.SUCCESS)
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Requested model is not open in this adapter",
            )
        for model in self._models.values():
            if model.name == name:
                self._current_model = model
                return AdapterResult(status=AdapterResultStatus.SUCCESS)
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Requested model is not open in this adapter",
        )

    async def close_model(self, save: bool = False) -> AdapterResult[None]:
        """Mock closing the current model.

        Args:
            save (bool): The save value. Defaults to False.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.WARNING, error="No active model to close"
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        if save:
            # Simulate save operation
            await asyncio.sleep(self._delays["model_operation"])

        self._current_model = None

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=None,
            execution_time=self._delays["model_operation"],
        )

    async def get_model_info(self) -> AdapterResult[dict[str, Any]]:
        """Mock metadata for the currently active model.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        model = self._current_model
        feature_count = len(self._features)
        info = {
            "title": model.name,
            "path": model.path,
            "type": model.type,
            "configuration": model.configuration or "Default",
            "is_dirty": False,
            "feature_count": feature_count,
            "rebuild_status": 0,
        }

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=info,
            execution_time=self._delays["model_operation"] / 2,
        )

    async def list_open_models(self) -> AdapterResult[list[dict[str, Any]]]:
        """List documents currently retained by the deterministic mock session."""
        if not self._connected:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Not connected")
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {
                    "title": model.name,
                    "path": model.path,
                    "type": model.type,
                    "configuration": model.configuration or "Default",
                    "is_active": model is self._current_model,
                }
                for model in self._models.values()
            ],
        )

    async def list_features(
        self, include_suppressed: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Mock feature tree listing for the active model.

        Args:
            include_suppressed (bool): The include suppressed value. Defaults to False.

        Returns:
            AdapterResult[list[dict[str, Any]]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["feature_operation"] / 2)
        self._operation_count += 1

        # Seed realistic feature names for empty mock state.
        if not self._features:
            seeded = [
                {"name": "Origin", "type": "OriginProfileFeature", "suppressed": False},
                {"name": "Front Plane", "type": "RefPlane", "suppressed": False},
                {"name": "Right Plane", "type": "RefPlane", "suppressed": False},
                {"name": "Top Plane", "type": "RefPlane", "suppressed": False},
                {"name": "Sketch1", "type": "ProfileFeature", "suppressed": False},
            ]
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data=seeded,
                execution_time=self._delays["feature_operation"] / 2,
            )

        feature_rows: list[dict[str, Any]] = []
        for i, feature in enumerate(self._features.values()):
            row = {
                "name": feature.name,
                "type": feature.type,
                "suppressed": bool((feature.properties or {}).get("suppressed", False)),
                "position": i,
            }
            if include_suppressed or not row["suppressed"]:
                feature_rows.append(row)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature_rows,
            execution_time=self._delays["feature_operation"] / 2,
        )

    async def select_feature(self, feature_name: str) -> AdapterResult[dict[str, Any]]:
        """Mock feature selection/highlight — succeeds without COM side-effects.

        Args:
            feature_name (str): The feature name value.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )
        await asyncio.sleep(self._delays["feature_operation"] / 4)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "selected": True,
                "feature_name": feature_name,
                "entity_type": "mock",
            },
            execution_time=self._delays["feature_operation"] / 4,
        )

    async def list_configurations(self) -> AdapterResult[list[str]]:
        """Mock configuration listing for the active model.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        active = self._current_model.configuration or "Default"
        configs = self._configurations.setdefault(self._current_model.path, ["Default"])
        if not any(config.casefold() == active.casefold() for config in configs):
            configs.append(active)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=list(configs),
            execution_time=self._delays["model_operation"] / 2,
        )

    async def get_selection_state(self) -> AdapterResult[dict[str, Any]]:
        """Return an empty deterministic selection set for offline workflows."""
        if not self._current_model:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"count": 0, "truncated": False, "items": []},
        )

    async def create_configuration(
        self, configuration_name: str, description: str = ""
    ) -> AdapterResult[str]:
        """Mock creation and activation of one independent configuration."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        configurations = self._configurations.setdefault(
            self._current_model.path, ["Default"]
        )
        if any(
            configuration.casefold() == configuration_name.casefold()
            for configuration in configurations
        ):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Configuration already exists",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        configurations.append(configuration_name)
        self._configuration_descriptions[
            (self._current_model.path, configuration_name.casefold())
        ] = description
        self._current_model.configuration = configuration_name
        self._models[self._current_model.path] = self._current_model
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=configuration_name,
            execution_time=self._delays["model_operation"] / 2,
        )

    async def delete_configuration(self, configuration_name: str) -> AdapterResult[str]:
        """Mock deletion of a non-active configuration."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        configurations = self._configurations.setdefault(
            self._current_model.path, ["Default"]
        )
        matching_name = next(
            (
                configuration
                for configuration in configurations
                if configuration.casefold() == configuration_name.casefold()
            ),
            None,
        )
        if matching_name is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Configuration does not exist",
            )
        if (
            matching_name.casefold()
            == (self._current_model.configuration or "Default").casefold()
        ):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active configuration cannot be deleted",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        configurations.remove(matching_name)
        self._configuration_descriptions.pop(
            (self._current_model.path, matching_name.casefold()), None
        )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=matching_name,
            execution_time=self._delays["model_operation"] / 2,
        )

    async def activate_configuration(
        self, configuration_name: str
    ) -> AdapterResult[str]:
        """Mock switching to a configuration already available on the model."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        active = self._current_model.configuration or "Default"
        configurations = self._configurations.setdefault(
            self._current_model.path, ["Default"]
        )
        if not any(config.casefold() == active.casefold() for config in configurations):
            configurations.append(active)
        matching_name = next(
            (
                configuration
                for configuration in configurations
                if configuration.casefold() == configuration_name.casefold()
            ),
            None,
        )
        if matching_name is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Configuration does not exist",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        self._current_model.configuration = matching_name
        self._models[self._current_model.path] = self._current_model
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=matching_name,
            execution_time=self._delays["model_operation"] / 2,
        )

    async def create_part(
        self, name: str | None = None, units: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Mock creating a new part.

        Args:
            name (str | None): The name value. Defaults to None.
            units (str | None): The units value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        model_id = name or f"Part{len(self._models) + 1}"
        model = SolidWorksModel(
            path=f"Mock://{model_id}.sldprt",
            name=f"{model_id}",
            type="Part",
            is_active=True,
            configuration="Default",
            properties={
                "created": datetime.now().isoformat(),
                "mock": True,
                "units": units or "mm",
            },
        )

        self._current_model = model
        self._models[model.path] = model
        self._sheet_metal_flattened[model.path] = False

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def create_assembly(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Mock creating a new assembly.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        model_id = name or f"Assembly{len(self._models) + 1}"
        model = SolidWorksModel(
            path=f"Mock://{model_id}.sldasm",
            name=f"{model_id}",
            type="Assembly",
            is_active=True,
            configuration="Default",
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        self._current_model = model
        self._models[model.path] = model
        self._assembly_components[model.path] = []

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def create_drawing(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Mock creating a new drawing.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        model_id = name or f"Drawing{len(self._models) + 1}"
        model = SolidWorksModel(
            path=f"Mock://{model_id}.slddrw",
            name=f"{model_id}",
            type="Drawing",
            is_active=True,
            configuration="Default",
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        self._current_model = model
        self._models[model.path] = model
        self._drawing_views[model.path] = []
        self._drawing_sheets[model.path] = ["Sheet1", "Sheet2"]
        self._active_drawing_sheets[model.path] = "Sheet1"
        self._drawing_boms[model.path] = []
        self._drawing_revision_tables[model.path] = []
        self._drawing_annotations[model.path] = []

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def insert_assembly_component(
        self,
        component_path: str,
        x_m: float,
        y_m: float,
        z_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock insertion of one saved part into the active assembly."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not an assembly",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        components = self._assembly_components.setdefault(self._current_model.path, [])
        component_id = f"component_{len(components) + 1}"
        component = {
            "component_id": component_id,
            "display_name": f"{Path(component_path).stem}-{len(components) + 1}",
            "document_type": "part",
        }
        components.append(component)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=dict(component),
            execution_time=self._delays["model_operation"] / 2,
        )

    async def list_assembly_components(self) -> AdapterResult[list[dict[str, Any]]]:
        """Mock component listing for the active assembly."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not an assembly",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        components = self._assembly_components.get(self._current_model.path, [])
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[dict(component) for component in components],
            execution_time=self._delays["model_operation"] / 2,
        )

    async def check_assembly_interference(
        self,
        include_coincident: bool,
        include_multibody_part_interferences: bool,
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Return an empty deterministic interference result for a mock assembly."""
        _ = include_coincident, include_multibody_part_interferences
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not an assembly",
            )
        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[],
            execution_time=self._delays["model_operation"] / 2,
        )

    async def get_assembly_component(
        self, component_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock exact component lookup in the active assembly."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not an assembly",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        components = self._assembly_components.get(self._current_model.path, [])
        component = next(
            (item for item in components if item["display_name"] == component_name),
            None,
        )
        if component is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Assembly component '{component_name}' was not found",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=dict(component),
            execution_time=self._delays["model_operation"] / 2,
        )

    async def list_assembly_mate_entities(
        self,
        component_id: str | None = None,
        entity_types: tuple[str, ...] = ("face", "edge"),
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Return deterministic, COM-free geometry tokens for mock assembly components."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not an assembly",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        components = self._assembly_components.get(self._current_model.path, [])
        entities: list[dict[str, str]] = []
        for component in components:
            if component_id is not None and component["component_id"] != component_id:
                continue
            for entity_type, count in (("face", 2), ("edge", 1)):
                if entity_type not in entity_types:
                    continue
                for position in range(1, count + 1):
                    encoded = base64.b64encode(
                        (
                            f"{self._current_model.path}|{component['component_id']}|"
                            f"{entity_type}|{position}"
                        ).encode()
                    ).decode("ascii")
                    entities.append(
                        {
                            "component_id": component["component_id"],
                            "entity_type": entity_type,
                            "display_name": (
                                f"{component['display_name']} {entity_type.title()} "
                                f"{position}"
                            ),
                            "persistent_reference": encoded,
                        }
                    )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=entities,
            execution_time=self._delays["model_operation"] / 2,
        )

    async def create_assembly_mate(
        self,
        first_persistent_reference: str,
        second_persistent_reference: str,
        *,
        mate_type: str,
        alignment: str,
        flip: bool,
        distance_m: float,
        angle_radians: float,
        for_positioning_only: bool,
        lock_rotation: bool,
    ) -> AdapterResult[dict[str, Any]]:
        """Create and record a deterministic mock mate from current geometry tokens."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not an assembly",
            )
        if mate_type not in {
            "coincident",
            "concentric",
            "parallel",
            "perpendicular",
            "tangent",
            "distance",
            "angle",
        } or alignment not in {"aligned", "anti_aligned"}:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Unsupported assembly mate type or alignment",
            )

        available = await self.list_assembly_mate_entities()
        if not available.is_success:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Unable to enumerate assembly mate entities",
            )
        tokens = {
            str(entity["persistent_reference"])
            for entity in available.data or []
            if isinstance(entity, dict)
        }
        if (
            first_persistent_reference not in tokens
            or second_persistent_reference not in tokens
        ):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Assembly mate entity can no longer be resolved",
            )

        self._operation_count += 1
        mates = self._assembly_mates.setdefault(self._current_model.path, [])
        mate = {
            "mate_type": mate_type,
            "display_name": f"{mate_type.title()} Mate {len(mates) + 1}",
        }
        mates.append(mate)
        _ = (
            flip,
            distance_m,
            angle_radians,
            for_positioning_only,
            lock_rotation,
        )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=dict(mate),
            execution_time=self._delays["model_operation"] / 2,
        )

    async def list_assembly_mates(self) -> AdapterResult[list[dict[str, Any]]]:
        """List deterministic mock mates with server-only persistent tokens."""
        if not self._current_model or self._current_model.type != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not an assembly",
            )
        mates = self._assembly_mates.get(self._current_model.path, [])
        rows = []
        for index, mate in enumerate(mates, start=1):
            token = base64.b64encode(
                f"{self._current_model.path}|mate|{index}".encode()
            ).decode("ascii")
            rows.append({**mate, "persistent_reference": token})
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=rows)

    async def delete_assembly_mate(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete exactly one mock mate after validating its issued token."""
        listed = await self.list_assembly_mates()
        if not listed.is_success:
            return AdapterResult(status=listed.status, error=listed.error)
        mates = self._assembly_mates.get(self._current_model.path, [])  # type: ignore[union-attr]
        for index, mate in enumerate(listed.data or []):
            if mate.get("persistent_reference") == persistent_reference:
                removed = mates.pop(index)
                self._operation_count += 1
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS, data=dict(removed)
                )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Assembly mate can no longer be resolved",
        )

    async def create_drawing_view(
        self,
        model_path: str,
        model_view_name: str,
        x_m: float,
        y_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock creation of a standard model view in the active drawing."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        views = self._drawing_views.setdefault(self._current_model.path, [])
        view = {
            "view_id": f"Drawing View{len(views) + 1}",
            "display_name": f"{model_view_name.lstrip('*')} View",
            "position_m": {"x": x_m, "y": y_m},
            "scale_decimal": 1.0,
        }
        views.append(view)
        _ = model_path, x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"view_id": view["view_id"], "display_name": view["display_name"]},
            execution_time=self._delays["model_operation"] / 2,
        )

    async def list_drawing_views(self) -> AdapterResult[list[dict[str, Any]]]:
        """Mock model-view listing for the active drawing."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        views = self._drawing_views.get(self._current_model.path, [])
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {"view_id": view["view_id"], "display_name": view["display_name"]}
                for view in views
            ],
            execution_time=self._delays["model_operation"] / 2,
        )

    async def activate_drawing_view(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock activation of one existing drawing view."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        views = self._drawing_views.get(self._current_model.path, [])
        view = next((candidate for candidate in views if candidate["view_id"] == view_id), None)
        if view is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"view_id": view["view_id"], "display_name": view["display_name"]},
        )

    async def list_drawing_sheets(self) -> AdapterResult[list[dict[str, Any]]]:
        """Mock read-only enumeration of active drawing sheets."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        current_path = self._current_model.path
        active_sheet = self._active_drawing_sheets.get(current_path, "Sheet1")
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {"name": name, "is_active": name == active_sheet}
                for name in self._drawing_sheets.get(current_path, ["Sheet1"])
            ],
        )

    async def activate_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock activation of one sheet already present in the drawing."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        current_path = self._current_model.path
        sheets = self._drawing_sheets.get(current_path, ["Sheet1"])
        if sheet_name not in sheets:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing sheet no longer exists",
            )
        self._active_drawing_sheets[current_path] = sheet_name
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": sheet_name, "is_active": True},
        )

    async def create_drawing_sheet(
        self,
        sheet_name: str,
        paper_size: str,
        width_m: float,
        height_m: float,
        first_angle_projection: bool,
        scale_numerator: int,
        scale_denominator: int,
        zone_rows: int,
        zone_columns: int,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock creation of one new active drawing sheet."""
        _ = (paper_size, width_m, height_m, first_angle_projection,
             scale_numerator, scale_denominator, zone_rows, zone_columns)
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Active model is not a drawing")
        current_path = self._current_model.path
        sheets = self._drawing_sheets.setdefault(current_path, ["Sheet1"])
        if sheet_name in sheets:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="A drawing sheet with this name already exists")
        sheets.append(sheet_name)
        self._active_drawing_sheets[current_path] = sheet_name
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": sheet_name, "is_active": True, "created": True},
        )

    async def delete_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock deletion while preserving the required final drawing sheet."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        current_path = self._current_model.path
        sheets = self._drawing_sheets.get(current_path, ["Sheet1"])
        if sheet_name not in sheets:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing sheet no longer exists",
            )
        if len(sheets) <= 1:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="SolidWorks requires at least one drawing sheet",
            )
        sheets.remove(sheet_name)
        if self._active_drawing_sheets.get(current_path) == sheet_name:
            self._active_drawing_sheets[current_path] = sheets[0]
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": sheet_name, "deleted": True},
        )

    async def rename_drawing_sheet(
        self, sheet_name: str, new_sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock renaming of one sheet while retaining its active-state mapping."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        current_path = self._current_model.path
        sheets = self._drawing_sheets.get(current_path, ["Sheet1"])
        if sheet_name not in sheets:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing sheet no longer exists",
            )
        if new_sheet_name in sheets:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="A drawing sheet with this name already exists",
            )
        sheets[sheets.index(sheet_name)] = new_sheet_name
        if self._active_drawing_sheets.get(current_path) == sheet_name:
            self._active_drawing_sheets[current_path] = new_sheet_name
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": new_sheet_name, "renamed_from": sheet_name},
        )

    async def reorder_drawing_sheets(
        self, sheet_names: list[str]
    ) -> AdapterResult[list[dict[str, Any]]]:
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Active model is not a drawing")
        current_path = self._current_model.path
        sheets = self._drawing_sheets.get(current_path, ["Sheet1"])
        if {name.casefold() for name in sheet_names} != {name.casefold() for name in sheets}:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Sheet order must contain every current drawing sheet exactly once")
        self._drawing_sheets[current_path] = list(sheet_names)
        self._operation_count += 1
        active = self._active_drawing_sheets.get(current_path, "Sheet1")
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=[{"name": name, "is_active": name == active} for name in sheet_names])

    async def get_drawing_view_outline(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Return a deterministic drawing-sheet outline for one mock view."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        views = self._drawing_views.get(self._current_model.path, [])
        position = next(
            (index for index, view in enumerate(views) if view["view_id"] == view_id),
            None,
        )
        if position is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )
        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        left_m = 0.02 + position * 0.06
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "view_id": view_id,
                "left_m": left_m,
                "bottom_m": 0.02,
                "right_m": left_m + 0.05,
                "top_m": 0.07,
            },
            execution_time=self._delays["model_operation"] / 2,
        )

    async def create_projected_drawing_view(
        self, source_view_id: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Mock projected-view creation from an existing active drawing view."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )

        views = self._drawing_views.setdefault(self._current_model.path, [])
        source_view = next(
            (view for view in views if view["view_id"] == source_view_id), None
        )
        if source_view is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        view = {
            "view_id": f"Drawing View{len(views) + 1}",
            "display_name": f"Projected {source_view['display_name']}",
        }
        views.append(view)
        _ = x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"view_id": view["view_id"], "display_name": view["display_name"]},
            execution_time=self._delays["model_operation"] / 2,
        )

    async def get_configuration(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one mock configuration with its stored description."""
        if not self._current_model:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
        configurations = self._configurations.setdefault(
            self._current_model.path, ["Default"]
        )
        name = next(
            (
                candidate
                for candidate in configurations
                if candidate.casefold() == configuration_name.casefold()
            ),
            None,
        )
        if name is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Configuration does not exist",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": name,
                "description": self._configuration_descriptions.get(
                    (self._current_model.path, name.casefold()), ""
                ),
                "is_active": name.casefold()
                == (self._current_model.configuration or "Default").casefold(),
            },
        )

    async def update_drawing_view_layout(
        self,
        view_id: str,
        x_m: float | None,
        y_m: float | None,
        scale_decimal: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock movement and scaling of one drawing view."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Active model is not a drawing")
        views = self._drawing_views.get(self._current_model.path, [])
        view = next((candidate for candidate in views if candidate["view_id"] == view_id), None)
        if view is None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="The requested drawing view no longer exists")
        if x_m is not None and y_m is not None:
            view["position_m"] = {"x": x_m, "y": y_m}
        if scale_decimal is not None:
            view["scale_decimal"] = scale_decimal
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data={
            "view_id": view["view_id"],
            "display_name": view["display_name"],
            "position_m": view.get("position_m"),
            "scale_decimal": view.get("scale_decimal"),
        })

    async def align_drawing_views(
        self,
        view_ids: list[str],
        direction: str,
        gap_m: float,
        start_x_m: float | None,
        start_y_m: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Arrange mock views deterministically using fixed outline dimensions."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        views = self._drawing_views.get(self._current_model.path, [])
        selected = [next((view for view in views if view["view_id"] == view_id), None) for view_id in view_ids]
        if any(view is None for view in selected):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )
        width_m, height_m = 0.06, 0.06
        first_left, first_bottom = 0.02, 0.03
        first_center_x = first_left + width_m / 2
        first_center_y = first_bottom + height_m / 2
        cursor = (start_x_m if start_x_m is not None else first_left)
        baseline = (start_y_m if start_y_m is not None else first_center_y)
        placements: list[dict[str, Any]] = []
        for view in selected:
            assert view is not None
            if direction == "horizontal":
                center_x, center_y = cursor + width_m / 2, baseline
                cursor += width_m + gap_m
            else:
                center_x, center_y = baseline, cursor + height_m / 2
                cursor += height_m + gap_m
            view["position_m"] = {"x": center_x, "y": center_y}
            placements.append(
                {
                    "view_id": view["view_id"],
                    "display_name": view["display_name"],
                    "position_m": {"x": center_x, "y": center_y},
                    "outline_m": {
                        "left": center_x - width_m / 2,
                        "bottom": center_y - height_m / 2,
                        "right": center_x + width_m / 2,
                        "top": center_y + height_m / 2,
                    },
                }
            )
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "direction": direction,
                "gap_m": gap_m,
                "views": placements,
            },
        )

    async def rebuild_drawing(self) -> AdapterResult[dict[str, Any]]:
        """Mock rebuild/save confirmation for the current drawing."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"rebuilt": True, "saved": True},
        )

    async def create_auxiliary_drawing_view(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Mock auxiliary-view creation from a listed drawing edge reference."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        if not persistent_reference:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="A drawing edge reference is required",
            )
        views = self._drawing_views.setdefault(self._current_model.path, [])
        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1
        view = {
            "view_id": f"Auxiliary View{len(views) + 1}",
            "display_name": "Auxiliary View",
        }
        views.append(view)
        _ = x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=dict(view),
            execution_time=self._delays["model_operation"] / 2,
        )

    async def create_section_drawing_view(
        self,
        source_view_id: str,
        cut_start_x_m: float,
        cut_start_y_m: float,
        cut_end_x_m: float,
        cut_end_y_m: float,
        x_m: float,
        y_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Create a deterministic section view from a current mock drawing view."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        views = self._drawing_views.setdefault(self._current_model.path, [])
        source = next(
            (view for view in views if view["view_id"] == source_view_id), None
        )
        if source is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )
        _ = cut_start_x_m, cut_start_y_m, cut_end_x_m, cut_end_y_m, x_m, y_m
        self._operation_count += 1
        view = {
            "view_id": f"Section View{len(views) + 1}",
            "display_name": f"Section {source['display_name']}",
        }
        views.append(view)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(view))

    async def get_sheet_metal_info(self) -> AdapterResult[dict[str, Any]]:
        """Return deterministic sheet-metal state for a mock part document."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )
        await asyncio.sleep(self._delays["model_operation"] / 4)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "is_sheet_metal": True,
                "is_flattened": self._sheet_metal_flattened.get(
                    self._current_model.path, False
                ),
                "thickness_mm": 1.5,
                "flat_pattern_feature_name": "Flat-Pattern1",
            },
            execution_time=self._delays["model_operation"] / 4,
        )

    async def set_sheet_metal_flat_state(
        self, flattened: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Set deterministic flat state for a mock sheet-metal part."""
        info = await self.get_sheet_metal_info()
        if not info.is_success or not self._current_model:
            return AdapterResult(status=info.status, error=info.error)
        self._sheet_metal_flattened[self._current_model.path] = flattened
        updated = dict(info.data or {})
        updated["is_flattened"] = flattened
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=updated,
            execution_time=self._delays["model_operation"] / 4,
        )

    async def export_flat_pattern_dxf(
        self, file_path: str, model_path: str, sheet_metal_options: int
    ) -> AdapterResult[dict[str, Any]]:
        """Write a minimal mock DXF for a sheet-metal flat-pattern export."""
        info = await self.get_sheet_metal_info()
        if not info.is_success:
            return AdapterResult(status=info.status, error=info.error)
        target = Path(file_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n",
                encoding="ascii",
            )
        except OSError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "sheet_metal": info.data,
                "sheet_metal_options": sheet_metal_options,
                "source_model_path": model_path,
            },
            execution_time=self._delays["model_operation"] / 2,
        )

    def _mock_drawing_annotation_targets(
        self, view_id: str, target_types: tuple[str, ...]
    ) -> list[dict[str, str]]:
        """Generate deterministic server-only drawing annotation reference rows."""
        current_model = self._current_model
        if current_model is None or current_model.type != "Drawing":
            raise ValueError("Active model is not a drawing")
        views = self._drawing_views.get(current_model.path, [])
        if not any(view["view_id"] == view_id for view in views):
            raise ValueError("The requested drawing view no longer exists")

        def _token(target_type: str) -> str:
            return base64.b64encode(
                f"{current_model.path}|{view_id}|{target_type}".encode()
            ).decode("ascii")

        names = {
            "dimension": f"{view_id} Dimension 1",
            "component": f"{view_id} Component 1",
            "edge": f"{view_id} Edge 1",
        }
        return [
            {
                "view_id": view_id,
                "target_type": target_type,
                "display_name": names[target_type],
                "persistent_reference": _token(target_type),
            }
            for target_type in target_types
            if target_type in names
        ]

    async def create_drawing_bom(
        self,
        source_view_id: str,
        bom_type: str,
        configuration_name: str,
        x_m: float,
        y_m: float,
        detailed_cut_list: bool,
    ) -> AdapterResult[dict[str, Any]]:
        """Record a deterministic mock bill of materials in the active drawing."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Active model is not a drawing"
            )
        if not any(
            view["view_id"] == source_view_id
            for view in self._drawing_views.get(self._current_model.path, [])
        ):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )
        boms = self._drawing_boms.setdefault(self._current_model.path, [])
        bom = {
            "display_name": f"BOM {len(boms) + 1}",
            "bom_type": bom_type,
            "row_count": 2,
            "column_count": 3,
            "persistent_reference": base64.b64encode(
                f"{self._current_model.path}|bom|{len(boms) + 1}".encode()
            ).decode("ascii"),
        }
        boms.append(bom)
        _ = configuration_name, x_m, y_m, detailed_cut_list
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(bom))

    async def update_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Refresh exactly one mock BOM identified by its issued token."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        for bom in self._drawing_boms.get(self._current_model.path, []):
            if bom.get("persistent_reference") == persistent_reference:
                self._operation_count += 1
                return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(bom))
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Drawing BOM can no longer be resolved",
        )

    async def delete_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete exactly one mock BOM identified by its issued token."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a drawing",
            )
        boms = self._drawing_boms.get(self._current_model.path, [])
        for index, bom in enumerate(boms):
            if bom.get("persistent_reference") == persistent_reference:
                removed = boms.pop(index)
                self._operation_count += 1
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS, data=dict(removed)
                )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Drawing BOM can no longer be resolved",
        )

    async def create_drawing_note(
        self, text: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Record one positioned mock drawing note."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Active model is not a drawing"
            )
        annotations = self._drawing_annotations.setdefault(self._current_model.path, [])
        annotation = {
            "annotation_type": "note",
            "display_name": f"Note {len(annotations) + 1}",
        }
        annotations.append(annotation)
        _ = text, x_m, y_m
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(annotation))

    async def create_drawing_revision_table(
        self, x_m: float, y_m: float, anchor_type: int, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Record one mock revision table with an initial revision."""
        _ = x_m, y_m, anchor_type
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Active model is not a drawing")
        tables = self._drawing_revision_tables.setdefault(self._current_model.path, [])
        table = {
            "display_name": "Revision Table",
            "revision": revision,
            "revision_id": len(tables),
            "current_revision": revision,
            "persistent_reference": f"mock-revision-table-{len(tables)}",
            "rows": [
                {
                    "row_number": 0,
                    "revision_id": len(tables),
                    "revision": revision,
                    "symbol_count": 0,
                }
            ],
        }
        tables.append(table)
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(table))

    def _mock_revision_table(self, persistent_reference: str) -> dict[str, Any] | None:
        """Resolve a mock revision table by its opaque persistent token."""
        if not self._current_model:
            return None
        for table in self._drawing_revision_tables.get(self._current_model.path, []):
            if table.get("persistent_reference") == persistent_reference:
                return table
        return None

    async def list_drawing_revisions(
        self, persistent_reference: str
    ) -> AdapterResult[list[dict[str, Any]]]:
        table = self._mock_revision_table(persistent_reference)
        if table is None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Drawing revision table can no longer be resolved")
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[dict(row) for row in table.get("rows", [])],
        )

    async def add_drawing_revision(
        self, persistent_reference: str, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        table = self._mock_revision_table(persistent_reference)
        if table is None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Drawing revision table can no longer be resolved")
        rows = table.setdefault("rows", [])
        revision_id = max((int(row.get("revision_id", -1)) for row in rows), default=-1) + 1
        row = {
            "row_number": len(rows),
            "revision_id": revision_id,
            "revision": revision,
            "symbol_count": 0,
        }
        rows.append(row)
        table["revision"] = revision
        table["current_revision"] = revision
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(row))

    async def delete_drawing_revision(
        self, persistent_reference: str, revision_id: int, delete_symbols: bool
    ) -> AdapterResult[dict[str, Any]]:
        _ = delete_symbols
        table = self._mock_revision_table(persistent_reference)
        if table is None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Drawing revision table can no longer be resolved")
        rows = table.setdefault("rows", [])
        for index, row in enumerate(rows):
            if int(row.get("revision_id", -1)) == revision_id:
                rows.pop(index)
                for row_number, remaining in enumerate(rows):
                    remaining["row_number"] = row_number
                table["current_revision"] = rows[-1]["revision"] if rows else ""
                self._operation_count += 1
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS,
                    data={"revision_id": revision_id, "deleted": True},
                )
        return AdapterResult(status=AdapterResultStatus.ERROR, error="Revision row can no longer be resolved")

    async def list_drawing_annotation_targets(
        self, view_id: str, target_types: tuple[str, ...]
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Return deterministic mock dimension, component, and edge targets."""
        try:
            targets = self._mock_drawing_annotation_targets(view_id, target_types)
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=targets)

    async def _create_mock_drawing_annotation(
        self,
        persistent_reference: str,
        expected_target_type: str,
        annotation_type: str,
    ) -> AdapterResult[dict[str, Any]]:
        """Validate one mock opaque target and record its resulting annotation."""
        if not self._current_model or self._current_model.type != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Active model is not a drawing"
            )
        valid_targets = [
            target
            for view in self._drawing_views.get(self._current_model.path, [])
            for target in self._mock_drawing_annotation_targets(
                view["view_id"], (expected_target_type,)
            )
        ]
        if persistent_reference not in {
            target["persistent_reference"] for target in valid_targets
        }:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Drawing annotation target can no longer be resolved",
            )
        annotations = self._drawing_annotations.setdefault(self._current_model.path, [])
        display_names = {
            "dimension": "Drawing Dimension",
            "balloon": "BOM Balloon",
            "hole_callout": "Hole Callout",
            "ordinate_dimension": "Ordinate Dimension",
            "surface_finish": "Surface Finish",
            "weld_symbol": "Weld Symbol",
            "geometric_tolerance": "Geometric Tolerance",
            "center_mark": "Center Mark",
        }
        annotation = {
            "annotation_type": annotation_type,
            "display_name": f"{display_names[annotation_type]} {len(annotations) + 1}",
        }
        annotations.append(annotation)
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(annotation))

    async def place_drawing_dimension(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Record a mock placed display dimension."""
        _ = x_m, y_m
        return await self._create_mock_drawing_annotation(
            persistent_reference, "dimension", "dimension"
        )

    async def create_drawing_balloon(
        self,
        persistent_reference: str,
        x_m: float,
        y_m: float,
        style: str,
        text: str,
    ) -> AdapterResult[dict[str, Any]]:
        """Record a mock BOM balloon."""
        _ = x_m, y_m, style, text
        return await self._create_mock_drawing_annotation(
            persistent_reference, "component", "balloon"
        )

    async def create_drawing_hole_callout(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Record a mock automatic hole callout."""
        _ = x_m, y_m
        return await self._create_mock_drawing_annotation(
            persistent_reference, "edge", "hole_callout"
        )

    async def create_drawing_ordinate_dimension(
        self, persistent_reference: str, x_m: float, y_m: float, ordinate_type: str
    ) -> AdapterResult[dict[str, Any]]:
        _ = x_m, y_m
        if ordinate_type not in {"horizontal", "vertical"}:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Unsupported ordinate dimension type")
        return await self._create_mock_drawing_annotation(
            persistent_reference, "dimension", "ordinate_dimension"
        )

    async def create_drawing_surface_finish(
        self, persistent_reference: str, x_m: float, y_m: float, symbol: str, value: str
    ) -> AdapterResult[dict[str, Any]]:
        _ = x_m, y_m, value
        if symbol not in {"machining_required", "machining_prohibited", "any_process"}:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Unsupported surface finish symbol")
        return await self._create_mock_drawing_annotation(
            persistent_reference, "edge", "surface_finish"
        )

    async def create_drawing_weld_symbol(
        self, persistent_reference: str, x_m: float, y_m: float, weld_type: str, size: str
    ) -> AdapterResult[dict[str, Any]]:
        _ = x_m, y_m, weld_type, size
        return await self._create_mock_drawing_annotation(
            persistent_reference, "edge", "weld_symbol"
        )

    async def create_drawing_geometric_tolerance(
        self, persistent_reference: str, x_m: float, y_m: float, tolerance: str, datum: str
    ) -> AdapterResult[dict[str, Any]]:
        _ = x_m, y_m, tolerance, datum
        return await self._create_mock_drawing_annotation(
            persistent_reference, "dimension", "geometric_tolerance"
        )

    async def create_drawing_center_mark(
        self, persistent_reference: str, size_m: float, show_connection_lines: bool
    ) -> AdapterResult[dict[str, Any]]:
        _ = size_m, show_connection_lines
        return await self._create_mock_drawing_annotation(
            persistent_reference, "edge", "center_mark"
        )

    async def list_custom_properties(
        self, configuration_name: str | None = None
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Mock document or configuration custom-property listing."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["model_operation"] / 4)
        self._operation_count += 1
        properties = self._custom_properties.get(self._current_model.path, {}).get(
            configuration_name or "", {}
        )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {"name": name, "value": value, "resolved_value": value}
                for name, value in properties.items()
            ],
            execution_time=self._delays["model_operation"] / 4,
        )

    async def set_custom_property(
        self, name: str, value: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock creation or replacement of one custom property."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["model_operation"] / 4)
        self._operation_count += 1
        properties_by_scope = self._custom_properties.setdefault(
            self._current_model.path, {}
        )
        properties = properties_by_scope.setdefault(configuration_name or "", {})
        properties[name] = value
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": name, "value": value, "resolved_value": value},
            execution_time=self._delays["model_operation"] / 4,
        )

    async def delete_custom_property(
        self, name: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, str]]:
        """Mock deletion of one document or configuration custom property."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        properties = self._custom_properties.get(self._current_model.path, {}).get(
            configuration_name or "", {}
        )
        matching_name = next(
            (
                property_name
                for property_name in properties
                if property_name.casefold() == name.casefold()
            ),
            None,
        )
        if matching_name is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Custom property does not exist",
            )

        await asyncio.sleep(self._delays["model_operation"] / 4)
        self._operation_count += 1
        del properties[matching_name]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": matching_name},
            execution_time=self._delays["model_operation"] / 4,
        )

    async def _create_mock_advanced_feature(
        self, feature_type: str, parameters: dict[str, Any]
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a typed mock feature after checking that a part is active."""
        if not self._current_model or self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"{feature_type}{len(self._features) + 1}",
            type=feature_type,
            id=str(uuid.uuid4()),
            parameters=parameters,
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_sheet_metal_base_flange(
        self,
        sketch_name: str,
        thickness_mm: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock base flange and retain its K factor per document."""
        result = await self._create_mock_advanced_feature(
            "BaseFlange",
            {
                "sketch_name": sketch_name,
                "thickness_mm": thickness_mm,
                "bend_radius_mm": bend_radius_mm,
                "k_factor": k_factor,
            },
        )
        if result.is_success and self._current_model:
            self._sheet_metal_k_factors[self._current_model.path] = k_factor
        return result

    async def create_sheet_metal_edge_flange(
        self,
        edge_persistent_reference: str,
        length_mm: float,
        angle_degrees: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock edge flange from a workflow-owned edge token."""
        result = await self._create_mock_advanced_feature(
            "EdgeFlange",
            {
                "edge_persistent_reference": edge_persistent_reference,
                "length_mm": length_mm,
                "angle_degrees": angle_degrees,
                "bend_radius_mm": bend_radius_mm,
                "k_factor": k_factor,
            },
        )
        if result.is_success and self._current_model:
            self._sheet_metal_k_factors[self._current_model.path] = k_factor
        return result

    async def set_sheet_metal_k_factor(
        self, k_factor: float
    ) -> AdapterResult[dict[str, Any]]:
        """Set and immediately read back the active mock part K factor."""
        if not self._current_model or self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )
        self._sheet_metal_k_factors[self._current_model.path] = k_factor
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"k_factor": self._sheet_metal_k_factors[self._current_model.path]},
        )

    async def create_shell(
        self, face_persistent_references: list[str], thickness_mm: float, outward: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock shell from workflow-owned face tokens."""
        return await self._create_mock_advanced_feature(
            "Shell",
            {
                "face_persistent_references": list(face_persistent_references),
                "thickness_mm": thickness_mm,
                "outward": outward,
            },
        )

    async def create_chamfer(
        self,
        edge_persistent_references: list[str],
        distance_mm: float,
        angle_degrees: float,
        chamfer_type: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock chamfer from workflow-owned edge capabilities."""
        result = await self._create_mock_advanced_feature(
            "Chamfer",
            {
                "edge_persistent_references": list(edge_persistent_references),
                "distance_mm": distance_mm,
                "angle_degrees": angle_degrees,
                "chamfer_type": chamfer_type,
            },
        )
        if result.is_success:
            result.metadata = {
                "diagnostics": {
                    "requested_edge_count": len(edge_persistent_references),
                    "selected_edge_count": len(edge_persistent_references),
                    "rebuild_succeeded": True,
                    "geometry_validated": False,
                    "geometry_validation_note": "Mock adapter does not run SolidWorks BREP validation.",
                }
            }
        return result

    async def create_rib(
        self,
        sketch_name: str,
        thickness_mm: float,
        draft_angle_degrees: float,
        direction: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock rib from a workflow-owned exited sketch."""
        return await self._create_mock_advanced_feature(
            "Rib",
            {
                "sketch_name": sketch_name,
                "thickness_mm": thickness_mm,
                "draft_angle_degrees": draft_angle_degrees,
                "direction": direction,
            },
        )

    async def create_dome(
        self, face_persistent_reference: str, height_mm: float, reverse_direction: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock dome from one workflow-owned face token."""
        return await self._create_mock_advanced_feature(
            "Dome",
            {
                "face_persistent_reference": face_persistent_reference,
                "height_mm": height_mm,
                "reverse_direction": reverse_direction,
            },
        )

    async def create_draft(
        self,
        face_persistent_references: list[str],
        angle_degrees: float,
        reverse_direction: bool,
        edge_draft: bool,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock multi-face draft from workflow-owned face tokens."""
        return await self._create_mock_advanced_feature(
            "Draft",
            {
                "face_persistent_references": list(face_persistent_references),
                "angle_degrees": angle_degrees,
                "reverse_direction": reverse_direction,
                "edge_draft": edge_draft,
            },
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
        """Create a mock thicken feature from one workflow-owned face token."""
        return await self._create_mock_advanced_feature(
            "Thicken",
            {
                "face_persistent_reference": face_persistent_reference,
                "thickness_mm": thickness_mm,
                "operation": operation,
                "direction": direction,
                "fill_volume": fill_volume,
                "merge_result": merge_result,
            },
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
        """Create a mock wrap from a trusted sketch and face token."""
        return await self._create_mock_advanced_feature(
            "Wrap",
            {
                "sketch_name": sketch_name,
                "face_persistent_reference": face_persistent_reference,
                "operation": operation,
                "thickness_mm": thickness_mm,
                "reverse_direction": reverse_direction,
                "method": method,
            },
        )

    async def create_extrusion(
        self,
        params: ExtrusionParameters | str,
        depth: float | None = None,
        direction: str | None = None,
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating an extrusion feature.

        Args:
            params (ExtrusionParameters | str): The params value.
            depth (float | None): The depth value. Defaults to None.
            direction (str | None): The direction value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        if isinstance(params, str):
            params = ExtrusionParameters(depth=depth or 0.0)

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        feature_id = f"Boss-Extrude{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type="Extrusion",
            id=str(uuid.uuid4()),
            parameters={
                "depth": params.depth,
                "direction": direction or "blind",
                "draft_angle": params.draft_angle,
                "reverse_direction": params.reverse_direction,
                "both_directions": params.both_directions,
                "thin_feature": params.thin_feature,
                "thin_thickness": params.thin_thickness,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_cut_extrude(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a cut-extrude feature from the last exited sketch."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature_id = f"Cut-Extrude{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type="Cut-Extrude",
            id=str(uuid.uuid4()),
            parameters={
                "depth": params.depth,
                "draft_angle": params.draft_angle,
                "reverse_direction": params.reverse_direction,
                "both_directions": params.both_directions,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def add_chamfer(
        self, distance: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock an equal-distance chamfer on explicitly supplied edges."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"Chamfer{len(self._features) + 1}",
            type="Chamfer",
            id=str(uuid.uuid4()),
            parameters={"distance": distance, "edges": list(edge_names)},
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a revolve feature.

        Args:
            params (RevolveParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        feature_id = (
            f"Cut-Revolve{len(self._features) + 1}"
            if params.is_cut
            else f"Boss-Revolve{len(self._features) + 1}"
        )
        feature = SolidWorksFeature(
            name=feature_id,
            type="Cut-Revolve" if params.is_cut else "Revolve",
            id=str(uuid.uuid4()),
            parameters={
                "angle": params.angle,
                "reverse_direction": params.reverse_direction,
                "both_directions": params.both_directions,
                "thin_feature": params.thin_feature,
                "thin_thickness": params.thin_thickness,
                "is_cut": params.is_cut,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_reference_plane(
        self, params: ReferencePlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating one offset reference plane on the active part."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"Plane{len(self._features) + 1}",
            type="ReferencePlane",
            id=str(uuid.uuid4()),
            parameters={
                "base_plane": params.base_plane,
                "offset": params.offset,
                "reverse_direction": params.reverse_direction,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def get_reference_axis(
        self, params: ReferenceAxisParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock resolving one built-in origin axis without mutating the model."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                name=f"{params.axis} Axis",
                type="OriginAxis",
                id=f"origin-axis-{params.axis.lower()}",
                parameters={"axis": params.axis},
                properties={"mock": True},
            ),
        )

    async def get_reference_plane(
        self, params: OriginPlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock resolving one built-in origin plane without mutating the model."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                name=f"{params.plane} Plane",
                type="OriginPlane",
                id=f"origin-plane-{params.plane.lower()}",
                parameters={"plane": params.plane},
                properties={"mock": True},
            ),
        )

    async def create_feature_linear_pattern(
        self, params: FeatureLinearPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a one-direction pattern of one trusted feature."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"LinearPattern{len(self._features) + 1}",
            type="LinearPattern",
            id=str(uuid.uuid4()),
            parameters=params.model_dump(),
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_feature_circular_pattern(
        self, params: FeatureCircularPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a circular pattern of one trusted feature."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"CircularPattern{len(self._features) + 1}",
            type="CircularPattern",
            id=str(uuid.uuid4()),
            parameters=params.model_dump(),
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def mirror_feature(
        self, params: FeatureMirrorParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock mirroring one trusted feature about one reference plane."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"Mirror{len(self._features) + 1}",
            type="Mirror",
            id=str(uuid.uuid4()),
            parameters=params.model_dump(),
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_hole_wizard(
        self, params: HoleWizardParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating one controlled Hole Wizard feature."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"HoleWizard{len(self._features) + 1}",
            type="HoleWizard",
            id=str(uuid.uuid4()),
            parameters=params.model_dump(),
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_tapped_hole(
        self, params: ThreadedHoleParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating one standard tapped Hole Wizard feature."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        feature = SolidWorksFeature(
            name=f"TappedHole{len(self._features) + 1}",
            type="TappedHole",
            id=str(uuid.uuid4()),
            parameters=params.model_dump(),
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a sweep feature.

        Args:
            params (SweepParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        operation = "Cut-Sweep" if params.is_cut else "Boss-Sweep"
        feature_id = f"{operation}{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type=operation,
            id=str(uuid.uuid4()),
            parameters={
                "profile": params.profile,
                "path": params.path,
                "twist_along_path": params.twist_along_path,
                "twist_angle": params.twist_angle,
                "is_cut": params.is_cut,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a loft feature.

        Args:
            params (LoftParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        operation = "Cut-Loft" if params.is_cut else "Boss-Loft"
        feature_id = f"{operation}{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type=operation,
            id=str(uuid.uuid4()),
            parameters={
                "profiles": params.profiles,
                "guide_curves": params.guide_curves,
                "start_tangent": params.start_tangent,
                "end_tangent": params.end_tangent,
                "is_cut": params.is_cut,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_sketch(self, plane: str) -> AdapterResult[dict[str, Any]]:  # type: ignore[override]
        """Mock creating a sketch.

        Args:
            plane (str): The plane value.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            # Legacy tests start sketching immediately after connect.
            await self.create_part()

        await asyncio.sleep(self._delays["sketch_operation"])
        self._operation_count += 1

        sketch_id = f"Sketch{len(self._sketches) + 1}"
        self._sketches[sketch_id] = plane
        self._current_sketch = sketch_id

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "id": sketch_id,
                "name": sketch_id,
                "sketch_name": sketch_id,
                "plane": plane,
            },
            execution_time=self._delays["sketch_operation"],
        )

    async def create_3d_sketch(self) -> AdapterResult[dict[str, Any]]:  # type: ignore[override]
        """Open a deterministic three-dimensional mock sketch."""
        if not self._current_model or self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )
        await asyncio.sleep(self._delays["sketch_operation"])
        self._operation_count += 1
        sketch_id = f"3DSketch{len(self._sketches) + 1}"
        self._sketches[sketch_id] = "3D"
        self._current_sketch = sketch_id
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"id": sketch_id, "name": sketch_id, "sketch_name": sketch_id},
            execution_time=self._delays["sketch_operation"],
        )

    async def add_sketch_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        construction: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Legacy compatibility wrapper around add_line.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.
            construction (bool): The construction value. Defaults to False.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        result = await self.add_line(x1, y1, x2, y2)
        if not result.is_success:
            return AdapterResult(
                status=result.status,
                error=result.error,
                execution_time=result.execution_time,
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "id": result.data,
                "start": {"x": x1, "y": y1},
                "end": {"x": x2, "y": y2},
                "construction": construction,
            },
            execution_time=result.execution_time,
        )

    async def save_file(
        self, file_path: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Legacy compatibility save operation for tests.

        Args:
            file_path (str | None): Path to the target file. Defaults to None.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )
        await asyncio.sleep(0.02)
        self._operation_count += 1
        resolved_path = file_path or self._current_model.path
        if file_path:
            target = Path(file_path)
            previous_path = self._current_model.path
            self._current_model = self._current_model.model_copy(
                update={"path": str(target), "name": target.name}
            )
            self._models.pop(previous_path, None)
            self._models[self._current_model.path] = self._current_model
            components = self._assembly_components.pop(previous_path, None)
            if components is not None:
                self._assembly_components[self._current_model.path] = components
            assembly_mates = self._assembly_mates.pop(previous_path, None)
            if assembly_mates is not None:
                self._assembly_mates[self._current_model.path] = assembly_mates
            drawing_views = self._drawing_views.pop(previous_path, None)
            if drawing_views is not None:
                self._drawing_views[self._current_model.path] = drawing_views
            drawing_sheets = self._drawing_sheets.pop(previous_path, None)
            if drawing_sheets is not None:
                self._drawing_sheets[self._current_model.path] = drawing_sheets
            active_drawing_sheet = self._active_drawing_sheets.pop(previous_path, None)
            if active_drawing_sheet is not None:
                self._active_drawing_sheets[self._current_model.path] = (
                    active_drawing_sheet
                )
            drawing_boms = self._drawing_boms.pop(previous_path, None)
            if drawing_boms is not None:
                self._drawing_boms[self._current_model.path] = drawing_boms
            revision_tables = self._drawing_revision_tables.pop(previous_path, None)
            if revision_tables is not None:
                self._drawing_revision_tables[self._current_model.path] = revision_tables
            drawing_annotations = self._drawing_annotations.pop(previous_path, None)
            if drawing_annotations is not None:
                self._drawing_annotations[self._current_model.path] = (
                    drawing_annotations
                )
            sheet_metal_flattened = self._sheet_metal_flattened.pop(previous_path, None)
            if sheet_metal_flattened is not None:
                self._sheet_metal_flattened[self._current_model.path] = (
                    sheet_metal_flattened
                )
            custom_properties = self._custom_properties.pop(previous_path, None)
            if custom_properties is not None:
                self._custom_properties[self._current_model.path] = custom_properties
            equations = self._equations.pop(previous_path, None)
            if equations is not None:
                self._equations[self._current_model.path] = equations
            if target.is_absolute():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"mock native SolidWorks model")
                except OSError:
                    # Preserve the legacy mock's success-only behavior for callers
                    # that supply an intentionally unavailable compatibility path.
                    pass
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"file_path": resolved_path, "saved": True},
            execution_time=0.02,
        )

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Mock adding a line to sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        line_id = f"Line{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(line_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=line_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_3d_line(
        self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float
    ) -> AdapterResult[str]:
        """Add a deterministic three-dimensional line to a 3D mock sketch."""
        if not self._current_sketch or self._sketches.get(self._current_sketch) != "3D":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active 3D sketch",
            )
        _ = x1, y1, z1, x2, y2, z2
        entity_id = f"3DLine{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(entity_id)
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=entity_id)

    async def convert_sketch_entities(
        self, edge_persistent_references: list[str]
    ) -> AdapterResult[str]:
        """Record a deterministic converted-entities result for active sketch work."""
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active sketch",
            )
        if not edge_persistent_references:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No part edges were supplied",
            )
        entity_id = f"ConvertedEntities{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(entity_id)
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=entity_id)

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Mock adding a construction centerline to sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        centerline_id = f"Centerline_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(centerline_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=centerline_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_spline(self, points: list[dict[str, float]]) -> AdapterResult[str]:
        """Mock adding a NURBS spline through the supplied points.

        Args:
            points (list[dict[str, float]]): Ordered control points with
                ``"x"`` / ``"y"`` keys. Minimum 2 points.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if len(points) < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="add_spline requires at least 2 points",
            )
        # Match the real adapter's point contract: each dict must carry
        # both ``x`` and ``y`` keys, or the live impl raises KeyError on
        # ``point["x"]``. Validating here keeps mock/live parity so tests
        # that pass against the mock won't fail live on malformed input.
        for index, point in enumerate(points):
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"add_spline point at index {index} is missing "
                        "required 'x' and/or 'y' keys"
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        spline_id = f"Spline_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(spline_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=spline_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        """Mock adding a circle to sketch.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        circle_id = f"Circle{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(circle_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=circle_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_point(self, x: float, y: float) -> AdapterResult[str]:
        """Mock adding one position point to the active sketch."""
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1
        point_id = f"Point{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(point_id)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=point_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_arc(
        self,
        center_x: float,
        center_y: float,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> AdapterResult[str]:
        """Mock adding a circular arc to sketch.

        Args:
            center_x (float): Arc centre X.
            center_y (float): Arc centre Y.
            start_x (float): Arc start point X.
            start_y (float): Arc start point Y.
            end_x (float): Arc end point X.
            end_y (float): Arc end point Y.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        arc_id = f"Arc_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(arc_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=arc_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Mock adding a rectangle to sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"])
        self._operation_count += 1

        rect_id = f"Rectangle{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(rect_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=rect_id,
            execution_time=self._delays["sketch_operation"],
        )

    async def add_ellipse(
        self,
        center_x: float,
        center_y: float,
        major_axis: float,
        minor_axis: float,
    ) -> AdapterResult[str]:
        """Mock adding an axis-aligned ellipse to sketch.

        Args:
            center_x (float): Ellipse centre X in millimetres.
            center_y (float): Ellipse centre Y in millimetres.
            major_axis (float): Full major-axis length in millimetres.
            minor_axis (float): Full minor-axis length in millimetres.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        ellipse_id = f"Ellipse_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(ellipse_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=ellipse_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_sketch_text(
        self, text: str, x: float, y: float, height: float, angle_degrees: float
    ) -> AdapterResult[str]:
        """Mock adding one text entity to the active sketch."""
        _ = text, x, y, height, angle_degrees
        if not self._current_sketch:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
        text_id = f"SketchText_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(text_id)
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=text_id)

    async def add_polygon(
        self, center_x: float, center_y: float, radius: float, sides: int
    ) -> AdapterResult[str]:
        """Mock adding a regular polygon to sketch.

        Args:
            center_x (float): Polygon centre X in millimetres.
            center_y (float): Polygon centre Y in millimetres.
            radius (float): Circumradius in millimetres (distance from centre
                to each vertex; matches the real adapter's
                ``CreatePolygon(..., Inscribed=True)`` semantics).
            sides (int): Number of polygon sides.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        # ID format mirrors the real PyWin32 adapter, which uses
        # ``_register_sketch_entity("Polygon", ...)`` (counter-based) so the
        # returned ID is a valid input to downstream sketch_linear_pattern /
        # sketch_circular_pattern / sketch_mirror / sketch_offset calls.
        polygon_id = f"Polygon_{len(self._sketch_entity_ids) + 1}"
        self._sketch_entity_ids.add(polygon_id)
        _ = sides  # informational only; the sides count is no longer part of the ID

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=polygon_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_linear_pattern(
        self,
        entities: list[str],
        direction_x: float,
        direction_y: float,
        spacing: float,
        count: int,
    ) -> AdapterResult[str]:
        """Mock creating a linear sketch pattern.

        Mirrors the real adapter's validation rules — empty entities,
        count < 2, non-positive spacing, and a zero direction vector each
        produce a clear error without "creating" a pattern.

        Args:
            entities (list[str]): Seed entity IDs.
            direction_x (float): Pattern direction X component.
            direction_y (float): Pattern direction Y component.
            spacing (float): Distance between instances in millimetres.
            count (int): Total number of instances (including the seed).

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_linear_pattern requires at least one entity",
            )
        if count < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_linear_pattern requires count >= 2",
            )
        if spacing <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_linear_pattern requires spacing > 0",
            )
        if math.hypot(direction_x, direction_y) < 1e-9:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=("sketch_linear_pattern requires a non-zero direction vector"),
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        pattern_id = f"LinearPattern_{count}x{spacing}_{random.randint(1000, 9999)}"
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=pattern_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_circular_pattern(
        self,
        entities: list[str],
        angle: float,
        count: int,
    ) -> AdapterResult[str]:
        """Mock creating a circular sketch pattern around the sketch origin.

        Args:
            entities (list[str]): Seed entity IDs.
            angle (float): Total swept angle in degrees.
            count (int): Total number of instances (including the seed).

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_circular_pattern requires at least one entity",
            )
        if count < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_circular_pattern requires count >= 2",
            )
        if angle <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_circular_pattern requires angle > 0",
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        pattern_id = f"CircularPattern_{count}x{angle}deg_{random.randint(1000, 9999)}"
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=pattern_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_mirror(
        self, entities: list[str], mirror_line: str
    ) -> AdapterResult[str]:
        """Mock mirroring sketch entities about a centerline.

        Args:
            entities (list[str]): IDs of segments to mirror.
            mirror_line (str): ID of the centerline to mirror across.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_mirror requires at least one entity",
            )
        if not mirror_line:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "sketch_mirror requires a mirror_line entity ID (add_centerline)"
                ),
            )
        if mirror_line not in self._sketch_entity_ids:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unknown mirror_line entity '{mirror_line}'. Use the "
                    "ID returned by add_centerline."
                ),
            )
        # Mirror the real adapter — IModelDoc2::SketchMirror needs a
        # centreline as the mirror axis; other segments silently no-op.
        if not mirror_line.startswith("Centerline_"):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"mirror_line must be a centerline (from add_centerline), "
                    f"got '{mirror_line}'"
                ),
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        mirror_id = f"Mirror_{mirror_line}_{random.randint(1000, 9999)}"
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=mirror_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_offset(
        self,
        entities: list[str],
        offset_distance: float,
        reverse_direction: bool,
    ) -> AdapterResult[str]:
        """Mock offsetting sketch entities by a fixed distance.

        Args:
            entities (list[str]): IDs of segments to offset.
            offset_distance (float): Distance in millimetres. Must be > 0.
            reverse_direction (bool): Flip the offset direction.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_offset requires at least one entity",
            )
        if offset_distance <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "sketch_offset requires offset_distance > 0 — use "
                    "reverse_direction to flip the side"
                ),
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        direction = "inward" if reverse_direction else "outward"
        offset_id = f"Offset_{offset_distance}_{direction}_{random.randint(1000, 9999)}"
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=offset_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_trim(
        self, entity_id: str, trim_x: float, trim_y: float
    ) -> AdapterResult[str]:
        """Mock trimming one registered entity at a bounded sketch point."""
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if entity_id not in self._sketch_entity_ids:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unknown sketch entity '{entity_id}'. Use IDs returned by "
                    "the sketch creation tools."
                ),
            )
        _ = trim_x, trim_y
        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=entity_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_split(
        self,
        entity_id: str,
        x: float,
        y: float,
        z: float = 0.0,
        closed: bool = False,
        x2: float | None = None,
        y2: float | None = None,
        z2: float | None = None,
    ) -> AdapterResult[str]:
        """Mock splitting one registered sketch segment."""
        if not self._current_sketch:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
        if entity_id not in self._sketch_entity_ids:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Unknown sketch entity '{entity_id}'",
            )
        if closed and (x2 is None or y2 is None):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="closed sketch split requires a second point",
            )
        _ = x, y, z, x2, y2, z2
        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=f"Split_{entity_id}_{random.randint(1000, 9999)}",
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_fillet(
        self, entities: list[str], radius: float
    ) -> AdapterResult[str]:
        """Mock rounding a corner formed by two sketch entities."""
        if not self._current_sketch:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
        if len(entities) != 2 or len(set(entities)) != 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch fillet requires two distinct entities",
            )
        if radius <= 0:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="radius must be > 0")
        if any(entity not in self._sketch_entity_ids for entity in entities):
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Unknown sketch entity")
        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=f"Fillet_{random.randint(1000, 9999)}",
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_chamfer(
        self, entities: list[str], distance: float, angle_degrees: float = 45.0
    ) -> AdapterResult[str]:
        """Mock beveling a corner formed by two sketch entities."""
        if not self._current_sketch:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
        if len(entities) != 2 or len(set(entities)) != 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch chamfer requires two distinct entities",
            )
        if distance <= 0 or not 0 < angle_degrees < 180:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="invalid chamfer dimensions")
        if any(entity not in self._sketch_entity_ids for entity in entities):
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Unknown sketch entity")
        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=f"Chamfer_{random.randint(1000, 9999)}",
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_sketch_constraint(
        self,
        entity1: str,
        entity2: str | None,
        relation_type: str,
        entity3: str | None = None,
    ) -> AdapterResult[str]:
        """Mock adding a geometric constraint between sketch entities.

        Args:
            entity1 (str): Primary sketch-entity ID.
            entity2 (str | None): Secondary sketch-entity ID, or None for
                single-entity relations (horizontal, vertical, fix).
            relation_type (str): Constraint name (see RELATION_NAME_MAP).
            entity3 (str | None): Third entity ID — only used by the
                ``symmetric`` relation (the centerline of symmetry). All
                other relation types reject a non-null ``entity3``.

        Returns:
            AdapterResult[str]: SUCCESS with a placeholder constraint ID, or
                ERROR if no active sketch, the relation_type is unsupported,
                arity is wrong, or an entity ID is unknown.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        rt_norm = (relation_type or "").strip().lower()
        if rt_norm not in RELATION_NAME_MAP:
            supported = ", ".join(sorted(RELATION_NAME_MAP))
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unsupported relation type '{relation_type}'. "
                    f"Supported: {supported}"
                ),
            )

        # Arity validation — mirror the real adapter
        if rt_norm == "symmetric":
            if entity2 is None or entity3 is None:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Relation '{relation_type}' requires entity1, "
                        "entity2, and entity3 (the centerline of symmetry)"
                    ),
                )
        elif entity3 is not None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Relation '{relation_type}' does not accept entity3 — "
                    "only 'symmetric' takes a third entity (the centerline)"
                ),
            )

        # Validate entity IDs against the in-process sketch-entity registry
        # so the mock surfaces the same "Unknown sketch entity" error as the
        # real adapter (which checks adapter._sketch_entities).
        for ent in (entity1, entity2, entity3):
            if ent is not None and ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        constraint_id = f"Constraint_{relation_type}_{random.randint(1000, 9999)}"

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=constraint_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def exit_sketch(self) -> AdapterResult[None]:
        """Mock exiting sketch mode.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.WARNING, error="No active sketch to exit"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._current_sketch = None

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=None,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def check_sketch_fully_defined(
        self, sketch_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock check for sketch definition status.

        Args:
            sketch_name (str | None): Optional sketch name to inspect. Defaults to None.

        Returns:
            AdapterResult[dict[str, Any]]: Definition status payload.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 3)
        self._operation_count += 1

        resolved_name = sketch_name or self._current_sketch or "Sketch1"
        if sketch_name and sketch_name not in self._sketches:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Sketch not found: {sketch_name}",
            )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "sketch_name": resolved_name,
                "is_fully_defined": True,
                "definition_state": "fully_defined",
                "source": "mock",
                "raw_status": True,
            },
            execution_time=self._delays["sketch_operation"] / 3,
        )

    async def check_sketch_feature_use(
        self, sketch_name: str | None = None, feature_use: str = "boss_extrude"
    ) -> AdapterResult[dict[str, Any]]:
        """Mock the kernel feature-use check for an existing sketch."""
        if not self._current_model:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
        if feature_use not in {
            "base_extrude",
            "boss_extrude",
            "surface_extrude",
            "cut_extrude",
        }:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Unsupported feature_use: {feature_use}",
            )
        resolved_name = sketch_name or self._current_sketch or "Sketch1"
        if sketch_name and sketch_name not in self._sketches:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Sketch not found: {sketch_name}",
            )
        await asyncio.sleep(self._delays["sketch_operation"] / 3)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "sketch_name": resolved_name,
                "feature_use": feature_use,
                "feature_type": 3 if feature_use == "boss_extrude" else None,
                "is_valid": True,
                "status_code": 0,
                "status": "ok",
                "open_contour_count": 0,
                "closed_contour_count": 1,
                "source": "mock",
            },
            execution_time=self._delays["sketch_operation"] / 3,
        )

    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        """Mock getting mass properties.

        Returns:
            AdapterResult[MassProperties]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        # Generate realistic mock data
        properties = MassProperties(
            volume=random.uniform(1000, 100000),  # mm³
            surface_area=random.uniform(1000, 50000),  # mm²
            mass=random.uniform(0.1, 50.0),  # kg (assuming typical density)
            center_of_mass=[
                random.uniform(-100, 100),
                random.uniform(-100, 100),
                random.uniform(-100, 100),
            ],
            moments_of_inertia={
                "Ixx": random.uniform(1e6, 1e9),
                "Iyy": random.uniform(1e6, 1e9),
                "Izz": random.uniform(1e6, 1e9),
                "Ixy": random.uniform(-1e8, 1e8),
                "Ixz": random.uniform(-1e8, 1e8),
                "Iyz": random.uniform(-1e8, 1e8),
            },
        )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=properties,
            execution_time=self._delays["feature_operation"],
        )

    async def get_bounding_box(self) -> AdapterResult[dict[str, list[float]]]:
        """Return deterministic active-part extents in millimetres."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "min_point_mm": [0.0, 0.0, 0.0],
                "max_point_mm": [100.0, 50.0, 25.0],
                "size_mm": [100.0, 50.0, 25.0],
            },
        )

    async def list_equations(self) -> AdapterResult[list[dict[str, Any]]]:
        """Return the active mock part's equations with stable indices."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        equations = self._equations.get(self._current_model.path, [])
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {**equation, "index": index} for index, equation in enumerate(equations)
            ],
        )

    async def set_equation(
        self, name: str, expression: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create or update one mock equation for the active part."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        equations = self._equations.setdefault(self._current_model.path, [])
        for index, equation in enumerate(equations):
            if str(equation["name"]).casefold() == name.casefold():
                equation["expression"] = expression
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS,
                    data={**equation, "index": index},
                )
        equation = {
            "name": name,
            "expression": expression,
            "is_global_variable": "@" not in name,
        }
        equations.append(equation)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={**equation, "index": len(equations) - 1},
        )

    async def delete_equation(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Delete one mock equation from the active part."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        equations = self._equations.setdefault(self._current_model.path, [])
        for index, equation in enumerate(equations):
            if str(equation["name"]).casefold() == name.casefold():
                deleted = equations.pop(index)
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS,
                    data={**deleted, "index": index},
                )
        return AdapterResult(
            status=AdapterResultStatus.ERROR, error="Equation does not exist"
        )

    async def list_solid_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Return a deterministic solid-body summary for the active mock part."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        _ = include_hidden
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {
                    "body_id": "body_1",
                    "name": f"{self._current_model.name} Body",
                    "visible": True,
                }
            ],
        )

    async def list_surface_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Return no surface bodies for the solid-only mock part model."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        _ = include_hidden
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=[])

    async def list_part_topology(
        self, entity_types: tuple[str, ...] = ("face", "edge")
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Return deterministic topology tokens and measurements for a mock part."""
        if not self._current_model or self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active model is not a part",
            )
        rows: list[dict[str, Any]] = []
        geometry_by_type: dict[str, dict[str, Any]] = {
            "face": {"area_mm2": 1_000.0, "normal": [0.0, 0.0, 1.0]},
            "edge": {"length_mm": 25.0, "curve_identity": 3001},
            "vertex": {"point_mm": [0.0, 0.0, 0.0]},
        }
        for entity_type, count in (("face", 3), ("edge", 4), ("vertex", 8)):
            if entity_type not in entity_types:
                continue
            for index in range(1, count + 1):
                token = base64.b64encode(
                    f"{self._current_model.path}|{entity_type}|{index}".encode()
                ).decode("ascii")
                rows.append(
                    {
                        "entity_type": entity_type,
                        "display_name": f"{entity_type.title()} {index}",
                        "persistent_reference": token,
                        **geometry_by_type[entity_type],
                    }
                )
        self._operation_count += 1
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=rows)

    async def list_sheet_metal_feature_geometry(
        self,
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Return deterministic flat-pattern and bend faces for a mock part."""
        if not self._current_model or self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {
                    "display_name": "Flat-Pattern1",
                    "feature_kind": "flat_pattern",
                    "faces": [
                        {
                            "display_name": "Flat-Pattern1 Fixed Face",
                            "persistent_reference": "mock-flat-pattern-face",
                        }
                    ],
                },
                {
                    "display_name": "Edge-Flange1",
                    "feature_kind": "bend",
                    "faces": [
                        {
                            "display_name": "Edge-Flange1 Bend Face",
                            "persistent_reference": "mock-bend-face",
                        }
                    ],
                },
            ],
        )

    async def insert_sheet_metal_bend_notes(self) -> AdapterResult[dict[str, Any]]:
        """Mock generating bend notes from the current flat pattern."""
        if not self._current_model or self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"flat_pattern_feature_name": "Flat-Pattern1"},
        )

    async def insert_sheet_metal_bend_table(
        self, x_m: float, y_m: float, start_value: int
    ) -> AdapterResult[dict[str, Any]]:
        """Mock generating a default-template bend table."""
        if not self._current_model or self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "display_name": "Bend Table1",
                "annotation_count": 1,
            },
        )

    async def get_material_assignment(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, str]]:
        """Return the active mock part configuration's material assignment."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        key = (self._current_model.name, configuration_name)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=dict(
                self._material_assignments.get(
                    key,
                    {"database_name": "", "material_name": ""},
                )
            ),
        )

    async def set_material_assignment(
        self, configuration_name: str, database_name: str, material_name: str
    ) -> AdapterResult[dict[str, str]]:
        """Persist one material assignment in the active mock part configuration."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if self._current_model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active document is not a part",
            )
        assignment = {
            "database_name": database_name,
            "material_name": material_name,
        }
        self._material_assignments[(self._current_model.name, configuration_name)] = (
            assignment
        )
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(assignment))

    async def export_image(self, payload: dict) -> AdapterResult[dict]:
        """Mock export of a PNG/JPG viewport screenshot.

        Args:
            payload (dict): The payload value.

        Returns:
            AdapterResult[dict]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        import pathlib

        file_path = payload.get("file_path", "")
        width = int(payload.get("width", 1280))
        height = int(payload.get("height", 720))
        orientation = str(payload.get("view_orientation", "current"))

        await asyncio.sleep(0.3)
        self._operation_count += 1

        out = pathlib.Path(file_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Write a valid 1×1 white PNG binary so UI image tags don't break
        _PNG_1X1_WHITE = (
            b"\x89PNG\r\n\x1a\n"  # PNG signature
            b"\x00\x00\x00\rIHDR"  # IHDR chunk length+type
            b"\x00\x00\x00\x01\x00\x00\x00\x01"  # 1x1
            b"\x08\x02\x00\x00\x00\x90wS\xde"  # 8-bit RGB + CRC
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff\xff\x00\x05\xfe\x02\xfe\xdc\xcaY\xe7"  # IDAT
            b"\x00\x00\x00\x00IEND\xaeB`\x82"  # IEND
        )
        out.write_bytes(_PNG_1X1_WHITE)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "file_path": file_path,
                "format": "PNG",
                "dimensions": f"{width}x{height}",
                "view": orientation,
            },
            execution_time=0.3,
            metadata={"mock": True, "orientation": orientation},
        )

    async def pack_and_go_assembly(
        self,
        source_path: str,
        target_dir: str,
        name_prefix: str = "",
    ) -> AdapterResult[dict]:
        """Mock Pack-and-Go: simulate copying an assembly to a target directory.

        Args:
            source_path (str): Path to the source .sldasm file.
            target_dir (str): Directory to copy files into.

        Returns:
            AdapterResult[dict]: Simulated pack-and-go result.
        """
        import pathlib

        await asyncio.sleep(0.05)
        self._operation_count += 1

        source = pathlib.Path(source_path)
        out_dir = pathlib.Path(target_dir)
        mock_parts = [f"{name_prefix}MockPart{i}.SLDPRT" for i in range(1, 3)]
        source_files = [str(source)] + [str(source.parent / p) for p in mock_parts]
        copied_files = [str(out_dir / f"{name_prefix}{source.name}")] + [
            str(out_dir / p) for p in mock_parts
        ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "source_assembly": str(source),
                "target_dir": str(out_dir),
                "copied_files": copied_files,
                "source_files": source_files,
                "save_statuses": [0] * (len(mock_parts) + 1),
                "all_files_saved": True,
            },
            execution_time=0.05,
            metadata={"mock": True},
        )

    async def export_file(
        self, file_path: str, format_type: str
    ) -> AdapterResult[None]:
        """Mock exporting a file.

        Args:
            file_path (str): Path to the target file.
            format_type (str): The format type value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        # Simulate export time based on format
        export_times = {
            "step": 2.0,
            "iges": 1.5,
            "stl": 1.0,
            "pdf": 0.5,
            "jpg": 0.3,
            "glb": 1.2,
            "gltf": 1.2,
        }

        format_lower = format_type.lower()
        delay = export_times.get(format_lower, 1.0)

        await asyncio.sleep(delay)
        self._operation_count += 1

        # Write a minimal placeholder file so callers that check existence get a valid result
        if format_lower == "stl":
            import pathlib

            out = pathlib.Path(file_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            # Minimal ASCII STL — a single unit triangle (mock geometry placeholder)
            out.write_text(
                "solid mock\n"
                "  facet normal 0 0 1\n"
                "    outer loop\n"
                "      vertex 0 0 0\n"
                "      vertex 10 0 0\n"
                "      vertex 5 10 0\n"
                "    endloop\n"
                "  endfacet\n"
                "  facet normal 0 0 1\n"
                "    outer loop\n"
                "      vertex 0 0 0\n"
                "      vertex 10 0 0\n"
                "      vertex 5 0 10\n"
                "    endloop\n"
                "  endfacet\n"
                "endsolid mock\n",
                encoding="utf-8",
            )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=None,
            execution_time=delay,
            metadata={"exported_format": format_type, "file_path": file_path},
        )

    async def export_drawing_cad(
        self,
        file_path: str,
        format_type: str,
        model_space: bool = True,
        compatibility_profile: str = "standard",
    ) -> AdapterResult[None]:
        """Mock a model-space or paper-space drawing DWG/DXF export."""
        if compatibility_profile not in {"standard", "caxa_2015"}:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Unsupported CAD compatibility profile",
            )
        if compatibility_profile == "caxa_2015" and (
            format_type.lower() != "dxf" or not model_space
        ):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="CAXA 2015 export requires a model-space DXF",
            )
        if format_type.lower() not in {"dwg", "dxf"}:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Drawing CAD export format must be DWG or DXF",
            )
        result = await self.export_file(file_path, format_type)
        if result.is_success:
            result.metadata["model_space"] = model_space
            result.metadata["compatibility_profile"] = compatibility_profile
        return result

    async def list_dimensions(self) -> AdapterResult[list[dict[str, Any]]]:
        """Return mock model dimensions without exposing COM implementation details."""
        await asyncio.sleep(0.05)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {"name": name, "value_mm": value}
                for name, value in self._dimensions.items()
            ],
            execution_time=0.05,
        )

    async def get_dimension(self, name: str) -> AdapterResult[float]:
        """Mock getting a dimension value.

        Args:
            name (str): The name value.

        Returns:
            AdapterResult[float]: The result produced by the operation.
        """
        await asyncio.sleep(0.05)  # Very fast operation
        self._operation_count += 1

        if name in self._dimensions:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data=self._dimensions[name],
                execution_time=0.05,
            )
        else:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Dimension '{name}' not found"
            )

    async def set_dimension(self, name: str, value: float) -> AdapterResult[None]:
        """Mock setting a dimension value.

        Args:
            name (str): The name value.
            value (float): The value value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        await asyncio.sleep(0.1)  # Fast operation
        self._operation_count += 1

        self._dimensions[name] = value

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS, data=None, execution_time=0.1
        )
