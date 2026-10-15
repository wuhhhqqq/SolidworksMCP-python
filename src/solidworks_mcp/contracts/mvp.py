"""Typed input and output contracts for the SolidWorks MCP MVP tools."""

from __future__ import annotations

import re
from enum import StrEnum
from math import hypot, isclose, isfinite
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')


def _validate_windows_leaf_name(value: str, *, field_name: str) -> None:
    """Reject names Windows cannot safely represent as ordinary leaf files."""
    if any(
        character in _WINDOWS_INVALID_FILENAME_CHARACTERS or ord(character) < 32
        for character in value
    ):
        raise ValueError(
            f"{field_name} contains unsupported Windows file-name characters"
        )
    if value.endswith((".", " ")):
        raise ValueError(f"{field_name} must not end with a period or space")
    base_name = value.split(".", maxsplit=1)[0].rstrip(". ").upper()
    if base_name in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{field_name} must not use a reserved Windows device name")


def _validate_configuration_name(value: str) -> str:
    """Keep configuration names as plain SolidWorks identifiers."""
    if any(separator in value for separator in ("/", "\\", ":")):
        raise ValueError(
            "configuration_name must be a SolidWorks configuration name, not a path"
        )
    if any(ord(character) < 32 for character in value):
        raise ValueError("configuration_name must not contain control characters")
    return value


def _validate_custom_property_name(value: str) -> str:
    """Keep custom-property keys as identifiers rather than path-like input."""
    if any(separator in value for separator in ("/", "\\", ":")):
        raise ValueError("name must be a custom property name, not a path")
    if any(ord(character) < 32 for character in value):
        raise ValueError("name must not contain control characters")
    return value


def _validate_equation_name(value: str) -> str:
    """Accept a single SolidWorks equation target without assignment syntax."""
    if any(character in value for character in ('"', "=")):
        raise ValueError("name must not contain quotation marks or an equals sign")
    if any(ord(character) < 32 for character in value):
        raise ValueError("name must not contain control characters")
    return value


def _validate_equation_expression(value: str) -> str:
    """Keep one formula expression distinct from the assignment the server builds."""
    if "=" in value:
        raise ValueError("expression must not contain an equals sign")
    if any(ord(character) < 32 for character in value):
        raise ValueError("expression must not contain control characters")
    return value


