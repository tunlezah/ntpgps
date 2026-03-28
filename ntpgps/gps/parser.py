"""GPS data parser with comprehensive validation layers.

Handles real-world messy data from gpsd including:
- Satellites visible but not used (uSat=0)
- Signal strengths of 0 mixed with valid values
- Negative elevation satellites
- Fix mode=1 but time still being emitted
- Mixed GNSS constellations (GPS, SBAS, GLONASS, QZSS)
- Extremely high PDOP values
- Time present even when fix is invalid
"""

import json
import logging
import math
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Callable

logger = logging.getLogger(__name__)


class FixMode(IntEnum):
    UNKNOWN = 0
    NO_FIX = 1
    FIX_2D = 2
    FIX_3D = 3


class GNSSConstellation(IntEnum):
    GPS = 0
    SBAS = 1
    GALILEO = 2
    BEIDOU = 3
    IMES = 4
    QZSS = 5
    GLONASS = 6

    @classmethod
    def from_gnssid(cls, gnssid: int) -> "GNSSConstellation":
        try:
            return cls(gnssid)
        except ValueError:
            return cls.GPS

    @classmethod
    def from_prn(cls, prn: int) -> "GNSSConstellation":
        """Infer constellation from PRN when gnssid not available."""
        if 1 <= prn <= 32:
            return cls.GPS
        elif 33 <= prn <= 64:
            return cls.SBAS
        elif 65 <= prn <= 96:
            return cls.GLONASS
        elif 120 <= prn <= 158:
            return cls.SBAS
        elif 159 <= prn <= 163:
            return cls.SBAS
        elif 193 <= prn <= 202:
            return cls.QZSS
        elif 201 <= prn <= 237:
            return cls.BEIDOU
        elif 301 <= prn <= 336:
            return cls.GALILEO
        return cls.GPS


@dataclass
class Satellite:
    prn: int
    elevation: float
    azimuth: float
    signal_strength: float
    used: bool
    constellation: GNSSConstellation
    gnssid: int = 0
    svid: int = 0
    health: int = 0

    @property
    def signal_quality(self) -> str:
        if self.signal_strength <= 0:
            return "none"
        elif self.signal_strength < 16:
            return "weak"
        elif self.signal_strength < 26:
            return "moderate"
        else:
            return "strong"

    @property
    def is_valid_signal(self) -> bool:
        return self.signal_strength > 0

    @property
    def is_above_horizon(self) -> bool:
        return self.elevation >= 0

    def to_dict(self) -> dict:
        return {
            "prn": self.prn,
            "elevation": self.elevation,
            "azimuth": self.azimuth,
            "signal_strength": self.signal_strength,
            "used": self.used,
            "constellation": self.constellation.name,
            "gnssid": self.gnssid,
            "svid": self.svid,
            "signal_quality": self.signal_quality,
        }


@dataclass
class GPSFix:
    mode: FixMode
    time_str: str
    timestamp: float
    latitude: float
    longitude: float
    altitude: float
    speed: float
    climb: float
    ept: float  # estimated time error
    epx: float  # estimated longitude error
    epy: float  # estimated latitude error
    epv: float  # estimated vertical error

    @property
    def has_valid_time(self) -> bool:
        return bool(self.time_str) and self.timestamp > 0

    @property
    def has_position(self) -> bool:
        return self.mode >= FixMode.FIX_2D and self.latitude != 0.0 and self.longitude != 0.0

    @property
    def has_3d_fix(self) -> bool:
        return self.mode == FixMode.FIX_3D

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "mode_name": self.mode.name,
            "time": self.time_str,
            "timestamp": self.timestamp,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "speed": self.speed,
            "ept": self.ept,
            "has_valid_time": self.has_valid_time,
            "has_position": self.has_position,
            "has_3d_fix": self.has_3d_fix,
        }


@dataclass
class SkyView:
    satellites: list[Satellite] = field(default_factory=list)
    pdop: float = 99.99
    hdop: float = 99.99
    vdop: float = 99.99
    tdop: float = 99.99
    gdop: float = 99.99
    n_visible: int = 0
    n_used: int = 0

    @property
    def geometry_quality(self) -> str:
        if self.pdop <= 2.0:
            return "excellent"
        elif self.pdop <= 5.0:
            return "good"
        elif self.pdop <= 10.0:
            return "moderate"
        elif self.pdop <= 20.0:
            return "poor"
        else:
            return "unusable"

    @property
    def has_usable_geometry(self) -> bool:
        return self.pdop < 100.0 and self.n_used >= 4

    def to_dict(self) -> dict:
        return {
            "satellites": [s.to_dict() for s in self.satellites],
            "pdop": self.pdop,
            "hdop": self.hdop,
            "vdop": self.vdop,
            "tdop": self.tdop,
            "gdop": self.gdop,
            "n_visible": self.n_visible,
            "n_used": self.n_used,
            "geometry_quality": self.geometry_quality,
        }


