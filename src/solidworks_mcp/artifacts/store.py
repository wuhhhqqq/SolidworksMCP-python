"""Safe, per-operation storage for SolidWorks input and output artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.mvp import Artifact

_EXPORT_EXTENSIONS = {"step": ".step", "stl": ".stl", "dxf": ".dxf"}
_NATIVE_EXTENSIONS = {
    "part": ".sldprt",
    "assembly": ".sldasm",
    "drawing": ".slddrw",
}
_NEUTRAL_INPUT_EXTENSIONS = frozenset({".step", ".stp", ".iges", ".igs"})
_SUPPORTED_INPUT_EXTENSIONS = frozenset(
    {*_NATIVE_EXTENSIONS.values(), *_NEUTRAL_INPUT_EXTENSIONS}
)
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')


def _validate_windows_leaf_name(file_name: str, *, field_name: str) -> None:
    """Reject a leaf name that Windows treats as an unsafe special file."""
    if any(
        character in _WINDOWS_INVALID_FILENAME_CHARACTERS or ord(character) < 32
        for character in file_name
    ):
        raise ValueError(
            f"{field_name} contains unsupported Windows file-name characters"
        )
    if file_name.endswith((".", " ")):
        raise ValueError(f"{field_name} must not end with a period or space")
    base_name = file_name.split(".", maxsplit=1)[0].rstrip(". ").upper()
    if base_name in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{field_name} must not use a reserved Windows device name")


class ArtifactStore:
    """Create export targets beneath one configured, trusted root directory."""

    def __init__(self, root: Path) -> None:
        """Initialize the trusted artifact root without creating it eagerly."""
        self.root = root.expanduser().resolve()

    def prepare_export_path(
        self,
        *,
        session_id: str,
        operation_id: str,
        artifact_name: str,
        format_type: str,
    ) -> Path:
        """Return an export target that cannot escape the trusted root."""
        extension = _EXPORT_EXTENSIONS[format_type]
        return self._prepare_path(
            session_id=session_id,
            operation_id=operation_id,
            artifact_name=artifact_name,
            extension=extension,
        )

    def prepare_native_save_path(
        self,
        *,
        session_id: str,
        operation_id: str,
        artifact_name: str,
        document_type: str,
    ) -> Path:
        """Return a native SolidWorks save target under the trusted artifact root."""
        extension = _NATIVE_EXTENSIONS.get(document_type)
        if extension is None:
            raise ValueError(
                "native save requires a part, assembly, or drawing document"
            )
        return self._prepare_path(
            session_id=session_id,
            operation_id=operation_id,
            artifact_name=artifact_name,
            extension=extension,
        )

    def _prepare_path(
        self,
        *,
        session_id: str,
        operation_id: str,
        artifact_name: str,
        extension: str,
    ) -> Path:
        """Resolve a caller-provided leaf name to a safe operation output path."""
        candidate = Path(artifact_name)
        if candidate.name != artifact_name or artifact_name in {".", ".."}:
            raise ValueError("artifact_name must be a simple file name")
        _validate_windows_leaf_name(artifact_name, field_name="artifact_name")
        if candidate.suffix and candidate.suffix.lower() != extension:
            raise ValueError(f"artifact_name must use the {extension} extension")

        filename = artifact_name if candidate.suffix else f"{artifact_name}{extension}"
        target = (self.root / session_id / operation_id / filename).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("export target escapes the configured artifact root")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def describe(self, path: Path, *, kind: str) -> Artifact:
        """Build integrity metadata for an export that was written successfully."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("artifact is outside the configured artifact root")
        if not resolved.is_file():
            raise FileNotFoundError(f"export did not create an artifact: {resolved}")

        digest = self._sha256(resolved)
        return Artifact(
            name=resolved.name,
            kind=kind,
            path=str(resolved),
            size_bytes=resolved.stat().st_size,
            sha256=digest,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        """Calculate an artifact digest without loading large CAD files into memory."""
        digest = hashlib.sha256()
        with path.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


class ModelInputStore:
    """Resolve user-supplied model names inside one trusted input directory."""

    def __init__(self, root: Path) -> None:
        """Initialize the trusted model input root without creating files."""
        self.root = root.expanduser().resolve()

    def resolve_model_path(self, file_name: str) -> Path:
        """Resolve a native SolidWorks leaf file beneath the trusted input root."""
        candidate = Path(file_name)
        if candidate.name != file_name or file_name in {".", ".."}:
            raise ValueError("file_name must be a simple file name")
        _validate_windows_leaf_name(file_name, field_name="file_name")
        if candidate.suffix.lower() not in _SUPPORTED_INPUT_EXTENSIONS:
            raise ValueError(
                "file_name must be a native SolidWorks file or a STEP/IGES neutral file"
            )

        input_path = self.root / file_name
        if input_path.is_symlink():
            raise ValueError("model input must not be a symbolic link")
        resolved = input_path.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("model input escapes the configured input root")
        if not resolved.is_file():
            raise FileNotFoundError(f"model input was not found: {file_name}")
        return resolved
