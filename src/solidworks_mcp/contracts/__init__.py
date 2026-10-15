"""Stable, client-facing contracts for typed MCP workflows."""

from .mvp import (
    AddRectangleRequest,
    Artifact,
    CreateExtrusionRequest,
    CreatePartRequest,
    CreateSketchRequest,
    CreateTappedHoleRequest,
    DocumentRef,
    EntityRef,
    ExitSketchRequest,
    ExportFileRequest,
    OperationError,
    OperationResult,
    OperationRoute,
    OperationStatus,
    SessionRef,
)

__all__ = [
    "AddRectangleRequest",
    "Artifact",
    "CreateExtrusionRequest",
    "CreatePartRequest",
    "CreateSketchRequest",
    "CreateTappedHoleRequest",
    "DocumentRef",
    "EntityRef",
    "ExportFileRequest",
    "ExitSketchRequest",
    "OperationError",
    "OperationResult",
    "OperationRoute",
    "OperationStatus",
    "SessionRef",
]
