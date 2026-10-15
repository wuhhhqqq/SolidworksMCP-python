"""OS-level ownership tests; no SolidWorks or COM application is invoked."""

import os
from pathlib import Path
import subprocess
import sys

from solidworks_mcp.adapters.process_lease import ProcessLease


def _child(path: Path, crash: bool = False) -> subprocess.CompletedProcess:
    source = Path(__file__).resolve().parents[3] / "src"
    code = (
        "import sys,os; from pathlib import Path; sys.path.insert(0,sys.argv[2]); "
        "from solidworks_mcp.adapters.process_lease import ProcessLease; "
        "lease=ProcessLease(Path(sys.argv[1])); lease.acquire('child'); "
        + ("os._exit(0)" if crash else "lease.release('child'); print('acquired')")
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(path), str(source)],
        capture_output=True, text=True, timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_second_process_is_rejected_until_all_local_adapters_release(tmp_path):
    path = tmp_path / "automation.lock"
    lease = ProcessLease(path)
    lease.acquire("adapter-a")
    lease.acquire("adapter-a")
    lease.acquire("adapter-b")
    try:
        blocked = _child(path)
        assert blocked.returncode != 0
        assert "SW_AUTOMATION_PROCESS_BUSY" in blocked.stderr
        lease.release("adapter-a")
        assert _child(path).returncode != 0
    finally:
        lease.release("adapter-a")
        lease.release("adapter-b")
    recovered = _child(path)
    assert recovered.returncode == 0, recovered.stderr
    assert recovered.stdout.strip() == "acquired"


def test_crashed_process_does_not_leave_a_stale_owner(tmp_path):
    path = tmp_path / "automation.lock"
    crashed = _child(path, crash=True)
    assert crashed.returncode == 0, crashed.stderr
    lease = ProcessLease(path)
    lease.acquire("next")
    lease.release("next")
    assert path.exists()
