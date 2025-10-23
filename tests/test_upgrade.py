from __future__ import annotations

import sys
from pathlib import Path

import pytest

import app as firenotes_app
from services import upgrade


class DummyResult:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""


@pytest.fixture(autouse=True)
def enable_testing_flag():
    original = firenotes_app.app.config.get('TESTING')
    firenotes_app.app.config['TESTING'] = True
    try:
        yield
    finally:
        if original is None:
            firenotes_app.app.config.pop('TESTING', None)
        else:
            firenotes_app.app.config['TESTING'] = original


def test_perform_upgrade_executes_expected_git_commands(monkeypatch):
    data_root = upgrade.ensure_data_root()
    for child in data_root.glob("*"):
        if child.is_file():
            child.unlink()

    backup_dir = data_root.parent / "upgrade_backups"
    if backup_dir.exists():
        for entry in backup_dir.glob("*"):
            if entry.is_file():
                entry.unlink()

    expected_backup = backup_dir / "backup_test.zip"

    def fake_create_backup(destination_dir: Path | None = None) -> Path:
        assert destination_dir is not None
        destination_dir.mkdir(parents=True, exist_ok=True)
        expected_backup.write_text("backup")
        return expected_backup

    monkeypatch.setattr(upgrade, "create_backup_archive", fake_create_backup)

    rev_outputs = ["abc123\n", "def456\n"]
    commands: list[tuple[tuple[str, ...], Path]] = []

    def fake_runner(args, *, cwd=None):
        assert cwd == upgrade._resolve_repo_root()
        commands.append((tuple(args), cwd))
        if tuple(args[:2]) == ("git", "status"):
            return DummyResult("")
        if tuple(args[:2]) == ("git", "rev-parse"):
            return DummyResult(rev_outputs.pop(0))
        if args[0] == sys.executable:
            return DummyResult("")
        return DummyResult("")

    result = upgrade.perform_upgrade(runner=fake_runner)

    assert result.backup_path == expected_backup
    assert result.previous_revision == "abc123"
    assert result.current_revision == "def456"

    requirement_path = upgrade._resolve_repo_root() / "requirements.txt"
    assert commands == [
        (("git", "status", "--porcelain"), upgrade._resolve_repo_root()),
        (("git", "rev-parse", "HEAD"), upgrade._resolve_repo_root()),
        (("git", "fetch", "origin", "master"), upgrade._resolve_repo_root()),
        (("git", "checkout", "master"), upgrade._resolve_repo_root()),
        (("git", "reset", "--hard", "origin/master"), upgrade._resolve_repo_root()),
        (("git", "rev-parse", "HEAD"), upgrade._resolve_repo_root()),
        ((
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirement_path),
        ), upgrade._resolve_repo_root()),
    ]


def test_perform_upgrade_aborts_when_repository_is_dirty(monkeypatch):
    def fake_runner(args, *, cwd=None):
        assert tuple(args[:2]) == ("git", "status")
        return DummyResult(" M app.py\n")

    called = False

    def failing_backup(*_, **__):
        nonlocal called
        called = True
        raise AssertionError("backup should not be created when upgrade aborts")

    monkeypatch.setattr(upgrade, "create_backup_archive", failing_backup)

    with pytest.raises(upgrade.UpgradeError):
        upgrade.perform_upgrade(runner=fake_runner, install_dependencies=False)

    assert not called


def test_upgrade_endpoint_invokes_service(monkeypatch, tmp_path):
    client = firenotes_app.app.test_client()

    def fake_perform_upgrade(remote: str, branch: str, install_dependencies: bool):
        assert remote == 'origin'
        assert branch == 'master'
        assert install_dependencies is True
        return upgrade.UpgradeResult(
            backup_path=tmp_path / 'backup.zip',
            previous_revision='abc123',
            current_revision='def456',
        )

    monkeypatch.setattr(firenotes_app, 'perform_upgrade', fake_perform_upgrade)

    response = client.post('/api/system/upgrade', json={})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['status'] == 'ok'
    assert payload['previousRevision'] == 'abc123'
    assert payload['currentRevision'] == 'def456'
    assert payload['dependenciesInstalled'] is True


def test_upgrade_endpoint_respects_skip_dependencies(monkeypatch, tmp_path):
    client = firenotes_app.app.test_client()

    def fake_perform_upgrade(remote: str, branch: str, install_dependencies: bool):
        assert not install_dependencies
        return upgrade.UpgradeResult(
            backup_path=tmp_path / 'backup.zip',
            previous_revision='abc123',
            current_revision='def456',
        )

    monkeypatch.setattr(firenotes_app, 'perform_upgrade', fake_perform_upgrade)

    response = client.post('/api/system/upgrade', json={'skipDependencies': True})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['dependenciesInstalled'] is False


def test_upgrade_endpoint_returns_error_on_failure(monkeypatch):
    client = firenotes_app.app.test_client()

    def failing_upgrade(*_, **__):
        raise upgrade.UpgradeError('dirty tree detected')

    monkeypatch.setattr(firenotes_app, 'perform_upgrade', failing_upgrade)

    response = client.post('/api/system/upgrade', json={})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['status'] == 'error'
    assert 'dirty tree detected' in payload['message']
