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
from pocketvideo.core.models import ResourceMode
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
                window.cpu_cap_spin.setValue(42)
                window.threads_spin.setValue(3)
                window.concurrent_spin.setValue(2)
                window.chk_gpu_enable.setChecked(True)

                self.assertEqual(controller.resource_limits.mode, ResourceMode.CUSTOM)
                self.assertEqual(window.res_mode_combo.currentData(), ResourceMode.CUSTOM.value)
                self.assertEqual(controller.resource_limits.cpu_hard_cap_percent, 42)
                self.assertEqual(controller.resource_limits.cpu_threads, 3)
                self.assertEqual(controller.resource_limits.max_concurrent_jobs, 2)
                self.assertTrue(controller.resource_limits.enable_gpu_encoding)

                light_index = window.res_mode_combo.findData(ResourceMode.LIGHT.value)
                window.res_mode_combo.setCurrentIndex(light_index)
                self.assertEqual(controller.resource_limits.mode, ResourceMode.LIGHT)
                self.assertEqual(controller.resource_limits.cpu_hard_cap_percent, 40)
                self.assertEqual(window.cpu_cap_spin.value(), 40)
                self.assertEqual(window.threads_spin.value(), controller.resource_limits.cpu_threads)
                self.assertEqual(window.concurrent_spin.value(), 1)
                self.assertFalse(window.chk_gpu_enable.isChecked())
            finally:
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
