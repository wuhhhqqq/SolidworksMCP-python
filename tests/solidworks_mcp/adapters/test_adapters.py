"""
Tests for SolidWorks adapter implementations.

Comprehensive test suite covering adapter factory, pywin32 adapter,
mock adapter, circuit breaker, and connection pooling functionality.
"""

import math
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from unittest.mock import call as mock_call

import pytest

from solidworks_mcp.adapters import (
    AdapterFactory,
    create_adapter,
)
from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    FeatureCircularPatternParameters,
    FeatureLinearPatternParameters,
    FeatureMirrorParameters,
    HoleWizardParameters,
    OriginPlaneParameters,
    ReferenceAxisParameters,
    ThreadedHoleParameters,
)
from solidworks_mcp.adapters.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerAdapter,
    CircuitState,
)
from solidworks_mcp.adapters.connection_pool import (
    ConnectionPool,
    ConnectionPoolAdapter,
)
from solidworks_mcp.adapters.factory import _register_default_adapters
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.pywin32_adapter import (
    PyWin32Adapter,
    resolve_solidworks_launcher,
)
from solidworks_mcp.config import AdapterType
from solidworks_mcp.exceptions import (
    SolidWorksMCPError,
    SolidWorksOperationError,
)


class TestAdapterFactory:
    """Test suite for adapter factory."""

    @pytest.mark.asyncio
    async def test_create_mock_adapter(self, mock_config):
        """Test creating mock adapter."""
        adapter = await create_adapter(mock_config)
        assert isinstance(adapter, MockSolidWorksAdapter)
        assert adapter.config == mock_config

    @pytest.mark.asyncio
    async def test_create_pywin32_adapter_on_windows(self, mock_config):
        """Test creating pywin32 adapter on Windows."""
        # Override config to use pywin32
        mock_config.adapter_type = AdapterType.PYWIN32
        mock_config.mock_solidworks = False

        with patch("platform.system", return_value="Windows"):
            with patch("solidworks_mcp.adapters.pywin32_adapter.PyWin32Adapter"):
                await create_adapter(mock_config)
                # Would be PyWin32Adapter on actual Windows system

    @pytest.mark.asyncio
    async def test_create_adapter_non_windows_fallback(self, mock_config):
        """Test fallback to mock adapter on non-Windows systems."""
        mock_config.adapter_type = AdapterType.PYWIN32
        mock_config.mock_solidworks = False

        with patch("platform.system", return_value="Linux"):
            adapter = await create_adapter(mock_config)
            assert isinstance(adapter, MockSolidWorksAdapter)


def test_resolve_solidworks_launcher_prefers_configured_executable(tmp_path) -> None:
    executable = tmp_path / "SLDWORKS.exe"
    executable.touch()

    assert resolve_solidworks_launcher({"solidworks_path": str(executable)}) == (
        "executable",
        executable,
    )


def test_invalid_configured_launcher_does_not_fall_back(monkeypatch, tmp_path):
    fallback = tmp_path / "SLDWORKS.exe"
    fallback.touch()
    monkeypatch.setattr(
        "solidworks_mcp.adapters.pywin32_adapter._solidworks_executable_candidates", lambda config: [fallback],
    )
    with pytest.raises(SolidWorksMCPError, match="Configured SolidWorks path is invalid"):
        resolve_solidworks_launcher({"solidworks_path": str(tmp_path / "missing.exe")})


def test_shortcut_target_is_checked_before_selection(monkeypatch, tmp_path):
    utility = tmp_path / "license-manager.exe"
    utility.touch()
    executable = tmp_path / "SLDWORKS.exe"
    executable.touch()
    wrong = tmp_path / "SOLIDWORKS Licensing.lnk"
    wrong.touch()
    correct = tmp_path / "SOLIDWORKS 2019.lnk"
    correct.touch()
    shell = SimpleNamespace(CreateShortcut=lambda path: SimpleNamespace(
        TargetPath=str(utility if Path(path) == wrong else executable),
    ))
    monkeypatch.setattr("solidworks_mcp.adapters.pywin32_adapter.win32com.client.Dispatch", Mock(return_value=shell))
    monkeypatch.setattr("solidworks_mcp.adapters.pywin32_adapter._solidworks_shortcut_candidates", lambda config: [wrong, correct])
    assert resolve_solidworks_launcher({}) == ("shortcut", correct)


@pytest.mark.asyncio
async def test_circuit_breaker_ignores_expected_missing_solidworks_connection() -> None:
    """Attach-only absence must not block a later explicit launch action."""
    backing = SimpleNamespace(
        connect=AsyncMock(
            side_effect=SolidWorksMCPError(
                "Failed to connect to SolidWorks: No running SolidWorks instance was found"
            )
        )
    )
    breaker = CircuitBreakerAdapter(adapter=backing, failure_threshold=1)

    with pytest.raises(SolidWorksMCPError):
        await breaker.connect()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_circuit_breaker_explicit_start_success_closes_open_breaker() -> None:
    backing = SimpleNamespace(
        start_solidworks=AsyncMock(return_value={"started": True}),
    )
    breaker = CircuitBreakerAdapter(adapter=backing, failure_threshold=1)
    breaker.state = CircuitState.OPEN
    breaker.last_failure_time = 0

    result = await breaker.start_solidworks(30)

    assert result["started"] is True
    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_connection_pool_explicit_start_seeds_empty_pool() -> None:
    """A launch from an empty pool must make the first tool immediately usable."""
    backing = SimpleNamespace(
        start_solidworks=AsyncMock(return_value={"started": True, "pid": 9}),
        connect=AsyncMock(),
        disconnect=AsyncMock(),
    )
    pool = ConnectionPoolAdapter(adapter_factory=lambda: backing, pool_size=1)

    result = await pool.start_solidworks(30)

    assert result["started"] is True
    assert pool.pool_initialized is True
    assert pool.size == 1
    assert pool.available_adapters.qsize() == 1

def test_adapter_factory_registry():
    """Test that all adapter types are registered and collected by pytest."""
    _register_default_adapters()
    factory = AdapterFactory()
    assert AdapterType.MOCK in factory._adapters
    assert AdapterType.PYWIN32 in factory._adapters


class TestMockAdapter:
    """Test suite for mock SolidWorks adapter."""

    @pytest.mark.asyncio
    async def test_mock_adapter_initialization(self, mock_config):
        """Test mock adapter initialization."""
        adapter = MockSolidWorksAdapter(mock_config)
        assert adapter.config == mock_config
        assert not adapter.is_connected

    @pytest.mark.asyncio
    async def test_mock_adapter_connect_disconnect(self, mock_adapter):
        """Test mock adapter connection lifecycle."""
        # Connect
        await mock_adapter.connect()
        assert mock_adapter.is_connected

        # Disconnect
        await mock_adapter.disconnect()
        assert not mock_adapter.is_connected

    @pytest.mark.asyncio
    async def test_mock_equations_follow_save_as_and_reset_after_disconnect(
        self, mock_adapter, tmp_path
    ):
        """Equation state follows its model path and never leaks across sessions."""
        await mock_adapter.connect()
        await mock_adapter.create_part("EquationPart")
        created = await mock_adapter.set_equation("PlateWidth", "120mm")
        saved = await mock_adapter.save_file(str(tmp_path / "renamed.sldprt"))
        renamed_equations = await mock_adapter.list_equations()

        assert created.is_success
        assert saved.is_success
        assert renamed_equations.data == [
            {
                "index": 0,
                "name": "PlateWidth",
                "expression": "120mm",
                "is_global_variable": True,
            }
        ]

        await mock_adapter.disconnect()
        await mock_adapter.connect()
        await mock_adapter.create_part("EquationPart")
        fresh_equations = await mock_adapter.list_equations()

        assert fresh_equations.is_success
        assert fresh_equations.data == []

    @pytest.mark.asyncio
    async def test_mock_adapter_health_check(self, mock_adapter):
        """Test mock adapter health check."""
        await mock_adapter.connect()
        health = await mock_adapter.health_check()

        assert health["status"] == "healthy"
        assert health["adapter_type"] == "mock"
        assert health["connected"] is True
        assert "version" in health
        assert "uptime" in health

    @pytest.mark.asyncio
    async def test_mock_adapter_file_operations(self, mock_adapter):
        """Test mock adapter file operations."""
        await mock_adapter.connect()

        # Test open model
        result = await mock_adapter.open_model("test.sldprt")
        assert result.is_success
        assert "test.sldprt" in result.data["title"]

        # Test save file
        result = await mock_adapter.save_file("test_saved.sldprt")
        assert result.is_success
        assert result.data["file_path"] == "test_saved.sldprt"

        # Save As changes the active-document identity, just as SolidWorks does.
        activated = await mock_adapter.activate_model(
            "test_saved.sldprt", "test_saved.sldprt"
        )
        assert activated.is_success

    @pytest.mark.asyncio
    async def test_mock_adapter_modeling_operations(self, mock_adapter):
        """Test mock adapter modeling operations."""
        await mock_adapter.connect()

        # Test create part

        result = await mock_adapter.create_part("TestPart", "mm")
        assert result.is_success
        assert result.data["name"] == "TestPart"

        assert result.data["units"] == "mm"

        # Test create extrusion

        result = await mock_adapter.create_extrusion("Sketch1", 10.0, "blind")
        assert result.is_success

        assert result.data["depth"] == 10.0
        assert result.data["direction"] == "blind"

    @pytest.mark.asyncio
    async def test_mock_adapter_sketch_operations(self, mock_adapter):
        """Test mock adapter sketch operations."""

        await mock_adapter.connect()

        # Test create sketch
        result = await mock_adapter.create_sketch("Front Plane")
        assert result.is_success
        assert result.data["plane"] == "Front Plane"

        # Test add sketch line
        result = await mock_adapter.add_sketch_line(0, 0, 10, 10, False)
        assert result.is_success

        assert result.data["start"] == {"x": 0, "y": 0}
        assert result.data["end"] == {"x": 10, "y": 10}

    @pytest.mark.asyncio
    async def test_mock_adapter_error_simulation(self, mock_config):
        """Test mock adapter error simulation."""

        config = mock_config
        config.simulate_errors = True
        adapter = MockSolidWorksAdapter(config)

        await adapter.connect()

        # Some operations should fail when error simulation is enabled
        with pytest.raises(SolidWorksOperationError):
            await adapter.open_model("nonexistent.sldprt")


class TestCircuitBreaker:
    """Test suite for circuit breaker pattern."""

    def test_circuit_breaker_initialization(self):
        """Test circuit breaker initialization."""
        cb = CircuitBreaker(
            failure_threshold=3, recovery_timeout=10.0, expected_exception=Exception
        )

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_success_path(self):
        """Test circuit breaker with successful operations."""
        cb = CircuitBreakerAdapter(failure_threshold=3, recovery_timeout=10.0)

        async def success_operation():
            """Test helper for success operation."""
            return "success"

        # Successful operations should pass through
        result = await cb.call(success_operation)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_failure_threshold(self):
        """Test circuit breaker opening after failure threshold."""
        cb = CircuitBreakerAdapter(failure_threshold=2, recovery_timeout=10.0)

        async def failing_operation():
            """Test helper for failing operation."""
            raise Exception("Operation failed")

        # First failure
        with pytest.raises(RuntimeError):
            await cb.call(failing_operation)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 1

        # Second failure - should open circuit
        with pytest.raises(RuntimeError):
            await cb.call(failing_operation)
        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 2

        # Further calls should be rejected immediately
        with pytest.raises(Exception, match="Circuit breaker is open"):
            await cb.call(failing_operation)

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovery(self):
        """Test circuit breaker recovery after timeout."""
        cb = CircuitBreakerAdapter(failure_threshold=1, recovery_timeout=0.1)

        async def failing_then_success():
            """Test helper for failing then success."""
            if cb.failure_count == 0:
                raise Exception("First call fails")
            return "success"

        # Trigger circuit open
        with pytest.raises(RuntimeError):
            await cb.call(failing_then_success)
        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        import asyncio

        await asyncio.sleep(0.2)

        # Next call should enter half-open state
        result = await cb.call(lambda: "success")
        assert result == "success"
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0


class TestConnectionPool:
    """Test suite for connection pooling."""

    @pytest.mark.asyncio
    async def test_connection_pool_creation(self):
        """Test connection pool creation and basic functionality."""

        async def create_connection():
            """Test helper for create connection."""
            mock_conn = Mock()
            mock_conn.is_connected = True
            mock_conn.close = AsyncMock()
            return mock_conn

        pool = ConnectionPoolAdapter(
            create_connection=create_connection, max_size=3, timeout=5.0
        )

        assert pool.size == 0
        assert pool.active_connections == 0

    @pytest.mark.asyncio
    async def test_connection_pool_acquire_release(self):
        """Test acquiring and releasing connections."""

        async def create_connection():
            """Test helper for create connection."""
            mock_conn = Mock()
            mock_conn.is_connected = True
            mock_conn.close = AsyncMock()
            return mock_conn

        pool = ConnectionPool(create_connection=create_connection, max_size=2)

        # Acquire connection
        conn1 = await pool.acquire()
        assert conn1 is not None
        assert pool.active_connections == 1

        # Acquire another connection
        conn2 = await pool.acquire()
        assert conn2 is not None
        assert pool.active_connections == 2
        assert conn1 != conn2  # Should be different connections

        # Release connections
        await pool.release(conn1)
        assert pool.active_connections == 1

        await pool.release(conn2)
        assert pool.active_connections == 0

    @pytest.mark.asyncio
    async def test_connection_pool_max_size(self):
        """Test connection pool respects max size."""

        connection_count = 0

        async def create_connection():
            """Test helper for create connection."""
            nonlocal connection_count
            connection_count += 1
            mock_conn = Mock()
            mock_conn.id = connection_count
            mock_conn.is_connected = True
            mock_conn.close = AsyncMock()
            return mock_conn

        pool = ConnectionPool(create_connection=create_connection, max_size=2)

        # Acquire up to max size
        conn1 = await pool.acquire()
        await pool.acquire()

        # Pool should reuse connections when at max capacity
        await pool.release(conn1)
        conn3 = await pool.acquire()

        # conn3 should be the reused conn1
        assert conn3.id == conn1.id

    @pytest.mark.asyncio
    async def test_connection_pool_cleanup(self):
        """Test connection pool cleanup."""

        created_connections = []

        async def create_connection():
            """Test helper for create connection."""
            mock_conn = Mock()
            mock_conn.is_connected = True
            mock_conn.close = AsyncMock()
            created_connections.append(mock_conn)
            return mock_conn

        pool = ConnectionPool(create_connection=create_connection, max_size=2)

        # Create some connections
        conn1 = await pool.acquire()
        conn2 = await pool.acquire()
        await pool.release(conn1)
        await pool.release(conn2)

        # Cleanup should close all connections
        await pool.cleanup()

        for conn in created_connections:
            conn.close.assert_called_once()

        assert pool.size == 0
        assert pool.active_connections == 0

    @pytest.mark.asyncio
    async def test_connection_pool_acquire_timeout_path(self):
        """Test legacy ConnectionPool timeout when max size is exhausted."""

        async def create_connection():
            """Test helper for create connection."""
            mock_conn = Mock()
            mock_conn.close = AsyncMock()
            return mock_conn

        pool = ConnectionPool(
            create_connection=create_connection, max_size=1, timeout=0.05
        )

        first = await pool.acquire()
        assert first is not None

        with pytest.raises(TimeoutError):
            await pool.acquire()

    @pytest.mark.asyncio
    async def test_connection_pool_release_unknown_connection_noop(self):
        """Test release no-op branch for connection not tracked in in-use set."""

        async def create_connection():
            """Test helper for create connection."""
            mock_conn = Mock()
            mock_conn.close = AsyncMock()
            return mock_conn

        pool = ConnectionPool(create_connection=create_connection, max_size=1)

        unknown_conn = Mock()
        await pool.release(unknown_conn)
        assert pool.active_connections == 0


class TestConnectionPoolAdapterExtras:
    """Additional branch coverage for ConnectionPoolAdapter initialization and legacy fields."""

    @pytest.mark.asyncio
    async def test_connection_pool_adapter_default_factory_and_aliases(self):
        """Test constructor branches for default adapter factory, max_size alias, and timeout alias."""
        adapter = ConnectionPoolAdapter(
            adapter_factory=None,
            create_connection=None,
            max_size=2,
            timeout=1.5,
            config={"mock_solidworks": True},
        )

        assert adapter.pool_size == 2
        assert adapter.timeout == 1.5
        assert adapter.adapter_factory is not None

    def test_adapter_health_legacy_key_membership(self):
        """Test AdapterHealth __contains__ and __getitem__ legacy compatibility keys."""
        from datetime import datetime

        from solidworks_mcp.adapters.base import AdapterHealth

        health = AdapterHealth(
            healthy=True,
            last_check=datetime.now(),
            error_count=0,
            success_count=1,
            average_response_time=0.01,
            connection_status="connected",
            metrics={"adapter_type": "mock", "version": "x", "uptime": 1.0},
        )

        assert "status" in health
        assert "connected" in health
        assert "adapter_type" in health
        assert health["status"] == "healthy"
        assert health["connected"] is True

    @pytest.mark.asyncio
    async def test_connection_wrappers_forward_add_centerline(self):
        """Test that wrapper adapters expose add_centerline passthroughs."""

        class _StubAdapter:
            """Test suite for StubAdapter."""

            async def connect(self):
                """Test helper for connect."""
                return None

            async def disconnect(self):
                """Test helper for disconnect."""
                return None

            async def add_centerline(self, x1, y1, x2, y2):
                """Test helper for add centerline."""
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS,
                    data=f"centerline:{x1},{y1}->{x2},{y2}",
                )

        circuit = CircuitBreakerAdapter(adapter=_StubAdapter())
        circuit_result = await circuit.add_centerline(0.0, 0.0, 10.0, 0.0)
        assert circuit_result.is_success
        assert circuit_result.data == "centerline:0.0,0.0->10.0,0.0"

        pool = ConnectionPoolAdapter(adapter_factory=lambda: _StubAdapter(), max_size=1)
        await pool.connect()
        pool_result = await pool.add_centerline(0.0, 0.0, 10.0, 0.0)
        assert pool_result.is_success
        assert pool_result.data == "centerline:0.0,0.0->10.0,0.0"
        await pool.disconnect()


