"""Unit tests for command-line interface (CLI)."""

from __future__ import annotations

from pathlib import Path

from mrtg_poncab.auth import verify_password
from mrtg_poncab.cli import build_parser, main
from mrtg_poncab.config import settings
from mrtg_poncab.db import Database


def test_cli_parser_commands() -> None:
    """Ensure argument parser recognizes subcommands and options."""
    parser = build_parser()

    # init-db
    args_init = parser.parse_args(["init-db"])
    assert args_init.command == "init-db"

    # create-user
    args_user = parser.parse_args(["create-user", "-u", "engineer", "-p", "secret123"])
    assert args_user.command == "create-user"
    assert args_user.username == "engineer"
    assert args_user.password == "secret123"

    # collect
    args_col = parser.parse_args(["collect", "-i", "60", "-n", "5"])
    assert args_col.command == "collect"
    assert args_col.interval == 60
    assert args_col.iterations == 5

    # web
    args_web = parser.parse_args(["web", "--host", "127.0.0.1", "--port", "9000"])
    assert args_web.command == "web"
    assert args_web.host == "127.0.0.1"
    assert args_web.port == 9000


def test_cli_init_db_and_create_user(tmp_path: Path) -> None:
    """Test CLI execution of init-db and create-user commands."""
    db_path = tmp_path / "cli_test.db"
    settings.database_path = db_path

    # Run init-db
    ret_init = main(["init-db"])
    assert ret_init == 0

    db = Database(db_path)
    admin = db.get_user("admin")
    assert admin is not None

    # Create new operator user
    ret_user = main(["create-user", "-u", "operator", "-p", "OperatorPass1!"])
    assert ret_user == 0
    op = db.get_user("operator")
    assert op is not None
    assert verify_password("OperatorPass1!", op["password_hash"]) is True

    # Update operator password
    ret_update = main(["create-user", "-u", "operator", "-p", "NewPass2!"])
    assert ret_update == 0
    op_updated = db.get_user("operator")
    assert op_updated is not None
    assert verify_password("NewPass2!", op_updated["password_hash"]) is True
