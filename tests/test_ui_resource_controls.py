"""Qt UI integration tests for user-editable resource controls.

Run on the Windows build runner with PySide6 installed; skip on minimal Linux
environments where only the headless media backend is available.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pocketvideo.core.config import SettingsManager
from pocketvideo.core.models import PresetId, ResourceMode
from pocketvideo.ui.controller import AppController
from pocketvideo.ui.main_window import PYSIDE6_AVAILABLE

if PYSIDE6_AVAILABLE:
    from PySide6 import QtWidgets
    from pocketvideo.ui.main_window import PocketVideoMainWindow


@unittest.skipUnless(PYSIDE6_AVAILABLE, "PySide6 is not installed in this environment")
class UIResourceControlsTestSuite(unittest.TestCase):
    def test_manual_ui_changes_update_state_and_preset_can_be_restored(self) -> None:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory(prefix="pocketvideo_qt_resources_") as work:
            root = Path(work)
            controller = AppController(
                settings_manager=SettingsManager(root / "settings"),
                work_dir=root / "work",
            )
            window = PocketVideoMainWindow(controller)
            try:
                eighty_five = window.cpu_cap_combo.findData(85)
                self.assertGreaterEqual(eighty_five, 0)
                window.cpu_cap_combo.setCurrentIndex(eighty_five)
                window.threads_spin.setValue(3)
                window.concurrent_spin.setValue(2)
                window.chk_gpu_enable.setChecked(True)

                self.assertEqual(controller.resource_limits.mode, ResourceMode.CUSTOM)
                self.assertEqual(window.res_mode_combo.currentData(), ResourceMode.CUSTOM.value)
                self.assertEqual(controller.resource_limits.cpu_hard_cap_percent, 85)
                self.assertEqual(controller.resource_limits.cpu_threads, 3)
                self.assertEqual(controller.resource_limits.max_concurrent_jobs, 2)
                self.assertTrue(controller.resource_limits.enable_gpu_encoding)

                light_index = window.res_mode_combo.findData(ResourceMode.LIGHT.value)
                window.res_mode_combo.setCurrentIndex(light_index)
                self.assertEqual(controller.resource_limits.mode, ResourceMode.LIGHT)
                self.assertEqual(controller.resource_limits.cpu_hard_cap_percent, 40)
                self.assertEqual(window.cpu_cap_combo.currentData(), 40)
                self.assertEqual(window.threads_spin.value(), controller.resource_limits.cpu_threads)
                self.assertEqual(window.concurrent_spin.value(), 1)
                self.assertFalse(window.chk_gpu_enable.isChecked())
                self.assertEqual(
                    [window.cpu_cap_combo.itemData(n)
                     for n in range(window.cpu_cap_combo.count())],
                    [10, 20, 30, 40, 50, 60, 70, 75, 80, 85, 90, 95, 100, "custom"],
                )

                # Both the visual comparison table and the preset cards select
                # the very same backend preset.
                window._on_preset_selected(PresetId.MAX_SPACE_SAVING)
                self.assertEqual(controller.active_preset, PresetId.MAX_SPACE_SAVING)
                self.assertEqual(window.preset_table.currentRow(), list(PresetId).index(PresetId.MAX_SPACE_SAVING))

                self.assertEqual(window.preset_table.rowCount(), 5)
                self.assertEqual(window.preset_tabs.currentIndex(), 0)
                self.assertIn("QScrollArea", window.styleSheet())
                self.assertIn("QTableWidget", window.styleSheet())
                window.pref_quality.setChecked(True)
                self.assertEqual(controller.active_preset, PresetId.BEST_QUALITY)

                # Default output folder must survive a fresh controller startup.
                persistent = root / "saved_output"
                window.out_dir_edit.setText(str(persistent))
                window._save_default_folder()
                self.assertEqual(controller.settings.output_directory, str(persistent.resolve()))
                controller2 = AppController(
                    settings_manager=SettingsManager(root / "settings"),
                    work_dir=root / "work2",
                )
                self.assertEqual(controller2.output_dir, persistent.resolve())
                self.assertFalse(controller.remove_selected_file(root / "not_selected.mp4"))
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
