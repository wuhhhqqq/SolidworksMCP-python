# Typed MCP MVP workflow

The prefixed `solidworks_*` tools are a small, typed workflow that runs alongside
the legacy tool catalog. They are the recommended starting point for a new MCP
client because every call returns the same structured `OperationResult` envelope.

## Part-to-export sequence

1. Call `solidworks_create_session` and retain `data.session.session_id`.
2. Call `solidworks_create_part`; retain `data.document.document_id`.
3. For an offset sketch support, call `solidworks_create_reference_plane` with
   `Front`, `Top`, or `Right`, an offset, and a unit. Retain only the returned
   `data.reference_plane.persistent_id`.
4. Call `solidworks_create_sketch` on `Front`, `Top`, or `Right`, or pass that
   current reference-plane ID as `reference_plane_id`; retain the returned
   `data.sketch.persistent_id`.
5. Add geometry with `solidworks_add_rectangle`, `solidworks_add_line`,
   `solidworks_add_circle`, `solidworks_add_point`, `solidworks_add_arc`,
   `solidworks_add_centerline`, `solidworks_add_spline`, `solidworks_add_polygon`, or
   `solidworks_add_ellipse`. While that sketch remains active, use
   `solidworks_trim_sketch_entity` to trim one returned entity at a point in
   millimetres. It accepts only the returned entity ID, never a SolidWorks UI
   selection string. For advanced corner editing, use
   `solidworks_split_sketch_entity`, `solidworks_fillet_sketch_corner`, or
   `solidworks_chamfer_sketch_corner` before exiting the sketch; all three also
   accept only current server-issued entity IDs.
6. Call `solidworks_exit_sketch` using the same sketch reference.
7. Call `solidworks_create_extrusion`, `solidworks_create_cut_extrude`, or
   `solidworks_create_revolve` with that last exited sketch. A revolve requires
   a centreline in that sketch for the SolidWorks rotation axis.
8. For a sweep, create and exit both a closed profile sketch and a distinct
   path sketch, then call `solidworks_create_sweep` with their returned sketch
   references. For a loft, create and exit two or more profile sketches, then
   call `solidworks_create_loft` with their references in loft order. These
   tools do not accept guessed SolidWorks selection strings or arbitrary sketch
   names.
9. To pattern a newly returned feature, call
   `solidworks_create_feature_linear_pattern` with its current feature ID and
   a fixed `X`, `Y`, or `Z` direction. For a circular pattern, first call
   `solidworks_get_reference_axis`, then pass that returned `axis_*` ID to
   `solidworks_create_feature_circular_pattern`. Pattern output is a new,
   current feature ID; the source feature ID is expired by the mutation.
   On localized templates that do not enumerate native origin axes, requesting
   an axis creates a named `MCP_OriginAxis_X`, `MCP_OriginAxis_Y`, or
   `MCP_OriginAxis_Z` helper from the matching pair of origin planes.
10. To mirror a current feature, call `solidworks_get_reference_plane` for a
    standard origin plane in the current revision, then pass the returned
    `ref_*` ID to `solidworks_mirror_feature`. A custom offset plane is also
    a `ref_*`, but it expires after any successful feature write.
11. To create a Hole Wizard, create a location sketch on a standard or current
    reference plane, add one or more `solidworks_add_point` points, exit the
    sketch, then call `solidworks_create_hole_wizard`. The first typed preset
    is an ANSI Metric M6 clearance hole with bounded diameter and depth. For a
    standard tapped hole, call `solidworks_create_tapped_hole` with one of the
    supported `M3x0.5` through `M16x2.0` presets and set `through_all` when the
    thread must pass through the part. In through-all mode, `depth` is retained
    for a consistent request shape but SOLIDWORKS ignores it. This creates a
    SOLIDWORKS Hole Wizard
    tapped-hole feature; cosmetic thread and callout behavior remains controlled
    by the active SOLIDWORKS document settings.
12. Call `solidworks_export_file` with `format_type` of `step` or `stl`.
13. Call `solidworks_close_session` after exiting any active sketch when the
   client no longer needs its workflow references.

