"""Connection pool adapter for managing multiple SolidWorks connections.

Provides connection pooling capabilities to allow parallel SolidWorks operations when
multiple instances are available.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
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


class ConnectionPoolAdapter(SolidWorksAdapter):
    """Connection pool wrapper for SolidWorks adapters.

    Args:
        adapter_factory (Callable[[], SolidWorksAdapter] | None): Factory callable used to
                                                                  create adapter instances.
                                                                  Defaults to None.
        pool_size (int): Number of adapters to maintain in the pool. Defaults to 3.
        max_retries (int): Maximum number of retry attempts. Defaults to 3.
        create_connection (Callable[[], SolidWorksAdapter] | None): Factory callable used to
                                                                    create a connection.
                                                                    Defaults to None.
        max_size (int | None): Maximum number of items allowed in the pool. Defaults to
                               None.
        timeout (float | None): Maximum time to wait in seconds. Defaults to None.
        config (dict[str, object] | None): Configuration values for the operation. Defaults
                                           to None.

    Attributes:
        _lock (Any): The lock value.
        adapter_factory (Any): The adapter factory value.
        max_retries (Any): The max retries value.
        pool_initialized (Any): The pool initialized value.
        pool_size (Any): The pool size value.
        timeout (Any): The timeout value.
    """

    def __init__(
        self,
        adapter_factory: Callable[[], SolidWorksAdapter] | None = None,
        pool_size: int = 3,
        max_retries: int = 3,
        create_connection: Callable[[], SolidWorksAdapter] | None = None,
        max_size: int | None = None,
        timeout: float | None = None,
        config: dict[str, object] | None = None,
    ) -> None:
        """Initialize the connection pool adapter.

        Args:
            adapter_factory (Callable[[], SolidWorksAdapter] | None): Factory callable used to
                                                                      create adapter instances.
                                                                      Defaults to None.
            pool_size (int): Number of adapters to maintain in the pool. Defaults to 3.
            max_retries (int): Maximum number of retry attempts. Defaults to 3.
            create_connection (Callable[[], SolidWorksAdapter] | None): Factory callable used to
                                                                        create a connection.
                                                                        Defaults to None.
            max_size (int | None): Maximum number of items allowed in the pool. Defaults to
                                   None.
            timeout (float | None): Maximum time to wait in seconds. Defaults to None.
            config (dict[str, object] | None): Configuration values for the operation. Defaults
                                               to None.

        Returns:
            None: None.
        """
        if adapter_factory is None and create_connection is not None:
            adapter_factory = create_connection
        if max_size is not None:
            pool_size = max_size
        if adapter_factory is None:
            from .mock_adapter import MockSolidWorksAdapter

            def adapter_factory() -> MockSolidWorksAdapter:
                """Provide adapter factory support for the connection pool adapter.

                Returns:
                    MockSolidWorksAdapter: The result produced by the operation.
                """

                return MockSolidWorksAdapter(config or {})

        super().__init__(config)
        self.adapter_factory = adapter_factory
        self.pool_size = pool_size
        self.max_retries = max_retries

        self.pool: list[SolidWorksAdapter] = []
        self.available_adapters: asyncio.Queue[SolidWorksAdapter] = asyncio.Queue()
        self.pool_initialized = False
        self._lock = asyncio.Lock()
        self.timeout = timeout if timeout is not None else 30.0

    async def run_native(self, callback: Any) -> Any:
        """Run compound native work using one leased STA adapter, without retries."""
        adapter = await self._get_adapter()
        try:
            return await adapter.run_native(callback)
        finally:
            await self._return_adapter(adapter)

    async def _attempt_async(
        self, operation: Callable[[], Awaitable[T]], default: T | None = None
    ) -> T | None:
        """Run an async operation and return default on failure.

        Args:
            operation (Callable[[], Awaitable[T]]): Callable object executed by the helper.
            default (T | None): Fallback value returned when the operation fails. Defaults to
                                None.

        Returns:
            T | None: The result produced by the operation.
        """
        try:
            return await operation()
        except Exception:
            return default

    async def _attempt_async_with_error(
        self, operation: Callable[[], Awaitable[T]]
    ) -> tuple[T | None, Exception | None]:
        """Run an async operation and return (result, error).

        Args:
            operation (Callable[[], Awaitable[T]]): Callable object executed by the helper.

        Returns:
            tuple[T | None, Exception | None]: A tuple containing the resulting values.
        """
        try:
            return await operation(), None
        except Exception as exc:
            return None, exc

    def _attempt_sync(
        self, operation: Callable[[], T], default: T | None = None
    ) -> T | None:
        """Run a sync operation and return default on failure.

        Args:
            operation (Callable[[], T]): Callable object executed by the helper.
            default (T | None): Fallback value returned when the operation fails. Defaults to
                                None.

        Returns:
            T | None: The result produced by the operation.
        """
        try:
            return operation()
        except Exception:
            return default

    async def _invoke_with_optional_args(
        self,
        adapter: SolidWorksAdapter,
        method_name: str,
        *args: object,
    ) -> object:
        """Invoke adapter method with args, retrying without args on signature mismatch.

        Args:
            adapter (SolidWorksAdapter): Adapter instance used for the operation.
            method_name (str): Name of the method to invoke.
            *args (object): Additional positional arguments forwarded to the call.

        Returns:
            object: The result produced by the operation.
        """
        method = getattr(adapter, method_name)
        try:
            return await method(*args)
        except TypeError:
            return await method()

    async def _replace_failed_adapter(self) -> Exception | None:
        """Create, connect, and return a replacement adapter.

        Returns None on success, or the captured exception on failure.

        Returns:
            Exception | None: The result produced by the operation.
        """
        try:
            new_adapter = self.adapter_factory()
            await new_adapter.connect()
            await self._return_adapter(new_adapter)
            return None
        except Exception as exc:
            return exc

    @property
    def size(self) -> int:
        """Provide size support for the connection pool adapter.

        Returns:
            int: The computed numeric result.
        """
        return len(self.pool)

    @property
    def active_connections(self) -> int:
        """Provide active connections support for the connection pool adapter.

        Returns:
            int: The computed numeric result.
        """
        return max(0, len(self.pool) - self.available_adapters.qsize())

    async def acquire(self) -> SolidWorksAdapter:
        """Provide acquire support for the connection pool adapter.

        Returns:
            SolidWorksAdapter: The result produced by the operation.
        """
        return await self._get_adapter(timeout=self.timeout)

    async def release(self, adapter: SolidWorksAdapter) -> None:
        """Provide release support for the connection pool adapter.

        Args:
            adapter (SolidWorksAdapter): Adapter instance used for the operation.

        Returns:
            None: None.
        """
        await self._return_adapter(adapter)

    async def cleanup(self) -> None:
        """Provide cleanup support for the connection pool adapter.

        Returns:
            None: None.
        """
        await self.disconnect()

    async def _initialize_pool(self) -> None:
        """Initialize the connection pool.

        Returns:
            None: None.
        """
        if self.pool_initialized:
            return

        async with self._lock:
            if self.pool_initialized:
                return  # type: ignore[unreachable]  # double-check locking pattern

            logger.info(f"Initializing connection pool with {self.pool_size} adapters")

            for i in range(self.pool_size):
                try:
                    adapter = self.adapter_factory()
                    await adapter.connect()
                    self.pool.append(adapter)
                    await self.available_adapters.put(adapter)
                    logger.debug(f"Initialized adapter {i + 1}/{self.pool_size}")
                except Exception as e:
                    logger.warning(f"Failed to initialize adapter {i + 1}: {e}")

            # An empty pool is not initialized. Leaving it retryable is
            # essential when SolidWorks is deliberately started later from the
            # Task Pane; otherwise every following tool waits on an empty queue.
            self.pool_initialized = bool(self.pool)
            logger.info(f"Connection pool initialized with {len(self.pool)} adapters")
            if not self.pool:
                raise Exception("No SolidWorks adapter could be connected")

    async def _get_adapter(self, timeout: float = 30.0) -> SolidWorksAdapter:
        """Get an available adapter from the pool.

        Args:
            timeout (float): Maximum time to wait in seconds. Defaults to 30.0.

        Returns:
            SolidWorksAdapter: The result produced by the operation.

        Raises:
            Exception: If the operation cannot be completed.
        """
        await self._initialize_pool()

        try:
            adapter = await asyncio.wait_for(
                self.available_adapters.get(), timeout=timeout
            )
            return adapter
        except TimeoutError as err:
            raise Exception(f"No adapter available within {timeout} seconds") from err

    async def _return_adapter(self, adapter: SolidWorksAdapter) -> None:
        """Return an adapter to the pool.

        Args:
            adapter (SolidWorksAdapter): Adapter instance used for the operation.

        Returns:
            None: None.
        """
        await self.available_adapters.put(adapter)

    async def _execute_with_pool(
        self,
        operation_name: str,
        operation: Callable[[SolidWorksAdapter], Awaitable[AdapterResult[T]]],
    ) -> AdapterResult[T]:
        """Build internal execute with pool.

        Args:
            operation_name (str): The operation name value.
            operation (Callable[[SolidWorksAdapter], Awaitable[AdapterResult[T]]]): Callable
                                                                                    object
                                                                                    executed by
                                                                                    the helper.

        Returns:
            AdapterResult[T]: The result produced by the operation.
        """
        retries = 0
        last_error = None

        while retries <= self.max_retries:
            adapter = None
            try:
                adapter = await self._get_adapter()
                result = await operation(adapter)

                # Return adapter to pool
                await self._return_adapter(adapter)

                return result

            except Exception as e:
                last_error = e
                retries += 1

                logger.warning(
                    f"Operation {operation_name} failed (attempt {retries}): {e}"
                )

                if adapter:
                    # Don't return failed adapter to pool, create a new one
                    from .base import SolidWorksAdapter

                    typed_adapter: SolidWorksAdapter = adapter
                    await self._attempt_async(lambda a=typed_adapter: a.disconnect())  # type: ignore[misc]

                    replacement_error = await self._attempt_async(
                        self._replace_failed_adapter
                    )
                    if replacement_error is not None:
                        logger.error(
                            f"Failed to create replacement adapter: {replacement_error}"
                        )

                if retries <= self.max_retries:
                    await asyncio.sleep(1.0 * retries)  # Exponential backoff

        # All retries exhausted
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=f"Operation {operation_name} failed after {self.max_retries} retries: {last_error}",
        )

    # Adapter interface implementation

    async def connect(self) -> None:
        """Initialize the connection pool.

        Returns:
            None: None.
        """
        await self._initialize_pool()

    async def start_solidworks(
        self, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        """Launch through one pooled adapter without creating parallel instances."""
        # Do not initialize all pooled connections here: when SolidWorks is
        # stopped that would issue several attach attempts. Use the first
        # existing adapter, or the factory's single backing adapter, instead.
        async with self._lock:
            target: Any | None = self.pool[0] if self.pool else None
            if target is None and self.adapter_factory is not None:
                target = self.adapter_factory()
            method = (
                getattr(target, "start_solidworks", None)
                if target is not None
                else None
            )
            if not callable(method):
                raise Exception(
                    "The configured SolidWorks adapter does not support explicit launch"
                )
            result = await self._invoke_with_optional_args(
                target, "start_solidworks", timeout_seconds
            )

            # A previously failed attach leaves no queued adapters. Seed the
            # pool with the successfully launched/connected adapter so the
            # first tool call does not wait for its normal queue timeout.
            if target not in self.pool:
                self.pool.clear()
                while not self.available_adapters.empty():
                    self.available_adapters.get_nowait()
                self.pool.append(target)
                await self.available_adapters.put(target)
            self.pool_initialized = True
            return result

    async def disconnect(self) -> None:
        """Disconnect all adapters in the pool.

        Returns:
            None: None.
        """
        for adapter in self.pool:
            from .base import SolidWorksAdapter

            typed_adapter: SolidWorksAdapter = adapter
            _, error = await self._attempt_async_with_error(
                lambda a=typed_adapter: a.disconnect()  # type: ignore[misc]
            )
            if error is not None:
                logger.warning(f"Error disconnecting adapter: {error}")

        self.pool.clear()

        # Clear the queue
        while not self.available_adapters.empty():
            try:
                self.available_adapters.get_nowait()
            except asyncio.QueueEmpty:
                break

        self.pool_initialized = False

    async def close_all_session_docs(self) -> AdapterResult[None]:
        """Close session-owned documents for every pooled adapter.

        This only delegates document cleanup; underlying adapters must not
        call ``ExitApp`` or ``Quit``.
        """
        failures: list[str] = []
        for adapter in self.pool:
            result, error = await self._attempt_async_with_error(
                lambda a=adapter: a.close_all_session_docs()
            )
            if error is not None:
                failures.append(str(error))
            elif result is not None and result.is_error:
                failures.append(result.error or "unknown error")
        if failures:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Failed to close session documents: " + "; ".join(failures),
            )
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)

    async def pack_and_go_assembly(
        self, source_path: str, target_dir: str, name_prefix: str = ""
    ) -> AdapterResult[dict[str, Any]]:
        """Run native Pack-and-Go through one pooled adapter."""
        return await self._execute_with_pool(
            "pack_and_go_assembly",
            lambda adapter: adapter.pack_and_go_assembly(  # type: ignore[attr-defined]
                source_path, target_dir, name_prefix
            ),
        )

    def is_connected(self) -> bool:
        """Check if pool is initialized.

        Returns:
            bool: True if connected, otherwise False.
        """
        return self.pool_initialized and len(self.pool) > 0

    async def health_check(self) -> AdapterHealth:
        """Get health status of the connection pool.

        Returns:
            AdapterHealth: The result produced by the operation.
        """
        healthy_count = 0
        total_response_time = 0

        if not self.pool_initialized:
            return AdapterHealth(
                healthy=False,
                last_check=datetime.now(),
                error_count=0,
                success_count=0,
                average_response_time=0,
                connection_status="pool_not_initialized",
                metrics={
                    "pool_size": 0,
                    "available_adapters": 0,
                    "healthy_adapters": 0,
                },
            )

        # Check health of all adapters
        for adapter in self.pool:
            from .base import SolidWorksAdapter

            typed_adapter: SolidWorksAdapter = adapter
            health = await self._attempt_async(lambda a=typed_adapter: a.health_check())  # type: ignore[misc]
            if not health:
                continue
            if health.healthy:
                healthy_count += 1
            total_response_time += health.average_response_time

        avg_response_time = total_response_time / len(self.pool) if self.pool else 0

        return AdapterHealth(
            healthy=healthy_count > 0,
            last_check=datetime.now(),
            error_count=len(self.pool) - healthy_count,
            success_count=healthy_count,
            average_response_time=avg_response_time,
            connection_status="pooled",
            metrics={
                "pool_size": len(self.pool),
                "available_adapters": self.available_adapters.qsize(),
                "healthy_adapters": healthy_count,
            },
        )

    # Model operations

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        """Open model using pool.

        Args:
            file_path (str): Path to the target file.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "open_model", lambda adapter: adapter.open_model(file_path)
        )

    async def activate_model(
        self, name: str, path: str | None = None
    ) -> AdapterResult[None]:
        """Activate a model through one pooled adapter connection."""
        return await self._execute_with_pool(
            "activate_model", lambda adapter: adapter.activate_model(name, path)
        )

    async def close_model(self, save: bool = False) -> AdapterResult[None]:
        """Close model using pool.

        Args:
            save (bool): The save value. Defaults to False.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "close_model", lambda adapter: adapter.close_model(save)
        )

    async def save_file(self, file_path: str | None = None) -> AdapterResult[None]:
        """Save model using pool.

        Args:
            file_path (str | None): Path to the target file. Defaults to None.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "save_file", lambda adapter: adapter.save_file(file_path)
        )

    async def create_part(
        self, name: str | None = None, units: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create part using pool.

        Args:
            name (str | None): The name value. Defaults to None.
            units (str | None): The units value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """

        async def _op(adapter: SolidWorksAdapter) -> AdapterResult[SolidWorksModel]:
            """Build internal op.

            Args:
                adapter (SolidWorksAdapter): Adapter instance used for the operation.

            Returns:
                AdapterResult[SolidWorksModel]: The result produced by the operation.
            """
            if name is None and units is None:
                return await adapter.create_part()
            result = await self._invoke_with_optional_args(
                adapter, "create_part", name, units
            )
            return cast(AdapterResult[SolidWorksModel], result)

        return await self._execute_with_pool("create_part", _op)

    async def create_assembly(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create assembly using pool.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """

        async def _op(adapter: SolidWorksAdapter) -> AdapterResult[SolidWorksModel]:
            """Build internal op.

            Args:
                adapter (SolidWorksAdapter): Adapter instance used for the operation.

            Returns:
                AdapterResult[SolidWorksModel]: The result produced by the operation.
            """
            if name is None:
                return await adapter.create_assembly()
            result = await self._invoke_with_optional_args(
                adapter, "create_assembly", name
            )
            return cast(AdapterResult[SolidWorksModel], result)

        return await self._execute_with_pool("create_assembly", _op)

    async def create_drawing(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create drawing using pool.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "create_drawing", lambda adapter: adapter.create_drawing(name)
        )

    async def get_assembly_component(
        self, component_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Look up one active-assembly component using one pooled adapter."""
        return await self._execute_with_pool(
            "get_assembly_component",
            lambda adapter: adapter.get_assembly_component(component_name),
        )

    async def list_assembly_mate_entities(
        self,
        component_id: str | None = None,
        entity_types: tuple[str, ...] = ("face", "edge"),
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List assembly mate entities using one pooled adapter."""
        return await self._execute_with_pool(
            "list_assembly_mate_entities",
            lambda adapter: adapter.list_assembly_mate_entities(
                component_id, entity_types
            ),
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
        """Create an assembly mate using one pooled adapter connection."""
        return await self._execute_with_pool(
            "create_assembly_mate",
            lambda adapter: adapter.create_assembly_mate(
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
        )

    # Feature operations

    async def create_sheet_metal_base_flange(
        self,
        sketch_name: str,
        thickness_mm: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a base flange using one pooled adapter."""
        return await self._execute_with_pool(
            "create_sheet_metal_base_flange",
            lambda adapter: adapter.create_sheet_metal_base_flange(
                sketch_name, thickness_mm, bend_radius_mm, k_factor
            ),
        )

    async def create_sheet_metal_edge_flange(
        self,
        edge_persistent_reference: str,
        length_mm: float,
        angle_degrees: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create an edge flange using one pooled adapter."""
        return await self._execute_with_pool(
            "create_sheet_metal_edge_flange",
            lambda adapter: adapter.create_sheet_metal_edge_flange(
                edge_persistent_reference,
                length_mm,
                angle_degrees,
                bend_radius_mm,
                k_factor,
            ),
        )

    async def set_sheet_metal_k_factor(
        self, k_factor: float
    ) -> AdapterResult[dict[str, Any]]:
        """Set the sheet-metal K factor using one pooled adapter."""
        return await self._execute_with_pool(
            "set_sheet_metal_k_factor",
            lambda adapter: adapter.set_sheet_metal_k_factor(k_factor),
        )

    async def get_sheet_metal_info(self) -> AdapterResult[dict[str, Any]]:
        """Read sheet-metal state using one pooled adapter."""
        return await self._execute_with_pool(
            "get_sheet_metal_info", lambda adapter: adapter.get_sheet_metal_info()
        )

    async def set_sheet_metal_flat_state(
        self, flattened: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Set sheet-metal flat state using one pooled adapter."""
        return await self._execute_with_pool(
            "set_sheet_metal_flat_state",
            lambda adapter: adapter.set_sheet_metal_flat_state(flattened),
        )

    async def export_flat_pattern_dxf(
        self, file_path: str, model_path: str, sheet_metal_options: int
    ) -> AdapterResult[dict[str, Any]]:
        """Export one flat pattern using one pooled adapter."""
        return await self._execute_with_pool(
            "export_flat_pattern_dxf",
            lambda adapter: adapter.export_flat_pattern_dxf(
                file_path, model_path, sheet_metal_options
            ),
        )

    async def create_shell(
        self, face_persistent_references: list[str], thickness_mm: float, outward: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a shell using one pooled adapter."""
        return await self._execute_with_pool(
            "create_shell",
            lambda adapter: adapter.create_shell(
                face_persistent_references, thickness_mm, outward
            ),
        )

    async def create_rib(
        self,
        sketch_name: str,
        thickness_mm: float,
        draft_angle_degrees: float,
        direction: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a rib using one pooled adapter."""
        return await self._execute_with_pool(
            "create_rib",
            lambda adapter: adapter.create_rib(
                sketch_name, thickness_mm, draft_angle_degrees, direction
            ),
        )

    async def create_dome(
        self, face_persistent_reference: str, height_mm: float, reverse_direction: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a dome using one pooled adapter."""
        return await self._execute_with_pool(
            "create_dome",
            lambda adapter: adapter.create_dome(
                face_persistent_reference, height_mm, reverse_direction
            ),
        )

    async def create_extrusion(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create extrusion using pool.

        Args:
            params (ExtrusionParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "create_extrusion", lambda adapter: adapter.create_extrusion(params)
        )

    async def create_cut_extrude(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create cut-extrude using pool."""
        return await self._execute_with_pool(
            "create_cut_extrude", lambda adapter: adapter.create_cut_extrude(params)
        )

    async def add_fillet(
        self, radius: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Add fillet using pool."""
        return await self._execute_with_pool(
            "add_fillet", lambda adapter: adapter.add_fillet(radius, edge_names)
        )

    async def add_chamfer(
        self, distance: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Add an equal-distance chamfer using the active pooled adapter."""
        return await self._execute_with_pool(
            "add_chamfer", lambda adapter: adapter.add_chamfer(distance, edge_names)
        )

    async def create_chamfer(
        self,
        edge_persistent_references: list[str],
        distance_mm: float,
        angle_degrees: float,
        chamfer_type: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a persistent-reference chamfer using one pooled adapter."""
        return await self._execute_with_pool(
            "create_chamfer",
            lambda adapter: adapter.create_chamfer(
                edge_persistent_references, distance_mm, angle_degrees, chamfer_type
            ),
        )

    async def list_assembly_mates(self) -> AdapterResult[list[dict[str, Any]]]:
        """List current assembly mates using one pooled adapter."""
        return await self._execute_with_pool(
            "list_assembly_mates", lambda adapter: adapter.list_assembly_mates()
        )

    async def delete_assembly_mate(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one workflow-owned assembly mate using one pooled adapter."""
        return await self._execute_with_pool(
            "delete_assembly_mate",
            lambda adapter: adapter.delete_assembly_mate(persistent_reference),
        )

    async def list_part_topology(
        self, entity_types: tuple[str, ...] = ("face", "edge")
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List active-part topology using one pooled adapter."""
        return await self._execute_with_pool(
            "list_part_topology",
            lambda adapter: adapter.list_part_topology(entity_types),
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
        """Create a section drawing view using one pooled adapter."""
        return await self._execute_with_pool(
            "create_section_drawing_view",
            lambda adapter: adapter.create_section_drawing_view(
                source_view_id,
                cut_start_x_m,
                cut_start_y_m,
                cut_end_x_m,
                cut_end_y_m,
                x_m,
                y_m,
            ),
        )

    async def create_auxiliary_drawing_view(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create an auxiliary drawing view using one pooled adapter."""
        return await self._execute_with_pool(
            "create_auxiliary_drawing_view",
            lambda adapter: adapter.create_auxiliary_drawing_view(
                persistent_reference, x_m, y_m
            ),
        )

    async def activate_drawing_view(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one drawing view using a pooled adapter."""
        return await self._execute_with_pool(
            "activate_drawing_view",
            lambda adapter: adapter.activate_drawing_view(view_id),
        )

    async def list_drawing_sheets(self) -> AdapterResult[list[dict[str, Any]]]:
        """List drawing sheets using one pooled adapter."""
        return await self._execute_with_pool(
            "list_drawing_sheets", lambda adapter: adapter.list_drawing_sheets()
        )

    async def activate_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one drawing sheet using a pooled adapter."""
        return await self._execute_with_pool(
            "activate_drawing_sheet",
            lambda adapter: adapter.activate_drawing_sheet(sheet_name),
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
        """Create one drawing sheet using a pooled adapter."""
        return await self._execute_with_pool(
            "create_drawing_sheet",
            lambda adapter: adapter.create_drawing_sheet(
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
        )

    async def delete_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one drawing sheet using a pooled adapter."""
        return await self._execute_with_pool(
            "delete_drawing_sheet",
            lambda adapter: adapter.delete_drawing_sheet(sheet_name),
        )

    async def rename_drawing_sheet(
        self, sheet_name: str, new_sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Rename one drawing sheet using a pooled adapter."""
        return await self._execute_with_pool(
            "rename_drawing_sheet",
            lambda adapter: adapter.rename_drawing_sheet(sheet_name, new_sheet_name),
        )

    async def reorder_drawing_sheets(self, sheet_names: list[str]) -> AdapterResult[list[dict[str, Any]]]:
        return await self._execute_with_pool("reorder_drawing_sheets", lambda adapter: adapter.reorder_drawing_sheets(sheet_names))

    async def update_drawing_view_layout(
        self,
        view_id: str,
        x_m: float | None,
        y_m: float | None,
        scale_decimal: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Update one drawing view layout using a pooled adapter."""
        return await self._execute_with_pool(
            "update_drawing_view_layout",
            lambda adapter: adapter.update_drawing_view_layout(
                view_id, x_m, y_m, scale_decimal
            ),
        )

    async def align_drawing_views(
        self,
        view_ids: list[str],
        direction: str,
        gap_m: float,
        start_x_m: float | None,
        start_y_m: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Arrange drawing views using one pooled adapter operation."""
        return await self._execute_with_pool(
            "align_drawing_views",
            lambda adapter: adapter.align_drawing_views(
                view_ids, direction, gap_m, start_x_m, start_y_m
            ),
        )

    async def rebuild_drawing(self) -> AdapterResult[dict[str, Any]]:
        """Rebuild and save the active drawing using one pooled adapter."""
        return await self._execute_with_pool(
            "rebuild_drawing", lambda adapter: adapter.rebuild_drawing()
        )

    async def update_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Update one workflow-owned BOM using one pooled adapter."""
        return await self._execute_with_pool(
            "update_drawing_bom",
            lambda adapter: adapter.update_drawing_bom(persistent_reference),
        )

    async def delete_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one workflow-owned BOM using one pooled adapter."""
        return await self._execute_with_pool(
            "delete_drawing_bom",
            lambda adapter: adapter.delete_drawing_bom(persistent_reference),
        )

    async def create_drawing_revision_table(
        self, x_m: float, y_m: float, anchor_type: int, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create a revision table using one pooled adapter."""
        return await self._execute_with_pool(
            "create_drawing_revision_table",
            lambda adapter: adapter.create_drawing_revision_table(
                x_m, y_m, anchor_type, revision
            ),
        )

    async def create_drawing_ordinate_dimension(
        self, persistent_reference: str, x_m: float, y_m: float, ordinate_type: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_pool(
            "create_drawing_ordinate_dimension",
            lambda adapter: adapter.create_drawing_ordinate_dimension(
                persistent_reference, x_m, y_m, ordinate_type
            ),
        )

    async def create_drawing_surface_finish(
        self, persistent_reference: str, x_m: float, y_m: float, symbol: str, value: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_pool(
            "create_drawing_surface_finish",
            lambda adapter: adapter.create_drawing_surface_finish(
                persistent_reference, x_m, y_m, symbol, value
            ),
        )

    async def create_drawing_weld_symbol(
        self, persistent_reference: str, x_m: float, y_m: float, weld_type: str, size: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_pool(
            "create_drawing_weld_symbol",
            lambda adapter: adapter.create_drawing_weld_symbol(
                persistent_reference, x_m, y_m, weld_type, size
            ),
        )

    async def create_drawing_geometric_tolerance(
        self, persistent_reference: str, x_m: float, y_m: float, tolerance: str, datum: str
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_pool(
            "create_drawing_geometric_tolerance",
            lambda adapter: adapter.create_drawing_geometric_tolerance(
                persistent_reference, x_m, y_m, tolerance, datum
            ),
        )

    async def create_drawing_center_mark(
        self, persistent_reference: str, size_m: float, show_connection_lines: bool
    ) -> AdapterResult[dict[str, Any]]:
        return await self._execute_with_pool(
            "create_drawing_center_mark",
            lambda adapter: adapter.create_drawing_center_mark(
                persistent_reference, size_m, show_connection_lines
            ),
        )

    async def list_drawing_revisions(
        self, persistent_reference: str
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List revision rows using one pooled adapter."""
        return await self._execute_with_pool(
            "list_drawing_revisions",
            lambda adapter: adapter.list_drawing_revisions(persistent_reference),
        )

    async def add_drawing_revision(
        self, persistent_reference: str, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Add one revision row using one pooled adapter."""
        return await self._execute_with_pool(
            "add_drawing_revision",
            lambda adapter: adapter.add_drawing_revision(persistent_reference, revision),
        )

    async def delete_drawing_revision(
        self, persistent_reference: str, revision_id: int, delete_symbols: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one revision row using one pooled adapter."""
        return await self._execute_with_pool(
            "delete_drawing_revision",
            lambda adapter: adapter.delete_drawing_revision(
                persistent_reference, revision_id, delete_symbols
            ),
        )

    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create revolve using pool.

        Args:
            params (RevolveParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "create_revolve", lambda adapter: adapter.create_revolve(params)
        )

    async def create_reference_plane(
        self, params: ReferencePlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create an offset reference plane using a pooled adapter."""
        return await self._execute_with_pool(
            "create_reference_plane",
            lambda adapter: adapter.create_reference_plane(params),
        )

    async def get_reference_axis(
        self, params: ReferenceAxisParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Resolve a standard origin axis using a pooled adapter."""
        return await self._execute_with_pool(
            "get_reference_axis", lambda adapter: adapter.get_reference_axis(params)
        )

    async def get_reference_plane(
        self, params: OriginPlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Resolve a standard origin plane using a pooled adapter."""
        return await self._execute_with_pool(
            "get_reference_plane", lambda adapter: adapter.get_reference_plane(params)
        )

    async def create_feature_linear_pattern(
        self, params: FeatureLinearPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a feature linear pattern using a pooled adapter."""
        return await self._execute_with_pool(
            "create_feature_linear_pattern",
            lambda adapter: adapter.create_feature_linear_pattern(params),
        )

    async def create_feature_circular_pattern(
        self, params: FeatureCircularPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a feature circular pattern using a pooled adapter."""
        return await self._execute_with_pool(
            "create_feature_circular_pattern",
            lambda adapter: adapter.create_feature_circular_pattern(params),
        )

    async def mirror_feature(
        self, params: FeatureMirrorParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mirror one feature using a pooled adapter."""
        return await self._execute_with_pool(
            "mirror_feature", lambda adapter: adapter.mirror_feature(params)
        )

    async def create_hole_wizard(
        self, params: HoleWizardParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a controlled Hole Wizard feature using a pooled adapter."""
        return await self._execute_with_pool(
            "create_hole_wizard", lambda adapter: adapter.create_hole_wizard(params)
        )

    async def create_tapped_hole(
        self, params: ThreadedHoleParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a tapped Hole Wizard feature using a pooled adapter."""
        return await self._execute_with_pool(
            "create_tapped_hole", lambda adapter: adapter.create_tapped_hole(params)
        )

    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create sweep using pool.

        Args:
            params (SweepParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "create_sweep", lambda adapter: adapter.create_sweep(params)
        )

    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create loft using pool.

        Args:
            params (LoftParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "create_loft", lambda adapter: adapter.create_loft(params)
        )

    # Sketch operations

    async def create_sketch(self, plane: str) -> AdapterResult[str]:
        """Create sketch using pool.

        Args:
            plane (str): The plane value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "create_sketch", lambda adapter: adapter.create_sketch(plane)
        )

    async def create_3d_sketch(self) -> AdapterResult[str]:
        """Create a 3D sketch using one pooled adapter."""
        return await self._execute_with_pool(
            "create_3d_sketch", lambda adapter: adapter.create_3d_sketch()
        )

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add line using pool.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "add_line", lambda adapter: adapter.add_line(x1, y1, x2, y2)
        )

    async def add_3d_line(
        self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float
    ) -> AdapterResult[str]:
        """Add a 3D line using one pooled adapter."""
        return await self._execute_with_pool(
            "add_3d_line",
            lambda adapter: adapter.add_3d_line(x1, y1, z1, x2, y2, z2),
        )

    async def convert_sketch_entities(
        self, edge_persistent_references: list[str]
    ) -> AdapterResult[str]:
        """Convert workflow-owned edges using one pooled adapter."""
        return await self._execute_with_pool(
            "convert_sketch_entities",
            lambda adapter: adapter.convert_sketch_entities(edge_persistent_references),
        )

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add centerline using pool.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "add_centerline",
            lambda adapter: adapter.add_centerline(x1, y1, x2, y2),
        )

    async def add_sketch_text(
        self, text: str, x: float, y: float, height: float, angle_degrees: float
    ) -> AdapterResult[str]:
        """Add sketch text using one pooled adapter."""
        return await self._execute_with_pool(
            "add_sketch_text",
            lambda adapter: adapter.add_sketch_text(
                text, x, y, height, angle_degrees
            ),
        )

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        """Add circle using pool.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "add_circle", lambda adapter: adapter.add_circle(center_x, center_y, radius)
        )

    async def add_point(self, x: float, y: float) -> AdapterResult[str]:
        """Add one sketch point using a pooled adapter."""
        return await self._execute_with_pool(
            "add_point", lambda adapter: adapter.add_point(x, y)
        )

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add rectangle using pool.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "add_rectangle", lambda adapter: adapter.add_rectangle(x1, y1, x2, y2)
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
        """Add arc using pool.

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
        return await self._execute_with_pool(
            "add_arc",
            lambda adapter: adapter.add_arc(
                center_x,
                center_y,
                start_x,
                start_y,
                end_x,
                end_y,
            ),
        )

    async def sketch_trim(
        self, entity_id: str, trim_x: float, trim_y: float
    ) -> AdapterResult[str]:
        """Trim one active-sketch entity using the pooled adapter."""
        return await self._execute_with_pool(
            "sketch_trim",
            lambda adapter: adapter.sketch_trim(entity_id, trim_x, trim_y),
        )

    async def add_sketch_constraint(
        self,
        entity1: str,
        entity2: str | None,
        relation_type: str,
        entity3: str | None = None,
    ) -> AdapterResult[str]:
        """Add sketch constraint using pool."""
        return await self._execute_with_pool(
            "add_sketch_constraint",
            lambda adapter: adapter.add_sketch_constraint(
                entity1,
                entity2,
                relation_type,
                entity3,
            ),
        )

    async def add_sketch_dimension(
        self,
        entity1: str,
        entity2: str | None,
        dimension_type: str,
        value: float,
    ) -> AdapterResult[str]:
        """Add sketch dimension using pool."""
        return await self._execute_with_pool(
            "add_sketch_dimension",
            lambda adapter: adapter.add_sketch_dimension(
                entity1,
                entity2,
                dimension_type,
                value,
            ),
        )

    async def check_sketch_fully_defined(
        self, sketch_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Check sketch definition status using pool."""
        return await self._execute_with_pool(
            "check_sketch_fully_defined",
            lambda adapter: adapter.check_sketch_fully_defined(sketch_name),
        )

    async def check_sketch_feature_use(
        self, sketch_name: str | None = None, feature_use: str = "boss_extrude"
    ) -> AdapterResult[dict[str, Any]]:
        """Check feature-use validity using a pooled adapter."""
        return await self._execute_with_pool(
            "check_sketch_feature_use",
            lambda adapter: adapter.check_sketch_feature_use(sketch_name, feature_use),
        )

    async def exit_sketch(self) -> AdapterResult[None]:
        """Exit sketch using pool.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "exit_sketch", lambda adapter: adapter.exit_sketch()
        )

    # Analysis operations

    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        """Get mass properties using pool.

        Returns:
            AdapterResult[MassProperties]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "get_mass_properties", lambda adapter: adapter.get_mass_properties()
        )

    async def get_model_info(self) -> AdapterResult[dict[str, object]]:
        """Get active model metadata using pool.

        Returns:
            AdapterResult[dict[str, object]]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "get_model_info", lambda adapter: adapter.get_model_info()
        )

    async def list_features(
        self, include_suppressed: bool = False
    ) -> AdapterResult[list[dict[str, object]]]:
        """List model features using pool.

        Args:
            include_suppressed (bool): The include suppressed value. Defaults to False.

        Returns:
            AdapterResult[list[dict[str, object]]]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "list_features",
            lambda adapter: adapter.list_features(include_suppressed),
        )

    async def list_open_models(self) -> AdapterResult[list[dict[str, Any]]]:
        """List open SolidWorks documents using one pooled adapter."""
        return await self._execute_with_pool(
            "list_open_models", lambda adapter: adapter.list_open_models()
        )

    async def list_configurations(self) -> AdapterResult[list[str]]:
        """List model configurations using pool.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "list_configurations", lambda adapter: adapter.list_configurations()
        )

    async def get_configuration(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one configuration using a pooled adapter."""
        return await self._execute_with_pool(
            "get_configuration",
            lambda adapter: adapter.get_configuration(configuration_name),
        )

    async def get_selection_state(self) -> AdapterResult[dict[str, Any]]:
        """Read the active selection through one pooled adapter."""
        return await self._execute_with_pool(
            "get_selection_state", lambda adapter: adapter.get_selection_state()
        )

    async def create_configuration(
        self, configuration_name: str, description: str = ""
    ) -> AdapterResult[str]:
        """Create a model configuration using one pooled adapter."""
        return await self._execute_with_pool(
            "create_configuration",
            lambda adapter: adapter.create_configuration(
                configuration_name, description
            ),
        )

    async def activate_configuration(
        self, configuration_name: str
    ) -> AdapterResult[str]:
        """Activate a model configuration using one pooled adapter."""
        return await self._execute_with_pool(
            "activate_configuration",
            lambda adapter: adapter.activate_configuration(configuration_name),
        )

    async def execute_macro(
        self, params: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Provide execute macro support for the connection pool adapter.

        Args:
            params (dict[str, Any]): The params value.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "execute_macro",
            lambda adapter: adapter.execute_macro(params),  # type: ignore[attr-defined]
        )

    # Export operations

    async def export_image(self, payload: dict) -> AdapterResult[dict]:
        """Export viewport image using pool.

        Args:
            payload (dict): The payload value.

        Returns:
            AdapterResult[dict]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "export_image", lambda adapter: adapter.export_image(payload)
        )

    async def export_file(
        self, file_path: str, format_type: str
    ) -> AdapterResult[None]:
        """Export file using pool.

        Args:
            file_path (str): Path to the target file.
            format_type (str): The format type value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "export_file", lambda adapter: adapter.export_file(file_path, format_type)
        )

    async def export_drawing_cad(
        self,
        file_path: str,
        format_type: str,
        model_space: bool = True,
        compatibility_profile: str = "standard",
    ) -> AdapterResult[None]:
        """Export a drawing as model-space or paper-space DWG/DXF."""
        return await self._execute_with_pool(
            "export_drawing_cad",
            lambda adapter: adapter.export_drawing_cad(
                file_path, format_type, model_space, compatibility_profile
            ),
        )

    # Dimension operations

    async def list_dimensions(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-model dimensions using the pooled adapter."""
        return await self._execute_with_pool(
            "list_dimensions", lambda adapter: adapter.list_dimensions()
        )

    async def get_dimension(self, name: str) -> AdapterResult[float]:
        """Get dimension using pool.

        Args:
            name (str): The name value.

        Returns:
            AdapterResult[float]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "get_dimension", lambda adapter: adapter.get_dimension(name)
        )

    async def set_dimension(self, name: str, value: float) -> AdapterResult[None]:
        """Set dimension using pool.

        Args:
            name (str): The name value.
            value (float): The value value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        return await self._execute_with_pool(
            "set_dimension", lambda adapter: adapter.set_dimension(name, value)
        )


class ConnectionPool:
    """Legacy alias class expected by tests.

    Args:
        create_connection (Callable[[], object | Awaitable[object]]): Factory callable used
                                                                      to create a
                                                                      connection.
        max_size (int): Maximum number of items allowed in the pool. Defaults to 3.
        timeout (float): Maximum time to wait in seconds. Defaults to 30.0.

    Attributes:
        _create_connection (Any): The create connection value.
        _max_size (Any): The max size value.
        _timeout (Any): The timeout value.
    """

    def __init__(
        self,
        create_connection: Callable[[], object | Awaitable[object]],
        max_size: int = 3,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the connection pool.

        Args:
            create_connection (Callable[[], object | Awaitable[object]]): Factory callable used
                                                                          to create a
                                                                          connection.
            max_size (int): Maximum number of items allowed in the pool. Defaults to 3.
            timeout (float): Maximum time to wait in seconds. Defaults to 30.0.

        Returns:
            None: None.
        """
        self._create_connection = create_connection
        self._max_size = max_size
        self._timeout = timeout
        self._available: list[object] = []
        self._in_use: set[int] = set()
        self._all_connections: list[object] = []

    @property
    def size(self) -> int:
        """Provide size support for the connection pool.

        Returns:
            int: The computed numeric result.
        """
        return len(self._all_connections)

    @property
    def active_connections(self) -> int:
        """Provide active connections support for the connection pool.

        Returns:
            int: The computed numeric result.
        """
        return len(self._in_use)

    async def _new_connection(self) -> object:
        """Build internal new connection.

        Returns:
            object: The result produced by the operation.
        """
        conn = self._create_connection()
        if asyncio.iscoroutine(conn):
            conn = await conn
        self._all_connections.append(conn)
        return conn

    async def acquire(self) -> object:
        """Provide acquire support for the connection pool.

        Returns:
            object: The result produced by the operation.

        Raises:
            TimeoutError: No connection available within timeout.
        """
        if self._available:
            conn = self._available.pop()
            self._in_use.add(id(conn))
            return conn

        if len(self._all_connections) < self._max_size:
            conn = await self._new_connection()
            self._in_use.add(id(conn))
            return conn

        end_time = time.time() + self._timeout
        while time.time() < end_time:
            if self._available:
                conn = self._available.pop()
                self._in_use.add(id(conn))
                return conn
            await asyncio.sleep(0.01)

        raise TimeoutError("No connection available within timeout")

    async def release(self, conn: object) -> None:
        """Provide release support for the connection pool.

        Args:
            conn (object): The conn value.

        Returns:
            None: None.
        """
        conn_id = id(conn)
        if conn_id in self._in_use:
            self._in_use.remove(conn_id)
            self._available.append(conn)

    async def cleanup(self) -> None:
        """Provide cleanup support for the connection pool.

        Returns:
            None: None.
        """
        for conn in self._all_connections:
            close = getattr(conn, "close", None)
            if close is None:
                continue
            result = close()
            if asyncio.iscoroutine(result):
                await result

        self._available.clear()
        self._in_use.clear()
        self._all_connections.clear()
