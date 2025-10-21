"""Helpers for exporting and restoring FireNotes data backups."""
from __future__ import annotations

import io
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zipfile import BadZipFile, ZipFile, ZipInfo, ZIP_DEFLATED

from data_paths import ensure_data_root

__all__ = [
    "BackupError",
    "create_backup_archive",
    "restore_backup_from_stream",
]


class BackupError(RuntimeError):
    """Raised when a backup export or import fails due to user correctable errors."""


_EXCLUDED_TOP_LEVEL = {"temp_backups", "data_temp_backup"}
_METADATA_DIRS = {"__MACOSX"}


def create_backup_archive(destination_dir: Optional[Path] = None) -> Path:
    """Create a ZIP archive of the data directory.

    Parameters
    ----------
    destination_dir:
        Optional directory where the archive should be created. If omitted, a
        ``temp_backups`` directory next to the data root is used.
    """

    data_root = ensure_data_root()
    if not data_root.exists() or not data_root.is_dir():
        raise BackupError("Data directory not found.")

    if destination_dir is None:
        destination_dir = data_root.parent / "temp_backups"
    destination_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = destination_dir / f"backup_{timestamp}.zip"

    with ZipFile(archive_path, mode="w", compression=ZIP_DEFLATED) as archive:
        for entry in sorted(_iter_backup_entries(data_root)):
            archive.write(entry, entry.relative_to(data_root).as_posix())

    if archive_path.stat().st_size == 0:
        archive_path.unlink(missing_ok=True)
        raise BackupError("Backup archive was empty.")

    return archive_path


def restore_backup_from_stream(stream: io.BufferedIOBase) -> None:
    """Restore the data directory from a ZIP archive stream."""

    data_root = ensure_data_root()
    parent = data_root.parent
    temp_backup_dir = parent / "data_temp_backup"
    restore_dir = parent / "data_restore_tmp"

    _ensure_deleted(temp_backup_dir)
    _ensure_deleted(restore_dir)

    had_existing_data = False
    if data_root.exists():
        if any(data_root.iterdir()):
            shutil.copytree(data_root, temp_backup_dir)
            had_existing_data = True
        else:
            temp_backup_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_backup_dir.mkdir(parents=True, exist_ok=True)

    restore_dir.mkdir(parents=True, exist_ok=True)

    try:
        _extract_archive(stream, restore_dir)
        extracted_root = _resolve_extracted_root(restore_dir)

        if data_root.exists():
            shutil.rmtree(data_root)
        data_root.mkdir(parents=True, exist_ok=True)

        for item in extracted_root.iterdir():
            target = data_root / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)

    except BackupError:
        _restore_from_temp_backup(data_root, temp_backup_dir, had_existing_data)
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        _restore_from_temp_backup(data_root, temp_backup_dir, had_existing_data)
        raise BackupError("Failed to restore backup.") from exc
    else:
        _ensure_deleted(temp_backup_dir)
    finally:
        _ensure_deleted(restore_dir)


def _iter_backup_entries(data_root: Path) -> Iterable[Path]:
    for entry in data_root.rglob("*"):
        if not entry.is_file():
            continue
        relative = entry.relative_to(data_root)
        if relative.parts and relative.parts[0] in _EXCLUDED_TOP_LEVEL:
            continue
        yield entry


def _ensure_deleted(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _extract_archive(stream: io.BufferedIOBase, destination: Path) -> None:
    try:
        stream.seek(0)
    except (AttributeError, OSError):  # pragma: no cover - best effort
        pass

    try:
        archive = ZipFile(stream)
    except BadZipFile as exc:
        raise BackupError("The uploaded file is not a valid ZIP archive.") from exc

    with archive:
        has_files = False
        for info in archive.infolist():
            normalized = _normalize_member(info)
            if normalized is None:
                continue
            has_files = True
            output_path = destination / normalized
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if info.is_dir():
                output_path.mkdir(parents=True, exist_ok=True)
                continue
            with archive.open(info) as src, open(output_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

        if not has_files:
            raise BackupError("The provided backup archive was empty.")


def _normalize_member(info: ZipInfo) -> Optional[Path]:
    name = info.filename
    if not name:
        return None
    path = Path(name)
    parts = []
    for part in path.parts:
        if not part or part == ".":
            continue
        if part in _METADATA_DIRS:
            return None
        if part == "..":
            raise BackupError("Backup archive contains unsafe paths.")
        parts.append(part)
    if not parts:
        return None
    if parts[0] in _EXCLUDED_TOP_LEVEL:
        return None
    return Path(*parts)


def _resolve_extracted_root(restore_dir: Path) -> Path:
    candidates = [item for item in restore_dir.iterdir() if item.name not in _METADATA_DIRS]
    if len(candidates) == 1 and candidates[0].is_dir():
        return candidates[0]
    return restore_dir


def _restore_from_temp_backup(data_root: Path, temp_backup_dir: Path, had_existing: bool) -> None:
    try:
        if data_root.exists():
            shutil.rmtree(data_root)
        if had_existing and temp_backup_dir.exists():
            shutil.move(str(temp_backup_dir), str(data_root))
        elif not had_existing:
            data_root.mkdir(parents=True, exist_ok=True)
    finally:
        if temp_backup_dir.exists() and not had_existing:
            _ensure_deleted(temp_backup_dir)