### 3D chamfer sequence

For a solid edge chamfer, first call `solidworks_list_part_topology` with
`entity_types: ["edge"]`. Select the returned opaque `entity_id` values by
their displayed length and curve metadata, then call `solidworks_create_chamfer`.
`equal_distance` uses the requested distance on both adjacent faces;
`distance_angle` uses the distance plus `angle_degrees`. Do not pass
`Edge<1>`-style names and do not reuse edge IDs after any feature mutation.
The response includes a non-blocking edge-length preflight and SolidWorks
feature/rebuild diagnostics. A preflight warning is a reason to verify the
edge or reduce the distance, not a claim that the BREP is invalid.

The export tool deliberately accepts an `artifact_name`, not an arbitrary output
path. Files are created below the configured `data_dir/artifacts` directory and
the result contains their absolute path, size, and SHA-256 digest.

## Native model input and output

Existing model files are opened only from the administrator-configured model input
directory. By default this is `data_dir/inputs`. If
`SOLIDWORKS_MCP_MODEL_INPUT_DIR` is set, it must still be a subdirectory of
`data_dir`; set `SOLIDWORKS_MCP_DATA_DIR` to relocate the whole server-owned
storage root. The server service account must be the only account allowed to
write `data_dir`, its `inputs` directory, and `artifacts` directory. Place a
native `.sldprt`, `.sldasm`, or `.slddrw` file there, then call
`solidworks_open_model` with its leaf `file_name`. Paths, parent-directory
references, and non-native extensions are rejected before SolidWorks COM is
called.

Call `solidworks_save_model` with the session, document reference, and an
`artifact_name` to save a native copy. The server derives the correct extension
from the opened document type and saves only beneath
`data_dir/artifacts/<session>/<operation>/`. The response includes the updated
document revision plus the output file's path, size, and SHA-256 digest.

## Documents, features, and configurations

Use `solidworks_create_assembly` or `solidworks_create_drawing` to create a
template-based document in the current workflow session. Like parts, they
return a session-owned `document_id`; use `solidworks_save_model` to create a
controlled native artifact.

To assemble a saved part, create and save the part **before** creating the
target assembly. Call `solidworks_insert_assembly_component` with the active
assembly document reference and the saved part's session document reference.
The tool does not accept a file path: it verifies that the source is a
same-session `.sldprt` produced by `solidworks_save_model` under the controlled
artifact root. The public `x`, `y`, and `z` coordinates accept `mm` (default),
`m`, or `in`; the server converts them to SolidWorks system units (metres).
Use `solidworks_list_assembly_components` for a bounded component page. Its
output contains only component ID, display name, document type, and standard
pagination fields.

To create an assembly mate, first call
`solidworks_list_assembly_mate_entities` for the active assembly. It returns a
bounded page of faces and edges with `entity_id`, component ID, entity type, and
display name. The SolidWorks persistent reference remains inside the server; do
not construct or retain raw selection strings. Pass two distinct current
`entity_id` values to `solidworks_create_assembly_mate`. It supports
`coincident`, `concentric`, `parallel`, `perpendicular`, `tangent`, `distance`,
and `angle` mates. Distance accepts `mm` (default), `m`, or `in`; angle is in
degrees. Distance and angle mates require their corresponding dimension.

To create a drawing view, save a same-session part or assembly first, create a
drawing, then call `solidworks_create_drawing_view` with the drawing document
reference and saved model document reference. The source model path stays on
the server: the client can choose only a standard orientation (`front`, `back`,
`top`, `bottom`, `left`, `right`, `isometric`, `dimetric`, or `trimetric`) and
an `x`/`y` sheet position in `mm` (default), `m`, or `in`. The returned view
contains only an opaque view ID and display name. Use
`solidworks_list_drawing_views` for a bounded page of model views; the sheet
pseudo-view is never returned.

To add an orthographic projection, call
`solidworks_create_projected_drawing_view` with the active drawing document,
a `source_view_id` returned by the drawing-view tools, and a sheet position.
The server resolves that identifier inside the current drawing before it uses
`CreateProjectionView`; it never accepts a COM object, model path, or arbitrary
selection from the client.

