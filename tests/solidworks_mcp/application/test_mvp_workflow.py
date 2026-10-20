"""Contract tests for the typed SolidWorks MCP MVP workflow."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from solidworks_mcp.adapters.base import (
    ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED,
    AdapterHealth,
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    FeatureCircularPatternParameters,
    FeatureLinearPatternParameters,
    FeatureMirrorParameters,
    HoleWizardParameters,
    LoftParameters,
    MassProperties,
    OriginPlaneParameters,
    ReferenceAxisParameters,
    ReferencePlaneParameters,
    RevolveParameters,
    SolidWorksFeature,
    SolidWorksModel,
    SweepParameters,
    ThreadedHoleParameters,
)
from solidworks_mcp.adapters.connection_pool import ConnectionPoolAdapter
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.application.mvp_workflow import MvpWorkflow
from solidworks_mcp.application.sessions import MateEntityReferenceError, SessionService
from solidworks_mcp.artifacts.store import ArtifactStore, ModelInputStore
from solidworks_mcp.contracts.mvp import (
    AlignDrawingViewsRequest,
    ActivateConfigurationRequest,
    ActivateDocumentRequest,
    AddArcRequest,
    AddCenterlineRequest,
    AddCircleRequest,
    AddEllipseRequest,
    AddLineRequest,
    AddPointRequest,
    AddPolygonRequest,
    AddRectangleRequest,
    AddSplineRequest,
    CheckAssemblyInterferenceRequest,
    CloseDocumentRequest,
    CreateAssemblyMateRequest,
    CreateAssemblyRequest,
    CreateChamferRequest,
    CreateConfigurationRequest,
    CreateCutExtrudeRequest,
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
    CreateDrawingRequest,
    CreateDrawingViewRequest,
    CreateExtrusionRequest,
    CreateFeatureCircularPatternRequest,
    CreateFeatureLinearPatternRequest,
    CreateHoleWizardRequest,
    CreateTappedHoleRequest,
    CreateLoftRequest,
    CreatePartRequest,
    CreateProjectedDrawingViewRequest,
    CreateReferencePlaneRequest,
    CreateRevolveRequest,
    CreateSketchCircularPatternRequest,
    CreateSketchLinearPatternRequest,
    CreateSketchRequest,
    CreateSweepRequest,
    CreateThickenRequest,
    CreateWrapRequest,
    DeleteConfigurationRequest,
    DeleteCustomPropertyRequest,
    DeleteEquationRequest,
    DocumentRequest,
    ExitSketchRequest,
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
    ListConfigurationsRequest,
    ListCustomPropertiesRequest,
    ListDimensionsRequest,
    ListDrawingAnnotationTargetsRequest,
    ListDrawingViewsRequest,
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
    OperationStatus,
    PlaceDrawingDimensionRequest,
    RebuildDrawingRequest,
    RenameDrawingSheetRequest,
    SaveModelRequest,
    SetCustomPropertyRequest,
    SetDimensionRequest,
    SetEquationRequest,
    SetFeatureDimensionRequest,
    SetMaterialAssignmentRequest,
    SheetMetalFlatStateRequest,
    TrimSketchEntityRequest,
)


class WorkflowAdapter:
    """Small deterministic adapter used to test workflow contracts."""

    def __init__(self) -> None:
        self.extrusion_params: ExtrusionParameters | None = None
        self.revolve_params: RevolveParameters | None = None
        self.sweep_params: SweepParameters | None = None
        self.loft_params: LoftParameters | None = None
        self.reference_plane_params: list[ReferencePlaneParameters] = []
        self.linear_pattern_params: FeatureLinearPatternParameters | None = None
        self.circular_pattern_params: FeatureCircularPatternParameters | None = None
        self.mirror_feature_params: FeatureMirrorParameters | None = None
        self.hole_wizard_params: HoleWizardParameters | None = None
        self.tapped_hole_params: ThreadedHoleParameters | None = None
        self.trim_calls: list[tuple[str, float, float]] = []
        self.sketch_number = 0
        self.export_target: Path | None = None
        self.opened_path: Path | None = None
        self.save_target: Path | None = None
        self.active_model_name: str | None = None
        self.activation_names: list[str] = []
        self.sketch_model_name: str | None = None
        self.sketch_plane_name: str | None = None
        self.features_model_name: str | None = None
        self.configurations_model_name: str | None = None
        self.bodies_model_name: str | None = None
        self.material_configuration_names: list[str] = []
        self.material_assignments: dict[str, dict[str, str]] = {
            "Default": {
                "database_name": r"C:\\materials\\solidworks materials.sldmat",
                "material_name": "Plain Carbon Steel",
            }
        }
        self.configuration_activations: list[str] = []
        self.configuration_creations: list[tuple[str, str]] = []
        self.assembly_component_calls: list[tuple[str, float, float, float]] = []
        self.assembly_components: list[dict[str, str]] = []
        self.assembly_mate_calls: list[dict[str, object]] = []
        self.drawing_view_calls: list[tuple[str, str, float, float]] = []
        self.drawing_views: list[dict[str, str]] = []
        self.projected_drawing_view_calls: list[tuple[str, float, float]] = []
        self.alignment_calls: list[tuple[list[str], str, float, float | None, float | None]] = []
        self.custom_properties: dict[str, dict[str, str]] = {}
        self.equations: list[dict[str, object]] = [
            {
                "index": 0,
                "name": "PlateWidth",
                "expression": "100mm",
                "is_global_variable": True,
            },
            {
                "index": 1,
                "name": "D1@Sketch1",
                "expression": '"PlateWidth" / 2',
                "is_global_variable": False,
            },
        ]
        self.active_configuration = "Default"
        self.configurations = ["Default", "Fabrication"]
        self.closed_models: list[bool] = []
        self.dimension_reads: list[str] = []
        self.dimension_writes: list[tuple[str, float]] = []
        self.dimensions: dict[str, float] = {"D1@Boss-Extrude1": 10.0}

    def is_connected(self) -> bool:
        return True

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            healthy=True,
            last_check="2026-07-22T00:00:00Z",
            error_count=0,
            success_count=1,
            average_response_time=0.01,
            connection_status="connected",
        )
    async def create_part(
        self, name: str, units: str
    ) -> AdapterResult[SolidWorksModel]:
        self.active_model_name = name
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksModel(
                path="",
                name=name,
                type="Part",
                is_active=True,
                configuration="Default",
                properties={"units": units},
            ),
        )

    async def create_assembly(self, name: str) -> AdapterResult[SolidWorksModel]:
        self.active_model_name = name
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksModel(
                path="",
                name=name,
                type="Assembly",
                is_active=True,
                configuration="Default",
            ),
        )

    async def create_drawing(self, name: str) -> AdapterResult[SolidWorksModel]:
        self.active_model_name = name
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksModel(
                path="",
                name=name,
                type="Drawing",
                is_active=True,
                configuration="Default",
            ),
        )

    async def close_model(self, save: bool = False) -> AdapterResult[None]:
        self.closed_models.append(save)
        self.active_model_name = None
        return AdapterResult(status=AdapterResultStatus.SUCCESS)

    async def get_dimension(self, name: str) -> AdapterResult[float]:
        self.dimension_reads.append(name)
        if name not in self.dimensions:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Dimension does not exist"
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS, data=self.dimensions[name]
        )

    async def set_dimension(self, name: str, value: float) -> AdapterResult[None]:
        self.dimension_writes.append((name, value))
        if name not in self.dimensions:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Dimension does not exist"
            )
        self.dimensions[name] = value
        return AdapterResult(status=AdapterResultStatus.SUCCESS)

    async def list_dimensions(self) -> AdapterResult[list[dict[str, object]]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {"name": name, "value_mm": value}
                for name, value in self.dimensions.items()
            ],
        )

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        self.opened_path = Path(file_path)
        self.active_model_name = Path(file_path).name
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksModel(
                path=file_path,
                name=Path(file_path).name,
                type="Part",
                is_active=True,
            ),
        )

    async def save_file(self, file_path: str | None = None) -> AdapterResult[None]:
        assert file_path is not None
        self.save_target = Path(file_path)
        self.save_target.parent.mkdir(parents=True, exist_ok=True)
        self.save_target.write_bytes(b"native SolidWorks model")
        return AdapterResult(status=AdapterResultStatus.SUCCESS)

    async def activate_model(
        self, name: str, path: str | None = None
    ) -> AdapterResult[None]:
        _ = path
        self.activation_names.append(name)
        self.active_model_name = name
        return AdapterResult(status=AdapterResultStatus.SUCCESS)

    async def create_sketch(self, plane: str) -> AdapterResult[str]:
        self.sketch_model_name = self.active_model_name
        self.sketch_plane_name = plane
        self.sketch_number += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=f"Sketch{self.sketch_number}",
        )

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Rectangle1")

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Line1")

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Circle1")

    async def add_point(self, x: float, y: float) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Point1")

    async def add_arc(
        self,
        center_x: float,
        center_y: float,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Arc1")

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Centerline1")

    async def add_spline(self, points: list[dict[str, float]]) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Spline1")

    async def add_polygon(
        self, center_x: float, center_y: float, radius: float, sides: int
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Polygon1")

    async def add_ellipse(
        self, center_x: float, center_y: float, major_axis: float, minor_axis: float
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Ellipse1")

    async def sketch_linear_pattern(
        self,
        entities: list[str],
        direction_x: float,
        direction_y: float,
        spacing: float,
        count: int,
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="LinearPattern1")

    async def sketch_circular_pattern(
        self, entities: list[str], angle: float, count: int
    ) -> AdapterResult[str]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS, data="CircularPattern1"
        )

    async def sketch_mirror(
        self, entities: list[str], mirror_line: str
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Mirror1")

    async def sketch_offset(
        self, entities: list[str], offset_distance: float, reverse_direction: bool
    ) -> AdapterResult[str]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data="Offset1")

    async def sketch_trim(
        self, entity_id: str, trim_x: float, trim_y: float
    ) -> AdapterResult[str]:
        self.trim_calls.append((entity_id, trim_x, trim_y))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=entity_id)

    async def exit_sketch(self) -> AdapterResult[None]:
        return AdapterResult(status=AdapterResultStatus.SUCCESS)

    async def create_extrusion(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.extrusion_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id="Feature1", name="Boss-Extrude1", type="Extrusion"
            ),
        )

    async def create_cut_extrude(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.extrusion_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id="Feature2", name="Cut-Extrude1", type="Cut-Extrude"
            ),
        )

    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.revolve_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(id="Feature3", name="Boss-Revolve1", type="Revolve"),
        )

    async def create_reference_plane(
        self, params: ReferencePlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.reference_plane_params.append(params)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id=f"Plane{len(self.reference_plane_params)}",
                name=f"Plane{len(self.reference_plane_params)}",
                type="ReferencePlane",
            ),
        )

    async def get_reference_axis(
        self, params: ReferenceAxisParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id=f"OriginAxis-{params.axis}",
                name=f"{params.axis} Axis",
                type="OriginAxis",
            ),
        )

    async def get_reference_plane(
        self, params: OriginPlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id=f"OriginPlane-{params.plane}",
                name=f"{params.plane} Plane",
                type="OriginPlane",
            ),
        )

    async def create_feature_linear_pattern(
        self, params: FeatureLinearPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.linear_pattern_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id="FeatureLinearPattern", name="LinearPattern1", type="LinearPattern"
            ),
        )

    async def create_feature_circular_pattern(
        self, params: FeatureCircularPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.circular_pattern_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id="FeatureCircularPattern",
                name="CircularPattern1",
                type="CircularPattern",
            ),
        )

    async def mirror_feature(
        self, params: FeatureMirrorParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.mirror_feature_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(id="FeatureMirror", name="Mirror1", type="Mirror"),
        )

    async def create_hole_wizard(
        self, params: HoleWizardParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.hole_wizard_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id="FeatureHoleWizard", name="HoleWizard1", type="HoleWizard"
            ),
        )

    async def create_tapped_hole(
        self, params: ThreadedHoleParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.tapped_hole_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(
                id="FeatureTappedHole", name="TappedHole1", type="TappedHole"
            ),
        )

    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.sweep_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(id="Feature4", name="Sweep1", type="Sweep"),
        )

    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        self.loft_params = params
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=SolidWorksFeature(id="Feature5", name="Loft1", type="Loft"),
        )

    async def export_file(
        self, file_path: str, format_type: str
    ) -> AdapterResult[None]:
        self.export_target = Path(file_path)
        self.export_target.parent.mkdir(parents=True, exist_ok=True)
        self.export_target.write_text(f"{format_type} export", encoding="utf-8")
        return AdapterResult(status=AdapterResultStatus.SUCCESS)

    async def get_model_info(self) -> AdapterResult[dict[str, str]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": "Bracket", "type": "Part"},
        )

    async def list_features(
        self, include_suppressed: bool = False
    ) -> AdapterResult[list[dict[str, object]]]:
        self.features_model_name = self.active_model_name
        features = [
            {"name": "Origin", "type": "OriginProfileFeature", "suppressed": False},
            {"name": "Boss-Extrude1", "type": "BossExtrude", "suppressed": False},
            {"name": "HiddenCut", "type": "Cut", "suppressed": True},
        ]
        if not include_suppressed:
            features = [feature for feature in features if not feature["suppressed"]]
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=features)

    async def list_configurations(self) -> AdapterResult[list[str]]:
        self.configurations_model_name = self.active_model_name
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=list(self.configurations),
        )

    async def get_configuration(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, object]]:
        if configuration_name not in self.configurations:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="missing")
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": configuration_name,
                "description": "Welded construction",
                "is_active": configuration_name == self.active_configuration,
            },
        )

    async def create_configuration(
        self, name: str, description: str = ""
    ) -> AdapterResult[str]:
        if any(
            configuration.casefold() == name.casefold()
            for configuration in self.configurations
        ):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Configuration already exists",
            )
        self.configurations.append(name)
        self.active_configuration = name
        self.configuration_creations.append((name, description))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=name)

    async def delete_configuration(self, name: str) -> AdapterResult[str]:
        if name not in self.configurations:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Configuration does not exist",
            )
        if name == self.active_configuration:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Active configuration cannot be deleted",
            )
        self.configurations.remove(name)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=name)

    async def list_solid_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, object]]]:
        self.bodies_model_name = self.active_model_name
        bodies = [
            {"body_id": "body_1", "name": "Base", "visible": True},
            {"body_id": "body_2", "name": "Lug", "visible": True},
        ]
        if include_hidden:
            bodies.append({"body_id": "body_3", "name": "Hidden", "visible": False})
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=bodies)

    async def list_surface_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, object]]]:
        _ = include_hidden
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=[])

    async def get_material_assignment(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, str]]:
        self.material_configuration_names.append(configuration_name)
        assignment = self.material_assignments.get(
            configuration_name,
            {"database_name": "", "material_name": ""},
        )
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(assignment))

    async def set_material_assignment(
        self, configuration_name: str, database_name: str, material_name: str
    ) -> AdapterResult[dict[str, str]]:
        self.material_configuration_names.append(configuration_name)
        assignment = {
            "database_name": database_name,
            "material_name": material_name,
        }
        self.material_assignments[configuration_name] = assignment
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(assignment))

    async def activate_configuration(self, name: str) -> AdapterResult[str]:
        if name not in self.configurations:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Configuration does not exist",
            )
        self.active_configuration = name
        self.configuration_activations.append(name)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=name)

    async def insert_assembly_component(
        self, component_path: str, x_m: float, y_m: float, z_m: float
    ) -> AdapterResult[dict[str, str]]:
        self.assembly_component_calls.append((component_path, x_m, y_m, z_m))
        component = {
            "component_id": f"component_{len(self.assembly_components) + 1}",
            "display_name": f"FixturePart-{len(self.assembly_components) + 1}",
            "document_type": "part",
        }
        self.assembly_components.append(component)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=component)

    async def list_assembly_components(self) -> AdapterResult[list[dict[str, str]]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=list(self.assembly_components),
        )

    async def get_assembly_component(
        self, component_name: str
    ) -> AdapterResult[dict[str, str]]:
        component = next(
            (
                item
                for item in self.assembly_components
                if item["display_name"] == component_name
            ),
            None,
        )
        if component is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Assembly component '{component_name}' was not found",
            )
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(component))

    async def list_assembly_mate_entities(
        self,
        component_id: str | None = None,
        entity_types: tuple[str, ...] = ("face", "edge"),
    ) -> AdapterResult[list[dict[str, str]]]:
        entities = [
            {
                "component_id": "component_1",
                "entity_type": "face",
                "display_name": "FixturePart-1 Face 1",
                "persistent_reference": "AQ==",
            },
            {
                "component_id": "component_2",
                "entity_type": "face",
                "display_name": "FixturePart-2 Face 1",
                "persistent_reference": "Ag==",
            },
            {
                "component_id": "component_1",
                "entity_type": "edge",
                "display_name": "FixturePart-1 Edge 1",
                "persistent_reference": "Aw==",
            },
        ]
        if component_id is not None:
            entities = [
                entity for entity in entities if entity["component_id"] == component_id
            ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                entity for entity in entities if entity["entity_type"] in entity_types
            ],
        )

    async def create_assembly_mate(
        self,
        first_persistent_reference: str,
        second_persistent_reference: str,
        *,
        mate_type: str,
        alignment: str,
        flip: bool,
        distance_m: float,
        angle_radians: float,
        for_positioning_only: bool,
        lock_rotation: bool,
    ) -> AdapterResult[dict[str, str]]:
        self.assembly_mate_calls.append(
            {
                "first_persistent_reference": first_persistent_reference,
                "second_persistent_reference": second_persistent_reference,
                "mate_type": mate_type,
                "alignment": alignment,
                "flip": flip,
                "distance_m": distance_m,
                "angle_radians": angle_radians,
                "for_positioning_only": for_positioning_only,
                "lock_rotation": lock_rotation,
            }
        )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"mate_type": mate_type, "display_name": "Mate1"},
        )

    async def create_drawing_view(
        self,
        model_path: str,
        model_view_name: str,
        x_m: float,
        y_m: float,
    ) -> AdapterResult[dict[str, str]]:
        self.drawing_view_calls.append((model_path, model_view_name, x_m, y_m))
        view = {
            "view_id": f"Drawing View{len(self.drawing_views) + 1}",
            "display_name": f"{model_view_name.lstrip('*')} View",
        }
        self.drawing_views.append(view)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=view)

    async def list_drawing_views(self) -> AdapterResult[list[dict[str, str]]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=list(self.drawing_views),
        )

    async def get_drawing_view_outline(
        self, view_id: str
    ) -> AdapterResult[dict[str, float | str]]:
        if not any(view["view_id"] == view_id for view in self.drawing_views):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "view_id": view_id,
                "left_m": 0.02,
                "bottom_m": 0.03,
                "right_m": 0.08,
                "top_m": 0.09,
            },
        )

    async def align_drawing_views(
        self,
        view_ids: list[str],
        direction: str,
        gap_m: float,
        start_x_m: float | None,
        start_y_m: float | None,
    ) -> AdapterResult[dict[str, object]]:
        self.alignment_calls.append(
            (view_ids, direction, gap_m, start_x_m, start_y_m)
        )
        if any(view_id not in {view["view_id"] for view in self.drawing_views} for view_id in view_ids):
            return AdapterResult(status=AdapterResultStatus.ERROR, error="view missing")
        placements = []
        for index, view_id in enumerate(view_ids):
            placements.append(
                {
                    "view_id": view_id,
                    "display_name": next(view["display_name"] for view in self.drawing_views if view["view_id"] == view_id),
                    "position_m": {"x": (start_x_m or 0) + index * (0.06 + gap_m), "y": start_y_m or 0},
                    "outline_m": {"left": 0.0, "bottom": 0.0, "right": 0.06, "top": 0.06},
                }
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"direction": direction, "gap_m": gap_m, "views": placements},
        )

    async def rebuild_drawing(self) -> AdapterResult[dict[str, bool]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"rebuilt": True, "saved": True},
        )

    async def rename_drawing_sheet(
        self, sheet_name: str, new_sheet_name: str
    ) -> AdapterResult[dict[str, str]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": new_sheet_name, "renamed_from": sheet_name},
        )

    async def create_projected_drawing_view(
        self, source_view_id: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, str]]:
        if not any(view["view_id"] == source_view_id for view in self.drawing_views):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="The requested drawing view no longer exists",
            )
        self.projected_drawing_view_calls.append((source_view_id, x_m, y_m))
        view = {
            "view_id": f"Drawing View{len(self.drawing_views) + 1}",
            "display_name": f"Projected {source_view_id}",
        }
        self.drawing_views.append(view)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=view)

    async def list_custom_properties(
        self, configuration_name: str | None = None
    ) -> AdapterResult[list[dict[str, str]]]:
        properties = self.custom_properties.get(configuration_name or "", {})
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {"name": name, "value": value, "resolved_value": value}
                for name, value in properties.items()
            ],
        )

    async def set_custom_property(
        self, name: str, value: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, str]]:
        properties = self.custom_properties.setdefault(configuration_name or "", {})
        properties[name] = value
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"name": name, "value": value, "resolved_value": value},
        )

    async def delete_custom_property(
        self, name: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, str]]:
        properties = self.custom_properties.setdefault(configuration_name or "", {})
        if name not in properties:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Custom property does not exist",
            )
        del properties[name]
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data={"name": name})

    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=MassProperties(
                volume=1.0,
                surface_area=2.0,
                mass=3.0,
                center_of_mass=[0.0, 0.0, 0.0],
                moments_of_inertia={"ixx": 1.0},
            ),
        )

    async def get_bounding_box(self) -> AdapterResult[dict[str, object]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "min_point_mm": [-10.0, -5.0, 0.0],
                "max_point_mm": [90.0, 45.0, 25.0],
                "size_mm": [100.0, 50.0, 25.0],
            },
        )

    async def list_equations(self) -> AdapterResult[list[dict[str, object]]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[dict(equation) for equation in self.equations],
        )

    async def set_equation(
        self, name: str, expression: str
    ) -> AdapterResult[dict[str, object]]:
        for equation in self.equations:
            if equation["name"] == name:
                equation["expression"] = expression
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS, data=dict(equation)
                )
        equation = {
            "index": len(self.equations),
            "name": name,
            "expression": expression,
            "is_global_variable": "@" not in name,
        }
        self.equations.append(equation)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dict(equation))

    async def delete_equation(self, name: str) -> AdapterResult[dict[str, object]]:
        for index, equation in enumerate(self.equations):
            if equation["name"] == name:
                del self.equations[index]
                return AdapterResult(
                    status=AdapterResultStatus.SUCCESS, data=dict(equation)
                )
        return AdapterResult(
            status=AdapterResultStatus.ERROR, error="Equation does not exist"
        )


class SerialWorkflowAdapter(WorkflowAdapter):
    """Adapter probe that records whether workflow calls overlap."""

    def __init__(self) -> None:
        super().__init__()
        self.active_calls = 0
        self.maximum_active_calls = 0

    async def create_part(
        self, name: str, units: str
    ) -> AdapterResult[SolidWorksModel]:
        self.active_calls += 1
        self.maximum_active_calls = max(self.maximum_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0)
            return await super().create_part(name, units)
        finally:
            self.active_calls -= 1


class RejectingOpenWorkflowAdapter(WorkflowAdapter):
    """Adapter that simulates a COM failure containing sensitive local details."""

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=f"COM error opening {file_path}: C:\\private\\designs\\secret.sldprt",
        )


@pytest.mark.asyncio
async def test_create_session_warns_when_solidworks_is_disconnected(
    tmp_path: Path,
) -> None:
    """Session creation succeeds but clearly reports the unavailable CAD app."""
    adapter = WorkflowAdapter()
    adapter.is_connected = lambda: False  # type: ignore[method-assign]
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))

    result = await workflow.create_session()

    assert result.status is OperationStatus.SUCCEEDED
    assert result.data["session"]["connected"] is False
    assert result.warnings
    assert "SolidWorks is disconnected" in result.warnings[0]


@pytest.mark.asyncio
async def test_model_operation_returns_not_connected_when_solidworks_is_unavailable(
    tmp_path: Path,
) -> None:
    """An unavailable app is reported before an adapter operation is attempted."""
    adapter = WorkflowAdapter()
    adapter.is_connected = lambda: False  # type: ignore[method-assign]
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()

    result = await workflow.create_part(
        CreatePartRequest(
            session_id=session.data["session"]["session_id"], name="Unavailable"
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_NOT_CONNECTED"
    assert adapter.active_model_name is None


@pytest.mark.asyncio
async def test_start_solidworks_uses_explicit_adapter_launcher(tmp_path: Path) -> None:
    """The workflow exposes the adapter's safe launch-and-attach operation."""
    adapter = WorkflowAdapter()
    adapter.start_solidworks = (  # type: ignore[attr-defined]
        lambda _timeout: _started_solidworks_result()
    )
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))

    result = await workflow.start_solidworks(45)

    assert result.status is OperationStatus.SUCCEEDED
    assert result.data["startup"]["connected"] is True
    assert result.data["startup"]["started"] is True


