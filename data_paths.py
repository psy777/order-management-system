"""Centralized helpers for resolving the application's data directory."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

LOGGER = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent
DATA_ROOT = APP_ROOT / "data"
LEGACY_DATA_ROOT = APP_ROOT.parent / "data"

_migration_attempted = False

def ensure_data_root() -> Path:
    """Return the canonical data root, creating and migrating as needed."""
    global _migration_attempted

    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    if not _migration_attempted:
        _migration_attempted = True
        _migrate_from_legacy()

    return DATA_ROOT


def _migrate_from_legacy() -> None:
    """Move files from the legacy parent /data directory into the new root."""
    if LEGACY_DATA_ROOT == DATA_ROOT:
        return
    if not LEGACY_DATA_ROOT.exists() or not LEGACY_DATA_ROOT.is_dir():
        return

    try:
        migrated = False
        for item in LEGACY_DATA_ROOT.iterdir():
            destination = DATA_ROOT / item.name
            if destination.exists():
                LOGGER.warning(
                    "Skipping legacy data item %s; destination already exists", item.name
                )
                continue
            shutil.move(str(item), str(destination))
            migrated = True

        if migrated:
            LOGGER.info(
                "Migrated data from legacy directory %s to %s", LEGACY_DATA_ROOT, DATA_ROOT
            )

        # Remove the empty legacy directory to avoid confusion
        try:
            next(LEGACY_DATA_ROOT.iterdir())
        except StopIteration:
            LEGACY_DATA_ROOT.rmdir()
    except Exception as exc:  # pragma: no cover - defensive logging
        LOGGER.warning("Failed to migrate legacy data directory: %s", exc)
