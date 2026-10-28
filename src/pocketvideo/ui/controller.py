"""Application presentation controller bridging UI actions, media inspection, and queue execution."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from pocketvideo.core.config import AppSettings, SettingsManager
from pocketvideo.core.i18n import Translator
from pocketvideo.core.models import (
    AdvancedEncodingOptions,
    CompatibilityMode,
    EncodingJobSpec,
    JobResultComparison,
    MediaMetadata,
    OperationMode,
    PresetId,
    ProcessPriority,
    ResourceLimits,
    ResourceMode,
    TargetSizePlan,
)
from pocketvideo.media.binaries import BinaryPaths, resolve_binaries
from pocketvideo.media.encoder import (
    extract_representative_frame,
    generate_sample_compression,
)
from pocketvideo.media.presets import (
    PRESET_CATALOG,
    calculate_target_size_plan,
    can_smart_remux,
)
from pocketvideo.media.probe import inspect_media
from pocketvideo.queue.manager import (
    QueueItem,
    QueueManager,
    build_safe_output_path,
    discover_videos_in_folder,
)
from pocketvideo.resources.job_object import preset_resource_limits


class AppController:
    """Stateful presenter/controller driving both the PySide6 UI and headless tests."""

    def __init__(
        self,
        settings_manager: Optional[SettingsManager] = None,
        binaries: Optional[BinaryPaths] = None,
        work_dir: Optional[Path] = None,
    ) -> None:
        self.settings_mgr = settings_manager or SettingsManager()
        self.settings: AppSettings = self.settings_mgr.settings
        self.translator = Translator(self.settings.language)

        self.binaries: Optional[BinaryPaths] = binaries
        self.binary_error: Optional[str] = None
        if self.binaries is None:
            try:
                self.binaries = resolve_binaries(
                    custom_ffmpeg=self.settings.custom_ffmpeg_path or None,
                    custom_ffprobe=self.settings.custom_ffprobe_path or None,
                )
            except Exception as exc:
                self.binary_error = str(exc)

        self.selected_files: list[MediaMetadata] = []
        self.active_preset: PresetId = PresetId(self.settings.default_preset)
        self.compatibility_mode: CompatibilityMode = CompatibilityMode(
            self.settings.compatibility_mode
        )
        self.target_container: str = self.settings.default_container
        self.target_size_mb: float = 250.0
        self.output_dir: Path = Path(self.settings.output_directory)
        self.advanced_options = AdvancedEncodingOptions()
        self.resource_limits = self._limits_from_settings()

        self.queue_manager: Optional[QueueManager] = None
        if self.binaries is not None:
            self.queue_manager = QueueManager(
                binaries=self.binaries,
                work_dir=work_dir,
                on_job_completed=self._on_job_completed,
            )

    def _limits_from_settings(self) -> ResourceLimits:
        mode = ResourceMode(self.settings.resource_mode)
        if mode in (ResourceMode.LIGHT, ResourceMode.BALANCED, ResourceMode.MAXIMUM):
            base = preset_resource_limits(mode)
        else:
            base = ResourceLimits(mode=ResourceMode.CUSTOM)
        base.cpu_hard_cap_percent = self.settings.cpu_hard_cap_percent
        base.enable_cpu_hard_cap = self.settings.enable_cpu_hard_cap
        base.cpu_threads = self.settings.cpu_threads
        base.process_priority = ProcessPriority(self.settings.process_priority)
        base.max_concurrent_jobs = self.settings.max_concurrent_jobs
        base.enable_gpu_encoding = self.settings.enable_gpu_encoding
        base.preferred_gpu_encoder = self.settings.preferred_gpu_encoder
        return base

    def set_language(self, language: str) -> None:
        self.translator.set_language(language)
        self.settings.language = language
        self.settings_mgr.save_settings()

    def set_theme(self, theme: str) -> None:
        self.settings.theme = theme
        self.settings_mgr.save_settings()

    def apply_resource_mode(self, mode: ResourceMode) -> ResourceLimits:
        limits = preset_resource_limits(mode)
        self.resource_limits = limits
        self.settings.resource_mode = mode.value
        self.settings.cpu_hard_cap_percent = limits.cpu_hard_cap_percent
        self.settings.enable_cpu_hard_cap = limits.enable_cpu_hard_cap
        self.settings.cpu_threads = limits.cpu_threads
        self.settings.process_priority = limits.process_priority.value
        self.settings.max_concurrent_jobs = limits.max_concurrent_jobs
        self.settings.enable_gpu_encoding = limits.enable_gpu_encoding
        self.settings_mgr.save_settings()
        if self.queue_manager:
            self.queue_manager.set_max_concurrent_jobs(limits.max_concurrent_jobs)
        return limits

    def update_resource_controls(
        self,
        *,
        cpu_hard_cap_percent: Optional[int] = None,
        cpu_threads: Optional[int] = None,
        max_concurrent_jobs: Optional[int] = None,
        enable_gpu_encoding: Optional[bool] = None,
    ) -> ResourceLimits:
        """Apply manual resource settings, persist them, and update queue concurrency."""
        cap = (
            self.resource_limits.cpu_hard_cap_percent
            if cpu_hard_cap_percent is None else max(10, min(100, int(cpu_hard_cap_percent)))
        )
        threads = (
            self.resource_limits.cpu_threads
            if cpu_threads is None else max(0, min(128, int(cpu_threads)))
        )
        concurrent = (
            self.resource_limits.max_concurrent_jobs
            if max_concurrent_jobs is None else max(1, min(8, int(max_concurrent_jobs)))
        )
        gpu = (
            self.resource_limits.enable_gpu_encoding
            if enable_gpu_encoding is None else bool(enable_gpu_encoding)
        )
        self.resource_limits = replace(
            self.resource_limits,
            mode=ResourceMode.CUSTOM,
            cpu_hard_cap_percent=cap,
            enable_cpu_hard_cap=cap < 100,
            cpu_threads=threads,
            max_concurrent_jobs=concurrent,
            enable_gpu_encoding=gpu,
        )
        self.settings.resource_mode = ResourceMode.CUSTOM.value
        self.settings.cpu_hard_cap_percent = cap
        self.settings.enable_cpu_hard_cap = cap < 100
        self.settings.cpu_threads = threads
        self.settings.max_concurrent_jobs = concurrent
        self.settings.enable_gpu_encoding = gpu
        self.settings_mgr.save_settings()
        if self.queue_manager:
            self.queue_manager.set_max_concurrent_jobs(concurrent)
        return self.resource_limits

    def add_files(self, paths: list[str | Path]) -> tuple[list[MediaMetadata], list[str]]:
        """Inspect and add media files to the current selection. Returns (added, errors)."""
        if self.binaries is None:
            return [], [self.binary_error or "FFmpeg/FFprobe binaries are not available."]
        added: list[MediaMetadata] = []
        errors: list[str] = []
        existing_paths = {m.file_path for m in self.selected_files}
        for raw_p in paths:
            p = Path(raw_p).resolve()
            if p in existing_paths:
                continue
            try:
                meta = inspect_media(p, self.binaries)
                self.selected_files.append(meta)
                existing_paths.add(p)
                added.append(meta)
            except Exception as exc:
                errors.append(f"{p.name}: {exc}")
        return added, errors

    def add_folder(self, folder: str | Path, recursive: bool = True) -> tuple[list[MediaMetadata], list[str]]:
        discovered = discover_videos_in_folder(Path(folder), recursive=recursive)
        return self.add_files([str(p) for p in discovered])

    def clear_selected_files(self) -> None:
        self.selected_files.clear()

    def evaluate_active_warnings(self) -> list[str]:
        """Collect contextual warnings for the currently selected files and preset."""
        warnings: list[str] = []
        t = self.translator.t
        preset_desc = PRESET_CATALOG[self.active_preset]
        if preset_desc.warning_key:
            warnings.append(t(preset_desc.warning_key))

        for meta in self.selected_files:
            if meta.already_efficient and meta.efficiency_warning_key:
                warnings.append(f"[{meta.file_path.name}] {t(meta.efficiency_warning_key)}")
            v = meta.primary_video
            if v and v.is_dolby_vision:
                warnings.append(f"[{meta.file_path.name}] {t('warn_dolby_vision')}")
            elif v and v.is_hdr:
                warnings.append(
                    f"[{meta.file_path.name}] {t('warn_hdr_detected', hdr_format=v.hdr_format_name)}"
                )

            if self.active_preset == PresetId.CUSTOM_SIZE:
                plan = self.preview_target_size_plan(meta)
                if plan.warning_message_key:
                    warnings.append(
                        f"[{meta.file_path.name}] {t(plan.warning_message_key, kbps=plan.required_video_kbps)}"
                    )
        return warnings

    def preview_target_size_plan(self, media: Optional[MediaMetadata] = None) -> TargetSizePlan:
        target_media = media or (self.selected_files[0] if self.selected_files else None)
        if target_media is None:
            raise ValueError("No media file selected for target size calculation.")
        return calculate_target_size_plan(
            target_media,
            target_size_mb=self.target_size_mb,
            compatibility_mode=self.compatibility_mode,
        )

    def check_conversion_mode(
        self, media: MediaMetadata, target_container: str
    ) -> tuple[OperationMode, str]:
        """Determine whether conversion uses Fast Smart Remux or Full Transcoding."""
        ok, explanation = can_smart_remux(media, target_container)
        if ok:
            return OperationMode.SMART_REMUX, explanation
        return OperationMode.CONVERT_TRANSCODE, explanation

    def enqueue_current_batch(
        self,
        operation_mode: OperationMode = OperationMode.COMPRESS,
        allow_smart_remux: bool = True,
        auto_start: bool = True,
    ) -> list[QueueItem]:
        """Create and enqueue jobs for all currently selected files."""
        if self.queue_manager is None:
            raise RuntimeError(self.binary_error or "QueueManager is unavailable.")
        if not self.selected_files:
            raise ValueError("No video files selected.")

        queued_items: list[QueueItem] = []
        for meta in self.selected_files:
            suffix = "_remux" if operation_mode == OperationMode.SMART_REMUX else "_compressed"
            out_path = build_safe_output_path(
                meta.file_path,
                self.output_dir,
                self.target_container,
                suffix_tag=suffix,
            )
            spec = EncodingJobSpec(
                input_path=meta.file_path,
                output_path=out_path,
                operation_mode=operation_mode,
                preset_id=self.active_preset,
                compatibility_mode=self.compatibility_mode,
                target_container=self.target_container,
                target_size_mb=self.target_size_mb if self.active_preset == PresetId.CUSTOM_SIZE else None,
                allow_smart_remux=allow_smart_remux,
                advanced=replace(self.advanced_options),
                resource_limits=replace(self.resource_limits),
            )
            item = self.queue_manager.enqueue(spec, input_metadata=meta, auto_start=auto_start)
            queued_items.append(item)
        return queued_items

    def generate_before_after_preview(
        self,
        media: MediaMetadata,
        preview_dir: Path,
        sample_duration: float = 2.5,
        on_progress: Optional[Callable[[object], None]] = None,
    ) -> tuple[Path, Path, Path]:
        """Run a short sample compression and extract before/after PNG frames."""
        if self.binaries is None:
            raise RuntimeError(self.binary_error or "FFmpeg binaries unavailable.")
        preview_dir.mkdir(parents=True, exist_ok=True)
        sample_out = preview_dir / f"sample_{media.file_path.stem}.{self.target_container}"
        spec = EncodingJobSpec(
            input_path=media.file_path,
            output_path=sample_out,
            operation_mode=OperationMode.COMPRESS,
            preset_id=self.active_preset,
            compatibility_mode=self.compatibility_mode,
            target_container=self.target_container,
            target_size_mb=self.target_size_mb,
            advanced=replace(self.advanced_options),
            resource_limits=replace(self.resource_limits),
        )
        return generate_sample_compression(
            spec=spec,
            media=media,
            binaries=self.binaries,
            sample_output_path=sample_out,
            sample_duration_seconds=sample_duration,
            progress_callback=on_progress,
        )

    def extract_single_frame(self, media: MediaMetadata, output_png: Path) -> Path:
        if self.binaries is None:
            raise RuntimeError(self.binary_error or "FFmpeg binaries unavailable.")
        return extract_representative_frame(
            media.file_path,
            output_png,
            self.binaries,
            duration_seconds=media.duration_seconds,
        )

    def _on_job_completed(self, comparison: JobResultComparison) -> None:
        self.settings_mgr.append_history(comparison)
