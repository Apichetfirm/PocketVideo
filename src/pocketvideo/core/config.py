"""Safe settings and history persistence with atomic file writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile
from typing import Optional

from pocketvideo.core.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from pocketvideo.core.models import (
    CompatibilityMode,
    JobResultComparison,
    PresetId,
    ProcessPriority,
    ResourceMode,
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
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    target = base / "PocketVideo"
    target.mkdir(parents=True, exist_ok=True)
    return target


def get_default_output_dir() -> Path:
    """Return default separate output directory (never overwrites source files)."""
    videos_dir = Path.home() / "Videos" / "PocketVideo_Output"
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
    output_directory: str = field(default_factory=lambda: str(get_default_output_dir()))
    resource_mode: str = ResourceMode.BALANCED.value
    cpu_hard_cap_percent: int = 75
    enable_cpu_hard_cap: bool = True
    cpu_threads: int = 0
    process_priority: str = ProcessPriority.BELOW_NORMAL.value
    max_concurrent_jobs: int = 1
    enable_gpu_encoding: bool = False
    preferred_gpu_encoder: str = "auto"
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


class SettingsManager:
    """Manages atomic loading and saving of user settings and job history."""

    def __init__(self, app_data_dir: Optional[Path] = None) -> None:
        self.base_dir = get_app_data_dir(app_data_dir)
        self.settings_path = self.base_dir / "settings.json"
        self.history_path = self.base_dir / "history.json"
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
        if not self.history_path.exists():
            return []
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            return [JobResultComparison.from_dict(item) for item in raw if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError, ValueError):
            return []

    def append_history(self, result: JobResultComparison, max_entries: int = 200) -> None:
        history = self.load_history()
        history.insert(0, result)
        history = history[:max_entries]
        self._atomic_write_json(self.history_path, [item.to_dict() for item in history])

    def clear_history(self) -> None:
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
            os.replace(tmp_name, target_path)
        except Exception:
            if os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise
