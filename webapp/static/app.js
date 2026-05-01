document.addEventListener('DOMContentLoaded', async () => {
    console.log("[JS] Script loaded successfully.");

    const statusEl = document.getElementById('status');
    
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        statusEl.textContent = data.status === 'connected' 
            ? `Database connected. Tables: ${data.tables.join(', ')}` 
            : `DB Error: ${data.message}`;
    } catch (e) {
        statusEl.textContent = 'Failed to connect to database.';
        console.error(e);
    }

    document.getElementById('apply-filters').addEventListener('click', loadDevices);
    document.getElementById('reset-filters').addEventListener('click', () => {
        document.getElementById('vendor-filter').value = '';
        document.getElementById('signal-filter').value = '';
        document.getElementById('date-filter').value = '';
        loadDevices();
    });

    // Auto-load
    await loadDevices();
});

async function loadDevices() {
    console.log("[JS] loadDevices() called");

    const tbody = document.querySelector('#device-table tbody');
    const debugMsg = document.getElementById('debug-message');
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
        
        const text = await res.text();
        console.log("[JS] Raw Response:", text);

        if (!text.trim()) {
            throw new Error('Empty response');
        }

        const devices = JSON.parse(text);
        console.log("[JS] Parsed devices count:", devices.length);

        if (!Array.isArray(devices)) {
            tbody.innerHTML = `<tr><td colspan="5" style="color:red;">Invalid data format.</td></tr>`;
            return;
        }

        if (devices.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#888;">No devices found.</td></tr>';
            return;
        }

        tbody.innerHTML = devices.map(d => {
            const mac = d.address || d.addr || d.mac || 'N/A';
            const vendor = d.manufacturer || d.oui || 'Unknown';
            const rssi = d.max_rssi_dbm != null ? d.max_rssi_dbm + ' dBm' : 'N/A';
            const lastSeen = d.last_seen_utc || d.last_seen || 'N/A';
            const firstSeen = d.first_seen_utc || d.first_seen || 'N/A';

            return `
                <tr>
                    <td>${mac}</td>
                    <td>${vendor}</td>
                    <td>${rssi}</td>
                    <td>${lastSeen}</td>
                    <td>${firstSeen}</td>
                </tr>
            `;
        }).join('');

    } catch (e) {
        console.error("[JS] Error:", e);
        tbody.innerHTML = `<tr><td colspan="5" style="color:red;">Error: ${e.message}</td></tr>`;
    }
}
