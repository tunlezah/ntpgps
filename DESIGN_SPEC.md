# GPS-Disciplined NTP Server Dashboard -- Design Specification

## 1. Layout Design

### Grid Structure (No-Scroll, Viewport-Locked)

Desktop (>=1280px): 4-column x 3-row CSS Grid, `100vh` locked, `overflow: hidden`.

```
+-------------------+-------------------+-------------------+-------------------+
|      TIME         |   GPS HEALTH      |       PPS         |   NTP TRACKING    |
|  (hero clock,     | (status cards,    | (present/stable,  | (ref, offset,     |
|   source badge)   |  fix, PDOP, sats) |  offset, jitter)  |  freq, skew)      |
+-------------------+-------------------+-------------------+-------------------+
|              SATELLITES (3 cols span)                      |     ALERTS        |
|  (SNR bar chart with colorblind patterns + legend)        | (scrollable log)  |
+-------------------+-------------------+-------------------+-------------------+
|          DRIFT GRAPH (2 cols span)    |       CONTROLS (2 cols span)          |
|  (GPS vs Network time-series canvas)  | (source mode + restart NTP)           |
+-------------------+-------------------+-------------------+-------------------+
```

```css
grid-template-areas:
    "time    health   pps     tracking"
    "sats    sats     sats    alerts"
    "drift   drift    ctrl    ctrl";
```

### Priority (determines grid placement)
1. Time display -- top-left, hero treatment, always visible
2. Satellite status -- full row span for bar chart width
3. GPS health -- top row, quick glance
4. Drift graph -- bottom, 2-col span for chart readability
5. PPS -- top row, compact indicators
6. Alerts -- right side, scrollable within panel
7. Controls -- bottom-right, least accessed

### Tablet (<= 1024px): 2 columns, vertical scroll allowed
```css
grid-template-areas:
    "time     time"
    "health   pps"
    "sats     sats"
    "tracking alerts"
    "drift    drift"
    "ctrl     ctrl";
```

### Sizing Rules
- Fixed header: 38px, dashboard starts below
- `gap: 6px` between panels, `padding: 8px 10px` per panel
- `overflow: hidden` on body (desktop); `overflow-y: auto` on tablet
- Each panel has `overflow: hidden` (self-contained)
- Alert log is the only element with internal `overflow-y: auto`

---

## 2. Color System

### Signal Strength (SNR) -- Colorblind-Safe

| Range     | Color   | Dark Hex    | Light Hex   | Pattern Overlay                        |
|-----------|---------|-------------|-------------|----------------------------------------|
| 0-15 dB   | Red     | `#d42a2a`   | `#cf222e`   | Horizontal stripes (3px bars)          |
| 16-25 dB  | Amber   | `#d4a017`   | `#9a6700`   | 45-degree diagonal hatching            |
| 26+ dB    | Green   | `#2e9e41`   | `#1a7f37`   | Solid fill (no pattern = "good")       |
| Unused    | Gray    | N/A         | N/A         | Dashed outline, 40% opacity            |

Patterns are applied via CSS `::after` pseudo-elements with `repeating-linear-gradient`.
Legend swatches mirror the same patterns for consistency.

### Status Indicators

| State   | Dark Mode   | Light Mode  | Icon Prefix (CSS ::before)   |
|---------|-------------|-------------|------------------------------|
| Healthy | `#2e9e41`   | `#1a7f37`   | Checkmark character           |
| Warning | `#d4a017`   | `#9a6700`   | Warning triangle character    |
| Error   | `#d42a2a`   | `#cf222e`   | X mark character              |

### Dark Mode Palette

| Token            | Value       | Purpose                    |
|------------------|-------------|----------------------------|
| `--bg`           | `#0d1117`   | Page background            |
| `--bg-panel`     | `#161b22`   | Panel background           |
| `--bg-panel-alt` | `#1c2230`   | Card alternate bg          |
| `--border`       | `#30363d`   | Panel borders              |
| `--text`         | `#e6edf3`   | Primary text               |
| `--text-muted`   | `#8b949e`   | Labels, captions           |
| `--text-bright`  | `#ffffff`   | Emphasis text              |
| `--accent`       | `#6366f1`   | Links, active states       |

### Light Mode Palette

