let map = null;
let layers = {
    ble: L.layerGroup(),
    wifi: L.layerGroup(),
    rf: L.layerGroup(),
    track: L.layerGroup(),
    rssiCircles: L.layerGroup(),  // RSSI radius circles kept separate from cluster groups
    heatmap: null,
};
let clusterGroups = {
    ble: L.markerClusterGroup({
        maxClusterRadius: 50,
        iconCreateFunction: createClusterIcon.bind(null, 'ble'),
    }),
    wifi: L.markerClusterGroup({
        maxClusterRadius: 50,
        iconCreateFunction: createClusterIcon.bind(null, 'wifi'),
    }),
    rf: L.markerClusterGroup({
        maxClusterRadius: 50,
        iconCreateFunction: createClusterIcon.bind(null, 'rf'),
    }),
};
let allDevices = [];
let allPoints = [];
let stats = { ble: 0, wifi: 0, rf: 0 };
let drawLayer = null;           // holds the drawn shape
let activeShape = null;         // current L.Rectangle or L.Polygon, null if no filter

document.addEventListener('DOMContentLoaded', async () => {
    console.log("[map.js] Initializing map...");
    initMap();
    await loadSessions();
    await refreshMapData();
    setupControls();
});

function initMap() {
    map = L.map('map', {
        attributionControl: true,
        zoom: 13,
        center: [40, -74],
    });

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '© OpenStreetMap, © CartoDB',
        maxZoom: 19,
        subdomains: 'abcd',
    }).addTo(map);

    // Add all layer groups to map
    layers.rssiCircles.addTo(map);
    clusterGroups.ble.addTo(map);
    clusterGroups.wifi.addTo(map);
    clusterGroups.rf.addTo(map);
    layers.track.addTo(map);

    // Initialize draw layer
    drawLayer = L.featureGroup().addTo(map);

    // Listen for draw completion
    map.on(L.Draw.Event.CREATED, onAreaDrawn);
}

async function loadSessions() {
    try {
        const res = await fetch('/api/sessions');
        if (!res.ok) return;
        const sessions = await res.json();
        const select = document.getElementById('session-select');
        sessions.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.session_id || '';
            opt.textContent = `${s.session_id?.slice(0, 12)} • ${new Date(s.started_at_utc).toLocaleString()}`;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error("[map.js] Failed to load sessions:", e);
    }
}

async function refreshMapData() {
    // Clear any active area filter state (without re-rendering markers)
    drawLayer.clearLayers();
    activeShape = null;
    document.getElementById('filter-status').style.display = 'none';
    document.getElementById('filter-device-list').innerHTML = '';

    const sessionId = document.getElementById('session-select').value || null;
    const url = sessionId ? `/api/map/devices?session=${encodeURIComponent(sessionId)}` : `/api/map/devices`;

    try {
        const res = await fetch(url);
        if (!res.ok) {
            console.error(`[map.js] GET ${url} returned ${res.status}`);
            return;
        }
        allDevices = await res.json();
        buildMarkers(allDevices);
    } catch (e) {
        console.error("[map.js] Failed to load devices:", e);
    }

    // Load track
    const trackUrl = sessionId ? `/api/map/track?session=${encodeURIComponent(sessionId)}` : `/api/map/track`;
    try {
        const res = await fetch(trackUrl);
        if (!res.ok) return;
        allPoints = await res.json();
        loadTrack(allPoints);
    } catch (e) {
        console.error("[map.js] Failed to load track:", e);
    }
}