async def _started_solidworks_result() -> dict[str, object]:
    return {"started": True, "already_running": False, "connected": True}


@pytest.fixture
def workflow(tmp_path: Path) -> MvpWorkflow:
    """Return a workflow with an isolated artifact directory."""
    return MvpWorkflow(
        adapter=WorkflowAdapter(), artifact_store=ArtifactStore(tmp_path)
    )


@pytest.mark.asyncio
async def test_close_document_removes_the_session_owned_document(
    workflow: MvpWorkflow,
) -> None:
    """A typed close releases the COM document and its opaque document reference."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="CloseMe")
    )
    document_id = part.data["document"]["document_id"]

    closed = await workflow.close_document(
        CloseDocumentRequest(session_id=session_id, document_id=document_id, save=False)
    )

    assert closed.status is OperationStatus.SUCCEEDED
    assert closed.data["closed_document_id"] == document_id
    assert workflow.adapter.closed_models == [False]
    remaining = await workflow.list_documents(session_id)
    assert remaining.data == {"documents": [], "active_document_id": None}


@pytest.mark.asyncio
async def test_feature_dimension_read_and_write_hide_localized_solidworks_names(
    workflow: MvpWorkflow,
) -> None:
    """Dimension tools resolve an opaque feature ID without exposing a native name."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="Dimensioned")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    await workflow.add_circle(
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=0,
            center_y=0,
            radius=5,
        )
    )
    await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id, document_id=document_id, sketch_id=sketch_id
        )
    )
    feature = await workflow.create_extrusion(
        CreateExtrusionRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            depth=10,
        )
    )
    feature_id = feature.data["feature"]["persistent_id"]

    before = await workflow.get_feature_dimension(
        GetFeatureDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=feature_id,
            dimension_index=1,
        )
    )
    changed = await workflow.set_feature_dimension(
        SetFeatureDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=feature_id,
            dimension_index=1,
            value=20,
        )
    )

    assert before.status is OperationStatus.SUCCEEDED
    assert before.data["dimension"] == {"index": 1, "value_mm": 10.0}
    assert changed.status is OperationStatus.SUCCEEDED
    assert changed.data["dimension"] == {"index": 1, "value_mm": 20.0}
    assert "Boss-Extrude1" not in repr(before.data)
    assert workflow.adapter.dimension_reads == ["D1@Boss-Extrude1", "D1@Boss-Extrude1"]
    assert workflow.adapter.dimension_writes == [("D1@Boss-Extrude1", 20.0)]


