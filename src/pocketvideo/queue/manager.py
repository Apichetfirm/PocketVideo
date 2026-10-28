"""Asynchronous batch processing queue manager with output verification and safe cleanup."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional

from pocketvideo.core.models import (
    EncodingJobSpec,
    JobProgress,
    JobResultComparison,
    JobStatus,
    MediaMetadata,
)
from pocketvideo.media.binaries import BinaryPaths, _get_CreationFlags
from pocketvideo.media.encoder import (
    FFmpegProgressParser,
    PlannedEncodeExecution,
    build_encode_plan,
    cleanup_twopass_logs,
)
from pocketvideo.media.probe import inspect_media
from pocketvideo.resources.job_object import (
    ProcessResourceController,
    resume_process,
    suspend_process,
)

logger = logging.getLogger("pocketvideo.queue")

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".3gp",
    ".ts",
    ".mts",
    ".flv",
}


def discover_videos_in_folder(folder: Path, recursive: bool = True) -> list[Path]:
    """Scan a directory for supported video files, sorted by name."""
    if not folder.exists() or not folder.is_dir():
        return []
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    matches = [
        p.resolve()
        for p in iterator
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        and not p.name.startswith(".pocketvideo_tmp_")
    ]
    return sorted(matches)


def build_safe_output_path(
    input_path: Path,
    output_dir: Path,
    target_container: str,
    suffix_tag: str = "_pocketvideo",
) -> Path:
    """Generate a non-destructive output path that never collides with source or existing files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = "." + target_container.lower().lstrip(".")
    stem = input_path.stem
    candidate = (output_dir / f"{stem}{suffix_tag}{ext}").resolve()

    if candidate == input_path.resolve():
        candidate = (output_dir / f"{stem}_compressed{ext}").resolve()

    counter = 1
    while candidate.exists() or candidate == input_path.resolve():
        candidate = (output_dir / f"{stem}{suffix_tag}_{counter}{ext}").resolve()
        counter += 1
    return candidate


@dataclass
class QueueItem:
    """State container for a single queued job."""
    spec: EncodingJobSpec
    input_metadata: Optional[MediaMetadata] = None
    status: JobStatus = JobStatus.QUEUED
    progress: JobProgress = field(default_factory=JobProgress)
    result: Optional[JobResultComparison] = None
    error_message: str = ""
    detailed_log: str = ""
    warnings: list[str] = field(default_factory=list)
    active_pid: Optional[int] = None
    resource_summary: str = ""


