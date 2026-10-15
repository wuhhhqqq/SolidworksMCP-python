# SolidWorks API Coverage Map

**Last updated:** 2026-07-28
**Reference:** SolidWorks 2026 API (SOLIDWORKS COM API via pywin32)  
**Purpose:** Planning reference for future tool development — maps every major SolidWorks API functional category against what this MCP server currently implements.

---

## Table of Contents

1. [Implemented Tools](#1-implemented-tools)
2. [SolidWorks API Coverage Map](#2-solidworks-api-coverage-map)
3. [Missing High-Priority Tools](#3-missing-high-priority-tools)
4. [Missing Medium/Low Priority Tools](#4-missing-mediumlow-priority-tools)
5. [Summary Table](#5-summary-table)

---

## 1. Implemented Tools

### Document / Model Lifecycle (`modeling.py`, `file_management.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `open_model` | Opens any SW file (.sldprt/.sldasm/.slddrw) | ISldWorks / IModelDoc2 |
| `create_part` | Creates a new blank part document | ISldWorks::NewDocument |
| `create_assembly` | Creates a new blank assembly document | ISldWorks::NewDocument |
| `create_drawing` | Creates a new blank drawing document | ISldWorks::NewDocument |
| `close_model` | Closes the active document (optionally saving) | IModelDoc2::CloseDoc |
| `load_part` | Convenience wrapper: opens a .sldprt | ISldWorks::OpenDoc6 |
| `load_assembly` | Convenience wrapper: opens a .sldasm | ISldWorks::OpenDoc6 |
| `save_file` | Saves the active document in place | IModelDoc2::Save3 |
| `save_as` | Saves the active document to a new path/format | IModelDoc2::SaveAs4 |
| `save_part` | Type-safe save with path validation for parts | IModelDoc2::Save3 |
| `save_assembly` | Type-safe save with path validation + reference copy | IModelDoc2::Save3 / GetDependencies2 |
| `get_file_properties` | Returns file metadata (simulated) | IModelDoc2::CustomPropertyManager |
| `get_model_info` | Compact model context summary (type, config, feature count) | IModelDoc2 introspection |
| `pack_and_go_assembly` | Copies assembly + all referenced parts to a new folder | IModelDocExtension::GetPackAndGo / SavePackAndGo |

### Feature Tree / Configurations (`file_management.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `list_features` | Lists all feature-tree entries | IModelDoc2::FirstFeature / GetNextFeature |
| `classify_feature_tree` | Classifies model family (solid, sheet metal, VBA, assembly, drawing) | Internal classifier |
| `list_configurations` | Lists configuration names | IModelDoc2::GetConfigurationNames |
| `solidworks_list_features` | Session-scoped, paginated feature-tree listing | IModelDoc2::FirstFeature / GetNextFeature |
| `solidworks_list_configurations` | Session-scoped, paginated configuration listing | IModelDoc2::GetConfigurationNames |
| `solidworks_create_configuration` | Creates, activates, and verifies an independent configuration | IModelDoc2::AddConfiguration3 / ShowConfiguration2 |
| `solidworks_activate_configuration` | Verifies a configuration switch | IModelDoc2::ShowConfiguration2 |
| `manage_file_properties` | Read/update/copy/move/delete file properties | IModelDoc2::CustomPropertyManager |
| `convert_file_format` | Convert file to another format via export | IModelDoc2::SaveAs4 |
| `batch_file_operations` | Bulk file lifecycle operations | Filesystem |
| `batch_export` | Batch-converts directories of SW files | ISldWorks / IModelDoc2::SaveAs4 |

### Sketching (`sketching.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `create_sketch` | Creates a new sketch on a named reference plane | ISketchManager::InsertSketch |
| `exit_sketch` | Exits sketch editing mode | ISketchManager::InsertSketch (exit) |
| `check_sketch_fully_defined` | Reports under/fully/over-defined status | ISketch::IsFullyDefined |
| `add_line` | Adds a line segment | ISketchManager::CreateLine |
| `add_circle` | Adds a circle | ISketchManager::CreateCircle |
| `add_rectangle` | Adds a 2-corner rectangle (4 lines) | ISketchManager::CreateCornerRectangle |
| `add_arc` | Adds a center-point arc | ISketchManager::CreateArc |
| `add_spline` | Adds a spline through control points | ISketchManager::CreateSpline |
| `add_centerline` | Adds a construction/centerline | ISketchManager::CreateLine (construction) |
| `add_polygon` | Adds a regular N-sided polygon | ISketchManager::CreatePolygon |
| `add_ellipse` | Adds an ellipse | ISketchManager::CreateEllipse |
| `solidworks_add_sketch_text` | Adds text to the active sketch | ISketchManager::CreateText |
| `add_sketch_constraint` | Adds a geometric relation (parallel, tangent, etc.) | ISketchRelationManager::AddRelation |
| `add_sketch_dimension` | Adds a dimensional constraint | IModelDocExtension::AddSmartDimension2 |
| `sketch_linear_pattern` | Linear step-and-repeat of sketch entities | ISketchManager::CreateLinearSketchStepAndRepeat |
| `sketch_circular_pattern` | Circular step-and-repeat of sketch entities | ISketchManager::CreateCircularSketchStepAndRepeat |
| `sketch_mirror` | Mirrors entities about a centerline | ISketchManager::SketchMirror |
| `sketch_offset` | Offsets sketch entities by a distance | ISketchManager::SketchOffset4 |
| `sketch_tutorial_simple_hole` | Tutorial: full hole sketch workflow | Composed from above |
| `tutorial_simple_hole` | Guided tutorial: sketch+cut workflow | Composed from above |

### Feature Modeling — Boss/Cut/Form (`modeling.py`, `adapters/solidworks/features.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `create_extrusion` | Boss-extrude from active sketch | IFeatureManager::FeatureExtrusion3 |
| `create_cut_extrude` | Cut-extrude from active sketch | IFeatureManager::FeatureCut4 |
| `create_revolve` | Revolve boss around centerline axis | IFeatureManager::FeatureRevolve2 |
| `create_sweep` | Sweep profile along a path sketch | IFeatureManager::InsertProtrusionSwept4 |
| `create_loft` | Loft solid between ≥2 profile sketches | IFeatureManager::InsertProtrusionBlend2 |
| `add_fillet` | Constant-radius fillet on named edges | IModelDoc2::FeatureFillet3 / IFeatureManager::FeatureFillet3 |
| `solidworks_create_chamfer` | Revision-bound equal-distance or distance-angle chamfer on listed part edges, with opaque-edge selection checks, edge-length preflight diagnostics, and SW2019 fallback; `add_chamfer` remains legacy compatibility | IModelDoc2::FeatureChamferType / IFeatureManager::InsertFeatureChamfer |
| `get_dimension` | Reads a named dimension value | IModelDoc2::Parameter |
| `set_dimension` | Sets a named dimension and rebuilds | IModelDoc2::Parameter / ForceRebuild3 |

### Export (`export.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `export_step` | STEP export (AP214/AP203) | IModelDoc2::SaveAs4 |
| `export_iges` | IGES export | IModelDoc2::SaveAs4 |
| `export_stl` | STL export (binary/ASCII) | IModelDoc2::SaveAs4 |
| `export_pdf` | PDF export from drawing/part | IModelDoc2::SaveAs4 |
| `export_dwg` | DWG export from drawing | IModelDoc2::SaveAs4 |
| `export_image` | JPG/PNG/BMP image capture | IModelDoc2::SaveBitmapWithVariable |
| `batch_export` | Directory-level multi-format export | Composed from above |

### Analysis (`analysis.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `calculate_mass_properties` | Volume, surface area, mass, CoM, MoI | IMassProperty |
| `get_mass_properties` | Alias for calculate_mass_properties | IMassProperty |
| `check_interference` | Assembly interference detection | IInterferenceDetectionMgr |
| `analyze_geometry` | Curvature/draft/thickness analysis (simulated) | ISurface / IGeometryManager |
| `get_material_properties` | Material properties (simulated) | IPartDoc::GetMaterialPropertyName2 |
| `solidworks_list_solid_bodies` | Lists paginated safe summaries of solid bodies | IPartDoc::GetBodies2 |
| `solidworks_get_material_assignment` | Reads the active configuration's material database and name | IPartDoc::GetMaterialPropertyName2 |
| `solidworks_set_material_assignment` | Assigns and verifies the active configuration's material | IPartDoc::SetMaterialPropertyName2 |

### Drawing (`drawing.py`, `drawing_analysis.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `create_drawing_view` | Creates a model view in a drawing (simulated) | IDrawingDoc::CreateDrawViewFromModelView3 |
| `solidworks_create_drawing_view` | Adds a standard model view from a controlled saved part/assembly | IDrawingDoc::CreateDrawViewFromModelView3 |
| `solidworks_list_drawing_views` | Lists paginated safe summaries of model views | IDrawingDoc::GetFirstView / IView::GetNextView |
| `solidworks_create_projected_drawing_view` | Adds a projected view from a server-issued active-drawing view ID | IDrawingDoc::CreateProjectionView |
| `solidworks_list_custom_properties` | Lists document or active-configuration custom properties | ICustomPropertyManager::GetNames / Get6 |
| `solidworks_set_custom_property` | Creates or replaces a text custom property | ICustomPropertyManager::Add3 |
| `add_dimension` | Adds a dimension in a drawing (simulated) | IModelDocExtension::AddSmartDimension2 |
| `add_note` | Adds a text note (simulated) | IAnnotationView::InsertNote |
| `create_section_view` | Creates a section view (simulated) | IDrawingDoc::CreateSectionView5 |
| `create_detail_view` | Creates a detail view (simulated) | IDrawingDoc::CreateDetailViewAt4 |
| `update_sheet_format` | Applies a sheet format template (simulated) | ISheet::SetSheetInfo2 |
| `auto_dimension_view` | Auto-dimensions a view (simulated) | IModelDocExtension::AddSmartDimension2 |
| `check_drawing_standards` | Validates against drafting standards (simulated) | Internal |
| `create_technical_drawing` | Full drawing-from-model workflow | IDrawingDoc |
| `add_drawing_view` | Add/update/delete a drawing view | IDrawingDoc |
| `add_annotation` | Add annotation (note, balloon, symbol) | IAnnotationView |
| `update_title_block` | Update title block fields | ISheet / INote |
| `analyze_drawing_comprehensive` | Full quality analysis of a drawing (simulated) | IDrawingDoc inspection |
| `analyze_drawing_dimensions` | Dimension consistency analysis (simulated) | IDisplayDimension |
| `analyze_drawing_annotations` | Annotation quality analysis (simulated) | INote / IAnnotation |
| `check_drawing_compliance` | Standards compliance check (simulated) | Internal |
| `analyze_drawing_views` | Drawing view analysis (simulated) | IView |
| `generate_drawing_report` | Comprehensive quality report (simulated) | Internal |
| `compare_drawing_versions` | Compares two drawing revisions (simulated) | Internal |
| `validate_drawing_completeness` | Manufacturing readiness check (simulated) | Internal |

### Automation (`automation.py`)

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `generate_vba_code` | Generates template VBA code for an operation | Internal template |
| `automation_start_macro_recording` | Starts SolidWorks macro recording (simulated) | ISldWorks::RunMacro2 |
| `automation_stop_macro_recording` | Stops macro recording (simulated) | ISldWorks::RunMacro2 |
| `batch_process_files` | Batch rebuild/export across a directory (simulated) | ISldWorks / IModelDoc2 |
| `manage_design_table` | Create/update Excel-driven design tables (simulated) | IDesignTable |
| `execute_workflow` | Sequential/parallel tool pipeline (simulated) | Internal |
| `create_template` | Creates a blank document template file (simulated) | ISldWorks::NewDocument |
| `optimize_performance` | Suggests/applies SW performance settings (simulated) | ISldWorks::SetUserPreferenceIntegerValue |

### Miscellaneous

| Tool Name | What It Does | SW API Category |
|---|---|---|
| `vba_generation.*` | Advanced VBA generation helpers | Internal |
| `docs_discovery.*` | SW API documentation search | Web/filesystem |
| `template_management.*` | Template extract/apply/batch/compare | IModelDoc2 / CustomPropertyManager |
| `macro_recording.*` | Dedicated macro recording module | ISldWorks::RunMacro2 |

**Total registered MCP tools: 254**
**Real COM coverage:** the typed MVP and adapter-backed tools are implemented and
covered by Mock/adapter tests; legacy tools still marked simulated remain outside
that guarantee.

---

## 2. SolidWorks API Coverage Map

The SW API is organized around the major interfaces exposed by the COM type library. Categories below map to the official SolidWorks API functional hierarchy.

---

### 2.1 Application-Level API (`ISldWorks`)

Covers top-level application control: document management, add-in registration, user-preference settings, macro execution, and application events.

| Operation | Status | Tool / Notes |
|---|---|---|
| Open document (`OpenDoc6`) | Implemented | `open_model`, `load_part`, `load_assembly` |
| Create new document (`NewDocument`) | Implemented | `create_part`, `create_assembly`, `create_drawing` |
| Close document (`CloseDoc`) | Implemented | `close_model` |
| Get active document (`ActiveDoc`) | Implemented | Internal — used by all adapter ops |
| List open documents (`GetDocuments`) | Implemented | `list_open_models` returns safe title/path/type/configuration metadata |
| Activate a specific session-owned document | Implemented | `solidworks_activate_document` uses `ActivateDoc3` internally and accepts only an opaque `document_id` |
| Run a VBA macro (`RunMacro2`) | Implemented (trusted) | `solidworks_execute_trusted_macro` validates manifest, hash confirmation, and calls `RunMacro2` |
| Get/set user preferences (`SetUserPreferenceIntegerValue`) | **Missing** (Low) | `optimize_performance` stubs this |
| SolidWorks version info (`RevisionNumber`) | Implemented | Internal version detection in features.py |
| Add-in management | **Missing** (Low) | `GetAddInObject`, etc. |
| Application events (document open/close callbacks) | **Missing** (Low) | Event sink registration |
| Frame/window handle | **Missing** (Low) | UI manipulation |

---

### 2.2 Document API (`IModelDoc2`) — Core

Covers operations on the active document regardless of type (part/assembly/drawing): rebuild, properties, selection, dimensions, and configurations.

| Operation | Status | Tool / Notes |
|---|---|---|
| `Save3` / `SaveAs4` | Implemented | `save_file`, `save_as`, `save_part`, `save_assembly` |
| `ForceRebuild3` | Implemented | Internal — called before SelectByID2 |
| `Parameter` (read dimension) | Implemented | `get_dimension` |
| `Parameter.SystemValue = x` (write dimension) | Implemented | `set_dimension` |
| `FirstFeature` / `GetNextFeature` | Implemented | `list_features`, internal tree walks |
| `FeatureByName` | Implemented | Internal — sweep/loft/selection |
| `GetPathName` | Implemented | Internal — pack-and-go reference resolution |
| `GetTitle` | Implemented | Internal — model info |
| `GetDependencies2` | Implemented | `save_assembly` with `include_references=True` |
| `GetConfigurationNames` | Implemented | `list_configurations` |
| `ActiveConfiguration` | Implemented | Typed workflow records verified active configuration |
| `ShowConfiguration2` | Implemented | `solidworks_activate_configuration` |
| `AddConfiguration3` | Implemented | `solidworks_create_configuration` creates, activates, and verifies an independent configuration |
| `CopyConfiguration` | **Missing** (Low) | Duplicate a configuration |
| `DeleteConfiguration2` | Implemented (typed) | `solidworks_delete_configuration` rejects deletion of the active configuration and verifies the updated configuration list |
| `GetAnnotations` (in 3D) | **Missing** (Medium) | 3D annotation / model-based definition |
| `InsertNote` (3D) | **Missing** (Medium) | 3D annotation notes |
| `CustomPropertyManager.GetNames/Get6/Add3` | Implemented | `solidworks_list_custom_properties` / `solidworks_set_custom_property` support document and active-configuration scopes |
| `Extension.CustomPropertyManager` | Implemented | Typed workflow resolves the document or verified active configuration manager |
| `SketchManager` (enter sketch) | Implemented | `create_sketch` |
| `ClearSelection2` | Implemented | Internal — used before multi-edge operations |
| `FeatureFillet3` | Implemented | `add_fillet` |
| `FeatureChamfer` | Implemented (typed) | `solidworks_create_chamfer` resolves opaque topology edge IDs, verifies selection/rebuild/feature state, and returns diagnostics; `add_chamfer` is legacy |

---

### 2.3 Part API (`IPartDoc`)

Extends `IModelDoc2` for parts: body operations, material assignment, sheet metal.

| Operation | Status | Tool / Notes |
|---|---|---|
| `GetBodies2` — list solid bodies | Implemented | `solidworks_list_solid_bodies` returns paginated safe summaries |
| `GetMaterialPropertyName2` | Implemented | `solidworks_get_material_assignment`; legacy `get_material_properties` remains simulated |
| `SetMaterialPropertyName2` | Implemented | `solidworks_set_material_assignment` writes and verifies the active configuration |
| `GetSurfaceBodies` / `GetBodies2(swSheetBody)` | Implemented | `solidworks_list_surface_bodies` returns paginated safe surface-body summaries |
| Sheet-metal state / flat pattern | Implemented (partial) | `solidworks_get_sheet_metal_info` returns sheet-metal status, thickness, flat state, and flat-pattern feature name; `solidworks_set_sheet_metal_k_factor` edits and verifies the active K factor; typed bend-table/note insertion and direct flat/bend face enumeration are available, while table-cell inspection/editing remains follow-on work |
| `InsertBaseFlange2` / `InsertEdgeFlange2` | Implemented (typed) | `solidworks_create_sheet_metal_base_flange` creates a base flange from an exited session sketch; `solidworks_create_sheet_metal_edge_flange` creates an edge flange from a server-issued part-edge ID |
| `SetBendState` (`Flattened` / `Folded`) | Implemented | `solidworks_set_sheet_metal_flat_state` sets the typed bend state, rebuilds, and verifies the requested state |
| Weldment operations | **Missing** (Low) | Structural member insertion |
| `GetFeatureManagerDesignTree` | Implemented (indirectly) | `list_features` |
| Multi-body: `InsertCombineFeature` | **Missing** (Medium) | Combine / subtract bodies |
| `InsertShell` | Implemented (typed) | `solidworks_create_shell` removes only server-issued faces and applies a verified thickness |
| `InsertRib` | Implemented (typed) | `solidworks_create_rib` builds a rib from an exited session sketch |
| `InsertDome` | Implemented (typed) | `solidworks_create_dome` applies a dome to a server-issued part face |
| `InsertMultiFaceDraft` | Implemented (typed) | `solidworks_create_draft` applies a neutral-plane multi-face draft from current server-issued face IDs |
| `FeatureBossThicken` / `FeatureCutThicken` | Implemented (typed) | `solidworks_create_thicken` thickens one current server-issued surface face into a boss or cut |
| `InsertWrapFeature2` | Implemented (typed) | `solidworks_create_wrap` wraps one exited session sketch onto a current server-issued part face |
| `InsertSplit` | **Missing** (Low) | Split body |

---

### 2.4 Assembly API (`IAssemblyDoc`)

Extends `IModelDoc2` for assemblies: component insertion, mating, explode views, BOM.

| Operation | Status | Tool / Notes |
|---|---|---|
| `AddComponent5` — insert component | Implemented | `solidworks_insert_assembly_component` inserts a saved session-owned part at a typed position |
| `GetComponents` — list components | Implemented | `solidworks_list_assembly_components` returns paginated safe summaries |
| `GetComponentByName` | Implemented (typed) | `solidworks_get_assembly_component` performs exact, selection-free lookup by display name |
| `GetPersistReference3` / `GetObjectByPersistReference3` | Implemented | Server-only persistent geometry references behind `solidworks_list_assembly_mate_entities` |
| `AddMate5` — add mate constraint | Implemented | `solidworks_create_assembly_mate` supports typed coincident, concentric, parallel, perpendicular, tangent, distance, and angle mates |
| `GetMates` — list mates | Implemented | `solidworks_list_assembly_mates` returns revision-bound opaque mate IDs |
| `DeleteMate` | Implemented | `solidworks_delete_assembly_mate` deletes only a current server-issued mate ID |
| `IInterferenceDetectionMgr::GetInterferences` | Implemented (typed) | `solidworks_check_assembly_interference` runs native detection for the current session-owned assembly and returns component names, volume, and possible-interference status |
| `ExplodeView` | **Missing** (Medium) | Create explode view |
| `UnExplodeView` | **Missing** (Low) | Collapse explode |
| `AutoExplode` | **Missing** (Low) | Auto-generate explode steps |
| BOM management (`IBomTable`) | Implemented | `solidworks_create_drawing_bom`, `solidworks_update_drawing_bom`, and `solidworks_delete_drawing_bom` operate only on revision-bound server-issued BOM IDs |
| Pattern component (`InsertLinearComponentPattern`) | **Missing** (Low) | Linear component array |
| Pattern component (`InsertCircularComponentPattern`) | **Missing** (Low) | Circular component array |
| Mirror component (`InsertMirrorComponent`) | **Missing** (Low) | Mirror component |
| Smart mates | **Missing** (Low) | Smart-mate via drag |
| Simulation-ready assembly check | **Missing** (Low) | Pre-simulation validation |

---

### 2.5 Drawing API (`IDrawingDoc`)

Extends `IModelDoc2` for drawings: sheets, views, annotations, BOM tables, revision tables.

| Operation | Status | Tool / Notes |
|---|---|---|
| `CreateDrawViewFromModelView3` | Implemented | `solidworks_create_drawing_view` uses a saved session-owned part or assembly and standard view direction |
| `GetFirstView` / `GetNextView` | Implemented | `solidworks_list_drawing_views` returns bounded safe model-view summaries and skips the sheet pseudo-view |
| `GetSheetNames` / `GetCurrentSheet` | Implemented (typed) | `solidworks_list_drawing_sheets` returns bounded sheet names and active state |
| `ActivateSheet` | Implemented (typed) | `solidworks_activate_drawing_sheet` activates an exact current sheet name |
| `CreateProjectionView` | Implemented | `solidworks_create_projected_drawing_view` resolves only a server-issued view ID in the active drawing before COM selection |
| `CreateSectionView5` | Implemented (typed) | `solidworks_create_section_drawing_view` creates a section from a revision-bound drawing view ID and explicit cut line |
| `CreateDetailViewAt4` | Simulated | `create_detail_view` |
| `CreateAuxiliaryViewAt` | Implemented (typed) | `solidworks_create_auxiliary_drawing_view` resolves a listed drawing edge target |
| `ActivateView` | Implemented (typed) | `solidworks_activate_drawing_view` resolves and activates a current view ID |
| Sheet management (`ISheet::SetSheetInfo2`) | Simulated | `update_sheet_format` |
| Add/remove sheets | Implemented (typed) | List, create (`NewSheet4` with old-version fallback), activate, and safe-delete drawing sheets; creation supports A0-A4 and explicit custom sizes |
| Rename/reorder sheets | Implemented (typed) | `solidworks_rename_drawing_sheet` uses `ISheet.SetName`; `solidworks_reorder_drawing_sheets` validates the complete current list and calls `IDrawingDoc.ReorderSheets` |
| `IView::InsertBomTable4` — insert BOM table | Implemented | `solidworks_create_drawing_bom` accepts only a session-owned saved assembly and a revision-bound server-issued drawing view ID |
| Revision table | Implemented (typed) | `solidworks_create_drawing_revision_table` calls `InsertRevisionTable2` and adds an initial revision row |
| Revision rows | Implemented (typed) | `solidworks_list_drawing_revisions`, `solidworks_add_drawing_revision`, and `solidworks_delete_drawing_revision` use server-issued persistent table references |
| General table | **Missing** (Low) | Custom tables |
| `IView::GetOutline` | Implemented | `solidworks_get_drawing_view_outline` returns a current view's safe millimetre layout extents |
| `IView::ScaleDecimal` | Implemented (typed) | `solidworks_update_drawing_view_layout` sets an explicit per-view decimal scale |
| Move/align views | Implemented | `solidworks_update_drawing_view_layout` moves a listed view; `solidworks_align_drawing_views` arranges multiple views horizontally or vertically from `GetOutline` dimensions with gap and optional start coordinates |
| Break view | **Missing** (Low) | Broken-out section |
| Crop view | **Missing** (Low) | Cropped view boundary |
| Smart dimension in drawing | Implemented | `solidworks_list_drawing_annotation_targets` plus `solidworks_place_drawing_dimension` retain persistent references server-side and call `AddDimension2` |
| Ordinate dimension | Implemented (typed) | `solidworks_create_drawing_ordinate_dimension` calls `AddOrdinateDimension2` from a server-issued display-dimension target |
| Geometric tolerance (FCF) | Implemented (typed) | `solidworks_create_drawing_geometric_tolerance` calls `InsertGtol` and configures the frame through `SetFrameValues` |
| Surface finish symbol | Implemented (typed) | `solidworks_create_drawing_surface_finish` calls `InsertSurfaceFinishSymbol3` for ISO finish symbols on listed edges |
| Weld symbol | Implemented (typed) | `solidworks_create_drawing_weld_symbol` calls `InsertWeldSymbol3` and `SetWeldSymbol` on listed edges |
| Datum target | **Missing** (Low) | GD&T datum targets |
| Balloon annotation | Implemented | `solidworks_create_drawing_balloon` calls `IModelDoc2::InsertBOMBalloon2` from a server-issued visible component target |
| Center mark / centerline | Partial | `solidworks_create_drawing_center_mark` calls `AddCenterMark` for a listed circular edge; centerline creation remains pending |
| Hole callout | Implemented | `solidworks_create_drawing_hole_callout` calls `IDrawingDoc::AddHoleCallout2` from a server-issued visible edge target |
| Note (real) | Implemented | `solidworks_create_drawing_note` calls `IModelDoc2::InsertNote` and positions the returned annotation |
| Layer management | **Missing** (Low) | Drawing layers |
| Print/plot drawing | **Missing** (Medium) | Print to paper/PDF via API |
| `IDrawingDoc::RebuildAndSave` | Implemented (typed) | `solidworks_rebuild_drawing` rebuilds and saves a session-owned drawing before downstream inspection or export |

---

### 2.6 Sketch API (`ISketchManager` / `ISketch`)

| Operation | Status | Tool / Notes |
|---|---|---|
| `InsertSketch` (enter/exit) | Implemented | `create_sketch`, `exit_sketch` |
| `CreateLine` | Implemented | `add_line` |
| `CreateCircle` | Implemented | `add_circle` |
| `CreateCornerRectangle` | Implemented | `add_rectangle` |
| `CreateArc` (center-point) | Implemented | `add_arc` |
| `CreateSpline` | Implemented | `add_spline` |
| `CreateEllipse` | Implemented | `add_ellipse` |
| `CreatePolygon` | Implemented | `add_polygon` |
| `SketchMirror` | Implemented | `sketch_mirror` |
| `SketchOffset4` | Implemented | `sketch_offset` |
| `SketchTrim5` / `SketchTrim` | Implemented (typed) | `solidworks_trim_sketch_entity`; current server-issued entity, active sketch only |
| `CreateLinearSketchStepAndRepeat` | Implemented | `sketch_linear_pattern` |
| `CreateCircularSketchStepAndRepeat` | Implemented | `sketch_circular_pattern` |
| `AddRelation` (via ISketchRelationManager) | Implemented | `add_sketch_constraint` |
| `AddSmartDimension2` | Implemented | `add_sketch_dimension` |
| Trim entities (`SketchTrim5`) | Implemented (typed) | `solidworks_trim_sketch_entity`; `SketchTrim` fallback supports older SolidWorks versions |
| Split entities (`SplitOpenSegment` / `SplitClosedSegment`) | Implemented (typed) | `solidworks_split_sketch_entity` splits one current sketch segment at one point, or a closed curve at two points |
| Sketch text (`CreateText`) | Implemented (typed) | `solidworks_add_sketch_text` creates a text entity with position, height, and angle |
| Convert entities (`SketchConvertEntities`) | Implemented (typed) | `solidworks_convert_sketch_entities` projects only server-issued part-edge IDs into the active session sketch |
| Intersection curve | **Missing** (Medium) | Intersect surfaces/planes with sketch |
| 3D sketch | Implemented (typed) | `solidworks_create_3d_sketch` and `solidworks_add_3d_line` create a session-owned 3D sketch and line geometry |
| `ISketch::IsFullyDefined` | Implemented | `check_sketch_fully_defined` |
| `SketchFillet` | Implemented (typed) | `solidworks_fillet_sketch_corner` rounds two current sketch entities with a metric radius |
| `SketchChamfer` | Implemented (typed) | `solidworks_chamfer_sketch_corner` creates an equal-distance chamfer between two current sketch entities |
| Dynamic mirror (live symmetry) | **Missing** (Low) | `ISketchManager::InsertSketchMirror` |
| Sketch picture / texture | **Missing** (Low) | Import image as sketch reference |
| 3-point arc | **Missing** (Low) | `CreateArc` 3-point variant |
| Parabolic / conic arc | **Missing** (Low) | Conic sections |

---

### 2.7 Feature Manager API (`IFeatureManager`)

| Operation | Status | Tool / Notes |
|---|---|---|
| `FeatureExtrusion3` / `FeatureExtrusion2` | Implemented | `create_extrusion` |
| `FeatureCut4` / `FeatureCut3` | Implemented | `create_cut_extrude` |
| `FeatureRevolve2` | Implemented | `solidworks_create_revolve` supports `operation="boss"` and `operation="cut"` |
| `InsertProtrusionSwept4` / `InsertCutSwept4` | Implemented | `solidworks_create_sweep` supports `operation="boss"` and `operation="cut"`; cut calls fall back to `InsertCutSwept3` |
| `InsertProtrusionBlend2` / `InsertCutBlend` | Implemented | `solidworks_create_loft` supports `operation="boss"` and `operation="cut"` |
| `FeatureFillet3` | Implemented | `add_fillet` |
| `InsertFeatureChamfer` | Implemented (typed) | `solidworks_create_chamfer` uses persistent edge references with a legacy API fallback; `add_chamfer` remains name/coordinate based for compatibility |
| `InsertShell` | Implemented (typed) | `solidworks_create_shell` uses a server-issued face list and explicit wall thickness |
| `InsertRib` | Implemented (typed) | `solidworks_create_rib` uses a session-owned exited sketch |
| `InsertMultiFaceDraft` | Implemented (typed) | `solidworks_create_draft` uses only current server-issued face IDs and an explicit draft angle |
| `FeatureBossThicken` / `FeatureCutThicken` | Implemented (typed) | `solidworks_create_thicken` turns one current server-issued surface face into a boss or cut with an explicit direction |
| `InsertWrapFeature2` | Implemented (typed) | `solidworks_create_wrap` combines one exited session sketch with one current server-issued part face |
| Linear pattern (`FeatureLinearPattern4`) | Implemented (typed) | `solidworks_create_feature_linear_pattern`; one feature, one standard direction |
| Circular pattern (`FeatureCircularPattern5`) | Implemented (typed) | `solidworks_create_feature_circular_pattern`; one feature, current `axis_*` origin axis |
| Mirror feature (`InsertMirrorFeature2`) | Implemented (typed) | `solidworks_mirror_feature`; one feature, current `ref_*` plane |
| Hole Wizard (`HoleWizard5`) | Implemented (limited typed) | `solidworks_create_hole_wizard`; ANSI Metric M6 clearance hole, plus `solidworks_create_tapped_hole` for bounded ANSI Metric M3-M16 tapped-hole presets from an exited point sketch |
| `InsertDome` | Implemented (typed) | `solidworks_create_dome` uses one server-issued face and an explicit height |
| `InsertSplit` | **Missing** (Low) | Split body feature |
| `InsertScale` | **Missing** (Low) | Scale body |
| Flex feature | **Missing** (Low) | Flex/deform a body |
| `InsertCutSurface` | **Missing** (Low) | Surface cut |
| `InsertBoundaryBoss` | **Missing** (Low) | Boundary feature |
| `InsertDeformSurface` | **Missing** (Low) | Deform surface |
| `InsertFreeform` | **Missing** (Low) | Freeform surface push/pull |
| `InsertMoldCavity` | **Missing** (Low) | Mold cavity |

---

### 2.8 Configuration API (`IConfiguration` / `IConfigurationManager`)

| Operation | Status | Tool / Notes |
|---|---|---|
| `GetConfigurationNames` | Implemented | `list_configurations` |
| `ShowConfiguration2` (activate) | Implemented | `solidworks_activate_configuration` and post-create verification |
| `AddConfiguration3` (create) | Implemented | `solidworks_create_configuration` for independent configurations |
| `CopyConfiguration` | **Missing** (Low) | Copy config with all properties |
| `DeleteConfiguration2` | Implemented (typed) | `solidworks_delete_configuration` safely removes one non-active configuration with list readback verification |
| `IConfiguration.Name` | Implemented (typed) | `solidworks_get_configuration` resolves one exact configuration without activating it |
| `IConfiguration.Comment` | Implemented (typed) | `solidworks_get_configuration` returns the current configuration description |
| Design table (`IDesignTable`) | Simulated | `manage_design_table` (no real COM) |
| `GetDesignTable` | **Missing** (Medium) | Read existing design table |
| `InsertFamilyTableMate` | **Missing** (Low) | Mate config in family table |
| Explode states per config | **Missing** (Low) | Per-config explode |
| Display states | **Missing** (Low) | Appearance per config |
| Derived configurations | **Missing** (Low) | Linked child configs |

---

### 2.9 Selection API (`IModelDocExtension::SelectByID2` / `ISelectionMgr`)

| Operation | Status | Tool / Notes |
|---|---|---|
| `SelectByID2` (by entity name + type) | Implemented | Internal — used by fillet, chamfer, cut |
| `SelectByID2` (by coordinate) | Implemented | Internal — coordinate-based edge selection |
| `Select2` (via `IFeature`) | Implemented | Internal — sweep/loft profile selection |
| `ClearSelection2` | Implemented | Internal |
| `ForceRebuild3` (pre-selection rebuild) | Implemented | Internal |
| `GetSelectedObjectCount2` | Implemented | `get_selection_state` returns a bounded selection count |
| `GetSelectedObject6` | Implemented | `get_selection_state` returns serializable per-item metadata only |
| `ISelectionMgr::GetSelectedObjectsComponent3` | Implemented | `get_selection_state` includes the optional owning component name |
| Selection filters | **Missing** (Low) | Filter selection to faces/edges/vertices |

---

### 2.10 Equations / Global Variables (`IEquationMgr`)

| Operation | Status | Tool / Notes |
|---|---|---|
| Read equation value | Implemented | `solidworks_get_equation` reads one named equation or global variable |
| `GetCount` / `Equation` | Implemented | `solidworks_list_equations` provides bounded typed rows |
| `Add2` / `SetEquationAndConfigurationOption` | Implemented | `solidworks_set_equation` creates or updates with readback verification |
| `Delete` | Implemented | `solidworks_delete_equation` removes a named row with readback verification |
| Global variables | Implemented | Global-variable status is returned with each equation row |
| Linked values | **Missing** (Medium) | Read linked dimension values |

---

### 2.11 Mass Properties (`IMassProperty`)

| Operation | Status | Tool / Notes |
|---|---|---|
| Volume | Implemented | `calculate_mass_properties` |
| Surface area | Implemented | `calculate_mass_properties` |
| Mass | Implemented | `calculate_mass_properties` |
| Center of mass | Implemented | `calculate_mass_properties` |
| Moments of inertia (Ixx, Iyy, Izz, Ixy, Ixz, Iyz) | Implemented | `calculate_mass_properties` |
| Principal axes | Implemented | `calculate_mass_properties` |
| Per-body mass props (multi-body) | **Missing** (Medium) | Multi-body breakdowns |
| Per-component mass props (assembly) | **Missing** (Medium) | Assembly component mass rollup |
| Override mass / center of mass | **Missing** (Low) | Custom mass overrides |

---

### 2.12 Geometry / Topology API (`IFace2`, `IEdge`, `IVertex`, `IBody2`)

| Operation | Status | Tool / Notes |
|---|---|---|
| `GetFaces` | Implemented | `solidworks_list_part_topology` enumerates revision-bound face capabilities |
| `IFace2::GetArea` | Implemented | Face area is returned in mm2 by `solidworks_list_part_topology` |
| `IFace2::Normal` | Implemented | Face normal is returned by `solidworks_list_part_topology` when SolidWorks supplies it |
| `IEdge::GetCurve` | Implemented | Curve identity is returned by `solidworks_list_part_topology` when available |
| `IEdge::GetLength` | Implemented | Edge length is returned in mm by `solidworks_list_part_topology` |
| `IVertex::GetPoint` | Implemented | Vertex point is returned in mm by `solidworks_list_part_topology` |
| `IBody2::GetMassProperties` | **Missing** (Medium) | Per-body mass properties |
| Bounding box (`IPartDoc::GetPartBox`) | Implemented | `solidworks_get_bounding_box` returns active-part extents in mm |
| Ray intersection | **Missing** (Low) | Raycast for selection helpers |
| Sheet metal flat pattern geometry | Implemented | `solidworks_list_sheet_metal_feature_geometry` reads `IFeature::GetFaces` for flat-pattern and bend features, then issues only opaque face IDs |

---

### 2.13 Materials API (`IPartDoc` / `IMaterial`)

| Operation | Status | Tool / Notes |
|---|---|---|
| `GetMaterialPropertyName2` | Implemented | `solidworks_get_material_assignment` reads the active part configuration |
| `SetMaterialPropertyName2` | Implemented | `solidworks_set_material_assignment` verifies the active part configuration update |
| Material library (`ISwAppExt::GetMaterialDatabaseFileNames`) | **Missing** (Medium) | List available materials |
| Custom material creation | **Missing** (Low) | Define new material in library |
| Material appearance / color | **Missing** (Low) | Visual material properties |
| `IPartDoc::DeleteMaterialProperty` | **Missing** (Low) | Remove material assignment |

---

### 2.14 Reference Geometry (`IRefPlane`, `IRefAxis`, `IRefPoint`)

| Operation | Status | Tool / Notes |
|---|---|---|
| Select named plane for sketch | Implemented | `create_sketch` resolves "Top/Front/Right" |
| `InsertRefPlane` (reference plane) | Implemented (typed) | `solidworks_create_reference_plane` creates a bounded offset from Front/Top/Right |
| `InsertRefAxis` | **Missing** (Medium) | Create a reference axis |
| `InsertRefPoint` | **Missing** (Low) | Create a reference point |
| Mate references | **Missing** (Low) | Persistent mate references on a model |
| Bounding box reference | **Missing** (Low) | Auto bounding box reference geometry |

---

### 2.15 Appearances / Display (`IDisplayStateMgr`, `IAppearanceSetting`)

| Operation | Status | Tool / Notes |
|---|---|---|
| Set body color | **Missing** (Medium) | Per-body/face appearance |
| Assign material appearance | **Missing** (Low) | Visual texture/finish |
| Display states | **Missing** (Low) | Per-configuration visual states |
| `IModelDoc2::ViewZoomtofit2` | **Missing** (Low) | Zoom-to-fit for screenshots |
| Render with PhotoView 360 | **Missing** (Low) | High-fidelity render |
| Section view (3D) | **Missing** (Low) | In-session clipping planes |

---

### 2.16 File Format Import/Export (Extended)

| Operation | Status | Tool / Notes |
|---|---|---|
| STEP export | Implemented | `export_step` |
| IGES export | Implemented | `export_iges` |
| STL export | Implemented | `export_stl` |
| PDF export | Implemented | `export_pdf` |
| DWG/DXF export | Implemented | `export_dwg` |
| Image export (JPG/PNG) | Implemented | `export_image` |
| 3MF export | **Missing** (Medium) | Additive manufacturing format |
| OBJ export | **Missing** (Low) | Mesh exchange format |
| ACIS (SAT) export | **Missing** (Low) | ACIS solid exchange |
| Parasolid export | **Missing** (Low) | Parasolid kernel exchange |
| VRML export | **Missing** (Low) | Legacy 3D web format |
| AMF export | **Missing** (Low) | Additive manufacturing format v2 |
| DXF flat-pattern export | Implemented | `solidworks_export_flat_pattern_dxf` exports a controlled artifact using `IPartDoc::ExportToDWG2` and explicit sheet-metal option flags |
| eDrawings export | **Missing** (Low) | SW viewer format |
| Import STEP/IGES | Implemented (controlled) | `solidworks_open_model` accepts only a STEP/IGES leaf file under `model_input_dir` and imports it through `OpenDoc6` as a part |
| Import DXF/DWG | **Missing** (Low) | Import 2D CAD geometry |

---

### 2.17 Pack and Go / References

| Operation | Status | Tool / Notes |
|---|---|---|
| `IModelDocExtension::GetPackAndGo` | Implemented | `pack_and_go_assembly` (native path) |
| `IPackAndGo::SavePackAndGo` | Implemented | `pack_and_go_assembly` |
| `IPackAndGo::SetSaveToName` | Implemented | Internal |
| `IPackAndGo::FlattenToSingleFolder` | Implemented | Internal |
| `GetDependencies2` (fallback) | Implemented | `save_assembly` include_references |
| Update file references (`ReplaceReferencedDocument`) | **Missing** (Medium) | Repath a reference |
| Find and repair broken references | **Missing** (Medium) | Reference repair workflow |
| `IModelDoc2::FileFindReferences` | **Missing** (Low) | List where-used |

---

### 2.18 Macros and VBA (`ISldWorks::RunMacro2`, `IVBAMacro`)

| Operation | Status | Tool / Notes |
|---|---|---|
| Generate VBA code (template) | Implemented | `generate_vba_code` |
| Run an existing VBA macro | Implemented (trusted) | `solidworks_execute_trusted_macro` -> `RunMacro2` |
| Start/stop macro recording | Simulated | `automation_start_macro_recording` |
| Export/import macro | **Missing** (Low) | .swp file management |

---

### 2.19 Simulation / Analysis (SolidWorks Simulation / Flow)

> These require the SolidWorks Simulation and/or Flow Simulation add-in to be licensed and loaded.

| Operation | Status | Tool / Notes |
|---|---|---|
| FEA study creation | **Missing** (Low) | COSMOSWORKS/SW Simulation add-in |
| FEA mesh generation | **Missing** (Low) | SW Simulation |
| FEA result extraction | **Missing** (Low) | Stress, displacement results |
| Flow simulation | **Missing** (Low) | SW Flow Simulation add-in |
| Motion study | **Missing** (Low) | SW Motion add-in |
| Fatigue / design study | **Missing** (Low) | SW Simulation Premium |

---

## 3. Missing High-Priority Tools

These are the most impactful gaps — operations that a typical modeling workflow needs and that the current server cannot handle with real COM calls.

---

### Completed: `add_chamfer` MCP Tool

**Implementation:** `modeling.py` now registers the real COM-backed tool. It requires a positive distance and explicit edge references, so it never reuses a stale UI selection. The mock, circuit-breaker, and connection-pool adapters all forward the same operation.

**Resulting contract:**
```python
# modeling.py — mirror AddFilletInput
class AddChamferInput(CompatInput):
    distance: float = Field(gt=0, description="Chamfer distance in millimeters")
    edge_names: list[str] = Field(min_length=1, description="Explicit edge references")
    
async def add_chamfer(input_data: AddChamferInput) -> dict[str, Any]:
    result = await adapter.add_chamfer(input_data.distance, input_data.edge_names)
    ...
```

---

### 3.2 Configuration Management Tools

**Why needed:** Almost all real SW parts have multiple configurations (material variants, size families). Without the ability to switch, create, and inspect configurations, the server cannot support parametric variant workflows.

**Remaining tools to add:**
- `get_active_configuration()` → `IModelDoc2::ConfigurationManager::ActiveConfiguration`
- `add_configuration(name: str, copy_from: str | None)` → derived/copy configuration support

**Adapter method:** `pywin32_adapter.py` via the COM executor.

---

The typed workflow now implements `solidworks_list_configurations`,
`solidworks_activate_configuration`, and
`solidworks_create_configuration(configuration_name, description="")`. The
create tool uses `AddConfiguration3` for an independent configuration and only
returns success after the new configuration is confirmed active. The remaining
configuration work is active-configuration inspection and copy/derived
configurations. `solidworks_delete_configuration` now rejects an active
configuration and uses `DeleteConfiguration2` with a configuration-list
readback for non-active configurations.

### 3.3 Custom Properties (Real Implementation)

**Why needed:** `get_file_properties()` returns hard-coded simulated data. Custom properties are used for BOM, revision tracking, and PLM integrations. This is one of the most-used SW API features in enterprise workflows.

**Tools to add:**
- `get_custom_properties(config_name: str | None)` → `IModelDoc2::Extension::CustomPropertyManager::Get6`
- `set_custom_property(name: str, value: str, config_name: str | None)` → `ICustomPropertyManager::Set3`
- `delete_custom_property(name: str, config_name: str | None)` → `ICustomPropertyManager::Delete2`

---

### 3.4 Material Assignment (Real Implementation)

**Why needed:** `get_material_properties()` is entirely simulated. Assigning the correct material drives mass properties, simulation, and BOM data. This is a basic modeling task.

**Original plan (see implementation update below):**
- `list_available_materials(library: str | None)` → filesystem scan of SW material DB

---

### 3.5 Feature Pattern Tools

**Implemented typed baseline:** `solidworks_create_feature_linear_pattern`,
`solidworks_create_feature_circular_pattern`, and `solidworks_mirror_feature`
accept only current session-owned feature capabilities. Linear patterns are
one-direction (`X`/`Y`/`Z`); circular patterns use a current origin `axis_*`;
mirrors use a current `ref_*` plane. Two-direction, body, curve, fill, and
variable patterns remain follow-on work.

**Future expansion:**
- `create_linear_pattern(feature_name: str, direction: str, spacing: float, count: int, direction2: str | None, spacing2: float | None, count2: int | None)` → `IFeatureManager::FeatureLinearPattern3`
- `create_circular_pattern(feature_name: str, axis: str, angle: float, count: int)` → `IFeatureManager::FeatureCircularPattern3`
- `mirror_feature(feature_name: str, mirror_plane: str)` → `IFeatureManager::InsertMirrorFeature2`

---

### 3.6 Hole Wizard Tool

**Implemented typed baseline:** `solidworks_create_hole_wizard` creates an
ANSI Metric M6 clearance Hole Wizard from an exited, server-owned location sketch.
`solidworks_create_tapped_hole` creates a standard ANSI Metric tapped Hole Wizard
for the bounded presets `M3x0.5`, `M4x0.7`, `M5x0.8`, `M6x1.0`, `M8x1.25`,
`M10x1.5`, `M12x1.75`, and `M16x2.0`, with blind or through-all termination.
The MCP records the full nominal size while passing the nominal diameter seed
(`M6`, for example) expected by the SOLIDWORKS 2019 Hole Wizard database.
MDB paths, arbitrary face selection, and raw selection strings are excluded.
Counterbore, countersink, custom thread classes, and arbitrary Toolbox database
presets remain follow-on work.

**Future expansion:**
- `create_hole_wizard(standard: str, type: str, size: str, depth: float | None, position_x: float, position_y: float, plane: str)` → `IFeatureManager::HoleWizard5`

---

### 3.7 Convert Entities (Sketch)

**Why needed:** "Convert Entities" projects existing model edges/faces into the active sketch. This is used in nearly every sketch-on-face workflow (e.g., pocket on a face that matches the face boundary). Without it, users must manually redraw geometry.

**Implementation update:** `solidworks_convert_sketch_entities` now projects only
server-issued part-edge IDs into the active session sketch through
`ISketchManager::SketchConvertEntities`.

---

### 3.8 Trim / Extend Entities (Sketch)

**Current status:** `solidworks_trim_sketch_entity` trims one server-issued
entity while the same session sketch is active. The caller supplies the entity
ID returned by a prior sketch-creation tool and one trim point in millimetres.
The adapter selects only that registry-backed entity, calls
`ISketchManager::SketchTrim5`, and falls back to `SketchTrim` when the newer
member is unavailable (including older SolidWorks releases). It clears the COM
selection set before and after the operation.

**Remaining expansion:** Extend geometry and power-trim modes need separate,
typed behavior contracts. The current scope does not accept raw SolidWorks
entity names or the UI's ambient selection state.

**Why needed:** After adding multiple intersecting sketch entities (circles, lines), you need to trim the unwanted portions. This is required for any complex profile (slots with rounded ends, keyways, etc.).

**Superseded raw-interface proposal:**
- `sketch_trim(entity_name: str, trim_location_x: float, trim_location_y: float)` → `ISketchManager::SketchTrim5`

---

### 3.9 Run Existing Macro

**Why needed:** The `generate_vba_code` tool generates code but cannot execute it. Closing the loop by running a saved `.swp` macro file would unlock complex operations not directly supported by the MCP API surface.

**Tools to add:**
- `run_macro(macro_path: str, module_name: str, sub_name: str)` → `ISldWorks::RunMacro2`

---

### 3.10 Assembly Component Operations

**Current status:** The typed workflow can insert a controlled saved part, list
safe component summaries, issue revision-bound opaque face/edge IDs, and create
typed `AddMate5` constraints. Persistent references remain server-side and are
re-resolved immediately before the COM call. `solidworks_list_assembly_mates`
issues revision-bound mate IDs, and `solidworks_delete_assembly_mate` deletes
only one such current ID.

---

### 3.11 Bounding Box

`solidworks_get_bounding_box` now uses `IPartDoc::GetPartBox(True)` for an
active part and normalizes the native metre array into typed `min_point_mm`,
`max_point_mm`, and `size_mm` output. Assembly-wide bounding boxes and
reference-geometry bounding boxes remain follow-on work.

---

### 3.12 Shell Feature

**Current status:** `solidworks_create_shell` removes only server-issued
faces from the active part and applies an explicit wall thickness through
`IFeatureManager::InsertShell`. The topology IDs are invalidated after the
model-changing operation.

---

### 3.13 Advanced Surface Features

`solidworks_create_draft` uses `IFeatureManager::InsertMultiFaceDraft` with a
bounded angle and current, server-issued face IDs. `solidworks_create_thicken`
uses `FeatureBossThicken` or `FeatureCutThicken` after resolving one current
surface face. `solidworks_create_wrap` selects a session-owned exited sketch
and one current server-issued face before calling `InsertWrapFeature2`. All
three operations clear their selections after the call and invalidate prior
topology IDs after a successful mutation.

---

### 3.14 Drawing Views (Real COM Calls)

**Current status:** `solidworks_create_drawing_view` uses
`IDrawingDoc::CreateDrawViewFromModelView3` for controlled saved part/assembly
sources, and `solidworks_list_drawing_views` uses `GetFirstView`/`GetNextView`.
`solidworks_create_projected_drawing_view` resolves an active-drawing view ID,
selects only that resolved view, and calls `CreateProjectionView`. The legacy
drawing tools remain simulated.

**Implementation update:** `solidworks_create_section_drawing_view` now
creates a section view from a revision-bound source drawing view ID and an
explicit two-point cutting line.

---

### 3.15 Sheet Metal Creation and Detail

**Current status:** `solidworks_get_sheet_metal_info` discovers sheet-metal state,
`solidworks_set_sheet_metal_flat_state` uses `IModelDoc2::SetBendState` for
the flattened or folded state, and `solidworks_export_flat_pattern_dxf` exports a saved,
session-owned `.sldprt` through `IPartDoc::ExportToDWG2`. DXF outputs remain
under the configured artifact root.

**Implementation update:** `solidworks_create_sheet_metal_base_flange` creates
a base flange from an exited session sketch,
`solidworks_create_sheet_metal_edge_flange` creates an edge flange from a
server-issued part edge, and `solidworks_set_sheet_metal_k_factor` updates and
verifies the active K factor.

**Remaining tools (in order of priority):**
1. Bend-table and bend-note inspection/editing after insertion

**Implementation update:** `solidworks_list_sheet_metal_feature_geometry`
now enumerates each flat-pattern or bend feature's associated faces through
`IFeature::GetFaces`. The workflow converts every face to a revision-bound,
server-owned opaque ID; it does not expose COM objects or raw SolidWorks
selection strings.

`solidworks_insert_sheet_metal_bend_notes` resolves the current flat-pattern
feature internally and calls `IPartDoc::InsertBendNotes`.
`solidworks_insert_sheet_metal_bend_table` calls `IPartDoc::InsertBendTable`
with a bounded model position, an integer start value, and the configured
SolidWorks default template. Neither tool accepts a caller-controlled template
path or a raw COM feature reference.

---

### 3.15 Equation Manager

The typed surface now provides `solidworks_list_equations`,
`solidworks_get_equation`, `solidworks_set_equation`, and
`solidworks_delete_equation`. The implementation uses `IEquationMgr::GetCount`,
`Equation`, `Add2`, `SetEquationAndConfigurationOption`, and `Delete`; all
writes use a server-constructed assignment and a post-operation readback.
Expression writes apply to all configurations. Linked values, external equation
files, configuration-specific equation scopes, and real-model end-to-end
verification remain follow-on work.

---

## 4. Missing Medium/Low Priority Tools

### 4.1 Medium Priority

| Feature | SW API Method | Notes |
|---|---|---|
| Reference plane creation | `IFeatureManager::InsertRefPlane` | Implemented through the typed reference-plane workflow |
| Reference axis | `IFeatureManager::InsertRefAxis` | Needed for circular patterns |
| `InsertThicken` | `IFeatureManager::InsertThicken` | Thicken surface to solid |
| 3MF export | `IModelDoc2::SaveAs4` with 3MF type | 3D printing |
| Select active session-owned document | `ISldWorks::ActivateDoc3` | Implemented by `solidworks_activate_document`; `solidworks_list_documents` exposes only documents already owned by the current workflow session |
| List open documents | `ISldWorks::GetDocuments` | Multi-document context |
| Ordinate dimensions (drawing) | `IModelDocExtension::AddOrdinateDimension` | Drawing chain dims |
| Update file references | `ISldWorks::ReplaceReferencedDocument` | Reference repair |
| 3D sketch | `ISketchManager::Insert3DSketch` | Space curves |
| Sketch text | `ISketchManager::InsertSketchText` | Engraved labels |
| Sketch split | `ISketchManager::SketchSplit` | Split entity at a point |
| Sketch fillet | `ISketchManager::SketchFillet` | Round corners in sketch |
| Display states | `IDisplayStateMgr` | Per-config appearance |

### 4.2 Low Priority

| Feature | SW API Method | Notes |
|---|---|---|
| Rib feature | `IFeatureManager::InsertRib` | Plastic-part ribs |
| Dome feature | `IFeatureManager::InsertDome` | Curved dome |
| Split body | `IFeatureManager::InsertSplit` | Multi-body splitting |
| Wrap feature | `IFeatureManager::InsertWrap2` | Wrap sketch on curved surface |
| Deform feature | `IFeatureManager::InsertDeform` | Flex/deform body |
| Freeform surface | `IFeatureManager::InsertFreeform` | Push-pull surface |
| Weldments | `IFeatureManager::InsertStructuralMember` | Structural member |
| Component patterns (assembly) | `InsertLinearComponentPattern` | Repeat component |
| Mirror component (assembly) | `InsertMirrorComponent` | Mirror in assembly |
| Explode view | `IAssemblyDoc::ExplodeView` | Exploded assembly view |
| Auto-explode | `IAssemblyDoc::AutoExplode` | Auto-generate explode |
| Render (PhotoView) | `ISldWorks::GetAddInObject("SldWorks.Application.PhotoView360")` | High-fidelity render |
| Set body color | `IModelDocExtension::SetUserPreferenceTextValue` | Appearance |
| eDrawings export | `ISldWorks::RunCommand` | eDrawings publish |
| OBJ/VRML/AMF export | `IModelDoc2::SaveAs4` | Mesh formats |
| Dynamic mirror sketch | `ISketchManager::InsertSketchMirror` | Live symmetry |
| Sketch picture | `ISketchManager::InsertSketchPicture` | Image reference |
| Print drawing | `IModelDoc2::Print3` | Paper/plotter output |
| Add-in management | `ISldWorks::GetAddInObject` | Add-in integration |
| FEA stub | SW Simulation COM API | Requires Simulation license |
| Motion study | SW Motion COM API | Requires Motion license |
| Flow simulation | SW Flow COM API | Requires Flow license |
| Material library management | Filesystem scan + DB | List available materials |
| Derived configurations | `IModelDoc2::AddConfiguration3` with parent | Config families |
| Export revision table | `IRevisionTable` | Drawing revision history |
| Layer management (drawing) | `ILayerMgr` | Drawing layer control |

---

## 5. Summary Table

| SW API Category | Approx. API Operations | Implemented (real COM) | Implemented (simulated) | Missing | % Real Coverage |
|---|---|---|---|---|---|
| Application (`ISldWorks`) | 30 | 6 | 1 | 23 | 20% |
| Document core (`IModelDoc2`) | 40 | 18 | 2 | 20 | 45% |
| Part (`IPartDoc`) | 25 | 4 | 1 | 20 | 16% |
| Assembly (`IAssemblyDoc`) | 30 | 2 | 1 | 27 | 7% |
| Drawing (`IDrawingDoc`) | 50 | 7 | 20 | 23 | 14% |
| Sketch (`ISketchManager`) | 35 | 17 | 0 | 18 | 49% |
| Feature Manager (`IFeatureManager`) | 45 | 7 | 0 | 38 | 16% |
| Configuration (`IConfiguration`) | 20 | 1 | 1 | 18 | 5% |
| Selection (`ISelectionMgr`) | 15 | 5 | 0 | 10 | 33% |
| Equations (`IEquationMgr`) | 10 | 0 | 0 | 10 | 0% |
| Mass Properties (`IMassProperty`) | 10 | 7 | 0 | 3 | 70% |
| Geometry / Topology (`IFace2`, `IEdge`) | 20 | 0 | 0 | 20 | 0% |
| Materials (`IMaterial`) | 10 | 0 | 1 | 9 | 0% |
| Reference Geometry | 10 | 1 | 0 | 9 | 10% |
| Appearances / Display | 15 | 0 | 0 | 15 | 0% |
| Export formats | 20 | 7 | 1 | 12 | 35% |
| Import formats | 10 | 0 | 0 | 10 | 0% |
| Pack and Go / References | 10 | 5 | 0 | 5 | 50% |
| Macros / VBA | 8 | 0 | 3 | 5 | 0% |
| Simulation / FEA / Flow | 20 | 0 | 0 | 20 | 0% |
| **TOTAL** | **~433** | **~87** | **~31** | **~315** | **~20%** |

> **Note on "simulated":** Tools marked simulated accept parameters and return plausible-looking responses but do not make any COM calls to SolidWorks. They will not modify a real document. These are stubs awaiting a real implementation.

> **Note on counting:** "API operations" are counted at the granularity of meaningful user-facing operations (not every individual overload of every COM method). The ~433 figure is an estimate derived from the major interface areas; the actual SW API has thousands of individual method signatures.

---

### Recommended Implementation Sequence

Based on impact and dependency order:

1. **`add_chamfer` MCP tool** — 1 hour; adapter code already written
2. **`set_material` / `get_material`** — 1 day
3. **Feature patterns** (linear, circular, mirror) — 2 days
4. **Hole Wizard** — 1 day
5. **`sketch_trim` / `sketch_convert_entities`** — 1 day
6. **Reference plane / axis creation** — 1 day
7. **Shell feature** — 0.5 days
8. **Bounding box** — 0.5 days
9. **Assembly: insert component + add mate** — 3 days
10. **Equation Manager** — 2 days
11. **Section drawing views** (COM-backed) — 2 days
12. **Sheet metal (base flange, edge flange, flatten, DXF export)** — 3 days
13. **Run macro** — 0.5 days
