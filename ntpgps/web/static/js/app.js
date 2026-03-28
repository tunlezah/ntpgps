/**
 * NTP GPS Server - Dashboard Application
 * Real-time WebSocket-driven dashboard for GPS-disciplined NTP monitoring.
 */

(function () {
    'use strict';

    // --- State ---
    let ws = null;
    let reconnectDelay = 1000;
    let lastStatus = null;
    let driftHistory = [];
    const MAX_DRIFT_POINTS = 300;

    // Canberra timezone
    const CANBERRA_TZ = 'Australia/Sydney'; // Canberra uses same tz as Sydney

    // --- DOM Elements ---
    const $ = (id) => document.getElementById(id);

    // --- WebSocket ---
    function connectWebSocket() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/ws`;

        ws = new WebSocket(url);

        ws.onopen = function () {
            setConnectionStatus('connected', 'Connected');
            reconnectDelay = 1000;
        };

        ws.onclose = function () {
            setConnectionStatus('disconnected', 'Disconnected');
            scheduleReconnect();
        };

        ws.onerror = function () {
            setConnectionStatus('disconnected', 'Error');
        };

        ws.onmessage = function (event) {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'status') {
                    lastStatus = data;
                    updateDashboard(data);
                }
            } catch (e) {
                console.error('WS parse error:', e);
            }
        };
    }

    function scheduleReconnect() {
        setTimeout(function () {
            reconnectDelay = Math.min(reconnectDelay * 1.5, 30000);
            connectWebSocket();
        }, reconnectDelay);
    }

    function setConnectionStatus(cls, text) {
        const el = $('connStatus');
        el.className = 'connection-status ' + cls;
        el.textContent = text;
    }

    // --- Keep-alive ping ---
    setInterval(function () {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
        }
    }, 15000);

    // --- Dashboard Update ---
    function updateDashboard(data) {
        updateTime(data);
        updateGPSHealth(data);
        updatePPS(data);
        updateSatellites(data);
        updateDrift(data);
        updateAlerts(data);
        updateControls(data);
        updateTracking(data);
    }

    function updateTime(data) {
        // UTC time
        const now = new Date();
        $('timeUTC').textContent = formatTime(now, 'UTC');

        // Canberra time
        $('timeLocal').textContent = formatTime(now, CANBERRA_TZ);

        // DST check
        const isDST = isCanberraDST(now);
        $('dstIndicator').textContent = 'DST: ' + (isDST ? 'YES' : 'NO');

        // Source info
        const src = data.source || {};
        $('activeSource').textContent = src.active_source || '--';
        $('stratum').textContent = src.stratum !== undefined ? src.stratum : '--';
        $('sourceState').textContent = formatState(src.state || '--');

        // Color stratum
        const stratEl = $('stratum');
        stratEl.style.color = src.stratum === 1 ? 'var(--status-ok)' :
            src.stratum <= 3 ? 'var(--status-warn)' : 'var(--status-error)';
    }

    function formatTime(date, tz) {
        try {
            return date.toLocaleTimeString('en-AU', {
                timeZone: tz,
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
        } catch (e) {
            return '--:--:--';
        }
    }

    function isCanberraDST(date) {
        // Check if Canberra is in DST by comparing UTC offsets
        const jan = new Date(date.getFullYear(), 0, 1);
        const jul = new Date(date.getFullYear(), 6, 1);
        try {
            const janOff = getTimezoneOffset(jan, CANBERRA_TZ);
            const julOff = getTimezoneOffset(jul, CANBERRA_TZ);
            const curOff = getTimezoneOffset(date, CANBERRA_TZ);
            const stdOff = Math.max(janOff, julOff); // Standard time has larger offset (more behind UTC)
            return curOff < stdOff;
        } catch (e) {
            return false;
        }
    }

    function getTimezoneOffset(date, tz) {
        const utcStr = date.toLocaleString('en-US', { timeZone: 'UTC' });
        const tzStr = date.toLocaleString('en-US', { timeZone: tz });
        return (new Date(utcStr) - new Date(tzStr)) / 60000;
    }

    function formatState(state) {
        const names = {
            'gps_locked': 'GPS Locked',
            'gps_degraded': 'GPS Degraded',
            'holdover': 'Holdover',
            'network': 'Network',
            'startup': 'Starting...',
            'manual_gps': 'GPS (Manual)',
            'manual_network': 'Net (Manual)'
        };
        return names[state] || state;
    }

    function updateGPSHealth(data) {
        const fix = (data.gps || {}).fix || {};
        const sky = (data.gps || {}).sky || {};
        const validation = data.validation || {};
        const checks = validation.checks || {};

        // Fix type
        const fixNames = { 0: 'Unknown', 1: 'No Fix', 2: '2D Fix', 3: '3D Fix' };
        const fixEl = $('fixType');
        fixEl.textContent = fixNames[fix.mode] || 'Unknown';
        fixEl.className = 'health-value' + (fix.mode >= 3 ? ' fix-3d' : fix.mode >= 2 ? ' fix-2d' : '');
        fixEl.style.color = fix.mode >= 3 ? 'var(--status-ok)' : fix.mode >= 2 ? 'var(--status-warn)' : 'var(--status-error)';

        // Satellites
        $('satsUsed').textContent = sky.n_used || 0;
        $('satsVisible').textContent = sky.n_visible || 0;

        // PDOP
        const pdop = sky.pdop || 99.99;
        const pdopEl = $('pdop');
        pdopEl.textContent = pdop.toFixed(1);
        pdopEl.style.color = pdop <= 2 ? 'var(--status-ok)' : pdop <= 6 ? 'var(--status-warn)' : 'var(--status-error)';

        // GPS Sanity
        setIndicator($('gpsSanity'), validation.trusted ? 'ok' : validation.usable ? 'warn' : 'error',
            validation.trusted ? 'GOOD' : validation.usable ? 'DEGRADED' : 'BAD');

        // Time Valid
        setIndicator($('timeValid'), checks.time_present ? 'ok' : 'error',
            checks.time_present ? 'YES' : 'NO');

        // Geometry
        $('geometryQuality').textContent = (sky.geometry_quality || '--').toUpperCase();
    }

    function setIndicator(el, level, text) {
        el.textContent = text;
        el.className = 'health-indicator indicator-' + (level === 'ok' ? 'ok' : level === 'warn' ? 'warn' : level === 'error' ? 'error' : 'unknown');
    }

    function updatePPS(data) {
        const pps = (data.gps || {}).pps || {};

        setIndicator($('ppsPresent'), pps.present ? 'ok' : 'error', pps.present ? 'YES' : 'NO');
        setIndicator($('ppsStable'), !pps.present ? 'unknown' : pps.stable ? 'ok' : 'warn',
            !pps.present ? 'N/A' : pps.stable ? 'STABLE' : 'UNSTABLE');
        $('ppsOffset').textContent = pps.present ? pps.offset_us.toFixed(1) + ' \u00B5s' : '-- \u00B5s';
        $('ppsJitter').textContent = pps.present ? pps.jitter_us.toFixed(1) + ' \u00B5s' : '-- \u00B5s';
    }

    function updateSatellites(data) {
        const sky = (data.gps || {}).sky || {};
        const sats = sky.satellites || [];
        const chart = $('satelliteChart');

        $('satSummary').textContent = `(${sky.n_used || 0} used / ${sky.n_visible || 0} visible)`;

        // Sort: used first, then by signal strength descending
        const sorted = [...sats].sort((a, b) => {
            if (a.used !== b.used) return b.used - a.used;
            return b.signal_strength - a.signal_strength;
        });

        // Build bars
        chart.innerHTML = '';
        const maxSS = 50; // Max signal strength for bar scaling

        for (const sat of sorted) {
            const wrapper = document.createElement('div');
            wrapper.className = 'sat-bar-wrapper';

            const ssLabel = document.createElement('div');
            ssLabel.className = 'sat-bar-ss';
            ssLabel.textContent = sat.signal_strength > 0 ? Math.round(sat.signal_strength) : '';

            const bar = document.createElement('div');
            bar.className = 'sat-bar' + (sat.used ? '' : ' unused');
            const height = Math.max(2, (sat.signal_strength / maxSS) * 100);
            bar.style.height = height + '%';

            // Color by signal strength
            if (sat.signal_strength <= 0) {
                bar.style.background = 'var(--signal-none)';
            } else if (sat.signal_strength < 16) {
                bar.style.background = 'var(--signal-weak)';
            } else if (sat.signal_strength < 26) {
                bar.style.background = 'var(--signal-moderate)';
            } else {
                bar.style.background = 'var(--signal-strong)';
            }

            const label = document.createElement('div');
            label.className = 'sat-bar-label';
            // Show constellation prefix
            const prefix = { 'GPS': 'G', 'GLONASS': 'R', 'GALILEO': 'E', 'BEIDOU': 'C', 'SBAS': 'S', 'QZSS': 'J' };
            label.textContent = (prefix[sat.constellation] || '?') + sat.prn;
            label.title = `${sat.constellation} PRN ${sat.prn}\nEl: ${sat.elevation}\u00B0 Az: ${sat.azimuth}\u00B0\nSS: ${sat.signal_strength} dB\nUsed: ${sat.used ? 'Yes' : 'No'}`;

            wrapper.appendChild(ssLabel);
            wrapper.appendChild(bar);
            wrapper.appendChild(label);
            chart.appendChild(wrapper);
        }
    }

    function updateDrift(data) {
        const drift = data.drift || {};
        const samples = drift.recent_samples || [];
        const stats = drift.statistics || {};

        // Update stats display
        $('driftRate').textContent = (stats.drift_rate_ppm || 0).toFixed(3);
        $('driftSamples').textContent = stats.sample_count || 0;

        // Store new samples
        for (const s of samples) {
            if (!driftHistory.length || s.timestamp > driftHistory[driftHistory.length - 1].timestamp) {
                driftHistory.push(s);
            }
        }
        while (driftHistory.length > MAX_DRIFT_POINTS) {
            driftHistory.shift();
        }

        drawDriftChart();
    }

    function drawDriftChart() {
        const canvas = $('driftCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * (window.devicePixelRatio || 1);
        canvas.height = rect.height * (window.devicePixelRatio || 1);
        ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);

        const w = rect.width;
        const h = rect.height;
        const pad = { top: 10, right: 10, bottom: 20, left: 45 };
        const plotW = w - pad.left - pad.right;
        const plotH = h - pad.top - pad.bottom;

        // Clear
        ctx.clearRect(0, 0, w, h);

        if (driftHistory.length < 2) {
            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim();
            ctx.font = '11px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Waiting for drift data...', w / 2, h / 2);
            return;
        }

        // Compute range
        let minVal = Infinity, maxVal = -Infinity;
        for (const s of driftHistory) {
            minVal = Math.min(minVal, s.gps_offset_ms, s.network_offset_ms);
            maxVal = Math.max(maxVal, s.gps_offset_ms, s.network_offset_ms);
        }
        const range = Math.max(maxVal - minVal, 0.1);
        minVal -= range * 0.1;
        maxVal += range * 0.1;

        const styles = getComputedStyle(document.documentElement);
        const gridColor = styles.getPropertyValue('--canvas-grid').trim();
        const gpsColor = styles.getPropertyValue('--canvas-line-gps').trim();
        const netColor = styles.getPropertyValue('--canvas-line-net').trim();
        const textColor = styles.getPropertyValue('--text-muted').trim();

        // Grid
        ctx.strokeStyle = gridColor;
        ctx.lineWidth = 1;
        for (let i = 0; i <= 4; i++) {
            const y = pad.top + (plotH * i / 4);
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(pad.left + plotW, y);
            ctx.stroke();
        }

        // Y-axis labels
        ctx.fillStyle = textColor;
        ctx.font = '9px monospace';
        ctx.textAlign = 'right';
        for (let i = 0; i <= 4; i++) {
            const val = maxVal - (maxVal - minVal) * i / 4;
            const y = pad.top + (plotH * i / 4);
            ctx.fillText(val.toFixed(2) + 'ms', pad.left - 4, y + 3);
        }

        // Plot function
        function plotLine(key, color) {
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            const t0 = driftHistory[0].timestamp;
            const tRange = driftHistory[driftHistory.length - 1].timestamp - t0 || 1;
            for (let i = 0; i < driftHistory.length; i++) {
                const x = pad.left + ((driftHistory[i].timestamp - t0) / tRange) * plotW;
                const y = pad.top + ((maxVal - driftHistory[i][key]) / (maxVal - minVal)) * plotH;
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.stroke();
        }

        plotLine('gps_offset_ms', gpsColor);
        plotLine('network_offset_ms', netColor);

        // Legend
        ctx.font = '9px sans-serif';
        ctx.textAlign = 'left';
        ctx.fillStyle = gpsColor;
        ctx.fillText('GPS', pad.left + 5, pad.top + 10);
        ctx.fillStyle = netColor;
        ctx.fillText('Network', pad.left + 35, pad.top + 10);
    }

    function updateAlerts(data) {
        const alerts = data.alerts || [];
        const list = $('alertList');
        $('alertCount').textContent = alerts.length;

        if (alerts.length === 0) {
            list.innerHTML = '<div class="alert-empty">No alerts</div>';
            return;
        }

        // Show most recent 20
        const recent = alerts.slice(-20).reverse();
        list.innerHTML = '';
        for (const alert of recent) {
            const item = document.createElement('div');
            item.className = 'alert-item ' + (alert.level || 'info');

            const timeStr = new Date(alert.timestamp * 1000).toLocaleTimeString('en-AU', { hour12: false });
            item.innerHTML = `<span class="alert-time">${timeStr}</span>${escapeHtml(alert.message)}`;
            list.appendChild(item);
        }
    }

    function updateControls(data) {
        const src = data.source || {};
        const mode = src.mode || 'auto';

        // Highlight active mode button
        document.querySelectorAll('.btn-source').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
    }

    function updateTracking(data) {
        const tracking = ((data.chrony || {}).tracking) || {};
        $('tiRef').textContent = tracking.ref_name || tracking.ref_id || '--';
        $('tiOffset').textContent = tracking.last_offset_us !== undefined ? tracking.last_offset_us.toFixed(1) + ' \u00B5s' : '--';
        $('tiFreq').textContent = tracking.frequency_ppm !== undefined ? tracking.frequency_ppm.toFixed(3) + ' ppm' : '--';
        $('tiSkew').textContent = tracking.skew_ppm !== undefined ? tracking.skew_ppm.toFixed(3) + ' ppm' : '--';
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // --- Theme ---
    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        document.querySelectorAll('.btn-theme').forEach(b => b.classList.remove('active'));
        const btn = document.getElementById('btnTheme' + theme.charAt(0).toUpperCase() + theme.slice(1));
        if (btn) btn.classList.add('active');
        localStorage.setItem('ntpgps-theme', theme);
    }

    function initTheme() {
        const saved = localStorage.getItem('ntpgps-theme');
        if (saved) setTheme(saved);
    }

    // --- Controls ---
    function initControls() {
        // Theme buttons
        $('btnThemeDark').onclick = () => setTheme('dark');
        $('btnThemeLight').onclick = () => setTheme('light');
        $('btnThemeSystem').onclick = () => setTheme('system');

        // Source mode buttons
        document.querySelectorAll('.btn-source').forEach(btn => {
            btn.onclick = function () {
                const mode = this.dataset.mode;
                fetch('/api/source/mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: mode })
                }).catch(console.error);
            };
        });

        // Restart NTP
        $('btnRestartNTP').onclick = function () {
            if (!confirm('Restart the NTP service? This will briefly interrupt time synchronization.')) return;
            fetch('/api/chrony/restart', { method: 'POST' })
                .then(r => r.json())
                .then(d => {
                    alert(d.message || (d.success ? 'Restarted' : 'Failed'));
                })
                .catch(err => alert('Error: ' + err));
        };
    }

    // --- Local clock update (runs independently of WS) ---
    function updateLocalClock() {
        const now = new Date();
        $('timeUTC').textContent = formatTime(now, 'UTC');
        $('timeLocal').textContent = formatTime(now, CANBERRA_TZ);
    }

    // --- Init ---
    function init() {
        initTheme();
        initControls();
        connectWebSocket();
        // Update clock every second
        setInterval(updateLocalClock, 1000);
        // Redraw drift on resize
        window.addEventListener('resize', drawDriftChart);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
