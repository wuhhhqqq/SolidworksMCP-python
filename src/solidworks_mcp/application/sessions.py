"""In-memory, client-scoped workflow state without exposing COM objects."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from ..contracts.mvp import DocumentRef, EntityRef, SessionRef


class SessionLookupError(LookupError):
    """Raised when a session or document reference is not owned by the caller."""


class SessionCapacityError(RuntimeError):
    """Raised when the bounded in-memory session registry is full."""


class SketchStateError(RuntimeError):
    """Raised when an operation does not target the session's latest sketch state."""


class MateEntityReferenceError(RuntimeError):
    """Raised when a server-issued assembly mate entity is invalid or stale."""

    def __init__(self, reason: str) -> None:
        """Record the client-safe reason used by the workflow error mapper."""
        super().__init__(reason)
        self.reason = reason


class DrawingAnnotationReferenceError(RuntimeError):
    """Raised when a server-issued drawing annotation target is invalid or stale."""

    def __init__(self, reason: str) -> None:
        """Record the client-safe reason used by the workflow error mapper."""
        super().__init__(reason)
        self.reason = reason


class DrawingSourceViewReferenceError(RuntimeError):
    """Raised when a server-issued drawing source view is invalid or stale."""

    def __init__(self, reason: str) -> None:
        """Record the client-safe reason used by the workflow error mapper."""
        super().__init__(reason)
        self.reason = reason


class PartTopologyReferenceError(RuntimeError):
    """Raised when a server-issued part face or edge is invalid or stale."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AssemblyMateReferenceError(RuntimeError):
    """Raised when a server-issued assembly mate is invalid or stale."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DrawingBomReferenceError(RuntimeError):
    """Raised when a server-issued drawing BOM is invalid or stale."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DrawingRevisionTableReferenceError(RuntimeError):
    """Raised when a server-issued revision table is invalid or stale."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AssemblyMateEntityRef:
    """Internal persistent geometry reference for one active assembly revision."""

    entity_id: str
    assembly_document_id: str
    revision: int
    component_id: str
    entity_type: str
    display_name: str
    persistent_reference: str


@dataclass(frozen=True)
class DrawingAnnotationTargetRef:
    """Internal persistent target for a dimension, component, or edge in one drawing."""

    target_id: str
    drawing_document_id: str
    revision: int
    view_id: str
    target_type: str
    display_name: str
    persistent_reference: str


@dataclass(frozen=True)
class DrawingSourceViewRef:
    """Internal source-model binding for one current drawing view revision."""

    view_id: str
    drawing_document_id: str
    drawing_revision: int
    source_document_id: str
    source_document_revision: int


@dataclass(frozen=True)
class PartTopologyRef:
    """Internal persistent face or edge reference for one active part revision."""

    entity_id: str
    part_document_id: str
    revision: int
    entity_type: str
    display_name: str
    persistent_reference: str
    length_mm: float | None = None
    curve_identity: int | None = None


@dataclass(frozen=True)
class AssemblyMateRef:
    """Internal persistent reference for one existing active-assembly mate."""

    mate_id: str
    assembly_document_id: str
    revision: int
    display_name: str
    mate_type: str
    persistent_reference: str


@dataclass(frozen=True)
class DrawingBomRef:
    """Internal persistent reference for one current drawing BOM table."""

    bom_id: str
    drawing_document_id: str
    revision: int
    persistent_reference: str


@dataclass(frozen=True)
class DrawingRevisionTableRef:
    """Internal persistent reference for one current drawing revision table."""

    revision_table_id: str
    drawing_document_id: str
    revision: int
    persistent_reference: str


@dataclass(frozen=True)
class DimensionRef:
    """Internal native dimension name bound to one current document revision."""

    dimension_id: str
    document_id: str
    revision: int
    native_name: str


