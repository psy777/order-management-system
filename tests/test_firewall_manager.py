import json

import pytest

from services import firewall


@pytest.fixture
def temp_manager(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ledger = data_dir / firewall._FIREWALL_LEDGER_FILENAME  # noqa: SLF001 - test helper
    ledger.write_text(json.dumps({}))

    manager = firewall.FirewallManager(data_directory=data_dir)
    monkeypatch.setattr(manager, "_is_supported", lambda: True)
    return manager, ledger


def test_registration_access_reapplies_rule_when_recorded(temp_manager, monkeypatch):
    manager, ledger = temp_manager

    ledger.write_text(
        json.dumps({"registration": {"5002": True}}, sort_keys=True)
    )

    calls = []

    def fake_open_port(port):
        calls.append(port)
        return True

    monkeypatch.setattr(manager, "_open_port", fake_open_port)

    manager.ensure_registration_access(5002)

    assert calls == [5002]


def test_reconcile_trusted_ips_reapplies_existing_rules(temp_manager, monkeypatch):
    manager, ledger = temp_manager
    ledger.write_text(
        json.dumps({"trusted": {"5002": ["192.168.1.10"]}}, sort_keys=True)
    )

    calls = []

    def fake_allow(ip, port):
        calls.append((ip, port))
        return True

    monkeypatch.setattr(manager, "_allow_ip", fake_allow)
    monkeypatch.setattr(manager, "_revoke_ip", lambda ip, port: True)

    manager.reconcile_trusted_ips(["192.168.1.10"], 5002)

    assert calls == [("192.168.1.10", 5002)]


def test_windows_allow_enables_existing_rule(monkeypatch, tmp_path):
    manager = firewall.FirewallManager(data_directory=tmp_path)
    manager._system = "windows"  # noqa: SLF001 - adjust for test scenario
    monkeypatch.setattr(manager, "_is_supported", lambda: True)

    commands = []

    def fake_run(command):
        commands.append(command)
        if "add" in command:
            raise firewall.FirewallError("Cannot create a file when that file already exists.")

    monkeypatch.setattr(manager, "_run_command", fake_run)

    result = manager._allow_ip("192.168.1.50", 5002)

    assert result is True
    assert any("set" in cmd for cmd in commands)


def test_windows_open_port_enables_existing_rule(monkeypatch, tmp_path):
    manager = firewall.FirewallManager(data_directory=tmp_path)
    manager._system = "windows"  # noqa: SLF001 - adjust for test scenario
    monkeypatch.setattr(manager, "_is_supported", lambda: True)

    commands = []

    def fake_run(command):
        commands.append(command)
        if "add" in command:
            raise firewall.FirewallError("Rule already exists")

    monkeypatch.setattr(manager, "_run_command", fake_run)

    result = manager._open_port(5002)

    assert result is True
    assert any("set" in cmd for cmd in commands)


def test_run_command_detects_permission_error(monkeypatch, tmp_path):
    manager = firewall.FirewallManager(data_directory=tmp_path)

    def fake_run(command, check, capture_output, text):  # noqa: D401 - test helper
        raise firewall.subprocess.CalledProcessError(
            returncode=1,
            cmd=command,
            stderr='Access is denied.',
        )

    monkeypatch.setattr(firewall.subprocess, 'run', fake_run)

    with pytest.raises(firewall.FirewallPermissionError):
        manager._run_command(['netsh'])
