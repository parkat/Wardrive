// collectors.js — collector status panel for warDrive pages
// Polls collector status every 5 seconds, renders badges, handles start/stop controls

const COLLECTORS_POLL_MS = 5000;
let collectorsPoller = null;

// Initialize the collectors panel on page load
document.addEventListener('DOMContentLoaded', initCollectorsPanel);

function initCollectorsPanel() {
    pollCollectorsStatus();
    collectorsPoller = setInterval(pollCollectorsStatus, COLLECTORS_POLL_MS);

    document.getElementById('btn-start-collectors')
        ?.addEventListener('click', handleStartCollectors);
    document.getElementById('btn-stop-collectors')
        ?.addEventListener('click', handleStopCollectors);
}

async function pollCollectorsStatus() {
    try {
        const res = await fetch('/api/collectors/status');
        if (!res.ok) return;
        const data = await res.json();
        renderCollectorsPanel(data);
    } catch (e) {
        console.debug('[collectors] status poll failed:', e);
    }
}

function renderCollectorsPanel(data) {
    const panel = document.getElementById('collector-panel');
    if (!panel) return;

    const collectors = data.collectors || {};
    const wardrive = data.wardrive_running;

    // Build collector badges
    const badges = ['wifi', 'sdr', 'esp32', 'gps'].map(name => {
        const info = collectors[name] || {};
        const running = info.running;
        const enabled = info.enabled;

        let cls = 'collector-badge';
        let label = name.toUpperCase();

        if (!enabled) {
            cls += ' badge-disabled';
        } else if (running) {
            cls += ' badge-running';
        } else {
            cls += ' badge-stopped';
        }

        const title = `${name}: ${running ? 'running' : enabled ? 'stopped' : 'disabled'}`;
        return `<span class="${cls}" title="${title}">${label}</span>`;
    }).join('');

    // Build session label if active
    const sessionLabel = data.session_id
        ? `<span class="collector-session">${data.session_id.slice(0, 15)}…</span>`
        : '';

    // Build buttons
    const startBtn = `<button id="btn-start-collectors" class="collector-btn btn-start" ${wardrive ? 'disabled' : ''}>Start</button>`;
    const stopBtn = `<button id="btn-stop-collectors" class="collector-btn btn-stop" ${!wardrive ? 'disabled' : ''}>Stop</button>`;

    // Render panel
    panel.innerHTML = `
        <div class="collector-badges">${badges}</div>
        ${sessionLabel}
        <div class="collector-controls">${startBtn}${stopBtn}</div>
    `;

    // Re-attach event listeners after innerHTML replacement
    document.getElementById('btn-start-collectors')
        ?.addEventListener('click', handleStartCollectors);
    document.getElementById('btn-stop-collectors')
        ?.addEventListener('click', handleStopCollectors);
}

async function handleStartCollectors() {
    const btn = document.getElementById('btn-start-collectors');
    const originalText = btn.textContent;
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Starting…';
    }

    try {
        const res = await fetch('/api/collectors/start', { method: 'POST' });
        const data = await res.json();

        if (data.status === 'sudo_not_configured') {
            showSudoersModal(data.sudoers_snippet, data.message);
        } else if (data.status === 'started' || data.status === 'already_running') {
            // Poll immediately to refresh badges
            await pollCollectorsStatus();
        } else if (data.status === 'error') {
            alert(`Error starting collectors: ${data.message}`);
        }
    } catch (e) {
        console.error('[collectors] start failed:', e);
        alert('Failed to start collectors');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }
}

async function handleStopCollectors() {
    const btn = document.getElementById('btn-stop-collectors');
    const originalText = btn.textContent;
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Stopping…';
    }

    try {
        const res = await fetch('/api/collectors/stop', { method: 'POST' });
        const data = await res.json();

        if (data.status === 'sudo_required_for_kill') {
            showSudoersModal(data.sudoers_snippet, data.message);
        } else if (data.status === 'stopped' || data.status === 'not_running') {
            // Poll immediately to refresh badges
            await pollCollectorsStatus();
        } else if (data.status === 'error') {
            alert(`Error stopping collectors: ${data.message}`);
        }
    } catch (e) {
        console.error('[collectors] stop failed:', e);
        alert('Failed to stop collectors');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }
}

function showSudoersModal(snippet, message) {
    // Create or reuse modal
    let modal = document.getElementById('sudoers-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'sudoers-modal';
        modal.className = 'sudoers-modal';
        document.body.appendChild(modal);
    }

    modal.innerHTML = `
        <div class="sudoers-modal-box">
            <h3>Sudo Not Configured</h3>
            <p>${escapeHtmlCollectors(message)}</p>
            <p>Run this command to configure sudoers:</p>
            <div style="background: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 3px; padding: 0.5rem; margin: 0.5rem 0; cursor: pointer;" onclick="navigator.clipboard.writeText('sudo visudo -f /etc/sudoers.d/wardrive'); this.style.opacity='0.7'; setTimeout(() => this.style.opacity='1', 200);" title="Click to copy">
                <code style="color: var(--accent-secondary);">sudo visudo -f /etc/sudoers.d/wardrive</code>
            </div>
            <p style="color: var(--text-muted); font-size: 0.8rem; margin-top: 0.5rem;">Then add these lines:</p>
            <pre class="sudoers-snippet">${escapeHtmlCollectors(snippet)}</pre>
            <p style="color: var(--text-muted); font-size: 0.8rem; margin-top: 1rem;">This allows the webapp to start/stop wardrive.sh without password prompts.</p>
            <button onclick="document.getElementById('sudoers-modal').remove()">Close</button>
        </div>
    `;
}

// Minimal escapeHtml for this module (independent of app.js)
function escapeHtmlCollectors(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}
