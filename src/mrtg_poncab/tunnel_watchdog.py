"""Automated health diagnostics and self-healing watchdog for tunnel.web.id."""

from __future__ import annotations

import logging
import re
import socket
import time
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)

MEMBER_BASE_URL = "https://member.tunnel.web.id"
LOGIN_URL = f"{MEMBER_BASE_URL}/login.php"


class TunnelWatchdog:
    """Monitors tunnel port health, diagnoses failure modes, and triggers safe auto-healing."""

    def __init__(self) -> None:
        self.last_restart_epoch: float = 0.0

    def check_tcp_port(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """Test if the remote TCP port is actively listening and reachable."""
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except (TimeoutError, ConnectionRefusedError, OSError):
            return False

    def diagnose(
        self,
        host: str | None = None,
        port: int | None = None,
    ) -> dict[str, Any]:
        """Perform rapid network triangulation to identify the failure layer.

        Returns:
            Dictionary with status, code, and recommended action.
        """
        target_host = host or settings.routeros_host
        target_port = port or settings.routeros_port

        # 1. Check if the tunnel port itself is open
        port_open = self.check_tcp_port(target_host, target_port, timeout=3.0)
        if port_open:
            return {
                "code": "HEALTHY",
                "status": "UP",
                "port_open": True,
                "message": f"Port {target_port} on {target_host} is reachable and listening.",
            }

        # 2. Port is closed - Check if host resolves
        try:
            resolved_ip = socket.gethostbyname(target_host)
        except socket.gaierror:
            return {
                "code": "DNS_FAILURE",
                "status": "DOWN",
                "port_open": False,
                "message": (
                    f"Cannot resolve tunnel host '{target_host}'. Check local DNS or internet."
                ),
            }

        # 3. Check if server IP accepts connection on HTTPS (443)
        server_alive = self.check_tcp_port(resolved_ip, 443, timeout=3.0)
        if not server_alive:
            return {
                "code": "PROVIDER_DOWN",
                "status": "DOWN",
                "port_open": False,
                "message": (
                    f"Server {target_host} ({resolved_ip}) unreachable. Provider or local ISP down."
                ),
            }

        # 4. Server is alive but port is closed
        return {
            "code": "PORT_CLOSED",
            "status": "DOWN",
            "port_open": False,
            "message": (
                f"Tunnel server {target_host} is online, but port {target_port} is closed. "
                "MikroTik is disconnected or tunnel session requires restart."
            ),
        }

    def inspect_member_portal(
        self,
        email: str | None = None,
        password: str | None = None,
        service_id: str | None = None,
    ) -> dict[str, Any]:
        """Log into member.tunnel.web.id and inspect the actual status of the VPN service."""
        user_email = email or settings.tunnel_web_email
        user_pwd = password or settings.tunnel_web_password
        srv_id = service_id or settings.tunnel_web_service_id

        if not user_email or not user_pwd:
            return {
                "success": False,
                "code": "NO_CREDENTIALS",
                "message": "TUNNEL_WEB_EMAIL or TUNNEL_WEB_PASSWORD not configured in .env",
            }

        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                # 1. Login to member portal
                login_resp = client.post(
                    LOGIN_URL,
                    data={"email": user_email, "password": user_pwd},
                )
                is_logged_in = (
                    "logout.php" in login_resp.text
                    or "dashboard" in login_resp.url.path.lower()
                )
                if not is_logged_in:
                    lower_text = login_resp.text.lower()
                    if "password salah" in lower_text or "tidak terdaftar" in lower_text:
                        return {
                            "success": False,
                            "code": "AUTH_FAILED",
                            "message": "Invalid tunnel.web.id login credentials",
                        }

                # 2. Navigate to service details
                detail_url = f"{MEMBER_BASE_URL}/layanan-detail.php?id={srv_id}"
                detail_resp = client.get(detail_url)
                body = detail_resp.text

                has_koneksi_error = "koneksi error" in body.lower()
                has_tidak_terhubung = "tidak terhubung" in body.lower()
                has_terhubung = "terhubung" in body.lower() and not has_tidak_terhubung

                # Detect restart button or form
                restart_url = None
                restart_match = re.search(
                    r'href=[\'"]([^\'"]*restart[^\'"]*)[\'"]', body, re.IGNORECASE
                )
                if restart_match:
                    found_link = restart_match.group(1)
                    restart_url = (
                        found_link
                        if found_link.startswith("http")
                        else f"{MEMBER_BASE_URL}/{found_link.lstrip('/')}"
                    )

                service_status = "UNKNOWN"
                if has_koneksi_error:
                    service_status = "KONEKSI_ERROR"
                elif has_terhubung:
                    service_status = "CONNECTED"
                elif has_tidak_terhubung:
                    service_status = "DISCONNECTED"

                return {
                    "success": True,
                    "code": service_status,
                    "service_id": srv_id,
                    "needs_restart": has_koneksi_error,
                    "restart_url": restart_url,
                    "message": f"Service #{srv_id} status on tunnel.web.id: {service_status}",
                    "client": client,
                    "raw_body": body,
                }

        except Exception as e:
            logger.warning("Tunnel web inspection error: %s", e)
            return {
                "success": False,
                "code": "INSPECTION_ERROR",
                "message": f"Failed to check member.tunnel.web.id: {e}",
            }

    def auto_heal_if_needed(self) -> dict[str, Any]:
        """Check tunnel health, and if in 'Koneksi Error' state, restart with cooldown guard."""
        # Rapid check first
        diag = self.diagnose()
        if diag["code"] == "HEALTHY":
            return {"action": "NONE", "message": "Tunnel is healthy and port is open."}

        # Port is closed - Check portal
        portal = self.inspect_member_portal()
        if not portal.get("success"):
            return {
                "action": "ERROR",
                "diagnosis": diag,
                "message": portal.get("message"),
            }

        # If it's pure Disconnected (MikroTik offline), do NOT restart to avoid spamming
        if portal.get("code") == "DISCONNECTED":
            return {
                "action": "NONE",
                "diagnosis": diag,
                "portal_status": "DISCONNECTED",
                "message": (
                    "MikroTik is offline at Poncab (power outage or ISP down). "
                    "Tunnel server is healthy."
                ),
            }

        # If it's Koneksi Error, restart is needed!
        if portal.get("needs_restart"):
            cooldown = settings.tunnel_restart_cooldown_minutes * 60
            now = time.time()
            if (now - self.last_restart_epoch) < cooldown:
                remaining = int(cooldown - (now - self.last_restart_epoch))
                return {
                    "action": "COOLDOWN",
                    "message": (
                        f"Tunnel restart needed, but cooldown active for {remaining}s more."
                    ),
                }

            # Execute restart
            restart_url = portal.get("restart_url")
            client = portal.get("client")
            if restart_url and client:
                try:
                    client.get(restart_url)
                    self.last_restart_epoch = now
                    logger.info(
                        "Tunnel #%s restart triggered successfully via watchdog",
                        settings.tunnel_web_service_id,
                    )
                    return {
                        "action": "RESTARTED",
                        "message": (
                            f"Triggered Restart VPN for service #{settings.tunnel_web_service_id}."
                        ),
                    }
                except Exception as e:
                    return {"action": "FAILED", "message": f"Error calling restart URL: {e}"}

        return {
            "action": "NONE",
            "message": f"Portal status is {portal.get('code')}; no restart action required.",
        }


# Global singleton instance
tunnel_watchdog = TunnelWatchdog()