class TestPyWin32AdapterBranches:
    """Additional PyWin32Adapter branch coverage with pure mocks."""

    @staticmethod
    def _build_adapter(monkeypatch) -> PyWin32Adapter:
        """Test helper for build adapter."""
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.PYWIN32_AVAILABLE", True
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.platform.system",
            lambda: "Windows",
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pywintypes",
            SimpleNamespace(com_error=RuntimeError),
            raising=False,
        )
        adapter = PyWin32Adapter({})
        # Synchronous mock ComExecutor: avoids "ComExecutor is not running"
        # guard in _handle_com_operation for tests that set swApp/currentModel
        # directly without going through connect().
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_com = MagicMock()
        mock_com._thread = mock_thread
        mock_com.run = lambda fn, timeout=None: fn()
        adapter._com = mock_com
        return adapter

    @pytest.mark.asyncio
    async def test_explicit_start_reuses_existing_com_instance(self, monkeypatch):
        """Explicit launch must not create a second SolidWorks process."""
        adapter = self._build_adapter(monkeypatch)
        adapter._com_initialized = True
        app = SimpleNamespace(
            RevisionNumber="2019",
            SetUserPreferenceToggle=Mock(),
            Visible=False,
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.win32com",
            SimpleNamespace(client=SimpleNamespace(GetActiveObject=Mock(return_value=app))),
            raising=False,
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter._dynamic_module",
            SimpleNamespace(Dispatch=Mock(return_value=app)),
            raising=False,
        )

        result = await adapter.start_solidworks()

        assert result["already_running"] is True
        assert result["started"] is False
        assert result["connected"] is True
        assert app.Visible is True

    @pytest.mark.asyncio
    async def test_explicit_start_launches_configured_executable(self, monkeypatch, tmp_path):
        """Explicit launch waits for the newly started executable to register COM."""
        executable = tmp_path / "SLDWORKS.exe"
        executable.touch()
        adapter = self._build_adapter(monkeypatch)
        adapter.config_dict["solidworks_path"] = str(executable)
        adapter.config_dict["solidworks_startup_timeout_seconds"] = 5
        adapter._com_initialized = True
        app = SimpleNamespace(
            RevisionNumber="2019",
            SetUserPreferenceToggle=Mock(),
            Visible=False,
        )
        active_calls = [RuntimeError("not registered"), app]
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.win32com",
            SimpleNamespace(
                client=SimpleNamespace(GetActiveObject=Mock(side_effect=active_calls))
            ),
            raising=False,
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter._dynamic_module",
            SimpleNamespace(Dispatch=Mock(return_value=app)),
            raising=False,
        )
        process = SimpleNamespace(pid=4321, poll=Mock(return_value=None))
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.subprocess.Popen",
            Mock(return_value=process),
        )

        async def fast_sleep(_seconds):
            return None

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.asyncio.sleep", fast_sleep
        )

        result = await adapter.start_solidworks()

        assert result["started"] is True
        assert result["already_running"] is False
        assert result["pid"] == 4321
        assert result["launcher"] == str(executable)

    @pytest.mark.asyncio
    async def test_existing_process_without_com_is_waited_for_not_relaunched(self, monkeypatch):
        adapter = self._build_adapter(monkeypatch)
        adapter._com_initialized = True
        app = SimpleNamespace(RevisionNumber="2019", SetUserPreferenceToggle=Mock(), Visible=False)
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.win32com",
            SimpleNamespace(client=SimpleNamespace(GetActiveObject=Mock(side_effect=[RuntimeError("loading"), app]))),
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter._dynamic_module",
            SimpleNamespace(Dispatch=Mock(return_value=app)),
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.running_solidworks_process_ids", lambda: (123,),
        )
        launch = Mock(side_effect=AssertionError("must not launch twice"))
        monkeypatch.setattr("solidworks_mcp.adapters.pywin32_adapter.subprocess.Popen", launch)
        result = await adapter.start_solidworks(5)
        assert result["started"] is False
        assert result["already_running"] is True
        assert result["pid"] == 123
        launch.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_timeout_retains_process_for_retry(self, monkeypatch, tmp_path):
        adapter = self._build_adapter(monkeypatch)
        executable = tmp_path / "SLDWORKS.exe"
        executable.touch()
        adapter.config_dict["solidworks_path"] = str(executable)
        coordinator = adapter._session_coordinator
        monkeypatch.setattr(coordinator, "initialize_com_apartment", Mock())
        monkeypatch.setattr(coordinator, "uninitialize_com_apartment", Mock())
        acquire = Mock(return_value=None)
        monkeypatch.setattr(coordinator, "_try_acquire_active_application", acquire)
        process = SimpleNamespace(pid=456, poll=Mock(return_value=None))
        launch = Mock(return_value=process)
        monkeypatch.setattr("solidworks_mcp.adapters.pywin32_adapter.subprocess.Popen", launch)
        monkeypatch.setattr("solidworks_mcp.adapters.pywin32_adapter.time", SimpleNamespace(monotonic=Mock(side_effect=[0, 6])))
        with pytest.raises(SolidWorksMCPError, match="COM"):
            await adapter.start_solidworks(5)
        assert adapter._launched_process is process
        acquire.side_effect = [None, object()]
        monkeypatch.setattr(coordinator, "_prepare_connected_application", AsyncMock())
        monkeypatch.setattr("solidworks_mcp.adapters.pywin32_adapter.time", SimpleNamespace(monotonic=Mock(side_effect=[10, 10])))
        result = await adapter.start_solidworks(5)
        assert result["started"] is False
        assert result["pid"] == 456
        launch.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["connect", "start_solidworks"])
    async def test_cancelled_connection_cleans_com_before_releasing_lease(self, monkeypatch, method):
        from solidworks_mcp.adapters import pywin32_adapter as module

        adapter = self._build_adapter(monkeypatch)
        coordinator = adapter._session_coordinator
        monkeypatch.setattr(coordinator, "initialize_com_apartment", lambda: setattr(adapter, "_com_initialized", True))
        monkeypatch.setattr(coordinator, "uninitialize_com_apartment", lambda: setattr(adapter, "_com_initialized", False))
        app = object()
        monkeypatch.setattr(coordinator, "acquire_solidworks_application", AsyncMock(return_value=app))
        monkeypatch.setattr(coordinator, "_try_acquire_active_application", Mock(return_value=app))

        async def cancelled_prepare(app):
            adapter.swApp = app
            raise asyncio.CancelledError()

        monkeypatch.setattr(coordinator, "_prepare_connected_application", cancelled_prepare)
        with pytest.raises(asyncio.CancelledError):
            await getattr(adapter, method)()
        assert adapter.swApp is None
        assert adapter._com_initialized is False
        other = object()
        module.AUTOMATION_PROCESS_LEASE.acquire(other)
        module.AUTOMATION_PROCESS_LEASE.release(other)
        assert module.AUTOMATION_PROCESS_LEASE._file is None

    @pytest.mark.asyncio
    async def test_health_check_and_model_info_branches(self, monkeypatch):
        """Test healthy/disconnected checks and model-info default branches."""
        adapter = self._build_adapter(monkeypatch)

        adapter.swApp = SimpleNamespace(RevisionNumber=lambda: "33.2")
        healthy = await adapter.health_check()
        assert healthy.healthy is True
        assert healthy.connection_status == "connected"
        assert healthy.metrics["sw_version"] == "33.2"

        adapter.swApp = SimpleNamespace(RevisionNumber=lambda: None)
        unhealthy = await adapter.health_check()
        assert unhealthy.healthy is False
        assert unhealthy.connection_status == "disconnected"

        adapter.currentModel = SimpleNamespace(
            GetTitle=lambda: "Model1",
            GetPathName=lambda: "C:/tmp/model1.sldprt",
            GetType=lambda: 99,
            GetActiveConfiguration=lambda: None,
            GetSaveFlag=lambda: True,
            GetRebuildStatus=lambda: 0,
            FeatureManager=SimpleNamespace(GetFeatureCount=lambda include_hidden: 7),
        )
        info = await adapter.get_model_info()
        assert info.is_success
        assert info.data["type"] == "Unknown"
        assert info.data["configuration"] == "Default"

        adapter.currentModel = SimpleNamespace(
            GetTitle=lambda: "Model1",
            GetPathName=lambda: "C:/tmp/model1.sldprt",
            GetType=lambda: 1,
        )
        limited_info = await adapter.get_model_info()
        assert limited_info.is_success
        assert limited_info.data["configuration"] == "Default"
        assert limited_info.data["feature_count"] == 0

    @pytest.mark.asyncio
    async def test_save_export_close_and_dimension_error_paths(self, monkeypatch):
        """Test save/export/close operation branches and dimension failure paths."""
        adapter = self._build_adapter(monkeypatch)
        # Avoid real filesystem calls: makedirs is a no-op and existence check
        # always returns True so SaveAs3 returning True is treated as success.
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.makedirs",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.path.exists",
            lambda p: True,
        )

        no_model = await adapter.close_model()
        assert no_model.status.name == "WARNING"

        model = SimpleNamespace(
            Save=Mock(),
            Save3=Mock(return_value=False),
            SaveAs3=Mock(return_value=False),
            Parameter=Mock(return_value=None),
            ForceRebuild3=Mock(return_value=False),
            GetTitle=Mock(return_value="Model1"),
        )
        adapter.currentModel = model
        adapter.swApp = SimpleNamespace(CloseDoc=Mock())

        assert (await adapter.save_file()).is_error
        assert (await adapter.save_file("C:/tmp/new.sldprt")).is_error
        assert (await adapter.export_file("C:/tmp/out.bad", "badfmt")).is_error

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.path.exists",
            lambda p: False,
        )
        assert (await adapter.export_file("C:/tmp/out.step", "step")).is_error
        assert (await adapter.get_dimension("D1@Sketch1")).is_error
        assert (await adapter.set_dimension("D1@Sketch1", 20.0)).is_error
        assert (await adapter.rebuild_model()).is_error

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.path.exists",
            lambda p: True,
        )
        model.Save3 = Mock(return_value=True)
        model.SaveAs3 = Mock(return_value=True)
        model.ForceRebuild3 = Mock(return_value=True)
        model.Parameter = Mock(
            return_value=SimpleNamespace(
                GetValue3=Mock(return_value=0.015), SetValue3=Mock(return_value=True)
            )
        )

        assert (await adapter.save_file()).is_success
        assert (await adapter.save_file("C:/tmp/new.sldprt")).is_success
        assert (await adapter.export_file("C:/tmp/out.step", "step")).is_success
        assert (await adapter.get_dimension("D1@Sketch1")).data == pytest.approx(15.0)
        assert (await adapter.set_dimension("D1@Sketch1", 20.0)).is_success
        assert (await adapter.rebuild_model()).is_success

        closed = await adapter.close_model(save=True)
        assert closed.is_success
        assert adapter.currentModel is None
        adapter.swApp.CloseDoc.assert_any_call("Model1")

    @pytest.mark.asyncio
    async def test_close_model_accepts_string_title_property(self, monkeypatch):
        """CloseModel supports late-bound COM title properties as well as methods."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(GetTitle="SavedAssembly.sldasm")
        adapter.swApp = SimpleNamespace(CloseDoc=Mock())

        closed = await adapter.close_model()

        assert closed.is_success
        assert adapter.currentModel is None
        adapter.swApp.CloseDoc.assert_called_once_with("SavedAssembly.sldasm")

    @pytest.mark.asyncio
    async def test_drawing_cad_export_uses_model_space_and_restores_preference(
        self, monkeypatch
    ):
        """DWG/DXF export must not leave the global paper-space option changed."""
        adapter = self._build_adapter(monkeypatch)
        model = SimpleNamespace(GetType=Mock(return_value=3), SaveAs3=Mock(return_value=True))
        adapter.currentModel = model
        adapter.swApp = SimpleNamespace(
            ActiveDoc=model,
            GetUserPreferenceToggle=Mock(return_value=True),
            SetUserPreferenceToggle=Mock(),
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.makedirs",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.path.exists",
            lambda _path: False,
        )

        result = await adapter.export_drawing_cad("C:/tmp/model-space.dxf", "dxf")

        assert result.is_success
        adapter.swApp.SetUserPreferenceToggle.assert_has_calls(
            [mock_call(484, False), mock_call(484, True)]
        )
        model.SaveAs3.assert_called_once_with("C:\\tmp\\model-space.dxf", 0, 2)

    @pytest.mark.asyncio
    async def test_caxa_dxf_export_sets_legacy_preferences_and_restores_them(
        self, monkeypatch, tmp_path
    ):
        """CAXA export must be R12-only and restore every global preference."""
        adapter = self._build_adapter(monkeypatch)
        model = SimpleNamespace(GetType=Mock(return_value=3), SaveAs3=Mock(return_value=True))
        adapter.currentModel = model
        adapter.swApp = SimpleNamespace(
            ActiveDoc=model,
            GetUserPreferenceToggle=Mock(return_value=True),
            SetUserPreferenceToggle=Mock(),
            GetUserPreferenceIntegerValue=Mock(return_value=8),
            SetUserPreferenceIntegerValue=Mock(),
        )
        compatibility_data = {"profile": "caxa_2015", "dxf_version": "R12"}
        normalize = Mock(return_value=compatibility_data)
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.normalize_for_caxa_2015",
            normalize,
        )

        target = tmp_path / "drawing-caxa2015.dxf"
        result = await adapter.export_drawing_cad(
            str(target), "dxf", True, "caxa_2015"
        )

        assert result.is_success
        assert result.data == compatibility_data
        assert target.exists() is False
        adapter.swApp.SetUserPreferenceToggle.assert_has_calls(
            [
                mock_call(484, False),
                mock_call(8, False),
                mock_call(305, False),
                mock_call(316, True),
                mock_call(358, False),
                mock_call(400, False),
                mock_call(484, True),
                mock_call(8, True),
                mock_call(305, True),
                mock_call(316, True),
                mock_call(358, True),
                mock_call(400, True),
            ]
        )
        adapter.swApp.SetUserPreferenceIntegerValue.assert_has_calls(
            [mock_call(0, 0), mock_call(0, 8)]
        )
        normalize.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_all_session_docs_only_closes_tracked_documents(self, monkeypatch):
        """Session cleanup uses each tracked document's current title."""
        adapter = self._build_adapter(monkeypatch)
        first = SimpleNamespace(GetTitle=Mock(return_value="saved-part.sldprt"))
        second = SimpleNamespace(GetTitle=Mock(return_value="Drawing1"))
        adapter.swApp = SimpleNamespace(
            GetDocuments=(first, second),
            CloseDoc=Mock(),
        )
        adapter.currentModel = second
        adapter._track_session_document(first)
        adapter._track_session_document(second)

        result = await adapter.close_all_session_docs()

        assert result.is_success
        assert adapter.currentModel is None
        assert adapter._session_owned_documents == []
        assert adapter.swApp.CloseDoc.call_args_list == [
            mock_call("Drawing1"),
            mock_call("saved-part.sldprt"),
        ]

    @pytest.mark.asyncio
    async def test_close_all_session_docs_keeps_documents_that_could_not_close(
        self, monkeypatch
    ):
        """Failed closes remain tracked so cleanup can be retried safely."""
        adapter = self._build_adapter(monkeypatch)
        document = SimpleNamespace(GetTitle=Mock(return_value="locked.sldprt"))
        adapter.swApp = SimpleNamespace(
            GetDocuments=(document,),
            CloseDoc=Mock(side_effect=RuntimeError("locked")),
        )
        adapter._track_session_document(document)

        result = await adapter.close_all_session_docs()

        assert result.is_error
        assert adapter._session_owned_documents == [document]

    @pytest.mark.asyncio
    async def test_close_all_session_docs_does_not_close_replacement_document(
        self, monkeypatch
    ):
        """A stale MCP wrapper must not close a user-reopened replacement."""
        adapter = self._build_adapter(monkeypatch)

        class _Document:
            def __init__(self, title: str) -> None:
                self.title = title

            def GetTitle(self) -> str:
                return self.title

        original = _Document("fixture.sldprt")
        replacement = _Document("fixture.sldprt")
        adapter.swApp = SimpleNamespace(
            GetDocuments=(replacement,),
            CloseDoc=Mock(),
        )
        adapter._track_session_document(original)

        result = await adapter.close_all_session_docs()

        assert result.is_success
        assert adapter._session_owned_documents == []
        adapter.swApp.CloseDoc.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_all_session_docs_keeps_documents_when_enumeration_fails(
        self, monkeypatch
    ):
        """Cleanup refuses a title-only close when SolidWorks cannot enumerate docs."""
        adapter = self._build_adapter(monkeypatch)
        document = SimpleNamespace(GetTitle=Mock(return_value="fixture.sldprt"))
        adapter.swApp = SimpleNamespace(
            GetDocuments=Mock(side_effect=RuntimeError("COM busy")),
            CloseDoc=Mock(),
        )
        adapter._track_session_document(document)

        result = await adapter.close_all_session_docs()

        assert result.is_error
        assert adapter._session_owned_documents == [document]
        adapter.swApp.CloseDoc.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_all_session_docs_keeps_documents_when_enumeration_api_missing(
        self, monkeypatch
    ):
        """Cleanup refuses a title-only close when GetDocuments is unavailable."""
        adapter = self._build_adapter(monkeypatch)
        document = SimpleNamespace(GetTitle=Mock(return_value="fixture.sldprt"))
        adapter.swApp = SimpleNamespace(CloseDoc=Mock())
        adapter._track_session_document(document)

        result = await adapter.close_all_session_docs()

        assert result.is_error
        assert adapter._session_owned_documents == [document]
        adapter.swApp.CloseDoc.assert_not_called()

    @pytest.mark.asyncio
    async def test_sketch_placeholder_and_exit_paths(self, monkeypatch):
        """Test sketch operation helpers and exit-sketch branches.

        The linear pattern call is wired with a fake SketchManager so the
        real impl (``CreateLinearSketchStepAndRepeat`` via the executor)
        succeeds. Circular pattern, mirror, and offset are still
        placeholders at this point in the stack — they don't touch COM
        and so only need ``currentSketchManager`` to be truthy.
        """
        adapter = self._build_adapter(monkeypatch)

        no_sketch = await adapter.exit_sketch()
        assert no_sketch.status.name == "WARNING"

        adapter.currentSketchManager = SimpleNamespace(InsertSketch=Mock())

        # Unknown relation types now error out — the placeholder that always
        # returned success was replaced by a real SketchAddConstraints call.
        assert (await adapter.add_sketch_constraint("L1", None, "unknown")).is_error
        # add_sketch_dimension's no-model fast path returns a synthesised ID
        # when ``adapter.currentModel`` is ``None``.
        assert (
            await adapter.add_sketch_dimension("L1", None, "linear", 10.0)
        ).is_success

        # Linear, circular, and mirror are real COM-touching impls now
        # (placeholders only before #16/#17/#18). Wire the minimum:
        #   currentSketchManager.CreateLinearSketchStepAndRepeat(...)
        #   currentSketchManager.CreateCircularSketchStepAndRepeat(...)
        #   currentModel.SketchMirror() (mirror)
        #   currentModel.ClearSelection2 and SelectionManager.CreateSelectData
        #   adapter._sketch_entities[entity_id].Select4(...)
        #   The mirror_line entity must use a ``Centerline_*`` ID since
        #   the real impl rejects anything else.
        seed_entity = Mock()
        seed_entity.Select4 = Mock(return_value=True)
        # Circular pattern now requires a resolvable seed centre — either via
        # GetCenterPoint (single dispatch) or via _sketch_entity_centers
        # (group entities). Wire GetCenterPoint so this test's seed stands in
        # for a circle/arc/ellipse rather than a line.
        seed_entity.GetCenterPoint = Mock(return_value=(0.030, 0.0))
        centerline = Mock()
        centerline.Select4 = Mock(return_value=True)
        adapter._sketch_entities = {
            "L1": seed_entity,
            "Centerline_42": centerline,
        }

        adapter.currentSketchManager = SimpleNamespace(
            InsertSketch=Mock(),
            CreateLinearSketchStepAndRepeat=Mock(return_value=True),
            CreateCircularSketchStepAndRepeat=Mock(return_value=True),
            SketchOffset2=Mock(return_value=True),
        )

        select_data = Mock()
        selection_mgr = Mock()
        selection_mgr.CreateSelectData = Mock(return_value=select_data)
        adapter.currentModel = SimpleNamespace(
            ClearSelection2=Mock(return_value=True),
            SelectionManager=selection_mgr,
            SketchMirror=Mock(return_value=None),
        )

        assert (
            await adapter.sketch_linear_pattern(["L1"], 1.0, 0.0, 5.0, 3)
        ).is_success
        assert (await adapter.sketch_circular_pattern(["L1"], 180.0, 4)).is_success
        assert (await adapter.sketch_mirror(["L1"], "Centerline_42")).is_success
        assert (await adapter.sketch_offset(["L1"], 1.0, True)).is_success

        exited = await adapter.exit_sketch()
        assert exited.is_success
        assert adapter.currentSketchManager is None

    @pytest.mark.asyncio
    async def test_list_features_prefers_tree_traversal(self, monkeypatch):
        """List features should traverse FirstFeature/GetNextFeature when available."""
        adapter = self._build_adapter(monkeypatch)

        class _Feature:
            """Test suite for Feature."""

            def __init__(self, name, feature_type, suppressed=False):
                """Test helper for init."""
                self.Name = name
                self._feature_type = feature_type
                self._suppressed = suppressed
                self._next = None

            def GetTypeName2(self):
                """Test helper for GetTypeName2."""
                return self._feature_type

            def IsSuppressed(self):
                """Test helper for IsSuppressed."""
                return self._suppressed

            def GetNextFeature(self):
                """Test helper for GetNextFeature."""
                return self._next

        f1 = _Feature("Front Plane", "RefPlane", False)
        f2 = _Feature("Boss-Extrude1", "Boss", False)
        f3 = _Feature("Cut-Extrude1", "Cut", True)
        f1._next = f2
        f2._next = f3

        adapter.currentModel = SimpleNamespace(
            FirstFeature=lambda: f1,
            FeatureManager=SimpleNamespace(GetFeatureCount=lambda _all: 0),
        )

        result = await adapter.list_features(include_suppressed=False)

        assert result.is_success
        assert [row["name"] for row in result.data] == ["Front Plane", "Boss-Extrude1"]

    @pytest.mark.asyncio
    async def test_list_features_falls_back_to_reverse_positions(self, monkeypatch):
        """List features should fall back to model FeatureByPositionReverse when needed."""
        adapter = self._build_adapter(monkeypatch)

        class _Feature:
            """Test suite for Feature."""

            def __init__(self, name, feature_type):
                """Test helper for init."""
                self.Name = name
                self._feature_type = feature_type

            def GetTypeName2(self):
                """Test helper for GetTypeName2."""
                return self._feature_type

            def IsSuppressed(self):
                """Test helper for IsSuppressed."""
                return False

        by_pos = {
            1: _Feature("Boss-Extrude1", "Boss"),
            2: _Feature("Sketch1", "ProfileFeature"),
        }

        adapter.currentModel = SimpleNamespace(
            FirstFeature=lambda: (_ for _ in ()).throw(RuntimeError("not available")),
            FeatureByPositionReverse=lambda pos: by_pos.get(pos),
            FeatureManager=SimpleNamespace(GetFeatureCount=lambda _all: 2),
        )

        result = await adapter.list_features(include_suppressed=False)

        assert result.is_success
        assert [row["name"] for row in result.data] == ["Boss-Extrude1", "Sketch1"]

    @pytest.mark.asyncio
    async def test_list_features_suppressed_fallback_uses_is_suppressed2(
        self, monkeypatch
    ):
        """When IsSuppressed is unavailable, list_features should fallback to IsSuppressed2."""
        adapter = self._build_adapter(monkeypatch)

        class _Feature:
            """Test suite for Feature."""

            Name = "SuppressedCut"

            def GetTypeName2(self):
                """Test helper for GetTypeName2."""
                return "Cut"

            def IsSuppressed(self):
                """Test helper for IsSuppressed."""
                raise RuntimeError("IsSuppressed unavailable")

            def IsSuppressed2(self, *_args):
                """Test helper for IsSuppressed2."""
                return (True,)

            def GetNextFeature(self):
                """Test helper for GetNextFeature."""
                return None

        adapter.currentModel = SimpleNamespace(
            FirstFeature=lambda: _Feature(),
            FeatureManager=SimpleNamespace(GetFeatureCount=lambda _all: 0),
        )

        hidden = await adapter.list_features(include_suppressed=False)
        shown = await adapter.list_features(include_suppressed=True)

        assert hidden.is_success
        assert hidden.data == []
        assert shown.is_success
        assert len(shown.data) == 1
        assert shown.data[0]["suppressed"] is True

    @pytest.mark.asyncio
    async def test_list_configurations_supports_tuple_property(self, monkeypatch):
        """list_configurations should handle COM variants exposing names as tuple properties."""
        adapter = self._build_adapter(monkeypatch)

        adapter.currentModel = SimpleNamespace(GetConfigurationNames=("Default", "Alt"))
        result = await adapter.list_configurations()

        assert result.is_success
        assert result.data == ["Default", "Alt"]

    @pytest.mark.asyncio
    async def test_list_configurations_falls_back_to_active_configuration(
        self, monkeypatch
    ):
        """list_configurations should fall back to the active configuration name."""
        adapter = self._build_adapter(monkeypatch)

        adapter.currentModel = SimpleNamespace(
            GetConfigurationNames=Mock(return_value=None),
            GetActiveConfiguration=Mock(
                return_value=SimpleNamespace(GetName=Mock(return_value="Default"))
            ),
        )

        result = await adapter.list_configurations()

        assert result.is_success
        assert result.data == ["Default"]

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        __import__("platform").system() != "Windows",
        reason="win32com only available on Windows",
    )
    async def test_connect_disconnect_and_document_creation_paths(self, monkeypatch):
        """Test COM connect lifecycle plus model creation/open branches with mocks."""
        adapter = self._build_adapter(monkeypatch)

        co_initialize = Mock()
        co_uninitialize = Mock()
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pythoncom",
            SimpleNamespace(
                CoInitialize=co_initialize,
                CoUninitialize=co_uninitialize,
                VT_BYREF=0x4000,
                VT_I4=3,
            ),
            raising=False,
        )

        fake_app = SimpleNamespace(
            Visible=False,
            SetUserPreferenceToggle=Mock(),
            OpenDoc6=Mock(),
            NewDocument=Mock(),
            CloseDoc=Mock(),
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.win32com",
            SimpleNamespace(
                client=SimpleNamespace(
                    GetActiveObject=Mock(return_value=fake_app),
                    VARIANT=lambda _kind, val: val,
                )
            ),
            raising=False,
        )

        import win32com.client.dynamic as _win32_dynamic

        monkeypatch.setattr(_win32_dynamic, "Dispatch", Mock(return_value=fake_app))

        await adapter.connect()
        assert adapter.swApp is fake_app
        assert fake_app.Visible is True
        fake_app.SetUserPreferenceToggle.assert_any_call(150, False)
        fake_app.SetUserPreferenceToggle.assert_any_call(149, False)
        # CoInitialize now runs on the ComExecutor thread, not in connect() directly.

        fake_open_model = SimpleNamespace(
            GetTitle=lambda: "OpenedAsm",
            GetActiveConfiguration=lambda: None,
            GetSaveTime=lambda: "now",
        )
        fake_app.OpenDoc6.return_value = fake_open_model
        fake_app.GetUserPreferenceStringValue = Mock(
            side_effect=lambda idx: {
                0: "C:/Templates/Part.prtdot",
                1: "",
                2: "",
            }[idx]
        )

        fake_app.NewDocument.side_effect = lambda template, *_args: SimpleNamespace(
            GetTitle=lambda: (
                template.split("/")[-1]
                .replace(".prtdot", "")
                .replace(".asmdot", "")
                .replace(".drwdot", "")
            )
        )

        opened = await adapter.open_model("C:/Models/sample.sldasm")
        assert opened.is_success
        assert opened.data.type == "Assembly"
        assert opened.data.configuration == "Default"

        unsupported = await adapter.open_model("C:/Models/sample.txt")
        assert unsupported.is_error
        assert "Unsupported file type" in (unsupported.error or "")

        part = await adapter.create_part()
        assembly = await adapter.create_assembly()
        drawing = await adapter.create_drawing()
        assert part.is_success and part.data.type == "Part"
        assert assembly.is_success and assembly.data.type == "Assembly"
        assert drawing.is_success and drawing.data.type == "Drawing"

        adapter.currentModel = SimpleNamespace(GetTitle=lambda: "ActiveModel")
        adapter.currentSketch = object()
        adapter.currentSketchManager = object()
        toggle_call_count = fake_app.SetUserPreferenceToggle.call_count
        fake_app.ExitApp = Mock()
        fake_app.Quit = Mock()
        await adapter.disconnect()
        assert fake_app.SetUserPreferenceToggle.call_count == toggle_call_count
        fake_app.ExitApp.assert_not_called()
        fake_app.Quit.assert_not_called()
        assert adapter.swApp is None
        assert adapter.currentSketchManager is None
        # CoUninitialize now runs on the ComExecutor thread, not in disconnect() directly.

    @pytest.mark.asyncio
    async def test_sketch_geometry_feature_and_mass_property_paths(self, monkeypatch):
        """Test sketch/entity creation, feature creation, and mass properties with full mocks."""
        adapter = self._build_adapter(monkeypatch)

        feature_id = SimpleNamespace(ToString=lambda: "feat-1")
        feature_obj = SimpleNamespace(Name="Feat1", GetID=lambda: feature_id)

        sketch_manager = SimpleNamespace(
            InsertSketch=Mock(return_value=SimpleNamespace(Name="SketchA")),
            CreateLine=Mock(return_value=object()),
            CreateCircleByRadius=Mock(return_value=object()),
            CreateCornerRectangle=Mock(return_value=object()),
            CreateArc=Mock(return_value=object()),
            CreateSpline2=Mock(return_value=object()),
            CreateCenterLine=Mock(return_value=object()),
            CreatePolygon=Mock(return_value=object()),
            CreateEllipse=Mock(return_value=object()),
        )
        feature_manager = SimpleNamespace(
            FeatureExtrusion3=Mock(return_value=feature_obj),
            FeatureExtrusion2=Mock(return_value=feature_obj),
            FeatureExtruThin2=Mock(return_value=feature_obj),
            FeatureRevolve2=Mock(return_value=feature_obj),
            FeatureCut3=Mock(return_value=feature_obj),
            FeatureFillet3=Mock(return_value=feature_obj),
            FeatureChamfer=Mock(return_value=feature_obj),
            InsertFeatureChamfer=Mock(return_value=feature_obj),
        )
        mass_props = SimpleNamespace(
            Volume=2.0e-9,
            SurfaceArea=5.0e-6,
            Mass=0.25,
            CenterOfMass=[0.01, 0.02, 0.03],
            GetMomentOfInertia=lambda _about: [1, 2, 3, 4, 5, 6, 7, 8, 9],
        )
        extension = SimpleNamespace(
            SelectByID2=Mock(return_value=True),
            CreateMassProperty=Mock(return_value=mass_props),
        )
        adapter.currentModel = SimpleNamespace(
            Extension=extension,
            SketchManager=sketch_manager,
            FeatureManager=feature_manager,
            ClearSelection2=Mock(return_value=True),
        )

        created_sketch = await adapter.create_sketch("XY")
        assert created_sketch.is_success
        assert created_sketch.data == "SketchA"

        assert (await adapter.add_line(0, 0, 10, 0)).is_success
        assert (await adapter.add_circle(0, 0, 5)).is_success
        assert (await adapter.add_rectangle(0, 0, 5, 3)).is_success
        assert (await adapter.add_arc(0, 0, 1, 0, 0, 1)).is_success
        assert (
            await adapter.add_spline(
                [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 0.0}]
            )
        ).is_success
        assert (await adapter.add_centerline(0, 0, 0, 10)).is_success
        assert (await adapter.add_polygon(0, 0, 5, 6)).is_success
        assert (await adapter.add_ellipse(0, 0, 10, 6)).is_success

        extrude_standard = await adapter.create_extrusion(
            SimpleNamespace(
                depth=10.0,
                draft_angle=2.0,
                reverse_direction=False,
                thin_feature=False,
                thin_thickness=None,
                merge_result=True,
            )
        )
        extrude_thin = await adapter.create_extrusion(
            SimpleNamespace(
                depth=8.0,
                draft_angle=0.0,
                reverse_direction=True,
                thin_feature=True,
                thin_thickness=1.0,
                merge_result=True,
            )
        )
        revolve = await adapter.create_revolve(
            SimpleNamespace(
                angle=180.0,
                reverse_direction=False,
                both_directions=True,
                thin_feature=False,
                thin_thickness=None,
                merge_result=True,
            )
        )
        cut = await adapter.create_cut_extrude(
            SimpleNamespace(depth=4.0, draft_angle=1.0, reverse_direction=False)
        )
        fillet = await adapter.add_fillet(2.0, ["Edge1", "Edge2"])
        chamfer = await adapter.add_chamfer(1.5, ["Edge1"])
        mass = await adapter.get_mass_properties()

        assert extrude_standard.is_success
        assert extrude_thin.is_success
        assert revolve.is_success
        assert cut.is_success
        assert fillet.is_success
        assert chamfer.is_success
        assert mass.is_success
        assert mass.data.volume == pytest.approx(2.0)
        assert mass.data.surface_area == pytest.approx(5.0)
        assert mass.data.center_of_mass == [10.0, 20.0, 30.0]

        plane_select_calls = [
            call.args
            for call in extension.SelectByID2.call_args_list
            if len(call.args) >= 9
            and call.args[0] == "Top Plane"
            and call.args[1] == "PLANE"
        ]
        assert plane_select_calls, "Expected plane selection call for sketch creation"
        assert any(call_args[7] in ("", None, 0) for call_args in plane_select_calls)
        assert feature_manager.FeatureExtrusion3.called
        assert feature_manager.FeatureExtruThin2.called
        assert feature_manager.FeatureRevolve2.called
        assert feature_manager.FeatureCut3.called

    @pytest.mark.asyncio
    async def test_feature_id_and_mass_properties_tuple_fallback(self, monkeypatch):
        """Feature IDs and mass properties should support common COM compatibility variants."""
        adapter = self._build_adapter(monkeypatch)

        feature_obj = SimpleNamespace(Name="FeatInt", GetID=lambda: 123)
        feature_manager = SimpleNamespace(
            FeatureExtrusion3=Mock(return_value=feature_obj),
            FeatureExtrusion2=Mock(return_value=feature_obj),
            FeatureExtruThin2=Mock(return_value=feature_obj),
            FeatureRevolve2=Mock(return_value=feature_obj),
            FeatureCut3=Mock(return_value=feature_obj),
            FeatureFillet3=Mock(return_value=feature_obj),
            FeatureChamfer=Mock(return_value=feature_obj),
            InsertFeatureChamfer=Mock(return_value=feature_obj),
        )
        sketch_manager = SimpleNamespace(
            InsertSketch=Mock(return_value=SimpleNamespace(Name="SketchA")),
            CreateCenterLine=Mock(return_value=object()),
            CreateLine=Mock(return_value=object()),
        )
        extension = SimpleNamespace(
            SelectByID2=Mock(return_value=True),
            CreateMassProperty=Mock(side_effect=RuntimeError("member not found")),
        )
        adapter.currentModel = SimpleNamespace(
            Extension=extension,
            SketchManager=sketch_manager,
            FeatureManager=feature_manager,
            ClearSelection2=Mock(return_value=True),
            GetMassProperties=(
                0.001,
                0.002,
                0.003,
                4.0e-9,
                7.0e-6,
                0.5,
                11.0,
                22.0,
                33.0,
                44.0,
                55.0,
                66.0,
            ),
        )

        extrude = await adapter.create_extrusion(
            SimpleNamespace(
                depth=10.0,
                draft_angle=0.0,
                reverse_direction=False,
                thin_feature=False,
                thin_thickness=None,
                merge_result=True,
            )
        )
        mass = await adapter.get_mass_properties()

        assert extrude.is_success
        assert extrude.data.id == "123"
        assert mass.is_success
        assert mass.data.volume == pytest.approx(4.0)
        assert mass.data.surface_area == pytest.approx(7.0)
        assert mass.data.mass == pytest.approx(0.5)
        assert mass.data.center_of_mass == [1.0, 2.0, 3.0]
        assert mass.data.moments_of_inertia["Ixx"] == pytest.approx(11.0)
        assert mass.data.moments_of_inertia["Iyy"] == pytest.approx(22.0)
        assert mass.data.moments_of_inertia["Izz"] == pytest.approx(33.0)

    @pytest.mark.asyncio
    async def test_sketch_methods_guard_and_failure_paths(self, monkeypatch):
        """Cover no-active-sketch guards and per-entity creation failures."""
        adapter = self._build_adapter(monkeypatch)

        # Guard returns when sketch manager is missing.
        for call in (
            adapter.add_line(0, 0, 1, 1),
            adapter.add_circle(0, 0, 1),
            adapter.add_rectangle(0, 0, 1, 1),
            adapter.add_arc(0, 0, 1, 0, 0, 1),
            adapter.add_spline(
                [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 0.0}]
            ),
            adapter.add_centerline(0, 0, 1, 1),
            adapter.add_polygon(0, 0, 1, 5),
            adapter.add_ellipse(0, 0, 2, 1),
            adapter.add_sketch_constraint("L1", None, "parallel"),
        ):
            result = await call
            assert result.is_error

        adapter.currentSketchManager = SimpleNamespace(
            CreateLine=Mock(return_value=None),
            CreateCircleByRadius=Mock(return_value=None),
            CreateCornerRectangle=Mock(return_value=None),
            CreateArc=Mock(return_value=None),
            CreateSpline2=Mock(return_value=None),
            CreateCenterLine=Mock(return_value=None),
            CreatePolygon=Mock(return_value=None),
            CreateEllipse=Mock(return_value=None),
        )

        assert "Failed to create line" in (
            (await adapter.add_line(0, 0, 1, 1)).error or ""
        )
        assert "Failed to create circle" in (
            (await adapter.add_circle(0, 0, 1)).error or ""
        )
        assert "Failed to create rectangle" in (
            (await adapter.add_rectangle(0, 0, 1, 1)).error or ""
        )
        assert "Failed to create arc" in (
            (await adapter.add_arc(0, 0, 1, 0, 0, 1)).error or ""
        )
        assert "Failed to create spline" in (
            (
                await adapter.add_spline(
                    [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 0.0}]
                )
            ).error
            or ""
        )
        assert "Failed to create centerline" in (
            (await adapter.add_centerline(0, 0, 1, 1)).error or ""
        )
        assert "Failed to create polygon" in (
            (await adapter.add_polygon(0, 0, 1, 5)).error or ""
        )
        assert "Failed to create ellipse" in (
            (await adapter.add_ellipse(0, 0, 2, 1)).error or ""
        )

    @pytest.mark.asyncio
    async def test_create_sketch_plane_selection_fallback_paths(self, monkeypatch):
        """Cover create_sketch feature-lookup/SelectByID2 fallback and InsertSketch variants."""
        adapter = self._build_adapter(monkeypatch)

        class _FakeComError(Exception):
            """Test suite for FakeComError."""

            pass

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pywintypes",
            SimpleNamespace(com_error=_FakeComError),
            raising=False,
        )

        class _PlaneFeature:
            """Test suite for PlaneFeature."""

            def Select2(self, *_args):
                """Test helper for Select2."""
                return True

        feature_calls = {"count": 0}

        def _feature_by_name(_name):
            """Test helper for feature by name."""
            feature_calls["count"] += 1
            if feature_calls["count"] == 1:
                raise RuntimeError("feature lookup failed")
            return _PlaneFeature()

        adapter.currentModel = SimpleNamespace(
            FeatureByName=_feature_by_name,
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False)),
            SketchManager=SimpleNamespace(
                InsertSketch=Mock(side_effect=[_FakeComError("variant"), None])
            ),
            GetActiveSketch2=Mock(side_effect=RuntimeError("no active sketch object")),
        )

        sketch_result = await adapter.create_sketch("Top")
        assert sketch_result.is_success
        assert str(sketch_result.data).startswith("Sketch_")

        # Force total selection failure to cover explicit error branch.
        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(side_effect=RuntimeError("lookup failed")),
            Extension=SimpleNamespace(
                SelectByID2=Mock(side_effect=RuntimeError("select failed"))
            ),
            SketchManager=SimpleNamespace(InsertSketch=Mock(return_value=None)),
        )
        failed = await adapter.create_sketch("Top")
        assert failed.is_error
        assert "Failed to select plane" in (failed.error or "")

    @pytest.mark.asyncio
    async def test_save_file_fallback_and_noop_paths(self, monkeypatch, tmp_path):
        """Cover save_file fallback branches: SaveAs fallback, Save3 fallback, and clean-file no-op."""
        adapter = self._build_adapter(monkeypatch)

        target = tmp_path / "out.sldprt"

        # SaveAs3 fails, SaveAs fallback also fails -> explicit error branch.
        adapter.swApp = SimpleNamespace(
            CloseDoc=Mock(side_effect=RuntimeError("locked"))
        )
        adapter.currentModel = SimpleNamespace(
            SaveAs3=Mock(return_value=1),
            SaveAs=Mock(return_value=1),
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.remove",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")),
        )
        failed_save_as = await adapter.save_file(str(target))
        assert failed_save_as.is_error
        assert "Failed to save as" in (failed_save_as.error or "")

        # SaveAs3 reports numeric success but file is still missing -> file-not-written error.
        adapter.currentModel = SimpleNamespace(SaveAs3=Mock(return_value=0))
        missing_output = await adapter.save_file(str(target))
        assert missing_output.is_error
        assert "File not written after save" in (missing_output.error or "")

        # Save3 raises; fallback Save returns non-success, but existing path means no-op success.
        existing = tmp_path / "existing.sldprt"
        existing.write_text("x", encoding="utf-8")
        adapter.currentModel = SimpleNamespace(
            Save3=Mock(side_effect=RuntimeError("Save3 unavailable")),
            Save=Mock(return_value=1),
            GetPathName=Mock(return_value=str(existing)),
        )
        no_op = await adapter.save_file()
        assert no_op.is_success

    @pytest.mark.asyncio
    async def test_feature_and_configuration_edge_paths(self, monkeypatch):
        """Cover list_features/list_configurations edge cases and guarded feature ops."""
        adapter = self._build_adapter(monkeypatch)

        # Guard branches on no active model.
        assert (await adapter.list_configurations()).is_error
        assert (
            await adapter.create_cut_extrude(
                SimpleNamespace(depth=1.0, draft_angle=0.0, reverse_direction=False)
            )
        ).is_error
        assert (await adapter.add_fillet(1.0, ["E1"])).is_error
        assert (await adapter.add_chamfer(1.0, ["E1"])).is_error
        assert (await adapter.rebuild_model()).is_error

        class _Feature:
            """Test suite for Feature."""

            def __init__(self, name, next_feature=None):
                """Test helper for init."""
                self.Name = name
                self._next = next_feature

            def IsSuppressed(self):
                """Test helper for IsSuppressed."""
                raise RuntimeError("fallback")

            def IsSuppressed2(self, *_args):
                """Test helper for IsSuppressed2."""
                return 1

            def GetTypeName2(self):
                """Test helper for GetTypeName2."""
                raise RuntimeError("unknown type")

            def GetNextFeature(self):
                """Test helper for GetNextFeature."""
                raise RuntimeError("walk break")

        dup = _Feature("Dup")
        adapter.currentModel = SimpleNamespace(
            FirstFeature=lambda: dup,
            FeatureManager=SimpleNamespace(
                GetFeatureCount=Mock(side_effect=RuntimeError("count fail"))
            ),
            FeatureByPositionReverse=Mock(side_effect=RuntimeError("reverse fail")),
        )
        listed = await adapter.list_features(include_suppressed=True)
        assert listed.is_success
        assert listed.data and listed.data[0]["type"] == "Unknown"

        # list_configurations callable/None/string/list branches.
        adapter.currentModel = SimpleNamespace(
            GetConfigurationNames=Mock(return_value=None)
        )
        assert (await adapter.list_configurations()).data == []
        adapter.currentModel = SimpleNamespace(GetConfigurationNames="Default")
        assert (await adapter.list_configurations()).data == ["Default"]
        adapter.currentModel = SimpleNamespace(GetConfigurationNames=["Default", "Alt"])
        assert (await adapter.list_configurations()).data == ["Default", "Alt"]

    @pytest.mark.asyncio
    async def test_feature_creation_failure_paths(self, monkeypatch):
        """Cover failure branches for cut/fillet/chamfer feature creation."""
        adapter = self._build_adapter(monkeypatch)
        model = SimpleNamespace(
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False)),
            FeatureManager=SimpleNamespace(
                FeatureCut3=Mock(return_value=None),
                FeatureFillet3=Mock(return_value=None),
                FeatureChamfer=Mock(return_value=None),
            ),
        )
        adapter.currentModel = model

        cut_fail = await adapter.create_cut_extrude(
            SimpleNamespace(depth=2.0, draft_angle=0.0, reverse_direction=False)
        )
        assert cut_fail.is_error
        assert "Failed to create cut extrude feature" in (cut_fail.error or "")

        fillet_select_fail = await adapter.add_fillet(1.0, ["Edge1"])
        chamfer_select_fail = await adapter.add_chamfer(1.0, ["Edge1"])
        assert fillet_select_fail.is_error
        assert "Failed to select edge" in (fillet_select_fail.error or "")
        assert chamfer_select_fail.is_error
        assert "Failed to select edge" in (chamfer_select_fail.error or "")

        model.Extension.SelectByID2 = Mock(return_value=True)
        fillet_create_fail = await adapter.add_fillet(1.0, ["Edge1"])
        chamfer_create_fail = await adapter.add_chamfer(1.0, ["Edge1"])
        assert fillet_create_fail.is_error
        assert "Failed to create fillet" in (fillet_create_fail.error or "")
        assert chamfer_create_fail.is_error
        assert "Failed to create chamfer" in (chamfer_create_fail.error or "")

    @pytest.mark.asyncio
    async def test_title_template_and_document_type_fallbacks(self, monkeypatch):
        """Cover _read_model_title, _resolve_template_path, and _get_document_type fallback branches."""
        adapter = self._build_adapter(monkeypatch)

        class _ModelWithTitleProperty:
            """Test suite for ModelWithTitleProperty."""

            GetTitle = 123
            Title = "FromTitleProperty"

        class _ModelUntitled:
            """Test suite for ModelUntitled."""

            def GetTitle(self):
                """Test helper for GetTitle."""
                raise RuntimeError("title fail")

            Title = None

        assert (
            adapter._read_model_title(_ModelWithTitleProperty()) == "FromTitleProperty"
        )
        assert adapter._read_model_title(_ModelUntitled()) == "Untitled"

        adapter.swApp = SimpleNamespace(
            GetUserPreferenceStringValue=Mock(
                side_effect=lambda idx: {
                    8: "C:/Templates/Part.prtdot",
                    0: "C:/Templates/Backup.prtdot",
                }.get(idx, "")
            )
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.os.path.exists",
            lambda p: str(p).endswith("Part.prtdot"),
        )
        resolved = adapter._resolve_template_path([8, 0], ".prtdot")
        assert resolved and resolved.endswith("Part.prtdot")

        adapter.currentModel = None
        assert adapter._get_document_type() == "Unknown"

    @pytest.mark.asyncio
    async def test_create_part_and_assembly_no_template_paths(self, monkeypatch):
        """Cover create_part/create_assembly branches where native creators fail and no template exists."""
        adapter = self._build_adapter(monkeypatch)

        adapter.swApp = SimpleNamespace(
            NewPart=Mock(side_effect=RuntimeError("native part failed")),
            NewAssembly=Mock(side_effect=RuntimeError("native asm failed")),
            GetUserPreferenceStringValue=Mock(return_value=""),
            NewDocument=Mock(return_value=None),
        )
        monkeypatch.setattr(adapter, "_find_bundled_template", lambda _extension: None)

        part_result = await adapter.create_part()
        asm_result = await adapter.create_assembly()
        assert part_result.is_error
        assert "No part template configured" in (part_result.error or "")
        assert asm_result.is_error
        assert "No assembly template configured" in (asm_result.error or "")

    def test_finds_standard_installed_template_when_preferences_are_empty(
        self, monkeypatch, tmp_path
    ):
        """Use a standard ProgramData template without modifying SW preferences."""
        adapter = self._build_adapter(monkeypatch)
        template = (
            tmp_path / "SOLIDWORKS" / "SOLIDWORKS 2026" / "templates" / "gb_part.prtdot"
        )
        template.parent.mkdir(parents=True)
        template.write_text("template", encoding="utf-8")
        monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
        adapter.swApp = SimpleNamespace(
            GetUserPreferenceStringValue=Mock(return_value="")
        )

        assert adapter._resolve_template_path([8, 0], ".prtdot") == str(template)

    @pytest.mark.asyncio
    async def test_save_guard_non_numeric_success_and_rebuild_failure(
        self, monkeypatch
    ):
        """Cover save guard path, non-numeric Save3 success, and rebuild operation failure branch."""
        adapter = self._build_adapter(monkeypatch)

        no_model_save = await adapter.save_file()
        assert no_model_save.is_error

        adapter.currentModel = SimpleNamespace(
            Save3=Mock(return_value=object()),
            ForceRebuild3=Mock(return_value=False),
        )
        # non-numeric truthy value follows bool(value) branch in _is_success
        save_success = await adapter.save_file()
        assert save_success.is_success

        rebuild_fail = await adapter.rebuild_model()
        assert rebuild_fail.is_error
        assert "Failed to rebuild model" in (rebuild_fail.error or "")

    @pytest.mark.asyncio
    async def test_list_features_additional_fallback_branches(self, monkeypatch):
        """Cover list_features branches for nested suppression failures, dedupe, and reverse traversal errors."""
        adapter = self._build_adapter(monkeypatch)

        class _Feature:
            """Test suite for Feature."""

            def __init__(self, name: str, next_feature=None):
                """Test helper for init."""
                self.Name = name
                self._next = next_feature

            def IsSuppressed(self):
                """Test helper for IsSuppressed."""
                raise RuntimeError("primary suppression unavailable")

            def IsSuppressed2(self, *_args):
                """Test helper for IsSuppressed2."""
                raise RuntimeError("secondary suppression unavailable")

            def GetTypeName2(self):
                """Test helper for GetTypeName2."""
                return "Boss"

            def GetNextFeature(self):
                """Test helper for GetNextFeature."""
                return self._next

        duplicate_1 = _Feature("Dup")
        duplicate_2 = _Feature("Dup")
        duplicate_1._next = duplicate_2
        duplicate_2._next = None

        adapter.currentModel = SimpleNamespace(
            FirstFeature=lambda: duplicate_1,
            FeatureManager=SimpleNamespace(
                GetFeatureCount=Mock(side_effect=RuntimeError("count unavailable"))
            ),
            FeatureByPositionReverse=Mock(side_effect=RuntimeError("reverse fail")),
        )

        listed = await adapter.list_features(include_suppressed=True)
        assert listed.is_success
        assert len(listed.data) == 1  # dedupe branch
        assert listed.data[0]["suppressed"] is False

        # Cover _append_feature early return when reverse traversal yields None.
        adapter.currentModel = SimpleNamespace(
            FirstFeature=lambda: None,
            FeatureManager=SimpleNamespace(GetFeatureCount=Mock(return_value=1)),
            FeatureByPositionReverse=Mock(return_value=None),
        )
        none_feature = await adapter.list_features(include_suppressed=True)
        assert none_feature.is_success
        assert none_feature.data == []

    @pytest.mark.asyncio
    async def test_set_view_orientation_calls_show_named_view2(
        self, monkeypatch
    ) -> None:
        """_set_view_orientation should call ShowNamedView2 with correct constant."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(ShowNamedView2=Mock())
        adapter._set_view_orientation(target_doc, "front", 1)
        target_doc.ShowNamedView2.assert_called_once_with("", 1)

    @pytest.mark.asyncio
    async def test_set_view_orientation_logs_on_failure(
        self, monkeypatch, caplog
    ) -> None:
        """_set_view_orientation should log warning but not raise on exception."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(
            ShowNamedView2=Mock(side_effect=RuntimeError("COM error"))
        )
        adapter._set_view_orientation(target_doc, "front", 1)
        assert target_doc.ShowNamedView2.called

    @pytest.mark.asyncio
    async def test_zoom_to_fit_calls_view_zoom_to_fit2_success(
        self, monkeypatch
    ) -> None:
        """_zoom_to_fit should call ViewZoomToFit2 and succeed."""
        adapter = self._build_adapter(monkeypatch)
        active_view = SimpleNamespace(ZoomToFit=Mock())
        target_doc = SimpleNamespace(ViewZoomToFit2=Mock(), ActiveView=active_view)
        adapter._zoom_to_fit(target_doc)
        target_doc.ViewZoomToFit2.assert_called_once()
        active_view.ZoomToFit.assert_not_called()

    @pytest.mark.asyncio
    async def test_zoom_to_fit_falls_back_to_active_view_zoom_to_fit(
        self, monkeypatch
    ) -> None:
        """_zoom_to_fit should fall back to ActiveView.ZoomToFit on failure."""
        adapter = self._build_adapter(monkeypatch)
        active_view = SimpleNamespace(ZoomToFit=Mock())
        target_doc = SimpleNamespace(
            ViewZoomToFit2=Mock(side_effect=RuntimeError("COM error")),
            ActiveView=active_view,
        )
        adapter._zoom_to_fit(target_doc)
        assert target_doc.ViewZoomToFit2.called
        assert active_view.ZoomToFit.called

    @pytest.mark.asyncio
    async def test_zoom_to_fit_both_fail_logs_warning(
        self, monkeypatch, caplog
    ) -> None:
        """_zoom_to_fit should log warning if both zoom methods fail."""
        adapter = self._build_adapter(monkeypatch)
        active_view = SimpleNamespace(
            ZoomToFit=Mock(side_effect=RuntimeError("COM error"))
        )
        target_doc = SimpleNamespace(
            ViewZoomToFit2=Mock(side_effect=RuntimeError("COM error")),
            ActiveView=active_view,
        )
        adapter._zoom_to_fit(target_doc)
        assert target_doc.ViewZoomToFit2.called
        assert active_view.ZoomToFit.called

    @pytest.mark.asyncio
    async def test_save_screenshot_with_modelview_success(
        self, monkeypatch, tmp_path
    ) -> None:
        """_save_screenshot_with_modelview should return True when file created."""
        adapter = self._build_adapter(monkeypatch)
        test_file = tmp_path / "test.png"
        test_file.touch()
        model_view = SimpleNamespace(SaveBitmapWithVariableSize=Mock(return_value=True))
        result = adapter._save_screenshot_with_modelview(
            model_view, str(test_file), 1280, 720
        )
        assert result is True
        model_view.SaveBitmapWithVariableSize.assert_called_once_with(
            str(test_file), 1280, 720
        )

    @pytest.mark.asyncio
    async def test_save_screenshot_with_modelview_file_not_exists(
        self, monkeypatch
    ) -> None:
        """_save_screenshot_with_modelview should return False if file doesn't exist."""
        adapter = self._build_adapter(monkeypatch)
        model_view = SimpleNamespace(SaveBitmapWithVariableSize=Mock(return_value=True))
        result = adapter._save_screenshot_with_modelview(
            model_view, "/nonexistent/file.png", 1280, 720
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_save_screenshot_with_modelview_exception(self, monkeypatch) -> None:
        """_save_screenshot_with_modelview should return False on exception."""
        adapter = self._build_adapter(monkeypatch)
        model_view = SimpleNamespace(
            SaveBitmapWithVariableSize=Mock(side_effect=RuntimeError("COM error"))
        )
        result = adapter._save_screenshot_with_modelview(
            model_view, "/path/file.png", 1280, 720
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_save_screenshot_with_targetdoc_success(
        self, monkeypatch, tmp_path
    ) -> None:
        """_save_screenshot_with_targetdoc should return True when file created."""
        adapter = self._build_adapter(monkeypatch)
        test_file = tmp_path / "test.png"
        test_file.touch()
        target_doc = SimpleNamespace(SaveBitmapWithVariableSize=Mock(return_value=True))
        result = adapter._save_screenshot_with_targetdoc(
            target_doc, str(test_file), 1280, 720
        )
        assert result is True
        target_doc.SaveBitmapWithVariableSize.assert_called_once_with(
            str(test_file), 1280, 720
        )

    @pytest.mark.asyncio
    async def test_save_screenshot_with_targetdoc_file_not_exists(
        self, monkeypatch
    ) -> None:
        """_save_screenshot_with_targetdoc should return False if file doesn't exist."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(SaveBitmapWithVariableSize=Mock(return_value=True))
        result = adapter._save_screenshot_with_targetdoc(
            target_doc, "/nonexistent/file.png", 1280, 720
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_save_screenshot_with_targetdoc_exception(self, monkeypatch) -> None:
        """_save_screenshot_with_targetdoc should return False on exception."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(
            SaveBitmapWithVariableSize=Mock(side_effect=RuntimeError("COM error"))
        )
        result = adapter._save_screenshot_with_targetdoc(
            target_doc, "/path/file.png", 1280, 720
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_save_screenshot_with_saveas3_success(
        self, monkeypatch, tmp_path
    ) -> None:
        """_save_screenshot_with_saveas3 should return silently when file created."""
        adapter = self._build_adapter(monkeypatch)
        test_file = tmp_path / "test.png"
        test_file.touch()
        target_doc = SimpleNamespace(SaveAs3=Mock())
        adapter._save_screenshot_with_saveas3(target_doc, str(test_file))
        target_doc.SaveAs3.assert_called_once_with(str(test_file), 0, 2)

    @pytest.mark.asyncio
    async def test_save_screenshot_with_saveas3_file_not_created(
        self, monkeypatch
    ) -> None:
        """_save_screenshot_with_saveas3 should raise if SaveAs3 doesn't create file."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(SaveAs3=Mock())
        with pytest.raises(RuntimeError) as exc_info:
            adapter._save_screenshot_with_saveas3(target_doc, "/nonexistent/file.png")
        assert "All screenshot methods failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_save_screenshot_with_saveas3_raises(self, monkeypatch) -> None:
        """_save_screenshot_with_saveas3 should raise if SaveAs3 raises."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(
            SaveAs3=Mock(side_effect=RuntimeError("COM SaveAs3 error"))
        )
        with pytest.raises(RuntimeError) as exc_info:
            adapter._save_screenshot_with_saveas3(target_doc, "/path/file.png")
        assert "All screenshot methods failed" in str(exc_info.value)

    # ── select_feature helpers ──────────────────────────────────────────────

    def test_normalize_feature_name_strips_quotes_and_casefolds(
        self, monkeypatch
    ) -> None:
        """_normalize_feature_name strips whitespace, quotes, and casefolds."""
        adapter = self._build_adapter(monkeypatch)
        assert adapter._normalize_feature_name('"Boss-Extrude"') == "boss-extrude"
        assert adapter._normalize_feature_name(None) == ""
        assert adapter._normalize_feature_name("  SKETCH1  ") == "sketch1"

    def test_build_feature_candidate_names_with_extension(self, monkeypatch) -> None:
        """_build_feature_candidate_names returns bare + @stem + @title when GetTitle has extension."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(GetTitle=Mock(return_value="MyPart.SLDPRT"))
        result = adapter._build_feature_candidate_names("Boss", target_doc)
        assert result == ["Boss", "Boss@MyPart", "Boss@MyPart.SLDPRT"]

    def test_build_feature_candidate_names_no_extension(self, monkeypatch) -> None:
        """_build_feature_candidate_names omits duplicate when title == stem."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(GetTitle=Mock(return_value="MyPart"))
        result = adapter._build_feature_candidate_names("Boss", target_doc)
        assert result == ["Boss", "Boss@MyPart"]

    def test_build_feature_candidate_names_get_title_raises(self, monkeypatch) -> None:
        """_build_feature_candidate_names falls back to bare name when GetTitle raises."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(GetTitle=Mock(side_effect=RuntimeError("COM")))
        result = adapter._build_feature_candidate_names("Boss", target_doc)
        assert result == ["Boss"]

    def test_try_select_by_extension_returns_on_first_success(
        self, monkeypatch
    ) -> None:
        """_try_select_by_extension returns result dict when SelectByID2 returns True."""
        adapter = self._build_adapter(monkeypatch)
        select_mock = Mock(return_value=True)
        target_doc = SimpleNamespace(Extension=SimpleNamespace(SelectByID2=select_mock))
        result = adapter._try_select_by_extension(target_doc, ["Boss"], "Boss")
        assert result is not None
        assert result["selected"] is True
        assert result["feature_name"] == "Boss"
        assert result["entity_type"] == "BODYFEATURE"  # first entity type tried

    def test_try_select_by_extension_returns_none_when_all_false(
        self, monkeypatch
    ) -> None:
        """_try_select_by_extension returns None when SelectByID2 always returns False."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False))
        )
        result = adapter._try_select_by_extension(target_doc, ["Boss"], "Boss")
        assert result is None

    def test_try_select_by_extension_skips_exceptions_and_continues(
        self, monkeypatch
    ) -> None:
        """_try_select_by_extension swallows individual exceptions and keeps trying."""
        adapter = self._build_adapter(monkeypatch)
        call_count = 0

        def _flaky_select(name, entity_type, *args):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("COM error")
            return True

        target_doc = SimpleNamespace(
            Extension=SimpleNamespace(SelectByID2=_flaky_select)
        )
        result = adapter._try_select_by_extension(target_doc, ["Boss"], "Boss")
        assert result is not None
        assert result["selected"] is True

    def test_try_select_by_component_select4_success(self, monkeypatch) -> None:
        """_try_select_by_component returns result when Select4 succeeds."""
        adapter = self._build_adapter(monkeypatch)
        component = SimpleNamespace(
            Select4=Mock(return_value=True),
            Select=Mock(return_value=False),
            Select2=Mock(return_value=False),
        )
        target_doc = SimpleNamespace(GetComponentByName=Mock(return_value=component))
        result = adapter._try_select_by_component(target_doc, ["Axle"], "Axle")
        assert result is not None
        assert result["selected"] is True
        assert result["entity_type"] == "component:Select4"

    def test_try_select_by_component_falls_back_to_select2(self, monkeypatch) -> None:
        """_try_select_by_component falls back to Select2 when Select4/Select fail."""
        adapter = self._build_adapter(monkeypatch)
        component = SimpleNamespace(
            Select4=Mock(return_value=False),
            Select=Mock(return_value=False),
            Select2=Mock(return_value=True),
        )
        target_doc = SimpleNamespace(GetComponentByName=Mock(return_value=component))
        result = adapter._try_select_by_component(target_doc, ["Axle"], "Axle")
        assert result is not None
        assert result["entity_type"] == "component:Select2"

    def test_try_select_by_component_no_method_returns_none(self, monkeypatch) -> None:
        """_try_select_by_component returns None when doc has no GetComponentByName."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace()  # no GetComponentByName
        result = adapter._try_select_by_component(target_doc, ["Axle"], "Axle")
        assert result is None

    def test_try_select_by_component_component_is_none_returns_none(
        self, monkeypatch
    ) -> None:
        """_try_select_by_component returns None when GetComponentByName returns None."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(GetComponentByName=Mock(return_value=None))
        result = adapter._try_select_by_component(target_doc, ["Axle"], "Axle")
        assert result is None

    def test_try_select_by_feature_tree_finds_matching_name(self, monkeypatch) -> None:
        """_try_select_by_feature_tree returns result when feature.Name matches."""
        adapter = self._build_adapter(monkeypatch)
        feature = SimpleNamespace(
            Name="Boss-Extrude1",
            Select2=Mock(return_value=True),
            GetNextFeature=Mock(return_value=None),
        )
        target_doc = SimpleNamespace(FirstFeature=Mock(return_value=feature))
        result = adapter._try_select_by_feature_tree(
            target_doc, "Boss-Extrude1", ["Boss-Extrude1"]
        )
        assert result is not None
        assert result["selected"] is True
        assert result["entity_type"] == "feature-tree"
        assert result["selected_name"] == "Boss-Extrude1"

    def test_try_select_by_feature_tree_returns_none_when_no_match(
        self, monkeypatch
    ) -> None:
        """_try_select_by_feature_tree returns None when no feature matches."""
        adapter = self._build_adapter(monkeypatch)
        feature = SimpleNamespace(
            Name="OtherFeature",
            Select2=Mock(return_value=True),
            GetNextFeature=Mock(return_value=None),
        )
        target_doc = SimpleNamespace(FirstFeature=Mock(return_value=feature))
        result = adapter._try_select_by_feature_tree(target_doc, "Boss", ["Boss"])
        assert result is None

    def test_try_select_by_feature_tree_first_feature_none(self, monkeypatch) -> None:
        """_try_select_by_feature_tree returns None immediately when FirstFeature is None."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(FirstFeature=Mock(return_value=None))
        result = adapter._try_select_by_feature_tree(target_doc, "Boss", ["Boss"])
        assert result is None

    def test_try_select_by_feature_tree_empty_name_returns_none(
        self, monkeypatch
    ) -> None:
        """_try_select_by_feature_tree should skip empty feature names via _matches(False)."""
        adapter = self._build_adapter(monkeypatch)
        feature = SimpleNamespace(
            Name="",
            Select2=Mock(return_value=False),
            GetNextFeature=Mock(return_value=None),
        )
        target_doc = SimpleNamespace(FirstFeature=Mock(return_value=feature))

        result = adapter._try_select_by_feature_tree(
            target_doc, "TargetFeature", ["TargetFeature"]
        )

        assert result is None

    # ── execute_macro helpers ───────────────────────────────────────────────

    def test_invoke_run_macro2_tuple_success(self, monkeypatch) -> None:
        """_invoke_run_macro2 returns dict when RunMacro2 returns (True, 0) tuple."""
        adapter = self._build_adapter(monkeypatch)
        adapter.swApp = SimpleNamespace(RunMacro2=Mock(return_value=(True, 0)))
        result = adapter._invoke_run_macro2("/macros/test.swp", "TestModule", "main")
        assert result["macro_path"] == "/macros/test.swp"
        assert result["module_name"] == "TestModule"
        assert result["errors"] == 0

    def test_invoke_run_macro2_bool_success(self, monkeypatch) -> None:
        """_invoke_run_macro2 handles scalar True return (non-tuple)."""
        adapter = self._build_adapter(monkeypatch)
        adapter.swApp = SimpleNamespace(RunMacro2=Mock(return_value=True))
        result = adapter._invoke_run_macro2("/macros/test.swp", "TestModule", "main")
        assert result["errors"] == 0

    def test_invoke_run_macro2_tuple_failure_raises(self, monkeypatch) -> None:
        """_invoke_run_macro2 raises SolidWorksMCPError when RunMacro2 returns (False, 5)."""
        adapter = self._build_adapter(monkeypatch)
        adapter.swApp = SimpleNamespace(RunMacro2=Mock(return_value=(False, 5)))
        with pytest.raises(SolidWorksMCPError):
            adapter._invoke_run_macro2("/macros/test.swp", "TestModule", "main")

    def test_invoke_run_macro2_bool_failure_raises(self, monkeypatch) -> None:
        """_invoke_run_macro2 raises SolidWorksMCPError when RunMacro2 returns False."""
        adapter = self._build_adapter(monkeypatch)
        adapter.swApp = SimpleNamespace(RunMacro2=Mock(return_value=False))
        with pytest.raises(SolidWorksMCPError):
            adapter._invoke_run_macro2("/macros/test.swp", "TestModule", "main")

    @pytest.mark.asyncio
    async def test_export_image_returns_error_without_connection(
        self, monkeypatch
    ) -> None:
        """export_image should validate currentModel and swApp preconditions."""
        adapter = self._build_adapter(monkeypatch)

        no_model = await adapter.export_image({"file_path": "C:/tmp/out.png"})
        assert no_model.is_error
        assert no_model.error == "No active model"

        adapter.currentModel = SimpleNamespace()
        adapter.swApp = None
        no_sw = await adapter.export_image({"file_path": "C:/tmp/out.png"})
        assert no_sw.is_error
        assert no_sw.error == "SolidWorks not connected"

    @pytest.mark.asyncio
    async def test_export_image_success_with_orientation(
        self, monkeypatch, tmp_path
    ) -> None:
        """export_image should set orientation and persist an image on success."""
        adapter = self._build_adapter(monkeypatch)
        output_file = tmp_path / "shot.png"

        def _save_bitmap(path, *_args):
            output_file.write_text("image", encoding="utf-8")
            return True

        target_doc = SimpleNamespace(
            SaveBitmapWithVariableSize=Mock(side_effect=_save_bitmap),
            ShowNamedView2=Mock(),
            ViewZoomToFit2=Mock(),
        )
        adapter.currentModel = target_doc
        adapter.swApp = SimpleNamespace(
            ActiveDoc=target_doc,
            Frame=SimpleNamespace(SetFocus=Mock()),
        )

        result = await adapter.export_image(
            {
                "file_path": str(output_file),
                "view_orientation": "front",
                "width": 640,
                "height": 480,
            }
        )

        assert result.is_success
        assert result.data["file_path"] == str(output_file)
        assert result.data["format"] == "PNG"
        assert result.data["dimensions"] == "640x480"
        assert result.data["view"] == "front"
        target_doc.ShowNamedView2.assert_called_once_with("", 1)
        target_doc.ViewZoomToFit2.assert_called_once()

    def test_prepare_stl_export_data_paths(self, monkeypatch) -> None:
        """_prepare_stl_export_data should handle missing app, missing data, and success."""
        adapter = self._build_adapter(monkeypatch)

        adapter.swApp = None
        assert adapter._prepare_stl_export_data() is None

        adapter.swApp = SimpleNamespace(GetExportFileData=Mock(return_value=None))
        assert adapter._prepare_stl_export_data() is None

        stl_data = SimpleNamespace(Merge=False, ExportBodiesAs=1)
        adapter.swApp = SimpleNamespace(GetExportFileData=Mock(return_value=stl_data))
        result = adapter._prepare_stl_export_data()
        assert result is stl_data
        assert stl_data.Merge is True
        assert stl_data.ExportBodiesAs == 0

    def test_save_stl_with_extension_fallback_to_no_export_data(
        self, monkeypatch, tmp_path
    ) -> None:
        """_save_stl_with_extension should retry SaveAs2 with None export data."""
        adapter = self._build_adapter(monkeypatch)
        out_file = tmp_path / "out.stl"

        call_counter = {"count": 0}

        def _saveas2(path, *_args):
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise RuntimeError("type mismatch")
            out_file.write_text("solid", encoding="utf-8")

        extension = SimpleNamespace(SaveAs2=Mock(side_effect=_saveas2))
        saved = adapter._save_stl_with_extension(extension, object(), str(out_file))

        assert saved is True
        assert extension.SaveAs2.call_count == 2

    def test_save_stl_with_extension_returns_false_on_failure(
        self, monkeypatch
    ) -> None:
        """_save_stl_with_extension should return False when SaveAs2 keeps failing."""
        adapter = self._build_adapter(monkeypatch)
        extension = SimpleNamespace(SaveAs2=Mock(side_effect=RuntimeError("fail")))

        saved = adapter._save_stl_with_extension(
            extension, object(), "C:/tmp/missing.stl"
        )

        assert saved is False

    def test_save_stl_with_fallback_raises_when_file_not_created(
        self, monkeypatch
    ) -> None:
        """_save_stl_with_fallback should raise when SaveAs3 does not create output."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(SaveAs3=Mock(return_value=False))

        with pytest.raises(Exception, match="STL export failed"):
            adapter._save_stl_with_fallback(target_doc, "C:/tmp/missing.stl")

    @pytest.mark.asyncio
    async def test_select_feature_uses_extension_strategy(self, monkeypatch) -> None:
        """select_feature should return selected result when SelectByID2 succeeds."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            GetTitle=Mock(return_value="MyPart.SLDPRT"),
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=True)),
            FirstFeature=Mock(return_value=None),
        )

        result = await adapter.select_feature("Boss-Extrude1")

        assert result.is_success
        assert result.data["selected"] is True
        assert result.data["feature_name"] == "Boss-Extrude1"
        assert result.data["entity_type"] == "BODYFEATURE"

    @pytest.mark.asyncio
    async def test_select_feature_returns_unselected_when_all_strategies_fail(
        self, monkeypatch
    ) -> None:
        """select_feature should return selected=False when no strategy finds a match."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            GetTitle=Mock(return_value="MyPart.SLDPRT"),
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False)),
            GetComponentByName=Mock(return_value=None),
            FirstFeature=Mock(return_value=None),
        )

        result = await adapter.select_feature("MissingFeature")

        assert result.is_success
        assert result.data == {
            "selected": False,
            "feature_name": "MissingFeature",
            "selected_name": "MissingFeature",
        }

    @pytest.mark.asyncio
    async def test_execute_macro_returns_error_without_macro_path(
        self, monkeypatch
    ) -> None:
        """execute_macro should error when neither macro_path nor macro_file is provided."""
        adapter = self._build_adapter(monkeypatch)
        result = await adapter.execute_macro({})
        assert result.is_error
        assert result.error == "No macro_path provided"

    @pytest.mark.asyncio
    async def test_execute_macro_returns_error_when_file_missing(
        self, monkeypatch
    ) -> None:
        """execute_macro should error when macro file path does not exist."""
        adapter = self._build_adapter(monkeypatch)
        result = await adapter.execute_macro({"macro_path": "C:/missing/not_found.swp"})
        assert result.is_error
        assert "Macro file not found" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_macro_success_with_macro_file_alias(
        self, monkeypatch, tmp_path
    ) -> None:
        """execute_macro should accept macro_file alias and parse module name from file."""
        adapter = self._build_adapter(monkeypatch)
        adapter.swApp = SimpleNamespace(RunMacro2=Mock(return_value=(True, 0)))

        macro_file = tmp_path / "my_macro.swp"
        macro_file.write_text('Attribute VB_Name = "MyMacroModule"\n', encoding="utf-8")

        result = await adapter.execute_macro(
            {
                "macro_file": str(macro_file),
                "proc_name": "main",
            }
        )

        assert result.is_success
        assert result.data["macro_path"] == str(macro_file)
        assert result.data["module_name"] == "MyMacroModule"
        assert result.data["errors"] == 0
        adapter.swApp.RunMacro2.assert_called_once_with(
            str(macro_file), "MyMacroModule", "main", 0, 0
        )

    @pytest.mark.asyncio
    async def test_execute_macro_uses_explicit_manifest_module_name(
        self, monkeypatch, tmp_path
    ) -> None:
        """A trusted macro manifest can provide its module without parsing the .swp file."""
        adapter = self._build_adapter(monkeypatch)
        adapter.swApp = SimpleNamespace(RunMacro2=Mock(return_value=(True, 0)))
        macro_file = tmp_path / "trusted.swp"
        macro_file.write_bytes(b"binary VBA project")

        result = await adapter.execute_macro(
            {
                "macro_path": str(macro_file),
                "module_name": "ProvisionedModule",
                "proc_name": "Main",
            }
        )

        assert result.is_success
        adapter.swApp.RunMacro2.assert_called_once_with(
            str(macro_file), "ProvisionedModule", "Main", 0, 0
        )

    @pytest.mark.asyncio
    async def test_export_file_stl_errors_when_extension_missing(
        self, monkeypatch, tmp_path
    ) -> None:
        """export_file should fail STL export when target document has no Extension."""
        adapter = self._build_adapter(monkeypatch)
        out_file = tmp_path / "missing_ext.stl"

        target_doc = SimpleNamespace(
            ResolveAllLightweightComponents=Mock(),
            Extension=None,
        )
        adapter.currentModel = target_doc
        adapter.swApp = SimpleNamespace(ActiveDoc=target_doc)

        result = await adapter.export_file(str(out_file), "stl")
        assert result.is_error
        assert "No Extension object available for STL export" in (result.error or "")

    @pytest.mark.asyncio
    async def test_export_file_stl_success_via_extension_path(
        self, monkeypatch, tmp_path
    ) -> None:
        """export_file should complete STL export when SaveAs2 path succeeds."""
        adapter = self._build_adapter(monkeypatch)
        out_file = tmp_path / "ok_ext.stl"

        extension = SimpleNamespace(SaveAs2=Mock())
        target_doc = SimpleNamespace(
            ResolveAllLightweightComponents=Mock(),
            Extension=extension,
            SaveAs3=Mock(),
        )
        adapter.currentModel = target_doc
        adapter.swApp = SimpleNamespace(ActiveDoc=target_doc)

        monkeypatch.setattr(
            adapter,
            "_prepare_stl_export_data",
            Mock(return_value=SimpleNamespace(Merge=True, ExportBodiesAs=0)),
        )
        monkeypatch.setattr(
            adapter, "_save_stl_with_extension", Mock(return_value=True)
        )
        monkeypatch.setattr(adapter, "_save_stl_with_fallback", Mock())

        result = await adapter.export_file(str(out_file), "stl")

        assert result.is_success
        target_doc.ResolveAllLightweightComponents.assert_called_once_with(True)
        adapter._save_stl_with_extension.assert_called_once()
        adapter._save_stl_with_fallback.assert_not_called()

    @pytest.mark.asyncio
    async def test_export_file_stl_falls_back_to_saveas3(
        self, monkeypatch, tmp_path
    ) -> None:
        """export_file should use _save_stl_with_fallback when SaveAs2 path fails."""
        adapter = self._build_adapter(monkeypatch)
        out_file = tmp_path / "fallback_ok.stl"

        extension = SimpleNamespace(SaveAs2=Mock())
        target_doc = SimpleNamespace(
            ResolveAllLightweightComponents=Mock(),
            Extension=extension,
            SaveAs3=Mock(),
        )
        adapter.currentModel = target_doc
        adapter.swApp = SimpleNamespace(ActiveDoc=target_doc)

        monkeypatch.setattr(
            adapter,
            "_prepare_stl_export_data",
            Mock(return_value=SimpleNamespace(Merge=True, ExportBodiesAs=0)),
        )
        monkeypatch.setattr(
            adapter, "_save_stl_with_extension", Mock(return_value=False)
        )
        monkeypatch.setattr(adapter, "_save_stl_with_fallback", Mock(return_value=None))

        result = await adapter.export_file(str(out_file), "stl")

        assert result.is_success
        adapter._save_stl_with_extension.assert_called_once()
        adapter._save_stl_with_fallback.assert_called_once_with(
            target_doc, str(out_file)
        )

    @pytest.mark.asyncio
    async def test_export_file_non_stl_accepts_false_when_file_written(
        self, monkeypatch, tmp_path
    ) -> None:
        """export_file should succeed if SaveAs3 is False but output file exists."""
        adapter = self._build_adapter(monkeypatch)
        out_file = tmp_path / "out.step"

        def _saveas3(path, *_args):
            out_file.write_text("ok", encoding="utf-8")
            return False

        target_doc = SimpleNamespace(SaveAs3=Mock(side_effect=_saveas3))
        adapter.currentModel = target_doc
        adapter.swApp = SimpleNamespace(ActiveDoc=target_doc)

        result = await adapter.export_file(str(out_file), "step")

        assert result.is_success
        target_doc.SaveAs3.assert_called_once()

    def test_save_stl_with_fallback_succeeds_when_file_exists(
        self, monkeypatch, tmp_path
    ) -> None:
        """_save_stl_with_fallback should return cleanly when output exists after SaveAs3."""
        adapter = self._build_adapter(monkeypatch)
        out_file = tmp_path / "fallback_success.stl"

        def _saveas3(path, *_args):
            out_file.write_text("solid", encoding="utf-8")
            return True

        target_doc = SimpleNamespace(SaveAs3=Mock(side_effect=_saveas3))

        adapter._save_stl_with_fallback(target_doc, str(out_file))
        target_doc.SaveAs3.assert_called_once_with(str(out_file), 0, 2)

    def test_save_stl_with_fallback_swallows_saveas3_error_if_file_exists(
        self, monkeypatch, tmp_path
    ) -> None:
        """_save_stl_with_fallback should not raise when SaveAs3 errors but file is present."""
        adapter = self._build_adapter(monkeypatch)
        out_file = tmp_path / "already_exists.stl"
        out_file.write_text("solid", encoding="utf-8")
        target_doc = SimpleNamespace(SaveAs3=Mock(side_effect=RuntimeError("COM fail")))

        adapter._save_stl_with_fallback(target_doc, str(out_file))
        target_doc.SaveAs3.assert_called_once_with(str(out_file), 0, 2)

    @pytest.mark.asyncio
    async def test_export_file_returns_error_without_active_model(
        self, monkeypatch
    ) -> None:
        """export_file should return a guard error when currentModel is missing."""
        adapter = self._build_adapter(monkeypatch)

        result = await adapter.export_file("C:/tmp/out.step", "step")

        assert result.is_error
        assert result.error == "No active model"

    @pytest.mark.asyncio
    async def test_dimension_methods_return_error_without_active_model(
        self, monkeypatch
    ) -> None:
        """get_dimension/set_dimension should guard when there is no current model."""
        adapter = self._build_adapter(monkeypatch)

        get_result = await adapter.get_dimension("D1@Sketch1")
        set_result = await adapter.set_dimension("D1@Sketch1", 12.5)

        assert get_result.is_error
        assert get_result.error == "No active model"
        assert set_result.is_error
        assert set_result.error == "No active model"

    @pytest.mark.asyncio
    async def test_save_file_raises_when_save3_none_and_save_missing(
        self, monkeypatch
    ) -> None:
        """save_file should error when Save3 returns None and no Save fallback exists."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            Save3=Mock(return_value=None),
            GetPathName=Mock(return_value=""),
        )

        result = await adapter.save_file()

        assert result.is_error
        assert "Failed to save file" in (result.error or "")

    @pytest.mark.asyncio
    async def test_model_info_and_list_features_return_error_without_model(
        self, monkeypatch
    ) -> None:
        """get_model_info/list_features should return guard errors when model is absent."""
        adapter = self._build_adapter(monkeypatch)

        info_result = await adapter.get_model_info()
        list_result = await adapter.list_features()

        assert info_result.is_error
        assert info_result.error == "No active model"
        assert list_result.is_error
        assert list_result.error == "No active model"

    @pytest.mark.asyncio
    async def test_list_features_handles_none_feature_in_reverse_fallback(
        self, monkeypatch
    ) -> None:
        """list_features should ignore None entries returned by reverse position traversal."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FirstFeature=Mock(return_value=None),
            FeatureManager=SimpleNamespace(GetFeatureCount=Mock(return_value=1)),
            FeatureByPositionReverse=Mock(return_value=None),
        )

        result = await adapter.list_features(include_suppressed=True)

        assert result.is_success
        assert result.data == []

    def test_try_select_by_component_non_callable_and_selector_exception(
        self, monkeypatch
    ) -> None:
        """_try_select_by_component should skip non-callable selectors and swallow selector errors."""
        adapter = self._build_adapter(monkeypatch)
        component = SimpleNamespace(
            Select4="not-callable",
            Select=Mock(side_effect=RuntimeError("COM select fail")),
            Select2=Mock(return_value=False),
        )
        target_doc = SimpleNamespace(GetComponentByName=Mock(return_value=component))

        result = adapter._try_select_by_component(target_doc, ["Axle"], "Axle")

        assert result is None

    def test_try_select_by_feature_tree_empty_name_select2_exception_and_next(
        self, monkeypatch
    ) -> None:
        """_try_select_by_feature_tree should handle empty names, Select2 exceptions, and continue traversal."""
        adapter = self._build_adapter(monkeypatch)

        second = SimpleNamespace(
            Name="TargetFeature",
            Select2=Mock(return_value=True),
            GetNextFeature=Mock(return_value=None),
        )
        first = SimpleNamespace(
            Name="TargetFeature",
            Select2=Mock(side_effect=RuntimeError("select fail")),
            GetNextFeature=Mock(return_value=second),
        )
        target_doc = SimpleNamespace(FirstFeature=Mock(return_value=first))

        result = adapter._try_select_by_feature_tree(
            target_doc, "TargetFeature", ["TargetFeature"]
        )

        assert result is not None
        assert result["selected"] is True
        assert result["selected_name"] == "TargetFeature"

    @pytest.mark.asyncio
    async def test_select_feature_returns_component_strategy_result(
        self, monkeypatch
    ) -> None:
        """select_feature should return when component strategy succeeds."""
        adapter = self._build_adapter(monkeypatch)
        target_doc = SimpleNamespace(
            GetTitle=Mock(return_value="Asm1.SLDASM"),
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False)),
            GetComponentByName=Mock(
                return_value=SimpleNamespace(
                    Select4=Mock(return_value=True),
                    Select=Mock(return_value=False),
                    Select2=Mock(return_value=False),
                )
            ),
            FirstFeature=Mock(return_value=None),
        )
        adapter.currentModel = target_doc

        result = await adapter.select_feature("Bracket-1")

        assert result.is_success
        assert result.data["selected"] is True
        assert result.data["entity_type"] == "component:Select4"

    @pytest.mark.asyncio
    async def test_select_feature_returns_feature_tree_strategy_result(
        self, monkeypatch
    ) -> None:
        """select_feature should return when extension/component fail but feature-tree succeeds."""
        adapter = self._build_adapter(monkeypatch)
        feature = SimpleNamespace(
            Name="Boss-Extrude1",
            Select2=Mock(return_value=True),
            GetNextFeature=Mock(return_value=None),
        )
        target_doc = SimpleNamespace(
            GetTitle=Mock(return_value="Part1.SLDPRT"),
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False)),
            GetComponentByName=Mock(return_value=None),
            FirstFeature=Mock(return_value=feature),
        )
        adapter.currentModel = target_doc

        result = await adapter.select_feature("Boss-Extrude1")

        assert result.is_success
        assert result.data["selected"] is True
        assert result.data["entity_type"] == "feature-tree"

    @pytest.mark.asyncio
    async def test_select_feature_returns_error_without_active_model(
        self, monkeypatch
    ) -> None:
        """select_feature should guard when there is no active model."""
        adapter = self._build_adapter(monkeypatch)

        result = await adapter.select_feature("Boss-Extrude1")

        assert result.is_error
        assert result.error == "No active model"

    @pytest.mark.asyncio
    async def test_connect_and_document_creation_not_connected_paths(
        self, monkeypatch
    ) -> None:
        """Cover connection/document creation branches when COM app is missing."""
        adapter = self._build_adapter(monkeypatch)

        adapter.swApp = None
        assert adapter._resolve_template_path([8, 0], ".prtdot") is None

        adapter.currentModel = SimpleNamespace(GetTitle=Mock(return_value="Part1"))
        close_result = await adapter.close_model()
        assert close_result.is_error
        assert "not connected" in (close_result.error or "").lower()

        monkeypatch.setattr(adapter, "is_connected", lambda: True)
        adapter.swApp = None
        part_result = await adapter.create_part()
        assembly_result = await adapter.create_assembly()
        drawing_result = await adapter.create_drawing()
        assert part_result.is_error
        assert assembly_result.is_error
        assert drawing_result.is_error
        assert "not connected" in (part_result.error or "").lower()
        assert "not connected" in (assembly_result.error or "").lower()
        assert "not connected" in (drawing_result.error or "").lower()

    @pytest.mark.asyncio
    async def test_connect_raises_when_com_app_instance_is_none(
        self, monkeypatch
    ) -> None:
        """connect should raise when COM returns None instead of an app instance."""
        adapter = self._build_adapter(monkeypatch)

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pythoncom",
            SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock()),
            raising=False,
        )
        fake_client = SimpleNamespace(GetActiveObject=Mock(return_value=None))
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.win32com",
            SimpleNamespace(client=fake_client),
            raising=False,
        )
        # Late binding goes through win32com.client.dynamic.Dispatch — must
        # also stub it or the call will hit the real COM (which connects on
        # any box with SolidWorks installed).
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter._dynamic_module",
            SimpleNamespace(Dispatch=Mock()),
            raising=False,
        )

        async def _fast_sleep(_seconds):
            return None

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.asyncio.sleep", _fast_sleep
        )

        with pytest.raises(
            SolidWorksMCPError, match="Start SolidWorks from the desktop shortcut"
        ):
            await adapter.connect()
        assert fake_client.GetActiveObject.call_count == 8
        _dynamic_module = __import__(
            "solidworks_mcp.adapters.pywin32_adapter", fromlist=["_dynamic_module"]
        )._dynamic_module
        _dynamic_module.Dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_extrusion_and_revolve_feature_none_errors(
        self, monkeypatch
    ) -> None:
        """create_extrusion/create_revolve should error when feature manager returns None."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FeatureManager=SimpleNamespace(
                FeatureExtrusion3=Mock(return_value=None),
                FeatureExtrusion2=Mock(return_value=None),
                FeatureRevolve2=Mock(return_value=None),
            )
        )

        extrusion_result = await adapter.create_extrusion(
            SimpleNamespace(
                depth=5.0,
                draft_angle=0.0,
                reverse_direction=False,
                thin_feature=False,
                thin_thickness=None,
                merge_result=True,
            )
        )
        revolve_result = await adapter.create_revolve(
            SimpleNamespace(
                angle=90.0,
                reverse_direction=False,
                both_directions=False,
                thin_feature=False,
                thin_thickness=None,
                merge_result=True,
            )
        )

        assert extrusion_result.is_error
        assert "Failed to create extrusion feature" in (extrusion_result.error or "")
        assert revolve_result.is_error
        assert "Failed to create revolve feature" in (revolve_result.error or "")

    @pytest.mark.asyncio
    async def test_create_reference_plane_uses_metre_offset_and_clears_selection(
        self, monkeypatch
    ) -> None:
        """Offset plane creation owns its selection lifecycle on all outcomes."""
        adapter = self._build_adapter(monkeypatch)
        clear_selection = Mock(return_value=True)
        base_plane = SimpleNamespace(Select2=Mock(return_value=True))
        inserted_plane = SimpleNamespace(Name="Plane1", GetID=lambda: 12)
        insert_reference_plane = Mock(return_value=inserted_plane)
        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(return_value=base_plane),
            ClearSelection2=clear_selection,
            FeatureManager=SimpleNamespace(InsertRefPlane=insert_reference_plane),
        )

        result = await adapter.create_reference_plane(
            SimpleNamespace(base_plane="Front", offset=25.4, reverse_direction=False)
        )

        assert result.is_success
        assert result.data.name == "Plane1"
        assert base_plane.Select2.call_args.args == (False, 0)
        assert insert_reference_plane.call_args.args == (8, 0.0254, 0, 0.0, 0, 0.0)
        assert clear_selection.call_count == 2

        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(return_value=None),
            ClearSelection2=clear_selection,
        )
        selection_failed = await adapter.create_reference_plane(
            SimpleNamespace(base_plane="Top", offset=10.0, reverse_direction=True)
        )

        assert selection_failed.is_error
        assert "Top Plane" in (selection_failed.error or "")
        assert clear_selection.call_count == 4

        extension_select = Mock(return_value=True)
        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(return_value=None),
            Extension=SimpleNamespace(SelectByID2=extension_select),
            ClearSelection2=clear_selection,
            FeatureManager=SimpleNamespace(
                InsertRefPlane=Mock(return_value=inserted_plane)
            ),
        )
        fallback = await adapter.create_reference_plane(
            SimpleNamespace(base_plane="Right", offset=10.0, reverse_direction=False)
        )

        assert fallback.is_success
        assert extension_select.call_args.args[0:2] == ("Right Plane", "PLANE")
        assert clear_selection.call_count == 6

        reverse_insert = Mock(return_value=inserted_plane)
        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(return_value=base_plane),
            ClearSelection2=clear_selection,
            FeatureManager=SimpleNamespace(InsertRefPlane=reverse_insert),
        )
        reversed_plane = await adapter.create_reference_plane(
            SimpleNamespace(base_plane="Front", offset=10.0, reverse_direction=True)
        )

        assert reversed_plane.is_success
        assert reverse_insert.call_args.args == (264, 0.01, 0, 0.0, 0, 0.0)
        assert clear_selection.call_count == 8

    @pytest.mark.asyncio
    async def test_standard_planes_fall_back_to_feature_tree_for_localized_templates(
        self, monkeypatch
    ) -> None:
        """Standard origin planes remain usable when their display names are localized."""
        adapter = self._build_adapter(monkeypatch)
        clear_selection = Mock(return_value=True)
        front = SimpleNamespace(Name="front-local", GetTypeName2=lambda: "RefPlane")
        top = SimpleNamespace(Name="top-local", GetTypeName2=lambda: "RefPlane")
        right = SimpleNamespace(Name="right-local", GetTypeName2=lambda: "RefPlane")
        front.GetNextFeature = lambda: top
        top.GetNextFeature = lambda: right
        right.GetNextFeature = lambda: None
        for plane in (front, top, right):
            plane.Select2 = Mock(return_value=True)
        created = SimpleNamespace(Name="Plane1", GetID=lambda: 12)
        adapter.currentModel = SimpleNamespace(
            FirstFeature=front,
            FeatureByName=Mock(return_value=None),
            ClearSelection2=clear_selection,
            FeatureManager=SimpleNamespace(InsertRefPlane=Mock(return_value=created)),
        )

        reference_plane = await adapter.create_reference_plane(
            SimpleNamespace(base_plane="Front", offset=10.0, reverse_direction=False)
        )
        resolved_plane = await adapter.get_reference_plane(
            OriginPlaneParameters(plane="Right")
        )

        assert reference_plane.is_success
        assert resolved_plane.is_success
        assert resolved_plane.data.name == "right-local"
        assert front.Select2.call_args == mock_call(False, 0)

    @pytest.mark.asyncio
    async def test_standard_planes_fall_back_to_reverse_position_for_localized_templates(
        self, monkeypatch
    ) -> None:
        """Late-bound COM can resolve localized origin planes by position."""
        adapter = self._build_adapter(monkeypatch)
        origin = SimpleNamespace(GetTypeName2=lambda: "OriginProfileFeature")
        right = SimpleNamespace(Name="right-local", GetTypeName2=lambda: "RefPlane")
        top = SimpleNamespace(Name="top-local", GetTypeName2=lambda: "RefPlane")
        front = SimpleNamespace(Name="front-local", GetTypeName2=lambda: "RefPlane")
        features = (origin, right, top, front)
        adapter.currentModel = SimpleNamespace(
            FirstFeature=None,
            FeatureByName=Mock(return_value=None),
            FeatureByPositionReverse=lambda position: (
                features[position] if position < len(features) else None
            ),
        )

        resolved_front = await adapter.get_reference_plane(
            OriginPlaneParameters(plane="Front")
        )
        resolved_top = await adapter.get_reference_plane(
            OriginPlaneParameters(plane="Top")
        )
        resolved_right = await adapter.get_reference_plane(
            OriginPlaneParameters(plane="Right")
        )

        assert resolved_front.is_success
        assert resolved_top.is_success
        assert resolved_right.is_success
        assert resolved_front.data.name == "front-local"
        assert resolved_top.data.name == "top-local"
        assert resolved_right.data.name == "right-local"

    @pytest.mark.asyncio
    async def test_standard_axis_materializes_helper_for_localized_templates(
        self, monkeypatch
    ) -> None:
        """Localized templates create a re-usable axis from two origin planes."""
        adapter = self._build_adapter(monkeypatch)
        clear_selection = Mock(return_value=True)
        front = SimpleNamespace(Name="front-local", GetTypeName2=lambda: "RefPlane")
        top = SimpleNamespace(Name="top-local", GetTypeName2=lambda: "RefPlane")
        right = SimpleNamespace(Name="right-local", GetTypeName2=lambda: "RefPlane")
        front.GetNextFeature = lambda: top
        top.GetNextFeature = lambda: right
        right.GetNextFeature = lambda: None
        for plane in (front, top, right):
            plane.Select2 = Mock(return_value=True)
        created_axis = SimpleNamespace(
            Name="axis-local", GetID=lambda: 9, GetTypeName2=lambda: "RefAxis"
        )
        insert_axis = Mock(return_value=True)
        adapter.currentModel = SimpleNamespace(
            FirstFeature=front,
            FeatureByName=Mock(return_value=None),
            ClearSelection2=clear_selection,
            FeatureByPositionReverse=lambda position: (
                created_axis if position == 0 else None
            ),
            InsertAxis2=insert_axis,
        )

        result = await adapter.get_reference_axis(ReferenceAxisParameters(axis="Z"))

        assert result.is_success
        assert result.data.name == "MCP_OriginAxis_Z"
        assert result.data.id == "9"
        assert right.Select2.call_args == mock_call(False, 0)
        assert top.Select2.call_args == mock_call(True, 0)
        assert insert_axis.call_args == mock_call(True)
        assert clear_selection.call_count == 2

    @pytest.mark.asyncio
    async def test_typed_reference_and_feature_operations_use_trusted_names(
        self, monkeypatch
    ) -> None:
        """Typed feature operations select only server-resolved native names."""
        adapter = self._build_adapter(monkeypatch)
        clear_selection = Mock(return_value=True)
        feature = SimpleNamespace(Name="Boss-Extrude1", GetID=lambda: 1)
        axis = SimpleNamespace(Name="Z Axis", GetID=lambda: 2)
        plane = SimpleNamespace(Name="Right Plane", GetID=lambda: 3)
        sketch = SimpleNamespace(Name="Sketch2", GetID=lambda: 4)
        for candidate in (feature, axis, plane, sketch):
            candidate.Select2 = Mock(return_value=True)
        created = SimpleNamespace(Name="Pattern1", GetID=lambda: 10)
        feature_manager = SimpleNamespace(
            FeatureLinearPattern4=Mock(return_value=created),
            FeatureCircularPattern5=Mock(return_value=created),
            InsertMirrorFeature2=Mock(return_value=created),
            HoleWizard5=Mock(return_value=created),
        )
        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(
                side_effect=lambda name: {
                    "Boss-Extrude1": feature,
                    "Z Axis": axis,
                    "Right Plane": plane,
                    "Sketch2": sketch,
                }.get(name)
            ),
            ClearSelection2=clear_selection,
            FeatureManager=feature_manager,
        )

        origin_axis = await adapter.get_reference_axis(
            ReferenceAxisParameters(axis="Z")
        )
        origin_plane = await adapter.get_reference_plane(
            OriginPlaneParameters(plane="Right")
        )
        linear = await adapter.create_feature_linear_pattern(
            FeatureLinearPatternParameters(
                feature_name="Boss-Extrude1",
                direction="X",
                instances=3,
                spacing=12.5,
                reverse_direction=True,
            )
        )
        circular = await adapter.create_feature_circular_pattern(
            FeatureCircularPatternParameters(
                feature_name="Boss-Extrude1",
                axis_name="Z Axis",
                instances=4,
                angle_degrees=180,
                reverse_direction=True,
            )
        )
        mirrored = await adapter.mirror_feature(
            FeatureMirrorParameters(
                feature_name="Boss-Extrude1", plane_name="Right Plane"
            )
        )
        hole = await adapter.create_hole_wizard(
            HoleWizardParameters(location_sketch_name="Sketch2", diameter=6, depth=20)
        )
        tapped_hole = await adapter.create_tapped_hole(
            ThreadedHoleParameters(
                location_sketch_name="Sketch2",
                thread_size="M6x1.0",
                depth=20,
                through_all=True,
            )
        )

        assert origin_axis.is_success and origin_axis.data.name == "Z Axis"
        assert origin_plane.is_success and origin_plane.data.name == "Right Plane"
        assert linear.is_success and circular.is_success and mirrored.is_success
        assert hole.is_success and tapped_hole.is_success
        linear_args = feature_manager.FeatureLinearPattern4.call_args.args
        assert len(linear_args) == 20
        assert linear_args == (
            3,
            0.0125,
            1,
            0.0,
            True,
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
        circular_args = feature_manager.FeatureCircularPattern5.call_args.args
        assert len(circular_args) == 14
        assert circular_args == (
            4,
            pytest.approx(math.pi),
            True,
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
        assert feature_manager.InsertMirrorFeature2.call_args.args == (
            False,
            False,
            False,
            False,
            0,
        )
        hole_args = feature_manager.HoleWizard5.call_args_list[0].args
        assert len(hole_args) == 27
        assert hole_args[:8] == (
            2,
            1,
            40,
            "M6",
            0,
            0.006,
            0.02,
            0.0,
        )
        assert hole_args[8:20] == (-1,) * 12
        assert hole_args[20:] == ("", False, True, True, True, True, False)
        tapped_hole_args = feature_manager.HoleWizard5.call_args_list[1].args
        assert len(tapped_hole_args) == 27
        assert tapped_hole_args[:8] == (4, 1, 43, "M6", 33, 0.0, 0.02, 0.0)
        assert tapped_hole_args[8:20] == (-1,) * 12
        assert tapped_hole_args[20:] == ("", False, True, True, True, True, False)
        assert feature.Select2.call_args_list == [
            mock_call(False, 4),
            mock_call(False, 4),
            mock_call(False, 1),
        ]
        assert axis.Select2.call_args == mock_call(True, 1)
        assert plane.Select2.call_args == mock_call(True, 2)
        assert sketch.Select2.call_args == mock_call(False, 0)
        assert clear_selection.call_count == 12

    @pytest.mark.asyncio
    async def test_create_sketch_custom_plane_does_not_fall_back_to_origin_plane(
        self, monkeypatch
    ) -> None:
        """A missing server-resolved plane must not create a sketch on Front/Top/Right."""
        adapter = self._build_adapter(monkeypatch)
        feature_by_name = Mock(return_value=None)
        adapter.currentModel = SimpleNamespace(
            FeatureByName=feature_by_name,
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False)),
            SketchManager=SimpleNamespace(InsertSketch=Mock(return_value=None)),
        )

        result = await adapter.create_sketch("Plane17")

        assert result.is_error
        assert "Plane17" in (result.error or "")
        assert [call.args[0] for call in feature_by_name.call_args_list] == [
            "Plane17",
            "Plane17",
        ]

    @pytest.mark.asyncio
    async def test_create_sketch_clears_selection_when_insert_sketch_fails(
        self, monkeypatch
    ) -> None:
        """A failed sketch insertion cannot contaminate the next COM operation."""
        adapter = self._build_adapter(monkeypatch)
        clear_selection = Mock(return_value=True)
        plane = SimpleNamespace(Select2=Mock(return_value=True))
        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(return_value=plane),
            ClearSelection2=clear_selection,
            SketchManager=SimpleNamespace(
                InsertSketch=Mock(side_effect=RuntimeError("insert failed"))
            ),
        )

        result = await adapter.create_sketch("Plane17")

        assert result.is_error
        assert clear_selection.call_count == 2

    @staticmethod
    def _sketch_tree(*names):
        """Build a FirstFeature→GetNextFeature chain of ProfileFeature mocks.

        Each node mimics the COM ``IFeature`` attributes the feature-tree
        walk in ``features.py`` reads: ``GetTypeName2`` and ``Name`` as
        properties and ``GetNextFeature`` as the link to the next node.
        """
        nxt = None
        for name in reversed(names):
            nxt = SimpleNamespace(
                GetTypeName2="ProfileFeature", Name=name, GetNextFeature=nxt
            )
        return nxt

    @staticmethod
    def _feature_by_name(selected, *, fail=()):
        """Return a FeatureByName mock that records ``Select2(append, mark)``.

        ``features.py`` selects sketches/curves via
        ``currentModel.FeatureByName(name).Select2(append, mark)``.  This
        helper returns a callable that appends ``(name, append, mark)`` to
        ``selected`` and reports success, except for names in ``fail`` (which
        return ``None`` from ``FeatureByName`` to simulate a missing feature).
        """

        def factory(name):
            if name in fail:
                return None
            feature = SimpleNamespace()
            feature.Select2 = lambda append, mark, _n=name: bool(
                selected.append((_n, append, mark)) or True
            )
            return feature

        return factory

    @pytest.mark.asyncio
    async def test_create_sweep_no_model_and_missing_path(self, monkeypatch) -> None:
        """create_sweep guards: no active model, then missing path name."""
        adapter = self._build_adapter(monkeypatch)

        adapter.currentModel = None
        no_model = await adapter.create_sweep(SimpleNamespace(path="Sketch2"))
        assert no_model.is_error
        assert "No active model" in (no_model.error or "")

        adapter.currentModel = SimpleNamespace()
        no_path = await adapter.create_sweep(
            SimpleNamespace(path="", twist_along_path=False, twist_angle=0.0)
        )
        assert no_path.is_error
        assert "path" in (no_path.error or "")

    @pytest.mark.asyncio
    async def test_create_sweep_success_selects_profile_and_path(
        self, monkeypatch
    ) -> None:
        """create_sweep selects an explicit profile (mark 1) and path (mark 4)."""
        adapter = self._build_adapter(monkeypatch)

        selected: list[tuple] = []
        swept = Mock(return_value=SimpleNamespace(Name="Sweep1", GetID=lambda: 7))
        adapter.currentModel = SimpleNamespace(
            FirstFeature=self._sketch_tree("Sketch@Profile", "Path@Guide"),
            FeatureByName=self._feature_by_name(selected),
            ClearSelection2=Mock(return_value=True),
            FeatureManager=SimpleNamespace(InsertProtrusionSwept4=swept),
        )

        result = await adapter.create_sweep(
            SimpleNamespace(
                path="Path@Guide",
                profile="Sketch@Profile",
                twist_along_path=True,
                twist_angle=90.0,
                merge_result=True,
            )
        )

        assert result.is_success, result.error
        assert result.data.type == "Sweep"
        assert result.data.parameters["profile"] == "Sketch@Profile"
        assert result.data.parameters["path"] == "Path@Guide"

        # Profile selected under mark 1 (append False), path under mark 4
        # (append True). Recorded tuples are (name, append, mark).
        assert ("Sketch@Profile", False, 1) in selected
        assert ("Path@Guide", True, 4) in selected

        # TwistCtrlOption (arg index 2) == 8 (constant twist along path) and
        # TwistAngle (arg index 15) == 90 degrees in radians.
        swept_args = swept.call_args.args
        assert swept_args[2] == 8
        assert swept_args[15] == pytest.approx(math.radians(90.0))

    @pytest.mark.asyncio
    async def test_create_sweep_no_profile_distinct_from_path(
        self, monkeypatch
    ) -> None:
        """When the only sketch is the path itself, create_sweep errors."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FirstFeature=self._sketch_tree("Sketch2"),
            FeatureByName=self._feature_by_name([]),
            ClearSelection2=Mock(return_value=True),
            FeatureManager=SimpleNamespace(InsertProtrusionSwept4=Mock()),
        )

        result = await adapter.create_sweep(
            SimpleNamespace(path="Sketch2", twist_along_path=False, twist_angle=0.0)
        )
        assert result.is_error
        assert "profile sketch distinct from the path" in (result.error or "")

    @pytest.mark.asyncio
    async def test_create_sweep_feature_none_errors(self, monkeypatch) -> None:
        """create_sweep errors when InsertProtrusionSwept4 returns None."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FirstFeature=self._sketch_tree("Sketch1", "Sketch2"),
            FeatureByName=self._feature_by_name([]),
            ClearSelection2=Mock(return_value=True),
            FeatureManager=SimpleNamespace(
                InsertProtrusionSwept4=Mock(return_value=None)
            ),
        )

        result = await adapter.create_sweep(
            SimpleNamespace(path="Sketch2", twist_along_path=False, twist_angle=0.0)
        )
        assert result.is_error
        assert "Failed to create sweep feature" in (result.error or "")

    @pytest.mark.asyncio
    async def test_create_sweep_profile_selection_failure(self, monkeypatch) -> None:
        """create_sweep errors with the profile name when Select2 can't find it."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FirstFeature=self._sketch_tree("Sketch1", "Sketch2"),
            # FeatureByName returns None for the inferred profile sketch.
            FeatureByName=self._feature_by_name([], fail=("Sketch1",)),
            ClearSelection2=Mock(return_value=True),
            FeatureManager=SimpleNamespace(InsertProtrusionSwept4=Mock()),
        )

        result = await adapter.create_sweep(
            SimpleNamespace(path="Sketch2", twist_along_path=False, twist_angle=0.0)
        )
        assert result.is_error
        assert "Sketch1" in (result.error or "")
        assert adapter.currentModel.ClearSelection2.call_count == 2

    @pytest.mark.asyncio
    async def test_create_loft_no_model_and_too_few_profiles(self, monkeypatch) -> None:
        """create_loft guards: no active model, then fewer than two profiles."""
        adapter = self._build_adapter(monkeypatch)

        adapter.currentModel = None
        no_model = await adapter.create_loft(SimpleNamespace(profiles=["A", "B"]))
        assert no_model.is_error
        assert "No active model" in (no_model.error or "")

        adapter.currentModel = SimpleNamespace()
        too_few = await adapter.create_loft(SimpleNamespace(profiles=["OnlyOne"]))
        assert too_few.is_error
        assert "at least 2 profile" in (too_few.error or "")

    @pytest.mark.asyncio
    async def test_create_loft_success_selects_profiles_in_order(
        self, monkeypatch
    ) -> None:
        """create_loft selects every profile under mark 1 (first replaces,
        rest append) and guide curves under mark 2."""
        adapter = self._build_adapter(monkeypatch)

        selected: list[tuple] = []
        blend = Mock(return_value=SimpleNamespace(Name="Loft1", GetID=lambda: 9))
        adapter.currentModel = SimpleNamespace(
            FeatureByName=self._feature_by_name(selected),
            ClearSelection2=Mock(return_value=True),
            FeatureManager=SimpleNamespace(InsertProtrusionBlend2=blend),
        )

        result = await adapter.create_loft(
            SimpleNamespace(
                profiles=["Sketch1", "Sketch2"],
                guide_curves=["Sketch3"],
                start_tangent="normal",
                end_tangent=None,
                merge_result=True,
            )
        )

        assert result.is_success, result.error
        assert result.data.type == "Loft"
        assert result.data.parameters["profiles"] == ["Sketch1", "Sketch2"]

        # Recorded tuples are (name, append, mark).
        assert ("Sketch1", False, 1) in selected  # first profile, replace
        assert ("Sketch2", True, 1) in selected  # second profile, append
        assert ("Sketch3", True, 2) in selected  # guide curve, mark 2

        # StartMatchingType (arg 4) == 1 (tangent to normal) from
        # start_tangent="normal"; EndMatchingType (arg 5) == 0 from None.
        blend_args = blend.call_args.args
        assert blend_args[4] == 1
        assert blend_args[5] == 0

    @pytest.mark.asyncio
    async def test_create_loft_feature_none_errors(self, monkeypatch) -> None:
        """create_loft errors when InsertProtrusionBlend2 returns None."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FeatureByName=self._feature_by_name([]),
            ClearSelection2=Mock(return_value=True),
            FeatureManager=SimpleNamespace(
                InsertProtrusionBlend2=Mock(return_value=None)
            ),
        )

        result = await adapter.create_loft(
            SimpleNamespace(
                profiles=["Sketch1", "Sketch2"],
                guide_curves=None,
                start_tangent=None,
                end_tangent=None,
                merge_result=True,
            )
        )
        assert result.is_error
        assert "Failed to create loft feature" in (result.error or "")
        assert adapter.currentModel.ClearSelection2.call_count == 2

    @pytest.mark.asyncio
    async def test_create_loft_profile_selection_failure(self, monkeypatch) -> None:
        """create_loft errors with the profile name when Select2 can't find it."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FeatureByName=self._feature_by_name([], fail=("SketchA",)),
            ClearSelection2=Mock(return_value=True),
            FeatureManager=SimpleNamespace(InsertProtrusionBlend2=Mock()),
        )

        result = await adapter.create_loft(
            SimpleNamespace(
                profiles=["SketchA", "SketchB"],
                guide_curves=None,
                start_tangent=None,
                end_tangent=None,
                merge_result=True,
            )
        )
        assert result.is_error
        assert "SketchA" in (result.error or "")

    @pytest.mark.asyncio
    async def test_create_sketch_empty_plane_and_total_select_failure(
        self, monkeypatch
    ) -> None:
        """create_sketch should skip empty candidates and surface explicit plane selection failure."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            FeatureByName=Mock(return_value=None),
            Extension=SimpleNamespace(SelectByID2=Mock(return_value=False)),
            SketchManager=SimpleNamespace(InsertSketch=Mock(return_value=None)),
            GetActiveSketch2=Mock(return_value="Sketch1"),
        )

        failed = await adapter.create_sketch("")
        assert failed.is_error
        assert "Failed to select plane" in (failed.error or "")

    @pytest.mark.asyncio
    async def test_sketch_and_mass_property_guard_and_fallback_errors(
        self, monkeypatch
    ) -> None:
        """Cover sketch guard returns and mass-properties fallback error branches."""
        adapter = self._build_adapter(monkeypatch)

        adapter.currentModel = None
        assert (await adapter.create_sketch("Top")).is_error
        assert (await adapter.get_mass_properties()).is_error

        adapter.currentSketchManager = None
        assert (await adapter.add_sketch_dimension("L1", None, "linear", 10.0)).is_error
        assert (await adapter.sketch_linear_pattern(["L1"], 1.0, 0.0, 5.0, 3)).is_error
        assert (await adapter.sketch_circular_pattern(["L1"], 180.0, 4)).is_error
        assert (await adapter.sketch_mirror(["L1"], "CL1")).is_error
        assert (await adapter.sketch_offset(["L1"], 1.0, True)).is_error

        adapter.currentModel = SimpleNamespace(
            ForceRebuild3=Mock(return_value=True),
            Extension=SimpleNamespace(CreateMassProperty=Mock(return_value=None)),
            GetMassProperties=[0.0, 0.0, 0.0, 1.0, 2.0, 3.0],
        )
        mass_result = await adapter.get_mass_properties()
        assert mass_result.is_success
        assert mass_result.data is not None
        assert mass_result.data.moments_of_inertia == {
            "Ixx": 0.0,
            "Iyy": 0.0,
            "Izz": 0.0,
            "Ixy": 0.0,
            "Ixz": 0.0,
            "Iyz": 0.0,
        }

        # IMassProperty path with short MOI tuple should zero-fill inertia values.
        adapter.currentModel = SimpleNamespace(
            ForceRebuild3=Mock(return_value=True),
            Extension=SimpleNamespace(
                CreateMassProperty=Mock(
                    return_value=SimpleNamespace(
                        Volume=1.0,
                        SurfaceArea=2.0,
                        Mass=3.0,
                        CenterOfMass=[0.0, 0.0, 0.0],
                        GetMomentOfInertia=Mock(return_value=[1.0]),
                    )
                )
            ),
        )
        short_moi = await adapter.get_mass_properties()
        assert short_moi.is_success
        assert short_moi.data is not None
        assert short_moi.data.moments_of_inertia == {
            "Ixx": 0.0,
            "Iyy": 0.0,
            "Izz": 0.0,
            "Ixy": 0.0,
            "Ixz": 0.0,
            "Iyz": 0.0,
        }

        adapter.currentModel = SimpleNamespace(
            ForceRebuild3=Mock(return_value=True),
            Extension=SimpleNamespace(CreateMassProperty=Mock(return_value=None)),
            GetMassProperties=[0.0, 0.0, 0.0],
        )
        mass_error = await adapter.get_mass_properties()
        assert mass_error.is_error
        assert "Failed to get mass properties" in (mass_error.error or "")

    @pytest.mark.asyncio
    async def test_export_image_and_stl_prep_failure_paths(
        self, monkeypatch, tmp_path
    ) -> None:
        """Cover export_image all-methods-failed and STL export-data exception branch."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace()
        adapter.swApp = SimpleNamespace(
            ActiveDoc=None,
            Frame=SimpleNamespace(SetFocus=Mock()),
        )

        monkeypatch.setattr(
            adapter,
            "_save_screenshot_with_modelview",
            lambda *_args, **_kwargs: False,
        )
        monkeypatch.setattr(
            adapter,
            "_save_screenshot_with_targetdoc",
            lambda *_args, **_kwargs: False,
        )
        monkeypatch.setattr(
            adapter,
            "_save_screenshot_with_saveas3",
            lambda *_args, **_kwargs: None,
        )

        failed = await adapter.export_image(
            {
                "file_path": str(tmp_path / "never-created.png"),
                "orientation": "current",
                "width": 800,
                "height": 600,
            }
        )
        assert failed.is_error
        assert "All screenshot methods produced no output" in (failed.error or "")

        adapter.swApp = SimpleNamespace(
            GetExportFileData=Mock(side_effect=RuntimeError("COM failure"))
        )
        assert adapter._prepare_stl_export_data() is None

    @pytest.mark.asyncio
    async def test_set_dimension_setvalue_failure_branch(self, monkeypatch) -> None:
        """set_dimension should report error when SetValue3 returns False."""
        adapter = self._build_adapter(monkeypatch)
        adapter.currentModel = SimpleNamespace(
            Parameter=Mock(
                return_value=SimpleNamespace(SetValue3=Mock(return_value=False))
            )
        )

        result = await adapter.set_dimension("D1@Sketch1", 10.0)
        assert result.is_error
        assert "Failed to set dimension" in (result.error or "")

    @pytest.mark.asyncio
    async def test_com_coordinator_initialize_skip_and_acquire_failure_paths(
        self, monkeypatch
    ) -> None:
        """Coordinator should skip duplicate init and never COM-start SolidWorks."""
        adapter = self._build_adapter(monkeypatch)

        co_init = Mock()
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pythoncom",
            SimpleNamespace(CoInitialize=co_init, CoUninitialize=Mock()),
            raising=False,
        )
        adapter._com_initialized = True
        adapter._initialize_com_apartment()
        co_init.assert_not_called()

        async def _fast_sleep(_seconds):
            return None

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.asyncio.sleep", _fast_sleep
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.win32com",
            SimpleNamespace(
                client=SimpleNamespace(
                    GetActiveObject=Mock(side_effect=RuntimeError("active fail")),
                )
            ),
            raising=False,
        )
        # Acquire path now routes through win32com.client.dynamic.Dispatch —
        # stub it so the retry loop sees the same RuntimeError surface.
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter._dynamic_module",
            SimpleNamespace(Dispatch=Mock(side_effect=RuntimeError("must not be called"))),
            raising=False,
        )

        with pytest.raises(
            SolidWorksMCPError, match="Start SolidWorks from the desktop shortcut"
        ):
            await adapter._acquire_solidworks_application()
        _dynamic_module = __import__(
            "solidworks_mcp.adapters.pywin32_adapter", fromlist=["_dynamic_module"]
        )._dynamic_module
        _dynamic_module.Dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_com_coordinator_wait_ready_timeout_and_no_revision_branch(
        self, monkeypatch
    ) -> None:
        """wait_for_server_ready should no-op without RevisionNumber and timeout when never ready."""
        adapter = self._build_adapter(monkeypatch)

        await adapter._wait_for_server_ready(SimpleNamespace())

        async def _fast_sleep(_seconds):
            return None

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.asyncio.sleep", _fast_sleep
        )

        never_ready = SimpleNamespace(RevisionNumber=lambda: None)
        with pytest.raises(SolidWorksMCPError, match="did not become ready"):
            await adapter._wait_for_server_ready(never_ready)

    @pytest.mark.parametrize(
        "select4_result,select2_result,select_result,expected",
        [
            (True, False, False, True),
            (False, True, False, True),
            (False, False, True, True),
            (False, False, False, False),
        ],
    )
    def test_sketch_geometry_select_entity_variants(
        self, monkeypatch, select4_result, select2_result, select_result, expected
    ) -> None:
        """select_entity should follow Select4 -> Select2 -> Select fallback order."""
        adapter = self._build_adapter(monkeypatch)
        entity = SimpleNamespace(
            Select4=Mock(return_value=select4_result),
            Select2=Mock(return_value=select2_result),
            Select=Mock(return_value=select_result),
        )
        assert adapter._select_sketch_entity(entity, append=False) is expected

    def test_sketch_geometry_set_display_dimension_value_fallbacks(
        self, monkeypatch
    ) -> None:
        """set_display_dimension_value should use SetSystemValue3, then SetSystemValue2, then SystemValue."""
        adapter = self._build_adapter(monkeypatch)

        dim3 = SimpleNamespace(
            SetSystemValue3=Mock(return_value=True),
            SetSystemValue2=Mock(return_value=True),
        )
        display3 = SimpleNamespace(
            GetDimension2=Mock(return_value=dim3), GetDimension=Mock(return_value=None)
        )
        adapter._set_display_dimension_value(display3, 12.0)
        dim3.SetSystemValue3.assert_called_once()

        dim2 = SimpleNamespace(
            SetSystemValue3=Mock(side_effect=RuntimeError("no v3")),
            SetSystemValue2=Mock(return_value=True),
        )
        display2 = SimpleNamespace(
            GetDimension2=Mock(return_value=dim2), GetDimension=Mock(return_value=None)
        )
        adapter._set_display_dimension_value(display2, 13.0)
        dim2.SetSystemValue2.assert_called_once()

        dim1 = SimpleNamespace(
            SetSystemValue3=Mock(side_effect=RuntimeError("no v3")),
            SetSystemValue2=Mock(side_effect=RuntimeError("no v2")),
            SystemValue=None,
        )
        display1 = SimpleNamespace(
            GetDimension2=Mock(return_value=dim1), GetDimension=Mock(return_value=None)
        )
        adapter._set_display_dimension_value(display1, 14.0)
        assert dim1.SystemValue == pytest.approx(0.014)

    @pytest.mark.parametrize(
        "point_obj,expected",
        [
            (None, None),
            (SimpleNamespace(X=1.0, Y=2.0, Z=3.0), (1.0, 2.0, 3.0)),
            (SimpleNamespace(GetCoords=lambda: [4.0, 5.0, 6.0]), (4.0, 5.0, 6.0)),
            (SimpleNamespace(GetCoords=lambda: [1.0, 2.0]), None),
        ],
    )
    def test_sketch_geometry_point_xyz_variants(
        self, monkeypatch, point_obj, expected
    ) -> None:
        """point_xyz should support direct XYZ, GetCoords fallback, and invalid payloads."""
        adapter = self._build_adapter(monkeypatch)
        assert adapter._sketch_geometry.point_xyz(point_obj) == expected

    @pytest.mark.parametrize(
        "mode,expected", [("setcoords", True), ("setcoords2", True), ("none", False)]
    )
    def test_sketch_geometry_set_point_xyz_variants(
        self, monkeypatch, mode, expected
    ) -> None:
        """set_point_xyz should try SetCoords then SetCoords2 and return False on full failure."""
        adapter = self._build_adapter(monkeypatch)

        if mode == "setcoords":
            point = SimpleNamespace(
                SetCoords=Mock(return_value=True), SetCoords2=Mock(return_value=True)
            )
        elif mode == "setcoords2":
            point = SimpleNamespace(
                SetCoords=Mock(side_effect=RuntimeError("no setcoords")),
                SetCoords2=Mock(return_value=True),
            )
        else:
            point = SimpleNamespace(
                SetCoords=Mock(side_effect=RuntimeError("no setcoords")),
                SetCoords2=Mock(side_effect=RuntimeError("no setcoords2")),
            )

        assert adapter._sketch_geometry.set_point_xyz(point, 1.0, 2.0, 3.0) is expected

    def test_sketch_geometry_segment_vertex_and_placement_paths(
        self, monkeypatch
    ) -> None:
        """Sketch geometry helpers should cover endpoint parsing, shared vertices, and placement edge cases."""
        adapter = self._build_adapter(monkeypatch)
        service = adapter._sketch_geometry

        segment = SimpleNamespace(
            GetStartPoint=(0.0, 0.0, 0.0), GetEndPoint=(10.0, 0.0, 0.0)
        )
        assert service.read_segment_endpoints(segment) == (
            (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0),
        )
        assert service.segment_point_objects(
            SimpleNamespace(GetStartPoint2="p1", GetEndPoint2="p2")
        ) == ("p1", "p2")

        shared_p = SimpleNamespace(X=0.0, Y=0.0, Z=0.0)
        other_p = SimpleNamespace(X=1.0, Y=0.0, Z=0.0)
        e1 = SimpleNamespace(GetStartPoint2=shared_p, GetEndPoint2=other_p)
        e2 = SimpleNamespace(
            GetStartPoint2=shared_p, GetEndPoint2=SimpleNamespace(X=0.0, Y=1.0, Z=0.0)
        )
        shared = service.shared_segment_vertex(e1, e2)
        assert shared is not None

        assert (
            service.smart_dimension_direction(1.0, 0.1)
            == adapter.constants["swSmartDimensionDirectionRight"]
        )
        assert (
            service.smart_dimension_direction(-1.0, 0.0)
            == adapter.constants["swSmartDimensionDirectionLeft"]
        )
        assert (
            service.smart_dimension_direction(0.0, 1.0)
            == adapter.constants["swSmartDimensionDirectionUp"]
        )

        assert (
            service.single_line_dimension_placement(
                SimpleNamespace(
                    GetStartPoint=(0.0, 0.0, 0.0), GetEndPoint=(0.0, 0.0, 0.0)
                )
            )
            is None
        )
        placement = service.single_line_dimension_placement(segment)
        assert placement is not None

        assert (
            service.angular_dimension_placement(
                segment,
                SimpleNamespace(
                    GetStartPoint=(20.0, 0.0, 0.0), GetEndPoint=(30.0, 0.0, 0.0)
                ),
            )
            is None
        )
        angle_ok = service.angular_dimension_placement(
            SimpleNamespace(
                GetStartPoint=(0.0, 0.0, 0.0), GetEndPoint=(10.0, 0.0, 0.0)
            ),
            SimpleNamespace(
                GetStartPoint=(0.0, 0.0, 0.0), GetEndPoint=(0.0, 10.0, 0.0)
            ),
        )
        assert angle_ok is not None

    @pytest.mark.parametrize(
        "current_path,current_title,active_path,active_title,expect_active",
        [
            (None, None, "C:/a.sldprt", "A", False),
            ("C:/a.sldprt", "A", "C:/a.sldprt", "B", True),
            (None, "A", None, "A", True),
            ("C:/a.sldprt", "A", "C:/b.sldprt", "A", False),
        ],
    )
    def test_document_routing_identity_and_resolution_variants(
        self,
        monkeypatch,
        current_path,
        current_title,
        active_path,
        active_title,
        expect_active,
    ) -> None:
        """Document routing should pick active or current model based on identity/path/title policy."""
        adapter = self._build_adapter(monkeypatch)

        def _doc(path, title):
            return SimpleNamespace(
                GetPathName=(lambda: path) if path is not None else (lambda: ""),
                GetTitle=(lambda: title) if title is not None else (lambda: ""),
            )

        current_doc = _doc(current_path, current_title)
        active_doc = _doc(active_path, active_title)
        adapter.currentModel = current_doc
        adapter.swApp = SimpleNamespace(ActiveDoc=active_doc)

        resolved = adapter._resolve_export_target_doc()
        assert (resolved is active_doc) is expect_active

        path_value, title_value = adapter._document_identity(current_doc)
        if current_path:
            assert path_value is not None
        if current_title:
            assert title_value == current_title

    def test_pywin32_wrapper_delegations(self, monkeypatch) -> None:
        """Thin pywin32 wrappers should delegate to coordinator/geometry/routing services."""
        adapter = self._build_adapter(monkeypatch)

        adapter._session_coordinator = SimpleNamespace(
            initialize_com_apartment=Mock(),
            uninitialize_com_apartment=Mock(),
            acquire_solidworks_application=AsyncMock(return_value="app"),
            wait_for_server_ready=AsyncMock(return_value=None),
            set_automation_preferences=Mock(),
        )
        adapter._sketch_geometry = SimpleNamespace(
            select_entity=Mock(return_value=True),
            set_display_dimension_value=Mock(),
        )
        adapter._document_routing = SimpleNamespace(
            document_identity=Mock(return_value=("C:/x.sldprt", "X")),
        )

        adapter._initialize_com_apartment()
        adapter._uninitialize_com_apartment()
        assert adapter._select_sketch_entity(SimpleNamespace(), append=False) is True
        adapter._set_display_dimension_value(SimpleNamespace(), 10.0)
        assert adapter._document_identity(SimpleNamespace()) == ("C:/x.sldprt", "X")


