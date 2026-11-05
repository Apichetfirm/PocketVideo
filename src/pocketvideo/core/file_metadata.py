"""Preserve filesystem timestamps after a verified export.

Windows supports restoring creation time through SetFileTime. On other systems
creation/birth time generally cannot be set portably; atime/mtime are copied.
Failure is reported to the caller as a non-fatal export warning.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def copy_source_timestamps(source_stat: Any, output: Path) -> None:
    """Copy original access/modified times and Windows creation time to output."""
    os.utime(output, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    # Python 3.12+ exposes st_birthtime; on older Windows st_ctime is creation time.
    created = getattr(source_stat, "st_birthtime", source_stat.st_ctime)
    ticks = int((created + 11644473600) * 10_000_000)
    filetime = FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    set_time = kernel.SetFileTime
    set_time.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME),
                         ctypes.c_void_p, ctypes.c_void_p]
    set_time.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(output), 0x100, 0x1 | 0x2 | 0x4, None, 3, 0, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid or handle is None:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not set_time(handle, ctypes.byref(filetime), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)