| Token            | Value       | Purpose                    |
|------------------|-------------|----------------------------|
| `--bg`           | `#f0f2f5`   | Page background            |
| `--bg-panel`     | `#ffffff`   | Panel background           |
| `--bg-panel-alt` | `#f6f8fa`   | Card alternate bg          |
| `--border`       | `#d0d7de`   | Panel borders              |
| `--text`         | `#1f2328`   | Primary text               |
| `--text-muted`   | `#656d76`   | Labels, captions           |
| `--text-bright`  | `#000000`   | Emphasis text              |
| `--accent`       | `#4f46e5`   | Links, active states       |

### System Theme Mode
Uses `@media (prefers-color-scheme: light)` to override `[data-theme="system"]` tokens.
JS theme toggle stores preference in `localStorage` as `"ntpgps-theme"`.

---

## 3. Real-Time Update Strategy

### Transport: WebSocket (matching existing Flask-Sock backend)

The backend (`flask_sock`) sends a unified JSON status object on each 1-second monitor tick.
The client receives via `ws.onmessage`, parses, and dispatches to individual update functions.

**Why the existing WebSocket approach works well:**
- Backend already broadcasts full status every second
- Single message contains all panel data (atomic updates)
- flask-sock handles connection lifecycle
- Client-side ping/pong keep-alive every 15 seconds
- Automatic reconnect with exponential backoff (1s to 30s)

### Update Frequencies (server-side, all in single WS message)
| Data        | Effective Rate | Notes                                |
|-------------|---------------|--------------------------------------|
| Clock       | 1s (WS) + 1s (local) | Local JS clock ticks independently |
| Satellites  | 1s            | From GPS chipset via gpsd            |
| Health      | 1s            | Validation checks                    |
| Drift       | 1s            | Appended to 300-point rolling window |
| PPS         | 1s            | Per-pulse data from gpsd             |
| Tracking    | 1s            | Chrony tracking info                 |
| Alerts      | Event-driven  | Accumulated in source engine         |

### Smooth Satellite Bar Updates
- CSS `transition: height 300ms cubic-bezier(.4,0,.2,1)` on bar elements
- DOM nodes are **reused** (not recreated): only height, class, and text change
- This produces smooth animated height transitions between readings
- Eliminates layout thrashing from `innerHTML` rebuilds

### Drift Graph Strategy
- Custom `<canvas>` chart -- zero external dependencies, ~120 lines
- Rolling window: 300 points (5 minutes at 1 Hz)
- `Array.shift()` removes oldest, `.push()` adds newest
- `requestAnimationFrame` render loop with `_driftDirty` flag (no wasted draws)
- Auto-scales Y axis with 10% padding
- Dashed zero-line reference when range spans zero
- Device pixel ratio scaling for crisp Retina rendering
- Resolves CSS custom properties at draw time (theme-aware)

### Alert Notifications
- **In-panel log**: scrollable list with left-border severity color + timestamp
- **Toast overlay**: slides in from top-right, auto-dismisses after 5 seconds
- Toasts only fire for `critical` and `warning` level (not info)
- PPS state transitions also trigger toasts
- `aria-live="polite"` on toast container for screen readers

---

## 4. Accessibility (WCAG 2.1 AA)