class TestMockAdapterAdditionalCoverage:
    """Target additional edge paths for mock adapter coverage."""

    @pytest.mark.asyncio
    async def test_uncovered_mock_adapter_branches(self):
        """Test uncovered mock adapter branches."""
        adapter = MockSolidWorksAdapter({})

        # Cover bool/callable compatibility shim and direct method path.
        assert bool(adapter.is_connected) is False
        assert MockSolidWorksAdapter.is_connected(adapter) is False

        await adapter.connect()
        opened_asm = await adapter.open_model("C:/Models/a.sldasm")
        opened_drw = await adapter.open_model("C:/Models/a.slddrw")
        unsupported = await adapter.open_model("C:/Models/a.txt")
        assert opened_asm.is_success
        assert opened_drw.is_success
        assert unsupported.is_error

        closed_with_save = await adapter.close_model(save=True)
        assert closed_with_save.is_success

        no_sketch_exit = await adapter.exit_sketch()
        no_mass = await adapter.get_mass_properties()
        assert no_sketch_exit.status == AdapterResultStatus.WARNING
        assert no_mass.is_error

        await adapter.create_part("ExportPart", "mm")
        exported_default_delay = await adapter.export_file("C:/tmp/out.obj", "obj")
        assert exported_default_delay.is_success
        assert exported_default_delay.execution_time == pytest.approx(1.0)