def _validate_opaque_sketch_entity_id(value: str) -> str:
    """Accept only the internal identifier format returned for sketch entities."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,255}", value):
        raise ValueError("entity IDs must contain server-issued entity IDs")
    return value


def _default_assembly_mate_entity_types() -> list[Literal["face", "edge"]]:
    """Return the complete typed default for assembly mate geometry discovery."""
    return ["face", "edge"]


def _default_part_topology_entity_types() -> list[Literal["face", "edge", "vertex"]]:
    """Return the bounded default used for safe part topology discovery."""
    return ["face", "edge"]


def _default_drawing_annotation_target_types() -> list[
    Literal["dimension", "component", "edge"]
]:
    """Return the complete typed default for drawing annotation discovery."""
    return ["dimension", "component", "edge"]


class StrictContract(BaseModel):
    """Base model for public MCP inputs that rejects misspelled fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OperationStatus(StrEnum):
    """Terminal or confirmation state for an MCP operation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    NEEDS_CONFIRMATION = "needs_confirmation"


class OperationRoute(StrEnum):
    """Execution mechanism reported to MCP clients."""

    COM = "com"
    VBA = "vba"
    VBA_FALLBACK = "vba_fallback"
    NONE = "none"


class OperationError(StrictContract):
    """Actionable, non-sensitive error information for a failed operation."""

    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    suggestion: str = Field(min_length=1, max_length=500)


class Artifact(StrictContract):
    """An output file safely produced by an MCP operation."""

    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=32)
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class TrustedMacroRef(StrictContract):
    """Client-safe metadata for an administrator-provisioned VBA macro project."""

    macro_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    module_name: str = Field(min_length=1, max_length=120)
    procedure_name: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SessionRef(StrictContract):
    """Opaque identifier for one client-owned SolidWorks workflow session."""

    session_id: str = Field(min_length=1)
    connected: bool


class DocumentRef(StrictContract):
    """Stable reference to a document owned by an MCP session."""

    document_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    document_type: Literal["part", "assembly", "drawing", "unknown"]
    path: str | None = None
    configuration: str | None = Field(default=None, max_length=120)
    revision: int = Field(ge=1)


class EntityRef(StrictContract):
    """Stable reference to a sketch, feature, or other SolidWorks entity."""

    document_id: str = Field(min_length=1)
    entity_type: Literal["sketch", "feature", "sketch_entity"]
    persistent_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    revision: int = Field(ge=1)


class FeatureSummary(StrictContract):
    """Client-safe feature-tree entry for one tracked SolidWorks document."""

    feature_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=240)
    feature_type: str = Field(min_length=1, max_length=120)
    suppressed: bool
    position: int = Field(ge=0)


class ConfigurationSummary(StrictContract):
    """Client-safe configuration name and whether it is tracked as active."""

    name: str = Field(min_length=1, max_length=120)
    is_active: bool


class ConfigurationDetailSummary(ConfigurationSummary):
    """One current configuration with its SolidWorks comment text."""

    description: str = Field(default="", max_length=1024)


class AssemblyComponentSummary(StrictContract):
    """Client-safe identity and document type for one assembly component."""

    component_id: str = Field(min_length=1, max_length=240)
    display_name: str = Field(min_length=1, max_length=240)
    document_type: Literal["part", "assembly", "drawing", "unknown"]


class AssemblyMateEntitySummary(StrictContract):
    """Client-safe geometric entity reference eligible for an assembly mate."""

    entity_id: str = Field(min_length=1, max_length=128)
    component_id: str = Field(min_length=1, max_length=240)
    entity_type: Literal["face", "edge"]
    display_name: str = Field(min_length=1, max_length=240)


class AssemblyMateSummary(StrictContract):
    """Client-safe confirmation of one successfully created assembly mate."""

    mate_type: Literal[
        "coincident",
        "concentric",
        "parallel",
        "perpendicular",
        "tangent",
        "distance",
        "angle",
    ]
    display_name: str = Field(min_length=1, max_length=240)


class AssemblyInterferenceSummary(StrictContract):
    """One interference reported by SolidWorks without exposing COM objects."""

    component_names: list[str] = Field(min_length=2, max_length=100)
    volume_mm3: float = Field(ge=0)
    is_possible: bool = False


class PartTopologyEntitySummary(StrictContract):
    """One revision-bound part topology entity and its safe measured geometry."""

    entity_id: str = Field(min_length=1, max_length=128)
    entity_type: Literal["face", "edge", "vertex"]
    display_name: str = Field(min_length=1, max_length=240)
    area_mm2: float | None = Field(default=None, ge=0)
    normal: list[float] | None = Field(default=None, min_length=3, max_length=3)
    length_mm: float | None = Field(default=None, ge=0)
    curve_identity: int | None = None
    point_mm: list[float] | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_geometry(self) -> PartTopologyEntitySummary:
        """Keep geometry dimensional, finite, and specific to its topology type."""
        vectors = (self.normal, self.point_mm)
        if any(
            vector is not None and not all(isfinite(value) for value in vector)
            for vector in vectors
        ):
            raise ValueError("topology vectors must contain finite coordinates")
        if self.area_mm2 is not None and not isfinite(self.area_mm2):
            raise ValueError("area_mm2 must be finite")
        if self.length_mm is not None and not isfinite(self.length_mm):
            raise ValueError("length_mm must be finite")
        if self.entity_type != "face" and (
            self.area_mm2 is not None or self.normal is not None
        ):
            raise ValueError("only faces may include area or normal geometry")
        if self.entity_type != "edge" and (
            self.length_mm is not None or self.curve_identity is not None
        ):
            raise ValueError("only edges may include length or curve geometry")
        if self.entity_type != "vertex" and self.point_mm is not None:
            raise ValueError("only vertices may include point geometry")
        return self


class AssemblyMateListSummary(StrictContract):
    """One revision-bound existing assembly mate available for deletion."""

    mate_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=240)
    mate_type: str = Field(min_length=1, max_length=120)


class DrawingViewSummary(StrictContract):
    """Client-safe identity and display name for one drawing model view."""

    view_id: str = Field(min_length=1, max_length=240)
    display_name: str = Field(min_length=1, max_length=240)


class DrawingSheetSummary(StrictContract):
    """Client-safe name and active-state summary for one drawing sheet."""

    name: str = Field(min_length=1, max_length=240)
    is_active: bool


class DrawingViewOutlineSummary(StrictContract):
    """Axis-aligned drawing-sheet outline for one active model view in millimetres."""

    view_id: str = Field(min_length=1, max_length=240)
    left_mm: float
    bottom_mm: float
    right_mm: float
    top_mm: float

    @model_validator(mode="after")
    def validate_extents(self) -> DrawingViewOutlineSummary:
        """Reject non-finite or inverted extents returned by the adapter."""
        values = (self.left_mm, self.bottom_mm, self.right_mm, self.top_mm)
        if not all(isfinite(value) for value in values):
            raise ValueError("drawing-view outline values must be finite")
        if self.right_mm < self.left_mm or self.top_mm < self.bottom_mm:
            raise ValueError("drawing-view outline extents are inconsistent")
        return self


class CustomPropertySummary(StrictContract):
    """Client-safe value of one document or configuration custom property."""

    name: str = Field(min_length=1, max_length=255)
    value: str = Field(max_length=4096)
    resolved_value: str = Field(max_length=4096)


class SolidBodySummary(StrictContract):
    """Client-safe summary of one solid body in a part document."""

    body_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    visible: bool


class BoundingBoxSummary(StrictContract):
    """Axis-aligned active-part extents in millimetres."""

    min_point_mm: list[float] = Field(min_length=3, max_length=3)
    max_point_mm: list[float] = Field(min_length=3, max_length=3)
    size_mm: list[float] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_extents(self) -> BoundingBoxSummary:
        """Reject non-finite or internally inconsistent adapter geometry."""
        values = self.min_point_mm + self.max_point_mm + self.size_mm
        if not all(isfinite(value) for value in values):
            raise ValueError("bounding-box values must be finite")
        expected_sizes = [
            maximum - minimum
            for minimum, maximum in zip(
                self.min_point_mm, self.max_point_mm, strict=True
            )
        ]
        if any(size < 0 for size in expected_sizes) or any(
            not isclose(size, expected, rel_tol=1e-9, abs_tol=1e-9)
            for size, expected in zip(self.size_mm, expected_sizes, strict=True)
        ):
            raise ValueError("bounding-box extents are inconsistent")
        return self


class EquationSummary(StrictContract):
    """One SolidWorks equation or global variable, without COM internals."""

    index: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=240)
    expression: str = Field(min_length=1, max_length=2_048)
    is_global_variable: bool


class MaterialAssignmentSummary(StrictContract):
    """Material assignment reported for one active part configuration."""

    database_name: str = Field(max_length=1024)
    material_name: str = Field(max_length=512)


class SheetMetalSummary(StrictContract):
    """Client-safe state reported for an active sheet-metal part."""

    is_sheet_metal: bool
    is_flattened: bool | None = None
    thickness_mm: float | None = Field(default=None, ge=0)
    flat_pattern_feature_name: str | None = Field(default=None, max_length=240)


class SheetMetalFeatureGeometrySummary(StrictContract):
    """Feature-associated faces for a flat pattern or individual bend."""

    display_name: str = Field(min_length=1, max_length=240)
    feature_kind: Literal["bend", "flat_pattern"]
    face_ids: list[str] = Field(default_factory=list, max_length=100)


class SheetMetalBendTableSummary(StrictContract):
    """Client-safe confirmation for one generated sheet-metal bend table."""

    display_name: str = Field(min_length=1, max_length=240)
    annotation_count: int | None = Field(default=None, ge=0)


class SheetMetalBendNotesSummary(StrictContract):
    """Client-safe confirmation for bend notes generated from a flat pattern."""

    flat_pattern_feature_name: str = Field(min_length=1, max_length=240)


class DrawingBomSummary(StrictContract):
    """Client-safe confirmation of one drawing bill of materials table."""

    display_name: str = Field(min_length=1, max_length=240)
    bom_type: Literal["top_level_only", "parts_only", "indented"]
    row_count: int | None = Field(default=None, ge=0)
    column_count: int | None = Field(default=None, ge=0)


class DrawingAnnotationSummary(StrictContract):
    """Client-safe confirmation for one drawing annotation."""

    annotation_type: Literal[
        "note", "dimension", "balloon", "hole_callout",
        "ordinate_dimension", "surface_finish", "weld_symbol", "geometric_tolerance", "center_mark",
    ]
    display_name: str = Field(min_length=1, max_length=240)


class DrawingAnnotationTargetSummary(StrictContract):
    """Revision-bound opaque target that can be used for a drawing annotation."""

    target_id: str = Field(min_length=1, max_length=128)
    view_id: str = Field(min_length=1, max_length=240)
    target_type: Literal["dimension", "component", "edge"]
    display_name: str = Field(min_length=1, max_length=240)


class OperationResult(StrictContract):
    """Common structured result returned by every MVP MCP tool."""

    status: OperationStatus
    operation_id: str = Field(default_factory=lambda: f"op_{uuid4().hex}")
    route: OperationRoute = OperationRoute.NONE
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    error: OperationError | None = None

    @model_validator(mode="after")
    def validate_error_state(self) -> OperationResult:
        """Require structured errors for failed operations only."""
        if self.status is OperationStatus.FAILED and self.error is None:
            raise ValueError("failed operations require an error")
        if self.status is not OperationStatus.FAILED and self.error is not None:
            raise ValueError("only failed operations may include an error")
        return self

    @classmethod
    def succeeded(
        cls,
        *,
        data: dict[str, Any],
        route: OperationRoute = OperationRoute.COM,
        operation_id: str | None = None,
        warnings: list[str] | None = None,
        artifacts: list[Artifact] | None = None,
    ) -> OperationResult:
        """Create a successful result with the shared output envelope."""
        return cls(
            status=OperationStatus.SUCCEEDED,
            operation_id=operation_id or f"op_{uuid4().hex}",
            route=route,
            data=data,
            warnings=warnings or [],
            artifacts=artifacts or [],
        )

    @classmethod
    def failed(
        cls,
        *,
        code: str,
        message: str,
        suggestion: str,
        operation_id: str | None = None,
    ) -> OperationResult:
        """Create a failed result without leaking implementation details."""
        return cls(
            status=OperationStatus.FAILED,
            operation_id=operation_id or f"op_{uuid4().hex}",
            error=OperationError(code=code, message=message, suggestion=suggestion),
        )


class SessionRequest(StrictContract):
    """Base input for operations scoped to an existing workflow session."""

    session_id: str = Field(min_length=1, max_length=128)


class DocumentRequest(SessionRequest):
    """Base input for operations scoped to a session document."""

    document_id: str = Field(min_length=1, max_length=128)


class CheckAssemblyInterferenceRequest(DocumentRequest):
    """Check every component in the current session-owned assembly."""

    include_coincident: bool = False
    include_multibody_part_interferences: bool = False


class ActivateDocumentRequest(DocumentRequest):
    """Activate one already session-owned SolidWorks document by opaque ID."""


class CloseDocumentRequest(DocumentRequest):
    """Close the active session-owned document and revoke its opaque reference."""

    save: bool = False


class FeatureDimensionRequest(DocumentRequest):
    """Address one model dimension through a current opaque feature reference."""

    feature_id: str = Field(min_length=1, max_length=256)
    dimension_index: int = Field(default=1, ge=1, le=100)

    @field_validator("feature_id")
    @classmethod
    def validate_feature_id(cls, value: str) -> str:
        """Reject control characters before resolving a server-issued feature ID."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("feature_id must not contain control characters")
        return value


class GetFeatureDimensionRequest(FeatureDimensionRequest):
    """Read one driving dimension of a current server-issued feature."""


class SetFeatureDimensionRequest(FeatureDimensionRequest):
    """Change one driving feature dimension in millimetres and rebuild the part."""

    value: float = Field(gt=0, le=10_000)


class DimensionSummary(StrictContract):
    """Client-safe model dimension value addressed by an opaque server ID."""

    dimension_id: str = Field(min_length=1, max_length=128)
    value_mm: float


class ListDimensionsRequest(DocumentRequest):
    """List a bounded page of current model dimensions as opaque IDs."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class DimensionRequest(DocumentRequest):
    """Base request for one current server-issued model dimension ID."""

    dimension_id: str = Field(min_length=1, max_length=128)

    @field_validator("dimension_id")
    @classmethod
    def validate_dimension_id(cls, value: str) -> str:
        """Reject control characters before looking up a session capability."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("dimension_id must not contain control characters")
        return value


class GetDimensionRequest(DimensionRequest):
    """Read one current server-issued model dimension."""


class SetDimensionRequest(DimensionRequest):
    """Change one current model dimension in millimetres and rebuild the part."""

    value: float = Field(gt=0, le=10_000)


