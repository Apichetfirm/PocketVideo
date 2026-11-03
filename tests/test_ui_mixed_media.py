"""Qt integration for the mixed-media import and persistent output settings."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pocketvideo.core.config import SettingsManager
from pocketvideo.core.models import ImagePreset, OutputPolicy, AfterExportAction
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE

if PYSIDE6_AVAILABLE:
    from PySide6 import QtWidgets
    from pocketvideo.ui.main_window import PocketVideoMainWindow


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 not installed")
class MixedMediaUITests(unittest.TestCase):
    def test_photo_gallery_and_output_settings_persist(self) -> None:
        from PIL import Image
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory(prefix="pv_ui_mixed_") as d:
            root = Path(d)
            img = root / "ทดสอบ_ภาพมือถือ.jpg"
            Image.new("RGB", (96, 64), (120, 80, 30)).save(img, "JPEG")
            controller = AppController(settings_manager=SettingsManager(root / "settings"))
            added, errors = controller.add_files([img])
            self.assertFalse(errors, errors)
            self.assertEqual(len(added), 1)
            self.assertEqual(added[0].media_kind, "image")

            window = PocketVideoMainWindow(controller)
            try:
                window._refresh_selection_summary()
                self.assertEqual(window.file_table.rowCount(), 1)
                self.assertIn("Photo", window.file_table.item(0, 2).text())
                self.assertTrue(img.exists())
                window.suffix_edit.setText("_ใหม่")
                window.image_format_combo.setCurrentIndex(
                    window.image_format_combo.findData("heic")
                )
                window.image_preset_combo.setCurrentIndex(
                    window.image_preset_combo.findData(ImagePreset.SMALLEST.value)
                )
                window.output_policy_combo.setCurrentIndex(
                    window.output_policy_combo.findData(OutputPolicy.KEEP_BOTH.value)
                )
                window.after_action_combo.setCurrentIndex(
                    window.after_action_combo.findData(AfterExportAction.NONE.value)
                )
                self.assertTrue(window._on_output_preferences_changed())
                controller2 = AppController(
                    settings_manager=SettingsManager(root / "settings")
                )
                self.assertEqual(controller2.filename_suffix, "_ใหม่")
                self.assertEqual(controller2.image_output_format, "heic")
                self.assertEqual(controller2.image_preset, ImagePreset.SMALLEST)
                self.assertEqual(controller2.output_policy, OutputPolicy.KEEP_BOTH)
                self.assertEqual(controller2.settings.after_export_action, "none")

                # Removes the file *only* from selection, never from disk.
                window._remove_imported_file(img)
                self.assertEqual(len(controller.selected_files), 0)
                self.assertTrue(img.exists())
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
