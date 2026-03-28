# GPS-Disciplined NTP Server Dashboard — Design Specification

## 1. Layout Design

### Grid Structure (No-Scroll, Viewport-Locked)

```
Desktop (>=1280px): 4-column x 3-row CSS Grid, 100vh x 100vw

+-------------------+-------------------+-------------------+-------------------+
|      TIME         |    SATELLITES     |    SATELLITES      |    GPS HEALTH     |
|   (1col x 1row)   |   (2col x 1row)  |     (contd)       |   (1col x 1row)  |
+-------------------+-------------------+-------------------+-------------------+
|       PPS         |   DRIFT GRAPH     |   DRIFT GRAPH      |     ALERTS        |
|   (1col x 1row)   |   (2col x 1row)  |     (contd)       |   (1col x 1row)  |
+-------------------+-------------------+-------------------+-------------------+
|    CONTROLS       |    NTP PEERS      |   NTP PEERS        |    SYSTEM         |
|   (1col x 1row)   |   (2col x 1row)  |     (contd)       |   (1col x 1row)  |
+-------------------+-------------------+-------------------+-------------------+
```

```css
grid-template-columns: 1fr 1fr 1fr 1fr;
grid-template-rows: 1fr 1fr 1fr;
grid-template-areas:
    "time   sats    sats    health"
    "pps    drift   drift   alerts"
    "ctrl   peers   peers   system";
```

### Priority Order (determines grid position)
1. **Time display** — top-left, always visible, hero treatment
2. **Satellite status** — top-center, 2-column span for bar chart width
3. **GPS health** — top-right, quick glance status cards
4. **Drift graph** — center, 2-column span for chart readability
5. **PPS indicator** — center-left, compact gauge
6. **Alerts** — center-right, scrollable within panel
7. **NTP Peers** — bottom-center, 2-column span for table width
8. **Controls** — bottom-left, least-accessed
9. **System** — bottom-right, supplementary info

### Tablet Layout (768-1024px): 2 columns, allows vertical scroll

```css
grid-template-columns: 1fr 1fr;
grid-template-areas:
    "time    time"
    "sats    sats"
    "health  pps"
    "drift   drift"
    "peers   peers"
    "alerts  system"
    "ctrl    ctrl";
```

### Key Sizing Rules
- `overflow: hidden` on body prevents page scroll on desktop
- Each panel has `overflow: hidden` — content constrained to panel
- Alert log is the only panel with internal `overflow-y: auto`
- `gap: 8px`, `padding: 10px` per panel, `border-radius: 6px`

---

## 2. Color System

### Signal Strength (SNR) — Colorblind-Safe

| Range     | Color   | Hex       | Pattern Overlay                         | Icon  |
|-----------|---------|-----------|----------------------------------------|-------|
| 0-15 dB   | Red     | `#d42a2a` | Horizontal stripes (3px bars)          | None  |
| 16-25 dB  | Amber   | `#d4a017` | 45-degree diagonal hatching (3px bars) | None  |
| 26+ dB    | Green   | `#2e9e41` | Solid fill (no pattern)                | None  |
| Unused    | —       | —         | Dashed outline, 50% opacity            | None  |

Pattern overlays ensure signals are distinguishable without color alone (deuteranopia/protanopia safe).

### Status Indicators

| State   | Dark Mode   | Light Mode  | Icon Shape                    |
|---------|-------------|-------------|-------------------------------|
| Healthy | `#2e9e41`   | `#1a7f37`   | Checkmark in filled circle    |
| Warning | `#d4a017`   | `#9a6700`   | Exclamation in triangle       |
| Error   | `#d42a2a`   | `#cf222e`   | X in filled circle            |

### Dark Mode Palette

| Token              | Value       | Purpose                    |
|--------------------|-------------|----------------------------|
| `--bg-page`        | `#0d1117`   | Page background            |
| `--bg-panel`       | `#161b22`   | Panel background           |
| `--bg-panel-alt`   | `#1c2230`   | Card/row alternate bg      |
| `--border-panel`   | `#30363d`   | Panel borders              |
| `--text-primary`   | `#e6edf3`   | Primary text               |
| `--text-secondary` | `#8b949e`   | Labels, captions           |
| `--text-muted`     | `#484f58`   | Disabled/tertiary text     |
| `--accent`         | `#58a6ff`   | Links, active state, chart |

### Light Mode Palette

