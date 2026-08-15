"""Tests for WhatsApp notification dispatch and intelligent root-cause diagnosis."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mrtg_poncab.notifier import (
    SCENARIO_DEBIAN_NET_DOWN,
    SCENARIO_MIKROTIK_OFFLINE,
    SCENARIO_TUNNEL_PROVIDER_DOWN,
    SCENARIO_TUNNEL_SESSION_ERROR,
    format_autoheal_notice,
    format_down_alert,
    format_resolved_alert,
    format_shutdown_notice,
    format_startup_notice,
    is_recent_reboot,
    send_whatsapp_message,
)


def test_is_recent_reboot_detection() -> None:
    """Correctly classify recent reboot vs long-running uptime."""
    # Long-running uptimes (ISP or tunnel outage)
    assert is_recent_reboot("11w3d1h48m12s") is False
    assert is_recent_reboot("80d 12:00:00") is False
    assert is_recent_reboot("2w5d") is False
    assert is_recent_reboot("3d04h12m") is False

    # Recent reboots (power outage or fresh restart)
    assert is_recent_reboot("00:04:12") is True
    assert is_recent_reboot("5m20s") is True
    assert is_recent_reboot("45s") is True
    assert is_recent_reboot("0h4m10s") is True

    # None / Empty
    assert is_recent_reboot(None) is False
    assert is_recent_reboot("") is False


def test_format_down_alert_scenarios() -> None:
    """format_down_alert generates tailored alert messages for each failure scenario."""
    # Scenario 1: Debian Network Down
    msg1 = format_down_alert(
        reason="No internet connection",
        scenario=SCENARIO_DEBIAN_NET_DOWN,
        timestamp="2026-09-21 10:00:00",
    )
    assert "[NOC ALERT] DEBIAN MONITOR NETWORK DOWN" in msg1
    assert "OUTBOUND NETWORK UNREACHABLE" in msg1

    # Scenario 2: Tunnel Provider Down
    msg2 = format_down_alert(
        reason="Server 443 unreachable",
        scenario=SCENARIO_TUNNEL_PROVIDER_DOWN,
        timestamp="2026-09-21 10:00:00",
    )
    assert "[NOC ALERT] TUNNEL PROVIDER SERVICE DOWN" in msg2
    assert "TUNNEL GATEWAY UNREACHABLE" in msg2

    # Scenario 3: Tunnel Session Suspended (Koneksi Error)
    msg3 = format_down_alert(
        reason="Ghost session detected",
        scenario=SCENARIO_TUNNEL_SESSION_ERROR,
        timestamp="2026-09-21 10:00:00",
    )
    assert "[NOC ALERT] TUNNEL SESSION SUSPENDED" in msg3
    assert "SESSION ERROR (Koneksi Error)" in msg3
    assert "Auto-Healing" in msg3

    # Scenario 4: MikroTik Offline
    msg4 = format_down_alert(
        reason="Port 5336 closed",
        scenario=SCENARIO_MIKROTIK_OFFLINE,
        timestamp="2026-09-21 10:00:00",
    )
    assert "[NOC ALERT] MIKROTIK PONCAB OFFLINE" in msg4
    assert "DOWN (Unreachable)" in msg4
    assert "Facility power outage at Poncab" in msg4


def test_format_resolved_alert_power_outage() -> None:
    """format_resolved_alert diagnoses recent reboot as power outage."""
    msg = format_resolved_alert(
        uptime="00:03:15",
        rx_bps=10_000_000.0,
        tx_bps=5_000_000.0,
    )
    assert "[NOC RESOLVED]" in msg
    assert "UP" in msg
    assert "RECENT POWER OUTAGE" in msg


def test_format_resolved_alert_isp_outage() -> None:
    """format_resolved_alert diagnoses long uptime as ISP/tunnel network drop."""
    msg = format_resolved_alert(
        uptime="11w3d4h12m",
        rx_bps=10_000_000.0,
        tx_bps=5_000_000.0,
    )
    assert "[NOC RESOLVED]" in msg
    assert "UP" in msg
    assert "TRANSIENT ISP" in msg


def test_send_whatsapp_message_disabled_by_default() -> None:
    """send_whatsapp_message returns False when wa_alert_enabled is False and no override."""
    with patch("mrtg_poncab.config.settings.wa_alert_enabled", False):
        assert send_whatsapp_message("test", target_jid=None) is False


def test_send_whatsapp_message_success_mocked() -> None:
    """send_whatsapp_message dispatches HTTP POST and handles 200 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with (
        patch("mrtg_poncab.config.settings.wa_alert_enabled", True),
        patch("mrtg_poncab.config.settings.wa_gateway_url", "http://localhost:3000"),
        patch("mrtg_poncab.config.settings.wa_target_jid", "123@g.us"),
        patch("httpx.Client.post", return_value=mock_resp),
    ):
        result = send_whatsapp_message("Test Alert Message")
        assert result is True


def test_format_startup_and_shutdown_notices() -> None:
    """format_startup_notice and format_shutdown_notice generate structured lifecycle alerts."""
    start_msg = format_startup_notice(
        node_name="Test Node",
        target="10.0.0.1:8728",
        timestamp="2026-09-20 20:00:00",
    )
    assert "[NOC SYSTEM ONLINE]" in start_msg
    assert "Test Node" in start_msg
    assert "10.0.0.1:8728" in start_msg

    stop_msg = format_shutdown_notice(
        node_name="Test Node",
        reason="Maintenance restart",
        timestamp="2026-09-20 20:01:00",
    )
    assert "[NOC SYSTEM STOPPED]" in stop_msg
    assert "Maintenance restart" in stop_msg


def test_format_autoheal_notice() -> None:
    """format_autoheal_notice generates structured auto-heal action notice."""
    msg = format_autoheal_notice(
        service_id="46486",
        action_message="Restart sukses",
        timestamp="2026-09-20 20:02:00",
    )
    assert "[NOC AUTO-HEAL]" in msg
    assert "#46486" in msg
    assert "Restart sukses" in msg

