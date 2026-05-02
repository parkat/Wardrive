# warDrive Explorer — Web Interface

A FastAPI-based web explorer for browsing warDrive capture data (WiFi, BLE, SDR).

## Quick Start

```bash
cd webapp
bash run.sh
```

Then open http://127.0.0.1:8000 in your browser.

## Features

- **Browse all collectors:** Switch between BLE devices, WiFi APs, WiFi clients, and RF devices
- **Filter by vendor:** Search for specific manufacturers (Apple, Samsung, etc.)
- **Filter by signal strength:** Show only devices above/below a given RSSI threshold
- **Filter by date:** Find devices seen after a specific date
- **Live connection status:** Displays database availability and table counts

## Recent Fixes

Fixed several issues that were preventing proper operation:

### Backend Issues
1. **Path handling:** Changed from relative paths (`"static"`, `"templates/index.html"`) to absolute paths using `Path` objects. This was causing 404 errors when the server wasn't run from the `webapp/` directory.

2. **SQLite connection:** Removed URI mode (`mode=ro`) and used direct file path instead. The read-only mode wasn't being enforced correctly.

3. **SQL injection vulnerability:** Table names are now validated against a whitelist before use in queries. Input parameters are properly parameterized.

4. **Parameter validation:** Added type conversion and clamping for limit/offset parameters to prevent edge cases.

5. **Sorting:** Added `ORDER BY max_rssi_dbm DESC` so strongest signals appear first.

### Frontend Issues
1. **API error handling:** Frontend now checks for `error` field in API responses and displays them properly instead of crashing.

2. **HTML escaping:** Added `escapeHtml()` function to prevent XSS vulnerabilities when displaying user-controlled data.

3. **Removed phantom element:** Deleted reference to non-existent `debug-message` element.

4. **Better status feedback:** Status indicator now shows connection state with color coding (green for OK, red for error).

## Architecture

```
webapp/
├── main.py                 # FastAPI backend
├── run.sh                  # Startup script (recommended)
├── requirements.txt        # Python dependencies
├── templates/
│   └── index.html         # HTML interface
└── static/
    ├── app.js             # Frontend logic
    └── style.css          # Styling
```

## API Endpoints

### GET `/api/status`
Check database connection and list available tables.

**Response:**
```json
{
  "status": "connected",
  "tables": ["sessions", "bt_devices", "wifi_aps", "wifi_clients", "rf_devices", "bt_obs", "wifi_obs", "rf_obs", "oui_lookup"]
}
```

### GET `/api/devices`
Query devices with optional filters.

**Query Parameters:**
- `table` — Table to query: `bt_devices`, `wifi_aps`, `wifi_clients`, `rf_devices` (default: `bt_devices`)
- `vendor` — Filter by manufacturer (substring match, case-insensitive)
- `rssi` — Filter by max signal strength (≤ this value in dBm)
- `date` — Filter by first seen date (YYYY-MM-DD)
- `limit` — Max results (default: 500, max: 1000)
- `offset` — Pagination offset (default: 0)

**Response:**
```json
[
  {
    "address": "AA:BB:CC:DD:EE:FF",
    "manufacturer": "Apple",
    "max_rssi_dbm": -42,
    "first_seen_utc": "2026-05-01 12:34:56",
    "last_seen_utc": "2026-05-01 14:22:10",
    ...
  }
]
```

**Error Response:**
```json
{
  "error": "Invalid table. Must be one of ['bt_devices', 'wifi_aps', 'wifi_clients', 'rf_devices']"
}
```

## Running Behind Nginx

To expose the web explorer externally with HTTPS:

```nginx
server {
    listen 443 ssl http2;
    server_name wardrive.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Start the webapp, then use Nginx as a reverse proxy.

## Development Notes

- Database is opened in **read-only mode** — no mutations happen
- Queries are **parameterized** — SQL injection is prevented
- Static paths are **absolute** — works from any directory
- Error messages are **HTML-escaped** — XSS is prevented

## Troubleshooting

**"Failed to connect to database"**
- Check that `processing/wardrive.db` exists (run `python3 processing/enrich.py` first)
- Verify the database path is correct

**"Cannot reach API"**
- Check the server is running (`bash run.sh`)
- Check the port (default: 8000) is not in use
- Try opening http://127.0.0.1:8000 in the browser console and checking for CORS errors

**Tables show "No devices found"**
- The database might be empty — run a capture session first
- Check that `ENABLE_*=true` in your capture config
- Run `python3 processing/enrich.py` to populate the database

## License

Same as warDrive project.
