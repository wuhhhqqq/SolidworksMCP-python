"""Safe discovery and integrity checks for administrator-provisioned VBA macros."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts.mvp import TrustedMacroRef

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MACRO_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_MANIFEST_MAX_BYTES = 64 * 1024


class TrustedMacroStoreError(RuntimeError):
    """Base class for controlled macro-store errors."""


class TrustedMacrosDisabledError(TrustedMacroStoreError):
    """Raised when trusted macro execution has not been explicitly enabled."""


class TrustedMacroNotFoundError(TrustedMacroStoreError):
    """Raised when a requested trusted macro cannot be resolved safely."""


class TrustedMacroChangedError(TrustedMacroStoreError):
    """Raised when the macro or its manifest changed after confirmation."""


@dataclass(frozen=True)
class TrustedMacro:
    """Verified internal representation of a trusted VBA project and manifest."""

    macro_id: str
    display_name: str
    description: str
    source_path: Path
    module_name: str
    procedure_name: str
    size_bytes: int
    sha256: str

    def public_ref(self) -> TrustedMacroRef:
        """Return the client-safe macro metadata without exposing a filesystem path."""
        return TrustedMacroRef(
            macro_id=self.macro_id,
            display_name=self.display_name,
            description=self.description,
            module_name=self.module_name,
            procedure_name=self.procedure_name,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
        )


class TrustedMacroStore:
    """Expose only manifest-declared ``.swp`` files from one trusted directory.

    An administrator provisions a VBA project next to a JSON manifest.  The MCP client
    can choose a manifest identifier, but never a Windows path, module name, or procedure.
    """

    def __init__(
        self,
        root: Path,
        *,
        enabled: bool,
        max_macro_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        """Initialize a store rooted at the configured, administrator-controlled directory."""
        self.root = root.expanduser().resolve()
        self.enabled = enabled
        self.max_macro_bytes = max_macro_bytes

    def list(self) -> list[TrustedMacro]:
        """Return valid provisioned macros in deterministic manifest-name order."""
        if not self.enabled or not self.root.is_dir():
            return []

        records: dict[str, TrustedMacro] = {}
        duplicate_ids: set[str] = set()
        for manifest_path in sorted(self.root.glob("*.json")):
            try:
                macro = self._read_manifest(manifest_path)
            except (OSError, TrustedMacroStoreError):
                # A malformed administrative manifest must never become executable.
                continue
            if macro.macro_id in records:
                duplicate_ids.add(macro.macro_id)
            else:
                records[macro.macro_id] = macro
        for macro_id in duplicate_ids:
            records.pop(macro_id, None)
        return list(records.values())

    def get(self, macro_id: str) -> TrustedMacro:
        """Resolve one enabled macro by opaque manifest identifier."""
        if not self.enabled:
            raise TrustedMacrosDisabledError("trusted macro execution is disabled")
        for macro in self.list():
            if macro.macro_id == macro_id:
                return macro
        raise TrustedMacroNotFoundError("trusted macro was not found")

    def verify_unchanged(self, expected: TrustedMacro) -> TrustedMacro:
        """Re-read a macro and reject any manifest, target, or hash change."""
        actual = self.get(expected.macro_id)
        if (
            actual.source_path != expected.source_path
            or actual.module_name != expected.module_name
            or actual.procedure_name != expected.procedure_name
            or actual.sha256 != expected.sha256
            or actual.size_bytes != expected.size_bytes
        ):
            raise TrustedMacroChangedError("trusted macro content or manifest changed")
        return actual

    def _read_manifest(self, manifest_path: Path) -> TrustedMacro:
        """Parse and validate one small JSON manifest without trusting its file path."""
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise TrustedMacroStoreError("manifest must be a regular file")
        resolved_manifest = manifest_path.resolve()
        if not resolved_manifest.is_relative_to(self.root):
            raise TrustedMacroStoreError("manifest is outside the configured root")
        try:
            if resolved_manifest.stat().st_size > _MANIFEST_MAX_BYTES:
                raise TrustedMacroStoreError("manifest is too large")
            raw = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrustedMacroStoreError("manifest is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise TrustedMacroStoreError("manifest must contain an object")

        allowed_keys = {
            "macro_id",
            "display_name",
            "description",
            "file",
            "module_name",
            "procedure_name",
        }
        if set(raw) != allowed_keys:
            raise TrustedMacroStoreError(
                "manifest fields do not match the trusted schema"
            )

        macro_id = self._require_identifier(raw, "macro_id", _MACRO_ID)
        display_name = self._require_text(raw, "display_name", maximum=120)
        description = self._require_text(raw, "description", maximum=500)
        module_name = self._require_identifier(raw, "module_name", _IDENTIFIER)
        procedure_name = self._require_identifier(raw, "procedure_name", _IDENTIFIER)
        macro_file = self._require_text(raw, "file", maximum=120)
        candidate = Path(macro_file)
        if candidate.name != macro_file or candidate.suffix.lower() != ".swp":
            raise TrustedMacroStoreError("manifest macro file must be a .swp leaf name")

        source_candidate = self.root / candidate
        if source_candidate.is_symlink() or not source_candidate.is_file():
            raise TrustedMacroStoreError("macro must be a regular file")
        source_path = source_candidate.resolve()
        if not source_path.is_relative_to(self.root):
            raise TrustedMacroStoreError("macro is outside the configured root")
        try:
            size_bytes, digest = self._hash_file(source_path)
        except OSError as exc:
            raise TrustedMacroStoreError("macro could not be read safely") from exc
        return TrustedMacro(
            macro_id=macro_id,
            display_name=display_name,
            description=description,
            source_path=source_path,
            module_name=module_name,
            procedure_name=procedure_name,
            size_bytes=size_bytes,
            sha256=digest,
        )

    def _hash_file(self, path: Path) -> tuple[int, str]:
        """Hash a macro incrementally and enforce its configured size limit."""
        size_bytes = path.stat().st_size
        if size_bytes < 1 or size_bytes > self.max_macro_bytes:
            raise TrustedMacroStoreError("macro file size is outside the allowed range")
        digest = hashlib.sha256()
        with path.open("rb") as macro_file:
            for chunk in iter(lambda: macro_file.read(64 * 1024), b""):
                digest.update(chunk)
        if path.stat().st_size != size_bytes:
            raise TrustedMacroStoreError("macro changed while its hash was calculated")
        return size_bytes, digest.hexdigest()

    @staticmethod
    def _require_text(raw: dict[str, Any], field: str, *, maximum: int) -> str:
        """Read one bounded non-empty manifest string."""
        value = raw.get(field)
        if not isinstance(value, str):
            raise TrustedMacroStoreError(f"manifest {field} must be text")
        result = value.strip()
        if not result or len(result) > maximum:
            raise TrustedMacroStoreError(f"manifest {field} has an invalid length")
        return result

    @classmethod
    def _require_identifier(
        cls,
        raw: dict[str, Any],
        field: str,
        pattern: re.Pattern[str],
    ) -> str:
        """Read a VBA-safe or macro-ID-safe manifest identifier."""
        value = cls._require_text(raw, field, maximum=120)
        if pattern.fullmatch(value) is None:
            raise TrustedMacroStoreError(f"manifest {field} is not a valid identifier")
        return value