@pytest.mark.asyncio
async def test_listed_dimension_id_can_be_read_and_written_without_native_name(
    workflow: MvpWorkflow,
) -> None:
    """A dimension discovered in an opened model remains opaque to the client."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="ImportedPart")
    )
    document_id = part.data["document"]["document_id"]

    listed = await workflow.list_dimensions(
        ListDimensionsRequest(session_id=session_id, document_id=document_id)
    )
    dimension_id = listed.data["dimensions"][0]["dimension_id"]
    read = await workflow.get_dimension(
        GetDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            dimension_id=dimension_id,
        )
    )
    changed = await workflow.set_dimension(
        SetDimensionRequest(
            session_id=session_id,
            document_id=document_id,
            dimension_id=dimension_id,
            value=18,
        )
    )

    assert listed.status is OperationStatus.SUCCEEDED
    assert listed.data["dimensions"][0]["value_mm"] == 10.0
    assert "Boss-Extrude1" not in repr(listed.data)
    assert read.data["dimension"] == {"dimension_id": dimension_id, "value_mm": 10.0}
    assert changed.data["dimension"]["dimension_id"] != dimension_id
    assert changed.data["dimension"]["value_mm"] == 18.0
    assert workflow.adapter.dimension_writes == [("D1@Boss-Extrude1", 18.0)]


@pytest.mark.asyncio
async def test_part_to_step_workflow_returns_stable_references_and_artifact(
    workflow: MvpWorkflow,
) -> None:
    """A caller can build and export a part without passing raw COM objects."""
    session = await workflow.create_session()
    assert session.status is OperationStatus.SUCCEEDED
    session_id = session.data["session"]["session_id"]

    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="Bracket", units="mm")
    )
    assert part.status is OperationStatus.SUCCEEDED
    document_id = part.data["document"]["document_id"]

    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    assert sketch_id == "Sketch1"

    rectangle = await workflow.add_rectangle(
        AddRectangleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=0,
            x2=20,
            y2=10,
        )
    )
    assert rectangle.status is OperationStatus.SUCCEEDED

    exited = await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
        )
    )
    assert exited.status is OperationStatus.SUCCEEDED

    extrusion = await workflow.create_extrusion(
        CreateExtrusionRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            depth=25,
            unit="mm",
        )
    )
    assert extrusion.status is OperationStatus.SUCCEEDED
    assert extrusion.data["feature"]["persistent_id"] == "Feature1"
    assert workflow.adapter.extrusion_params is not None
    assert workflow.adapter.extrusion_params.depth == 25

    exported = await workflow.export_file(
        ExportFileRequest(
            session_id=session_id,
            document_id=document_id,
            artifact_name="bracket",
            format_type="step",
        )
    )
    assert exported.status is OperationStatus.SUCCEEDED
    assert exported.data["artifact"]["path"].endswith("bracket.step")
    assert Path(exported.data["artifact"]["path"]).is_file()


@pytest.mark.asyncio
async def test_reference_plane_is_opaque_revision_bound_and_usable_for_sketches(
    workflow: MvpWorkflow,
) -> None:
    """Reference-plane capabilities select only their originating plane."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="OffsetBracket")
    )
    document_id = part.data["document"]["document_id"]

    first_plane = await workflow.create_reference_plane(
        CreateReferencePlaneRequest(
            session_id=session_id,
            document_id=document_id,
            base_plane="Front",
            offset=1,
            unit="in",
        )
    )

    assert first_plane.status is OperationStatus.SUCCEEDED
    reference_id = first_plane.data["reference_plane"]["persistent_id"]
    assert reference_id.startswith("ref_")
    assert first_plane.data["reference_plane"]["display_name"] == "Plane1"
    assert first_plane.data["document"]["revision"] == 2
    assert workflow.adapter.reference_plane_params == [
        ReferencePlaneParameters(base_plane="Front", offset=25.4)
    ]

    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id,
            document_id=document_id,
            reference_plane_id=reference_id,
        )
    )
    assert sketch.status is OperationStatus.SUCCEEDED
    assert workflow.adapter.sketch_plane_name == "Plane1"
    exited = await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch.data["sketch"]["persistent_id"],
        )
    )
    assert exited.status is OperationStatus.SUCCEEDED

    second_plane = await workflow.create_reference_plane(
        CreateReferencePlaneRequest(
            session_id=session_id,
            document_id=document_id,
            base_plane="Top",
            offset=10,
        )
    )
    assert second_plane.status is OperationStatus.SUCCEEDED

    stale = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id,
            document_id=document_id,
            reference_plane_id=reference_id,
        )
    )
    assert stale.status is OperationStatus.FAILED
    assert stale.error is not None
    assert stale.error.code == "SW_REFERENCE_PLANE_NOT_CURRENT"


@pytest.mark.asyncio
async def test_reference_plane_is_revalidated_after_waiting_for_shared_com_lock(
    workflow: MvpWorkflow,
) -> None:
    """A concurrent document mutation cannot race an accepted plane into COM."""
    session_result = await workflow.create_session()
    session_id = session_result.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="ReferencePlaneRace")
    )
    document_id = part.data["document"]["document_id"]
    reference_plane = await workflow.create_reference_plane(
        CreateReferencePlaneRequest(
            session_id=session_id,
            document_id=document_id,
            base_plane="Right",
            offset=10,
        )
    )
    reference_id = reference_plane.data["reference_plane"]["persistent_id"]

    await workflow._adapter_lock.acquire()
    try:
        sketch_task = asyncio.create_task(
            workflow.create_sketch(
                CreateSketchRequest(
                    session_id=session_id,
                    document_id=document_id,
                    reference_plane_id=reference_id,
                )
            )
        )
        await asyncio.sleep(0)
        session = workflow.sessions.require(session_id)
        workflow.sessions.bump_document_revision(session, document_id)
    finally:
        workflow._adapter_lock.release()

    result = await sketch_task
    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_REFERENCE_PLANE_NOT_CURRENT"
    assert workflow.adapter.sketch_number == 0


@pytest.mark.asyncio
async def test_typed_feature_patterns_and_mirror_require_current_capabilities(
    workflow: MvpWorkflow,
) -> None:
    """Feature writes accept only tracked current-revision feature/axis/plane IDs."""
    session_result = await workflow.create_session()
    session_id = session_result.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="PatternBracket")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    await workflow.add_circle(
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=0,
            center_y=0,
            radius=5,
        )
    )
    await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id, document_id=document_id, sketch_id=sketch_id
        )
    )
    extrusion = await workflow.create_extrusion(
        CreateExtrusionRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            depth=10,
        )
    )
    feature_id = extrusion.data["feature"]["persistent_id"]

    linear = await workflow.create_feature_linear_pattern(
        CreateFeatureLinearPatternRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=feature_id,
            direction="X",
            instances=3,
            spacing=12.5,
        )
    )
    assert linear.status is OperationStatus.SUCCEEDED
    assert workflow.adapter.linear_pattern_params == FeatureLinearPatternParameters(
        feature_name="Boss-Extrude1", direction="X", instances=3, spacing=12.5
    )

    stale_feature = await workflow.create_feature_linear_pattern(
        CreateFeatureLinearPatternRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=feature_id,
            direction="X",
            instances=2,
            spacing=5,
        )
    )
    assert stale_feature.status is OperationStatus.FAILED
    assert stale_feature.error is not None
    assert stale_feature.error.code == "SW_FEATURE_NOT_CURRENT"

    axis = await workflow.get_reference_axis(
        GetReferenceAxisRequest(
            session_id=session_id, document_id=document_id, axis="Z"
        )
    )
    assert axis.status is OperationStatus.SUCCEEDED
    assert axis.data["reference_axis"]["persistent_id"].startswith("axis_")

    circular = await workflow.create_feature_circular_pattern(
        CreateFeatureCircularPatternRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=linear.data["feature"]["persistent_id"],
            reference_axis_id=axis.data["reference_axis"]["persistent_id"],
            instances=4,
            angle_degrees=180,
        )
    )
    assert circular.status is OperationStatus.SUCCEEDED
    assert workflow.adapter.circular_pattern_params == FeatureCircularPatternParameters(
        feature_name="LinearPattern1",
        axis_name="Z Axis",
        instances=4,
        angle_degrees=180,
    )

    origin_plane = await workflow.get_reference_plane(
        GetReferencePlaneRequest(
            session_id=session_id, document_id=document_id, plane="Right"
        )
    )
    mirrored = await workflow.mirror_feature(
        MirrorFeatureRequest(
            session_id=session_id,
            document_id=document_id,
            feature_id=circular.data["feature"]["persistent_id"],
            reference_plane_id=origin_plane.data["reference_plane"]["persistent_id"],
        )
    )
    assert mirrored.status is OperationStatus.SUCCEEDED
    assert workflow.adapter.mirror_feature_params == FeatureMirrorParameters(
        feature_name="CircularPattern1", plane_name="Right Plane"
    )


@pytest.mark.asyncio
async def test_hole_wizard_uses_only_an_exited_typed_point_sketch(
    workflow: MvpWorkflow,
) -> None:
    """The Hole Wizard receives no caller-selected face, path, or database data."""
    session_result = await workflow.create_session()
    session_id = session_result.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="HoleBracket")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(session_id=session_id, document_id=document_id, plane="Top")
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    point = await workflow.add_point(
        AddPointRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x=12,
            y=-4,
        )
    )
    assert point.status is OperationStatus.SUCCEEDED
    await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id, document_id=document_id, sketch_id=sketch_id
        )
    )

    hole = await workflow.create_hole_wizard(
        CreateHoleWizardRequest(
            session_id=session_id,
            document_id=document_id,
            location_sketch_id=sketch_id,
            diameter=6.5,
            depth=20,
        )
    )
    assert hole.status is OperationStatus.SUCCEEDED
    assert workflow.adapter.hole_wizard_params == HoleWizardParameters(
        location_sketch_name="Sketch1", diameter=6.5, depth=20
    )

    stale = await workflow.create_hole_wizard(
        CreateHoleWizardRequest(
            session_id=session_id,
            document_id=document_id,
            location_sketch_id=sketch_id,
            diameter=6.5,
            depth=20,
        )
    )
    assert stale.status is OperationStatus.FAILED
    assert stale.error is not None
    assert stale.error.code == "SW_SKETCH_NOT_READY"


@pytest.mark.asyncio
async def test_tapped_hole_uses_only_an_exited_typed_point_sketch(
    workflow: MvpWorkflow,
) -> None:
    """The tapped-hole tool resolves only an exited server-owned point sketch."""
    session_result = await workflow.create_session()
    session_id = session_result.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="TappedHoleBracket")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(session_id=session_id, document_id=document_id, plane="Top")
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    await workflow.add_point(
        AddPointRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x=12,
            y=-4,
        )
    )
    await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id, document_id=document_id, sketch_id=sketch_id
        )
    )

    hole = await workflow.create_tapped_hole(
        CreateTappedHoleRequest(
            session_id=session_id,
            document_id=document_id,
            location_sketch_id=sketch_id,
            thread_size="M6x1.0",
            depth=20,
            through_all=True,
        )
    )

    assert hole.status is OperationStatus.SUCCEEDED
    assert workflow.adapter.tapped_hole_params == ThreadedHoleParameters(
        location_sketch_name="Sketch1",
        thread_size="M6x1.0",
        depth=20,
        through_all=True,
    )


