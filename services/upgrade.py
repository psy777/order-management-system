"""Utilities for upgrading the FireCoast installation in-place."""
from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Protocol, Sequence

from data_paths import ensure_data_root
from services.backup import BackupError, create_backup_archive

LOGGER = logging.getLogger(__name__)

DEFAULT_REPOSITORY_URL = "https://github.com/psy777/FireCoast.git"
REVISION_MARKER = ".firecoast_revision"
PRESERVED_PATHS: frozenset[str] = frozenset(
    {
        "data",
        "upgrade_backups",
        ".env",
        ".env.local",
        "venv",
        ".venv",
    }
)


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
    repository_url: Optional[str] = None,
) -> UpgradeResult:
    """Upgrade the repository to the latest commit on ``remote/branch``.

    The function performs a safety backup of the ``data/`` directory, ensures
    the working tree is clean, fast-forwards to the requested branch, and
    optionally reinstalls dependencies from ``requirements.txt``.
    """

    repo_root = _resolve_repo_root()
    runner = runner or _run_command

    if _is_git_repository(repo_root):
        result = _upgrade_via_git_checkout(
            repo_root,
            remote,
            branch,
            runner,
            install_dependencies,
        )
    else:
        result = _upgrade_via_clone(
            repo_root,
            remote,
            branch,
            runner,
            install_dependencies,
            repository_url,
        )

    LOGGER.info(
        "Upgrade complete. Previous revision %s, new revision %s",
        result.previous_revision,
        result.current_revision,
    )
    return result


def _upgrade_via_git_checkout(
    repo_root: Path,
    remote: str,
    branch: str,
    runner: CommandRunner,
    install_dependencies: bool,
) -> UpgradeResult:
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
    _write_revision_marker(repo_root, current_revision)

    if install_dependencies:
        LOGGER.info("Reinstalling Python dependencies")
        _install_dependencies(runner, repo_root)

    return UpgradeResult(
        backup_path=backup_path,
        previous_revision=previous_revision,
        current_revision=current_revision,
    )


def _upgrade_via_clone(
    repo_root: Path,
    remote: str,
    branch: str,
    runner: CommandRunner,
    install_dependencies: bool,
    repository_url: Optional[str],
) -> UpgradeResult:
    LOGGER.info("Repository metadata not found; falling back to clone-based upgrade")

    previous_revision = _read_revision_marker(repo_root) or "unknown"

    LOGGER.info("Creating data backup prior to upgrade")
    backup_path = _create_data_backup()

    remote_url = _coerce_remote_to_url(remote, repository_url)
    if not remote_url:
        raise UpgradeError(
            "Unable to determine a repository URL. Provide one in the request or set the FIRECOAST_UPGRADE_REPO environment variable."
        )

    with _clone_repository(remote_url, branch, runner, repo_root) as clone_dir:
        current_revision = _get_revision(
            runner, clone_dir, context="determine cloned revision"
        )
        _synchronise_application_tree(clone_dir, repo_root)

    _write_revision_marker(repo_root, current_revision)

    if install_dependencies:
        LOGGER.info("Reinstalling Python dependencies")
        _install_dependencies(runner, repo_root)

    return UpgradeResult(
        backup_path=backup_path,
        previous_revision=previous_revision,
        current_revision=current_revision,
    )


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_git_repository(path: Path) -> bool:
    return (path / ".git").exists()


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


def _coerce_remote_to_url(remote: str, repository_url: Optional[str]) -> str:
    if repository_url:
        candidate = repository_url.strip()
        if candidate:
            return candidate

    remote = remote.strip()
    if _looks_like_url(remote):
        return remote

    env_override = os.getenv("FIRECOAST_UPGRADE_REPO")
    if env_override and env_override.strip():
        return env_override.strip()

    return DEFAULT_REPOSITORY_URL


def _looks_like_url(candidate: str) -> bool:
    return "://" in candidate or candidate.startswith("git@")


@contextlib.contextmanager
def _clone_repository(
    remote_url: str,
    branch: str,
    runner: CommandRunner,
    repo_root: Path,
) -> Iterator[Path]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="firecoast-upgrade-"))
    clone_target = tmp_dir / "clone"

    LOGGER.info("Cloning %s (branch %s) into %s", remote_url, branch, clone_target)
    _run_with_error_handling(
        runner,
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            remote_url,
            str(clone_target),
        ],
        repo_root.parent,
        f"clone repository from {remote_url}",
    )

    try:
        yield clone_target
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _synchronise_application_tree(source: Path, destination: Path) -> None:
    preserve = set(PRESERVED_PATHS)

    existing_entries = {entry.name for entry in source.iterdir()}

    for entry in destination.iterdir():
        if entry.name in preserve:
            continue
        if entry.name not in existing_entries:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()

    for entry in source.iterdir():
        if entry.name in preserve or entry.name == ".git":
            continue

        target = destination / entry.name
        if entry.is_dir():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(entry, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)


def _write_revision_marker(repo_root: Path, revision: str) -> None:
    marker_path = repo_root / REVISION_MARKER
    marker_path.write_text(revision)


def _read_revision_marker(repo_root: Path) -> Optional[str]:
    marker_path = repo_root / REVISION_MARKER
    if marker_path.exists():
        return marker_path.read_text().strip() or None
    return None


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
