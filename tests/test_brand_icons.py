"""Cross-platform checks for the new PocketVideo icon system.

Qt's native style icons must work in dark and light UI, without system emoji,
an external icon font, or a network request.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE

if PYSIDE6_AVAILABLE:
    from PySide6 import QtCore, QtWidgets
    from pocketvideo.ui.icons import app_icon, icon
    from pocketvideo.ui.main_window import PocketVideoMainWindow
    from pocketvideo.ui.controller import AppController
    from pocketvideo.core.config import SettingsManager


class IconArtTests(unittest.TestCase):
    def test_original_icon_renders_without_an_external_asset(self):
        from PIL import Image
        script = Path(__file__).resolve().parents[1] / "packaging" / "generate_icons.py"
        spec = importlib.util.spec_from_file_location("pocketvideo_icons_build", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = module.render_icon()
        self.assertEqual(original.size, (1024, 1024))
        self.assertEqual(original.getpixel((0, 0))[3], 0)
        self.assertGreater(original.getpixel((500, 500))[3], 0)


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 not installed")
class NativeActionIconTests(unittest.TestCase):
    def test_icons_present_in_both_themes_with_readable_labels(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsManager(Path(directory) / "config")
            controller = AppController(settings_manager=settings,
                                       work_dir=Path(directory) / "work")
            window = PocketVideoMainWindow(controller)
            try:
                self.assertFalse(window.windowIcon().isNull())
                self.assertFalse(app_icon(64).pixmap(64, 64).isNull())
                self.assertFalse(icon("folder").isNull())
                for button in (
                    window.btn_nav_dashboard, window.btn_nav_queue,
                    window.btn_nav_history, window.btn_nav_settings,
                    window.btn_start_compress, window.btn_pause_job,
                    window.btn_resume_job, window.btn_retry_failed,
                    window.drop_zone.btn_add_files,
                    window.drop_zone.btn_add_folder,
                    window.drop_zone.btn_clear,
                    window.queue_columns_btn,
                ):
                    self.assertFalse(button.icon().isNull(),
                                     f"Missing semantic icon on {button.objectName()}")
                    self.assertTrue(button.text(), "Icon is not a substitute for readable text")
                self.assertEqual(
                    window.queue_columns_btn.toolButtonStyle(),
                    QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
                )
                for theme in ("dark", "light"):
                    window.apply_theme(theme)
                    app.processEvents()
                    self.assertFalse(window.windowIcon().isNull())
                self.assertEqual(window.brand_icon_label.accessibleName(),
                                 "PocketVideo application icon")
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()
