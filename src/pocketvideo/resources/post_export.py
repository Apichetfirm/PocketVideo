"""Explicit, confirmed Windows power actions after successful batch completion.

This module never runs on its own. The GUI must confirm a selected action at
batch start AND offer a cancelable countdown after every output is verified.
No forced process termination or destructive command flags are used.
"""
from __future__ import annotations

import ctypes
import os
import subprocess

from pocketvideo.core.models import AfterExportAction


def run_after_export(action: AfterExportAction, *, user_confirmed: bool = False) -> None:
    """Carry out a user's second explicit confirmation, on Windows only."""
    action = AfterExportAction(action)
    if action == AfterExportAction.NONE:
        return
    if not user_confirmed:
        raise PermissionError("Sleep/shutdown requires explicit GUI confirmation.")
    if os.name != "nt":
        raise OSError("Power actions are supported only on Windows.")
    if action == AfterExportAction.SHUTDOWN:
        # /f is intentionally omitted to let Windows protect open documents.
        subprocess.run(["shutdown.exe", "/s", "/t", "0"], check=True, shell=False)
        return
    if action == AfterExportAction.SLEEP:
        powrprof = ctypes.WinDLL("PowrProf.dll")
        powrprof.SetSuspendState.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]
        powrprof.SetSuspendState.restype = ctypes.c_ubyte
        if not powrprof.SetSuspendState(0, 0, 0):
            raise ctypes.WinError()
