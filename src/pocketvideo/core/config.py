"""Safe settings and history persistence with atomic file writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile
import sys
import threading
from typing import Optional

from pocketvideo.core.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from pocketvideo.core.savings import SavingsStats
from pocketvideo.core.models import (
    CompatibilityMode,
    JobResultComparison,
    PresetId,
    ProcessPriority,
    ResourceMode,
    ImagePreset,
    OutputPolicy,
    AfterExportAction,
)


def get_app_data_dir(custom_dir: Optional[Path] = None) -> Path:
    """Resolve user-local application data directory without hardcoded paths."""
    if custom_dir is not None:
        custom_dir.mkdir(parents=True, exist_ok=True)
        return custom_dir
    env_override = os.environ.get("POCKETVIDEO_APPDATA")
    if env_override:
        path = Path(env_override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    target = base / "PocketVideo"
    target.mkdir(parents=True, exist_ok=True)
    return target


def get_default_output_dir() -> Path:
    """Return default separate output directory (never overwrites source files)."""
    videos_dir = (
        Path.home() / "Movies" / "PocketVideo_Output"
        if sys.platform == "darwin"
        else Path.home() / "Videos" / "PocketVideo_Output"
    )
    return videos_dir


def configure_logging(app_data_dir: Optional[Path] = None) -> Path:
    """Configure rotating file + console logging for diagnostics."""
    base_dir = get_app_data_dir(app_data_dir)
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pocketvideo.log"

    root_logger = logging.getLogger("pocketvideo")
    if not root_logger.handlers:
        root_logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    return log_file


@dataclass
class AppSettings:
    """User preferences persisted across application launches."""
    language: str = DEFAULT_LANGUAGE
    theme: str = "dark"  # "dark" or "light"
    default_preset: str = PresetId.BEST_BALANCE.value
    compatibility_mode: str = CompatibilityMode.MODERN_ANDROID.value
    default_container: str = "mp4"
    default_image_format: str = "webp"
    default_image_preset: str = ImagePreset.BALANCED.value
    image_quality: int = 82
    filename_suffix: str = "_compressed"
    output_policy: str = OutputPolicy.KEEP_BOTH.value
    after_export_action: str = AfterExportAction.NONE.value
    skip_larger_outputs: bool = False
    output_directory: str = field(default_factory=lambda: str(get_default_output_dir()))
    resource_mode: str = ResourceMode.BALANCED.value
    cpu_hard_cap_percent: int = 75
    enable_cpu_hard_cap: bool = True
    cpu_threads: int = 0
    process_priority: str = ProcessPriority.BELOW_NORMAL.value
    max_concurrent_jobs: int = 1
    enable_gpu_encoding: bool = False
    preferred_gpu_encoder: str = "auto"
    queue_visible_columns: list[int] = field(default_factory=lambda: [1, 9, 10, 3, 4, 5, 7, 2, 8])
    queue_column_widths: dict[str, int] = field(default_factory=dict)
    queue_column_order: list[int] = field(default_factory=list)
    queue_sidebar_width: int = 210
    preserve_metadata: bool = True
    preserve_subtitles: bool = True
    custom_ffmpeg_path: str = ""
    custom_ffprobe_path: str = ""

    def validate(self) -> None:
        if self.language not in SUPPORTED_LANGUAGES:
            self.language = DEFAULT_LANGUAGE
        if self.theme not in ("dark", "light"):
            self.theme = "dark"
        self.cpu_hard_cap_percent = max(10, min(100, int(self.cpu_hard_cap_percent)))
        self.cpu_threads = max(0, min(128, int(self.cpu_threads)))
        self.max_concurrent_jobs = max(1, min(8, int(self.max_concurrent_jobs)))
        self.image_quality = max(1, min(100, int(self.image_quality)))
        self.skip_larger_outputs = bool(self.skip_larger_outputs)
        if self.preferred_gpu_encoder not in ("auto", "nvenc", "qsv", "amf", "videotoolbox"):
            self.preferred_gpu_encoder = "auto"
        allowed_columns = set(range(11))
        if not isinstance(self.queue_visible_columns, list):
            self.queue_visible_columns = [1, 9, 10, 3, 4, 5, 7, 2, 8]
        self.queue_visible_columns = [
            int(x) for x in self.queue_visible_columns
            if isinstance(x, int) and x in allowed_columns
        ] or [1, 9, 10, 3, 4, 5, 7, 2, 8]
        # Logical index 0 (job ID) remains stored even when hidden, so all
        # underlying queue/selection actions remain correct.
        if not isinstance(self.queue_column_widths, dict):
            self.queue_column_widths = {}
        self.queue_column_widths = {
            str(k): max(55, min(1000, int(v)))
            for k, v in self.queue_column_widths.items()
            if str(k).isdigit() and int(k) in allowed_columns
            and isinstance(v, (int, float))
        }
        if not isinstance(self.queue_column_order, list):
            self.queue_column_order = []
        self.queue_column_order = [
            int(x) for x in self.queue_column_order
            if isinstance(x, int) and x in allowed_columns
        ]
        self.queue_sidebar_width = max(135, min(320, int(self.queue_sidebar_width)))
        if self.default_image_format not in ("jpeg", "png", "webp", "heic", "avif"):
            self.default_image_format = "webp"
        if self.default_image_preset not in {p.value for p in ImagePreset}:
            self.default_image_preset = ImagePreset.BALANCED.value
        if self.output_policy not in {p.value for p in OutputPolicy}:
            self.output_policy = OutputPolicy.KEEP_BOTH.value
        if self.after_export_action not in {p.value for p in AfterExportAction}:
            self.after_export_action = AfterExportAction.NONE.value
        from pocketvideo.core.output_safety import validate_suffix
        try:
            self.filename_suffix = validate_suffix(self.filename_suffix)
        except ValueError:
            self.filename_suffix = "_compressed"


class SettingsManager:
    """Manages atomic loading and saving of user settings and job history."""

    def __init__(self, app_data_dir: Optional[Path] = None) -> None:
        self.base_dir = get_app_data_dir(app_data_dir)
        self.settings_path = self.base_dir / "settings.json"
        self.history_path = self.base_dir / "history.json"
        self.savings_stats_path = self.base_dir / "savings_stats.json"
        self._history_lock = threading.RLock()
        self.settings = self.load_settings()

    def load_settings(self) -> AppSettings:
        if not self.settings_path.exists():
            return AppSettings()
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return AppSettings()
            valid_keys = AppSettings.__dataclass_fields__.keys()
            filtered = {k: v for k, v in raw.items() if k in valid_keys}
            settings = AppSettings(**filtered)
            settings.validate()
            return settings
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return AppSettings()

    def save_settings(self, settings: Optional[AppSettings] = None) -> None:
        if settings is not None:
            self.settings = settings
        self.settings.validate()
        self._atomic_write_json(self.settings_path, asdict(self.settings))

    def load_history(self) -> list[JobResultComparison]:
        """Read metadata snapshots; missing originals/outputs are NOT an error.

        A malformed legacy record must never hide all the other history rows.
        """
        with self._history_lock:
            if not self.history_path.exists():
                return []
            try:
                raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                return []
            if not isinstance(raw, list):
                return []
            entries: list[JobResultComparison] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    entries.append(JobResultComparison.from_dict(item))
                except (ValueError, TypeError, KeyError, OverflowError):
                    logging.getLogger("pocketvideo").warning(
                        "Skipped a malformed history entry, preserving other records"
                    )
            return entries

    def load_savings_stats(self) -> SavingsStats:
        """Read lifetime stats; backfill old history once without double count.

        History is a list of completed result snapshots. A separate cumulative
        ledger survives history cleanup/truncation. Both remain useful after
        original footage is deleted or USB disks are unplugged.
        """
        with self._history_lock:
            try:
                raw = json.loads(self.savings_stats_path.read_text(encoding="utf-8"))
                stats = SavingsStats.from_dict(raw)
                needs_save = False
            except (OSError, ValueError, TypeError, UnicodeError):
                stats = SavingsStats()
                needs_save = True
            for result in self.load_history():
                if stats.add(result):
                    needs_save = True
            if needs_save:
                self._atomic_write_json(self.savings_stats_path, stats.to_dict())
            return stats

    def append_history(self, result: JobResultComparison, max_entries: int = 2000) -> None:
        """Atomically store verified measurements, then reconcile lifetime totals."""
        with self._history_lock:
            history = self.load_history()
            # Retries/callback races must not inflate totals or list duplicates.
            history = [entry for entry in history if entry.job_id != result.job_id]
            history.insert(0, result)
            history = history[:max_entries]
            self._atomic_write_json(
                self.history_path, [item.to_dict() for item in history]
            )
            self.load_savings_stats()

    def clear_history(self) -> None:
        """Clear visible history only. Lifetime savings remain as a durable stat."""
        with self._history_lock:
            # Reconcile first, otherwise a first-time migration would lose
            # successful jobs that were only present in older history.json.
            self.load_savings_stats()
            self._atomic_write_json(self.history_path, [])

    @staticmethod
    def _atomic_write_json(target_path: Path, payload: object) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target_path.parent), prefix=".tmp_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target_path)
        except Exception:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise
