"""Main application orchestrator.

Coordinates GPS data collection, source selection, Chrony monitoring,
and serves the web interface with WebSocket real-time updates.
"""

import json
import logging
import os
import threading
import time

from ntpgps import __version__
from ntpgps.config.settings import Config
from ntpgps.gps.parser import GPSDataCollector, GPSTimeValidator
from ntpgps.ntp.chrony import ChronyManager
from ntpgps.ntp.source_manager import SourceSelectionEngine

logger = logging.getLogger(__name__)


class NTPGPSServer:
    """Main application that ties all components together."""

    def __init__(self, config: Config):
        self.config = config
        self.version = __version__

        # GPS data collection
        self.gps = GPSDataCollector(
            host=config.get("gps.gpsd_host", "127.0.0.1"),
            port=config.get("gps.gpsd_port", 2947),
            on_update=self._on_gps_update,
        )

        # GPS time validation
        self.validator = GPSTimeValidator(
            min_satellites=config.get("gps.min_satellites_for_valid_fix", 4),
            max_pdop=config.get("gps.max_pdop_for_valid_fix", 6.0),
            min_signal_db=config.get("gps.min_signal_strength_db", 15.0),
        )

        # Chrony management
        self.chrony = ChronyManager(
            config_path=config.get("ntp.chrony_config_path", "/etc/chrony/chrony.conf"),
        )

        # Source selection engine
        self.source_engine = SourceSelectionEngine(
            gps_loss_timeout_minutes=config.get("source_selection.gps_loss_timeout_minutes", 15),
            flap_hold_time_minutes=config.get("source_selection.flap_hold_time_minutes", 10),
            holdover_max_minutes=config.get("source_selection.holdover_max_minutes", 120),
            degraded_stratum=config.get("source_selection.degraded_stratum", 2),
            holdover_stratum=config.get("source_selection.holdover_stratum", 3),
            drift_alert_threshold_ms=config.get("source_selection.drift_alert_threshold_ms", 50),
        )

        # Set initial mode
        mode = config.get("source_selection.mode", "auto")
        if mode != "auto":
            self.source_engine.set_mode(mode)

        # WebSocket clients
        self._ws_clients: set = set()
        self._ws_lock = threading.Lock()

        # Monitoring thread
        self._running = False
        self._monitor_thread: threading.Thread | None = None
        self._last_validation: dict = {
            "valid": False,
            "trusted": False,
            "usable": False,
            "checks": {
                "time_present": False,
                "fix_valid": False,
                "sufficient_satellites": False,
                "geometry_acceptable": False,
                "signal_quality_ok": False,
                "time_consistent": True,
                "pps_stable": True,
            },
            "consecutive_valid": 0,
            "consecutive_invalid": 0,
        }

    def start(self) -> None:
        """Start all services."""
        logger.info("Starting NTP GPS Server v%s", self.version)

        # Start GPS collector
        self.gps.start()
        logger.info("GPS collector started (gpsd at %s:%d)",
                     self.gps.host, self.gps.port)

        # Run first monitor tick synchronously so initial WS connections
        # get populated data immediately
        try:
            self._run_initial_tick()
        except Exception:
            logger.warning("Initial monitor tick failed (non-fatal)", exc_info=True)

        # Start background monitor thread
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="monitor"
        )
        self._monitor_thread.start()
        logger.info("Monitor thread started (1s interval)")
        logger.info("All services started")

    def _run_initial_tick(self) -> None:
        """Run one monitor tick synchronously at startup to populate initial state."""
        self.chrony.poll()
        network_available = self.chrony.has_network_sources()
        gps_state = self.gps.get_state()
        logger.info("Initial state: gps_connected=%s, network_sources=%s",
                     gps_state["connected"], network_available)

    def stop(self) -> None:
        """Stop all services."""
        logger.info("Stopping NTP GPS Server")
        self._running = False
        self.gps.stop()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    def register_ws_client(self, ws) -> None:
        with self._ws_lock:
            self._ws_clients.add(ws)

    def unregister_ws_client(self, ws) -> None:
        with self._ws_lock:
            self._ws_clients.discard(ws)

    def _broadcast_ws(self, data: dict) -> None:
        """Broadcast data to all WebSocket clients."""
        with self._ws_lock:
            if not self._ws_clients:
                return
            clients = set(self._ws_clients)

        msg = json.dumps(data)
        dead = set()
        for ws in clients:
            try:
                ws.send(msg)
            except Exception:
                logger.debug("WebSocket send failed, removing client")
                dead.add(ws)
        if dead:
            with self._ws_lock:
                self._ws_clients -= dead

    def _on_gps_update(self, msg_class: str) -> None:
        """Called when GPS data is received (from GPS collector thread)."""
        pass  # Monitor thread handles periodic updates

    def _monitor_loop(self) -> None:
        """Main monitoring loop: polls chrony, validates GPS, updates sources."""
        while self._running:
            try:
                self._monitor_tick()
            except Exception:
                logger.exception("Monitor loop error")
            time.sleep(1.0)

    def _monitor_tick(self) -> None:
        """Single monitoring iteration."""
        # Poll chrony
        self.chrony.poll()

        # Validate GPS
        gps_state = self.gps.get_state()
        from ntpgps.gps.parser import GPSFix, SkyView, PPSStatus, FixMode, Satellite, GNSSConstellation

        fix_data = gps_state["fix"]
        fix = GPSFix(
            mode=FixMode(fix_data["mode"]),
            time_str=fix_data["time"],
            timestamp=fix_data["timestamp"],
            latitude=fix_data["latitude"],
            longitude=fix_data["longitude"],
            altitude=fix_data["altitude"],
            speed=fix_data["speed"],
            climb=0,
            ept=fix_data["ept"],
            epx=0, epy=0, epv=0,
        )

        sky_data = gps_state["sky"]
        sky = SkyView(
            satellites=[
                Satellite(
                    prn=s["prn"], elevation=s["elevation"], azimuth=s["azimuth"],
                    signal_strength=s["signal_strength"], used=s["used"],
                    constellation=GNSSConstellation[s["constellation"]],
                    gnssid=s["gnssid"], svid=s.get("svid", s["prn"]),
                )
                for s in sky_data["satellites"]
            ],
            pdop=sky_data["pdop"],
            hdop=sky_data["hdop"],
            vdop=sky_data["vdop"],
            tdop=sky_data["tdop"],
            gdop=sky_data["gdop"],
            n_visible=sky_data["n_visible"],
            n_used=sky_data["n_used"],
        )

        pps_data = gps_state["pps"]
        pps = PPSStatus(
            present=pps_data["present"],
            stable=pps_data["stable"],
            offset_us=pps_data["offset_us"],
            jitter_us=pps_data["jitter_us"],
            last_seen=pps_data["last_seen"],
        )

        validation = self.validator.validate(fix, sky, pps)
        self._last_validation = validation

        # Update source selection
        network_available = self.chrony.has_network_sources()
        self.source_engine.update(validation, network_available)

        # Track drift
        gps_offset = self.chrony.get_gps_offset_ms()
        net_offset = self.chrony.get_network_offset_ms()
        if gps_offset is not None and net_offset is not None:
            self.source_engine.drift_tracker.add_sample(gps_offset, net_offset)

        # Broadcast to WebSocket clients
        self._broadcast_ws(self.get_full_status())

    def get_full_status(self) -> dict:
        """Get complete system status for API/WebSocket."""
        return {
            "type": "status",
            "timestamp": time.time(),
            "version": self.version,
            "gps": self.gps.get_state(),
            "validation": self._last_validation,
            "source": self.source_engine.get_status(),
            "chrony": {
                "sources": self.chrony.get_sources(),
                "tracking": self.chrony.get_tracking(),
            },
            "drift": {
                "statistics": self.source_engine.drift_tracker.get_statistics(),
                "recent_samples": self.source_engine.drift_tracker.get_recent_samples(60),
            },
            "alerts": self.source_engine.get_alerts(),
        }
