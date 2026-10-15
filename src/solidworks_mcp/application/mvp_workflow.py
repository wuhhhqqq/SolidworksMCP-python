"""Minimal typed workflow that turns adapter calls into reliable MCP results."""

from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from loguru import logger

from ..adapters.base import (
    ADAPTER_ERROR_CHAMFER_CREATION,
    ADAPTER_ERROR_CHAMFER_EDGE_SELECTION,
    ADAPTER_ERROR_CHAMFER_SELECTION_COUNT,
    ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED,
    ExtrusionParameters,
    FeatureCircularPatternParameters,
    FeatureLinearPatternParameters,
    FeatureMirrorParameters,
    HoleWizardParameters,
    LoftParameters,
    OriginPlaneParameters,
    ReferenceAxisParameters,
    ReferencePlaneParameters,
    RevolveParameters,
    SweepParameters,
    ThreadedHoleParameters,
)
from ..adapters.connection_pool import ConnectionPoolAdapter
from ..artifacts.store import ArtifactStore, ModelInputStore
from ..artifacts.trusted_macros import (
    TrustedMacro,
    TrustedMacroChangedError,
    TrustedMacroNotFoundError,
    TrustedMacrosDisabledError,
    TrustedMacroStore,
)
from ..contracts.mvp import (
    ActivateConfigurationRequest,
    AlignDrawingViewsRequest,
    ActivateDrawingSheetRequest,
    ActivateDrawingViewRequest,
    ActivateDocumentRequest,
    AddDrawingRevisionRequest,
    Add3DLineRequest,
    AddArcRequest,
    AddCenterlineRequest,
    AddCircleRequest,
    AddEllipseRequest,
    AddLineRequest,
    AddPointRequest,
    AddPolygonRequest,
    AddRectangleRequest,
    AddSplineRequest,
    AddSketchTextRequest,
    AssemblyComponentSummary,
    AssemblyInterferenceSummary,
    AssemblyMateEntitySummary,
    AssemblyMateListSummary,
    AssemblyMateSummary,
    BoundingBoxSummary,
    CheckAssemblyInterferenceRequest,
    ChamferSketchCornerRequest,
    CloseDocumentRequest,
    ConfigurationSummary,
    ConfigurationDetailSummary,
    ConvertSketchEntitiesRequest,
    Create3DSketchRequest,
    CreateAssemblyMateRequest,
    CreateAssemblyRequest,
    CreateChamferRequest,
    CreateConfigurationRequest,
    CreateCutExtrudeRequest,
    CreateDomeRequest,
    CreateDraftRequest,
    CreateDrawingBalloonRequest,
    CreateDrawingBomRequest,
    CreateDrawingHoleCalloutRequest,
    CreateDrawingOrdinateDimensionRequest,
    CreateDrawingSurfaceFinishRequest,
    CreateDrawingWeldSymbolRequest,
    CreateDrawingGeometricToleranceRequest,
    CreateDrawingCenterMarkRequest,
    CreateDrawingNoteRequest,
    CreateDrawingRevisionTableRequest,
    CreateDrawingRequest,
    CreateDrawingSheetRequest,
    CreateDrawingViewRequest,
    CreateExtrusionRequest,
    CreateFeatureCircularPatternRequest,
    CreateFeatureLinearPatternRequest,
    CreateHoleWizardRequest,
    CreateTappedHoleRequest,
    CreateLoftRequest,
    CreatePartRequest,
    CreateProjectedDrawingViewRequest,
    CreateAuxiliaryDrawingViewRequest,
    CreateReferencePlaneRequest,
    CreateRevolveRequest,
    CreateRibRequest,
    CreateSectionDrawingViewRequest,
    CreateSheetMetalBaseFlangeRequest,
    CreateSheetMetalEdgeFlangeRequest,
    CreateShellRequest,
    CreateSketchCircularPatternRequest,
    CreateSketchLinearPatternRequest,
    CreateSketchRequest,
    CreateSweepRequest,
    CreateThickenRequest,
    CreateWrapRequest,
    CustomPropertySummary,
    DeleteAssemblyMateRequest,
    DeleteConfigurationRequest,
    DeleteCustomPropertyRequest,
    DeleteDrawingBomRequest,
    DeleteDrawingRevisionRequest,
    DeleteDrawingSheetRequest,
    DeleteEquationRequest,
    DimensionSummary,
    DocumentRequest,
    DrawingAnnotationSummary,
    DrawingAnnotationTargetSummary,
    DrawingBomSummary,
    DrawingViewOutlineSummary,
    DrawingViewSummary,
    DrawingSheetSummary,
    EntityRef,
    EquationSummary,
    ExecuteTrustedMacroRequest,
    ExitSketchRequest,
    FilletSketchCornerRequest,
    ExportFileRequest,
    ExportFlatPatternDxfRequest,
    FeatureSummary,
    GetAssemblyComponentRequest,
    GetBoundingBoxRequest,
    GetConfigurationRequest,
    GetDimensionRequest,
    GetDrawingViewOutlineRequest,
    GetEquationRequest,
    GetFeatureDimensionRequest,
    GetReferenceAxisRequest,
    GetReferencePlaneRequest,
    InsertAssemblyComponentRequest,
    InsertSheetMetalBendNotesRequest,
    InsertSheetMetalBendTableRequest,
    ListAssemblyComponentsRequest,
    ListAssemblyMateEntitiesRequest,
    ListAssemblyMatesRequest,
    ListConfigurationsRequest,
    ListCustomPropertiesRequest,
    ListDimensionsRequest,
    ListDrawingAnnotationTargetsRequest,
    ListDrawingRevisionsRequest,
    ListDrawingSheetsRequest,
    ListDrawingViewsRequest,
    ListEquationsRequest,
    ListFeaturesRequest,
    ListPartTopologyRequest,
    ListSheetMetalFeatureGeometryRequest,
    ListSolidBodiesRequest,
    ListSurfaceBodiesRequest,
    MaterialAssignmentSummary,
    MirrorFeatureRequest,
    MirrorSketchEntitiesRequest,
    OffsetSketchEntitiesRequest,
    OpenModelRequest,
    OperationResult,
    OperationRoute,
    OperationStatus,
    PartTopologyEntitySummary,
    PlaceDrawingDimensionRequest,
    PrepareTrustedMacroRequest,
    RebuildDrawingRequest,
    RenameDrawingSheetRequest,
    ReorderDrawingSheetsRequest,
    SaveModelRequest,
    SetCustomPropertyRequest,
    SetDimensionRequest,
    SetEquationRequest,
    SetFeatureDimensionRequest,
    SetMaterialAssignmentRequest,
    SetSheetMetalKFactorRequest,
    SheetMetalBendNotesSummary,
    SheetMetalBendTableSummary,
    SheetMetalFeatureGeometrySummary,
    SheetMetalFlatStateRequest,
    SheetMetalSummary,
    SolidBodySummary,
    TrimSketchEntityRequest,
    SplitSketchEntityRequest,
    UpdateDrawingViewLayoutRequest,
    UpdateDrawingBomRequest,
)
from .sessions import (
    AssemblyMateReferenceError,
    DrawingAnnotationReferenceError,
    DrawingBomReferenceError,
    DrawingRevisionTableReferenceError,
    DrawingSourceViewReferenceError,
    MateEntityReferenceError,
    PartTopologyReferenceError,
    SessionCapacityError,
    SessionLookupError,
    SessionService,
    SketchStateError,
)


@dataclass(frozen=True)
class PendingMacroConfirmation:
    """One-time macro authorization bound to a single MCP workflow session."""

    session_id: str
    macro: TrustedMacro
    expires_at: datetime


