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

    // Update filter availability when table changes
    document.getElementById('table-selector').addEventListener('change', updateFilterAvailability);

    // Auto-load initial data
    updateFilterAvailability();
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

function updateFilterAvailability() {
    const table = document.getElementById('table-selector').value;

    // Define which filters are available for each table
    const filterAvailability = {
        bt_devices: {
            vendor: true,   // has manufacturer column
            rssi: true,     // has max_rssi_dbm
            date: true
        },
        wifi_aps: {
            vendor: false,  // no manufacturer column
            rssi: true,     // has max_signal_dbm
            date: true
        },
        wifi_clients: {
            vendor: false,  // no manufacturer
            rssi: false,    // no signal column
            date: true
        },
        rf_devices: {
            vendor: false,  // no vendor
            rssi: false,    // no signal column
            date: true
        }
    };

    const availability = filterAvailability[table] || {};
    const vendorInput = document.getElementById('vendor-filter');
    const rssiInput = document.getElementById('signal-filter');

    // Update vendor filter
    vendorInput.disabled = !availability.vendor;
    vendorInput.placeholder = availability.vendor ? 'Vendor (e.g., Apple)' : 'Not available for this table';

    // Update RSSI filter
    rssiInput.disabled = !availability.rssi;
    rssiInput.placeholder = availability.rssi ? 'Max RSSI (e.g., -60)' : 'Not available for this table';

    // Clear disabled fields
    if (!availability.vendor) vendorInput.value = '';
    if (!availability.rssi) rssiInput.value = '';

    console.log(`[JS] Updated filters for ${table}: vendor=${availability.vendor}, rssi=${availability.rssi}`);
}