@pytest.mark.asyncio
async def test_sketch_write_rechecks_state_after_a_queued_exit(
    workflow: MvpWorkflow,
) -> None:
    """A queued point write cannot enter COM after the sketch is exited."""
    session_result = await workflow.create_session()
    session_id = session_result.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="SketchWriteRace")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(session_id=session_id, document_id=document_id, plane="Top")
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    session = workflow.sessions.require(session_id)

    await session.operation_lock.acquire()
    try:
        exit_task = asyncio.create_task(
            workflow.exit_sketch(
                ExitSketchRequest(
                    session_id=session_id,
                    document_id=document_id,
                    sketch_id=sketch_id,
                )
            )
        )
        await asyncio.sleep(0)
        point_task = asyncio.create_task(
            workflow.add_point(
                AddPointRequest(
                    session_id=session_id,
                    document_id=document_id,
                    sketch_id=sketch_id,
                    x=2,
                    y=3,
                )
            )
        )
        await asyncio.sleep(0)
    finally:
        session.operation_lock.release()

    exited = await exit_task
    point = await point_task

    assert exited.status is OperationStatus.SUCCEEDED
    assert point.status is OperationStatus.FAILED
    assert point.error is not None
    assert point.error.code == "SW_SKETCH_NOT_READY"


@pytest.mark.asyncio
async def test_typed_sketch_geometry_and_cut_share_the_tracked_sketch(
    workflow: MvpWorkflow,
) -> None:
    """Core sketch geometry and cut writes remain session- and sketch-scoped."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="CutBracket")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]

    line = await workflow.add_line(
        AddLineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=0,
            x2=20,
            y2=0,
        )
    )
    circle = await workflow.add_circle(
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=10,
            center_y=10,
            radius=5,
        )
    )
    arc = await workflow.add_arc(
        AddArcRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=0,
            center_y=0,
            start_x=5,
            start_y=0,
            end_x=0,
            end_y=5,
        )
    )
    centerline = await workflow.add_centerline(
        AddCenterlineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=-10,
            x2=0,
            y2=10,
        )
    )
    spline = await workflow.add_spline(
        AddSplineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            points=[{"x": 0, "y": 0}, {"x": 5, "y": 3}, {"x": 10, "y": 0}],
        )
    )
    polygon = await workflow.add_polygon(
        AddPolygonRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=20,
            center_y=20,
            radius=4,
            sides=6,
        )
    )
    ellipse = await workflow.add_ellipse(
        AddEllipseRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=30,
            center_y=20,
            major_axis=12,
            minor_axis=6,
        )
    )
    linear_pattern = await workflow.create_sketch_linear_pattern(
        CreateSketchLinearPatternRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[line.data["line"]["persistent_id"]],
            direction_x=1,
            direction_y=0,
            spacing=8,
            count=3,
        )
    )
    circular_pattern = await workflow.create_sketch_circular_pattern(
        CreateSketchCircularPatternRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[circle.data["circle"]["persistent_id"]],
            angle_degrees=180,
            count=3,
        )
    )
    mirrored = await workflow.mirror_sketch_entities(
        MirrorSketchEntitiesRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[arc.data["arc"]["persistent_id"]],
            mirror_line_id=centerline.data["centerline"]["persistent_id"],
        )
    )
    offset = await workflow.offset_sketch_entities(
        OffsetSketchEntitiesRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_ids=[ellipse.data["ellipse"]["persistent_id"]],
            distance=1,
        )
    )
    trimmed = await workflow.trim_sketch_entity(
        TrimSketchEntityRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            entity_id=line.data["line"]["persistent_id"],
            x=10,
            y=0,
        )
    )

    assert line.data["line"]["persistent_id"] == "Line1"
    assert circle.data["circle"]["persistent_id"] == "Circle1"
    assert arc.data["arc"]["persistent_id"] == "Arc1"
    assert centerline.data["centerline"]["persistent_id"] == "Centerline1"
    assert spline.data["spline"]["persistent_id"] == "Spline1"
    assert polygon.data["polygon"]["persistent_id"] == "Polygon1"
    assert ellipse.data["ellipse"]["persistent_id"] == "Ellipse1"
    assert linear_pattern.data["pattern"]["persistent_id"] == "LinearPattern1"
    assert circular_pattern.data["pattern"]["persistent_id"] == "CircularPattern1"
    assert mirrored.data["mirror"]["persistent_id"] == "Mirror1"
    assert offset.data["offset"]["persistent_id"] == "Offset1"
    assert trimmed.data["trimmed_entity"]["persistent_id"] == "Line1"
    assert workflow.adapter.trim_calls == [("Line1", 10, 0)]

    exited = await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
        )
    )
    assert exited.status is OperationStatus.SUCCEEDED

    cut = await workflow.create_cut_extrude(
        CreateCutExtrudeRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            depth=4,
        )
    )
    assert cut.status is OperationStatus.SUCCEEDED
    assert cut.data["feature"]["display_name"] == "Cut-Extrude1"
    assert cut.data["document"]["revision"] == 2


@pytest.mark.asyncio
async def test_revolve_uses_the_last_exited_session_sketch(
    workflow: MvpWorkflow,
) -> None:
    """Revolves retain the same sketch ownership and revision guarantees as extrudes."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="TurnedBracket")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    await workflow.add_centerline(
        AddCenterlineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=-10,
            x2=0,
            y2=10,
        )
    )
    await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
        )
    )

    revolved = await workflow.create_revolve(
        CreateRevolveRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            angle_degrees=180,
            thin_feature=True,
            thin_thickness=2,
        )
    )

    assert revolved.status is OperationStatus.SUCCEEDED
    assert revolved.data["feature"]["display_name"] == "Boss-Revolve1"
    assert revolved.data["document"]["revision"] == 2
    assert workflow.adapter.revolve_params == RevolveParameters(
        angle=180,
        thin_feature=True,
        thin_thickness=2,
    )


@pytest.mark.asyncio
async def test_revolve_supports_a_typed_cut_operation(workflow: MvpWorkflow) -> None:
    """A revolve cut uses the same session-owned sketch rules as a boss revolve."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="TurnedCutBracket")
    )
    document_id = part.data["document"]["document_id"]
    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    await workflow.add_centerline(
        AddCenterlineRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            x1=0,
            y1=-10,
            x2=0,
            y2=10,
        )
    )
    await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
        )
    )

    cut = await workflow.create_revolve(
        CreateRevolveRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            angle_degrees=180,
            operation="cut",
        )
    )

    assert cut.status is OperationStatus.SUCCEEDED
    assert workflow.adapter.revolve_params == RevolveParameters(
        angle=180,
        is_cut=True,
    )


@pytest.mark.asyncio
async def test_sweep_and_loft_only_accept_exited_session_sketches(
    workflow: MvpWorkflow,
) -> None:
    """Multi-sketch features use server-owned sketch references rather than names."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="BlendedBracket")
    )
    document_id = part.data["document"]["document_id"]

    async def create_exited_sketch() -> str:
        sketch = await workflow.create_sketch(
            CreateSketchRequest(
                session_id=session_id, document_id=document_id, plane="Front"
            )
        )
        sketch_id = sketch.data["sketch"]["persistent_id"]
        exited = await workflow.exit_sketch(
            ExitSketchRequest(
                session_id=session_id,
                document_id=document_id,
                sketch_id=sketch_id,
            )
        )
        assert exited.status is OperationStatus.SUCCEEDED
        return sketch_id

    profile_id = await create_exited_sketch()
    path_id = await create_exited_sketch()
    sweep = await workflow.create_sweep(
        CreateSweepRequest(
            session_id=session_id,
            document_id=document_id,
            profile_sketch_id=profile_id,
            path_sketch_id=path_id,
            twist_along_path=True,
            twist_angle_degrees=90,
            operation="cut",
        )
    )

    assert sweep.status is OperationStatus.SUCCEEDED
    assert sweep.data["feature"]["display_name"] == "Sweep1"
    assert workflow.adapter.sweep_params == SweepParameters(
        profile=profile_id,
        path=path_id,
        twist_along_path=True,
        twist_angle=90,
        is_cut=True,
    )

    first_loft_profile = await create_exited_sketch()
    second_loft_profile = await create_exited_sketch()
    loft = await workflow.create_loft(
        CreateLoftRequest(
            session_id=session_id,
            document_id=document_id,
            profile_sketch_ids=[first_loft_profile, second_loft_profile],
            start_tangent="normal",
            operation="cut",
        )
    )

    assert loft.status is OperationStatus.SUCCEEDED
    assert loft.data["feature"]["display_name"] == "Loft1"
    assert workflow.adapter.loft_params == LoftParameters(
        profiles=[first_loft_profile, second_loft_profile],
        start_tangent="normal",
        is_cut=True,
    )

    rejected = await workflow.create_sweep(
        CreateSweepRequest(
            session_id=session_id,
            document_id=document_id,
            profile_sketch_id=profile_id,
            path_sketch_id=path_id,
        )
    )
    assert rejected.status is OperationStatus.FAILED
    assert rejected.error is not None
    assert rejected.error.code == "SW_SKETCH_NOT_READY"


@pytest.mark.asyncio
async def test_open_model_and_save_native_copy_use_controlled_roots(
    tmp_path: Path,
) -> None:
    """Existing native models can be opened and saved without raw client paths."""
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    source = input_root / "bracket.sldprt"
    source.write_bytes(b"input model")
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(
        adapter=adapter,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        model_input_store=ModelInputStore(input_root),
    )

    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    opened = await workflow.open_model(
        OpenModelRequest(session_id=session_id, file_name="bracket.sldprt")
    )

    assert opened.status is OperationStatus.SUCCEEDED
    assert adapter.opened_path == source.resolve()
    document_id = opened.data["document"]["document_id"]

    saved = await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=document_id,
            artifact_name="bracket-revised",
        )
    )

    assert saved.status is OperationStatus.SUCCEEDED
    assert adapter.save_target is not None
    assert adapter.save_target.is_relative_to((tmp_path / "artifacts").resolve())
    assert adapter.save_target.suffix == ".sldprt"
    assert Path(saved.data["artifact"]["path"]).is_file()
    assert saved.data["document"]["revision"] == 2


@pytest.mark.asyncio
async def test_open_model_accepts_step_from_the_controlled_input_root(
    tmp_path: Path,
) -> None:
    """Neutral geometry imports retain the same no-raw-path input policy."""
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    source = input_root / "supplier-bracket.step"
    source.write_bytes(b"ISO-10303-21;")
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(
        adapter=adapter,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        model_input_store=ModelInputStore(input_root),
    )

    session = await workflow.create_session()
    opened = await workflow.open_model(
        OpenModelRequest(
            session_id=session.data["session"]["session_id"],
            file_name="supplier-bracket.step",
        )
    )

    assert opened.status is OperationStatus.SUCCEEDED
    assert adapter.opened_path == source.resolve()
    assert opened.data["document"]["document_type"] == "part"


@pytest.mark.asyncio
async def test_open_model_reports_a_missing_controlled_input(tmp_path: Path) -> None:
    """A missing file does not reach COM and returns an actionable error code."""
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        model_input_store=ModelInputStore(tmp_path / "inputs"),
    )
    session = await workflow.create_session()

    result = await workflow.open_model(
        OpenModelRequest(
            session_id=session.data["session"]["session_id"],
            file_name="missing.sldprt",
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_MODEL_INPUT_NOT_FOUND"


@pytest.mark.asyncio
async def test_document_operation_reactivates_its_session_document(
    tmp_path: Path,
) -> None:
    """Interleaved workflow sessions cannot operate on a stale adapter current model."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    first = await workflow.create_session()
    second = await workflow.create_session()
    first_id = first.data["session"]["session_id"]
    second_id = second.data["session"]["session_id"]

    first_part = await workflow.create_part(
        CreatePartRequest(session_id=first_id, name="First")
    )
    await workflow.create_part(CreatePartRequest(session_id=second_id, name="Second"))
    first_document_id = first_part.data["document"]["document_id"]

    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=first_id, document_id=first_document_id, plane="Front"
        )
    )

    assert sketch.status is OperationStatus.SUCCEEDED
    assert adapter.activation_names[-1] == "First"
    assert adapter.sketch_model_name == "First"


@pytest.mark.asyncio
async def test_create_assembly_and_drawing_register_typed_document_references(
    workflow: MvpWorkflow,
) -> None:
    """Document creation tools return session-owned assembly and drawing references."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]

    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="Fixture")
    )
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="FixtureDrawing")
    )

    assert assembly.status is OperationStatus.SUCCEEDED
    assert assembly.data["document"]["document_type"] == "assembly"
    assert drawing.status is OperationStatus.SUCCEEDED
    assert drawing.data["document"]["document_type"] == "drawing"
    assert (
        drawing.data["document"]["document_id"]
        != assembly.data["document"]["document_id"]
    )


