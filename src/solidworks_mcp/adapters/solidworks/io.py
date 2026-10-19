"""Model I/O mixin for PyWin32 SolidWorks operations."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from loguru import logger

from .. import sw_type_info as _sw_type_info
from ..base import (
    ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED,
    AdapterResult,
    AdapterResultStatus,
    MassProperties,
    SolidWorksModel,
)

try:
    import pythoncom
    import win32com.client
except ImportError:  # pragma: no cover
    pythoncom = SimpleNamespace()
    win32com = SimpleNamespace(client=SimpleNamespace())

try:
    import comtypes  # type: ignore[import-untyped]
    import comtypes.client as _comtypes_client  # type: ignore[import-untyped]
    from comtypes.automation import (
        VARIANT as _ComtypesVariant,  # type: ignore[import-untyped]
    )
    from comtypes.automation import (
        IDispatch as _ComtypesIDispatch,  # type: ignore[import-untyped]
    )

    _COMTYPES_AVAILABLE = True
except ImportError:  # pragma: no cover
    _COMTYPES_AVAILABLE = False
    _ComtypesIDispatch = None
    _ComtypesVariant = None

# SolidWorks type library GUID (stable across SW versions)
_SW_TLB_GUID = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"
# Cached comtypes wrapper module (loaded once per process)
_sw_comtypes_lib: Any = None
# SolidWorks may briefly lag before a newly opened document appears in
# ``GetDocuments``. Keep a process-local path cache so a second adapter does
# not issue another OpenDoc6 during that short window.
_OPEN_DOCUMENT_CACHE: dict[str, tuple[Any, Any]] = {}


def _same_com_reference(left: Any, right: Any) -> bool:
    """Compare late-bound COM wrappers without leaking comparison failures."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False
_SW_CUSTOM_INFO_TEXT = 30
_SW_CUSTOM_PROPERTY_REPLACE_VALUE = 2
_SW_MATE_TYPES = {
    "coincident": 0,
    "concentric": 1,
    "perpendicular": 2,
    "parallel": 3,
    "tangent": 4,
    "distance": 5,
    "angle": 6,
}
_SW_MATE_ALIGNMENTS = {"aligned": 0, "anti_aligned": 1}
_MAX_MATE_ENTITIES_PER_BODY = 10_000
_SW_BOM_TYPES = {"top_level_only": 2, "parts_only": 1, "indented": 3}
_SW_BALLOON_STYLES = {
    "circular": 1,
    "split_circular": 7,
    "hexagonal": 3,
    "square": 11,
}
_SW_BALLOON_FIT_TIGHTEST = 0
_SW_BALLOON_TEXT_CUSTOM = 0
_SW_ORDINATE_DIMENSION_TYPES = {"horizontal": 0, "vertical": 1}
_SW_SURFACE_FINISH_SYMBOLS = {
    "machining_required": 1,
    "machining_prohibited": 2,
    "any_process": 0,
}
_SW_SURFACE_FINISH_LAY_NONE = 0
_SW_BOM_ANCHOR_TYPE_TOP_LEFT = 0
_SW_REVISION_ANCHOR_TYPES = {
    "top_left": 0,
    "top_right": 1,
    "bottom_left": 2,
    "bottom_right": 3,
}
_SW_BOM_INDENTED_NUMBERING_NONE = 0
_SW_DRAWING_PAPER_SIZES = {
    "a0": 0,
    "a1": 1,
    "a2": 2,
    "a3": 3,
    "a4": 4,
    "user_defined": 12,
}
_SW_DRAWING_TEMPLATE_NONE = 0
_SW_DRAWING_STANDARD_DIMENSIONS_M = {
    "a0": (1.189, 0.841),
    "a1": (0.841, 0.594),
    "a2": (0.594, 0.420),
    "a3": (0.420, 0.297),
    "a4": (0.297, 0.210),
}
_SW_EXPORT_TO_DWG_SHEET_METAL = 1
_SW_SHEET_METAL_OPTION_GEOMETRY = 1
_SW_SHEET_METAL_OPTION_HIDDEN_EDGES = 2
_SW_SHEET_METAL_OPTION_BEND_LINES = 4
_SW_SHEET_METAL_OPTION_SKETCHES = 8
_SW_SHEET_METAL_OPTION_MERGE_COPLANAR_FACES = 16
_SW_SHEET_METAL_OPTION_LIBRARY_FEATURES = 32
_SW_SHEET_METAL_OPTION_FORMING_TOOLS = 64
_SW_SHEET_METAL_OPTION_BOUNDING_BOX = 2048
_SW_BEND_STATE_FLATTENED = 2
_SW_BEND_STATE_FOLDED = 3
_MAX_DRAWING_ANNOTATION_TARGETS = 10_000
_MAX_FEATURE_PARAMETER_INDEX = 64
_MAX_EMPTY_FEATURE_PARAMETER_GAP = 8
_DISP_E_MEMBERNOTFOUND = -2147352573
_DISP_E_MEMBERNOTFOUND_UNSIGNED = _DISP_E_MEMBERNOTFOUND + (1 << 32)
_DISP_E_TYPEMISMATCH = -2147352571
_DISP_E_TYPEMISMATCH_UNSIGNED = _DISP_E_TYPEMISMATCH + (1 << 32)
_DISP_E_BADPARAMCOUNT = -2147352562
_DISP_E_BADPARAMCOUNT_UNSIGNED = _DISP_E_BADPARAMCOUNT + (1 << 32)
_ASSEMBLY_ENTITY_LOCATOR_PREFIX = "assembly_entity:"


def _is_member_not_found_error(error: BaseException) -> bool:
    """Return whether a COM failure means the invoked member is unavailable."""
    values = (getattr(error, "hresult", None), *getattr(error, "args", ()))
    return any(
        value in {_DISP_E_MEMBERNOTFOUND, _DISP_E_MEMBERNOTFOUND_UNSIGNED}
        for value in values
        if isinstance(value, int)
    )


def _is_type_mismatch_error(error: BaseException) -> bool:
    """Return whether a COM failure reports an incompatible argument type."""
    values = (getattr(error, "hresult", None), *getattr(error, "args", ()))
    return any(
        value in {_DISP_E_TYPEMISMATCH, _DISP_E_TYPEMISMATCH_UNSIGNED}
        for value in values
        if isinstance(value, int)
    )


def _is_bad_param_count_error(error: BaseException) -> bool:
    """Return whether a COM failure reports an incorrect argument count."""
    values = (getattr(error, "hresult", None), *getattr(error, "args", ()))
    return any(
        value in {_DISP_E_BADPARAMCOUNT, _DISP_E_BADPARAMCOUNT_UNSIGNED}
        for value in values
        if isinstance(value, int)
    )


def _normalize_solidworks_text(value: Any) -> str:
    """Repair GBK mojibake returned by some Chinese SolidWorks COM installs.

    A few late-bound pywin32 installations return Chinese BSTR values decoded as
    Latin-1.  Only accept the reversible repair when it produces CJK text, so
    regular Latin-1 configuration names remain untouched.
    """
    text = str(value)
    try:
        repaired = text.encode("latin-1").decode("gbk")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text
    if any("\u3400" <= character <= "\u9fff" for character in repaired):
        return repaired
    return text


def _get_sw_comtypes_lib() -> Any:
    """Return the comtypes wrapper for the SolidWorks type library.

    Tries SW version numbers 35..27 (newest to oldest). Returns ``None`` when
    comtypes is unavailable or the TLB cannot be found in the registry.
    """
    global _sw_comtypes_lib
    if _sw_comtypes_lib is not None:
        return _sw_comtypes_lib
    if not _COMTYPES_AVAILABLE:
        return None
    for major in (35, 34, 33, 32, 31, 30, 29, 28, 27):  # pragma: no cover
        try:
            _sw_comtypes_lib = _comtypes_client.GetModule(
                (comtypes.GUID(_SW_TLB_GUID), major, 0)
            )
            return _sw_comtypes_lib
        except Exception:
            continue
    return None  # pragma: no cover


def _bridge_com_to_comtypes(pywin32_obj: Any, iface: Any) -> Any:  # pragma: no cover
    """Bridge a pywin32 CDispatch/COM object to a comtypes interface pointer.

    Extracts the raw IUnknown pointer from pywin32's repr string, then
    QueryInterface-es for *iface* via comtypes.  The AddRef keeps the object
    alive through the Python reference.
    """
    unk = pywin32_obj._oleobj_.QueryInterface(pythoncom.IID_IUnknown)
    m = re.search(r"obj at (0x[0-9a-fA-F]+)", repr(unk))
    if not m:
        raise RuntimeError(f"Could not extract COM pointer from {repr(unk)!r}")
    ptr_int = int(m.group(1), 16)
    ct_unk = ctypes.cast(ptr_int, ctypes.POINTER(comtypes.IUnknown))
    ct_unk.AddRef()
    return ct_unk.QueryInterface(iface)


