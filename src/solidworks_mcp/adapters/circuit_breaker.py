"""Circuit breaker adapter for SolidWorks operations.

Implements the circuit breaker pattern to prevent cascading failures when SolidWorks
operations fail repeatedly.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypeVar, cast

from loguru import logger

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

T = TypeVar("T")


def _to_input_dict(params: Any) -> dict[str, Any]:
    """Convert a Pydantic model or plain dict to a flat dict for SoC logging."""
    if hasattr(params, "model_dump"):
        return cast(dict[str, Any], params.model_dump())
    return cast(dict[str, Any], params) if isinstance(params, dict) else {}


class CircuitState(Enum):
    """Circuit breaker states.

    Attributes:
        CLOSED (Any): The closed value.
        HALF_OPEN (Any): The half open value.
        OPEN (Any): The open value.
    """

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit is open, blocking requests
    HALF_OPEN = "half_open"  # Testing if service is back


class CircuitBreakerAdapter(SolidWorksAdapter):
    """Circuit breaker wrapper for SolidWorks adapters.

    Args:
        adapter (SolidWorksAdapter | None): Adapter instance used for the operation.
                                            Defaults to None.
        failure_threshold (int): The failure threshold value. Defaults to 5.
        recovery_timeout (int): The recovery timeout value. Defaults to 60.
        half_open_max_calls (int): The half open max calls value. Defaults to 3.
        config (dict[str, object] | None): Configuration values for the operation. Defaults
                                           to None.

    Attributes:
        adapter (Any): The adapter value.
        failure_count (Any): The failure count value.
        failure_threshold (Any): The failure threshold value.
        half_open_calls (Any): The half open calls value.
        half_open_max_calls (Any): The half open max calls value.
        recovery_timeout (Any): The recovery timeout value.
        state (Any): The state value.
    """

    def __init__(
        self,
        adapter: SolidWorksAdapter | None = None,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 3,
        config: dict[str, object] | None = None,
    ) -> None:
        """Initialize the circuit breaker adapter.

        Args:
            adapter (SolidWorksAdapter | None): Adapter instance used for the operation.
                                                Defaults to None.
            failure_threshold (int): The failure threshold value. Defaults to 5.
            recovery_timeout (int): The recovery timeout value. Defaults to 60.
            half_open_max_calls (int): The half open max calls value. Defaults to 3.
            config (dict[str, object] | None): Configuration values for the operation. Defaults
                                               to None.

        Returns:
            None: None.
        """
        if adapter is None:
            from .mock_adapter import MockSolidWorksAdapter

            adapter = MockSolidWorksAdapter(config or {})
        super().__init__(config)
        self.adapter = adapter
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_calls = 0

    async def _invoke_with_optional_args(
        self,
        method: Callable[..., Awaitable[T]],
        *args: object,
    ) -> T:
        """Invoke adapter method with args, retrying without args on signature mismatch.

        Args:
            method (Callable[..., Awaitable[T]]): The method value.
            *args (object): Additional positional arguments forwarded to the call.

        Returns:
            T: The result produced by the operation.
        """
        try:
            return await method(*args)
        except TypeError:
            return await method()

    def _should_allow_request(self) -> bool:
        """Check if request should be allowed through circuit breaker.

        Returns:
            bool: True if should allow request, otherwise False.
        """
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            # Check if enough time has passed to try again
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            return self.half_open_calls < self.half_open_max_calls

    def _record_success(self) -> None:
        """Record successful operation.

        Returns:
            None: None.
        """
        if self.state in {CircuitState.OPEN, CircuitState.HALF_OPEN}:
            # An explicit recovery action, such as starting SolidWorks, is a
            # successful probe and must close a previously open breaker.
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.half_open_calls = 0
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0

    def _record_failure(self) -> None:
        """Record failed operation.

        Returns:
            None: None.
        """
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Go back to open state
            self.state = CircuitState.OPEN
        elif (
            self.state == CircuitState.CLOSED
            and self.failure_count >= self.failure_threshold
        ):
            # Open circuit breaker
            self.state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker opened after {self.failure_count} failures"
            )

    @staticmethod
    def _is_expected_unavailable_connection(exc: Exception) -> bool:
        """Recognize attach-only failures while the desktop app is stopped."""
        current: BaseException | None = exc
        while current is not None:
            if "no running solidworks instance was found" in str(current).lower():
                return True
            current = current.__cause__ or current.__context__
        return False

    async def _execute_with_circuit_breaker(
        self,
        operation_name: str,
        operation: Callable[[], Awaitable[AdapterResult[T]]],
        input_dict: dict[str, Any] | None = None,
    ) -> AdapterResult[T]:
        """Build internal execute with circuit breaker.

        Args:
            operation_name (str): The operation name value.
            operation (Callable[[], Awaitable[AdapterResult[T]]]): Callable object executed by
                                                                   the helper.
            input_dict (dict | None): Input parameters for SoC logging. Defaults to None.

        Returns:
            AdapterResult[T]: The result produced by the operation.
        """
        if not self._should_allow_request():
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Circuit breaker is {self.state.value} for {operation_name}",
                metadata={"circuit_state": self.state.value},
            )

        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1

        t0 = time.time()
        try:
            result = await operation()
            latency_ms = (time.time() - t0) * 1000.0
            if result.is_success:
                self._record_success()
            else:
                self._record_failure()
            self._soc_log(operation_name, input_dict, result, latency_ms)
            return result
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            self._record_failure()
            err_result: AdapterResult[T] = AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Circuit breaker caught exception in {operation_name}: {e}",
                metadata={"circuit_state": self.state.value},
            )
            self._soc_log(operation_name, input_dict, err_result, latency_ms)
            return err_result

    def _soc_log(
        self,
        tool_name: str,
        input_dict: dict[str, Any] | None,
        result: AdapterResult[Any],
        latency_ms: float,
    ) -> None:
        """Write a ToolCallRecord if soc_session_id is set."""
        if not self.soc_session_id or input_dict is None:
            return
        try:
            from solidworks_mcp.agents.history_db import insert_tool_call_record

            output_data: dict[str, Any] = {}
            if result.data is not None:
                try:
                    output_data = {"data": result.data}
                except Exception:  # pragma: no cover
                    output_data = {"data": str(result.data)}

            insert_tool_call_record(
                session_id=self.soc_session_id,
                tool_name=tool_name,
                input_json=json.dumps(input_dict, default=str),
                output_json=json.dumps(output_data, default=str),
                success=result.is_success,
                latency_ms=latency_ms,
                db_path=self.soc_db_path,
            )
        except Exception as exc:
            logger.debug(
                f"[soc_log] failed to write ToolCallRecord for {tool_name}: {exc}"
            )

    # Adapter interface implementation

    async def connect(self) -> None:
        """Connect through circuit breaker.

        Returns:
            None: None.

        Raises:
            Exception: If the operation cannot be completed.
        """
        if not self._should_allow_request():
            raise Exception(f"Circuit breaker is {self.state.value}")

        try:
            await self.adapter.connect()
            self._record_success()
        except Exception as exc:
            # A stopped SolidWorks process is expected before a user invokes
            # the panel's explicit start action. Repeated refreshes should not
            # open the circuit breaker in that normal state.
            if not self._is_expected_unavailable_connection(exc):
                self._record_failure()
            raise

    async def start_solidworks(
        self, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        """Explicitly launch SolidWorks through the protected backing adapter."""
        method = getattr(self.adapter, "start_solidworks", None)
        if not callable(method):
            raise Exception("The configured SolidWorks adapter does not support explicit launch")
        try:
            result = await self._invoke_with_optional_args(method, timeout_seconds)
            self._record_success()
            return result
        except Exception:
            self._record_failure()
            raise

    async def disconnect(self) -> None:
        """Disconnect - always allowed.

        Returns:
            None: None.
        """
        await self.adapter.disconnect()

    async def close_all_session_docs(self) -> AdapterResult[None]:
        """Forward scoped document cleanup without applying circuit state."""
        return await self.adapter.close_all_session_docs()

    async def pack_and_go_assembly(
        self, source_path: str, target_dir: str, name_prefix: str = ""
    ) -> AdapterResult[dict[str, Any]]:
        """Forward native assembly Pack-and-Go through the protected adapter."""
        return await self._execute_with_circuit_breaker(
            "pack_and_go_assembly",
            lambda: self.adapter.pack_and_go_assembly(  # type: ignore[attr-defined]
                source_path, target_dir, name_prefix
            ),
            input_dict={
                "source_path": source_path,
                "target_dir": target_dir,
                "name_prefix": name_prefix,
            },
        )

    def is_connected(self) -> bool:
        """Check connection status.

        Returns:
            bool: True if connected, otherwise False.
        """
        return self.adapter.is_connected()

    async def health_check(self) -> AdapterHealth:
        """Get health check with circuit breaker status.

        Returns:
            AdapterHealth: The result produced by the operation.
        """
        base_health = await self.adapter.health_check()
        if base_health.metrics is None:
            base_health.metrics = {}
        base_health.metrics["circuit_breaker"] = {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure_time": self.last_failure_time,
            "half_open_calls": self.half_open_calls,
        }

        # Consider circuit as unhealthy if open
        if self.state == CircuitState.OPEN:
            base_health.healthy = False
            base_health.connection_status = "circuit_breaker_open"

        return base_health

    # Model operations

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        """Open model through circuit breaker.

        Args:
            file_path (str): Path to the target file.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "open_model",
            lambda: self.adapter.open_model(file_path),
            input_dict={"file_path": file_path},
        )

    async def activate_model(
        self, name: str, path: str | None = None
    ) -> AdapterResult[None]:
        """Activate a tracked document through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "activate_model",
            lambda: self.adapter.activate_model(name, path),
            input_dict={"name": name, "path": path},
        )

    async def close_model(self, save: bool = False) -> AdapterResult[None]:
        """Close model through circuit breaker.

        Args:
            save (bool): The save value. Defaults to False.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "close_model",
            lambda: self.adapter.close_model(save),
            input_dict={"save": save},
        )

    async def save_file(self, file_path: str | None = None) -> AdapterResult[None]:
        """Save model through circuit breaker.

        Args:
            file_path (str | None): Path to the target file. Defaults to None.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "save_file",
            lambda: self.adapter.save_file(file_path),
            input_dict={"file_path": file_path},
        )

    async def execute_macro(
        self, params: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Provide execute macro support for the circuit breaker adapter.

        Args:
            params (dict[str, Any]): The params value.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "execute_macro",
            lambda: self.adapter.execute_macro(params),  # type: ignore[attr-defined]
            input_dict=params,
        )

    async def create_part(
        self, name: str | None = None, units: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create part through circuit breaker.

        Args:
            name (str | None): The name value. Defaults to None.
            units (str | None): The units value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """

        async def _op() -> AdapterResult[SolidWorksModel]:
            """Build internal op.

            Returns:
                AdapterResult[SolidWorksModel]: The result produced by the operation.
            """
            if name is None and units is None:
                return await self.adapter.create_part()
            return await self._invoke_with_optional_args(
                self.adapter.create_part,
                name,
                units,
            )

        return await self._execute_with_circuit_breaker(
            "create_part", _op, input_dict={"name": name, "units": units}
        )

    async def call(self, operation: Callable[[], object | Awaitable[object]]) -> object:
        """Legacy call API used by tests.

        Args:
            operation (Callable[[], object | Awaitable[object]]): Callable object executed by
                                                                  the helper.

        Returns:
            object: The result produced by the operation.

        Raises:
            RuntimeError: If the operation cannot be completed.
            Exception: Circuit breaker is open.
        """
        if not self._should_allow_request():
            raise Exception("Circuit breaker is open")
        try:
            result = operation()
            if asyncio.iscoroutine(result):
                result = await result
            self._record_success()
            return result
        except Exception as exc:
            self._record_failure()
            raise RuntimeError(str(exc)) from exc

    async def create_assembly(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create assembly through circuit breaker.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """

        async def _op() -> AdapterResult[SolidWorksModel]:
            """Build internal op.

            Returns:
                AdapterResult[SolidWorksModel]: The result produced by the operation.
            """
            if name is None:
                return await self.adapter.create_assembly()
            return await self._invoke_with_optional_args(
                self.adapter.create_assembly,
                name,
            )

        return await self._execute_with_circuit_breaker(
            "create_assembly", _op, input_dict={"name": name}
        )

    async def create_drawing(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create drawing through circuit breaker.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_drawing",
            lambda: self.adapter.create_drawing(name),
            input_dict={"name": name},
        )

    async def insert_assembly_component(
        self,
        component_path: str,
        x_m: float,
        y_m: float,
        z_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert one component through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "insert_assembly_component",
            lambda: self.adapter.insert_assembly_component(
                component_path, x_m, y_m, z_m
            ),
            input_dict={
                "component_path": component_path,
                "x_m": x_m,
                "y_m": y_m,
                "z_m": z_m,
            },
        )

    async def list_assembly_components(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active assembly components through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_assembly_components",
            lambda: self.adapter.list_assembly_components(),
            input_dict={},
        )

    async def get_assembly_component(
        self, component_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Look up one active-assembly component through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_assembly_component",
            lambda: self.adapter.get_assembly_component(component_name),
            input_dict={"component_name": component_name},
        )

    async def list_assembly_mate_entities(
        self,
        component_id: str | None = None,
        entity_types: tuple[str, ...] = ("face", "edge"),
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List internal assembly mate entities through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_assembly_mate_entities",
            lambda: self.adapter.list_assembly_mate_entities(
                component_id, entity_types
            ),
            input_dict={"component_id": component_id, "entity_types": entity_types},
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
        """Create one assembly mate through the circuit breaker without logging tokens."""
        return await self._execute_with_circuit_breaker(
            "create_assembly_mate",
            lambda: self.adapter.create_assembly_mate(
                first_persistent_reference,
                second_persistent_reference,
                mate_type=mate_type,
                alignment=alignment,
                flip=flip,
                distance_m=distance_m,
                angle_radians=angle_radians,
                for_positioning_only=for_positioning_only,
                lock_rotation=lock_rotation,
            ),
            input_dict={
                "mate_type": mate_type,
                "alignment": alignment,
                "distance_m": distance_m,
                "angle_radians": angle_radians,
            },
        )

    async def create_drawing_view(
        self,
        model_path: str,
        model_view_name: str,
        x_m: float,
        y_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Create one drawing model view through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_drawing_view",
            lambda: self.adapter.create_drawing_view(
                model_path, model_view_name, x_m, y_m
            ),
            input_dict={
                "model_path": model_path,
                "model_view_name": model_view_name,
                "x_m": x_m,
                "y_m": y_m,
            },
        )

    async def list_drawing_views(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active drawing model views through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_drawing_views",
            lambda: self.adapter.list_drawing_views(),
            input_dict={},
        )

    async def list_assembly_mates(self) -> AdapterResult[list[dict[str, Any]]]:
        """List current assembly mates through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_assembly_mates",
            lambda: self.adapter.list_assembly_mates(),
            input_dict={},
        )

    async def delete_assembly_mate(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete a workflow-owned assembly mate without logging its token."""
        return await self._execute_with_circuit_breaker(
            "delete_assembly_mate",
            lambda: self.adapter.delete_assembly_mate(persistent_reference),
            input_dict={},
        )

    async def get_drawing_view_outline(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one drawing-view outline through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_drawing_view_outline",
            lambda: self.adapter.get_drawing_view_outline(view_id),
            input_dict={"view_id": view_id},
        )

    async def create_projected_drawing_view(
        self, source_view_id: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create one projected drawing view through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_projected_drawing_view",
            lambda: self.adapter.create_projected_drawing_view(
                source_view_id, x_m, y_m
            ),
            input_dict={"source_view_id": source_view_id, "x_m": x_m, "y_m": y_m},
        )

    async def list_custom_properties(
        self, configuration_name: str | None = None
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List custom properties through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_custom_properties",
            lambda: self.adapter.list_custom_properties(configuration_name),
            input_dict={"configuration_name": configuration_name},
        )

    async def set_custom_property(
        self, name: str, value: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Set one custom property through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_custom_property",
            lambda: self.adapter.set_custom_property(name, value, configuration_name),
            input_dict={
                "name": name,
                "value": value,
                "configuration_name": configuration_name,
            },
        )

    # Feature operations

    async def create_sheet_metal_base_flange(
        self,
        sketch_name: str,
        thickness_mm: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a base flange through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_sheet_metal_base_flange",
            lambda: self.adapter.create_sheet_metal_base_flange(
                sketch_name, thickness_mm, bend_radius_mm, k_factor
            ),
            input_dict={
                "thickness_mm": thickness_mm,
                "bend_radius_mm": bend_radius_mm,
                "k_factor": k_factor,
            },
        )

    async def create_sheet_metal_edge_flange(
        self,
        edge_persistent_reference: str,
        length_mm: float,
        angle_degrees: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create an edge flange through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_sheet_metal_edge_flange",
            lambda: self.adapter.create_sheet_metal_edge_flange(
                edge_persistent_reference,
                length_mm,
                angle_degrees,
                bend_radius_mm,
                k_factor,
            ),
            input_dict={
                "length_mm": length_mm,
                "angle_degrees": angle_degrees,
                "bend_radius_mm": bend_radius_mm,
                "k_factor": k_factor,
            },
        )

    async def set_sheet_metal_k_factor(
        self, k_factor: float
    ) -> AdapterResult[dict[str, Any]]:
        """Set a sheet-metal K factor through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_sheet_metal_k_factor",
            lambda: self.adapter.set_sheet_metal_k_factor(k_factor),
            input_dict={"k_factor": k_factor},
        )

    async def get_sheet_metal_info(self) -> AdapterResult[dict[str, Any]]:
        """Read sheet-metal state through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_sheet_metal_info",
            lambda: self.adapter.get_sheet_metal_info(),
            input_dict={},
        )

    async def set_sheet_metal_flat_state(
        self, flattened: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Set sheet-metal flat state through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_sheet_metal_flat_state",
            lambda: self.adapter.set_sheet_metal_flat_state(flattened),
            input_dict={"flattened": flattened},
        )

    async def export_flat_pattern_dxf(
        self, file_path: str, model_path: str, sheet_metal_options: int
    ) -> AdapterResult[dict[str, Any]]:
        """Export one flat pattern through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "export_flat_pattern_dxf",
            lambda: self.adapter.export_flat_pattern_dxf(
                file_path, model_path, sheet_metal_options
            ),
            input_dict={"sheet_metal_options": sheet_metal_options},
        )

    async def create_shell(
        self, face_persistent_references: list[str], thickness_mm: float, outward: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a shell through the circuit breaker without logging face tokens."""
        return await self._execute_with_circuit_breaker(
            "create_shell",
            lambda: self.adapter.create_shell(
                face_persistent_references, thickness_mm, outward
            ),
            input_dict={
                "face_count": len(face_persistent_references),
                "thickness_mm": thickness_mm,
                "outward": outward,
            },
        )

    async def create_rib(
        self,
        sketch_name: str,
        thickness_mm: float,
        draft_angle_degrees: float,
        direction: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a rib through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_rib",
            lambda: self.adapter.create_rib(
                sketch_name, thickness_mm, draft_angle_degrees, direction
            ),
            input_dict={
                "thickness_mm": thickness_mm,
                "draft_angle_degrees": draft_angle_degrees,
                "direction": direction,
            },
        )

    async def create_dome(
        self, face_persistent_reference: str, height_mm: float, reverse_direction: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a dome through the circuit breaker without logging its face token."""
        return await self._execute_with_circuit_breaker(
            "create_dome",
            lambda: self.adapter.create_dome(
                face_persistent_reference, height_mm, reverse_direction
            ),
            input_dict={"height_mm": height_mm, "reverse_direction": reverse_direction},
        )

    async def create_extrusion(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create extrusion through circuit breaker.

        Args:
            params (ExtrusionParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_extrusion",
            lambda: self.adapter.create_extrusion(params),
            input_dict=_to_input_dict(params),
        )

    async def create_cut_extrude(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create cut-extrude through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_cut_extrude",
            lambda: self.adapter.create_cut_extrude(params),
            input_dict=_to_input_dict(params),
        )

    async def add_fillet(
        self, radius: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Add fillet through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_fillet",
            lambda: self.adapter.add_fillet(radius, edge_names),
            input_dict={"radius": radius, "edge_names": edge_names},
        )

    async def add_chamfer(
        self, distance: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Add an equal-distance chamfer through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_chamfer",
            lambda: self.adapter.add_chamfer(distance, edge_names),
            input_dict={"distance": distance, "edge_names": edge_names},
        )

    async def create_chamfer(
        self,
        edge_persistent_references: list[str],
        distance_mm: float,
        angle_degrees: float,
        chamfer_type: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a persistent-reference chamfer through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_chamfer",
            lambda: self.adapter.create_chamfer(
                edge_persistent_references, distance_mm, angle_degrees, chamfer_type
            ),
            input_dict={
                "edge_count": len(edge_persistent_references),
                "distance_mm": distance_mm,
                "angle_degrees": angle_degrees,
                "chamfer_type": chamfer_type,
            },
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
        """Create a section drawing view through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_section_drawing_view",
            lambda: self.adapter.create_section_drawing_view(
                source_view_id,
                cut_start_x_m,
                cut_start_y_m,
                cut_end_x_m,
                cut_end_y_m,
                x_m,
                y_m,
            ),
            input_dict={"source_view_id": source_view_id, "x_m": x_m, "y_m": y_m},
        )

    async def create_auxiliary_drawing_view(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create an auxiliary drawing view through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_auxiliary_drawing_view",
            lambda: self.adapter.create_auxiliary_drawing_view(
                persistent_reference, x_m, y_m
            ),
            input_dict={"x_m": x_m, "y_m": y_m},
        )

    async def activate_drawing_view(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one drawing view through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "activate_drawing_view",
            lambda: self.adapter.activate_drawing_view(view_id),
            input_dict={"view_id": view_id},
        )

    async def list_drawing_sheets(self) -> AdapterResult[list[dict[str, Any]]]:
        """List drawing sheets through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_drawing_sheets",
            self.adapter.list_drawing_sheets,
            input_dict={},
        )

    async def activate_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one drawing sheet through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "activate_drawing_sheet",
            lambda: self.adapter.activate_drawing_sheet(sheet_name),
            input_dict={"sheet_name": sheet_name},
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
        """Create one drawing sheet through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_drawing_sheet",
            lambda: self.adapter.create_drawing_sheet(
                sheet_name,
                paper_size,
                width_m,
                height_m,
                first_angle_projection,
                scale_numerator,
                scale_denominator,
                zone_rows,
                zone_columns,
            ),
            input_dict={
                "sheet_name": sheet_name,
                "paper_size": paper_size,
                "width_m": width_m,
                "height_m": height_m,
                "first_angle_projection": first_angle_projection,
                "scale_numerator": scale_numerator,
                "scale_denominator": scale_denominator,
                "zone_rows": zone_rows,
                "zone_columns": zone_columns,
            },
        )

    async def delete_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one drawing sheet through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_drawing_sheet",
            lambda: self.adapter.delete_drawing_sheet(sheet_name),
            input_dict={"sheet_name": sheet_name},
        )

    async def rename_drawing_sheet(
        self, sheet_name: str, new_sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Rename one drawing sheet through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "rename_drawing_sheet",
            lambda: self.adapter.rename_drawing_sheet(sheet_name, new_sheet_name),
            input_dict={"sheet_name": sheet_name, "new_sheet_name": new_sheet_name},
        )

    async def reorder_drawing_sheets(self, sheet_names: list[str]) -> AdapterResult[list[dict[str, Any]]]:
        return await self._execute_with_circuit_breaker("reorder_drawing_sheets", lambda: self.adapter.reorder_drawing_sheets(sheet_names), input_dict={"sheet_names": sheet_names})

    async def update_drawing_view_layout(
        self,
        view_id: str,
        x_m: float | None,
        y_m: float | None,
        scale_decimal: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Update one drawing view layout through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "update_drawing_view_layout",
            lambda: self.adapter.update_drawing_view_layout(
                view_id, x_m, y_m, scale_decimal
            ),
            input_dict={
                "view_id": view_id,
                "x_m": x_m,
                "y_m": y_m,
                "scale_decimal": scale_decimal,
            },
        )

    async def align_drawing_views(
        self,
        view_ids: list[str],
        direction: str,
        gap_m: float,
        start_x_m: float | None,
        start_y_m: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Arrange drawing views through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "align_drawing_views",
            lambda: self.adapter.align_drawing_views(
                view_ids, direction, gap_m, start_x_m, start_y_m
            ),
            input_dict={
                "view_ids": view_ids,
                "direction": direction,
                "gap_m": gap_m,
                "start_x_m": start_x_m,
                "start_y_m": start_y_m,
            },
        )

    async def rebuild_drawing(self) -> AdapterResult[dict[str, Any]]:
        """Rebuild and save the active drawing through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "rebuild_drawing",
            lambda: self.adapter.rebuild_drawing(),
            input_dict={},
        )

    async def update_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Update a workflow-owned drawing BOM through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "update_drawing_bom",
            lambda: self.adapter.update_drawing_bom(persistent_reference),
            input_dict={},
        )

    async def delete_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete a workflow-owned drawing BOM through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_drawing_bom",
            lambda: self.adapter.delete_drawing_bom(persistent_reference),
            input_dict={},
        )

    async def create_drawing_revision_table(
        self, x_m: float, y_m: float, anchor_type: int, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create a revision table through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_drawing_revision_table",
            lambda: self.adapter.create_drawing_revision_table(
                x_m, y_m, anchor_type, revision
            ),
            input_dict={
                "x_m": x_m,
                "y_m": y_m,
                "anchor_type": anchor_type,
                "revision": revision,
            },
        )

    async def create_drawing_ordinate_dimension(
        self, persistent_reference: str, x_m: float, y_m: float, ordinate_type: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_circuit_breaker(
            "create_drawing_ordinate_dimension",
            lambda: self.adapter.create_drawing_ordinate_dimension(
                persistent_reference, x_m, y_m, ordinate_type
            ),
            input_dict={"persistent_reference": persistent_reference, "x_m": x_m, "y_m": y_m, "ordinate_type": ordinate_type},
        )

    async def create_drawing_surface_finish(
        self, persistent_reference: str, x_m: float, y_m: float, symbol: str, value: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_circuit_breaker(
            "create_drawing_surface_finish",
            lambda: self.adapter.create_drawing_surface_finish(
                persistent_reference, x_m, y_m, symbol, value
            ),
            input_dict={"persistent_reference": persistent_reference, "x_m": x_m, "y_m": y_m, "symbol": symbol, "value": value},
        )

    async def create_drawing_weld_symbol(
        self, persistent_reference: str, x_m: float, y_m: float, weld_type: str, size: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_circuit_breaker(
            "create_drawing_weld_symbol",
            lambda: self.adapter.create_drawing_weld_symbol(
                persistent_reference, x_m, y_m, weld_type, size
            ),
            input_dict={"persistent_reference": persistent_reference, "x_m": x_m, "y_m": y_m, "weld_type": weld_type, "size": size},
        )

    async def create_drawing_geometric_tolerance(
        self, persistent_reference: str, x_m: float, y_m: float, tolerance: str, datum: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_circuit_breaker(
            "create_drawing_geometric_tolerance",
            lambda: self.adapter.create_drawing_geometric_tolerance(
                persistent_reference, x_m, y_m, tolerance, datum
            ),
            input_dict={"persistent_reference": persistent_reference, "x_m": x_m, "y_m": y_m, "tolerance": tolerance, "datum": datum},
        )

    async def create_drawing_center_mark(
        self, persistent_reference: str, size_m: float, show_connection_lines: bool
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_circuit_breaker(
            "create_drawing_center_mark",
            lambda: self.adapter.create_drawing_center_mark(
                persistent_reference, size_m, show_connection_lines
            ),
            input_dict={"persistent_reference": persistent_reference, "size_m": size_m, "show_connection_lines": show_connection_lines},
        )

    async def list_drawing_revisions(
        self, persistent_reference: str
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List revision rows through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_drawing_revisions",
            lambda: self.adapter.list_drawing_revisions(persistent_reference),
            input_dict={"persistent_reference": persistent_reference},
        )

    async def add_drawing_revision(
        self, persistent_reference: str, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Add one revision row through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_drawing_revision",
            lambda: self.adapter.add_drawing_revision(persistent_reference, revision),
            input_dict={"persistent_reference": persistent_reference, "revision": revision},
        )

    async def delete_drawing_revision(
        self, persistent_reference: str, revision_id: int, delete_symbols: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one revision row through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_drawing_revision",
            lambda: self.adapter.delete_drawing_revision(
                persistent_reference, revision_id, delete_symbols
            ),
            input_dict={
                "persistent_reference": persistent_reference,
                "revision_id": revision_id,
                "delete_symbols": delete_symbols,
            },
        )

    async def list_part_topology(
        self, entity_types: tuple[str, ...] = ("face", "edge")
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List active part topology through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_part_topology",
            lambda: self.adapter.list_part_topology(entity_types),
            input_dict={"entity_types": entity_types},
        )

    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create revolve through circuit breaker.

        Args:
            params (RevolveParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_revolve",
            lambda: self.adapter.create_revolve(params),
            input_dict=_to_input_dict(params),
        )

    async def create_reference_plane(
        self, params: ReferencePlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create an offset reference plane through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_reference_plane",
            lambda: self.adapter.create_reference_plane(params),
            input_dict=_to_input_dict(params),
        )

    async def get_reference_axis(
        self, params: ReferenceAxisParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Resolve a standard origin axis through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_reference_axis",
            lambda: self.adapter.get_reference_axis(params),
            input_dict=_to_input_dict(params),
        )

    async def get_reference_plane(
        self, params: OriginPlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Resolve a standard origin plane through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_reference_plane",
            lambda: self.adapter.get_reference_plane(params),
            input_dict=_to_input_dict(params),
        )

    async def create_feature_linear_pattern(
        self, params: FeatureLinearPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a feature linear pattern through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_feature_linear_pattern",
            lambda: self.adapter.create_feature_linear_pattern(params),
            input_dict=_to_input_dict(params),
        )

    async def create_feature_circular_pattern(
        self, params: FeatureCircularPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a feature circular pattern through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_feature_circular_pattern",
            lambda: self.adapter.create_feature_circular_pattern(params),
            input_dict=_to_input_dict(params),
        )

    async def mirror_feature(
        self, params: FeatureMirrorParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mirror one feature through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "mirror_feature",
            lambda: self.adapter.mirror_feature(params),
            input_dict=_to_input_dict(params),
        )

    async def create_hole_wizard(
        self, params: HoleWizardParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a controlled Hole Wizard feature through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_hole_wizard",
            lambda: self.adapter.create_hole_wizard(params),
            input_dict=_to_input_dict(params),
        )

    async def create_tapped_hole(
        self, params: ThreadedHoleParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a tapped Hole Wizard feature through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_tapped_hole",
            lambda: self.adapter.create_tapped_hole(params),
            input_dict=_to_input_dict(params),
        )

    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create sweep through circuit breaker.

        Args:
            params (SweepParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_sweep",
            lambda: self.adapter.create_sweep(params),
            input_dict=_to_input_dict(params),
        )

    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create loft through circuit breaker.

        Args:
            params (LoftParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_loft",
            lambda: self.adapter.create_loft(params),
            input_dict=_to_input_dict(params),
        )

    # Sketch operations

    async def create_sketch(self, plane: str) -> AdapterResult[str]:
        """Create sketch through circuit breaker.

        Args:
            plane (str): The plane value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "create_sketch",
            lambda: self.adapter.create_sketch(plane),
            input_dict={"plane": plane},
        )

    async def create_3d_sketch(self) -> AdapterResult[str]:
        """Create a 3D sketch through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_3d_sketch", lambda: self.adapter.create_3d_sketch(), input_dict={}
        )

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add line through circuit breaker.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_line",
            lambda: self.adapter.add_line(x1, y1, x2, y2),
            input_dict={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        )

    async def add_3d_line(
        self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float
    ) -> AdapterResult[str]:
        """Add a 3D line through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_3d_line",
            lambda: self.adapter.add_3d_line(x1, y1, z1, x2, y2, z2),
            input_dict={"x1": x1, "y1": y1, "z1": z1, "x2": x2, "y2": y2, "z2": z2},
        )

    async def convert_sketch_entities(
        self, edge_persistent_references: list[str]
    ) -> AdapterResult[str]:
        """Convert workflow-owned edges through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "convert_sketch_entities",
            lambda: self.adapter.convert_sketch_entities(edge_persistent_references),
            input_dict={"edge_count": len(edge_persistent_references)},
        )

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add centerline through circuit breaker.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_centerline",
            lambda: self.adapter.add_centerline(x1, y1, x2, y2),
            input_dict={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        )

    async def add_sketch_text(
        self, text: str, x: float, y: float, height: float, angle_degrees: float
    ) -> AdapterResult[str]:
        """Add sketch text through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_sketch_text",
            lambda: self.adapter.add_sketch_text(
                text, x, y, height, angle_degrees
            ),
            input_dict={
                "text": text,
                "x": x,
                "y": y,
                "height": height,
                "angle_degrees": angle_degrees,
            },
        )

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        """Add circle through circuit breaker.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_circle",
            lambda: self.adapter.add_circle(center_x, center_y, radius),
            input_dict={"center_x": center_x, "center_y": center_y, "radius": radius},
        )

    async def add_point(self, x: float, y: float) -> AdapterResult[str]:
        """Add one sketch point through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_point",
            lambda: self.adapter.add_point(x, y),
            input_dict={"x": x, "y": y},
        )

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add rectangle through circuit breaker.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_rectangle",
            lambda: self.adapter.add_rectangle(x1, y1, x2, y2),
            input_dict={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
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
        """Add arc through circuit breaker.

        Args:
            center_x (float): Arc center X coordinate.
            center_y (float): Arc center Y coordinate.
            start_x (float): Arc start X coordinate.
            start_y (float): Arc start Y coordinate.
            end_x (float): Arc end X coordinate.
            end_y (float): Arc end Y coordinate.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "add_arc",
            lambda: self.adapter.add_arc(
                center_x,
                center_y,
                start_x,
                start_y,
                end_x,
                end_y,
            ),
            input_dict={
                "center_x": center_x,
                "center_y": center_y,
                "start_x": start_x,
                "start_y": start_y,
                "end_x": end_x,
                "end_y": end_y,
            },
        )

    async def add_spline(self, points: list[dict[str, float]]) -> AdapterResult[str]:
        """Add spline through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_spline",
            lambda: self.adapter.add_spline(points),
            input_dict={"points": points},
        )

    async def add_polygon(
        self, center_x: float, center_y: float, radius: float, sides: int
    ) -> AdapterResult[str]:
        """Add polygon through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_polygon",
            lambda: self.adapter.add_polygon(center_x, center_y, radius, sides),
            input_dict={
                "center_x": center_x,
                "center_y": center_y,
                "radius": radius,
                "sides": sides,
            },
        )

    async def add_ellipse(
        self,
        center_x: float,
        center_y: float,
        major_axis: float,
        minor_axis: float,
    ) -> AdapterResult[str]:
        """Add ellipse through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_ellipse",
            lambda: self.adapter.add_ellipse(
                center_x, center_y, major_axis, minor_axis
            ),
            input_dict={
                "center_x": center_x,
                "center_y": center_y,
                "major_axis": major_axis,
                "minor_axis": minor_axis,
            },
        )

    async def sketch_linear_pattern(
        self,
        entities: list[str],
        direction_x: float,
        direction_y: float,
        spacing: float,
        count: int,
    ) -> AdapterResult[str]:
        """Sketch linear pattern through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_linear_pattern",
            lambda: self.adapter.sketch_linear_pattern(
                entities, direction_x, direction_y, spacing, count
            ),
            input_dict={
                "entities": entities,
                "direction_x": direction_x,
                "direction_y": direction_y,
                "spacing": spacing,
                "count": count,
            },
        )

    async def sketch_circular_pattern(
        self,
        entities: list[str],
        angle: float,
        count: int,
    ) -> AdapterResult[str]:
        """Sketch circular pattern through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_circular_pattern",
            lambda: self.adapter.sketch_circular_pattern(entities, angle, count),
            input_dict={"entities": entities, "angle": angle, "count": count},
        )

    async def sketch_mirror(
        self, entities: list[str], mirror_line: str
    ) -> AdapterResult[str]:
        """Sketch mirror through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_mirror",
            lambda: self.adapter.sketch_mirror(entities, mirror_line),
            input_dict={"entities": entities, "mirror_line": mirror_line},
        )

    async def sketch_offset(
        self,
        entities: list[str],
        offset_distance: float,
        reverse_direction: bool,
    ) -> AdapterResult[str]:
        """Sketch offset through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_offset",
            lambda: self.adapter.sketch_offset(
                entities, offset_distance, reverse_direction
            ),
            input_dict={
                "entities": entities,
                "offset_distance": offset_distance,
                "reverse_direction": reverse_direction,
            },
        )

    async def sketch_trim(
        self, entity_id: str, trim_x: float, trim_y: float
    ) -> AdapterResult[str]:
        """Trim one active-sketch entity through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "sketch_trim",
            lambda: self.adapter.sketch_trim(entity_id, trim_x, trim_y),
            input_dict={"entity_id": entity_id, "trim_x": trim_x, "trim_y": trim_y},
        )

    async def add_sketch_constraint(
        self,
        entity1: str,
        entity2: str | None,
        relation_type: str,
        entity3: str | None = None,
    ) -> AdapterResult[str]:
        """Add sketch constraint through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_sketch_constraint",
            lambda: self.adapter.add_sketch_constraint(
                entity1,
                entity2,
                relation_type,
                entity3,
            ),
            input_dict={
                "entity1": entity1,
                "entity2": entity2,
                "relation_type": relation_type,
                "entity3": entity3,
            },
        )

    async def add_sketch_dimension(
        self,
        entity1: str,
        entity2: str | None,
        dimension_type: str,
        value: float,
    ) -> AdapterResult[str]:
        """Add sketch dimension through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "add_sketch_dimension",
            lambda: self.adapter.add_sketch_dimension(
                entity1,
                entity2,
                dimension_type,
                value,
            ),
            input_dict={
                "entity1": entity1,
                "entity2": entity2,
                "dimension_type": dimension_type,
                "value": value,
            },
        )

    async def check_sketch_fully_defined(
        self, sketch_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Check sketch definition status through circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "check_sketch_fully_defined",
            lambda: self.adapter.check_sketch_fully_defined(sketch_name),
            input_dict={"sketch_name": sketch_name},
        )

    async def check_sketch_feature_use(
        self, sketch_name: str | None = None, feature_use: str = "boss_extrude"
    ) -> AdapterResult[dict[str, Any]]:
        """Check native feature-use validity through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "check_sketch_feature_use",
            lambda: self.adapter.check_sketch_feature_use(sketch_name, feature_use),
            input_dict={"sketch_name": sketch_name, "feature_use": feature_use},
        )

    async def exit_sketch(self) -> AdapterResult[None]:
        """Exit sketch through circuit breaker.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "exit_sketch",
            lambda: self.adapter.exit_sketch(),
            input_dict={},
        )

    # Analysis operations

    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        """Get mass properties through circuit breaker.

        Returns:
            AdapterResult[MassProperties]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "get_mass_properties",
            lambda: self.adapter.get_mass_properties(),
            input_dict={},
        )

    async def get_bounding_box(self) -> AdapterResult[dict[str, list[float]]]:
        """Read active-part extents through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_bounding_box",
            lambda: self.adapter.get_bounding_box(),
            input_dict={},
        )

    async def list_equations(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-part equations through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_equations",
            lambda: self.adapter.list_equations(),
            input_dict={},
        )

    async def set_equation(
        self, name: str, expression: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create or update an equation through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "set_equation",
            lambda: self.adapter.set_equation(name, expression),
            input_dict={"name": name, "expression": expression},
        )

    async def delete_equation(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Delete an equation through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "delete_equation",
            lambda: self.adapter.delete_equation(name),
            input_dict={"name": name},
        )

    async def get_model_info(self) -> AdapterResult[dict[str, object]]:
        """Get active model metadata through circuit breaker.

        Returns:
            AdapterResult[dict[str, object]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "get_model_info",
            lambda: self.adapter.get_model_info(),
            input_dict={},
        )

    async def list_features(
        self, include_suppressed: bool = False
    ) -> AdapterResult[list[dict[str, object]]]:
        """List model features through circuit breaker.

        Args:
            include_suppressed (bool): The include suppressed value. Defaults to False.

        Returns:
            AdapterResult[list[dict[str, object]]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "list_features",
            lambda: self.adapter.list_features(include_suppressed),
            input_dict={"include_suppressed": include_suppressed},
        )

    async def list_open_models(self) -> AdapterResult[list[dict[str, Any]]]:
        """List open SolidWorks documents through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_open_models",
            lambda: self.adapter.list_open_models(),
            input_dict={},
        )

    async def list_configurations(self) -> AdapterResult[list[str]]:
        """List model configurations through circuit breaker.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "list_configurations",
            lambda: self.adapter.list_configurations(),
            input_dict={},
        )

    async def get_configuration(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one configuration through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_configuration",
            lambda: self.adapter.get_configuration(configuration_name),
            input_dict={"configuration_name": configuration_name},
        )

    async def get_selection_state(self) -> AdapterResult[dict[str, Any]]:
        """Read the active selection through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "get_selection_state",
            lambda: self.adapter.get_selection_state(),
            input_dict={},
        )

    async def create_configuration(
        self, configuration_name: str, description: str = ""
    ) -> AdapterResult[str]:
        """Create a model configuration through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "create_configuration",
            lambda: self.adapter.create_configuration(configuration_name, description),
            input_dict={
                "configuration_name": configuration_name,
                "description": description,
            },
        )

    async def activate_configuration(
        self, configuration_name: str
    ) -> AdapterResult[str]:
        """Activate a model configuration through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "activate_configuration",
            lambda: self.adapter.activate_configuration(configuration_name),
            input_dict={"configuration_name": configuration_name},
        )

    # Export operations

    async def export_image(self, payload: dict) -> AdapterResult[dict]:
        """Export viewport image through circuit breaker.

        Args:
            payload (dict): The payload value.

        Returns:
            AdapterResult[dict]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "export_image",
            lambda: self.adapter.export_image(payload),
            input_dict=payload,
        )

    async def export_file(
        self, file_path: str, format_type: str
    ) -> AdapterResult[None]:
        """Export file through circuit breaker.

        Args:
            file_path (str): Path to the target file.
            format_type (str): The format type value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "export_file",
            lambda: self.adapter.export_file(file_path, format_type),
            input_dict={"file_path": file_path, "format_type": format_type},
        )

    async def export_drawing_cad(
        self,
        file_path: str,
        format_type: str,
        model_space: bool = True,
        compatibility_profile: str = "standard",
    ) -> AdapterResult[None]:
        """Export a drawing as model-space or paper-space DWG/DXF."""
        return await self._execute_with_circuit_breaker(
            "export_drawing_cad",
            lambda: self.adapter.export_drawing_cad(
                file_path, format_type, model_space, compatibility_profile
            ),
            input_dict={
                "file_path": file_path,
                "format_type": format_type,
                "model_space": model_space,
                "compatibility_profile": compatibility_profile,
            },
        )

    # Dimension operations

    async def list_dimensions(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-model dimensions through the circuit breaker."""
        return await self._execute_with_circuit_breaker(
            "list_dimensions",
            lambda: self.adapter.list_dimensions(),
        )

    async def get_dimension(self, name: str) -> AdapterResult[float]:
        """Get dimension through circuit breaker.

        Args:
            name (str): The name value.

        Returns:
            AdapterResult[float]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "get_dimension",
            lambda: self.adapter.get_dimension(name),
            input_dict={"name": name},
        )

    async def set_dimension(self, name: str, value: float) -> AdapterResult[None]:
        """Set dimension through circuit breaker.

        Args:
            name (str): The name value.
            value (float): The value value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_circuit_breaker(
            "set_dimension",
            lambda: self.adapter.set_dimension(name, value),
            input_dict={"name": name, "value": value},
        )

    # SolidWorks-as-Code checkpoint

    async def soc_create_checkpoint(
        self,
        label: str,
        file_path: str,
        *,
        feature_tree: list[dict[str, Any]] | None = None,
    ) -> int | None:
        """Create a named SoC checkpoint after saving the model.

        Records a SoCCheckpoint row and a ModelStateSnapshot.  Call this
        immediately after ``save_file`` to mark a stable rollback point.

        Args:
            label (str): Short human name (e.g. "base-extrude").
            file_path (str): Path of the .sldprt that was just saved.
            feature_tree (list | None): Optional feature list from list_features().

        Returns:
            int | None: The new SoCCheckpoint.id, or None if soc_session_id is unset.
        """
        if not self.soc_session_id:
            return None
        try:
            from solidworks_mcp.agents.history_db import (
                create_soc_checkpoint,
                insert_model_state_snapshot,
                list_tool_call_records,
            )

            records = list_tool_call_records(
                self.soc_session_id, db_path=self.soc_db_path
            )
            last_id = records[-1]["id"] if records else None

            snapshot_id: int | None = None
            if feature_tree is not None:
                import json as _json

                insert_model_state_snapshot(
                    session_id=self.soc_session_id,
                    model_path=file_path,
                    feature_tree_json=_json.dumps(feature_tree, default=str),
                    db_path=self.soc_db_path,
                )
                from solidworks_mcp.agents.history_db import list_model_state_snapshots

                snaps = list_model_state_snapshots(
                    self.soc_session_id, db_path=self.soc_db_path
                )
                if snaps:
                    snapshot_id = snaps[0]["id"]

            return create_soc_checkpoint(
                session_id=self.soc_session_id,
                label=label,
                file_path=file_path,
                last_record_id=last_id,
                snapshot_id=snapshot_id,
                db_path=self.soc_db_path,
            )
        except Exception as exc:
            logger.debug(
                f"[soc_checkpoint] failed to create checkpoint {label!r}: {exc}"
            )
            return None


