"""Crash-safe queue journal.

Only active/noncompleted specs are written. On restart they are *never* run
automatically: the user explicitly clicks Retry Failed after checking drives.
"""
from __future__ import annotations

from dataclasses import asdict, fields
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from pocketvideo.core.models import (
    AdvancedEncodingOptions, CompatibilityMode, EncodingJobSpec, ImagePreset,
    OperationMode, OutputPolicy, PresetId, ProcessPriority, ResourceLimits,
    ResourceMode,
)


def _simple(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _simple(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_simple(x) for x in value]
    return value


def serialize_spec(spec: EncodingJobSpec) -> dict[str, Any]:
    return _simple(asdict(spec))


def deserialize_spec(data: dict[str, Any]) -> EncodingJobSpec:
    if not isinstance(data, dict):
        raise ValueError("Invalid saved queue job")
    valid = {f.name for f in fields(EncodingJobSpec)}
    x = {k: v for k, v in data.items() if k in valid}
    x["input_path"] = Path(x["input_path"])
    x["output_path"] = Path(x["output_path"])
    for k, enum_type in (
        ("operation_mode", OperationMode), ("preset_id", PresetId),
        ("compatibility_mode", CompatibilityMode), ("image_preset", ImagePreset),
        ("output_policy", OutputPolicy),
    ):
        if k in x:
            x[k] = enum_type(x[k])
    limits = x.get("resource_limits", {})
    if isinstance(limits, dict):
        permitted = {f.name for f in fields(ResourceLimits)}
        limits = {k: v for k, v in limits.items() if k in permitted}
        if "mode" in limits:
            limits["mode"] = ResourceMode(limits["mode"])
        if "process_priority" in limits:
            limits["process_priority"] = ProcessPriority(limits["process_priority"])
        x["resource_limits"] = ResourceLimits(**limits)
    opts = x.get("advanced", {})
    if isinstance(opts, dict):
        permitted = {f.name for f in fields(AdvancedEncodingOptions)}
        x["advanced"] = AdvancedEncodingOptions(
            **{k: v for k, v in opts.items() if k in permitted}
        )
    if x.get("source_fingerprint") is not None:
        fp = x["source_fingerprint"]
        if len(fp) != 4:
            raise ValueError("Invalid source fingerprint")
        x["source_fingerprint"] = tuple(int(i) for i in fp)
    spec = EncodingJobSpec(**x)
    if not spec.input_path.is_absolute() or not spec.output_path.is_absolute():
        raise ValueError("Saved queue paths must be absolute")
    return spec


class QueueJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def save(self, entries: list[tuple[EncodingJobSpec, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = {
            "schema_version": 1,
            "pending": [{"spec": serialize_spec(spec), "status": status}
                        for spec, status in entries],
        }
        fd, tmp = tempfile.mkstemp(prefix=".queue_", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(content, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def load(self) -> list[EncodingJobSpec]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1 or not isinstance(data.get("pending"), list):
                return []
            recovered: list[EncodingJobSpec] = []
            for entry in data["pending"][:10000]:
                try:
                    recovered.append(deserialize_spec(entry["spec"]))
                except (KeyError, ValueError, TypeError):
                    continue
            return recovered
        except (OSError, ValueError, TypeError):
            return []
