"""Contract tests for the controlled trusted-VBA macro workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.application.mvp_workflow import MvpWorkflow
from solidworks_mcp.artifacts.store import ArtifactStore
from solidworks_mcp.artifacts.trusted_macros import TrustedMacroStore
from solidworks_mcp.contracts.mvp import (
    ExecuteTrustedMacroRequest,
    OperationRoute,
    OperationStatus,
    PrepareTrustedMacroRequest,
)


class TrustedMacroAdapter:
    """Minimal adapter that captures the exact macro invocation request."""

    def __init__(self) -> None:
        self.execute_request: dict[str, str] | None = None

    def is_connected(self) -> bool:
        """Report a healthy, connected adapter."""
        return True

    async def health_check(self) -> None:
        """Keep session creation independent of a health payload shape."""
        return None

    async def execute_macro(
        self, params: dict[str, str]
    ) -> AdapterResult[dict[str, int]]:
        """Capture the invocation and emulate a successful RunMacro2 result."""
        self.execute_request = dict(params)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"errors": 0},
        )


def _write_trusted_macro(root: Path, *, macro_id: str = "create_bracket") -> Path:
    """Provision one signed-by-hash macro and its administrator manifest."""
    root.mkdir(parents=True)
    macro_file = root / "create_bracket.swp"
    macro_file.write_bytes(b"SolidWorks VBA project bytes")
    (root / f"{macro_id}.json").write_text(
        json.dumps(
            {
                "macro_id": macro_id,
                "display_name": "Create bracket",
                "description": "Creates a standard bracket in the active document.",
                "file": macro_file.name,
                "module_name": "BracketModule",
                "procedure_name": "Main",
            }
        ),
        encoding="utf-8",
    )
    return macro_file


@pytest.fixture
def trusted_workflow(tmp_path: Path) -> tuple[MvpWorkflow, TrustedMacroAdapter, Path]:
    """Create a workflow with one trusted macro made available by an administrator."""
    macro_root = tmp_path / "trusted_macros"
    macro_file = _write_trusted_macro(macro_root)
    adapter = TrustedMacroAdapter()
    workflow = MvpWorkflow(
        adapter=adapter,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        trusted_macro_store=TrustedMacroStore(macro_root, enabled=True),
    )
    return workflow, adapter, macro_file


@pytest.mark.asyncio
async def test_trusted_macro_requires_prepare_hash_and_one_time_confirmation(
    trusted_workflow: tuple[MvpWorkflow, TrustedMacroAdapter, Path],
) -> None:
    """Only a listed, hash-confirmed macro reaches the adapter's RunMacro2 surface."""
    workflow, adapter, macro_file = trusted_workflow
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]

    listed = await workflow.list_trusted_macros()
    assert listed.status is OperationStatus.SUCCEEDED
    assert listed.data["macros"][0]["macro_id"] == "create_bracket"
    assert "path" not in listed.data["macros"][0]

    prepared = await workflow.prepare_trusted_macro(
        PrepareTrustedMacroRequest(session_id=session_id, macro_id="create_bracket")
    )
    assert prepared.status is OperationStatus.NEEDS_CONFIRMATION
    assert prepared.route is OperationRoute.VBA
    confirmation = prepared.data["confirmation"]
    assert confirmation["macro"]["sha256"]
    assert str(macro_file) not in prepared.model_dump_json()

    executed = await workflow.execute_trusted_macro(
        ExecuteTrustedMacroRequest(
            session_id=session_id,
            confirmation_id=confirmation["confirmation_id"],
            macro_sha256=confirmation["macro"]["sha256"],
        )
    )
    assert executed.status is OperationStatus.SUCCEEDED
    assert executed.route is OperationRoute.VBA
    assert executed.data["macro"]["macro_id"] == "create_bracket"
    assert str(macro_file) not in executed.model_dump_json()
    assert adapter.execute_request == {
        "macro_path": str(macro_file.resolve()),
        "module_name": "BracketModule",
        "proc_name": "Main",
    }

    replay = await workflow.execute_trusted_macro(
        ExecuteTrustedMacroRequest(
            session_id=session_id,
            confirmation_id=confirmation["confirmation_id"],
            macro_sha256=confirmation["macro"]["sha256"],
        )
    )
    assert replay.status is OperationStatus.FAILED
    assert replay.error.code == "SW_MACRO_CONFIRMATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_trusted_macro_refuses_changed_file_after_confirmation(
    trusted_workflow: tuple[MvpWorkflow, TrustedMacroAdapter, Path],
) -> None:
    """The pre-execution integrity check catches a file swap after user confirmation."""
    workflow, adapter, macro_file = trusted_workflow
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    prepared = await workflow.prepare_trusted_macro(
        PrepareTrustedMacroRequest(session_id=session_id, macro_id="create_bracket")
    )
    confirmation = prepared.data["confirmation"]
    macro_file.write_bytes(b"replacement content")

    result = await workflow.execute_trusted_macro(
        ExecuteTrustedMacroRequest(
            session_id=session_id,
            confirmation_id=confirmation["confirmation_id"],
            macro_sha256=confirmation["macro"]["sha256"],
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error.code == "SW_TRUSTED_MACRO_CHANGED"
    assert adapter.execute_request is None


@pytest.mark.asyncio
async def test_trusted_macro_blocks_active_sketch_and_invalidates_all_sessions(
    trusted_workflow: tuple[MvpWorkflow, TrustedMacroAdapter, Path],
) -> None:
    """Global macro execution cannot race sketch edits or leave stale session state."""
    workflow, adapter, _ = trusted_workflow
    macro_session = await workflow.create_session()
    sketch_session = await workflow.create_session()
    macro_session_id = macro_session.data["session"]["session_id"]
    sketch_session_id = sketch_session.data["session"]["session_id"]
    sketch_state = workflow.sessions.require(sketch_session_id)
    sketch_document = workflow.sessions.register_document(
        sketch_state, {"name": "SketchPart", "type": "Part"}
    )
    workflow.sessions.record_active_sketch(
        sketch_state,
        sketch_document.document_id,
        {"id": "Sketch1", "name": "Sketch1"},
    )
    prepared = await workflow.prepare_trusted_macro(
        PrepareTrustedMacroRequest(
            session_id=macro_session_id, macro_id="create_bracket"
        )
    )
    confirmation = prepared.data["confirmation"]

    blocked = await workflow.execute_trusted_macro(
        ExecuteTrustedMacroRequest(
            session_id=macro_session_id,
            confirmation_id=confirmation["confirmation_id"],
            macro_sha256=confirmation["macro"]["sha256"],
        )
    )

    assert blocked.status is OperationStatus.FAILED
    assert blocked.error is not None
    assert blocked.error.code == "SW_MACRO_SKETCH_ACTIVE"
    assert adapter.execute_request is None

    workflow.sessions.invalidate_active_context(sketch_state)
    prepared = await workflow.prepare_trusted_macro(
        PrepareTrustedMacroRequest(
            session_id=macro_session_id, macro_id="create_bracket"
        )
    )
    confirmation = prepared.data["confirmation"]
    executed = await workflow.execute_trusted_macro(
        ExecuteTrustedMacroRequest(
            session_id=macro_session_id,
            confirmation_id=confirmation["confirmation_id"],
            macro_sha256=confirmation["macro"]["sha256"],
        )
    )

    assert executed.status is OperationStatus.SUCCEEDED
    assert workflow.sessions.require(macro_session_id).documents == {}
    assert workflow.sessions.require(sketch_session_id).documents == {}
    assert workflow.sessions.require(sketch_session_id).active_document_id is None


@pytest.mark.asyncio
async def test_disabled_trusted_macros_are_not_discoverable_or_executable(
    tmp_path: Path,
) -> None:
    """Macro execution is opt-in even when macro files have been provisioned."""
    macro_root = tmp_path / "trusted_macros"
    _write_trusted_macro(macro_root)
    workflow = MvpWorkflow(
        adapter=TrustedMacroAdapter(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        trusted_macro_store=TrustedMacroStore(macro_root, enabled=False),
    )
    session = await workflow.create_session()

    listed = await workflow.list_trusted_macros()
    assert listed.status is OperationStatus.SUCCEEDED
    assert listed.data["macros"] == []
    assert listed.warnings

    prepared = await workflow.prepare_trusted_macro(
        PrepareTrustedMacroRequest(
            session_id=session.data["session"]["session_id"],
            macro_id="create_bracket",
        )
    )
    assert prepared.status is OperationStatus.FAILED
    assert prepared.error.code == "SW_TRUSTED_MACROS_DISABLED"


@pytest.mark.asyncio
async def test_expired_macro_confirmations_are_purged_before_new_prepare(
    tmp_path: Path,
) -> None:
    """Repeated expired preparations cannot grow the confirmation map indefinitely."""
    macro_root = tmp_path / "trusted_macros"
    _write_trusted_macro(macro_root)
    workflow = MvpWorkflow(
        adapter=TrustedMacroAdapter(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        trusted_macro_store=TrustedMacroStore(macro_root, enabled=True),
        macro_confirmation_ttl_seconds=0,
    )
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]

    first = await workflow.prepare_trusted_macro(
        PrepareTrustedMacroRequest(session_id=session_id, macro_id="create_bracket")
    )
    second = await workflow.prepare_trusted_macro(
        PrepareTrustedMacroRequest(session_id=session_id, macro_id="create_bracket")
    )

    assert first.status is OperationStatus.NEEDS_CONFIRMATION
    assert second.status is OperationStatus.NEEDS_CONFIRMATION
    assert len(workflow._macro_confirmations) == 1


def test_trusted_macro_store_ignores_unsafe_or_ambiguous_manifests(
    tmp_path: Path,
) -> None:
    """Traversal targets and duplicate IDs never become executable macro choices."""
    macro_root = tmp_path / "trusted_macros"
    _write_trusted_macro(macro_root)
    (macro_root / "unsafe.json").write_text(
        json.dumps(
            {
                "macro_id": "unsafe",
                "display_name": "Unsafe",
                "description": "Attempts to escape the trusted root.",
                "file": "..\\outside.swp",
                "module_name": "UnsafeModule",
                "procedure_name": "Main",
            }
        ),
        encoding="utf-8",
    )
    (macro_root / "duplicate.json").write_text(
        json.dumps(
            {
                "macro_id": "create_bracket",
                "display_name": "Duplicate",
                "description": "Must make the shared macro ID unavailable.",
                "file": "create_bracket.swp",
                "module_name": "OtherModule",
                "procedure_name": "Main",
            }
        ),
        encoding="utf-8",
    )

    store = TrustedMacroStore(macro_root, enabled=True)
    assert store.list() == []
