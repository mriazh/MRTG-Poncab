"""Tests for TunnelWatchdog network triangulation and auto-healing."""

from __future__ import annotations

import time
from unittest.mock import patch

from mrtg_poncab.tunnel_watchdog import TunnelWatchdog


def test_watchdog_diagnose_healthy() -> None:
    """Watchdog returns HEALTHY when target port is reachable."""
    wd = TunnelWatchdog()
    with patch.object(wd, "check_tcp_port", return_value=True):
        diag = wd.diagnose("id-04.tunnel.web.id", 5336)
        assert diag["code"] == "HEALTHY"
        assert diag["status"] == "UP"
        assert diag["port_open"] is True


def test_watchdog_diagnose_port_closed() -> None:
    """Watchdog distinguishes closed port from dead server."""
    wd = TunnelWatchdog()

    # Port closed, but server alive on 443
    def mock_check(host: str, port: int, timeout: float = 3.0) -> bool:
        return port == 443

    with (
        patch.object(wd, "check_tcp_port", side_effect=mock_check),
        patch("socket.gethostbyname", return_value="157.66.54.157"),
    ):
        diag = wd.diagnose("id-04.tunnel.web.id", 5336)
        assert diag["code"] == "PORT_CLOSED"
        assert diag["status"] == "DOWN"
        assert "port 5336 is closed" in diag["message"]


def test_watchdog_cooldown_guard() -> None:
    """Watchdog honors cooldown timer to prevent spamming restart."""
    wd = TunnelWatchdog()
    wd.last_restart_epoch = time.time() - 60  # restarted 1 min ago (cooldown 30 min)

    with (
        patch.object(wd, "diagnose", return_value={"code": "PORT_CLOSED"}),
        patch.object(
            wd,
            "inspect_member_portal",
            return_value={"success": True, "needs_restart": True, "restart_url": "http://example.com/restart"},
        ),
    ):
        res = wd.auto_heal_if_needed()
        assert res["action"] == "COOLDOWN"
        assert "cooldown active" in res["message"]
