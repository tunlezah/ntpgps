"""Tests for source selection engine with anti-flapping."""

import time

import pytest

from ntpgps.ntp.source_manager import (
    DriftTracker,
    SourceMode,
    SourceSelectionEngine,
    SourceState,
)


class TestDriftTracker:
    def test_empty_statistics(self):
        tracker = DriftTracker()
        stats = tracker.get_statistics()
        assert stats["sample_count"] == 0

    def test_add_samples(self):
        tracker = DriftTracker()
        for i in range(20):
            tracker.add_sample(float(i) * 0.1, float(i) * 0.2)
        stats = tracker.get_statistics()
        assert stats["sample_count"] == 20

    def test_max_samples(self):
        tracker = DriftTracker(max_samples=10)
        for i in range(20):
            tracker.add_sample(float(i), float(i))
        stats = tracker.get_statistics()
        assert stats["sample_count"] == 10

    def test_recent_samples(self):
        tracker = DriftTracker()
        for i in range(5):
            tracker.add_sample(float(i), float(i) * 2)
        recent = tracker.get_recent_samples(3)
        assert len(recent) == 3

    def test_drift_rate_estimation(self):
        tracker = DriftTracker()
        # Add samples with known linear drift
        for i in range(100):
            tracker.add_sample(float(i) * 0.001, 0.0)
        # Should estimate a positive drift rate
        assert tracker.drift_rate_ms_per_sec != 0

    def test_estimate_offset(self):
        tracker = DriftTracker()
        # Manually set drift rate for testing
        tracker._estimated_drift_rate = 0.01  # 0.01 ms/s = 10 ppm
        offset = tracker.estimate_offset_after(60)
        assert abs(offset - 0.6) < 0.001  # 60s * 0.01 ms/s = 0.6ms


def _make_valid_gps():
    return {
        "valid": True,
        "trusted": True,
        "usable": True,
        "checks": {},
        "consecutive_valid": 5,
        "consecutive_invalid": 0,
    }


def _make_degraded_gps():
    return {
        "valid": False,
        "trusted": False,
        "usable": True,
        "checks": {},
        "consecutive_valid": 0,
        "consecutive_invalid": 1,
    }


def _make_no_gps():
    return {
        "valid": False,
        "trusted": False,
        "usable": False,
        "checks": {},
        "consecutive_valid": 0,
        "consecutive_invalid": 10,
    }