class TestPyWin32AdapterAdditionalCoverage:
    """Target additional edge paths for pywin32 adapter coverage."""

    @staticmethod
    def _build_adapter(monkeypatch) -> PyWin32Adapter:
        """Test helper for build adapter."""
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.PYWIN32_AVAILABLE", True
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.platform.system",
            lambda: "Windows",
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pywintypes",
            SimpleNamespace(com_error=RuntimeError),
            raising=False,
        )
        adapter = PyWin32Adapter({})
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_com = MagicMock()
        mock_com._thread = mock_thread
        mock_com.run = lambda fn, timeout=None: fn()
        adapter._com = mock_com
        return adapter

    def test_platform_guard_raises_on_non_windows(self, monkeypatch):
        """Test platform guard raises on non windows."""
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.PYWIN32_AVAILABLE", True
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.platform.system",
            lambda: "Linux",
        )
        with pytest.raises(SolidWorksMCPError, match="requires Windows"):
            PyWin32Adapter({})

    @pytest.mark.asyncio
    async def test_connect_failure_and_com_error_branch(self, monkeypatch):
        """Test connect failure and com error branch."""
        adapter = self._build_adapter(monkeypatch)

        # connect() delegates to _session_coordinator.connect(), which wraps
        # any exception from initialize_com_apartment as SolidWorksMCPError.
        adapter._session_coordinator.initialize_com_apartment = MagicMock(
            side_effect=RuntimeError("boom")
        )
        with pytest.raises(SolidWorksMCPError, match="Failed to connect"):
            await adapter.connect()
        # Restore so the adapter is usable for the COM-error branch test below.
        adapter._session_coordinator.initialize_com_apartment = MagicMock()

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pywintypes",
            SimpleNamespace(com_error=ValueError),
            raising=False,
        )
        com_result = adapter._handle_com_operation(
            "forced_com_error", lambda: (_ for _ in ()).throw(ValueError("bad com"))
        )
        assert com_result.is_error
        assert "COM error in forced_com_error" in (com_result.error or "")

    @pytest.mark.asyncio
    async def test_open_create_and_feature_failure_paths(self, monkeypatch):
        """Test open create and feature failure paths."""
        adapter = self._build_adapter(monkeypatch)

        fake_app = SimpleNamespace(
            OpenDoc6=Mock(return_value=None),
            NewDocument=Mock(return_value=None),
            GetUserPreferenceStringValue=Mock(
                side_effect=lambda idx: {
                    0: "C:/Templates/Part.prtdot",
                    1: "",
                    2: "",
                }.get(idx, "")
            ),
        )
        adapter.swApp = fake_app

        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pythoncom",
            SimpleNamespace(VT_BYREF=0x4000, VT_I4=3),
            raising=False,
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.win32com",
            SimpleNamespace(client=SimpleNamespace(VARIANT=lambda _kind, val: val)),
            raising=False,
        )

        failed_part_open = await adapter.open_model("C:/tmp/model.sldprt")
        failed_drawing_open = await adapter.open_model("C:/tmp/model.slddrw")
        assert failed_part_open.is_error
        assert failed_drawing_open.is_error

        failed_part_create = await adapter.create_part()
        failed_assembly_create = await adapter.create_assembly()
        failed_drawing_create = await adapter.create_drawing()
        assert failed_part_create.is_error
        assert failed_assembly_create.is_error
        assert failed_drawing_create.is_error

        adapter.swApp = SimpleNamespace(
            RevisionNumber=Mock(side_effect=[RuntimeError("x"), "Unknown"])
        )
        health = await adapter.health_check()
        assert health.healthy is False

        adapter.currentModel = SimpleNamespace(
            FeatureManager=SimpleNamespace(
                FeatureExtrusion2=Mock(return_value=None),
                FeatureExtruThin2=Mock(return_value=None),
                FeatureRevolve2=Mock(return_value=None),
            )
        )
        extrusion_failed = await adapter.create_extrusion(
            SimpleNamespace(
                depth=10.0,
                draft_angle=0.0,
                reverse_direction=False,
                thin_feature=False,
                thin_thickness=None,
            )
        )
        revolve_failed = await adapter.create_revolve(
            SimpleNamespace(
                angle=90.0,
                reverse_direction=False,
                both_directions=False,
                thin_feature=False,
                thin_thickness=None,
            )
        )
        assert extrusion_failed.is_error
        assert revolve_failed.is_error


