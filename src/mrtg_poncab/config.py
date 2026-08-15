"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the collector and web application.

    Environment variables use the same names as the fields, in upper case.
    Relative database paths are intentionally resolved at use time so tests and
    deployments can choose their own working directory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_title: str = Field(
        default="MRTG Traffic Monitor",
        validation_alias=AliasChoices("app_title", "app_name"),
    )
    site_name: str = Field(
        default="Enterprise Gateway",
        validation_alias=AliasChoices("site_name", "site"),
    )
    location_name: str = Field(
        default="Branch Office",
        validation_alias=AliasChoices("location_name", "location"),
    )
    uplink_name: str = Field(
        default="Main Uplink (150 Mbps)",
        validation_alias=AliasChoices("uplink_name", "uplink"),
    )
    database_path: Path = Field(
        default=Path("data/traffic.db"),
        validation_alias=AliasChoices("database_path", "db_path"),
    )

    routeros_host: str = Field(
        default="192.168.88.1",
        validation_alias=AliasChoices("routeros_host", "router_host", "mikrotik_host"),
    )
    routeros_port: int = Field(
        default=8728,
        validation_alias=AliasChoices("routeros_port", "router_port", "mikrotik_port"),
    )
    routeros_username: str = Field(
        default="mrtg",
        validation_alias=AliasChoices("routeros_username", "router_username"),
    )
    routeros_password: str = Field(
        default="",
        validation_alias=AliasChoices("routeros_password", "router_password"),
    )
    routeros_interface: str = Field(
        default="WAN",
        validation_alias=AliasChoices("routeros_interface", "router_interface", "interface"),
    )

    polling_interval: int = Field(
        default=60,
        validation_alias=AliasChoices("polling_interval", "poll_interval"),
    )
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    secret_key: str | None = None
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 28_800
    remember_me_ttl_seconds: int = 2_592_000

    admin_username: str = "admin"
    admin_password: str | None = None

    # tunnel.web.id watchdog & auto-healing
    tunnel_web_email: str | None = None
    tunnel_web_password: str | None = None
    tunnel_web_service_id: str | None = None
    tunnel_auto_restart: bool = False
    tunnel_restart_cooldown_minutes: int = 30

    # WhatsApp GOWA Alert Notification
    wa_alert_enabled: bool = False
    wa_gateway_url: str = "http://localhost:3000"
    wa_device_id: str | None = None
    wa_target_jid: str | None = None
    wa_fail_threshold: int = 2

    @property
    def poll_interval(self) -> int:
        """Backward-compatible short name used by collector code."""

        return self.polling_interval

    @property
    def db_path(self) -> Path:
        """Backward-compatible short name for the database path."""

        return self.database_path

    @property
    def app_name(self) -> str:
        """Backward-compatible property returning app_title."""

        return self.app_title


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the current process."""

    return Settings()


settings = get_settings()

__all__ = ["Settings", "get_settings", "settings"]
