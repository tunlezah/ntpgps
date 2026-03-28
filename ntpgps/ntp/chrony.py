"""Chrony management and monitoring layer.

Interfaces with chronyc to:
- Monitor source status, tracking data, and statistics
- Generate chrony.conf for GPS+PPS+network configuration
- Parse chronyc output programmatically
- Manage chrony service (restart, reload)
"""

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ChronySource:
    mode: str  # ^ = server, # = local refclock, = = peer
    state: str  # * = synced, + = combined, - = not combined, x = falseticker, ? = unreachable, ~ = too variable
    name: str
    stratum: int
    poll: int
    reach: int  # octal
    last_rx: str
    offset: float  # seconds
    error: float  # seconds

    @property
    def is_selected(self) -> bool:
        return self.state == "*"

    @property
    def is_reachable(self) -> bool:
        return self.reach > 0

    @property
    def reach_percent(self) -> float:
        # reach is octal register of last 8 polls
        binary = bin(self.reach)[2:]
        return (binary.count("1") / max(len(binary), 1)) * 100

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "state": self.state,
            "name": self.name,
            "stratum": self.stratum,
            "poll": self.poll,
            "reach": self.reach,
            "reach_percent": round(self.reach_percent, 0),
            "last_rx": self.last_rx,
            "offset_ms": round(self.offset * 1000, 3),
            "error_ms": round(self.error * 1000, 3),
            "is_selected": self.is_selected,
            "is_reachable": self.is_reachable,
        }


@dataclass
class ChronyTracking:
    ref_id: str = ""
    ref_name: str = ""
    stratum: int = 0
    ref_time: str = ""
    system_time_offset: float = 0.0
    last_offset: float = 0.0
    rms_offset: float = 0.0
    frequency: float = 0.0  # ppm
    residual_freq: float = 0.0  # ppm
    skew: float = 0.0  # ppm
    root_delay: float = 0.0
    root_dispersion: float = 0.0
    update_interval: float = 0.0
    leap_status: str = "Normal"

    def to_dict(self) -> dict:
        return {
            "ref_id": self.ref_id,
            "ref_name": self.ref_name,
            "stratum": self.stratum,
            "ref_time": self.ref_time,
            "system_time_offset_us": round(self.system_time_offset * 1e6, 2),
            "last_offset_us": round(self.last_offset * 1e6, 2),
            "rms_offset_us": round(self.rms_offset * 1e6, 2),
            "frequency_ppm": round(self.frequency, 3),
            "residual_freq_ppm": round(self.residual_freq, 3),
            "skew_ppm": round(self.skew, 3),
            "root_delay_ms": round(self.root_delay * 1000, 3),
            "root_dispersion_ms": round(self.root_dispersion * 1000, 3),
            "update_interval": round(self.update_interval, 1),
            "leap_status": self.leap_status,
        }