class SolidWorksIOMixin:
    """Expose model open/save/create/configuration methods through a mixin."""

    @staticmethod
    def _adapter(obj: Any) -> Any:
        """Return the runtime adapter object for dynamic attribute access."""
        return cast(Any, obj)

    @staticmethod
    def _is_success(value: Any) -> bool:
        """Interpret SolidWorks save API return values consistently.

        Args:
            value: Return value from ``Save*`` COM calls.

        Returns:
            bool: ``True`` when return value indicates success.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value == 0
        return bool(value)

    @staticmethod
    def _read_com_member(obj: Any, name: str) -> Any:
        """Read a COM property that may be exposed as a zero-argument method."""
        value = getattr(obj, name, None)
        return value() if callable(value) else value

    @staticmethod
    def _equation_parts(raw_equation: Any) -> tuple[str, str]:
        """Split a SolidWorks equation string into its name and expression."""
        equation = _normalize_solidworks_text(raw_equation).strip()
        matched = re.fullmatch(r'"([^"=]+)"\s*=\s*(.+)', equation)
        if matched is None:
            raise RuntimeError("SolidWorks returned an invalid equation string")
        name, expression = (part.strip() for part in matched.groups())
        if not name or not expression:
            raise RuntimeError("SolidWorks returned an incomplete equation")
        return name, expression

    @classmethod
    def _equation_manager(cls, adapter: Any) -> Any:
        """Return a flagged equation manager or raise a focused COM error."""
        getter = getattr(adapter.currentModel, "GetEquationMgr", None)
        if not callable(getter):
            raise RuntimeError("Active part does not support the equation manager")
        manager = getter()
        if manager is None:
            raise RuntimeError("SolidWorks did not return an equation manager")
        _sw_type_info.flag_methods(manager, "IEquationMgr")
        return manager

    @classmethod
    def _equation_rows(cls, adapter: Any, manager: Any) -> list[dict[str, Any]]:
        """Read all equation-manager rows as safe, serializable dictionaries."""
        count = cls._read_com_member(manager, "GetCount")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RuntimeError("SolidWorks returned an invalid equation count")
        rows: list[dict[str, Any]] = []
        for index in range(count):
            equation = manager.Equation(index)
            name, expression = cls._equation_parts(equation)
            global_variable = adapter._attempt(
                lambda position=index: manager.GlobalVariable(position),
                default="@" not in name,
            )
            rows.append(
                {
                    "index": index,
                    "name": name,
                    "expression": expression,
                    "is_global_variable": bool(global_variable),
                }
            )
        return rows

    @classmethod
    def _find_equation_row(
        cls, rows: list[dict[str, Any]], name: str
    ) -> dict[str, Any] | None:
        """Find an equation by its left-hand-side name, case-insensitively."""
        expected_name = name.casefold()
        return next(
            (row for row in rows if str(row["name"]).casefold() == expected_name),
            None,
        )

    @staticmethod
    def _require_part_document(adapter: Any) -> None:
        """Reject part-only API calls before invoking a COM member on another type."""
        get_document_type = getattr(adapter, "_get_document_type", None)
        if not callable(get_document_type):
            return
        document_type = adapter._attempt(get_document_type, default=None)
        if document_type and str(document_type).strip().casefold() != "part":
            raise RuntimeError("Active document is not a part")

    @staticmethod
    def _material_assignment_from_com_result(raw: Any) -> dict[str, str]:
        """Normalize pywin32's varied ``GetMaterialPropertyName2`` return shapes."""
        if raw is None:
            return {"database_name": "", "material_name": ""}
        if isinstance(raw, str):
            return {
                "database_name": "",
                "material_name": _normalize_solidworks_text(raw),
            }
        if not isinstance(raw, (tuple, list)):
            raise RuntimeError("SolidWorks returned an invalid material assignment")
        values = list(raw)
        if len(values) > 1 and values and isinstance(values[0], (bool, int)):
            values = values[1:]
        if not values:
            return {"database_name": "", "material_name": ""}
        if len(values) == 1:
            return {
                "database_name": "",
                "material_name": _normalize_solidworks_text(values[0] or ""),
            }

        normalized_values = [
            _normalize_solidworks_text(value or "") for value in values
        ]
        database_positions = [
            index
            for index, value in enumerate(normalized_values)
            if value.casefold().endswith(".sldmat")
        ]
        if database_positions:
            database_position = database_positions[-1]
            material_position = next(
                (
                    index
                    for index in range(len(normalized_values) - 1, -1, -1)
                    if index != database_position and normalized_values[index]
                ),
                None,
            )
            if material_position is not None:
                return {
                    "database_name": normalized_values[database_position],
                    "material_name": normalized_values[material_position],
                }
        return {
            "database_name": normalized_values[-2],
            "material_name": normalized_values[-1],
        }

    @classmethod
    def _resolve_configuration_name(
        cls, adapter: Any, configuration_name: str
    ) -> str:
        """Map common English/localized default aliases to the native name."""
        requested = _normalize_solidworks_text(configuration_name).strip()
        if not requested:
            return requested
        names_getter = getattr(adapter.currentModel, "GetConfigurationNames", None)
        if not callable(names_getter):
            return requested
        names = adapter._attempt(names_getter, default=None)
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, (tuple, list)):
            return requested
        normalized_names = [
            _normalize_solidworks_text(name).strip()
            for name in names
            if _normalize_solidworks_text(name).strip()
        ]
        exact = next(
            (
                name
                for name in normalized_names
                if name.casefold() == requested.casefold()
            ),
            None,
        )
        if exact is not None:
            return exact
        if requested.casefold() in {"default", "默认", "standard", "标准"}:
            localized_default = next(
                (
                    name
                    for name in normalized_names
                    if name.casefold() in {"default", "默认", "standard", "标准"}
                ),
                None,
            )
            if localized_default is not None:
                return localized_default
        return requested

    @classmethod
    def _read_material_assignment(
        cls, adapter: Any, configuration_name: str
    ) -> dict[str, str]:
        """Read a part material through pywin32's two-parameter COM signature.

        ``IPartDoc::GetMaterialPropertyName2`` takes the configuration name and
        one ``BSTR*`` output parameter.  Generated pywin32 wrappers make the
        output parameter optional, while the late-bound wrapper used by this
        adapter may require it explicitly.  The old fallback passed three
        arguments, which produces ``DISP_E_BADPARAMCOUNT`` after a successful
        material write and made the MCP report a false failure.
        """
        configuration_name = cls._resolve_configuration_name(
            adapter, configuration_name
        )
        getter = getattr(adapter.currentModel, "GetMaterialPropertyName2", None)
        if not callable(getter):
            raise RuntimeError("Active part does not support GetMaterialPropertyName2")

        def _normalize(raw: Any, output_parameter: Any = None) -> dict[str, str]:
            assignment = cls._material_assignment_from_com_result(raw)
            output_database = _normalize_solidworks_text(
                getattr(output_parameter, "value", "") or ""
            ).strip()
            if output_database and not assignment["database_name"]:
                assignment["database_name"] = output_database
            return assignment

        try:
            return _normalize(getter(configuration_name))
        except TypeError as first_error:
            fallback_error: BaseException = first_error
        except Exception as first_error:
            if not _is_bad_param_count_error(first_error):
                raise
            fallback_error = first_error

        variant_ctor = getattr(getattr(win32com, "client", None), "VARIANT", None)
        vt_byref = int(getattr(pythoncom, "VT_BYREF", 0))
        vt_bstr = int(getattr(pythoncom, "VT_BSTR", 0))
        if callable(variant_ctor) and vt_byref and vt_bstr:
            output_parameter = variant_ctor(vt_byref | vt_bstr, "")
            try:
                return _normalize(
                    getter(configuration_name, output_parameter), output_parameter
                )
            except Exception as error:
                if not (
                    isinstance(error, TypeError)
                    or _is_bad_param_count_error(error)
                    or _is_type_mismatch_error(error)
                ):
                    raise
                fallback_error = error

        missing = getattr(pythoncom, "Missing", None)
        try:
            return _normalize(getter(configuration_name, missing))
        except TypeError:
            raise fallback_error

    @classmethod
    def _active_configuration_name(
        cls, adapter: Any, model: Any | None = None
    ) -> str:
        """Read the native active configuration name, including localized names."""
        document = model if model is not None else adapter.currentModel
        active_configuration = adapter._attempt(
            lambda: document.GetActiveConfiguration(), default=None
        )
        active_name = adapter._attempt(
            lambda: active_configuration.GetName(), default=None
        )
        if not active_name:
            active_name = adapter._attempt(
                lambda: active_configuration.Name, default=None
            )
        normalized = _normalize_solidworks_text(active_name or "").strip()
        return normalized or "Default"

    def _resolve_template_path(
        self, preferred_indices: list[int], extension: str
    ) -> str | None:
        """Resolve a template path from SolidWorks user preference slots.

        Args:
            preferred_indices: Preference indices to probe in order.
            extension: Expected template extension such as ``.prtdot``.

        Returns:
            str | None: First existing template match, otherwise first non-empty
            path candidate, or ``None`` if nothing is configured.
        """
        adapter = self._adapter(self)
        existing_match: str | None = None
        first_non_empty: str | None = None
        app = adapter.swApp
        if app is None:
            return None

        for index in preferred_indices:
            template = adapter._attempt(
                lambda idx=index: app.GetUserPreferenceStringValue(idx)
            )
            if not template or not isinstance(template, str):
                continue
            if first_non_empty is None:
                first_non_empty = template
            if template.lower().endswith(extension.lower()) and os.path.exists(
                template
            ):
                existing_match = template
                break

        return (
            existing_match or first_non_empty or self._find_bundled_template(extension)
        )

    def _find_bundled_template(self, extension: str) -> str | None:
        """Find a standard installed template when user preferences are empty.

        SolidWorks can start successfully before its per-user default template
        preferences have been initialized.  In that state, the standard
        templates installed under ProgramData are still safe, deterministic
        fallbacks.  This method only searches fixed installation locations and
        never accepts a client-controlled path.
        """
        extension = extension.lower()
        preferred_names = {
            ".prtdot": ("gb_part.prtdot", "part.prtdot"),
            ".asmdot": ("gb_assembly.asmdot", "assembly.asmdot"),
            ".drwdot": ("gb_a4.drwdot", "gb.drwdot", "drawing.drwdot"),
        }.get(extension, ())
        if not preferred_names:
            return None

        program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        roots = sorted((program_data / "SOLIDWORKS").glob("SOLIDWORKS *"), reverse=True)
        for installation_root in roots:
            template_directory = installation_root / "templates"
            for filename in preferred_names:
                candidate = template_directory / filename
                if candidate.is_file():
                    return str(candidate)

            generic_matches = sorted(template_directory.glob(f"*{extension}"))
            if generic_matches:
                return str(generic_matches[0])

        return None

    def _read_model_title(self, model: Any) -> str:
        """Read a model title regardless of COM exposing method or property.

        Args:
            model: SolidWorks model COM object.

        Returns:
            str: Best-effort model title, defaulting to ``"Untitled"``.
        """
        adapter = self._adapter(self)
        title = adapter._attempt(lambda: adapter._get_attr_or_call(model, "GetTitle"))
        if isinstance(title, str) and title:
            return title

        title_value = getattr(model, "Title", None)
        if isinstance(title_value, str) and title_value:
            return title_value

        return "Untitled"

    def _active_document_after_creation(
        self, app: Any, expected_document_type: int, previous_document: Any = None
    ) -> Any | None:
        """Return the active document when SolidWorks creates it without returning it.

        Some legacy SolidWorks/pywin32 combinations create and activate a new
        document for ``NewPart`` or ``NewAssembly`` but marshal the COM return
        value as ``None``.  The side effect is still real, so treating that
        value as a failure causes clients to retry and create duplicate blank
        documents.  Accept the active document only after its native type has
        been verified.
        """
        adapter = self._adapter(self)
        active_document = adapter._attempt(
            lambda: adapter._get_attr_or_call(app, "ActiveDoc"), default=None
        )
        if active_document is None:
            return None
        document_type = adapter._attempt(
            lambda: adapter._get_attr_or_call(active_document, "GetType"),
            default=None,
        )
        try:
            if int(document_type) != expected_document_type:
                return None
        except (TypeError, ValueError):
            return None
        if previous_document is not None:
            previous_title = self._read_model_title(previous_document)
            active_title = self._read_model_title(active_document)
            if active_document is previous_document or active_title == previous_title:
                return None
        return active_document

    @staticmethod
    def _component_summary(adapter: Any, component: Any) -> dict[str, str]:
        """Extract a portable, client-safe summary from an IComponent2 object."""

        def _read_member(obj: Any, member_name: str) -> Any:
            """Read a COM property regardless of whether it is exposed as a method."""
            member = getattr(obj, member_name, None)
            return member() if callable(member) else member

        component_id = adapter._attempt(
            lambda: _read_member(component, "GetID"), default=None
        )
        display_name = adapter._attempt(
            lambda: _read_member(component, "Name2"), default=None
        )
        if not display_name:
            display_name = adapter._attempt(
                lambda: _read_member(component, "Name"), default=None
            )
        if not display_name:
            raise RuntimeError("SolidWorks component did not report a display name")

        component_model = adapter._attempt(
            lambda: _read_member(component, "GetModelDoc2"), default=None
        )
        component_type = adapter._attempt(
            lambda: _read_member(component_model, "GetType"), default=None
        )
        document_type = {1: "part", 2: "assembly", 3: "drawing"}.get(
            component_type, "unknown"
        )
        component_path = adapter._attempt(
            lambda: _read_member(component, "GetPathName"), default=""
        )
        if document_type == "unknown":
            document_type = SolidWorksIOMixin._document_type_from_path(component_path)
        normalized_display_name = _normalize_solidworks_text(display_name).strip()
        normalized_component_id = _normalize_solidworks_text(
            component_id if component_id is not None else normalized_display_name
        ).strip()
        if not normalized_component_id:
            raise RuntimeError("SolidWorks component did not report an identifier")
        if document_type == "unknown":
            known_types = getattr(adapter, "_assembly_component_types", {})
            document_type = known_types.get(normalized_component_id, "unknown")
        return {
            "component_id": normalized_component_id,
            "display_name": normalized_display_name,
            "document_type": document_type,
        }

    @staticmethod
    def _assembly_components(adapter: Any) -> list[Any]:
        """Return active-assembly components without serializing their COM objects."""
        document_type = SolidWorksIOMixin._read_com_member(
            adapter.currentModel, "GetType"
        )
        if document_type != 2:
            raise RuntimeError("Active model is not an assembly")
        get_components = getattr(adapter.currentModel, "GetComponents", None)
        if not callable(get_components):
            raise RuntimeError("Active assembly does not support GetComponents")
        raw_components = get_components(False)
        if raw_components is None:
            return []
        if isinstance(raw_components, (list, tuple)):
            return [component for component in raw_components if component is not None]
        return [raw_components]

    @staticmethod
    def _as_com_collection(value: Any) -> list[Any]:
        """Normalize pywin32 variant arrays and common out-parameter shapes."""
        if value is None:
            return []
        if isinstance(value, tuple) and value and isinstance(value[0], (list, tuple)):
            value = value[0]
        if isinstance(value, (list, tuple)):
            return [entry for entry in value if entry is not None]
        return [value]

    @staticmethod
    def _component_bodies(adapter: Any, component: Any) -> list[Any]:
        """Read component bodies across the IDispatch variants used by SolidWorks."""
        _sw_type_info.flag_methods(component, "IComponent2")
        _sw_type_info.flag_method(component, "IComponent2", "GetBodies3")
        get_bodies = adapter._attempt(
            lambda: getattr(component, "GetBodies3", None), default=None
        )
        raw_bodies = None
        if callable(get_bodies):
            try:
                body_info = win32com.client.VARIANT(
                    pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None
                )
                raw_bodies = get_bodies(0, body_info)
            except Exception as error:
                if not _is_member_not_found_error(error):
                    raise
        if raw_bodies is None:
            _sw_type_info.flag_method(component, "IComponent2", "GetBody")
            raw_bodies = adapter._attempt(
                lambda: SolidWorksIOMixin._read_com_member(component, "GetBody"),
                default=None,
            )
        if raw_bodies is None:
            _sw_type_info.flag_method(component, "IComponent2", "GetModelDoc2")
            component_model = adapter._attempt(
                lambda: SolidWorksIOMixin._read_com_member(component, "GetModelDoc2"),
                default=None,
            )
            _sw_type_info.flag_method(component_model, "IPartDoc", "GetBodies2")
            get_part_bodies = adapter._attempt(
                lambda: getattr(component_model, "GetBodies2", None), default=None
            )
            if callable(get_part_bodies):
                raw_bodies = adapter._attempt(
                    lambda: get_part_bodies(0, False), default=None
                )
        return SolidWorksIOMixin._as_com_collection(raw_bodies)

    @staticmethod
    def _body_entities(
        adapter: Any,
        body: Any,
        first_member: str,
        next_member: str,
        collection_member: str,
    ) -> list[Any]:
        """Read body topology across linked-list and array COM variants."""
        _sw_type_info.flag_method(body, "IBody2", first_member)
        _sw_type_info.flag_method(body, "IBody2", collection_member)
        first_entity = adapter._attempt(
            lambda: SolidWorksIOMixin._read_com_member(body, first_member),
            default=None,
        )
        if first_entity is not None:
            return SolidWorksIOMixin._linked_entities(first_entity, next_member)
        raw_entities = adapter._attempt(
            lambda: SolidWorksIOMixin._read_com_member(body, collection_member),
            default=None,
        )
        return SolidWorksIOMixin._as_com_collection(raw_entities)

    @staticmethod
    def _assembly_entity_locator(
        component_id: str, entity_type: str, position: int
    ) -> str:
        """Encode a revision-bound fallback locator for assembly topology."""
        payload = json.dumps(
            {"component_id": component_id, "entity_type": entity_type, "position": position},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return _ASSEMBLY_ENTITY_LOCATOR_PREFIX + base64.urlsafe_b64encode(payload).decode(
            "ascii"
        )

    @staticmethod
    def _decode_assembly_entity_locator(token: str) -> tuple[str, str, int] | None:
        """Decode an internal assembly topology locator without trusting its data."""
        if not token.startswith(_ASSEMBLY_ENTITY_LOCATOR_PREFIX):
            return None
        try:
            encoded = token.removeprefix(_ASSEMBLY_ENTITY_LOCATOR_PREFIX)
            padded = encoded + "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            component_id = str(payload["component_id"]).strip()
            entity_type = str(payload["entity_type"]).strip().lower()
            position = int(payload["position"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("Assembly mate entity reference is invalid") from error
        if not component_id or entity_type not in {"face", "edge"} or position < 1:
            raise RuntimeError("Assembly mate entity reference is invalid")
        return component_id, entity_type, position

    @staticmethod
    def _persistent_reference_token(extension: Any, entity: Any) -> str:
        """Encode one SolidWorks persistent reference for server-side storage."""
        # Older SolidWorks dispatches can omit the v3 method even when their
        # type library advertises it.  Probe each method defensively because
        # CDispatch raises during ``getattr`` for a missing member.
        raw_reference = None
        last_error: BaseException | None = None
        for method_name in ("GetPersistReference3", "GetPersistReference"):
            try:
                _sw_type_info.flag_method(extension, "IModelDocExtension", method_name)
                getter = getattr(extension, method_name, None)
                uses_vtable_extension = False
                getter_entity = entity
                if callable(getter):
                    try:
                        raw_reference = getter(getter_entity)
                    except Exception as error:
                        # The active model can require a comtypes vtable
                        # extension even though the body topology arrives as
                        # pywin32 CDispatch objects. The vtable signature
                        # requires IDispatch, so bridge that object only when
                        # comtypes rejects the pywin32 wrapper.
                        if (
                            _ComtypesIDispatch is None
                            or "QueryInterface" not in str(error)
                        ):
                            raise
                        getter_entity = _bridge_com_to_comtypes(
                            entity, _ComtypesIDispatch
                        )
                        uses_vtable_extension = True
                        raw_reference = getter(getter_entity)
                    if (
                        raw_reference is None
                        and uses_vtable_extension
                        and _ComtypesVariant is not None
                    ):
                        # ``GetPersistReference3`` declares its return as an
                        # out VARIANT. Some comtypes wrappers leave its
                        # Python return value empty, so read the explicit
                        # output storage as a compatibility fallback.
                        output_reference = _ComtypesVariant()
                        try:
                            explicit_result = getter(
                                getter_entity, ctypes.byref(output_reference)
                            )
                        except TypeError:
                            pass
                        else:
                            raw_reference = (
                                explicit_result
                                if explicit_result is not None
                                else output_reference.value
                            )
                if raw_reference is not None:
                    break
            except Exception as error:
                last_error = error
                if not _is_member_not_found_error(error):
                    raise
        if raw_reference is None and last_error is not None:
            raise RuntimeError(
                "Active model does not support persistent references"
            ) from last_error
        if isinstance(raw_reference, (tuple, list)):
            raw_reference = next(
                (
                    value
                    for value in raw_reference
                    if isinstance(value, (bytes, bytearray, memoryview))
                ),
                None,
            )
        if not isinstance(raw_reference, (bytes, bytearray, memoryview)):
            raise RuntimeError(
                "SolidWorks did not return a persistent geometry reference"
            )
        reference = bytes(raw_reference)
        if not reference:
            raise RuntimeError(
                "SolidWorks returned an empty persistent geometry reference"
            )
        return base64.b64encode(reference).decode("ascii")

    @staticmethod
    def _model_extension(adapter: Any) -> Any:
        """Return the active model extension across SolidWorks dispatch variants."""
        extension = adapter._attempt(
            lambda: SolidWorksIOMixin._read_com_member(
                adapter.currentModel, "Extension"
            ),
            default=None,
        )
        if extension is not None:
            return extension

        # Some older SolidWorks IDispatch views expose neither ``Extension``
        # nor the inherited IModelDoc2 property on ActiveDoc. Query the
        # extension's vtable interface directly in that case.
        sw_lib = _get_sw_comtypes_lib()
        if sw_lib is not None:
            try:
                model_doc = _bridge_com_to_comtypes(
                    adapter.currentModel, sw_lib.IModelDoc2
                )
                return model_doc.Extension
            except Exception as error:
                logger.debug("IModelDoc2 extension vtable bridge failed: {!r}", error)

        # NewDocument can return an IAssemblyDoc IDispatch view that does not
        # expose inherited IModelDoc2 properties. ActiveDoc yields the base
        # document view and retains the same active assembly.
        app = getattr(adapter, "swApp", None)
        active_model = adapter._attempt(
            lambda: SolidWorksIOMixin._read_com_member(app, "ActiveDoc"),
            default=None,
        )
        return adapter._attempt(
            lambda: SolidWorksIOMixin._read_com_member(active_model, "Extension"),
            default=None,
        )

    @staticmethod
    def _linked_entities(first_entity: Any, next_member: str) -> list[Any]:
        """Walk one SolidWorks linked-list API with a hard loop safety bound."""
        entities: list[Any] = []
        seen: set[int] = set()
        current_entity = first_entity
        while (
            current_entity is not None and len(entities) < _MAX_MATE_ENTITIES_PER_BODY
        ):
            identity = id(current_entity)
            if identity in seen:
                break
            seen.add(identity)
            entities.append(current_entity)
            current_entity = SolidWorksIOMixin._read_com_member(
                current_entity, next_member
            )
        return entities

    @staticmethod
    def _document_type_from_path(path: Any) -> str:
        """Infer a native document type only from a trusted SolidWorks extension."""
        return {
            ".sldprt": "part",
            ".sldasm": "assembly",
            ".slddrw": "drawing",
        }.get(Path(str(path)).suffix.lower(), "unknown")

    @staticmethod
    def _drawing_view_summary(adapter: Any, view: Any) -> dict[str, str]:
        """Extract a client-safe, stable name from an ``IView`` COM object."""

        def _read_member(obj: Any, member_name: str) -> Any:
            member = getattr(obj, member_name, None)
            return member() if callable(member) else member

        view_name = adapter._attempt(lambda: _read_member(view, "Name"), default=None)
        if not view_name:
            view_name = adapter._attempt(
                lambda: _read_member(view, "GetName2"), default=None
            )
        normalized_name = _normalize_solidworks_text(view_name).strip()
        if not normalized_name:
            raise RuntimeError("SolidWorks drawing view did not report a name")
        return {"view_id": normalized_name, "display_name": normalized_name}

    @classmethod
    def _drawing_view_name_candidates(
        cls, adapter: Any, model_path: str, requested_name: str
    ) -> tuple[str, ...]:
        """Return the requested standard view plus a localized source-model name.

        ``CreateDrawViewFromModelView3`` accepts the localized value returned by
        ``IModelDoc2.GetModelViewNames``.  The documented English standard names
        (for example ``*Isometric``) return no view on localized installations.
        Standard views always occupy the same positions in that list, so a saved,
        open source model lets us derive the local equivalent without exposing
        locale-specific strings through the MCP contract.
        """
        standard_view_indexes = {
            "*Front": 1,
            "*Back": 2,
            "*Left": 3,
            "*Right": 4,
            "*Top": 5,
            "*Bottom": 6,
            "*Isometric": 7,
            "*Dimetric": 8,
            "*Trimetric": 9,
        }
        index = standard_view_indexes.get(requested_name)
        if index is None:
            return (requested_name,)

        try:
            app = getattr(adapter, "swApp", None)
            _sw_type_info.flag_method(app, "ISldWorks", "GetDocuments")
            raw_documents = cls._read_com_member(app, "GetDocuments") if app else None
            if not isinstance(raw_documents, (list, tuple)):
                return (requested_name,)

            expected_path = os.path.normcase(os.path.abspath(model_path))
            source_model = None
            for document in raw_documents:
                _sw_type_info.flag_method(document, "IModelDoc2", "GetPathName")
                document_path = cls._read_com_member(document, "GetPathName")
                if not isinstance(document_path, str):
                    continue
                if os.path.normcase(os.path.abspath(document_path)) == expected_path:
                    source_model = document
                    break
            if source_model is None:
                return (requested_name,)

            _sw_type_info.flag_method(source_model, "IModelDoc2", "GetModelViewNames")
            raw_names = cls._read_com_member(source_model, "GetModelViewNames")
            if not isinstance(raw_names, (list, tuple)) or len(raw_names) <= index:
                return (requested_name,)
            localized_name = str(raw_names[index]).strip()
            if not localized_name or localized_name == requested_name:
                return (requested_name,)
            return (requested_name, localized_name)
        except Exception:
            return (requested_name,)

    @classmethod
    def _drawing_view_by_id(cls, adapter: Any, view_id: str) -> Any:
        """Resolve a server-issued drawing-view ID in the current drawing only."""
        if cls._read_com_member(adapter.currentModel, "GetType") != 3:
            raise RuntimeError("Active model is not a drawing")
        sheet_view = cls._read_com_member(adapter.currentModel, "GetFirstView")
        view = cls._read_com_member(sheet_view, "GetNextView")
        while view is not None:
            if cls._drawing_view_summary(adapter, view)["view_id"] == view_id:
                return view
            view = cls._read_com_member(view, "GetNextView")
        raise RuntimeError("The requested drawing view no longer exists")

    @staticmethod
    def _decode_persistent_reference(token: str, *, description: str) -> bytes:
        """Decode one internal base64 persistent reference safely."""
        try:
            reference = base64.b64decode(token.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError) as exc:
            raise RuntimeError(f"{description} reference is invalid") from exc
        if not reference:
            raise RuntimeError(f"{description} reference is invalid")
        return reference

    @classmethod
    def _resolve_persistent_reference(
        cls, adapter: Any, token: str, *, description: str
    ) -> Any:
        """Resolve an internal reference without accepting a COM selection string."""
        extension = cls._model_extension(adapter)
        resolver = getattr(extension, "GetObjectByPersistReference3", None)
        if not callable(resolver):
            raise RuntimeError("Active model does not support persistent references")
        raw_resolved = resolver(
            cls._decode_persistent_reference(token, description=description)
        )
        entity = raw_resolved
        error_code = 0
        if isinstance(raw_resolved, (tuple, list)):
            entity = raw_resolved[0] if raw_resolved else None
            if len(raw_resolved) > 1 and isinstance(raw_resolved[-1], (bool, int)):
                error_code = int(raw_resolved[-1])
        if entity is None or error_code != 0:
            raise RuntimeError(f"{description} can no longer be resolved")
        return entity

    @staticmethod
    def _select_annotation_entity(adapter: Any, entity: Any) -> bool:
        """Select one COM entity through its object interface, never by client text."""
        selector = getattr(adapter, "select_entity", None)
        if callable(selector):
            return bool(selector(entity, False))
        for member_name, arguments in (
            ("Select4", (False, None)),
            ("Select2", (False, 0)),
            ("Select", (False,)),
        ):
            method = getattr(entity, member_name, None)
            if callable(method) and bool(method(*arguments)):
                return True
        return False

    @classmethod
    def _position_annotation(
        cls, annotation_owner: Any, x_m: float, y_m: float
    ) -> None:
        """Set a drawing annotation position and reject a reported COM failure."""
        annotation = cls._read_com_member(annotation_owner, "GetAnnotation")
        if annotation is None:
            annotation = annotation_owner
        setter = getattr(annotation, "SetPosition2", None)
        if not callable(setter):
            raise RuntimeError("SolidWorks did not return a positionable annotation")
        accepted = setter(x_m, y_m, 0.0)
        if accepted is False:
            raise RuntimeError("SolidWorks could not position the drawing annotation")

    @classmethod
    def _annotation_summary(
        cls, adapter: Any, annotation_owner: Any, annotation_type: str, fallback: str
    ) -> dict[str, str]:
        """Return a safe annotation display name without serializing COM objects."""
        annotation = cls._read_com_member(annotation_owner, "GetAnnotation")
        if annotation is None:
            annotation = annotation_owner
        name = adapter._attempt(
            lambda: cls._read_com_member(annotation, "GetName"), default=None
        )
        normalized_name = _normalize_solidworks_text(name).strip()
        return {
            "annotation_type": annotation_type,
            "display_name": normalized_name or fallback,
        }

    @classmethod
    def _model_features(cls, adapter: Any):
        """Iterate active-model features across pywin32 traversal variants.

        Some SolidWorks versions expose ``FirstFeature`` as an ambiguous COM
        property and reject the method call. They still reliably support
        ``FeatureByPositionReverse``. Prefer normal tree order and use the
        positional accessor only when the forward traversal cannot start.
        """
        _sw_type_info.flag_methods(adapter.currentModel, "IModelDoc2")
        feature = adapter._attempt(
            lambda: cls._read_com_member(adapter.currentModel, "FirstFeature"),
            default=None,
        )
        seen: set[int] = set()
        while feature is not None and len(seen) < _MAX_DRAWING_ANNOTATION_TARGETS:
            identity = id(feature)
            if identity in seen:
                break
            seen.add(identity)
            yield feature
            feature = adapter._attempt(
                lambda feature=feature: cls._read_com_member(feature, "GetNextFeature"),
                default=None,
            )
        if seen:
            return

        for position in range(_MAX_DRAWING_ANNOTATION_TARGETS):
            feature = adapter._attempt(
                lambda position=position: adapter.currentModel.FeatureByPositionReverse(
                    position
                ),
                default=None,
            )
            if feature is None:
                break
            yield feature

    @classmethod
    def _sheet_metal_info(cls, adapter: Any) -> dict[str, Any]:
        """Inspect the feature tree and bend state for the active part."""
        cls._require_part_document(adapter)
        sheet_metal_feature = None
        flat_pattern_name: str | None = None
        for current_feature in cls._model_features(adapter):
            type_name = _normalize_solidworks_text(
                cls._read_com_member(current_feature, "GetTypeName2")
                or cls._read_com_member(current_feature, "GetTypeName")
                or ""
            ).strip()
            normalized_type = type_name.casefold()
            if normalized_type == "sheetmetal":
                sheet_metal_feature = current_feature
            if normalized_type == "flatpattern" and flat_pattern_name is None:
                flat_pattern_name = _normalize_solidworks_text(
                    cls._read_com_member(current_feature, "Name")
                    or cls._read_com_member(current_feature, "GetName")
                    or "Flat Pattern"
                ).strip()
        if sheet_metal_feature is None:
            return {
                "is_sheet_metal": False,
                "is_flattened": None,
                "thickness_mm": None,
                "flat_pattern_feature_name": None,
            }

        bend_state = adapter._attempt(
            lambda: cls._read_com_member(adapter.currentModel, "GetBendState"),
            default=None,
        )
        is_flattened = (
            True
            if bend_state == _SW_BEND_STATE_FLATTENED
            else False
            if bend_state == _SW_BEND_STATE_FOLDED
            else None
        )
        definition = adapter._attempt(
            lambda: cls._read_com_member(sheet_metal_feature, "GetDefinition"),
            default=None,
        )
        thickness_m = adapter._attempt(
            lambda: cls._read_com_member(definition, "Thickness"), default=None
        )
        thickness_mm = (
            float(thickness_m) * 1000
            if isinstance(thickness_m, (int, float)) and thickness_m >= 0
            else None
        )
        return {
            "is_sheet_metal": True,
            "is_flattened": is_flattened,
            "thickness_mm": thickness_mm,
            "flat_pattern_feature_name": flat_pattern_name or None,
        }

    @classmethod
    def _flat_pattern_feature(cls, adapter: Any) -> tuple[Any, str]:
        """Return the active part's flat-pattern feature and its safe display name."""
        cls._require_part_document(adapter)
        for position, candidate in enumerate(cls._model_features(adapter), start=1):
            _sw_type_info.flag_methods(candidate, "IFeature")
            type_name = (
                _normalize_solidworks_text(
                    cls._read_com_member(candidate, "GetTypeName2")
                    or cls._read_com_member(candidate, "GetTypeName")
                    or ""
                )
                .replace(" ", "")
                .casefold()
            )
            if "flatpattern" not in type_name:
                continue
            name = _normalize_solidworks_text(
                cls._read_com_member(candidate, "Name")
                or cls._read_com_member(candidate, "GetName")
                or f"Flat Pattern {position}"
            ).strip()
            return candidate, name
        raise RuntimeError("Active sheet-metal part has no flat-pattern feature")

    @staticmethod
    def _custom_property_manager(adapter: Any, configuration_name: str | None) -> Any:
        """Return the document or configuration property manager for the active model."""
        extension = getattr(adapter.currentModel, "Extension", None)
        manager_accessor = getattr(extension, "CustomPropertyManager", None)
        scope_name = configuration_name or ""
        if manager_accessor is None:
            raise RuntimeError("Active document does not expose CustomPropertyManager")
        if callable(manager_accessor):
            manager = manager_accessor(scope_name)
        else:
            try:
                manager = manager_accessor[scope_name]
            except (KeyError, TypeError, AttributeError):
                manager = manager_accessor
        if manager is None:
            raise RuntimeError("Active document does not expose CustomPropertyManager")
        return manager

    @staticmethod
    def _custom_property_member(manager: Any, member_name: str) -> Any:
        """Resolve a property-manager member across pywin32 binding modes."""
        _sw_type_info.flag_methods(manager, "ICustomPropertyManager")
        flag_as_method = getattr(manager, "_FlagAsMethod", None)
        if callable(flag_as_method):
            try:
                flag_as_method(member_name)
            except Exception:
                pass
        return getattr(manager, member_name, None)

    @staticmethod
    def _custom_property_byref_value(
        reader: Any, name: str, *, with_cache_flag: bool
    ) -> tuple[str, str] | None:
        """Read a property through late-bound COM output ``VARIANT`` values."""
        variant_ctor = getattr(getattr(win32com, "client", None), "VARIANT", None)
        vt_byref = int(getattr(pythoncom, "VT_BYREF", 0))
        vt_bstr = int(getattr(pythoncom, "VT_BSTR", 0))
        vt_bool = int(getattr(pythoncom, "VT_BOOL", 0))
        if not callable(variant_ctor) or not (vt_byref and vt_bstr):
            return None
        raw_value = variant_ctor(vt_byref | vt_bstr, "")
        resolved_value = variant_ctor(vt_byref | vt_bstr, "")
        try:
            if with_cache_flag:
                was_resolved = variant_ctor(vt_byref | vt_bool, False)
                linked_to_property = variant_ctor(vt_byref | vt_bool, False)
                reader(
                    name,
                    False,
                    raw_value,
                    resolved_value,
                    was_resolved,
                    linked_to_property,
                )
            else:
                reader(name, raw_value, resolved_value)
        except Exception:
            return None
        return (
            str(getattr(raw_value, "value", "") or ""),
            str(getattr(resolved_value, "value", "") or ""),
        )

    @staticmethod
    def _custom_property_value(manager: Any, name: str) -> tuple[str, str]:
        """Read raw and resolved text through the most compatible COM shape."""
        get6 = SolidWorksIOMixin._custom_property_member(manager, "Get6")
        if callable(get6):
            try:
                value = get6(name, False)
            except Exception:
                value = None
            else:
                if isinstance(value, (tuple, list)):
                    fields = list(value)
                    if len(fields) >= 3 and isinstance(fields[0], int):
                        return str(fields[1] or ""), str(fields[2] or "")
                    if len(fields) >= 2:
                        return str(fields[0] or ""), str(fields[1] or "")
                    if fields:
                        return str(fields[0] or ""), str(fields[0] or "")
                if isinstance(value, str):
                    return value, value
            byref_value = SolidWorksIOMixin._custom_property_byref_value(
                get6, name, with_cache_flag=True
            )
            if byref_value is not None:
                return byref_value

        get2 = SolidWorksIOMixin._custom_property_member(manager, "Get2")
        if callable(get2):
            try:
                value = get2(name)
            except Exception:
                value = None
            else:
                if isinstance(value, (tuple, list)) and len(value) >= 2:
                    return str(value[0] or ""), str(value[1] or "")
                if isinstance(value, str):
                    return value, value
            byref_value = SolidWorksIOMixin._custom_property_byref_value(
                get2, name, with_cache_flag=False
            )
            if byref_value is not None:
                return byref_value
        raise RuntimeError(f"SolidWorks could not read custom property {name!r}")

    @staticmethod
    def _custom_property_names(manager: Any) -> list[str]:
        """Return normalized property names without serializing COM arrays."""
        get_names = SolidWorksIOMixin._custom_property_member(manager, "GetNames")
        if get_names is None:
            raise RuntimeError("CustomPropertyManager does not support GetNames")
        raw_names = get_names() if callable(get_names) else get_names
        if raw_names is None:
            return []
        if isinstance(raw_names, str):
            raw_names = [raw_names]
        if not isinstance(raw_names, (tuple, list)):
            raise RuntimeError("SolidWorks returned an invalid custom property list")
        names: list[str] = []
        for raw_name in raw_names:
            if not isinstance(raw_name, str):
                continue
            name = _normalize_solidworks_text(raw_name).strip()
            if name:
                names.append(name)
        return names

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        """Open a SolidWorks model file and set it as active on the adapter.

        Args:
            file_path: Path to a native SolidWorks file or a STEP/IGES file.

        Returns:
            AdapterResult[SolidWorksModel]: Model metadata for the opened document.
        """
        adapter = self._adapter(self)
        if not adapter.is_connected():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        def _open() -> SolidWorksModel:
            """Open the model document."""
            resolved_path = os.path.abspath(file_path)
            file_path_lower = resolved_path.lower()
            if file_path_lower.endswith(".sldprt"):
                doc_type = adapter.constants["swDocPART"]
                model_type = "Part"
            elif file_path_lower.endswith(".sldasm"):
                doc_type = adapter.constants["swDocASSEMBLY"]
                model_type = "Assembly"
            elif file_path_lower.endswith(".slddrw"):
                doc_type = adapter.constants["swDocDRAWING"]
                model_type = "Drawing"
            elif file_path_lower.endswith((".step", ".stp", ".iges", ".igs")):
                # OpenDoc6 imports neutral geometry into a part document.
                doc_type = adapter.constants["swDocPART"]
                model_type = "Part"
            else:
                raise ValueError(f"Unsupported file type: {resolved_path}")

            app = adapter.swApp
            get_documents_member = getattr(app, "GetDocuments", None)
            enumeration_sentinel = object()
            raw_open_documents = (
                adapter._attempt(
                    lambda: self._read_com_member(app, "GetDocuments"),
                    default=enumeration_sentinel,
                )
                if get_documents_member is not None
                else None
            )
            enumeration_failed = get_documents_member is not None and (
                raw_open_documents is enumeration_sentinel or raw_open_documents is None
            )
            # ``GetDocuments`` normally marshals to a tuple, but older
            # pywin32/ SolidWorks combinations may expose an iterable or a
            # single document dispatch.  Normalize all supported shapes so a
            # second preview/agent call can reuse the existing document.
            open_documents: list[Any] = []
            if isinstance(raw_open_documents, (list, tuple)):
                open_documents = [
                    doc for doc in raw_open_documents if doc is not None
                ]
            elif raw_open_documents is not None and not isinstance(
                raw_open_documents, (str, bytes)
            ):
                try:
                    open_documents = [
                        doc for doc in raw_open_documents if doc is not None
                    ]
                except TypeError:
                    if getattr(raw_open_documents, "GetPathName", None) is not None:
                        open_documents = [raw_open_documents]

            expected_path = os.path.normcase(os.path.abspath(resolved_path))
            existing_model = None
            for document in open_documents:
                document_path = adapter._attempt(
                    lambda document=document: self._read_com_member(
                        document, "GetPathName"
                    ),
                    default=None,
                )
                if isinstance(document_path, str) and document_path:
                    if (
                        os.path.normcase(os.path.abspath(document_path))
                        == expected_path
                    ):
                        existing_model = document
                        break

            # If GetDocuments was unavailable, the active document is still a
            # reliable fallback for the common preview/retry case.
            if existing_model is None:
                active_document = adapter._attempt(
                    lambda: self._read_com_member(app, "ActiveDoc"), default=None
                )
                active_path = adapter._attempt(
                    lambda: self._read_com_member(active_document, "GetPathName"),
                    default=None,
                )
                if isinstance(active_path, str) and active_path:
                    if (
                        os.path.normcase(os.path.abspath(active_path))
                        == expected_path
                    ):
                        existing_model = active_document

            # If GetDocuments is temporarily empty while SolidWorks finishes
            # registering a just-opened document, use the process cache as a
            # narrow fallback. Prefer the fresh enumeration/ActiveDoc result
            # above so a user-reopened same-path document wins over stale cache.
            if existing_model is None:
                cached_entry = _OPEN_DOCUMENT_CACHE.get(expected_path)
                if cached_entry is not None:
                    cached_app, cached_document = cached_entry
                    cached_path = adapter._attempt(
                        lambda: self._read_com_member(
                            cached_document, "GetPathName"
                        ),
                        default=None,
                    )
                    if _same_com_reference(cached_app, app) and (
                        isinstance(cached_path, str)
                        and os.path.normcase(os.path.abspath(cached_path))
                        == expected_path
                    ):
                        existing_model = cached_document
                    else:
                        _OPEN_DOCUMENT_CACHE.pop(expected_path, None)

            # A COM-busy GetDocuments call is different from an empty document
            # collection. If neither ActiveDoc nor the process cache identifies
            # the target, do not issue OpenDoc6: that can create a second tab for
            # a document that is already open but temporarily undiscoverable.
            if enumeration_failed and existing_model is None:
                raise RuntimeError(
                    "SolidWorks document enumeration is temporarily unavailable; "
                    "retry open_model after SolidWorks finishes updating its documents"
                )

            target_was_already_open = existing_model is not None
            if existing_model is not None:
                model = existing_model
                # Reusing a document must also make it the active SolidWorks
                # document.  Activation APIs vary by release, so treat the
                # call as best-effort and retain the known COM document when a
                # legacy binding exposes no activation method.
                title_for_activation = self._read_model_title(model)
                active_member_available = hasattr(app, "ActiveDoc")
                active_now = adapter._attempt(
                    lambda: self._read_com_member(app, "ActiveDoc"), default=None
                )
                active_title = (
                    self._read_model_title(active_now)
                    if active_now is not None
                    else ""
                )
                if title_for_activation and active_title != title_for_activation:
                    activate_doc3 = getattr(app, "ActivateDoc3", None)
                    activate_doc2 = getattr(app, "ActivateDoc2", None)
                    activate_doc = getattr(app, "ActivateDoc", None)
                    activation_result = None
                    activation_attempted = False
                    if callable(activate_doc3):
                        activation_attempted = True
                        activation_result = adapter._attempt(
                            lambda: activate_doc3(title_for_activation, True, 0, 0),
                            default=None,
                        )
                    elif callable(activate_doc2):
                        activation_attempted = True
                        activation_result = adapter._attempt(
                            lambda: activate_doc2(title_for_activation, True, 0),
                            default=None,
                        )
                    elif callable(activate_doc):
                        activation_attempted = True
                        activation_result = adapter._attempt(
                            lambda: activate_doc(title_for_activation), default=None
                        )
                    if activation_attempted:
                        active_after = adapter._attempt(
                            lambda: self._read_com_member(app, "ActiveDoc"),
                            default=None,
                        )
                        activated_document = (
                            active_after
                            if active_after is not None
                            else activation_result
                        )
                        activated_path = adapter._attempt(
                            lambda: self._read_com_member(
                                activated_document, "GetPathName"
                            ),
                            default=None,
                        )
                        if (
                            not isinstance(activated_path, str)
                            or os.path.normcase(os.path.abspath(activated_path))
                            != expected_path
                        ):
                            raise RuntimeError(
                                "SolidWorks activated a different document or did not "
                                f"activate {resolved_path}"
                            )
                    else:
                        if active_member_available:
                            raise RuntimeError(
                                "SolidWorks cannot activate the already-open document"
                            )
            else:
                variant_ctor = getattr(
                    getattr(win32com, "client", None), "VARIANT", None
                )
                vt_byref = int(getattr(pythoncom, "VT_BYREF", 0))
                vt_i4 = int(getattr(pythoncom, "VT_I4", 0))
                if callable(variant_ctor):
                    errors = variant_ctor(vt_byref | vt_i4, 0)
                    warnings = variant_ctor(vt_byref | vt_i4, 0)
                else:
                    errors = 0
                    warnings = 0
                model = app.OpenDoc6(
                    resolved_path, doc_type, 1, "", errors, warnings
                )
            if model is None:
                raise Exception(f"Failed to open model: {resolved_path}")

            adapter._attempt(
                lambda: _sw_type_info.flag_doc(model, int(doc_type)), default=0
            )

            adapter.currentModel = model
            _OPEN_DOCUMENT_CACHE[expected_path] = (app, model)
            tracker = getattr(adapter, "_track_session_document", None)
            if callable(tracker) and not target_was_already_open:
                tracker(model)
            title = self._read_model_title(model)
            config = self._active_configuration_name(adapter, model)

            return SolidWorksModel(
                path=resolved_path,
                name=title,
                type=model_type,
                is_active=True,
                configuration=config,
                properties={
                    "reused_existing_document": target_was_already_open,
                    "created_new_document": not target_was_already_open,
                    "last_modified": (
                        model.GetSaveTime()
                        if callable(getattr(model, "GetSaveTime", None))
                        else None
                    ),
                },
            )

        return cast(
            AdapterResult[SolidWorksModel],
            adapter._handle_com_operation("open_model", _open),
        )

    async def activate_model(
        self, name: str, path: str | None = None
    ) -> AdapterResult[None]:
        """Activate an already-open document before a stateful workflow operation."""
        adapter = self._adapter(self)
        if not adapter.is_connected():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        def _matches(document: Any) -> bool:
            """Return whether a document matches the tracked path or title."""
            document_path, document_title = adapter._document_routing.document_identity(
                document
            )
            if path:
                if not document_path:
                    return False
                return bool(
                    os.path.abspath(path).casefold() == document_path.casefold()
                )
            return bool(document_title and document_title == name)

        def _activate() -> None:
            """Activate the requested document and update the adapter model reference."""
            if adapter.currentModel is not None and _matches(adapter.currentModel):
                return
            app = adapter.swApp
            if app is None:
                raise RuntimeError("SolidWorks application is not connected")

            variant_ctor = getattr(getattr(win32com, "client", None), "VARIANT", None)
            vt_byref = int(getattr(pythoncom, "VT_BYREF", 0))
            vt_i4 = int(getattr(pythoncom, "VT_I4", 0))
            errors = variant_ctor(vt_byref | vt_i4, 0) if callable(variant_ctor) else 0
            activated = None
            activate_doc3 = getattr(app, "ActivateDoc3", None)
            if callable(activate_doc3):
                activated = activate_doc3(name, True, 0, errors)
            else:
                activate_doc2 = getattr(app, "ActivateDoc2", None)
                if callable(activate_doc2):
                    activated = activate_doc2(name, True, errors)
                else:
                    activate_doc = getattr(app, "ActivateDoc", None)
                    if callable(activate_doc):
                        activated = activate_doc(name)
            document = activated or getattr(app, "ActiveDoc", None)
            if document is None or not _matches(document):
                raise RuntimeError(
                    "SolidWorks could not activate the requested document"
                )
            adapter.currentModel = document

        return cast(
            AdapterResult[None],
            adapter._handle_com_operation("activate_model", _activate),
        )

    async def close_model(self, save: bool = False) -> AdapterResult[None]:
        """Close the current SolidWorks model and optionally save first.

        Args:
            save: When ``True``, calls ``Save`` before closing.

        Returns:
            AdapterResult[None]: Result of the close operation.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.WARNING, error="No active model to close"
            )
        model = adapter.currentModel
        app = adapter.swApp
        if model is None or app is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="SolidWorks application is not connected",
            )

        def _close() -> None:
            """Close the model document."""
            if save:
                model.Save()
            title = self._read_model_title(model)
            if not title or title == "Untitled":
                raise RuntimeError(
                    "SolidWorks did not report a document title to close"
                )
            # Cache the path before CloseDoc: SolidWorks disconnects the
            # IModelDoc2 dispatch immediately, and even GetPathName can raise
            # RPC_E_DISCONNECTED after the document is closed.
            model_path = adapter._attempt(
                lambda: self._read_com_member(model, "GetPathName"), default=None
            )
            app.CloseDoc(title)
            if isinstance(model_path, str) and model_path:
                _OPEN_DOCUMENT_CACHE.pop(
                    os.path.normcase(os.path.abspath(model_path)), None
                )
            untracker = getattr(adapter, "_untrack_session_document", None)
            if callable(untracker):
                untracker(model)
            adapter.currentModel = None

        return cast(
            AdapterResult[None],
            adapter._handle_com_operation("close_model", _close),
        )

    async def create_part(
        self, name: str | None = None, units: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create a new part document and set it as active.

        Args:
            name: Reserved for future naming policy.
            units: Reserved for future units policy.

        Returns:
            AdapterResult[SolidWorksModel]: Metadata for the new part document.
        """
        adapter = self._adapter(self)
        if not adapter.is_connected():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        def _create() -> SolidWorksModel:
            """Create a new part."""
            _ = name, units
            model = None
            app = adapter.swApp
            if app is None:
                raise Exception("SolidWorks application is not connected")
            previous_document = adapter._attempt(
                lambda: adapter._get_attr_or_call(app, "ActiveDoc"), default=None
            )

            new_part = getattr(app, "NewPart", None)
            if callable(new_part):
                model = adapter._attempt(new_part)

            # SW 2019 can activate the document but return a null IDispatch
            # from NewPart.  Use the verified active Part before trying the
            # template fallback, which would otherwise open a duplicate part.
            if model is None:
                model = self._active_document_after_creation(
                    app, 1, previous_document
                )

            if model is None:
                part_template = self._resolve_template_path([8, 0, 1, 2, 3], ".prtdot")
                if not part_template:
                    raise Exception("No part template configured in SolidWorks")
                model = app.NewDocument(part_template, 0, 0, 0)

            if model is None:
                model = self._active_document_after_creation(
                    app, 1, previous_document
                )

            if model is None:
                raise Exception("Failed to create new part")

            adapter._attempt(lambda: _sw_type_info.flag_doc(model, 1), default=0)
            adapter.currentModel = model
            tracker = getattr(adapter, "_track_session_document", None)
            if callable(tracker):
                tracker(model)
            title = self._read_model_title(model)
            return SolidWorksModel(
                path="",
                name=title,
                type="Part",
                is_active=True,
                configuration=self._active_configuration_name(adapter, model),
                properties={"created": datetime.now().isoformat()},
            )

        return cast(
            AdapterResult[SolidWorksModel],
            adapter._handle_com_operation("create_part", _create),
        )

    async def create_assembly(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create a new assembly document and set it as active.

        Args:
            name: Reserved for future naming policy.

        Returns:
            AdapterResult[SolidWorksModel]: Metadata for the new assembly document.
        """
        adapter = self._adapter(self)
        if not adapter.is_connected():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        def _create() -> SolidWorksModel:
            """Create a new assembly."""
            _ = name
            model = None
            app = adapter.swApp
            if app is None:
                raise Exception("SolidWorks application is not connected")
            previous_document = adapter._attempt(
                lambda: adapter._get_attr_or_call(app, "ActiveDoc"), default=None
            )

            new_assembly = getattr(app, "NewAssembly", None)
            if callable(new_assembly):
                model = adapter._attempt(new_assembly)

            if model is None:
                model = self._active_document_after_creation(
                    app, 2, previous_document
                )

            if model is None:
                asm_template = self._resolve_template_path([9, 2, 3, 1, 0], ".asmdot")
                if not asm_template:
                    raise Exception("No assembly template configured in SolidWorks")
                model = app.NewDocument(asm_template, 0, 0, 0)

            if model is None:
                model = self._active_document_after_creation(
                    app, 2, previous_document
                )

            if model is None:
                raise Exception("Failed to create new assembly")

            adapter._attempt(lambda: _sw_type_info.flag_doc(model, 2), default=0)
            adapter.currentModel = model
            tracker = getattr(adapter, "_track_session_document", None)
            if callable(tracker):
                tracker(model)
            title = self._read_model_title(model)
            return SolidWorksModel(
                path="",
                name=title,
                type="Assembly",
                is_active=True,
                configuration=self._active_configuration_name(adapter, model),
                properties={"created": datetime.now().isoformat()},
            )

        return cast(
            AdapterResult[SolidWorksModel],
            adapter._handle_com_operation("create_assembly", _create),
        )

    async def create_drawing(
        self, name: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Create a new drawing document and set it as active.

        Args:
            name: Reserved for future naming policy.

        Returns:
            AdapterResult[SolidWorksModel]: Metadata for the new drawing document.
        """
        adapter = self._adapter(self)
        if not adapter.is_connected():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        def _create() -> SolidWorksModel:
            """Create a new drawing."""
            _ = name
            app = adapter.swApp
            if app is None:
                raise Exception("SolidWorks application is not connected")

            drw_template = self._resolve_template_path([1, 0, 2, 3, 9], ".drwdot")
            if not drw_template:
                raise Exception("No drawing template configured in SolidWorks")

            model = app.NewDocument(drw_template, 12, 0.2794, 0.2159)
            if not model:
                raise Exception("Failed to create new drawing")

            adapter._attempt(lambda: _sw_type_info.flag_doc(model, 3), default=0)
            adapter.currentModel = model
            tracker = getattr(adapter, "_track_session_document", None)
            if callable(tracker):
                tracker(model)
            title = self._read_model_title(model)
            return SolidWorksModel(
                path="",
                name=title,
                type="Drawing",
                is_active=True,
                configuration="Default",
                properties={"created": datetime.now().isoformat()},
            )

        return cast(
            AdapterResult[SolidWorksModel],
            adapter._handle_com_operation("create_drawing", _create),
        )

    async def insert_assembly_component(
        self,
        component_path: str,
        x_m: float,
        y_m: float,
        z_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a saved part into the active assembly through ``AddComponent5``.

        Coordinates are supplied in SolidWorks system units (metres). The
        workflow layer owns path authorization and converts client units.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _insert() -> dict[str, str]:
            """Insert and summarize one component."""
            document_type = getattr(adapter.currentModel, "GetType", None)
            document_type = (
                document_type() if callable(document_type) else document_type
            )
            if document_type != 2:
                raise RuntimeError("Active model is not an assembly")
            add_component = getattr(adapter.currentModel, "AddComponent5", None)
            if not callable(add_component):
                raise RuntimeError("Active assembly does not support AddComponent5")
            component = add_component(component_path, 0, "", False, "", x_m, y_m, z_m)
            if component is None:
                raise RuntimeError("SolidWorks did not insert the component")
            summary = self._component_summary(adapter, component)
            document_type_from_path = self._document_type_from_path(component_path)
            if document_type_from_path != "unknown":
                summary["document_type"] = document_type_from_path
                known_types = getattr(adapter, "_assembly_component_types", None)
                if not isinstance(known_types, dict):
                    known_types = {}
                    adapter._assembly_component_types = known_types
                known_types[summary["component_id"]] = document_type_from_path
            return summary

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("insert_assembly_component", _insert),
        )

    async def list_assembly_components(self) -> AdapterResult[list[dict[str, Any]]]:
        """List all component summaries from the active assembly via ``GetComponents``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, str]]:
            """Enumerate and normalize component objects."""
            document_type = getattr(adapter.currentModel, "GetType", None)
            document_type = (
                document_type() if callable(document_type) else document_type
            )
            if document_type != 2:
                raise RuntimeError("Active model is not an assembly")
            get_components = getattr(adapter.currentModel, "GetComponents", None)
            if not callable(get_components):
                raise RuntimeError("Active assembly does not support GetComponents")
            raw_components = get_components(False)
            if raw_components is None:
                return []
            if not isinstance(raw_components, (list, tuple)):
                raw_components = [raw_components]
            return [
                self._component_summary(adapter, component)
                for component in raw_components
                if component is not None
            ]

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_assembly_components", _list),
        )

    async def check_assembly_interference(
        self,
        include_coincident: bool,
        include_multibody_part_interferences: bool,
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Run `IInterferenceDetectionMgr` for the active assembly without UI commands."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _check() -> list[dict[str, Any]]:
            if self._read_com_member(adapter.currentModel, "GetType") != 2:
                raise RuntimeError("Active model is not an assembly")
            manager = self._read_com_member(
                adapter.currentModel, "InterferenceDetectionManager"
            )
            if manager is None:
                manager = self._read_com_member(
                    adapter.currentModel, "get_InterferenceDetectionManager"
                )
            if manager is None:
                raise RuntimeError(
                    "Active assembly does not support interference detection"
                )
            previous_coincident = self._read_com_member(
                manager, "TreatCoincidenceAsInterference"
            )
            previous_multibody = self._read_com_member(
                manager, "IncludeMultibodyPartInterferences"
            )
            try:
                manager.TreatCoincidenceAsInterference = include_coincident
                manager.IncludeMultibodyPartInterferences = (
                    include_multibody_part_interferences
                )
                getter = getattr(manager, "GetInterferences", None)
                if not callable(getter):
                    raise RuntimeError(
                        "Active assembly does not expose GetInterferences"
                    )
                interferences: list[dict[str, Any]] = []
                for interference in self._as_com_collection(getter()):
                    component_names = [
                        _normalize_solidworks_text(
                            self._read_com_member(component, "Name2")
                            or self._read_com_member(component, "Name")
                            or ""
                        )
                        for component in self._as_com_collection(
                            self._read_com_member(interference, "Components")
                        )
                    ]
                    component_names = [name for name in component_names if name]
                    volume_m3 = self._read_com_member(interference, "Volume")
                    if (
                        len(component_names) < 2
                        or not isinstance(volume_m3, (int, float))
                        or not isfinite(float(volume_m3))
                        or float(volume_m3) < 0
                    ):
                        raise RuntimeError(
                            "SolidWorks returned an incomplete interference result"
                        )
                    volume_mm3 = float(volume_m3) * 1_000_000_000.0
                    if not isfinite(volume_mm3):
                        raise RuntimeError(
                            "SolidWorks returned an interference volume outside the supported range"
                        )
                    interferences.append(
                        {
                            "component_names": component_names,
                            "volume_mm3": volume_mm3,
                            "is_possible": bool(
                                self._read_com_member(
                                    interference, "IsPossibleInterference"
                                )
                            ),
                        }
                    )
                return interferences
            finally:
                try:
                    if isinstance(previous_coincident, bool):
                        manager.TreatCoincidenceAsInterference = previous_coincident
                finally:
                    try:
                        if isinstance(previous_multibody, bool):
                            manager.IncludeMultibodyPartInterferences = (
                                previous_multibody
                            )
                    finally:
                        done = getattr(manager, "Done", None)
                        if callable(done):
                            done()

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("check_assembly_interference", _check),
        )

    async def get_assembly_component(
        self, component_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Find one active-assembly component using ``GetComponentByName``.

        A bounded ``GetComponents`` scan is retained for old or late-bound COM
        clients that do not expose ``GetComponentByName``. Neither path changes
        the SolidWorks selection set.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _get() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 2:
                raise RuntimeError("Active model is not an assembly")
            component = None
            getter = getattr(adapter.currentModel, "GetComponentByName", None)
            if callable(getter):
                component = getter(component_name)
            if component is None:
                for candidate in self._assembly_components(adapter):
                    summary = self._component_summary(adapter, candidate)
                    if summary["display_name"] == component_name:
                        component = candidate
                        break
            if component is None:
                raise RuntimeError(
                    f"Assembly component '{component_name}' was not found"
                )
            return self._component_summary(adapter, component)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("get_assembly_component", _get),
        )

    async def list_assembly_mate_entities(
        self,
        component_id: str | None = None,
        entity_types: tuple[str, ...] = ("face", "edge"),
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List persistent face and edge references from the active assembly.

        The persistent-reference token is an adapter-to-workflow implementation
        detail. The MCP tool converts it to an opaque, revision-bound entity ID
        before returning any response to a client.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        requested_types = set(entity_types)

        def _list() -> list[dict[str, str]]:
            stage = "reading the model extension"
            try:
                extension = self._model_extension(adapter)
                if extension is None:
                    raise RuntimeError("Active assembly does not expose a model extension")

                entities: list[dict[str, str]] = []
                stage = "listing assembly components"
                for component in self._assembly_components(adapter):
                    stage = "reading the assembly component summary"
                    summary = self._component_summary(adapter, component)
                    if component_id is not None and summary["component_id"] != component_id:
                        continue
                    stage = f"reading bodies for component {summary['display_name']}"
                    face_position = 0
                    edge_position = 0
                    for body in self._component_bodies(adapter, component):
                        stage = f"reading topology for component {summary['display_name']}"
                        _sw_type_info.flag_methods(body, "IBody2")
                        if "face" in requested_types:
                            for face in self._body_entities(
                                adapter, body, "GetFirstFace", "GetNextFace", "GetFaces"
                            ):
                                face_position += 1
                                position = face_position
                                stage = f"encoding a face reference for component {summary['display_name']}"
                                try:
                                    reference = self._persistent_reference_token(
                                        extension, face
                                    )
                                except RuntimeError:
                                    reference = self._assembly_entity_locator(
                                        summary["component_id"], "face", position
                                    )
                                entities.append(
                                {
                                    "component_id": summary["component_id"],
                                    "entity_type": "face",
                                    "display_name": (
                                        f"{summary['display_name']} Face {position}"
                                    ),
                                    "persistent_reference": reference,
                                }
                                )
                        if "edge" in requested_types:
                            for edge in self._body_entities(
                                adapter, body, "GetFirstEdge", "GetNext", "GetEdges"
                            ):
                                edge_position += 1
                                position = edge_position
                                stage = f"encoding an edge reference for component {summary['display_name']}"
                                try:
                                    reference = self._persistent_reference_token(
                                        extension, edge
                                    )
                                except RuntimeError:
                                    reference = self._assembly_entity_locator(
                                        summary["component_id"], "edge", position
                                    )
                                entities.append(
                                {
                                    "component_id": summary["component_id"],
                                    "entity_type": "edge",
                                    "display_name": (
                                        f"{summary['display_name']} Edge {position}"
                                    ),
                                    "persistent_reference": reference,
                                }
                                )
                return entities
            except Exception as error:
                logger.debug("Assembly mate-entity enumeration failed while {}: {!r}", stage, error)
                raise

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_assembly_mate_entities", _list),
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
        """Create one ``AddMate5`` constraint from internal persistent references."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if mate_type not in _SW_MATE_TYPES or alignment not in _SW_MATE_ALIGNMENTS:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Unsupported assembly mate type or alignment",
            )

        def _decode_reference(token: str) -> bytes:
            try:
                return base64.b64decode(token.encode("ascii"), validate=True)
            except (UnicodeEncodeError, ValueError) as exc:
                raise RuntimeError("Assembly mate entity reference is invalid") from exc

        def _resolve_entity(extension: Any, token: str) -> Any:
            locator = self._decode_assembly_entity_locator(token)
            if locator is not None:
                component_id, entity_type, position = locator
                entity_members = {
                    "face": ("GetFirstFace", "GetNextFace", "GetFaces"),
                    "edge": ("GetFirstEdge", "GetNext", "GetEdges"),
                }
                members = entity_members[entity_type]
                remaining = position
                for component in self._assembly_components(adapter):
                    summary = self._component_summary(adapter, component)
                    if summary["component_id"] != component_id:
                        continue
                    for body in self._component_bodies(adapter, component):
                        candidates = self._body_entities(adapter, body, *members)
                        if remaining <= len(candidates):
                            return candidates[remaining - 1]
                        remaining -= len(candidates)
                    break
                raise RuntimeError("Assembly mate entity can no longer be resolved")
            if extension is None:
                raise RuntimeError("Active model does not support persistent references")
            resolver = getattr(extension, "GetObjectByPersistReference3", None)
            if not callable(resolver):
                raise RuntimeError(
                    "Active model does not support persistent references"
                )
            raw_resolved = resolver(_decode_reference(token))
            entity = raw_resolved
            error_code = 0
            if isinstance(raw_resolved, (tuple, list)):
                if not raw_resolved:
                    entity = None
                else:
                    entity = raw_resolved[0]
                    if len(raw_resolved) > 1 and isinstance(
                        raw_resolved[-1], (bool, int)
                    ):
                        error_code = int(raw_resolved[-1])
            if entity is None or error_code != 0:
                raise RuntimeError("Assembly mate entity can no longer be resolved")
            return entity

        def _select(entity: Any, append: bool) -> bool:
            selector = getattr(adapter, "select_entity", None)
            if callable(selector):
                return bool(selector(entity, append))
            for member_name, arguments in (
                ("Select4", (append, None)),
                ("Select2", (append, 0)),
                ("Select", (append,)),
            ):
                method = getattr(entity, member_name, None)
                if callable(method) and bool(method(*arguments)):
                    return True
            return False

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 2:
                raise RuntimeError("Active model is not an assembly")
            extension = self._model_extension(adapter)
            first_entity = _resolve_entity(extension, first_persistent_reference)
            second_entity = _resolve_entity(extension, second_persistent_reference)
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            if not callable(clear_selection):
                raise RuntimeError("Active assembly does not support ClearSelection2")
            add_mate = getattr(adapter.currentModel, "AddMate5", None)
            if not callable(add_mate):
                raise RuntimeError("Active assembly does not support AddMate5")
            clear_selection(True)
            try:
                if not _select(first_entity, False) or not _select(second_entity, True):
                    raise RuntimeError(
                        "SolidWorks could not select the requested mate entities"
                    )
                arguments = (
                    _SW_MATE_TYPES[mate_type],
                    _SW_MATE_ALIGNMENTS[alignment],
                    flip,
                    distance_m,
                    0.0,
                    0.0,
                    1.0,
                    1.0,
                    angle_radians,
                    0.0,
                    0.0,
                    for_positioning_only,
                    lock_rotation,
                    0,
                    # ``ErrorStatus`` is a required COM out parameter.
                    # Passing a plain integer works with a few generated
                    # wrappers but produces DISP_E_TYPEMISMATCH through the
                    # legacy IDispatch view used by this installation.
                    win32com.client.VARIANT(
                        pythoncom.VT_BYREF | pythoncom.VT_I4, 0
                    ),
                )
                try:
                    raw_mate = add_mate(*arguments)
                except TypeError:
                    raw_mate = add_mate(*arguments[:-1])
                except Exception as error:
                    if not _is_type_mismatch_error(error):
                        raise
                    sw_lib = _get_sw_comtypes_lib()
                    if sw_lib is None:
                        raise
                    assembly = _bridge_com_to_comtypes(
                        adapter.currentModel, sw_lib.IAssemblyDoc
                    )
                    raw_mate = assembly.AddMate5(*arguments[:-1])
                mate_feature = raw_mate
                error_code = 0
                output_error = arguments[-1]
                output_value = getattr(output_error, "value", None)
                if isinstance(output_value, (bool, int)):
                    error_code = int(output_value)
                if isinstance(raw_mate, (tuple, list)):
                    mate_feature = None
                    for value in raw_mate:
                        if isinstance(value, (bool, int)):
                            error_code = int(value)
                        elif value is not None:
                            mate_feature = value
                if mate_feature is None or mate_feature is False or error_code != 0:
                    raise RuntimeError(
                        "SolidWorks rejected the requested assembly mate"
                    )
                display_name = _normalize_solidworks_text(
                    self._read_com_member(mate_feature, "Name")
                    or self._read_com_member(mate_feature, "GetName")
                    or f"{mate_type.title()} Mate"
                ).strip()
                return {"mate_type": mate_type, "display_name": display_name}
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_assembly_mate", _create),
        )

    async def list_assembly_mates(self) -> AdapterResult[list[dict[str, Any]]]:
        """List current assembly mates as workflow-only persistent references."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, Any]]:
            if self._read_com_member(adapter.currentModel, "GetType") != 2:
                raise RuntimeError("Active model is not an assembly")
            extension = self._model_extension(adapter)
            getter = getattr(adapter.currentModel, "GetMates", None)
            if extension is None or not callable(getter):
                raise RuntimeError("Active assembly does not support mate enumeration")
            rows: list[dict[str, Any]] = []
            for position, mate in enumerate(self._as_com_collection(getter()), start=1):
                if mate is None:
                    continue
                name = _normalize_solidworks_text(
                    self._read_com_member(mate, "Name")
                    or self._read_com_member(mate, "GetName")
                    or f"Mate {position}"
                ).strip()
                raw_type = self._read_com_member(mate, "Type")
                mate_type = _normalize_solidworks_text(raw_type).strip() or "unknown"
                rows.append(
                    {
                        "display_name": name or f"Mate {position}",
                        "mate_type": mate_type,
                        "persistent_reference": self._persistent_reference_token(
                            extension, mate
                        ),
                    }
                )
            return rows

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_assembly_mates", _list),
        )

    async def delete_assembly_mate(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete a mate resolved from a server-only persistent reference."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _delete() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 2:
                raise RuntimeError("Active model is not an assembly")
            mate = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Assembly mate"
            )
            display_name = _normalize_solidworks_text(
                self._read_com_member(mate, "Name")
                or self._read_com_member(mate, "GetName")
                or "Assembly Mate"
            ).strip()
            deleter = getattr(adapter.currentModel, "DeleteMate", None)
            if not callable(deleter):
                raise RuntimeError("Active assembly does not support DeleteMate")
            accepted: Any = None
            try:
                accepted = deleter(mate)
            except TypeError as exc:
                if not self._select_annotation_entity(adapter, mate):
                    raise RuntimeError(
                        "SolidWorks could not select the requested mate"
                    ) from exc
                accepted = deleter()
            finally:
                adapter.currentModel.ClearSelection2(True)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected the assembly mate deletion")
            return {"display_name": display_name or "Assembly Mate"}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("delete_assembly_mate", _delete),
        )

    async def create_drawing_view(
        self,
        model_path: str,
        model_view_name: str,
        x_m: float,
        y_m: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Create one standard model view through ``CreateDrawViewFromModelView3``.

        The coordinates are SolidWorks drawing-sheet system units (metres). The
        workflow layer supplies only a saved, trusted native model path and a
        fixed standard view name such as ``*Front``.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _create() -> dict[str, str]:
            document_type = getattr(adapter.currentModel, "GetType", None)
            document_type = (
                document_type() if callable(document_type) else document_type
            )
            if document_type != 3:
                raise RuntimeError("Active model is not a drawing")
            create_view = getattr(
                adapter.currentModel, "CreateDrawViewFromModelView3", None
            )
            if not callable(create_view):
                raise RuntimeError(
                    "Active drawing does not support CreateDrawViewFromModelView3"
                )
            view = create_view(model_path, model_view_name, x_m, y_m, 0.0)
            if view is not None and view is not False:
                return self._drawing_view_summary(adapter, view)
            for candidate_name in self._drawing_view_name_candidates(
                adapter, model_path, model_view_name
            ):
                if candidate_name == model_view_name:
                    continue
                view = create_view(model_path, candidate_name, x_m, y_m, 0.0)
                if view is not None and view is not False:
                    return self._drawing_view_summary(adapter, view)
            raise RuntimeError("SolidWorks did not create the drawing view")

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_view", _create),
        )

    async def list_drawing_views(self) -> AdapterResult[list[dict[str, Any]]]:
        """List drawing model views via ``GetFirstView``/``GetNextView``.

        SolidWorks returns the sheet object as the first ``IView``. It is
        intentionally skipped so the public list contains only model views.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, str]]:
            document_type = getattr(adapter.currentModel, "GetType", None)
            document_type = (
                document_type() if callable(document_type) else document_type
            )
            if document_type != 3:
                raise RuntimeError("Active model is not a drawing")
            _sw_type_info.flag_method(
                adapter.currentModel, "IDrawingDoc", "GetFirstView"
            )
            get_first_view = getattr(adapter.currentModel, "GetFirstView", None)
            if not callable(get_first_view):
                raise RuntimeError("Active drawing does not support GetFirstView")
            sheet_view = get_first_view()
            if sheet_view is None:
                return []
            _sw_type_info.flag_method(sheet_view, "IView", "GetNextView")
            get_next_view = getattr(sheet_view, "GetNextView", None)
            view = get_next_view() if callable(get_next_view) else None
            views: list[dict[str, str]] = []
            while view is not None:
                views.append(self._drawing_view_summary(adapter, view))
                _sw_type_info.flag_method(view, "IView", "GetNextView")
                get_next_view = getattr(view, "GetNextView", None)
                view = get_next_view() if callable(get_next_view) else None
            return views

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_drawing_views", _list),
        )

    async def activate_drawing_view(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one current drawing view through ``IDrawingDoc.ActivateView``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _activate() -> dict[str, str]:
            view = self._drawing_view_by_id(adapter, view_id)
            activator = getattr(adapter.currentModel, "ActivateView", None)
            if not callable(activator):
                raise RuntimeError("Active drawing does not support ActivateView")
            accepted = activator(view_id)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected drawing-view activation")
            return self._drawing_view_summary(adapter, view)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("activate_drawing_view", _activate),
        )

    async def list_drawing_sheets(self) -> AdapterResult[list[dict[str, Any]]]:
        """List sheet names and the current active sheet through ``IDrawingDoc``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, Any]]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            get_sheet_names = getattr(adapter.currentModel, "GetSheetNames", None)
            if not callable(get_sheet_names):
                raise RuntimeError("Active drawing does not support GetSheetNames")
            raw_names = get_sheet_names()
            if isinstance(raw_names, tuple) and len(raw_names) == 1:
                raw_names = raw_names[0]
            if raw_names is None:
                return []
            if not isinstance(raw_names, (list, tuple)):
                raise RuntimeError("SolidWorks returned invalid drawing sheet names")
            current_sheet = self._read_com_member(adapter.currentModel, "GetCurrentSheet")
            active_name = _normalize_solidworks_text(
                self._read_com_member(current_sheet, "GetName")
                or self._read_com_member(current_sheet, "Name")
            ).strip()
            sheets: list[dict[str, Any]] = []
            for raw_name in raw_names:
                name = _normalize_solidworks_text(raw_name).strip()
                if not name:
                    continue
                sheets.append({"name": name[:240], "is_active": name == active_name})
            return sheets

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_drawing_sheets", _list),
        )

    async def activate_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Activate one exact current sheet through ``IDrawingDoc.ActivateSheet``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _activate() -> dict[str, Any]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            get_sheet_names = getattr(adapter.currentModel, "GetSheetNames", None)
            activator = getattr(adapter.currentModel, "ActivateSheet", None)
            if not callable(get_sheet_names) or not callable(activator):
                raise RuntimeError("Active drawing does not support sheet activation")
            raw_names = get_sheet_names()
            if isinstance(raw_names, tuple) and len(raw_names) == 1:
                raw_names = raw_names[0]
            if raw_names is None:
                raise RuntimeError("SolidWorks returned no drawing sheet names")
            if not isinstance(raw_names, (list, tuple)):
                raise RuntimeError("SolidWorks returned invalid drawing sheet names")
            known_names = {
                _normalize_solidworks_text(candidate).strip()
                for candidate in raw_names
                if _normalize_solidworks_text(candidate).strip()
            }
            if sheet_name not in known_names:
                raise RuntimeError("The requested drawing sheet no longer exists")
            accepted = activator(sheet_name)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected drawing-sheet activation")
            current_sheet = self._read_com_member(adapter.currentModel, "GetCurrentSheet")
            active_name = _normalize_solidworks_text(
                self._read_com_member(current_sheet, "GetName")
                or self._read_com_member(current_sheet, "Name")
            ).strip()
            if active_name and active_name != sheet_name:
                raise RuntimeError("SolidWorks did not activate the requested drawing sheet")
            return {"name": sheet_name, "is_active": True}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("activate_drawing_sheet", _activate),
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
        """Create a drawing sheet through NewSheet4 with old-version fallback."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _create() -> dict[str, Any]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            get_sheet_names = getattr(adapter.currentModel, "GetSheetNames", None)
            if not callable(get_sheet_names):
                raise RuntimeError("Active drawing does not support GetSheetNames")
            raw_names = get_sheet_names()
            if isinstance(raw_names, tuple) and len(raw_names) == 1:
                raw_names = raw_names[0]
            names = {
                _normalize_solidworks_text(candidate).strip()
                for candidate in (raw_names or ())
                if _normalize_solidworks_text(candidate).strip()
            }
            if sheet_name in names:
                raise RuntimeError("A drawing sheet with this name already exists")
            paper_enum = _SW_DRAWING_PAPER_SIZES.get(paper_size)
            if paper_enum is None:
                raise RuntimeError("Unsupported SolidWorks drawing paper size")
            methods = [
                ("NewSheet4", (sheet_name, paper_enum, _SW_DRAWING_TEMPLATE_NONE,
                               scale_numerator, scale_denominator,
                               first_angle_projection, "", width_m, height_m, "",
                               0.0, 0.0, 0.0, 0.0, zone_rows, zone_columns)),
                ("NewSheet3", (sheet_name, paper_enum, _SW_DRAWING_TEMPLATE_NONE,
                               scale_numerator, scale_denominator,
                               first_angle_projection, "", width_m, height_m, "")),
                ("NewSheet2", (sheet_name, paper_enum, _SW_DRAWING_TEMPLATE_NONE,
                               scale_numerator, scale_denominator,
                               first_angle_projection, "", width_m, height_m)),
            ]
            last_error: BaseException | None = None
            for method_name, args in methods:
                creator = getattr(adapter.currentModel, method_name, None)
                if not callable(creator):
                    continue
                try:
                    accepted = creator(*args)
                except (TypeError, ValueError) as error:
                    last_error = error
                    continue
                if accepted is False:
                    raise RuntimeError(f"SolidWorks rejected {method_name} sheet creation")
                current_sheet = self._read_com_member(adapter.currentModel, "GetCurrentSheet")
                active_name = _normalize_solidworks_text(
                    self._read_com_member(current_sheet, "GetName")
                    or self._read_com_member(current_sheet, "Name")
                ).strip()
                return {"name": sheet_name, "is_active": active_name in {"", sheet_name}, "created": True}
            if last_error is not None:
                raise RuntimeError(f"SolidWorks drawing-sheet creation signature was rejected: {last_error}")
            raise RuntimeError("Active drawing does not support NewSheet4/NewSheet3/NewSheet2")

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_sheet", _create),
        )

    async def delete_drawing_sheet(
        self, sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one exact sheet through ``IDrawingDoc.DeleteSheet``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _delete() -> dict[str, Any]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            get_sheet_names = getattr(adapter.currentModel, "GetSheetNames", None)
            deleter = getattr(adapter.currentModel, "DeleteSheet", None)
            if not callable(get_sheet_names) or not callable(deleter):
                raise RuntimeError("Active drawing does not support sheet deletion")
            raw_names = get_sheet_names()
            if isinstance(raw_names, tuple) and len(raw_names) == 1:
                raw_names = raw_names[0]
            if not isinstance(raw_names, (list, tuple)):
                raise RuntimeError("SolidWorks returned invalid drawing sheet names")
            names = [
                _normalize_solidworks_text(candidate).strip()
                for candidate in raw_names
                if _normalize_solidworks_text(candidate).strip()
            ]
            if sheet_name not in names:
                raise RuntimeError("The requested drawing sheet no longer exists")
            if len(names) <= 1:
                raise RuntimeError("SolidWorks requires at least one drawing sheet")
            accepted = deleter(sheet_name)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected drawing-sheet deletion")
            return {"name": sheet_name, "deleted": True}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("delete_drawing_sheet", _delete),
        )

    async def rename_drawing_sheet(
        self, sheet_name: str, new_sheet_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Rename one current sheet through the native ``ISheet.SetName`` API."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _rename() -> dict[str, Any]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            get_sheet_names = getattr(adapter.currentModel, "GetSheetNames", None)
            activate_sheet = getattr(adapter.currentModel, "ActivateSheet", None)
            get_current_sheet = getattr(adapter.currentModel, "GetCurrentSheet", None)
            if not all(
                callable(member)
                for member in (get_sheet_names, activate_sheet, get_current_sheet)
            ):
                raise RuntimeError("Active drawing does not support sheet renaming")
            raw_names = get_sheet_names()
            if isinstance(raw_names, tuple) and len(raw_names) == 1:
                raw_names = raw_names[0]
            if not isinstance(raw_names, (list, tuple)):
                raise RuntimeError("SolidWorks returned invalid drawing sheet names")
            names = [
                _normalize_solidworks_text(candidate).strip()
                for candidate in raw_names
                if _normalize_solidworks_text(candidate).strip()
            ]
            if sheet_name not in names:
                raise RuntimeError("The requested drawing sheet no longer exists")
            if any(name.casefold() == new_sheet_name.casefold() for name in names):
                raise RuntimeError("A drawing sheet with this name already exists")
            accepted = activate_sheet(sheet_name)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected drawing-sheet activation")
            sheet = get_current_sheet()
            rename = getattr(sheet, "SetName", None)
            if not callable(rename):
                raise RuntimeError("The requested drawing sheet does not support SetName")
            accepted = rename(new_sheet_name)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected drawing-sheet renaming")
            renamed = get_sheet_names()
            if isinstance(renamed, tuple) and len(renamed) == 1:
                renamed = renamed[0]
            renamed_names = {
                _normalize_solidworks_text(candidate).strip()
                for candidate in (renamed or ())
                if _normalize_solidworks_text(candidate).strip()
            }
            if new_sheet_name not in renamed_names or sheet_name in renamed_names:
                raise RuntimeError("SolidWorks did not verify the drawing-sheet rename")
            return {"name": new_sheet_name, "renamed_from": sheet_name}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("rename_drawing_sheet", _rename),
        )

    async def reorder_drawing_sheets(
        self, sheet_names: list[str]
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Reorder every drawing sheet through ``IDrawingDoc.ReorderSheets``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _reorder() -> list[dict[str, Any]]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            get_names = getattr(adapter.currentModel, "GetSheetNames", None)
            reorder = getattr(adapter.currentModel, "ReorderSheets", None)
            if not callable(get_names) or not callable(reorder):
                raise RuntimeError("Active drawing does not support sheet reordering")
            current = get_names()
            if isinstance(current, tuple) and len(current) == 1:
                current = current[0]
            current_names = [_normalize_solidworks_text(value).strip() for value in (current or ())]
            if {value.casefold() for value in current_names} != {value.casefold() for value in sheet_names}:
                raise RuntimeError("Sheet order must contain every current drawing sheet exactly once")
            accepted = reorder(sheet_names)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected drawing-sheet reordering")
            reordered = get_names()
            if isinstance(reordered, tuple) and len(reordered) == 1:
                reordered = reordered[0]
            names = [_normalize_solidworks_text(value).strip() for value in (reordered or ())]
            if names != sheet_names:
                raise RuntimeError("SolidWorks did not verify the requested drawing-sheet order")
            current_sheet = self._read_com_member(adapter.currentModel, "GetCurrentSheet")
            active = _normalize_solidworks_text(self._read_com_member(current_sheet, "GetName") or self._read_com_member(current_sheet, "Name")).strip()
            return [{"name": name, "is_active": name == active} for name in names]

        return cast(AdapterResult[list[dict[str, Any]]], adapter._handle_com_operation("reorder_drawing_sheets", _reorder))

    async def get_drawing_view_outline(
        self, view_id: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one active-drawing view's ``IView.GetOutline`` extents in metres."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _get() -> dict[str, Any]:
            view = self._drawing_view_by_id(adapter, view_id)
            _sw_type_info.flag_method(view, "IView", "GetOutline")
            get_outline = getattr(view, "GetOutline", None)
            if not callable(get_outline):
                raise RuntimeError(
                    "The requested drawing view does not support GetOutline"
                )
            outline = get_outline()
            if isinstance(outline, tuple) and len(outline) == 1:
                outline = outline[0]
            if not isinstance(outline, (list, tuple)) or len(outline) != 4:
                raise RuntimeError(
                    "SolidWorks returned an invalid drawing-view outline"
                )
            try:
                left_m, bottom_m, right_m, top_m = (float(value) for value in outline)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "SolidWorks returned a non-numeric drawing-view outline"
                ) from exc
            if not all(isfinite(value) for value in (left_m, bottom_m, right_m, top_m)):
                raise RuntimeError(
                    "SolidWorks returned a non-finite drawing-view outline"
                )
            if right_m < left_m or top_m < bottom_m:
                raise RuntimeError("SolidWorks returned inverted drawing-view extents")
            return {
                "view_id": view_id,
                "left_m": left_m,
                "bottom_m": bottom_m,
                "right_m": right_m,
                "top_m": top_m,
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("get_drawing_view_outline", _get),
        )

    async def update_drawing_view_layout(
        self,
        view_id: str,
        x_m: float | None,
        y_m: float | None,
        scale_decimal: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Move and/or rescale one current view through typed ``IView`` members."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _update() -> dict[str, Any]:
            view = self._drawing_view_by_id(adapter, view_id)
            if x_m is not None and y_m is not None:
                try:
                    setattr(view, "Position", (x_m, y_m))
                except (AttributeError, TypeError, ValueError) as error:
                    raise RuntimeError("SolidWorks drawing view does not support Position updates") from error
            if scale_decimal is not None:
                try:
                    setattr(view, "ScaleDecimal", scale_decimal)
                except (AttributeError, TypeError, ValueError) as error:
                    raise RuntimeError("SolidWorks drawing view does not support ScaleDecimal updates") from error
            result = self._drawing_view_summary(adapter, view)
            result.update({
                "position_m": {"x": x_m, "y": y_m} if x_m is not None else None,
                "scale_decimal": scale_decimal,
            })
            return result

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("update_drawing_view_layout", _update),
        )

    async def align_drawing_views(
        self,
        view_ids: list[str],
        direction: str,
        gap_m: float,
        start_x_m: float | None,
        start_y_m: float | None,
    ) -> AdapterResult[dict[str, Any]]:
        """Arrange current drawing views from their native ``GetOutline`` sizes."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _align() -> dict[str, Any]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            if direction not in {"horizontal", "vertical"}:
                raise RuntimeError("Drawing-view alignment direction is invalid")
            if not isfinite(gap_m) or gap_m < 0:
                raise RuntimeError("Drawing-view alignment gap is invalid")

            def _outline(view: Any) -> tuple[float, float, float, float]:
                _sw_type_info.flag_method(view, "IView", "GetOutline")
                getter = getattr(view, "GetOutline", None)
                if not callable(getter):
                    raise RuntimeError("The requested drawing view does not support GetOutline")
                raw = getter()
                if isinstance(raw, tuple) and len(raw) == 1:
                    raw = raw[0]
                if not isinstance(raw, (list, tuple)) or len(raw) != 4:
                    raise RuntimeError("SolidWorks returned an invalid drawing-view outline")
                try:
                    values = tuple(float(value) for value in raw)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("SolidWorks returned a non-numeric drawing-view outline") from exc
                if not all(isfinite(value) for value in values):
                    raise RuntimeError("SolidWorks returned a non-finite drawing-view outline")
                left_m, bottom_m, right_m, top_m = values
                if right_m < left_m or top_m < bottom_m:
                    raise RuntimeError("SolidWorks returned inverted drawing-view extents")
                return values

            resolved = [self._drawing_view_by_id(adapter, view_id) for view_id in view_ids]
            outlines = [_outline(view) for view in resolved]
            first_left, first_bottom, first_right, first_top = outlines[0]
            cursor = start_x_m if start_x_m is not None else first_left
            baseline = (
                start_y_m
                if start_y_m is not None
                else (first_bottom + first_top) / 2
            )
            if direction == "vertical":
                cursor = start_y_m if start_y_m is not None else first_bottom
                baseline = (
                    start_x_m
                    if start_x_m is not None
                    else (first_left + first_right) / 2
                )

            placements: list[dict[str, Any]] = []
            for view, outline in zip(resolved, outlines, strict=True):
                left_m, bottom_m, right_m, top_m = outline
                width_m, height_m = right_m - left_m, top_m - bottom_m
                if direction == "horizontal":
                    center_x_m, center_y_m = cursor + width_m / 2, baseline
                    cursor += width_m + gap_m
                else:
                    center_x_m, center_y_m = baseline, cursor + height_m / 2
                    cursor += height_m + gap_m
                try:
                    setattr(view, "Position", (center_x_m, center_y_m))
                except (AttributeError, TypeError, ValueError) as error:
                    raise RuntimeError("SolidWorks drawing view does not support Position updates") from error
                summary = self._drawing_view_summary(adapter, view)
                placements.append(
                    {
                        **summary,
                        "position_m": {"x": center_x_m, "y": center_y_m},
                        "outline_m": {
                            "left": center_x_m - width_m / 2,
                            "bottom": center_y_m - height_m / 2,
                            "right": center_x_m + width_m / 2,
                            "top": center_y_m + height_m / 2,
                        },
                    }
                )
            return {"direction": direction, "gap_m": gap_m, "views": placements}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("align_drawing_views", _align),
        )

    async def rebuild_drawing(self) -> AdapterResult[dict[str, Any]]:
        """Call ``IDrawingDoc.RebuildAndSave`` for the active drawing only."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _rebuild() -> dict[str, Any]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            _sw_type_info.flag_method(
                adapter.currentModel, "IDrawingDoc", "RebuildAndSave"
            )
            rebuild_and_save = getattr(adapter.currentModel, "RebuildAndSave", None)
            if not callable(rebuild_and_save):
                raise RuntimeError("Active drawing does not support RebuildAndSave")
            accepted = rebuild_and_save()
            if accepted is False:
                raise RuntimeError("SolidWorks rejected the drawing rebuild and save")
            return {"rebuilt": True, "saved": True}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("rebuild_drawing", _rebuild),
        )

    async def create_projected_drawing_view(
        self, source_view_id: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create a projected drawing view from a known active-drawing view.

        The public workflow supplies only a view identifier that it previously
        issued for the active drawing. This method re-resolves the identifier in
        the drawing before selecting it, so an arbitrary COM object cannot be
        targeted through the MCP boundary.  When the connected SolidWorks
        version does not expose ``IDrawingDoc.CreateProjectionView``, this
        operation fails explicitly rather than creating an independent standard
        view with different associativity and geometry.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _create() -> dict[str, str]:
            document_type = getattr(adapter.currentModel, "GetType", None)
            document_type = (
                document_type() if callable(document_type) else document_type
            )
            if document_type != 3:
                raise RuntimeError("Active model is not a drawing")
            _sw_type_info.flag_method(
                adapter.currentModel, "IDrawingDoc", "GetFirstView"
            )
            get_first_view = getattr(adapter.currentModel, "GetFirstView", None)
            if not callable(get_first_view):
                raise RuntimeError("Active drawing does not support GetFirstView")

            sheet_view = get_first_view()
            _sw_type_info.flag_method(sheet_view, "IView", "GetNextView")
            view = (
                sheet_view.GetNextView()
                if sheet_view is not None
                and callable(getattr(sheet_view, "GetNextView", None))
                else None
            )
            source_view = None
            while view is not None:
                if (
                    self._drawing_view_summary(adapter, view)["view_id"]
                    == source_view_id
                ):
                    source_view = view
                    break
                _sw_type_info.flag_method(view, "IView", "GetNextView")
                get_next_view = getattr(view, "GetNextView", None)
                view = get_next_view() if callable(get_next_view) else None
            if source_view is None:
                raise RuntimeError("The requested drawing view no longer exists")

            extension = adapter._attempt(
                lambda: getattr(adapter.currentModel, "Extension", None),
                default=None,
            )
            select_by_id = adapter._attempt(
                lambda: getattr(extension, "SelectByID2", None), default=None
            )
            # The local gen_py cache can be newer or older than the running
            # SolidWorks instance. It is useful for method flagging only; the
            # actual invocation is the capability check.
            _sw_type_info.flag_method(
                adapter.currentModel, "IDrawingDoc", "CreateProjectionView"
            )
            try:
                create_projection = getattr(
                    adapter.currentModel, "CreateProjectionView", None
                )
            except Exception as exc:
                if _is_member_not_found_error(exc):
                    raise RuntimeError(
                        f"{ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED}: "
                        "the connected SolidWorks version does not expose "
                        "IDrawingDoc.CreateProjectionView"
                    ) from exc
                raise
            clear_selection = adapter._attempt(
                lambda: getattr(adapter.currentModel, "ClearSelection2", None),
                default=None,
            )
            if not callable(create_projection):
                raise RuntimeError(
                    f"{ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED}: "
                    "the connected SolidWorks version does not expose "
                    "IDrawingDoc.CreateProjectionView"
                )
            if not callable(select_by_id):
                raise RuntimeError("Active drawing does not support view selection")
            try:
                selected = select_by_id(
                    source_view_id, "DRAWINGVIEW", 0.0, 0.0, 0.0, False, 0, None, 0
                )
                if not selected:
                    raise RuntimeError(
                        "SolidWorks could not select the source drawing view"
                    )
                try:
                    projected_view = create_projection(x_m, y_m, 0.0)
                except Exception as exc:
                    if _is_member_not_found_error(exc):
                        raise RuntimeError(
                            f"{ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED}: "
                            "the connected SolidWorks version does not expose "
                            "IDrawingDoc.CreateProjectionView"
                        ) from exc
                    raise
                if projected_view is None:
                    raise RuntimeError(
                        "SolidWorks did not create the projected drawing view"
                    )
                return self._drawing_view_summary(adapter, projected_view)
            finally:
                if callable(clear_selection):
                    clear_selection(True)

        result = cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_projected_drawing_view", _create),
        )
        if (
            not result.is_success
            and ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED in (result.error or "")
        ):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "The connected SolidWorks version does not support "
                    "associated projected drawing views."
                ),
                execution_time=result.execution_time,
                metadata={
                    **(result.metadata or {}),
                    "error_code": ADAPTER_ERROR_PROJECTED_DRAWING_VIEW_UNSUPPORTED,
                },
            )
        return result

    async def create_auxiliary_drawing_view(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create an auxiliary view from a server-issued drawing edge reference."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            extension = self._model_extension(adapter)
            if extension is None:
                raise RuntimeError("Active drawing does not expose a model extension")
            edge = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing edge"
            )
            creator = getattr(adapter.currentModel, "CreateAuxiliaryViewAt", None)
            if not callable(creator):
                raise RuntimeError(
                    "Active drawing does not support CreateAuxiliaryViewAt"
                )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            try:
                if not self._select_annotation_entity(adapter, edge):
                    raise RuntimeError("SolidWorks could not select the drawing edge")
                view = creator(x_m, y_m, 0.0, 0)
                if view is None or view is False:
                    raise RuntimeError("SolidWorks did not create the auxiliary view")
                return self._drawing_view_summary(adapter, view)
            finally:
                if callable(clear_selection):
                    clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_auxiliary_drawing_view", _create),
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
        """Create a section view from a resolved view and explicit cutting-line points."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _create() -> dict[str, str]:
            source_view = self._drawing_view_by_id(adapter, source_view_id)
            creator = getattr(adapter.currentModel, "CreateSectionView5", None)
            if not callable(creator):
                creator = getattr(adapter.currentModel, "CreateSectionView", None)
            if not callable(creator):
                raise RuntimeError(
                    "Active drawing does not support section view creation"
                )
            view = None
            last_error: Exception | None = None
            for arguments in (
                (
                    source_view,
                    cut_start_x_m,
                    cut_start_y_m,
                    cut_end_x_m,
                    cut_end_y_m,
                    x_m,
                    y_m,
                ),
                (
                    cut_start_x_m,
                    cut_start_y_m,
                    cut_end_x_m,
                    cut_end_y_m,
                    x_m,
                    y_m,
                    source_view,
                ),
            ):
                try:
                    view = creator(*arguments)
                except Exception as exc:
                    last_error = exc
                    continue
                if view is not None and view is not False:
                    break
            if view is None or view is False:
                if last_error is not None:
                    raise RuntimeError(
                        f"SolidWorks rejected the section view: {last_error}"
                    ) from last_error
                raise RuntimeError("SolidWorks did not create the section view")
            return self._drawing_view_summary(adapter, view)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_section_drawing_view", _create),
        )

    async def get_sheet_metal_info(self) -> AdapterResult[dict[str, Any]]:
        """Return safe sheet-metal feature and bend-state information for a part."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation(
                "get_sheet_metal_info", lambda: self._sheet_metal_info(adapter)
            ),
        )

    async def set_sheet_metal_flat_state(
        self, flattened: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Flatten or refold an active sheet-metal part with a verified state readback."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _set() -> dict[str, Any]:
            info = self._sheet_metal_info(adapter)
            if not info["is_sheet_metal"]:
                raise RuntimeError("Active part is not a sheet-metal model")
            setter = getattr(adapter.currentModel, "SetBendState", None)
            if not callable(setter):
                raise RuntimeError(
                    "Active part does not support SetBendState for sheet metal"
                )
            requested_state = (
                _SW_BEND_STATE_FLATTENED if flattened else _SW_BEND_STATE_FOLDED
            )
            accepted = setter(requested_state)
            if accepted is False:
                raise RuntimeError(
                    "SolidWorks rejected the requested sheet-metal state"
                )
            rebuild = getattr(adapter.currentModel, "ForceRebuild3", None)
            if callable(rebuild):
                rebuild(False)
            updated = self._sheet_metal_info(adapter)
            if (
                updated["is_flattened"] is not None
                and updated["is_flattened"] is not flattened
            ):
                raise RuntimeError(
                    "SolidWorks did not apply the requested sheet-metal state"
                )
            return updated

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("set_sheet_metal_flat_state", _set),
        )

    async def insert_sheet_metal_bend_notes(self) -> AdapterResult[dict[str, Any]]:
        """Insert SolidWorks bend notes using the current flat-pattern feature."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _insert() -> dict[str, str]:
            info = self._sheet_metal_info(adapter)
            if not info["is_sheet_metal"]:
                raise RuntimeError("Active part is not a sheet-metal model")
            flat_pattern_feature, flat_pattern_name = self._flat_pattern_feature(
                adapter
            )
            creator = getattr(adapter.currentModel, "InsertBendNotes", None)
            if not callable(creator):
                raise RuntimeError("Active part does not support InsertBendNotes")
            accepted = creator(flat_pattern_feature)
            if accepted is False:
                raise RuntimeError("SolidWorks rejected bend-note insertion")
            return {"flat_pattern_feature_name": flat_pattern_name}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("insert_sheet_metal_bend_notes", _insert),
        )

    async def insert_sheet_metal_bend_table(
        self, x_m: float, y_m: float, start_value: int
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a bend table using SolidWorks' configured default template."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _insert() -> dict[str, Any]:
            info = self._sheet_metal_info(adapter)
            if not info["is_sheet_metal"]:
                raise RuntimeError("Active part is not a sheet-metal model")
            creator = getattr(adapter.currentModel, "InsertBendTable", None)
            if not callable(creator):
                raise RuntimeError("Active part does not support InsertBendTable")
            table = creator(x_m, y_m, str(start_value), "")
            if not table:
                raise RuntimeError(
                    "SolidWorks did not create a bend table using its default template"
                )
            feature = self._read_com_member(table, "GetFeature")
            display_name = _normalize_solidworks_text(
                self._read_com_member(feature, "Name")
                or self._read_com_member(table, "Name")
                or "Bend Table"
            ).strip()
            annotation_count = self._read_com_member(table, "GetTableAnnotationCount")
            return {
                "display_name": display_name or "Bend Table",
                "annotation_count": (
                    int(annotation_count)
                    if isinstance(annotation_count, (int, float))
                    and annotation_count >= 0
                    else None
                ),
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("insert_sheet_metal_bend_table", _insert),
        )

    async def export_flat_pattern_dxf(
        self, file_path: str, model_path: str, sheet_metal_options: int
    ) -> AdapterResult[dict[str, Any]]:
        """Export a saved part through ``IPartDoc.ExportToDWG2`` flat-pattern mode."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _export() -> dict[str, Any]:
            info = self._sheet_metal_info(adapter)
            if not info["is_sheet_metal"]:
                raise RuntimeError("Active part is not a sheet-metal model")
            exporter = getattr(adapter.currentModel, "ExportToDWG2", None)
            if not callable(exporter):
                raise RuntimeError("Active part does not support ExportToDWG2")
            # ExportToDWG2 requires a 12-double alignment matrix even when no
            # transform is requested. All positional units are metres.
            accepted = exporter(
                file_path,
                model_path,
                _SW_EXPORT_TO_DWG_SHEET_METAL,
                True,
                (0.0,) * 12,
                False,
                False,
                sheet_metal_options,
                None,
            )
            if accepted is False:
                raise RuntimeError("SolidWorks rejected the flat-pattern DXF export")
            return {"sheet_metal": info, "sheet_metal_options": sheet_metal_options}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("export_flat_pattern_dxf", _export),
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
        """Insert a BOM table through ``IView.InsertBomTable4``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if bom_type not in _SW_BOM_TYPES:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Unsupported drawing BOM type"
            )

        def _create() -> dict[str, Any]:
            source_view = self._drawing_view_by_id(adapter, source_view_id)
            creator = getattr(source_view, "InsertBomTable4", None)
            if not callable(creator):
                raise RuntimeError(
                    "Active drawing does not support BOM table insertion"
                )
            table = creator(
                False,
                x_m,
                y_m,
                _SW_BOM_ANCHOR_TYPE_TOP_LEFT,
                _SW_BOM_TYPES[bom_type],
                configuration_name,
                "",
                False,
                _SW_BOM_INDENTED_NUMBERING_NONE,
                detailed_cut_list,
            )
            if table is None or table is False:
                raise RuntimeError("SolidWorks did not create the requested BOM table")
            annotation = self._read_com_member(table, "GetAnnotation")
            name = _normalize_solidworks_text(
                self._read_com_member(annotation, "GetName")
                or self._read_com_member(table, "GetName")
                or "Bill of Materials"
            ).strip()
            row_count = self._read_com_member(table, "RowCount")
            column_count = self._read_com_member(table, "ColumnCount")
            extension = self._model_extension(adapter)
            if extension is None:
                raise RuntimeError("Active drawing does not expose a model extension")
            return {
                "display_name": name or "Bill of Materials",
                "bom_type": bom_type,
                "row_count": int(row_count)
                if isinstance(row_count, (int, float)) and row_count >= 0
                else None,
                "column_count": int(column_count)
                if isinstance(column_count, (int, float)) and column_count >= 0
                else None,
                "persistent_reference": self._persistent_reference_token(
                    extension, table
                ),
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_bom", _create),
        )

    async def update_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Refresh a BOM table resolved only from its workflow-issued token."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _update() -> dict[str, Any]:
            table = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing BOM"
            )
            updater = getattr(table, "UpdateTable", None)
            if not callable(updater):
                updater = getattr(table, "Update", None)
            if not callable(updater):
                raise RuntimeError("Drawing BOM table does not support updates")
            accepted = updater()
            if accepted is False:
                raise RuntimeError("SolidWorks rejected the drawing BOM update")
            annotation = self._read_com_member(table, "GetAnnotation")
            name = _normalize_solidworks_text(
                self._read_com_member(annotation, "GetName")
                or self._read_com_member(table, "GetName")
                or "Bill of Materials"
            ).strip()
            row_count = self._read_com_member(table, "RowCount")
            column_count = self._read_com_member(table, "ColumnCount")
            return {
                "display_name": name or "Bill of Materials",
                "bom_type": "parts_only",
                "row_count": int(row_count)
                if isinstance(row_count, (int, float)) and row_count >= 0
                else None,
                "column_count": int(column_count)
                if isinstance(column_count, (int, float)) and column_count >= 0
                else None,
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("update_drawing_bom", _update),
        )

    async def delete_drawing_bom(
        self, persistent_reference: str
    ) -> AdapterResult[dict[str, Any]]:
        """Delete a BOM table resolved only from its workflow-issued token."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _delete() -> dict[str, str]:
            table = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing BOM"
            )
            annotation = self._read_com_member(table, "GetAnnotation")
            name = _normalize_solidworks_text(
                self._read_com_member(annotation, "GetName")
                or self._read_com_member(table, "GetName")
                or "Bill of Materials"
            ).strip()
            deleter = getattr(table, "Delete", None)
            if not callable(deleter):
                deleter = getattr(table, "DeleteTable", None)
            if not callable(deleter):
                raise RuntimeError("Drawing BOM table does not support deletion")
            accepted = deleter()
            if accepted is False:
                raise RuntimeError("SolidWorks rejected the drawing BOM deletion")
            return {"display_name": name or "Bill of Materials"}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("delete_drawing_bom", _delete),
        )

    async def create_drawing_note(
        self, text: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Insert and position a real drawing note using ``IModelDoc2.InsertNote``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            creator = getattr(adapter.currentModel, "InsertNote", None)
            if not callable(creator):
                raise RuntimeError("Active drawing does not support InsertNote")
            note = creator(text)
            if note is None or note is False:
                raise RuntimeError(
                    "SolidWorks did not create the requested drawing note"
                )
            self._position_annotation(note, x_m, y_m)
            return self._annotation_summary(adapter, note, "note", "Drawing Note")

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_note", _create),
        )

    async def create_drawing_revision_table(
        self, x_m: float, y_m: float, anchor_type: int, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a revision table through ``IDrawingDoc.InsertRevisionTable2``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _create() -> dict[str, Any]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            creator = getattr(adapter.currentModel, "InsertRevisionTable2", None)
            if not callable(creator):
                raise RuntimeError("Active drawing does not support InsertRevisionTable2")
            table = creator(True, x_m, y_m, anchor_type, "", 0, True)
            if table is None or table is False:
                raise RuntimeError("SolidWorks did not create the revision table")
            add_revision = getattr(table, "AddRevision", None)
            if not callable(add_revision):
                raise RuntimeError("SolidWorks revision table does not support AddRevision")
            revision_id = add_revision(revision)
            current_revision = self._read_com_member(table, "CurrentRevision")
            extension = self._model_extension(adapter)
            try:
                persistent_reference = self._persistent_reference_token(extension, table)
            except RuntimeError:
                # Older/late-bound test doubles and some legacy SW builds do
                # not expose persistent references for annotation tables.
                persistent_reference = ""
            result = {
                "display_name": "Revision Table",
                "revision": revision,
                "revision_id": int(revision_id)
                if isinstance(revision_id, (int, float))
                else None,
                "current_revision": _normalize_solidworks_text(current_revision).strip()
                if current_revision is not None
                else revision,
            }
            if persistent_reference:
                result["persistent_reference"] = persistent_reference
            return result

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_revision_table", _create),
        )

    def _resolve_revision_table(self, persistent_reference: str) -> Any:
        """Resolve a server-owned revision table persistent reference."""
        adapter = self._adapter(self)
        table = self._resolve_persistent_reference(
            adapter, persistent_reference, description="Drawing revision table"
        )
        if table is None or not callable(getattr(table, "GetRevisionForId", None)):
            raise RuntimeError("Persistent reference is not a revision table")
        return table

    async def list_drawing_revisions(
        self, persistent_reference: str
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Enumerate revision rows and symbol counts from one revision table."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _list() -> list[dict[str, Any]]:
            table = self._resolve_revision_table(persistent_reference)
            rows: list[dict[str, Any]] = []
            get_row_id = getattr(table, "GetIdForRowNumber", None)
            get_revision = getattr(table, "GetRevisionForId", None)
            get_symbol_count = getattr(table, "GetRevisionSymbolCount", None)
            if not callable(get_row_id) or not callable(get_revision):
                raise RuntimeError("SolidWorks revision table does not support row enumeration")
            for row_number in range(0, 1024):
                try:
                    revision_id = get_row_id(row_number)
                    revision = get_revision(int(revision_id))
                except Exception:
                    if row_number == 0:
                        # Some versions number the header at row 0 and data at 1.
                        continue
                    break
                if revision is None:
                    break
                symbols = 0
                if callable(get_symbol_count):
                    try:
                        symbols = int(get_symbol_count(int(revision_id)))
                    except Exception:
                        symbols = 0
                rows.append(
                    {
                        "row_number": row_number,
                        "revision_id": int(revision_id),
                        "revision": _normalize_solidworks_text(revision).strip(),
                        "symbol_count": symbols,
                    }
                )
            return rows

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_drawing_revisions", _list),
        )

    async def add_drawing_revision(
        self, persistent_reference: str, revision: str
    ) -> AdapterResult[dict[str, Any]]:
        """Add one revision row and return its native revision ID."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _add() -> dict[str, Any]:
            table = self._resolve_revision_table(persistent_reference)
            add_revision = getattr(table, "AddRevision", None)
            if not callable(add_revision):
                raise RuntimeError("SolidWorks revision table does not support AddRevision")
            revision_id = add_revision(revision)
            current_revision = self._read_com_member(table, "CurrentRevision")
            return {
                "revision_id": int(revision_id) if isinstance(revision_id, (int, float)) else None,
                "revision": revision,
                "current_revision": _normalize_solidworks_text(current_revision).strip()
                if current_revision is not None
                else revision,
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("add_drawing_revision", _add),
        )

    async def delete_drawing_revision(
        self, persistent_reference: str, revision_id: int, delete_symbols: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Delete one revision row by native revision ID."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _delete() -> dict[str, Any]:
            table = self._resolve_revision_table(persistent_reference)
            delete_revision = getattr(table, "DeleteRevision", None)
            if not callable(delete_revision):
                raise RuntimeError("SolidWorks revision table does not support DeleteRevision")
            result = delete_revision(revision_id, delete_symbols)
            if result is False:
                raise RuntimeError("SolidWorks failed to delete the revision row")
            return {"revision_id": revision_id, "deleted": True}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("delete_drawing_revision", _delete),
        )

    async def list_drawing_annotation_targets(
        self, view_id: str, target_types: tuple[str, ...]
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List internal persistent references for real drawing annotation targets."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        requested_types = set(target_types)

        def _list() -> list[dict[str, str]]:
            view = self._drawing_view_by_id(adapter, view_id)
            extension = self._model_extension(adapter)
            if extension is None:
                raise RuntimeError("Active drawing does not expose a model extension")
            targets: list[dict[str, str]] = []

            def _append(target_type: str, display_name: str, entity: Any) -> None:
                if len(targets) >= _MAX_DRAWING_ANNOTATION_TARGETS:
                    return
                try:
                    token = self._persistent_reference_token(extension, entity)
                except RuntimeError:
                    return
                targets.append(
                    {
                        "view_id": view_id,
                        "target_type": target_type,
                        "display_name": display_name,
                        "persistent_reference": token,
                    }
                )

            if "dimension" in requested_types:
                first_dimension = self._read_com_member(
                    view, "GetFirstDisplayDimension5"
                )
                if first_dimension is None:
                    first_dimension = self._read_com_member(
                        view, "GetFirstDisplayDimension"
                    )
                next_member = (
                    "GetNext5"
                    if callable(getattr(first_dimension, "GetNext5", None))
                    else "GetNext"
                )
                for position, dimension in enumerate(
                    self._linked_entities(first_dimension, next_member), start=1
                ):
                    _append("dimension", f"{view_id} Dimension {position}", dimension)

            raw_components = self._read_com_member(view, "GetVisibleComponents")
            components = self._as_com_collection(raw_components)
            if "component" in requested_types:
                for position, component in enumerate(components, start=1):
                    display_name = _normalize_solidworks_text(
                        self._read_com_member(component, "Name2")
                        or self._read_com_member(component, "Name")
                        or f"{view_id} Component {position}"
                    ).strip()
                    _append(
                        "component",
                        display_name or f"{view_id} Component {position}",
                        component,
                    )

            if "edge" in requested_types:
                get_visible_entities = getattr(view, "GetVisibleEntities2", None)
                if callable(get_visible_entities):
                    for component in components or [None]:
                        raw_edges = get_visible_entities(component, 1)
                        for position, edge in enumerate(
                            self._as_com_collection(raw_edges), start=1
                        ):
                            _append("edge", f"{view_id} Edge {position}", edge)
            return targets

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_drawing_annotation_targets", _list),
        )

    async def place_drawing_dimension(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Place an existing display dimension at a controlled drawing-sheet position."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _place() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            dimension = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing dimension"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "AddDimension2", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError(
                    "Active drawing does not support dimension placement"
                )
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, dimension):
                    raise RuntimeError(
                        "SolidWorks could not select the drawing dimension"
                    )
                placed_dimension = creator(x_m, y_m, 0.0)
                if placed_dimension is None or placed_dimension is False:
                    raise RuntimeError("SolidWorks did not place the drawing dimension")
                return self._annotation_summary(
                    adapter, placed_dimension, "dimension", "Drawing Dimension"
                )
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("place_drawing_dimension", _place),
        )

    async def create_drawing_balloon(
        self,
        persistent_reference: str,
        x_m: float,
        y_m: float,
        style: str,
        text: str,
    ) -> AdapterResult[dict[str, Any]]:
        """Create a positioned BOM balloon from a visible component reference."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if style not in _SW_BALLOON_STYLES:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Unsupported balloon style"
            )

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            component = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing component"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "InsertBOMBalloon2", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError("Active drawing does not support BOM balloons")
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, component):
                    raise RuntimeError(
                        "SolidWorks could not select the drawing component"
                    )
                balloon = creator(
                    _SW_BALLOON_STYLES[style],
                    _SW_BALLOON_FIT_TIGHTEST,
                    _SW_BALLOON_TEXT_CUSTOM,
                    text,
                    _SW_BALLOON_TEXT_CUSTOM,
                    "",
                )
                if balloon is None or balloon is False:
                    raise RuntimeError(
                        "SolidWorks did not create the requested BOM balloon"
                    )
                self._position_annotation(balloon, x_m, y_m)
                return self._annotation_summary(
                    adapter, balloon, "balloon", "BOM Balloon"
                )
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_balloon", _create),
        )

    async def create_drawing_hole_callout(
        self, persistent_reference: str, x_m: float, y_m: float
    ) -> AdapterResult[dict[str, Any]]:
        """Create a positioned automatic hole callout from a visible drawing edge."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            edge = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing edge"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "AddHoleCallout2", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError(
                    "Active drawing does not support automatic hole callouts"
                )
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, edge):
                    raise RuntimeError(
                        "SolidWorks could not select the drawing hole edge"
                    )
                callout = creator(x_m, y_m, 0.0)
                if callout is None or callout is False:
                    raise RuntimeError(
                        "SolidWorks did not create the requested hole callout"
                    )
                return self._annotation_summary(
                    adapter, callout, "hole_callout", "Hole Callout"
                )
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_hole_callout", _create),
        )

    async def create_drawing_ordinate_dimension(
        self, persistent_reference: str, x_m: float, y_m: float, ordinate_type: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create an ordinate dimension from a selected display dimension."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
        dimension_type = _SW_ORDINATE_DIMENSION_TYPES.get(ordinate_type)
        if dimension_type is None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Unsupported ordinate dimension type")

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            dimension = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing dimension"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "AddOrdinateDimension2", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError("Active drawing does not support ordinate dimensions")
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, dimension):
                    raise RuntimeError("SolidWorks could not select the drawing dimension")
                annotation = creator(dimension_type, x_m, y_m, 0.0)
                if annotation is None or annotation is False:
                    raise RuntimeError("SolidWorks did not create the ordinate dimension")
                return self._annotation_summary(
                    adapter, annotation, "ordinate_dimension", "Ordinate Dimension"
                )
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_ordinate_dimension", _create),
        )

    async def create_drawing_surface_finish(
        self, persistent_reference: str, x_m: float, y_m: float, symbol: str, value: str
    ) -> AdapterResult[dict[str, Any]]:
        """Insert an ISO surface-finish symbol attached to a drawing edge."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
        symbol_type = _SW_SURFACE_FINISH_SYMBOLS.get(symbol)
        if symbol_type is None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="Unsupported surface finish symbol")

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            edge = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing edge"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "InsertSurfaceFinishSymbol3", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError("Active drawing does not support surface-finish symbols")
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, edge):
                    raise RuntimeError("SolidWorks could not select the drawing edge")
                # SW 2019's InsertSurfaceFinishSymbol3 takes the symbol and
                # leader styles first, then its position and roughness fields.
                # Keep optional surface-finish text empty and use ``value`` as
                # the maximum roughness field, which is the common 1.6/3.2 callout.
                annotation = creator(
                    symbol_type,
                    0,
                    x_m,
                    y_m,
                    0.0,
                    _SW_SURFACE_FINISH_LAY_NONE,
                    0,
                    "",
                    "",
                    "",
                    "",
                    value,
                    "",
                    "",
                )
                if annotation is None or annotation is False:
                    raise RuntimeError("SolidWorks did not create the surface-finish symbol")
                self._position_annotation(annotation, x_m, y_m)
                return self._annotation_summary(
                    adapter, annotation, "surface_finish", "Surface Finish"
                )
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_surface_finish", _create),
        )

    async def create_drawing_weld_symbol(
        self, persistent_reference: str, x_m: float, y_m: float, weld_type: str, size: str
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a weld symbol attached to a drawing edge."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            edge = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing edge"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "InsertWeldSymbol3", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError("Active drawing does not support weld symbols")
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, edge):
                    raise RuntimeError("SolidWorks could not select the drawing edge")
                annotation = creator()
                if annotation is None or annotation is False:
                    raise RuntimeError("SolidWorks did not create the weld symbol")
                set_symbol = getattr(annotation, "SetWeldSymbol", None)
                if not callable(set_symbol) or set_symbol(True, weld_type, size, "", "", "") is False:
                    raise RuntimeError("SolidWorks could not configure the weld symbol")
                self._position_annotation(annotation, x_m, y_m)
                return self._annotation_summary(adapter, annotation, "weld_symbol", "Weld Symbol")
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_weld_symbol", _create),
        )

    async def create_drawing_geometric_tolerance(
        self, persistent_reference: str, x_m: float, y_m: float, tolerance: str, datum: str
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a geometric tolerance frame attached to a drawing dimension."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            dimension = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing dimension"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "InsertGtol", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError("Active drawing does not support geometric tolerances")
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, dimension):
                    raise RuntimeError("SolidWorks could not select the drawing dimension")
                annotation = creator()
                if annotation is None or annotation is False:
                    raise RuntimeError("SolidWorks did not create the geometric tolerance")
                set_values = getattr(annotation, "SetFrameValues", None)
                if not callable(set_values) or set_values(1, tolerance, "", datum, "", "") is False:
                    raise RuntimeError("SolidWorks could not configure the geometric tolerance")
                self._position_annotation(annotation, x_m, y_m)
                return self._annotation_summary(
                    adapter, annotation, "geometric_tolerance", "Geometric Tolerance"
                )
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_geometric_tolerance", _create),
        )

    async def create_drawing_center_mark(
        self, persistent_reference: str, size_m: float, show_connection_lines: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Create a center mark from one selected circular drawing edge."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _create() -> dict[str, str]:
            if self._read_com_member(adapter.currentModel, "GetType") != 3:
                raise RuntimeError("Active model is not a drawing")
            edge = self._resolve_persistent_reference(
                adapter, persistent_reference, description="Drawing edge"
            )
            clear_selection = getattr(adapter.currentModel, "ClearSelection2", None)
            creator = getattr(adapter.currentModel, "AddCenterMark", None)
            if not callable(clear_selection) or not callable(creator):
                raise RuntimeError("Active drawing does not support center marks")
            clear_selection(True)
            try:
                if not self._select_annotation_entity(adapter, edge):
                    raise RuntimeError("SolidWorks could not select the drawing edge")
                accepted = creator(size_m, show_connection_lines)
                if accepted is False or accepted is None:
                    raise RuntimeError("SolidWorks did not create the center mark")
                return {"annotation_type": "center_mark", "display_name": "Center Mark"}
            finally:
                clear_selection(True)

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("create_drawing_center_mark", _create),
        )

    async def list_custom_properties(
        self, configuration_name: str | None = None
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List raw and resolved custom properties through ``GetNames``/``Get6``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, str]]:
            manager = self._custom_property_manager(adapter, configuration_name)
            return [
                {
                    "name": name,
                    "value": value,
                    "resolved_value": resolved_value,
                }
                for name in self._custom_property_names(manager)
                for value, resolved_value in [
                    self._custom_property_value(manager, name)
                ]
            ]

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_custom_properties", _list),
        )

    async def set_custom_property(
        self, name: str, value: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Create or replace one text custom property through ``Add3``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _set() -> dict[str, str]:
            manager = self._custom_property_manager(adapter, configuration_name)
            add3 = getattr(manager, "Add3", None)
            if not callable(add3):
                raise RuntimeError("CustomPropertyManager does not support Add3")
            result = add3(
                name,
                _SW_CUSTOM_INFO_TEXT,
                value,
                _SW_CUSTOM_PROPERTY_REPLACE_VALUE,
            )
            if isinstance(result, bool):
                if not result:
                    raise RuntimeError("SolidWorks rejected the custom property update")
            elif isinstance(result, int) and result != 0:
                raise RuntimeError("SolidWorks rejected the custom property update")
            resolved_value = value
            try:
                _, resolved_value = self._custom_property_value(manager, name)
            except RuntimeError:
                pass
            return {
                "name": name,
                "value": value,
                "resolved_value": resolved_value,
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("set_custom_property", _set),
        )

    async def delete_custom_property(
        self, name: str, configuration_name: str | None = None
    ) -> AdapterResult[dict[str, str]]:
        """Delete and verify one custom property through ``Delete2``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _delete() -> dict[str, str]:
            manager = self._custom_property_manager(adapter, configuration_name)
            deleter = getattr(manager, "Delete2", None)
            if not callable(deleter):
                raise RuntimeError("CustomPropertyManager does not support Delete2")
            result = deleter(name)
            if isinstance(result, bool):
                if not result:
                    raise RuntimeError(
                        "SolidWorks rejected the custom property deletion"
                    )
            elif isinstance(result, int) and result != 0:
                raise RuntimeError("SolidWorks rejected the custom property deletion")
            if any(
                existing_name.casefold() == name.casefold()
                for existing_name in self._custom_property_names(manager)
            ):
                raise RuntimeError("SolidWorks did not remove the custom property")
            return {"name": name}

        return cast(
            AdapterResult[dict[str, str]],
            adapter._handle_com_operation("delete_custom_property", _delete),
        )

    async def list_dimensions(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-model driving dimensions with native names kept server-side.

        The primary path probes each feature's ``Parameter('D<n>')`` accessor.
        Unlike display dimensions, this works for saved models even when their
        dimensions are not currently visible in the SolidWorks UI. Display
        dimensions remain a compatibility fallback for older feature types.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _member(obj: Any, name: str) -> Any:
            return self._read_com_member(obj, name)

        def _dimension_name(dimension: Any, display_dimension: Any) -> str:
            for obj, names in (
                (dimension, ("GetNameForSelection", "Name")),
                (display_dimension, ("GetNameForSelection", "Name")),
            ):
                if obj is None:
                    continue
                for member_name in names:
                    value = adapter._attempt(
                        lambda target=obj, member=member_name: _member(target, member),
                        default=None,
                    )
                    normalized = _normalize_solidworks_text(value).strip()
                    if normalized and "@" in normalized:
                        return normalized
            return ""

        def _dimension_value_mm(dimension: Any) -> float | None:
            value = adapter._attempt(
                lambda: _member(dimension, "SystemValue"), default=None
            )
            if value is None:
                getter = adapter._attempt(
                    lambda: getattr(dimension, "GetSystemValue3", None),
                    default=None,
                )
                value = adapter._attempt(
                    lambda: getter(0, None) if callable(getter) else None,
                    default=None,
                )
            if value is None:
                getter = adapter._attempt(
                    lambda: getattr(dimension, "GetValue3", None), default=None
                )
                value = adapter._attempt(
                    lambda: getter(0, 0) if callable(getter) else None,
                    default=None,
                )
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            try:
                value_mm = float(value) * 1000.0
            except (TypeError, ValueError):
                return None
            return value_mm if isfinite(value_mm) else None

        def _list() -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()

            def _append_dimension(dimension: Any, display_dimension: Any = None) -> None:
                if dimension is None:
                    return
                _sw_type_info.flag_methods(dimension, "IDimension")
                native_name = _dimension_name(dimension, display_dimension)
                value_mm = _dimension_value_mm(dimension)
                if native_name and value_mm is not None and native_name not in seen:
                    seen.add(native_name)
                    rows.append({"name": native_name, "value_mm": value_mm})

            def _display_dimensions(feature: Any) -> None:
                """Use the legacy display-dimension collection as a fallback."""
                display_dimension = None
                for member_name in (
                    "GetFirstDisplayDimension",
                    "GetFirstDisplayDimension2",
                ):
                    display_dimension = adapter._attempt(
                        lambda target=feature, member=member_name: _member(
                            target, member
                        ),
                        default=None,
                    )
                    if display_dimension is not None:
                        break

                dimension_guard = 0
                while display_dimension is not None and dimension_guard < 1_000:
                    dimension_guard += 1
                    _sw_type_info.flag_methods(display_dimension, "IDisplayDimension")
                    dimension = adapter._attempt(
                        lambda current=display_dimension: _member(
                            current, "GetDimension"
                        ),
                        default=None,
                    )
                    _append_dimension(dimension, display_dimension)
                    display_dimension = adapter._attempt(
                        lambda current=display_dimension: _member(
                            current, "GetNext2"
                        ),
                        default=None,
                    )

            def _feature_type(feature: Any) -> str:
                raw_type = adapter._attempt(
                    lambda current=feature: _member(current, "GetTypeName2"),
                    default=None,
                )
                return _normalize_solidworks_text(raw_type or "").strip().casefold()

            def _process_feature(feature: Any) -> None:
                """Append all usable driving dimensions owned by one feature."""
                _sw_type_info.flag_methods(feature, "IFeature")
                feature_type = _feature_type(feature)
                if feature_type.endswith("folder") or feature_type == "detailcabinet":
                    return

                found_parameter = False
                empty_parameter_gap = 0
                for index in range(1, _MAX_FEATURE_PARAMETER_INDEX + 1):
                    dimension = adapter._attempt(
                        lambda position=index, current=feature: current.Parameter(
                            f"D{position}"
                        ),
                        default=None,
                    )
                    if dimension is None:
                        if not found_parameter:
                            break
                        empty_parameter_gap += 1
                        if empty_parameter_gap >= _MAX_EMPTY_FEATURE_PARAMETER_GAP:
                            break
                        continue
                    found_parameter = True
                    empty_parameter_gap = 0
                    _append_dimension(dimension)

                if not found_parameter:
                    _display_dimensions(feature)

            _sw_type_info.flag_methods(adapter.currentModel, "IModelDoc2")
            feature = adapter._attempt(
                lambda: _member(adapter.currentModel, "FirstFeature"), default=None
            )
            feature_guard = 0

            while feature is not None and feature_guard < 10_000:
                feature_guard += 1
                _process_feature(feature)
                feature = adapter._attempt(
                    lambda current=feature: _member(current, "GetNextFeature"),
                    default=None,
                )

            if feature_guard == 0:
                # ``FirstFeature`` is unavailable through IDispatch on a few
                # SolidWorks releases. The positional accessor exposes the
                # same features in reverse order and is reliable there.
                for index in range(10_000):
                    feature = adapter._attempt(
                        lambda position=index: adapter.currentModel.FeatureByPositionReverse(
                            position
                        ),
                        default=None,
                    )
                    if feature is None:
                        break
                    _process_feature(feature)
            return rows

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_dimensions", _list),
        )

    async def get_dimension(self, name: str) -> AdapterResult[float]:
        """Read a named model dimension in millimetres.

        Args:
            name: Fully-qualified dimension name.

        Returns:
            AdapterResult[float]: Dimension value in millimetres.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _get() -> float:  # pragma: no cover
            """Get the dimension value."""
            dimension = adapter.currentModel.Parameter(name)
            if not dimension:
                raise Exception(f"Dimension '{name}' not found")
            # SystemValue is reliable on SW 2025 (in meters, convert to mm)
            value = adapter._attempt(lambda: dimension.SystemValue, default=None)
            if value is None:
                # Fall back to GetValue3 for older SW versions
                value = adapter._attempt(
                    lambda: dimension.GetValue3(0, 0), default=None
                )
            if value is None:
                raise Exception(f"Failed to read dimension '{name}'")
            return float(value) * 1000

        return cast(
            AdapterResult[float],
            adapter._handle_com_operation("get_dimension", _get),
        )

    async def set_dimension(self, name: str, value: float) -> AdapterResult[None]:
        """Set a named model dimension in millimetres and rebuild.

        Args:
            name: Fully-qualified dimension name.
            value: New value in millimetres.

        Returns:
            AdapterResult[None]: Result of the set operation.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _set() -> None:
            """Set the dimension value."""
            dimension = adapter.currentModel.Parameter(name)
            if not dimension:
                raise Exception(f"Dimension '{name}' not found")

            # SetValue3 has gen_py parameter mapping issues on SW 2025.
            # SystemValue (in meters) is reliable.
            value_m = value / 1000.0
            adapter._attempt(
                lambda: setattr(dimension, "SystemValue", value_m),
                default=None,
            )

            # Rebuild: try EditRebuild3 first, fall back to ForceRebuild3
            rebuilt = adapter._attempt(
                lambda: adapter.currentModel.EditRebuild3(), default=None
            )
            if rebuilt is None:
                rebuilt = adapter._attempt(
                    lambda: adapter.currentModel.ForceRebuild3(True), default=None
                )
            if rebuilt is None:
                raise Exception("Failed to set dimension")

        return cast(
            AdapterResult[None],
            adapter._handle_com_operation("set_dimension", _set),
        )

    async def save_file(self, file_path: str | None = None) -> AdapterResult[None]:
        """Save the active model to its current path or to a new file path.

        Args:
            file_path: Optional target path for Save As.

        Returns:
            AdapterResult[None]: Result of the save operation.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _save() -> None:
            """Save the model."""
            if file_path:
                resolved_path = os.path.abspath(file_path)
                os.makedirs(os.path.dirname(resolved_path), exist_ok=True)

                if adapter.swApp:
                    adapter._attempt(lambda: adapter.swApp.CloseDoc(resolved_path))

                if os.path.exists(resolved_path):
                    adapter._attempt(lambda: os.remove(resolved_path))

                save_as3_result = adapter.currentModel.SaveAs3(resolved_path, 0, 0)
                if not self._is_success(save_as3_result):
                    save_as = getattr(adapter.currentModel, "SaveAs", None)
                    if callable(save_as):
                        fallback_result = save_as(resolved_path)
                        if not self._is_success(fallback_result):
                            raise Exception(f"Failed to save as: {resolved_path}")
                    else:
                        raise Exception(f"Failed to save as: {resolved_path}")

                if not os.path.exists(resolved_path):
                    raise Exception(f"File not written after save: {resolved_path}")
                return

            save_result = adapter._attempt(
                lambda: adapter.currentModel.Save3(1, None, None)
            )
            if save_result is None:
                save_fn = getattr(adapter.currentModel, "Save", None)
                if callable(save_fn):
                    save_result = save_fn()
                else:
                    raise Exception("Failed to save file")

            if self._is_success(save_result):
                return

            path_attr = getattr(adapter.currentModel, "GetPathName", "")
            model_path = path_attr() if callable(path_attr) else path_attr
            if model_path and os.path.exists(model_path):
                return
            raise Exception("Failed to save file")

        return cast(
            AdapterResult[None],
            adapter._handle_com_operation("save_file", _save),
        )

    async def rebuild_model(self) -> AdapterResult[None]:
        """Force a model rebuild.

        Returns:
            AdapterResult[None]: Result of the rebuild operation.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _rebuild() -> None:
            """Rebuild the model."""
            success = adapter.currentModel.ForceRebuild3(False)
            if not success:
                raise Exception("Failed to rebuild model")

        return cast(
            AdapterResult[None],
            adapter._handle_com_operation("rebuild_model", _rebuild),
        )

    async def get_model_info(self) -> AdapterResult[dict[str, Any]]:
        """Collect summary metadata about the active model.

        Returns:
            AdapterResult[dict[str, Any]]: Model information payload.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _get_info() -> dict[str, Any]:
            """Get model information with per-field COM compatibility fallback."""
            active_config = adapter._attempt(
                lambda: adapter._get_attr_or_call(
                    adapter.currentModel, "GetActiveConfiguration"
                )
            )
            # 'Name' on Configuration is a property, not a method.
            config_name = adapter._attempt(
                lambda: adapter._get_attr_or_call(active_config, "Name"),
                default="Default",
            )
            if not config_name:
                config_name = "Default"
            # Try GetSaveFlag (method) first, fallback to property
            is_dirty_raw = adapter._attempt(
                lambda: adapter._get_attr_or_call(adapter.currentModel, "GetSaveFlag"),
                default=None,
            )
            is_dirty = bool(is_dirty_raw) if is_dirty_raw is not None else None
            feature_count = adapter._attempt(
                lambda: int(
                    adapter.currentModel.FeatureManager.GetFeatureCount(True) or 0
                ),
                default=0,
            )
            rebuild_status_raw = adapter._attempt(
                lambda: adapter._get_attr_or_call(
                    adapter.currentModel, "GetRebuildStatus"
                ),
                default=None,
            )
            # GetRebuildStatus returns 0=ok, 1=needs rebuild, or None=failed
            rebuild_status = (
                rebuild_status_raw if rebuild_status_raw is not None else None
            )
            return {
                "title": adapter._attempt(
                    lambda: adapter._get_attr_or_call(adapter.currentModel, "GetTitle"),
                    default="Untitled",
                ),
                "path": adapter._attempt(
                    lambda: adapter._get_attr_or_call(
                        adapter.currentModel, "GetPathName"
                    ),
                    default="",
                ),
                "type": adapter._attempt(adapter._get_document_type, default="Unknown"),
                "configuration": str(config_name),
                "is_dirty": is_dirty,
                "feature_count": feature_count,
                "rebuild_status": rebuild_status,
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("get_model_info", _get_info),
        )

    async def list_open_models(self) -> AdapterResult[list[dict[str, Any]]]:
        """List documents open in the connected SolidWorks application."""
        adapter = self._adapter(self)
        if not adapter.swApp:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="SolidWorks is not connected"
            )

        def _list() -> list[dict[str, Any]]:
            documents = adapter._get_attr_or_call(adapter.swApp, "GetDocuments")
            if documents is None:
                return []
            if not isinstance(documents, (tuple, list)):
                documents = [documents]
            active_document = adapter._attempt(
                lambda: adapter._get_attr_or_call(adapter.swApp, "ActiveDoc"),
                default=None,
            )
            document_types = {1: "Part", 2: "Assembly", 3: "Drawing"}
            summaries: list[dict[str, Any]] = []
            for document in documents:
                if document is None:
                    continue
                raw_type = adapter._attempt(
                    lambda current=document: adapter._get_attr_or_call(
                        current, "GetType"
                    ),
                    default=0,
                )
                active_config = adapter._attempt(
                    lambda current=document: adapter._get_attr_or_call(
                        current, "GetActiveConfiguration"
                    ),
                    default=None,
                )
                config_name = adapter._attempt(
                    lambda: adapter._get_attr_or_call(active_config, "Name"),
                    default=None,
                )
                summaries.append(
                    {
                        "title": _normalize_solidworks_text(
                            adapter._attempt(
                                lambda current=document: adapter._get_attr_or_call(
                                    current, "GetTitle"
                                ),
                                default="Untitled",
                            )
                        ),
                        "path": str(
                            adapter._attempt(
                                lambda current=document: adapter._get_attr_or_call(
                                    current, "GetPathName"
                                ),
                                default="",
                            )
                            or ""
                        ),
                        "type": document_types.get(int(raw_type or 0), "Unknown"),
                        "configuration": _normalize_solidworks_text(config_name)
                        if config_name
                        else None,
                        "is_active": document is active_document,
                    }
                )
            return summaries

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_open_models", _list),
        )

    async def list_configurations(self) -> AdapterResult[list[str]]:
        """List all configuration names on the active model.

        Returns:
            AdapterResult[list[str]]: Configuration names, or empty list when unavailable.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        def _list() -> list[str]:
            """List configurations."""
            raw_names = getattr(adapter.currentModel, "GetConfigurationNames", None)
            names = raw_names() if callable(raw_names) else raw_names
            if names is None:
                names = []
            if isinstance(names, str):
                return [names]

            normalized_names = [_normalize_solidworks_text(name) for name in names]
            if normalized_names:
                return normalized_names

            active_config = adapter._attempt(
                lambda: adapter.currentModel.GetActiveConfiguration(), default=None
            )
            active_name = adapter._attempt(lambda: active_config.Name, default=None)
            if not active_name:
                active_name = adapter._attempt(
                    lambda: active_config.GetName(), default=None
                )
            if active_name:
                return [_normalize_solidworks_text(active_name)]
            return []

        return cast(
            AdapterResult[list[str]],
            adapter._handle_com_operation("list_configurations", _list),
        )

    async def get_configuration(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Read one configuration's native name and comment without activating it."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

        def _get() -> dict[str, Any]:
            getter = getattr(adapter.currentModel, "GetConfigurationByName", None)
            if not callable(getter):
                raise RuntimeError("Active document does not support GetConfigurationByName")
            configuration = getter(configuration_name)
            if configuration is None:
                raise RuntimeError("Configuration does not exist")
            name = _normalize_solidworks_text(
                self._read_com_member(configuration, "Name")
                or self._read_com_member(configuration, "GetName")
            ).strip()
            if not name or name.casefold() != configuration_name.casefold():
                raise RuntimeError("SolidWorks returned a different configuration")
            comment = _normalize_solidworks_text(
                self._read_com_member(configuration, "Comment")
            ).strip()
            active_configuration = self._read_com_member(
                adapter.currentModel, "GetActiveConfiguration"
            )
            active_name = _normalize_solidworks_text(
                self._read_com_member(active_configuration, "Name")
                or self._read_com_member(active_configuration, "GetName")
            ).strip()
            return {
                "name": name,
                "description": comment[:1024],
                "is_active": name.casefold() == active_name.casefold(),
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("get_configuration", _get),
        )

    async def list_solid_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List safe summaries from ``IPartDoc.GetBodies2(swSolidBody, ...)``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, Any]]:
            self._require_part_document(adapter)
            get_bodies = getattr(adapter.currentModel, "GetBodies2", None)
            if not callable(get_bodies):
                raise RuntimeError("Active part does not support GetBodies2")
            raw_bodies = get_bodies(0, not include_hidden)
            if raw_bodies is None:
                return []
            bodies = (
                list(raw_bodies)
                if isinstance(raw_bodies, (tuple, list))
                else [raw_bodies]
            )
            summaries: list[dict[str, Any]] = []
            for position, body in enumerate(bodies, start=1):
                if body is None:
                    continue
                adapter._attempt(
                    lambda current_body=body: _sw_type_info.flag_methods(
                        current_body, "IBody2"
                    ),
                    default=None,
                )
                name = _normalize_solidworks_text(
                    self._read_com_member(body, "Name") or f"Solid Body {position}"
                ).strip()
                visible = self._read_com_member(body, "Visible")
                summaries.append(
                    {
                        "body_id": f"body_{position}",
                        "name": name or f"Solid Body {position}",
                        "visible": bool(visible)
                        if visible is not None
                        else not include_hidden,
                    }
                )
            return summaries

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_solid_bodies", _list),
        )

    async def list_surface_bodies(
        self, include_hidden: bool = False
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List safe summaries from ``IPartDoc.GetBodies2(swSheetBody, ...)``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, Any]]:
            self._require_part_document(adapter)
            get_bodies = getattr(adapter.currentModel, "GetBodies2", None)
            if not callable(get_bodies):
                raise RuntimeError("Active part does not support GetBodies2")
            # swSheetBody is 1. SolidWorks calls these "sheet bodies" in the
            # API; they are the surface bodies exposed to design workflows.
            raw_bodies = get_bodies(1, not include_hidden)
            if raw_bodies is None:
                return []
            bodies = (
                list(raw_bodies)
                if isinstance(raw_bodies, (tuple, list))
                else [raw_bodies]
            )
            summaries: list[dict[str, Any]] = []
            for position, body in enumerate(bodies, start=1):
                if body is None:
                    continue
                adapter._attempt(
                    lambda current_body=body: _sw_type_info.flag_methods(
                        current_body, "IBody2"
                    ),
                    default=None,
                )
                name = _normalize_solidworks_text(
                    self._read_com_member(body, "Name") or f"Surface Body {position}"
                ).strip()
                visible = self._read_com_member(body, "Visible")
                summaries.append(
                    {
                        "body_id": f"surface_body_{position}",
                        "name": name or f"Surface Body {position}",
                        "visible": bool(visible)
                        if visible is not None
                        else not include_hidden,
                    }
                )
            return summaries

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_surface_bodies", _list),
        )

    async def list_part_topology(
        self, entity_types: tuple[str, ...] = ("face", "edge")
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List active-part face and edge tokens for feature and sketch operations."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _finite_scalar(value: Any) -> float | None:
            """Normalize one finite SolidWorks scalar without leaking COM values."""
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if isfinite(number) else None

        def _vector(value: Any, *, scale: float = 1.0) -> list[float] | None:
            """Return one finite three-coordinate vector from a COM array."""
            if not isinstance(value, (list, tuple)) or len(value) < 3:
                return None
            coordinates = [_finite_scalar(item) for item in value[:3]]
            if any(item is None for item in coordinates):
                return None
            finite_coordinates = cast(list[float], coordinates)
            return [item * scale for item in finite_coordinates]

        def _face_geometry(face: Any) -> dict[str, Any]:
            geometry: dict[str, Any] = {}
            area_m2 = _finite_scalar(self._read_com_member(face, "GetArea"))
            if area_m2 is not None and area_m2 >= 0:
                geometry["area_mm2"] = area_m2 * 1_000_000
            normal = _vector(self._read_com_member(face, "Normal"))
            if normal is not None:
                geometry["normal"] = normal
            return geometry

        def _edge_geometry(edge: Any) -> dict[str, Any]:
            geometry: dict[str, Any] = {}
            length_m = _finite_scalar(self._read_com_member(edge, "GetLength"))
            if length_m is not None and length_m >= 0:
                geometry["length_mm"] = length_m * 1000
            curve = self._read_com_member(edge, "GetCurve")
            identity = _finite_scalar(self._read_com_member(curve, "Identity"))
            if identity is not None and identity.is_integer():
                geometry["curve_identity"] = int(identity)
            return geometry

        def _vertex_geometry(vertex: Any) -> dict[str, Any]:
            point_mm = _vector(self._read_com_member(vertex, "GetPoint"), scale=1000)
            return {"point_mm": point_mm} if point_mm is not None else {}

        def _list() -> list[dict[str, Any]]:
            self._require_part_document(adapter)
            extension = self._model_extension(adapter)
            if extension is None:
                raise RuntimeError("Active part does not expose a model extension")
            get_bodies = getattr(adapter.currentModel, "GetBodies2", None)
            if not callable(get_bodies):
                raise RuntimeError("Active part does not support GetBodies2")
            rows: list[dict[str, Any]] = []
            for body_position, body in enumerate(
                self._as_com_collection(get_bodies(0, False)), start=1
            ):
                if body is None:
                    continue
                if "face" in entity_types:
                    first_face = self._read_com_member(body, "GetFirstFace")
                    for position, face in enumerate(
                        self._linked_entities(first_face, "GetNextFace"), start=1
                    ):
                        rows.append(
                            {
                                "entity_type": "face",
                                "display_name": f"Body {body_position} Face {position}",
                                "persistent_reference": self._persistent_reference_token(
                                    extension, face
                                ),
                                **_face_geometry(face),
                            }
                        )
                if "edge" in entity_types:
                    first_edge = self._read_com_member(body, "GetFirstEdge")
                    for position, edge in enumerate(
                        self._linked_entities(first_edge, "GetNext"), start=1
                    ):
                        rows.append(
                            {
                                "entity_type": "edge",
                                "display_name": f"Body {body_position} Edge {position}",
                                "persistent_reference": self._persistent_reference_token(
                                    extension, edge
                                ),
                                **_edge_geometry(edge),
                            }
                        )
                if "vertex" in entity_types:
                    vertices = self._as_com_collection(
                        self._read_com_member(body, "GetVertices")
                    )
                    for position, vertex in enumerate(vertices, start=1):
                        if vertex is None:
                            continue
                        rows.append(
                            {
                                "entity_type": "vertex",
                                "display_name": (
                                    f"Body {body_position} Vertex {position}"
                                ),
                                "persistent_reference": self._persistent_reference_token(
                                    extension, vertex
                                ),
                                **_vertex_geometry(vertex),
                            }
                        )
            return rows

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_part_topology", _list),
        )

    async def list_sheet_metal_feature_geometry(
        self,
    ) -> AdapterResult[list[dict[str, Any]]]:
        """List flat-pattern and bend feature faces using persistent references.

        The feature tree exposes the geometry owned by each bend feature through
        ``IFeature::GetFaces``.  Those COM face objects never leave the adapter:
        each is encoded with ``GetPersistReference3`` so the workflow can issue
        opaque, revision-bound face IDs.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _feature_kind(type_name: Any) -> str | None:
            normalized = (
                _normalize_solidworks_text(type_name).replace(" ", "").casefold()
            )
            if "flatpattern" in normalized:
                return "flat_pattern"
            if any(
                marker in normalized
                for marker in (
                    "bend",
                    "edgeflange",
                    "miterflange",
                    "hem",
                    "jog",
                )
            ):
                return "bend"
            return None

        def _list() -> list[dict[str, Any]]:
            self._require_part_document(adapter)
            extension = self._model_extension(adapter)
            if extension is None:
                raise RuntimeError("Active part does not expose a model extension")

            rows: list[dict[str, Any]] = []
            feature = self._read_com_member(adapter.currentModel, "FirstFeature")
            for position, candidate in enumerate(
                self._linked_entities(feature, "GetNextFeature"), start=1
            ):
                _sw_type_info.flag_methods(candidate, "IFeature")
                kind = _feature_kind(
                    self._read_com_member(candidate, "GetTypeName2")
                    or self._read_com_member(candidate, "GetTypeName")
                )
                if kind is None:
                    continue
                name = _normalize_solidworks_text(
                    self._read_com_member(candidate, "Name")
                    or f"{kind.replace('_', ' ').title()} {position}"
                ).strip()
                faces: list[dict[str, str]] = []
                for face_position, face in enumerate(
                    self._as_com_collection(
                        self._read_com_member(candidate, "GetFaces")
                    ),
                    start=1,
                ):
                    if face is None:
                        continue
                    try:
                        token = self._persistent_reference_token(extension, face)
                    except RuntimeError:
                        continue
                    faces.append(
                        {
                            "display_name": f"{name} Face {face_position}",
                            "persistent_reference": token,
                        }
                    )
                rows.append(
                    {
                        "display_name": name,
                        "feature_kind": kind,
                        "faces": faces,
                    }
                )
            return rows

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_sheet_metal_feature_geometry", _list),
        )

    async def get_material_assignment(
        self, configuration_name: str
    ) -> AdapterResult[dict[str, str]]:
        """Read material database and name from an active part configuration."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _get() -> dict[str, str]:
            self._require_part_document(adapter)
            return self._read_material_assignment(adapter, configuration_name)

        return cast(
            AdapterResult[dict[str, str]],
            adapter._handle_com_operation("get_material_assignment", _get),
        )

    async def set_material_assignment(
        self, configuration_name: str, database_name: str, material_name: str
    ) -> AdapterResult[dict[str, str]]:
        """Assign a part material and verify it through a fresh COM readback."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _set() -> dict[str, str]:
            self._require_part_document(adapter)
            resolved_configuration_name = self._resolve_configuration_name(
                adapter, configuration_name
            )
            setter = getattr(adapter.currentModel, "SetMaterialPropertyName2", None)
            if not callable(setter):
                raise RuntimeError(
                    "Active part does not support SetMaterialPropertyName2"
                )
            accepted = setter(
                resolved_configuration_name, database_name, material_name
            )
            if isinstance(accepted, bool) and not accepted:
                raise RuntimeError("SolidWorks rejected the material assignment")
            assignment = self._read_material_assignment(
                adapter, resolved_configuration_name
            )
            if assignment["material_name"].casefold() != material_name.casefold():
                raise RuntimeError("SolidWorks did not apply the requested material")
            return assignment

        return cast(
            AdapterResult[dict[str, str]],
            adapter._handle_com_operation("set_material_assignment", _set),
        )

    async def create_configuration(
        self, configuration_name: str, description: str = ""
    ) -> AdapterResult[str]:
        """Create, activate, and verify one independent model configuration."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        def _configuration_names() -> list[str]:
            raw_names = getattr(adapter.currentModel, "GetConfigurationNames", None)
            names = raw_names() if callable(raw_names) else raw_names
            if names is None:
                return []
            if isinstance(names, str):
                names = [names]
            return [
                _normalize_solidworks_text(name).strip()
                for name in names
                if _normalize_solidworks_text(name).strip()
            ]

        def _active_configuration_name() -> str:
            configuration_manager = adapter._attempt(
                lambda: adapter.currentModel.ConfigurationManager, default=None
            )
            active_config = (
                adapter._attempt(
                    lambda: configuration_manager.ActiveConfiguration, default=None
                )
                if configuration_manager is not None
                else None
            )
            if active_config is None:
                active_config = adapter._attempt(
                    lambda: adapter.currentModel.GetActiveConfiguration(), default=None
                )
            if active_config is None:
                raise RuntimeError("SolidWorks did not report an active configuration")
            active_name = adapter._attempt(lambda: active_config.Name, default=None)
            if not active_name:
                active_name = adapter._attempt(
                    lambda: active_config.GetName(), default=None
                )
            normalized_name = _normalize_solidworks_text(active_name).strip()
            if not normalized_name:
                raise RuntimeError(
                    "SolidWorks did not report an active configuration name"
                )
            return normalized_name

        def _create() -> str:
            existing_names = _configuration_names()
            if any(
                name.casefold() == configuration_name.casefold()
                for name in existing_names
            ):
                raise RuntimeError("Configuration already exists")

            creator = getattr(adapter.currentModel, "AddConfiguration3", None)
            if not callable(creator):
                raise RuntimeError("Active document does not support AddConfiguration3")
            created_configuration = creator(
                configuration_name,
                description,
                "",
                0,
                "",
                "",
            )
            if created_configuration is None or created_configuration is False:
                raise RuntimeError(
                    "SolidWorks did not create the requested configuration"
                )

            created_name = adapter._attempt(
                lambda: self._read_com_member(created_configuration, "Name"),
                default=None,
            )
            if created_name is not None and (
                _normalize_solidworks_text(created_name).strip().casefold()
                != configuration_name.casefold()
            ):
                raise RuntimeError("SolidWorks created a different configuration")

            updated_names = _configuration_names()
            if not any(
                name.casefold() == configuration_name.casefold()
                for name in updated_names
            ):
                raise RuntimeError("SolidWorks did not report the new configuration")

            activator = getattr(adapter.currentModel, "ShowConfiguration2", None)
            if not callable(activator):
                raise RuntimeError(
                    "Active document does not support ShowConfiguration2"
                )
            activator(configuration_name)
            active_name = _active_configuration_name()
            if active_name.casefold() != configuration_name.casefold():
                raise RuntimeError("SolidWorks activated a different configuration")
            return active_name

        return cast(
            AdapterResult[str],
            adapter._handle_com_operation("create_configuration", _create),
        )

    async def delete_configuration(self, configuration_name: str) -> AdapterResult[str]:
        """Delete one non-active configuration through ``DeleteConfiguration2``."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        def _configuration_names() -> list[str]:
            raw_names = getattr(adapter.currentModel, "GetConfigurationNames", None)
            names = raw_names() if callable(raw_names) else raw_names
            if names is None:
                return []
            if isinstance(names, str):
                names = [names]
            return [
                _normalize_solidworks_text(name).strip()
                for name in names
                if _normalize_solidworks_text(name).strip()
            ]

        def _active_configuration_name() -> str:
            configuration_manager = adapter._attempt(
                lambda: adapter.currentModel.ConfigurationManager, default=None
            )
            active_config = (
                adapter._attempt(
                    lambda: configuration_manager.ActiveConfiguration, default=None
                )
                if configuration_manager is not None
                else None
            )
            if active_config is None:
                active_config = adapter._attempt(
                    lambda: adapter.currentModel.GetActiveConfiguration(), default=None
                )
            active_name = adapter._attempt(lambda: active_config.Name, default=None)
            if not active_name:
                active_name = adapter._attempt(
                    lambda: active_config.GetName(), default=None
                )
            normalized_name = _normalize_solidworks_text(active_name).strip()
            if not normalized_name:
                raise RuntimeError("SolidWorks did not report an active configuration")
            return normalized_name

        def _delete() -> str:
            existing_name = next(
                (
                    name
                    for name in _configuration_names()
                    if name.casefold() == configuration_name.casefold()
                ),
                None,
            )
            if not existing_name:
                raise RuntimeError("Configuration does not exist")
            if existing_name.casefold() == _active_configuration_name().casefold():
                raise RuntimeError("Active configuration cannot be deleted")

            deleter = getattr(adapter.currentModel, "DeleteConfiguration2", None)
            if not callable(deleter):
                raise RuntimeError(
                    "Active document does not support DeleteConfiguration2"
                )
            accepted = deleter(existing_name)
            if accepted is False or accepted is None:
                raise RuntimeError("SolidWorks rejected the configuration deletion")
            if any(
                name.casefold() == existing_name.casefold()
                for name in _configuration_names()
            ):
                raise RuntimeError("SolidWorks did not remove the configuration")
            return existing_name

        return cast(
            AdapterResult[str],
            adapter._handle_com_operation("delete_configuration", _delete),
        )

    async def activate_configuration(
        self, configuration_name: str
    ) -> AdapterResult[str]:
        """Activate and verify a named configuration on the active model.

        Args:
            configuration_name: Existing SolidWorks configuration name.

        Returns:
            AdapterResult[str]: The verified active configuration name.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        def _activate() -> str:
            """Switch the active model and confirm the requested configuration."""
            # Some SolidWorks versions return False when the requested
            # configuration is already active. Verify the postcondition below
            # instead of treating that no-change result as an API failure.
            adapter.currentModel.ShowConfiguration2(configuration_name)
            configuration_manager = adapter._attempt(
                lambda: adapter.currentModel.ConfigurationManager, default=None
            )
            active_config = (
                adapter._attempt(
                    lambda: configuration_manager.ActiveConfiguration, default=None
                )
                if configuration_manager is not None
                else None
            )
            if active_config is None:
                active_config = adapter._attempt(
                    lambda: adapter.currentModel.GetActiveConfiguration(), default=None
                )
            if not active_config:
                raise Exception("SolidWorks did not report an active configuration")
            active_name = adapter._attempt(lambda: active_config.Name, default=None)
            if not active_name:
                active_name = adapter._attempt(
                    lambda: active_config.GetName(), default=None
                )
            normalized_active_name = _normalize_solidworks_text(active_name)
            if (
                not active_name
                or normalized_active_name.casefold() != configuration_name.casefold()
            ):
                raise Exception("SolidWorks activated a different configuration")
            return normalized_active_name

        return cast(
            AdapterResult[str],
            adapter._handle_com_operation("activate_configuration", _activate),
        )

    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        """Get mass properties for the active model.

        Returns:
            AdapterResult[MassProperties]: Computed mass, volume, area, COM, and inertia.
        """
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _get() -> MassProperties:
            """Get mass properties."""
            adapter._attempt(
                lambda: adapter.currentModel.ForceRebuild3(False), default=None
            )

            # Primary: Extension.CreateMassProperty() object API (most detailed)
            mass_props = adapter._attempt(
                lambda: adapter.currentModel.Extension.CreateMassProperty(),
                default=None,
            )

            if mass_props:
                volume = mass_props.Volume * 1e9
                surface_area = mass_props.SurfaceArea * 1e6
                mass = mass_props.Mass

                center_of_mass = [0.0, 0.0, 0.0]
                com = adapter._attempt(lambda: mass_props.CenterOfMass, default=None)
                if isinstance(com, (list, tuple)) and len(com) >= 3:
                    center_of_mass = [com[0] * 1000, com[1] * 1000, com[2] * 1000]

                moi = adapter._attempt(
                    lambda: mass_props.GetMomentOfInertia(0), default=None
                )
                if not isinstance(moi, (list, tuple)) or len(moi) < 9:
                    moi = [0.0] * 9
            else:
                # Fallback: GetMassProperties as attribute (tuple) or callable (SW 2022)
                gmp = getattr(adapter.currentModel, "GetMassProperties", None)
                if callable(gmp):
                    raw = adapter._attempt(gmp, default=None)
                elif isinstance(gmp, (list, tuple)):
                    raw = gmp
                else:
                    raw = None

                if not isinstance(raw, (list, tuple)) or len(raw) < 6:
                    raise Exception("Failed to get mass properties")

                center_of_mass = [
                    raw[0] * 1000.0,
                    raw[1] * 1000.0,
                    raw[2] * 1000.0,
                ]
                volume = raw[3] * 1e9
                surface_area = raw[4] * 1e6
                mass = raw[5]

                moi = [0.0] * 9
                if len(raw) >= 12:
                    moi[0] = raw[6]
                    moi[4] = raw[7]
                    moi[8] = raw[8]
                    moi[1] = raw[9]
                    moi[5] = raw[10]
                    moi[2] = raw[11]

            return MassProperties(
                volume=volume,
                surface_area=surface_area,
                mass=mass,
                center_of_mass=center_of_mass,
                moments_of_inertia={
                    "Ixx": moi[0],
                    "Iyy": moi[4],
                    "Izz": moi[8],
                    "Ixy": moi[1],
                    "Ixz": moi[2],
                    "Iyz": moi[5],
                },
            )

        return cast(
            AdapterResult[MassProperties],
            adapter._handle_com_operation("get_mass_properties", _get),
        )

    async def get_bounding_box(self) -> AdapterResult[dict[str, list[float]]]:
        """Read the active part's axis-aligned ``GetPartBox`` extents in mm."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _get() -> dict[str, list[float]]:
            self._require_part_document(adapter)
            _sw_type_info.flag_methods(adapter.currentModel, "IPartDoc")
            # Keep native system units (metres), then normalize once for MCP.
            raw_box = adapter.currentModel.GetPartBox(True)
            if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 6:
                raise RuntimeError("SolidWorks returned an invalid part bounding box")
            if any(isinstance(value, bool) for value in raw_box):
                raise RuntimeError("SolidWorks returned an invalid part bounding box")
            try:
                values_m = [float(value) for value in raw_box]
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "SolidWorks returned an invalid part bounding box"
                ) from exc
            if not all(isfinite(value) for value in values_m):
                raise RuntimeError("SolidWorks returned a non-finite part bounding box")

            minimum_m = values_m[:3]
            maximum_m = values_m[3:]
            if any(
                minimum > maximum
                for minimum, maximum in zip(minimum_m, maximum_m, strict=True)
            ):
                raise RuntimeError(
                    "SolidWorks returned reversed part bounding-box extents"
                )
            minimum_mm = [value * 1_000.0 for value in minimum_m]
            maximum_mm = [value * 1_000.0 for value in maximum_m]
            return {
                "min_point_mm": minimum_mm,
                "max_point_mm": maximum_mm,
                "size_mm": [
                    maximum - minimum
                    for minimum, maximum in zip(minimum_mm, maximum_mm, strict=True)
                ],
            }

        return cast(
            AdapterResult[dict[str, list[float]]],
            adapter._handle_com_operation("get_bounding_box", _get),
        )

    async def list_equations(self) -> AdapterResult[list[dict[str, Any]]]:
        """List active-part equations and global variables with parsed fields."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> list[dict[str, Any]]:
            self._require_part_document(adapter)
            manager = self._equation_manager(adapter)
            return self._equation_rows(adapter, manager)

        return cast(
            AdapterResult[list[dict[str, Any]]],
            adapter._handle_com_operation("list_equations", _list),
        )

    async def set_equation(
        self, name: str, expression: str
    ) -> AdapterResult[dict[str, Any]]:
        """Create or update one equation and verify its post-write readback."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _set() -> dict[str, Any]:
            self._require_part_document(adapter)
            manager = self._equation_manager(adapter)
            assignment = f'"{name}" = {expression}'
            existing = self._find_equation_row(
                self._equation_rows(adapter, manager), name
            )
            if existing is None:
                manager.Add2(-1, assignment, True)
            else:
                update_result = manager.SetEquationAndConfigurationOption(
                    int(existing["index"]), assignment, 2, None
                )
                if update_result != 0:
                    raise RuntimeError("SolidWorks rejected the equation update")
            evaluator = getattr(manager, "EvaluateAll", None)
            if not callable(evaluator):
                raise RuntimeError("SolidWorks does not support equation evaluation")
            if evaluator() != 0:
                raise RuntimeError("SolidWorks failed to evaluate the equation")
            verified = self._find_equation_row(
                self._equation_rows(adapter, manager), name
            )
            if verified is None:
                raise RuntimeError("SolidWorks did not retain the equation write")
            if verified["expression"] != expression:
                raise RuntimeError(
                    "SolidWorks did not retain the requested equation expression"
                )
            return verified

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("set_equation", _set),
        )

    async def delete_equation(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Delete one named equation and verify it no longer exists in SolidWorks."""
        adapter = self._adapter(self)
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _delete() -> dict[str, Any]:
            self._require_part_document(adapter)
            manager = self._equation_manager(adapter)
            existing = self._find_equation_row(
                self._equation_rows(adapter, manager), name
            )
            if existing is None:
                raise RuntimeError("Equation does not exist")
            manager.Delete(int(existing["index"]))
            if self._find_equation_row(self._equation_rows(adapter, manager), name):
                raise RuntimeError("SolidWorks did not remove the equation")
            return existing

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("delete_equation", _delete),
        )

    async def pack_and_go_assembly(  # pragma: no cover
        self,
        source_path: str,
        target_dir: str,
        name_prefix: str = "",
    ) -> AdapterResult[dict[str, Any]]:
        """Copy an assembly and all its referenced components to a self-contained folder.

        Uses ``IModelDocExtension.GetPackAndGo()`` → ``IPackAndGo`` via the
        comtypes vtable interface (bypassing the broken IDispatch path present
        in SolidWorks 2026's late-binding layer), then calls
        ``IModelDocExtension.SavePackAndGo()`` to execute the copy.  All file
        paths inside the copied assembly are automatically updated by
        SolidWorks — this is the native Pack-and-Go mechanism.

        Args:
            source_path: Absolute path to the source ``.sldasm`` file.
            target_dir: Directory where the assembly and parts will be copied.
                        Created if it does not exist.
            name_prefix: Optional prefix applied to every copied filename.

        Returns:
            AdapterResult[dict]: On success, ``data`` is a dict with keys:
            ``source_assembly``, ``target_dir``, ``copied_files``,
            ``source_files``, ``save_statuses``, and ``all_files_saved``.
        """
        adapter = self._adapter(self)
        source = Path(source_path)
        out_dir = Path(target_dir)

        def _do_pack_and_go() -> dict[str, Any]:  # pragma: no cover
            # Load comtypes TLB (cached after first call)
            sw_lib = _get_sw_comtypes_lib()
            if sw_lib is None:
                raise RuntimeError(
                    "comtypes SolidWorks type library not available. "
                    "Ensure comtypes is installed and SolidWorks is registered."
                )

            # Prepare a clean target directory. SW holds file locks on previously
            # opened assemblies so rmtree raises WinError 32. We rename the old
            # dir aside (Windows allows rename with open handles) and delete the
            # backup afterwards; if rename also fails we just proceed and let SW
            # overwrite existing files.
            if out_dir.exists() and any(out_dir.iterdir()):
                raise RuntimeError(
                    f"Pack-and-Go target directory is not empty: {out_dir}"
                )
            if out_dir.exists():
                backup = out_dir.parent / f"{out_dir.name}_bak_{uuid.uuid4().hex[:8]}"
                try:
                    os.rename(out_dir, backup)
                    try:
                        shutil.rmtree(backup)
                    except Exception:
                        pass  # best-effort cleanup; stale backup is harmless
                except OSError:
                    pass  # rename also failed — proceed; SW will overwrite files
            out_dir.mkdir(parents=True, exist_ok=True)

            # Reuse an already-open source assembly.  Pack-and-Go is often
            # called from a preview/retry path, where blindly calling OpenDoc6
            # would create another tab for the same assembly.
            expected_source = os.path.normcase(os.path.abspath(str(source)))
            get_documents_member = getattr(adapter.swApp, "GetDocuments", None)
            enumeration_sentinel = object()
            raw_documents = (
                adapter._attempt(
                    lambda: adapter._get_attr_or_call(
                        adapter.swApp, "GetDocuments"
                    ),
                    default=enumeration_sentinel,
                )
                if get_documents_member is not None
                else None
            )
            enumeration_failed = get_documents_member is not None and (
                raw_documents is enumeration_sentinel or raw_documents is None
            )
            if isinstance(raw_documents, (list, tuple)):
                open_documents = [
                    document for document in raw_documents if document is not None
                ]
            else:
                try:
                    open_documents = [
                        document
                        for document in raw_documents
                        if document is not None
                    ]
                except TypeError:
                    open_documents = (
                        [raw_documents]
                        if raw_documents is not None
                        and getattr(raw_documents, "GetPathName", None) is not None
                        else []
                    )
            model = None
            for document in open_documents:
                document_path = adapter._attempt(
                    lambda document=document: adapter._get_attr_or_call(
                        document, "GetPathName"
                    ),
                    default=None,
                )
                if isinstance(document_path, str) and document_path:
                    if (
                        os.path.normcase(os.path.abspath(document_path))
                        == expected_source
                    ):
                        model = document
                        break

            if model is None:
                cached_entry = _OPEN_DOCUMENT_CACHE.get(expected_source)
                if cached_entry is not None:
                    cached_app, cached_document = cached_entry
                    cached_path = adapter._attempt(
                        lambda: adapter._get_attr_or_call(
                            cached_document, "GetPathName"
                        ),
                        default=None,
                    )
                    if _same_com_reference(cached_app, adapter.swApp) and (
                        isinstance(cached_path, str)
                        and os.path.normcase(os.path.abspath(cached_path))
                        == expected_source
                    ):
                        model = cached_document
                    else:
                        _OPEN_DOCUMENT_CACHE.pop(expected_source, None)

            if enumeration_failed and model is None:
                raise RuntimeError(
                    "SolidWorks document enumeration is temporarily unavailable; "
                    "retry Pack-and-Go after SolidWorks finishes updating its documents"
                )

            if model is None:
                vt = pythoncom.VT_BYREF | pythoncom.VT_I4
                from win32com.client import VARIANT  # noqa: PLC0415

                err = VARIANT(vt, 0)
                warn = VARIANT(vt, 0)
                model = adapter.swApp.OpenDoc6(str(source), 2, 1, "", err, warn)
                if model is None:
                    raise RuntimeError(
                        f"OpenDoc6 failed err={err.value} warn={warn.value}"
                    )
                tracker = getattr(adapter, "_track_session_document", None)
                if callable(tracker):
                    tracker(model)
            _sw_type_info.flag_doc(model, 2)
            adapter.currentModel = model

            # Bridge model.Extension → IModelDocExtension via comtypes vtable
            ext_ct = _bridge_com_to_comtypes(model.Extension, sw_lib.IModelDocExtension)

            # GetPackAndGo() via vtable (IDispatch path broken in SW 2026)
            pg = ext_ct.GetPackAndGo()

            # Configure: flatten all files to root of target directory
            pg.FlattenToSingleFolder = True
            pg.SetSaveToName(True, str(out_dir) + "\\")
            if name_prefix:
                pg.AddPrefix = name_prefix

            # Record what files will be packed
            names_result = pg.GetDocumentNames()
            source_files: list[str] = list(names_result[0]) if names_result[0] else []

            # Execute Pack and Go — SavePackAndGo returns a tuple of per-file status codes
            ext_ct2 = _bridge_com_to_comtypes(
                model.Extension, sw_lib.IModelDocExtension
            )
            status_arr = ext_ct2.SavePackAndGo(pg)
            save_statuses: list[int] = list(status_arr) if status_arr else []

            copied_files = sorted(
                str(p)
                for p in out_dir.rglob("*")
                if p.is_file() and p.suffix.lower() in {".sldasm", ".sldprt", ".slddrw"}
            )
            return {
                "source_assembly": str(source),
                "target_dir": str(out_dir),
                "copied_files": copied_files,
                "source_files": source_files,
                "save_statuses": save_statuses,
                "all_files_saved": all(s == 0 for s in save_statuses),
            }

        result = adapter._handle_com_operation("pack_and_go_assembly", _do_pack_and_go)
        if not result.is_success:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Pack and Go failed: {result.error}",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=result.data,
            execution_time=result.execution_time,
        )
