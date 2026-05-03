let currentSession = null;
let lastFeedTimestamp = null;
let allObservations = [];
let counters = { bt: 0, wifi: 0, rf: 0 };
let pollInterval = 5000;
let isAutoRefreshing = true;
let pollTimeoutId = null;
let sessionStartTime = null;

document.addEventListener('DOMContentLoaded', async () => {
    console.log("[JS] warDrive Live View initializing...");

    setupControls();
    await initializeSession();
    await loadFeed();
    startPolling();
    startSessionClock();
});

async function initializeSession() {
    try {
        const res = await fetch('/api/live/session');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (data.active_session) {
            currentSession = data.active_session;
            sessionStartTime = new Date(currentSession.started_at_utc);

            document.getElementById('session-id').textContent = currentSession.session_id || '—';
            document.getElementById('session-bt').textContent = currentSession.bt_count || 0;
            document.getElementById('session-wifi').textContent = currentSession.wifi_count || 0;
            document.getElementById('session-rf').textContent = currentSession.rf_count || 0;

            counters.bt = currentSession.bt_count || 0;
            counters.wifi = currentSession.wifi_count || 0;
            counters.rf = currentSession.rf_count || 0;

            updateCounterDisplay();

            const statusEl = document.getElementById('status');
            const sourceEl = document.getElementById('data-source');
            if (data.status === 'raw_data_not_yet_enriched') {
                statusEl.textContent = '⏳ Data Capturing';
                statusEl.classList.add('warning');
                sourceEl.textContent = '(raw files • awaiting enrichment)';
                sourceEl.className = 'data-source-badge';
            } else if (data.status === 'enriched') {
                statusEl.textContent = '● ENRICHED';
                statusEl.classList.add('connected');
                sourceEl.textContent = '';
            } else {
                statusEl.textContent = '● LIVE';
                statusEl.classList.add('connected');
                sourceEl.textContent = '';
            }
        } else {
            document.getElementById('status').textContent = '⚠ No Active Session';
            document.getElementById('status').classList.add('error');
            document.getElementById('data-source').textContent = '';
        }
    } catch (e) {
        console.error("[JS] Failed to load session:", e);
        document.getElementById('status').textContent = '⚠ Offline';
        document.getElementById('status').classList.add('error');
    }
}

