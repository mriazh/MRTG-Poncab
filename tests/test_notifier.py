"""Tests for WhatsApp notification dispatch and intelligent root-cause diagnosis."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mrtg_poncab.notifier import (
    format_down_alert,
    format_resolved_alert,
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


def test_format_down_alert() -> None:
    """format_down_alert generates structured incident message."""
    msg = format_down_alert(
        router_name="MikroTik WAN",
        location="Pondok Cabe",
        reason="Port 5336 unreachable",
        timestamp="2026-09-20 14:00:00",
    )
    assert "[NOC ALERT]" in msg
    assert "DOWN" in msg
    assert "MikroTik WAN" in msg
    assert "https://mrtg.mriazh.my.id" in msg


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
