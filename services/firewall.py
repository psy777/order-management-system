"""Platform-aware helpers for managing host firewall access rules."""
from __future__ import annotations

import ipaddress
import json
import logging
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional, Sequence, Set

from data_paths import ensure_data_root


class FirewallError(RuntimeError):
    """Raised when the firewall helper cannot execute an operation."""


class FirewallUnsupportedError(FirewallError):
    """Raised when the current platform or tooling cannot be automated."""


_FIREWALL_LEDGER_FILENAME = "firecoast_firewall.json"
_RULE_PREFIX = "FireCoastTrusted"
_REGISTRATION_RULE_PREFIX = "FireCoastRegistration"


def _normalize_ip(ip: Optional[str]) -> Optional[str]:
    if not ip:
        return None
    try:
        parsed = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    # For now we only support IPv4 rules for consistency across helpers.
    if parsed.version != 4:
        return None
    return str(parsed)


class FirewallManager:
    """Best-effort firewall controller that keeps host rules in sync."""

    def __init__(self, data_directory: Optional[Path] = None) -> None:
        data_root = Path(data_directory) if data_directory else Path(ensure_data_root())
        self._ledger_path = data_root / _FIREWALL_LEDGER_FILENAME
        self._system = platform.system().lower()
        self._logger = logging.getLogger("firecoast.firewall")
        self._supports_ufw = shutil.which("ufw") is not None
        self._supports_iptables = shutil.which("iptables") is not None

    def reconcile_trusted_ips(self, ips: Iterable[str], port: int) -> None:
        """Ensure the firewall allows *exactly* the provided IPs for the port."""
        normalized = {
            candidate
            for candidate in (_normalize_ip(ip) for ip in ips)
            if candidate
        }
        ledger = self._load_ledger()
        port_key = str(port)
        existing: Set[str] = set(ledger.get("trusted", {}).get(port_key, []))

        if not self._is_supported():
            raise FirewallUnsupportedError(
                f"Firewall automation is not supported on platform '{self._system}'."
            )

        updated_existing = set(existing)

        # Ensure each desired IP currently has an allow rule applied.
        for ip in sorted(normalized):
            if self._allow_ip(ip, port):
                updated_existing.add(ip)
            elif ip in existing:
                # If we could not re-apply the rule but it was previously tracked,
                # keep the entry so we can retry on the next sync.
                updated_existing.add(ip)

        # Remove stale entries that no longer correspond to trusted devices.
        for ip in sorted(existing - normalized):
            if not self._revoke_ip(ip, port):
                updated_existing.add(ip)
            elif ip in updated_existing:
                updated_existing.discard(ip)

        ledger.setdefault("trusted", {})[port_key] = sorted(updated_existing)
        self._save_ledger(ledger)

    def ensure_registration_access(self, port: int) -> None:
        """Ensure the port is open so new devices can submit registration requests."""
        if not self._is_supported():
            raise FirewallUnsupportedError(
                f"Firewall automation is not supported on platform '{self._system}'."
            )

        ledger = self._load_ledger()
        port_key = str(port)
        if self._open_port(port):
            registration = ledger.setdefault("registration", {})
            registration[port_key] = True
            self._save_ledger(ledger)

    # -- internal helpers -------------------------------------------------

    def _is_supported(self) -> bool:
        if self._system == "windows":
            return True
        if self._system == "linux":
            return self._supports_ufw or self._supports_iptables
        return False

    def _allow_ip(self, ip: str, port: int) -> bool:
        rule_name = self._rule_name_for_ip(ip) if self._system == "windows" else None
        try:
            command = self._build_allow_command(ip, port)
            self._run_command(command)
            return True
        except FirewallUnsupportedError:
            raise
        except FirewallError as exc:
            if (
                rule_name
                and "already exists" in str(exc).lower()
                and self._enable_rule(rule_name)
            ):
                return True
            self._logger.warning("Failed to allow %s for port %s: %s", ip, port, exc)
            return False

    def _revoke_ip(self, ip: str, port: int) -> bool:
        try:
            command = self._build_revoke_command(ip, port)
            self._run_command(command)
            return True
        except FirewallUnsupportedError:
            raise
        except FirewallError as exc:
            self._logger.warning("Failed to revoke %s for port %s: %s", ip, port, exc)
            return False

    def _build_allow_command(self, ip: str, port: int) -> Sequence[str]:
        if self._system == "windows":
            rule_name = self._rule_name_for_ip(ip)
            return [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule_name}",
                "dir=in",
                "action=allow",
                f"remoteip={ip}",
                f"localport={port}",
                "protocol=TCP",
            ]
        if self._system == "linux":
            if self._supports_ufw:
                return [
                    "ufw",
                    "--force",
                    "allow",
                    "from",
                    ip,
                    "to",
                    "any",
                    "port",
                    str(port),
                    "proto",
                    "tcp",
                    "comment",
                    _RULE_PREFIX,
                ]
            if self._supports_iptables:
                return [
                    "iptables",
                    "-I",
                    "INPUT",
                    "-p",
                    "tcp",
                    "--dport",
                    str(port),
                    "-s",
                    ip,
                    "-j",
                    "ACCEPT",
                ]
        raise FirewallUnsupportedError(
            f"Firewall allow command is unavailable for platform '{self._system}'."
        )

    def _build_revoke_command(self, ip: str, port: int) -> Sequence[str]:
        if self._system == "windows":
            rule_name = self._rule_name_for_ip(ip)
            return [
                "netsh",
                "advfirewall",
                "firewall",
                "delete",
                "rule",
                f"name={rule_name}",
                f"remoteip={ip}",
                "protocol=TCP",
                f"localport={port}",
            ]
        if self._system == "linux":
            if self._supports_ufw:
                return [
                    "ufw",
                    "--force",
                    "delete",
                    "allow",
                    "from",
                    ip,
                    "to",
                    "any",
                    "port",
                    str(port),
                    "proto",
                    "tcp",
                ]
            if self._supports_iptables:
                return [
                    "iptables",
                    "-D",
                    "INPUT",
                    "-p",
                    "tcp",
                    "--dport",
                    str(port),
                    "-s",
                    ip,
                    "-j",
                    "ACCEPT",
                ]
        raise FirewallUnsupportedError(
            f"Firewall revoke command is unavailable for platform '{self._system}'."
        )

    def _open_port(self, port: int) -> bool:
        try:
            command = self._build_open_port_command(port)
            self._run_command(command)
            return True
        except FirewallUnsupportedError:
            raise
        except FirewallError as exc:
            if (
                self._system == "windows"
                and "already exists" in str(exc).lower()
                and self._enable_rule(self._registration_rule_name(port))
            ):
                return True
            self._logger.warning("Failed to open port %s: %s", port, exc)
            return False

    def _build_open_port_command(self, port: int) -> Sequence[str]:
        if self._system == "windows":
            rule_name = self._registration_rule_name(port)
            return [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule_name}",
                "dir=in",
                "action=allow",
                f"localport={port}",
                "protocol=TCP",
            ]
        if self._system == "linux":
            if self._supports_ufw:
                return [
                    "ufw",
                    "--force",
                    "allow",
                    "to",
                    "any",
                    "port",
                    str(port),
                    "proto",
                    "tcp",
                    "comment",
                    _REGISTRATION_RULE_PREFIX,
                ]
            if self._supports_iptables:
                return [
                    "iptables",
                    "-I",
                    "INPUT",
                    "-p",
                    "tcp",
                    "--dport",
                    str(port),
                    "-j",
                    "ACCEPT",
                ]
        raise FirewallUnsupportedError(
            f"Firewall open-port command is unavailable for platform '{self._system}'."
        )

    def _enable_rule(self, rule_name: str) -> bool:
        if self._system != "windows":
            return False
        try:
            command = self._build_enable_rule_command(rule_name)
            self._run_command(command)
            return True
        except FirewallUnsupportedError:
            raise
        except FirewallError as exc:
            self._logger.warning("Failed to enable firewall rule %s: %s", rule_name, exc)
            return False

    def _build_enable_rule_command(self, rule_name: str) -> Sequence[str]:
        if self._system == "windows":
            return [
                "netsh",
                "advfirewall",
                "firewall",
                "set",
                "rule",
                f"name={rule_name}",
                "new",
                "enable=yes",
            ]
        raise FirewallUnsupportedError(
            f"Firewall enable command is unavailable for platform '{self._system}'."
        )

    def _rule_name_for_ip(self, ip: str) -> str:
        return f"{_RULE_PREFIX}_{ip.replace('.', '_')}"

    def _registration_rule_name(self, port: int) -> str:
        return f"{_REGISTRATION_RULE_PREFIX}_{port}"

    def _run_command(self, command: Sequence[str]) -> None:
        try:
            subprocess.run(
                list(command),
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:  # pragma: no cover - defensive
            raise FirewallUnsupportedError(
                f"Required command '{command[0]}' is not available"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else str(exc)
            raise FirewallError(stderr) from exc

    def _load_ledger(self) -> dict:
        if not self._ledger_path.exists():
            return {}
        try:
            return json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_ledger(self, ledger: dict) -> None:
        try:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self._ledger_path.write_text(
                json.dumps(ledger, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - disk errors are rare in tests
            self._logger.warning("Unable to persist firewall ledger: %s", exc)


_manager_instance: Optional[FirewallManager] = None


def get_firewall_manager() -> FirewallManager:
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = FirewallManager()
    return _manager_instance


def reset_firewall_manager() -> None:
    """Testing hook to discard cached manager state."""
    global _manager_instance
    _manager_instance = None
