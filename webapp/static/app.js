const TABLE_COLUMNS = {
    bt_devices: [
        { field: 'device_type', label: 'Type', render: renderDeviceBadge },
        { field: 'address', label: 'Address', render: d => `<span class="monospace">${escapeHtml(d)}</span>` },
        { field: 'name', label: 'Name', render: d => d ? escapeHtml(d) : '<em style="color: #6b7280;">—</em>' },
        { field: 'manufacturer', label: 'Manufacturer', render: d => escapeHtml(d || 'Unknown') },
        { field: 'apple_continuity_type', label: 'Apple Type', render: d => d ? `<span class="device-badge badge-apple">${escapeHtml(d)}</span>` : '—' },
        { field: 'service_names', label: 'Services', render: d => d ? truncate(escapeHtml(d), 40) : '—' },
        { field: 'max_rssi_dbm', label: 'RSSI', render: renderSignalBar },
        { field: 'wigle_sighting_count', label: 'Wigle', render: d => d > 0 ? `<span class="wigle-pill">${d} sightings</span>` : '—' },
        { field: 'last_seen_utc', label: 'Last Seen', render: d => `<span class="time-relative">${timeAgo(d)}</span>` },
    ],
    wifi_aps: [
        { field: 'device_type', label: 'Type', render: renderDeviceBadge },
        { field: 'bssid', label: 'BSSID', render: d => `<span class="monospace">${escapeHtml(d)}</span>` },
        { field: 'ssid', label: 'SSID', render: d => d ? truncate(escapeHtml(d), 30) : '<em style="color: #6b7280;">[Hidden]</em>' },
        { field: 'manufacturer', label: 'Manufacturer', render: d => escapeHtml(d || 'Unknown') },
        { field: 'channel', label: 'Channel', render: d => d ? `<span style="text-align: center;">${d}</span>` : '—' },
        { field: 'encryption', label: 'Encryption', render: d => d ? truncate(escapeHtml(d), 25) : '—' },
        { field: 'max_signal_dbm', label: 'Signal', render: renderSignalBar },
        { field: 'wigle_sighting_count', label: 'Wigle', render: d => d > 0 ? `<span class="wigle-pill">${d} sightings</span>` : '—' },
        { field: 'last_seen_utc', label: 'Last Seen', render: d => `<span class="time-relative">${timeAgo(d)}</span>` },
    ],
    wifi_clients: [
        { field: 'device_type', label: 'Type', render: renderDeviceBadge },
        { field: 'mac', label: 'MAC', render: d => `<span class="monospace">${escapeHtml(d)}</span>` },
        { field: 'manufacturer', label: 'Manufacturer', render: d => escapeHtml(d || 'Unknown') },
        { field: 'probe_ssid', label: 'Probe SSID', render: d => d ? truncate(escapeHtml(d), 30) : '—' },
        { field: 'last_seen_utc', label: 'Last Seen', render: d => `<span class="time-relative">${timeAgo(d)}</span>` },
    ],
    rf_devices: [
        { field: 'device_type', label: 'Type', render: renderDeviceBadge },
        { field: 'device_id', label: 'Device ID', render: d => `<span class="monospace">${escapeHtml(d)}</span>` },
        { field: 'model', label: 'Model', render: d => escapeHtml(d || 'Unknown') },
        { field: 'protocol', label: 'Protocol', render: d => d ? escapeHtml(d) : '—' },
        { field: 'frequency_mhz', label: 'Frequency', render: d => d ? `${d} MHz` : '—' },
        { field: 'max_rssi_dbm', label: 'RSSI', render: renderSignalBar },
        { field: 'last_seen_utc', label: 'Last Seen', render: d => `<span class="time-relative">${timeAgo(d)}</span>` },
    ]
};

let currentTable = 'bt_devices';
let allSessions = [];
let allDevices = [];
let filteredDevices = [];
let currentSort = { field: null, direction: 'asc' };
let currentSearchQuery = '';

document.addEventListener('DOMContentLoaded', async () => {
    console.log("[JS] warDrive Explorer initializing...");

    setupTableTabs();
    setupSessionPanel();
    setupDetailPanel();
    setupGlobalSearch();
    await fetchStatus();
    await fetchSessions();
    await loadDevices();

    document.getElementById('apply-filters').addEventListener('click', loadDevices);
    document.getElementById('reset-filters').addEventListener('click', resetFilters);
    document.getElementById('session-filter').addEventListener('change', loadDevices);
    document.getElementById('export-csv').addEventListener('click', () => exportData('csv'));
    document.getElementById('export-json').addEventListener('click', () => exportData('json'));
});

function setupTableTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentTable = btn.dataset.table;
            updateTableHead();
            loadDevices();
        });
    });
}

