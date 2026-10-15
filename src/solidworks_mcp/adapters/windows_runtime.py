"""Read desktop process state without starting or terminating applications."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os

from ..exceptions import SolidWorksMCPError


def running_solidworks_process_ids() -> tuple[int, ...]:
    """Find SLDWORKS.exe processes in this Windows login session.

    Process snapshots cover the interval before COM registration and survive
    MCP restarts. An unreadable snapshot is an error, not permission to launch.
    """
    if os.name != "nt":
        return ()

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise SolidWorksMCPError("Cannot determine the Windows desktop session; launch refused")
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise SolidWorksMCPError("Cannot inspect running processes; SolidWorks launch refused")
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ids: list[int] = []
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        if not found and ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise SolidWorksMCPError("Cannot read the process snapshot; SolidWorks launch refused")
        while found:
            if entry.szExeFile.lower() == "sldworks.exe":
                process_session = wintypes.DWORD()
                if not kernel.ProcessIdToSessionId(entry.th32ProcessID, ctypes.byref(process_session)):
                    raise SolidWorksMCPError("Cannot identify a running SolidWorks session; launch refused")
                if process_session.value == session.value:
                    ids.append(int(entry.th32ProcessID))
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise SolidWorksMCPError("Incomplete process snapshot; SolidWorks launch refused")
        return tuple(ids)
    finally:
        kernel.CloseHandle(snapshot)
