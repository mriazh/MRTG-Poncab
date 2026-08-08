"""Command-line interface (CLI) for MRTG-Poncab operations."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import threading
from collections.abc import Sequence

import uvicorn

from .auth import ensure_admin_user, hash_password
from .collector import TrafficCollector
from .config import settings
from .db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mrtg_poncab.cli")


def cmd_init_db(args: argparse.Namespace) -> int:
    """Initialize SQLite database schema and seed default administrator."""
    db = Database(settings.database_path)
    logger.info("Initializing database at: %s", settings.database_path)
    db.initialize()
    admin = ensure_admin_user(db, settings)
    logger.info("Database initialized. Administrator user '%s' is ready.", admin["username"])
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    """Create or update a dashboard user password."""
    db = Database(settings.database_path)
    db.initialize()

    username = args.username.strip()
    if not username:
        logger.error("Username cannot be empty")
        return 1

    password = args.password
    if not password:
        password = getpass.getpass(f"Enter password for '{username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            logger.error("Passwords do not match")
            return 1

    hashed = hash_password(password)
    user = db.get_user(username)
    if user:
        # Update existing user's password
        with db.connection() as conn, conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user["id"]))
        logger.info("Password updated successfully for existing user '%s'.", username)
    else:
        user_id = db.create_user(username=username, password_hash=hashed)
        logger.info("Created new user '%s' with id %d.", username, user_id)
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    """Run the continuous traffic collection daemon."""
    logger.info("Starting MRTG collector daemon...")
    collector = TrafficCollector()
    stop_event = threading.Event()

    try:
        collector.run(
            interval=args.interval,
            max_iterations=args.iterations,
            stop_event=stop_event,
        )
    except KeyboardInterrupt:
        logger.info("Collector interrupted by user; shutting down...")
        stop_event.set()
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """Run the FastAPI web dashboard server."""
    host = args.host or settings.web_host
    port = args.port or settings.web_port
    logger.info("Starting web server on http://%s:%d ...", host, port)
    uvicorn.run("mrtg_poncab.web.app:app", host=host, port=port, reload=False)
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    """Run both collector daemon and web dashboard concurrently."""
    stop_event = threading.Event()
    collector = TrafficCollector()

    def collector_worker() -> None:
        try:
            collector.run(interval=args.interval, stop_event=stop_event)
        except Exception as exc:
            logger.error("Collector worker encountered error: %s", exc)

    collector_thread = threading.Thread(
        target=collector_worker, daemon=True, name="CollectorThread"
    )
    collector_thread.start()

    host = args.host or settings.web_host
    port = args.port or settings.web_port
    logger.info("Running unified collector and web service on http://%s:%d", host, port)

    try:
        uvicorn.run("mrtg_poncab.web.app:app", host=host, port=port, reload=False)
    except KeyboardInterrupt:
        logger.info("Shutting down unified services...")
    finally:
        stop_event.set()
        collector_thread.join(timeout=3)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build and return command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="mrtg-poncab",
        description="MRTG-Poncab - MikroTik WAN Traffic Monitoring & Reporting",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-db
    p_init = subparsers.add_parser("init-db", help="Initialize SQLite schema and seed admin")
    p_init.set_defaults(func=cmd_init_db)

    # create-user
    p_user = subparsers.add_parser("create-user", help="Create or update a dashboard user")
    p_user.add_argument("--username", "-u", required=True, help="Username to create or update")
    p_user.add_argument("--password", "-p", help="Password (prompted securely if omitted)")
    p_user.set_defaults(func=cmd_create_user)

    # collect
    p_collect = subparsers.add_parser("collect", help="Run background traffic collector daemon")
    p_collect.add_argument("--interval", "-i", type=int, help="Polling interval in seconds")
    p_collect.add_argument(
        "--iterations", "-n", type=int, help="Maximum polling cycles (for tests)"
    )
    p_collect.set_defaults(func=cmd_collect)

    # web
    p_web = subparsers.add_parser("web", help="Run FastAPI web dashboard")
    p_web.add_argument("--host", help="Binding host IP (default 0.0.0.0)")
    p_web.add_argument("--port", type=int, help="Binding port (default 8000)")
    p_web.set_defaults(func=cmd_web)

    # all
    p_all = subparsers.add_parser("all", help="Run both collector and web server concurrently")
    p_all.add_argument("--host", help="Binding host IP")
    p_all.add_argument("--port", type=int, help="Binding port")
    p_all.add_argument("--interval", "-i", type=int, help="Polling interval in seconds")
    p_all.set_defaults(func=cmd_all)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Main execution entrypoint for CLI."""
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