class OpenModelRequest(SessionRequest):
    """Open a native SolidWorks or STEP/IGES file from the trusted input root."""

    file_name: str = Field(min_length=1, max_length=160)

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        """Reject paths and non-native formats before any COM call is attempted."""
        if any(separator in value for separator in ("/", "\\", ":")) or value in {
            ".",
            "..",
        }:
            raise ValueError("file_name must be a simple file name, not a path")
        if Path(value).suffix.lower() not in {
            ".sldprt",
            ".sldasm",
            ".slddrw",
            ".step",
            ".stp",
            ".iges",
            ".igs",
        }:
            raise ValueError(
                "file_name must use a native SolidWorks or STEP/IGES extension"
            )
        _validate_windows_leaf_name(value, field_name="file_name")
        return value


class CreatePartRequest(SessionRequest):
    """Input for creating the active part document in a workflow session."""

    name: str = Field(min_length=1, max_length=120)
    units: Literal["mm", "m", "in"] = "mm"


class CreateAssemblyRequest(SessionRequest):
    """Input for creating an assembly document from the configured SolidWorks template."""

    name: str = Field(min_length=1, max_length=120)


class CreateDrawingRequest(SessionRequest):
    """Input for creating a drawing document from the configured SolidWorks template."""

    name: str = Field(min_length=1, max_length=120)


class ListFeaturesRequest(DocumentRequest):
    """Input for a bounded feature-tree listing on the active session document."""

    include_suppressed: bool = False
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListConfigurationsRequest(DocumentRequest):
    """Input for a bounded configuration listing on the active session document."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListSolidBodiesRequest(DocumentRequest):
    """Input for a bounded solid-body listing on an active part document."""

    include_hidden: bool = False
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ListSurfaceBodiesRequest(ListSolidBodiesRequest):
    """Input for a bounded surface-body listing on an active part document."""


class GetBoundingBoxRequest(DocumentRequest):
    """Read the active part's axis-aligned model extents in millimetres."""


class ListEquationsRequest(DocumentRequest):
    """Read a bounded page of active-part equations and global variables."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class EquationNameRequest(DocumentRequest):
    """Base input for one server-resolved equation or global-variable name."""

    name: str = Field(min_length=1, max_length=240)

    @field_validator("name")
    @classmethod
    def validate_equation_name(cls, value: str) -> str:
        """Prevent a caller from supplying a complete or compound assignment."""
        return _validate_equation_name(value)


class GetEquationRequest(EquationNameRequest):
    """Read one equation or global variable by its left-hand-side name."""


class SetEquationRequest(EquationNameRequest):
    """Create or update one equation or global variable on all configurations."""

    expression: str = Field(min_length=1, max_length=2_048)

    @field_validator("expression")
    @classmethod
    def validate_equation_expression(cls, value: str) -> str:
        """Keep the public formula separate from server-owned assignment syntax."""
        return _validate_equation_expression(value)


class DeleteEquationRequest(EquationNameRequest):
    """Delete one equation or global variable by its left-hand-side name."""


class GetMaterialAssignmentRequest(DocumentRequest):
    """Input for reading an active part configuration's material assignment."""


class SetMaterialAssignmentRequest(DocumentRequest):
    """Input for assigning one material from a SolidWorks material database."""

    database_name: str = Field(min_length=1, max_length=1024)
    material_name: str = Field(min_length=1, max_length=512)

    @field_validator("database_name", "material_name")
    @classmethod
    def validate_material_text(cls, value: str) -> str:
        """Reject control characters before passing material metadata to COM."""
        if any(ord(character) < 32 for character in value):
            raise ValueError(
                "material assignment values must not contain control characters"
            )
        return value

    @field_validator("database_name")
    @classmethod
    def validate_material_database(cls, value: str) -> str:
        """Accept only the native SolidWorks material-database file type."""
        if Path(value).suffix.lower() != ".sldmat":
            raise ValueError("database_name must reference a .sldmat material database")
        return value


class GetConfigurationRequest(DocumentRequest):
    """Read one exact current configuration and its description."""

    configuration_name: str = Field(min_length=1, max_length=120)

    @field_validator("configuration_name")
    @classmethod
    def validate_configuration_name(cls, value: str) -> str:
        return _validate_configuration_name(value)


class ActivateConfigurationRequest(DocumentRequest):
    """Input for switching an active document to one existing configuration."""

    configuration_name: str = Field(min_length=1, max_length=120)

    @field_validator("configuration_name")
    @classmethod
    def validate_configuration_name(cls, value: str) -> str:
        """Reject values that can be interpreted as a path or control sequence."""
        return _validate_configuration_name(value)


class CreateConfigurationRequest(DocumentRequest):
    """Create and activate one independent configuration on an active document."""

    configuration_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1024)

    @field_validator("configuration_name")
    @classmethod
    def validate_configuration_name(cls, value: str) -> str:
        """Reject configuration names that can be interpreted as path input."""
        return _validate_configuration_name(value)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        """Reject control sequences before passing configuration metadata to COM."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("description must not contain control characters")
        return value


class DeleteConfigurationRequest(DocumentRequest):
    """Delete one non-active configuration from a tracked document."""

    configuration_name: str = Field(min_length=1, max_length=120)

    @field_validator("configuration_name")
    @classmethod
    def validate_configuration_name(cls, value: str) -> str:
        """Reject path-like configuration identifiers."""
        return _validate_configuration_name(value)


class InsertAssemblyComponentRequest(DocumentRequest):
    """Insert one saved session-owned part into the active assembly document."""

    source_part_document_id: str = Field(min_length=1, max_length=128)
    x: float = Field(default=0.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=0.0, ge=-1_000_000, le=1_000_000)
    z: float = Field(default=0.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"


class ListAssemblyComponentsRequest(DocumentRequest):
    """Input for a bounded list of components in the active assembly document."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class GetAssemblyComponentRequest(DocumentRequest):
    """Find one active-assembly component by an exact display name."""

    component_name: str = Field(min_length=1, max_length=240)

    @field_validator("component_name")
    @classmethod
    def validate_component_name(cls, value: str) -> str:
        """Keep component lookup input as a name, never a path or selection string."""
        if any(separator in value for separator in ("/", "\\", ":")):
            raise ValueError(
                "component_name must be a component display name, not a path"
            )
        if any(ord(character) < 32 for character in value):
            raise ValueError("component_name must not contain control characters")
        return value


class ListAssemblyMateEntitiesRequest(DocumentRequest):
    """List stable face or edge references for components in an active assembly."""

    component_id: str | None = Field(default=None, min_length=1, max_length=240)
    entity_types: list[Literal["face", "edge"]] = Field(
        default_factory=_default_assembly_mate_entity_types,
        min_length=1,
        max_length=2,
    )
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("component_id")
    @classmethod
    def validate_component_id(cls, value: str | None) -> str | None:
        """Reject control characters from a component identifier before COM lookup."""
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("component_id must not contain control characters")
        return value

    @field_validator("entity_types")
    @classmethod
    def validate_entity_types(cls, value: list[str]) -> list[str]:
        """Require a non-duplicated, deterministic geometry type selection."""
        if len(set(value)) != len(value):
            raise ValueError("entity_types must not contain duplicates")
        return value


