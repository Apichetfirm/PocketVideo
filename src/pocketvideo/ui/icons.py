"""Small, native-style icon system for a readable PocketVideo desktop UI.

All action icons come from Qt's native platform style, so they stay crisp on
Windows/macOS and do not depend on platform-specific emoji fonts or a
third-party icon pack. The program mark is an original geometric drawing.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


ICON_MAP: dict[str, str] = {
    "dashboard": "SP_DesktopIcon",
    "convert": "SP_BrowserReload",
    "queue": "SP_FileDialogDetailedView",
    "history": "SP_FileDialogListView",
    "settings": "SP_ComputerIcon",
    "file": "SP_FileIcon",
    "add": "SP_DialogOpenButton",
    "folder": "SP_DirOpenIcon",
    "remove": "SP_TrashIcon",
    "preview": "SP_MediaPlay",
    "play": "SP_MediaPlay",
    "pause": "SP_MediaPause",
    "resume": "SP_MediaPlay",
    "cancel": "SP_MediaStop",
    "retry": "SP_BrowserReload",
    "save": "SP_DialogSaveButton",
    "columns": "SP_FileDialogDetailedView",
    "theme": "SP_DesktopIcon",
    "compare": "SP_FileDialogContentsView",
    "more": "SP_TitleBarUnshadeButton",
}


def icon(name: str, widget: QtWidgets.QWidget | None = None) -> QtGui.QIcon:
    """Get a native icon without icon font/plugin/network dependencies."""
    style = widget.style() if widget is not None else QtWidgets.QApplication.style()
    standard = ICON_MAP.get(name, "SP_FileIcon")
    value = getattr(QtWidgets.QStyle.StandardPixmap, standard, None)
    if value is None:
        value = QtWidgets.QStyle.StandardPixmap.SP_FileIcon
    return style.standardIcon(value)


def assign(
    widget: QtWidgets.QAbstractButton,
    name: str,
    *,
    tooltip: str | None = None,
    size: int = 18,
) -> None:
    widget.setIcon(icon(name, widget))
    widget.setIconSize(QtCore.QSize(size, size))
    if tooltip:
        widget.setToolTip(tooltip)


def app_icon(size: int = 256) -> QtGui.QIcon:
    """Draw an original pocket/play mark directly with Qt (no SVG plugin)."""
    size = max(24, int(size))
    image = QtGui.QPixmap(size, size)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(image)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.scale(size / 100.0, size / 100.0)
    base = QtCore.QRectF(4, 4, 92, 92)
    bg = QtGui.QLinearGradient(6, 5, 94, 96)
    bg.setColorAt(0, QtGui.QColor("#4E8AFF"))
    bg.setColorAt(0.55, QtGui.QColor("#416CE9"))
    bg.setColorAt(1, QtGui.QColor("#6C42D7"))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(base, 24, 24)
    # A deliberately bold silhouette: readable at 16px, including macOS Dock.
    p.setBrush(QtGui.QColor("#FFFFFF"))
    p.drawRoundedRect(QtCore.QRectF(20, 24, 60, 52), 12, 12)
    p.setBrush(QtGui.QColor("#426CE8"))
    triangle = QtGui.QPolygonF([
        QtCore.QPointF(43, 35),
        QtCore.QPointF(43, 65),
        QtCore.QPointF(67, 50),
    ])
    p.drawPolygon(triangle)
    # Subtle 'pocket' line across the bottom of the window.
    p.setPen(QtGui.QPen(QtGui.QColor("#DCE5FF"), 3, QtCore.Qt.PenStyle.SolidLine,
                       QtCore.Qt.PenCapStyle.RoundCap))
    p.drawLine(QtCore.QPointF(31, 70), QtCore.QPointF(68, 70))
    p.end()
    return QtGui.QIcon(image)


def decorate_main_window(window: QtWidgets.QMainWindow) -> None:
    """Apply meaningful, small icons without changing button text or signals."""
    window.setWindowIcon(app_icon())
    groups = {
        "dashboard": ("btn_nav_dashboard",),
        "convert": ("btn_nav_convert",),
        "queue": ("btn_nav_queue",),
        "history": ("btn_nav_history",),
        "settings": ("btn_nav_settings",),
        "file": ("btn_add_files",),
        "folder": ("btn_add_folder", "btn_browse_out"),
        "remove": ("btn_clear", "btn_cancel_all"),
        "preview": ("btn_inspect", "btn_start_convert"),
        "play": ("btn_start_compress",),
        "pause": ("btn_pause_job",),
        "resume": ("btn_resume_job",),
        "cancel": ("btn_cancel_job",),
        "retry": ("btn_retry_failed",),
        "save": ("btn_save_out_default",),
        "columns": ("queue_columns_btn",),
        "theme": ("theme_btn",),
        "compare": ("btn_toggle_advanced",),
    }
    for semantic, attrs in groups.items():
        for attr in attrs:
            widget = getattr(window, attr, None)
            if widget is not None and isinstance(widget, QtWidgets.QAbstractButton):
                assign(widget, semantic)
    for attr, semantic in (
        ("btn_add_files", "add"),
        ("btn_add_folder", "folder"),
        ("btn_clear", "remove"),
        ("btn_inspect", "compare"),
    ):
        widget = getattr(getattr(window, "drop_zone", None), attr, None)
        if widget is not None:
            assign(widget, semantic)