### Contrast Ratios
| Pair                                        | Ratio  | Requirement |
|---------------------------------------------|--------|-------------|
| `--text` (#e6edf3) on `--bg-panel` (#161b22) (dark) | 13.2:1 | 4.5:1 pass |
| `--text-muted` (#8b949e) on `--bg-panel` (dark)      | 5.8:1  | 4.5:1 pass |
| `--text` (#1f2328) on `--bg-panel` (#ffffff) (light)  | 14.7:1 | 4.5:1 pass |
| Signal green on dark bg                     | 5.1:1  | 3:1 UI pass |
| Signal red on dark bg                       | 4.8:1  | 3:1 UI pass |
| Signal amber on dark bg                     | 7.2:1  | 3:1 UI pass |

### Font Sizes
| Element      | Default    | At 1024px  | Purpose           |
|-------------|------------|------------|-------------------|
| Hero clock  | 2.4rem     | 1.8rem     | Primary time      |
| Local time  | 1.4rem     | 1.0rem     | Secondary time    |
| Panel titles| 0.68rem    | 0.68rem    | Section labels    |
| Body text   | 0.78rem    | 0.78rem    | General content   |
| Small/labels| 0.68rem    | 0.68rem    | Labels, captions  |
| Tiny        | 0.6rem     | 0.6rem     | Axis ticks, PRNs  |

### Screen Reader Support
- Every panel is a `<section>` with `aria-label`
- Satellite chart has `role="img"` with dynamic `aria-label` summary
- Each satellite bar has per-bar `aria-label` (PRN, SNR, used, strength tier)
- Status indicators use CSS `::before` pseudo-element icons (checkmark/warning/X)
- `.sr-only` text provides screen-reader-accessible status descriptions
- Theme toggle uses `role="radiogroup"` with `aria-checked`
- Alert log has `role="log"` with `aria-relevant="additions"`
- Toast container has `aria-live="polite"`, each toast has `role="alert"`
- Dialog uses `role="dialog"` with `aria-modal="true"`, focus management
- `prefers-reduced-motion: reduce` disables all animations/transitions

### Keyboard Navigation
- All buttons and interactive elements are standard `<button>` elements
- `:focus-visible` outline: `2px solid var(--accent)`, `offset: 2px`
- Confirmation dialog: focus trapped, Escape to close, click outside to close
- Tab order follows visual layout (grid source order matches DOM order)

---

## 5. Technology Stack

| Layer      | Choice                | Rationale                                  |
|------------|----------------------|---------------------------------------------|
| Transport  | WebSocket (flask-sock)| Matches existing backend, bidirectional     |
| JS         | Vanilla ES2020 IIFE  | No build step, self-contained, ~500 lines   |
| CSS        | Custom Properties    | Theme switching via `data-theme` attribute   |
| Charts     | Custom `<canvas>`    | Zero dependencies, ~120 lines, full control  |
| Icons      | CSS `::before` chars | No icon font dependency                      |
| HTML       | Jinja2 template      | `{{ version }}` from Flask                   |
| Backend    | Flask + flask-sock   | Existing Python stack                        |

### File Structure
```
ntpgps/web/
  templates/
    index.html          -- Jinja2 template, all markup
  static/
    css/style.css       -- Design tokens + all component styles (~500 lines)
    js/app.js           -- Client logic, chart, WebSocket (~500 lines)
```

---

## 6. Panel Specifications

### Time Panel
- Hero clock: monospace, `tabular-nums`, 2.4rem, bright white
- Local time: accent color, 1.4rem
- DST badge: pill with accent tint
- Source badge: colored pill (green=GPS, amber=degraded, red=free-running)
- Meta row: 3-column grid (Source, Stratum, State)
- Stratum number colored: green (1), amber (2-3), red (4+)

### GPS Health Panel
- 2x3 grid of status items
- Each: label + value/indicator pair
- Indicators use `::before` pseudo-element icons for colorblind safety
- Items: Fix Type, Satellites (used/visible), PDOP, GPS Sanity, Time Valid, Geometry

### PPS Panel
- 2x2 grid: Present, Stability, Offset, Jitter
- PPS Present indicator pulses (box-shadow animation) on each update
- State transitions (acquired/lost) trigger toast notifications

### NTP Tracking Panel
- Monospace readout: Ref, Offset, Frequency, Skew
- Background-tinted card for visual separation

### Satellite Panel (3-column span)
- Horizontal bar chart, bars grow upward
- Percentage of 50 dB max
- Color + pattern per strength tier (green=solid, amber=diagonal, red=horizontal)
- Unused satellites: dashed outline, 40% opacity
- SNR value above each bar, PRN label below
- Legend in footer: colored swatches matching bar patterns
- DOM nodes reused for smooth CSS height transitions

### Drift Graph Panel (2-column span)
- Canvas line chart: GPS offset (accent) + Network offset (amber)
- Rolling 300-point window, auto-scaled Y axis
- Grid lines, Y-axis ms labels, X-axis time labels
- Inline legend (GPS/Network color swatches)
- Stats below: Rate (ppm), Sample count

### Alerts Panel
- Scrollable log with left-border severity colors
- Info: blue, Warning: amber, Critical: red
- Background tint per severity
- Alert count badge in panel title
- Toast overlay for critical/warning level

### Controls Panel (2-column span)
- Source mode: 3-button toggle group (Auto/GPS/Network)
- Restart NTP: full-width action button
- Confirmation dialog: modal overlay with Cancel/Confirm
- Dialog: focus management, Escape to close, backdrop click to close
