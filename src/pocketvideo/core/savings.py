"""Durable, shareable lifetime compression savings independent of source files.

Job history stores *verified before/after byte counts at export time*; it never
reopens files to calculate the metrics. The lifetime ledger is independent of
the visible history window and uses stable job IDs for idempotency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json

from pocketvideo.core.models import JobResultComparison


@dataclass
class SavingsStats:
    completed_jobs: int = 0
    original_bytes: int = 0
    output_bytes: int = 0
    smaller_jobs: int = 0
    larger_jobs: int = 0
    processed_ids: list[str] = field(default_factory=list, repr=False)

    @property
    def net_saved_bytes(self) -> int:
        return self.original_bytes - self.output_bytes

    @property
    def reduction_percent(self) -> float:
        """Weighted compression ratio, never the sum/average of file percentages."""
        return 100.0 * self.net_saved_bytes / self.original_bytes if self.original_bytes else 0.0

    def add(self, result: JobResultComparison) -> bool:
        """Count a verified completed job once; requires NO file to exist."""
        key = str(result.job_id).strip()
        if not key:
            # Migration compatibility for an old exported history without IDs.
            stable = json.dumps(
                [str(result.input_path), str(result.output_path),
                 result.original_size_bytes, result.output_size_bytes,
                 result.completed_at_epoch], ensure_ascii=False
            )
            key = "legacy:" + hashlib.sha256(stable.encode("utf-8")).hexdigest()
        if key in self.processed_ids:
            return False
        original = int(result.original_size_bytes)
        output = int(result.output_size_bytes)
        if original < 0 or output < 0:
            return False
        self.processed_ids.append(key)
        self.completed_jobs += 1
        self.original_bytes += original
        self.output_bytes += output
        if output < original:
            self.smaller_jobs += 1
        elif output > original:
            self.larger_jobs += 1
        return True

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "completed_jobs": self.completed_jobs,
            "original_bytes": self.original_bytes,
            "output_bytes": self.output_bytes,
            "smaller_jobs": self.smaller_jobs,
            "larger_jobs": self.larger_jobs,
            "processed_ids": self.processed_ids,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "SavingsStats":
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("Unsupported statistics data")
        ids = raw.get("processed_ids")
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ValueError("Invalid counted-job IDs")
        fields = ("completed_jobs", "original_bytes", "output_bytes",
                  "smaller_jobs", "larger_jobs")
        values = {name: int(raw.get(name, 0)) for name in fields}
        if any(v < 0 for v in values.values()):
            raise ValueError("Negative cumulative statistics")
        if values["completed_jobs"] != len(ids):
            raise ValueError("Stats count differs from processed IDs")
        return cls(**values, processed_ids=list(dict.fromkeys(ids)))


def share_summary(stats: SavingsStats, language: str, bytes_label: str) -> str:
    """Share-ready text that does not claim storage is freed while originals remain."""
    pct = stats.reduction_percent
    if language == "en":
        return (
            f"PocketVideo • My compression results 🎬\n"
            f"{stats.completed_jobs:,} files processed • Net reduction: "
            f"{bytes_label} ({pct:+.1f}%)\n"
            "“More space, more possibilities.”\n"
            "Based on before/after file sizes; physical free space depends "
            "on whether originals are retained.\n"
            "https://github.com/Apichetfirm/PocketVideo"
        )
    return (
        "PocketVideo • สถิติการบีบอัดของฉัน 🎬\n"
        f"จัดการแล้ว {stats.completed_jobs:,} ไฟล์ • ลดขนาดสุทธิ "
        f"{bytes_label} ({pct:+.1f}%)\n"
        "“พื้นที่มากขึ้น ชีวิตก็มีความเป็นไปได้มากขึ้น”\n"
        "คำนวณจากขนาดไฟล์ก่อน–หลัง; พื้นที่ว่างจริงขึ้นกับ"
        "การเก็บหรือลบต้นฉบับ\n"
        "https://github.com/Apichetfirm/PocketVideo"
    )
