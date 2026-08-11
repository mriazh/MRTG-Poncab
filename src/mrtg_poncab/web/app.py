"""FastAPI web application and reporting API for MRTG-Poncab."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..auth import (
    SESSION_COOKIE_NAME,
    create_user_session,
    ensure_admin_user,
    get_current_user_optional,
    get_db,
    require_authenticated_user,
    revoke_session,
    verify_password,
)
from ..config import settings
from ..console import console_manager, execute_routeros_command
from ..db import Database
from ..export import export_csv, export_excel
from ..graph_renderer import format_engineering_bits, render_traffic_graph

WIB_OFFSET = timedelta(hours=7)
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def _now_wib() -> datetime:
    """Return current datetime in WIB (UTC+7)."""
    return datetime.now(UTC) + WIB_OFFSET


def resolve_time_range(
    preset: str | None = None,
    start_str: str | None = None,
    end_str: str | None = None,
    fullday: str | None = None,
) -> tuple[int, int, str, str, str]:
    """Resolve preset or custom datetime inputs into start/end epochs and labels.

    Returns:
        (start_epoch, end_epoch, display_start, display_end, active_preset).
        All human-facing timestamps assume WIB (UTC+7).
    """
    now = _now_wib()

    if fullday:
        # Full day single-date selection (00:00:00 to 23:59:59 WIB)
        try:
            f_clean = fullday.strip()[:10]
            d = datetime.strptime(f_clean, "%Y-%m-%d")
            st_wib = datetime(d.year, d.month, d.day, 0, 0, 0)
            et_wib = datetime(d.year, d.month, d.day, 23, 59, 59)
            active = "fullday"
        except Exception:
            st_wib = datetime(now.year, now.month, now.day, 0, 0, 0)
            et_wib = now
            active = "today"
    elif preset == "1h":
        et_wib = now
        st_wib = now - timedelta(hours=1)
        active = "1h"
    elif preset == "3h":
        et_wib = now
        st_wib = now - timedelta(hours=3)
        active = "3h"
    elif preset == "6h":
        et_wib = now
        st_wib = now - timedelta(hours=6)
        active = "6h"
    elif preset == "12h":
        et_wib = now
        st_wib = now - timedelta(hours=12)
        active = "12h"
    elif preset == "yesterday":
        yesterday = now - timedelta(days=1)
        st_wib = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0)
        et_wib = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59)
        active = "yesterday"
    elif preset == "24h":
        et_wib = now
        st_wib = now - timedelta(hours=24)
        active = "24h"
    elif preset == "7d":
        et_wib = now
        st_wib = now - timedelta(days=7)
        active = "7d"
    elif preset == "month":
        st_wib = datetime(now.year, now.month, 1, 0, 0, 0)
        et_wib = now
        active = "month"
    elif preset == "today":
        st_wib = datetime(now.year, now.month, now.day, 0, 0, 0)
        et_wib = now
        active = "today"
    elif start_str and end_str:
        # Custom range from form (format: YYYY-MM-DDTHH:MM or full ISO)
        st_clean = start_str.strip().replace("T", " ")
        et_clean = end_str.strip().replace("T", " ")
        try:
            st_wib = datetime.fromisoformat(st_clean)
            et_wib = datetime.fromisoformat(et_clean)
            active = "custom"
        except Exception:
            # Fallback to today on parse failure
            st_wib = datetime(now.year, now.month, now.day, 0, 0, 0)
            et_wib = now
            active = "today"
    else:
        # Default: today (00:00:00 to now)
        st_wib = datetime(now.year, now.month, now.day, 0, 0, 0)
        et_wib = now
        active = "today"

    # Convert WIB naive datetimes to UTC epochs
    # st_wib is at UTC+7, so epoch = (st_wib - 7 hours) in UTC
    st_utc = st_wib - WIB_OFFSET
    et_utc = et_wib - WIB_OFFSET
    start_epoch = int(st_utc.replace(tzinfo=UTC).timestamp())
    end_epoch = int(et_utc.replace(tzinfo=UTC).timestamp())

    display_st = st_wib.strftime("%Y-%m-%d %H:%M:%S")
    display_et = et_wib.strftime("%Y-%m-%d %H:%M:%S")

    return start_epoch, end_epoch, display_st, display_et, active


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan context: seeds default admin user on startup."""
    db = Database(settings.database_path)
    ensure_admin_user(db, settings)
    yield