async function loadFeed() {
    try {
        const since = lastFeedTimestamp || new Date(Date.now() - 30000).toISOString();
        const res = await fetch(`/api/live/feed?since=${encodeURIComponent(since)}&limit=100`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const data = await res.json();
        if (Array.isArray(data)) {
            const newObservations = data.filter(obs => {
                const isDuplicate = allObservations.some(
                    existing => existing.timestamp_utc === obs.timestamp_utc && existing.address === obs.address
                );
                return !isDuplicate;
            });

            if (newObservations.length > 0) {
                newObservations.forEach(obs => {
                    counters[obs.type]++;
                    allObservations.unshift(obs);
                });

                updateFeedUI(newObservations);
                updateLeaderboard();
                updateCounterDisplay();
            }

            if (data.length > 0) {
                lastFeedTimestamp = data[0].timestamp_utc;
            }
        }
    } catch (e) {
        console.error("[JS] Error loading feed:", e);
    }
}

function updateFeedUI(newObservations) {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;

    // Insert newest rows at the top so the feed is newest-first
    newObservations.forEach(obs => {
        const row = renderFeedRow(obs);
        feed.insertAdjacentHTML('afterbegin', row);
    });

    // Flash new rows
    const feedRows = feed.querySelectorAll('.feed-row-new');
    feedRows.forEach(row => {
        setTimeout(() => row.classList.remove('feed-row-new'), 500);
    });

    // Trim oldest rows (at the bottom) to keep max 200
    const rows = feed.querySelectorAll('.feed-row');
    if (rows.length > 200) {
        for (let i = 200; i < rows.length; i++) {
            rows[i].remove();
        }
    }
}

function renderFeedRow(obs) {
    const typeBadge = renderTypeBadge(obs.type);
    const signalBar = obs.rssi_dbm != null ? renderSignalBar(obs.rssi_dbm) : '—';
    const name = obs.name ? escapeHtml(obs.name) : '<em style="color: #6b7280;">—</em>';
    const address = escapeHtml(obs.address);
    const timeStr = timeAgo(obs.timestamp_utc);

    return `
        <div class="feed-row feed-row-new">
            <div class="feed-type">${typeBadge}</div>
            <div class="feed-address monospace">${address}</div>
            <div class="feed-name">${name}</div>
            <div class="feed-signal">${signalBar}</div>
            <div class="feed-time time-relative">${timeStr}</div>
        </div>
    `;
}

function renderTypeBadge(type) {
    const badges = {
        ble: '<span class="device-badge badge-iot-device">BLE</span>',
        wifi: '<span class="device-badge badge-router">WiFi</span>',
        rf: '<span class="device-badge badge-iot-sensor">RF</span>'
    };
    return badges[type] || '<span class="device-badge badge-unknown">?</span>';
}

function updateLeaderboard() {
    const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();

    const recent = allObservations
        .filter(obs => obs.timestamp_utc >= fiveMinutesAgo && obs.rssi_dbm != null)
        .sort((a, b) => (b.rssi_dbm || -100) - (a.rssi_dbm || -100))
        .slice(0, 10);

    const leaderboard = document.getElementById('leaderboard');
    leaderboard.innerHTML = recent.map(obs => {
        const strength = Math.max(0, Math.min(100, (obs.rssi_dbm + 100) * 2));
        let color = 'signal-weak';
        if (obs.rssi_dbm > -50) color = 'signal-strong';
        else if (obs.rssi_dbm > -70) color = 'signal-medium';

        return `
            <div class="leaderboard-item">
                <div class="leader-type">${renderTypeBadge(obs.type)}</div>
                <div class="leader-address monospace">${escapeHtml(obs.address)}</div>
                <div class="leader-signal">
                    <div class="signal-bar" style="min-width: 80px;">
                        <div class="signal-bar-fill ${color}" style="width: ${strength}%"></div>
                    </div>
                </div>
                <div class="leader-dbm">${obs.rssi_dbm} dBm</div>
            </div>
        `;
    }).join('');

    if (recent.length === 0) {
        leaderboard.innerHTML = '<div style="color: #6b7280; text-align: center; padding: 1rem;">No signals yet</div>';
    }
}

function updateCounterDisplay() {
    document.getElementById('counter-bt').textContent = counters.bt;
    document.getElementById('counter-wifi').textContent = counters.wifi;
    document.getElementById('counter-rf').textContent = counters.rf;

    // Animate delta badges
    ['bt', 'wifi', 'rf'].forEach(type => {
        const delta = currentSession ? (counters[type] - (currentSession[`${type}_count`] || 0)) : 0;
        const deltaEl = document.getElementById(`delta-${type}`);
        deltaEl.textContent = `+${Math.max(0, delta)}`;
        if (delta > 0) {
            deltaEl.classList.add('show-delta');
            setTimeout(() => deltaEl.classList.remove('show-delta'), 2000);
        }
    });

    // Update rates (per minute)
    if (sessionStartTime) {
        const elapsedSeconds = (Date.now() - sessionStartTime.getTime()) / 1000;
        const elapsedMinutes = Math.max(1, elapsedSeconds / 60);
        const btRate = (counters.bt / elapsedMinutes).toFixed(1);
        const wifiRate = (counters.wifi / elapsedMinutes).toFixed(1);
        const rfRate = (counters.rf / elapsedMinutes).toFixed(1);

        document.getElementById('rate-bt').textContent = `${btRate} /min`;
        document.getElementById('rate-wifi').textContent = `${wifiRate} /min`;
        document.getElementById('rate-rf').textContent = `${rfRate} /min`;
    }
}

function startSessionClock() {
    setInterval(() => {
        if (sessionStartTime) {
            const now = new Date();
            const elapsed = now - sessionStartTime;
            const hours = Math.floor(elapsed / 3600000);
            const minutes = Math.floor((elapsed % 3600000) / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);

            const timeStr = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
            document.getElementById('session-time').textContent = timeStr;
        }
    }, 1000);
}

function startPolling() {
    const poll = async () => {
        if (isAutoRefreshing) {
            await loadFeed();
        }
        pollTimeoutId = setTimeout(poll, pollInterval);
    };
    pollTimeoutId = setTimeout(poll, pollInterval);
}

function stopPolling() {
    if (pollTimeoutId) {
        clearTimeout(pollTimeoutId);
        pollTimeoutId = null;
    }
}

function setupControls() {
    const autoRefreshCheckbox = document.getElementById('auto-refresh');
    if (autoRefreshCheckbox) {
        autoRefreshCheckbox.addEventListener('change', (e) => {
            isAutoRefreshing = e.target.checked;
            const indicator = document.getElementById('status-indicator');
            if (indicator) {
                if (isAutoRefreshing) {
                    indicator.classList.remove('paused');
                    indicator.classList.add('pulsing');
                    indicator.textContent = '● LIVE';
                } else {
                    indicator.classList.remove('pulsing');
                    indicator.classList.add('paused');
                    indicator.textContent = '⏸ PAUSED';
                }
            }
        });
    }

    const pollIntervalSelect = document.getElementById('poll-interval');
    if (pollIntervalSelect) {
        pollIntervalSelect.addEventListener('change', (e) => {
            pollInterval = parseInt(e.target.value);
            stopPolling();
            startPolling();
        });
    }

    const clearFeedBtn = document.getElementById('clear-feed');
    if (clearFeedBtn) {
        clearFeedBtn.addEventListener('click', () => {
            allObservations = [];
            const feedEl = document.getElementById('activity-feed');
            const leaderEl = document.getElementById('leaderboard');
            if (feedEl) feedEl.innerHTML = '';
            if (leaderEl) leaderEl.innerHTML = '<div style="color: #6b7280; text-align: center; padding: 1rem;">Feed cleared</div>';
        });
    }
}

// Helpers
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

        if (seconds < 60) return `${seconds}s`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h`;
        const days = Math.floor(hours / 24);
        return `${days}d`;
    } catch (e) {
        return '—';
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