Use `solidworks_set_custom_property` to create or replace a text custom
property, then `solidworks_list_custom_properties` to inspect a bounded page.
Both tools support `scope="document"` (the default) and
`scope="active_configuration"`. For configuration-specific data, activate the
desired configuration first; the tool uses the configuration SolidWorks has
verified as active, rather than accepting an arbitrary configuration name.

Use `solidworks_list_features` to inspect a bounded feature-tree page. It
accepts `include_suppressed`, `limit` (1--100), and `offset`, and returns only
the feature name, feature type, suppression state, position, and standard
pagination fields. Use `solidworks_list_configurations` similarly to inspect
configuration names.

`solidworks_create_configuration` accepts a new configuration name and an
optional description for the active session document. The server rejects
existing names, calls `AddConfiguration3`, confirms the new name appears in
SolidWorks, activates it with `ShowConfiguration2`, and verifies the active
name before returning a new document revision. It creates independent
configurations only; copying and derived configurations are not yet exposed.

`solidworks_delete_configuration` deletes a named inactive configuration only.
The workflow rejects deletion of the configuration that SolidWorks currently
reports as active, and the adapter re-lists configurations after
`DeleteConfiguration2` before reporting success.

`solidworks_activate_configuration` accepts the active session document and an
existing configuration name. The server reactivates the tracked document,
calls SolidWorks `ShowConfiguration2`, verifies the name that SolidWorks made
active, and returns a new document revision. Use that returned document
reference for downstream analysis or export calls.

For drawing revisions, call `solidworks_create_drawing_revision_table` first
and retain `data.revision_table.revision_table_id`. Then use
`solidworks_list_drawing_revisions`, `solidworks_add_drawing_revision`, or
`solidworks_delete_drawing_revision`. Table IDs are revision-bound and become
stale after any drawing mutation, so use the ID returned by the latest table
creation/listing operation.

## Geometry and parametrics

`solidworks_get_bounding_box` reads an active part's axis-aligned extents using
`IPartDoc::GetPartBox`. It returns `min_point_mm`, `max_point_mm`, and
`size_mm`; clients never receive the underlying COM array.

Use `solidworks_list_equations` to page through equations and global variables,
or `solidworks_get_equation` to read one by its returned `name`. The response
contains only a stable index, name, expression, and global-variable flag.
`solidworks_set_equation` creates or replaces one assignment, while
`solidworks_delete_equation` removes one. Both tools require an active part,
construct the assignment on the server, and return SolidWorks readback data.
An equation name cannot include quotation marks or `=`; an expression cannot
include `=`, so a caller cannot submit a compound assignment. Writes apply to
all configurations through `IEquationMgr`.

## State guarantees

- The server keeps COM objects private; clients receive document and entity
  references only.
- A session serializes operations against its SolidWorks instance.
- The typed, stateful workflow rejects `enable_connection_pooling=true`; one
  workflow must keep document state on one SolidWorks COM connection.
- Extrusion only accepts the last sketch which that session exited. This rejects
  stale UI selections instead of accidentally extruding a different sketch.
- Sweep and loft accept only distinct sketches that the same session created
  and exited. The workflow resolves those references on the server before it
  selects the actual profile, path, or guide sketch through COM.
- Every sketch reference is bound to its document revision. Any successful
  document mutation expires prior exited-sketch references; create and exit a
  fresh sketch before the next feature operation.
- Sketch edits, including trimming, require the currently active sketch in the
  same session. The adapter clears SolidWorks selection state before and after
  each trim, so a manual selection cannot change its target.
- Reference-plane IDs are opaque, session-owned, and bound to the document
  revision. Creating any new feature, changing a configuration, or saving the
  document expires them; create a fresh plane before using it for a new sketch.
- Origin-plane and origin-axis capabilities are read-only `ref_*` and `axis_*`
  references. They are also revision-bound, so acquire them after the feature
  whose pattern or mirror operation they will support.
