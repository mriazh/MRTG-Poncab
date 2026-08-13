"""WhatsApp notification dispatch engine using GOWA REST gateway."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import settings
from .graph_renderer import format_engineering_bits

logger = logging.getLogger(__name__)

WIB_OFFSET = timedelta(hours=7)


def _now_wib_str() -> str:
    """Return formatted current time in WIB."""
    now_wib = datetime.now(UTC) + WIB_OFFSET
    return now_wib.strftime("%Y-%m-%d %H:%M:%S")


def is_recent_reboot(uptime: str | None) -> bool:
    """Determine whether router recently rebooted (e.g. power outage) vs long-running uptime."""
    if not uptime:
        return False

    u = uptime.strip().lower()

    # If it contains weeks ('w') or days ('d'), it has been running for days/weeks
    if "w" in u or "d" in u:
        return False

    # If format is HH:MM:SS (e.g. 00:04:12)
    if ":" in u:
        parts = u.split(":")
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
            # If less than 1 hour, treat as fresh reboot
            return hours == 0 and minutes < 30
        except (ValueError, IndexError):
            pass

    # If format is XhYmZs or XmYs
    if "h" in u:
        try:
            hours_val = int(u.split("h")[0].strip())
            return hours_val == 0
        except ValueError:
            return False

    # Only minutes/seconds (e.g. 5m20s or 45s)
    return bool("m" in u or "s" in u)


def format_down_alert(
    router_name: str = "MikroTik RB960PGS (WAN 150M)",
    location: str = "GMF AeroAsia Pondok Cabe",
    reason: str = "Tunnel link dropped / Router API polling unresponsive.",
    timestamp: str | None = None,
) -> str:
    """Compose structured NOC incident alert message."""
    ts = timestamp or _now_wib_str()
    return (
        "🚨 *[NOC ALERT] MIKROTIK PONCAB OFFLINE!*\n\n"
        f"📍 *Device:* {router_name}\n"
        f"🏢 *Location:* {location}\n"
        f"⏱ *Time:* {ts} WIB\n"
        "🔌 *Status:* DOWN (Unreachable)\n\n"
        f"⚠️ *Details:* {reason}\n"
        "Probable cause: Facility power outage or Telkom IndiBiz uplink drop.\n\n"
        "📊 *Dashboard:* https://mrtg.mriazh.my.id"
    )


def format_resolved_alert(
    uptime: str,
    rx_bps: float = 0.0,
    tx_bps: float = 0.0,
    router_name: str = "MikroTik RB960PGS (WAN 150M)",
    location: str = "GMF AeroAsia Pondok Cabe",
    timestamp: str | None = None,
) -> str:
    """Compose structured NOC resolution message with automated root-cause diagnosis."""
    ts = timestamp or _now_wib_str()
    is_reboot = is_recent_reboot(uptime)

    if is_reboot:
        diagnosis = (
            "⚡ *RECENT POWER OUTAGE / REBOOT*\n"
            "Router has just completed cold boot. "
            "Network uplink and tunnel have fully recovered."
        )
    else:
        diagnosis = (
            "🌐 *TRANSIENT ISP / TUNNEL DROPOUT*\n"
            "Router remained powered on (system uptime sustained). "
            "IndiBiz ISP uplink or tunnel session dropped momentarily and is now re-established."
        )

    in_fmt = format_engineering_bits(rx_bps)
    out_fmt = format_engineering_bits(tx_bps)

    return (
        "✅ *[NOC RESOLVED] MIKROTIK PONCAB ONLINE!*\n\n"
        f"📍 *Device:* {router_name}\n"
        f"🏢 *Location:* {location}\n"
        f"⏱ *Time:* {ts} WIB\n"
        "🔌 *Status:* UP (Connected)\n"
        f"⏳ *Uptime:* {uptime}\n"
        f"📈 *Live Traffic:* In: {in_fmt} | Out: {out_fmt}\n\n"
        f"📝 *System Diagnosis:*\n{diagnosis}\n\n"
        "📊 *Dashboard:* https://mrtg.mriazh.my.id"
    )


def format_startup_notice(
    node_name: str = "Debian Server (Office Host)",
    target: str = "id-04.tunnel.web.id:5336 (WAN 150M)",
    timestamp: str | None = None,
) -> str:
    """Compose informational daemon startup / system online notice."""
    ts = timestamp or _now_wib_str()
    return (
        "🟢 *[NOC SYSTEM ONLINE] MRTG Collector Daemon Started*\n\n"
        f"💻 *Node:* {node_name}\n"
        f"🎯 *Target:* {target}\n"
        f"⏱ *Time:* {ts} WIB\n"
        "Status: Collector daemon active and monitoring traffic samples.\n\n"
        "📊 *Dashboard:* https://mrtg.mriazh.my.id"
    )


def format_shutdown_notice(
    node_name: str = "Debian Server (Office Host)",
    reason: str = "Daemon stopping for update or maintenance restart.",
    timestamp: str | None = None,
) -> str:
    """Compose informational daemon graceful shutdown notice."""
    ts = timestamp or _now_wib_str()
    return (
        "⏸️ *[NOC SYSTEM STOPPED] MRTG Collector Daemon Stopping*\n\n"
        f"💻 *Node:* {node_name}\n"
        f"⏱ *Time:* {ts} WIB\n"
        f"ℹ️ *Note:* {reason}\n"
        "If intentional (deploy/update), recovery notice will follow automatically."
    )


def send_whatsapp_message(
    message: str,
    target_jid: str | None = None,
    gateway_url: str | None = None,
    device_id: str | None = None,
    timeout: float = 10.0,
) -> bool:
    """Send text message via GOWA WhatsApp REST gateway."""
    if not settings.wa_alert_enabled and target_jid is None:
        return False

    url_base = gateway_url or settings.wa_gateway_url
    target = target_jid or settings.wa_target_jid
    dev_id = device_id or settings.wa_device_id

    if not url_base or not target:
        logger.warning("WhatsApp alert skipped: gateway URL or target JID not set")
        return False

    endpoint = f"{url_base.rstrip('/')}/send/message"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if dev_id:
        headers["X-Device-Id"] = dev_id

    payload: dict[str, Any] = {
        "phone": target,
        "message": message,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
            if resp.status_code == 200:
                logger.info("WhatsApp notification sent successfully to %s", target)
                return True
            else:
                logger.warning(
                    "WhatsApp gateway returned HTTP %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False
    except Exception as e:
        logger.warning("Failed to dispatch WhatsApp message via %s: %s", endpoint, e)
        return False
