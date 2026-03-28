/**
 * NTP GPS Server - Dashboard Application
 *
 * Real-time WebSocket-driven dashboard for GPS-disciplined NTP monitoring.
 *
 * Architecture decisions:
 * - WebSocket for real-time updates (matches existing Flask-Sock backend)
 * - Vanilla JS, zero dependencies, no build step
 * - Custom canvas chart for drift graphs (lightweight, no library)
 * - Smooth bar transitions via CSS transitions (height changes only)
 * - DOM node reuse in satellite chart (avoids layout thrashing)
 * - WCAG 2.1 AA: aria-labels, screen-reader text, focus management
 * - Colorblind-safe: patterns + icons supplement color coding
 */

(function () {
    'use strict';

    // =========================================================================
    // State
    // =========================================================================

    let ws = null;
    let reconnectDelay = 1000;
    let lastStatus = null;
    let driftHistory = [];
    const MAX_DRIFT_POINTS = 300;

    // Timezone for local display
    const LOCAL_TZ = 'Australia/Sydney'; // Canberra uses same tz

    // DOM helper
    const $ = (id) => document.getElementById(id);

    // =========================================================================
    // 1. Theme Manager
    // =========================================================================

    function setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        document.querySelectorAll('.btn-theme').forEach(b => {
            b.classList.remove('active');
            b.setAttribute('aria-checked', 'false');
        });
        const btnId = {
            dark: 'btnThemeDark',
            light: 'btnThemeLight',
            system: 'btnThemeSystem'
        }[theme];
        const btn = $(btnId);
        if (btn) {
            btn.classList.add('active');
            btn.setAttribute('aria-checked', 'true');
        }
        localStorage.setItem('ntpgps-theme', theme);
    }

    function initTheme() {
        const saved = localStorage.getItem('ntpgps-theme');
        if (saved) setTheme(saved);
    }

    // =========================================================================
    // 2. Toast Notifications
    // =========================================================================

    const Toasts = {
        container: null,
        DURATION: 5000,

        init() {
            this.container = $('toasts');
        },

        show(message, level) {
            level = level || 'info';
            const el = document.createElement('div');
            el.className = 'toast toast--' + level;
            el.setAttribute('role', 'alert');
            el.textContent = message;
            this.container.appendChild(el);

            setTimeout(() => {
                el.classList.add('toast--dismiss');
                el.addEventListener('animationend', () => el.remove());
            }, this.DURATION);
        }
    };

    // =========================================================================
    // 3. Confirmation Dialog
    // =========================================================================

    const ConfirmDialog = {
        _resolve: null,

        show(title, message) {
            $('dialogTitle').textContent = title;
            $('dialogMessage').textContent = message;
            const overlay = $('dialogOverlay');
            overlay.classList.add('visible');
            overlay.setAttribute('aria-hidden', 'false');
            $('dialogConfirm').focus();
            return new Promise(resolve => { this._resolve = resolve; });
        },

        close(result) {
            const overlay = $('dialogOverlay');
            overlay.classList.remove('visible');
            overlay.setAttribute('aria-hidden', 'true');
            if (this._resolve) {
                this._resolve(result);
                this._resolve = null;
            }
        },

        init() {
            const self = this;
            $('dialogCancel').addEventListener('click', () => self.close(false));
            $('dialogConfirm').addEventListener('click', () => self.close(true));
            $('dialogOverlay').addEventListener('click', (e) => {
                if (e.target === $('dialogOverlay')) self.close(false);
            });
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && $('dialogOverlay').classList.contains('visible')) {
                    self.close(false);
                }
            });
        }
    };

    // =========================================================================
    // 4. WebSocket Connection
    // =========================================================================

    function connectWebSocket() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = proto + '//' + location.host + '/ws';

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

    // Keep-alive ping
    setInterval(function () {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
        }
    }, 15000);

    // =========================================================================
    // 5. Dashboard Update Dispatcher
    // =========================================================================

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

    // =========================================================================
    // 6. Time Panel
    // =========================================================================

    function updateTime(data) {
        const now = new Date();
        $('timeUTC').textContent = formatTime(now, 'UTC');
        $('timeLocal').textContent = formatTime(now, LOCAL_TZ);

        // DST
        const isDST = isLocalDST(now);
        $('dstIndicator').textContent = 'DST: ' + (isDST ? 'YES' : 'NO');

        // Source info
        const src = data.source || {};
        $('activeSource').textContent = src.active_source || '--';

        const stratVal = src.stratum;
        $('stratum').textContent = stratVal !== undefined ? stratVal : '--';
        const stratEl = $('stratum');
        stratEl.style.color = stratVal === 1 ? 'var(--status-ok)' :
            stratVal <= 3 ? 'var(--status-warn)' : 'var(--status-error)';

        const state = src.state || '--';
        $('sourceState').textContent = formatState(state);

        // Source badge
        updateSourceBadge(state, src.active_source);
    }

    function updateSourceBadge(state, source) {
        const badge = $('sourceBadge');
        if (!badge) return;

        const isGPS = state === 'gps_locked' || state === 'manual_gps';
        const isDegraded = state === 'gps_degraded' || state === 'holdover';

        if (isGPS) {
            badge.className = 'source-badge source-badge--gps';
            badge.textContent = 'GPS';
            badge.setAttribute('aria-label', 'Time source: GPS locked');
        } else if (isDegraded) {
            badge.className = 'source-badge source-badge--degraded';
            badge.textContent = formatState(state);
            badge.setAttribute('aria-label', 'Time source: ' + formatState(state));
        } else {
            badge.className = 'source-badge source-badge--free';
            badge.textContent = source || state;
            badge.setAttribute('aria-label', 'Time source: ' + (source || state));
        }
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

    function isLocalDST(date) {
        try {
            const jan = new Date(date.getFullYear(), 0, 1);
            const jul = new Date(date.getFullYear(), 6, 1);
            const janOff = getTimezoneOffset(jan, LOCAL_TZ);
            const julOff = getTimezoneOffset(jul, LOCAL_TZ);
            const curOff = getTimezoneOffset(date, LOCAL_TZ);
            const stdOff = Math.max(janOff, julOff);
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
            'gps_degraded': 'Degraded',
            'holdover': 'Holdover',
            'network': 'Network',
            'startup': 'Starting',
            'manual_gps': 'GPS (Manual)',
            'manual_network': 'Net (Manual)'
        };
        return names[state] || state;
    }

    // =========================================================================
    // 7. GPS Health Panel
    // =========================================================================

    function updateGPSHealth(data) {
        const fix = (data.gps || {}).fix || {};
        const sky = (data.gps || {}).sky || {};
        const validation = data.validation || {};
        const checks = validation.checks || {};

        // Fix type
        const fixNames = { 0: 'Unknown', 1: 'No Fix', 2: '2D Fix', 3: '3D Fix' };
        const fixEl = $('fixType');
        fixEl.textContent = fixNames[fix.mode] || 'Unknown';
        fixEl.style.color = fix.mode >= 3 ? 'var(--status-ok)' :
            fix.mode >= 2 ? 'var(--status-warn)' : 'var(--status-error)';

        // Satellites
        $('satsUsed').textContent = sky.n_used || 0;
        $('satsVisible').textContent = sky.n_visible || 0;

        // PDOP
        const pdop = sky.pdop || 99.99;
        const pdopEl = $('pdop');
        pdopEl.textContent = pdop.toFixed(1);
        pdopEl.style.color = pdop <= 2 ? 'var(--status-ok)' :
            pdop <= 6 ? 'var(--status-warn)' : 'var(--status-error)';

        // GPS Sanity (with accessible text)
        setIndicator($('gpsSanity'),
            validation.trusted ? 'ok' : validation.usable ? 'warn' : 'error',
            validation.trusted ? 'GOOD' : validation.usable ? 'DEGRADED' : 'BAD');
        $('gpsSanitySR').textContent = 'GPS sanity: ' +
            (validation.trusted ? 'healthy' : validation.usable ? 'warning' : 'error');

        // Time Valid
        setIndicator($('timeValid'),
            checks.time_present ? 'ok' : 'error',
            checks.time_present ? 'YES' : 'NO');
        $('timeValidSR').textContent = 'Time valid: ' + (checks.time_present ? 'yes' : 'no');

        // Geometry
        $('geometryQuality').textContent = (sky.geometry_quality || '--').toString().toUpperCase();
    }

    function setIndicator(el, level, text) {
        el.textContent = text;
        const cls = level === 'ok' ? 'ok' : level === 'warn' ? 'warn' :
            level === 'error' ? 'error' : 'unknown';
        el.className = 'health-indicator indicator-' + cls;
    }

    // =========================================================================
    // 8. PPS Panel
    // =========================================================================

    let _lastPPSState = null;

    function updatePPS(data) {
        const pps = (data.gps || {}).pps || {};

        setIndicator($('ppsPresent'),
            pps.present ? 'ok' : 'error',
            pps.present ? 'YES' : 'NO');

        setIndicator($('ppsStable'),
            !pps.present ? 'unknown' : pps.stable ? 'ok' : 'warn',
            !pps.present ? 'N/A' : pps.stable ? 'STABLE' : 'UNSTABLE');

        $('ppsOffset').textContent = pps.present ?
            pps.offset_us.toFixed(1) + ' \u00B5s' : '-- \u00B5s';
        $('ppsJitter').textContent = pps.present ?
            pps.jitter_us.toFixed(1) + ' \u00B5s' : '-- \u00B5s';

        // PPS pulse animation (flash on each update when present)
        if (pps.present) {
            const indicator = $('ppsPresent');
            indicator.classList.remove('pps-pulse');
            void indicator.offsetWidth; // force reflow
            indicator.classList.add('pps-pulse');
        }

        // Detect PPS loss/gain transitions for toast alerts
        if (_lastPPSState !== null && _lastPPSState !== pps.present) {
            if (pps.present) {
                Toasts.show('PPS signal acquired', 'info');
            } else {
                Toasts.show('PPS signal lost', 'error');
            }
        }
        _lastPPSState = pps.present;
    }

    // =========================================================================
    // 9. Satellite Bar Chart
    // =========================================================================

    function updateSatellites(data) {
        const sky = (data.gps || {}).sky || {};
        const sats = sky.satellites || [];
        const chart = $('satelliteChart');

        // Summary text
        const usedCount = sky.n_used || 0;
        const visCount = sky.n_visible || 0;
        $('satSummary').textContent = '(' + usedCount + ' used / ' + visCount + ' visible)';

        // Accessible description on the chart container
        chart.setAttribute('aria-label',
            'Satellite signal strength: ' + usedCount + ' of ' + visCount + ' in use');

        // Sort: used first, then by signal strength descending
        const sorted = sats.slice().sort(function (a, b) {
            if (a.used !== b.used) return b.used - a.used;
            return b.signal_strength - a.signal_strength;
        });

        const maxSS = 50;
        const constellationPrefix = {
            'GPS': 'G', 'GLONASS': 'R', 'GALILEO': 'E',
            'BEIDOU': 'C', 'SBAS': 'S', 'QZSS': 'J'
        };

        // Reuse DOM nodes for smooth CSS transitions instead of innerHTML rebuild
        while (chart.children.length > sorted.length) {
            chart.removeChild(chart.lastChild);
        }

        for (let i = 0; i < sorted.length; i++) {
            const sat = sorted[i];
            let wrapper = chart.children[i];

            if (!wrapper) {
                wrapper = document.createElement('div');
                wrapper.className = 'sat-bar-wrapper';
                wrapper.innerHTML =
                    '<div class="sat-bar-ss"></div>' +
                    '<div class="sat-bar"></div>' +
                    '<div class="sat-bar-label"></div>';
                chart.appendChild(wrapper);
            }

            const ssLabel = wrapper.children[0];
            const bar = wrapper.children[1];
            const prnLabel = wrapper.children[2];

            // Height (percentage of max)
            const height = Math.max(2, (sat.signal_strength / maxSS) * 100);
            bar.style.height = height + '%';

            // Strength class (determines color + pattern)
            let strengthClass;
            if (sat.signal_strength <= 0) {
                strengthClass = 'strength-none';
            } else if (sat.signal_strength < 16) {
                strengthClass = 'strength-weak';
            } else if (sat.signal_strength < 26) {
                strengthClass = 'strength-moderate';
            } else {
                strengthClass = 'strength-strong';
            }

            bar.className = 'sat-bar ' + strengthClass + (sat.used ? '' : ' unused');

            // SNR label
            ssLabel.textContent = sat.signal_strength > 0 ?
                Math.round(sat.signal_strength) : '';

            // PRN label
            const prefix = constellationPrefix[sat.constellation] || '?';
            prnLabel.textContent = prefix + sat.prn;

            // Tooltip
            prnLabel.title = sat.constellation + ' PRN ' + sat.prn +
                '\nEl: ' + sat.elevation + '\u00B0 Az: ' + sat.azimuth + '\u00B0' +
                '\nSS: ' + sat.signal_strength + ' dB' +
                '\nUsed: ' + (sat.used ? 'Yes' : 'No');

            // Per-bar accessible label
            bar.setAttribute('aria-label',
                'PRN ' + prefix + sat.prn + ': ' + Math.round(sat.signal_strength) +
                ' dB, ' + (sat.used ? 'in use' : 'not used') +
                ', signal ' + strengthClass.replace('strength-', ''));
        }
    }

    // =========================================================================
    // 10. Drift Chart (Custom Canvas, Zero Dependencies)
    // =========================================================================

    let _driftRAF = null;
    let _driftDirty = false;

    function updateDrift(data) {
        const drift = data.drift || {};
        const samples = drift.recent_samples || [];
        const stats = drift.statistics || {};

        // Stats display
        $('driftRate').textContent = (stats.drift_rate_ppm || 0).toFixed(3);
        $('driftSamples').textContent = stats.sample_count || 0;

        // Append new samples (deduplicated by timestamp)
        for (const s of samples) {
            if (!driftHistory.length ||
                s.timestamp > driftHistory[driftHistory.length - 1].timestamp) {
                driftHistory.push(s);
            }
        }
        while (driftHistory.length > MAX_DRIFT_POINTS) {
            driftHistory.shift();
        }

        _driftDirty = true;
    }

    function driftChartLoop() {
        if (_driftDirty) {
            drawDriftChart();
            _driftDirty = false;
        }
        _driftRAF = requestAnimationFrame(driftChartLoop);
    }

    function drawDriftChart() {
        const canvas = $('driftCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const rect = canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;

        // High-DPI canvas sizing
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        const w = rect.width;
        const h = rect.height;
        const pad = { top: 10, right: 10, bottom: 20, left: 48 };
        const plotW = w - pad.left - pad.right;
        const plotH = h - pad.top - pad.bottom;

        ctx.clearRect(0, 0, w, h);

        // Resolve theme colors
        const styles = getComputedStyle(document.documentElement);
        const gridColor = styles.getPropertyValue('--chart-grid').trim();
        const gpsColor = styles.getPropertyValue('--canvas-line-gps').trim();
        const netColor = styles.getPropertyValue('--canvas-line-net').trim();
        const textColor = styles.getPropertyValue('--chart-text').trim();

        if (driftHistory.length < 2) {
            ctx.fillStyle = textColor;
            ctx.font = '11px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Waiting for drift data\u2026', w / 2, h / 2);
            return;
        }

        // Compute Y range
        let minVal = Infinity, maxVal = -Infinity;
        for (const s of driftHistory) {
            minVal = Math.min(minVal, s.gps_offset_ms, s.network_offset_ms);
            maxVal = Math.max(maxVal, s.gps_offset_ms, s.network_offset_ms);
        }
        const range = Math.max(maxVal - minVal, 0.1);
        minVal -= range * 0.1;
        maxVal += range * 0.1;

        // Grid lines
        ctx.strokeStyle = gridColor;
        ctx.lineWidth = 1;
        const gridLines = 4;
        for (let i = 0; i <= gridLines; i++) {
            const y = pad.top + (plotH * i / gridLines);
            ctx.beginPath();
            ctx.moveTo(pad.left, y);
            ctx.lineTo(pad.left + plotW, y);
            ctx.stroke();
        }

        // Y-axis labels
        ctx.fillStyle = textColor;
        ctx.font = '9px monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        for (let i = 0; i <= gridLines; i++) {
            const val = maxVal - (maxVal - minVal) * i / gridLines;
            const y = pad.top + (plotH * i / gridLines);
            ctx.fillText(val.toFixed(2) + 'ms', pad.left - 4, y);
        }

        // Zero reference line (dashed)
        if (minVal < 0 && maxVal > 0) {
            const zeroY = pad.top + (maxVal / (maxVal - minVal)) * plotH;
            ctx.save();
            ctx.strokeStyle = textColor;
            ctx.globalAlpha = 0.3;
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(pad.left, zeroY);
            ctx.lineTo(pad.left + plotW, zeroY);
            ctx.stroke();
            ctx.restore();
        }

        // X-axis time labels
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillStyle = textColor;
        const labelCount = Math.max(1, Math.floor(driftHistory.length / 5));
        const t0 = driftHistory[0].timestamp;
        const tRange = driftHistory[driftHistory.length - 1].timestamp - t0 || 1;
        for (let i = 0; i < driftHistory.length; i += labelCount) {
            const x = pad.left + ((driftHistory[i].timestamp - t0) / tRange) * plotW;
            const d = new Date(driftHistory[i].timestamp * 1000);
            ctx.fillText(d.toTimeString().slice(0, 8), x, pad.top + plotH + 4);
        }

        // Plot lines
        function plotLine(key, color) {
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.lineJoin = 'round';
            ctx.beginPath();
            for (let i = 0; i < driftHistory.length; i++) {
                const x = pad.left + ((driftHistory[i].timestamp - t0) / tRange) * plotW;
                const y = pad.top + ((maxVal - driftHistory[i][key]) / (maxVal - minVal)) * plotH;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.stroke();

            // Subtle fill under curve
            const lastX = pad.left + ((driftHistory[driftHistory.length - 1].timestamp - t0) / tRange) * plotW;
            ctx.lineTo(lastX, pad.top + plotH);
            ctx.lineTo(pad.left, pad.top + plotH);
            ctx.closePath();
            ctx.globalAlpha = 0.06;
            ctx.fillStyle = color;
            ctx.fill();
            ctx.globalAlpha = 1;
        }

        plotLine('gps_offset_ms', gpsColor);
        plotLine('network_offset_ms', netColor);

        // Legend
        ctx.font = '9px sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillStyle = gpsColor;
        ctx.fillRect(pad.left + 5, pad.top + 4, 10, 2);
        ctx.fillText('GPS', pad.left + 18, pad.top + 1);
        ctx.fillStyle = netColor;
        ctx.fillRect(pad.left + 50, pad.top + 4, 10, 2);
        ctx.fillText('Network', pad.left + 63, pad.top + 1);
    }

    // =========================================================================
    // 11. Alerts
    // =========================================================================

    let _lastAlertCount = 0;

    function updateAlerts(data) {
        const alerts = data.alerts || [];
        const list = $('alertList');
        $('alertCount').textContent = alerts.length;

        if (alerts.length === 0) {
            list.innerHTML = '<div class="alert-empty">No alerts</div>';
            return;
        }

        // Show most recent 30
        const recent = alerts.slice(-30).reverse();
        list.innerHTML = '';

        for (const alert of recent) {
            const item = document.createElement('div');
            item.className = 'alert-item ' + (alert.level || 'info');

            const timeStr = new Date(alert.timestamp * 1000)
                .toLocaleTimeString('en-AU', { hour12: false });
            item.innerHTML = '<span class="alert-time">' + timeStr + '</span>' +
                escapeHtml(alert.message);
            list.appendChild(item);
        }

        // Toast for new alerts (only new ones since last update)
        if (alerts.length > _lastAlertCount) {
            const newAlerts = alerts.slice(_lastAlertCount);
            for (const a of newAlerts) {
                if (a.level === 'critical' || a.level === 'warning') {
                    Toasts.show(a.message, a.level === 'critical' ? 'error' : 'warn');
                }
            }
        }
        _lastAlertCount = alerts.length;
    }

    // =========================================================================
    // 12. Controls
    // =========================================================================

    function updateControls(data) {
        const src = data.source || {};
        const mode = src.mode || 'auto';
        document.querySelectorAll('.btn-source').forEach(function (btn) {
            btn.classList.toggle('active', btn.dataset.mode === mode);
        });
    }

    // =========================================================================
    // 13. NTP Tracking
    // =========================================================================

    function updateTracking(data) {
        const tracking = ((data.chrony || {}).tracking) || {};
        $('tiRef').textContent = tracking.ref_name || tracking.ref_id || '--';
        $('tiOffset').textContent = tracking.last_offset_us !== undefined ?
            tracking.last_offset_us.toFixed(1) + ' \u00B5s' : '--';
        $('tiFreq').textContent = tracking.frequency_ppm !== undefined ?
            tracking.frequency_ppm.toFixed(3) + ' ppm' : '--';
        $('tiSkew').textContent = tracking.skew_ppm !== undefined ?
            tracking.skew_ppm.toFixed(3) + ' ppm' : '--';
    }

    // =========================================================================
    // 14. Utilities
    // =========================================================================

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // =========================================================================
    // 15. Control Event Handlers
    // =========================================================================

    function initControls() {
        // Theme buttons
        $('btnThemeDark').onclick = function () { setTheme('dark'); };
        $('btnThemeLight').onclick = function () { setTheme('light'); };
        $('btnThemeSystem').onclick = function () { setTheme('system'); };

        // Source mode buttons
        document.querySelectorAll('.btn-source').forEach(function (btn) {
            btn.onclick = function () {
                var mode = this.dataset.mode;
                fetch('/api/source/mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: mode })
                }).catch(console.error);
            };
        });

        // Restart NTP (now with proper confirmation dialog)
        $('btnRestartNTP').onclick = async function () {
            const confirmed = await ConfirmDialog.show(
                'Restart NTP',
                'This will restart the NTP service. Clients may lose sync briefly. Continue?'
            );
            if (!confirmed) return;

            try {
                const resp = await fetch('/api/chrony/restart', { method: 'POST' });
                const d = await resp.json();
                if (d.success) {
                    Toasts.show('NTP service restarted', 'info');
                } else {
                    Toasts.show('Restart failed: ' + (d.message || 'Unknown error'), 'error');
                }
            } catch (err) {
                Toasts.show('Error: ' + err.message, 'error');
            }
        };
    }

    // =========================================================================
    // 16. Local Clock (runs independently of WebSocket)
    // =========================================================================

    function updateLocalClock() {
        const now = new Date();
        $('timeUTC').textContent = formatTime(now, 'UTC');
        $('timeLocal').textContent = formatTime(now, LOCAL_TZ);
    }

    // =========================================================================
    // 17. Initialization
    // =========================================================================

    function init() {
        initTheme();
        Toasts.init();
        ConfirmDialog.init();
        initControls();
        connectWebSocket();

        // Clock ticks independently at 1 Hz
        setInterval(updateLocalClock, 1000);

        // Start drift chart render loop
        driftChartLoop();

        // Redraw drift chart on resize
        window.addEventListener('resize', function () { _driftDirty = true; });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