- Typed feature IDs are current-revision, session-owned capabilities. A feature
  pattern, mirror, or Hole Wizard expires its input feature and any previously
  issued axis or plane references for that document.
- Assembly mate entity IDs are bound to one active assembly document revision.
  Any successful assembly mutation, including component insertion or mate
  creation, expires prior IDs. List mate entities again before the next mate.
- The shared COM lock rechecks sketch ownership immediately before document
  activation. Sketch entry and exit update workflow state while that lock is
  still held, so another session cannot switch documents during sketch editing.
- Failed calls return `status="failed"`, an error code, and a suggested next step.

## Result contract

Each typed tool returns an `OperationResult` object. Successful calls use
`status="succeeded"` and place returned references and data in `data`. Failed
calls use `status="failed"` and include `error.code`, `error.message`, and
`error.suggestion`. Export results also contain an `artifacts` list with the
absolute path, byte size, and SHA-256 checksum of each generated file.

## Trusted VBA macro execution

The typed surface can also execute an existing, administrator-provisioned
SolidWorks VBA project (`.swp`). This is deliberately opt-in:

```text
SOLIDWORKS_MCP_ENABLE_TRUSTED_MACROS=true
SOLIDWORKS_MCP_TRUSTED_MACRO_DIR=D:\\SolidWorksMcpTrustedMacros
```

The configured directory must be writable only by the administrator running the
MCP server. For each macro project, provision a `.swp` and a matching JSON
manifest. The manifest is the only place where the VBA module and entry-point
procedure are declared:

```json
{
  "macro_id": "create_bracket",
  "display_name": "Create standard bracket",
  "description": "Creates a standard bracket in the active document.",
  "file": "create_bracket.swp",
  "module_name": "BracketModule",
  "procedure_name": "Main"
}
```

Run a macro with three typed calls:

1. `solidworks_list_trusted_macros` discovers only valid manifests and never
   reveals local macro paths.
2. `solidworks_prepare_trusted_macro` takes a session and `macro_id`, then
   returns `status="needs_confirmation"`, a one-time confirmation ID, and the
   SHA-256 hash that must be reviewed.
3. `solidworks_execute_trusted_macro` takes that session, confirmation ID, and
   exact hash. The server re-reads the manifest and project immediately before
   calling `RunMacro2`; any content or metadata change is rejected.

The confirmation expires after five minutes by default
(`SOLIDWORKS_MCP_MACRO_CONFIRMATION_TTL_SECONDS`, 30–3600 seconds) and cannot
be replayed. Execution can affect arbitrary SolidWorks documents and files, so
it is marked destructive. Execution is rejected while any workflow session is
editing a sketch. After a successful run, every workflow session's document and
sketch references are invalidated instead of being treated as reliable UI state.

Do not treat generated VBA source text as an executable `.swp` project. A
trusted macro must be created or recorded by SolidWorks, reviewed, and
provisioned outside the MCP request path.

## Current scope

This MVP covers controlled native model input/output; creating part, assembly,
and drawing documents; saved-part assembly insertion and component inspection;
native assembly interference detection for the active session-owned assembly;
real standard drawing-view creation and inspection from a controlled saved part
or assembly, plus projected views from server-issued view IDs; real document and
active-configuration custom-property read/write;
the core sketch/feature workflow including extrude, cut-extrude, revolve,
sweep, loft, standard/offset reference planes and axes, one-direction feature
patterns, feature mirrors, and an ANSI Metric M6 clearance Hole Wizard from a typed
point-location sketch;
shell, rib, dome, multi-face draft, surface thicken, and sketch wrap features using
only current server-issued topology IDs and session-owned exited sketches;
active-part bounding
box and equation/global-variable inspection and editing; feature and configuration
inspection; verified configuration activation; model and mass inspection;
solid-body inspection plus active-configuration material read/write with COM
readback verification;
STEP/STL export; and an administrator-provisioned VBA project through the
controlled trusted macro flow. Section/detail views, drawing annotation editing,
PDM, macro recording, and arbitrary/generated VBA source execution remain
follow-on work and must not be treated as supported by this typed surface yet.
