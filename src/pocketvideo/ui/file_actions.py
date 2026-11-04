"""Safe Windows Explorer 'Open file location' helper.

Explorer receives a selected *file*, not merely its parent folder. No shell
interpolation is performed and originals are never modified.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def reveal_file_in_location(file_path: Path) -> bool:
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File unavailable: {path}")
    if sys.platform == "win32":
        # Keep /select and filename in the same Explorer argument. subprocess
        # will apply Windows quoting for spaces and Thai/unicode file names.
        subprocess.Popen(
            ["explorer.exe", f"/select,{path}"],
            shell=False,
            close_fds=True,
        )
        return True
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)], shell=False, close_fds=True)
        return True
    # Best-effort fallback for developer/test environments outside Windows.
    from PySide6 import QtCore, QtGui
    return bool(QtGui.QDesktopServices.openUrl(
        QtCore.QUrl.fromLocalFile(str(path.parent))
    ))
