# NTP GPS Server v1.0.0

Production-grade GPS-disciplined NTP server built on Chrony, with real-time web dashboard, robust source selection, and comprehensive GPS data validation.

## Architecture

```
┌──────────────┐     ┌──────────┐     ┌──────────────────┐
│  u-blox U7   │────>│   gpsd   │────>│   GPS Parser     │
│  (USB GPS)   │     │          │     │  (validation,    │
│              │     │  SHM 0/1 │────>│   filtering)     │
└──────────────┘     └──────────┘     └────────┬─────────┘
                                               │
┌──────────────┐     ┌──────────┐     ┌────────v─────────┐
│ NTP Pools    │────>│  Chrony  │<───>│ Source Selection  │
│ (fallback)   │     │ (NTP     │     │ Engine            │
│              │     │  engine) │     │ (anti-flapping,   │
└──────────────┘     └──────────┘     │  holdover)        │
                                      └────────┬─────────┘
                                               │
                     ┌──────────┐     ┌────────v─────────┐
                     │ Web      │<───>│ Backend Server    │
                     │ Browser  │ WS  │ (Flask + WS)     │
                     └──────────┘     └──────────────────┘
```

### Components

| Component | Description |
|---|---|
| `ntpgps/gps/parser.py` | GPS data collection from gpsd with UBX binary filtering, multi-constellation support, and comprehensive validation |
| `ntpgps/ntp/source_manager.py` | Source selection state machine with hysteresis anti-flapping, holdover mode, and drift tracking |
| `ntpgps/ntp/chrony.py` | Chrony configuration generation, monitoring (sources/tracking parsing), and service management |
| `ntpgps/config/settings.py` | YAML configuration with validation, dotted-key access, and safe defaults |
| `ntpgps/web/app.py` | Flask web application with REST API and WebSocket real-time updates |
| `ntpgps/server.py` | Main orchestrator tying GPS, Chrony, source selection, and web interface together |

## Features

### GPS Data Handling
- Connects to gpsd for u-blox U7 (and compatible) receivers over USB
- Filters UBX binary data mixed into NMEA stream
- Handles real-world anomalies: mode=1 with valid time, nSat=26 but uSat=0, negative elevations, 0 dB signals, high PDOP
- Multi-constellation support: GPS, GLONASS, SBAS, QZSS (Galileo/BeiDou ready)
- Multi-layer time validation preventing "false good" states
- Automatic reconnection with exponential backoff

### Source Selection
- **GPS is ALWAYS preferred** when available
- State machine: STARTUP -> GPS_LOCKED -> GPS_DEGRADED -> HOLDOVER -> NETWORK
- Anti-flapping: configurable minimum hold times before state transitions
- GPS loss timeout before entering holdover (default: 15 minutes)
- Holdover with drift estimation using linear regression
- Manual override: force GPS, Network, or Auto mode
- Stratum automatically adjusted: 1 (GPS locked), 2 (degraded), 3 (holdover)

### Web Dashboard
- Single-page, no-scroll layout with CSS Grid
- Dark / Light / System theme modes
- Real-time updates via WebSocket (1 second interval)
- 8 panels: Time, GPS Health, PPS, Satellites, Drift Graph, Alerts, Controls, NTP Tracking
- Signal strength bars: green (>25 dB), amber (16-25), red (<16), with colorblind-friendly patterns
- Drift chart: GPS vs Network offset over time (Canvas-based)

### NTP Integration
- Chrony as core NTP engine with GPS (SHM 0) and PPS (SHM 1) refclocks
- GPS preferred source with `prefer` flag
- Network pool servers as fallback
- `local stratum 10 orphan` for holdover
- Automatic chrony.conf generation from configuration
- Service restart via web UI

## Requirements

- **OS**: Raspberry Pi OS (Bookworm+), Ubuntu Server 24.04 or 26.04
- **Hardware**: u-blox U7 GPS receiver (USB), or compatible gpsd-supported device
- **Software**: Python 3.10+, chrony, gpsd

## Quick Start

### Install
```bash
sudo ./install.sh
```

