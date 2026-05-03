# Analytics Dashboard Implementation Summary

## Overview
A comprehensive Analytics dashboard has been added to the warDrive Explorer web application, providing visualization of collected RF/WiFi/BLE data patterns with real-time updates.

## Files Created

### 1. `/home/parkat/warDrive/webapp/templates/analytics.html`
- New Analytics page template with responsive design
- Integrates Chart.js via CDN (v4.4.0 from jsdelivr)
- Features 6 interactive visualizations
- Matches existing dark theme with green/cyan accents
- Auto-refresh every 30 seconds

## Files Modified

### 1. `/home/parkat/warDrive/webapp/main.py`
- **New Route**: `@app.get("/analytics")` - Serves analytics.html
- **New API Endpoint**: `@app.get("/api/analytics")` - Returns aggregated statistics
  - Queries database for all analytics data
  - Returns formatted JSON with all visualization datasets
  - Includes comprehensive error handling

### 2. `/home/parkat/warDrive/webapp/templates/index.html`
- Added "Analytics" navigation link in header
- Link placed between "Map" and "Report" for logical flow

## API Endpoint: `/api/analytics`

Returns JSON with the following structure:

```json
{
  "signal_strength": {
    "ble": [
      {"range": "Weak (< -85)", "count": 100},
      {"range": "Fair (-85 to -70)", "count": 250},
      ...
    ],
    "wifi": [...]
  },
  "device_types": [
    {"type": "Phone", "count": 450},
    {"type": "Router", "count": 200},
    ...
  ],
  "hourly_discovery": [
    {
      "hour": "2026-05-02 14:00:00",
      "ble": 145,
      "wifi_ap": 89,
      "rf": 5
    },
    ...
  ],
  "wifi_encryption": [
    {"encryption": "WPA2", "count": 320},
    ...
  ],
  "top_manufacturers": [
    {"manufacturer": "Apple", "count": 580},
    ...
  ],
  "session_comparison": {
    "ble_per_session": [145, 234, 189, ...],
    "wifi_per_session": [89, 156, 201, ...],
    "avg_ble": 189.3,
    "avg_wifi": 148.7
  }
}
```

## Dashboard Visualizations

### 1. BLE Signal Strength Distribution (Horizontal Bar Chart)
- Categorizes BLE devices by signal range
- Ranges: Strong (-50 to 0), Good (-70 to -50), Fair (-85 to -70), Weak (< -85)
- Color: Green (#00ff88)

### 2. WiFi AP Signal Strength Distribution (Horizontal Bar Chart)
- Categorizes WiFi access points by signal range
- Same ranges as BLE
- Color: Cyan (#00d4ff)

### 3. Device Type Distribution (Doughnut Chart)
- Shows aggregate counts: BLE, WiFi AP, WiFi Client, RF Device
- Color palette: Green, Cyan, Purple, Amber

### 4. WiFi Encryption Distribution
- Top-10 encryption types seen across all APs (WPA2, WPA3, Open, etc.)
- Sourced from `wifi_aps.encryption`

### 5. Devices Discovered Per Hour (Line Chart)
- Timeline showing device discovery rate across all sessions
- Three data series: BLE, WiFi APs, RF Devices
- X-axis: Time (hourly buckets)
- Y-axis: Number of unique devices discovered

### 6. Top 10 Manufacturers by Device Count (Horizontal Bar Chart)
- Ranks manufacturers by total BLE devices discovered
- Sourced from `bt_devices.manufacturer` (OUI-resolved)
- Color: Green (#00ff88)

### 7. Devices Per Session - BLE vs WiFi APs (Grouped Bar Chart)
- Compares discovery rates between BLE and WiFi per session
- Shows average statistics:
  - Avg BLE devices per session
  - Avg WiFi APs per session

### 8. Summary Statistics (4-Card Panel)
- Avg BLE/Session
- Avg WiFi APs/Session
- Total Device Types
- Top Manufacturer

## Design & Theme Consistency

- **Font**: JetBrains Mono (monospace, consistent with app)
- **Colors**: Matches existing theme
  - Primary accent: #00ff88 (green)
  - Secondary accent: #00d4ff (cyan)
  - Background: #0a0a0a, #111827
  - Text: #e2e8f0
- **Layout**: Responsive grid layout
  - Desktop: 2-column layout for signal distributions
  - Tablet: 1-column layout
  - Mobile: Full-width single charts
- **Status Indicator**: Real-time connection status in header

## Data Sources

All data aggregated from the warDrive database:
- **bt_devices** & **bt_obs**: BLE device metrics
- **wifi_aps** & **wifi_obs**: WiFi AP metrics
- **wifi_clients**: WiFi client device metrics
- **rf_devices** & **rf_obs**: RF device metrics
- **sessions**: Session metadata for comparison

## Database Queries

Each visualization uses optimized SQL queries:

1. **Signal distributions**: CASE-based categorization with COUNT aggregation
2. **Device types**: UNION ALL aggregation across all device tables
3. **Hourly discovery**: Time-series with DISTINCT device counting
4. **Manufacturers**: Top-N ranking with aggregation
5. **Session comparison**: LEFT JOINs for per-session metrics

## Frontend Features

- **Automatic Loading**: Charts render on page load
- **Auto-Refresh**: Every 30 seconds via setInterval
- **Error Handling**: Displays error messages if API fails
- **Status Indicator**: Shows "Connecting..." → "Connected" → "Error"
- **Chart Management**: Properly destroys old charts before rendering new ones
- **Responsive Design**: Adapts to mobile, tablet, desktop screens

## Performance Notes

- All queries use indexed columns where available
- DISTINCT operations are optimized for the observation tables
- Hourly aggregation reduces data volume for timeline visualization
- Top-N limits prevent excessive data transfer

## Future Enhancements

Potential additions:
- Session-specific filtering on dashboard
- Customizable date range selection
- Export analytics data as CSV/JSON
- Real-time streaming updates via WebSocket
- Device type filtering in charts
- Signal strength range adjustments
- Comparison between date ranges