@dataclass
class PPSStatus:
    present: bool = False
    stable: bool = False
    offset_us: float = 0.0
    jitter_us: float = 0.0
    last_seen: float = 0.0

    @property
    def age_seconds(self) -> float:
        if self.last_seen == 0:
            return float("inf")
        return time.time() - self.last_seen

    @property
    def is_fresh(self) -> bool:
        return self.age_seconds < 5.0

    def to_dict(self) -> dict:
        return {
            "present": self.present,
            "stable": self.stable,
            "offset_us": round(self.offset_us, 2),
            "jitter_us": round(self.jitter_us, 2),
            "last_seen": self.last_seen,
            "age_seconds": round(self.age_seconds, 2),
            "is_fresh": self.is_fresh,
        }


class GPSTimeValidator:
    """Multi-layer validation for GPS time trustworthiness.

    Prevents "false good" states where time is present but unreliable.
    """

    def __init__(
        self,
        min_satellites: int = 4,
        max_pdop: float = 6.0,
        min_signal_db: float = 15.0,
    ):
        self.min_satellites = min_satellites
        self.max_pdop = max_pdop
        self.min_signal_db = min_signal_db
        self._last_valid_time: float = 0.0
        self._consecutive_valid: int = 0
        self._consecutive_invalid: int = 0
        self._time_jump_threshold: float = 2.0  # seconds

    def validate(self, fix: GPSFix, sky: SkyView, pps: PPSStatus) -> dict:
        """Validate GPS time quality. Returns validation result dict."""
        checks = {
            "time_present": fix.has_valid_time,
            "fix_valid": fix.mode >= FixMode.FIX_2D,
            "sufficient_satellites": sky.n_used >= self.min_satellites,
            "geometry_acceptable": sky.pdop <= self.max_pdop,
            "signal_quality_ok": self._check_signal_quality(sky),
            "time_consistent": self._check_time_consistency(fix),
            "pps_stable": pps.stable if pps.present else True,  # pass if no PPS expected
        }

        is_valid = all(checks.values())

        if is_valid:
            self._consecutive_valid += 1
            self._consecutive_invalid = 0
            self._last_valid_time = fix.timestamp
        else:
            self._consecutive_valid = 0
            self._consecutive_invalid += 1

        # Require at least 3 consecutive valid readings for high confidence
        is_trusted = is_valid and self._consecutive_valid >= 3

        # Degraded but usable: time present and some satellites, even if not ideal
        is_usable = (
            fix.has_valid_time
            and fix.mode >= FixMode.NO_FIX
            and sky.n_used >= 1
        )

        return {
            "valid": is_valid,
            "trusted": is_trusted,
            "usable": is_usable,
            "checks": checks,
            "consecutive_valid": self._consecutive_valid,
            "consecutive_invalid": self._consecutive_invalid,
        }

    def _check_signal_quality(self, sky: SkyView) -> bool:
        """Check that used satellites have adequate signal strength."""
        used_sats = [s for s in sky.satellites if s.used and s.is_valid_signal]
        if not used_sats:
            return False
        avg_signal = sum(s.signal_strength for s in used_sats) / len(used_sats)
        return avg_signal >= self.min_signal_db

    def _check_time_consistency(self, fix: GPSFix) -> bool:
        """Check for suspicious time jumps."""
        if self._last_valid_time == 0:
            return True  # First reading, can't check
        if not fix.has_valid_time:
            return False
        elapsed_real = time.time() - self._last_valid_time
        # Allow for some tolerance (GPS updates may not be exactly on time)
        if elapsed_real < 0 or elapsed_real > 300:
            return True  # Too long ago to compare meaningfully
        time_diff = abs(fix.timestamp - self._last_valid_time)
        # Time should advance roughly in step with real time
        return abs(time_diff - elapsed_real) < self._time_jump_threshold


