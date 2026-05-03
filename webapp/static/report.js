let currentSession = null;
let allSessions = [];

document.addEventListener('DOMContentLoaded', async () => {
    console.log("[JS Report] Initializing report page...");

    await fetchStatus();
    await fetchSessions();
    await loadReport();

    document.getElementById('report-session-filter').addEventListener('change', loadReport);
    document.getElementById('export-pdf-btn').addEventListener('click', exportReportPDF);
});

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        const statusEl = document.getElementById('status');
        if (data.status === 'connected') {
            statusEl.textContent = '● LIVE';
            statusEl.classList.add('connected');
        } else {
            statusEl.textContent = '⚠ Database Error';
            statusEl.classList.add('error');
        }
    } catch (e) {
        document.getElementById('status').textContent = '⚠ Offline';
        document.getElementById('status').classList.add('error');
        console.error("[JS Report] Status fetch failed:", e);
    }
}

async function fetchSessions() {
    try {
        const res = await fetch('/api/sessions');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (!Array.isArray(data)) return;

        allSessions = data;
        const sel = document.getElementById('report-session-filter');

        data.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.session_id;
            const dateStr = s.started_at_utc ? new Date(s.started_at_utc).toLocaleDateString() : 'Unknown';
            opt.textContent = `${s.session_id} (${dateStr})`;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error("[JS Report] Session fetch failed:", e);
    }
}

