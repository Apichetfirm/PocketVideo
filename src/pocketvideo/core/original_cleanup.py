"""Safety gate for opt-in removal of an original AFTER verified export.

Only works with in-session jobs carrying the source fingerprint captured at
enqueue time. Physical removal is handled by the UI through the OS Trash.
"""
from __future__ import annotations

import os
from pathlib import Path

from pocketvideo.core.models import JobResultComparison
from pocketvideo.core.output_safety import confirm_source


def verified_original_for_trash(
    result: JobResultComparison,
    expected_fingerprint: tuple[int, int, int, int] | None,
) -> Path:
    if expected_fingerprint is None:
        raise ValueError("Cannot verify original identity; re-import before deleting.")
    original = result.source_backup_path or result.input_path
    output = result.output_path
    if not original.is_file() or not output.is_file():
        raise FileNotFoundError("Original or verified output is no longer available.")
    if original.resolve() == output.resolve() or os.path.samefile(original, output):
        raise ValueError("Original and output point to the same file.")
    if output.stat().st_size <= 0 or output.stat().st_size != result.output_size_bytes:
        raise ValueError("Export has changed or is empty; original will be kept.")
    confirm_source(original, expected_fingerprint)
    return original