class GPSDataCollector:
    """Connects to gpsd and collects GPS data with robust error handling.

    Handles:
    - Connection drops and reconnection
    - Malformed JSON
    - UBX binary data in stream
    - All gpsd message types (TPV, SKY, PPS, DEVICES, VERSION)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 2947,
        on_update: Callable | None = None,
    ):
        self.host = host
        self.port = port
        self.on_update = on_update

        self.fix = GPSFix(
            mode=FixMode.UNKNOWN, time_str="", timestamp=0,
            latitude=0, longitude=0, altitude=0, speed=0, climb=0,
            ept=0, epx=0, epy=0, epv=0,
        )
        self.sky = SkyView()
        self.pps = PPSStatus()

        self._socket: socket.socket | None = None
        self._buffer = ""
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._last_data_time: float = 0
        self._reconnect_delay: float = 1.0
        self._max_reconnect_delay: float = 30.0
        self._gpsd_version: str = ""

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def data_age(self) -> float:
        if self._last_data_time == 0:
            return float("inf")
        return time.time() - self._last_data_time

    def start(self) -> None:
        """Start the GPS data collection thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="gps-collector")
        self._thread.start()
        logger.info("GPS data collector started (gpsd at %s:%d)", self.host, self.port)

    def stop(self) -> None:
        """Stop the GPS data collection thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._disconnect()
        logger.info("GPS data collector stopped")

    def get_state(self) -> dict:
        """Get current GPS state as a dict (thread-safe)."""
        with self._lock:
            return {
                "connected": self._connected,
                "data_age": round(self.data_age, 1),
                "fix": self.fix.to_dict(),
                "sky": self.sky.to_dict(),
                "pps": self.pps.to_dict(),
                "gpsd_version": self._gpsd_version,
            }

    def _run(self) -> None:
        """Main collection loop with reconnection logic."""
        while self._running:
            try:
                if not self._connected:
                    self._connect()
                self._read_data()
            except (ConnectionError, OSError, socket.error) as e:
                logger.warning("GPS connection error: %s", e)
                self._disconnect()
                self._wait_reconnect()
            except Exception:
                logger.exception("Unexpected error in GPS collector")
                self._disconnect()
                self._wait_reconnect()

    def _connect(self) -> None:
        """Connect to gpsd and send WATCH command."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(10.0)
        self._socket.connect((self.host, self.port))
        self._buffer = ""

        # Enable JSON streaming
        watch_cmd = '?WATCH={"enable":true,"json":true,"pps":true}\n'
        self._socket.sendall(watch_cmd.encode())

        self._connected = True
        self._reconnect_delay = 1.0
        logger.info("Connected to gpsd at %s:%d", self.host, self.port)

    def _disconnect(self) -> None:
        """Safely disconnect from gpsd."""
        self._connected = False
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def _wait_reconnect(self) -> None:
        """Wait before reconnecting with exponential backoff."""
        if not self._running:
            return
        logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
        time.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    def _read_data(self) -> None:
        """Read and parse data from gpsd socket."""
        if not self._socket:
            raise ConnectionError("Not connected")

        self._socket.settimeout(5.0)
        try:
            chunk = self._socket.recv(4096)
        except socket.timeout:
            return  # No data, but connection is still alive

        if not chunk:
            raise ConnectionError("Connection closed by gpsd")

        # Filter out UBX binary data (starts with 0xB5 0x62)
        text = self._filter_binary(chunk)
        self._buffer += text

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            self._parse_line(line)

        # Prevent buffer from growing unbounded
        if len(self._buffer) > 65536:
            logger.warning("GPS buffer overflow, clearing")
            self._buffer = ""

    @staticmethod
    def _filter_binary(data: bytes) -> str:
        """Filter out UBX binary data from the stream.

        UBX messages start with 0xB5 0x62. We strip any non-ASCII bytes
        to handle mixed binary/text streams from u-blox devices.
        """
        result = []
        i = 0
        raw = data
        while i < len(raw):
            byte = raw[i]
            # UBX sync bytes - skip entire UBX message
            if byte == 0xB5 and i + 1 < len(raw) and raw[i + 1] == 0x62:
                if i + 5 < len(raw):
                    # UBX length is in bytes 4-5 (little-endian)
                    payload_len = raw[i + 4] | (raw[i + 5] << 8)
                    # Skip header(6) + payload + checksum(2)
                    skip = 6 + payload_len + 2
                    i += max(skip, 1)
                    continue
                else:
                    # Incomplete UBX header, skip to end
                    break
            # Keep printable ASCII and common whitespace
            if 32 <= byte <= 126 or byte in (9, 10, 13):
                result.append(chr(byte))
            i += 1
        return "".join(result)

    def _parse_line(self, line: str) -> None:
        """Parse a single JSON line from gpsd."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON line: %.80s", line)
            return

        msg_class = msg.get("class", "")
        with self._lock:
            if msg_class == "TPV":
                self._parse_tpv(msg)
            elif msg_class == "SKY":
                self._parse_sky(msg)
            elif msg_class == "PPS":
                self._parse_pps(msg)
            elif msg_class == "VERSION":
                self._gpsd_version = msg.get("release", "unknown")
                logger.info("gpsd version: %s", self._gpsd_version)
            elif msg_class == "DEVICES":
                devices = msg.get("devices", [])
                logger.info("gpsd devices: %s", [d.get("path", "?") for d in devices])

        self._last_data_time = time.time()

        if self.on_update and msg_class in ("TPV", "SKY", "PPS"):
            try:
                self.on_update(msg_class)
            except Exception:
                logger.exception("Error in GPS update callback")

    def _parse_tpv(self, msg: dict) -> None:
        """Parse TPV (Time-Position-Velocity) message."""
        mode_raw = msg.get("mode", 0)
        try:
            mode = FixMode(mode_raw)
        except ValueError:
            mode = FixMode.UNKNOWN

        time_str = msg.get("time", "")
        timestamp = 0.0
        if time_str:
            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                timestamp = dt.timestamp()
            except (ValueError, TypeError):
                time_str = ""
                timestamp = 0.0

        self.fix = GPSFix(
            mode=mode,
            time_str=time_str,
            timestamp=timestamp,
            latitude=msg.get("lat", 0.0),
            longitude=msg.get("lon", 0.0),
            altitude=msg.get("altHAE", msg.get("alt", 0.0)),
            speed=msg.get("speed", 0.0),
            climb=msg.get("climb", 0.0),
            ept=msg.get("ept", 0.0),
            epx=msg.get("epx", 0.0),
            epy=msg.get("epy", 0.0),
            epv=msg.get("epv", 0.0),
        )

    def _parse_sky(self, msg: dict) -> None:
        """Parse SKY message with robust satellite handling."""
        satellites = []
        for sat_data in msg.get("satellites", []):
            prn = sat_data.get("PRN", 0)
            gnssid = sat_data.get("gnssid", -1)

            if gnssid >= 0:
                constellation = GNSSConstellation.from_gnssid(gnssid)
            else:
                constellation = GNSSConstellation.from_prn(prn)

            el = sat_data.get("el", 0.0)
            az = sat_data.get("az", 0.0)
            ss = sat_data.get("ss", 0.0)
            used = sat_data.get("used", False)

            # Handle null/None values
            if el is None:
                el = 0.0
            if az is None:
                az = 0.0
            if ss is None:
                ss = 0.0

            satellites.append(Satellite(
                prn=prn,
                elevation=float(el),
                azimuth=float(az),
                signal_strength=float(ss),
                used=bool(used),
                constellation=constellation,
                gnssid=gnssid if gnssid >= 0 else constellation.value,
                svid=sat_data.get("svid", prn),
                health=sat_data.get("health", 0),
            ))

        n_visible = len(satellites)
        n_used = sum(1 for s in satellites if s.used)

        # Override with gpsd-reported counts if available and higher
        reported_n_sat = msg.get("nSat", n_visible)
        reported_u_sat = msg.get("uSat", n_used)
        # Trust the satellite array over reported counts for used
        # but use reported nSat if higher (some sats may not be in array)
        n_visible = max(n_visible, reported_n_sat if isinstance(reported_n_sat, int) else n_visible)

        self.sky = SkyView(
            satellites=satellites,
            pdop=self._safe_dop(msg.get("pdop")),
            hdop=self._safe_dop(msg.get("hdop")),
            vdop=self._safe_dop(msg.get("vdop")),
            tdop=self._safe_dop(msg.get("tdop")),
            gdop=self._safe_dop(msg.get("gdop")),
            n_visible=n_visible,
            n_used=n_used,
        )

    @staticmethod
    def _safe_dop(value) -> float:
        """Safely parse DOP value, returning 99.99 for invalid."""
        if value is None:
            return 99.99
        try:
            v = float(value)
            if math.isnan(v) or math.isinf(v) or v <= 0:
                return 99.99
            return v
        except (TypeError, ValueError):
            return 99.99

    def _parse_pps(self, msg: dict) -> None:
        """Parse PPS timing message."""
        real_sec = msg.get("real_sec", 0)
        real_nsec = msg.get("real_nsec", 0)
        clock_sec = msg.get("clock_sec", 0)
        clock_nsec = msg.get("clock_nsec", 0)
        precision = msg.get("precision", -1)

        if real_sec > 0:
            offset_ns = (real_sec - clock_sec) * 1_000_000_000 + (real_nsec - clock_nsec)
            offset_us = offset_ns / 1000.0

            # Running jitter estimate (exponential moving average)
            if self.pps.present:
                alpha = 0.3
                new_jitter = abs(offset_us - self.pps.offset_us)
                self.pps.jitter_us = alpha * new_jitter + (1 - alpha) * self.pps.jitter_us
            else:
                self.pps.jitter_us = 0.0

            self.pps.present = True
            self.pps.offset_us = offset_us
            self.pps.last_seen = time.time()
            self.pps.stable = self.pps.jitter_us < 500  # < 500us jitter = stable