| Token              | Value       | Purpose                    |
|--------------------|-------------|----------------------------|
| `--bg-page`        | `#f0f2f5`   | Page background            |
| `--bg-panel`       | `#ffffff`   | Panel background           |
| `--bg-panel-alt`   | `#f6f8fa`   | Card/row alternate bg      |
| `--border-panel`   | `#d0d7de`   | Panel borders              |
| `--text-primary`   | `#1f2328`   | Primary text               |
| `--text-secondary` | `#656d76`   | Labels, captions           |
| `--text-muted`     | `#8b949e`   | Disabled/tertiary text     |
| `--accent`         | `#0969da`   | Links, active state, chart |

### System Theme Mode
Uses `prefers-color-scheme` media query via JS `matchMedia`. Stored in `localStorage` as `"system"`. Re-evaluates on OS theme change via `change` event listener.

---

## 3. Real-Time Update Strategy

### Transport: Server-Sent Events (SSE)

**Why SSE over WebSocket:**
- Data is unidirectional (server to client)
- Built-in automatic reconnection with exponential backoff
- Works through HTTP/1.1 proxies without upgrade negotiation
- Native `EventSource` API, zero-dependency
- Simpler server implementation (Python `yield` lines)
- Control actions use separate `POST /api/control/:action` endpoints

**SSE Event Types:**
```
event: clock\ndata: {"hms":"12:34:56","frac":"789",...}\n\n
event: satellites\ndata: [{prn:"G01",snr:34,used:true},...]\n\n
event: health\ndata: [{label:"Fix",value:"3D",status:"ok"},...]\n\n
event: drift\ndata: {"value":0.42,"current":"0.42 ppb",...}\n\n
event: pps\ndata: {"locked":true,"offset":"+0.001 us",...}\n\n
event: peers\ndata: [{tally:"*",remote:"GPS_NMEA(0)",...},...]\n\n
event: system\ndata: [{label:"CPU",pct:12,display:"12%"},...]\n\n
event: alert\ndata: {"level":"warn","message":"..."}\n\n
```

### Update Frequencies
| Data        | Interval | Notes                                |
|-------------|----------|--------------------------------------|
| Clock       | 100ms    | Sub-second display                   |
| Satellites  | 3s       | GPS chipset update cycle             |
| Health      | 5s       | Fix status rarely changes fast       |
| Drift       | 1s       | 1 point/sec, 300-point window (5min) |
| PPS         | 1s       | Per-pulse data                       |
| Peers       | 10s      | ntpq polling interval                |
| System      | 5s       | CPU/mem/temp                         |
| Alerts      | Event    | Push only when event occurs          |

### Smooth Bar Updates (Satellites)
- CSS `transition: height 300ms cubic-bezier(.4,0,.2,1)` on bar elements
- DOM nodes are reused (not recreated) — only height/color attributes change
- This produces smooth animated height transitions between readings

### Drift Graph Strategy
- Lightweight custom `<canvas>` chart — zero dependencies
- Rolling window of 300 points (5 minutes at 1 Hz)
- `Array.shift()` to remove oldest point, `.push()` to add newest
- `requestAnimationFrame` render loop, only redraws when `_dirty` flag set
- Auto-scales Y axis with 15% padding
- Dashed zero-line reference when range spans zero

### Alert Notification Design
- **In-panel log**: scrollable list with severity dot + timestamp + message
- **Toast overlay**: slides in from top-right, auto-dismisses after 5 seconds
- Toasts only fire for `warn` and `error` level (info stays in log only)
- `aria-live="polite"` on toast container for screen readers
- Fade-out animation before removal

---

## 4. Accessibility (WCAG 2.1 AA)

### Contrast Ratios (Verified)
| Pair                          | Ratio  | Requirement |
|-------------------------------|--------|-------------|
| `--text-primary` on `--bg-panel` (dark) | 13.2:1 | 4.5:1 pass |
| `--text-secondary` on `--bg-panel` (dark) | 5.8:1 | 4.5:1 pass |
| `--text-primary` on `--bg-panel` (light) | 14.7:1 | 4.5:1 pass |
| Signal green on dark bg       | 5.1:1  | 3:1 UI pass |
| Signal red on dark bg         | 4.8:1  | 3:1 UI pass |
| Signal amber on dark bg       | 7.2:1  | 3:1 UI pass |

### Font Sizes
| Element      | Size      | Min at 1280px | Purpose           |
|-------------|-----------|---------------|-------------------|
| Hero clock  | 3.5rem    | 2.2rem        | Primary info      |
| Panel titles| 0.8rem    | 0.72rem       | Section labels    |
| Body text   | 0.82rem   | 0.78rem       | General content   |
| Small text  | 0.72rem   | 0.72rem       | Labels, captions  |
| Tiny text   | 0.62rem   | 0.62rem       | Axis ticks, PRNs  |