class ChronyManager:
    """Manages Chrony NTP daemon configuration and monitoring."""

    def __init__(
        self,
        config_path: str = "/etc/chrony/chrony.conf",
        chronyc_path: str = "chronyc",
    ):
        self.config_path = Path(config_path)
        self.chronyc = chronyc_path
        self._sources: list[ChronySource] = []
        self._tracking = ChronyTracking()
        self._last_poll: float = 0
        self._poll_interval: float = 2.0

    def poll(self) -> None:
        """Poll chrony for current status."""
        now = time.time()
        if now - self._last_poll < self._poll_interval:
            return
        self._last_poll = now

        self._sources = self._parse_sources()
        self._tracking = self._parse_tracking()

    def get_sources(self) -> list[dict]:
        return [s.to_dict() for s in self._sources]

    def get_tracking(self) -> dict:
        return self._tracking.to_dict()

    def get_selected_source(self) -> ChronySource | None:
        for s in self._sources:
            if s.is_selected:
                return s
        return None

    def get_gps_offset_ms(self) -> float | None:
        """Get GPS source offset in milliseconds."""
        for s in self._sources:
            if s.name in ("GPS", "PPS", "NMEA", "SHM0", "SHM1"):
                return s.offset * 1000
        return None

    def get_network_offset_ms(self) -> float | None:
        """Get best network source offset in milliseconds."""
        network_sources = [s for s in self._sources if s.mode == "^" and s.is_reachable]
        if not network_sources:
            return None
        # Return offset of best (lowest stratum, then lowest offset)
        best = min(network_sources, key=lambda s: (s.stratum, abs(s.offset)))
        return best.offset * 1000

    def has_network_sources(self) -> bool:
        return any(s.mode == "^" and s.is_reachable for s in self._sources)

    def _run_chronyc(self, *args: str) -> str:
        """Run a chronyc command and return stdout."""
        cmd = [self.chronyc, "-n"] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.debug("chronyc %s failed: %s", args, result.stderr.strip())
                return ""
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("chronyc error: %s", e)
            return ""

    def _parse_sources(self) -> list[ChronySource]:
        """Parse 'chronyc sources' output."""
        output = self._run_chronyc("sources")
        if not output:
            return []

        sources = []
        # chronyc sources output format:
        # MS Name/IP address         Stratum Poll Reach LastRx Last sample
        # ===============================================================================
        # #* GPS                           0   4   377     2   +250ns[ +450ns] +/-  125ns
        # ^- 0.au.pool.ntp.org             2   6   377    38   -2345us[-2345us] +/- 34ms

        for line in output.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("=") or line.startswith("MS ") or line.startswith("210"):
                continue

            if len(line) < 2:
                continue

            mode_char = line[0]
            state_char = line[1]

            if mode_char not in "^#=":
                continue

            # Parse the rest with regex
            # After mode+state: Name  Stratum  Poll  Reach  LastRx  Last sample
            match = re.match(
                r'^[#^=][*+\-x?~\s]\s+'
                r'(\S+)\s+'          # name
                r'(\d+)\s+'          # stratum
                r'(\d+)\s+'          # poll
                r'([0-7]+)\s+'       # reach (octal)
                r'(\S+)\s+'          # last rx
                r'([^\[]+)'          # offset part
                r'.*?\+/-\s*'        # +/-
                r'(.+)$',            # error
                line,
            )
            if not match:
                continue

            name = match.group(1)
            stratum = int(match.group(2))
            poll = int(match.group(3))
            reach = int(match.group(4), 8)  # octal
            last_rx = match.group(5)
            offset_str = match.group(6).strip()
            error_str = match.group(7).strip()

            offset = self._parse_time_value(offset_str)
            error = self._parse_time_value(error_str)

            sources.append(ChronySource(
                mode=mode_char,
                state=state_char,
                name=name,
                stratum=stratum,
                poll=poll,
                reach=reach,
                last_rx=last_rx,
                offset=offset,
                error=error,
            ))

        return sources

    def _parse_tracking(self) -> ChronyTracking:
        """Parse 'chronyc tracking' output."""
        output = self._run_chronyc("tracking")
        if not output:
            return ChronyTracking()

        tracking = ChronyTracking()
        for line in output.strip().split("\n"):
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if key == "Reference ID":
                # "47505300 (GPS)" -> ref_id and ref_name
                parts = value.split("(")
                tracking.ref_id = parts[0].strip()
                if len(parts) > 1:
                    tracking.ref_name = parts[1].rstrip(")")
            elif key == "Stratum":
                tracking.stratum = int(value)
            elif key == "Ref time (UTC)":
                tracking.ref_time = value
            elif key == "System time":
                tracking.system_time_offset = self._parse_tracking_value(value)
            elif key == "Last offset":
                tracking.last_offset = self._parse_tracking_value(value)
            elif key == "RMS offset":
                tracking.rms_offset = self._parse_tracking_value(value)
            elif key == "Frequency":
                # "1.234 ppm slow"
                match = re.match(r'([\d.]+)\s+ppm\s+(\w+)', value)
                if match:
                    freq = float(match.group(1))
                    if match.group(2) == "slow":
                        freq = -freq
                    tracking.frequency = freq
            elif key == "Residual freq":
                match = re.match(r'([+-]?[\d.]+)\s+ppm', value)
                if match:
                    tracking.residual_freq = float(match.group(1))
            elif key == "Skew":
                match = re.match(r'([\d.]+)\s+ppm', value)
                if match:
                    tracking.skew = float(match.group(1))
            elif key == "Root delay":
                tracking.root_delay = self._parse_tracking_value(value)
            elif key == "Root dispersion":
                tracking.root_dispersion = self._parse_tracking_value(value)
            elif key == "Update interval":
                match = re.match(r'([\d.]+)', value)
                if match:
                    tracking.update_interval = float(match.group(1))
            elif key == "Leap status":
                tracking.leap_status = value

        return tracking

    @staticmethod
    def _parse_time_value(s: str) -> float:
        """Parse chrony time value like '+250ns', '-2345us', '+1.2ms', '34ms'."""
        s = s.strip().rstrip("[]").strip()
        # Extract the last numeric+unit token
        match = re.search(r'([+-]?\d+\.?\d*)\s*(ns|us|ms|s)\b', s)
        if not match:
            return 0.0
        value = float(match.group(1))
        unit = match.group(2)
        multipliers = {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}
        return value * multipliers.get(unit, 1.0)

    @staticmethod
    def _parse_tracking_value(s: str) -> float:
        """Parse tracking output value like '0.000001234 seconds fast'."""
        match = re.match(r'([\d.]+)\s+seconds?\s*(\w*)', s)
        if match:
            val = float(match.group(1))
            direction = match.group(2)
            if direction == "slow":
                val = -val
            return val
        return 0.0

    def generate_config(
        self,
        gps_shm_unit: int = 0,
        pps_shm_unit: int = 1,
        gps_offset: float = 0.0,
        gps_delay: float = 0.2,
        gps_precision: float = 1e-1,
        pps_precision: float = 1e-7,
        network_servers: list[str] | None = None,
        local_stratum: int = 10,
    ) -> str:
        """Generate chrony.conf content for GPS-disciplined NTP server."""
        if network_servers is None:
            network_servers = [
                "0.au.pool.ntp.org",
                "1.au.pool.ntp.org",
                "2.au.pool.ntp.org",
                "3.au.pool.ntp.org",
            ]

        lines = [
            "# Chrony configuration for GPS-disciplined NTP server",
            "# Generated by NTP GPS Server v1.0.0",
            f"# Generated at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            "",
            "# GPS NMEA time via shared-memory from gpsd (primary source)",
            f"# SHM {gps_shm_unit} = GPS time (~10ms accuracy over USB)",
            f"# prefer: GPS is always the preferred time source",
            f"refclock SHM {gps_shm_unit} refid GPS precision {gps_precision:.0e} "
            f"offset {gps_offset} delay {gps_delay} poll 4 filter 64 prefer",
            "",
            "# PPS timing via shared-memory from gpsd (optional on USB)",
            f"# SHM {pps_shm_unit} = PPS signal (only if gpsd receives PPS)",
            f"# noselect: tracked for monitoring, not used alone",
            f"refclock SHM {pps_shm_unit} refid PPS precision {pps_precision:.0e} "
            f"poll 4 filter 64 trust noselect",
            "",
            "# Network NTP servers as fallback",
        ]

        for server in network_servers:
            lines.append(f"server {server} iburst maxpoll 10 minpoll 6")

        lines.extend([
            "",
            "# Source selection tuning",
            "minsources 2",
            "combinelimit 3",
            "",
            "# Allow large initial offset correction",
            "makestep 1.0 3",
            "",
            "# Enable RTC sync (useful for Raspberry Pi)",
            "rtcsync",
            "",
            "# Local clock as last resort (holdover)",
            f"local stratum {local_stratum} orphan",
            "",
            "# Drift file",
            "driftfile /var/lib/chrony/chrony.drift",
            "",
            "# Log files",
            "logdir /var/log/chrony",
            "log tracking measurements statistics refclocks",
            "",
            "# Allow NTP clients on local network",
            "allow all",
            "",
            "# Serve time to NTP clients",
            "port 123",
            "",
            "# Larger distance tolerance for GPS over USB",
            "maxdistance 16.0",
            "",
            "# Key file for chronyc authentication",
            "keyfile /etc/chrony/chrony.keys",
            "",
            "# NTS trusted certificates (if available)",
            "ntstrustedcerts /etc/ssl/certs",
        ])

        return "\n".join(lines) + "\n"

    def write_config(self, content: str) -> None:
        """Write chrony configuration file with backup."""
        if self.config_path.exists():
            backup = self.config_path.with_suffix(".conf.bak")
            import shutil
            shutil.copy2(self.config_path, backup)
            logger.info("Backed up existing config to %s", backup)

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(content)
        logger.info("Wrote chrony config to %s", self.config_path)

    def restart_service(self) -> tuple[bool, str]:
        """Restart chrony service."""
        try:
            result = subprocess.run(
                ["systemctl", "restart", "chrony"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info("Chrony service restarted successfully")
                return True, "Chrony restarted successfully"
            msg = f"Failed to restart chrony: {result.stderr.strip()}"
            logger.error(msg)
            return False, msg
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            msg = f"Error restarting chrony: {e}"
            logger.error(msg)
            return False, msg

    def reload_sources(self) -> tuple[bool, str]:
        """Tell chrony to reload sources."""
        output = self._run_chronyc("reload", "sources")
        if output:
            return True, "Sources reloaded"
        return False, "Failed to reload sources"