function buildMarkers(devices) {
    // Clear existing markers and RSSI circles
    clusterGroups.ble.clearLayers();
    clusterGroups.wifi.clearLayers();
    clusterGroups.rf.clearLayers();
    layers.rssiCircles.clearLayers();

    stats = { ble: 0, wifi: 0, rf: 0 };

    devices.forEach(d => {
        if (!d.lat || !d.lon) return;

        const color = d.type === 'ble' ? '#22d3ee' : d.type === 'wifi' ? '#4ade80' : '#fb923c';
        const cluster = clusterGroups[d.type];

        // Main marker
        const marker = L.circleMarker([d.lat, d.lon], {
            radius: 6,
            fillColor: color,
            color: color,
            weight: 2,
            opacity: 1,
            fillOpacity: 0.8,
        });

        // RSSI circle
        const radius = rssiToRadius(d.rssi_dbm);
        const circle = L.circle([d.lat, d.lon], {
            radius: radius,
            color: color,
            fillColor: color,
            weight: 1,
            opacity: 0.15,
            fillOpacity: 0.1,
            interactive: false,
        });

        // Popup
        marker.bindPopup(buildPopup(d));

        // Add marker to cluster group, circles to separate layer (not clustered)
        cluster.addLayer(marker);
        layers.rssiCircles.addLayer(circle);

        stats[d.type]++;
    });

    // Update stats
    const total = stats.ble + stats.wifi + stats.rf;
    document.getElementById('pin-count').textContent = total;
    document.getElementById('stat-ble').textContent = stats.ble;
    document.getElementById('stat-wifi').textContent = stats.wifi;
    document.getElementById('stat-rf').textContent = stats.rf;

    // Build heatmap points
    const heatPoints = devices
        .filter(d => d.lat && d.lon && d.rssi_dbm)
        .map(d => [d.lat, d.lon, Math.max(0, Math.min(1, (d.rssi_dbm + 100) / 80))]);

    if (layers.heatmap) {
        map.removeLayer(layers.heatmap);
    }
    if (heatPoints.length > 0) {
        layers.heatmap = L.heatLayer(heatPoints, {
            radius: 25,
            blur: 15,
            maxZoom: 17,
            minOpacity: 0.2,
        });
    }
}

function buildPopup(d) {
    const typeBadge = d.type === 'ble' ? '🔵 BLE' : d.type === 'wifi' ? '📡 WiFi' : '📶 RF';
    const rssiBar = d.rssi_dbm ? `<div style="margin-top:0.5rem; background:#1a1f2e; padding:0.25rem; border-radius:2px;"><div style="background:${d.type === 'ble' ? '#22d3ee' : d.type === 'wifi' ? '#4ade80' : '#fb923c'}; width:${Math.max(0, Math.min(100, (d.rssi_dbm + 100) * 2))}%; height:4px;"></div></div>` : '';

    return `
        <div class="popup-header">${typeBadge}</div>
        <div class="popup-row">
            <span class="popup-label">Address</span>
            <span class="popup-value">${escapeHtml(d.address)}</span>
        </div>
        ${d.name ? `<div class="popup-row"><span class="popup-label">Name</span><span class="popup-value">${escapeHtml(d.name)}</span></div>` : ''}
        ${d.manufacturer ? `<div class="popup-row"><span class="popup-label">Vendor</span><span class="popup-value">${escapeHtml(d.manufacturer.slice(0, 20))}</span></div>` : ''}
        ${d.device_type ? `<div class="popup-row"><span class="popup-label">Type</span><span class="popup-value">${escapeHtml(d.device_type)}</span></div>` : ''}
        ${d.rssi_dbm ? `<div class="popup-row"><span class="popup-label">Signal</span><span class="popup-value">${d.rssi_dbm} dBm</span></div>${rssiBar}` : ''}
    `;
}

function loadTrack(points) {
    layers.track.clearLayers();
    if (points.length === 0) return;

    const coords = points.map(p => [p.lat, p.lon]);
    const polyline = L.polyline(coords, {
        color: '#94a3b8',
        weight: 2,
        opacity: 0.7,
        dashArray: '5, 5',
    });
    layers.track.addLayer(polyline);

    // Start marker (green)
    const start = L.circleMarker([points[0].lat, points[0].lon], {
        radius: 6,
        fillColor: '#4ade80',
        color: '#4ade80',
        weight: 2,
        opacity: 1,
        fillOpacity: 0.8,
    }).bindPopup('Start');
    layers.track.addLayer(start);

    // End marker (red)
    const end = L.circleMarker([points[points.length - 1].lat, points[points.length - 1].lon], {
        radius: 6,
        fillColor: '#ef4444',
        color: '#ef4444',
        weight: 2,
        opacity: 1,
        fillOpacity: 0.8,
    }).bindPopup('End');
    layers.track.addLayer(end);
}

