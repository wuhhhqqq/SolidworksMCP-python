"""Keep mock COM adapter tests independent of live desktop instances."""

import pytest

from solidworks_mcp.adapters.process_lease import ProcessLease


@pytest.fixture(autouse=True)
def isolated_adapter_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "solidworks_mcp.adapters.pywin32_adapter.AUTOMATION_PROCESS_LEASE",
        ProcessLease(tmp_path / "automation.lock"),
    )
    monkeypatch.setattr(
        "solidworks_mcp.adapters.pywin32_adapter.running_solidworks_process_ids",
        lambda: (),
    )
