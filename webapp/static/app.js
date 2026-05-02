document.addEventListener('DOMContentLoaded', async () => {
    console.log("[JS] Script loaded successfully.");

    const statusEl = document.getElementById('status');

    try {
        const res = await fetch('/api/status');
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();
        if (data.status === 'connected') {
            statusEl.textContent = `✓ Database connected (${data.tables.length} tables)`;
            statusEl.style.color = '#28a745';
        } else {
            statusEl.textContent = `✗ Database error: ${data.message}`;
            statusEl.style.color = '#dc3545';
        }
    } catch (e) {
        statusEl.textContent = `✗ Cannot reach API: ${e.message}`;
        statusEl.style.color = '#dc3545';
        console.error("[JS] Failed to fetch status:", e);
    }

    document.getElementById('apply-filters').addEventListener('click', loadDevices);
    document.getElementById('reset-filters').addEventListener('click', () => {
        document.getElementById('vendor-filter').value = '';
        document.getElementById('signal-filter').value = '';
        document.getElementById('date-filter').value = '';
        loadDevices();
    });

    // Auto-load initial data
    await loadDevices();
});

async function loadDevices() {
    console.log("[JS] loadDevices() called");

    const tbody = document.querySelector('#device-table tbody');
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:12px;">Loading...</td></tr>';

    const tableSelector = document.getElementById('table-selector').value;
    const vendor = document.getElementById('vendor-filter').value.trim();
    const rssi = document.getElementById('signal-filter').value.trim();
    const date = document.getElementById('date-filter').value;

    const params = new URLSearchParams();
    params.append('table', tableSelector);
    if (vendor) params.append('vendor', vendor);
    if (rssi) params.append('rssi', rssi);
    if (date) params.append('date', date);
    params.append('limit', 500);
    params.append('offset', 0);

    const fetchUrl = `/api/devices?${params.toString()}`;
    console.log("[JS] Fetching:", fetchUrl);

    try {
        const res = await fetch(fetchUrl);

        if (!res.ok) {
            throw new Error(`Server returned ${res.status}`);
        }

        const data = await res.json();
        console.log("[JS] Parsed response:", data);

        // Check for API error response
        if (data.error) {
            tbody.innerHTML = `<tr><td colspan="5" style="color:red;">API Error: ${escapeHtml(data.error)}</td></tr>`;
            return;
        }

        if (!Array.isArray(data)) {
            tbody.innerHTML = `<tr><td colspan="5" style="color:red;">Invalid data format (expected array).</td></tr>`;
            return;
        }

        if (data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#888;">No devices found.</td></tr>';
            return;
        }

        tbody.innerHTML = data.map(d => {
            const mac = d.address || d.addr || d.mac || 'N/A';
            const vendor = d.manufacturer || d.oui || 'Unknown';
            const rssi = d.max_rssi_dbm != null ? d.max_rssi_dbm + ' dBm' : 'N/A';
            const lastSeen = d.last_seen_utc || d.last_seen || 'N/A';
            const firstSeen = d.first_seen_utc || d.first_seen || 'N/A';

            return `
                <tr>
                    <td>${escapeHtml(mac)}</td>
                    <td>${escapeHtml(vendor)}</td>
                    <td>${escapeHtml(rssi)}</td>
                    <td>${escapeHtml(lastSeen)}</td>
                    <td>${escapeHtml(firstSeen)}</td>
                </tr>
            `;
        }).join('');

    } catch (e) {
        console.error("[JS] Error:", e);
        tbody.innerHTML = `<tr><td colspan="5" style="color:red;">Error: ${escapeHtml(e.message)}</td></tr>`;
    }
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