class TestSourceSelectionEngine:
    def test_startup_to_gps_locked(self):
        engine = SourceSelectionEngine(flap_hold_time_minutes=0)
        assert engine.status.state == SourceState.STARTUP

        status = engine.update(_make_valid_gps())
        assert status.state == SourceState.GPS_LOCKED
        assert status.stratum == 1
        assert status.active_source == "GPS"

    def test_startup_to_network_when_no_gps(self):
        engine = SourceSelectionEngine(flap_hold_time_minutes=0)
        status = engine.update(_make_no_gps(), network_available=True)
        assert status.state == SourceState.NETWORK

    def test_startup_stays_startup_when_nothing(self):
        engine = SourceSelectionEngine()
        status = engine.update(_make_no_gps(), network_available=False)
        assert status.state == SourceState.STARTUP

    def test_gps_locked_stratum_1(self):
        engine = SourceSelectionEngine(flap_hold_time_minutes=0)
        engine.update(_make_valid_gps())
        assert engine.status.stratum == 1

    def test_gps_degraded_stratum_2(self):
        engine = SourceSelectionEngine(
            flap_hold_time_minutes=0,
            gps_loss_timeout_minutes=0.001,
            degraded_stratum=2,
        )
        # Start with GPS locked
        engine.update(_make_valid_gps())
        assert engine.status.state == SourceState.GPS_LOCKED

        # Force time to allow transition
        engine.status.last_state_change_time = time.time() - 3600

        # GPS degrades
        engine.update(_make_degraded_gps())
        assert engine.status.state == SourceState.GPS_DEGRADED
        assert engine.status.stratum == 2

    def test_holdover_after_gps_loss(self):
        engine = SourceSelectionEngine(
            gps_loss_timeout_minutes=0.001,  # Very short for testing
            flap_hold_time_minutes=0,
        )
        # Start locked
        engine.update(_make_valid_gps())

        # GPS lost - need to wait for timeout
        engine._gps_lost_time = time.time() - 120  # Pretend lost 2 minutes ago
        engine.update(_make_no_gps())
        assert engine.status.state == SourceState.HOLDOVER

    def test_network_fallback_after_holdover(self):
        engine = SourceSelectionEngine(
            gps_loss_timeout_minutes=0.001,
            holdover_max_minutes=0.001,
            flap_hold_time_minutes=0,
        )
        # GPS locked -> holdover
        engine.update(_make_valid_gps())
        engine._gps_lost_time = time.time() - 120
        engine.update(_make_no_gps())
        assert engine.status.state == SourceState.HOLDOVER

        # Holdover expires -> network
        engine._holdover_start_time = time.time() - 120
        engine.update(_make_no_gps(), network_available=True)
        assert engine.status.state == SourceState.NETWORK

    def test_anti_flapping_hold_time(self):
        """Transitions should respect hold time."""
        engine = SourceSelectionEngine(flap_hold_time_minutes=10)

        # Start locked
        engine.update(_make_valid_gps())
        assert engine.status.state == SourceState.GPS_LOCKED

        # Immediately try to degrade - should NOT transition (hold time)
        engine.update(_make_degraded_gps())
        # State change time is very recent, so no transition yet
        assert engine.status.state == SourceState.GPS_LOCKED

    def test_gps_recovery_from_holdover(self):
        engine = SourceSelectionEngine(
            gps_loss_timeout_minutes=0.001,
            flap_hold_time_minutes=0,
        )
        # GPS locked -> holdover
        engine.update(_make_valid_gps())
        engine._gps_lost_time = time.time() - 120
        engine.update(_make_no_gps())
        assert engine.status.state == SourceState.HOLDOVER

        # GPS returns
        engine.status.last_state_change_time = time.time() - 3600
        engine.update(_make_valid_gps())
        assert engine.status.state == SourceState.GPS_LOCKED

    def test_manual_gps_mode(self):
        engine = SourceSelectionEngine()
        engine.set_mode("gps")
        assert engine.status.mode == SourceMode.GPS
        assert engine.status.state == SourceState.MANUAL_GPS

        # Update with no GPS - should stay in manual GPS
        engine.update(_make_no_gps())
        assert engine.status.mode == SourceMode.GPS

    def test_manual_network_mode(self):
        engine = SourceSelectionEngine()
        engine.set_mode("network")
        assert engine.status.mode == SourceMode.NETWORK
        assert engine.status.state == SourceState.MANUAL_NETWORK

    def test_auto_mode_restores(self):
        engine = SourceSelectionEngine(flap_hold_time_minutes=0)
        engine.set_mode("network")
        engine.set_mode("auto")
        assert engine.status.mode == SourceMode.AUTO
        # Should re-evaluate on next update
        engine.update(_make_valid_gps())
        # May or may not transition immediately depending on state

    def test_invalid_mode_raises(self):
        engine = SourceSelectionEngine()
        with pytest.raises(ValueError):
            engine.set_mode("invalid")

    def test_alerts_generated(self):
        engine = SourceSelectionEngine(flap_hold_time_minutes=0)
        engine.update(_make_valid_gps())
        alerts = engine.get_alerts()
        assert len(alerts) > 0
        assert any("GPS acquired" in a["message"] for a in alerts)

    def test_alerts_since_filter(self):
        engine = SourceSelectionEngine(flap_hold_time_minutes=0)
        before = time.time()
        engine.update(_make_valid_gps())
        # Get alerts since before
        alerts = engine.get_alerts(since=before - 1)
        assert len(alerts) > 0

    def test_get_status_dict(self):
        engine = SourceSelectionEngine()
        status = engine.get_status()
        assert "state" in status
        assert "stratum" in status
        assert "drift" in status
        assert "mode" in status

    def test_holdover_elapsed_tracking(self):
        engine = SourceSelectionEngine(
            gps_loss_timeout_minutes=0.001,
            flap_hold_time_minutes=0,
        )
        engine.update(_make_valid_gps())
        engine._gps_lost_time = time.time() - 120
        engine.update(_make_no_gps())

        # In holdover
        engine._holdover_start_time = time.time() - 300  # 5 minutes ago
        engine.update(_make_no_gps())
        assert engine.status.holdover_elapsed_minutes > 4

    def test_gps_always_preferred_over_network(self):
        """GPS must ALWAYS be preferred. Network only as last resort."""
        engine = SourceSelectionEngine(flap_hold_time_minutes=0)

        # Even degraded GPS should be preferred
        engine.update(_make_degraded_gps(), network_available=True)
        # From startup, degraded GPS should lead to GPS_DEGRADED
        assert engine.status.state == SourceState.GPS_DEGRADED
        assert "GPS" in engine.status.active_source

    def test_no_flapping_scenario(self):
        """Simulate GPS going in and out - no rapid state changes."""
        engine = SourceSelectionEngine(flap_hold_time_minutes=5)

        # Lock GPS
        engine.update(_make_valid_gps())
        assert engine.status.state == SourceState.GPS_LOCKED

        # GPS briefly degrades
        engine.update(_make_degraded_gps())
        # Should NOT change state (hold time)
        assert engine.status.state == SourceState.GPS_LOCKED

        # GPS returns
        engine.update(_make_valid_gps())
        assert engine.status.state == SourceState.GPS_LOCKED