function rssiToRadius(rssi) {
    if (!rssi) return 50;
    // d = 10^((-59 - rssi) / 20), clamped to 5-200m
    const dist = Math.pow(10, (-59 - rssi) / 20);
    return Math.max(5, Math.min(200, dist));
}

function createClusterIcon(type, cluster) {
    const count = cluster.getChildCount();
    const color = type === 'ble' ? '#22d3ee' : type === 'wifi' ? '#4ade80' : '#fb923c';

    return L.divIcon({
        html: `<div style="background:${color}; border:2px solid #0f1117; border-radius:50%; width:40px; height:40px; display:flex; align-items:center; justify-content:center; color:#0f1117; font-weight:700; font-size:12px;">${count}</div>`,
        iconSize: [40, 40],
        className: 'custom-cluster-icon',
    });
}

function startDrawing(type) {
    // Cancel any existing in-progress draw
    if (window._activeDrawHandler) {
        window._activeDrawHandler.disable();
    }
    const handler = type === 'rectangle'
        ? new L.Draw.Rectangle(map, {
            shapeOptions: { color: '#4ade80', weight: 2, fillOpacity: 0.1, dashArray: '5,5' }
          })
        : new L.Draw.Polygon(map, {
            shapeOptions: { color: '#4ade80', weight: 2, fillOpacity: 0.1, dashArray: '5,5' },
            allowIntersection: false,
          });
    handler.enable();
    window._activeDrawHandler = handler;
}

function onAreaDrawn(e) {
    // Remove any previous drawn shape
    drawLayer.clearLayers();
    activeShape = e.layer;
    drawLayer.addLayer(activeShape);
    window._activeDrawHandler = null;

    // Filter devices
    const filtered = allDevices.filter(d => d.lat && d.lon && pointInShape(d, activeShape));
    buildMarkers(filtered);

    // Show filter status
    const filterStatus = document.getElementById('filter-status');
    filterStatus.style.display = 'flex';
    filterStatus.style.flexDirection = 'column';
    document.getElementById('filter-ble').textContent = filtered.filter(d => d.type === 'ble').length;
    document.getElementById('filter-wifi').textContent = filtered.filter(d => d.type === 'wifi').length;
    document.getElementById('filter-rf').textContent = filtered.filter(d => d.type === 'rf').length;

    // Populate device list
    populateFilteredDeviceList(filtered);
}

function clearAreaFilter() {
    drawLayer.clearLayers();
    activeShape = null;
    buildMarkers(allDevices);
    document.getElementById('filter-status').style.display = 'none';
    document.getElementById('filter-device-list').innerHTML = '';
}

function populateFilteredDeviceList(devices) {
    const listContainer = document.getElementById('filter-device-list');
    listContainer.innerHTML = '';

    if (devices.length === 0) {
        listContainer.innerHTML = '<div style="color:#a0aec0; text-align:center; padding:1rem;">No devices in area</div>';
        return;
    }

    devices.forEach(d => {
        const item = document.createElement('div');
        item.className = `filter-device-item ${d.type}`;

        const addr = document.createElement('span');
        addr.className = 'device-item-addr';
        addr.textContent = escapeHtml(d.address);

        const meta = document.createElement('div');
        meta.className = 'device-item-meta';

        const name = d.name ? `${escapeHtml(d.name.slice(0, 15))}` : '';
        const rssi = d.rssi_dbm ? `${d.rssi_dbm}dBm` : '';

        if (name) {
            const nameEl = document.createElement('span');
            nameEl.textContent = name;
            meta.appendChild(nameEl);
        }
        if (rssi) {
            const rssiEl = document.createElement('span');
            rssiEl.textContent = rssi;
            meta.appendChild(rssiEl);
        }

        item.appendChild(addr);
        if (meta.children.length > 0) {
            item.appendChild(meta);
        }

        item.addEventListener('click', () => {
            const marker = findMarkerForDevice(d);
            if (marker) {
                marker.openPopup();
            }
        });

        listContainer.appendChild(item);
    });
}