class MvpWorkflow:
    """Execute a safe part/sketch/extrude/export MVP through one adapter."""

    _MAX_PENDING_MACRO_CONFIRMATIONS = 128

    def __init__(
        self,
        *,
        adapter: Any,
        artifact_store: ArtifactStore,
        model_input_store: ModelInputStore | None = None,
        trusted_macro_store: TrustedMacroStore | None = None,
        macro_confirmation_ttl_seconds: int = 300,
        max_sessions: int = 64,
    ) -> None:
        """Initialize the workflow with its session registry and output store."""
        self.adapter = adapter
        self.artifact_store = artifact_store
        self.model_input_store = model_input_store or ModelInputStore(
            artifact_store.root.parent / "inputs"
        )
        self.sessions = SessionService(max_sessions=max_sessions)
        self.trusted_macro_store = trusted_macro_store
        self.macro_confirmation_ttl_seconds = macro_confirmation_ttl_seconds
        self._macro_confirmations: dict[str, PendingMacroConfirmation] = {}
        self._stateful_sessions_supported = not isinstance(
            adapter, ConnectionPoolAdapter
        )
        # All MCP sessions share one desktop SolidWorks/COM instance.  Per-session
        # locks protect references, while this lock prevents UI/COM state from
        # being interleaved across otherwise independent sessions.
        self._adapter_lock = asyncio.Lock()

    async def create_session(self) -> OperationResult:
        """Create an opaque session and return connection health to the client."""
        if not self._stateful_sessions_supported:
            return OperationResult.failed(
                code="SW_STATEFUL_SESSION_POOLING_UNSUPPORTED",
                message="Typed SolidWorks workflow sessions cannot run through a connection pool.",
                suggestion="Disable enable_connection_pooling before using solidworks_* workflow tools.",
            )
        connected = bool(self.adapter.is_connected())
        try:
            session = self.sessions.create(connected=connected)
        except SessionCapacityError:
            return OperationResult.failed(
                code="SW_SESSION_CAPACITY_REACHED",
                message="The server has reached its workflow session capacity.",
                suggestion="Close an unused session with solidworks_close_session, then retry.",
            )
        health = await self._health_check()
        health_payload = (
            self._as_dict(health)
            if health is not None
            else {"healthy": connected, "connection_status": "unknown"}
        )
        return OperationResult.succeeded(
            route=OperationRoute.NONE,
            data={"session": session.ref.model_dump(), "health": health_payload},
        )

    async def close_session(self, session_id: str) -> OperationResult:
        """Release one client workflow session without closing user documents."""
        try:
            session = self.sessions.require(session_id)
        except SessionLookupError:
            return self._session_not_found()

        async with session.operation_lock:
            if session.active_sketch is not None:
                return OperationResult.failed(
                    code="SW_SESSION_SKETCH_ACTIVE",
                    message="The session is still editing a SolidWorks sketch.",
                    suggestion="Exit the active sketch before closing the workflow session.",
                )
            self.sessions.close(session_id)
            self._macro_confirmations = {
                confirmation_id: pending
                for confirmation_id, pending in self._macro_confirmations.items()
                if pending.session_id != session_id
            }
        return OperationResult.succeeded(
            route=OperationRoute.NONE,
            data={"closed_session_id": session_id},
        )

    async def get_status(self, session_id: str) -> OperationResult:
        """Return a concise status snapshot for one existing session."""
        try:
            session = self.sessions.require(session_id)
        except SessionLookupError:
            return self._session_not_found()
        health = await self._health_check()
        return OperationResult.succeeded(
            route=OperationRoute.NONE,
            data={
                "session": session.ref.model_dump(),
                "active_document_id": session.active_document_id,
                "health": self._as_dict(health) if health is not None else {},
            },
        )

    async def list_documents(self, session_id: str) -> OperationResult:
        """List the current session's document capabilities without exposing COM state."""
        try:
            session = self.sessions.require(session_id)
        except SessionLookupError:
            return self._session_not_found()
        documents = [document.model_dump() for document in session.documents.values()]
        return OperationResult.succeeded(
            route=OperationRoute.NONE,
            data={
                "documents": documents,
                "active_document_id": session.active_document_id,
            },
        )

    async def activate_document(
        self, request: ActivateDocumentRequest
    ) -> OperationResult:
        """Activate one existing session document through SolidWorks' document API."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_document(session, request.document_id)
        except SessionLookupError:
            return self._document_not_found()
        if self._foreign_sketch_context_locked(session):
            return self._sketch_context_locked()
        if session.active_sketch is not None:
            return self._sketch_not_ready(
                "exit the active sketch before switching workflow documents"
            )

        async with session.operation_lock:
            result = await self._call_adapter(
                lambda: self.adapter.activate_model(document.name, document.path),
                session=session,
            )
            if isinstance(result, OperationResult):
                return result
            try:
                activated = self.sessions.activate_document(
                    session, request.document_id
                )
            except (SessionLookupError, SketchStateError):
                return self._document_not_found()
            return OperationResult.succeeded(
                route=OperationRoute.COM,
                data={"document": activated.model_dump()},
            )

    async def close_document(self, request: CloseDocumentRequest) -> OperationResult:
        """Close the active workflow document and revoke all matching session IDs."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if self.sessions.has_active_sketch():
            return self._sketch_not_ready(
                "exit every active workflow sketch before closing a document"
            )

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.close_model(save=request.save),
            )
            if isinstance(result, OperationResult):
                return result
            closed_document = self.sessions.close_document(session, request.document_id)
            return OperationResult.succeeded(
                data={
                    "closed_document_id": closed_document.document_id,
                    "saved": request.save,
                },
                route=OperationRoute.COM,
            )

    async def create_part(self, request: CreatePartRequest) -> OperationResult:
        """Create and track the session's active part document."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._session_not_found()
        if self._foreign_sketch_context_locked(session):
            return self._sketch_context_locked()

        async with session.operation_lock:
            result = await self._call_adapter(
                lambda: self.adapter.create_part(request.name, request.units),
                session=session,
            )
            if isinstance(result, OperationResult):
                return result
            document = self.sessions.register_document(
                session, self._as_dict(result.data)
            )
            return OperationResult.succeeded(
                data={"document": document.model_dump()}, route=OperationRoute.COM
            )

    async def create_assembly(self, request: CreateAssemblyRequest) -> OperationResult:
        """Create and track the session's active assembly document."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._session_not_found()
        if self._foreign_sketch_context_locked(session):
            return self._sketch_context_locked()

        async with session.operation_lock:
            result = await self._call_adapter(
                lambda: self.adapter.create_assembly(request.name), session=session
            )
            if isinstance(result, OperationResult):
                return result
            document = self.sessions.register_document(
                session, self._as_dict(result.data)
            )
            return OperationResult.succeeded(
                data={"document": document.model_dump()}, route=OperationRoute.COM
            )

    async def create_drawing(self, request: CreateDrawingRequest) -> OperationResult:
        """Create and track the session's active drawing document."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._session_not_found()
        if self._foreign_sketch_context_locked(session):
            return self._sketch_context_locked()

        async with session.operation_lock:
            result = await self._call_adapter(
                lambda: self.adapter.create_drawing(request.name), session=session
            )
            if isinstance(result, OperationResult):
                return result
            document = self.sessions.register_document(
                session, self._as_dict(result.data)
            )
            return OperationResult.succeeded(
                data={"document": document.model_dump()}, route=OperationRoute.COM
            )

    async def open_model(self, request: OpenModelRequest) -> OperationResult:
        """Open one native SolidWorks file from the configured trusted input root."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._session_not_found()
        if self._foreign_sketch_context_locked(session):
            return self._sketch_context_locked()

        try:
            source = self.model_input_store.resolve_model_path(request.file_name)
        except FileNotFoundError:
            return OperationResult.failed(
                code="SW_MODEL_INPUT_NOT_FOUND",
                message="The requested native SolidWorks input file was not found.",
                suggestion="Place the file in the configured model_input_dir and retry with its leaf file name.",
            )
        except OSError:
            return OperationResult.failed(
                code="SW_MODEL_INPUT_UNAVAILABLE",
                message="The configured model input directory could not be read safely.",
                suggestion="Ask the server administrator to verify the input directory permissions and retry.",
            )
        except ValueError as exc:
            return OperationResult.failed(
                code="SW_MODEL_INPUT_REJECTED",
                message="The requested model input is outside the allowed input policy.",
                suggestion=str(exc),
            )

        async with session.operation_lock:
            result = await self._call_adapter(
                lambda: self.adapter.open_model(str(source)), session=session
            )
            if isinstance(result, OperationResult):
                return result
            document = self.sessions.register_document(
                session, self._as_dict(result.data)
            )
            return OperationResult.succeeded(
                data={"document": document.model_dump()}, route=OperationRoute.COM
            )

    async def create_sketch(self, request: CreateSketchRequest) -> OperationResult:
        """Create and track the one sketch currently active in the session."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        sketch: EntityRef | None = None

        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
                plane_name: str | None = request.plane
                if request.reference_plane_id is not None:
                    plane_name = self.sessions.require_reference_plane(
                        session, request.document_id, request.reference_plane_id
                    ).display_name
            except SessionLookupError:
                return self._document_not_found()
            except SketchStateError:
                return OperationResult.failed(
                    code="SW_REFERENCE_PLANE_NOT_CURRENT",
                    message="The requested reference plane is not current for this document.",
                    suggestion="Create a new reference plane or use a current reference_plane_id, then retry.",
                )
            if plane_name is None:
                return OperationResult.failed(
                    code="SW_SKETCH_PLANE_REQUIRED",
                    message="A current standard or reference plane is required to create a sketch.",
                    suggestion="Specify one standard plane or a current reference_plane_id, then retry.",
                )

            def record_sketch(result: Any) -> None:
                """Record sketch ownership before another session can enter COM."""
                nonlocal sketch
                sketch = self.sessions.record_active_sketch(
                    session, request.document_id, self._as_dict(result.data)
                )

            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.create_sketch(plane_name),
                on_success=record_sketch,
                pre_call=(
                    None
                    if request.reference_plane_id is None
                    else lambda: self._require_current_reference_plane(
                        session, request.document_id, request.reference_plane_id
                    )
                ),
            )
            if isinstance(result, OperationResult):
                return result
            if sketch is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a sketch but did not return a usable sketch reference.",
                    suggestion="Inspect the active document and create the sketch again.",
                )
            return OperationResult.succeeded(
                data={"sketch": sketch.model_dump()}, route=OperationRoute.COM
            )

    async def create_3d_sketch(self, request: Create3DSketchRequest) -> OperationResult:
        """Open and track one 3D sketch in the active session part document."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_failure = self._require_part_document(document)
        if part_failure is not None:
            return part_failure

        sketch: EntityRef | None = None
        async with session.operation_lock:

            def record_sketch(result: Any) -> None:
                nonlocal sketch
                sketch = self.sessions.record_active_sketch(
                    session, request.document_id, self._as_dict(result.data)
                )

            result = await self._call_document_adapter(
                session,
                document,
                self.adapter.create_3d_sketch,
                on_success=record_sketch,
            )
            if isinstance(result, OperationResult):
                return result
            if sketch is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a 3D sketch but did not return a usable reference.",
                    suggestion="Inspect the active part and create the 3D sketch again.",
                )
            return OperationResult.succeeded(
                data={"sketch": sketch.model_dump()}, route=OperationRoute.COM
            )

    async def create_reference_plane(
        self, request: CreateReferencePlaneRequest
    ) -> OperationResult:
        """Create an offset part reference plane and issue an opaque capability."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
            except SessionLookupError:
                return self._document_not_found()
            part_failure = self._require_part_document(document)
            if part_failure is not None:
                return part_failure
            if session.active_sketch is not None:
                return self._sketch_not_ready(
                    "exit the active sketch before creating a reference plane"
                )

            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.create_reference_plane(
                    ReferencePlaneParameters(
                        base_plane=request.base_plane,
                        offset=self._millimetres(request.offset, request.unit),
                        reverse_direction=request.reverse_direction,
                    )
                ),
            )
            if isinstance(result, OperationResult):
                return result
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            try:
                reference_plane = self.sessions.record_reference_plane(
                    session, request.document_id, self._as_dict(result.data)
                )
            except SketchStateError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a reference plane but did not return a usable name.",
                    suggestion="Inspect the active part in SolidWorks before creating another reference plane.",
                )
            return OperationResult.succeeded(
                data={
                    "reference_plane": reference_plane.model_dump(),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def get_reference_axis(
        self, request: GetReferenceAxisRequest
    ) -> OperationResult:
        """Issue an opaque capability for one built-in part origin axis."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        reference_axis: EntityRef | None = None
        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
            except SessionLookupError:
                return self._document_not_found()
            part_failure = self._require_part_document(document)
            if part_failure is not None:
                return part_failure

            def record_axis(result: Any) -> None:
                nonlocal reference_axis
                reference_axis = self.sessions.record_reference_axis(
                    session, request.document_id, self._as_dict(result.data)
                )

            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_reference_axis(
                    ReferenceAxisParameters(axis=request.axis)
                ),
                on_success=record_axis,
            )
            if isinstance(result, OperationResult):
                return result
            if reference_axis is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return a usable standard origin axis.",
                    suggestion="Verify the active part's origin references and retry.",
                )
            return OperationResult.succeeded(
                data={"reference_axis": reference_axis.model_dump()},
                route=OperationRoute.COM,
            )

    async def get_reference_plane(
        self, request: GetReferencePlaneRequest
    ) -> OperationResult:
        """Issue an opaque capability for one built-in part origin plane."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        reference_plane: EntityRef | None = None
        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
            except SessionLookupError:
                return self._document_not_found()
            part_failure = self._require_part_document(document)
            if part_failure is not None:
                return part_failure

            def record_plane(result: Any) -> None:
                nonlocal reference_plane
                reference_plane = self.sessions.record_reference_plane(
                    session, request.document_id, self._as_dict(result.data)
                )

            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_reference_plane(
                    OriginPlaneParameters(plane=request.plane)
                ),
                on_success=record_plane,
            )
            if isinstance(result, OperationResult):
                return result
            if reference_plane is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return a usable standard origin plane.",
                    suggestion="Verify the active part's origin references and retry.",
                )
            return OperationResult.succeeded(
                data={"reference_plane": reference_plane.model_dump()},
                route=OperationRoute.COM,
            )

    async def create_feature_linear_pattern(
        self, request: CreateFeatureLinearPatternRequest
    ) -> OperationResult:
        """Create a one-direction pattern from one current typed feature."""
        return await self._create_feature_from_trusted_inputs(
            request,
            resolve=lambda session: FeatureLinearPatternParameters(
                feature_name=self.sessions.require_feature(
                    session, request.document_id, request.feature_id
                ).display_name,
                direction=request.direction,
                instances=request.instances,
                spacing=self._millimetres(request.spacing, request.unit),
                reverse_direction=request.reverse_direction,
            ),
            create_feature=self.adapter.create_feature_linear_pattern,
            stale_error=self._feature_not_current(),
        )

    async def create_feature_circular_pattern(
        self, request: CreateFeatureCircularPatternRequest
    ) -> OperationResult:
        """Create a circular pattern from current feature and origin-axis capabilities."""
        return await self._create_feature_from_trusted_inputs(
            request,
            resolve=lambda session: FeatureCircularPatternParameters(
                feature_name=self.sessions.require_feature(
                    session, request.document_id, request.feature_id
                ).display_name,
                axis_name=self.sessions.require_reference_axis(
                    session, request.document_id, request.reference_axis_id
                ).display_name,
                instances=request.instances,
                angle_degrees=request.angle_degrees,
                reverse_direction=request.reverse_direction,
            ),
            create_feature=self.adapter.create_feature_circular_pattern,
            stale_error=self._feature_or_axis_not_current(),
        )

    async def mirror_feature(self, request: MirrorFeatureRequest) -> OperationResult:
        """Mirror one current typed feature about one current typed plane."""
        return await self._create_feature_from_trusted_inputs(
            request,
            resolve=lambda session: FeatureMirrorParameters(
                feature_name=self.sessions.require_feature(
                    session, request.document_id, request.feature_id
                ).display_name,
                plane_name=self.sessions.require_reference_plane(
                    session, request.document_id, request.reference_plane_id
                ).display_name,
            ),
            create_feature=self.adapter.mirror_feature,
            stale_error=self._feature_or_plane_not_current(),
        )

    async def create_hole_wizard(
        self, request: CreateHoleWizardRequest
    ) -> OperationResult:
        """Create a controlled ANSI Metric clearance Hole Wizard from a location sketch."""
        return await self._create_feature_from_trusted_inputs(
            request,
            resolve=lambda session: HoleWizardParameters(
                location_sketch_name=self.sessions.require_exited_sketch(
                    session, request.document_id, request.location_sketch_id
                ).display_name,
                diameter=self._millimetres(request.diameter, request.unit),
                depth=self._millimetres(request.depth, request.unit),
            ),
            create_feature=self.adapter.create_hole_wizard,
            stale_error=self._sketch_not_ready(
                "create a location sketch with a point, exit it, then create the Hole Wizard"
            ),
        )

    async def create_tapped_hole(
        self, request: CreateTappedHoleRequest
    ) -> OperationResult:
        """Create a standard ANSI Metric tapped Hole Wizard from a location sketch."""
        return await self._create_feature_from_trusted_inputs(
            request,
            resolve=lambda session: ThreadedHoleParameters(
                location_sketch_name=self.sessions.require_exited_sketch(
                    session, request.document_id, request.location_sketch_id
                ).display_name,
                thread_size=request.thread_size,
                depth=self._millimetres(request.depth, request.unit),
                through_all=request.through_all,
            ),
            create_feature=self.adapter.create_tapped_hole,
            stale_error=self._sketch_not_ready(
                "create a location sketch with a point, exit it, then create the tapped Hole Wizard"
            ),
        )

    async def add_rectangle(self, request: AddRectangleRequest) -> OperationResult:
        """Add one profile rectangle to the currently active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="rectangle",
            display_name="Rectangle",
            call=lambda: self.adapter.add_rectangle(
                request.x1, request.y1, request.x2, request.y2
            ),
        )

    async def add_line(self, request: AddLineRequest) -> OperationResult:
        """Add one line segment to the currently active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="line",
            display_name="Line",
            call=lambda: self.adapter.add_line(
                request.x1, request.y1, request.x2, request.y2
            ),
        )

    async def add_3d_line(self, request: Add3DLineRequest) -> OperationResult:
        """Add one line to the currently active server-owned 3D sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="line",
            display_name="3D Line",
            call=lambda: self.adapter.add_3d_line(
                request.x1,
                request.y1,
                request.z1,
                request.x2,
                request.y2,
                request.z2,
            ),
        )

    async def add_circle(self, request: AddCircleRequest) -> OperationResult:
        """Add one circle to the currently active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="circle",
            display_name="Circle",
            call=lambda: self.adapter.add_circle(
                request.center_x, request.center_y, request.radius
            ),
        )

    async def add_point(self, request: AddPointRequest) -> OperationResult:
        """Add one Hole Wizard placement point to the active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="point",
            display_name="Point",
            call=lambda: self.adapter.add_point(request.x, request.y),
        )

    async def add_arc(self, request: AddArcRequest) -> OperationResult:
        """Add one circular arc to the currently active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="arc",
            display_name="Arc",
            call=lambda: self.adapter.add_arc(
                request.center_x,
                request.center_y,
                request.start_x,
                request.start_y,
                request.end_x,
                request.end_y,
            ),
        )

    async def add_centerline(self, request: AddCenterlineRequest) -> OperationResult:
        """Add one construction centreline to the active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="centerline",
            display_name="Centerline",
            call=lambda: self.adapter.add_centerline(
                request.x1, request.y1, request.x2, request.y2
            ),
        )

    async def add_spline(self, request: AddSplineRequest) -> OperationResult:
        """Add one NURBS spline to the currently active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="spline",
            display_name="Spline",
            call=lambda: self.adapter.add_spline(
                [point.model_dump() for point in request.points]
            ),
        )

    async def add_polygon(self, request: AddPolygonRequest) -> OperationResult:
        """Add one regular polygon to the currently active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="polygon",
            display_name="Polygon",
            call=lambda: self.adapter.add_polygon(
                request.center_x, request.center_y, request.radius, request.sides
            ),
        )

    async def add_ellipse(self, request: AddEllipseRequest) -> OperationResult:
        """Add one axis-aligned ellipse to the currently active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="ellipse",
            display_name="Ellipse",
            call=lambda: self.adapter.add_ellipse(
                request.center_x,
                request.center_y,
                request.major_axis,
                request.minor_axis,
            ),
        )

    async def add_sketch_text(self, request: AddSketchTextRequest) -> OperationResult:
        """Add one text entity to the active tracked sketch."""
        return await self._add_sketch_entity(
            request,
            entity_key="sketch_text",
            display_name="Sketch Text",
            call=lambda: self.adapter.add_sketch_text(
                request.text,
                request.x,
                request.y,
                request.height,
                request.angle_degrees,
            ),
        )

    async def create_sketch_linear_pattern(
        self, request: CreateSketchLinearPatternRequest
    ) -> OperationResult:
        """Pattern server-issued entities in one linear direction."""
        return await self._add_sketch_entity(
            request,
            entity_key="pattern",
            display_name="Linear Sketch Pattern",
            call=lambda: self.adapter.sketch_linear_pattern(
                request.entity_ids,
                request.direction_x,
                request.direction_y,
                request.spacing,
                request.count,
            ),
        )

    async def create_sketch_circular_pattern(
        self, request: CreateSketchCircularPatternRequest
    ) -> OperationResult:
        """Pattern server-issued entities around the active sketch origin."""
        return await self._add_sketch_entity(
            request,
            entity_key="pattern",
            display_name="Circular Sketch Pattern",
            call=lambda: self.adapter.sketch_circular_pattern(
                request.entity_ids, request.angle_degrees, request.count
            ),
        )

    async def mirror_sketch_entities(
        self, request: MirrorSketchEntitiesRequest
    ) -> OperationResult:
        """Mirror server-issued entities about a server-issued centreline."""
        return await self._add_sketch_entity(
            request,
            entity_key="mirror",
            display_name="Mirrored Sketch Entities",
            call=lambda: self.adapter.sketch_mirror(
                request.entity_ids, request.mirror_line_id
            ),
        )

    async def offset_sketch_entities(
        self, request: OffsetSketchEntitiesRequest
    ) -> OperationResult:
        """Offset server-issued entities while the tracked sketch remains active."""
        return await self._add_sketch_entity(
            request,
            entity_key="offset",
            display_name="Offset Sketch Entities",
            call=lambda: self.adapter.sketch_offset(
                request.entity_ids, request.distance, request.reverse_direction
            ),
        )

    async def trim_sketch_entity(
        self, request: TrimSketchEntityRequest
    ) -> OperationResult:
        """Trim one server-issued entity while its session sketch remains active."""
        return await self._add_sketch_entity(
            request,
            entity_key="trimmed_entity",
            display_name="Trimmed Sketch Entity",
            call=lambda: self.adapter.sketch_trim(
                request.entity_id, request.x, request.y
            ),
        )

    async def split_sketch_entity(
        self, request: SplitSketchEntityRequest
    ) -> OperationResult:
        """Split one active-sketch segment and return the new entity capability."""
        return await self._add_sketch_entity(
            request,
            entity_key="split_entity",
            display_name="Split Sketch Entity",
            call=lambda: self.adapter.sketch_split(
                request.entity_id,
                request.x,
                request.y,
                request.z,
                request.closed,
                request.x2,
                request.y2,
                request.z2,
            ),
        )

    async def fillet_sketch_corner(
        self, request: FilletSketchCornerRequest
    ) -> OperationResult:
        """Round the corner between two active-sketch entities."""
        return await self._add_sketch_entity(
            request,
            entity_key="fillet_entity",
            display_name="Sketch Fillet",
            call=lambda: self.adapter.sketch_fillet(request.entity_ids, request.radius),
        )

    async def chamfer_sketch_corner(
        self, request: ChamferSketchCornerRequest
    ) -> OperationResult:
        """Bevel the corner between two active-sketch entities."""
        return await self._add_sketch_entity(
            request,
            entity_key="chamfer_entity",
            display_name="Sketch Chamfer",
            call=lambda: self.adapter.sketch_chamfer(
                request.entity_ids, request.distance, request.angle_degrees
            ),
        )

    async def convert_sketch_entities(
        self, request: ConvertSketchEntitiesRequest
    ) -> OperationResult:
        """Convert only current server-issued part edges into the active sketch."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
                self.sessions.require_active_sketch(
                    session, request.document_id, request.sketch_id
                )
                edges = [
                    self.sessions.resolve_part_topology(
                        session, request.document_id, edge_id
                    )
                    for edge_id in request.edge_ids
                ]
            except SessionLookupError:
                return self._document_not_found()
            except SketchStateError:
                return self._sketch_not_ready(
                    "create_sketch before converting entities"
                )
            except PartTopologyReferenceError as exc:
                return self._part_topology_reference_error(exc.reason)
            if any(edge.entity_type != "edge" for edge in edges):
                return OperationResult.failed(
                    code="SW_PART_EDGE_REQUIRED",
                    message="Convert Entities accepts only server-issued part edge IDs.",
                    suggestion="List part topology with entity_types=['edge'] and retry.",
                )
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.convert_sketch_entities(
                    [edge.persistent_reference for edge in edges]
                ),
            )
            if isinstance(result, OperationResult):
                return result
            entity = EntityRef(
                document_id=request.document_id,
                entity_type="sketch_entity",
                persistent_id=str(result.data),
                display_name="Converted Entities",
                revision=document.revision,
            )
            return OperationResult.succeeded(
                data={"converted_entities": entity.model_dump()},
                route=OperationRoute.COM,
            )

    async def _add_sketch_entity(
        self,
        request: Any,
        *,
        entity_key: str,
        display_name: str,
        call: Any,
    ) -> OperationResult:
        """Run one sketch write while preserving session-owned sketch state."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
                self.sessions.require_active_sketch(
                    session, request.document_id, request.sketch_id
                )
            except SessionLookupError:
                return self._document_not_found()
            except SketchStateError:
                return self._sketch_not_ready("create_sketch before adding geometry")

            def revalidate_active_sketch() -> OperationResult | None:
                try:
                    self.sessions.require_active_sketch(
                        session, request.document_id, request.sketch_id
                    )
                except SessionLookupError:
                    return self._document_not_found()
                except SketchStateError:
                    return self._sketch_not_ready(
                        "create_sketch before adding geometry"
                    )
                return None

            result = await self._call_document_adapter(
                session,
                document,
                call,
                pre_call=revalidate_active_sketch,
            )
            if isinstance(result, OperationResult):
                return result
            entity = EntityRef(
                document_id=request.document_id,
                entity_type="sketch_entity",
                persistent_id=str(result.data),
                display_name=display_name,
                revision=self.sessions.require_document(
                    session, request.document_id
                ).revision,
            )
            return OperationResult.succeeded(
                data={entity_key: entity.model_dump()}, route=OperationRoute.COM
            )

    async def exit_sketch(self, request: ExitSketchRequest) -> OperationResult:
        """Exit the current sketch and make it eligible for the next extrusion."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
            self.sessions.require_active_sketch(
                session, request.document_id, request.sketch_id
            )
        except SessionLookupError:
            return self._document_not_found()
        except SketchStateError:
            return self._sketch_not_ready("create or reactivate the requested sketch")

        exited_sketch: EntityRef | None = None

        def record_exit(_: Any) -> None:
            """Clear global sketch ownership before another session can enter COM."""
            nonlocal exited_sketch
            exited_sketch = self.sessions.exit_sketch(
                session, request.document_id, request.sketch_id
            )

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.exit_sketch, on_success=record_exit
            )
            if isinstance(result, OperationResult):
                return result
            if exited_sketch is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks exited the sketch but the workflow could not update its sketch state.",
                    suggestion="Reopen the document in a new workflow session before continuing.",
                )
            return OperationResult.succeeded(
                data={"sketch": exited_sketch.model_dump()}, route=OperationRoute.COM
            )

    async def create_extrusion(
        self, request: CreateExtrusionRequest
    ) -> OperationResult:
        """Extrude only the last exited sketch tracked by the same session."""
        return await self._create_feature_from_exited_sketch(
            request,
            params=ExtrusionParameters(
                depth=self._millimetres(request.depth, request.unit),
                end_condition=request.end_condition,
                reverse_direction=request.reverse_direction,
                both_directions=request.both_directions,
                merge_result=request.merge_result,
            ),
            create_feature=self.adapter.create_extrusion,
        )

    async def create_cut_extrude(
        self, request: CreateCutExtrudeRequest
    ) -> OperationResult:
        """Cut material using only the last exited sketch in the same session."""
        return await self._create_feature_from_exited_sketch(
            request,
            params=ExtrusionParameters(
                depth=self._millimetres(request.depth, request.unit),
                end_condition=request.end_condition,
                reverse_direction=request.reverse_direction,
                both_directions=request.both_directions,
                merge_result=request.merge_result,
            ),
            create_feature=self.adapter.create_cut_extrude,
        )

    async def create_sheet_metal_base_flange(
        self, request: CreateSheetMetalBaseFlangeRequest
    ) -> OperationResult:
        """Create a base flange from one current session-owned exited sketch."""
        return await self._create_feature_from_trusted_inputs(
            request,
            resolve=lambda session: (
                self.sessions.require_exited_sketch(
                    session, request.document_id, request.sketch_id
                ).display_name,
                self._millimetres(request.thickness, request.unit),
                self._millimetres(request.bend_radius, request.unit),
                request.k_factor,
            ),
            create_feature=lambda params: self.adapter.create_sheet_metal_base_flange(
                *params
            ),
            stale_error=self._sketch_not_ready(
                "create a closed sketch, exit it, then create the base flange"
            ),
        )

    async def create_sheet_metal_edge_flange(
        self, request: CreateSheetMetalEdgeFlangeRequest
    ) -> OperationResult:
        """Create an edge flange from one current server-issued part edge."""
        return await self._create_feature_from_part_topology(
            request,
            entity_ids=[request.edge_id],
            required_type="edge",
            create_feature=lambda entities: self.adapter.create_sheet_metal_edge_flange(
                entities[0].persistent_reference,
                self._millimetres(request.length, request.unit),
                request.angle_degrees,
                self._millimetres(request.bend_radius, request.unit),
                request.k_factor,
            ),
        )

    async def set_sheet_metal_k_factor(
        self, request: SetSheetMetalKFactorRequest
    ) -> OperationResult:
        """Set and verify the K factor of the active sheet-metal part."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_failure = self._require_part_document(document)
        if part_failure is not None:
            return part_failure
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.set_sheet_metal_k_factor(request.k_factor),
            )
            if isinstance(result, OperationResult):
                return result
            actual = self._as_dict(result.data).get("k_factor")
            if not isinstance(actual, (int, float)) or not math.isclose(
                float(actual), request.k_factor, abs_tol=1e-9
            ):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not verify the requested sheet-metal K factor.",
                    suggestion="Inspect the active sheet-metal definition and retry.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "k_factor": float(actual),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def create_shell(self, request: CreateShellRequest) -> OperationResult:
        """Shell the active part after removing current server-issued faces."""
        return await self._create_feature_from_part_topology(
            request,
            entity_ids=request.face_ids,
            required_type="face",
            create_feature=lambda entities: self.adapter.create_shell(
                [entity.persistent_reference for entity in entities],
                self._millimetres(request.thickness, request.unit),
                request.outward,
            ),
        )

    async def create_chamfer(self, request: CreateChamferRequest) -> OperationResult:
        """Create a 3D chamfer from current server-issued part edge capabilities."""
        return await self._create_feature_from_part_topology(
            request,
            entity_ids=request.edge_ids,
            required_type="edge",
            create_feature=lambda entities: self.adapter.create_chamfer(
                [entity.persistent_reference for entity in entities],
                self._millimetres(request.distance, request.unit),
                request.angle_degrees,
                request.chamfer_type,
            ),
            diagnostics_factory=lambda entities: self._chamfer_preflight(
                entities,
                distance_mm=self._millimetres(request.distance, request.unit),
                angle_degrees=request.angle_degrees,
                chamfer_type=request.chamfer_type,
            ),
        )

    async def create_rib(self, request: CreateRibRequest) -> OperationResult:
        """Create a rib from one current session-owned exited sketch."""
        return await self._create_feature_from_trusted_inputs(
            request,
            resolve=lambda session: (
                self.sessions.require_exited_sketch(
                    session, request.document_id, request.sketch_id
                ).display_name,
                self._millimetres(request.thickness, request.unit),
                request.draft_angle_degrees,
                request.direction,
            ),
            create_feature=lambda params: self.adapter.create_rib(*params),
            stale_error=self._sketch_not_ready(
                "create an open rib sketch, exit it, then create the rib"
            ),
        )

    async def create_dome(self, request: CreateDomeRequest) -> OperationResult:
        """Create a dome on one current server-issued part face."""
        return await self._create_feature_from_part_topology(
            request,
            entity_ids=[request.face_id],
            required_type="face",
            create_feature=lambda entities: self.adapter.create_dome(
                entities[0].persistent_reference,
                self._millimetres(request.height, request.unit),
                request.reverse_direction,
            ),
        )

    async def create_draft(self, request: CreateDraftRequest) -> OperationResult:
        """Apply a neutral-plane draft using only current server-issued faces."""
        return await self._create_feature_from_part_topology(
            request,
            entity_ids=request.face_ids,
            required_type="face",
            create_feature=lambda entities: self.adapter.create_draft(
                [entity.persistent_reference for entity in entities],
                request.angle_degrees,
                request.reverse_direction,
                request.edge_draft,
            ),
        )

    async def create_thicken(self, request: CreateThickenRequest) -> OperationResult:
        """Thicken one current part face into a real SolidWorks feature."""
        return await self._create_feature_from_part_topology(
            request,
            entity_ids=[request.face_id],
            required_type="face",
            create_feature=lambda entities: self.adapter.create_thicken(
                entities[0].persistent_reference,
                self._millimetres(request.thickness, request.unit),
                request.operation,
                request.direction,
                request.fill_volume,
                request.merge_result,
            ),
        )

    async def create_wrap(self, request: CreateWrapRequest) -> OperationResult:
        """Wrap one exited session sketch onto one current server-issued part face."""
        return await self._create_feature_from_exited_sketch_and_part_face(
            request,
            face_id=request.face_id,
            create_feature=lambda sketch_name, face_reference: self.adapter.create_wrap(
                sketch_name,
                face_reference,
                request.operation,
                self._millimetres(request.thickness, request.unit),
                request.reverse_direction,
                request.method,
            ),
        )

    async def create_revolve(self, request: CreateRevolveRequest) -> OperationResult:
        """Revolve a profile about a centreline in the last exited sketch."""
        return await self._create_feature_from_exited_sketch(
            request,
            params=RevolveParameters(
                angle=request.angle_degrees,
                reverse_direction=request.reverse_direction,
                both_directions=request.both_directions,
                thin_feature=request.thin_feature,
                thin_thickness=request.thin_thickness,
                merge_result=request.merge_result,
                is_cut=request.operation == "cut",
            ),
            create_feature=self.adapter.create_revolve,
        )

    async def create_sweep(self, request: CreateSweepRequest) -> OperationResult:
        """Sweep a session-owned profile sketch along a session-owned path sketch."""
        return await self._create_feature_from_exited_sketches(
            request,
            sketch_ids=[request.profile_sketch_id, request.path_sketch_id],
            params=SweepParameters(
                profile=request.profile_sketch_id,
                path=request.path_sketch_id,
                twist_along_path=request.twist_along_path,
                twist_angle=request.twist_angle_degrees,
                merge_result=request.merge_result,
                is_cut=request.operation == "cut",
            ),
            create_feature=self.adapter.create_sweep,
        )

    async def create_loft(self, request: CreateLoftRequest) -> OperationResult:
        """Loft between ordered session-owned profile sketches."""
        return await self._create_feature_from_exited_sketches(
            request,
            sketch_ids=[*request.profile_sketch_ids, *request.guide_sketch_ids],
            params=LoftParameters(
                profiles=request.profile_sketch_ids,
                guide_curves=request.guide_sketch_ids or None,
                start_tangent=(
                    None if request.start_tangent == "none" else request.start_tangent
                ),
                end_tangent=(
                    None if request.end_tangent == "none" else request.end_tangent
                ),
                merge_result=request.merge_result,
                is_cut=request.operation == "cut",
            ),
            create_feature=self.adapter.create_loft,
        )

    async def _create_feature_from_part_topology(
        self,
        request: Any,
        *,
        entity_ids: list[str],
        required_type: Literal["face", "edge"],
        create_feature: Any,
        diagnostics_factory: Any | None = None,
    ) -> OperationResult:
        """Create a part feature from current opaque face or edge capabilities."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
            except SessionLookupError:
                return self._document_not_found()
            part_failure = self._require_part_document(document)
            if part_failure is not None:
                return part_failure
            if session.active_sketch is not None:
                return self._sketch_not_ready(
                    "exit the active sketch before creating this part feature"
                )
            try:
                entities = [
                    self.sessions.resolve_part_topology(
                        session, request.document_id, entity_id
                    )
                    for entity_id in entity_ids
                ]
            except PartTopologyReferenceError as exc:
                return self._part_topology_reference_error(exc.reason)
            if any(entity.entity_type != required_type for entity in entities):
                return OperationResult.failed(
                    code="SW_PART_TOPOLOGY_TYPE_MISMATCH",
                    message=f"This feature requires server-issued part {required_type} IDs.",
                    suggestion=(
                        f"List part topology with entity_types=['{required_type}'] and retry."
                    ),
                )
            preflight_diagnostics = (
                diagnostics_factory(entities) if diagnostics_factory is not None else None
            )
            feature: EntityRef | None = None
            updated_document: Any | None = None

            def record_feature(result: Any) -> None:
                nonlocal feature, updated_document
                updated_document = self.sessions.bump_document_revision(
                    session, request.document_id
                )
                try:
                    feature = self.sessions.record_feature(
                        session, request.document_id, self._as_dict(result.data)
                    )
                except SketchStateError:
                    feature = None

            result = await self._call_document_adapter(
                session,
                document,
                lambda: create_feature(entities),
                operation_id=(
                    "create_chamfer"
                    if getattr(request, "chamfer_type", None) is not None
                    else None
                ),
                on_success=record_feature,
            )
            if isinstance(result, OperationResult):
                if isinstance(preflight_diagnostics, dict) and result.status is OperationStatus.FAILED:
                    result.data.setdefault("diagnostics", {})["preflight"] = (
                        preflight_diagnostics
                    )
                return result
            if feature is None or updated_document is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a feature but did not return a usable feature reference.",
                    suggestion="Inspect the active part and refresh the topology listing before retrying.",
                )
            response_data = {
                "feature": feature.model_dump(),
                "document": updated_document.model_dump(),
            }
            adapter_data = self._as_dict(result.data)
            diagnostics = adapter_data.get("diagnostics") or (
                (getattr(result, "metadata", None) or {}).get("diagnostics")
            )
            if isinstance(preflight_diagnostics, dict) or isinstance(diagnostics, dict):
                combined_diagnostics: dict[str, Any] = {}
                if isinstance(preflight_diagnostics, dict):
                    combined_diagnostics["preflight"] = preflight_diagnostics
                if isinstance(diagnostics, dict):
                    combined_diagnostics.update(diagnostics)
                response_data["diagnostics"] = combined_diagnostics
            return OperationResult.succeeded(data=response_data, route=OperationRoute.COM)

    async def _create_feature_from_trusted_inputs(
        self,
        request: Any,
        *,
        resolve: Any,
        create_feature: Any,
        stale_error: OperationResult,
    ) -> OperationResult:
        """Create a part feature only from current server-owned capabilities."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        created_feature: EntityRef | None = None
        updated_document: Any | None = None
        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
            except SessionLookupError:
                return self._document_not_found()
            part_failure = self._require_part_document(document)
            if part_failure is not None:
                return part_failure
            if session.active_sketch is not None:
                return self._sketch_not_ready(
                    "exit the active sketch before creating a part feature"
                )
            try:
                params = resolve(session)
            except SessionLookupError:
                return self._document_not_found()
            except SketchStateError:
                return stale_error

            def revalidate() -> OperationResult | None:
                try:
                    resolve(session)
                except SessionLookupError:
                    return self._document_not_found()
                except SketchStateError:
                    return stale_error
                return None

            def record_feature(result: Any) -> None:
                nonlocal created_feature, updated_document
                updated_document = self.sessions.bump_document_revision(
                    session, request.document_id
                )
                try:
                    created_feature = self.sessions.record_feature(
                        session, request.document_id, self._as_dict(result.data)
                    )
                except SketchStateError:
                    created_feature = None

            result = await self._call_document_adapter(
                session,
                document,
                lambda: create_feature(params),
                on_success=record_feature,
                pre_call=revalidate,
            )
            if isinstance(result, OperationResult):
                return result
            if created_feature is None or updated_document is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a feature but did not return a usable feature reference.",
                    suggestion="Inspect the active part in SolidWorks and recreate the workflow session before continuing.",
                )
            return OperationResult.succeeded(
                data={
                    "feature": created_feature.model_dump(),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def _create_feature_from_exited_sketch_and_part_face(
        self,
        request: Any,
        *,
        face_id: str,
        create_feature: Any,
    ) -> OperationResult:
        """Create a feature requiring one exited sketch and one current face capability."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
                sketch = self.sessions.require_exited_sketch(
                    session, request.document_id, request.sketch_id
                )
                face = self.sessions.resolve_part_topology(
                    session, request.document_id, face_id
                )
            except SessionLookupError:
                return self._document_not_found()
            except SketchStateError:
                return self._sketch_not_ready(
                    "create the wrap sketch, exit it, then create the wrap feature"
                )
            except PartTopologyReferenceError as exc:
                return self._part_topology_reference_error(exc.reason)
            part_failure = self._require_part_document(document)
            if part_failure is not None:
                return part_failure
            if session.active_sketch is not None:
                return self._sketch_not_ready(
                    "exit the active sketch before creating this part feature"
                )
            if face.entity_type != "face":
                return OperationResult.failed(
                    code="SW_PART_TOPOLOGY_TYPE_MISMATCH",
                    message="This feature requires a server-issued part face ID.",
                    suggestion="List part topology with entity_types=['face'] and retry.",
                )
            feature: EntityRef | None = None
            updated_document: Any | None = None

            def revalidate() -> OperationResult | None:
                try:
                    self.sessions.require_exited_sketch(
                        session, request.document_id, request.sketch_id
                    )
                    current_face = self.sessions.resolve_part_topology(
                        session, request.document_id, face_id
                    )
                except SessionLookupError:
                    return self._document_not_found()
                except SketchStateError:
                    return self._sketch_not_ready(
                        "create the wrap sketch, exit it, then create the wrap feature"
                    )
                except PartTopologyReferenceError as exc:
                    return self._part_topology_reference_error(exc.reason)
                if current_face.entity_type != "face":
                    return OperationResult.failed(
                        code="SW_PART_TOPOLOGY_TYPE_MISMATCH",
                        message="This feature requires a server-issued part face ID.",
                        suggestion="List part topology with entity_types=['face'] and retry.",
                    )
                return None

            def record_feature(result: Any) -> None:
                nonlocal feature, updated_document
                updated_document = self.sessions.bump_document_revision(
                    session, request.document_id
                )
                try:
                    feature = self.sessions.record_feature(
                        session, request.document_id, self._as_dict(result.data)
                    )
                except SketchStateError:
                    feature = None

            result = await self._call_document_adapter(
                session,
                document,
                lambda: create_feature(sketch.display_name, face.persistent_reference),
                on_success=record_feature,
                pre_call=revalidate,
            )
            if isinstance(result, OperationResult):
                return result
            if feature is None or updated_document is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a feature but did not return a usable feature reference.",
                    suggestion="Inspect the active part and refresh the topology listing before retrying.",
                )
            return OperationResult.succeeded(
                data={
                    "feature": feature.model_dump(),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def _create_feature_from_exited_sketch(
        self, request: Any, *, params: Any, create_feature: Any
    ) -> OperationResult:
        """Create a revision-bound feature from the session's last exited sketch."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
                self.sessions.require_extrusion_sketch(
                    session, request.document_id, request.sketch_id
                )
            except SessionLookupError:
                return self._document_not_found()
            except SketchStateError:
                return self._sketch_not_ready(
                    "call create_sketch, add geometry, and exit_sketch before creating a feature"
                )
            feature: EntityRef | None = None
            updated_document: Any | None = None

            def record_feature(result: Any) -> None:
                nonlocal feature, updated_document
                updated_document = self.sessions.bump_document_revision(
                    session, request.document_id
                )
                try:
                    feature = self.sessions.record_feature(
                        session, request.document_id, self._as_dict(result.data)
                    )
                except SketchStateError:
                    feature = None

            result = await self._call_document_adapter(
                session,
                document,
                lambda: create_feature(params),
                on_success=record_feature,
            )
            if isinstance(result, OperationResult):
                return result
            if feature is None or updated_document is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a feature but did not return a usable feature reference.",
                    suggestion="Inspect the active part in SolidWorks and recreate the workflow session before continuing.",
                )
            return OperationResult.succeeded(
                data={
                    "feature": feature.model_dump(),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def _create_feature_from_exited_sketches(
        self,
        request: Any,
        *,
        sketch_ids: list[str],
        params: Any,
        create_feature: Any,
    ) -> OperationResult:
        """Create a feature only from sketches previously exited in this session."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._document_not_found()

        async with session.operation_lock:
            try:
                document = self.sessions.require_active_document(
                    session, request.document_id
                )
                if session.active_sketch is not None:
                    raise SketchStateError("a sketch is still active")
                for sketch_id in sketch_ids:
                    self.sessions.require_exited_sketch(
                        session, request.document_id, sketch_id
                    )
            except SessionLookupError:
                return self._document_not_found()
            except SketchStateError:
                return self._sketch_not_ready(
                    "create each required sketch in this session and exit every sketch before creating the feature"
                )
            feature: EntityRef | None = None
            updated_document: Any | None = None

            def record_feature(result: Any) -> None:
                nonlocal feature, updated_document
                updated_document = self.sessions.bump_document_revision(
                    session, request.document_id
                )
                try:
                    feature = self.sessions.record_feature(
                        session, request.document_id, self._as_dict(result.data)
                    )
                except SketchStateError:
                    feature = None

            result = await self._call_document_adapter(
                session,
                document,
                lambda: create_feature(params),
                on_success=record_feature,
            )
            if isinstance(result, OperationResult):
                return result
            if feature is None or updated_document is None:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a feature but did not return a usable feature reference.",
                    suggestion="Inspect the active part in SolidWorks and recreate the workflow session before continuing.",
                )
            return OperationResult.succeeded(
                data={
                    "feature": feature.model_dump(),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def export_file(self, request: ExportFileRequest) -> OperationResult:
        """Export the active document beneath the configured artifact root only."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()

        operation_id = f"op_{uuid4().hex}"
        try:
            target = self.artifact_store.prepare_export_path(
                session_id=request.session_id,
                operation_id=operation_id,
                artifact_name=request.artifact_name,
                format_type=request.format_type,
            )
        except (OSError, ValueError):
            return OperationResult.failed(
                operation_id=operation_id,
                code="SW_EXPORT_TARGET_UNAVAILABLE",
                message="The requested export target could not be prepared safely.",
                suggestion="Use a simple artifact name and ask the server administrator to verify artifact directory permissions.",
            )
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.export_file(str(target), request.format_type),
                operation_id=operation_id,
            )
            if isinstance(result, OperationResult):
                return result
            try:
                artifact = self.artifact_store.describe(
                    target, kind=request.format_type
                )
            except FileNotFoundError:
                return OperationResult.failed(
                    operation_id=operation_id,
                    code="SW_EXPORT_ARTIFACT_MISSING",
                    message="SolidWorks reported a successful export but no output file was created.",
                    suggestion="Check the active document and the SolidWorks export settings, then retry.",
                )
            except (OSError, ValueError):
                return OperationResult.failed(
                    operation_id=operation_id,
                    code="SW_EXPORT_ARTIFACT_UNAVAILABLE",
                    message="The generated export artifact could not be read safely.",
                    suggestion="Ask the server administrator to verify artifact directory permissions and retry.",
                )
            return OperationResult.succeeded(
                operation_id=operation_id,
                data={"artifact": artifact.model_dump()},
                artifacts=[artifact],
                route=OperationRoute.COM,
            )

    async def get_sheet_metal_info(self, request: DocumentRequest) -> OperationResult:
        """Read sheet-metal state from the active part without exposing COM features."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.get_sheet_metal_info
            )
            if isinstance(result, OperationResult):
                return result
            try:
                sheet_metal = self._sheet_metal_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable sheet-metal state.",
                    suggestion="Rebuild the active part in SolidWorks and retry.",
                )
            return OperationResult.succeeded(
                data={"sheet_metal": sheet_metal.model_dump()}
            )

    async def set_sheet_metal_flat_state(
        self, request: SheetMetalFlatStateRequest
    ) -> OperationResult:
        """Explicitly flatten or refold the active sheet-metal part."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.set_sheet_metal_flat_state(request.flattened),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                sheet_metal = self._sheet_metal_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks changed sheet-metal state but did not return usable state.",
                    suggestion="Inspect the active part in SolidWorks before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "sheet_metal": sheet_metal.model_dump(),
                    "document": updated_document.model_dump(),
                }
            )

    async def export_flat_pattern_dxf(
        self, request: ExportFlatPatternDxfRequest
    ) -> OperationResult:
        """Export the active saved sheet-metal part as a controlled flat-pattern DXF."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        source_path = self._trusted_sheet_metal_source_path(document)
        if isinstance(source_path, OperationResult):
            return source_path

        operation_id = f"op_{uuid4().hex}"
        try:
            target = self.artifact_store.prepare_export_path(
                session_id=request.session_id,
                operation_id=operation_id,
                artifact_name=request.artifact_name,
                format_type="dxf",
            )
        except (OSError, ValueError):
            return OperationResult.failed(
                operation_id=operation_id,
                code="SW_EXPORT_TARGET_UNAVAILABLE",
                message="The requested flat-pattern DXF target could not be prepared safely.",
                suggestion="Use a simple .dxf artifact name and verify artifact directory permissions.",
            )

        sheet_metal_options = self._sheet_metal_export_options(request)
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.export_flat_pattern_dxf(
                    str(target), str(source_path), sheet_metal_options
                ),
                operation_id=operation_id,
            )
            if isinstance(result, OperationResult):
                return result
            try:
                artifact = self.artifact_store.describe(target, kind="dxf")
            except FileNotFoundError:
                return OperationResult.failed(
                    operation_id=operation_id,
                    code="SW_EXPORT_ARTIFACT_MISSING",
                    message="SolidWorks reported a successful flat-pattern export but no DXF was created.",
                    suggestion="Verify the part is a valid sheet-metal model and retry.",
                )
            except (OSError, ValueError):
                return OperationResult.failed(
                    operation_id=operation_id,
                    code="SW_EXPORT_ARTIFACT_UNAVAILABLE",
                    message="The generated flat-pattern DXF could not be read safely.",
                    suggestion="Verify artifact directory permissions and retry.",
                )
            return OperationResult.succeeded(
                operation_id=operation_id,
                data={
                    "artifact": artifact.model_dump(),
                    "sheet_metal_options": sheet_metal_options,
                },
                artifacts=[artifact],
                route=OperationRoute.COM,
            )

    async def save_model(self, request: SaveModelRequest) -> OperationResult:
        """Save the active native document to a controlled artifact path."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()

        operation_id = f"op_{uuid4().hex}"
        try:
            target = self.artifact_store.prepare_native_save_path(
                session_id=request.session_id,
                operation_id=operation_id,
                artifact_name=request.artifact_name,
                document_type=document.document_type,
            )
        except (OSError, ValueError):
            return OperationResult.failed(
                operation_id=operation_id,
                code="SW_NATIVE_SAVE_REJECTED",
                message="The requested native save target is outside the allowed output policy.",
                suggestion="Use a simple artifact name and ask the server administrator to verify artifact directory permissions.",
            )

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.save_file(str(target)),
                operation_id=operation_id,
            )
            if isinstance(result, OperationResult):
                return result
            try:
                artifact = self.artifact_store.describe(
                    target, kind=target.suffix.lstrip(".").lower()
                )
            except FileNotFoundError:
                return OperationResult.failed(
                    operation_id=operation_id,
                    code="SW_NATIVE_SAVE_ARTIFACT_MISSING",
                    message="SolidWorks reported a successful save but no native model file was created.",
                    suggestion="Check the active document save state and SolidWorks file permissions, then retry.",
                )
            except (OSError, ValueError):
                return OperationResult.failed(
                    operation_id=operation_id,
                    code="SW_NATIVE_SAVE_ARTIFACT_UNAVAILABLE",
                    message="The generated native model file could not be read safely.",
                    suggestion="Ask the server administrator to verify artifact directory permissions and retry.",
                )
            updated_document = self.sessions.record_native_save(
                session, request.document_id, str(target)
            )
            return OperationResult.succeeded(
                operation_id=operation_id,
                data={
                    "document": updated_document.model_dump(),
                    "artifact": artifact.model_dump(),
                },
                artifacts=[artifact],
                route=OperationRoute.COM,
            )

    async def get_model_info(self, request: DocumentRequest) -> OperationResult:
        """Read concise metadata for the active session document."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.get_model_info
            )
            if isinstance(result, OperationResult):
                return result
            return OperationResult.succeeded(data={"model": self._as_dict(result.data)})

    async def get_mass_properties(self, request: DocumentRequest) -> OperationResult:
        """Read mass properties for the active session document."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.get_mass_properties
            )
            if isinstance(result, OperationResult):
                return result
            return OperationResult.succeeded(
                data={"mass_properties": self._as_dict(result.data)}
            )

    async def get_bounding_box(self, request: GetBoundingBoxRequest) -> OperationResult:
        """Read the active part's axis-aligned bounding box in millimetres."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.get_bounding_box
            )
            if isinstance(result, OperationResult):
                return result
            try:
                bounding_box = BoundingBoxSummary(**self._as_dict(result.data))
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return a usable part bounding box.",
                    suggestion="Rebuild the active part in SolidWorks and retry the bounding-box operation.",
                )
            return OperationResult.succeeded(
                data={"bounding_box": bounding_box.model_dump()}
            )

    async def list_dimensions(self, request: ListDimensionsRequest) -> OperationResult:
        """List active-part dimensions as revision-bound opaque identifiers."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_failure = self._require_part_document(document)
        if part_failure is not None:
            return part_failure
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.list_dimensions
            )
            if isinstance(result, OperationResult):
                return result
            try:
                native_dimensions = self._native_dimension_rows(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable model dimension data.",
                    suggestion="Rebuild the active part and retry dimension discovery.",
                )
            self.sessions.begin_dimension_listing(session, document.document_id)
            dimensions = [
                DimensionSummary(
                    dimension_id=self.sessions.issue_dimension(
                        session,
                        document.document_id,
                        native_name=native_name,
                    ).dimension_id,
                    value_mm=value_mm,
                )
                for native_name, value_mm in native_dimensions
            ]
            return OperationResult.succeeded(
                data={
                    **self._paginate(dimensions, request.limit, request.offset),
                    "dimensions": [
                        dimension.model_dump()
                        for dimension in dimensions[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                },
                route=OperationRoute.COM,
            )

    async def get_dimension(self, request: GetDimensionRequest) -> OperationResult:
        """Read one current opaque dimension without accepting a native name."""
        resolved = self._resolve_dimension(request)
        if isinstance(resolved, OperationResult):
            return resolved
        session, document, dimension = resolved
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_dimension(dimension.native_name),
            )
            if isinstance(result, OperationResult):
                return result
            value_mm = self._dimension_value(result.data)
            if value_mm is None:
                return self._invalid_dimension_response("read")
            return OperationResult.succeeded(
                data={
                    "dimension": {
                        "dimension_id": dimension.dimension_id,
                        "value_mm": value_mm,
                    }
                },
                route=OperationRoute.COM,
            )

    async def set_dimension(self, request: SetDimensionRequest) -> OperationResult:
        """Set, rebuild, and read back one opaque model dimension."""
        resolved = self._resolve_dimension(request)
        if isinstance(resolved, OperationResult):
            return resolved
        session, document, dimension = resolved
        async with session.operation_lock:
            changed = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.set_dimension(
                    dimension.native_name, request.value
                ),
            )
            if isinstance(changed, OperationResult):
                return changed
            readback = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_dimension(dimension.native_name),
            )
            if isinstance(readback, OperationResult):
                return readback
            value_mm = self._dimension_value(readback.data)
            if value_mm is None:
                return self._invalid_dimension_response("change")
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            updated_dimension = self.sessions.issue_dimension(
                session,
                document.document_id,
                native_name=dimension.native_name,
            )
            return OperationResult.succeeded(
                data={
                    "dimension": {
                        "dimension_id": updated_dimension.dimension_id,
                        "value_mm": value_mm,
                    },
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def get_feature_dimension(
        self, request: GetFeatureDimensionRequest
    ) -> OperationResult:
        """Read a driving dimension through a current opaque feature reference."""
        resolved = self._resolve_feature_dimension(request)
        if isinstance(resolved, OperationResult):
            return resolved
        session, document, native_dimension_name = resolved
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_dimension(native_dimension_name),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                value_mm = float(result.data)
            except (TypeError, ValueError):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return a usable feature dimension value.",
                    suggestion="Rebuild the active part and retry the dimension read.",
                )
            if not math.isfinite(value_mm):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks returned a non-finite feature dimension value.",
                    suggestion="Inspect the feature dimension in SolidWorks and retry.",
                )
            return OperationResult.succeeded(
                data={
                    "dimension": {
                        "index": request.dimension_index,
                        "value_mm": value_mm,
                    }
                },
                route=OperationRoute.COM,
            )

    async def set_feature_dimension(
        self, request: SetFeatureDimensionRequest
    ) -> OperationResult:
        """Set and read back a feature dimension without exposing native names."""
        resolved = self._resolve_feature_dimension(request)
        if isinstance(resolved, OperationResult):
            return resolved
        session, document, native_dimension_name = resolved
        async with session.operation_lock:
            changed = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.set_dimension(
                    native_dimension_name, request.value
                ),
            )
            if isinstance(changed, OperationResult):
                return changed
            readback = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_dimension(native_dimension_name),
            )
            if isinstance(readback, OperationResult):
                return readback
            try:
                value_mm = float(readback.data)
            except (TypeError, ValueError):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks changed a feature dimension but did not return a usable readback.",
                    suggestion="Inspect the feature dimension in SolidWorks before continuing.",
                )
            if not math.isfinite(value_mm):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks returned a non-finite feature dimension value.",
                    suggestion="Inspect the feature dimension in SolidWorks before continuing.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "dimension": {
                        "index": request.dimension_index,
                        "value_mm": value_mm,
                    },
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def list_equations(self, request: ListEquationsRequest) -> OperationResult:
        """List a bounded page of active-part equations and global variables."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.list_equations
            )
            if isinstance(result, OperationResult):
                return result
            try:
                equations = self._equation_summaries(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable equation data.",
                    suggestion="Inspect the active part equations in SolidWorks and retry.",
                )
            return OperationResult.succeeded(
                data={
                    **self._paginate(equations, request.limit, request.offset),
                    "equations": [
                        equation.model_dump()
                        for equation in equations[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def get_equation(self, request: GetEquationRequest) -> OperationResult:
        """Read one active-part equation or global variable by its name."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.list_equations
            )
            if isinstance(result, OperationResult):
                return result
            try:
                equation = next(
                    (
                        item
                        for item in self._equation_summaries(result.data)
                        if item.name.casefold() == request.name.casefold()
                    ),
                    None,
                )
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable equation data.",
                    suggestion="Inspect the active part equations in SolidWorks and retry.",
                )
            if equation is None:
                return OperationResult.failed(
                    code="SW_EQUATION_NOT_FOUND",
                    message="The named equation or global variable does not exist.",
                    suggestion="List equations for the active part and use one returned name.",
                )
            return OperationResult.succeeded(data={"equation": equation.model_dump()})

    async def set_equation(self, request: SetEquationRequest) -> OperationResult:
        """Create or update one active-part equation, then return its readback."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.set_equation(request.name, request.expression),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                equation = self._equation_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks changed an equation but did not return a usable readback.",
                    suggestion="Inspect the active part equations in SolidWorks before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "equation": equation.model_dump(),
                    "document": updated_document.model_dump(),
                }
            )

    async def delete_equation(self, request: DeleteEquationRequest) -> OperationResult:
        """Delete one active-part equation or global variable after COM verification."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.delete_equation(request.name),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                equation = self._equation_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks deleted an equation but did not return a usable readback.",
                    suggestion="Inspect the active part equations in SolidWorks before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "equation": equation.model_dump(),
                    "document": updated_document.model_dump(),
                }
            )

    async def list_solid_bodies(
        self, request: ListSolidBodiesRequest
    ) -> OperationResult:
        """List a bounded, serializable page of solid bodies in an active part."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.list_solid_bodies(request.include_hidden),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                bodies = self._solid_body_summaries(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable solid-body data.",
                    suggestion="Rebuild the part in SolidWorks and retry the body listing operation.",
                )
            return OperationResult.succeeded(
                data={
                    **self._paginate(bodies, request.limit, request.offset),
                    "bodies": [
                        body.model_dump()
                        for body in bodies[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def list_surface_bodies(
        self, request: ListSurfaceBodiesRequest
    ) -> OperationResult:
        """List a bounded, serializable page of surface bodies in an active part."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.list_surface_bodies(request.include_hidden),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                bodies = self._solid_body_summaries(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable surface-body data.",
                    suggestion="Rebuild the part in SolidWorks and retry the body listing operation.",
                )
            return OperationResult.succeeded(
                data={
                    **self._paginate(bodies, request.limit, request.offset),
                    "bodies": [
                        body.model_dump()
                        for body in bodies[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def list_part_topology(
        self, request: ListPartTopologyRequest
    ) -> OperationResult:
        """Issue opaque current face and edge IDs for safe part feature operations."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.list_part_topology(tuple(request.entity_types)),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                candidates = self._part_topology_candidates(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable part topology references.",
                    suggestion="Rebuild the part in SolidWorks and retry the topology listing.",
                )
            page = candidates[request.offset : request.offset + request.limit]
            try:
                # Validate every public geometry row before issuing any opaque
                # capability, so a malformed adapter response cannot leave
                # partially-issued session state behind.
                summaries = [
                    PartTopologyEntitySummary(
                        entity_id="pending_topology_entity",
                        entity_type=cast(
                            Literal["face", "edge", "vertex"], candidate["entity_type"]
                        ),
                        display_name=candidate["display_name"],
                        area_mm2=candidate.get("area_mm2"),
                        normal=candidate.get("normal"),
                        length_mm=candidate.get("length_mm"),
                        curve_identity=candidate.get("curve_identity"),
                        point_mm=candidate.get("point_mm"),
                    )
                    for candidate in page
                ]
            except (TypeError, ValueError):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable part topology geometry.",
                    suggestion="Rebuild the part in SolidWorks and retry the topology listing.",
                )
            self.sessions.begin_part_topology_listing(session, request.document_id)
            entities = []
            for candidate, summary in zip(page, summaries, strict=True):
                issued = self.sessions.issue_part_topology(
                    session,
                    request.document_id,
                    entity_type=candidate["entity_type"],
                    display_name=candidate["display_name"],
                    persistent_reference=candidate["persistent_reference"],
                    length_mm=candidate.get("length_mm"),
                    curve_identity=candidate.get("curve_identity"),
                )
                entities.append(summary.model_copy(update={"entity_id": issued.entity_id}))
            return OperationResult.succeeded(
                data={
                    **self._paginate(candidates, request.limit, request.offset),
                    "entities": [
                        entity.model_dump(exclude_none=True) for entity in entities
                    ],
                }
            )

    async def list_sheet_metal_feature_geometry(
        self, request: ListSheetMetalFeatureGeometryRequest
    ) -> OperationResult:
        """Issue opaque face IDs for current flat-pattern and bend features."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                self.adapter.list_sheet_metal_feature_geometry,
            )
            if isinstance(result, OperationResult):
                return result
            try:
                candidates = list(result.data)
                if not all(isinstance(candidate, dict) for candidate in candidates):
                    raise ValueError("feature geometry must be an object list")
                for candidate in candidates:
                    if (
                        not isinstance(candidate.get("display_name"), str)
                        or candidate.get("feature_kind") not in {"bend", "flat_pattern"}
                        or not isinstance(candidate.get("faces"), list)
                    ):
                        raise ValueError("feature geometry is malformed")
                    for face in candidate["faces"]:
                        if (
                            not isinstance(face, dict)
                            or not isinstance(face.get("display_name"), str)
                            or not isinstance(face.get("persistent_reference"), str)
                            or not face["display_name"].strip()
                            or not face["persistent_reference"].strip()
                        ):
                            raise ValueError("feature face reference is malformed")
            except (TypeError, ValueError):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable sheet-metal feature geometry.",
                    suggestion="Rebuild the sheet-metal part and retry the geometry listing.",
                )

            self.sessions.begin_part_topology_listing(session, request.document_id)
            page = candidates[request.offset : request.offset + request.limit]
            features = []
            for candidate in page:
                face_ids = [
                    self.sessions.issue_part_topology(
                        session,
                        request.document_id,
                        entity_type="face",
                        display_name=face["display_name"],
                        persistent_reference=face["persistent_reference"],
                    ).entity_id
                    for face in candidate["faces"]
                ]
                features.append(
                    SheetMetalFeatureGeometrySummary(
                        display_name=candidate["display_name"],
                        feature_kind=cast(
                            Literal["bend", "flat_pattern"],
                            candidate["feature_kind"],
                        ),
                        face_ids=face_ids,
                    )
                )
            return OperationResult.succeeded(
                data={
                    **self._paginate(candidates, request.limit, request.offset),
                    "features": [feature.model_dump() for feature in features],
                }
            )

    async def insert_sheet_metal_bend_notes(
        self, request: InsertSheetMetalBendNotesRequest
    ) -> OperationResult:
        """Generate bend notes from the active part's server-resolved flat pattern."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                self.adapter.insert_sheet_metal_bend_notes,
            )
            if isinstance(result, OperationResult):
                return result
            try:
                notes = SheetMetalBendNotesSummary.model_validate(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks inserted bend notes but did not return usable confirmation data.",
                    suggestion="Inspect the active part in SolidWorks before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "bend_notes": notes.model_dump(),
                    "document": updated_document.model_dump(),
                }
            )

    async def insert_sheet_metal_bend_table(
        self, request: InsertSheetMetalBendTableRequest
    ) -> OperationResult:
        """Generate a default-template bend table at a bounded model position."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.insert_sheet_metal_bend_table(
                    self._millimetres(request.x, request.unit) / 1000,
                    self._millimetres(request.y, request.unit) / 1000,
                    request.start_value,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                bend_table = SheetMetalBendTableSummary.model_validate(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks inserted a bend table but did not return usable table data.",
                    suggestion="Check the configured SolidWorks bend-table template and retry.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "bend_table": bend_table.model_dump(exclude_none=True),
                    "document": updated_document.model_dump(),
                }
            )

    async def get_material_assignment(
        self, request: DocumentRequest
    ) -> OperationResult:
        """Read the tracked active configuration's material assignment."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error
        configuration_name = self._active_configuration(document)
        if isinstance(configuration_name, OperationResult):
            return configuration_name

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_material_assignment(configuration_name),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                material = self._material_assignment_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable material assignment data.",
                    suggestion="Inspect the active configuration material in SolidWorks and retry.",
                )
            return OperationResult.succeeded(
                data={
                    "configuration": configuration_name,
                    "material": material.model_dump(),
                }
            )

    async def set_material_assignment(
        self, request: SetMaterialAssignmentRequest
    ) -> OperationResult:
        """Set and verify one material assignment in the tracked active configuration."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        part_error = self._require_part_document(document)
        if part_error is not None:
            return part_error
        configuration_name = self._active_configuration(document)
        if isinstance(configuration_name, OperationResult):
            return configuration_name

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.set_material_assignment(
                    configuration_name,
                    request.database_name,
                    request.material_name,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                material = self._material_assignment_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks updated the material but did not return usable assignment data.",
                    suggestion="Inspect the active configuration material in SolidWorks and retry.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "configuration": configuration_name,
                    "material": material.model_dump(),
                    "document": updated_document.model_dump(),
                }
            )

    async def list_features(self, request: ListFeaturesRequest) -> OperationResult:
        """List a bounded, client-safe feature tree for the active document."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.list_features(request.include_suppressed),
            )
            if isinstance(result, OperationResult):
                return result
            features = self._feature_summaries(result.data)
            self.sessions.begin_feature_listing(session, document.document_id)
            issued_features = [
                feature.model_copy(
                    update={
                        "feature_id": self.sessions.issue_listed_feature(
                            session, document.document_id, feature.name
                        ).persistent_id
                    }
                )
                for feature in features
            ]
            return OperationResult.succeeded(
                data={
                    **self._paginate(issued_features, request.limit, request.offset),
                    "features": [
                        feature.model_dump()
                        for feature in issued_features[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def list_configurations(
        self, request: ListConfigurationsRequest
    ) -> OperationResult:
        """List bounded configuration metadata for the active document."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.list_configurations
            )
            if isinstance(result, OperationResult):
                return result
            configurations = self._configuration_summaries(
                result.data, document.configuration
            )
            return OperationResult.succeeded(
                data={
                    **self._paginate(configurations, request.limit, request.offset),
                    "configurations": [
                        configuration.model_dump()
                        for configuration in configurations[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def get_configuration(
        self, request: GetConfigurationRequest
    ) -> OperationResult:
        """Read one exact configuration without changing active-document state."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.get_configuration(request.configuration_name),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                configuration = self._configuration_detail_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable configuration metadata.",
                    suggestion="List configurations and retry with a current exact configuration name.",
                )
            return OperationResult.succeeded(
                data={"configuration": configuration.model_dump()}
            )

    async def activate_configuration(
        self, request: ActivateConfigurationRequest
    ) -> OperationResult:
        """Switch configuration only after reactivating the tracked document."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.activate_configuration(request.configuration_name),
            )
            if isinstance(result, OperationResult):
                return result
            configuration_name = str(result.data or request.configuration_name)
            updated_document = self.sessions.activate_configuration(
                session, request.document_id, configuration_name
            )
            return OperationResult.succeeded(
                data={
                    "configuration": {"name": configuration_name},
                    "document": updated_document.model_dump(),
                }
            )

    async def create_configuration(
        self, request: CreateConfigurationRequest
    ) -> OperationResult:
        """Create and activate a configuration after reactivating its document."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.create_configuration(
                    request.configuration_name, request.description
                ),
            )
            if isinstance(result, OperationResult):
                return result
            configuration_name = str(result.data or request.configuration_name).strip()
            if not configuration_name:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a configuration but did not report its name.",
                    suggestion="Inspect the active configuration in SolidWorks and retry the operation.",
                )
            updated_document = self.sessions.activate_configuration(
                session, request.document_id, configuration_name
            )
            return OperationResult.succeeded(
                data={
                    "configuration": {"name": configuration_name},
                    "document": updated_document.model_dump(),
                }
            )

    async def delete_configuration(
        self, request: DeleteConfigurationRequest
    ) -> OperationResult:
        """Delete a non-active configuration and invalidate stale document references."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if (
            document.configuration or ""
        ).casefold() == request.configuration_name.casefold():
            return OperationResult.failed(
                code="SW_ACTIVE_CONFIGURATION_DELETE_REJECTED",
                message="The active configuration cannot be deleted.",
                suggestion="Activate a different configuration, then retry the deletion.",
            )

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.delete_configuration(request.configuration_name),
            )
            if isinstance(result, OperationResult):
                return result
            deleted_name = str(result.data or request.configuration_name).strip()
            if not deleted_name:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks deleted a configuration but did not report its name.",
                    suggestion="List configurations to confirm the document state before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "configuration": {"name": deleted_name},
                    "document": updated_document.model_dump(),
                }
            )

    async def insert_assembly_component(
        self, request: InsertAssemblyComponentRequest
    ) -> OperationResult:
        """Insert a saved session-owned part into the active assembly document."""
        try:
            session = self.sessions.require(request.session_id)
            assembly_document = self.sessions.require_active_document(
                session, request.document_id
            )
            source_document = self.sessions.require_document(
                session, request.source_part_document_id
            )
        except SessionLookupError:
            return self._document_not_found()

        if assembly_document.document_type != "assembly":
            return OperationResult.failed(
                code="SW_ASSEMBLY_REQUIRED",
                message="Assembly component insertion requires an active assembly document.",
                suggestion="Create or activate an assembly document in this workflow session and retry.",
            )

        source_path = self._trusted_component_source_path(source_document)
        if isinstance(source_path, OperationResult):
            return source_path

        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        z_mm = round(self._millimetres(request.z, request.unit), 10)
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                assembly_document,
                lambda: self.adapter.insert_assembly_component(
                    str(source_path), x_mm / 1000, y_mm / 1000, z_mm / 1000
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                component = self._assembly_component_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks inserted a component but did not return a usable component reference.",
                    suggestion="Inspect the assembly in SolidWorks and retry the component listing operation.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, assembly_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "component": component.model_dump(),
                    "position_mm": {"x": x_mm, "y": y_mm, "z": z_mm},
                    "document": updated_document.model_dump(),
                }
            )

    async def list_assembly_components(
        self, request: ListAssemblyComponentsRequest
    ) -> OperationResult:
        """Return one bounded, client-safe page of active assembly components."""
        try:
            session = self.sessions.require(request.session_id)
            assembly_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if assembly_document.document_type != "assembly":
            return OperationResult.failed(
                code="SW_ASSEMBLY_REQUIRED",
                message="Assembly component listing requires an active assembly document.",
                suggestion="Create or activate an assembly document in this workflow session and retry.",
            )

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, assembly_document, self.adapter.list_assembly_components
            )
            if isinstance(result, OperationResult):
                return result
            try:
                components = self._assembly_component_summaries(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable assembly component references.",
                    suggestion="Rebuild the assembly in SolidWorks and retry the component listing operation.",
                )
            return OperationResult.succeeded(
                data={
                    **self._paginate(components, request.limit, request.offset),
                    "components": [
                        component.model_dump()
                        for component in components[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def get_assembly_component(
        self, request: GetAssemblyComponentRequest
    ) -> OperationResult:
        """Find one exact-name component in the active assembly document."""
        try:
            session = self.sessions.require(request.session_id)
            assembly_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if assembly_document.document_type != "assembly":
            return self._assembly_required("component lookup")

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                assembly_document,
                lambda: self.adapter.get_assembly_component(request.component_name),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                component = self._assembly_component_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks returned a component without a usable identity.",
                    suggestion="List assembly components to inspect the current assembly state.",
                )
            return OperationResult.succeeded(data={"component": component.model_dump()})

    async def check_assembly_interference(
        self, request: CheckAssemblyInterferenceRequest
    ) -> OperationResult:
        """Run real native interference detection for the active session assembly."""
        try:
            session = self.sessions.require(request.session_id)
            assembly_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if assembly_document.document_type != "assembly":
            return self._assembly_required("interference detection")

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                assembly_document,
                lambda: self.adapter.check_assembly_interference(
                    request.include_coincident,
                    request.include_multibody_part_interferences,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            if not isinstance(result.data, (list, tuple)):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return a usable interference result list.",
                    suggestion="Rebuild the assembly in SolidWorks and retry the interference check.",
                )
            try:
                interferences = [
                    AssemblyInterferenceSummary.model_validate(self._as_dict(item))
                    for item in result.data
                ]
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks returned incomplete interference details.",
                    suggestion="Resolve lightweight or suppressed components and retry the interference check.",
                )
            return OperationResult.succeeded(
                data={
                    "total": len(interferences),
                    "interferences": [
                        interference.model_dump() for interference in interferences
                    ],
                },
                route=OperationRoute.COM,
            )

    async def list_assembly_mate_entities(
        self, request: ListAssemblyMateEntitiesRequest
    ) -> OperationResult:
        """Issue revision-bound face and edge IDs for an active assembly."""
        try:
            session = self.sessions.require(request.session_id)
            assembly_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if assembly_document.document_type != "assembly":
            return self._assembly_required("listing mate entities")

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                assembly_document,
                lambda: self.adapter.list_assembly_mate_entities(
                    request.component_id, tuple(request.entity_types)
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                candidates = self._assembly_mate_entity_candidates(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable assembly mate entities.",
                    suggestion="Rebuild the assembly in SolidWorks and retry the geometry listing.",
                )

            self.sessions.begin_assembly_mate_entity_listing(
                session, assembly_document.document_id
            )
            page = candidates[request.offset : request.offset + request.limit]
            entities = [
                AssemblyMateEntitySummary(
                    entity_id=self.sessions.issue_assembly_mate_entity(
                        session,
                        assembly_document.document_id,
                        component_id=candidate["component_id"],
                        entity_type=cast(
                            Literal["face", "edge"], candidate["entity_type"]
                        ),
                        display_name=candidate["display_name"],
                        persistent_reference=candidate["persistent_reference"],
                    ).entity_id,
                    component_id=candidate["component_id"],
                    entity_type=cast(Literal["face", "edge"], candidate["entity_type"]),
                    display_name=candidate["display_name"],
                )
                for candidate in page
            ]
            return OperationResult.succeeded(
                data={
                    **self._paginate(candidates, request.limit, request.offset),
                    "entities": [entity.model_dump() for entity in entities],
                }
            )

    async def create_assembly_mate(
        self, request: CreateAssemblyMateRequest
    ) -> OperationResult:
        """Create one typed ``AddMate5`` constraint from current opaque entity IDs."""
        try:
            session = self.sessions.require(request.session_id)
            assembly_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if assembly_document.document_type != "assembly":
            return self._assembly_required("creating a mate")

        async with session.operation_lock:
            try:
                first_entity = self.sessions.resolve_assembly_mate_entity(
                    session, assembly_document.document_id, request.first_entity_id
                )
                second_entity = self.sessions.resolve_assembly_mate_entity(
                    session, assembly_document.document_id, request.second_entity_id
                )
            except MateEntityReferenceError as exc:
                return self._mate_entity_reference_error(exc.reason)

            distance_m = self._millimetres(request.distance or 0.0, request.unit) / 1000
            angle_radians = math.radians(request.angle or 0.0)
            result = await self._call_document_adapter(
                session,
                assembly_document,
                lambda: self.adapter.create_assembly_mate(
                    first_entity.persistent_reference,
                    second_entity.persistent_reference,
                    mate_type=request.mate_type,
                    alignment=request.alignment,
                    flip=request.flip,
                    distance_m=distance_m,
                    angle_radians=angle_radians,
                    for_positioning_only=request.for_positioning_only,
                    lock_rotation=request.lock_rotation,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                mate = self._assembly_mate_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a mate but did not return a usable summary.",
                    suggestion="Inspect the assembly mate list in SolidWorks before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, assembly_document.document_id
            )
            parameters: dict[str, float | str] = {}
            if request.distance is not None:
                parameters["distance_mm"] = self._millimetres(
                    request.distance, request.unit
                )
            if request.angle is not None:
                parameters["angle_degrees"] = request.angle
            return OperationResult.succeeded(
                data={
                    "mate": mate.model_dump(),
                    "parameters": parameters,
                    "document": updated_document.model_dump(),
                }
            )

    async def list_assembly_mates(
        self, request: ListAssemblyMatesRequest
    ) -> OperationResult:
        """List current assembly mates and issue opaque IDs for deletion."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if document.document_type != "assembly":
            return self._assembly_required("listing mates")
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, document, self.adapter.list_assembly_mates
            )
            if isinstance(result, OperationResult):
                return result
            try:
                candidates = self._assembly_mate_candidates(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable assembly mate references.",
                    suggestion="Rebuild the assembly and retry the mate listing.",
                )
            self.sessions.begin_assembly_mate_listing(session, request.document_id)
            page = candidates[request.offset : request.offset + request.limit]
            mates = [
                AssemblyMateListSummary(
                    mate_id=self.sessions.issue_assembly_mate(
                        session,
                        request.document_id,
                        display_name=candidate["display_name"],
                        mate_type=candidate["mate_type"],
                        persistent_reference=candidate["persistent_reference"],
                    ).mate_id,
                    display_name=candidate["display_name"],
                    mate_type=candidate["mate_type"],
                )
                for candidate in page
            ]
            return OperationResult.succeeded(
                data={
                    **self._paginate(candidates, request.limit, request.offset),
                    "mates": [mate.model_dump() for mate in mates],
                }
            )

    async def delete_assembly_mate(
        self, request: DeleteAssemblyMateRequest
    ) -> OperationResult:
        """Delete one current assembly mate using only its server-issued ID."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if document.document_type != "assembly":
            return self._assembly_required("deleting a mate")
        async with session.operation_lock:
            try:
                mate = self.sessions.resolve_assembly_mate(
                    session, request.document_id, request.mate_id
                )
            except AssemblyMateReferenceError as exc:
                return self._assembly_mate_reference_error(exc.reason)
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.delete_assembly_mate(mate.persistent_reference),
            )
            if isinstance(result, OperationResult):
                return result
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "deleted_mate": {
                        "display_name": str(
                            self._as_dict(result.data).get("display_name")
                            or mate.display_name
                        )[:240]
                    },
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def create_drawing_view(
        self, request: CreateDrawingViewRequest
    ) -> OperationResult:
        """Create one standard view from a saved session model in an active drawing."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
            source_document = self.sessions.require_document(
                session, request.source_model_document_id
            )
        except SessionLookupError:
            return self._document_not_found()

        if drawing_document.document_type != "drawing":
            return OperationResult.failed(
                code="SW_DRAWING_REQUIRED",
                message="Drawing view creation requires an active drawing document.",
                suggestion="Create or activate a drawing document in this workflow session and retry.",
            )
        source_path = self._trusted_drawing_view_source_path(source_document)
        if isinstance(source_path, OperationResult):
            return source_path

        model_view_name = {
            "front": "*Front",
            "back": "*Back",
            "top": "*Top",
            "bottom": "*Bottom",
            "left": "*Left",
            "right": "*Right",
            "isometric": "*Isometric",
            "dimetric": "*Dimetric",
            "trimetric": "*Trimetric",
        }[request.orientation]
        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_drawing_view(
                    str(source_path), model_view_name, x_mm / 1000, y_mm / 1000
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                view = self._drawing_view_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a drawing view but did not return a usable view reference.",
                    suggestion="Inspect the drawing in SolidWorks and retry the drawing view listing operation.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            self.sessions.record_drawing_source_view(
                session,
                drawing_document.document_id,
                source_document.document_id,
                view.view_id,
            )
            return OperationResult.succeeded(
                data={
                    "view": view.model_dump(),
                    "orientation": request.orientation,
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "document": updated_document.model_dump(),
                }
            )

    async def list_drawing_views(
        self, request: ListDrawingViewsRequest
    ) -> OperationResult:
        """Return one bounded, client-safe page of active drawing model views."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if drawing_document.document_type != "drawing":
            return OperationResult.failed(
                code="SW_DRAWING_REQUIRED",
                message="Drawing view listing requires an active drawing document.",
                suggestion="Create or activate a drawing document in this workflow session and retry.",
            )

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, drawing_document, self.adapter.list_drawing_views
            )
            if isinstance(result, OperationResult):
                return result
            try:
                views = self._drawing_view_summaries(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable drawing view references.",
                    suggestion="Rebuild the drawing in SolidWorks and retry the drawing view listing operation.",
                )
            return OperationResult.succeeded(
                data={
                    **self._paginate(views, request.limit, request.offset),
                    "views": [
                        view.model_dump()
                        for view in views[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def list_drawing_sheets(
        self, request: ListDrawingSheetsRequest
    ) -> OperationResult:
        """Return a bounded page of current drawing sheets."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, drawing_document, self.adapter.list_drawing_sheets
            )
            if isinstance(result, OperationResult):
                return result
            try:
                sheets = [
                    self._drawing_sheet_summary(candidate)
                    for candidate in (result.data or [])
                ]
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable drawing-sheet metadata.",
                    suggestion="Inspect the drawing sheets in SolidWorks and retry.",
                )
            page = sheets[request.offset : request.offset + request.limit]
            return OperationResult.succeeded(
                data={
                    **self._paginate(sheets, request.limit, request.offset),
                    "sheets": [sheet.model_dump() for sheet in page],
                }
            )

    async def create_drawing_sheet(
        self, request: CreateDrawingSheetRequest
    ) -> OperationResult:
        """Create one drawing sheet and invalidate revision-bound drawing references."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        standard_sizes = {
            "a0": (1189.0, 841.0),
            "a1": (841.0, 594.0),
            "a2": (594.0, 420.0),
            "a3": (420.0, 297.0),
            "a4": (297.0, 210.0),
        }
        if request.paper_size == "user_defined":
            width_mm = self._millimetres(request.width or 0, request.unit)
            height_mm = self._millimetres(request.height or 0, request.unit)
        else:
            width_mm, height_mm = standard_sizes[request.paper_size]
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_drawing_sheet(
                    request.sheet_name,
                    request.paper_size,
                    width_mm / 1000,
                    height_mm / 1000,
                    request.first_angle_projection,
                    request.scale_numerator,
                    request.scale_denominator,
                    request.zone_rows,
                    request.zone_columns,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                sheet = self._drawing_sheet_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a sheet but did not return usable metadata.",
                    suggestion="List drawing sheets again and retry with the returned sheet name.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "sheet": sheet.model_dump(),
                    "paper_size": request.paper_size,
                    "size_mm": {"width": width_mm, "height": height_mm},
                    "document": updated_document.model_dump(),
                },
                warnings=[
                    "Drawing views, annotations, and BOM references were invalidated after the sheet creation."
                ],
            )

    async def activate_drawing_sheet(
        self, request: ActivateDrawingSheetRequest
    ) -> OperationResult:
        """Activate one exact current drawing sheet name."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.activate_drawing_sheet(request.sheet_name),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                sheet = self._drawing_sheet_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks activated a sheet but did not return usable metadata.",
                    suggestion="List drawing sheets again and retry with a current sheet name.",
                )
            return OperationResult.succeeded(data={"sheet": sheet.model_dump()})

    async def delete_drawing_sheet(
        self, request: DeleteDrawingSheetRequest
    ) -> OperationResult:
        """Delete one sheet and invalidate drawing references after the mutation."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.delete_drawing_sheet(request.sheet_name),
            )
            if isinstance(result, OperationResult):
                return result
            deleted_name = str(self._as_dict(result.data).get("name") or "").strip()
            if deleted_name != request.sheet_name:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks deleted a drawing sheet but did not verify its name.",
                    suggestion="List drawing sheets before retrying the operation.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "deleted_sheet": {"name": deleted_name},
                    "document": updated_document.model_dump(),
                },
                warnings=[
                    "Drawing views, annotations, and BOM references were invalidated after the sheet deletion."
                ],
            )

    async def rename_drawing_sheet(
        self, request: RenameDrawingSheetRequest
    ) -> OperationResult:
        """Rename one drawing sheet and invalidate revision-bound drawing references."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.rename_drawing_sheet(
                    request.sheet_name, request.new_sheet_name
                ),
            )
            if isinstance(result, OperationResult):
                return result
            payload = self._as_dict(result.data)
            if (
                str(payload.get("name") or "").strip() != request.new_sheet_name
                or str(payload.get("renamed_from") or "").strip()
                != request.sheet_name
            ):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks renamed a drawing sheet but did not verify its new name.",
                    suggestion="List drawing sheets, then retry with the current exact sheet name.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "renamed_sheet": {
                        "name": request.new_sheet_name,
                        "renamed_from": request.sheet_name,
                    },
                    "document": updated_document.model_dump(),
                },
                warnings=[
                    "Drawing views, annotations, and BOM references were invalidated after the sheet rename."
                ],
            )

    async def reorder_drawing_sheets(
        self, request: ReorderDrawingSheetsRequest
    ) -> OperationResult:
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(session, request.document_id)
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            result = await self._call_document_adapter(session, drawing_document, lambda: self.adapter.reorder_drawing_sheets(request.sheet_names))
            if isinstance(result, OperationResult):
                return result
            try:
                sheets = [self._drawing_sheet_summary(candidate) for candidate in (result.data or [])]
            except ValueError:
                return OperationResult.failed(code="SW_ADAPTER_INVALID_RESPONSE", message="SolidWorks reordered sheets but did not return usable sheet metadata.", suggestion="List drawing sheets and retry.")
            if [sheet.name for sheet in sheets] != request.sheet_names:
                return OperationResult.failed(code="SW_ADAPTER_INVALID_RESPONSE", message="SolidWorks did not verify the requested drawing-sheet order.", suggestion="List drawing sheets and submit the complete current order again.")
            updated_document = self.sessions.bump_document_revision(session, drawing_document.document_id)
            return OperationResult.succeeded(data={"sheets": [sheet.model_dump() for sheet in sheets], "document": updated_document.model_dump()}, warnings=["Drawing views, annotations, and BOM references were invalidated after sheet reordering."])

    async def get_drawing_view_outline(
        self, request: GetDrawingViewOutlineRequest
    ) -> OperationResult:
        """Read a current active-drawing view's layout extents in millimetres."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.get_drawing_view_outline(request.view_id),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                outline = self._drawing_view_outline_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable drawing-view outline data.",
                    suggestion="List drawing views, then retry with a current view_id.",
                )
        return OperationResult.succeeded(data={"outline": outline.model_dump()})

    async def update_drawing_view_layout(
        self, request: UpdateDrawingViewLayoutRequest
    ) -> OperationResult:
        """Update one current drawing view position and/or scale."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        x_m = y_m = None
        if request.x is not None and request.y is not None:
            x_m = self._millimetres(request.x, request.unit) / 1000
            y_m = self._millimetres(request.y, request.unit) / 1000
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.update_drawing_view_layout(
                    request.view_id, x_m, y_m, request.scale_decimal
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                view = self._drawing_view_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks updated the view but did not return a usable view reference.",
                    suggestion="List drawing views again and retry with a current view_id.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "view": view.model_dump(),
                    "position_mm": (
                        {"x": request.x, "y": request.y, "unit": request.unit}
                        if request.x is not None
                        else None
                    ),
                    "scale_decimal": request.scale_decimal,
                    "document": updated_document.model_dump(),
                },
                warnings=[
                    "Drawing views, annotations, and BOM references were invalidated after the view layout mutation."
                ],
            )

    async def align_drawing_views(
        self, request: AlignDrawingViewsRequest
    ) -> OperationResult:
        """Arrange several drawing views using outline-aware spacing."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        if len(request.view_ids) < 2:
            return OperationResult.failed(
                code="SW_ALIGNMENT_REQUIRES_MULTIPLE_VIEWS",
                message="Drawing-view alignment requires at least two view IDs.",
                suggestion="List drawing views and provide two or more distinct current view IDs.",
            )
        gap_m = self._millimetres(request.gap, request.unit) / 1000
        start_x_m = (
            self._millimetres(request.start_x, request.unit) / 1000
            if request.start_x is not None
            else None
        )
        start_y_m = (
            self._millimetres(request.start_y, request.unit) / 1000
            if request.start_y is not None
            else None
        )
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.align_drawing_views(
                    request.view_ids,
                    request.direction,
                    gap_m,
                    start_x_m,
                    start_y_m,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                alignment = self._drawing_view_alignment_summary(result.data)
                returned_ids = [view["view_id"] for view in alignment["views"]]
                if returned_ids != request.view_ids:
                    raise ValueError("adapter returned a different view order")
            except (TypeError, ValueError):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks aligned the views but did not return a usable placement summary.",
                    suggestion="List drawing views again and retry with current view IDs.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "direction": request.direction,
                    "gap": {"value": request.gap, "unit": request.unit},
                    "views": alignment["views"],
                    "document": updated_document.model_dump(),
                },
                warnings=[
                    "Drawing views, annotations, and BOM references were invalidated after the alignment mutation."
                ],
            )

    async def rebuild_drawing(self, request: RebuildDrawingRequest) -> OperationResult:
        """Rebuild and save one active session-owned drawing document."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session, drawing_document, self.adapter.rebuild_drawing
            )
            if isinstance(result, OperationResult):
                return result
            payload = self._as_dict(result.data)
            if not payload.get("rebuilt") or not payload.get("saved"):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not confirm that the drawing was rebuilt and saved.",
                    suggestion="Save the drawing in SolidWorks, then retry the rebuild operation.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={"rebuilt": True, "saved": True, "document": updated_document.model_dump()},
                warnings=[
                    "Drawing views, annotations, and BOM references were invalidated after the drawing rebuild."
                ],
            )

    async def activate_drawing_view(
        self, request: ActivateDrawingViewRequest
    ) -> OperationResult:
        """Activate one current drawing view by its opaque view ID."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.activate_drawing_view(request.view_id),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                view = self._drawing_view_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks activated a drawing view but did not return a usable view reference.",
                    suggestion="List drawing views again and retry with a current view_id.",
                )
            return OperationResult.succeeded(
                data={"view": view.model_dump(), "active": True}
            )

    async def create_projected_drawing_view(
        self, request: CreateProjectedDrawingViewRequest
    ) -> OperationResult:
        """Create a projected view from a known view in the active drawing."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        if drawing_document.document_type != "drawing":
            return OperationResult.failed(
                code="SW_DRAWING_REQUIRED",
                message="Projected drawing-view creation requires an active drawing document.",
                suggestion="Create or activate a drawing document in this workflow session and retry.",
            )

        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_projected_drawing_view(
                    request.source_view_id, x_mm / 1000, y_mm / 1000
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                view = self._drawing_view_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a projected view but did not return a usable view reference.",
                    suggestion="Inspect the drawing in SolidWorks and retry the drawing view listing operation.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "view": view.model_dump(),
                    "source_view_id": request.source_view_id,
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "document": updated_document.model_dump(),
                }
            )

    async def create_auxiliary_drawing_view(
        self, request: CreateAuxiliaryDrawingViewRequest
    ) -> OperationResult:
        """Create an auxiliary view from a current, server-issued drawing edge."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        async with session.operation_lock:
            try:
                target = self.sessions.resolve_drawing_annotation_target(
                    session, drawing_document.document_id, request.target_id
                )
            except DrawingAnnotationReferenceError as exc:
                return self._drawing_annotation_reference_error(exc.reason)
            if target.target_type != "edge":
                return OperationResult.failed(
                    code="SW_DRAWING_ANNOTATION_TARGET_TYPE_MISMATCH",
                    message="The requested drawing target is not an edge.",
                    suggestion="List drawing annotation targets and use an edge target for the auxiliary view.",
                )
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_auxiliary_drawing_view(
                    target.persistent_reference, x_mm / 1000, y_mm / 1000
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                view = self._drawing_view_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created an auxiliary view but did not return a usable view reference.",
                    suggestion="List drawing views again and retry with a current edge target.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "view": view.model_dump(),
                    "target_id": request.target_id,
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "document": updated_document.model_dump(),
                }
            )

    async def create_section_drawing_view(
        self, request: CreateSectionDrawingViewRequest
    ) -> OperationResult:
        """Create a section view from a current drawing view and typed cut line."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        cut_start_x_mm = round(self._millimetres(request.cut_start_x, request.unit), 10)
        cut_start_y_mm = round(self._millimetres(request.cut_start_y, request.unit), 10)
        cut_end_x_mm = round(self._millimetres(request.cut_end_x, request.unit), 10)
        cut_end_y_mm = round(self._millimetres(request.cut_end_y, request.unit), 10)
        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_section_drawing_view(
                    request.source_view_id,
                    cut_start_x_mm / 1000,
                    cut_start_y_mm / 1000,
                    cut_end_x_mm / 1000,
                    cut_end_y_mm / 1000,
                    x_mm / 1000,
                    y_mm / 1000,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                view = self._drawing_view_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a section view but did not return a usable view reference.",
                    suggestion="Inspect the drawing and list its views before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "view": view.model_dump(),
                    "source_view_id": request.source_view_id,
                    "cut_line_mm": {
                        "start": {"x": cut_start_x_mm, "y": cut_start_y_mm},
                        "end": {"x": cut_end_x_mm, "y": cut_end_y_mm},
                    },
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def create_drawing_bom(
        self, request: CreateDrawingBomRequest
    ) -> OperationResult:
        """Insert one assembly BOM table in an active drawing from a known view."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
            source_document = self.sessions.require_document(
                session, request.source_assembly_document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        if source_document.document_type != "assembly":
            return OperationResult.failed(
                code="SW_BOM_SOURCE_NOT_ASSEMBLY",
                message="Drawing BOM creation requires a session-owned assembly source document.",
                suggestion="Create a drawing view from a saved .sldasm document and retry.",
            )
        source_path = self._trusted_drawing_view_source_path(source_document)
        if isinstance(source_path, OperationResult):
            return source_path
        configuration_name = self._active_configuration(source_document)
        if isinstance(configuration_name, OperationResult):
            return configuration_name

        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        async with session.operation_lock:
            try:
                self.sessions.resolve_drawing_source_view(
                    session,
                    drawing_document.document_id,
                    source_document.document_id,
                    request.source_view_id,
                )
            except DrawingSourceViewReferenceError as exc:
                return self._drawing_source_view_reference_error(exc.reason)
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_drawing_bom(
                    request.source_view_id,
                    request.bom_type,
                    configuration_name,
                    x_mm / 1000,
                    y_mm / 1000,
                    request.detailed_cut_list,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                bom = self._drawing_bom_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a BOM but did not return usable table data.",
                    suggestion="Inspect the drawing BOM table in SolidWorks before retrying.",
                )
            persistent_reference = str(
                self._as_dict(result.data).get("persistent_reference") or ""
            ).strip()
            if not persistent_reference or len(persistent_reference) > 4096:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a BOM but did not return a safe table reference.",
                    suggestion="Inspect the drawing BOM table and create it again in a new workflow session.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            bom_reference = self.sessions.record_drawing_bom(
                session, drawing_document.document_id, persistent_reference
            )
            return OperationResult.succeeded(
                data={
                    "bom": bom.model_dump(),
                    "bom_id": bom_reference.bom_id,
                    "source_view_id": request.source_view_id,
                    "source_assembly_document_id": source_document.document_id,
                    "configuration": configuration_name,
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "document": updated_document.model_dump(),
                }
            )

    async def update_drawing_bom(
        self, request: UpdateDrawingBomRequest
    ) -> OperationResult:
        """Refresh one active drawing BOM using a current opaque table reference."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            try:
                bom_reference = self.sessions.resolve_drawing_bom(
                    session, request.document_id, request.bom_id
                )
            except DrawingBomReferenceError as exc:
                return self._drawing_bom_reference_error(exc.reason)
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.update_drawing_bom(
                    bom_reference.persistent_reference
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                bom = self._drawing_bom_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks updated a BOM but did not return usable table data.",
                    suggestion="Inspect the drawing BOM and list the drawing views before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            refreshed_reference = self.sessions.record_drawing_bom(
                session, request.document_id, bom_reference.persistent_reference
            )
            return OperationResult.succeeded(
                data={
                    "bom": bom.model_dump(),
                    "bom_id": refreshed_reference.bom_id,
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def delete_drawing_bom(
        self, request: DeleteDrawingBomRequest
    ) -> OperationResult:
        """Delete one active drawing BOM using a current opaque table reference."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(document)
        if drawing_error is not None:
            return drawing_error
        async with session.operation_lock:
            try:
                bom_reference = self.sessions.resolve_drawing_bom(
                    session, request.document_id, request.bom_id
                )
            except DrawingBomReferenceError as exc:
                return self._drawing_bom_reference_error(exc.reason)
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.delete_drawing_bom(
                    bom_reference.persistent_reference
                ),
            )
            if isinstance(result, OperationResult):
                return result
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "deleted_bom": {
                        "display_name": str(
                            self._as_dict(result.data).get("display_name")
                            or "Bill of Materials"
                        )[:240]
                    },
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def create_drawing_note(
        self, request: CreateDrawingNoteRequest
    ) -> OperationResult:
        """Insert one real text note into an active drawing at a typed position."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error

        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_drawing_note(
                    request.text, x_mm / 1000, y_mm / 1000
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                annotation = self._drawing_annotation_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a note but did not return usable annotation data.",
                    suggestion="Inspect the drawing note in SolidWorks before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "annotation": annotation.model_dump(),
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "document": updated_document.model_dump(),
                }
            )

    async def create_drawing_revision_table(
        self, request: CreateDrawingRevisionTableRequest
    ) -> OperationResult:
        """Insert a revision table with one initial revision row."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        anchor_types = {
            "top_left": 0,
            "top_right": 1,
            "bottom_left": 2,
            "bottom_right": 3,
        }
        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.create_drawing_revision_table(
                    x_mm / 1000,
                    y_mm / 1000,
                    anchor_types[request.anchor],
                    request.revision,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            table = self._as_dict(result.data)
            if not table.get("display_name"):
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a revision table but returned no table metadata.",
                    suggestion="Inspect the drawing revision table before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            try:
                persistent_reference = str(table.get("persistent_reference") or "").strip()
                if not persistent_reference:
                    raise ValueError("missing persistent reference")
                revision_table = self.sessions.record_drawing_revision_table(
                    session,
                    request.document_id,
                    persistent_reference,
                )
            except Exception:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created a revision table but did not return a usable reference.",
                    suggestion="Inspect the drawing revision table before retrying.",
                )
            return OperationResult.succeeded(
                data={
                    "revision_table": {
                        "revision_table_id": revision_table.revision_table_id,
                        "display_name": str(table["display_name"])[:240],
                        "revision": str(table.get("revision") or request.revision)[:32],
                        "revision_id": table.get("revision_id"),
                        "current_revision": str(
                            table.get("current_revision") or request.revision
                        )[:32],
                    },
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "anchor": request.anchor,
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def list_drawing_revisions(
        self, request: ListDrawingRevisionsRequest
    ) -> OperationResult:
        """List rows and symbol counts from one current revision table."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
            table = self.sessions.resolve_drawing_revision_table(
                session, request.document_id, request.revision_table_id
            )
        except SessionLookupError:
            return self._document_not_found()
        except DrawingRevisionTableReferenceError as exc:
            return OperationResult.failed(
                code="SW_REVISION_TABLE_REFERENCE_INVALID",
                message=f"Revision table reference is {exc.reason}.",
                suggestion="Create or list the revision table again to obtain a current revision_table_id.",
            )
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.list_drawing_revisions(table.persistent_reference),
            )
            if isinstance(result, OperationResult):
                return result
            return OperationResult.succeeded(
                data={"revision_table_id": table.revision_table_id, "rows": result.data},
                route=OperationRoute.COM,
            )

    async def add_drawing_revision(
        self, request: AddDrawingRevisionRequest
    ) -> OperationResult:
        """Add one revision row and return the updated table state."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
            table = self.sessions.resolve_drawing_revision_table(
                session, request.document_id, request.revision_table_id
            )
        except SessionLookupError:
            return self._document_not_found()
        except DrawingRevisionTableReferenceError as exc:
            return OperationResult.failed(
                code="SW_REVISION_TABLE_REFERENCE_INVALID",
                message=f"Revision table reference is {exc.reason}.",
                suggestion="Create the revision table again to obtain a current revision_table_id.",
            )
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.add_drawing_revision(
                    table.persistent_reference, request.revision
                ),
            )
            if isinstance(result, OperationResult):
                return result
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "revision_table_id": table.revision_table_id,
                    "revision": self._as_dict(result.data),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def delete_drawing_revision(
        self, request: DeleteDrawingRevisionRequest
    ) -> OperationResult:
        """Delete one revision row and invalidate table capabilities."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
            table = self.sessions.resolve_drawing_revision_table(
                session, request.document_id, request.revision_table_id
            )
        except SessionLookupError:
            return self._document_not_found()
        except DrawingRevisionTableReferenceError as exc:
            return OperationResult.failed(
                code="SW_REVISION_TABLE_REFERENCE_INVALID",
                message=f"Revision table reference is {exc.reason}.",
                suggestion="Create the revision table again to obtain a current revision_table_id.",
            )
        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.delete_drawing_revision(
                    table.persistent_reference,
                    request.revision_id,
                    request.delete_symbols,
                ),
            )
            if isinstance(result, OperationResult):
                return result
            updated_document = self.sessions.bump_document_revision(
                session, request.document_id
            )
            return OperationResult.succeeded(
                data={
                    "revision_table_id": table.revision_table_id,
                    "deleted": self._as_dict(result.data),
                    "document": updated_document.model_dump(),
                },
                route=OperationRoute.COM,
            )

    async def list_drawing_annotation_targets(
        self, request: ListDrawingAnnotationTargetsRequest
    ) -> OperationResult:
        """Issue revision-bound opaque targets for drawing dimensions and annotations."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: self.adapter.list_drawing_annotation_targets(
                    request.view_id, tuple(request.target_types)
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                candidates = self._drawing_annotation_target_candidates(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable drawing annotation targets.",
                    suggestion="Rebuild the drawing views and retry target listing.",
                )
            self.sessions.begin_drawing_annotation_target_listing(
                session, drawing_document.document_id
            )
            targets = [
                self.sessions.issue_drawing_annotation_target(
                    session,
                    drawing_document.document_id,
                    view_id=candidate["view_id"],
                    target_type=candidate["target_type"],
                    display_name=candidate["display_name"],
                    persistent_reference=candidate["persistent_reference"],
                )
                for candidate in candidates
            ]
            summaries = [
                self._drawing_annotation_target_summary(target) for target in targets
            ]
            return OperationResult.succeeded(
                data={
                    **self._paginate(summaries, request.limit, request.offset),
                    "targets": [
                        target.model_dump()
                        for target in summaries[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def place_drawing_dimension(
        self, request: PlaceDrawingDimensionRequest
    ) -> OperationResult:
        """Place a server-issued display dimension on the active drawing sheet."""
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="dimension",
            operation="place_drawing_dimension",
            call=lambda target, x_m, y_m: self.adapter.place_drawing_dimension(
                target.persistent_reference, x_m, y_m
            ),
        )

    async def create_drawing_balloon(
        self, request: CreateDrawingBalloonRequest
    ) -> OperationResult:
        """Create a BOM balloon from one server-issued visible component target."""
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="component",
            operation="create_drawing_balloon",
            call=lambda target, x_m, y_m: self.adapter.create_drawing_balloon(
                target.persistent_reference, x_m, y_m, request.style, request.text
            ),
        )

    async def create_drawing_hole_callout(
        self, request: CreateDrawingHoleCalloutRequest
    ) -> OperationResult:
        """Create an automatic hole callout from one server-issued visible edge target."""
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="edge",
            operation="create_drawing_hole_callout",
            call=lambda target, x_m, y_m: self.adapter.create_drawing_hole_callout(
                target.persistent_reference, x_m, y_m
            ),
        )

    async def create_drawing_ordinate_dimension(
        self, request: CreateDrawingOrdinateDimensionRequest
    ) -> OperationResult:
        """Create a horizontal or vertical ordinate dimension."""
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="dimension",
            operation="create_drawing_ordinate_dimension",
            call=lambda target, x_m, y_m: self.adapter.create_drawing_ordinate_dimension(
                target.persistent_reference, x_m, y_m, request.ordinate_type
            ),
        )

    async def create_drawing_surface_finish(
        self, request: CreateDrawingSurfaceFinishRequest
    ) -> OperationResult:
        """Insert a surface-finish symbol on a drawing entity."""
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="edge",
            operation="create_drawing_surface_finish",
            call=lambda target, x_m, y_m: self.adapter.create_drawing_surface_finish(
                target.persistent_reference, x_m, y_m, request.symbol, request.value
            ),
        )

    async def create_drawing_weld_symbol(
        self, request: CreateDrawingWeldSymbolRequest
    ) -> OperationResult:
        """Insert a weld symbol on a drawing entity."""
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="edge",
            operation="create_drawing_weld_symbol",
            call=lambda target, x_m, y_m: self.adapter.create_drawing_weld_symbol(
                target.persistent_reference, x_m, y_m, request.weld_type, request.size
            ),
        )

    async def create_drawing_geometric_tolerance(
        self, request: CreateDrawingGeometricToleranceRequest
    ) -> OperationResult:
        """Insert a geometric tolerance frame on a drawing entity."""
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="dimension",
            operation="create_drawing_geometric_tolerance",
            call=lambda target, x_m, y_m: self.adapter.create_drawing_geometric_tolerance(
                target.persistent_reference, x_m, y_m, request.tolerance, request.datum
            ),
        )

    async def create_drawing_center_mark(
        self, request: CreateDrawingCenterMarkRequest
    ) -> OperationResult:
        """Create a center mark from a listed circular drawing edge."""
        size_mm = round(self._millimetres(request.size, request.unit), 10)
        return await self._create_targeted_drawing_annotation(
            request,
            expected_target_type="edge",
            operation="create_drawing_center_mark",
            call=lambda target, _x_m, _y_m: self.adapter.create_drawing_center_mark(
                target.persistent_reference, size_mm / 1000, request.show_connection_lines
            ),
        )

    async def list_custom_properties(
        self, request: ListCustomPropertiesRequest
    ) -> OperationResult:
        """List bounded document or active-configuration custom properties."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        configuration_name = self._custom_property_configuration(
            document, request.scope
        )
        if isinstance(configuration_name, OperationResult):
            return configuration_name

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.list_custom_properties(configuration_name),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                properties = self._custom_property_summaries(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks did not return usable custom property data.",
                    suggestion="Inspect the document properties in SolidWorks and retry.",
                )
            return OperationResult.succeeded(
                data={
                    **self._paginate(properties, request.limit, request.offset),
                    "scope": request.scope,
                    "configuration": configuration_name,
                    "properties": [
                        property_summary.model_dump()
                        for property_summary in properties[
                            request.offset : request.offset + request.limit
                        ]
                    ],
                }
            )

    async def set_custom_property(
        self, request: SetCustomPropertyRequest
    ) -> OperationResult:
        """Set one document or active-configuration custom property safely."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        configuration_name = self._custom_property_configuration(
            document, request.scope
        )
        if isinstance(configuration_name, OperationResult):
            return configuration_name

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.set_custom_property(
                    request.name, request.value, configuration_name
                ),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                property_summary = self._custom_property_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks updated a custom property but did not return usable property data.",
                    suggestion="Inspect the document properties in SolidWorks and retry.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "scope": request.scope,
                    "configuration": configuration_name,
                    "property": property_summary.model_dump(),
                    "document": updated_document.model_dump(),
                }
            )

    async def delete_custom_property(
        self, request: DeleteCustomPropertyRequest
    ) -> OperationResult:
        """Delete one document or active-configuration custom property safely."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        configuration_name = self._custom_property_configuration(
            document, request.scope
        )
        if isinstance(configuration_name, OperationResult):
            return configuration_name

        async with session.operation_lock:
            result = await self._call_document_adapter(
                session,
                document,
                lambda: self.adapter.delete_custom_property(
                    request.name, configuration_name
                ),
            )
            if isinstance(result, OperationResult):
                return result
            deleted_name = str(self._as_dict(result.data).get("name") or request.name)
            updated_document = self.sessions.bump_document_revision(
                session, document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "scope": request.scope,
                    "configuration": configuration_name,
                    "property": {"name": deleted_name},
                    "document": updated_document.model_dump(),
                }
            )

    async def list_trusted_macros(self) -> OperationResult:
        """List executable VBA projects that an administrator has provisioned safely."""
        if self.trusted_macro_store is None or not self.trusted_macro_store.enabled:
            return OperationResult.succeeded(
                route=OperationRoute.VBA,
                data={"macros": []},
                warnings=[
                    "Trusted VBA macro execution is disabled. An administrator must enable it explicitly."
                ],
            )
        macros = [
            macro.public_ref().model_dump() for macro in self.trusted_macro_store.list()
        ]
        return OperationResult.succeeded(
            route=OperationRoute.VBA, data={"macros": macros}
        )

    async def prepare_trusted_macro(
        self, request: PrepareTrustedMacroRequest
    ) -> OperationResult:
        """Bind one verified macro hash to a session and require explicit confirmation."""
        try:
            self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._session_not_found()

        self._purge_expired_macro_confirmations()
        if len(self._macro_confirmations) >= self._MAX_PENDING_MACRO_CONFIRMATIONS:
            return OperationResult.failed(
                code="SW_MACRO_CONFIRMATION_CAPACITY_REACHED",
                message="The server has reached its pending macro confirmation capacity.",
                suggestion="Execute, let expire, or close an existing workflow session before preparing another macro.",
            )

        try:
            macro = self._require_trusted_macro_store().get(request.macro_id)
        except TrustedMacrosDisabledError:
            return OperationResult.failed(
                code="SW_TRUSTED_MACROS_DISABLED",
                message="Trusted VBA macro execution is disabled by the server configuration.",
                suggestion="An administrator must enable trusted macros and provision a verified manifest before retrying.",
            )
        except TrustedMacroNotFoundError:
            return OperationResult.failed(
                code="SW_TRUSTED_MACRO_NOT_FOUND",
                message="The requested trusted macro is not available.",
                suggestion="Call solidworks_list_trusted_macros and use one of the returned macro_id values.",
            )

        confirmation_id = f"mac_{uuid4().hex}"
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self.macro_confirmation_ttl_seconds
        )
        self._macro_confirmations[confirmation_id] = PendingMacroConfirmation(
            session_id=request.session_id,
            macro=macro,
            expires_at=expires_at,
        )
        return OperationResult(
            status=OperationStatus.NEEDS_CONFIRMATION,
            route=OperationRoute.VBA,
            data={
                "confirmation": {
                    "confirmation_id": confirmation_id,
                    "expires_at": expires_at.isoformat(),
                    "macro": macro.public_ref().model_dump(),
                }
            },
            warnings=[
                "This macro can change files and the SolidWorks session. Confirm the listed hash before execution."
            ],
        )

    async def execute_trusted_macro(
        self, request: ExecuteTrustedMacroRequest
    ) -> OperationResult:
        """Run a one-time, hash-confirmed ``.swp`` through the COM adapter."""
        try:
            session = self.sessions.require(request.session_id)
        except SessionLookupError:
            return self._session_not_found()

        pending = self._macro_confirmations.pop(request.confirmation_id, None)
        self._purge_expired_macro_confirmations()
        if pending is None:
            return OperationResult.failed(
                code="SW_MACRO_CONFIRMATION_NOT_FOUND",
                message="The macro confirmation is missing or was already used.",
                suggestion="Call solidworks_prepare_trusted_macro again before executing the macro.",
            )
        if pending.session_id != request.session_id:
            return OperationResult.failed(
                code="SW_MACRO_CONFIRMATION_SESSION_MISMATCH",
                message="The macro confirmation belongs to a different workflow session.",
                suggestion="Prepare the macro again in the session that will execute it.",
            )
        if pending.expires_at <= datetime.now(UTC):
            return OperationResult.failed(
                code="SW_MACRO_CONFIRMATION_EXPIRED",
                message="The macro confirmation expired before execution.",
                suggestion="Prepare the macro again and confirm its current hash.",
            )
        if pending.macro.sha256 != request.macro_sha256:
            return OperationResult.failed(
                code="SW_MACRO_CONFIRMATION_HASH_MISMATCH",
                message="The supplied macro hash does not match the prepared macro.",
                suggestion="Use the sha256 returned by solidworks_prepare_trusted_macro.",
            )

        try:
            macro = self._require_trusted_macro_store().verify_unchanged(pending.macro)
        except (TrustedMacroChangedError, TrustedMacroNotFoundError):
            return OperationResult.failed(
                code="SW_TRUSTED_MACRO_CHANGED",
                message="The trusted macro or its manifest changed after confirmation.",
                suggestion="Review and re-provision the macro, then prepare and confirm it again.",
            )
        except TrustedMacrosDisabledError:
            return OperationResult.failed(
                code="SW_TRUSTED_MACROS_DISABLED",
                message="Trusted VBA macro execution is disabled by the server configuration.",
                suggestion="An administrator must enable trusted macros before retrying.",
            )

        async with session.operation_lock:
            try:
                async with self._adapter_lock:
                    if self.sessions.has_active_sketch():
                        return OperationResult.failed(
                            code="SW_MACRO_SKETCH_ACTIVE",
                            message="A trusted macro cannot run while any workflow session is editing a sketch.",
                            suggestion="Exit the active sketch in every workflow session, then prepare and execute the macro again.",
                        )
                    result = await self.adapter.execute_macro(
                        {
                            "macro_path": str(macro.source_path),
                            "module_name": macro.module_name,
                            "proc_name": macro.procedure_name,
                        }
                    )
                    if not getattr(result, "is_success", False):
                        return OperationResult.failed(
                            code="SW_TRUSTED_MACRO_EXECUTION_FAILED",
                            message="SolidWorks rejected the trusted VBA macro execution.",
                            suggestion="Review the macro in SolidWorks and verify its module and procedure in the administrator manifest.",
                        )
                    self.sessions.invalidate_all_contexts()
            except Exception:
                return OperationResult.failed(
                    code="SW_TRUSTED_MACRO_EXECUTION_FAILED",
                    message="SolidWorks could not execute the trusted VBA macro.",
                    suggestion="Check that SolidWorks is running and that the provisioned macro is compatible with this version.",
                )
            adapter_data = self._as_dict(getattr(result, "data", None))
            return OperationResult.succeeded(
                route=OperationRoute.VBA,
                data={
                    "macro": macro.public_ref().model_dump(),
                    "run": {"adapter_reported_errors": adapter_data.get("errors", 0)},
                },
                warnings=[
                    "The macro may have changed any open SolidWorks document. All workflow document and sketch references were invalidated."
                ],
            )

    async def _create_targeted_drawing_annotation(
        self,
        request: Any,
        *,
        expected_target_type: str,
        operation: str,
        call: Any,
    ) -> OperationResult:
        """Resolve one opaque drawing target and perform its typed annotation action."""
        try:
            session = self.sessions.require(request.session_id)
            drawing_document = self.sessions.require_active_document(
                session, request.document_id
            )
        except SessionLookupError:
            return self._document_not_found()
        drawing_error = self._require_drawing_document(drawing_document)
        if drawing_error is not None:
            return drawing_error
        x_mm = round(self._millimetres(request.x, request.unit), 10)
        y_mm = round(self._millimetres(request.y, request.unit), 10)
        async with session.operation_lock:
            try:
                target = self.sessions.resolve_drawing_annotation_target(
                    session, drawing_document.document_id, request.target_id
                )
            except DrawingAnnotationReferenceError as exc:
                return self._drawing_annotation_reference_error(exc.reason)
            if target.target_type != expected_target_type:
                return OperationResult.failed(
                    code="SW_DRAWING_ANNOTATION_TARGET_TYPE_MISMATCH",
                    message=(
                        f"The requested drawing target cannot be used for {operation}."
                    ),
                    suggestion=(
                        "List drawing annotation targets again and use a target with the "
                        f"{expected_target_type} type."
                    ),
                )
            result = await self._call_document_adapter(
                session,
                drawing_document,
                lambda: call(target, x_mm / 1000, y_mm / 1000),
            )
            if isinstance(result, OperationResult):
                return result
            try:
                annotation = self._drawing_annotation_summary(result.data)
            except ValueError:
                return OperationResult.failed(
                    code="SW_ADAPTER_INVALID_RESPONSE",
                    message="SolidWorks created an annotation but did not return usable data.",
                    suggestion="Inspect the active drawing in SolidWorks before retrying.",
                )
            updated_document = self.sessions.bump_document_revision(
                session, drawing_document.document_id
            )
            return OperationResult.succeeded(
                data={
                    "annotation": annotation.model_dump(),
                    "target_id": request.target_id,
                    "position_mm": {"x": x_mm, "y": y_mm},
                    "document": updated_document.model_dump(),
                }
            )

    async def _call_adapter(
        self,
        call: Any,
        *,
        operation_id: str | None = None,
        session: Any | None = None,
    ) -> Any:
        """Normalize adapter failures into safe MCP errors."""
        try:
            async with self._adapter_lock:
                if session is not None and self._foreign_sketch_context_locked(session):
                    return self._sketch_context_locked()
                result = await call()
        except Exception:
            return OperationResult.failed(
                operation_id=operation_id,
                code="SW_ADAPTER_EXCEPTION",
                message="SolidWorks could not complete the requested operation.",
                suggestion="Verify the active document state and retry the operation.",
            )
        if not getattr(result, "is_success", False):
            logger.debug(
                "SolidWorks adapter operation failed: {}",
                getattr(result, "error", "unknown adapter failure"),
            )
            return OperationResult.failed(
                operation_id=operation_id,
                code="SW_ADAPTER_FAILURE",
                message="SolidWorks rejected the requested operation.",
                suggestion="Check the active document state and retry the operation.",
            )
        return result

    async def _call_document_adapter(
        self,
        session: Any,
        document: Any,
        call: Any,
        *,
        operation_id: str | None = None,
        on_success: Any | None = None,
        pre_call: Any | None = None,
    ) -> Any:
        """Activate the tracked COM document and run one operation without interleaving."""
        if self._foreign_sketch_context_locked(session):
            return self._sketch_context_locked()
        try:
            async with self._adapter_lock:
                if self._foreign_sketch_context_locked(session):
                    return self._sketch_context_locked()
                try:
                    self.sessions.require_active_document(session, document.document_id)
                except SessionLookupError:
                    return self._document_not_found()
                if pre_call is not None:
                    pre_call_failure = pre_call()
                    if isinstance(pre_call_failure, OperationResult):
                        return pre_call_failure
                activated = await self.adapter.activate_model(
                    document.name, document.path
                )
                if not getattr(activated, "is_success", False):
                    return OperationResult.failed(
                        operation_id=operation_id,
                        code="SW_DOCUMENT_ACTIVATION_FAILED",
                        message="SolidWorks could not activate the requested workflow document.",
                        suggestion="Close conflicting documents or recreate the workflow session, then retry.",
                    )
                result = await call()
                if getattr(result, "is_success", False) and on_success is not None:
                    on_success(result)
        except Exception:
            return OperationResult.failed(
                operation_id=operation_id,
                code="SW_ADAPTER_EXCEPTION",
                message="SolidWorks could not complete the requested operation.",
                suggestion="Verify the active document state and retry the operation.",
            )
        if not getattr(result, "is_success", False):
            adapter_error_code = (getattr(result, "metadata", None) or {}).get(
                "error_code"
            )
            if adapter_error_code == ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED:
                return OperationResult.failed(
                    operation_id=operation_id,
                    code=ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED,
                    message=(
                        "The connected SolidWorks version does not support "
                        "associated projected drawing views."
                    ),
                    suggestion=(
                        "Use solidworks_create_drawing_view to create an independent "
                        "standard view, or use a SolidWorks version that supports "
                        "CreateProjectionView."
                    ),
                )
            chamfer_failures = {
                ADAPTER_ERROR_CHAMFER_EDGE_SELECTION: (
                    "SolidWorks could not resolve or select one of the requested chamfer edges.",
                    "List part topology with entity_types=['edge'] again and retry with the returned edge_id values.",
                ),
                ADAPTER_ERROR_CHAMFER_SELECTION_COUNT: (
                    "SolidWorks selected a different number of edges than requested for the chamfer.",
                    "Clear any manual selection, list current edge IDs, and submit each ID only once.",
                ),
                ADAPTER_ERROR_CHAMFER_CREATION: (
                    "SolidWorks rejected the chamfer dimensions or could not rebuild the chamfer feature.",
                    "Verify the edge geometry, reduce the distance, and retry; inspect the returned preflight warning if present.",
                ),
            }
            if adapter_error_code in chamfer_failures:
                message, suggestion = chamfer_failures[adapter_error_code]
                return OperationResult.failed(
                    operation_id=operation_id,
                    code=adapter_error_code,
                    message=message,
                    suggestion=suggestion,
                )
            logger.debug(
                "SolidWorks document operation failed for {}: {}",
                document.document_id,
                getattr(result, "error", "unknown adapter failure"),
            )
            return OperationResult.failed(
                operation_id=operation_id,
                code="SW_ADAPTER_FAILURE",
                message="SolidWorks rejected the requested operation.",
                suggestion="Check the active document state and retry the operation.",
            )
        return result

    def _foreign_sketch_context_locked(self, session: Any) -> bool:
        """Prevent another session from changing SolidWorks while sketch edit mode is open."""
        return self.sessions.has_foreign_active_sketch(session)

    def _require_trusted_macro_store(self) -> TrustedMacroStore:
        """Return the configured macro store or expose the disabled state safely."""
        if self.trusted_macro_store is None:
            raise TrustedMacrosDisabledError("trusted macro store is not configured")
        return self.trusted_macro_store

    def _purge_expired_macro_confirmations(self) -> None:
        """Bound confirmation memory by dropping expired one-time authorizations."""
        now = datetime.now(UTC)
        self._macro_confirmations = {
            confirmation_id: pending
            for confirmation_id, pending in self._macro_confirmations.items()
            if pending.expires_at > now
        }

    async def _health_check(self) -> Any | None:
        """Read adapter health without interleaving another COM operation."""
        try:
            async with self._adapter_lock:
                return await self.adapter.health_check()
        except Exception:
            return None

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        """Serialize adapter values without exposing COM objects to callers."""
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            if isinstance(dumped, dict):
                return dict(dumped)
            return {"value": str(dumped)}
        if is_dataclass(value) and not isinstance(value, type):
            return asdict(cast(Any, value))
        if isinstance(value, dict):
            return dict(value)
        return {"value": str(value)}

    @staticmethod
    def _feature_summaries(value: Any) -> list[FeatureSummary]:
        """Map adapter feature rows to a stable public schema."""
        if not isinstance(value, (list, tuple)):
            return []
        features: list[FeatureSummary] = []
        for position, raw_feature in enumerate(value):
            feature = MvpWorkflow._as_dict(raw_feature)
            name = str(feature.get("name") or "Unnamed feature").strip()[:240]
            feature_type = str(
                feature.get("type") or feature.get("feature_type") or "Unknown"
            ).strip()[:120]
            features.append(
                FeatureSummary(
                    feature_id="pending",
                    name=name or "Unnamed feature",
                    feature_type=feature_type or "Unknown",
                    suppressed=bool(feature.get("suppressed", False)),
                    position=position,
                )
            )
        return features

    @staticmethod
    def _configuration_summaries(
        value: Any, active_configuration: str | None
    ) -> list[ConfigurationSummary]:
        """Map adapter configuration names to a stable public schema."""
        if not isinstance(value, (list, tuple)):
            return []
        active_name = (active_configuration or "").casefold()
        return [
            ConfigurationSummary(
                name=str(configuration).strip()[:120] or "Default",
                is_active=str(configuration).strip().casefold() == active_name,
            )
            for configuration in value
        ]

    @staticmethod
    def _configuration_detail_summary(value: Any) -> ConfigurationDetailSummary:
        """Validate one adapter configuration and its non-COM comment text."""
        configuration = MvpWorkflow._as_dict(value)
        name = str(configuration.get("name") or "").strip()[:120]
        description = str(configuration.get("description") or "")[:1024]
        if not name:
            raise ValueError("adapter configuration is missing a name")
        return ConfigurationDetailSummary(
            name=name,
            description=description,
            is_active=bool(configuration.get("is_active")),
        )

    @staticmethod
    def _solid_body_summaries(value: Any) -> list[SolidBodySummary]:
        """Map adapter body rows to client-safe, non-COM summaries."""
        if not isinstance(value, (list, tuple)):
            return []
        summaries: list[SolidBodySummary] = []
        for raw_body in value:
            body = MvpWorkflow._as_dict(raw_body)
            body_id = str(body.get("body_id") or body.get("id") or "").strip()[:120]
            name = str(body.get("name") or "").strip()[:240]
            if not body_id or not name:
                raise ValueError("adapter solid body is missing an ID or name")
            summaries.append(
                SolidBodySummary(
                    body_id=body_id,
                    name=name,
                    visible=bool(body.get("visible", True)),
                )
            )
        return summaries

    @staticmethod
    def _equation_summary(value: Any) -> EquationSummary:
        """Map one adapter equation row to the public, parsed equation schema."""
        equation = MvpWorkflow._as_dict(value)
        index = equation.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError("adapter equation is missing an integer index")
        return EquationSummary(
            index=index,
            name=str(equation.get("name") or "").strip(),
            expression=str(equation.get("expression") or "").strip(),
            is_global_variable=bool(equation.get("is_global_variable")),
        )

    @staticmethod
    def _equation_summaries(value: Any) -> list[EquationSummary]:
        """Map an adapter equation collection into stable typed summaries."""
        if not isinstance(value, (list, tuple)):
            raise ValueError("adapter equations must be a list")
        return [MvpWorkflow._equation_summary(equation) for equation in value]

    @staticmethod
    def _material_assignment_summary(value: Any) -> MaterialAssignmentSummary:
        """Map one adapter material row to the stable public schema."""
        assignment = MvpWorkflow._as_dict(value)
        return MaterialAssignmentSummary(
            database_name=str(assignment.get("database_name") or "")[:1024],
            material_name=str(assignment.get("material_name") or "")[:512],
        )

    @staticmethod
    def _assembly_component_summary(value: Any) -> AssemblyComponentSummary:
        """Map one adapter component row to the public component schema."""
        component = MvpWorkflow._as_dict(value)
        component_id = str(
            component.get("component_id") or component.get("id") or ""
        ).strip()[:240]
        display_name = str(
            component.get("display_name") or component.get("name") or ""
        ).strip()[:240]
        if not component_id or not display_name:
            raise ValueError("adapter component is missing an ID or display name")
        document_type = str(component.get("document_type") or "unknown").strip().lower()
        if document_type not in {"part", "assembly", "drawing", "unknown"}:
            document_type = "unknown"
        return AssemblyComponentSummary(
            component_id=component_id,
            display_name=display_name,
            document_type=cast(
                Literal["part", "assembly", "drawing", "unknown"], document_type
            ),
        )

    @staticmethod
    def _assembly_component_summaries(value: Any) -> list[AssemblyComponentSummary]:
        """Map an adapter component collection to stable public summaries."""
        if not isinstance(value, (list, tuple)):
            return []
        return [
            MvpWorkflow._assembly_component_summary(component) for component in value
        ]

    @staticmethod
    def _assembly_mate_entity_candidate(value: Any) -> dict[str, str]:
        """Validate one adapter-only persistent geometry reference row."""
        entity = MvpWorkflow._as_dict(value)
        component_id = str(entity.get("component_id") or "").strip()[:240]
        entity_type = str(entity.get("entity_type") or "").strip().lower()
        display_name = str(entity.get("display_name") or "").strip()[:240]
        persistent_reference = str(entity.get("persistent_reference") or "").strip()
        if (
            not component_id
            or entity_type not in {"face", "edge"}
            or not display_name
            or not persistent_reference
            or len(persistent_reference) > 4096
        ):
            raise ValueError("adapter mate entity is missing safe reference metadata")
        return {
            "component_id": component_id,
            "entity_type": entity_type,
            "display_name": display_name,
            "persistent_reference": persistent_reference,
        }

    @staticmethod
    def _assembly_mate_entity_candidates(value: Any) -> list[dict[str, str]]:
        """Validate the adapter collection before any token enters session state."""
        if not isinstance(value, (list, tuple)):
            return []
        return [MvpWorkflow._assembly_mate_entity_candidate(entity) for entity in value]

    @staticmethod
    def _part_topology_candidate(value: Any) -> dict[str, Any]:
        """Validate one adapter-only topology row before it enters session state."""
        entity = MvpWorkflow._as_dict(value)
        entity_type = str(entity.get("entity_type") or "").strip().lower()
        display_name = str(entity.get("display_name") or "").strip()[:240]
        persistent_reference = str(entity.get("persistent_reference") or "").strip()
        if (
            entity_type not in {"face", "edge", "vertex"}
            or not display_name
            or not persistent_reference
            or len(persistent_reference) > 4096
        ):
            raise ValueError("adapter part topology is missing safe reference metadata")
        candidate: dict[str, Any] = {
            "entity_type": entity_type,
            "display_name": display_name,
            "persistent_reference": persistent_reference,
        }
        if entity_type == "face":
            if "area_mm2" in entity:
                candidate["area_mm2"] = entity["area_mm2"]
            if "normal" in entity:
                candidate["normal"] = entity["normal"]
        elif entity_type == "edge":
            if "length_mm" in entity:
                candidate["length_mm"] = entity["length_mm"]
            if "curve_identity" in entity:
                candidate["curve_identity"] = entity["curve_identity"]
        elif "point_mm" in entity:
            candidate["point_mm"] = entity["point_mm"]
        return candidate

    @staticmethod
    def _part_topology_candidates(value: Any) -> list[dict[str, Any]]:
        """Validate a part-topology collection before tokens enter session state."""
        if not isinstance(value, (list, tuple)):
            return []
        return [MvpWorkflow._part_topology_candidate(entity) for entity in value]

    @staticmethod
    def _assembly_mate_candidate(value: Any) -> dict[str, str]:
        """Validate one adapter-only existing-mate persistent reference row."""
        mate = MvpWorkflow._as_dict(value)
        display_name = str(mate.get("display_name") or mate.get("name") or "").strip()[
            :240
        ]
        mate_type = str(mate.get("mate_type") or "unknown").strip()[:120]
        persistent_reference = str(mate.get("persistent_reference") or "").strip()
        if (
            not display_name
            or not mate_type
            or not persistent_reference
            or len(persistent_reference) > 4096
        ):
            raise ValueError("adapter assembly mate is missing safe reference metadata")
        return {
            "display_name": display_name,
            "mate_type": mate_type,
            "persistent_reference": persistent_reference,
        }

    @staticmethod
    def _assembly_mate_candidates(value: Any) -> list[dict[str, str]]:
        """Validate an existing-mate collection before tokens enter session state."""
        if not isinstance(value, (list, tuple)):
            return []
        return [MvpWorkflow._assembly_mate_candidate(mate) for mate in value]

    @staticmethod
    def _assembly_mate_summary(value: Any) -> AssemblyMateSummary:
        """Map the adapter's successful AddMate5 response to the public schema."""
        mate = MvpWorkflow._as_dict(value)
        mate_type = str(mate.get("mate_type") or "").strip().lower()
        display_name = str(mate.get("display_name") or mate.get("name") or "").strip()[
            :240
        ]
        if not display_name:
            raise ValueError("adapter mate is missing a display name")
        if mate_type not in {
            "coincident",
            "concentric",
            "parallel",
            "perpendicular",
            "tangent",
            "distance",
            "angle",
        }:
            raise ValueError("adapter mate type is invalid")
        return AssemblyMateSummary(
            mate_type=cast(
                Literal[
                    "coincident",
                    "concentric",
                    "parallel",
                    "perpendicular",
                    "tangent",
                    "distance",
                    "angle",
                ],
                mate_type,
            ),
            display_name=display_name,
        )

    @staticmethod
    def _drawing_view_summary(value: Any) -> DrawingViewSummary:
        """Map one adapter drawing view row to the stable public schema."""
        view = MvpWorkflow._as_dict(value)
        view_id = str(view.get("view_id") or view.get("id") or "").strip()[:240]
        display_name = str(view.get("display_name") or view.get("name") or "").strip()[
            :240
        ]
        if not view_id or not display_name:
            raise ValueError("adapter drawing view is missing an ID or display name")
        return DrawingViewSummary(view_id=view_id, display_name=display_name)

    @staticmethod
    def _drawing_sheet_summary(value: Any) -> DrawingSheetSummary:
        """Validate one adapter drawing-sheet row for client consumption."""
        sheet = MvpWorkflow._as_dict(value)
        name = str(sheet.get("name") or "").strip()[:240]
        if not name:
            raise ValueError("adapter drawing sheet is missing a name")
        return DrawingSheetSummary(name=name, is_active=bool(sheet.get("is_active")))

    @staticmethod
    def _drawing_view_summaries(value: Any) -> list[DrawingViewSummary]:
        """Map adapter drawing views to stable public summaries."""
        if not isinstance(value, (list, tuple)):
            return []
        return [MvpWorkflow._drawing_view_summary(view) for view in value]

    @staticmethod
    def _drawing_view_outline_summary(value: Any) -> DrawingViewOutlineSummary:
        """Normalize a native metre-based adapter outline to public millimetres."""
        outline = MvpWorkflow._as_dict(value)
        view_id = str(outline.get("view_id") or "").strip()[:240]
        coordinates = ("left_m", "bottom_m", "right_m", "top_m")
        values = [outline.get(coordinate) for coordinate in coordinates]
        if not view_id or any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in values
        ):
            raise ValueError("adapter drawing-view outline is invalid")
        left_m, bottom_m, right_m, top_m = (
            float(cast(int | float, value)) for value in values
        )
        return DrawingViewOutlineSummary(
            view_id=view_id,
            left_mm=left_m * 1000,
            bottom_mm=bottom_m * 1000,
            right_mm=right_m * 1000,
            top_mm=top_m * 1000,
        )

    @staticmethod
    def _drawing_view_alignment_summary(value: Any) -> dict[str, Any]:
        """Normalize an adapter alignment response to public millimetre values."""
        payload = MvpWorkflow._as_dict(value)
        direction = str(payload.get("direction") or "").strip().lower()
        gap_m = payload.get("gap_m")
        raw_views = payload.get("views")
        if direction not in {"horizontal", "vertical"}:
            raise ValueError("adapter alignment direction is invalid")
        if not isinstance(gap_m, (int, float)) or isinstance(gap_m, bool) or not math.isfinite(float(gap_m)) or float(gap_m) < 0:
            raise ValueError("adapter alignment gap is invalid")
        if not isinstance(raw_views, (list, tuple)):
            raise ValueError("adapter alignment views are invalid")
        views: list[dict[str, Any]] = []
        for raw_view in raw_views:
            view = MvpWorkflow._as_dict(raw_view)
            summary = MvpWorkflow._drawing_view_summary(view)
            position = view.get("position_m")
            position_dict = MvpWorkflow._as_dict(position)
            x_m, y_m = position_dict.get("x"), position_dict.get("y")
            if any(
                not isinstance(coordinate, (int, float))
                or isinstance(coordinate, bool)
                or not math.isfinite(float(coordinate))
                for coordinate in (x_m, y_m)
            ):
                raise ValueError("adapter alignment position is invalid")
            placement: dict[str, Any] = {
                **summary.model_dump(),
                "position_mm": {"x": float(x_m) * 1000, "y": float(y_m) * 1000},
            }
            outline = view.get("outline_m")
            if outline is not None:
                outline_dict = MvpWorkflow._as_dict(outline)
                values = [outline_dict.get(name) for name in ("left", "bottom", "right", "top")]
                if any(
                    not isinstance(item, (int, float))
                    or isinstance(item, bool)
                    or not math.isfinite(float(item))
                    for item in values
                ):
                    raise ValueError("adapter alignment outline is invalid")
                placement["outline_mm"] = {
                    name: float(item) * 1000
                    for name, item in zip(("left", "bottom", "right", "top"), values, strict=True)
                }
            views.append(placement)
        return {"direction": direction, "gap_mm": float(gap_m) * 1000, "views": views}

    @staticmethod
    def _sheet_metal_summary(value: Any) -> SheetMetalSummary:
        """Map adapter sheet-metal state to the public schema."""
        sheet_metal = MvpWorkflow._as_dict(value)
        thickness = sheet_metal.get("thickness_mm")
        if thickness is not None and not isinstance(thickness, (int, float)):
            raise ValueError("adapter sheet-metal thickness is invalid")
        flat_pattern_name = sheet_metal.get("flat_pattern_feature_name")
        return SheetMetalSummary(
            is_sheet_metal=bool(sheet_metal.get("is_sheet_metal", False)),
            is_flattened=(
                bool(sheet_metal["is_flattened"])
                if sheet_metal.get("is_flattened") is not None
                else None
            ),
            thickness_mm=float(thickness) if thickness is not None else None,
            flat_pattern_feature_name=(
                str(flat_pattern_name).strip()[:240]
                if flat_pattern_name is not None and str(flat_pattern_name).strip()
                else None
            ),
        )

    @staticmethod
    def _drawing_bom_summary(value: Any) -> DrawingBomSummary:
        """Map one adapter BOM row to a stable public confirmation."""
        bom = MvpWorkflow._as_dict(value)
        display_name = str(bom.get("display_name") or bom.get("name") or "").strip()[
            :240
        ]
        bom_type = str(bom.get("bom_type") or "").strip()
        if not display_name or bom_type not in {
            "top_level_only",
            "parts_only",
            "indented",
        }:
            raise ValueError("adapter BOM is missing required summary data")
        return DrawingBomSummary(
            display_name=display_name,
            bom_type=cast(
                Literal["top_level_only", "parts_only", "indented"], bom_type
            ),
            row_count=MvpWorkflow._optional_nonnegative_int(bom.get("row_count")),
            column_count=MvpWorkflow._optional_nonnegative_int(bom.get("column_count")),
        )

    @staticmethod
    def _drawing_annotation_summary(value: Any) -> DrawingAnnotationSummary:
        """Map one adapter drawing annotation result to the public schema."""
        annotation = MvpWorkflow._as_dict(value)
        annotation_type = str(annotation.get("annotation_type") or "").strip()
        display_name = str(
            annotation.get("display_name") or annotation.get("name") or ""
        ).strip()[:240]
        if annotation_type not in {
            "note", "dimension", "balloon", "hole_callout",
            "ordinate_dimension", "surface_finish", "weld_symbol", "geometric_tolerance", "center_mark",
        }:
            raise ValueError("adapter drawing annotation type is invalid")
        if not display_name:
            raise ValueError("adapter drawing annotation is missing a display name")
        return DrawingAnnotationSummary(
            annotation_type=cast(
                Literal[
                    "note", "dimension", "balloon", "hole_callout",
                    "ordinate_dimension", "surface_finish", "weld_symbol", "geometric_tolerance", "center_mark",
                ],
                annotation_type,
            ),
            display_name=display_name,
        )

    @staticmethod
    def _drawing_annotation_target_candidate(value: Any) -> dict[str, str]:
        """Validate one adapter-only persistent drawing annotation target."""
        target = MvpWorkflow._as_dict(value)
        view_id = str(target.get("view_id") or "").strip()[:240]
        target_type = str(target.get("target_type") or "").strip().lower()
        display_name = str(target.get("display_name") or "").strip()[:240]
        persistent_reference = str(target.get("persistent_reference") or "").strip()
        if (
            not view_id
            or target_type not in {"dimension", "component", "edge"}
            or not display_name
            or not persistent_reference
            or len(persistent_reference) > 4096
        ):
            raise ValueError(
                "adapter drawing target is missing safe reference metadata"
            )
        return {
            "view_id": view_id,
            "target_type": target_type,
            "display_name": display_name,
            "persistent_reference": persistent_reference,
        }

    @staticmethod
    def _drawing_annotation_target_candidates(value: Any) -> list[dict[str, str]]:
        """Validate adapter target collection before session issuance."""
        if not isinstance(value, (list, tuple)):
            return []
        return [
            MvpWorkflow._drawing_annotation_target_candidate(target) for target in value
        ]

    @staticmethod
    def _drawing_annotation_target_summary(
        target: Any,
    ) -> DrawingAnnotationTargetSummary:
        """Convert one private session target to a client-safe opaque reference."""
        return DrawingAnnotationTargetSummary(
            target_id=target.target_id,
            view_id=target.view_id,
            target_type=target.target_type,
            display_name=target.display_name,
        )

    @staticmethod
    def _optional_nonnegative_int(value: Any) -> int | None:
        """Accept optional non-negative integer metadata from a COM adapter."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("adapter count is invalid")
        if value < 0 or int(value) != value:
            raise ValueError("adapter count is invalid")
        return int(value)

    @staticmethod
    def _custom_property_summary(value: Any) -> CustomPropertySummary:
        """Map one adapter property row to the stable public schema."""
        property_data = MvpWorkflow._as_dict(value)
        name = str(property_data.get("name") or "").strip()[:255]
        if not name:
            raise ValueError("adapter custom property is missing a name")
        return CustomPropertySummary(
            name=name,
            value=str(property_data.get("value") or "")[:4096],
            resolved_value=str(
                property_data.get("resolved_value") or property_data.get("value") or ""
            )[:4096],
        )

    @staticmethod
    def _custom_property_summaries(value: Any) -> list[CustomPropertySummary]:
        """Map an adapter property collection to stable public summaries."""
        if not isinstance(value, (list, tuple)):
            return []
        return [MvpWorkflow._custom_property_summary(property_) for property_ in value]

    @staticmethod
    def _custom_property_configuration(
        document: Any, scope: str
    ) -> str | None | OperationResult:
        """Resolve a public property scope to the exact active configuration name."""
        if scope == "document":
            return None
        configuration_name = str(document.configuration or "").strip()
        if configuration_name:
            return configuration_name
        return OperationResult.failed(
            code="SW_ACTIVE_CONFIGURATION_UNKNOWN",
            message="SolidWorks did not report an active configuration for this document.",
            suggestion="Activate a known configuration with solidworks_activate_configuration, then retry.",
        )

    @staticmethod
    def _active_configuration(document: Any) -> str | OperationResult:
        """Require the tracked active configuration for configuration-scoped APIs."""
        configuration_name = str(document.configuration or "").strip()
        if configuration_name:
            return configuration_name
        return OperationResult.failed(
            code="SW_ACTIVE_CONFIGURATION_UNKNOWN",
            message="SolidWorks did not report an active configuration for this document.",
            suggestion="Activate a known configuration with solidworks_activate_configuration, then retry.",
        )

    def _resolve_dimension(
        self, request: GetDimensionRequest | SetDimensionRequest
    ) -> tuple[Any, Any, Any] | OperationResult:
        """Resolve a current opaque model dimension and preserve its native name privately."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
            dimension = self.sessions.resolve_dimension(
                session, request.document_id, request.dimension_id
            )
        except SessionLookupError:
            return self._document_not_found()
        except SketchStateError:
            return OperationResult.failed(
                code="SW_DIMENSION_NOT_CURRENT",
                message="The requested dimension is not current for this document.",
                suggestion="List model dimensions again and use one of the newly returned dimension_id values.",
            )
        part_failure = self._require_part_document(document)
        if part_failure is not None:
            return part_failure
        return session, document, dimension

    @staticmethod
    def _dimension_value(value: Any) -> float | None:
        """Normalize one adapter dimension readback to a finite millimetre value."""
        try:
            value_mm = float(value)
        except (TypeError, ValueError):
            return None
        return value_mm if math.isfinite(value_mm) else None

    @classmethod
    def _native_dimension_rows(cls, value: Any) -> list[tuple[str, float]]:
        """Validate adapter dimension discovery without returning native names to clients."""
        if not isinstance(value, (list, tuple)):
            raise ValueError("dimensions must be a sequence")
        rows: list[tuple[str, float]] = []
        seen: set[str] = set()
        for raw_row in value:
            row = cls._as_dict(raw_row)
            native_name = str(row.get("name") or "").strip()
            value_mm = cls._dimension_value(row.get("value_mm"))
            if not native_name or "@" not in native_name or value_mm is None:
                raise ValueError("dimension row is invalid")
            if native_name not in seen:
                seen.add(native_name)
                rows.append((native_name, value_mm))
        return rows

    @staticmethod
    def _invalid_dimension_response(action: str) -> OperationResult:
        """Return a safe failure when the adapter returns an invalid dimension value."""
        return OperationResult.failed(
            code="SW_ADAPTER_INVALID_RESPONSE",
            message=f"SolidWorks did not return a usable dimension value after the {action}.",
            suggestion="Inspect and rebuild the active part in SolidWorks, then list dimensions again.",
        )

    def _resolve_feature_dimension(
        self, request: GetFeatureDimensionRequest | SetFeatureDimensionRequest
    ) -> tuple[Any, Any, str] | OperationResult:
        """Map an opaque current feature and index to a private COM dimension name."""
        try:
            session = self.sessions.require(request.session_id)
            document = self.sessions.require_active_document(
                session, request.document_id
            )
            feature = self.sessions.require_feature(
                session, request.document_id, request.feature_id
            )
        except SessionLookupError:
            return self._document_not_found()
        except SketchStateError:
            return self._feature_not_current()
        part_failure = self._require_part_document(document)
        if part_failure is not None:
            return part_failure
        return session, document, f"D{request.dimension_index}@{feature.display_name}"

    def _require_current_reference_plane(
        self, session: Any, document_id: str, reference_plane_id: str
    ) -> OperationResult | None:
        """Revalidate an opaque plane capability while the shared COM lock is held."""
        try:
            self.sessions.require_reference_plane(
                session, document_id, reference_plane_id
            )
        except SessionLookupError:
            return self._document_not_found()
        except SketchStateError:
            return OperationResult.failed(
                code="SW_REFERENCE_PLANE_NOT_CURRENT",
                message="The requested reference plane is not current for this document.",
                suggestion="Create a new reference plane or use a current reference_plane_id, then retry.",
            )
        return None

    @staticmethod
    def _feature_not_current() -> OperationResult:
        """Build a consistent stale-feature capability failure."""
        return OperationResult.failed(
            code="SW_FEATURE_NOT_CURRENT",
            message="The requested feature is not current for this document.",
            suggestion="Use the feature reference returned by the most recent typed feature operation, then retry.",
        )

    @staticmethod
    def _feature_or_axis_not_current() -> OperationResult:
        """Build a consistent stale feature-or-axis capability failure."""
        return OperationResult.failed(
            code="SW_FEATURE_OR_AXIS_NOT_CURRENT",
            message="The requested feature or reference axis is not current for this document.",
            suggestion="Get a current origin axis and use a feature from the current document revision, then retry.",
        )

    @staticmethod
    def _feature_or_plane_not_current() -> OperationResult:
        """Build a consistent stale feature-or-plane capability failure."""
        return OperationResult.failed(
            code="SW_FEATURE_OR_PLANE_NOT_CURRENT",
            message="The requested feature or reference plane is not current for this document.",
            suggestion="Create a current reference plane and use a feature from the current document revision, then retry.",
        )

    @staticmethod
    def _require_part_document(document: Any) -> OperationResult | None:
        """Return a client-safe failure when a part-only API targets another type."""
        if document.document_type == "part":
            return None
        return OperationResult.failed(
            code="SW_PART_REQUIRED",
            message="This operation requires an active part document.",
            suggestion="Create or open a part document in this workflow session and retry.",
        )

    @staticmethod
    def _require_drawing_document(document: Any) -> OperationResult | None:
        """Return a client-safe failure when a drawing-only API targets another type."""
        if document.document_type == "drawing":
            return None
        return OperationResult.failed(
            code="SW_DRAWING_REQUIRED",
            message="This operation requires an active drawing document.",
            suggestion="Create or activate a drawing document in this workflow session and retry.",
        )

    @staticmethod
    def _assembly_required(operation: str) -> OperationResult:
        """Build a consistent failure for operations scoped to an assembly document."""
        return OperationResult.failed(
            code="SW_ASSEMBLY_REQUIRED",
            message=f"Assembly {operation} requires an active assembly document.",
            suggestion="Create or activate an assembly document in this workflow session and retry.",
        )

    @staticmethod
    def _mate_entity_reference_error(reason: str) -> OperationResult:
        """Map session-only reference failures to actionable MCP error envelopes."""
        if reason == "stale":
            return OperationResult.failed(
                code="SW_MATE_ENTITY_STALE",
                message="The requested assembly mate entity belongs to an older document revision.",
                suggestion="List assembly mate entities again, then use the newly returned entity_id values.",
            )
        if reason == "wrong_document":
            return OperationResult.failed(
                code="SW_MATE_ENTITY_WRONG_DOCUMENT",
                message="The requested assembly mate entity belongs to another assembly document.",
                suggestion="List mate entities from the active assembly and use those entity_id values.",
            )
        return OperationResult.failed(
            code="SW_MATE_ENTITY_NOT_FOUND",
            message="The requested assembly mate entity was not issued by this workflow session.",
            suggestion="List assembly mate entities and use one of the returned entity_id values.",
        )

    @staticmethod
    def _part_topology_reference_error(reason: str) -> OperationResult:
        """Return a safe error for an invalid or stale part face/edge capability."""
        if reason == "stale":
            return OperationResult.failed(
                code="SW_PART_TOPOLOGY_STALE",
                message="The requested part topology changed after it was listed.",
                suggestion="List part topology again and use the newly issued IDs.",
            )
        if reason == "wrong_document":
            return OperationResult.failed(
                code="SW_PART_TOPOLOGY_WRONG_DOCUMENT",
                message="The requested part topology belongs to another document.",
                suggestion="Activate the correct part and list its topology again.",
            )
        return OperationResult.failed(
            code="SW_PART_TOPOLOGY_NOT_FOUND",
            message="The requested part topology ID is unknown to this workflow session.",
            suggestion="List part topology and use one of the returned IDs.",
        )

    @staticmethod
    def _assembly_mate_reference_error(reason: str) -> OperationResult:
        """Return a safe error for an invalid or stale existing-mate capability."""
        if reason == "stale":
            return OperationResult.failed(
                code="SW_ASSEMBLY_MATE_STALE",
                message="The requested assembly mate changed after it was listed.",
                suggestion="List assembly mates again and use a newly issued mate_id.",
            )
        if reason == "wrong_document":
            return OperationResult.failed(
                code="SW_ASSEMBLY_MATE_WRONG_DOCUMENT",
                message="The requested assembly mate belongs to another document.",
                suggestion="Activate the correct assembly and list its mates again.",
            )
        return OperationResult.failed(
            code="SW_ASSEMBLY_MATE_NOT_FOUND",
            message="The requested assembly mate ID is unknown to this workflow session.",
            suggestion="List assembly mates and use one of the returned mate IDs.",
        )

    @staticmethod
    def _drawing_bom_reference_error(reason: str) -> OperationResult:
        """Return a safe error for an invalid or stale drawing BOM capability."""
        if reason == "stale":
            return OperationResult.failed(
                code="SW_DRAWING_BOM_STALE",
                message="The requested drawing BOM changed after it was created or updated.",
                suggestion="Use the bom_id returned by the latest BOM operation.",
            )
        if reason == "wrong_document":
            return OperationResult.failed(
                code="SW_DRAWING_BOM_WRONG_DOCUMENT",
                message="The requested drawing BOM belongs to another document.",
                suggestion="Activate the correct drawing and use its returned bom_id.",
            )
        return OperationResult.failed(
            code="SW_DRAWING_BOM_NOT_FOUND",
            message="The requested drawing BOM ID is unknown to this workflow session.",
            suggestion="Create a BOM in this drawing session and use its returned bom_id.",
        )

    @staticmethod
    def _drawing_annotation_reference_error(reason: str) -> OperationResult:
        """Map private drawing target failures to actionable MCP error envelopes."""
        if reason == "stale":
            return OperationResult.failed(
                code="SW_DRAWING_ANNOTATION_TARGET_STALE",
                message="The requested drawing annotation target belongs to an older drawing revision.",
                suggestion="List drawing annotation targets again and use a newly returned target_id.",
            )
        if reason == "wrong_document":
            return OperationResult.failed(
                code="SW_DRAWING_ANNOTATION_TARGET_WRONG_DOCUMENT",
                message="The requested drawing annotation target belongs to another drawing document.",
                suggestion="List annotation targets from the active drawing and use those target_id values.",
            )
        return OperationResult.failed(
            code="SW_DRAWING_ANNOTATION_TARGET_NOT_FOUND",
            message="The requested drawing annotation target was not issued by this workflow session.",
            suggestion="List drawing annotation targets and use one of the returned target_id values.",
        )

    @staticmethod
    def _drawing_source_view_reference_error(reason: str) -> OperationResult:
        """Map private BOM source-view failures to actionable MCP error envelopes."""
        if reason == "stale":
            return OperationResult.failed(
                code="SW_DRAWING_BOM_SOURCE_VIEW_STALE",
                message="The requested BOM source view belongs to an older drawing or assembly revision.",
                suggestion="Recreate the assembly drawing view, then use its newly returned view_id.",
            )
        if reason == "wrong_document":
            return OperationResult.failed(
                code="SW_DRAWING_BOM_SOURCE_VIEW_WRONG_DRAWING",
                message="The requested BOM source view belongs to another drawing document.",
                suggestion="Create an assembly view in the active drawing and use its returned view_id.",
            )
        if reason == "wrong_source":
            return OperationResult.failed(
                code="SW_DRAWING_BOM_SOURCE_VIEW_MISMATCH",
                message="The requested BOM source view was created from a different assembly document.",
                suggestion="Use the view_id returned when creating a view of the requested assembly.",
            )
        return OperationResult.failed(
            code="SW_DRAWING_BOM_SOURCE_VIEW_NOT_FOUND",
            message="The requested BOM source view was not issued by this workflow session.",
            suggestion="Create a view of the saved assembly in this drawing and use its returned view_id.",
        )

    def _trusted_component_source_path(self, document: Any) -> Path | OperationResult:
        """Resolve only a saved session-owned part artifact as a component source."""
        if document.document_type != "part":
            return OperationResult.failed(
                code="SW_COMPONENT_SOURCE_NOT_PART",
                message="Assembly component insertion currently accepts only a saved part document.",
                suggestion="Use a session-owned .sldprt document saved with solidworks_save_model.",
            )
        if not document.path:
            return OperationResult.failed(
                code="SW_COMPONENT_SOURCE_NOT_SAVED",
                message="The source part has not been saved as a controlled native artifact.",
                suggestion="Call solidworks_save_model for the source part before inserting it into an assembly.",
            )

        source_path = Path(document.path)
        if source_path.suffix.lower() != ".sldprt":
            return OperationResult.failed(
                code="SW_COMPONENT_SOURCE_NOT_PART",
                message="The source document does not have a native part artifact path.",
                suggestion="Save the source part with solidworks_save_model and use its document reference.",
            )
        try:
            self.artifact_store.describe(source_path, kind="sldprt")
        except FileNotFoundError:
            return OperationResult.failed(
                code="SW_COMPONENT_SOURCE_MISSING",
                message="The saved source part artifact is no longer available to SolidWorks.",
                suggestion="Save the source part again with solidworks_save_model before inserting it.",
            )
        except (OSError, ValueError):
            return OperationResult.failed(
                code="SW_COMPONENT_SOURCE_UNTRUSTED",
                message="The requested source part is outside the controlled artifact policy.",
                suggestion="Use a part document saved by solidworks_save_model in this workflow session.",
            )
        return source_path.resolve()

    def _trusted_drawing_view_source_path(
        self, document: Any
    ) -> Path | OperationResult:
        """Resolve only saved session-owned part or assembly artifacts for drawing views."""
        if document.document_type not in {"part", "assembly"}:
            return OperationResult.failed(
                code="SW_DRAWING_VIEW_SOURCE_NOT_MODEL",
                message="Drawing views currently accept only a saved part or assembly document.",
                suggestion="Use a session-owned .sldprt or .sldasm document saved with solidworks_save_model.",
            )
        if not document.path:
            return OperationResult.failed(
                code="SW_DRAWING_VIEW_SOURCE_NOT_SAVED",
                message="The source model has not been saved as a controlled native artifact.",
                suggestion="Call solidworks_save_model for the source model before creating a drawing view.",
            )

        source_path = Path(document.path)
        expected_extension = {
            "part": ".sldprt",
            "assembly": ".sldasm",
        }[document.document_type]
        if source_path.suffix.lower() != expected_extension:
            return OperationResult.failed(
                code="SW_DRAWING_VIEW_SOURCE_NOT_MODEL",
                message="The source document does not have a matching native model artifact path.",
                suggestion="Save the source part or assembly with solidworks_save_model and use its document reference.",
            )
        try:
            self.artifact_store.describe(
                source_path, kind=expected_extension.lstrip(".")
            )
        except FileNotFoundError:
            return OperationResult.failed(
                code="SW_DRAWING_VIEW_SOURCE_MISSING",
                message="The saved source model artifact is no longer available to SolidWorks.",
                suggestion="Save the source model again with solidworks_save_model before creating the drawing view.",
            )
        except (OSError, ValueError):
            return OperationResult.failed(
                code="SW_DRAWING_VIEW_SOURCE_UNTRUSTED",
                message="The requested source model is outside the controlled artifact policy.",
                suggestion="Use a model document saved by solidworks_save_model in this workflow session.",
            )
        return source_path.resolve()

    def _trusted_sheet_metal_source_path(self, document: Any) -> Path | OperationResult:
        """Resolve only a saved session-owned native part as a DXF export source."""
        if document.document_type != "part":
            return OperationResult.failed(
                code="SW_PART_REQUIRED",
                message="Flat-pattern DXF export requires an active sheet-metal part document.",
                suggestion="Create or open a sheet-metal part in this workflow session and retry.",
            )
        if not document.path:
            return OperationResult.failed(
                code="SW_SHEET_METAL_SOURCE_NOT_SAVED",
                message="Flat-pattern DXF export requires a saved native part artifact.",
                suggestion="Call solidworks_save_model for the sheet-metal part before exporting DXF.",
            )
        source_path = Path(document.path)
        if source_path.suffix.lower() != ".sldprt":
            return OperationResult.failed(
                code="SW_SHEET_METAL_SOURCE_NOT_PART",
                message="The active document does not have a native .sldprt artifact path.",
                suggestion="Save the sheet-metal part with solidworks_save_model and retry.",
            )
        try:
            self.artifact_store.describe(source_path, kind="sldprt")
        except FileNotFoundError:
            return OperationResult.failed(
                code="SW_SHEET_METAL_SOURCE_MISSING",
                message="The saved sheet-metal source part is no longer available to SolidWorks.",
                suggestion="Save the sheet-metal part again before exporting DXF.",
            )
        except (OSError, ValueError):
            return OperationResult.failed(
                code="SW_SHEET_METAL_SOURCE_UNTRUSTED",
                message="The requested sheet-metal source is outside the controlled artifact policy.",
                suggestion="Use a part document saved with solidworks_save_model in this workflow session.",
            )
        return source_path.resolve()

    @staticmethod
    def _sheet_metal_export_options(request: ExportFlatPatternDxfRequest) -> int:
        """Translate explicit DXF options to documented ``ExportToDWG2`` bit flags."""
        options = 1
        for enabled, flag in (
            (request.include_hidden_edges, 2),
            (request.include_bend_lines, 4),
            (request.include_sketches, 8),
            (request.merge_coplanar_faces, 16),
            (request.include_library_features, 32),
            (request.include_forming_tools, 64),
            (request.include_bounding_box, 2048),
        ):
            if enabled:
                options |= flag
        return options

    @staticmethod
    def _paginate(
        items: list[Any], limit: int, offset: int
    ) -> dict[str, int | bool | None]:
        """Return standard bounded-list metadata without exposing adapter internals."""
        total = len(items)
        count = len(items[offset : offset + limit])
        next_offset = offset + count
        return {
            "total": total,
            "count": count,
            "offset": offset,
            "has_more": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
        }

    @staticmethod
    def _millimetres(value: float, unit: str) -> float:
        """Convert public length units to the adapter's millimetre convention."""
        return {"mm": value, "m": value * 1000, "in": value * 25.4}[unit]

    @staticmethod
    def _chamfer_preflight(
        entities: list[Any],
        *,
        distance_mm: float,
        angle_degrees: float,
        chamfer_type: str,
    ) -> dict[str, Any]:
        """Build conservative, non-blocking guidance from listed edge geometry.

        SolidWorks remains the authority for BREP validity.  The workflow only
        reports an early warning when a requested distance is at least as large
        as a measured edge length, which commonly means the selected edge or
        requested size is wrong.  Missing length metadata is reported rather
        than guessed.
        """
        warnings: list[dict[str, Any]] = []
        edge_metrics: list[dict[str, Any]] = []
        for entity in entities:
            length_mm = getattr(entity, "length_mm", None)
            metric: dict[str, Any] = {
                "edge_id": entity.entity_id,
                "display_name": entity.display_name,
                "length_mm": length_mm,
                "curve_identity": getattr(entity, "curve_identity", None),
            }
            if isinstance(length_mm, (int, float)) and math.isfinite(float(length_mm)):
                metric["distance_to_length_ratio"] = (
                    distance_mm / float(length_mm) if length_mm > 0 else None
                )
                if length_mm > 0 and distance_mm >= float(length_mm):
                    warnings.append(
                        {
                            "code": "SW_CHAMFER_DISTANCE_NOT_SMALLER_THAN_EDGE",
                            "severity": "warning",
                            "edge_id": entity.entity_id,
                            "message": (
                                "The requested distance is at least the measured "
                                "edge length; SolidWorks may reject or remove the edge."
                            ),
                            "suggestion": (
                                "Verify the selected edge and retry with a smaller "
                                "distance, then inspect the returned feature diagnostics."
                            ),
                        }
                    )
            else:
                metric["geometry_warning"] = "edge length was not returned"
            edge_metrics.append(metric)
        return {
            "status": "warning" if warnings else "ok",
            "blocking": False,
            "distance_mm": distance_mm,
            "angle_degrees": angle_degrees,
            "chamfer_type": chamfer_type,
            "angle_applied": chamfer_type == "distance_angle",
            "edge_metrics": edge_metrics,
            "warnings": warnings,
            "geometry_validated": False,
        }

    @staticmethod
    def _session_not_found() -> OperationResult:
        """Build a consistent session lookup failure."""
        return OperationResult.failed(
            code="SW_SESSION_NOT_FOUND",
            message="The SolidWorks session does not exist in this server process.",
            suggestion="Call solidworks_create_session and use the returned session_id.",
        )

    @staticmethod
    def _document_not_found() -> OperationResult:
        """Build a consistent document ownership/state failure."""
        return OperationResult.failed(
            code="SW_DOCUMENT_NOT_ACTIVE",
            message="The requested document is not active in this workflow session.",
            suggestion="Create or open the document in this session, then retry with its document_id.",
        )

    @staticmethod
    def _sketch_not_ready(suggestion: str) -> OperationResult:
        """Build the actionable sketch state failure used by write operations."""
        return OperationResult.failed(
            code="SW_SKETCH_NOT_READY",
            message="The requested sketch is not in the required SolidWorks editing state.",
            suggestion=suggestion,
        )

    @staticmethod
    def _sketch_context_locked() -> OperationResult:
        """Report that another session currently owns the global sketch edit context."""
        return OperationResult.failed(
            code="SW_SKETCH_CONTEXT_LOCKED",
            message="Another workflow session is editing a SolidWorks sketch.",
            suggestion="Finish or exit the other session's sketch before switching documents.",
        )
