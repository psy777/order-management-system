"""Command-line interface for upgrading the FireCoast installation."""
from __future__ import annotations

from services.upgrade import main


if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