class QueueManager:
    """Thread-safe batch queue manager supporting concurrency, pause/resume, cancel, and retry."""

    def __init__(
        self,
        binaries: BinaryPaths,
        work_dir: Optional[Path] = None,
        on_job_updated: Optional[Callable[[QueueItem], None]] = None,
        on_queue_summary: Optional[Callable[[float, int, int], None]] = None,
        on_job_completed: Optional[Callable[[JobResultComparison], None]] = None,
    ) -> None:
        self.binaries = binaries
        self.work_dir = work_dir or Path(tempfile.gettempdir()) / "pocketvideo_work"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.on_job_updated = on_job_updated
        self.on_queue_summary = on_queue_summary
        self.on_job_completed = on_job_completed

        self._lock = threading.RLock()
        self._items: dict[str, QueueItem] = {}
        self._order: list[str] = []
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancel_flags: set[str] = set()
        self._active_workers: int = 0
        self._max_concurrent: int = 1
        self._scheduler_thread: Optional[threading.Thread] = None
        self._shutdown: bool = False

    def set_max_concurrent_jobs(self, count: int) -> None:
        with self._lock:
            self._max_concurrent = max(1, min(8, int(count)))

    def items(self) -> list[QueueItem]:
        with self._lock:
            return [self._items[jid] for jid in self._order if jid in self._items]

    def get_item(self, job_id: str) -> Optional[QueueItem]:
        with self._lock:
            return self._items.get(job_id)

    def enqueue(
        self,
        spec: EncodingJobSpec,
        input_metadata: Optional[MediaMetadata] = None,
        auto_start: bool = True,
    ) -> QueueItem:
        item = QueueItem(spec=spec, input_metadata=input_metadata)
        with self._lock:
            self._items[spec.job_id] = item
            self._order.append(spec.job_id)
            self._max_concurrent = max(1, spec.resource_limits.max_concurrent_jobs)
        self._notify_item(item)
        if auto_start:
            self.start()
        return item

    def start(self) -> None:
        """Start the background dispatcher thread if not already running."""
        with self._lock:
            if self._scheduler_thread and self._scheduler_thread.is_alive():
                return
            self._shutdown = False
            self._scheduler_thread = threading.Thread(
                target=self._dispatch_loop, name="PocketVideoQueueDispatcher", daemon=True
            )
            self._scheduler_thread.start()

    def wait_for_all(self, timeout: float = 120.0) -> bool:
        """Block until all queued/running jobs finish (used in CLI and automated tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                active = any(
                    self._items[jid].status
                    in (
                        JobStatus.QUEUED,
                        JobStatus.VALIDATING,
                        JobStatus.PASS_1,
                        JobStatus.PROCESSING,
                        JobStatus.VERIFYING,
                        JobStatus.PAUSED,
                    )
                    for jid in self._order
                )
                if not active and self._active_workers == 0:
                    return True
            time.sleep(0.05)
        return False

    def pause_job(self, job_id: str) -> bool:
        with self._lock:
            item = self._items.get(job_id)
            proc = self._processes.get(job_id)
            if not item or not proc or item.status not in (JobStatus.PASS_1, JobStatus.PROCESSING):
                return False
            if suspend_process(proc.pid):
                item.status = JobStatus.PAUSED
                self._notify_item(item)
                return True
            return False

    def resume_job(self, job_id: str) -> bool:
        with self._lock:
            item = self._items.get(job_id)
            proc = self._processes.get(job_id)
            if not item or not proc or item.status != JobStatus.PAUSED:
                return False
            if resume_process(proc.pid):
                item.status = JobStatus.PROCESSING
                self._notify_item(item)
                return True
            return False

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            item = self._items.get(job_id)
            if not item:
                return False
            self._cancel_flags.add(job_id)
            if item.status == JobStatus.QUEUED:
                item.status = JobStatus.CANCELLED
                self._notify_item(item)
                return True
            proc = self._processes.get(job_id)
            if proc:
                if item.status == JobStatus.PAUSED:
                    resume_process(proc.pid)
                try:
                    proc.terminate()
                except OSError:
                    pass
                return True
        return False

    def cancel_all(self) -> None:
        with self._lock:
            job_ids = list(self._order)
        for jid in job_ids:
            self.cancel_job(jid)

    def retry_failed(self) -> int:
        retried = 0
        with self._lock:
            for jid in self._order:
                item = self._items[jid]
                if item.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                    self._cancel_flags.discard(jid)
                    item.status = JobStatus.QUEUED
                    item.error_message = ""
                    item.progress = JobProgress()
                    retried += 1
                    self._notify_item(item)
        if retried > 0:
            self.start()
        return retried

    def _dispatch_loop(self) -> None:
        while not self._shutdown:
            next_item: Optional[QueueItem] = None
            with self._lock:
                if self._active_workers < self._max_concurrent:
                    for jid in self._order:
                        cand = self._items[jid]
                        if cand.status == JobStatus.QUEUED and jid not in self._cancel_flags:
                            cand.status = JobStatus.VALIDATING
                            self._active_workers += 1
                            next_item = cand
                            break
                any_pending = any(
                    self._items[jid].status == JobStatus.QUEUED for jid in self._order
                )
                workers = self._active_workers

            if next_item is not None:
                worker = threading.Thread(
                    target=self._execute_job,
                    args=(next_item,),
                    name=f"PocketVideoWorker-{next_item.spec.job_id}",
                    daemon=True,
                )
                worker.start()
                continue

            if not any_pending and workers == 0:
                break
            time.sleep(0.05)

    def _execute_job(self, item: QueueItem) -> None:
        job_id = item.spec.job_id
        started_monotonic = time.monotonic()
        plan: Optional[PlannedEncodeExecution] = None
        resource_ctrl = ProcessResourceController(item.spec.resource_limits)

        try:
            self._notify_item(item)
            if item.input_metadata is None:
                item.input_metadata = inspect_media(item.spec.input_path, self.binaries)

            plan = build_encode_plan(
                item.spec, item.input_metadata, self.binaries, self.work_dir
            )
            item.warnings = list(plan.warnings)
            total_passes = len(plan.passes)
            stderr_accum: list[str] = []

            for pass_idx, cmd in enumerate(plan.passes, start=1):
                if job_id in self._cancel_flags:
                    raise InterruptedError("Job cancelled by user.")

                item.status = (
                    JobStatus.PASS_1 if (total_passes == 2 and pass_idx == 1) else JobStatus.PROCESSING
                )
                self._notify_item(item)

                parser = FFmpegProgressParser(
                    duration_seconds=item.input_metadata.duration_seconds,
                    pass_number=pass_idx,
                    total_passes=total_passes,
                    start_time=started_monotonic,
                )

                with subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=_get_CreationFlags(),
                ) as proc:
                    with self._lock:
                        self._processes[job_id] = proc
                        item.active_pid = proc.pid

                    applied_res = resource_ctrl.attach_to_process(proc)
                    item.resource_summary = resource_ctrl.status_summary
                    logger.info("Started FFmpeg pass %d/%d: %s", pass_idx, total_passes, applied_res)

                    pass_stderr: list[str] = []

                    def _read_stderr(pipe: object, sink: list[str]) -> None:
                        try:
                            for err_line in pipe:  # type: ignore[union-attr]
                                sink.append(err_line)
                        except Exception:
                            pass

                    stderr_thread = threading.Thread(
                        target=_read_stderr, args=(proc.stderr, pass_stderr), daemon=True
                    )
                    stderr_thread.start()

                    assert proc.stdout is not None
                    for out_line in proc.stdout:
                        if job_id in self._cancel_flags:
                            try:
                                proc.terminate()
                            except OSError:
                                pass
                            break
                        prog = parser.feed_line(out_line)
                        if prog:
                            item.progress = prog
                            self._notify_item(item)

                    proc.wait(timeout=30)
                    stderr_thread.join(timeout=5)
                    rc = proc.returncode

                stderr_accum.extend(pass_stderr)

                with self._lock:
                    self._processes.pop(job_id, None)
                    item.active_pid = None

                if job_id in self._cancel_flags:
                    raise InterruptedError("Job cancelled by user.")

                if rc != 0:
                    tail = "".join(pass_stderr[-15:]).strip()
                    raise RuntimeError(
                        f"FFmpeg pass {pass_idx} failed (exit code {rc}): {tail}"
                    )

            item.status = JobStatus.VERIFYING
            self._notify_item(item)

            if not plan.temp_output_path.exists() or plan.temp_output_path.stat().st_size == 0:
                raise RuntimeError("Output file was not created or is empty (0 bytes).")

            output_meta = inspect_media(plan.temp_output_path, self.binaries)
            if not output_meta.video_streams:
                raise RuntimeError("Output verification failed: missing readable video stream.")

            if item.input_metadata.duration_seconds > 1.0:
                dur_diff = abs(output_meta.duration_seconds - item.input_metadata.duration_seconds)
                if dur_diff > max(2.5, item.input_metadata.duration_seconds * 0.15):
                    raise RuntimeError(
                        f"Output verification failed: output duration ({output_meta.duration_seconds:.1f}s) "
                        f"diverges from input ({item.input_metadata.duration_seconds:.1f}s)."
                    )

            plan.final_output_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(plan.temp_output_path, plan.final_output_path)

            elapsed_total = max(0.01, time.monotonic() - started_monotonic)
            orig_size = item.input_metadata.file_size_bytes
            out_size = plan.final_output_path.stat().st_size
            saved_bytes = orig_size - out_size
            reduction_pct = ((orig_size - out_size) / max(1, orig_size)) * 100.0

            in_v = item.input_metadata.primary_video
            out_v = output_meta.primary_video

            comparison = JobResultComparison(
                job_id=job_id,
                input_path=item.spec.input_path,
                output_path=plan.final_output_path,
                operation_mode=plan.effective_mode,
                original_size_bytes=orig_size,
                output_size_bytes=out_size,
                space_saved_bytes=saved_bytes,
                reduction_percent=round(reduction_pct, 2),
                original_resolution=in_v.resolution_label if in_v else "unknown",
                output_resolution=out_v.resolution_label if out_v else "unknown",
                original_codec=in_v.codec_name if in_v else "unknown",
                output_codec=out_v.codec_name if out_v else "unknown",
                processing_duration_seconds=round(elapsed_total, 2),
                warnings=list(item.warnings),
            )

            item.result = comparison
            item.progress.percent = 100.0
            item.status = JobStatus.COMPLETED
            item.detailed_log = "".join(stderr_accum)
            self._notify_item(item)
            if self.on_job_completed:
                self.on_job_completed(comparison)

        except InterruptedError:
            item.status = JobStatus.CANCELLED
            item.error_message = "Cancelled by user."
            self._notify_item(item)
        except Exception as exc:
            logger.exception("Job %s failed: %s", job_id, exc)
            item.status = JobStatus.FAILED
            item.error_message = str(exc)
            self._notify_item(item)
        finally:
            resource_ctrl.close()
            if plan is not None:
                if plan.temp_output_path.exists():
                    try:
                        plan.temp_output_path.unlink()
                    except OSError:
                        pass
                cleanup_twopass_logs(plan.passlogfile_prefix)
            with self._lock:
                self._processes.pop(job_id, None)
                self._active_workers = max(0, self._active_workers - 1)
            self._notify_summary()

    def _notify_item(self, item: QueueItem) -> None:
        if self.on_job_updated:
            try:
                self.on_job_updated(item)
            except Exception:
                pass
        self._notify_summary()

    def _notify_summary(self) -> None:
        if not self.on_queue_summary:
            return
        with self._lock:
            all_items = [self._items[jid] for jid in self._order if jid in self._items]
        if not all_items:
            self.on_queue_summary(0.0, 0, 0)
            return
        total = len(all_items)
        completed = sum(1 for x in all_items if x.status == JobStatus.COMPLETED)
        avg_pct = sum(x.progress.percent for x in all_items) / total
        try:
            self.on_queue_summary(round(avg_pct, 1), completed, total)
        except Exception:
            pass
