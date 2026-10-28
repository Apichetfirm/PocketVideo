"""Windows Job Object CPU Hard Cap, Process Priority, Affinity, and Suspend/Resume controller.

On Windows 10/11 x64, this module uses Win32 API calls via `ctypes.windll.kernel32`:
- `CreateJobObjectW`
- `SetInformationJobObject` with:
  - `JobObjectExtendedLimitInformation` (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + priority class)
  - `JobObjectCpuRateControlInformation` (`JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP`)
- `AssignProcessToJobObject` to apply hard CPU rate limits to child FFmpeg processes.
- `NtSuspendProcess` / `NtResumeProcess` via `ntdll` on Windows (and `SIGSTOP` / `SIGCONT` on POSIX)
  for reliable process pause/resume.

Clearly distinguishes enforceable OS hard caps from best-effort controls.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
import signal
import subprocess
from typing import Optional

from pocketvideo.core.models import ProcessPriority, ResourceLimits, ResourceMode

logger = logging.getLogger("pocketvideo.resources")

# Windows Win32 Constants
JOB_OBJECT_LIMIT_PRIORITY_CLASS = 0x00000020
JOB_OBJECT_LIMIT_AFFINITY = 0x00000010
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x00000001
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x00000004

JobObjectBasicLimitInformation = 2
JobObjectExtendedLimitInformation = 9
JobObjectCpuRateControlInformation = 15

IDLE_PRIORITY_CLASS = 0x00000040
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000

PROCESS_SET_QUOTA = 0x0100
PROCESS_SET_INFORMATION = 0x0200
PROCESS_TERMINATE = 0x0001
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_ALL_ACCESS = 0x001F0FFF

WIN_PRIORITY_MAP: dict[ProcessPriority, int] = {
    ProcessPriority.IDLE: IDLE_PRIORITY_CLASS,
    ProcessPriority.BELOW_NORMAL: BELOW_NORMAL_PRIORITY_CLASS,
    ProcessPriority.NORMAL: NORMAL_PRIORITY_CLASS,
    ProcessPriority.ABOVE_NORMAL: ABOVE_NORMAL_PRIORITY_CLASS,
}


def _configure_win32_signatures(kernel32: object) -> None:
    """Keep native HANDLE results pointer-width-safe on 64-bit Windows."""
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (argtypes, restype) in signatures.items():
        method = getattr(kernel32, name)
        method.argtypes = argtypes
        method.restype = restype


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    """Win32 structure for enforcing a hard CPU percentage cap (CpuRate = percent * 100)."""
    _fields_ = [
        ("ControlFlags", wintypes.DWORD),
        ("CpuRate", wintypes.DWORD),
    ]


def preset_resource_limits(mode: ResourceMode) -> ResourceLimits:
    """Build default ResourceLimits for Light, Balanced, or Maximum mode."""
    total_cpus = os.cpu_count() or 4
    if mode == ResourceMode.LIGHT:
        return ResourceLimits(
            mode=ResourceMode.LIGHT,
            cpu_hard_cap_percent=40,
            enable_cpu_hard_cap=True,
            cpu_threads=max(1, total_cpus // 4),
            process_priority=ProcessPriority.IDLE,
            max_concurrent_jobs=1,
            enable_gpu_encoding=False,
        )
    if mode == ResourceMode.BALANCED:
        return ResourceLimits(
            mode=ResourceMode.BALANCED,
            cpu_hard_cap_percent=75,
            enable_cpu_hard_cap=True,
            cpu_threads=max(1, total_cpus // 2),
            process_priority=ProcessPriority.BELOW_NORMAL,
            max_concurrent_jobs=1,
            enable_gpu_encoding=False,
        )
    if mode == ResourceMode.MAXIMUM:
        return ResourceLimits(
            mode=ResourceMode.MAXIMUM,
            cpu_hard_cap_percent=100,
            enable_cpu_hard_cap=False,
            cpu_threads=0,
            process_priority=ProcessPriority.NORMAL,
            max_concurrent_jobs=2,
            enable_gpu_encoding=True,
        )
    return ResourceLimits(mode=ResourceMode.CUSTOM)


class ProcessResourceController:
    """Applies Windows Job Object CPU hard caps, priority, and affinity to child FFmpeg processes."""

    def __init__(self, limits: ResourceLimits, kernel32_api: Optional[object] = None) -> None:
        self.limits = limits
        self._kernel32 = kernel32_api or (
            ctypes.WinDLL("kernel32", use_last_error=True)
            if os.name == "nt" and hasattr(ctypes, "WinDLL") else None
        )
        if self._kernel32 is not None:
            _configure_win32_signatures(self._kernel32)
        self._job_handle: Optional[int] = None
        self.hard_cap_active: bool = False
        self.status_summary: str = "Not initialized"

    def attach_to_process(self, proc: subprocess.Popen[str]) -> dict[str, object]:
        """Attach resource controls to a spawned child FFmpeg process and return applied status."""
        pid = proc.pid
        applied_details: dict[str, object] = {
            "pid": pid,
            "platform": os.name,
            "cpu_hard_cap_enforced": False,
            "cpu_hard_cap_percent": self.limits.cpu_hard_cap_percent,
            "priority_applied": False,
            "affinity_applied": False,
        }

        if self._kernel32 is not None:
            self._apply_windows_job_object(pid, applied_details)
        else:
            self._apply_posix_fallback(pid, applied_details)

        return applied_details

    def _apply_windows_job_object(self, pid: int, details: dict[str, object]) -> None:
        k32 = self._kernel32
        assert k32 is not None
        try:
            # A two-pass encode may create a separate FFmpeg process for each pass.
            self.close()
            job_handle = k32.CreateJobObjectW(None, None)
            if not job_handle:
                logger.warning("CreateJobObjectW returned NULL.")
                return
            self._job_handle = int(job_handle)

            # 1. Configure Extended Limit Information (kill on close, priority, optional affinity)
            ext_info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PRIORITY_CLASS
            ext_info.BasicLimitInformation.PriorityClass = WIN_PRIORITY_MAP.get(
                self.limits.process_priority, BELOW_NORMAL_PRIORITY_CLASS
            )
            if self.limits.cpu_affinity_mask:
                flags |= JOB_OBJECT_LIMIT_AFFINITY
                ext_info.BasicLimitInformation.Affinity = int(self.limits.cpu_affinity_mask)
            ext_info.BasicLimitInformation.LimitFlags = flags

            res_ext = k32.SetInformationJobObject(
                job_handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(ext_info),
                ctypes.sizeof(ext_info),
            )
            if res_ext:
                details["priority_applied"] = True
                if self.limits.cpu_affinity_mask:
                    details["affinity_applied"] = True

            # 2. Configure Genuine CPU Hard Cap via JobObjectCpuRateControlInformation
            cap_pct = max(5, min(100, int(self.limits.cpu_hard_cap_percent)))
            if self.limits.enable_cpu_hard_cap and cap_pct < 100:
                cpu_info = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
                cpu_info.ControlFlags = (
                    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
                )
                # Windows expects percentage * 100 (e.g., 50% -> 5000)
                cpu_info.CpuRate = cap_pct * 100
                res_cpu = k32.SetInformationJobObject(
                    job_handle,
                    JobObjectCpuRateControlInformation,
                    ctypes.byref(cpu_info),
                    ctypes.sizeof(cpu_info),
                )
                if res_cpu:
                    self.hard_cap_active = True
                    details["cpu_hard_cap_enforced"] = True

            # 3. Open child FFmpeg process handle and assign to Job Object
            proc_handle = k32.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SET_INFORMATION,
                False,
                int(pid),
            )
            if not proc_handle:
                logger.warning("OpenProcess failed for PID %s; resource limits were not applied", pid)
                details.update(cpu_hard_cap_enforced=False, priority_applied=False, affinity_applied=False)
                self.close()
                return
            try:
                assigned = k32.AssignProcessToJobObject(job_handle, proc_handle)
                if not assigned:
                    logger.warning("AssignProcessToJobObject failed for PID %s", pid)
                    details.update(cpu_hard_cap_enforced=False, priority_applied=False, affinity_applied=False)
                    self.close()
                    return
            finally:
                k32.CloseHandle(proc_handle)

            self.status_summary = (
                f"Windows Job Object active (Hard CPU Cap: {cap_pct}% enforced={self.hard_cap_active})"
            )
        except Exception as exc:
            logger.warning("Failed to apply Windows Job Object limits: %s", exc)
            details.update(cpu_hard_cap_enforced=False, priority_applied=False, affinity_applied=False)
            self.close()
            self.status_summary = f"Windows Job Object fallback ({exc})"

    def _apply_posix_fallback(self, pid: int, details: dict[str, object]) -> None:
        """Apply POSIX nice priority and sched_setaffinity when running tests on Linux/macOS."""
        try:
            if hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
                nice_map = {
                    ProcessPriority.IDLE: 19,
                    ProcessPriority.BELOW_NORMAL: 10,
                    ProcessPriority.NORMAL: 0,
                    ProcessPriority.ABOVE_NORMAL: -5,
                }
                nice_val = nice_map.get(self.limits.process_priority, 10)
                if nice_val >= 0:
                    os.setpriority(os.PRIO_PROCESS, pid, nice_val)
                    details["priority_applied"] = True
        except OSError:
            pass

        if self.limits.cpu_affinity_mask and hasattr(os, "sched_setaffinity"):
            try:
                total_cpus = os.cpu_count() or 4
                cpus = {
                    i for i in range(total_cpus) if (self.limits.cpu_affinity_mask & (1 << i))
                }
                if cpus:
                    os.sched_setaffinity(pid, cpus)
                    details["affinity_applied"] = True
            except OSError:
                pass

        self.status_summary = (
            "POSIX best-effort priority/affinity active (Windows Job Object CPU Hard Cap requires Windows)"
        )

    def close(self) -> None:
        if self._job_handle is not None and self._kernel32 is not None:
            try:
                self._kernel32.CloseHandle(self._job_handle)
            except Exception:
                pass
            self._job_handle = None
        self.hard_cap_active = False


def suspend_process(pid: int) -> bool:
    """Pause a running FFmpeg process safely using NtSuspendProcess on Windows or SIGSTOP on POSIX."""
    if os.name == "nt" and hasattr(ctypes, "windll"):
        k32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        handle = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, int(pid))
        if not handle:
            return False
        try:
            status = ntdll.NtSuspendProcess(handle)
            return status == 0
        finally:
            k32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, signal.SIGSTOP)
            return True
        except OSError:
            return False


def resume_process(pid: int) -> bool:
    """Resume a paused FFmpeg process safely using NtResumeProcess on Windows or SIGCONT on POSIX."""
    if os.name == "nt" and hasattr(ctypes, "windll"):
        k32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        handle = k32.OpenProcess(PROCESS_SUSPEND_RESUME, False, int(pid))
        if not handle:
            return False
        try:
            status = ntdll.NtResumeProcess(handle)
            return status == 0
        finally:
            k32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, signal.SIGCONT)
            return True
        except OSError:
            return False