function updateTableHead() {
    const columns = TABLE_COLUMNS[currentTable] || [];
    const thead = document.getElementById('table-head');
    const headerRow = columns.map(col => {
        const sortClass = currentSort.field === col.field ? `sort-${currentSort.direction}` : '';
        return `<th class="sortable ${sortClass}" data-field="${col.field}">${col.label}</th>`;
    }).join('');
    thead.innerHTML = `<tr>${headerRow}</tr>`;

    // Add click listeners to headers
    thead.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => handleHeaderClick(th.dataset.field));
    });
}

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        const statusEl = document.getElementById('status');
        if (data.status === 'connected') {
            statusEl.textContent = '● LIVE';
            statusEl.classList.add('connected');

            if (data.counts) {
                updateStatsBar(data.counts);
            }
        } else {
            statusEl.textContent = '⚠ Database Error';
            statusEl.classList.add('error');
        }
    } catch (e) {
        document.getElementById('status').textContent = '⚠ Offline';
        document.getElementById('status').classList.add('error');
        console.error("[JS] Status fetch failed:", e);
    }
}

async function fetchSessions() {
    try {
        const res = await fetch('/api/sessions');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (!Array.isArray(data)) return;

        allSessions = data;
        const sel = document.getElementById('session-filter');

        data.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.session_id;
            const dateStr = s.started_at_utc ? new Date(s.started_at_utc).toLocaleDateString() : 'Unknown';
            opt.textContent = `${s.session_id} (${dateStr}) — WiFi:${s.ap_count} BLE:${s.bt_count} RF:${s.rf_count}`;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error("[JS] Session fetch failed:", e);
    }
}

function handleHeaderClick(field) {
    if (currentSort.field === field) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.field = field;
        currentSort.direction = 'asc';
    }
    renderTableWithSort();
}

function renderTableWithSort() {
    const tbody = document.getElementById('table-body');
    const columns = TABLE_COLUMNS[currentTable] || [];

    if (filteredDevices.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No devices found</td></tr>';
        return;
    }

    // Sort data
    let sorted = [...filteredDevices];
    if (currentSort.field) {
        sorted.sort((a, b) => {
            const aVal = a[currentSort.field];
            const bVal = b[currentSort.field];

            // Handle nulls/undefined
            if (aVal == null && bVal == null) return 0;
            if (aVal == null) return currentSort.direction === 'asc' ? 1 : -1;
            if (bVal == null) return currentSort.direction === 'asc' ? -1 : 1;

            // Numeric comparison
            if (typeof aVal === 'number' && typeof bVal === 'number') {
                return currentSort.direction === 'asc' ? aVal - bVal : bVal - aVal;
            }

            // String comparison
            const aStr = String(aVal).toLowerCase();
            const bStr = String(bVal).toLowerCase();
            const cmp = aStr.localeCompare(bStr);
            return currentSort.direction === 'asc' ? cmp : -cmp;
        });
    }

    updateTableHead();
    tbody.innerHTML = sorted.map(record => {
        const cells = columns.map(col => {
            const value = record[col.field];
            const rendered = col.render(value);
            return `<td>${rendered}</td>`;
        }).join('');
        return `<tr>${cells}</tr>`;
    }).join('');

    // Add click listeners to rows with data binding
    tbody.querySelectorAll('tr').forEach((row, index) => {
        row.addEventListener('click', () => handleRowClick(row, sorted[index]));
    });
}

function handleRowClick(rowElement, rowData) {
    // Remove active state from other rows
    document.querySelectorAll('tbody tr.active').forEach(tr => tr.classList.remove('active'));
    rowElement.classList.add('active');

    // Show detail panel
    openDetailPanel(rowData);
}

