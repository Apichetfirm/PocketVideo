"""Qt regression test: media file inspection must not block the GUI event loop."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PIL import Image
    from PySide6 import QtCore, QtWidgets
    from pocketvideo.core.config import SettingsManager
    from pocketvideo.ui.controller import AppController
    from pocketvideo.ui.main_window import PocketVideoMainWindow, PYSIDE6_AVAILABLE
except ImportError:
    PYSIDE6_AVAILABLE = False


@unittest.skipUnless(PYSIDE6_AVAILABLE, "Requires Qt")
class AsyncImportTests(unittest.TestCase):
    def test_photo_inspection_emits_result_on_gui_thread(self):
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "มือถือ.png"
            Image.new("RGB", (70, 50), (12, 90, 180)).save(img)
            ctrl = AppController(settings_manager=SettingsManager(root / "config"))
            win = PocketVideoMainWindow(ctrl)
            try:
                win._on_paths_dropped([str(img)])
                self.assertTrue(win._import_busy)
                deadline = QtCore.QElapsedTimer()
                deadline.start()
                while win._import_busy and deadline.elapsed() < 5000:
                    app.processEvents()
                    QtCore.QThread.msleep(15)
                self.assertFalse(win._import_busy)
                self.assertEqual(len(ctrl.selected_files), 1)
                self.assertEqual(ctrl.selected_files[0].media_kind, "image")
                self.assertTrue(win.btn_start_compress.isEnabled())
            finally:
                win.close()


if __name__ == "__main__":
    unittest.main()