@dataclass
class ManagedSession:
    """Mutable server-side state for one workflow session."""

    ref: SessionRef
    documents: dict[str, DocumentRef] = field(default_factory=dict)
    active_document_id: str | None = None
    active_sketch: EntityRef | None = None
    last_exited_sketch: EntityRef | None = None
    exited_sketches: dict[str, EntityRef] = field(default_factory=dict)
    reference_planes: dict[str, EntityRef] = field(default_factory=dict)
    reference_axes: dict[str, EntityRef] = field(default_factory=dict)
    features: dict[str, EntityRef] = field(default_factory=dict)
    assembly_mate_entities: dict[str, AssemblyMateEntityRef] = field(
        default_factory=dict
    )
    invalidated_assembly_mate_entity_ids: dict[str, None] = field(default_factory=dict)
    drawing_annotation_targets: dict[str, DrawingAnnotationTargetRef] = field(
        default_factory=dict
    )
    invalidated_drawing_annotation_target_ids: dict[str, None] = field(
        default_factory=dict
    )
    drawing_source_views: dict[str, DrawingSourceViewRef] = field(default_factory=dict)
    invalidated_drawing_source_view_ids: dict[str, None] = field(default_factory=dict)
    part_topology: dict[str, PartTopologyRef] = field(default_factory=dict)
    invalidated_part_topology_ids: dict[str, None] = field(default_factory=dict)
    assembly_mates: dict[str, AssemblyMateRef] = field(default_factory=dict)
    invalidated_assembly_mate_ids: dict[str, None] = field(default_factory=dict)
    drawing_boms: dict[str, DrawingBomRef] = field(default_factory=dict)
    invalidated_drawing_bom_ids: dict[str, None] = field(default_factory=dict)
    drawing_revision_tables: dict[str, DrawingRevisionTableRef] = field(default_factory=dict)
    invalidated_drawing_revision_table_ids: dict[str, None] = field(default_factory=dict)
    dimensions: dict[str, DimensionRef] = field(default_factory=dict)
    invalidated_dimension_ids: dict[str, None] = field(default_factory=dict)
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionService:
    """Own and validate opaque document and entity references for MCP clients."""

    _MAX_INVALIDATED_ASSEMBLY_MATE_ENTITIES = 4_096
    _MAX_INVALIDATED_DRAWING_ANNOTATION_TARGETS = 4_096
    _MAX_INVALIDATED_DRAWING_SOURCE_VIEWS = 4_096
    _MAX_INVALIDATED_PART_TOPOLOGY = 4_096
    _MAX_INVALIDATED_ASSEMBLY_MATES = 4_096
    _MAX_INVALIDATED_DRAWING_BOMS = 4_096
    _MAX_INVALIDATED_DRAWING_REVISION_TABLES = 4_096
    _MAX_INVALIDATED_DIMENSIONS = 4_096

    def __init__(self, *, max_sessions: int = 64) -> None:
        """Initialize an empty, process-local session registry."""
        self.max_sessions = max_sessions
        self._sessions: dict[str, ManagedSession] = {}

    def create(self, *, connected: bool) -> ManagedSession:
        """Create a session with no document state yet."""
        if len(self._sessions) >= self.max_sessions:
            raise SessionCapacityError("workflow session capacity reached")
        ref = SessionRef(session_id=f"sw_{uuid4().hex}", connected=connected)
        session = ManagedSession(ref=ref)
        self._sessions[ref.session_id] = session
        return session

    def close(self, session_id: str) -> ManagedSession:
        """Remove a session and all server-side references owned by it."""
        try:
            return self._sessions.pop(session_id)
        except KeyError as exc:
            raise SessionLookupError("session was not found") from exc

    def require(self, session_id: str) -> ManagedSession:
        """Return a known session or raise a safe lookup error."""
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionLookupError("session was not found") from exc

    def require_document(
        self, session: ManagedSession, document_id: str
    ) -> DocumentRef:
        """Return a document only when it belongs to the supplied session."""
        try:
            return session.documents[document_id]
        except KeyError as exc:
            raise SessionLookupError("document was not found in this session") from exc

    def require_active_document(
        self, session: ManagedSession, document_id: str
    ) -> DocumentRef:
        """Ensure the action targets the adapter's current active document."""
        document = self.require_document(session, document_id)
        if session.active_document_id != document_id:
            raise SessionLookupError("document is not the active session document")
        return document

    def activate_document(
        self, session: ManagedSession, document_id: str
    ) -> DocumentRef:
        """Mark one already session-owned document as the active workflow target."""
        document = self.require_document(session, document_id)
        if session.active_sketch is not None:
            raise SketchStateError(
                "cannot activate another document while a sketch is open"
            )
        session.active_document_id = document_id
        return document

    def close_document(self, session: ManagedSession, document_id: str) -> DocumentRef:
        """Forget every session capability backed by a COM document that was closed.

        A saved document can be opened by more than one workflow session. Closing it
        in the one SolidWorks desktop instance invalidates every matching session
        reference, not merely the document ID that initiated the close.
        """
        document = self.require_active_document(session, document_id)
        for candidate_session, candidate_document_id in self._matching_documents(
            session, document_id
        ):
            self._invalidate_document_context(candidate_session, candidate_document_id)
            candidate_session.documents.pop(candidate_document_id, None)
            if candidate_session.active_document_id == candidate_document_id:
                candidate_session.active_document_id = None
        return document

    def register_document(
        self, session: ManagedSession, model_data: dict[str, Any]
    ) -> DocumentRef:
        """Create a stable document reference from adapter-returned metadata."""
        raw_type = str(model_data.get("type", "unknown")).strip().lower()
        document_type = {
            "part": "part",
            "assembly": "assembly",
            "drawing": "drawing",
        }.get(raw_type, "unknown")
        name = str(model_data.get("name") or model_data.get("title") or "Untitled")
        path_value = model_data.get("path")
        document = DocumentRef(
            document_id=f"doc_{uuid4().hex}",
            name=name,
            document_type=cast(
                Literal["part", "assembly", "drawing", "unknown"], document_type
            ),
            path=str(path_value) if path_value else None,
            configuration=(
                str(model_data["configuration"])
                if model_data.get("configuration") is not None
                else None
            ),
            revision=1,
        )
        session.documents[document.document_id] = document
        session.active_document_id = document.document_id
        session.active_sketch = None
        session.last_exited_sketch = None
        session.exited_sketches.clear()
        session.reference_planes.clear()
        session.reference_axes.clear()
        session.features.clear()
        return document

    def record_active_sketch(
        self,
        session: ManagedSession,
        document_id: str,
        sketch_data: dict[str, Any],
    ) -> EntityRef:
        """Track the one sketch that is currently active in SolidWorks."""
        document = self.require_active_document(session, document_id)
        raw_persistent_id = (
            sketch_data.get("id")
            or sketch_data.get("sketch_name")
            or sketch_data.get("name")
            or sketch_data.get("value")
        )
        if raw_persistent_id is None or not str(raw_persistent_id).strip():
            raise SketchStateError("adapter did not return a sketch identifier")
        persistent_id = str(raw_persistent_id)
        entity = EntityRef(
            document_id=document.document_id,
            entity_type="sketch",
            persistent_id=persistent_id,
            display_name=str(sketch_data.get("name") or persistent_id),
            revision=document.revision,
        )
        session.active_sketch = entity
        session.last_exited_sketch = None
        return entity

    def require_active_sketch(
        self, session: ManagedSession, document_id: str, sketch_id: str
    ) -> EntityRef:
        """Ensure geometry is added to the sketch currently active in SolidWorks."""
        self.require_active_document(session, document_id)
        sketch = session.active_sketch
        if sketch is None or sketch.persistent_id != sketch_id:
            raise SketchStateError("the requested sketch is not currently active")
        return sketch

    def exit_sketch(
        self, session: ManagedSession, document_id: str, sketch_id: str
    ) -> EntityRef:
        """Move the active sketch into the only state eligible for extrusion."""
        sketch = self.require_active_sketch(session, document_id, sketch_id)
        session.active_sketch = None
        session.last_exited_sketch = sketch
        session.exited_sketches[sketch.persistent_id] = sketch
        return sketch

    def require_extrusion_sketch(
        self, session: ManagedSession, document_id: str, sketch_id: str
    ) -> EntityRef:
        """Require the last exited sketch because the adapter operates on active UI state."""
        document = self.require_active_document(session, document_id)
        sketch = session.last_exited_sketch
        if (
            sketch is None
            or sketch.persistent_id != sketch_id
            or sketch.revision != document.revision
        ):
            raise SketchStateError("the sketch is not ready for extrusion")
        return sketch

    def require_exited_sketch(
        self, session: ManagedSession, document_id: str, sketch_id: str
    ) -> EntityRef:
        """Resolve a previously exited sketch owned by this session and document."""
        document = self.require_active_document(session, document_id)
        sketch = session.exited_sketches.get(sketch_id)
        if (
            sketch is None
            or sketch.document_id != document_id
            or sketch.revision != document.revision
        ):
            raise SketchStateError(
                "the requested sketch was not exited in this session"
            )
        return sketch

    def record_reference_plane(
        self,
        session: ManagedSession,
        document_id: str,
        reference_data: dict[str, Any],
    ) -> EntityRef:
        """Store an opaque current-revision reference-plane capability."""
        document = self.require_active_document(session, document_id)
        display_name = str(
            reference_data.get("name") or reference_data.get("display_name") or ""
        ).strip()
        if not display_name:
            raise SketchStateError("adapter did not return a reference plane name")
        reference = EntityRef(
            document_id=document_id,
            entity_type="feature",
            persistent_id=f"ref_{uuid4().hex}",
            display_name=display_name,
            revision=document.revision,
        )
        session.reference_planes[reference.persistent_id] = reference
        return reference

    def require_reference_plane(
        self, session: ManagedSession, document_id: str, reference_plane_id: str
    ) -> EntityRef:
        """Resolve one current opaque reference plane without exposing COM selection."""
        document = self.require_active_document(session, document_id)
        reference = session.reference_planes.get(reference_plane_id)
        if (
            reference is None
            or reference.document_id != document_id
            or reference.revision != document.revision
        ):
            raise SketchStateError("the requested reference plane is not current")
        return reference

    def record_reference_axis(
        self,
        session: ManagedSession,
        document_id: str,
        axis_data: dict[str, Any],
    ) -> EntityRef:
        """Store one current standard-origin-axis capability."""
        document = self.require_active_document(session, document_id)
        display_name = str(
            axis_data.get("name") or axis_data.get("display_name") or ""
        ).strip()
        if not display_name:
            raise SketchStateError("adapter did not return a reference axis name")
        axis = EntityRef(
            document_id=document_id,
            entity_type="feature",
            persistent_id=f"axis_{uuid4().hex}",
            display_name=display_name,
            revision=document.revision,
        )
        session.reference_axes[axis.persistent_id] = axis
        return axis

    def require_reference_axis(
        self, session: ManagedSession, document_id: str, reference_axis_id: str
    ) -> EntityRef:
        """Resolve one current opaque origin-axis capability."""
        document = self.require_active_document(session, document_id)
        axis = session.reference_axes.get(reference_axis_id)
        if (
            axis is None
            or axis.document_id != document_id
            or axis.revision != document.revision
        ):
            raise SketchStateError("the requested reference axis is not current")
        return axis

    def record_feature(
        self,
        session: ManagedSession,
        document_id: str,
        feature_data: dict[str, Any],
    ) -> EntityRef:
        """Track one freshly created feature for one current document revision."""
        document = self.require_active_document(session, document_id)
        persistent_id = str(feature_data.get("id") or "").strip()
        display_name = str(feature_data.get("name") or "").strip()
        if not persistent_id or not display_name:
            raise SketchStateError("adapter did not return a usable feature reference")
        feature = EntityRef(
            document_id=document_id,
            entity_type="feature",
            persistent_id=persistent_id,
            display_name=display_name,
            revision=document.revision,
        )
        session.features[feature.persistent_id] = feature
        return feature

    def require_feature(
        self, session: ManagedSession, document_id: str, feature_id: str
    ) -> EntityRef:
        """Resolve one current server-recorded feature without COM name input."""
        document = self.require_active_document(session, document_id)
        feature = session.features.get(feature_id)
        if (
            feature is None
            or feature.document_id != document_id
            or feature.revision != document.revision
        ):
            raise SketchStateError("the requested feature is not current")
        return feature

    def begin_feature_listing(
        self, session: ManagedSession, document_id: str
    ) -> None:
        """Expire prior feature capabilities before issuing a refreshed feature list."""
        self.require_active_document(session, document_id)
        self._invalidate_features(session, document_id)

    def issue_listed_feature(
        self, session: ManagedSession, document_id: str, display_name: str
    ) -> EntityRef:
        """Issue an opaque current-revision reference for one listed feature."""
        document = self.require_active_document(session, document_id)
        feature = EntityRef(
            document_id=document_id,
            entity_type="feature",
            persistent_id=f"feat_{uuid4().hex}",
            display_name=display_name,
            revision=document.revision,
        )
        session.features[feature.persistent_id] = feature
        return feature

    def bump_document_revision(
        self, session: ManagedSession, document_id: str
    ) -> DocumentRef:
        """Increment matching document revisions after a successful mutation."""
        document = self.require_document(session, document_id)
        for candidate_session, candidate_document_id in self._matching_documents(
            session, document_id
        ):
            candidate_document = candidate_session.documents[candidate_document_id]
            self._invalidate_assembly_mate_entities(
                candidate_session, candidate_document_id
            )
            self._invalidate_drawing_annotation_targets(
                candidate_session, candidate_document_id
            )
            self._invalidate_drawing_source_views(
                candidate_session, candidate_document_id
            )
            self._invalidate_part_topology(candidate_session, candidate_document_id)
            self._invalidate_assembly_mates(candidate_session, candidate_document_id)
            self._invalidate_drawing_boms(candidate_session, candidate_document_id)
            self._invalidate_drawing_revision_tables(candidate_session, candidate_document_id)
            self._invalidate_exited_sketches(candidate_session, candidate_document_id)
            self._invalidate_reference_planes(candidate_session, candidate_document_id)
            self._invalidate_reference_axes(candidate_session, candidate_document_id)
            self._invalidate_features(candidate_session, candidate_document_id)
            self._invalidate_dimensions(candidate_session, candidate_document_id)
            candidate_session.documents[candidate_document_id] = (
                candidate_document.model_copy(
                    update={"revision": candidate_document.revision + 1}
                )
            )
        return session.documents[document.document_id]

    def activate_configuration(
        self, session: ManagedSession, document_id: str, configuration_name: str
    ) -> DocumentRef:
        """Record a verified configuration switch as a new document revision."""
        document = self.require_active_document(session, document_id)
        for candidate_session, candidate_document_id in self._matching_documents(
            session, document_id
        ):
            candidate_document = candidate_session.documents[candidate_document_id]
            self._invalidate_assembly_mate_entities(
                candidate_session, candidate_document_id
            )
            self._invalidate_drawing_annotation_targets(
                candidate_session, candidate_document_id
            )
            self._invalidate_drawing_source_views(
                candidate_session, candidate_document_id
            )
            self._invalidate_part_topology(candidate_session, candidate_document_id)
            self._invalidate_assembly_mates(candidate_session, candidate_document_id)
            self._invalidate_drawing_boms(candidate_session, candidate_document_id)
            self._invalidate_drawing_revision_tables(candidate_session, candidate_document_id)
            self._invalidate_exited_sketches(candidate_session, candidate_document_id)
            self._invalidate_reference_planes(candidate_session, candidate_document_id)
            self._invalidate_reference_axes(candidate_session, candidate_document_id)
            self._invalidate_features(candidate_session, candidate_document_id)
            self._invalidate_dimensions(candidate_session, candidate_document_id)
            candidate_session.documents[candidate_document_id] = (
                candidate_document.model_copy(
                    update={
                        "configuration": configuration_name,
                        "revision": candidate_document.revision + 1,
                    }
                )
            )
        return session.documents[document.document_id]

    def record_native_save(
        self, session: ManagedSession, document_id: str, path: str
    ) -> DocumentRef:
        """Track a successful native save-as as a new document revision."""
        document = self.require_document(session, document_id)
        self._invalidate_assembly_mate_entities(session, document_id)
        self._invalidate_drawing_annotation_targets(session, document_id)
        self._invalidate_drawing_source_views(session, document_id)
        self._invalidate_part_topology(session, document_id)
        self._invalidate_assembly_mates(session, document_id)
        self._invalidate_drawing_boms(session, document_id)
        self._invalidate_drawing_revision_tables(session, document_id)
        self._invalidate_exited_sketches(session, document_id)
        self._invalidate_reference_planes(session, document_id)
        self._invalidate_reference_axes(session, document_id)
        self._invalidate_features(session, document_id)
        self._invalidate_dimensions(session, document_id)
        updated = document.model_copy(
            update={
                "name": Path(path).name,
                "path": path,
                "revision": document.revision + 1,
            }
        )
        session.documents[document_id] = updated
        session.active_document_id = document_id
        return updated

    def begin_dimension_listing(
        self, session: ManagedSession, document_id: str
    ) -> None:
        """Replace a document's prior dimension capabilities before fresh discovery."""
        self.require_active_document(session, document_id)
        self._invalidate_dimensions(session, document_id)

    def issue_dimension(
        self,
        session: ManagedSession,
        document_id: str,
        *,
        native_name: str,
    ) -> DimensionRef:
        """Store a native dimension name privately and return an opaque identifier."""
        document = self.require_active_document(session, document_id)
        dimension = DimensionRef(
            dimension_id=f"dim_{uuid4().hex}",
            document_id=document.document_id,
            revision=document.revision,
            native_name=native_name,
        )
        session.dimensions[dimension.dimension_id] = dimension
        return dimension

    def resolve_dimension(
        self, session: ManagedSession, document_id: str, dimension_id: str
    ) -> DimensionRef:
        """Resolve one current dimension capability without accepting native names."""
        document = self.require_active_document(session, document_id)
        dimension = session.dimensions.get(dimension_id)
        if (
            dimension is None
            or dimension.document_id != document_id
            or dimension.revision != document.revision
        ):
            raise SketchStateError("the requested dimension is not current")
        return dimension

    @classmethod
    def _invalidate_document_context(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Remove all state that can no longer target one closed document."""
        if (
            session.active_sketch is not None
            and session.active_sketch.document_id == document_id
        ):
            session.active_sketch = None
        cls._invalidate_assembly_mate_entities(session, document_id)
        cls._invalidate_drawing_annotation_targets(session, document_id)
        cls._invalidate_drawing_source_views(session, document_id)
        cls._invalidate_part_topology(session, document_id)
        cls._invalidate_assembly_mates(session, document_id)
        cls._invalidate_drawing_boms(session, document_id)
        cls._invalidate_drawing_revision_tables(session, document_id)
        cls._invalidate_exited_sketches(session, document_id)
        cls._invalidate_reference_planes(session, document_id)
        cls._invalidate_reference_axes(session, document_id)
        cls._invalidate_features(session, document_id)
        cls._invalidate_dimensions(session, document_id)

    def issue_assembly_mate_entity(
        self,
        session: ManagedSession,
        document_id: str,
        *,
        component_id: str,
        entity_type: str,
        display_name: str,
        persistent_reference: str,
    ) -> AssemblyMateEntityRef:
        """Store a COM-free geometric reference and issue its opaque client ID."""
        document = self.require_active_document(session, document_id)
        entity = AssemblyMateEntityRef(
            entity_id=f"mate_entity_{uuid4().hex}",
            assembly_document_id=document.document_id,
            revision=document.revision,
            component_id=component_id,
            entity_type=entity_type,
            display_name=display_name,
            persistent_reference=persistent_reference,
        )
        session.assembly_mate_entities[entity.entity_id] = entity
        return entity

    def begin_assembly_mate_entity_listing(
        self, session: ManagedSession, document_id: str
    ) -> None:
        """Replace a document's prior issued mate IDs before a fresh listing."""
        self.require_active_document(session, document_id)
        self._invalidate_assembly_mate_entities(session, document_id)

    def resolve_assembly_mate_entity(
        self,
        session: ManagedSession,
        document_id: str,
        entity_id: str,
    ) -> AssemblyMateEntityRef:
        """Resolve an entity only for the exact active assembly document revision."""
        document = self.require_active_document(session, document_id)
        if entity_id in session.invalidated_assembly_mate_entity_ids:
            raise MateEntityReferenceError("stale")
        try:
            entity = session.assembly_mate_entities[entity_id]
        except KeyError as exc:
            raise MateEntityReferenceError("not_found") from exc
        if entity.assembly_document_id != document.document_id:
            raise MateEntityReferenceError("wrong_document")
        if entity.revision != document.revision:
            raise MateEntityReferenceError("stale")
        return entity

    def issue_part_topology(
        self,
        session: ManagedSession,
        document_id: str,
        *,
        entity_type: str,
        display_name: str,
        persistent_reference: str,
        length_mm: float | None = None,
        curve_identity: int | None = None,
    ) -> PartTopologyRef:
        """Store one COM-free active-part topology reference and issue an opaque ID."""
        document = self.require_active_document(session, document_id)
        entity = PartTopologyRef(
            entity_id=f"part_topology_{uuid4().hex}",
            part_document_id=document.document_id,
            revision=document.revision,
            entity_type=entity_type,
            display_name=display_name,
            persistent_reference=persistent_reference,
            length_mm=length_mm,
            curve_identity=curve_identity,
        )
        session.part_topology[entity.entity_id] = entity
        return entity

    def begin_part_topology_listing(
        self, session: ManagedSession, document_id: str
    ) -> None:
        """Replace prior part topology capabilities with a fresh bounded listing."""
        self.require_active_document(session, document_id)
        self._invalidate_part_topology(session, document_id)

    def resolve_part_topology(
        self, session: ManagedSession, document_id: str, entity_id: str
    ) -> PartTopologyRef:
        """Resolve one current face or edge only for the exact active part revision."""
        document = self.require_active_document(session, document_id)
        if entity_id in session.invalidated_part_topology_ids:
            raise PartTopologyReferenceError("stale")
        try:
            entity = session.part_topology[entity_id]
        except KeyError as exc:
            raise PartTopologyReferenceError("not_found") from exc
        if entity.part_document_id != document.document_id:
            raise PartTopologyReferenceError("wrong_document")
        if entity.revision != document.revision:
            raise PartTopologyReferenceError("stale")
        return entity

    def issue_assembly_mate(
        self,
        session: ManagedSession,
        document_id: str,
        *,
        display_name: str,
        mate_type: str,
        persistent_reference: str,
    ) -> AssemblyMateRef:
        """Store one COM-free existing-mate reference and issue an opaque ID."""
        document = self.require_active_document(session, document_id)
        mate = AssemblyMateRef(
            mate_id=f"assembly_mate_{uuid4().hex}",
            assembly_document_id=document.document_id,
            revision=document.revision,
            display_name=display_name,
            mate_type=mate_type,
            persistent_reference=persistent_reference,
        )
        session.assembly_mates[mate.mate_id] = mate
        return mate

    def begin_assembly_mate_listing(
        self, session: ManagedSession, document_id: str
    ) -> None:
        """Replace prior existing-mate capabilities with a fresh listing."""
        self.require_active_document(session, document_id)
        self._invalidate_assembly_mates(session, document_id)

    def resolve_assembly_mate(
        self, session: ManagedSession, document_id: str, mate_id: str
    ) -> AssemblyMateRef:
        """Resolve a current existing mate only for its active assembly revision."""
        document = self.require_active_document(session, document_id)
        if mate_id in session.invalidated_assembly_mate_ids:
            raise AssemblyMateReferenceError("stale")
        try:
            mate = session.assembly_mates[mate_id]
        except KeyError as exc:
            raise AssemblyMateReferenceError("not_found") from exc
        if mate.assembly_document_id != document.document_id:
            raise AssemblyMateReferenceError("wrong_document")
        if mate.revision != document.revision:
            raise AssemblyMateReferenceError("stale")
        return mate

    def record_drawing_bom(
        self, session: ManagedSession, document_id: str, persistent_reference: str
    ) -> DrawingBomRef:
        """Store the COM-only BOM token returned from a successful insertion."""
        document = self.require_active_document(session, document_id)
        bom = DrawingBomRef(
            bom_id=f"drawing_bom_{uuid4().hex}",
            drawing_document_id=document.document_id,
            revision=document.revision,
            persistent_reference=persistent_reference,
        )
        session.drawing_boms[bom.bom_id] = bom
        return bom

    def resolve_drawing_bom(
        self, session: ManagedSession, document_id: str, bom_id: str
    ) -> DrawingBomRef:
        """Resolve one current server-issued BOM table capability."""
        document = self.require_active_document(session, document_id)
        if bom_id in session.invalidated_drawing_bom_ids:
            raise DrawingBomReferenceError("stale")
        try:
            bom = session.drawing_boms[bom_id]
        except KeyError as exc:
            raise DrawingBomReferenceError("not_found") from exc
        if bom.drawing_document_id != document.document_id:
            raise DrawingBomReferenceError("wrong_document")
        if bom.revision != document.revision:
            raise DrawingBomReferenceError("stale")
        return bom

    def record_drawing_revision_table(
        self, session: ManagedSession, document_id: str, persistent_reference: str
    ) -> DrawingRevisionTableRef:
        """Store the COM-only revision-table token and issue an opaque ID."""
        document = self.require_active_document(session, document_id)
        table = DrawingRevisionTableRef(
            revision_table_id=f"drawing_revision_table_{uuid4().hex}",
            drawing_document_id=document.document_id,
            revision=document.revision,
            persistent_reference=persistent_reference,
        )
        session.drawing_revision_tables[table.revision_table_id] = table
        return table

    def resolve_drawing_revision_table(
        self, session: ManagedSession, document_id: str, revision_table_id: str
    ) -> DrawingRevisionTableRef:
        """Resolve a current revision table capability for the active drawing."""
        document = self.require_active_document(session, document_id)
        if revision_table_id in session.invalidated_drawing_revision_table_ids:
            raise DrawingRevisionTableReferenceError("stale")
        try:
            table = session.drawing_revision_tables[revision_table_id]
        except KeyError as exc:
            raise DrawingRevisionTableReferenceError("not_found") from exc
        if table.drawing_document_id != document.document_id:
            raise DrawingRevisionTableReferenceError("wrong_document")
        if table.revision != document.revision:
            raise DrawingRevisionTableReferenceError("stale")
        return table

    def record_drawing_source_view(
        self,
        session: ManagedSession,
        drawing_document_id: str,
        source_document_id: str,
        view_id: str,
    ) -> DrawingSourceViewRef:
        """Bind a newly created drawing view to its source document revision."""
        drawing_document = self.require_active_document(session, drawing_document_id)
        source_document = self.require_document(session, source_document_id)
        view = DrawingSourceViewRef(
            view_id=view_id,
            drawing_document_id=drawing_document.document_id,
            drawing_revision=drawing_document.revision,
            source_document_id=source_document.document_id,
            source_document_revision=source_document.revision,
        )
        session.drawing_source_views[view_id] = view
        return view

    def resolve_drawing_source_view(
        self,
        session: ManagedSession,
        drawing_document_id: str,
        source_document_id: str,
        view_id: str,
    ) -> DrawingSourceViewRef:
        """Resolve a current drawing view only for its original saved source model."""
        drawing_document = self.require_active_document(session, drawing_document_id)
        source_document = self.require_document(session, source_document_id)
        if view_id in session.invalidated_drawing_source_view_ids:
            raise DrawingSourceViewReferenceError("stale")
        try:
            view = session.drawing_source_views[view_id]
        except KeyError as exc:
            raise DrawingSourceViewReferenceError("not_found") from exc
        if view.drawing_document_id != drawing_document.document_id:
            raise DrawingSourceViewReferenceError("wrong_document")
        if view.drawing_revision != drawing_document.revision:
            raise DrawingSourceViewReferenceError("stale")
        if view.source_document_id != source_document.document_id:
            raise DrawingSourceViewReferenceError("wrong_source")
        if view.source_document_revision != source_document.revision:
            raise DrawingSourceViewReferenceError("stale")
        return view

    def issue_drawing_annotation_target(
        self,
        session: ManagedSession,
        document_id: str,
        *,
        view_id: str,
        target_type: str,
        display_name: str,
        persistent_reference: str,
    ) -> DrawingAnnotationTargetRef:
        """Store a COM-free target reference and issue its opaque client ID."""
        document = self.require_active_document(session, document_id)
        target = DrawingAnnotationTargetRef(
            target_id=f"drawing_target_{uuid4().hex}",
            drawing_document_id=document.document_id,
            revision=document.revision,
            view_id=view_id,
            target_type=target_type,
            display_name=display_name,
            persistent_reference=persistent_reference,
        )
        session.drawing_annotation_targets[target.target_id] = target
        return target

    def begin_drawing_annotation_target_listing(
        self, session: ManagedSession, document_id: str
    ) -> None:
        """Replace an active drawing's prior annotation targets before fresh listing."""
        self.require_active_document(session, document_id)
        self._invalidate_drawing_annotation_targets(session, document_id)

    def resolve_drawing_annotation_target(
        self,
        session: ManagedSession,
        document_id: str,
        target_id: str,
    ) -> DrawingAnnotationTargetRef:
        """Resolve a target only for the exact active drawing document revision."""
        document = self.require_active_document(session, document_id)
        if target_id in session.invalidated_drawing_annotation_target_ids:
            raise DrawingAnnotationReferenceError("stale")
        try:
            target = session.drawing_annotation_targets[target_id]
        except KeyError as exc:
            raise DrawingAnnotationReferenceError("not_found") from exc
        if target.drawing_document_id != document.document_id:
            raise DrawingAnnotationReferenceError("wrong_document")
        if target.revision != document.revision:
            raise DrawingAnnotationReferenceError("stale")
        return target

    @classmethod
    def _invalidate_assembly_mate_entities(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Expire references whose geometry may have changed after a mutation."""
        invalidated_ids = [
            entity_id
            for entity_id, entity in session.assembly_mate_entities.items()
            if entity.assembly_document_id == document_id
        ]
        for entity_id in invalidated_ids:
            session.assembly_mate_entities.pop(entity_id, None)
            session.invalidated_assembly_mate_entity_ids[entity_id] = None
        while (
            len(session.invalidated_assembly_mate_entity_ids)
            > cls._MAX_INVALIDATED_ASSEMBLY_MATE_ENTITIES
        ):
            oldest_entity_id = next(iter(session.invalidated_assembly_mate_entity_ids))
            session.invalidated_assembly_mate_entity_ids.pop(oldest_entity_id)

    @classmethod
    def _invalidate_drawing_annotation_targets(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Expire drawing geometry references when the drawing revision changes."""
        invalidated_ids = [
            target_id
            for target_id, target in session.drawing_annotation_targets.items()
            if target.drawing_document_id == document_id
        ]
        for target_id in invalidated_ids:
            session.drawing_annotation_targets.pop(target_id, None)
            session.invalidated_drawing_annotation_target_ids[target_id] = None
        while (
            len(session.invalidated_drawing_annotation_target_ids)
            > cls._MAX_INVALIDATED_DRAWING_ANNOTATION_TARGETS
        ):
            oldest_target_id = next(
                iter(session.invalidated_drawing_annotation_target_ids)
            )
            session.invalidated_drawing_annotation_target_ids.pop(oldest_target_id)

    @classmethod
    def _invalidate_drawing_source_views(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Expire source-view bindings when their drawing revision changes."""
        invalidated_ids = [
            view_id
            for view_id, view in session.drawing_source_views.items()
            if view.drawing_document_id == document_id
        ]
        for view_id in invalidated_ids:
            session.drawing_source_views.pop(view_id, None)
            session.invalidated_drawing_source_view_ids[view_id] = None
        while (
            len(session.invalidated_drawing_source_view_ids)
            > cls._MAX_INVALIDATED_DRAWING_SOURCE_VIEWS
        ):
            oldest_view_id = next(iter(session.invalidated_drawing_source_view_ids))
            session.invalidated_drawing_source_view_ids.pop(oldest_view_id)

    @classmethod
    def _invalidate_part_topology(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Expire part geometry capabilities after any part-model mutation."""
        invalidated_ids = [
            entity_id
            for entity_id, entity in session.part_topology.items()
            if entity.part_document_id == document_id
        ]
        for entity_id in invalidated_ids:
            session.part_topology.pop(entity_id, None)
            session.invalidated_part_topology_ids[entity_id] = None
        while (
            len(session.invalidated_part_topology_ids)
            > cls._MAX_INVALIDATED_PART_TOPOLOGY
        ):
            oldest_entity_id = next(iter(session.invalidated_part_topology_ids))
            session.invalidated_part_topology_ids.pop(oldest_entity_id)

    @classmethod
    def _invalidate_assembly_mates(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Expire existing-mate capabilities after an assembly mutation."""
        invalidated_ids = [
            mate_id
            for mate_id, mate in session.assembly_mates.items()
            if mate.assembly_document_id == document_id
        ]
        for mate_id in invalidated_ids:
            session.assembly_mates.pop(mate_id, None)
            session.invalidated_assembly_mate_ids[mate_id] = None
        while (
            len(session.invalidated_assembly_mate_ids)
            > cls._MAX_INVALIDATED_ASSEMBLY_MATES
        ):
            oldest_mate_id = next(iter(session.invalidated_assembly_mate_ids))
            session.invalidated_assembly_mate_ids.pop(oldest_mate_id)

    @classmethod
    def _invalidate_drawing_boms(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Expire BOM capabilities after a drawing mutation."""
        invalidated_ids = [
            bom_id
            for bom_id, bom in session.drawing_boms.items()
            if bom.drawing_document_id == document_id
        ]
        for bom_id in invalidated_ids:
            session.drawing_boms.pop(bom_id, None)
            session.invalidated_drawing_bom_ids[bom_id] = None
        while (
            len(session.invalidated_drawing_bom_ids) > cls._MAX_INVALIDATED_DRAWING_BOMS
        ):
            oldest_bom_id = next(iter(session.invalidated_drawing_bom_ids))
            session.invalidated_drawing_bom_ids.pop(oldest_bom_id)

    @classmethod
    def _invalidate_drawing_revision_tables(
        cls, session: ManagedSession, document_id: str
    ) -> None:
        """Expire revision-table capabilities after a drawing mutation."""
        invalidated_ids = [
            table_id
            for table_id, table in session.drawing_revision_tables.items()
            if table.drawing_document_id == document_id
        ]
        for table_id in invalidated_ids:
            session.drawing_revision_tables.pop(table_id, None)
            session.invalidated_drawing_revision_table_ids[table_id] = None
        while (
            len(session.invalidated_drawing_revision_table_ids)
            > cls._MAX_INVALIDATED_DRAWING_REVISION_TABLES
        ):
            oldest_table_id = next(iter(session.invalidated_drawing_revision_table_ids))
            session.invalidated_drawing_revision_table_ids.pop(oldest_table_id)

    def _matching_documents(
        self, session: ManagedSession, document_id: str
    ) -> list[tuple[ManagedSession, str]]:
        """Find session documents backed by the same saved native model identity."""
        document = self.require_document(session, document_id)
        identity = self._document_identity(document)
        matches = [(session, document_id)]
        if identity is None:
            return matches
        for candidate_session in self._sessions.values():
            if candidate_session is session:
                continue
            for (
                candidate_document_id,
                candidate_document,
            ) in candidate_session.documents.items():
                if self._document_identity(candidate_document) == identity:
                    matches.append((candidate_session, candidate_document_id))
        return matches

    @staticmethod
    def _document_identity(document: DocumentRef) -> str | None:
        """Return one normalized identity for a saved model shared by sessions."""
        if not document.path:
            return None
        try:
            return str(Path(document.path).resolve(strict=False)).casefold()
        except OSError:
            return str(document.path).replace("/", "\\").casefold()

    def has_foreign_active_sketch(self, session: ManagedSession) -> bool:
        """Return whether another session currently owns SolidWorks sketch edit mode."""
        return any(
            candidate is not session and candidate.active_sketch is not None
            for candidate in self._sessions.values()
        )

    def has_active_sketch(self) -> bool:
        """Return whether any workflow session currently owns sketch edit mode."""
        return any(
            session.active_sketch is not None for session in self._sessions.values()
        )

    def invalidate_active_context(self, session: ManagedSession) -> None:
        """Drop UI-state assumptions after an arbitrary VBA macro has run."""
        session.active_document_id = None
        session.active_sketch = None
        session.last_exited_sketch = None
        session.exited_sketches.clear()
        session.reference_planes.clear()
        session.reference_axes.clear()
        session.features.clear()
        session.part_topology.clear()
        session.invalidated_part_topology_ids.clear()
        session.assembly_mates.clear()
        session.invalidated_assembly_mate_ids.clear()
        session.drawing_boms.clear()
        session.invalidated_drawing_bom_ids.clear()

    @staticmethod
    def _invalidate_exited_sketches(session: ManagedSession, document_id: str) -> None:
        """Expire sketch capabilities after a document mutation changes its revision."""
        if (
            session.last_exited_sketch is not None
            and session.last_exited_sketch.document_id == document_id
        ):
            session.last_exited_sketch = None
        session.exited_sketches = {
            sketch_id: sketch
            for sketch_id, sketch in session.exited_sketches.items()
            if sketch.document_id != document_id
        }

    @staticmethod
    def _invalidate_reference_planes(session: ManagedSession, document_id: str) -> None:
        """Expire reference-plane capabilities after their document mutates."""
        session.reference_planes = {
            reference_id: reference
            for reference_id, reference in session.reference_planes.items()
            if reference.document_id != document_id
        }

    @staticmethod
    def _invalidate_reference_axes(session: ManagedSession, document_id: str) -> None:
        """Expire origin-axis capabilities after their document mutates."""
        session.reference_axes = {
            axis_id: axis
            for axis_id, axis in session.reference_axes.items()
            if axis.document_id != document_id
        }

    @staticmethod
    def _invalidate_features(session: ManagedSession, document_id: str) -> None:
        """Expire feature capabilities after their document mutates."""
        session.features = {
            feature_id: feature
            for feature_id, feature in session.features.items()
            if feature.document_id != document_id
        }

    @classmethod
    def _invalidate_dimensions(cls, session: ManagedSession, document_id: str) -> None:
        """Expire opaque dimension capabilities after their document mutates."""
        invalidated_ids = [
            dimension_id
            for dimension_id, dimension in session.dimensions.items()
            if dimension.document_id == document_id
        ]
        for dimension_id in invalidated_ids:
            session.dimensions.pop(dimension_id, None)
            session.invalidated_dimension_ids[dimension_id] = None
        while len(session.invalidated_dimension_ids) > cls._MAX_INVALIDATED_DIMENSIONS:
            oldest_dimension_id = next(iter(session.invalidated_dimension_ids))
            session.invalidated_dimension_ids.pop(oldest_dimension_id)

    def invalidate_all_contexts(self) -> None:
        """Drop every document and sketch reference after global COM automation."""
        for session in self._sessions.values():
            session.documents.clear()
            session.reference_planes.clear()
            session.reference_axes.clear()
            session.features.clear()
            session.assembly_mate_entities.clear()
            session.invalidated_assembly_mate_entity_ids.clear()
            session.drawing_annotation_targets.clear()
            session.invalidated_drawing_annotation_target_ids.clear()
            session.drawing_source_views.clear()
            session.invalidated_drawing_source_view_ids.clear()
            session.part_topology.clear()
            session.invalidated_part_topology_ids.clear()
            session.assembly_mates.clear()
            session.invalidated_assembly_mate_ids.clear()
            session.drawing_boms.clear()
            session.invalidated_drawing_bom_ids.clear()
            session.drawing_revision_tables.clear()
            session.invalidated_drawing_revision_table_ids.clear()
            session.dimensions.clear()
            session.invalidated_dimension_ids.clear()
            self.invalidate_active_context(session)
