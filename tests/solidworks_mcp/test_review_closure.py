"""Regression evidence for public execution-layer review findings."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
)
from solidworks_mcp.adapters.sta_adapter import StaAdapter
from solidworks_mcp.config import SecurityLevel, SolidWorksMCPConfig
from solidworks_mcp.security import setup_security
from solidworks_mcp.server import SolidWorksMCPServer


@pytest.mark.asyncio
async def test_tool_lock_covers_activation_and_followup(tmp_path):
    mcp = FastMCP("workflow-lock")
    await setup_security(mcp, SolidWorksMCPConfig(data_dir=tmp_path))
    activated = asyncio.Event()
    release = asyncio.Event()
    events = []

    @mcp.tool
    async def typed_operation() -> str:
        events.append("activate")
        activated.set()
        await release.wait()
        events.append("mutate")
        return "done"

    @mcp.tool
    async def legacy_operation() -> str:
        events.append("legacy")
        return "done"

    first = asyncio.create_task(mcp.call_tool("typed_operation", {}))
    await activated.wait()
    second = asyncio.create_task(mcp.call_tool("legacy_operation", {}))
    await asyncio.sleep(0)
    assert events == ["activate"]
    release.set()
    await asyncio.gather(first, second)
    assert events == ["activate", "mutate", "legacy"]


@pytest.mark.asyncio
async def test_panel_retains_shared_server_and_previous_results():
    from solidworks_mcp.ui.panel_runtime import PanelRuntime

    mcp = FastMCP("retained-results")

    @mcp.tool
    async def probe(value: int) -> dict:
        return {"status": "success", "value": value}

    server = SimpleNamespace(
        mcp=mcp, _setup_complete=True,
        state=SimpleNamespace(is_connected=True),
    )
    runtime = PanelRuntime()
    runtime.attach_server(server)
    first = await runtime.execute("probe", {"value": 1})
    await runtime.execute("probe", {"value": 2})
    old = runtime.operation_status(first["operation_id"])
    assert old["ok"]
    assert old["result"]["value"] == 1
    await runtime.close()
    assert runtime.server is server
    assert await runtime.ensure_ready() is server


def test_panel_history_expires_and_is_bounded(monkeypatch):
    from solidworks_mcp.ui import panel_runtime

    runtime = panel_runtime.PanelRuntime()
    monkeypatch.setattr(panel_runtime.time, "monotonic", lambda: 4000.0)
    runtime._completed = {
        "expired": (0.0, {"ok": True}),
        **{str(i): (3999.0, {"ok": True}) for i in range(205)},
    }
    assert runtime.operation_status("expired")["status"] == "unknown"
    runtime._archive_completed()
    assert "expired" not in runtime._completed
    assert len(runtime._completed) == 200


def test_canonical_config_wins_and_legacy_only_still_works(tmp_path):
    config = SolidWorksMCPConfig(
        data_dir=tmp_path, enable_circuit_breaker=False,
        enable_connection_pooling=True, connection_pool_size=7,
        enable_rate_limiting=False, circuit_breaker_enabled=True,
    )
    assert not config.enable_circuit_breaker
    assert config.enable_connection_pooling
    assert config.connection_pool_size == 7
    assert not config.enable_rate_limiting
    legacy = SolidWorksMCPConfig(data_dir=tmp_path, circuit_breaker_enabled=False)
    assert not legacy.enable_circuit_breaker


@pytest.mark.asyncio
async def test_instrumented_model_reads_are_fresh_and_mutation_runs_once(tmp_path):
    class Adapter:
        name = "A"
        calls = 0

        async def get_model_info(self):
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=self.name)

        async def create_extrusion(self, params):
            self.calls += 1
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data="feature")

    server = SolidWorksMCPServer(SolidWorksMCPConfig(data_dir=tmp_path, mock_solidworks=True))
    adapter = Adapter()
    server.adapter = adapter
    server._configure_runtime_services()
    assert (await adapter.get_model_info()).data == "A"
    adapter.name = "B"
    assert (await adapter.get_model_info()).data == "B"
    assert (await adapter.create_extrusion(ExtrusionParameters(depth=0.01))).is_success
    assert adapter.calls == 1
    assert sum(server._router._analyzer._history["create_extrusion"].values()) == 1


@pytest.mark.asyncio
async def test_security_applies_to_protocol_and_direct_dispatch(tmp_path):
    server = FastMCP("security-regression")
    config = SolidWorksMCPConfig(
        data_dir=tmp_path, security_level=SecurityLevel.STRICT,
        api_keys=["first", "second"], enable_rate_limiting=True, rate_limit_per_minute=2,
    )
    await setup_security(server, config)
    calls = []

    @server.tool
    async def probe(value: int) -> int:
        calls.append(value)
        return value

    with pytest.raises(Exception, match="authentication"):
        await server.call_tool("probe", {"value": 1})
    async with Client(server) as client:
        with pytest.raises(Exception, match="authentication"):
            await client.call_tool("probe", {"value": 2, "api_key": "wrong"})
        await client.call_tool("probe", {"value": 3, "api_key": "second"})
        await client.call_tool("probe", {"value": 4, "api_key": "first"})
        with pytest.raises(Exception, match="rate limit"):
            await client.call_tool("probe", {"value": 5, "api_key": "first", "client_id": "new"})
    assert calls == [3, 4]


@pytest.mark.asyncio
async def test_sta_cancellation_drains_native_work_and_preserves_thread(monkeypatch):
    from solidworks_mcp.adapters import com_executor

    monkeypatch.setattr(com_executor, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(com_executor, "pythoncom", SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    started, release = threading.Event(), threading.Event()
    threads = []

    class Native:
        async def connect(self):
            threads.append(threading.get_ident())

        async def mutate(self):
            threads.append(threading.get_ident())
            started.set()
            release.wait(5)
            return "done"

        async def disconnect(self):
            threads.append(threading.get_ident())

    adapter = StaAdapter(Native())
    await adapter.connect()
    task = asyncio.create_task(adapter.mutate())
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await adapter.disconnect()
    assert len(set(threads)) == 1
    assert threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_sta_disconnect_exception_still_stops_worker(monkeypatch):
    from solidworks_mcp.adapters import com_executor
    monkeypatch.setattr(com_executor, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(com_executor, "pythoncom", SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))

    class Native:
        async def disconnect(self):
            raise RuntimeError("RPC disconnected")

    adapter = StaAdapter(Native())
    with pytest.raises(RuntimeError, match="RPC disconnected"):
        await adapter.disconnect()
    assert adapter._executor._thread is None


@pytest.mark.asyncio
async def test_bearer_header_authentication(tmp_path, monkeypatch):
    server = FastMCP("bearer")
    config = SolidWorksMCPConfig(data_dir=tmp_path, api_keys=["bearer-test"], enable_rate_limiting=False)
    await setup_security(server, config)

    @server.tool()
    async def probe() -> str:
        return "ok"

    monkeypatch.setattr("solidworks_mcp.security.middleware.get_http_headers", lambda **kw: {"authorization": "Bearer bearer-test"})
    result = await server.call_tool("probe", {})
    assert result.content[0].text == "ok"


def test_stop_timeout_does_not_allow_second_sta_worker(monkeypatch):
    from solidworks_mcp.adapters import com_executor
    monkeypatch.setattr(com_executor, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(com_executor, "pythoncom", SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    executor = com_executor.ComExecutor()
    started, release = threading.Event(), threading.Event()
    executor.start()
    def block():
        started.set()
        release.wait(5)
    future = executor.submit(block)
    assert started.wait(2)
    executor.stop(timeout=0.01)
    try:
        with pytest.raises(RuntimeError, match="stopping"):
            executor.start()
        with pytest.raises(RuntimeError, match="not running"):
            executor.submit(lambda: None)
    finally:
        release.set()
        future.result(timeout=2)
        executor.stop()
