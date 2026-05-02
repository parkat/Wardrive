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

    try:
        with sqlite3.connect(str(DB_PATH)) as db:
            db.row_factory = sqlite3.Row
            # Safe: table name is validated against whitelist above
            query = f"SELECT * FROM {table}"
            conditions = []
            params = []

            if vendor:
                conditions.append("manufacturer LIKE ?")
                params.append(f"%{vendor}%")

            if rssi is not None:
                try:
                    rssi = float(rssi)
                    conditions.append("max_rssi_dbm <= ?")
                    params.append(rssi)
                except ValueError:
                    pass

            if date:
                conditions.append("first_seen_utc >= ?")
                params.append(f"{date} 00:00:00")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY max_rssi_dbm DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = db.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logging.error(f"Database error: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