function findMarkerForDevice(device) {
    for (const cluster of Object.values(clusterGroups)) {
        let foundMarker = null;
        cluster.eachLayer(layer => {
            if (layer instanceof L.CircleMarker && !foundMarker) {
                const latlng = layer.getLatLng();
                if (Math.abs(latlng.lat - device.lat) < 0.0001 && Math.abs(latlng.lng - device.lon) < 0.0001) {
                    foundMarker = layer;
                }
            }
        });
        if (foundMarker) return foundMarker;
    }
    return null;
}

function pointInShape(device, shape) {
    const latlng = L.latLng(device.lat, device.lon);
    if (shape instanceof L.Rectangle) {
        return shape.getBounds().contains(latlng);
    }
    // Polygon: ray-casting point-in-polygon
    const pts = shape.getLatLngs()[0];
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
        const xi = pts[i].lng, yi = pts[i].lat;
        const xj = pts[j].lng, yj = pts[j].lat;
        const intersect = ((yi > latlng.lat) !== (yj > latlng.lat))
            && (latlng.lng < (xj - xi) * (latlng.lat - yi) / (yj - yi) + xi);
        if (intersect) inside = !inside;
    }
    return inside;
}

function setupControls() {
    document.getElementById('session-select').addEventListener('change', refreshMapData);

    document.getElementById('toggle-ble').addEventListener('change', (e) => {
        if (e.target.checked) {
            clusterGroups.ble.addTo(map);
        } else {
            map.removeLayer(clusterGroups.ble);
        }
    });

    document.getElementById('toggle-wifi').addEventListener('change', (e) => {
        if (e.target.checked) {
            clusterGroups.wifi.addTo(map);
        } else {
            map.removeLayer(clusterGroups.wifi);
        }
    });

    document.getElementById('toggle-rf').addEventListener('change', (e) => {
        if (e.target.checked) {
            clusterGroups.rf.addTo(map);
        } else {
            map.removeLayer(clusterGroups.rf);
        }
    });

    document.getElementById('toggle-track').addEventListener('change', (e) => {
        if (e.target.checked) {
            layers.track.addTo(map);
        } else {
            map.removeLayer(layers.track);
        }
    });

    document.getElementById('toggle-heatmap').addEventListener('change', (e) => {
        if (e.target.checked && layers.heatmap) {
            layers.heatmap.addTo(map);
            // Optionally hide clusters when heatmap is on
            map.removeLayer(clusterGroups.ble);
            map.removeLayer(clusterGroups.wifi);
            map.removeLayer(clusterGroups.rf);
        } else {
            if (layers.heatmap) {
                map.removeLayer(layers.heatmap);
            }
            // Show clusters again
            if (document.getElementById('toggle-ble').checked) clusterGroups.ble.addTo(map);
            if (document.getElementById('toggle-wifi').checked) clusterGroups.wifi.addTo(map);
            if (document.getElementById('toggle-rf').checked) clusterGroups.rf.addTo(map);
        }
    });

    document.getElementById('fit-btn').addEventListener('click', () => {
        const bounds = L.latLngBounds();
        [clusterGroups.ble, clusterGroups.wifi, clusterGroups.rf].forEach(group => {
            group.eachLayer(layer => {
                if (layer instanceof L.CircleMarker) {
                    bounds.extend(layer.getLatLng());
                }
            });
        });
        allPoints.forEach(p => bounds.extend([p.lat, p.lon]));
        if (bounds.isValid()) {
            map.fitBounds(bounds, { padding: [50, 50] });
        }
    });

    document.getElementById('draw-rect-btn').addEventListener('click', () => startDrawing('rectangle'));
    document.getElementById('draw-poly-btn').addEventListener('click', () => startDrawing('polygon'));
    document.getElementById('clear-filter-btn').addEventListener('click', clearAreaFilter);
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