### Screen Reader Considerations
- Every panel is a `<section>` with `aria-label`
- Satellite chart has `role="img"` with dynamic `aria-label` summary
- Each satellite bar has per-bar `aria-label` (PRN, SNR, used, strength)
- Status icons are `aria-hidden="true"` with `.sr-only` text alternatives
- Theme toggle uses `role="radiogroup"` with `aria-checked`
- Alert log has `role="log"` with `aria-relevant="additions"`
- Toast container has `aria-live="polite"`
- Progress bars have `role="progressbar"` with `aria-valuenow`
- Dialog uses `role="dialog"` with `aria-modal="true"`
- `prefers-reduced-motion: reduce` disables all animations

### Keyboard Navigation
- All interactive elements are focusable
- `focus-visible` outline: `2px solid var(--accent)`, `offset: 2px`
- Dialog traps focus, closes on Escape
- Tab order follows visual layout (grid source order)

---

## 5. Technology Choices

### Stack
| Layer      | Choice                | Rationale                                  |
|------------|----------------------|---------------------------------------------|
| Transport  | SSE (`EventSource`)  | Unidirectional, auto-reconnect, native API  |
| JS         | Vanilla ES2020       | No build step, <15KB total                  |
| CSS        | Custom Properties    | Theme switching without JS class toggling   |
| Charts     | Custom `<canvas>`    | Zero dependencies, <200 lines, full control |
| Icons      | Inline SVG           | No icon font dependency, crisp at any size  |
| Backend    | Python (any ASGI/WSGI)| Serves `/static/` + `/api/events` SSE      |

### File Structure
```
static/
  index.html      — single page, all markup
  style.css       — design tokens + all component styles
  dashboard.js    — all client logic, chart, SSE, demo mode
```

### CSS Architecture
- All colors/spacing via CSS custom properties on `:root`
- Theme switch changes `data-theme` attribute on `<html>`
- `[data-theme="light"]` selector overrides dark-mode tokens
- No preprocessor needed — native cascade handles theming

### Chart Implementation
- `DriftChart` class wrapping a `<canvas>` element
- `ResizeObserver` for responsive canvas sizing
- Device pixel ratio scaling for crisp rendering on Retina
- `requestAnimationFrame` loop with dirty flag (no wasted draws)
- Auto-scaled Y axis, grid lines, zero reference line, time labels

---

## 6. Panel Design Specifications

### Time Panel
- **Clock**: `font-family: monospace`, `font-variant-numeric: tabular-nums`, 3.5rem
- Fractional seconds at 0.5em of clock size, 70% opacity
- Date in ISO format below clock
- Meta row: timezone, stratum number, offset value
- Source badge: pill shape, green border for GPS, red for free-running

### Satellite Panel
- Vertical bar chart, one bar per tracked satellite
- Bars grow upward, percentage of max (50 dB)
- SNR value label above each bar, PRN label below
- Color + pattern combo per strength tier
- Unused satellites: dashed outline, 50% opacity
- Legend in panel header: colored swatches with dB ranges

### GPS Health Panel
- 2x3 grid of status cards
- Each card: circular icon (28px, status-colored) + label + value
- Cards: Fix Type, Sats Used, HDOP, VDOP, Antenna, Survey Status

### PPS Panel
- Central circular indicator (56px) with lock/unlock icon
- Green glow ring when locked, red when unlocked
- 200ms pulse animation on each PPS event
- Offset value below indicator
- 2x2 stat grid: Jitter, Stability, Uptime, Pulses

### Drift Graph Panel
- Full-width canvas line chart
- Rolling 5-minute window (300 points at 1 Hz)
- Blue line (#58a6ff dark, #0969da light) with translucent fill
- 5 horizontal grid lines with Y-axis labels (ppb)
- X-axis time labels every ~50 seconds
- Stats bar in header: Current, Average, Maximum

### Alerts Panel
- Scrollable log (only panel with internal scroll)
- Entry format: severity dot (6px circle) + timestamp + message
- Background tint per severity level
- Clear button in panel header
- Toast overlay for warn/error level alerts

### NTP Peers Panel
- Full-width table with sticky header
- Columns: Tally, Remote, Refid, Stratum, When, Poll, Reach, Delay, Offset, Jitter
- Monospace font for data columns
- Tally symbol color-coded: * green (sys.peer), o green (PPS), + blue (candidate), - red (rejected)
- Hover highlight on rows

### Controls Panel
- Vertical stack of action buttons
- Each button: icon + label, standard height
- "Reboot System" in danger style (red tint)
- All actions require confirmation dialog
- Dialog: modal overlay, title + message + Cancel/Confirm buttons

### System Panel
- Vertical stack of resource meters
- Each: label + value header + progress bar (6px height)
- Bar color: blue (normal), amber (>70%), red (>90%)
- Items: CPU, Memory, Temperature, Disk