class CreateAssemblyMateRequest(DocumentRequest):
    """Create one typed mate from two server-issued assembly entity references."""

    first_entity_id: str = Field(min_length=1, max_length=128)
    second_entity_id: str = Field(min_length=1, max_length=128)
    mate_type: Literal[
        "coincident",
        "concentric",
        "parallel",
        "perpendicular",
        "tangent",
        "distance",
        "angle",
    ]
    alignment: Literal["aligned", "anti_aligned"] = "aligned"
    flip: bool = False
    distance: float | None = Field(default=None, ge=0, le=1_000_000)
    angle: float | None = Field(default=None, ge=-360, le=360)
    unit: Literal["mm", "m", "in"] = "mm"
    for_positioning_only: bool = False
    lock_rotation: bool = False

    @field_validator("first_entity_id", "second_entity_id")
    @classmethod
    def validate_entity_id(cls, value: str) -> str:
        """Only accept opaque server-issued identifiers, never control sequences."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("entity IDs must not contain control characters")
        return value

    @model_validator(mode="after")
    def validate_mate_parameters(self) -> CreateAssemblyMateRequest:
        """Require the dimension used by typed distance and angle mates."""
        if self.first_entity_id == self.second_entity_id:
            raise ValueError("an assembly mate requires two distinct entity IDs")
        if self.mate_type == "distance" and self.distance is None:
            raise ValueError("distance mates require a distance value")
        if self.mate_type == "angle" and self.angle is None:
            raise ValueError("angle mates require an angle value")
        return self


class ListAssemblyMatesRequest(DocumentRequest):
    """List current assembly mates and issue opaque IDs for later deletion."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class DeleteAssemblyMateRequest(DocumentRequest):
    """Delete one revision-bound assembly mate issued by the server."""

    mate_id: str = Field(min_length=1, max_length=128)

    @field_validator("mate_id")
    @classmethod
    def validate_mate_id(cls, value: str) -> str:
        """Reject control characters from an opaque server-issued mate ID."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("mate_id must not contain control characters")
        return value


class ListPartTopologyRequest(DocumentRequest):
    """List revision-bound part faces, edges, and vertices with safe measurements."""

    entity_types: list[Literal["face", "edge", "vertex"]] = Field(
        default_factory=_default_part_topology_entity_types,
        min_length=1,
        max_length=3,
    )
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("entity_types")
    @classmethod
    def validate_entity_types(cls, value: list[str]) -> list[str]:
        """Keep topology selection deterministic and non-duplicated."""
        if len(set(value)) != len(value):
            raise ValueError("entity_types must not contain duplicates")
        return value


class ListSheetMetalFeatureGeometryRequest(DocumentRequest):
    """List current flat-pattern and bend feature faces using opaque face IDs."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class InsertSheetMetalBendNotesRequest(DocumentRequest):
    """Insert SolidWorks bend notes for the active part's current flat pattern."""


