import logging
import sqlite3
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from contextlib import asynccontextmanager

app = FastAPI(title="warDrive Explorer")

WEBAPP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = WEBAPP_ROOT.parent
DB_PATH = PROJECT_ROOT / "processing" / "wardrive.db"

@asynccontextmanager
async def lifespan(app):
    print(f"[SYSTEM] Starting warDrive Explorer")
    print(f"[SYSTEM] DB Path: {DB_PATH}")
    print(f"[SYSTEM] DB Exists: {DB_PATH.exists()}")

    if DB_PATH.exists():
        try:
            with sqlite3.connect(str(DB_PATH)) as db:
                count = db.execute("SELECT COUNT(*) FROM bt_devices").fetchone()[0]
                print(f"[SYSTEM] bt_devices count: {count}")
                print(f"[SYSTEM] Loaded Routes: {[r.path for r in app.routes if hasattr(r, 'methods')]}")
        except Exception as e:
            print(f"[SYSTEM] Error checking DB: {e}")
    yield

app.router.lifespan_context = lifespan
app.mount("/static", StaticFiles(directory=str(WEBAPP_ROOT / "static")), name="static")

@app.get("/")
async def index():
    return FileResponse(WEBAPP_ROOT / "templates" / "index.html")

@app.get("/api/status")
async def get_db_status():
    if not DB_PATH.exists():
        return {"status": "db_not_found", "message": f"Database not found at {DB_PATH}"}
    try:
        with sqlite3.connect(str(DB_PATH)) as db:
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
            table_names = [row[0] for row in tables]
            return {"status": "connected", "tables": table_names}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/devices")
async def get_devices(
    table: str = "bt_devices",
    vendor: str = None,
    rssi: float = None,
    date: str = None,
    limit: int = 500,
    offset: int = 0
):
    allowed_tables = ["bt_devices", "wifi_aps", "wifi_clients", "rf_devices"]
    if table not in allowed_tables:
        return {"error": f"Invalid table. Must be one of {allowed_tables}"}

    if not DB_PATH.exists():
        return {"error": "Database not found"}

    # Clamp limit to reasonable value
    limit = min(int(limit), 1000)
    offset = max(int(offset), 0)

    # Table-specific column mappings
    table_schemas = {
        "bt_devices": {
            "primary_key": "address",
            "signal_column": "max_rssi_dbm",
            "vendor_column": "manufacturer",
            "first_seen": "first_seen_utc",
            "last_seen": "last_seen_utc",
        },
        "wifi_aps": {
            "primary_key": "bssid",
            "signal_column": "max_signal_dbm",
            "vendor_column": None,  # no vendor column
            "first_seen": "first_seen_utc",
            "last_seen": "last_seen_utc",
        },
        "wifi_clients": {
            "primary_key": "mac",
            "signal_column": None,  # no signal column
            "vendor_column": None,
            "first_seen": "first_seen_utc",
            "last_seen": "last_seen_utc",
        },
        "rf_devices": {
            "primary_key": "device_id",
            "signal_column": None,
            "vendor_column": None,
            "first_seen": "first_seen_utc",
            "last_seen": "last_seen_utc",
        },
    }

    schema = table_schemas.get(table)
    if not schema:
        return {"error": f"Unknown table schema: {table}"}

    try:
        with sqlite3.connect(str(DB_PATH)) as db:
            db.row_factory = sqlite3.Row
            # Safe: table name is validated against whitelist above
            query = f"SELECT * FROM {table}"
            conditions = []
            params = []

            # Vendor filter (only if table has vendor column)
            if vendor and schema["vendor_column"]:
                conditions.append(f"{schema['vendor_column']} LIKE ?")
                params.append(f"%{vendor}%")

            # RSSI filter (only if table has signal column)
            if rssi is not None and schema["signal_column"]:
                try:
                    rssi = float(rssi)
                    conditions.append(f"{schema['signal_column']} <= ?")
                    params.append(rssi)
                except ValueError:
                    pass

            # Date filter (works on all tables)
            if date:
                conditions.append(f"{schema['first_seen']} >= ?")
                params.append(f"{date} 00:00:00")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            # Sort by signal if available, otherwise by last seen
            if schema["signal_column"]:
                query += f" ORDER BY {schema['signal_column']} DESC"
            else:
                query += f" ORDER BY {schema['last_seen']} DESC"

            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = db.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Database error: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
