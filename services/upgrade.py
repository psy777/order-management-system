"""Utilities for upgrading the FireCoast installation in-place."""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Sequence

from data_paths import ensure_data_root
from services.backup import BackupError, create_backup_archive

LOGGER = logging.getLogger(__name__)


class CommandRunner(Protocol):
    """Callable protocol used to execute shell commands."""

    def __call__(
        self, args: Sequence[str], *, cwd: Optional[Path] = None
    ) -> subprocess.CompletedProcess[str]:
        ...


class UpgradeError(RuntimeError):
    """Raised when the upgrade process fails."""


@dataclass(frozen=True)
class UpgradeResult:
    """Structured results returned by :func:`perform_upgrade`."""

    backup_path: Path
    previous_revision: str
    current_revision: str


def perform_upgrade(
    remote: str = "origin",
    branch: str = "master",
    *,
    runner: Optional[CommandRunner] = None,
    install_dependencies: bool = True,
) -> UpgradeResult:
    """Upgrade the repository to the latest commit on ``remote/branch``.

    The function performs a safety backup of the ``data/`` directory, ensures
    the working tree is clean, fast-forwards to the requested branch, and
    optionally reinstalls dependencies from ``requirements.txt``.
    """

    repo_root = _resolve_repo_root()
    runner = runner or _run_command

    LOGGER.info("Checking repository status before upgrade")
    status_result = _run_with_error_handling(
        runner, ["git", "status", "--porcelain"], repo_root, "inspect repository status"
    )
    if status_result.stdout.strip():
        raise UpgradeError(
            "Uncommitted changes detected. Please commit or stash them before upgrading."
        )

    previous_revision = _get_revision(runner, repo_root, context="determine current revision")

    LOGGER.info("Creating data backup prior to upgrade")
    backup_path = _create_data_backup()

    LOGGER.info("Fetching latest commits from %s/%s", remote, branch)
    _run_with_error_handling(
        runner,
        ["git", "fetch", remote, branch],
        repo_root,
        f"fetch latest commits from {remote}/{branch}",
    )

    LOGGER.info("Checking out branch %s", branch)
    _run_with_error_handling(
        runner,
        ["git", "checkout", branch],
        repo_root,
        f"check out branch {branch}",
    )

    LOGGER.info("Resetting local branch to %s/%s", remote, branch)
    _run_with_error_handling(
        runner,
        ["git", "reset", "--hard", f"{remote}/{branch}"],
        repo_root,
        f"reset branch to {remote}/{branch}",
    )

    current_revision = _get_revision(
        runner, repo_root, context="determine upgraded revision"
    )

    if install_dependencies:
        LOGGER.info("Reinstalling Python dependencies")
        _install_dependencies(runner, repo_root)

    LOGGER.info(
        "Upgrade complete. Previous revision %s, new revision %s", previous_revision, current_revision
    )
    return UpgradeResult(
        backup_path=backup_path,
        previous_revision=previous_revision,
        current_revision=current_revision,
    )


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_command(args: Sequence[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_with_error_handling(
    runner: CommandRunner,
    args: Sequence[str],
    cwd: Path,
    action_description: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(args, cwd=cwd)
    except subprocess.CalledProcessError as exc:
        LOGGER.error("Failed to %s: %s", action_description, exc.stderr or exc.stdout)
        raise UpgradeError(f"Failed to {action_description}.") from exc


def _get_revision(
    runner: CommandRunner,
    cwd: Path,
    *,
    context: str,
) -> str:
    result = _run_with_error_handling(
        runner, ["git", "rev-parse", "HEAD"], cwd, context
    )
    return result.stdout.strip()


def _create_data_backup() -> Path:
    data_root = ensure_data_root()
    destination = data_root.parent / "upgrade_backups"
    destination.mkdir(parents=True, exist_ok=True)
    try:
        return create_backup_archive(destination)
    except BackupError as exc:  # pragma: no cover - defensive
        LOGGER.error("Backup creation failed: %s", exc)
        raise UpgradeError(f"Failed to create a data backup: {exc}") from exc


def _install_dependencies(runner: CommandRunner, repo_root: Path) -> None:
    requirements = repo_root / "requirements.txt"
    if not requirements.exists():
        LOGGER.info("requirements.txt not found; skipping dependency installation")
        return

    _run_with_error_handling(
        runner,
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirements),
        ],
        repo_root,
        "install Python dependencies",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry-point used by ``upgrade.py`` for a small CLI."""

    parser = argparse.ArgumentParser(description="Upgrade FireCoast to the latest master build.")
    parser.add_argument("--remote", default="origin", help="Git remote to pull from (default: origin)")
    parser.add_argument(
        "--branch",
        default="master",
        help="Git branch to fast-forward to (default: master)",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="Skip reinstalling dependencies from requirements.txt",
    )
    args = parser.parse_args(argv)

    try:
        result = perform_upgrade(
            remote=args.remote,
            branch=args.branch,
            install_dependencies=not args.skip_deps,
        )
    except UpgradeError as exc:
        print(f"Upgrade failed: {exc}")
        return 1

    print("Upgrade completed successfully.")
    print(f"Data backup created at: {result.backup_path}")
    print(f"Previous revision: {result.previous_revision}")
    print(f"Current revision: {result.current_revision}")
    if args.skip_deps:
        print("Dependency installation was skipped as requested.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI behavior
    raise SystemExit(main())