async function loadDevices() {
    console.log(`[JS] Loading ${currentTable}...`);

    const tbody = document.getElementById('table-body');
    tbody.innerHTML = '<tr><td colspan="10" class="loading">Loading...</td></tr>';

    const vendor = document.getElementById('vendor-filter').value.trim();
    const signal = document.getElementById('signal-filter').value.trim();
    const date = document.getElementById('date-filter').value;
    const session = document.getElementById('session-filter').value;

    const params = new URLSearchParams();
    params.append('table', currentTable);
    if (vendor) params.append('vendor', vendor);
    if (signal) params.append('rssi', signal);
    if (date) params.append('date', date);
    if (session) params.append('session', session);
    params.append('limit', 500);
    params.append('offset', 0);

    // Update stats bar based on selected session
    if (session) {
        const selectedSession = allSessions.find(s => s.session_id === session);
        if (selectedSession) {
            updateStatsBar({
                wifi_aps: selectedSession.ap_count,
                bt_devices: selectedSession.bt_count,
                rf_devices: selectedSession.rf_count
            });
        }
    } else {
        // Fetch global counts
        try {
            const statusRes = await fetch('/api/status');
            const statusData = await statusRes.json();
            if (statusData.counts) {
                updateStatsBar(statusData.counts);
            }
        } catch (e) {
            console.error("[JS] Failed to fetch global counts:", e);
        }
    }

    try {
        const res = await fetch(`/api/devices?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const data = await res.json();
        console.log(`[JS] Got ${Array.isArray(data) ? data.length : '?'} records`);

        if (!Array.isArray(data)) {
            tbody.innerHTML = '<tr><td colspan="10" class="error-message">Invalid response format</td></tr>';
            return;
        }

        if (data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No devices found</td></tr>';
            updateTableHead();
            return;
        }

        allDevices = data;
        currentSearchQuery = '';
        document.getElementById('global-search').value = '';
        applyFiltersAndSearch();

    } catch (e) {
        console.error("[JS] Error loading devices:", e);
        tbody.innerHTML = `<tr><td colspan="10" class="error-message">Error: ${escapeHtml(e.message)}</td></tr>`;
    }
}

function applyFiltersAndSearch() {
    const searchTerm = currentSearchQuery.toLowerCase();
    const columns = TABLE_COLUMNS[currentTable] || [];

    filteredDevices = allDevices.filter(device => {
        if (!searchTerm) return true;

        // Search across all visible columns
        return columns.some(col => {
            const value = device[col.field];
            if (value == null) return false;
            return String(value).toLowerCase().includes(searchTerm);
        });
    });

    // Reset sort to maintain consistency
    renderTableWithSort();
}

function setupGlobalSearch() {
    const searchInput = document.getElementById('global-search');
    if (!searchInput) return;

    searchInput.addEventListener('input', (e) => {
        currentSearchQuery = e.target.value;
        applyFiltersAndSearch();
    });
}

function resetFilters() {
    document.getElementById('vendor-filter').value = '';
    document.getElementById('signal-filter').value = '';
    document.getElementById('date-filter').value = '';
    document.getElementById('session-filter').value = '';
    document.getElementById('global-search').value = '';
    currentSearchQuery = '';
    currentSort = { field: null, direction: 'asc' };
    loadDevices();
}

function setupDetailPanel() {
    const closeBtn = document.getElementById('detail-close');
    const detailPanel = document.getElementById('detail-panel');

    if (!closeBtn || !detailPanel) return;

    closeBtn.addEventListener('click', closeDetailPanel);

    // Close panel when clicking outside (on the main content)
    document.addEventListener('click', (e) => {
        if (!detailPanel.contains(e.target) && !e.target.closest('tbody tr')) {
            closeDetailPanel();
        }
    });
}

function openDetailPanel(rowData) {
    const detailPanel = document.getElementById('detail-panel');
    const detailContent = document.getElementById('detail-content');
    const columns = TABLE_COLUMNS[currentTable] || [];

    if (!detailPanel || !detailContent) return;

    // Build HTML for all fields
    const fieldsHtml = columns.map(col => {
        const value = rowData[col.field];
        const displayValue = value != null ? String(value) : '—';
        const isEmpty = value == null;

        return `
            <div class="detail-field">
                <label class="detail-label">${col.label}</label>
                <div class="detail-value ${isEmpty ? 'empty' : ''}">${isEmpty ? 'N/A' : escapeHtml(displayValue)}</div>
            </div>
        `;
    }).join('');

    detailContent.innerHTML = fieldsHtml;
    detailPanel.classList.add('open');
}

function closeDetailPanel() {
    const detailPanel = document.getElementById('detail-panel');
    if (!detailPanel) return;

    detailPanel.classList.remove('open');
    document.querySelectorAll('tbody tr.active').forEach(tr => tr.classList.remove('active'));
}

function updateStatsBar(counts) {
    document.getElementById('stat-aps').textContent = counts.wifi_aps || '0';
    document.getElementById('stat-ble').textContent = counts.bt_devices || '0';
    document.getElementById('stat-rf').textContent = counts.rf_devices || '0';
}

async function setupSessionPanel() {
    const sessionToggle = document.getElementById('session-toggle');
    const sessionList = document.getElementById('session-list');

    if (!sessionToggle || !sessionList) return;

    sessionToggle.addEventListener('click', () => {
        sessionList.classList.toggle('active');
        sessionToggle.textContent = sessionList.classList.contains('active') ? '▲' : '▼';
    });

    try {
        const res = await fetch('/api/sessions');
        if (!res.ok) return;

        const sessions = await res.json();
        if (!Array.isArray(sessions) || sessions.length === 0) {
            sessionList.innerHTML = '<div style="color: #6b7280; text-align: center;">No sessions recorded</div>';
            return;
        }

        sessionList.innerHTML = sessions.map(s => `
            <div class="session-item">
                <div class="session-item-title">${escapeHtml(s.session_id)}</div>
                <div class="session-item-details">
                    ${s.started_at_utc ? timeAgo(s.started_at_utc) : 'Unknown'}
                    ${s.ended_at_utc ? `→ ${timeAgo(s.ended_at_utc)}` : ''}
                </div>
                <div class="session-counts">
                    <span>🔗 WiFi: ${s.ap_count || 0}</span>
                    <span>📱 BLE: ${s.bt_count || 0}</span>
                    <span>📡 RF: ${s.rf_count || 0}</span>
                </div>
            </div>
        `).join('');
    } catch (e) {
        console.error("[JS] Session fetch failed:", e);
    }
}

// Rendering helpers

function renderDeviceBadge(deviceType) {
    if (!deviceType) return '<span class="device-badge badge-unknown">Unknown</span>';

    const type = deviceType.toLowerCase();
    const badgeMap = {
        'camera': 'badge-camera',
        'phone': 'badge-phone',
        'router': 'badge-router',
        'ap': 'badge-router',
        'wearable': 'badge-wearable',
        'airpods': 'badge-airpods',
        'findmy': 'badge-findmy',
        'apple': 'badge-apple',
        'iot-sensor': 'badge-iot-sensor',
        'iot-device': 'badge-iot-device',
        'hotspot': 'badge-hotspot'
    };

    const badgeClass = badgeMap[type] || 'badge-unknown';
    return `<span class="device-badge ${badgeClass}">${escapeHtml(deviceType)}</span>`;
}

function renderSignalBar(dbm) {
    if (dbm == null) return '—';

    const strength = Math.max(0, Math.min(100, (dbm + 100) * 2));
    let color = 'signal-weak';
    if (dbm > -50) color = 'signal-strong';
    else if (dbm > -70) color = 'signal-medium';

    return `
        <div class="signal-bar-container">
            <div class="signal-bar">
                <div class="signal-bar-fill ${color}" style="width: ${strength}%"></div>
            </div>
            <span class="signal-value">${dbm} dBm</span>
        </div>
    `;
}

function timeAgo(isoString) {
    if (!isoString) return '—';

    try {
        const date = new Date(isoString);
        const now = new Date();
        const seconds = Math.floor((now - date) / 1000);

        if (seconds < 60) return `${seconds}s ago`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        if (days < 7) return `${days}d ago`;
        const weeks = Math.floor(days / 7);
        if (weeks < 4) return `${weeks}w ago`;
        const months = Math.floor(days / 30);
        if (months < 12) return `${months}mo ago`;
        return `${Math.floor(months / 12)}y ago`;
    } catch (e) {
        return '—';
    }
}

function truncate(text, length) {
    if (!text || text.length <= length) return text;
    return text.substring(0, length) + '…';
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}

// Export functionality
function exportData(format) {
    const tbody = document.getElementById('table-body');
    const rows = tbody.querySelectorAll('tr');

    if (rows.length === 0) {
        alert('No data to export');
        return;
    }

    const columns = TABLE_COLUMNS[currentTable] || [];
    const headers = columns.map(col => col.label);

    // Extract data from table rows
    const data = [];
    rows.forEach(row => {
        const cells = row.querySelectorAll('td');
        if (cells.length > 0) {
            const rowData = {};
            columns.forEach((col, idx) => {
                if (idx < cells.length) {
                    // Extract plain text from cell (ignore HTML)
                    rowData[col.label] = cells[idx].innerText.trim();
                }
            });
            data.push(rowData);
        }
    });

    if (format === 'csv') {
        exportCSV(data, headers);
    } else if (format === 'json') {
        exportJSON(data);
    }
}

function exportCSV(data, headers) {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
    const filename = `warDrive_${currentTable}_${timestamp}.csv`;

    // Create CSV content
    const csvContent = [
        headers.join(','),
        ...data.map(row => {
            return headers.map(header => {
                const value = row[header] || '';
                // Escape quotes and wrap in quotes if contains comma
                const escaped = String(value).replace(/"/g, '""');
                return escaped.includes(',') ? `"${escaped}"` : escaped;
            }).join(',');
        })
    ].join('\n');

    downloadFile(csvContent, filename, 'text/csv');
}

function exportJSON(data) {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
    const filename = `warDrive_${currentTable}_${timestamp}.json`;

    // Create JSON content
    const jsonContent = JSON.stringify({
        table: currentTable,
        exportedAt: new Date().toISOString(),
        rowCount: data.length,
        data: data
    }, null, 2);

    downloadFile(jsonContent, filename, 'application/json');
}

function downloadFile(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}
