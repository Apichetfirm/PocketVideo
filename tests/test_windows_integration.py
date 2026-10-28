"""Windows resource-controller integration and error-path tests."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock

from pocketvideo.core.models import ResourceLimits, ResourceMode
from pocketvideo.resources.job_object import ProcessResourceController


class WindowsResourceControllerTestSuite(unittest.TestCase):
    def test_failed_process_open_does_not_report_enforced_cpu_cap(self) -> None:
        kernel = MagicMock()
        kernel.CreateJobObjectW.return_value = 999
        kernel.SetInformationJobObject.return_value = 1
        kernel.OpenProcess.return_value = 0
        limits = ResourceLimits(
            mode=ResourceMode.CUSTOM,
            cpu_hard_cap_percent=45,
            enable_cpu_hard_cap=True,
        )
        controller = ProcessResourceController(limits, kernel32_api=kernel)
        details = controller.attach_to_process(MagicMock(pid=12345))
        self.assertFalse(details["cpu_hard_cap_enforced"])
        self.assertFalse(controller.hard_cap_active)
        self.assertIsNone(controller._job_handle)
        kernel.CloseHandle.assert_called_once_with(999)

    @unittest.skipUnless(os.name == "nt", "Real Win32 Job Object test requires Windows")
    def test_real_windows_job_enforces_and_closes_process(self) -> None:
        limits = ResourceLimits(
            mode=ResourceMode.CUSTOM,
            cpu_hard_cap_percent=45,
            enable_cpu_hard_cap=True,
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=creationflags,
        )
        controller = ProcessResourceController(limits)
        try:
            details = controller.attach_to_process(proc)
            self.assertTrue(details["cpu_hard_cap_enforced"], controller.status_summary)
            self.assertTrue(controller.hard_cap_active)
        finally:
            controller.close()
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