app = FastAPI(
    title="MRTG-Poncab",
    description="MikroTik WAN traffic monitoring, historical analysis, and reporting",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# 1. Authentication Endpoints
@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = "/",
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
) -> Any:
    """Render login form or redirect to dashboard if already authenticated."""
    if current_user is not None:
        return RedirectResponse(url=next, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next_url": next,
            "error": None,
            "current_user": None,
        },
    )


@app.post("/login")
def process_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(False),
    next: str = Form("/"),
    db: Database = Depends(get_db),
) -> Any:
    """Validate credentials, issue session cookie, and redirect."""
    user = db.get_user(username.strip())
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_url": next,
                "error": "Invalid username or password",
                "current_user": None,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token, ttl = create_user_session(
        database=db,
        user_id=user["id"],
        username=user["username"],
        remember_me=remember_me,
    )

    response = RedirectResponse(url=next, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
    return response


@app.get("/logout")
@app.post("/logout")
def process_logout(
    request: Request,
    db: Database = Depends(get_db),
) -> Response:
    """Revoke current session and redirect to login."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        revoke_session(db, token)

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response


# 2. Main Dashboard Endpoint
@app.get("/", response_class=HTMLResponse)
def dashboard_view(
    request: Request,
    preset: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    fullday: str | None = Query(None),
    current_user: dict[str, Any] = Depends(require_authenticated_user),
    db: Database = Depends(get_db),
) -> Any:
    """Render authenticated monitoring dashboard with telemetry and traffic graph."""
    start_epoch, end_epoch, display_st, display_et, active_preset = resolve_time_range(
        preset=preset,
        start_str=start,
        end_str=end,
        fullday=fullday,
    )

    # Telemetry card data
    latest = db.get_latest_sample()
    if latest:
        latest_status = latest["status"]
        latest_uptime = latest.get("uptime") or "unknown"
        ts_utc = datetime.fromtimestamp(latest["epoch"], tz=UTC)
        ts_wib = ts_utc + WIB_OFFSET
        latest_ts_wib = ts_wib.strftime("%Y-%m-%d %H:%M:%S WIB")
        cur_in = format_engineering_bits(latest["rx_bps"])
        cur_out = format_engineering_bits(latest["tx_bps"])
    else:
        latest_status = "NO DATA"
        latest_uptime = "unknown"
        latest_ts_wib = "No data recorded yet"
        cur_in = "0 b"
        cur_out = "0 b"

    # Query strings for graph & downloads
    if active_preset == "custom":
        range_params = f"start={start}&end={end}"
    elif active_preset == "fullday":
        range_params = f"fullday={fullday or display_st[:10]}"
    else:
        range_params = f"preset={active_preset}"

    graph_img_url = f"/api/graph.png?{range_params}"
    export_png_url = f"/api/graph.png?{range_params}&download=1"
    export_excel_url = f"/api/export/excel?{range_params}"
    export_csv_url = f"/api/export/csv?{range_params}"

    # Default custom input values (YYYY-MM-DD HH:MM)
    c_start = display_st[:16]
    c_end = display_et[:16]

    raw_recent = db.get_recent_traffic_samples(limit=10)
    formatted_recent: list[dict[str, Any]] = []
    for sample in raw_recent:
        sample_utc = datetime.fromtimestamp(sample["epoch"], tz=UTC)
        sample_wib = sample_utc + WIB_OFFSET
        formatted_recent.append(
            {
                "timestamp_wib": sample_wib.strftime("%Y-%m-%d %H:%M:%S"),
                "status": sample["status"],
                "inbound_formatted": format_engineering_bits(sample["rx_bps"]),
                "outbound_formatted": format_engineering_bits(sample["tx_bps"]),
                "uptime": sample.get("uptime") or "unknown",
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "current_user": current_user,
            "active_preset": active_preset,
            "fullday_val": (fullday or display_st[:10]) if active_preset == "fullday" else "",
            "custom_start": c_start,
            "custom_end": c_end,
            "latest_status": latest_status,
            "latest_uptime": latest_uptime,
            "latest_timestamp_wib": latest_ts_wib,
            "current_in_formatted": cur_in,
            "current_out_formatted": cur_out,
            "recent_samples": formatted_recent,
            "graph_title": (
                f"Traffic {settings.routeros_interface} (INDIBIZ 150M) - GMF Pondok Cabe"
            ),
            "graph_img_url": graph_img_url,
            "export_png_url": export_png_url,
            "export_excel_url": export_excel_url,
            "export_csv_url": export_csv_url,
            "display_start": display_st,
            "display_end": display_et,
        },
    )


# 3. Telemetry & Graph APIs
@app.get("/api/telemetry")
def api_telemetry(
    current_user: dict[str, Any] = Depends(require_authenticated_user),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Return JSON telemetry summary."""
    latest = db.get_latest_sample()
    raw_recent = db.get_recent_traffic_samples(limit=10)
    formatted_recent: list[dict[str, Any]] = []
    for sample in raw_recent:
        sample_utc = datetime.fromtimestamp(sample["epoch"], tz=UTC)
        sample_wib = sample_utc + WIB_OFFSET
        formatted_recent.append(
            {
                "timestamp_wib": sample_wib.strftime("%Y-%m-%d %H:%M:%S"),
                "status": sample["status"],
                "inbound_formatted": format_engineering_bits(sample["rx_bps"]),
                "outbound_formatted": format_engineering_bits(sample["tx_bps"]),
                "uptime": sample.get("uptime") or "unknown",
            }
        )

    if not latest:
        return {
            "status": "NO DATA",
            "uptime": "unknown",
            "latest_timestamp_wib": "No data recorded yet",
            "current_in_formatted": "0 b",
            "current_out_formatted": "0 b",
            "rx_bps": 0.0,
            "tx_bps": 0.0,
            "recent_samples": formatted_recent,
        }

    ts_utc = datetime.fromtimestamp(latest["epoch"], tz=UTC)
    ts_wib = ts_utc + WIB_OFFSET
    return {
        "status": latest["status"],
        "uptime": latest.get("uptime") or "unknown",
        "latest_timestamp_wib": ts_wib.strftime("%Y-%m-%d %H:%M:%S WIB"),
        "current_in_formatted": format_engineering_bits(latest["rx_bps"]),
        "current_out_formatted": format_engineering_bits(latest["tx_bps"]),
        "rx_bps": latest["rx_bps"],
        "tx_bps": latest["tx_bps"],
        "recent_samples": formatted_recent,
    }


@app.get("/api/graph.png")
def api_graph_png(
    preset: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    fullday: str | None = Query(None),
    width: int = Query(800),
    height: int = Query(360),
    download: bool = Query(False),
    current_user: dict[str, Any] = Depends(require_authenticated_user),
    db: Database = Depends(get_db),
) -> Response:
    """Render traffic graph for the selected time range."""
    start_epoch, end_epoch, display_st, display_et, _ = resolve_time_range(
        preset=preset,
        start_str=start,
        end_str=end,
        fullday=fullday,
    )

    samples = db.get_traffic_samples(start=start_epoch, end=end_epoch)
    png_bytes = render_traffic_graph(
        samples=samples,
        title=f"Traffic {settings.routeros_interface} (INDIBIZ 150M) - GMF Pondok Cabe",
        start_time=display_st,
        end_time=display_et,
        start_epoch=start_epoch,
        end_epoch=end_epoch,
        width_px=max(400, min(width, 2400)),
        height_px=max(200, min(height, 1200)),
    )

    headers = {}
    if download:
        safe_st = display_st.replace(":", "").replace(" ", "_")
        safe_et = display_et.replace(":", "").replace(" ", "_")
        filename = f"mrtg_wan_{safe_st}_to_{safe_et}.png"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return Response(content=png_bytes, media_type="image/png", headers=headers)


# 4. Reporting & Data Exports
@app.get("/api/export/csv")
def api_export_csv(
    preset: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    fullday: str | None = Query(None),
    current_user: dict[str, Any] = Depends(require_authenticated_user),
    db: Database = Depends(get_db),
) -> Response:
    """Export tabular traffic records as CSV."""
    start_epoch, end_epoch, display_st, display_et, _ = resolve_time_range(
        preset=preset,
        start_str=start,
        end_str=end,
        fullday=fullday,
    )

    samples = db.get_traffic_samples(start=start_epoch, end=end_epoch)
    csv_bytes = export_csv(samples, interface_name=settings.routeros_interface)

    safe_st = display_st.replace(":", "").replace(" ", "_")
    safe_et = display_et.replace(":", "").replace(" ", "_")
    filename = f"traffic_wan_{safe_st}_to_{safe_et}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/excel")