@pytest.mark.asyncio
async def test_assembly_component_tools_require_saved_part_and_return_safe_summaries(
    tmp_path: Path,
) -> None:
    """Components use a saved part document reference rather than a client file path."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="FixturePart")
    )
    part_document_id = part.data["document"]["document_id"]
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="Fixture")
    )
    assembly_document_id = assembly.data["document"]["document_id"]

    unsaved = await workflow.insert_assembly_component(
        InsertAssemblyComponentRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            source_part_document_id=part_document_id,
        )
    )

    assert unsaved.status is OperationStatus.FAILED
    assert unsaved.error is not None
    assert unsaved.error.code == "SW_COMPONENT_SOURCE_NOT_SAVED"

    source_part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="SavedFixturePart")
    )
    source_part_document_id = source_part.data["document"]["document_id"]
    saved_part = await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=source_part_document_id,
            artifact_name="fixture-part",
        )
    )
    assert saved_part.status is OperationStatus.SUCCEEDED
    saved_assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="SavedFixture")
    )
    saved_assembly_document_id = saved_assembly.data["document"]["document_id"]

    inserted = await workflow.insert_assembly_component(
        InsertAssemblyComponentRequest(
            session_id=session_id,
            document_id=saved_assembly_document_id,
            source_part_document_id=source_part_document_id,
            x=1,
            y=2,
            z=3,
            unit="in",
        )
    )
    listed = await workflow.list_assembly_components(
        ListAssemblyComponentsRequest(
            session_id=session_id,
            document_id=saved_assembly_document_id,
            limit=1,
        )
    )
    located = await workflow.get_assembly_component(
        GetAssemblyComponentRequest(
            session_id=session_id,
            document_id=saved_assembly_document_id,
            component_name="FixturePart-1",
        )
    )

    assert inserted.status is OperationStatus.SUCCEEDED
    assert inserted.data["component"] == {
        "component_id": "component_1",
        "display_name": "FixturePart-1",
        "document_type": "part",
    }
    assert inserted.data["position_mm"] == {"x": 25.4, "y": 50.8, "z": 76.2}
    assert inserted.data["document"]["revision"] == 2
    assert adapter.assembly_component_calls == [
        (str(adapter.save_target), 0.0254, 0.0508, 0.0762)
    ]
    assert listed.status is OperationStatus.SUCCEEDED
    assert listed.data["total"] == 1
    assert listed.data["components"] == [inserted.data["component"]]
    assert located.status is OperationStatus.SUCCEEDED
    assert located.data["component"] == inserted.data["component"]
    assert adapter.activation_names[-1] == "SavedFixture"


@pytest.mark.asyncio
async def test_assembly_component_tools_reject_a_part_outside_the_artifact_store(
    tmp_path: Path,
) -> None:
    """Opening a trusted input does not authorize it as an assembly component source."""
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "external.sldprt").write_bytes(b"native part")
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        model_input_store=ModelInputStore(input_root),
    )
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    source = await workflow.open_model(
        OpenModelRequest(session_id=session_id, file_name="external.sldprt")
    )
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="Fixture")
    )

    result = await workflow.insert_assembly_component(
        InsertAssemblyComponentRequest(
            session_id=session_id,
            document_id=assembly.data["document"]["document_id"],
            source_part_document_id=source.data["document"]["document_id"],
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_COMPONENT_SOURCE_UNTRUSTED"


@pytest.mark.asyncio
async def test_assembly_interference_uses_the_active_session_assembly(
    tmp_path: Path,
) -> None:
    """Interference detection returns native adapter data without a simulated success path."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="InterferenceFixture")
    )

    result = await workflow.check_assembly_interference(
        CheckAssemblyInterferenceRequest(
            session_id=session_id,
            document_id=assembly.data["document"]["document_id"],
            include_coincident=True,
            include_multibody_part_interferences=True,
        )
    )

    assert result.status is OperationStatus.SUCCEEDED
    assert result.route.value == "com"
    assert result.data == {"total": 0, "interferences": []}


@pytest.mark.asyncio
async def test_assembly_mate_tools_issue_opaque_refs_convert_units_and_invalidate_stale_refs(
    tmp_path: Path,
) -> None:
    """Only current-session geometric references can create one typed assembly mate."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="Fixture")
    )
    assembly_document_id = assembly.data["document"]["document_id"]

    listed = await workflow.list_assembly_mate_entities(
        ListAssemblyMateEntitiesRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            entity_types=["face"],
        )
    )

    assert listed.status is OperationStatus.SUCCEEDED
    entities = listed.data["entities"]
    assert len(entities) == 2
    assert {"entity_id", "component_id", "entity_type", "display_name"} == set(
        entities[0]
    )
    assert "persistent_reference" not in entities[0]

    created = await workflow.create_assembly_mate(
        CreateAssemblyMateRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            first_entity_id=entities[0]["entity_id"],
            second_entity_id=entities[1]["entity_id"],
            mate_type="distance",
            distance=1,
            unit="in",
        )
    )

    assert created.status is OperationStatus.SUCCEEDED
    assert created.data["mate"] == {"mate_type": "distance", "display_name": "Mate1"}
    assert created.data["document"]["revision"] == 2
    assert adapter.assembly_mate_calls == [
        {
            "first_persistent_reference": "AQ==",
            "second_persistent_reference": "Ag==",
            "mate_type": "distance",
            "alignment": "aligned",
            "flip": False,
            "distance_m": 0.0254,
            "angle_radians": 0.0,
            "for_positioning_only": False,
            "lock_rotation": False,
        }
    ]

    stale = await workflow.create_assembly_mate(
        CreateAssemblyMateRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            first_entity_id=entities[0]["entity_id"],
            second_entity_id=entities[1]["entity_id"],
            mate_type="coincident",
        )
    )

    assert stale.status is OperationStatus.FAILED
    assert stale.error is not None
    assert stale.error.code == "SW_MATE_ENTITY_STALE"


def test_shared_saved_assembly_mutation_invalidates_mate_entities_in_every_session(
    tmp_path: Path,
) -> None:
    """A mutation from one workflow session expires another session's old geometry IDs."""
    sessions = SessionService()
    first_session = sessions.create(connected=True)
    second_session = sessions.create(connected=True)
    model_data = {
        "name": "shared.sldasm",
        "type": "Assembly",
        "path": str(tmp_path / "shared.sldasm"),
    }
    first_document = sessions.register_document(first_session, model_data)
    second_document = sessions.register_document(second_session, model_data)
    issued = sessions.issue_assembly_mate_entity(
        first_session,
        first_document.document_id,
        component_id="component_1",
        entity_type="face",
        display_name="Fixture Face 1",
        persistent_reference="AQ==",
    )

    sessions.bump_document_revision(second_session, second_document.document_id)

    with pytest.raises(MateEntityReferenceError, match="stale"):
        sessions.resolve_assembly_mate_entity(
            first_session, first_document.document_id, issued.entity_id
        )
    assert not first_session.assembly_mate_entities


def test_fresh_mate_entity_listing_replaces_prior_session_references() -> None:
    """Repeated geometry listings retain only current IDs and a bounded stale cache."""
    sessions = SessionService()
    session = sessions.create(connected=True)
    document = sessions.register_document(
        session,
        {"name": "Fixture", "type": "Assembly", "path": ""},
    )
    issued = sessions.issue_assembly_mate_entity(
        session,
        document.document_id,
        component_id="component_1",
        entity_type="face",
        display_name="Fixture Face 1",
        persistent_reference="AQ==",
    )

    sessions.begin_assembly_mate_entity_listing(session, document.document_id)

    with pytest.raises(MateEntityReferenceError, match="stale"):
        sessions.resolve_assembly_mate_entity(
            session, document.document_id, issued.entity_id
        )
    assert not session.assembly_mate_entities
    assert len(session.invalidated_assembly_mate_entity_ids) == 1


@pytest.mark.asyncio
async def test_drawing_view_tools_require_saved_model_and_return_safe_summaries(
    tmp_path: Path,
) -> None:
    """Drawing views use saved model references, fixed orientations, and pagination."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    source_part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="DrawingSource")
    )
    source_document_id = source_part.data["document"]["document_id"]
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="FixtureDrawing")
    )
    drawing_document_id = drawing.data["document"]["document_id"]

    unsaved = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=source_document_id,
        )
    )
    assert unsaved.status is OperationStatus.FAILED
    assert unsaved.error is not None
    assert unsaved.error.code == "SW_DRAWING_VIEW_SOURCE_NOT_SAVED"

    source_part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="SavedDrawingSource")
    )
    source_document_id = source_part.data["document"]["document_id"]
    saved_source = await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=source_document_id,
            artifact_name="saved-drawing-source",
        )
    )
    assert saved_source.status is OperationStatus.SUCCEEDED
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="SavedFixtureDrawing")
    )
    drawing_document_id = drawing.data["document"]["document_id"]

    created = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=source_document_id,
            orientation="isometric",
            x=2,
            y=3,
            unit="in",
        )
    )
    listed = await workflow.list_drawing_views(
        ListDrawingViewsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            limit=1,
        )
    )
    outline = await workflow.get_drawing_view_outline(
        GetDrawingViewOutlineRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=created.data["view"]["view_id"],
        )
    )

    assert created.status is OperationStatus.SUCCEEDED
    assert created.data["view"] == {
        "view_id": "Drawing View1",
        "display_name": "Isometric View",
    }
    assert created.data["orientation"] == "isometric"
    assert created.data["position_mm"] == {"x": 50.8, "y": 76.2}
    assert created.data["document"]["revision"] == 2
    assert adapter.drawing_view_calls == [
        (str(adapter.save_target), "*Isometric", 0.0508, 0.0762)
    ]
    assert listed.status is OperationStatus.SUCCEEDED
    assert listed.data["total"] == 1
    assert listed.data["views"] == [created.data["view"]]
    assert outline.status is OperationStatus.SUCCEEDED
    assert outline.data["outline"] == {
        "view_id": "Drawing View1",
        "left_mm": 20.0,
        "bottom_mm": 30.0,
        "right_mm": 80.0,
        "top_mm": 90.0,
    }


@pytest.mark.asyncio
async def test_align_drawing_views_converts_units_and_bumps_revision(
    tmp_path: Path,
) -> None:
    """Batch alignment returns placements and invalidates the prior drawing revision."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    source = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="AlignmentSource")
    )
    source_id = source.data["document"]["document_id"]
    await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=source_id,
            artifact_name="alignment-source",
        )
    )
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="AlignmentDrawing")
    )
    drawing_id = drawing.data["document"]["document_id"]
    first = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_id,
            source_model_document_id=source_id,
        )
    )
    second = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_id,
            source_model_document_id=source_id,
            orientation="top",
        )
    )

    aligned = await workflow.align_drawing_views(
        AlignDrawingViewsRequest(
            session_id=session_id,
            document_id=drawing_id,
            view_ids=[first.data["view"]["view_id"], second.data["view"]["view_id"]],
            direction="horizontal",
            gap=0.5,
            start_x=1,
            start_y=2,
            unit="in",
        )
    )

    assert aligned.status is OperationStatus.SUCCEEDED
    assert adapter.alignment_calls == [
        (["Drawing View1", "Drawing View2"], "horizontal", 0.0127, 0.0254, 0.0508)
    ]
    assert aligned.data["gap"] == {"value": 0.5, "unit": "in"}
    assert aligned.data["document"]["revision"] == 4
    assert aligned.data["views"][0]["position_mm"] == {"x": 25.4, "y": 50.8}


@pytest.mark.asyncio
async def test_rebuild_drawing_bumps_the_session_document_revision(tmp_path: Path) -> None:
    """A rebuilt drawing returns an explicit save confirmation and fresh revision."""
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(), artifact_store=ArtifactStore(tmp_path)
    )
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="RebuildDrawing")
    )

    rebuilt = await workflow.rebuild_drawing(
        RebuildDrawingRequest(
            session_id=session_id,
            document_id=drawing.data["document"]["document_id"],
        )
    )

    assert rebuilt.status is OperationStatus.SUCCEEDED
    assert rebuilt.data == {
        "rebuilt": True,
        "saved": True,
        "document": {
            **drawing.data["document"],
            "revision": drawing.data["document"]["revision"] + 1,
        },
    }


@pytest.mark.asyncio
async def test_rename_drawing_sheet_returns_verified_sheet_identity(tmp_path: Path) -> None:
    """Sheet renaming is confined to the current drawing session document."""
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(), artifact_store=ArtifactStore(tmp_path)
    )
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="RenamedSheetDrawing")
    )

    renamed = await workflow.rename_drawing_sheet(
        RenameDrawingSheetRequest(
            session_id=session_id,
            document_id=drawing.data["document"]["document_id"],
            sheet_name="Sheet1",
            new_sheet_name="DetailSheet",
        )
    )

    assert renamed.status is OperationStatus.SUCCEEDED
    assert renamed.data["renamed_sheet"] == {
        "name": "DetailSheet",
        "renamed_from": "Sheet1",
    }