class InsertSheetMetalBendTableRequest(DocumentRequest):
    """Insert a bend table using the SolidWorks default table template."""

    x: float = Field(default=0, ge=-100_000, le=100_000)
    y: float = Field(default=0, ge=-100_000, le=100_000)
    start_value: int = Field(default=1, ge=0, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"


class CreateDrawingViewRequest(DocumentRequest):
    """Create one standard model view in the active drawing document."""

    source_model_document_id: str = Field(min_length=1, max_length=128)
    orientation: Literal[
        "front",
        "back",
        "top",
        "bottom",
        "left",
        "right",
        "isometric",
        "dimetric",
        "trimetric",
    ] = "front"
    x: float = Field(default=100.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=100.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"


class ListDrawingViewsRequest(DocumentRequest):
    """Input for a bounded page of model views in the active drawing document."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ActivateDrawingViewRequest(DocumentRequest):
    """Activate one current drawing view returned by the view-listing tool."""

    view_id: str = Field(min_length=1, max_length=240)

    @field_validator("view_id")
    @classmethod
    def validate_view_id(cls, value: str) -> str:
        """Reject control characters before resolving the view in COM."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("view_id must not contain control characters")
        return value


class ListDrawingSheetsRequest(DocumentRequest):
    """Input for a bounded page of sheets in the active drawing document."""

    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ActivateDrawingSheetRequest(DocumentRequest):
    """Activate one sheet name returned by the Sheet-listing tool."""

    sheet_name: str = Field(min_length=1, max_length=240)

    @field_validator("sheet_name")
    @classmethod
    def validate_sheet_name(cls, value: str) -> str:
        """Reject control characters before forwarding a sheet name to COM."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("sheet_name must not contain control characters")
        return value


class CreateDrawingSheetRequest(DocumentRequest):
    """Create an activated drawing sheet with a standard or custom paper size."""

    sheet_name: str = Field(min_length=1, max_length=240)
    paper_size: Literal["a0", "a1", "a2", "a3", "a4", "user_defined"] = "a4"
    width: float | None = Field(default=None, gt=0, le=10_000)
    height: float | None = Field(default=None, gt=0, le=10_000)
    unit: Literal["mm", "m", "in"] = "mm"
    first_angle_projection: bool = False
    scale_numerator: int = Field(default=1, ge=1, le=1_000)
    scale_denominator: int = Field(default=1, ge=1, le=1_000)
    zone_rows: int = Field(default=0, ge=0, le=100)
    zone_columns: int = Field(default=0, ge=0, le=100)

    @field_validator("sheet_name")
    @classmethod
    def validate_sheet_name(cls, value: str) -> str:
        """Reject control characters before creating a COM drawing sheet."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("sheet_name must not contain control characters")
        return value

    @model_validator(mode="after")
    def validate_custom_size(self) -> CreateDrawingSheetRequest:
        """Require dimensions only for a user-defined drawing paper size."""
        dimensions_supplied = self.width is not None or self.height is not None
        if self.paper_size == "user_defined":
            if self.width is None or self.height is None:
                raise ValueError("user_defined paper_size requires both width and height")
        elif dimensions_supplied:
            raise ValueError("width and height are only supported for user_defined paper_size")
        return self


class DeleteDrawingSheetRequest(ActivateDrawingSheetRequest):
    """Delete one listed drawing sheet while preserving at least one sheet."""


class RenameDrawingSheetRequest(DocumentRequest):
    """Rename one existing drawing sheet to a new unique sheet name."""

    sheet_name: str = Field(min_length=1, max_length=240)
    new_sheet_name: str = Field(min_length=1, max_length=240)

    @field_validator("sheet_name", "new_sheet_name")
    @classmethod
    def validate_sheet_name(cls, value: str) -> str:
        """Reject control characters before a sheet name reaches COM."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("sheet names must not contain control characters")
        return value

    @model_validator(mode="after")
    def validate_distinct_names(self) -> RenameDrawingSheetRequest:
        """Require a real rename rather than a no-op command."""
        if self.sheet_name.casefold() == self.new_sheet_name.casefold():
            raise ValueError("new_sheet_name must differ from sheet_name")
        return self


class ReorderDrawingSheetsRequest(DocumentRequest):
    """Replace the active drawing's sheet order with its complete current name list."""

    sheet_names: list[str] = Field(min_length=2, max_length=100)

    @field_validator("sheet_names")
    @classmethod
    def validate_sheet_names(cls, values: list[str]) -> list[str]:
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("sheet_names must not contain duplicates")
        if any(not value or any(ord(character) < 32 for character in value) for value in values):
            raise ValueError("sheet_names must contain non-empty printable names")
        return values


class GetDrawingViewOutlineRequest(DocumentRequest):
    """Read one server-issued active-drawing view's layout outline."""

    view_id: str = Field(min_length=1, max_length=240)

    @field_validator("view_id")
    @classmethod
    def validate_view_id(cls, value: str) -> str:
        """Reject control characters before resolving a drawing view in COM."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("view_id must not contain control characters")
        return value


class UpdateDrawingViewLayoutRequest(DocumentRequest):
    """Move and/or rescale one current drawing view returned by the server."""

    view_id: str = Field(min_length=1, max_length=240)
    x: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    y: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"
    scale_decimal: float | None = Field(default=None, gt=0, le=1_000)

    @field_validator("view_id")
    @classmethod
    def validate_view_id(cls, value: str) -> str:
        """Reject control characters before resolving the view in COM."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("view_id must not contain control characters")
        return value

    @model_validator(mode="after")
    def require_change(self) -> UpdateDrawingViewLayoutRequest:
        """Require at least one concrete layout mutation."""
        if self.scale_decimal is None and (self.x is None or self.y is None):
            raise ValueError("provide both x and y, or provide scale_decimal")
        if (self.x is None) != (self.y is None):
            raise ValueError("x and y must be provided together")
        return self


class AlignDrawingViewsRequest(DocumentRequest):
    """Arrange multiple current drawing views using their outline dimensions."""

    view_ids: list[str] = Field(min_length=2, max_length=100)
    direction: Literal["horizontal", "vertical"] = "horizontal"
    gap: float = Field(default=10.0, ge=0, le=1_000_000)
    start_x: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    start_y: float | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"

    @field_validator("view_ids")
    @classmethod
    def validate_view_ids(cls, value: list[str]) -> list[str]:
        """Require unique, printable server-issued view identifiers."""
        normalized = [item.strip() for item in value]
        if any(not item or any(ord(character) < 32 for character in item) for item in normalized):
            raise ValueError("view_ids must contain non-empty printable view IDs")
        if len(set(normalized)) != len(normalized):
            raise ValueError("view_ids must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_start_coordinates(self) -> AlignDrawingViewsRequest:
        """Require both start coordinates when an explicit origin is supplied."""
        if (self.start_x is None) != (self.start_y is None):
            raise ValueError("start_x and start_y must be provided together")
        return self


class RebuildDrawingRequest(DocumentRequest):
    """Rebuild and save the active session-owned drawing document."""


class CreateProjectedDrawingViewRequest(DocumentRequest):
    """Create one projected view from a server-issued view ID in the active drawing."""

    source_view_id: str = Field(min_length=1, max_length=240)
    x: float = Field(default=150.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=100.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"

    @field_validator("source_view_id")
    @classmethod
    def validate_source_view_id(cls, value: str) -> str:
        """Reject control characters before using the server-issued identifier."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("source_view_id must not contain control characters")
        return value


class CreateAuxiliaryDrawingViewRequest(DocumentRequest):
    """Create an auxiliary view from one listed drawing edge target."""

    target_id: str = Field(min_length=1, max_length=256)
    x: float = Field(default=150.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=100.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"

    @field_validator("target_id")
    @classmethod
    def validate_target_id(cls, value: str) -> str:
        """Reject control characters before resolving the opaque drawing target."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("target_id must not contain control characters")
        return value


class CreateDrawingBomRequest(DocumentRequest):
    """Insert one assembly bill of materials table into an active drawing."""

    source_assembly_document_id: str = Field(min_length=1, max_length=128)
    source_view_id: str = Field(min_length=1, max_length=240)
    bom_type: Literal["top_level_only", "parts_only", "indented"] = "parts_only"
    x: float = Field(default=180.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=80.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"
    detailed_cut_list: bool = False

    @field_validator("source_view_id")
    @classmethod
    def validate_source_view_id(cls, value: str) -> str:
        """Reject control characters before using a server-issued drawing view ID."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("source_view_id must not contain control characters")
        return value


class UpdateDrawingBomRequest(DocumentRequest):
    """Refresh one server-issued drawing BOM table after an assembly change."""

    bom_id: str = Field(min_length=1, max_length=128)

    @field_validator("bom_id")
    @classmethod
    def validate_bom_id(cls, value: str) -> str:
        """Reject control characters from an opaque server-issued BOM ID."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("bom_id must not contain control characters")
        return value


class DeleteDrawingBomRequest(UpdateDrawingBomRequest):
    """Delete one server-issued drawing BOM table from the active drawing."""


class CreateSectionDrawingViewRequest(DocumentRequest):
    """Create a section view from a current drawing view without UI selection."""

    source_view_id: str = Field(min_length=1, max_length=240)
    cut_start_x: float = Field(ge=-1_000_000, le=1_000_000)
    cut_start_y: float = Field(ge=-1_000_000, le=1_000_000)
    cut_end_x: float = Field(ge=-1_000_000, le=1_000_000)
    cut_end_y: float = Field(ge=-1_000_000, le=1_000_000)
    x: float = Field(default=150.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=100.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"

    @field_validator("source_view_id")
    @classmethod
    def validate_source_view_id(cls, value: str) -> str:
        """Reject raw selection syntax before resolving a current drawing view."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("source_view_id must not contain control characters")
        return value

    @model_validator(mode="after")
    def validate_cut_line(self) -> CreateSectionDrawingViewRequest:
        """Require a non-degenerate section cutting line."""
        if self.cut_start_x == self.cut_end_x and self.cut_start_y == self.cut_end_y:
            raise ValueError("section cut endpoints must be distinct")
        return self


class CreateDrawingNoteRequest(DocumentRequest):
    """Insert one text note at a controlled position in an active drawing."""

    text: str = Field(min_length=1, max_length=4096)
    x: float = Field(default=180.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=80.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"

    @field_validator("text")
    @classmethod
    def validate_note_text(cls, value: str) -> str:
        """Allow normal multi-line notes but reject embedded control sequences."""
        if any(
            ord(character) < 32 and character not in {"\n", "\r", "\t"}
            for character in value
        ):
            raise ValueError("text must not contain unsupported control characters")
        return value


class CreateDrawingRevisionTableRequest(DocumentRequest):
    """Create a revision table and add one initial revision row."""

    x: float = Field(default=180.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=80.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"
    anchor: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_left"
    revision: str = Field(default="A", min_length=1, max_length=32)

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        """Keep revision labels printable before they reach the table API."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("revision must not contain control characters")
        return value


class DrawingRevisionTableRequest(DocumentRequest):
    """Target one current server-issued drawing revision table."""

    revision_table_id: str = Field(min_length=1, max_length=128)

    @field_validator("revision_table_id")
    @classmethod
    def validate_revision_table_id(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("revision_table_id must not contain control characters")
        return value


class AddDrawingRevisionRequest(DrawingRevisionTableRequest):
    """Add one revision row to a current revision table."""

    revision: str = Field(min_length=1, max_length=32)

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("revision must not contain control characters")
        return value


class DeleteDrawingRevisionRequest(DrawingRevisionTableRequest):
    """Delete one revision row by its SolidWorks revision ID."""

    revision_id: int = Field(ge=0, le=1_000_000)
    delete_symbols: bool = True


class ListDrawingRevisionsRequest(DrawingRevisionTableRequest):
    """List rows and symbols from a current revision table."""


class ListDrawingAnnotationTargetsRequest(DocumentRequest):
    """List revision-bound entities that can be dimensioned or annotated."""

    view_id: str = Field(min_length=1, max_length=240)
    target_types: list[Literal["dimension", "component", "edge"]] = Field(
        default_factory=_default_drawing_annotation_target_types,
        min_length=1,
        max_length=3,
    )
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("view_id")
    @classmethod
    def validate_view_id(cls, value: str) -> str:
        """Reject control characters from a drawing view identifier."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("view_id must not contain control characters")
        return value

    @field_validator("target_types")
    @classmethod
    def validate_target_types(cls, value: list[str]) -> list[str]:
        """Require a deterministic, non-duplicated target-type selection."""
        if len(set(value)) != len(value):
            raise ValueError("target_types must not contain duplicates")
        return value


class DrawingAnnotationTargetRequest(DocumentRequest):
    """Base request for an annotation created from an opaque target ID."""

    target_id: str = Field(min_length=1, max_length=128)
    x: float = Field(default=180.0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=80.0, ge=-1_000_000, le=1_000_000)
    unit: Literal["mm", "m", "in"] = "mm"

    @field_validator("target_id")
    @classmethod
    def validate_target_id(cls, value: str) -> str:
        """Only accept an opaque server-issued annotation target ID."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("target_id must not contain control characters")
        return value


class PlaceDrawingDimensionRequest(DrawingAnnotationTargetRequest):
    """Place one already-imported model dimension on an active drawing sheet."""


class CreateDrawingBalloonRequest(DrawingAnnotationTargetRequest):
    """Create one BOM balloon from an opaque visible assembly component target."""

    text: str = Field(default="", max_length=512)
    style: Literal["circular", "split_circular", "hexagonal", "square"] = "circular"

    @field_validator("text")
    @classmethod
    def validate_balloon_text(cls, value: str) -> str:
        """Keep balloon text printable before it reaches SolidWorks."""
        if any(
            ord(character) < 32 and character not in {"\n", "\r", "\t"}
            for character in value
        ):
            raise ValueError("text must not contain unsupported control characters")
        return value


class CreateDrawingHoleCalloutRequest(DrawingAnnotationTargetRequest):
    """Create one automatic hole callout from an opaque visible edge target."""


class CreateDrawingOrdinateDimensionRequest(DrawingAnnotationTargetRequest):
    """Create an ordinate dimension from a server-issued drawing target."""

    ordinate_type: Literal["horizontal", "vertical"] = "horizontal"


class CreateDrawingSurfaceFinishRequest(DrawingAnnotationTargetRequest):
    """Insert an ISO surface-finish symbol on a server-issued drawing target."""

    symbol: Literal["machining_required", "machining_prohibited", "any_process"] = "machining_required"
    value: str = Field(default="", max_length=64)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("value must not contain control characters")
        return value


class CreateDrawingWeldSymbolRequest(DrawingAnnotationTargetRequest):
    """Insert a weld symbol on a server-issued drawing target."""

    weld_type: str = Field(default="Fillet", min_length=1, max_length=64)
    size: str = Field(default="", max_length=64)

    @field_validator("weld_type", "size")
    @classmethod
    def validate_weld_text(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("weld fields must not contain control characters")
        return value


class CreateDrawingGeometricToleranceRequest(DrawingAnnotationTargetRequest):
    """Insert a geometric tolerance frame on a server-issued drawing target."""

    tolerance: str = Field(min_length=1, max_length=128)
    datum: str = Field(default="", max_length=32)

    @field_validator("tolerance", "datum")
    @classmethod
    def validate_tolerance_text(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("tolerance fields must not contain control characters")
        return value


class CreateDrawingCenterMarkRequest(DrawingAnnotationTargetRequest):
    """Create a center mark from a server-issued circular drawing edge."""

    size: float = Field(default=5.0, gt=0, le=10_000)
    unit: Literal["mm", "m", "in"] = "mm"
    show_connection_lines: bool = True


class ListCustomPropertiesRequest(DocumentRequest):
    """Read a bounded page of document-level or active-configuration properties."""

    scope: Literal["document", "active_configuration"] = "document"
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SetCustomPropertyRequest(DocumentRequest):
    """Set one document-level or active-configuration custom property."""

    name: str = Field(min_length=1, max_length=255)
    value: str = Field(max_length=4096)
    scope: Literal["document", "active_configuration"] = "document"

    @field_validator("name")
    @classmethod
    def validate_property_name(cls, value: str) -> str:
        """Keep property keys as plain identifiers, not path-like input."""
        return _validate_custom_property_name(value)


class DeleteCustomPropertyRequest(DocumentRequest):
    """Delete one document-level or active-configuration custom property."""

    name: str = Field(min_length=1, max_length=255)
    scope: Literal["document", "active_configuration"] = "document"

    @field_validator("name")
    @classmethod
    def validate_property_name(cls, value: str) -> str:
        """Keep property keys as plain identifiers, not path-like input."""
        return _validate_custom_property_name(value)


class CreateSketchRequest(DocumentRequest):
    """Create a sketch on one standard plane or one server-issued reference plane."""

    plane: Literal["Front", "Top", "Right"] | None = None
    reference_plane_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_plane_target(self) -> CreateSketchRequest:
        """Require exactly one server-controlled sketch support plane."""
        if (self.plane is None) == (self.reference_plane_id is None):
            raise ValueError("provide exactly one of plane or reference_plane_id")
        return self


class Create3DSketchRequest(DocumentRequest):
    """Open one unconstrained three-dimensional sketch in the active part."""


class SketchRequest(DocumentRequest):
    """Base input for operations targeting the last session sketch."""

    sketch_id: str = Field(min_length=1, max_length=256)


class AddRectangleRequest(SketchRequest):
    """Input for adding one rectangular profile to the active sketch in millimetres."""

    x1: float
    y1: float
    x2: float
    y2: float

    @model_validator(mode="after")
    def validate_rectangle(self) -> AddRectangleRequest:
        """Reject degenerate rectangles before they reach SolidWorks."""
        if self.x1 == self.x2 or self.y1 == self.y2:
            raise ValueError("rectangle corners must span both x and y axes")
        return self


class AddLineRequest(SketchRequest):
    """Input for adding one non-degenerate line segment in millimetres."""

    x1: float
    y1: float
    x2: float
    y2: float

    @model_validator(mode="after")
    def validate_line(self) -> AddLineRequest:
        """Reject zero-length geometry before SolidWorks is called."""
        if self.x1 == self.x2 and self.y1 == self.y2:
            raise ValueError("line endpoints must be distinct")
        return self


class Add3DLineRequest(SketchRequest):
    """Add one non-degenerate line segment to an active 3D sketch in millimetres."""

    x1: float
    y1: float
    z1: float
    x2: float
    y2: float
    z2: float

    @model_validator(mode="after")
    def validate_line(self) -> Add3DLineRequest:
        """Reject a zero-length 3D line before calling SolidWorks."""
        if (self.x1, self.y1, self.z1) == (self.x2, self.y2, self.z2):
            raise ValueError("3D line endpoints must be distinct")
        return self


class AddCircleRequest(SketchRequest):
    """Input for adding one circle in millimetres."""

    center_x: float
    center_y: float
    radius: float = Field(gt=0, le=10_000)


class AddPointRequest(SketchRequest):
    """Input for adding one placement point to the active sketch in millimetres."""

    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)


class AddArcRequest(SketchRequest):
    """Input for adding one circular arc in millimetres."""

    center_x: float
    center_y: float
    start_x: float
    start_y: float
    end_x: float
    end_y: float

    @model_validator(mode="after")
    def validate_arc(self) -> AddArcRequest:
        """Require distinct endpoints on a consistent non-zero radius."""
        start_radius = hypot(self.start_x - self.center_x, self.start_y - self.center_y)
        end_radius = hypot(self.end_x - self.center_x, self.end_y - self.center_y)
        if start_radius <= 0 or end_radius <= 0:
            raise ValueError("arc endpoints must not be the arc centre")
        if not isclose(start_radius, end_radius, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("arc endpoints must lie on the same radius")
        if self.start_x == self.end_x and self.start_y == self.end_y:
            raise ValueError("arc endpoints must be distinct")
        return self


class AddCenterlineRequest(AddLineRequest):
    """Input for adding one construction centreline in millimetres."""


class SketchPoint(StrictContract):
    """One two-dimensional sketch point expressed in millimetres."""

    x: float
    y: float


class AddSplineRequest(SketchRequest):
    """Input for adding one NURBS spline through ordered sketch points."""

    points: list[SketchPoint] = Field(min_length=2, max_length=100)


class AddPolygonRequest(SketchRequest):
    """Input for adding one regular polygon in millimetres."""

    center_x: float
    center_y: float
    radius: float = Field(gt=0, le=10_000)
    sides: int = Field(ge=3, le=40)


class AddEllipseRequest(SketchRequest):
    """Input for adding one axis-aligned ellipse in millimetres."""

    center_x: float
    center_y: float
    major_axis: float = Field(gt=0, le=20_000)
    minor_axis: float = Field(gt=0, le=20_000)


class AddSketchTextRequest(SketchRequest):
    """Input for adding one text entity to the active sketch."""

    text: str = Field(min_length=1, max_length=4096)
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)
    height: float = Field(gt=0, le=100_000)
    angle_degrees: float = Field(ge=-360, le=360, default=0.0)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Reject control sequences before forwarding text to SolidWorks."""
        if any(ord(character) < 32 for character in value):
            raise ValueError("text must not contain control characters")
        return value


class SketchEntityListRequest(SketchRequest):
    """Base input for edits that operate on server-issued sketch entity IDs."""

    entity_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("entity_ids")
    @classmethod
    def validate_entity_ids(cls, values: list[str]) -> list[str]:
        """Reject selection syntax and duplicate opaque entity identifiers."""
        if len(set(values)) != len(values):
            raise ValueError("entity_ids must not contain duplicates")
        for value in values:
            _validate_opaque_sketch_entity_id(value)
        return values


class CreateSketchLinearPatternRequest(SketchEntityListRequest):
    """Input for a single-row linear pattern in the active sketch."""

    direction_x: float
    direction_y: float
    spacing: float = Field(gt=0, le=10_000)
    count: int = Field(ge=2, le=1_000)

    @model_validator(mode="after")
    def validate_direction(self) -> CreateSketchLinearPatternRequest:
        """Require a meaningful direction vector before opening COM selection state."""
        if hypot(self.direction_x, self.direction_y) < 1e-9:
            raise ValueError("pattern direction must be non-zero")
        return self


class CreateSketchCircularPatternRequest(SketchEntityListRequest):
    """Input for a circular pattern about the active sketch origin."""

    angle_degrees: float = Field(gt=0, le=360)
    count: int = Field(ge=2, le=1_000)


class MirrorSketchEntitiesRequest(SketchEntityListRequest):
    """Input for mirroring sketch entities about one server-issued centreline."""

    mirror_line_id: str = Field(min_length=1, max_length=256)

    @field_validator("mirror_line_id")
    @classmethod
    def validate_mirror_line_id(cls, value: str) -> str:
        """Accept only the internal identifier format used by sketch entities."""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,255}", value):
            raise ValueError("mirror_line_id must be a server-issued entity ID")
        return value


class OffsetSketchEntitiesRequest(SketchEntityListRequest):
    """Input for offsetting a set of sketch entities in millimetres."""

    distance: float = Field(gt=0, le=10_000)
    reverse_direction: bool = False


class TrimSketchEntityRequest(SketchRequest):
    """Trim one current server-issued sketch entity at a point in millimetres."""

    entity_id: str = Field(min_length=1, max_length=256)
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id(cls, value: str) -> str:
        """Reject raw SolidWorks selection syntax before it reaches the adapter."""
        return _validate_opaque_sketch_entity_id(value)


class SplitSketchEntityRequest(SketchRequest):
    """Split one active-sketch segment at one or two points."""

    entity_id: str = Field(min_length=1, max_length=256)
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)
    z: float = Field(default=0.0, ge=-100_000, le=100_000)
    closed: bool = False
    x2: float | None = Field(default=None, ge=-100_000, le=100_000)
    y2: float | None = Field(default=None, ge=-100_000, le=100_000)
    z2: float | None = Field(default=None, ge=-100_000, le=100_000)

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id(cls, value: str) -> str:
        return _validate_opaque_sketch_entity_id(value)

    @model_validator(mode="after")
    def validate_closed_points(self) -> "SplitSketchEntityRequest":
        if self.closed and (self.x2 is None or self.y2 is None):
            raise ValueError("x2 and y2 are required when closed is true")
        return self


class SketchCornerRequest(SketchRequest):
    """Round or bevel the corner formed by exactly two sketch entities."""

    entity_ids: list[str] = Field(min_length=2, max_length=2)

    @field_validator("entity_ids")
    @classmethod
    def validate_entity_ids(cls, values: list[str]) -> list[str]:
        if len(set(values)) != 2:
            raise ValueError("entity_ids must contain two distinct entities")
        for value in values:
            _validate_opaque_sketch_entity_id(value)
        return values


class FilletSketchCornerRequest(SketchCornerRequest):
    """Apply a constant-radius sketch fillet in millimetres."""

    radius: float = Field(gt=0, le=100_000)


class ChamferSketchCornerRequest(SketchCornerRequest):
    """Apply a distance/angle sketch chamfer in millimetres and degrees."""

    distance: float = Field(gt=0, le=100_000)
    angle_degrees: float = Field(gt=0, lt=180, default=45.0)


class ConvertSketchEntitiesRequest(SketchRequest):
    """Convert server-issued part edges into entities in an active sketch."""

    edge_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("edge_ids")
    @classmethod
    def validate_edge_ids(cls, values: list[str]) -> list[str]:
        """Accept only non-duplicated opaque topology IDs."""
        if len(set(values)) != len(values):
            raise ValueError("edge_ids must not contain duplicates")
        if any(any(ord(character) < 32 for character in value) for value in values):
            raise ValueError("edge_ids must not contain control characters")
        return values


class ExitSketchRequest(SketchRequest):
    """Input for ending the currently active session sketch."""


class CreateExtrusionRequest(SketchRequest):
    """Input for extruding the last exited sketch in a session."""

    depth: float = Field(gt=0, le=10_000)
    unit: Literal["mm", "m", "in"] = "mm"
    end_condition: Literal["Blind", "Through All", "Mid Plane"] = "Blind"
    reverse_direction: bool = False
    both_directions: bool = False
    merge_result: bool = True


class CreateCutExtrudeRequest(CreateExtrusionRequest):
    """Input for cutting material with the last exited session sketch."""


class CreateSheetMetalBaseFlangeRequest(SketchRequest):
    """Create a base flange from a session-owned exited closed sketch."""

    thickness: float = Field(gt=0, le=1_000)
    bend_radius: float = Field(ge=0, le=10_000)
    k_factor: float = Field(default=0.5, ge=0, le=1)
    unit: Literal["mm", "m", "in"] = "mm"


class CreateSheetMetalEdgeFlangeRequest(DocumentRequest):
    """Create one edge flange from a server-issued part edge."""

    edge_id: str = Field(min_length=1, max_length=128)
    length: float = Field(gt=0, le=100_000)
    angle_degrees: float = Field(default=90, gt=-360, le=360)
    bend_radius: float = Field(ge=0, le=10_000)
    k_factor: float = Field(default=0.5, ge=0, le=1)
    unit: Literal["mm", "m", "in"] = "mm"


class SetSheetMetalKFactorRequest(DocumentRequest):
    """Set and verify the active part's sheet-metal K factor."""

    k_factor: float = Field(ge=0, le=1)


class CreateShellRequest(DocumentRequest):
    """Shell a part after removing server-issued faces."""

    face_ids: list[str] = Field(min_length=1, max_length=100)
    thickness: float = Field(gt=0, le=10_000)
    unit: Literal["mm", "m", "in"] = "mm"
    outward: bool = False

    @field_validator("face_ids")
    @classmethod
    def validate_face_ids(cls, values: list[str]) -> list[str]:
        """Require unique opaque face IDs."""
        if len(set(values)) != len(values):
            raise ValueError("face_ids must not contain duplicates")
        return values


class CreateChamferRequest(DocumentRequest):
    """Create a 3D chamfer from current server-issued part edge IDs.

    ``distance`` is measured perpendicular to each selected edge.  In
    ``equal_distance`` mode both adjacent faces use that distance; in
    ``distance_angle`` mode ``angle_degrees`` controls the second face.  Edge
    IDs must come from the current ``solidworks_list_part_topology`` result.
    """

    edge_ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description=(
            "Opaque edge_id values returned by solidworks_list_part_topology "
            "with entity_types=['edge']; do not use Edge<1> names."
        ),
    )
    distance: float = Field(
        gt=0,
        le=10_000,
        description="Chamfer distance in the selected unit, strictly greater than zero.",
    )
    angle_degrees: float = Field(
        default=45,
        gt=0,
        lt=180,
        description=(
            "Second-face angle in degrees for distance_angle; retained as a "
            "diagnostic input for equal_distance."
        ),
    )
    chamfer_type: Literal["equal_distance", "distance_angle"] = Field(
        default="equal_distance",
        description=(
            "equal_distance applies the distance to both faces; "
            "distance_angle uses distance plus angle_degrees."
        ),
    )
    unit: Literal["mm", "m", "in"] = Field(
        default="mm", description="Unit for distance: mm, m, or in."
    )

    @field_validator("edge_ids")
    @classmethod
    def validate_edge_ids(cls, values: list[str]) -> list[str]:
        """Require non-empty, unique opaque edge capabilities."""
        if len(set(values)) != len(values):
            raise ValueError("edge_ids must not contain duplicates")
        if any(not value.strip() for value in values):
            raise ValueError("edge_ids must not contain empty values")
        if any(any(ord(character) < 32 for character in value) for value in values):
            raise ValueError("edge_ids must not contain control characters")
        return values

    @model_validator(mode="after")
    def validate_finite_dimensions(self) -> CreateChamferRequest:
        """Reject infinity/NaN before they reach a late-bound COM call."""
        if not isfinite(self.distance):
            raise ValueError("distance must be finite")
        if not isfinite(self.angle_degrees):
            raise ValueError("angle_degrees must be finite")
        return self


class CreateRibRequest(SketchRequest):
    """Create a rib from a session-owned exited open sketch."""

    thickness: float = Field(gt=0, le=10_000)
    draft_angle_degrees: float = Field(default=0, ge=-89, le=89)
    direction: Literal["normal", "parallel"] = "normal"
    unit: Literal["mm", "m", "in"] = "mm"


class CreateDomeRequest(DocumentRequest):
    """Create a dome on one server-issued part face."""

    face_id: str = Field(min_length=1, max_length=128)
    height: float = Field(gt=0, le=100_000)
    unit: Literal["mm", "m", "in"] = "mm"
    reverse_direction: bool = False


class CreateDraftRequest(DocumentRequest):
    """Apply one multi-face neutral-plane draft using current opaque face IDs."""

    face_ids: list[str] = Field(min_length=1, max_length=100)
    angle_degrees: float = Field(gt=0, lt=89)
    reverse_direction: bool = False
    edge_draft: bool = False

    @field_validator("face_ids")
    @classmethod
    def validate_face_ids(cls, values: list[str]) -> list[str]:
        """Require unique server-issued faces for deterministic selection."""
        if len(set(values)) != len(values):
            raise ValueError("face_ids must not contain duplicates")
        return values


class CreateThickenRequest(DocumentRequest):
    """Thicken one current surface face into a boss or cut feature."""

    face_id: str = Field(min_length=1, max_length=128)
    thickness: float = Field(gt=0, le=10_000)
    operation: Literal["boss", "cut"] = "boss"
    direction: Literal["side1", "side2", "both"] = "side1"
    fill_volume: bool = False
    merge_result: bool = True
    unit: Literal["mm", "m", "in"] = "mm"


class CreateWrapRequest(SketchRequest):
    """Wrap a session-owned exited sketch onto one current part face."""

    face_id: str = Field(min_length=1, max_length=128)
    operation: Literal["emboss", "engrave", "scribe"] = "emboss"
    thickness: float = Field(default=1, ge=0, le=10_000)
    reverse_direction: bool = False
    method: Literal["analytical", "spline_surface"] = "analytical"
    unit: Literal["mm", "m", "in"] = "mm"

    @model_validator(mode="after")
    def validate_scribe_thickness(self) -> CreateWrapRequest:
        """Keep zero-depth scribe independent from emboss and engrave depth."""
        if self.operation != "scribe" and self.thickness <= 0:
            raise ValueError(
                "emboss and engrave operations require thickness greater than zero"
            )
        return self


class CreateRevolveRequest(SketchRequest):
    """Add or cut material by revolving an exited sketch about its centreline."""

    angle_degrees: float = Field(gt=0, le=360)
    operation: Literal["boss", "cut"] = "boss"
    reverse_direction: bool = False
    both_directions: bool = False
    thin_feature: bool = False
    thin_thickness: float | None = Field(default=None, gt=0, le=10_000)
    merge_result: bool = True

    @model_validator(mode="after")
    def validate_thin_feature(self) -> CreateRevolveRequest:
        """Require a wall thickness when the requested revolve is thin-walled."""
        if self.thin_feature and self.thin_thickness is None:
            raise ValueError("thin_feature requires thin_thickness")
        return self


class CreateReferencePlaneRequest(DocumentRequest):
    """Create one offset reference plane from a fixed standard origin plane."""

    base_plane: Literal["Front", "Top", "Right"]
    offset: float = Field(gt=0, le=10_000)
    unit: Literal["mm", "m", "in"] = "mm"
    reverse_direction: bool = False


class GetReferenceAxisRequest(DocumentRequest):
    """Issue a capability for one fixed standard origin axis in the active part."""

    axis: Literal["X", "Y", "Z"]


class GetReferencePlaneRequest(DocumentRequest):
    """Issue a capability for one fixed standard origin plane in the active part."""

    plane: Literal["Front", "Top", "Right"]


class FeatureRequest(DocumentRequest):
    """Base input that targets one server-recorded feature reference."""

    feature_id: str = Field(min_length=1, max_length=256)


class CreateFeatureLinearPatternRequest(FeatureRequest):
    """Create a one-direction pattern along a standard coordinate direction."""

    direction: Literal["X", "Y", "Z"]
    instances: int = Field(ge=2, le=1_000)
    spacing: float = Field(gt=0, le=100_000)
    unit: Literal["mm", "m", "in"] = "mm"
    reverse_direction: bool = False


class CreateFeatureCircularPatternRequest(FeatureRequest):
    """Create a pattern about one current server-issued origin-axis reference."""

    reference_axis_id: str = Field(min_length=1, max_length=256)
    instances: int = Field(ge=2, le=1_000)
    angle_degrees: float = Field(gt=0, le=360)
    reverse_direction: bool = False


class MirrorFeatureRequest(FeatureRequest):
    """Mirror one feature about one current server-issued reference plane."""

    reference_plane_id: str = Field(min_length=1, max_length=256)


class CreateHoleWizardRequest(DocumentRequest):
    """Create one controlled ANSI Metric clearance Hole Wizard from a location sketch."""

    location_sketch_id: str = Field(min_length=1, max_length=256)
    standard: Literal["ansi_metric"] = "ansi_metric"
    hole_type: Literal["simple"] = "simple"
    diameter: float = Field(gt=0, le=100)
    depth: float = Field(gt=0, le=10_000)
    unit: Literal["mm", "m", "in"] = "mm"


class CreateTappedHoleRequest(DocumentRequest):
    """Create a standard ANSI Metric tapped Hole Wizard from a location sketch."""

    location_sketch_id: str = Field(min_length=1, max_length=256)
    thread_size: Literal[
        "M3x0.5",
        "M4x0.7",
        "M5x0.8",
        "M6x1.0",
        "M8x1.25",
        "M10x1.5",
        "M12x1.75",
        "M16x2.0",
    ]
    depth: float = Field(gt=0, le=10_000)
    unit: Literal["mm", "m", "in"] = "mm"
    through_all: bool = False


class CreateSweepRequest(DocumentRequest):
    """Add or cut material by sweeping an exited profile along an exited path."""

    profile_sketch_id: str = Field(min_length=1, max_length=256)
    path_sketch_id: str = Field(min_length=1, max_length=256)
    twist_along_path: bool = False
    twist_angle_degrees: float = Field(default=0, ge=-36_000, le=36_000)
    merge_result: bool = True
    operation: Literal["boss", "cut"] = "boss"

    @model_validator(mode="after")
    def validate_sketches_and_twist(self) -> CreateSweepRequest:
        """Keep the two server-owned sketch references unambiguous."""
        if self.profile_sketch_id == self.path_sketch_id:
            raise ValueError("profile_sketch_id and path_sketch_id must be distinct")
        if not self.twist_along_path and self.twist_angle_degrees != 0:
            raise ValueError("twist_angle_degrees requires twist_along_path to be true")
        return self


class CreateLoftRequest(DocumentRequest):
    """Add or cut material by lofting through exited profile sketches in order."""

    profile_sketch_ids: list[str] = Field(min_length=2, max_length=50)
    guide_sketch_ids: list[str] = Field(default_factory=list, max_length=50)
    start_tangent: Literal["none", "normal"] = "none"
    end_tangent: Literal["none", "normal"] = "none"
    merge_result: bool = True
    operation: Literal["boss", "cut"] = "boss"

    @model_validator(mode="after")
    def validate_sketch_references(self) -> CreateLoftRequest:
        """Reject duplicate or overlapping server-owned sketch references."""
        if any(not sketch_id for sketch_id in self.profile_sketch_ids):
            raise ValueError("profile_sketch_ids must contain non-empty references")
        if any(not sketch_id for sketch_id in self.guide_sketch_ids):
            raise ValueError("guide_sketch_ids must contain non-empty references")
        if len(set(self.profile_sketch_ids)) != len(self.profile_sketch_ids):
            raise ValueError("profile_sketch_ids must not contain duplicates")
        if len(set(self.guide_sketch_ids)) != len(self.guide_sketch_ids):
            raise ValueError("guide_sketch_ids must not contain duplicates")
        if set(self.profile_sketch_ids) & set(self.guide_sketch_ids):
            raise ValueError(
                "guide_sketch_ids must be distinct from profile_sketch_ids"
            )
        return self


class ExportFileRequest(DocumentRequest):
    """Input for exporting the active document to a controlled artifact directory."""

    artifact_name: str = Field(min_length=1, max_length=120)
    format_type: Literal["step", "stl"]

    @field_validator("artifact_name")
    @classmethod
    def validate_artifact_name(cls, value: str) -> str:
        """Reject caller-supplied paths; exports only accept a leaf file name."""
        if any(separator in value for separator in ("/", "\\", ":")) or value in {
            ".",
            "..",
        }:
            raise ValueError("artifact_name must be a simple file name, not a path")
        return value


class SheetMetalFlatStateRequest(DocumentRequest):
    """Explicitly flatten or refold an active sheet-metal part."""

    flattened: bool


class ExportFlatPatternDxfRequest(DocumentRequest):
    """Export a sheet-metal flat pattern DXF under the controlled artifact root."""

    artifact_name: str = Field(min_length=1, max_length=120)
    include_hidden_edges: bool = False
    include_bend_lines: bool = True
    include_sketches: bool = False
    merge_coplanar_faces: bool = True
    include_library_features: bool = False
    include_forming_tools: bool = False
    include_bounding_box: bool = False

    @field_validator("artifact_name")
    @classmethod
    def validate_artifact_name(cls, value: str) -> str:
        """Accept only a DXF leaf name; the workflow owns its output directory."""
        if any(separator in value for separator in ("/", "\\", ":")) or value in {
            ".",
            "..",
        }:
            raise ValueError("artifact_name must be a simple file name, not a path")
        suffix = Path(value).suffix.lower()
        if suffix and suffix != ".dxf":
            raise ValueError("artifact_name must use the .dxf extension")
        _validate_windows_leaf_name(value, field_name="artifact_name")
        return value


class SaveModelRequest(DocumentRequest):
    """Input for saving the active native SolidWorks document as an artifact."""

    artifact_name: str = Field(min_length=1, max_length=120)

    @field_validator("artifact_name")
    @classmethod
    def validate_artifact_name(cls, value: str) -> str:
        """Reject paths; the workflow appends the correct native extension."""
        if any(separator in value for separator in ("/", "\\", ":")) or value in {
            ".",
            "..",
        }:
            raise ValueError("artifact_name must be a simple file name, not a path")
        suffix = Path(value).suffix.lower()
        if suffix and suffix not in {".sldprt", ".sldasm", ".slddrw"}:
            raise ValueError("artifact_name must use a native SolidWorks extension")
        _validate_windows_leaf_name(value, field_name="artifact_name")
        return value


class PrepareTrustedMacroRequest(SessionRequest):
    """Select one administrator-provisioned VBA macro for explicit confirmation."""

    macro_id: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"
    )


class ExecuteTrustedMacroRequest(SessionRequest):
    """Execute a previously prepared macro only when its hash is reconfirmed."""

    confirmation_id: str = Field(min_length=1, max_length=128)
    macro_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
