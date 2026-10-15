"""Diagnose the SolidWorks COM dimension traversal without modifying a model.

Usage from the repository root:

    .venv\\Scripts\\python.exe scripts\\diagnose_real_dimension_enumeration.py path\\to\\model.sldprt

The script opens the specified model through the production adapter, prints the
outcome of each COM call used by ``list_dimensions``, and closes the diagnostic
document without saving it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter


def _format_result(operation: str, callback: Any) -> tuple[Any, bool]:
    """Run one diagnostic COM operation and print a concise outcome."""
    try:
        value = callback()
    except Exception as exc:  # The exception is the diagnostic result.
        print(f"  {operation}: ERROR {type(exc).__name__}: {exc}")
        return None, False
    print(f"  {operation}: OK {value!r}")
    return value, True


def _read_or_call(obj: Any, name: str) -> Any:
    """Read a COM property which may be exposed as a zero-argument method."""
    member = getattr(obj, name)
    return member() if callable(member) else member


async def run(model_path: Path) -> int:
    """Open one model and report all display-dimension traversal calls."""
    if not model_path.is_file():
        print(f"Model file does not exist: {model_path}", file=sys.stderr)
        return 2

    adapter = PyWin32Adapter()
    await adapter.connect()
    try:
        opened = await adapter.open_model(str(model_path))
        if opened.data is None:
            print(f"Open failed: {opened.error}", file=sys.stderr)
            return 1
        model = adapter.currentModel
        if model is None:
            print("Adapter did not retain the opened model", file=sys.stderr)
            return 1

        sw_type_info.flag_methods(model, "IModelDoc2", "FirstFeature")
        _format_result(
            "model.FirstFeature", lambda: _read_or_call(model, "FirstFeature")
        )
        _format_result(
            "model.IFirstFeature", lambda: _read_or_call(model, "IFirstFeature")
        )
        equation_manager, equation_manager_ok = _format_result(
            "model.GetEquationMgr", lambda: model.GetEquationMgr()
        )
        if equation_manager is not None and equation_manager_ok:
            sw_type_info.flag_methods(equation_manager, "IEquationMgr")
            count, count_ok = _format_result(
                "equationManager.GetCount",
                lambda current=equation_manager: _read_or_call(current, "GetCount"),
            )
            if count_ok and isinstance(count, int):
                for equation_index in range(count):
                    _format_result(
                        f"equationManager.Equation({equation_index})",
                        lambda position=equation_index, current=equation_manager: current.Equation(
                            position
                        ),
                    )

        for feature_index in range(100):
            feature, found = _format_result(
                f"model.FeatureByPositionReverse({feature_index})",
                lambda position=feature_index: model.FeatureByPositionReverse(position),
            )
            if not found:
                return 1
            if feature is None:
                break
            sw_type_info.flag_methods(
                feature,
                "IFeature",
                "GetFirstDisplayDimension",
                "GetNextDisplayDimension",
                "GetNextFeature",
                "GetTypeName2",
            )
            print(f"feature[{feature_index}]")
            _format_result(
                "GetTypeName2", lambda current=feature: _read_or_call(current, "GetTypeName2")
            )
            for parameter_index in range(1, 9):
                parameter, parameter_ok = _format_result(
                    f"feature.Parameter(D{parameter_index})",
                    lambda index=parameter_index, current=feature: current.Parameter(
                        f"D{index}"
                    ),
                )
                if parameter is None or not parameter_ok:
                    continue
                sw_type_info.flag_methods(
                    parameter,
                    "IDimension",
                    "GetNameForSelection",
                    "GetSystemValue3",
                    "GetValue3",
                )
                _format_result(
                    "parameter.GetNameForSelection",
                    lambda current=parameter: current.GetNameForSelection(),
                )
                _format_result(
                    "parameter.SystemValue",
                    lambda current=parameter: current.SystemValue,
                )
            display, display_ok = _format_result(
                "GetFirstDisplayDimension",
                lambda current=feature: current.GetFirstDisplayDimension(),
            )
            indexed_display, indexed_display_ok = _format_result(
                "GetDisplayDimension(0)",
                lambda current=feature: current.GetDisplayDimension(0),
            )
            if display is None:
                display = indexed_display
                display_ok = indexed_display_ok

            display_index = 0
            while display is not None and display_ok and display_index < 100:
                display_index += 1
                print(f" display[{display_index}]")
                sw_type_info.flag_methods(
                    display,
                    "IDisplayDimension",
                    "GetDimension",
                    "GetNameForSelection",
                    "GetNext2",
                )
                dimension, dimension_ok = _format_result(
                    "GetDimension", lambda current=display: current.GetDimension()
                )
                _format_result(
                    "display.GetNameForSelection",
                    lambda current=display: current.GetNameForSelection(),
                )
                if dimension is not None and dimension_ok:
                    sw_type_info.flag_methods(
                        dimension,
                        "IDimension",
                        "GetNameForSelection",
                        "GetSystemValue3",
                        "GetValue3",
                    )
                    _format_result(
                        "dimension.GetNameForSelection",
                        lambda current=dimension: current.GetNameForSelection(),
                    )
                    _format_result(
                        "dimension.SystemValue",
                        lambda current=dimension: current.SystemValue,
                    )
                    _format_result(
                        "dimension.GetSystemValue3",
                        lambda current=dimension: current.GetSystemValue3(0, None),
                    )
                    _format_result(
                        "dimension.GetValue3",
                        lambda current=dimension: current.GetValue3(0, 0),
                    )
                display, display_ok = _format_result(
                    "feature.GetNextDisplayDimension",
                    lambda current_feature=feature, current_display=display: current_feature.GetNextDisplayDimension(current_display),
                )
        listed = await adapter.list_dimensions()
        print(f"adapter.list_dimensions.status: {listed.status.value}")
        print(f"adapter.list_dimensions.data: {listed.data!r}")
        if listed.error:
            print(f"adapter.list_dimensions.error: {listed.error}")
        return 0
    finally:
        if adapter.currentModel is not None:
            await adapter.close_model(save=False)
        await adapter.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Expected one model file path", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(run(Path(sys.argv[1]).resolve())))