async function loadReport() {
    const session = document.getElementById('report-session-filter').value || null;
    currentSession = session;

    const contentEl = document.getElementById('report-content');
    const loadingEl = document.getElementById('report-loading');
    const errorEl = document.getElementById('report-error');

    contentEl.style.display = 'none';
    loadingEl.style.display = 'block';
    errorEl.style.display = 'none';

    try {
        const url = `/api/report/summary${session ? `?session=${encodeURIComponent(session)}` : ''}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (data.error) {
            // Show a styled info notice instead of a red error for expected "disabled" state
            loadingEl.style.display = 'none';
            errorEl.style.background = 'rgba(245, 158, 11, 0.1)';
            errorEl.style.borderColor = 'var(--color-amber, #f59e0b)';
            errorEl.style.color = 'var(--color-amber, #f59e0b)';
            errorEl.innerHTML = `<strong>Report Unavailable:</strong> ${escapeHtml(data.error)}<br><br>Use the <a href="/" style="color: inherit;">Explorer</a> tab with session filters to analyze your data.`;
            errorEl.style.display = 'block';
            return;
        }

        renderReport(data);
        loadingEl.style.display = 'none';
        contentEl.style.display = 'block';
    } catch (e) {
        console.error("[JS Report] Report load failed:", e);
        errorEl.style.background = '';
        errorEl.style.borderColor = '';
        errorEl.style.color = '';
        errorEl.textContent = `Error loading report: ${escapeHtml(e.message)}`;
        errorEl.style.display = 'block';
        loadingEl.style.display = 'none';
    }
}

function renderReport(data) {
    // Update timestamp
    const now = new Date();
    document.getElementById('report-timestamp').textContent =
        `Generated: ${now.toLocaleString()} | Session: ${data.session || 'All Sessions'}`;

    // Device counts
    document.getElementById('count-wifi-ap').textContent = data.devices.wifi_ap || 0;
    document.getElementById('count-ble').textContent = data.devices.ble || 0;
    document.getElementById('count-wifi-client').textContent = data.devices.wifi_client || 0;
    document.getElementById('count-rf').textContent = data.devices.rf || 0;
    document.getElementById('count-total').textContent = data.devices.total || 0;

    // Time range
    if (data.time_range.start) {
        document.getElementById('time-start').textContent = formatDateTime(data.time_range.start);
    }
    if (data.time_range.end) {
        document.getElementById('time-end').textContent = formatDateTime(data.time_range.end);
    }

    // Signal strength stats
    renderSignalStats(data.signal_strength);

    // Manufacturers chart
    renderManufacturersChart(data.manufacturers);

    // Encryption distribution
    renderEncryptionChart(data.wifi_encryption);

    // Protocols/Device types
    if (data.wifi_protocols && data.wifi_protocols.length > 0) {
        document.getElementById('protocols-section').style.display = 'block';
        renderProtocolsChart(data.wifi_protocols);
    } else {
        document.getElementById('protocols-section').style.display = 'none';
    }
}

function renderSignalStats(signalStats) {
    const container = document.getElementById('signal-strength-stats');
    container.innerHTML = '';

    const types = [
        { key: 'ble', label: 'BLE Devices' },
        { key: 'wifi_ap', label: 'WiFi Access Points' },
        { key: 'rf', label: 'RF Devices' }
    ];

    types.forEach(type => {
        const stats = signalStats[type.key];
        if (stats) {
            const card = document.createElement('div');
            card.className = 'signal-card';
            card.innerHTML = `
                <div class="signal-type">${type.label}</div>
                <div class="signal-metric">
                    <span class="metric-label">Strongest:</span>
                    <span class="metric-value">${stats.max_dbm} dBm</span>
                </div>
                <div class="signal-metric">
                    <span class="metric-label">Weakest:</span>
                    <span class="metric-value">${stats.min_dbm} dBm</span>
                </div>
                <div class="signal-metric">
                    <span class="metric-label">Average:</span>
                    <span class="metric-value">${stats.avg_dbm} dBm</span>
                </div>
            `;
            container.appendChild(card);
        }
    });
}

function renderManufacturersChart(manufacturers) {
    const container = document.getElementById('manufacturers-chart');
    container.innerHTML = '';

    if (!manufacturers || manufacturers.length === 0) {
        container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 1rem;">No manufacturer data available</div>';
        return;
    }

    const maxCount = Math.max(...manufacturers.map(m => m.count));

    manufacturers.slice(0, 10).forEach(mfg => {
        const percentage = (mfg.count / maxCount) * 100;
        const item = document.createElement('div');
        item.className = 'bar-item';
        const mfgName = mfg.manufacturer || mfg.name || 'Unknown';
        item.innerHTML = `
            <div class="bar-name" title="${escapeHtml(mfgName)}">${escapeHtml(mfgName)}</div>
            <div class="bar-container">
                <div class="bar-fill" style="width: ${percentage}%">
                    <span class="bar-count">${mfg.count}</span>
                </div>
            </div>
        `;
        container.appendChild(item);
    });
}

function renderEncryptionChart(encryption) {
    const container = document.getElementById('encryption-chart');
    container.innerHTML = '';

    if (!encryption || encryption.length === 0) {
        container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 1rem;">No encryption data available</div>';
        return;
    }

    const maxCount = Math.max(...encryption.map(e => e.count));

    encryption.forEach(enc => {
        const percentage = (enc.count / maxCount) * 100;
        const item = document.createElement('div');
        item.className = 'bar-item';
        item.innerHTML = `
            <div class="bar-name" title="${escapeHtml(enc.encryption)}">${escapeHtml(enc.encryption)}</div>
            <div class="bar-container">
                <div class="bar-fill" style="width: ${percentage}%">
                    <span class="bar-count">${enc.count}</span>
                </div>
            </div>
        `;
        container.appendChild(item);
    });
}

function renderProtocolsChart(protocols) {
    const container = document.getElementById('protocols-chart');
    container.innerHTML = '';

    if (!protocols || protocols.length === 0) {
        container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 1rem;">No protocol data available</div>';
        return;
    }

    const maxCount = Math.max(...protocols.map(p => p.count));

    protocols.forEach(proto => {
        const percentage = (proto.count / maxCount) * 100;
        const item = document.createElement('div');
        item.className = 'bar-item';
        item.innerHTML = `
            <div class="bar-name" title="${escapeHtml(proto.protocol)}">${escapeHtml(proto.protocol)}</div>
            <div class="bar-container">
                <div class="bar-fill" style="width: ${percentage}%">
                    <span class="bar-count">${proto.count}</span>
                </div>
            </div>
        `;
        container.appendChild(item);
    });
}

function exportReportPDF() {
    const element = document.getElementById('report-content');
    if (!element) return;

    // Use DOM APIs to build the print window instead of document.write() with
    // raw innerHTML, which would be an XSS sink if any report field contained
    // unescaped content.
    const printWindow = window.open('', '', 'height=800,width=1000');
    if (!printWindow) return;

    const doc = printWindow.document;

    // Bootstrap a valid document structure
    doc.open();
    doc.write('<!DOCTYPE html><html><head></head><body></body></html>');
    doc.close();

    // <meta charset>
    const meta = doc.createElement('meta');
    meta.setAttribute('charset', 'UTF-8');
    doc.head.appendChild(meta);

    // <title> — textContent does NOT interpret HTML, so no injection possible
    const titleEl = doc.createElement('title');
    titleEl.textContent = 'warDrive Report';
    doc.head.appendChild(titleEl);

    // Google Fonts preconnect + stylesheet (href is a static literal)
    ['https://fonts.googleapis.com', 'https://fonts.gstatic.com'].forEach(href => {
        const link = doc.createElement('link');
        link.rel = 'preconnect';
        link.href = href;
        doc.head.appendChild(link);
    });
    const fontLink = doc.createElement('link');
    fontLink.rel = 'stylesheet';
    fontLink.href = 'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap';
    doc.head.appendChild(fontLink);

    // Inline print styles — assigned via textContent (safe)
    const styleEl = doc.createElement('style');
    styleEl.textContent = `
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'JetBrains Mono', monospace; background: white; color: #000; padding: 2rem; line-height: 1.6; }
        .report-page { max-width: 1200px; margin: 0 auto; }
        .report-header { margin-bottom: 2rem; text-align: center; border-bottom: 2px solid #000; padding-bottom: 1.5rem; }
        .report-title { font-size: 2rem; margin: 0 0 0.5rem 0; }
        .report-timestamp { color: #666; font-size: 0.9rem; }
        .report-section { background: white; border: 1px solid #ccc; border-radius: 4px; padding: 1.5rem; margin-bottom: 1.5rem; page-break-inside: avoid; }
        .section-title { color: #333; font-size: 1.2rem; margin: 0 0 1.5rem 0; padding-bottom: 0.75rem; border-bottom: 1px solid #ccc; text-transform: uppercase; letter-spacing: 0.5px; }
        .device-count-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
        .count-card { background: #f5f5f5; border: 1px solid #ccc; border-radius: 4px; padding: 1rem; text-align: center; }
        .count-label { color: #666; font-size: 0.85rem; margin-bottom: 0.5rem; }
        .count-value { color: #000; font-size: 2rem; font-weight: 600; }
        .bar-item { display: flex; justify-content: space-between; align-items: center; padding: 0.75rem; margin-bottom: 0.5rem; border: 1px solid #ddd; border-radius: 3px; background: #f9f9f9; }
        .bar-name { min-width: 150px; }
        .bar-container { flex: 1; background: #f0f0f0; border: 1px solid #ccc; border-radius: 3px; overflow: hidden; min-height: 24px; display: flex; align-items: center; margin: 0 1rem; }
        .bar-fill { background: #333; height: 100%; display: flex; align-items: center; justify-content: flex-end; padding-right: 0.5rem; }
        .bar-count { color: white; font-weight: 600; font-size: 0.85rem; }
        .signal-stat { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; }
        .signal-card { background: #f5f5f5; border: 1px solid #ccc; border-radius: 4px; padding: 1rem; }
        .signal-type { color: #333; font-weight: 600; margin-bottom: 0.75rem; }
        .signal-metric { display: flex; justify-content: space-between; margin-bottom: 0.5rem; }
        .metric-label { color: #666; }
        .metric-value { color: #000; font-weight: 600; }
        @media print { body { padding: 1rem; } .report-section { page-break-inside: avoid; } }
    `;
    doc.head.appendChild(styleEl);

    // Clone the already-rendered (and already HTML-escaped) report DOM and
    // append it via DOM adoption — this avoids any innerHTML/document.write
    // injection path entirely.
    const reportClone = element.cloneNode(true);
    doc.body.appendChild(reportClone);

    printWindow.print();
}

function formatDateTime(isoString) {
    if (!isoString) return '—';
    try {
        const date = new Date(isoString);
        return date.toLocaleString();
    } catch (e) {
        return isoString;
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