def api_export_excel(
    preset: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    fullday: str | None = Query(None),
    current_user: dict[str, Any] = Depends(require_authenticated_user),
    db: Database = Depends(get_db),
) -> Response:
    """Export formatted traffic report as Excel (.xlsx)."""
    start_epoch, end_epoch, display_st, display_et, _ = resolve_time_range(
        preset=preset,
        start_str=start,
        end_str=end,
        fullday=fullday,
    )

    samples = db.get_traffic_samples(start=start_epoch, end=end_epoch)
    excel_bytes = export_excel(
        samples=samples,
        interface_name=settings.routeros_interface,
        title=f"Traffic Report - WAN {settings.routeros_interface} - GMF Pondok Cabe",
        start_time=f"{display_st} WIB",
        end_time=f"{display_et} WIB",
    )

    safe_st = display_st.replace(":", "").replace(" ", "_")
    safe_et = display_et.replace(":", "").replace(" ", "_")
    filename = f"traffic_wan_{safe_st}_to_{safe_et}.xlsx"

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# 5. RouterOS Remote Console Endpoints
class ConsoleAuthRequest(BaseModel):
    username: str
    password: str


class ConsoleExecuteRequest(BaseModel):
    token: str
    command: str


class ConsoleTerminateRequest(BaseModel):
    token: str