class TestAdapterCompatibilityFixes:
    """Coverage for adapter compatibility fixes used by real integration tests."""

    @pytest.mark.asyncio
    async def test_pywin32_health_check_with_property_revision_number(
        self, monkeypatch
    ):
        """Test pywin32 health check with property revision number."""
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.PYWIN32_AVAILABLE", True
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.platform.system",
            lambda: "Windows",
        )
        monkeypatch.setattr(
            "solidworks_mcp.adapters.pywin32_adapter.pywintypes",
            SimpleNamespace(com_error=RuntimeError),
            raising=False,
        )

        adapter = PyWin32Adapter({})
        adapter.swApp = SimpleNamespace(RevisionNumber="33.2.1")
        health = await adapter.health_check()

        assert health.healthy is True
        assert health.metrics["sw_version"] == "33.2.1"

    @pytest.mark.asyncio
    async def test_circuit_breaker_create_part_accepts_optional_args(self):
        """Test circuit breaker create part accepts optional args."""
        base = MockSolidWorksAdapter({})
        await base.connect()
        cb = CircuitBreakerAdapter(base)

        result = await cb.create_part("CompatPart", "mm")

        assert result.is_success
        assert result.data.name == "CompatPart"

    @pytest.mark.asyncio
    async def test_circuit_breaker_create_assembly_name_fallback(self):
        """Test circuit breaker create assembly name fallback."""

        class NoArgAssemblyAdapter(MockSolidWorksAdapter):
            """Test suite for NoArgAssemblyAdapter."""

            async def create_assembly(self):  # type: ignore[override]
                """Test helper for create assembly."""
                return await super().create_assembly()

        base = NoArgAssemblyAdapter({})
        await base.connect()
        cb = CircuitBreakerAdapter(base)

        result = await cb.create_assembly("NamedAssembly")

        assert result.is_success

    @pytest.mark.asyncio
    async def test_circuit_breaker_exposes_save_file_passthrough(self):
        """Test circuit breaker exposes save file passthrough."""
        base = MockSolidWorksAdapter({})
        await base.connect()
        await base.create_part("SaveCompat", "mm")
        cb = CircuitBreakerAdapter(base)

        result = await cb.save_file("C:/tmp/save_compat.sldprt")

        assert result.is_success

    @pytest.mark.asyncio
    async def test_mock_adapter_preserves_drawing_views_after_save_as(self, tmp_path):
        """Saving a drawing updates the mock view registry to the new document path."""
        adapter = MockSolidWorksAdapter({})
        await adapter.connect()
        await adapter.create_drawing("FixtureDrawing")
        created = await adapter.create_drawing_view(
            "C:/controlled/fixture.sldprt", "*Front", 0.1, 0.1
        )
        saved = await adapter.save_file(str(tmp_path / "fixture.slddrw"))
        listed = await adapter.list_drawing_views()

        assert created.is_success
        assert saved.is_success
        assert listed.is_success
        assert listed.data == [created.data]

    @pytest.mark.asyncio
    async def test_mock_adapter_aligns_drawing_views_with_outline_spacing(self):
        """Batch alignment updates each listed view in the requested order."""
        adapter = MockSolidWorksAdapter({})
        await adapter.connect()
        await adapter.create_drawing("AlignmentDrawing")
        first = await adapter.create_drawing_view(
            "C:/controlled/source.sldprt", "*Front", 0.1, 0.1
        )
        second = await adapter.create_drawing_view(
            "C:/controlled/source.sldprt", "*Top", 0.2, 0.1
        )

        aligned = await adapter.align_drawing_views(
            [first.data["view_id"], second.data["view_id"]],
            "horizontal",
            0.01,
            0.1,
            0.2,
        )

        assert aligned.is_success
        assert [view["view_id"] for view in aligned.data["views"]] == [
            first.data["view_id"],
            second.data["view_id"],
        ]
        assert aligned.data["views"][0]["position_m"] == pytest.approx(
            {"x": 0.13, "y": 0.2}
        )
        assert aligned.data["views"][1]["position_m"] == pytest.approx(
            {"x": 0.2, "y": 0.2}
        )

    @pytest.mark.asyncio
    async def test_mock_adapter_preserves_custom_properties_after_save_as(
        self, tmp_path
    ):
        """Saving a document keeps mock custom properties bound to its new path."""
        adapter = MockSolidWorksAdapter({})
        await adapter.connect()
        await adapter.create_part("PropertyPart", "mm")
        set_property = await adapter.set_custom_property("PartNumber", "BRKT-001")
        saved = await adapter.save_file(str(tmp_path / "property-part.sldprt"))
        listed = await adapter.list_custom_properties()

        assert set_property.is_success
        assert saved.is_success
        assert listed.is_success
        assert listed.data == [
            {"name": "PartNumber", "value": "BRKT-001", "resolved_value": "BRKT-001"}
        ]

    @pytest.mark.asyncio
    async def test_circuit_breaker_forwards_projection_and_custom_properties(self):
        """New drawing/property APIs retain circuit-breaker protection."""
        base = MockSolidWorksAdapter({})
        await base.connect()
        await base.create_drawing("Fixture")
        created = await base.create_drawing_view(
            "C:/controlled/fixture-part.sldprt", "*Front", 0.1, 0.1
        )
        cb = CircuitBreakerAdapter(base)

        projected = await cb.create_projected_drawing_view(
            created.data["view_id"], 0.15, 0.1
        )
        set_property = await cb.set_custom_property("DrawingNumber", "DRW-001")
        properties = await cb.list_custom_properties()

        assert projected.is_success
        assert set_property.is_success
        assert properties.is_success
        assert properties.data == [
            {"name": "DrawingNumber", "value": "DRW-001", "resolved_value": "DRW-001"}
        ]

    @pytest.mark.asyncio
    async def test_circuit_breaker_forwards_assembly_component_operations(self):
        """Component and mate operations use the same protected adapter path."""
        base = MockSolidWorksAdapter({})
        await base.connect()
        await base.create_assembly("Fixture")
        cb = CircuitBreakerAdapter(base)

        inserted = await cb.insert_assembly_component(
            "C:/controlled/fixture-part.sldprt", 0.01, 0.02, 0.03
        )
        listed = await cb.list_assembly_components()
        entities = await cb.list_assembly_mate_entities(entity_types=("face",))
        mate = await cb.create_assembly_mate(
            entities.data[0]["persistent_reference"],
            entities.data[1]["persistent_reference"],
            mate_type="coincident",
            alignment="aligned",
            flip=False,
            distance_m=0.0,
            angle_radians=0.0,
            for_positioning_only=False,
            lock_rotation=False,
        )

        assert inserted.is_success
        assert listed.is_success
        assert listed.data == [inserted.data]
        assert entities.is_success
        assert mate.is_success
        assert mate.data["mate_type"] == "coincident"

    @pytest.mark.asyncio
    async def test_circuit_breaker_forwards_pack_and_go(self):
        """Pack-and-Go remains available when circuit breaking is enabled."""
        base = Mock()
        base.pack_and_go_assembly = AsyncMock(
            return_value=AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data={"target_dir": "C:/controlled/copy"},
            )
        )
        cb = CircuitBreakerAdapter(base)

        result = await cb.pack_and_go_assembly(
            "C:/controlled/source.sldasm", "C:/controlled/copy", "2x_"
        )

        assert result.is_success
        base.pack_and_go_assembly.assert_awaited_once_with(
            "C:/controlled/source.sldasm", "C:/controlled/copy", "2x_"
        )

    @pytest.mark.asyncio
    async def test_mock_save_file_preserves_assembly_mate_state(self, tmp_path):
        """Save As preserves mock mate ordering for subsequent assembly operations."""
        adapter = MockSolidWorksAdapter({})
        await adapter.connect()
        await adapter.create_assembly("Fixture")
        await adapter.insert_assembly_component(
            "C:/controlled/fixture-part.sldprt", 0.0, 0.0, 0.0
        )
        entities = await adapter.list_assembly_mate_entities(entity_types=("face",))
        first_mate = await adapter.create_assembly_mate(
            entities.data[0]["persistent_reference"],
            entities.data[1]["persistent_reference"],
            mate_type="coincident",
            alignment="aligned",
            flip=False,
            distance_m=0.0,
            angle_radians=0.0,
            for_positioning_only=False,
            lock_rotation=False,
        )
        saved = await adapter.save_file(str(tmp_path / "fixture.sldasm"))
        updated_entities = await adapter.list_assembly_mate_entities(
            entity_types=("face",)
        )
        second_mate = await adapter.create_assembly_mate(
            updated_entities.data[0]["persistent_reference"],
            updated_entities.data[1]["persistent_reference"],
            mate_type="coincident",
            alignment="aligned",
            flip=False,
            distance_m=0.0,
            angle_radians=0.0,
            for_positioning_only=False,
            lock_rotation=False,
        )

        assert first_mate.is_success
        assert saved.is_success
        assert second_mate.data["display_name"] == "Coincident Mate 2"