@pytest.mark.asyncio
async def test_drawing_bom_requires_a_current_view_of_the_requested_assembly(
    tmp_path: Path,
) -> None:
    """A BOM cannot combine an assembly with a view created from another model."""
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(), artifact_store=ArtifactStore(tmp_path)
    )
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="ViewSource")
    )
    part_document_id = part.data["document"]["document_id"]
    await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=part_document_id,
            artifact_name="view-source",
        )
    )
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="BomSource")
    )
    assembly_document_id = assembly.data["document"]["document_id"]
    await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            artifact_name="bom-source",
        )
    )
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="BomDrawing")
    )
    drawing_document_id = drawing.data["document"]["document_id"]
    part_view = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=part_document_id,
        )
    )

    result = await workflow.create_drawing_bom(
        CreateDrawingBomRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_assembly_document_id=assembly_document_id,
            source_view_id=part_view.data["view"]["view_id"],
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_DRAWING_BOM_SOURCE_VIEW_MISMATCH"


@pytest.mark.asyncio
async def test_projected_drawing_view_uses_known_view_id_and_converts_units(
    tmp_path: Path,
) -> None:
    """Projected view creation remains scoped to a current drawing view reference."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="ProjectionSource")
    )
    part_document_id = part.data["document"]["document_id"]
    saved = await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=part_document_id,
            artifact_name="projection-source",
        )
    )
    assert saved.status is OperationStatus.SUCCEEDED
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="ProjectionDrawing")
    )
    drawing_document_id = drawing.data["document"]["document_id"]
    source_view = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=part_document_id,
        )
    )

    projected = await workflow.create_projected_drawing_view(
        CreateProjectedDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_view_id=source_view.data["view"]["view_id"],
            x=4,
            y=5,
            unit="in",
        )
    )

    assert projected.status is OperationStatus.SUCCEEDED
    assert projected.data["source_view_id"] == "Drawing View1"
    assert projected.data["position_mm"] == {"x": 101.6, "y": 127.0}
    assert projected.data["document"]["revision"] == 3
    assert adapter.projected_drawing_view_calls == [("Drawing View1", 0.1016, 0.127)]
    assert projected.data["view"] == {
        "view_id": "Drawing View2",
        "display_name": "Projected Drawing View1",
    }


@pytest.mark.asyncio
async def test_projected_drawing_view_reports_unsupported_api_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP callers receive a stable capability error from older SolidWorks."""
    adapter = WorkflowAdapter()

    async def unsupported_projected_view(
        *_args: object,
    ) -> AdapterResult[dict[str, str]]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="The connected SolidWorks version does not support associated projected drawing views.",
            metadata={"error_code": ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED},
        )

    monkeypatch.setattr(
        adapter, "create_projected_drawing_view", unsupported_projected_view
    )
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="ProjectionSource")
    )
    saved = await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=part.data["document"]["document_id"],
            artifact_name="projection-source",
        )
    )
    assert saved.status is OperationStatus.SUCCEEDED
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="ProjectionDrawing")
    )
    drawing_document_id = drawing.data["document"]["document_id"]
    source_view = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=part.data["document"]["document_id"],
        )
    )
    assert source_view.status is OperationStatus.SUCCEEDED

    result = await workflow.create_projected_drawing_view(
        CreateProjectedDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_view_id=source_view.data["view"]["view_id"],
            x=100,
            y=100,
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED
    assert "solidworks_create_drawing_view" in result.error.suggestion
    assert (
        workflow.sessions.require_document(
            workflow.sessions.require(session_id), drawing_document_id
        ).revision
        == 2
    )


@pytest.mark.asyncio
async def test_custom_properties_are_paginated_and_scoped_to_active_configuration(
    workflow: MvpWorkflow,
) -> None:
    """Custom properties retain document/configuration scope and update revision."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="PropertyPart")
    )
    document_id = part.data["document"]["document_id"]

    document_property = await workflow.set_custom_property(
        SetCustomPropertyRequest(
            session_id=session_id,
            document_id=document_id,
            name="PartNumber",
            value="BRKT-001",
        )
    )
    activated = await workflow.activate_configuration(
        ActivateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Default",
        )
    )
    configured_property = await workflow.set_custom_property(
        SetCustomPropertyRequest(
            session_id=session_id,
            document_id=document_id,
            name="Revision",
            value="A",
            scope="active_configuration",
        )
    )
    document_properties = await workflow.list_custom_properties(
        ListCustomPropertiesRequest(
            session_id=session_id,
            document_id=document_id,
            limit=1,
        )
    )
    configured_properties = await workflow.list_custom_properties(
        ListCustomPropertiesRequest(
            session_id=session_id,
            document_id=document_id,
            scope="active_configuration",
        )
    )

    assert document_property.status is OperationStatus.SUCCEEDED
    assert document_property.data["scope"] == "document"
    assert document_property.data["configuration"] is None
    assert document_property.data["document"]["revision"] == 2
    assert activated.status is OperationStatus.SUCCEEDED
    assert activated.data["document"]["revision"] == 3
    assert configured_property.status is OperationStatus.SUCCEEDED
    assert configured_property.data["scope"] == "active_configuration"
    assert configured_property.data["configuration"] == "Default"
    assert configured_property.data["document"]["revision"] == 4
    assert document_properties.data["total"] == 1
    assert document_properties.data["properties"] == [
        {"name": "PartNumber", "value": "BRKT-001", "resolved_value": "BRKT-001"}
    ]
    assert configured_properties.data["properties"] == [
        {"name": "Revision", "value": "A", "resolved_value": "A"}
    ]


def test_custom_property_request_rejects_path_like_name() -> None:
    """Property names are identifiers and cannot be used as path input."""
    with pytest.raises(ValueError, match="name"):
        SetCustomPropertyRequest(
            session_id="session-1",
            document_id="document-1",
            name="..\\outside",
            value="value",
        )


@pytest.mark.asyncio
async def test_configuration_and_custom_property_deletion_are_verified(
    workflow: MvpWorkflow,
) -> None:
    """Deletion rejects active configurations and leaves no stale property state."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="DeletionFixture")
    )
    document_id = part.data["document"]["document_id"]

    created_property = await workflow.set_custom_property(
        SetCustomPropertyRequest(
            session_id=session_id,
            document_id=document_id,
            name="PartNumber",
            value="PN-001",
        )
    )
    deleted_property = await workflow.delete_custom_property(
        DeleteCustomPropertyRequest(
            session_id=session_id,
            document_id=document_id,
            name="PartNumber",
        )
    )
    assert created_property.status is OperationStatus.SUCCEEDED
    assert deleted_property.status is OperationStatus.SUCCEEDED
    assert deleted_property.data["property"]["name"] == "PartNumber"

    created_configuration = await workflow.create_configuration(
        CreateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="ToDelete",
        )
    )
    active_delete = await workflow.delete_configuration(
        DeleteConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="ToDelete",
        )
    )
    assert created_configuration.status is OperationStatus.SUCCEEDED
    assert active_delete.status is OperationStatus.FAILED
    assert active_delete.error.code == "SW_ACTIVE_CONFIGURATION_DELETE_REJECTED"

    activated = await workflow.activate_configuration(
        ActivateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Default",
        )
    )
    deleted_configuration = await workflow.delete_configuration(
        DeleteConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="ToDelete",
        )
    )
    assert activated.status is OperationStatus.SUCCEEDED
    assert deleted_configuration.status is OperationStatus.SUCCEEDED
    assert deleted_configuration.data["configuration"]["name"] == "ToDelete"


@pytest.mark.asyncio
async def test_get_configuration_returns_comment_without_mutating_document(
    tmp_path: Path,
) -> None:
    """Configuration metadata remains a read-only operation in the session."""
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(), artifact_store=ArtifactStore(tmp_path)
    )
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="ConfigurationMetadata")
    )
    document_id = part.data["document"]["document_id"]

    configuration = await workflow.get_configuration(
        GetConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Default",
        )
    )

    assert configuration.status is OperationStatus.SUCCEEDED
    assert configuration.data == {
        "configuration": {
            "name": "Default",
            "description": "Welded construction",
            "is_active": True,
        }
    }
def test_material_assignment_request_requires_a_solidworks_material_database() -> None:
    """Material assignment cannot pass arbitrary file types into COM."""
    with pytest.raises(ValueError, match="database_name"):
        SetMaterialAssignmentRequest(
            session_id="session-1",
            document_id="document-1",
            database_name="materials.txt",
            material_name="Alloy Steel",
        )


@pytest.mark.asyncio
async def test_feature_and_configuration_lists_reactivate_document_and_paginate(
    tmp_path: Path,
) -> None:
    """Read tools activate their session document and return bounded list results."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    first_session = await workflow.create_session()
    second_session = await workflow.create_session()
    first_id = first_session.data["session"]["session_id"]
    second_id = second_session.data["session"]["session_id"]
    first_part = await workflow.create_part(
        CreatePartRequest(session_id=first_id, name="First")
    )
    await workflow.create_part(CreatePartRequest(session_id=second_id, name="Second"))
    first_document_id = first_part.data["document"]["document_id"]

    features = await workflow.list_features(
        ListFeaturesRequest(
            session_id=first_id,
            document_id=first_document_id,
            limit=1,
            offset=1,
        )
    )
    configurations = await workflow.list_configurations(
        ListConfigurationsRequest(
            session_id=first_id,
            document_id=first_document_id,
            limit=1,
        )
    )

    assert features.status is OperationStatus.SUCCEEDED
    assert features.data["total"] == 2
    assert features.data["count"] == 1
    assert features.data["has_more"] is False
    assert features.data["features"][0]["name"] == "Boss-Extrude1"
    feature_id = features.data["features"][0]["feature_id"]
    assert feature_id.startswith("feat_")
    feature_dimension = await workflow.get_feature_dimension(
        GetFeatureDimensionRequest(
            session_id=first_id,
            document_id=first_document_id,
            feature_id=feature_id,
        )
    )
    assert feature_dimension.status is OperationStatus.SUCCEEDED
    assert feature_dimension.data["dimension"]["value_mm"] == 10.0
    assert configurations.status is OperationStatus.SUCCEEDED
    assert configurations.data["total"] == 2
    assert configurations.data["has_more"] is True
    assert adapter.features_model_name == "First"
    assert adapter.configurations_model_name == "First"
    assert adapter.activation_names[-1] == "First"


@pytest.mark.asyncio
async def test_part_body_and_material_tools_reactivate_and_preserve_configuration(
    workflow: MvpWorkflow,
) -> None:
    """Part inspection is paginated and material writes are configuration-scoped."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="Bracket")
    )
    document_id = part.data["document"]["document_id"]

    bodies = await workflow.list_solid_bodies(
        ListSolidBodiesRequest(
            session_id=session_id,
            document_id=document_id,
            limit=1,
            offset=1,
        )
    )
    surface_bodies = await workflow.list_surface_bodies(
        ListSurfaceBodiesRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )
    material_before = await workflow.get_material_assignment(
        GetMaterialAssignmentRequest(
            document_id=document_id,
            session_id=session_id,
        )
    )
    material_after = await workflow.set_material_assignment(
        SetMaterialAssignmentRequest(
            session_id=session_id,
            document_id=document_id,
            database_name=r"C:\\materials\\solidworks materials.sldmat",
            material_name="Alloy Steel",
        )
    )

    assert bodies.status is OperationStatus.SUCCEEDED
    assert bodies.data["total"] == 2
    assert bodies.data["bodies"] == [
        {"body_id": "body_2", "name": "Lug", "visible": True}
    ]
    assert surface_bodies.status is OperationStatus.SUCCEEDED
    assert surface_bodies.data["total"] == 0
    assert material_before.status is OperationStatus.SUCCEEDED
    assert material_before.data["material"]["material_name"] == "Plain Carbon Steel"
    assert material_after.status is OperationStatus.SUCCEEDED
    assert material_after.data["material"] == {
        "database_name": r"C:\\materials\\solidworks materials.sldmat",
        "material_name": "Alloy Steel",
    }
    assert material_after.data["configuration"] == "Default"
    assert material_after.data["document"]["revision"] == 2
    assert workflow.adapter.bodies_model_name == "Bracket"
    assert workflow.adapter.material_configuration_names == ["Default", "Default"]


@pytest.mark.asyncio
async def test_part_topology_returns_safe_face_edge_and_vertex_measurements(
    tmp_path: Path,
) -> None:
    """Topology details are typed, measured, and never expose persistent tokens."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="TopologyFixture")
    )
    document_id = part.data["document"]["document_id"]

    result = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face", "edge", "vertex"],
            limit=20,
        )
    )

    assert result.status is OperationStatus.SUCCEEDED
    assert result.data["total"] == 15
    face, edge, vertex = (
        result.data["entities"][0],
        result.data["entities"][3],
        result.data["entities"][7],
    )
    assert face["area_mm2"] == 1000.0
    assert face["normal"] == [0.0, 0.0, 1.0]
    assert edge["length_mm"] == 25.0
    assert edge["curve_identity"] == 3001
    assert vertex["point_mm"] == [0.0, 0.0, 0.0]
    assert all(
        "persistent_reference" not in entity for entity in result.data["entities"]
    )


@pytest.mark.asyncio
async def test_chamfer_uses_current_opaque_edges_and_returns_diagnostics(
    tmp_path: Path,
) -> None:
    """A chamfer cannot consume guessed edge names or stale topology IDs."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="ChamferFixture")
    )
    document_id = part.data["document"]["document_id"]
    topology = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["edge"],
        )
    )
    edge_id = topology.data["entities"][0]["entity_id"]

    result = await workflow.create_chamfer(
        CreateChamferRequest(
            session_id=session_id,
            document_id=document_id,
            edge_ids=[edge_id],
            distance=2,
            angle_degrees=30,
            chamfer_type="distance_angle",
        )
    )

    assert result.status is OperationStatus.SUCCEEDED
    assert result.data["feature"]["display_name"].startswith("Chamfer")
    assert result.data["diagnostics"]["requested_edge_count"] == 1
    assert result.data["diagnostics"]["selected_edge_count"] == 1
    assert result.data["diagnostics"]["geometry_validated"] is False
    assert result.data["diagnostics"]["preflight"]["status"] == "ok"
    assert result.data["diagnostics"]["preflight"]["angle_applied"] is True
    assert result.data["diagnostics"]["preflight"]["edge_metrics"][0]["length_mm"] == 25.0
    assert result.data["document"]["revision"] == 2

    refreshed_topology = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["edge"],
        )
    )
    warning = await workflow.create_chamfer(
        CreateChamferRequest(
            session_id=session_id,
            document_id=document_id,
            edge_ids=[refreshed_topology.data["entities"][0]["entity_id"]],
            distance=25,
            chamfer_type="equal_distance",
        )
    )

    assert warning.status is OperationStatus.SUCCEEDED
    assert warning.data["diagnostics"]["preflight"]["status"] == "warning"
    assert warning.data["diagnostics"]["preflight"]["warnings"][0]["code"] == (
        "SW_CHAMFER_DISTANCE_NOT_SMALLER_THAN_EDGE"
    )

    failure_topology = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["edge"],
        )
    )

    async def reject_chamfer(*_args: object) -> AdapterResult[object]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="SolidWorks rejected the chamfer dimensions",
        )

    adapter.create_chamfer = reject_chamfer  # type: ignore[method-assign]
    failure = await workflow.create_chamfer(
        CreateChamferRequest(
            session_id=session_id,
            document_id=document_id,
            edge_ids=[failure_topology.data["entities"][0]["entity_id"]],
            distance=25,
        )
    )

    assert failure.status is OperationStatus.FAILED
    assert failure.data["diagnostics"]["preflight"]["status"] == "warning"