@app.get("/console", response_class=HTMLResponse)
def console_view(
    request: Request,
    current_user: dict[str, Any] = Depends(require_authenticated_user),
) -> Any:
    """Render authenticated RouterOS Web Console page."""
    return templates.TemplateResponse(
        request=request,
        name="console.html",
        context={
            "current_user": current_user,
            "router_target": f"{settings.routeros_host}:{settings.routeros_port}",
        },
    )


@app.post("/api/console/auth")
def api_console_auth(
    payload: ConsoleAuthRequest,
    current_user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """Authenticate directly with MikroTik RouterOS API."""
    success, token_or_err, identity = console_manager.authenticate(
        host=settings.routeros_host,
        port=settings.routeros_port,
        username=payload.username,
        password=payload.password,
    )
    if not success:
        return {"success": False, "error": token_or_err}

    return {
        "success": True,
        "token": token_or_err,
        "identity": identity,
        "username": payload.username,
        "prompt": f"[{payload.username}@{identity}] > ",
    }


@app.post("/api/console/execute")
def api_console_execute(
    payload: ConsoleExecuteRequest,
    current_user: dict[str, Any] = Depends(require_authenticated_user),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Execute a RouterOS command within an authenticated console session."""
    session = console_manager.get_session(payload.token)
    if not session:
        return {
            "success": False,
            "error": "Console session expired or invalid. Please login again.",
            "session_expired": True,
        }

    cmd = payload.command.strip()
    success, output = execute_routeros_command(session, cmd)

    # Log to SQLite audit trail
    if cmd:
        db.insert_console_log(
            username=session.username,
            command=cmd,
            status="OK" if success else "ERROR",
            output_preview=output,
        )

    ts_wib = _now_wib().strftime("%H:%M:%S")
    return {
        "success": success,
        "output": output,
        "timestamp": ts_wib,
        "prompt": f"[{session.username}@{session.identity}] > ",
    }


@app.post("/api/console/terminate")
def api_console_terminate(
    payload: ConsoleTerminateRequest,
) -> dict[str, Any]:
    """Immediately invalidate an active console session token."""
    console_manager.terminate(payload.token)
    return {"success": True}


@app.get("/api/console/logs")
def api_console_logs(
    limit: int = Query(50),
    current_user: dict[str, Any] = Depends(require_authenticated_user),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Return recent console audit logs."""
    logs = db.get_recent_console_logs(limit=limit)
    return {"logs": logs}


# 6. Tunnel Watchdog Diagnostics & Auto-Healing
@app.get("/api/tunnel/diagnose")
def api_tunnel_diagnose(
    current_user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """Perform live triangulation diagnostic on the RouterOS tunnel connection."""
    from ..tunnel_watchdog import tunnel_watchdog

    diag = tunnel_watchdog.diagnose()
    portal_info = None
    if settings.tunnel_web_email and settings.tunnel_web_password:
        portal_info = tunnel_watchdog.inspect_member_portal()
        # Remove client object before JSON serialization
        if portal_info and "client" in portal_info:
            portal_info.pop("client", None)
            portal_info.pop("raw_body", None)

    return {
        "diagnosis": diag,
        "portal": portal_info,
        "target": f"{settings.routeros_host}:{settings.routeros_port}",
    }


@app.post("/api/tunnel/restart")
def api_tunnel_restart(
    current_user: dict[str, Any] = Depends(require_authenticated_user),
) -> dict[str, Any]:
    """Safely trigger VPN restart with cooldown guard."""
    from ..tunnel_watchdog import tunnel_watchdog

    return tunnel_watchdog.auto_heal_if_needed()
