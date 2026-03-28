"""Tests for Chrony management and monitoring."""

import pytest

from ntpgps.ntp.chrony import ChronyManager, ChronySource, ChronyTracking


class TestChronySource:
    def test_selected_source(self):
        src = ChronySource(
            mode="#", state="*", name="GPS", stratum=0, poll=4,
            reach=0o377, last_rx="2", offset=0.00025, error=0.000125,
        )
        assert src.is_selected
        assert src.is_reachable
        assert src.reach_percent == 100.0

    def test_unreachable_source(self):
        src = ChronySource(
            mode="^", state="?", name="pool.ntp.org", stratum=2, poll=6,
            reach=0, last_rx="-", offset=0, error=0,
        )
        assert not src.is_selected
        assert not src.is_reachable
        assert src.reach_percent == 0

    def test_partial_reach(self):
        src = ChronySource(
            mode="^", state="+", name="ntp.example.com", stratum=1, poll=6,
            reach=0o377, last_rx="5", offset=-0.002345, error=0.034,
        )
        assert src.reach_percent == 100.0

    def test_to_dict(self):
        src = ChronySource(
            mode="#", state="*", name="PPS", stratum=0, poll=4,
            reach=0o377, last_rx="1", offset=0.00000025, error=0.000000125,
        )
        d = src.to_dict()
        assert d["name"] == "PPS"
        assert d["is_selected"] is True
        assert "offset_ms" in d
        assert "error_ms" in d


class TestChronyTracking:
    def test_default(self):
        t = ChronyTracking()
        assert t.stratum == 0
        assert t.leap_status == "Normal"

    def test_to_dict(self):
        t = ChronyTracking(
            ref_id="47505300", ref_name="GPS", stratum=1,
            system_time_offset=0.000001234, last_offset=-0.000000567,
            frequency=1.5, skew=0.003,
        )
        d = t.to_dict()
        assert d["ref_name"] == "GPS"
        assert d["stratum"] == 1
        assert "system_time_offset_us" in d
        assert "frequency_ppm" in d


class TestParseTimeValue:
    def test_nanoseconds(self):
        assert abs(ChronyManager._parse_time_value("+250ns") - 250e-9) < 1e-12

    def test_microseconds(self):
        assert abs(ChronyManager._parse_time_value("-2345us") - (-2345e-6)) < 1e-10

    def test_milliseconds(self):
        assert abs(ChronyManager._parse_time_value("+1.2ms") - 1.2e-3) < 1e-10

    def test_seconds(self):
        assert abs(ChronyManager._parse_time_value("34ms") - 34e-3) < 1e-10

    def test_bracketed(self):
        assert abs(ChronyManager._parse_time_value("[+450ns]") - 450e-9) < 1e-12

    def test_empty_string(self):
        assert ChronyManager._parse_time_value("") == 0.0

    def test_no_match(self):
        assert ChronyManager._parse_time_value("garbage") == 0.0


class TestParseTrackingValue:
    def test_fast(self):
        val = ChronyManager._parse_tracking_value("0.000001234 seconds fast")
        assert val > 0

    def test_slow(self):
        val = ChronyManager._parse_tracking_value("0.000005678 seconds slow")
        assert val < 0

    def test_no_direction(self):
        val = ChronyManager._parse_tracking_value("0.000123 seconds")
        assert abs(val - 0.000123) < 1e-9


class TestGenerateConfig:
    def test_generates_valid_config(self):
        mgr = ChronyManager()
        config = mgr.generate_config()
        assert "refclock SHM 0" in config
        assert "refclock SHM 1" in config
        assert "GPS" in config
        assert "PPS" in config
        assert "pool.ntp.org" in config or "au.pool.ntp.org" in config
        assert "local stratum" in config
        assert "makestep" in config

    def test_custom_parameters(self):
        mgr = ChronyManager()
        config = mgr.generate_config(
            gps_shm_unit=2,
            pps_shm_unit=3,
            network_servers=["time.google.com"],
            local_stratum=5,
        )
        assert "SHM 2" in config
        assert "SHM 3" in config
        assert "time.google.com" in config
        assert "local stratum 5" in config

    def test_config_has_required_sections(self):
        mgr = ChronyManager()
        config = mgr.generate_config()
        # Must have all critical sections
        assert "prefer" in config  # GPS should be preferred
        assert "allow" in config  # Allow NTP clients
        assert "driftfile" in config
        assert "logdir" in config
        assert "maxdistance" in config  # Important for USB GPS