class CircuitBreaker:
    """Legacy standalone circuit breaker class expected by tests.

    Args:
        failure_threshold (int): The failure threshold value. Defaults to 5.
        recovery_timeout (float): The recovery timeout value. Defaults to 60.0.
        expected_exception (type[Exception]): The expected exception value. Defaults to
                                              Exception.

    Attributes:
        expected_exception (Any): The expected exception value.
        failure_count (Any): The failure count value.
        failure_threshold (Any): The failure threshold value.
        last_failure_time (Any): The last failure time value.
        recovery_timeout (Any): The recovery timeout value.
        state (Any): The state value.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type[Exception] = Exception,
    ) -> None:
        """Initialize the circuit breaker.

        Args:
            failure_threshold (int): The failure threshold value. Defaults to 5.
            recovery_timeout (float): The recovery timeout value. Defaults to 60.0.
            expected_exception (type[Exception]): The expected exception value. Defaults to
                                                  Exception.

        Returns:
            None: None.
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    async def call(self, operation: Callable[[], object | Awaitable[object]]) -> object:
        """Provide call support for the circuit breaker.

        Args:
            operation (Callable[[], object | Awaitable[object]]): Callable object executed by
                                                                  the helper.

        Returns:
            object: The result produced by the operation.

        Raises:
            Exception: Circuit breaker is open.
        """
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time < self.recovery_timeout:
                raise Exception("Circuit breaker is open")
            self.state = CircuitState.HALF_OPEN

        try:
            result = operation()
            if asyncio.iscoroutine(result):
                result = await result
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            return result
        except self.expected_exception:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
            raise
