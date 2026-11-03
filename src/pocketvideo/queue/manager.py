"""Asynchronous batch processing queue manager with output verification and safe cleanup."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import sys
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional

from pocketvideo.core.models import (
    OutputPolicy,
    EncodingJobSpec,
    OperationMode,
    JobProgress,
    JobResultComparison,
    JobStatus,
    MediaMetadata,
)
from pocketvideo.media.binaries import BinaryPaths, _get_CreationFlags
from pocketvideo.queue.persistence import QueueJournal, serialize_spec
from pocketvideo.media.encoder import (
    FFmpegProgressParser,
    PlannedEncodeExecution,
    build_encode_plan,
    cleanup_twopass_logs,
)
from pocketvideo.media.probe import inspect_media
from pocketvideo.media.images import (
    SUPPORTED_IMAGE_EXTENSIONS, inspect_image, encode_image,
)
from pocketvideo.core.output_safety import (
    StorageDisconnectedError, confirm_source, ensure_storage,
    promote_verified_output,
)
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
    """Scan a directory for supported video AND still images, sorted by name."""
    if not folder.exists() or not folder.is_dir():
        return []
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    matches = [
        p.resolve()
        for p in iterator
        if p.is_file()
        and p.suffix.lower() in (SUPPORTED_VIDEO_EXTENSIONS | SUPPORTED_IMAGE_EXTENSIONS)
        and not p.name.startswith((".pocketvideo_tmp_", ".pocketvideo_source_stage_"))
        and ".pocketvideo_backup_" not in p.name
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


class LargerOutputSkipped(RuntimeError):
    """User opted to preserve the smaller original instead of a larger output."""


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
        journal_path: Optional[Path] = None,
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
        self._storage_paused: bool = False
        self._journal = QueueJournal(journal_path) if journal_path else None
        self._journal_lock = threading.RLock()
        self._last_journal_signature: tuple[tuple[str, str], ...] = ()
        if self._journal:
            # Restore specs only; never silently restart work on launch.
            for spec in self._journal.load():
                if spec.job_id in self._items:
                    continue
                item = QueueItem(spec=spec, status=JobStatus.FAILED,
                                 error_message="Recovered interrupted job. Verify external drives, then choose Retry Failed.")
                self._items[spec.job_id] = item
                self._order.append(spec.job_id)
            self._last_journal_signature = self._pending_signature()

    def _pending_signature(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple((jid, self._items[jid].status.value)
                         for jid in self._order
                         if self._items[jid].status != JobStatus.COMPLETED)

    def _persist_queue(self) -> None:
        if not self._journal:
            return
        with self._journal_lock:
            sig = self._pending_signature()
            if sig == self._last_journal_signature:
                return
            with self._lock:
                pending = [(self._items[jid].spec, self._items[jid].status.value)
                           for jid, _ in sig]
            try:
                self._journal.save(pending)
                self._last_journal_signature = sig
            except OSError:
                logger.exception("Could not save queue recovery journal")

    def set_max_concurrent_jobs(self, count: int) -> None:
        with self._lock:
            self._max_concurrent = max(1, min(8, int(count)))

    def items(self) -> list[QueueItem]:
        with self._lock:
            return [self._items[jid] for jid in self._order if jid in self._items]

    def get_item(self, job_id: str) -> Optional[QueueItem]:
        with self._lock:
            return self._items.get(job_id)

    def is_idle(self) -> bool:
        """True only after every worker has exited, not merely after a 100% signal."""
        with self._lock:
            return self._active_workers == 0 and all(
                self._items[jid].status in (
                    JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
                ) for jid in self._order
            )

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
        """Retry failed jobs after reconnecting missing source/destination disks."""
        with self._lock:
            self._storage_paused = False
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
            if self._storage_paused:
                logger.warning("Queue halted after storage disconnect; reconnect and retry manually.")
                break
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
                item.input_metadata = (
                    inspect_image(item.spec.input_path)
                    if item.spec.media_kind == "image"
                    else inspect_media(item.spec.input_path, self.binaries)
                )
            confirm_source(item.spec.input_path, item.spec.source_fingerprint)
            try:
                ensure_storage(item.spec.input_path, item.spec.output_path.parent)
            except StorageDisconnectedError:
                self._storage_paused = True
                raise

            if item.spec.media_kind == "image":
                self._process_image_job(item, started_monotonic, resource_ctrl)
                return

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
            try:
                actual_output_path, preserved_backup = promote_verified_output(
                    plan.temp_output_path, plan.final_output_path,
                    item.spec.input_path, item.spec.output_policy,
                    item.spec.source_fingerprint,
                )
            except (OSError, StorageDisconnectedError):
                if not item.spec.input_path.is_file() or not plan.final_output_path.parent.is_dir():
                    self._storage_paused = True
                raise
            if preserved_backup:
                item.warnings.append(f"Previous file safely backed up at: {preserved_backup}")

            elapsed_total = max(0.01, time.monotonic() - started_monotonic)
            orig_size = item.input_metadata.file_size_bytes
            out_size = actual_output_path.stat().st_size
            saved_bytes = orig_size - out_size
            reduction_pct = ((orig_size - out_size) / max(1, orig_size)) * 100.0

            in_v = item.input_metadata.primary_video
            out_v = output_meta.primary_video

            comparison = JobResultComparison(
                job_id=job_id,
                input_path=item.spec.input_path,
                output_path=actual_output_path,
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
                source_backup_path=(
                    preserved_backup if item.spec.output_policy == OutputPolicy.REPLACE_SOURCE_BACKUP
                    else None
                ),
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
            if isinstance(exc, StorageDisconnectedError) or (
                not item.spec.input_path.is_file() or
                not item.spec.output_path.parent.is_dir()
            ):
                self._storage_paused = True
                exc = StorageDisconnectedError(
                    f"USB/HDD source or destination unavailable; reconnect drive and press Retry Failed. "
                    f"Original files were not intentionally deleted. Details: {exc}"
                )
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

    def _process_image_job(
        self, item: QueueItem, started_monotonic: float,
        resource_ctrl: ProcessResourceController,
    ) -> None:
        """Encode image in a child process so Windows CPU Job Object caps apply."""
        src = item.spec.input_path
        dst = item.spec.output_path
        staged = dst.with_name(f".pocketvideo_tmp_{item.spec.job_id}_{dst.name}")
        item.status = JobStatus.PROCESSING
        self._notify_item(item)
        try:
            # Separate process allows real Windows CPU hard caps, Pause/Resume
            # and Cancel for Pillow+libheif, rather than only FFmpeg videos.
            task_file = self.work_dir / f"image_{item.spec.job_id}.json"
            task_file.write_text(json.dumps(serialize_spec(item.spec)), encoding="utf-8")
            try:
                if getattr(sys, "frozen", False):
                    command = [sys.executable, "--internal-image-job",
                               str(task_file), str(staged)]
                else:
                    command = [sys.executable, "-m", "pocketvideo.main",
                               "--internal-image-job", str(task_file), str(staged)]
                with subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=_get_CreationFlags(),
                ) as proc:
                    with self._lock:
                        self._processes[item.spec.job_id] = proc
                        item.active_pid = proc.pid
                    resource_ctrl.attach_to_process(proc)
                    item.resource_summary = resource_ctrl.status_summary
                    started = time.monotonic()
                    while proc.poll() is None:
                        if item.spec.job_id in self._cancel_flags:
                            proc.terminate()
                            raise InterruptedError("Image conversion cancelled.")
                        if (not src.is_file() or not dst.parent.is_dir()):
                            self._storage_paused = True
                            proc.terminate()
                            raise StorageDisconnectedError(
                                "USB/HDD disconnected during image encoding."
                            )
                        if time.monotonic() - started > 900:
                            proc.terminate()
                            raise TimeoutError("Image encode exceeded 15-minute safety limit.")
                        time.sleep(0.2)
                    _out, err = proc.communicate(timeout=15)
                    if proc.returncode != 0:
                        raise RuntimeError(
                            f"Image worker exited {proc.returncode}: {err[-1500:]}"
                        )
            finally:
                try:
                    task_file.unlink(missing_ok=True)
                except OSError:
                    pass
                with self._lock:
                    self._processes.pop(item.spec.job_id, None)
                    item.active_pid = None
            # Verify image decode and geometry before final atomic promotion.
            verified_image = inspect_image(staged)
            width, height = verified_image.image_width, verified_image.image_height
            warnings: list[str] = []
            item.status = JobStatus.VERIFYING
            item.warnings = warnings
            self._notify_item(item)
            if item.spec.job_id in self._cancel_flags:
                raise InterruptedError("Image conversion cancelled.")
            actual, backup = promote_verified_output(
                staged, dst, src, item.spec.output_policy,
                item.spec.source_fingerprint,
            )
            if backup is not None:
                item.warnings.append(f"Previous file safely backed up at: {backup}")
            original_size = item.input_metadata.file_size_bytes if item.input_metadata else src.stat().st_size
            new_size = actual.stat().st_size
            result = JobResultComparison(
                job_id=item.spec.job_id,
                input_path=src,
                output_path=actual,
                operation_mode=item.spec.operation_mode,
                original_size_bytes=original_size,
                output_size_bytes=new_size,
                space_saved_bytes=original_size - new_size,
                reduction_percent=round((original_size - new_size) * 100 / max(1, original_size), 2),
                original_resolution=f"{item.input_metadata.image_width}x{item.input_metadata.image_height}",
                output_resolution=f"{width}x{height}",
                original_codec=item.input_metadata.container_format,
                output_codec=item.spec.target_container,
                processing_duration_seconds=round(time.monotonic() - started_monotonic, 2),
                warnings=list(item.warnings),
                source_backup_path=(
                    backup if item.spec.output_policy == OutputPolicy.REPLACE_SOURCE_BACKUP
                    else None
                ),
            )
            item.result = result
            item.progress.percent = 100.0
            item.status = JobStatus.COMPLETED
            self._notify_item(item)
            if self.on_job_completed:
                self.on_job_completed(result)
        finally:
            try:
                if staged.is_file():
                    staged.unlink()
            except OSError:
                pass

    def _notify_item(self, item: QueueItem) -> None:
        self._persist_queue()
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
