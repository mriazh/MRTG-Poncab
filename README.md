# MRTG-Poncab

Enterprise-grade network traffic monitoring, historical analysis, and reporting system for the MikroTik RB960PGS router at **GMF AeroAsia Pondok Cabe**.

The system polls the MikroTik RouterOS API over a dedicated TCP tunnel, records granular time-series traffic samples in an embedded SQLite database (WAL mode), and serves an authenticated web dashboard featuring custom date-range selection, RRDtool-identical graphs, and multi-format reporting exports (PNG, Excel, CSV).

---

## Key Capabilities

- **Direct RouterOS API Polling**: Queries cumulative octet counters (`rx-byte`, `tx-byte`) over TCP port `8728` (or custom tunnel port), bypassing SNMP UDP limitations.
- **Robust Delta Rate Engine**: Computes exact bits per second with counter rollover, router reboot, and rate sanity guards.
- **RRDtool Visual Fidelity**: Generates pixel-perfect telco-style graphs (stepped solid green inbound area `#00CC00`, stepped dark blue outbound line `#0000CC`, high-contrast pink dotted grid `#FFAAAA` at `zorder=3`, 3D chiseled outer bezel, 100% monospace typography, and directional arrows).
- **True RRDtool Dynamic Autoscale**: Implements standard logarithmic `nice_ceiling()` math with 5% headroom and 5 clean horizontal divisions, adapting effortlessly from idle/low traffic to 150 Mbps+ without clipping or flattening.
- **Multi-Timespan Adaptive Locators**: Dynamically adapts time ticks across all ranges: 1-minute ticks for sub-15m, 10-minute ticks for sub-2h, 2-hour ticks for 24h (MRTG Daily standard), and daily ticks for 7d (MRTG Weekly standard).
- **Custom Backdate & Presets**: Flexible range selection including *Hari Ini*, *Kemarin (00:00–23:59)*, *24 Jam Terakhir*, *7 Hari Terakhir*, *Bulan Ini*, and custom down-to-the-minute date-time picker.
- **Reporting & Exports**: Instant downloads of rendered PNG graphs, styled Excel (`.xlsx`) workbooks with metadata banners, and raw CSV files.
- **Session Authentication**: Protected web dashboard with PBKDF2-HMAC-SHA256 password hashing, auto-syncing admin password from `.env`, and "Remember Me" session persistence.
- **Zero External Database Overhead**: Powered by embedded SQLite with Write-Ahead Logging (WAL) for 100% portability across Windows 11 and Debian 13.

---

## Architecture Overview

```
┌──────────────────────────────────────┐
│        MikroTik RB960PGS             │
│    (GMF AeroAsia Pondok Cabe)        │
│    Interface: WAN (INDIBIZ 150M)     │
└──────────────────┬───────────────────┘
                   │ RouterOS API (TCP 8728)
                   ▼
┌──────────────────────────────────────┐
│       MRTG-Poncab Host               │
│   ┌──────────────────────────────┐   │
│   │ Collector Daemon (300s)      │   │
│   └──────────────┬───────────────┘   │
│                  ▼                   │
│   ┌──────────────────────────────┐   │
│   │ SQLite Database (traffic.db) │   │
│   └──────────────┬───────────────┘   │
│                  ▼                   │
│   ┌──────────────────────────────┐   │
│   │ FastAPI Web Dashboard        │   │
│   │ Matplotlib RRDtool Engine    │   │
│   │ Excel & CSV Exporters        │   │
│   └──────────────┬───────────────┘   │
└──────────────────┼───────────────────┘
                   │ HTTP / Web Dashboard
                   ▼
┌──────────────────────────────────────┐
│       Engineer Web Browser           │
│   - Date-Picker & Quick Presets      │
│   - Live In/Out Telemetry Card       │
│   - Download PNG, Excel, CSV         │
└──────────────────────────────────────┘
```

---

## Configuration Reference (`.env`)

Copy `.env.example` to `.env` and adjust the variables:

