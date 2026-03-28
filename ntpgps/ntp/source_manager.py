"""Source selection engine with anti-flapping hysteresis.

Implements a state machine for time source management:
- GPS is ALWAYS preferred when available
- Degraded GPS keeps GPS as source but adjusts stratum
- Network fallback only after sustained GPS loss
- Holdover mode with drift estimation when all sources lost
- No flapping between sources (hysteresis with minimum hold times)
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SourceState(Enum):
    GPS_LOCKED = "gps_locked"          # GPS valid, stratum 1
    GPS_DEGRADED = "gps_degraded"      # GPS present but poor quality, stratum 2
    HOLDOVER = "holdover"              # GPS lost, using drift estimate
    NETWORK = "network"                # Using network NTP sources
    STARTUP = "startup"                # Initial state, no source yet
    MANUAL_GPS = "manual_gps"          # User forced GPS mode
    MANUAL_NETWORK = "manual_network"  # User forced network mode


class SourceMode(Enum):
    AUTO = "auto"
    GPS = "gps"
    NETWORK = "network"


@dataclass
class DriftSample:
    timestamp: float
    gps_offset_ms: float
    network_offset_ms: float


@dataclass
class SourceStatus:
    state: SourceState = SourceState.STARTUP
    stratum: int = 16  # Maximum (unreachable) stratum at startup
    active_source: str = "none"
    gps_available: bool = False
    gps_trusted: bool = False
    network_available: bool = False
    holdover_elapsed_minutes: float = 0.0
    last_gps_lock_time: float = 0.0
    last_state_change_time: float = 0.0
    transition_reason: str = "startup"
    mode: SourceMode = SourceMode.AUTO

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "stratum": self.stratum,
            "active_source": self.active_source,
            "gps_available": self.gps_available,
            "gps_trusted": self.gps_trusted,
            "network_available": self.network_available,
            "holdover_elapsed_minutes": round(self.holdover_elapsed_minutes, 1),
            "last_gps_lock_time": self.last_gps_lock_time,
            "last_state_change_time": self.last_state_change_time,
            "transition_reason": self.transition_reason,
            "mode": self.mode.value,
        }


class DriftTracker:
    """Tracks drift between GPS and network sources."""

    def __init__(self, max_samples: int = 86400):
        self._samples: list[DriftSample] = []
        self._max_samples = max_samples
        self._estimated_drift_rate: float = 0.0  # ms per second

    def add_sample(self, gps_offset_ms: float, network_offset_ms: float) -> None:
        now = time.time()
        self._samples.append(DriftSample(
            timestamp=now,
            gps_offset_ms=gps_offset_ms,
            network_offset_ms=network_offset_ms,
        ))
        if len(self._samples) > self._max_samples:
            self._samples = self._samples[-self._max_samples:]
        self._update_drift_rate()

    def _update_drift_rate(self) -> None:
        """Estimate drift rate using linear regression on recent samples."""
        if len(self._samples) < 10:
            return
        recent = self._samples[-300:]  # Last 300 samples (~5 minutes at 1Hz)
        if len(recent) < 10:
            return

        n = len(recent)
        t0 = recent[0].timestamp
        sum_x = sum(s.timestamp - t0 for s in recent)
        sum_y = sum(s.gps_offset_ms for s in recent)
        sum_xy = sum((s.timestamp - t0) * s.gps_offset_ms for s in recent)
        sum_xx = sum((s.timestamp - t0) ** 2 for s in recent)

        denom = n * sum_xx - sum_x * sum_x
        if abs(denom) < 1e-10:
            return
        self._estimated_drift_rate = (n * sum_xy - sum_x * sum_y) / denom

    @property
    def drift_rate_ms_per_sec(self) -> float:
        return self._estimated_drift_rate

    @property
    def drift_rate_ppm(self) -> float:
        return self._estimated_drift_rate * 1000.0  # ms/s -> us/s = ppm

    def estimate_offset_after(self, seconds: float) -> float:
        """Estimate accumulated drift after given seconds of holdover."""
        return self._estimated_drift_rate * seconds

    def get_recent_samples(self, count: int = 300) -> list[dict]:
        recent = self._samples[-count:]
        return [
            {
                "timestamp": s.timestamp,
                "gps_offset_ms": round(s.gps_offset_ms, 3),
                "network_offset_ms": round(s.network_offset_ms, 3),
            }
            for s in recent
        ]

    def get_statistics(self) -> dict:
        if not self._samples:
            return {
                "sample_count": 0,
                "drift_rate_ms_per_sec": 0,
                "drift_rate_ppm": 0,
                "oldest_sample_age": 0,
            }
        now = time.time()
        return {
            "sample_count": len(self._samples),
            "drift_rate_ms_per_sec": round(self._estimated_drift_rate, 6),
            "drift_rate_ppm": round(self.drift_rate_ppm, 3),
            "oldest_sample_age": round(now - self._samples[0].timestamp, 0),
        }


class SourceSelectionEngine:
    """State machine for time source selection with hysteresis.

    GPS is ALWAYS preferred. Network is only used as last resort.
    Anti-flapping: minimum hold times enforced before any transition.
    """

    def __init__(
        self,
        gps_loss_timeout_minutes: float = 15.0,
        flap_hold_time_minutes: float = 10.0,
        holdover_max_minutes: float = 120.0,
        degraded_stratum: int = 2,
        holdover_stratum: int = 3,
        drift_alert_threshold_ms: float = 50.0,
    ):
        self.gps_loss_timeout = gps_loss_timeout_minutes * 60
        self.flap_hold_time = flap_hold_time_minutes * 60
        self.holdover_max = holdover_max_minutes * 60
        self.degraded_stratum = degraded_stratum
        self.holdover_stratum = holdover_stratum
        self.drift_alert_threshold_ms = drift_alert_threshold_ms

        self.status = SourceStatus()
        self.drift_tracker = DriftTracker()
        self._alerts: list[dict] = []
        self._max_alerts = 500
        self._gps_lost_time: float = 0.0
        self._holdover_start_time: float = 0.0

    def set_mode(self, mode: str) -> None:
        """Set manual override mode."""
        mode_map = {"auto": SourceMode.AUTO, "gps": SourceMode.GPS, "network": SourceMode.NETWORK}
        new_mode = mode_map.get(mode)
        if not new_mode:
            raise ValueError(f"Invalid mode: {mode}. Must be auto, gps, or network")

        self.status.mode = new_mode
        if new_mode == SourceMode.GPS:
            self._transition(SourceState.MANUAL_GPS, "manual override: GPS")
        elif new_mode == SourceMode.NETWORK:
            self._transition(SourceState.MANUAL_NETWORK, "manual override: network")
        else:
            # Auto: re-evaluate on next update
            logger.info("Source mode set to auto")

    def update(self, gps_validation: dict, network_available: bool = True) -> SourceStatus:
        """Update source selection based on current GPS validation state.

        Args:
            gps_validation: Result from GPSTimeValidator.validate()
            network_available: Whether network NTP sources are reachable
        """
        now = time.time()

        gps_trusted = gps_validation.get("trusted", False)
        gps_usable = gps_validation.get("usable", False)
        gps_valid = gps_validation.get("valid", False)

        self.status.gps_available = gps_usable
        self.status.gps_trusted = gps_trusted
        self.status.network_available = network_available

        # Manual modes bypass automatic selection
        if self.status.mode == SourceMode.GPS:
            if gps_trusted:
                self.status.stratum = 1
                self.status.active_source = "GPS (forced)"
            elif gps_usable:
                self.status.stratum = self.degraded_stratum
                self.status.active_source = "GPS degraded (forced)"
            else:
                self.status.stratum = self.holdover_stratum
                self.status.active_source = "GPS unavailable (forced mode)"
            return self.status

        if self.status.mode == SourceMode.NETWORK:
            self.status.stratum = 2
            self.status.active_source = "Network (forced)"
            return self.status

        # Auto mode: state machine
        current = self.status.state
        hold_elapsed = now - self.status.last_state_change_time

        if current == SourceState.STARTUP:
            if gps_trusted:
                self._transition(SourceState.GPS_LOCKED, "GPS acquired")
            elif gps_usable:
                self._transition(SourceState.GPS_DEGRADED, "GPS acquired (degraded)")
            elif network_available:
                self._transition(SourceState.NETWORK, "startup: no GPS, using network")

        elif current == SourceState.GPS_LOCKED:
            if gps_trusted:
                self.status.last_gps_lock_time = now
                self._gps_lost_time = 0
            elif gps_usable:
                # GPS degraded but still usable
                if hold_elapsed > self.flap_hold_time:
                    self._transition(SourceState.GPS_DEGRADED, "GPS quality degraded")
            else:
                # GPS lost
                if self._gps_lost_time == 0:
                    self._gps_lost_time = now
                gps_lost_duration = now - self._gps_lost_time
                if gps_lost_duration > self.gps_loss_timeout:
                    self._transition(SourceState.HOLDOVER, "GPS lost, entering holdover")

        elif current == SourceState.GPS_DEGRADED:
            if gps_trusted and hold_elapsed > self.flap_hold_time:
                self._transition(SourceState.GPS_LOCKED, "GPS quality restored")
                self._gps_lost_time = 0
            elif not gps_usable:
                if self._gps_lost_time == 0:
                    self._gps_lost_time = now
                gps_lost_duration = now - self._gps_lost_time
                if gps_lost_duration > self.gps_loss_timeout:
                    self._transition(SourceState.HOLDOVER, "GPS lost from degraded state")

        elif current == SourceState.HOLDOVER:
            self._holdover_start_time = self._holdover_start_time or now
            holdover_duration = now - self._holdover_start_time
            self.status.holdover_elapsed_minutes = holdover_duration / 60.0

            if gps_trusted and hold_elapsed > self.flap_hold_time:
                self._transition(SourceState.GPS_LOCKED, "GPS restored from holdover")
                self._holdover_start_time = 0
                self._gps_lost_time = 0
            elif gps_usable and hold_elapsed > self.flap_hold_time:
                self._transition(SourceState.GPS_DEGRADED, "GPS partially restored")
                self._holdover_start_time = 0
                self._gps_lost_time = 0
            elif holdover_duration > self.holdover_max:
                if network_available:
                    self._transition(SourceState.NETWORK, "holdover expired, falling back to network")
                    self._holdover_start_time = 0
                else:
                    # No choice but to stay in holdover
                    self._add_alert("critical", "Holdover expired with no network available")

        elif current == SourceState.NETWORK:
            if gps_trusted and hold_elapsed > self.flap_hold_time:
                self._transition(SourceState.GPS_LOCKED, "GPS restored, leaving network fallback")
                self._gps_lost_time = 0
            elif gps_usable and hold_elapsed > self.flap_hold_time:
                self._transition(SourceState.GPS_DEGRADED, "GPS partially restored")
                self._gps_lost_time = 0

        # Update stratum based on current state
        self._update_stratum()

        return self.status

    def _transition(self, new_state: SourceState, reason: str) -> None:
        """Perform a state transition with logging and alerting."""
        old_state = self.status.state
        if old_state == new_state:
            return

        logger.info("Source transition: %s -> %s (%s)", old_state.value, new_state.value, reason)
        self.status.state = new_state
        self.status.transition_reason = reason
        self.status.last_state_change_time = time.time()

        level = "info"
        if new_state in (SourceState.HOLDOVER, SourceState.NETWORK):
            level = "warning"
        self._add_alert(level, f"Source: {old_state.value} -> {new_state.value}: {reason}")

    def _update_stratum(self) -> None:
        """Set stratum and active_source based on current state."""
        state_config = {
            SourceState.GPS_LOCKED: (1, "GPS"),
            SourceState.GPS_DEGRADED: (self.degraded_stratum, "GPS (degraded)"),
            SourceState.HOLDOVER: (self.holdover_stratum, "Holdover"),
            SourceState.NETWORK: (2, "Network"),
            SourceState.STARTUP: (16, "none"),
            SourceState.MANUAL_GPS: (1, "GPS (manual)"),
            SourceState.MANUAL_NETWORK: (2, "Network (manual)"),
        }
        stratum, source = state_config.get(self.status.state, (16, "unknown"))
        self.status.stratum = stratum
        self.status.active_source = source

    def _add_alert(self, level: str, message: str) -> None:
        alert = {
            "timestamp": time.time(),
            "level": level,
            "message": message,
        }
        self._alerts.append(alert)
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]

    def get_alerts(self, since: float = 0) -> list[dict]:
        """Get alerts since given timestamp."""
        if since:
            return [a for a in self._alerts if a["timestamp"] > since]
        return list(self._alerts)

    def get_status(self) -> dict:
        return {
            **self.status.to_dict(),
            "drift": self.drift_tracker.get_statistics(),
        }