def test_chamfer_request_rejects_non_finite_dimensions_and_blank_edges() -> None:
    """Late-bound COM must never receive NaN, infinity, or empty edge IDs."""
    with pytest.raises(ValueError, match="distance"):
        CreateChamferRequest(
            session_id="session",
            document_id="document",
            edge_ids=["part_topology_edge"],
            distance=float("inf"),
        )
    with pytest.raises(ValueError, match="edge_ids must not contain empty values"):
        CreateChamferRequest(
            session_id="session",
            document_id="document",
            edge_ids=[""],
            distance=1,
        )


@pytest.mark.asyncio
async def test_invalid_topology_geometry_does_not_issue_capabilities(
    tmp_path: Path,
) -> None:
    """Malformed adapter geometry must not leave usable-looking edge IDs."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="MalformedTopologyFixture")
    )
    document_id = part.data["document"]["document_id"]

    async def malformed_topology(
        *_args: object,
    ) -> AdapterResult[list[dict[str, object]]]:
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {
                    "entity_type": "edge",
                    "display_name": "EdgeMalformed",
                    "persistent_reference": "cGVyc2lzdGVudA==",
                    "length_mm": float("nan"),
                }
            ],
        )

    adapter.list_part_topology = malformed_topology  # type: ignore[method-assign]
    result = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["edge"],
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_ADAPTER_INVALID_RESPONSE"
    assert workflow.sessions.require(session_id).part_topology == {}


@pytest.mark.asyncio
async def test_advanced_surface_features_use_only_current_opaque_references(
    tmp_path: Path,
) -> None:
    """Draft, thicken, and wrap record actual mock features without leaking tokens."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="AdvancedSurfaceFixture")
    )
    document_id = part.data["document"]["document_id"]

    draft_topology = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    draft = await workflow.create_draft(
        CreateDraftRequest(
            session_id=session_id,
            document_id=document_id,
            face_ids=[draft_topology.data["entities"][0]["entity_id"]],
            angle_degrees=3,
        )
    )
    thicken_topology = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    thicken = await workflow.create_thicken(
        CreateThickenRequest(
            session_id=session_id,
            document_id=document_id,
            face_id=thicken_topology.data["entities"][0]["entity_id"],
            thickness=1.5,
            operation="boss",
            direction="both",
        )
    )
    sketch = await workflow.create_sketch(
        CreateSketchRequest(
            session_id=session_id, document_id=document_id, plane="Front"
        )
    )
    sketch_id = sketch.data["sketch"]["persistent_id"]
    await workflow.add_circle(
        AddCircleRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            center_x=0,
            center_y=0,
            radius=4,
        )
    )
    await workflow.exit_sketch(
        ExitSketchRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
        )
    )
    wrap_topology = await workflow.list_part_topology(
        ListPartTopologyRequest(
            session_id=session_id,
            document_id=document_id,
            entity_types=["face"],
        )
    )
    wrap = await workflow.create_wrap(
        CreateWrapRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id=sketch_id,
            face_id=wrap_topology.data["entities"][0]["entity_id"],
            operation="engrave",
            thickness=0.5,
        )
    )

    assert draft.status is OperationStatus.SUCCEEDED
    assert draft.data["feature"]["display_name"].startswith("Draft")
    assert thicken.status is OperationStatus.SUCCEEDED
    assert thicken.data["feature"]["display_name"].startswith("Thicken")
    assert wrap.status is OperationStatus.SUCCEEDED
    assert wrap.data["feature"]["display_name"].startswith("Wrap")
    assert "persistent_reference" not in str(wrap.data)


@pytest.mark.asyncio
async def test_session_documents_can_be_listed_and_reactivated_by_opaque_id(
    workflow: MvpWorkflow,
) -> None:
    """Multi-document workflows never need a caller-provided title or path."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="DocumentSwitchPart")
    )
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="DocumentSwitchAssembly")
    )
    part_id = part.data["document"]["document_id"]
    assembly_id = assembly.data["document"]["document_id"]

    listed = await workflow.list_documents(session_id)
    activated = await workflow.activate_document(
        ActivateDocumentRequest(session_id=session_id, document_id=part_id)
    )

    assert listed.status is OperationStatus.SUCCEEDED
    assert [document["document_id"] for document in listed.data["documents"]] == [
        part_id,
        assembly_id,
    ]
    assert listed.data["active_document_id"] == assembly_id
    assert activated.status is OperationStatus.SUCCEEDED
    assert activated.data["document"]["document_id"] == part_id
    assert workflow.adapter.activation_names[-1] == "DocumentSwitchPart"


@pytest.mark.asyncio
async def test_sheet_metal_feature_geometry_issues_only_current_face_ids(
    tmp_path: Path,
) -> None:
    """Flat-pattern and bend faces remain server-bound topology capabilities."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="SheetMetalFixture")
    )
    document_id = part.data["document"]["document_id"]

    result = await workflow.list_sheet_metal_feature_geometry(
        ListSheetMetalFeatureGeometryRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )

    assert result.status is OperationStatus.SUCCEEDED
    assert result.data["total"] == 2
    assert result.data["features"] == [
        {
            "display_name": "Flat-Pattern1",
            "feature_kind": "flat_pattern",
            "face_ids": [result.data["features"][0]["face_ids"][0]],
        },
        {
            "display_name": "Edge-Flange1",
            "feature_kind": "bend",
            "face_ids": [result.data["features"][1]["face_ids"][0]],
        },
    ]
    assert all(
        face_id.startswith("part_topology_")
        for feature in result.data["features"]
        for face_id in feature["face_ids"]
    )
    assert "persistent_reference" not in str(result.data)


@pytest.mark.asyncio
async def test_sheet_metal_bend_notes_and_table_are_verified_mutations(
    tmp_path: Path,
) -> None:
    """Bend annotations and tables mutate a part only through typed operations."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="BendAnnotationFixture")
    )
    document_id = part.data["document"]["document_id"]

    notes = await workflow.insert_sheet_metal_bend_notes(
        InsertSheetMetalBendNotesRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )
    table = await workflow.insert_sheet_metal_bend_table(
        InsertSheetMetalBendTableRequest(
            session_id=session_id,
            document_id=document_id,
            x=25,
            y=10,
            start_value=3,
        )
    )

    assert notes.status is OperationStatus.SUCCEEDED
    assert notes.data["bend_notes"] == {"flat_pattern_feature_name": "Flat-Pattern1"}
    assert notes.data["document"]["revision"] == 2
    assert table.status is OperationStatus.SUCCEEDED
    assert table.data["bend_table"] == {
        "display_name": "Bend Table1",
        "annotation_count": 1,
    }
    assert table.data["document"]["revision"] == 3


@pytest.mark.asyncio
async def test_part_bounding_box_and_equation_tools_return_typed_readbacks(
    workflow: MvpWorkflow,
) -> None:
    """Part inspection and equation writes expose only verified typed values."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="EquationFixture")
    )
    document_id = part.data["document"]["document_id"]

    bounding_box = await workflow.get_bounding_box(
        GetBoundingBoxRequest(session_id=session_id, document_id=document_id)
    )
    equations = await workflow.list_equations(
        ListEquationsRequest(
            session_id=session_id,
            document_id=document_id,
            limit=1,
            offset=1,
        )
    )
    existing = await workflow.get_equation(
        GetEquationRequest(
            session_id=session_id,
            document_id=document_id,
            name="D1@Sketch1",
        )
    )
    updated = await workflow.set_equation(
        SetEquationRequest(
            session_id=session_id,
            document_id=document_id,
            name="PlateWidth",
            expression="120mm",
        )
    )
    created = await workflow.set_equation(
        SetEquationRequest(
            session_id=session_id,
            document_id=document_id,
            name="Clearance",
            expression="0.25mm",
        )
    )
    deleted = await workflow.delete_equation(
        DeleteEquationRequest(
            session_id=session_id,
            document_id=document_id,
            name="Clearance",
        )
    )

    assert bounding_box.status is OperationStatus.SUCCEEDED
    assert bounding_box.data["bounding_box"]["size_mm"] == [100.0, 50.0, 25.0]
    assert equations.status is OperationStatus.SUCCEEDED
    assert equations.data["total"] == 2
    assert equations.data["equations"] == [
        {
            "index": 1,
            "name": "D1@Sketch1",
            "expression": '"PlateWidth" / 2',
            "is_global_variable": False,
        }
    ]
    assert existing.status is OperationStatus.SUCCEEDED
    assert existing.data["equation"]["name"] == "D1@Sketch1"
    assert updated.status is OperationStatus.SUCCEEDED
    assert updated.data["equation"]["expression"] == "120mm"
    assert created.status is OperationStatus.SUCCEEDED
    assert created.data["equation"]["is_global_variable"] is True
    assert deleted.status is OperationStatus.SUCCEEDED
    assert deleted.data["equation"]["name"] == "Clearance"
    assert deleted.data["document"]["revision"] == 4


@pytest.mark.asyncio
async def test_part_bounding_box_and_equation_tools_reject_non_part_documents(
    workflow: MvpWorkflow,
) -> None:
    """Part-only geometry and equation APIs never invoke an assembly adapter call."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="EquationFixture")
    )
    document_id = assembly.data["document"]["document_id"]

    bounding_box = await workflow.get_bounding_box(
        GetBoundingBoxRequest(session_id=session_id, document_id=document_id)
    )
    equations = await workflow.list_equations(
        ListEquationsRequest(session_id=session_id, document_id=document_id)
    )

    assert bounding_box.status is OperationStatus.FAILED
    assert bounding_box.error is not None
    assert bounding_box.error.code == "SW_PART_REQUIRED"
    assert equations.status is OperationStatus.FAILED
    assert equations.error is not None
    assert equations.error.code == "SW_PART_REQUIRED"


def test_equation_request_rejects_ambiguous_assignment_syntax() -> None:
    """The server constructs the equation assignment and rejects a second equals sign."""
    with pytest.raises(ValueError, match="expression"):
        SetEquationRequest(
            session_id="session-1",
            document_id="document-1",
            name="Clearance",
            expression="x = 0.25mm",
        )


@pytest.mark.asyncio
async def test_part_body_and_material_tools_reject_non_part_documents(
    workflow: MvpWorkflow,
) -> None:
    """Part-only API calls do not reach an assembly document."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="Fixture")
    )
    document_id = assembly.data["document"]["document_id"]

    bodies = await workflow.list_solid_bodies(
        ListSolidBodiesRequest(session_id=session_id, document_id=document_id)
    )
    material = await workflow.get_material_assignment(
        GetMaterialAssignmentRequest(
            session_id=session_id,
            document_id=document_id,
        )
    )

    assert bodies.status is OperationStatus.FAILED
    assert bodies.error is not None and bodies.error.code == "SW_PART_REQUIRED"
    assert material.status is OperationStatus.FAILED
    assert material.error is not None and material.error.code == "SW_PART_REQUIRED"


@pytest.mark.asyncio
async def test_activate_configuration_updates_document_revision(
    workflow: MvpWorkflow,
) -> None:
    """Configuration activation is document-scoped and produces a new revision."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="Bracket")
    )
    document_id = part.data["document"]["document_id"]

    activated = await workflow.activate_configuration(
        ActivateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Fabrication",
        )
    )

    assert activated.status is OperationStatus.SUCCEEDED
    assert activated.data["configuration"]["name"] == "Fabrication"
    assert activated.data["document"]["configuration"] == "Fabrication"
    assert activated.data["document"]["revision"] == 2
    assert workflow.adapter.configuration_activations == ["Fabrication"]


@pytest.mark.asyncio
async def test_create_configuration_updates_document_revision_and_active_name(
    workflow: MvpWorkflow,
) -> None:
    """Configuration creation is verified and recorded as the active revision."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="Bracket")
    )
    document_id = part.data["document"]["document_id"]

    created = await workflow.create_configuration(
        CreateConfigurationRequest(
            session_id=session_id,
            document_id=document_id,
            configuration_name="Manufacturing",
            description="Welded construction",
        )
    )

    assert created.status is OperationStatus.SUCCEEDED
    assert created.data["configuration"] == {"name": "Manufacturing"}
    assert created.data["document"]["configuration"] == "Manufacturing"
    assert created.data["document"]["revision"] == 2
    assert workflow.adapter.configuration_creations == [
        ("Manufacturing", "Welded construction")
    ]