The installer will:
1. Detect your OS and platform (Raspberry Pi or generic)
2. Back up existing chrony/gpsd configurations
3. Install chrony, gpsd, and Python dependencies
4. Deploy the application to `/opt/ntpgps/`
5. Configure gpsd for your GPS device
6. Generate optimized chrony.conf for GPS timing
7. Create and enable the systemd service
8. Start all services

### Access Dashboard
Open `http://<your-ip>:8800` in a browser.

### Manual Run (Development)
```bash
pip install flask flask-sock pyyaml simple-websocket
python -m ntpgps.main --debug
```

### Uninstall
```bash
sudo ./uninstall.sh
```
Options: `--purge` (remove chrony/gpsd packages), `--keep-data`, `--dry-run`

## Configuration

Configuration file: `/etc/ntpgps/config.yaml` (system) or `config.yaml` (local).

Key settings:

| Setting | Default | Description |
|---|---|---|
| `server.port` | 8800 | Web interface port |
| `gps.gpsd_host` | 127.0.0.1 | gpsd host |
| `gps.min_satellites_for_valid_fix` | 4 | Minimum satellites for valid fix |
| `gps.max_pdop_for_valid_fix` | 6.0 | Maximum PDOP threshold |
| `source_selection.mode` | auto | Source mode: auto, gps, network |
| `source_selection.gps_loss_timeout_minutes` | 15 | Minutes before entering holdover |
| `source_selection.flap_hold_time_minutes` | 10 | Minimum time between state changes |
| `source_selection.holdover_max_minutes` | 120 | Maximum holdover before network fallback |
| `display.timezone` | Australia/Canberra | Display timezone |
| `display.theme` | system | Theme: dark, light, system |

All settings can be modified via the REST API (`POST /api/config`).

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web dashboard |
| `/ws` | WS | WebSocket for real-time updates |
| `/api/status` | GET | Full system status |
| `/api/config` | GET/POST | Read/write configuration |
| `/api/source/mode` | POST | Set source mode (auto/gps/network) |
| `/api/chrony/restart` | POST | Restart chrony service |
| `/api/chrony/sources` | GET | Chrony sources and tracking |
| `/api/alerts` | GET | Alert history |
| `/api/drift/history` | GET | Drift samples and statistics |
| `/api/health` | GET | Health check |

## Testing

```bash
pip install pytest pytest-cov
python -m pytest tests/ -v
```

121 tests covering:
- Configuration validation and persistence
- GPS data parsing (all edge cases: mode=1 with time, nSat/uSat mismatch, null values, UBX binary filtering, mixed constellations)
- Source selection state machine (all transitions, anti-flapping, holdover, manual override)
- Chrony output parsing (time values, tracking data)
- Web API endpoints (all routes, error handling)
- PPS status tracking

## Troubleshooting

### GPS not detected
- Check USB connection: `lsusb | grep -i u-blox`
- Check gpsd: `systemctl status gpsd` and `gpspipe -w | head`
- Verify device: `ls -la /dev/ttyACM0` or `/dev/ttyUSB0`

### No satellites in dashboard
- Ensure GPS antenna has clear sky view
- Wait for cold start (up to 26 seconds for first fix)
- Check gpsd output: `cgps` or `gpsmon`

### High offset or jitter
- USB GPS latency is typically 1-10ms; this is normal
- Ensure chrony's SHM offset is tuned for your receiver
- PPS over USB has ~1ms jitter vs ~1us for hardware PPS

### Source flapping
- Increase `source_selection.flap_hold_time_minutes`
- Increase `source_selection.gps_loss_timeout_minutes`
- Consider manual GPS mode if GPS is reliable

## Known Edge Cases

- u-blox 7 emits time with mode=1 (no fix) during acquisition; the validator correctly marks this as "usable but not trusted"
- SKY messages can report nSat=26 but uSat=0 during cold start; the system correctly treats this as insufficient for timing
- Negative elevation satellites (below horizon) are tracked but correctly flagged
- PDOP values of 99.99+ indicate no geometric solution; handled gracefully
- Mixed UBX binary in NMEA stream is filtered at the byte level

## License

MIT
