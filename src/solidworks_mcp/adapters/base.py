"""Base adapter interface for SolidWorks integration.

Defines the common interface that all SolidWorks adapters must implement, following the
adapter pattern from the original TypeScript implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


# Stable adapter error codes consumed by workflow-level MCP responses. These
# describe known SolidWorks API capability limits rather than transient COM errors.
ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED = "SW_PROJECTED_VIEW_UNSUPPORTED"
ADAPTER_ERROR_CHAMFER_EDGE_SELECTION = "SW_CHAMFER_EDGE_SELECTION_FAILED"
ADAPTER_ERROR_CHAMFER_SELECTION_COUNT = "SW_CHAMFER_SELECTION_COUNT_MISMATCH"
ADAPTER_ERROR_CHAMFER_CREATION = "SW_CHAMFER_CREATION_FAILED"


class AdapterHealth(BaseModel):
    """Health status information for adapters.

    Attributes:
        average_response_time (float): The average response time value.
        connection_status (str): The connection status value.
        error_count (int): The error count value.
        healthy (bool): The healthy value.
        last_check (datetime): The last check value.
        metrics (dict[str, Any] | None): The metrics value.
        success_count (int): The success count value.
    """

    healthy: bool
    last_check: datetime
    error_count: int
    success_count: int
    average_response_time: float
    connection_status: str
    metrics: dict[str, Any] | None = None

    def __getitem__(self, key: str) -> Any:
        """Build internal getitem.

        Args:
            key (str): The key value.

        Returns:
            Any: The result produced by the operation.
        """
        if key == "status":
            return "healthy" if self.healthy else "unhealthy"
        if key == "connected":
            return self.connection_status == "connected"
        if key == "adapter_type":
            return (self.metrics or {}).get("adapter_type")
        if key == "version":
            return (self.metrics or {}).get("version", "mock-1.0")
        if key == "uptime":
            return (self.metrics or {}).get("uptime", 0.0)
        return self.model_dump().get(key)

    def __contains__(self, key: str) -> bool:
        """Build internal contains.

        Args:
            key (str): The key value.

        Returns:
            bool: True if contains, otherwise False.
        """
        legacy_keys = {"status", "connected", "adapter_type", "version", "uptime"}
        if key in legacy_keys:
            return True
        return key in self.model_dump()


class AdapterResultStatus(StrEnum):
    """Result status for adapter operations.

    Attributes:
        ERROR (Any): The error value.
        SUCCESS (Any): The success value.
        TIMEOUT (Any): The timeout value.
        WARNING (Any): The warning value.
    """

    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    TIMEOUT = "timeout"


@dataclass
class AdapterResult(Generic[T]):
    """Result wrapper for adapter operations.

    Attributes:
        data (T | None): The data value.
        error (str | None): The error value.
        execution_time (float | None): The execution time value.
        metadata (dict[str, Any] | None): The metadata value.
        status (AdapterResultStatus): The status value.
    """

    status: AdapterResultStatus
    data: T | None = None
    error: str | None = None
    execution_time: float | None = None
    metadata: dict[str, Any] | None = None

    @property
    def is_success(self) -> bool:
        """Check if operation was successful.

        Returns:
            bool: True if success, otherwise False.
        """
        return self.status == AdapterResultStatus.SUCCESS

    @property
    def is_error(self) -> bool:
        """Check if operation had an error.

        Returns:
            bool: True if error, otherwise False.
        """
        return self.status == AdapterResultStatus.ERROR


# SolidWorks data models
class SolidWorksModel(BaseModel):
    """SolidWorks model information.

    Attributes:
        configuration (str | None): The configuration value.
        is_active (bool): The is active value.
        name (str): The name value.
        path (str): The path value.
        properties (dict[str, Any] | None): The properties value.
        type (str): The type value.
    """

    path: str
    name: str
    type: str  # "Part", "Assembly", "Drawing"
    is_active: bool
    configuration: str | None = None
    properties: dict[str, Any] | None = None

    def __getitem__(self, key: str) -> Any:
        """Build internal getitem.

        Args:
            key (str): The key value.

        Returns:
            Any: The result produced by the operation.
        """
        if key == "title":
            return self.name
        if key == "units":
            return (self.properties or {}).get("units")
        return self.model_dump().get(key)


class SolidWorksFeature(BaseModel):
    """SolidWorks feature information.

    Attributes:
        id (str | None): The id value.
        name (str): The name value.
        parameters (dict[str, Any] | None): The parameters value.
        parent (str | None): The parent value.
        properties (dict[str, Any] | None): The properties value.
        type (str): The type value.
    """

    name: str
    type: str
    id: str | None = None
    parent: str | None = None
    properties: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None

    def __getitem__(self, key: str) -> Any:
        """Build internal getitem.

        Args:
            key (str): The key value.

        Returns:
            Any: The result produced by the operation.
        """
        if self.parameters and key in self.parameters:
            return self.parameters.get(key)
        return self.model_dump().get(key)


class ExtrusionParameters(BaseModel):
    """Parameters for extrusion operations.

    Attributes:
        auto_select (bool): The auto select value.
        both_directions (bool): The both directions value.
        depth (float): The depth value.
        draft_angle (float): The draft angle value.
        end_condition (str): The end condition value.
        feature_scope (bool): The feature scope value.
        merge_result (bool): The merge result value.
        is_cut (bool): Whether the revolve removes material instead of adding it.
        reverse_direction (bool): The reverse direction value.
        thin_feature (bool): The thin feature value.
        thin_thickness (float | None): The thin thickness value.
        up_to_surface (str | None): The up to surface value.
    """

    depth: float
    draft_angle: float = 0.0
    reverse_direction: bool = False
    both_directions: bool = False
    thin_feature: bool = False
    thin_thickness: float | None = None
    auto_fillet_corners: bool = False
    fillet_corners_radius: float = 0.0
    end_condition: str = "Blind"
    up_to_surface: str | None = None
    merge_result: bool = True
    feature_scope: bool = False
    auto_select: bool = True


class RevolveParameters(BaseModel):
    """Parameters for revolve operations.

    Attributes:
        angle (float): The angle value.
        both_directions (bool): The both directions value.
        merge_result (bool): The merge result value.
        reverse_direction (bool): The reverse direction value.
        thin_feature (bool): The thin feature value.
        thin_thickness (float | None): The thin thickness value.
    """

    angle: float
    reverse_direction: bool = False
    both_directions: bool = False
    thin_feature: bool = False
    thin_thickness: float | None = None
    merge_result: bool = True
    is_cut: bool = False


class ReferencePlaneParameters(BaseModel):
    """Parameters for an offset reference plane based on a standard plane."""

    base_plane: str
    offset: float
    reverse_direction: bool = False


class OriginPlaneParameters(BaseModel):
    """Parameters identifying one standard origin plane in the active part."""

    plane: str


class ReferenceAxisParameters(BaseModel):
    """Parameters identifying one standard origin axis in the active part."""

    axis: str


class FeatureLinearPatternParameters(BaseModel):
    """Parameters for a one-direction linear pattern of one feature."""

    feature_name: str
    direction: str
    instances: int
    spacing: float
    reverse_direction: bool = False


class FeatureCircularPatternParameters(BaseModel):
    """Parameters for a circular pattern of one feature about a named axis."""

    feature_name: str
    axis_name: str
    instances: int
    angle_degrees: float
    reverse_direction: bool = False


class FeatureMirrorParameters(BaseModel):
    """Parameters for mirroring one feature about a named reference plane."""

    feature_name: str
    plane_name: str


class HoleWizardParameters(BaseModel):
    """Controlled parameters for an ANSI Metric M6 clearance Hole Wizard."""

    location_sketch_name: str
    diameter: float
    depth: float


class ThreadedHoleParameters(BaseModel):
    """Parameters for a standard ANSI Metric tapped Hole Wizard feature."""

    location_sketch_name: str
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
    depth: float
    through_all: bool = False


class SweepParameters(BaseModel):
    """Parameters for sweep operations.

    Attributes:
        merge_result (bool): The merge result value.
        path (str): The path value.
        profile (str | None): Optional explicitly selected profile sketch name.
        twist_along_path (bool): The twist along path value.
        twist_angle (float): The twist angle value.
        is_cut (bool): Remove material instead of adding it.
    """

    path: str
    profile: str | None = None
    twist_along_path: bool = False
    twist_angle: float = 0.0
    merge_result: bool = True
    is_cut: bool = False


class LoftParameters(BaseModel):
    """Parameters for loft operations.

    Attributes:
        end_tangent (str | None): The end tangent value.
        guide_curves (list[str] | None): The guide curves value.
        merge_result (bool): The merge result value.
        profiles (list[str]): The profiles value.
        start_tangent (str | None): The start tangent value.
        is_cut (bool): Remove material instead of adding it.
    """

    profiles: list[str]
    guide_curves: list[str] | None = None
    start_tangent: str | None = None
    end_tangent: str | None = None
    merge_result: bool = True
    is_cut: bool = False


class MassProperties(BaseModel):
    """Mass properties information.

    Attributes:
        center_of_mass (list[float]): The center of mass value.
        mass (float): The mass value.
        moments_of_inertia (dict[str, float]): The moments of inertia value.
        principal_axes (dict[str, list[float]] | None): The principal axes value.
        surface_area (float): The surface area value.
        volume (float): The volume value.
    """

    volume: float
    surface_area: float
    mass: float
    center_of_mass: list[float]  # [x, y, z]
    moments_of_inertia: dict[str, float]
    principal_axes: dict[str, list[float]] | None = None


class SolidWorksAdapter(ABC):
    """Base adapter interface for SolidWorks integration.

    Args:
        config (object | None): Configuration values for the operation. Defaults to None.

    Attributes:
        _metrics (Any): The metrics value.
        config (Any): The config value.
        config_dict (Any): The config dict value.
    """

    def __init__(self, config: object | None = None):
        """Initialize adapter with configuration.

        Args:
            config (object | None): Configuration values for the operation. Defaults to None.
        """
        if config is None:
            normalized_config: dict[str, Any] = {}
        elif isinstance(config, Mapping):
            normalized_config = dict(config)
        elif hasattr(config, "model_dump"):
            normalized_config = dict(config.model_dump())
        else:
            normalized_config = {}

        # Preserve original config object for compatibility with tests and
        # call sites that compare object identity/equality.
        self.config = config
        # Keep a normalized mapping for adapter internals.
        self.config_dict = normalized_config
        self._metrics = {
            "operations_count": 0,
            "errors_count": 0,
            "average_response_time": 0.0,
        }
        # SolidWorks-as-Code session logging. Set soc_session_id to enable
        # automatic ToolCallRecord writes for every adapter operation.
        self.soc_session_id: str | None = None
        self.soc_db_path: Path | None = None

    # Connection Management
    @abstractmethod
    async def connect(self) -> None:
        """Connect to SolidWorks application.

        Returns:
            None: None.
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from SolidWorks application.

        Returns:
            None: None.
        """
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to SolidWorks.

        Returns:
            bool: True if connected, otherwise False.
        """
        pass

    @abstractmethod
    async def health_check(self) -> AdapterHealth:
        """Get adapter health status.

        Returns:
            AdapterHealth: The result produced by the operation.
        """
        pass

    # Model Operations
    @abstractmethod
    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        """Open a SolidWorks model (part, assembly, or drawing).

        Args:
            file_path (str): Path to the target file.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        pass

    async def activate_model(
        self, name: str, path: str | None = None
    ) -> AdapterResult[None]:
        """Activate one already-open model by its trusted title or path.

        Stateful workflow tools call this before operating on a tracked document.
        Adapters that cannot select a previously opened document must return an
        error rather than applying an operation to an arbitrary active document.
        """
        _ = name, path
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="activate_model is not implemented by this adapter",
        )

    @abstractmethod
    async def close_model(self, save: bool = False) -> AdapterResult[None]:
        """Close the current model.

        Args:
            save (bool): The save value. Defaults to False.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        pass

    async def close_all_session_docs(self) -> AdapterResult[None]:
        """Close documents owned by this adapter session, if it tracks them.

        Implementations must never terminate the SolidWorks application as part
        of this cleanup. Adapters that do not track document ownership return a
        warning so server shutdown remains safe across all adapter types.
        """
        return AdapterResult(
            status=AdapterResultStatus.WARNING,
            error="This adapter does not track session-owned documents",
        )

    async def save_file(self, file_path: str | None = None) -> AdapterResult[Any]:
        """Save the active model to the existing path or the provided path.

        Args:
            file_path (str | None): Path to the target file. Defaults to None.

        Returns:
            AdapterResult[Any]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="save_file is not implemented by this adapter",
        )

    @abstractmethod
    async def get_model_info(self) -> AdapterResult[dict[str, Any]]:
        """Get metadata for the active model.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def list_features(
        self, include_suppressed: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List model features from the feature tree.

        Args:
            include_suppressed (bool): The include suppressed value. Defaults to False.

        Returns:
            AdapterResult[list[dict[str, Any]]]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def list_configurations(self) -> AdapterResult[list[str]]:
        """List configuration names for the active model.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        pass

    async def list_solid_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List solid bodies in the active part document.

        Args:
            include_hidden: Include bodies not visible in the current configuration.

        Returns:
            Serializable body summaries, or an explicit unsupported error.
        """
        _ = include_hidden
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_solid_bodies is not implemented by this adapter",
        )

    async def list_part_topology(
        self, entity_types: tuple[str, ...] = ("face", "edge")
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List internal persistent references for faces and edges in an active part."""
        _ = entity_types
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_part_topology is not implemented by this adapter",
        )

    async def list_sheet_metal_feature_geometry(
        self,
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List persistent face references associated with sheet-metal bends."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_sheet_metal_feature_geometry is not implemented by this adapter",
        )

    async def insert_sheet_metal_bend_notes(self) -> AdapterResult[dict[str, Any]]:
        """Insert bend notes using the active part's own flat-pattern feature."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="insert_sheet_metal_bend_notes is not implemented by this adapter",
        )

    async def insert_sheet_metal_bend_table(
        self, x_m: float, y_m: float, start_value: int
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a default-template bend table at a controlled model position."""
        _ = x_m, y_m, start_value
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="insert_sheet_metal_bend_table is not implemented by this adapter",
        )

    async def list_surface_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List surface bodies in the active part document."""
        _ = include_hidden
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_surface_bodies is not implemented by this adapter",
        )

    async def get_material_assignment(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, str]]:
        """Read material metadata for one active part configuration."""
        _ = configuration_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_material_assignment is not implemented by this adapter",
        )

    async def set_material_assignment(
        self, configuration_name: str, database_name: str, material_name: str
    ) -> AdapterResult[dict[str, str]]:
        """Assign and verify a material for one active part configuration."""
        _ = configuration_name, database_name, material_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="set_material_assignment is not implemented by this adapter",
        )

    async def create_configuration(
        self, configuration_name: str, description: str = ""
    ) -> AdapterResult[str]:
        """Create and activate one independent configuration on the active model."""
        _ = configuration_name, description
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_configuration is not implemented by this adapter",
        )

    async def delete_configuration(self, configuration_name: str) -> AdapterResult[str]:
        """Delete one non-active model configuration after adapter-side verification."""
        _ = configuration_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="delete_configuration is not implemented by this adapter",
        )

    async def activate_configuration(
        self, configuration_name: str
    ) -> AdapterResult[str]:
        """Activate an existing configuration on the active model.

        Adapters that cannot safely switch configurations return an explicit
        unsupported result instead of relying on UI selection state.

        Args:
            configuration_name (str): Existing SolidWorks configuration name.

        Returns:
            AdapterResult[str]: Verified active configuration name on success.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="activate_configuration is not implemented by this adapter",
        )

    @abstractmethod
    async def create_part(
        self, name: str | None = None, units: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create a new part document.

        Args:
            name (str | None): The name value. Defaults to None.
            units (str | None): The units value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def create_assembly(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create a new assembly document.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def create_drawing(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create a new drawing document.

        Args:
            name (str | None): The name value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        pass

    async def insert_assembly_component(
        self,
        component_path: str,
        x_m: float,
        y_m: float,
        z_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert one saved component into the active assembly in metres.

        Implementations must reject calls when the active document is not an
        assembly. The returned mapping contains only a stable component ID,
        display name, and referenced document type.
        """
        _ = component_path, x_m, y_m, z_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="insert_assembly_component is not implemented by this adapter",
        )

    async def list_assembly_components(self) -> AdapterResult[list[dict[str, Any]]]:
        """List client-safe component summaries from the active assembly."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_assembly_components is not implemented by this adapter",
        )

    async def check_assembly_interference(
        self,
        include_coincident: bool,
        include_multibody_part_interferences: bool,
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Run native interference detection for every component in an assembly."""
        _ = include_coincident, include_multibody_part_interferences
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="check_assembly_interference is not implemented by this adapter",
        )

    async def get_assembly_component(
        self, component_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Look up one active-assembly component by its display name."""
        _ = component_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_assembly_component is not implemented by this adapter",
        )

    async def list_assembly_mate_entities(
        self,
        component_id: str | None = None,
        entity_types: tuple[str, ...] = ("face", "edge"),
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List internal persistent references for geometry in the active assembly.

        Returned rows are consumed by the workflow only. Implementations must
        never expose COM objects and must include a persistent reference token.
        """
        _ = component_id, entity_types
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_assembly_mate_entities is not implemented by this adapter",
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
    ) -> AdapterResult[dict[str, Any]]:
        """Create one active-assembly mate from internal persistent references."""
        _ = (
            first_persistent_reference,
            second_persistent_reference,
            mate_type,
            alignment,
            flip,
            distance_m,
            angle_radians,
            for_positioning_only,
            lock_rotation,
        )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_assembly_mate is not implemented by this adapter",
        )

    async def list_assembly_mates(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-assembly mate references for safe enumeration and deletion."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_assembly_mates is not implemented by this adapter",
        )

    async def delete_assembly_mate(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one active-assembly mate identified by an internal token."""
        _ = persistent_reference
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="delete_assembly_mate is not implemented by this adapter",
        )

    async def create_drawing_view(
        self,
        model_path: str,
        model_view_name: str,
        x_m: float,
        y_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Create one standard model view in the active drawing in metres.

        The workflow layer owns source-path authorization and passes only a
        server-controlled native model path plus a fixed SolidWorks view name.
        """
        _ = model_path, model_view_name, x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_drawing_view is not implemented by this adapter",
        )

    async def list_drawing_views(self) -> AdapterResult[list[dict[str, Any]]]:
        """List client-safe model-view summaries from the active drawing."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_drawing_views is not implemented by this adapter",
        )

    async def activate_drawing_view(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one current drawing view by its server-issued view ID."""
        _ = view_id
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="activate_drawing_view is not implemented by this adapter",
        )

    async def list_drawing_sheets(self) -> AdapterResult[list[dict[str, Any]]]:
        """List current drawing sheets without changing the active sheet."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_drawing_sheets is not implemented by this adapter",
        )

    async def activate_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one current drawing sheet by exact name."""
        _ = sheet_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="activate_drawing_sheet is not implemented by this adapter",
        )

    async def create_drawing_sheet(
        self,
        sheet_name: str,
        paper_size: str,
        width_m: float,
        height_m: float,
        first_angle_projection: bool,
        scale_numerator: int,
        scale_denominator: int,
        zone_rows: int,
        zone_columns: int,
    ) -> AdapterResult[dict[str, Any]]:
        """Create and activate one drawing sheet in the active drawing document."""
        _ = (
            sheet_name,
            paper_size,
            width_m,
            height_m,
            first_angle_projection,
            scale_numerator,
            scale_denominator,
            zone_rows,
            zone_columns,
        )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_drawing_sheet is not implemented by this adapter",
        )

    async def delete_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one current drawing sheet, never the final sheet."""
        _ = sheet_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="delete_drawing_sheet is not implemented by this adapter",
        )

    async def rename_drawing_sheet(
        self, sheet_name: str, new_sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Rename one current drawing sheet through a typed sheet API."""
        _ = sheet_name, new_sheet_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="rename_drawing_sheet is not implemented by this adapter",
        )

    async def reorder_drawing_sheets(
        self, sheet_names: list[str]
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Replace the full current drawing-sheet order."""
        _ = sheet_names
        return AdapterResult(status=AdapterResultStatus.ERROR, error="reorder_drawing_sheets is not implemented by this adapter")

    async def get_drawing_view_outline(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one active-drawing view's axis-aligned layout outline in metres."""
        _ = view_id
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_drawing_view_outline is not implemented by this adapter",
        )

    async def update_drawing_view_layout(
        self,
        view_id: str,
        x_m: float | None,
        y_m: float | None,
        scale_decimal: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Move and/or rescale one current drawing view by its server-issued ID."""
        _ = view_id, x_m, y_m, scale_decimal
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="update_drawing_view_layout is not implemented by this adapter",
        )

    async def align_drawing_views(
        self,
        view_ids: list[str],
        direction: str,
        gap_m: float,
        start_x_m: float | None,
        start_y_m: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Arrange several current drawing views from their outline dimensions."""
        _ = view_ids, direction, gap_m, start_x_m, start_y_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="align_drawing_views is not implemented by this adapter",
        )

    async def rebuild_drawing(self) -> AdapterResult[dict[str, Any]]:
        """Rebuild and save the active drawing document."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="rebuild_drawing is not implemented by this adapter",
        )

    async def create_projected_drawing_view(
        self, source_view_id: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create a projected view from a known active-drawing view in metres.

        Implementations must resolve ``source_view_id`` only inside the active
        drawing and return a safe view identifier/display-name summary.
        """
        _ = source_view_id, x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_projected_drawing_view is not implemented by this adapter",
        )

    async def create_auxiliary_drawing_view(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create an auxiliary view from one server-issued drawing edge reference."""
        _ = persistent_reference, x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_auxiliary_drawing_view is not implemented by this adapter",
        )

    async def create_section_drawing_view(
        self,
        source_view_id: str,
        cut_start_x_m: float,
        cut_start_y_m: float,
        cut_end_x_m: float,
        cut_end_y_m: float,
        x_m: float,
        y_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Create one section view from a known view and explicit sheet coordinates."""
        _ = (
            source_view_id,
            cut_start_x_m,
            cut_start_y_m,
            cut_end_x_m,
            cut_end_y_m,
            x_m,
            y_m,
        )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_section_drawing_view is not implemented by this adapter",
        )

    async def get_sheet_metal_info(self) -> AdapterResult[dict[str, Any]]:
        """Return active-part sheet-metal state without exposing COM objects."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_sheet_metal_info is not implemented by this adapter",
        )

    async def set_sheet_metal_flat_state(
        self, flattened: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Flatten or refold the active sheet-metal part."""
        _ = flattened
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="set_sheet_metal_flat_state is not implemented by this adapter",
        )

    async def export_flat_pattern_dxf(
        self, file_path: str, model_path: str, sheet_metal_options: int
    ) -> AdapterResult[dict[str, Any]]:
        """Export one saved sheet-metal part through the dedicated DXF API."""
        _ = file_path, model_path, sheet_metal_options
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="export_flat_pattern_dxf is not implemented by this adapter",
        )

    async def create_drawing_bom(
        self,
        source_view_id: str,
        bom_type: str,
        configuration_name: str,
        x_m: float,
        y_m: float,
        detailed_cut_list: bool,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert one BOM table into the active drawing from a known model view."""
        _ = (
            source_view_id,
            bom_type,
            configuration_name,
            x_m,
            y_m,
            detailed_cut_list,
        )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_drawing_bom is not implemented by this adapter",
        )

    async def update_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Refresh a drawing BOM table from its internal persistent reference."""
        _ = persistent_reference
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="update_drawing_bom is not implemented by this adapter",
        )

    async def delete_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete a drawing BOM table from its internal persistent reference."""
        _ = persistent_reference
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="delete_drawing_bom is not implemented by this adapter",
        )

    async def create_drawing_note(
        self, text: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Insert one positioned text note into the active drawing."""
        _ = text, x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_drawing_note is not implemented by this adapter",
        )

    async def list_drawing_annotation_targets(
        self, view_id: str, target_types: tuple[str, ...]
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List internal persistent references eligible for drawing annotations."""
        _ = view_id, target_types
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_drawing_annotation_targets is not implemented by this adapter",
        )

    async def place_drawing_dimension(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Place one selected display dimension on the active drawing sheet."""
        _ = persistent_reference, x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="place_drawing_dimension is not implemented by this adapter",
        )

    async def create_drawing_balloon(
        self,
        persistent_reference: str,
        x_m: float,
        y_m: float,
        style: str,
        text: str,
    ) -> AdapterResult[dict[str, Any]]:
        """Create one BOM balloon from an internal component reference."""
        _ = persistent_reference, x_m, y_m, style, text
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_drawing_balloon is not implemented by this adapter",
        )

    async def create_drawing_hole_callout(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create one automatic hole callout from an internal edge reference."""
        _ = persistent_reference, x_m, y_m
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_drawing_hole_callout is not implemented by this adapter",
        )

    async def create_drawing_ordinate_dimension(
        self, persistent_reference: str, x_m: float, y_m: float, ordinate_type: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create an ordinate dimension from an internal drawing target."""
        _ = persistent_reference, x_m, y_m, ordinate_type
        return AdapterResult(status=AdapterResultStatus.ERROR, error="create_drawing_ordinate_dimension is not implemented by this adapter")

    async def create_drawing_surface_finish(
        self, persistent_reference: str, x_m: float, y_m: float, symbol: str, value: str
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a surface-finish symbol on an internal drawing target."""
        _ = persistent_reference, x_m, y_m, symbol, value
        return AdapterResult(status=AdapterResultStatus.ERROR, error="create_drawing_surface_finish is not implemented by this adapter")

    async def create_drawing_weld_symbol(
        self, persistent_reference: str, x_m: float, y_m: float, weld_type: str, size: str
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a weld symbol on an internal drawing target."""
        _ = persistent_reference, x_m, y_m, weld_type, size
        return AdapterResult(status=AdapterResultStatus.ERROR, error="create_drawing_weld_symbol is not implemented by this adapter")

    async def create_drawing_geometric_tolerance(
        self, persistent_reference: str, x_m: float, y_m: float, tolerance: str, datum: str
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a geometric tolerance frame on an internal drawing target."""
        _ = persistent_reference, x_m, y_m, tolerance, datum
        return AdapterResult(status=AdapterResultStatus.ERROR, error="create_drawing_geometric_tolerance is not implemented by this adapter")

    async def create_drawing_center_mark(
        self, persistent_reference: str, size_m: float, show_connection_lines: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Create a center mark from an internal circular drawing-edge target."""
        _ = persistent_reference, size_m, show_connection_lines
        return AdapterResult(status=AdapterResultStatus.ERROR, error="create_drawing_center_mark is not implemented by this adapter")

    async def create_drawing_revision_table(
        self,
        x_m: float,
        y_m: float,
        anchor_type: int,
        revision: str,
    ) -> AdapterResult[dict[str, Any]]:
        """Create a revision table on the active drawing sheet."""
        _ = x_m, y_m, anchor_type, revision
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_drawing_revision_table is not implemented by this adapter",
        )

    async def list_drawing_revisions(
        self, persistent_reference: str
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List rows from one revision table identified by a persistent reference."""
        _ = persistent_reference
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_drawing_revisions is not implemented by this adapter",
        )

    async def add_drawing_revision(
        self, persistent_reference: str, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Add one revision row to a revision table."""
        _ = persistent_reference, revision
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_drawing_revision is not implemented by this adapter",
        )

    async def delete_drawing_revision(
        self, persistent_reference: str, revision_id: int, delete_symbols: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one revision row from a revision table."""
        _ = persistent_reference, revision_id, delete_symbols
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="delete_drawing_revision is not implemented by this adapter",
        )

    async def list_custom_properties(
        self, configuration_name: str | None = None
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List custom properties for the document or one active configuration."""
        _ = configuration_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_custom_properties is not implemented by this adapter",
        )

    async def set_custom_property(
        self, name: str, value: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Set one document or active-configuration custom property."""
        _ = name, value, configuration_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="set_custom_property is not implemented by this adapter",
        )

    async def delete_custom_property(
        self, name: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, str]]:
        """Delete one custom property from the document or active configuration."""
        _ = name, configuration_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="delete_custom_property is not implemented by this adapter",
        )

    # Feature Operations
    @abstractmethod
    async def create_extrusion(
        self, params: ExtrusionParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create an extrusion feature.

        Args:
            params (ExtrusionParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a revolve feature.

        Args:
            params (RevolveParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        pass

    async def create_reference_plane(
        self, params: ReferencePlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create an offset reference plane from a standard reference plane."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_reference_plane is not implemented by this adapter",
        )

    async def get_reference_axis(
        self, params: ReferenceAxisParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Resolve one standard origin axis without creating model geometry."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_reference_axis is not implemented by this adapter",
        )

    async def get_reference_plane(
        self, params: OriginPlaneParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Resolve one standard origin plane without creating model geometry."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_reference_plane is not implemented by this adapter",
        )

    async def create_feature_linear_pattern(
        self, params: FeatureLinearPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a linear pattern from one trusted feature and a standard direction."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_feature_linear_pattern is not implemented by this adapter",
        )

    async def create_feature_circular_pattern(
        self, params: FeatureCircularPatternParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a circular pattern from one trusted feature and origin axis."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_feature_circular_pattern is not implemented by this adapter",
        )

    async def mirror_feature(
        self, params: FeatureMirrorParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mirror one trusted feature about one trusted reference plane."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="mirror_feature is not implemented by this adapter",
        )

    async def create_hole_wizard(
        self, params: HoleWizardParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a controlled Hole Wizard feature from a trusted location sketch."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_hole_wizard is not implemented by this adapter",
        )

    async def create_tapped_hole(
        self, params: ThreadedHoleParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a standard tapped Hole Wizard feature."""
        _ = params
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_tapped_hole is not implemented by this adapter",
        )

    @abstractmethod
    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a sweep feature.

        Args:
            params (SweepParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a loft feature.

        Args:
            params (LoftParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        pass

    # Sketch Operations
    @abstractmethod
    async def create_sketch(self, plane: str) -> AdapterResult[str]:
        """Create a new sketch on the specified plane.

        Args:
            plane (str): The plane value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add a line to the current sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        pass

    async def create_3d_sketch(self) -> AdapterResult[str]:
        """Open a new three-dimensional sketch in the active part document."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_3d_sketch is not implemented by this adapter",
        )

    async def add_3d_line(
        self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float
    ) -> AdapterResult[str]:
        """Add one three-dimensional line segment in millimetres."""
        _ = x1, y1, z1, x2, y2, z2
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_3d_line is not implemented by this adapter",
        )

    async def convert_sketch_entities(
        self, edge_persistent_references: list[str]
    ) -> AdapterResult[str]:
        """Convert internal part-edge references into active-sketch geometry."""
        _ = edge_persistent_references
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="convert_sketch_entities is not implemented by this adapter",
        )

    @abstractmethod
    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        """Add a circle to the current sketch.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        pass

    async def add_point(self, x: float, y: float) -> AdapterResult[str]:
        """Add one point to the active sketch in millimetres."""
        _ = x, y
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_point is not implemented by this adapter",
        )

    @abstractmethod
    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add a rectangle to the current sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        pass

    async def add_arc(
        self,
        center_x: float,
        center_y: float,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> AdapterResult[str]:
        """Add an arc to the current sketch.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            start_x (float): The start x value.
            start_y (float): The start y value.
            end_x (float): The end x value.
            end_y (float): The end y value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_arc is not implemented by this adapter",
        )

    async def add_spline(self, points: list[dict[str, float]]) -> AdapterResult[str]:
        """Add a spline through the provided points.

        Args:
            points (list[dict[str, float]]): The points value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_spline is not implemented by this adapter",
        )

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Add a centerline to the current sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_centerline is not implemented by this adapter",
        )

    async def add_polygon(
        self, center_x: float, center_y: float, radius: float, sides: int
    ) -> AdapterResult[str]:
        """Add a regular polygon to the current sketch.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.
            sides (int): The sides value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_polygon is not implemented by this adapter",
        )

    async def add_ellipse(
        self,
        center_x: float,
        center_y: float,
        major_axis: float,
        minor_axis: float,
    ) -> AdapterResult[str]:
        """Add an ellipse to the current sketch.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            major_axis (float): The major axis value.
            minor_axis (float): The minor axis value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_ellipse is not implemented by this adapter",
        )

    async def add_sketch_text(
        self,
        text: str,
        x: float,
        y: float,
        height: float,
        angle_degrees: float,
    ) -> AdapterResult[str]:
        """Add one text entity to the current sketch."""
        _ = text, x, y, height, angle_degrees
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_sketch_text is not implemented by this adapter",
        )

    async def add_sketch_constraint(
        self,
        entity1: str,
        entity2: str | None,
        relation_type: str,
        entity3: str | None = None,
    ) -> AdapterResult[str]:
        """Apply a geometric constraint between sketch entities.

        Args:
            entity1 (str): The entity1 value.
            entity2 (str | None): The entity2 value.
            relation_type (str): The relation type value.
            entity3 (str | None): Third entity ID — only used by the
                ``symmetric`` relation (the centerline of symmetry). All
                other relation types reject a non-null ``entity3``.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_sketch_constraint is not implemented by this adapter",
        )

    async def add_sketch_dimension(
        self,
        entity1: str,
        entity2: str | None,
        dimension_type: str,
        value: float,
    ) -> AdapterResult[str]:
        """Add a sketch dimension.

        Args:
            entity1 (str): The entity1 value.
            entity2 (str | None): The entity2 value.
            dimension_type (str): The dimension type value.
            value (float): The value value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_sketch_dimension is not implemented by this adapter",
        )

    async def check_sketch_fully_defined(
        self, sketch_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Check whether a sketch is fully defined.

        Args:
            sketch_name (str | None): Optional sketch name to inspect. Defaults to None.

        Returns:
            AdapterResult[dict[str, Any]]: Definition status payload.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="check_sketch_fully_defined is not implemented by this adapter",
        )

    async def check_sketch_feature_use(
        self, sketch_name: str | None = None, feature_use: str = "boss_extrude"
    ) -> AdapterResult[dict[str, Any]]:
        """Ask the SolidWorks sketch kernel whether a sketch can create a feature.

        Args:
            sketch_name: Optional sketch feature name. Defaults to the last active sketch.
            feature_use: Intended feature type, such as ``"boss_extrude"``.

        Returns:
            Feature-use validation payload, including native status and contour counts.
        """
        _ = sketch_name, feature_use
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="check_sketch_feature_use is not implemented by this adapter",
        )

    async def sketch_linear_pattern(
        self,
        entities: list[str],
        direction_x: float,
        direction_y: float,
        spacing: float,
        count: int,
    ) -> AdapterResult[str]:
        """Create a linear pattern of sketch entities.

        Args:
            entities (list[str]): The entities value.
            direction_x (float): The direction x value.
            direction_y (float): The direction y value.
            spacing (float): The spacing value.
            count (int): The count value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_linear_pattern is not implemented by this adapter",
        )

    async def sketch_circular_pattern(
        self,
        entities: list[str],
        angle: float,
        count: int,
    ) -> AdapterResult[str]:
        """Create a circular pattern of sketch entities around the sketch origin.

        The rotation axis is always the sketch origin — SW's
        ``CreateCircularSketchStepAndRepeat`` has no pattern-centre
        parameter and derives the axis from the seed's geometry. Place
        the seed entity at the desired radius from the origin.

        Args:
            entities (list[str]): The entities value.
            angle (float): The angle value.
            count (int): The count value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_circular_pattern is not implemented by this adapter",
        )

    async def sketch_mirror(
        self, entities: list[str], mirror_line: str
    ) -> AdapterResult[str]:
        """Mirror sketch entities about a mirror line.

        Args:
            entities (list[str]): The entities value.
            mirror_line (str): The mirror line value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_mirror is not implemented by this adapter",
        )

    async def sketch_offset(
        self,
        entities: list[str],
        offset_distance: float,
        reverse_direction: bool,
    ) -> AdapterResult[str]:
        """Offset sketch entities.

        Args:
            entities (list[str]): The entities value.
            offset_distance (float): The offset distance value.
            reverse_direction (bool): The reverse direction value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_offset is not implemented by this adapter",
        )

    async def sketch_trim(
        self, entity_id: str, trim_x: float, trim_y: float
    ) -> AdapterResult[str]:
        """Trim one registered sketch entity at a point expressed in millimetres.

        The returned entity ID remains the server-issued source entity ID. The
        underlying SolidWorks sketch segment is modified in place.
        """
        _ = entity_id, trim_x, trim_y
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_trim is not implemented by this adapter",
        )

    async def sketch_split(
        self,
        entity_id: str,
        x: float,
        y: float,
        z: float = 0.0,
        closed: bool = False,
        x2: float | None = None,
        y2: float | None = None,
        z2: float | None = None,
    ) -> AdapterResult[str]:
        """Split one registered sketch segment at one or two points."""
        _ = entity_id, x, y, z, closed, x2, y2, z2
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_split is not implemented by this adapter",
        )

    async def sketch_fillet(
        self, entities: list[str], radius: float
    ) -> AdapterResult[str]:
        """Round the corner formed by two registered sketch entities."""
        _ = entities, radius
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_fillet is not implemented by this adapter",
        )

    async def sketch_chamfer(
        self, entities: list[str], distance: float, angle_degrees: float = 45.0
    ) -> AdapterResult[str]:
        """Bevel the corner formed by two registered sketch entities."""
        _ = entities, distance, angle_degrees
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="sketch_chamfer is not implemented by this adapter",
        )

    async def add_sketch_circle(
        self,
        center_x: float,
        center_y: float,
        radius: float,
        construction: bool = False,
    ) -> AdapterResult[str]:
        """Alias for add_circle used by some tool flows.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.
            construction (bool): The construction value. Defaults to False.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return await self.add_circle(center_x, center_y, radius)

    async def create_cut(self, sketch_name: str, depth: float) -> AdapterResult[str]:
        """Create a cut feature from an existing sketch.

        Args:
            sketch_name (str): The sketch name value.
            depth (float): The depth value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_cut is not implemented by this adapter",
        )

    async def create_cut_extrude(
        self, params: ExtrusionParameters
    ) -> AdapterResult[Any]:
        """Create a cut-extrude feature from the active sketch.

        Cuts material from the current solid body using the active sketch profile.
        Equivalent to SolidWorks Insert > Cut > Extrude.

        Args:
            params (ExtrusionParameters): Depth and direction parameters.

        Returns:
            AdapterResult: Feature result or error.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_cut_extrude is not implemented by this adapter",
        )

    async def create_sheet_metal_base_flange(
        self,
        sketch_name: str,
        thickness_mm: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a sheet-metal base flange from a trusted exited sketch."""
        _ = sketch_name, thickness_mm, bend_radius_mm, k_factor
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_sheet_metal_base_flange is not implemented by this adapter",
        )

    async def create_sheet_metal_edge_flange(
        self,
        edge_persistent_reference: str,
        length_mm: float,
        angle_degrees: float,
        bend_radius_mm: float,
        k_factor: float,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a sheet-metal edge flange from an internal edge reference."""
        _ = (
            edge_persistent_reference,
            length_mm,
            angle_degrees,
            bend_radius_mm,
            k_factor,
        )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_sheet_metal_edge_flange is not implemented by this adapter",
        )

    async def set_sheet_metal_k_factor(
        self, k_factor: float
    ) -> AdapterResult[dict[str, Any]]:
        """Set and read back the active part sheet-metal K factor."""
        _ = k_factor
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="set_sheet_metal_k_factor is not implemented by this adapter",
        )

    async def create_shell(
        self, face_persistent_references: list[str], thickness_mm: float, outward: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a shell after selecting server-owned faces to remove."""
        _ = face_persistent_references, thickness_mm, outward
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_shell is not implemented by this adapter",
        )

    async def create_rib(
        self,
        sketch_name: str,
        thickness_mm: float,
        draft_angle_degrees: float,
        direction: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create one rib from a trusted exited sketch."""
        _ = sketch_name, thickness_mm, draft_angle_degrees, direction
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_rib is not implemented by this adapter",
        )

    async def create_dome(
        self, face_persistent_reference: str, height_mm: float, reverse_direction: bool
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a dome on a face identified by an internal persistent reference."""
        _ = face_persistent_reference, height_mm, reverse_direction
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_dome is not implemented by this adapter",
        )

    async def create_draft(
        self,
        face_persistent_references: list[str],
        angle_degrees: float,
        reverse_direction: bool,
        edge_draft: bool,
    ) -> AdapterResult[SolidWorksFeature]:
        """Apply a multi-face neutral-plane draft to persistent part faces."""
        _ = face_persistent_references, angle_degrees, reverse_direction, edge_draft
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_draft is not implemented by this adapter",
        )

    async def create_thicken(
        self,
        face_persistent_reference: str,
        thickness_mm: float,
        operation: str,
        direction: str,
        fill_volume: bool,
        merge_result: bool,
    ) -> AdapterResult[SolidWorksFeature]:
        """Thicken one persistent surface face into a boss or cut feature."""
        _ = (
            face_persistent_reference,
            thickness_mm,
            operation,
            direction,
            fill_volume,
            merge_result,
        )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_thicken is not implemented by this adapter",
        )

    async def create_wrap(
        self,
        sketch_name: str,
        face_persistent_reference: str,
        operation: str,
        thickness_mm: float,
        reverse_direction: bool,
        method: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Wrap a trusted sketch onto one persistent part face."""
        _ = (
            sketch_name,
            face_persistent_reference,
            operation,
            thickness_mm,
            reverse_direction,
            method,
        )
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_wrap is not implemented by this adapter",
        )

    async def add_fillet(
        self, radius: float, edge_names: list[str]
    ) -> AdapterResult[Any]:
        """Add a fillet feature to selected edges.

        Rounds the selected edges of the current solid body with the given radius.

        Args:
            radius (float): Fillet radius in millimeters.
            edge_names (list[str]): List of edge names to fillet.

        Returns:
            AdapterResult: Feature result or error.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_fillet is not implemented by this adapter",
        )

    async def add_chamfer(
        self, distance: float, edge_names: list[str]
    ) -> AdapterResult[Any]:
        """Add an equal-distance chamfer to explicitly selected edges.

        Args:
            distance: Chamfer distance in millimetres.
            edge_names: SolidWorks edge names or coordinate edge specifications.

        Returns:
            A created chamfer feature or an explicit adapter error.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="add_chamfer is not implemented by this adapter",
        )

    async def create_chamfer(
        self,
        edge_persistent_references: list[str],
        distance_mm: float,
        angle_degrees: float,
        chamfer_type: str,
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a chamfer from workflow-owned persistent edge references."""
        _ = edge_persistent_references, distance_mm, angle_degrees, chamfer_type
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="create_chamfer is not implemented by this adapter",
        )

    @abstractmethod
    async def exit_sketch(self) -> AdapterResult[None]:
        """Exit sketch editing mode.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        pass

    # Analysis Operations
    @abstractmethod
    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        """Get mass properties of the current model.

        Returns:
            AdapterResult[MassProperties]: The result produced by the operation.
        """
        pass

    async def get_bounding_box(self) -> AdapterResult[dict[str, list[float]]]:
        """Read the active part's axis-aligned extents in millimetres."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_bounding_box is not implemented by this adapter",
        )

    async def list_equations(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-part equations and global variables without COM objects."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_equations is not implemented by this adapter",
        )

    async def set_equation(
        self, name: str, expression: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create or update one equation or global variable by its name."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="set_equation is not implemented by this adapter",
        )

    async def delete_equation(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Delete one equation or global variable by its name."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="delete_equation is not implemented by this adapter",
        )

    # Export Operations
    @abstractmethod
    async def export_image(self, payload: dict) -> AdapterResult[dict]:
        """Export a viewport screenshot (PNG/JPG) of the current model.

        Payload keys: file_path (str): Absolute output path. width (int): Image width in pixels.
        height (int): Image height in pixels. view_orientation (str): One of "isometric",
        "front", "top", "right", "back", "bottom", "current".

        Args:
            payload (dict): The payload value.

        Returns:
            AdapterResult[dict]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def export_file(
        self, file_path: str, format_type: str
    ) -> AdapterResult[None]:
        """Export the current model to a file.

        Args:
            file_path (str): Path to the target file.
            format_type (str): The format type value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        pass

    async def get_configuration(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one exact configuration and its descriptive metadata."""
        _ = configuration_name
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_configuration is not implemented by this adapter",
        )

    async def list_open_models(self) -> AdapterResult[list[dict[str, Any]]]:
        """List native documents currently open in the SolidWorks instance."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_open_models is not implemented by this adapter",
        )

    async def get_selection_state(self) -> AdapterResult[dict[str, Any]]:
        """Return a serializable snapshot of the active SolidWorks selection set."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="get_selection_state is not implemented by this adapter",
        )

    async def export_drawing_cad(
        self,
        file_path: str,
        format_type: str,
        model_space: bool = True,
        compatibility_profile: str = "standard",
    ) -> AdapterResult[None]:
        """Export an active drawing as DWG or DXF with explicit space selection.

        Args:
            file_path: Destination DWG or DXF file path.
            format_type: Either ``"dwg"`` or ``"dxf"``.
            model_space: Write drawing entities to AutoCAD model space when true.
            compatibility_profile: ``"standard"`` or ``"caxa_2015"``. The
                CAXA profile creates a flattened R12 DXF for CAXA CRX readers.

        Returns:
            The export result.
        """
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="export_drawing_cad is not implemented by this adapter",
        )

    # Dimension Operations
    async def list_dimensions(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-model dimensions with their private native names and values."""
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="list_dimensions is not implemented by this adapter",
        )

    @abstractmethod
    async def get_dimension(self, name: str) -> AdapterResult[float]:
        """Get the value of a dimension.

        Args:
            name (str): The name value.

        Returns:
            AdapterResult[float]: The result produced by the operation.
        """
        pass

    @abstractmethod
    async def set_dimension(self, name: str, value: float) -> AdapterResult[None]:
        """Set the value of a dimension.

        Args:
            name (str): The name value.
            value (float): The value value.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        pass

    # Utility Methods
    def update_metrics(self, operation_time: float, success: bool) -> None:
        """Update adapter metrics.

        Args:
            operation_time (float): The operation time value.
            success (bool): The success value.

        Returns:
            None: None.
        """
        self._metrics["operations_count"] += 1
        if not success:
            self._metrics["errors_count"] += 1

        # Update average response time
        current_avg = self._metrics["average_response_time"]
        count = self._metrics["operations_count"]
        self._metrics["average_response_time"] = (
            current_avg * (count - 1) + operation_time
        ) / count

    def get_metrics(self) -> dict[str, Any]:
        """Get adapter metrics.

        Returns:
            dict[str, Any]: A dictionary containing the resulting values.
        """
        return self._metrics.copy()
