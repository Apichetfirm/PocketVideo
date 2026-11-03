"""Non-destructive output naming and transactional file promotion.

Originals are never touched until a verified output is ready. Explicit
replace-original keeps a recoverable backup and restores on failed rename.
Storage checks prevent continuing a queue against a disconnected USB volume.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import uuid

from pocketvideo.core.models import OutputPolicy


class StorageDisconnectedError(OSError):
    """A source or destination volume disappeared, or the source changed."""


def validate_suffix(value: str) -> str:
    if not isinstance(value, str) or len(value) > 48:
        raise ValueError("Suffix must be text up to 48 characters.")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value in (".", "..") or ".." in value:
        raise ValueError("Suffix contains forbidden Windows filename characters.")
    if value.endswith((" ", ".")):
        raise ValueError("Suffix cannot end in a space or period on Windows.")
    return value


def fingerprint(path: Path) -> tuple[int, int, int, int]:
    try:
        st = path.stat()
    except OSError as exc:
        raise StorageDisconnectedError(f"Source is unavailable (USB/HDD disconnected?): {path}") from exc
    if not path.is_file():
        raise StorageDisconnectedError(f"Input is not an accessible file: {path}")
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def confirm_source(path: Path, expected: tuple[int, int, int, int] | None) -> None:
    actual = fingerprint(path)
    if expected is not None and actual != expected:
        raise StorageDisconnectedError(
            f"Source was disconnected, replaced or modified while processing: {path}; original was not overwritten."
        )


def ensure_storage(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise StorageDisconnectedError(f"Source disk unavailable (USB/HDD disconnected): {source}")
    if not destination.exists() or not destination.is_dir():
        raise StorageDisconnectedError(f"Destination disk unavailable (USB/HDD disconnected): {destination}")


def make_output_path(
    source: Path,
    folder: Path,
    extension: str,
    suffix: str,
    policy: OutputPolicy,
    reserved: set[Path] | None = None,
    job_id: str | None = None,
) -> Path:
    """Choose a path without writing to it; name is reserved at commit time.

    For replace_original, encode to a sibling staging path, *never* to source.
    """
    suffix = validate_suffix(suffix)
    extension = extension.lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{2,8}", extension):
        raise ValueError("Invalid output extension.")
    source = source.resolve()
    folder = folder.expanduser().resolve()
    reserved = reserved or set()
    if policy == OutputPolicy.REPLACE_SOURCE_BACKUP:
        if source.suffix.lower().lstrip(".") != extension and not (
            extension == "jpeg" and source.suffix.lower() in (".jpg", ".jpeg")
        ):
            raise ValueError("Replacing original requires the same file format. Select Keep Both to convert extensions.")
        return source.with_name(f".pocketvideo_source_stage_{job_id or uuid.uuid4().hex}.{extension}")
    filename = f"{source.stem}{suffix}.{extension}"
    candidate = folder / filename
    if policy == OutputPolicy.KEEP_BOTH:
        index = 1
        while candidate == source or candidate.exists() or candidate in reserved:
            candidate = folder / f"{source.stem}{suffix}_{index}.{extension}"
            index += 1
    elif candidate == source:
        raise ValueError("You must explicitly select Replace Original with Backup to overwrite a source.")
    if candidate in reserved:
        raise ValueError("Multiple queued files resolve to the same output path; use Keep Both.")
    return candidate


def backup_path(path: Path) -> Path:
    for _ in range(100):
        name = f"{path.stem}.pocketvideo_backup_{uuid.uuid4().hex[:8]}{path.suffix}"
        target = path.with_name(name)
        if not target.exists():
            return target
    raise OSError("Unable to choose an unused backup filename.")


def promote_verified_output(
    staged_file: Path,
    destination: Path,
    source: Path,
    policy: OutputPolicy,
    expected_source: tuple[int, int, int, int] | None = None,
) -> tuple[Path, Path | None]:
    """Promote a verified temporary result, preserving recoverable originals.

    A backup is retained for every destructive replacement; no deletion after success.
    Keep-both/skip use exclusive destination creation rather than replacing an
    unrelated file if another process wins the naming race.
    """
    staged_file = staged_file.resolve()
    destination = destination.resolve()
    source = source.resolve()
    confirm_source(source, expected_source)
    ensure_storage(source, destination.parent)
    if not staged_file.is_file() or staged_file.stat().st_size <= 0:
        raise OSError("Verified output is missing; possible disk disconnect.")
    # Best-effort flush file data before metadata promotion; required when
    # replacing an original on an external drive. Cannot guarantee survival
    # of faulty USB firmware or power loss without physical hardware testing.
    with staged_file.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())

    target = source if policy == OutputPolicy.REPLACE_SOURCE_BACKUP else destination
    if policy != OutputPolicy.REPLACE_SOURCE_BACKUP and target == source:
        raise ValueError("Source overwrite requires the explicit backup policy.")
    if policy in (OutputPolicy.KEEP_BOTH, OutputPolicy.SKIP_EXISTING):
        # Claim destination atomically, don't race other processes and overwrite.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
        try:
            os.replace(staged_file, target)
        except OSError:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        return target, None

    # Both overwrite modes retain the previous file as a distinct backup.
    if not target.exists():
        if policy == OutputPolicy.REPLACE_SOURCE_BACKUP:
            raise StorageDisconnectedError(f"Original missing; USB drive may be disconnected: {target}")
        os.replace(staged_file, target)
        return target, None
    preserved = backup_path(target)
    os.replace(target, preserved)
    try:
        os.replace(staged_file, target)
    except OSError:
        # A disconnect can also prevent rollback; keep the backup untouched.
        try:
            if not target.exists():
                os.replace(preserved, target)
        except OSError:
            pass
        raise
    return target, preserved
