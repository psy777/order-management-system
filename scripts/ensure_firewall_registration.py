"""Helper to ensure the Windows firewall allows FireCoast registration traffic."""
from __future__ import annotations

import sys

from services.firewall import (
    FirewallError,
    FirewallManager,
    FirewallPermissionError,
    FirewallUnsupportedError,
)


def _resolve_port() -> int:
    """Match the application's port resolution logic."""
    try:
        from app import SERVER_PORT  # type: ignore import
    except Exception:  # pragma: no cover - fallback to default port
        return 5002
    return int(SERVER_PORT)


def _format_message(port: int, message: str) -> str:
    return f"[FireCoast] {message} (port {port})."


def main() -> int:
    port = _resolve_port()
    manager = FirewallManager()

    try:
        manager.ensure_registration_access(port)
    except FirewallUnsupportedError:
        print(_format_message(port, "Firewall automation is not supported on this platform; skipping"))
        return 0
    except FirewallPermissionError:
        print(_format_message(port, "Firewall automation requires Administrator privileges"))
        return 2
    except FirewallError as exc:
        print(_format_message(port, f"Warning: unable to configure the firewall automatically: {exc}"))
        return 3
    except Exception as exc:  # pragma: no cover - unexpected runtime error
        print(_format_message(port, f"Unexpected error while configuring the firewall: {exc}"))
        return 3

    print(_format_message(port, "Firewall access ensured for registration traffic"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