class TestAdapterIntegration:
    """Integration tests for adapter components."""

    @pytest.mark.asyncio
    async def test_adapter_with_circuit_breaker(self, mock_config):
        """Test adapter with circuit breaker protection."""
        mock_config.circuit_breaker_enabled = True
        adapter = await create_adapter(mock_config)

        await adapter.connect()

        # Normal operations should work
        result = await adapter.open_model("test.sldprt")
        assert result.is_success

    @pytest.mark.asyncio
    async def test_adapter_with_connection_pooling(self, mock_config):
        """Test adapter with connection pooling."""
        mock_config.connection_pooling = True
        mock_config.max_connections = 3

        adapter = await create_adapter(mock_config)
        await adapter.connect()

        # Test that operations work with pooled connections
        result = await adapter.create_part("TestPart")
        assert result.is_success

    @pytest.mark.asyncio
    async def test_adapter_error_handling(self, mock_adapter):
        """Test comprehensive adapter error handling."""
        await mock_adapter.connect()

        # Test operation error — mock_adapter has no RuntimeError wrapping, so
        # the original exception propagates as-is.
        with patch.object(
            mock_adapter, "open_model", side_effect=Exception("Test error")
        ):
            with pytest.raises(Exception, match="Test error"):
                await mock_adapter.open_model("test.sldprt")

        # Adapter should still be connected after error
        assert mock_adapter.is_connected

    @pytest.mark.asyncio
    async def test_adapter_performance_monitoring(self, mock_adapter, perf_monitor):
        """Test adapter operation performance."""
        await mock_adapter.connect()

        perf_monitor.start()
        result = await mock_adapter.create_part("PerfTestPart")
        perf_monitor.stop()

        assert result.is_success
        # Mock operations should be very fast
        perf_monitor.assert_max_time(0.1)
