"""Typed, prefixed MCP tools for the first reliable SolidWorks workflow slice."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from fastmcp import FastMCP
from pydantic import BaseModel

from ..application.mvp_workflow import MvpWorkflow
from ..artifacts.store import ArtifactStore, ModelInputStore
from ..artifacts.trusted_macros import TrustedMacroStore
from ..contracts.mvp import (
    AlignDrawingViewsRequest,
    ActivateConfigurationRequest,
    ActivateDrawingSheetRequest,
    ActivateDrawingViewRequest,
    ActivateDocumentRequest,
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
    AddDrawingRevisionRequest,
    ChamferSketchCornerRequest,
    CheckAssemblyInterferenceRequest,
    CloseDocumentRequest,
    ConvertSketchEntitiesRequest,
    Create3DSketchRequest,
    CreateAssemblyMateRequest,
    CreateAssemblyRequest,
    CreateConfigurationRequest,
    CreateChamferRequest,
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
    DeleteAssemblyMateRequest,
    DeleteConfigurationRequest,
    DeleteCustomPropertyRequest,
    DeleteDrawingBomRequest,
    DeleteDrawingRevisionRequest,
    DeleteDrawingSheetRequest,
    DeleteEquationRequest,
    DocumentRequest,
    ExecuteTrustedMacroRequest,
    ExitSketchRequest,
    FilletSketchCornerRequest,
    ExportFileRequest,
    ExportFlatPatternDxfRequest,
    GetAssemblyComponentRequest,
    GetBoundingBoxRequest,
    GetConfigurationRequest,
    GetDimensionRequest,
    GetDrawingViewOutlineRequest,
    GetEquationRequest,
    GetFeatureDimensionRequest,
    GetMaterialAssignmentRequest,
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
    ListDrawingSheetsRequest,
    ListDrawingViewsRequest,
    ListDrawingRevisionsRequest,
    ListEquationsRequest,
    ListFeaturesRequest,
    ListPartTopologyRequest,
    ListSheetMetalFeatureGeometryRequest,
    ListSolidBodiesRequest,
    ListSurfaceBodiesRequest,
    MirrorFeatureRequest,
    MirrorSketchEntitiesRequest,
    OffsetSketchEntitiesRequest,
    OpenModelRequest,
    OperationResult,
    PlaceDrawingDimensionRequest,
    PrepareTrustedMacroRequest,
    RebuildDrawingRequest,
    RenameDrawingSheetRequest,
    ReorderDrawingSheetsRequest,
    SaveModelRequest,
    SessionRequest,
    SetCustomPropertyRequest,
    SetDimensionRequest,
    SetEquationRequest,
    SetFeatureDimensionRequest,
    SetMaterialAssignmentRequest,
    SetSheetMetalKFactorRequest,
    SheetMetalFlatStateRequest,
    SplitSketchEntityRequest,
    TrimSketchEntityRequest,
    UpdateDrawingViewLayoutRequest,
    UpdateDrawingBomRequest,
)

TRequest = TypeVar("TRequest", bound=BaseModel)


def _parse_request(input_data: Any, request_type: type[TRequest]) -> TRequest:
    """Accept FastMCP-parsed models and raw JSON payloads in direct callers."""
    if isinstance(input_data, request_type):
        return input_data
    return request_type.model_validate(input_data)


async def register_mvp_tools(mcp: FastMCP, adapter: Any, config: Any) -> int:
    """Register the isolated v2 MVP toolset alongside legacy tools.

    The tools deliberately use a ``solidworks_`` prefix and a uniform
    ``OperationResult`` return type.  They do not replace the established
    unprefixed tool API until the typed workflow has live-SolidWorks coverage.
    """
    before = len(await mcp.list_tools())
    artifact_root = Path(config.data_dir) / "artifacts"
    configured_input_root = getattr(config, "model_input_dir", None)
    model_input_root = (
        Path(configured_input_root)
        if configured_input_root is not None
        else Path(config.data_dir) / "inputs"
    )
    configured_macro_root = getattr(config, "trusted_macro_dir", None)
    trusted_macro_root = (
        Path(configured_macro_root)
        if configured_macro_root is not None
        else Path(config.data_dir) / "trusted_macros"
    )
    workflow = MvpWorkflow(
        adapter=adapter,
        artifact_store=ArtifactStore(artifact_root),
        model_input_store=ModelInputStore(model_input_root),
        trusted_macro_store=TrustedMacroStore(
            trusted_macro_root,
            enabled=bool(getattr(config, "enable_trusted_macros", False)),
            max_macro_bytes=int(
                getattr(config, "trusted_macro_max_bytes", 20 * 1024 * 1024)
            ),
        ),
        macro_confirmation_ttl_seconds=int(
            getattr(config, "macro_confirmation_ttl_seconds", 300)
        ),
    )

    @mcp.tool(
        name="solidworks_create_session",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_session() -> OperationResult:
        """Create an opaque workflow session and report SolidWorks connection health."""
        return await workflow.create_session()

    @mcp.tool(
        name="solidworks_close_session",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_close_session(input_data: SessionRequest) -> OperationResult:
        """Release a workflow session after its active sketch has been exited."""
        return await workflow.close_session(input_data.session_id)

    @mcp.tool(
        name="solidworks_get_status",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_status(input_data: SessionRequest) -> OperationResult:
        """Read the active document and connection state for a workflow session."""
        return await workflow.get_status(input_data.session_id)

    @mcp.tool(
        name="solidworks_list_documents",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_documents(input_data: SessionRequest) -> OperationResult:
        """List the native documents already owned by the current workflow session."""
        input_data = _parse_request(input_data, SessionRequest)
        return await workflow.list_documents(input_data.session_id)

    @mcp.tool(
        name="solidworks_activate_document",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_activate_document(
        input_data: ActivateDocumentRequest,
    ) -> OperationResult:
        """Activate one existing session document by its opaque document ID."""
        input_data = _parse_request(input_data, ActivateDocumentRequest)
        return await workflow.activate_document(input_data)

    @mcp.tool(
        name="solidworks_close_document",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_close_document(
        input_data: CloseDocumentRequest,
    ) -> OperationResult:
        """Close the active session document using only its opaque document ID."""
        input_data = _parse_request(input_data, CloseDocumentRequest)
        return await workflow.close_document(input_data)

    @mcp.tool(
        name="solidworks_open_model",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_open_model(input_data: OpenModelRequest) -> OperationResult:
        """Open a native model from the configured trusted input directory."""
        return await workflow.open_model(input_data)

    @mcp.tool(
        name="solidworks_create_part",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_part(input_data: CreatePartRequest) -> OperationResult:
        """Create and track a new active part document in a workflow session."""
        return await workflow.create_part(input_data)

    @mcp.tool(
        name="solidworks_create_assembly",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_assembly(
        input_data: CreateAssemblyRequest,
    ) -> OperationResult:
        """Create and track a new assembly document in a workflow session."""
        return await workflow.create_assembly(input_data)

    @mcp.tool(
        name="solidworks_create_drawing",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing(
        input_data: CreateDrawingRequest,
    ) -> OperationResult:
        """Create and track a new drawing document in a workflow session."""
        return await workflow.create_drawing(input_data)

    @mcp.tool(
        name="solidworks_insert_assembly_component",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_insert_assembly_component(
        input_data: InsertAssemblyComponentRequest,
    ) -> OperationResult:
        """Insert one controlled saved part into the active workflow assembly."""
        return await workflow.insert_assembly_component(input_data)

    @mcp.tool(
        name="solidworks_list_assembly_components",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_assembly_components(
        input_data: ListAssemblyComponentsRequest,
    ) -> OperationResult:
        """List one bounded, safe page of components in the active workflow assembly."""
        return await workflow.list_assembly_components(input_data)

    @mcp.tool(
        name="solidworks_get_assembly_component",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_assembly_component(
        input_data: GetAssemblyComponentRequest,
    ) -> OperationResult:
        """Get one active-assembly component by its exact display name."""
        return await workflow.get_assembly_component(input_data)

    @mcp.tool(
        name="solidworks_check_assembly_interference",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_check_assembly_interference(
        input_data: CheckAssemblyInterferenceRequest,
    ) -> OperationResult:
        """Run native SolidWorks interference detection on the active assembly."""
        return await workflow.check_assembly_interference(input_data)

    @mcp.tool(
        name="solidworks_list_assembly_mate_entities",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_assembly_mate_entities(
        input_data: ListAssemblyMateEntitiesRequest,
    ) -> OperationResult:
        """List revision-bound opaque face and edge IDs for an active assembly."""
        return await workflow.list_assembly_mate_entities(input_data)

    @mcp.tool(
        name="solidworks_create_assembly_mate",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_assembly_mate(
        input_data: CreateAssemblyMateRequest,
    ) -> OperationResult:
        """Create one typed mate from two current opaque assembly entity IDs."""
        return await workflow.create_assembly_mate(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_view",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing_view(
        input_data: CreateDrawingViewRequest,
    ) -> OperationResult:
        """Add one standard view of a controlled saved model to an active drawing."""
        return await workflow.create_drawing_view(input_data)

    @mcp.tool(
        name="solidworks_list_drawing_views",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_drawing_views(
        input_data: ListDrawingViewsRequest,
    ) -> OperationResult:
        """List one bounded, safe page of model views in the active drawing."""
        return await workflow.list_drawing_views(input_data)

    @mcp.tool(
        name="solidworks_list_drawing_sheets",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_drawing_sheets(
        input_data: ListDrawingSheetsRequest,
    ) -> OperationResult:
        """List a bounded page of sheets in the active drawing."""
        return await workflow.list_drawing_sheets(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_sheet",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing_sheet(
        input_data: CreateDrawingSheetRequest,
    ) -> OperationResult:
        """Create and activate a standard or explicitly sized drawing sheet."""
        return await workflow.create_drawing_sheet(input_data)

    @mcp.tool(
        name="solidworks_activate_drawing_sheet",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_activate_drawing_sheet(
        input_data: ActivateDrawingSheetRequest,
    ) -> OperationResult:
        """Activate one existing drawing sheet by its listed exact name."""
        return await workflow.activate_drawing_sheet(input_data)

    @mcp.tool(
        name="solidworks_delete_drawing_sheet",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_delete_drawing_sheet(
        input_data: DeleteDrawingSheetRequest,
    ) -> OperationResult:
        """Delete one drawing sheet, retaining the mandatory final sheet."""
        return await workflow.delete_drawing_sheet(input_data)

    @mcp.tool(
        name="solidworks_rename_drawing_sheet",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_rename_drawing_sheet(
        input_data: RenameDrawingSheetRequest,
    ) -> OperationResult:
        """Rename one current drawing sheet to a distinct exact sheet name."""
        return await workflow.rename_drawing_sheet(input_data)

    @mcp.tool(name="solidworks_reorder_drawing_sheets", annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
    async def solidworks_reorder_drawing_sheets(input_data: ReorderDrawingSheetsRequest) -> OperationResult:
        """Replace the active drawing's complete sheet order."""
        try:
            input_data = _parse_request(input_data, ReorderDrawingSheetsRequest)
        except Exception as exc:
            return OperationResult.failed(code="SW_INVALID_INPUT", message=f"Invalid drawing-sheet reorder request: {str(exc)[:430]}", suggestion="Provide session_id, document_id, and every current sheet name once.")
        return await workflow.reorder_drawing_sheets(input_data)

    @mcp.tool(
        name="solidworks_get_drawing_view_outline",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_drawing_view_outline(
        input_data: GetDrawingViewOutlineRequest,
    ) -> OperationResult:
        """Read one active-drawing view's axis-aligned layout outline in millimetres."""
        return await workflow.get_drawing_view_outline(input_data)

    @mcp.tool(
        name="solidworks_update_drawing_view_layout",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_update_drawing_view_layout(
        input_data: UpdateDrawingViewLayoutRequest,
    ) -> OperationResult:
        """Move and/or rescale one current drawing view by its listed view ID."""
        return await workflow.update_drawing_view_layout(input_data)

    @mcp.tool(
        name="solidworks_align_drawing_views",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_align_drawing_views(
        input_data: AlignDrawingViewsRequest,
    ) -> OperationResult:
        """Arrange two or more current drawing views with outline-aware spacing."""
        return await workflow.align_drawing_views(input_data)

    @mcp.tool(
        name="solidworks_rebuild_drawing",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_rebuild_drawing(
        input_data: RebuildDrawingRequest,
    ) -> OperationResult:
        """Rebuild and save a session-owned drawing before inspection or export."""
        return await workflow.rebuild_drawing(input_data)

    @mcp.tool(
        name="solidworks_activate_drawing_view",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_activate_drawing_view(
        input_data: ActivateDrawingViewRequest,
    ) -> OperationResult:
        """Activate one drawing view returned by solidworks_list_drawing_views."""
        return await workflow.activate_drawing_view(input_data)

    @mcp.tool(
        name="solidworks_create_projected_drawing_view",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_projected_drawing_view(
        input_data: CreateProjectedDrawingViewRequest,
    ) -> OperationResult:
        """Create one projected view from a view ID returned by drawing view listing."""
        return await workflow.create_projected_drawing_view(input_data)

    @mcp.tool(
        name="solidworks_create_auxiliary_drawing_view",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_auxiliary_drawing_view(
        input_data: CreateAuxiliaryDrawingViewRequest,
    ) -> OperationResult:
        """Create an auxiliary view from an edge target listed in the active drawing."""
        return await workflow.create_auxiliary_drawing_view(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_bom",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing_bom(
        input_data: CreateDrawingBomRequest,
    ) -> OperationResult:
        """Insert a typed assembly BOM table in the active drawing from a known view."""
        return await workflow.create_drawing_bom(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_note",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing_note(
        input_data: CreateDrawingNoteRequest,
    ) -> OperationResult:
        """Insert a real, positioned text note in the active drawing."""
        return await workflow.create_drawing_note(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_revision_table",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing_revision_table(
        input_data: CreateDrawingRevisionTableRequest,
    ) -> OperationResult:
        """Insert a revision table and add one initial revision row."""
        return await workflow.create_drawing_revision_table(input_data)

    @mcp.tool(
        name="solidworks_list_drawing_revisions",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_drawing_revisions(
        input_data: ListDrawingRevisionsRequest,
    ) -> OperationResult:
        """List revision rows and symbol counts from a current revision table."""
        try:
            input_data = _parse_request(input_data, ListDrawingRevisionsRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid revision-table list request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, and revision_table_id.",
            )
        return await workflow.list_drawing_revisions(input_data)

    @mcp.tool(
        name="solidworks_add_drawing_revision",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_drawing_revision(
        input_data: AddDrawingRevisionRequest,
    ) -> OperationResult:
        """Add one revision row to a current revision table."""
        try:
            input_data = _parse_request(input_data, AddDrawingRevisionRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid revision add request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, revision_table_id, and revision.",
            )
        return await workflow.add_drawing_revision(input_data)

    @mcp.tool(
        name="solidworks_delete_drawing_revision",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_delete_drawing_revision(
        input_data: DeleteDrawingRevisionRequest,
    ) -> OperationResult:
        """Delete one revision row by its native revision ID."""
        try:
            input_data = _parse_request(input_data, DeleteDrawingRevisionRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid revision delete request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, revision_table_id, and revision_id.",
            )
        return await workflow.delete_drawing_revision(input_data)

    @mcp.tool(
        name="solidworks_list_drawing_annotation_targets",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_drawing_annotation_targets(
        input_data: ListDrawingAnnotationTargetsRequest,
    ) -> OperationResult:
        """List opaque revision-bound targets for drawing dimensions and callouts."""
        return await workflow.list_drawing_annotation_targets(input_data)

    @mcp.tool(
        name="solidworks_place_drawing_dimension",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_place_drawing_dimension(
        input_data: PlaceDrawingDimensionRequest,
    ) -> OperationResult:
        """Place a server-issued display dimension on the active drawing sheet."""
        return await workflow.place_drawing_dimension(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_balloon",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing_balloon(
        input_data: CreateDrawingBalloonRequest,
    ) -> OperationResult:
        """Create a BOM balloon from a server-issued visible component target."""
        return await workflow.create_drawing_balloon(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_hole_callout",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_drawing_hole_callout(
        input_data: CreateDrawingHoleCalloutRequest,
    ) -> OperationResult:
        """Create an automatic hole callout from a server-issued visible edge target."""
        return await workflow.create_drawing_hole_callout(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_ordinate_dimension",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    async def solidworks_create_drawing_ordinate_dimension(
        input_data: CreateDrawingOrdinateDimensionRequest,
    ) -> OperationResult:
        """Create a horizontal or vertical ordinate dimension from a listed display dimension."""
        try:
            input_data = _parse_request(input_data, CreateDrawingOrdinateDimensionRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid ordinate-dimension request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, and a current dimension target_id.",
            )
        return await workflow.create_drawing_ordinate_dimension(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_surface_finish",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    async def solidworks_create_drawing_surface_finish(
        input_data: CreateDrawingSurfaceFinishRequest,
    ) -> OperationResult:
        """Insert an ISO surface-finish symbol from a listed drawing edge."""
        try:
            input_data = _parse_request(input_data, CreateDrawingSurfaceFinishRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid surface-finish request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, and a current edge target_id.",
            )
        return await workflow.create_drawing_surface_finish(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_weld_symbol",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    async def solidworks_create_drawing_weld_symbol(
        input_data: CreateDrawingWeldSymbolRequest,
    ) -> OperationResult:
        """Insert a configured weld symbol from a listed drawing edge."""
        try:
            input_data = _parse_request(input_data, CreateDrawingWeldSymbolRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid weld-symbol request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, and a current edge target_id.",
            )
        return await workflow.create_drawing_weld_symbol(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_geometric_tolerance",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    async def solidworks_create_drawing_geometric_tolerance(
        input_data: CreateDrawingGeometricToleranceRequest,
    ) -> OperationResult:
        """Insert a geometric-tolerance frame from a listed display dimension."""
        try:
            input_data = _parse_request(input_data, CreateDrawingGeometricToleranceRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid geometric-tolerance request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, a tolerance, and a current dimension target_id.",
            )
        return await workflow.create_drawing_geometric_tolerance(input_data)

    @mcp.tool(
        name="solidworks_create_drawing_center_mark",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    async def solidworks_create_drawing_center_mark(
        input_data: CreateDrawingCenterMarkRequest,
    ) -> OperationResult:
        """Create a center mark from a listed circular drawing edge."""
        try:
            input_data = _parse_request(input_data, CreateDrawingCenterMarkRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid center-mark request: {str(exc)[:430]}",
                suggestion="Provide session_id, document_id, and a current circular edge target_id.",
            )
        return await workflow.create_drawing_center_mark(input_data)

    @mcp.tool(
        name="solidworks_list_custom_properties",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_custom_properties(
        input_data: ListCustomPropertiesRequest,
    ) -> OperationResult:
        """List bounded document-level or active-configuration custom properties."""
        return await workflow.list_custom_properties(input_data)

    @mcp.tool(
        name="solidworks_set_custom_property",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_set_custom_property(
        input_data: SetCustomPropertyRequest,
    ) -> OperationResult:
        """Create or replace one document-level or active-configuration property."""
        return await workflow.set_custom_property(input_data)

    @mcp.tool(
        name="solidworks_delete_custom_property",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_delete_custom_property(
        input_data: DeleteCustomPropertyRequest,
    ) -> OperationResult:
        """Delete one document-level or active-configuration custom property."""
        return await workflow.delete_custom_property(input_data)

    @mcp.tool(
        name="solidworks_create_sketch",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_sketch(
        input_data: CreateSketchRequest,
    ) -> OperationResult:
        """Create a sketch on an origin plane or a current server-issued reference plane."""
        return await workflow.create_sketch(input_data)

    @mcp.tool(
        name="solidworks_create_reference_plane",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_reference_plane(
        input_data: CreateReferencePlaneRequest,
    ) -> OperationResult:
        """Create an offset part reference plane and return its opaque reference ID."""
        return await workflow.create_reference_plane(input_data)

    @mcp.tool(
        name="solidworks_get_reference_axis",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_reference_axis(
        input_data: GetReferenceAxisRequest,
    ) -> OperationResult:
        """Return an opaque capability for the X, Y, or Z origin axis of a part."""
        return await workflow.get_reference_axis(input_data)

    @mcp.tool(
        name="solidworks_get_reference_plane",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_reference_plane(
        input_data: GetReferencePlaneRequest,
    ) -> OperationResult:
        """Return an opaque capability for the Front, Top, or Right origin plane."""
        return await workflow.get_reference_plane(input_data)

    @mcp.tool(
        name="solidworks_add_rectangle",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_rectangle(
        input_data: AddRectangleRequest,
    ) -> OperationResult:
        """Add one non-degenerate rectangular profile to the active session sketch."""
        return await workflow.add_rectangle(input_data)

    @mcp.tool(
        name="solidworks_add_line",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_line(input_data: AddLineRequest) -> OperationResult:
        """Add one line segment to the active session sketch in millimetres."""
        return await workflow.add_line(input_data)

    @mcp.tool(
        name="solidworks_add_circle",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_circle(input_data: AddCircleRequest) -> OperationResult:
        """Add one circle to the active session sketch in millimetres."""
        return await workflow.add_circle(input_data)

    @mcp.tool(
        name="solidworks_add_point",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_point(input_data: AddPointRequest) -> OperationResult:
        """Add one placement point to the active session sketch in millimetres."""
        return await workflow.add_point(input_data)

    @mcp.tool(
        name="solidworks_add_arc",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_arc(input_data: AddArcRequest) -> OperationResult:
        """Add one circular arc to the active session sketch in millimetres."""
        return await workflow.add_arc(input_data)

    @mcp.tool(
        name="solidworks_add_centerline",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_centerline(
        input_data: AddCenterlineRequest,
    ) -> OperationResult:
        """Add one construction centreline to the active session sketch."""
        return await workflow.add_centerline(input_data)

    @mcp.tool(
        name="solidworks_add_spline",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_spline(input_data: AddSplineRequest) -> OperationResult:
        """Add one NURBS spline through ordered points in the active session sketch."""
        return await workflow.add_spline(input_data)

    @mcp.tool(
        name="solidworks_add_sketch_text",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_sketch_text(
        input_data: AddSketchTextRequest,
    ) -> OperationResult:
        """Add text to the active session sketch and return its opaque entity ID."""
        return await workflow.add_sketch_text(input_data)

    @mcp.tool(
        name="solidworks_add_polygon",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_polygon(input_data: AddPolygonRequest) -> OperationResult:
        """Add one regular polygon to the active session sketch in millimetres."""
        return await workflow.add_polygon(input_data)

    @mcp.tool(
        name="solidworks_add_ellipse",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_ellipse(input_data: AddEllipseRequest) -> OperationResult:
        """Add one axis-aligned ellipse to the active session sketch in millimetres."""
        return await workflow.add_ellipse(input_data)

    @mcp.tool(
        name="solidworks_create_sketch_linear_pattern",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_sketch_linear_pattern(
        input_data: CreateSketchLinearPatternRequest,
    ) -> OperationResult:
        """Create one linear pattern from server-issued entities in the active sketch."""
        return await workflow.create_sketch_linear_pattern(input_data)

    @mcp.tool(
        name="solidworks_create_sketch_circular_pattern",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_sketch_circular_pattern(
        input_data: CreateSketchCircularPatternRequest,
    ) -> OperationResult:
        """Create one circular pattern about the active sketch origin."""
        return await workflow.create_sketch_circular_pattern(input_data)

    @mcp.tool(
        name="solidworks_mirror_sketch_entities",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_mirror_sketch_entities(
        input_data: MirrorSketchEntitiesRequest,
    ) -> OperationResult:
        """Mirror server-issued entities about a server-issued centreline."""
        return await workflow.mirror_sketch_entities(input_data)

    @mcp.tool(
        name="solidworks_offset_sketch_entities",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_offset_sketch_entities(
        input_data: OffsetSketchEntitiesRequest,
    ) -> OperationResult:
        """Offset server-issued entities in the active session sketch."""
        return await workflow.offset_sketch_entities(input_data)

    @mcp.tool(
        name="solidworks_trim_sketch_entity",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_trim_sketch_entity(
        input_data: TrimSketchEntityRequest,
    ) -> OperationResult:
        """Trim one server-issued active-sketch entity at a point in millimetres."""
        return await workflow.trim_sketch_entity(input_data)

    @mcp.tool(
        name="solidworks_split_sketch_entity",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_split_sketch_entity(
        input_data: SplitSketchEntityRequest,
    ) -> OperationResult:
        """Split one active sketch segment at one point (or two for closed curves)."""
        try:
            input_data = _parse_request(input_data, SplitSketchEntityRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid sketch split request: {str(exc)[:430]}",
                suggestion="Provide the current session_id, document_id, sketch_id, entity_id, x, and y.",
            )
        return await workflow.split_sketch_entity(input_data)

    @mcp.tool(
        name="solidworks_fillet_sketch_corner",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_fillet_sketch_corner(
        input_data: FilletSketchCornerRequest,
    ) -> OperationResult:
        """Round the corner formed by two server-issued sketch entities."""
        try:
            input_data = _parse_request(input_data, FilletSketchCornerRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid sketch fillet request: {str(exc)[:430]}",
                suggestion="Provide the current session_id, document_id, sketch_id, two entity_ids, and radius.",
            )
        return await workflow.fillet_sketch_corner(input_data)

    @mcp.tool(
        name="solidworks_chamfer_sketch_corner",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_chamfer_sketch_corner(
        input_data: ChamferSketchCornerRequest,
    ) -> OperationResult:
        """Bevel the corner formed by two server-issued sketch entities."""
        try:
            input_data = _parse_request(input_data, ChamferSketchCornerRequest)
        except Exception as exc:
            return OperationResult.failed(
                code="SW_INVALID_INPUT",
                message=f"Invalid sketch chamfer request: {str(exc)[:430]}",
                suggestion="Provide the current session_id, document_id, sketch_id, two entity_ids, and distance.",
            )
        return await workflow.chamfer_sketch_corner(input_data)

    @mcp.tool(
        name="solidworks_exit_sketch",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_exit_sketch(input_data: ExitSketchRequest) -> OperationResult:
        """Exit the active sketch so its profile can be used by the next feature tool."""
        return await workflow.exit_sketch(input_data)

    @mcp.tool(
        name="solidworks_create_extrusion",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_extrusion(
        input_data: CreateExtrusionRequest,
    ) -> OperationResult:
        """Extrude the last exited sketch in the supplied session and document."""
        return await workflow.create_extrusion(input_data)

    @mcp.tool(
        name="solidworks_create_cut_extrude",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_cut_extrude(
        input_data: CreateCutExtrudeRequest,
    ) -> OperationResult:
        """Cut material using the last exited sketch in the supplied session."""
        return await workflow.create_cut_extrude(input_data)

    @mcp.tool(
        name="solidworks_create_revolve",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_revolve(
        input_data: CreateRevolveRequest,
    ) -> OperationResult:
        """Add or cut material by revolving an exited profile about its centreline."""
        return await workflow.create_revolve(input_data)

    @mcp.tool(
        name="solidworks_create_sweep",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_sweep(
        input_data: CreateSweepRequest,
    ) -> OperationResult:
        """Sweep one exited session profile along a distinct exited session path."""
        return await workflow.create_sweep(input_data)

    @mcp.tool(
        name="solidworks_create_loft",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_loft(
        input_data: CreateLoftRequest,
    ) -> OperationResult:
        """Loft through two or more exited session profile sketches in order."""
        return await workflow.create_loft(input_data)

    @mcp.tool(
        name="solidworks_create_feature_linear_pattern",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_feature_linear_pattern(
        input_data: CreateFeatureLinearPatternRequest,
    ) -> OperationResult:
        """Pattern one current typed feature along a standard coordinate direction."""
        return await workflow.create_feature_linear_pattern(input_data)

    @mcp.tool(
        name="solidworks_create_feature_circular_pattern",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_feature_circular_pattern(
        input_data: CreateFeatureCircularPatternRequest,
    ) -> OperationResult:
        """Pattern one current typed feature about a current typed origin axis."""
        return await workflow.create_feature_circular_pattern(input_data)

    @mcp.tool(
        name="solidworks_mirror_feature",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_mirror_feature(
        input_data: MirrorFeatureRequest,
    ) -> OperationResult:
        """Mirror one current typed feature about a current typed reference plane."""
        return await workflow.mirror_feature(input_data)

    @mcp.tool(
        name="solidworks_create_hole_wizard",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_hole_wizard(
        input_data: CreateHoleWizardRequest,
    ) -> OperationResult:
        """Create an ANSI Metric clearance Hole Wizard from an exited point-location sketch."""
        return await workflow.create_hole_wizard(input_data)

    @mcp.tool(
        name="solidworks_create_tapped_hole",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_tapped_hole(
        input_data: CreateTappedHoleRequest,
    ) -> OperationResult:
        """Create a standard ANSI Metric tapped hole from an exited point-location sketch."""
        return await workflow.create_tapped_hole(input_data)

    @mcp.tool(
        name="solidworks_export_file",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_export_file(input_data: ExportFileRequest) -> OperationResult:
        """Export STEP or STL under the configured artifact root and return its hash."""
        return await workflow.export_file(input_data)

    @mcp.tool(
        name="solidworks_get_sheet_metal_info",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_sheet_metal_info(
        input_data: DocumentRequest,
    ) -> OperationResult:
        """Read active-part sheet-metal status, bend state, and available thickness."""
        return await workflow.get_sheet_metal_info(input_data)

    @mcp.tool(
        name="solidworks_set_sheet_metal_flat_state",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_set_sheet_metal_flat_state(
        input_data: SheetMetalFlatStateRequest,
    ) -> OperationResult:
        """Explicitly flatten or refold the active sheet-metal part."""
        return await workflow.set_sheet_metal_flat_state(input_data)

    @mcp.tool(
        name="solidworks_export_flat_pattern_dxf",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_export_flat_pattern_dxf(
        input_data: ExportFlatPatternDxfRequest,
    ) -> OperationResult:
        """Export the active saved sheet-metal part as a controlled flat-pattern DXF."""
        return await workflow.export_flat_pattern_dxf(input_data)

    @mcp.tool(
        name="solidworks_save_model",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_save_model(input_data: SaveModelRequest) -> OperationResult:
        """Save a native part, assembly, or drawing copy under the artifact root."""
        return await workflow.save_model(input_data)

    @mcp.tool(
        name="solidworks_get_model_info",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_model_info(input_data: DocumentRequest) -> OperationResult:
        """Read concise metadata for the active document in a workflow session."""
        return await workflow.get_model_info(input_data)

    @mcp.tool(
        name="solidworks_get_mass_properties",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_mass_properties(
        input_data: DocumentRequest,
    ) -> OperationResult:
        """Read mass properties for the active document in a workflow session."""
        return await workflow.get_mass_properties(input_data)

    @mcp.tool(
        name="solidworks_get_bounding_box",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_bounding_box(
        input_data: GetBoundingBoxRequest,
    ) -> OperationResult:
        """Read axis-aligned active-part extents in millimetres."""
        return await workflow.get_bounding_box(input_data)

    @mcp.tool(
        name="solidworks_list_dimensions",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_dimensions(
        input_data: ListDimensionsRequest,
    ) -> OperationResult:
        """List current model dimensions as opaque session-scoped identifiers."""
        input_data = _parse_request(input_data, ListDimensionsRequest)
        return await workflow.list_dimensions(input_data)

    @mcp.tool(
        name="solidworks_get_dimension",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_dimension(
        input_data: GetDimensionRequest,
    ) -> OperationResult:
        """Read one current model dimension through an opaque dimension ID."""
        input_data = _parse_request(input_data, GetDimensionRequest)
        return await workflow.get_dimension(input_data)

    @mcp.tool(
        name="solidworks_set_dimension",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_set_dimension(
        input_data: SetDimensionRequest,
    ) -> OperationResult:
        """Change and verify one current model dimension through an opaque ID."""
        input_data = _parse_request(input_data, SetDimensionRequest)
        return await workflow.set_dimension(input_data)

    @mcp.tool(
        name="solidworks_get_feature_dimension",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_feature_dimension(
        input_data: GetFeatureDimensionRequest,
    ) -> OperationResult:
        """Read a current feature dimension through an opaque feature ID and index."""
        input_data = _parse_request(input_data, GetFeatureDimensionRequest)
        return await workflow.get_feature_dimension(input_data)

    @mcp.tool(
        name="solidworks_set_feature_dimension",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_set_feature_dimension(
        input_data: SetFeatureDimensionRequest,
    ) -> OperationResult:
        """Change and verify a feature dimension through opaque workflow references."""
        input_data = _parse_request(input_data, SetFeatureDimensionRequest)
        return await workflow.set_feature_dimension(input_data)

    @mcp.tool(
        name="solidworks_list_equations",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_equations(
        input_data: ListEquationsRequest,
    ) -> OperationResult:
        """List a bounded page of active-part equations and global variables."""
        return await workflow.list_equations(input_data)

    @mcp.tool(
        name="solidworks_get_equation",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_equation(
        input_data: GetEquationRequest,
    ) -> OperationResult:
        """Read one active-part equation or global variable by name."""
        return await workflow.get_equation(input_data)

    @mcp.tool(
        name="solidworks_set_equation",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_set_equation(
        input_data: SetEquationRequest,
    ) -> OperationResult:
        """Create or update one equation or global variable on all configurations."""
        return await workflow.set_equation(input_data)

    @mcp.tool(
        name="solidworks_delete_equation",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_delete_equation(
        input_data: DeleteEquationRequest,
    ) -> OperationResult:
        """Delete one active-part equation or global variable by name."""
        return await workflow.delete_equation(input_data)

    @mcp.tool(
        name="solidworks_list_solid_bodies",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_solid_bodies(
        input_data: ListSolidBodiesRequest,
    ) -> OperationResult:
        """List a bounded page of solid bodies in an active part document."""
        return await workflow.list_solid_bodies(input_data)

    @mcp.tool(
        name="solidworks_list_surface_bodies",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_surface_bodies(
        input_data: ListSurfaceBodiesRequest,
    ) -> OperationResult:
        """List a bounded page of surface bodies in an active part document."""
        return await workflow.list_surface_bodies(input_data)

    @mcp.tool(
        name="solidworks_get_material_assignment",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_material_assignment(
        input_data: GetMaterialAssignmentRequest,
    ) -> OperationResult:
        """Read the active part configuration's material database and material name."""
        return await workflow.get_material_assignment(input_data)

    @mcp.tool(
        name="solidworks_set_material_assignment",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_set_material_assignment(
        input_data: SetMaterialAssignmentRequest,
    ) -> OperationResult:
        """Assign and verify one material in the active part configuration."""
        return await workflow.set_material_assignment(input_data)

    @mcp.tool(
        name="solidworks_list_features",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_features(
        input_data: ListFeaturesRequest,
    ) -> OperationResult:
        """List a bounded, safe feature tree for the active session document."""
        return await workflow.list_features(input_data)

    @mcp.tool(
        name="solidworks_list_configurations",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_configurations(
        input_data: ListConfigurationsRequest,
    ) -> OperationResult:
        """List bounded configuration metadata for the active session document."""
        return await workflow.list_configurations(input_data)

    @mcp.tool(
        name="solidworks_get_configuration",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_get_configuration(
        input_data: GetConfigurationRequest,
    ) -> OperationResult:
        """Read one exact configuration's description and active state."""
        return await workflow.get_configuration(input_data)

    @mcp.tool(
        name="solidworks_create_configuration",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_configuration(
        input_data: CreateConfigurationRequest,
    ) -> OperationResult:
        """Create, activate, and verify one configuration on the session document."""
        return await workflow.create_configuration(input_data)

    @mcp.tool(
        name="solidworks_delete_configuration",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_delete_configuration(
        input_data: DeleteConfigurationRequest,
    ) -> OperationResult:
        """Delete one non-active configuration from the session document."""
        return await workflow.delete_configuration(input_data)

    @mcp.tool(
        name="solidworks_activate_configuration",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_activate_configuration(
        input_data: ActivateConfigurationRequest,
    ) -> OperationResult:
        """Activate one existing configuration on the active session document."""
        return await workflow.activate_configuration(input_data)

    @mcp.tool(
        name="solidworks_list_trusted_macros",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_trusted_macros() -> OperationResult:
        """List administrator-provisioned VBA projects without exposing local file paths."""
        return await workflow.list_trusted_macros()

    @mcp.tool(
        name="solidworks_prepare_trusted_macro",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_prepare_trusted_macro(
        input_data: PrepareTrustedMacroRequest,
    ) -> OperationResult:
        """Prepare one trusted VBA macro and return a one-time hash confirmation."""
        return await workflow.prepare_trusted_macro(input_data)

    @mcp.tool(
        name="solidworks_execute_trusted_macro",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def solidworks_execute_trusted_macro(
        input_data: ExecuteTrustedMacroRequest,
    ) -> OperationResult:
        """Execute one prepared, hash-confirmed VBA project through SolidWorks RunMacro2."""
        return await workflow.execute_trusted_macro(input_data)

    @mcp.tool(
        name="solidworks_list_part_topology",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_part_topology(
        input_data: ListPartTopologyRequest,
    ) -> OperationResult:
        """List opaque current part faces and edges for safe feature operations."""
        return await workflow.list_part_topology(input_data)

    @mcp.tool(
        name="solidworks_list_sheet_metal_feature_geometry",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_sheet_metal_feature_geometry(
        input_data: ListSheetMetalFeatureGeometryRequest,
    ) -> OperationResult:
        """List current flat-pattern and bend faces as safe opaque part-face IDs."""
        input_data = _parse_request(input_data, ListSheetMetalFeatureGeometryRequest)
        return await workflow.list_sheet_metal_feature_geometry(input_data)

    @mcp.tool(
        name="solidworks_insert_sheet_metal_bend_notes",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_insert_sheet_metal_bend_notes(
        input_data: InsertSheetMetalBendNotesRequest,
    ) -> OperationResult:
        """Insert bend notes for the active part's server-resolved flat pattern."""
        input_data = _parse_request(input_data, InsertSheetMetalBendNotesRequest)
        return await workflow.insert_sheet_metal_bend_notes(input_data)

    @mcp.tool(
        name="solidworks_insert_sheet_metal_bend_table",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_insert_sheet_metal_bend_table(
        input_data: InsertSheetMetalBendTableRequest,
    ) -> OperationResult:
        """Insert a default-template bend table at a bounded part-model position."""
        input_data = _parse_request(input_data, InsertSheetMetalBendTableRequest)
        return await workflow.insert_sheet_metal_bend_table(input_data)

    @mcp.tool(
        name="solidworks_create_3d_sketch",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_3d_sketch(
        input_data: Create3DSketchRequest,
    ) -> OperationResult:
        """Open one three-dimensional sketch in the active part."""
        return await workflow.create_3d_sketch(input_data)

    @mcp.tool(
        name="solidworks_add_3d_line",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_add_3d_line(input_data: Add3DLineRequest) -> OperationResult:
        """Add one line to the active session-owned 3D sketch in millimetres."""
        return await workflow.add_3d_line(input_data)

    @mcp.tool(
        name="solidworks_convert_sketch_entities",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_convert_sketch_entities(
        input_data: ConvertSketchEntitiesRequest,
    ) -> OperationResult:
        """Convert listed part edges into active-sketch geometry."""
        return await workflow.convert_sketch_entities(input_data)

    @mcp.tool(
        name="solidworks_create_sheet_metal_base_flange",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_sheet_metal_base_flange(
        input_data: CreateSheetMetalBaseFlangeRequest,
    ) -> OperationResult:
        """Create a sheet-metal base flange from an exited session sketch."""
        return await workflow.create_sheet_metal_base_flange(input_data)

    @mcp.tool(
        name="solidworks_create_sheet_metal_edge_flange",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_sheet_metal_edge_flange(
        input_data: CreateSheetMetalEdgeFlangeRequest,
    ) -> OperationResult:
        """Create one sheet-metal edge flange from a listed part edge."""
        return await workflow.create_sheet_metal_edge_flange(input_data)

    @mcp.tool(
        name="solidworks_set_sheet_metal_k_factor",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_set_sheet_metal_k_factor(
        input_data: SetSheetMetalKFactorRequest,
    ) -> OperationResult:
        """Set and verify the active sheet-metal K factor."""
        return await workflow.set_sheet_metal_k_factor(input_data)

    @mcp.tool(
        name="solidworks_create_shell",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_shell(
        input_data: CreateShellRequest,
    ) -> OperationResult:
        """Shell the active part after removing listed faces."""
        return await workflow.create_shell(input_data)

    @mcp.tool(
        name="solidworks_create_chamfer",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_chamfer(
        input_data: CreateChamferRequest,
    ) -> OperationResult:
        """Create a 3D chamfer from current opaque part edge IDs.

        Required call sequence:

        1. Call ``solidworks_list_part_topology`` with
           ``entity_types=['edge']``.
        2. Choose one or more returned ``entity_id`` values using their
           displayed length/curve metadata.
        3. Pass those exact IDs in ``edge_ids``. Never invent ``Edge<1>``
           names and never reuse IDs after another part mutation.

        ``equal_distance`` applies ``distance`` to both adjacent faces.
        ``distance_angle`` applies ``distance`` to one face and
        ``angle_degrees`` to the other. Lengths are converted from ``unit``
        to millimetres before the SolidWorks COM call. The response includes a
        conservative preflight warning when the requested distance is at least
        a listed edge length; SolidWorks remains the authority for final BREP
        validity.
        """
        input_data = _parse_request(input_data, CreateChamferRequest)
        return await workflow.create_chamfer(input_data)

    @mcp.tool(
        name="solidworks_create_rib",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_rib(input_data: CreateRibRequest) -> OperationResult:
        """Create a rib from an exited session sketch."""
        return await workflow.create_rib(input_data)

    @mcp.tool(
        name="solidworks_create_dome",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_dome(input_data: CreateDomeRequest) -> OperationResult:
        """Create a dome on one listed part face."""
        return await workflow.create_dome(input_data)

    @mcp.tool(
        name="solidworks_create_draft",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_draft(
        input_data: CreateDraftRequest,
    ) -> OperationResult:
        """Create a neutral-plane draft from current server-issued part faces."""
        return await workflow.create_draft(input_data)

    @mcp.tool(
        name="solidworks_create_thicken",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_thicken(
        input_data: CreateThickenRequest,
    ) -> OperationResult:
        """Thicken one current surface face into a real part feature."""
        return await workflow.create_thicken(input_data)

    @mcp.tool(
        name="solidworks_create_wrap",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_wrap(input_data: CreateWrapRequest) -> OperationResult:
        """Wrap one exited session sketch onto a current server-issued part face."""
        return await workflow.create_wrap(input_data)

    @mcp.tool(
        name="solidworks_list_assembly_mates",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def solidworks_list_assembly_mates(
        input_data: ListAssemblyMatesRequest,
    ) -> OperationResult:
        """List current assembly mates and return opaque mate IDs."""
        return await workflow.list_assembly_mates(input_data)

    @mcp.tool(
        name="solidworks_delete_assembly_mate",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_delete_assembly_mate(
        input_data: DeleteAssemblyMateRequest,
    ) -> OperationResult:
        """Delete one current server-issued assembly mate."""
        return await workflow.delete_assembly_mate(input_data)

    @mcp.tool(
        name="solidworks_create_section_drawing_view",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_create_section_drawing_view(
        input_data: CreateSectionDrawingViewRequest,
    ) -> OperationResult:
        """Create a drawing section view from a current drawing view."""
        return await workflow.create_section_drawing_view(input_data)

    @mcp.tool(
        name="solidworks_update_drawing_bom",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_update_drawing_bom(
        input_data: UpdateDrawingBomRequest,
    ) -> OperationResult:
        """Refresh one current server-issued drawing BOM table."""
        return await workflow.update_drawing_bom(input_data)

    @mcp.tool(
        name="solidworks_delete_drawing_bom",
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def solidworks_delete_drawing_bom(
        input_data: DeleteDrawingBomRequest,
    ) -> OperationResult:
        """Delete one current server-issued drawing BOM table."""
        return await workflow.delete_drawing_bom(input_data)

    return len(await mcp.list_tools()) - before
