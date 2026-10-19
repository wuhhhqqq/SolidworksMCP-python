"""Keep UI workflow artifacts separate from the user's runtime files."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_ui_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run UI tests with a writable, disposable application data directory."""
    monkeypatch.chdir(tmp_path)