@pytest.mark.asyncio
async def test_document_catalog_tools_reject_a_stale_document(
    workflow: MvpWorkflow,
) -> None:
    """Feature/configuration operations do not accept a document owned by another session."""
    owner = await workflow.create_session()
    other = await workflow.create_session()
    owner_id = owner.data["session"]["session_id"]
    other_id = other.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=owner_id, name="Owned")
    )

    result = await workflow.list_configurations(
        ListConfigurationsRequest(
            session_id=other_id,
            document_id=part.data["document"]["document_id"],
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_DOCUMENT_NOT_ACTIVE"


def test_activate_configuration_request_rejects_path_like_names() -> None:
    """Configuration names remain identifiers, never a source of file path input."""
    with pytest.raises(ValueError, match="configuration_name"):
        ActivateConfigurationRequest(
            session_id="session-1",
            document_id="document-1",
            configuration_name="..\\outside",
        )


def test_create_configuration_request_rejects_path_like_names() -> None:
    """New configuration names are identifiers, never file-system input."""
    with pytest.raises(ValueError, match="configuration_name"):
        CreateConfigurationRequest(
            session_id="session-1",
            document_id="document-1",
            configuration_name="..\\outside",
        )


@pytest.mark.asyncio
async def test_foreign_sketch_edit_mode_blocks_document_switches(
    tmp_path: Path,
) -> None:
    """One SolidWorks instance cannot safely edit sketches from two MCP sessions."""
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(), artifact_store=ArtifactStore(tmp_path)
    )
    first = await workflow.create_session()
    second = await workflow.create_session()
    first_id = first.data["session"]["session_id"]
    second_id = second.data["session"]["session_id"]
    first_part = await workflow.create_part(
        CreatePartRequest(session_id=first_id, name="First")
    )
    await workflow.create_sketch(
        CreateSketchRequest(
            session_id=first_id,
            document_id=first_part.data["document"]["document_id"],
            plane="Front",
        )
    )

    blocked = await workflow.create_part(
        CreatePartRequest(session_id=second_id, name="Second")
    )

    assert blocked.status is OperationStatus.FAILED
    assert blocked.error is not None
    assert blocked.error.code == "SW_SKETCH_CONTEXT_LOCKED"


@pytest.mark.asyncio
async def test_create_part_rechecks_foreign_sketch_after_acquiring_com_lock(
    tmp_path: Path,
) -> None:
    """A sketch entered after the first check still blocks global document creation."""
    adapter = WorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    checks = 0

    def sketch_check(_session: object) -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    workflow._foreign_sketch_context_locked = sketch_check  # type: ignore[method-assign]
    result = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="BlockedPart")
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_SKETCH_CONTEXT_LOCKED"
    assert checks == 2
    assert adapter.active_model_name is None


@pytest.mark.asyncio
async def test_stateful_sessions_reject_connection_pool_adapters(
    tmp_path: Path,
) -> None:
    """A pooled adapter cannot preserve the document context across typed calls."""
    workflow = MvpWorkflow(
        adapter=ConnectionPoolAdapter(adapter_factory=WorkflowAdapter, pool_size=2),
        artifact_store=ArtifactStore(tmp_path),
    )

    result = await workflow.create_session()

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_STATEFUL_SESSION_POOLING_UNSUPPORTED"


@pytest.mark.asyncio
async def test_close_session_releases_bounded_session_capacity(tmp_path: Path) -> None:
    """Closing an unused session frees its bounded in-memory workflow slot."""
    workflow = MvpWorkflow(
        adapter=WorkflowAdapter(),
        artifact_store=ArtifactStore(tmp_path),
        max_sessions=1,
    )
    first = await workflow.create_session()

    capacity_error = await workflow.create_session()
    closed = await workflow.close_session(first.data["session"]["session_id"])
    replacement = await workflow.create_session()

    assert capacity_error.status is OperationStatus.FAILED
    assert capacity_error.error is not None
    assert capacity_error.error.code == "SW_SESSION_CAPACITY_REACHED"
    assert closed.status is OperationStatus.SUCCEEDED
    assert replacement.status is OperationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_open_model_does_not_expose_adapter_error_details(tmp_path: Path) -> None:
    """Raw COM errors are not returned to MCP clients as actionable suggestions."""
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    (input_root / "bracket.sldprt").write_bytes(b"input model")
    workflow = MvpWorkflow(
        adapter=RejectingOpenWorkflowAdapter(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        model_input_store=ModelInputStore(input_root),
    )
    session = await workflow.create_session()

    result = await workflow.open_model(
        OpenModelRequest(
            session_id=session.data["session"]["session_id"],
            file_name="bracket.sldprt",
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SW_ADAPTER_FAILURE"
    assert "private" not in result.error.suggestion


@pytest.mark.parametrize(
    "file_name", ["..\\bracket.sldprt", "bracket.dxf", "NUL.sldprt", "bad\x00.sldprt"]
)
def test_open_model_request_rejects_untrusted_file_names(file_name: str) -> None:
    """Opening a model accepts only a trusted supported leaf filename."""
    with pytest.raises(ValueError):
        OpenModelRequest(session_id="session-1", file_name=file_name)


@pytest.mark.asyncio
async def test_extrusion_requires_the_session_last_exited_sketch(
    workflow: MvpWorkflow,
) -> None:
    """The workflow rejects stale selections instead of relying on UI state."""
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]
    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="Bracket")
    )
    document_id = part.data["document"]["document_id"]

    result = await workflow.create_extrusion(
        CreateExtrusionRequest(
            session_id=session_id,
            document_id=document_id,
            sketch_id="unknown-sketch",
            depth=10,
        )
    )

    assert result.status is OperationStatus.FAILED
    assert result.error.code == "SW_SKETCH_NOT_READY"
    assert "create_sketch" in result.error.suggestion


@pytest.mark.asyncio
async def test_adapter_calls_are_serialized_across_sessions(tmp_path: Path) -> None:
    """Separate MCP sessions cannot interleave work against one COM instance."""
    adapter = SerialWorkflowAdapter()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    first = await workflow.create_session()
    second = await workflow.create_session()

    results = await asyncio.gather(
        workflow.create_part(
            CreatePartRequest(
                session_id=first.data["session"]["session_id"], name="First"
            )
        ),
        workflow.create_part(
            CreatePartRequest(
                session_id=second.data["session"]["session_id"], name="Second"
            )
        ),
    )

    assert all(result.status is OperationStatus.SUCCEEDED for result in results)
    assert adapter.maximum_active_calls == 1


def test_export_request_rejects_path_traversal() -> None:
    """Exports accept artifact names, not caller-controlled paths."""
    with pytest.raises(ValueError, match="artifact_name"):
        ExportFileRequest(
            session_id="session-1",
            document_id="document-1",
            artifact_name="..\\outside",
            format_type="step",
        )


@pytest.mark.asyncio
async def test_sheet_metal_and_drawing_delivery_workflow_uses_safe_artifacts_and_targets(
    tmp_path: Path,
) -> None:
    """Flat DXF, BOM, and all four drawing annotations work through opaque IDs."""
    adapter = MockSolidWorksAdapter(
        {
            "mock_connect_delay": 0,
            "mock_model_delay": 0,
            "mock_feature_delay": 0,
            "mock_sketch_delay": 0,
        }
    )
    await adapter.connect()
    workflow = MvpWorkflow(adapter=adapter, artifact_store=ArtifactStore(tmp_path))
    session = await workflow.create_session()
    session_id = session.data["session"]["session_id"]

    part = await workflow.create_part(
        CreatePartRequest(session_id=session_id, name="Bracket")
    )
    part_document_id = part.data["document"]["document_id"]
    native_part = await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=part_document_id,
            artifact_name="bracket",
        )
    )
    sheet_metal = await workflow.get_sheet_metal_info(
        DocumentRequest(session_id=session_id, document_id=part_document_id)
    )
    flattened = await workflow.set_sheet_metal_flat_state(
        SheetMetalFlatStateRequest(
            session_id=session_id,
            document_id=part_document_id,
            flattened=True,
        )
    )
    dxf = await workflow.export_flat_pattern_dxf(
        ExportFlatPatternDxfRequest(
            session_id=session_id,
            document_id=part_document_id,
            artifact_name="bracket-flat.dxf",
        )
    )

    assert native_part.status is OperationStatus.SUCCEEDED
    assert sheet_metal.data["sheet_metal"]["is_sheet_metal"] is True
    assert flattened.data["sheet_metal"]["is_flattened"] is True
    assert dxf.status is OperationStatus.SUCCEEDED
    assert Path(dxf.data["artifact"]["path"]).suffix == ".dxf"

    assembly = await workflow.create_assembly(
        CreateAssemblyRequest(session_id=session_id, name="Bracket Assembly")
    )
    assembly_document_id = assembly.data["document"]["document_id"]
    native_assembly = await workflow.save_model(
        SaveModelRequest(
            session_id=session_id,
            document_id=assembly_document_id,
            artifact_name="bracket-assembly",
        )
    )
    drawing = await workflow.create_drawing(
        CreateDrawingRequest(session_id=session_id, name="Bracket Drawing")
    )
    drawing_document_id = drawing.data["document"]["document_id"]
    drawing_view = await workflow.create_drawing_view(
        CreateDrawingViewRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_model_document_id=assembly_document_id,
        )
    )
    bom = await workflow.create_drawing_bom(
        CreateDrawingBomRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            source_assembly_document_id=assembly_document_id,
            source_view_id=drawing_view.data["view"]["view_id"],
        )
    )
    note = await workflow.create_drawing_note(
        CreateDrawingNoteRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            text="REMOVE ALL BURRS",
        )
    )

    assert native_assembly.status is OperationStatus.SUCCEEDED
    assert bom.data["bom"]["row_count"] == 2
    assert note.data["annotation"]["annotation_type"] == "note"

    dimension_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["dimension"],
        )
    )
    dimension = await workflow.place_drawing_dimension(
        PlaceDrawingDimensionRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=dimension_targets.data["targets"][0]["target_id"],
        )
    )
    component_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["component"],
        )
    )
    balloon = await workflow.create_drawing_balloon(
        CreateDrawingBalloonRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=component_targets.data["targets"][0]["target_id"],
        )
    )
    edge_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["edge"],
        )
    )
    callout = await workflow.create_drawing_hole_callout(
        CreateDrawingHoleCalloutRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=edge_targets.data["targets"][0]["target_id"],
        )
    )

    assert dimension.data["annotation"]["annotation_type"] == "dimension"
    assert balloon.data["annotation"]["annotation_type"] == "balloon"
    assert callout.data["annotation"]["annotation_type"] == "hole_callout"

    ordinate_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["dimension"],
        )
    )
    ordinate = await workflow.create_drawing_ordinate_dimension(
        CreateDrawingOrdinateDimensionRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=ordinate_targets.data["targets"][0]["target_id"],
            ordinate_type="vertical",
        )
    )
    geometric_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["dimension"],
        )
    )
    geometric_tolerance = await workflow.create_drawing_geometric_tolerance(
        CreateDrawingGeometricToleranceRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=geometric_targets.data["targets"][0]["target_id"],
            tolerance="0.05",
            datum="A",
        )
    )
    surface_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["edge"],
        )
    )
    surface_finish = await workflow.create_drawing_surface_finish(
        CreateDrawingSurfaceFinishRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=surface_targets.data["targets"][0]["target_id"],
            value="1.6",
        )
    )
    weld_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["edge"],
        )
    )
    weld_symbol = await workflow.create_drawing_weld_symbol(
        CreateDrawingWeldSymbolRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=weld_targets.data["targets"][0]["target_id"],
            weld_type="Fillet",
            size="5",
        )
    )
    center_targets = await workflow.list_drawing_annotation_targets(
        ListDrawingAnnotationTargetsRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            view_id=drawing_view.data["view"]["view_id"],
            target_types=["edge"],
        )
    )
    center_mark = await workflow.create_drawing_center_mark(
        CreateDrawingCenterMarkRequest(
            session_id=session_id,
            document_id=drawing_document_id,
            target_id=center_targets.data["targets"][0]["target_id"],
        )
    )

    assert ordinate.data["annotation"]["annotation_type"] == "ordinate_dimension"
    assert geometric_tolerance.data["annotation"]["annotation_type"] == "geometric_tolerance"
    assert surface_finish.data["annotation"]["annotation_type"] == "surface_finish"
    assert weld_symbol.data["annotation"]["annotation_type"] == "weld_symbol"
    assert center_mark.data["annotation"]["annotation_type"] == "center_mark"