```ini
# Application
APP_NAME="MRTG-Poncab"
DATABASE_PATH="data/traffic.db"

# MikroTik RouterOS API Settings
ROUTEROS_HOST="192.168.88.1"
ROUTEROS_PORT=8728
ROUTEROS_USERNAME="mrtg"
ROUTEROS_PASSWORD="YourRouterPassword"
ROUTEROS_INTERFACE="WAN"

# Collector Timing (seconds)
POLLING_INTERVAL=60

# Web Dashboard Server
WEB_HOST="0.0.0.0"
WEB_PORT=8000
SECRET_KEY="generate-a-secure-random-key"
SESSION_COOKIE_SECURE=false
SESSION_TTL_SECONDS=28800
REMEMBER_ME_TTL_SECONDS=2592000

# Default Initial Administrator
ADMIN_USERNAME="admin"
ADMIN_PASSWORD="ChangeMeImmediately123!"
```

---

## Local Development & Testing (Windows 11)

### 1. Prerequisites
- Python 3.11+ (Python 3.14 recommended)
- `uv` package manager (`winget install astral-sh.uv`)
- Git

### 2. Setup Environment
```powershell
# Clone or navigate to the repository
cd MRTG-Poncab

# Install all project and development dependencies
uv sync --all-extras

# Copy environment settings
cp .env.example .env
```

### 3. Initialize Database & Admin User
```powershell
uv run mrtg-poncab init-db
```
*(Default user: `admin` / `admin123`)*

To create or update a user password:
```powershell
uv run mrtg-poncab create-user --username engineer --password "YourPassword!"
```

### 4. Run the Application Locally
To run both the background collector and the web server together:
```powershell
uv run mrtg-poncab all --port 8000
```
Open your browser and navigate to: `http://localhost:8000`

### 5. Run Automated Tests & Quality Checks
```powershell
# Run full unit and integration test suite
uv run pytest -v

# Run linting
uv run ruff check

# Run type checks
uv run mypy src
```

---

## Production Deployment (Debian 13)

### 1. System Preparation
```bash
sudo apt update && sudo apt install -y git python3 python3-pip python3-venv curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

### 2. Deploy Project Directory
```bash
cd /home/mriazh
git clone <REPO_URL> MRTG-Poncab
cd MRTG-Poncab

# Install dependencies into virtual environment
uv sync --no-dev
cp .env.example .env
nano .env  # configure actual router password and secret key

# Initialize database
uv run mrtg-poncab init-db
```

### 3. Install Systemd Services (Auto-Run on Reboot)

We provide systemd units in the `systemd/` directory:

#### Option A: Unified Service (Collector + Web Dashboard)
```bash
sudo cp systemd/mrtg-poncab.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mrtg-poncab.service
```

#### Option B: Split Services (Dedicated Collector & Web Workers)
```bash
sudo cp systemd/mrtg-poncab-collector.service /etc/systemd/system/
sudo cp systemd/mrtg-poncab-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mrtg-poncab-collector.service
sudo systemctl enable --now mrtg-poncab-web.service
```

### 4. Check Status and Logs
```bash
sudo systemctl status mrtg-poncab.service
sudo journalctl -u mrtg-poncab.service -f
```

Access dashboard: `http://<SERVER_IP>:8000`

---

## CLI Command Reference

| Command | Description | Example |
| :--- | :--- | :--- |
| `init-db` | Create SQLite schema and seed administrator | `mrtg-poncab init-db` |
| `create-user` | Create or update a dashboard user | `mrtg-poncab create-user -u engineer -p pass` |
| `collect` | Run background traffic collection loop | `mrtg-poncab collect --interval 300` |
| `web` | Start FastAPI web server | `mrtg-poncab web --host 0.0.0.0 --port 8000` |
| `all` | Run collector and web server concurrently | `mrtg-poncab all --port 8000` |

---

## License & Attribution
Proprietary network monitoring tool developed for GMF AeroAsia Pondok Cabe. Graphing aesthetics designed to match RRDtool visual standards.
