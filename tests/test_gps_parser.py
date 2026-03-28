"""Tests for GPS data parser with real-world edge cases."""

import json
import time

import pytest

from ntpgps.gps.parser import (
    FixMode,
    GNSSConstellation,
    GPSDataCollector,
    GPSFix,
    GPSTimeValidator,
    PPSStatus,
    Satellite,
    SkyView,
)


class TestFixMode:
    def test_values(self):
        assert FixMode.NO_FIX == 1
        assert FixMode.FIX_2D == 2
        assert FixMode.FIX_3D == 3


class TestGNSSConstellation:
    def test_from_gnssid(self):
        assert GNSSConstellation.from_gnssid(0) == GNSSConstellation.GPS
        assert GNSSConstellation.from_gnssid(6) == GNSSConstellation.GLONASS
        assert GNSSConstellation.from_gnssid(1) == GNSSConstellation.SBAS

    def test_from_prn_gps(self):
        assert GNSSConstellation.from_prn(1) == GNSSConstellation.GPS
        assert GNSSConstellation.from_prn(32) == GNSSConstellation.GPS

    def test_from_prn_glonass(self):
        assert GNSSConstellation.from_prn(65) == GNSSConstellation.GLONASS
        assert GNSSConstellation.from_prn(96) == GNSSConstellation.GLONASS

    def test_from_prn_sbas(self):
        assert GNSSConstellation.from_prn(120) == GNSSConstellation.SBAS
        assert GNSSConstellation.from_prn(158) == GNSSConstellation.SBAS

    def test_from_prn_qzss(self):
        assert GNSSConstellation.from_prn(193) == GNSSConstellation.QZSS

    def test_from_invalid_gnssid(self):
        # Should default to GPS for unknown
        assert GNSSConstellation.from_gnssid(99) == GNSSConstellation.GPS


class TestSatellite:
    def test_signal_quality_none(self):
        sat = Satellite(prn=1, elevation=45, azimuth=90, signal_strength=0,
                        used=False, constellation=GNSSConstellation.GPS)
        assert sat.signal_quality == "none"
        assert not sat.is_valid_signal

    def test_signal_quality_weak(self):
        sat = Satellite(prn=1, elevation=45, azimuth=90, signal_strength=10,
                        used=False, constellation=GNSSConstellation.GPS)
        assert sat.signal_quality == "weak"

    def test_signal_quality_moderate(self):
        sat = Satellite(prn=1, elevation=45, azimuth=90, signal_strength=20,
                        used=True, constellation=GNSSConstellation.GPS)
        assert sat.signal_quality == "moderate"

    def test_signal_quality_strong(self):
        sat = Satellite(prn=1, elevation=45, azimuth=90, signal_strength=35,
                        used=True, constellation=GNSSConstellation.GPS)
        assert sat.signal_quality == "strong"

    def test_negative_elevation(self):
        sat = Satellite(prn=1, elevation=-5, azimuth=90, signal_strength=10,
                        used=False, constellation=GNSSConstellation.GPS)
        assert not sat.is_above_horizon

    def test_to_dict(self):
        sat = Satellite(prn=5, elevation=30, azimuth=180, signal_strength=28,
                        used=True, constellation=GNSSConstellation.GPS)
        d = sat.to_dict()
        assert d["prn"] == 5
        assert d["used"] is True
        assert d["signal_quality"] == "strong"
        assert d["constellation"] == "GPS"


class TestGPSFix:
    def test_no_fix_with_time(self):
        """Simulate mode=1 but time present (common u-blox behavior)."""
        fix = GPSFix(
            mode=FixMode.NO_FIX, time_str="2024-01-15T10:30:00Z",
            timestamp=1705312200.0, latitude=0, longitude=0,
            altitude=0, speed=0, climb=0, ept=0.5, epx=0, epy=0, epv=0,
        )
        assert fix.has_valid_time
        assert not fix.has_position
        assert not fix.has_3d_fix

    def test_3d_fix(self):
        fix = GPSFix(
            mode=FixMode.FIX_3D, time_str="2024-01-15T10:30:00Z",
            timestamp=1705312200.0, latitude=-35.28, longitude=149.13,
            altitude=580, speed=0, climb=0, ept=0.01, epx=5, epy=5, epv=10,
        )
        assert fix.has_valid_time
        assert fix.has_position
        assert fix.has_3d_fix

    def test_empty_time(self):
        fix = GPSFix(
            mode=FixMode.UNKNOWN, time_str="", timestamp=0,
            latitude=0, longitude=0, altitude=0, speed=0, climb=0,
            ept=0, epx=0, epy=0, epv=0,
        )
        assert not fix.has_valid_time


class TestSkyView:
    def test_empty_sky(self):
        sky = SkyView()
        assert sky.n_visible == 0
        assert sky.n_used == 0
        assert sky.geometry_quality == "unusable"
        assert not sky.has_usable_geometry

    def test_nsat_26_usat_0(self):
        """Real scenario: 26 satellites visible but none used."""
        sats = [
            Satellite(prn=i, elevation=30, azimuth=i*10, signal_strength=0,
                      used=False, constellation=GNSSConstellation.GPS)
            for i in range(1, 27)
        ]
        sky = SkyView(satellites=sats, pdop=100.0, n_visible=26, n_used=0)
        assert sky.n_visible == 26
        assert sky.n_used == 0
        assert not sky.has_usable_geometry
        assert sky.geometry_quality == "unusable"

    def test_good_geometry(self):
        sats = [
            Satellite(prn=i, elevation=45, azimuth=i*45, signal_strength=35,
                      used=True, constellation=GNSSConstellation.GPS)
            for i in range(1, 9)
        ]
        sky = SkyView(satellites=sats, pdop=1.5, n_visible=8, n_used=8)
        assert sky.has_usable_geometry
        assert sky.geometry_quality == "excellent"

    def test_high_pdop(self):
        """Real scenario: extremely high PDOP."""
        sky = SkyView(pdop=100.0, n_visible=3, n_used=3)
        assert sky.geometry_quality == "unusable"

    def test_mixed_constellations(self):
        """Mixed GPS, GLONASS, SBAS satellites."""
        sats = [
            Satellite(prn=1, elevation=45, azimuth=90, signal_strength=30,
                      used=True, constellation=GNSSConstellation.GPS),
            Satellite(prn=65, elevation=30, azimuth=180, signal_strength=25,
                      used=True, constellation=GNSSConstellation.GLONASS),
            Satellite(prn=120, elevation=20, azimuth=270, signal_strength=15,
                      used=False, constellation=GNSSConstellation.SBAS),
        ]
        sky = SkyView(satellites=sats, pdop=3.0, n_visible=3, n_used=2)
        constellations = {s.constellation for s in sky.satellites}
        assert GNSSConstellation.GPS in constellations
        assert GNSSConstellation.GLONASS in constellations
        assert GNSSConstellation.SBAS in constellations


class TestPPSStatus:
    def test_no_pps(self):
        pps = PPSStatus()
        assert not pps.present
        assert not pps.is_fresh

    def test_fresh_pps(self):
        pps = PPSStatus(present=True, stable=True, offset_us=50.0,
                        jitter_us=10.0, last_seen=time.time())
        assert pps.present
        assert pps.is_fresh
        assert pps.age_seconds < 1

    def test_stale_pps(self):
        pps = PPSStatus(present=True, stable=True, offset_us=50.0,
                        jitter_us=10.0, last_seen=time.time() - 10)
        assert not pps.is_fresh


class TestGPSTimeValidator:
    def _make_good_state(self):
        fix = GPSFix(
            mode=FixMode.FIX_3D, time_str="2024-01-15T10:30:00Z",
            timestamp=time.time(), latitude=-35.28, longitude=149.13,
            altitude=580, speed=0, climb=0, ept=0.01, epx=5, epy=5, epv=10,
        )
        sats = [
            Satellite(prn=i, elevation=45, azimuth=i*45, signal_strength=35,
                      used=True, constellation=GNSSConstellation.GPS)
            for i in range(1, 7)
        ]
        sky = SkyView(satellites=sats, pdop=2.0, n_visible=6, n_used=6)
        pps = PPSStatus(present=True, stable=True, offset_us=50, jitter_us=10,
                        last_seen=time.time())
        return fix, sky, pps

    def test_valid_fix_passes(self):
        validator = GPSTimeValidator()
        fix, sky, pps = self._make_good_state()
        result = validator.validate(fix, sky, pps)
        assert result["valid"]
        assert result["usable"]

    def test_trusted_requires_consecutive(self):
        validator = GPSTimeValidator()
        fix, sky, pps = self._make_good_state()

        # First two readings: valid but not yet trusted
        r1 = validator.validate(fix, sky, pps)
        assert r1["valid"]
        assert not r1["trusted"]

        r2 = validator.validate(fix, sky, pps)
        assert not r2["trusted"]

        # Third reading: now trusted
        r3 = validator.validate(fix, sky, pps)
        assert r3["trusted"]

    def test_no_fix_mode_1_with_time(self):
        """Mode 1 (no fix) but time present - common u-blox behavior."""
        validator = GPSTimeValidator()
        fix = GPSFix(
            mode=FixMode.NO_FIX, time_str="2024-01-15T10:30:00Z",
            timestamp=time.time(), latitude=0, longitude=0,
            altitude=0, speed=0, climb=0, ept=0, epx=0, epy=0, epv=0,
        )
        sky = SkyView(n_visible=5, n_used=0, pdop=99.99)
        pps = PPSStatus()

        result = validator.validate(fix, sky, pps)
        assert not result["valid"]  # Not valid (no fix, no sats)
        assert not result["trusted"]

    def test_too_few_satellites(self):
        validator = GPSTimeValidator(min_satellites=4)
        fix, sky, pps = self._make_good_state()
        sky.n_used = 2
        sky.satellites = sky.satellites[:2]

        result = validator.validate(fix, sky, pps)
        assert not result["valid"]

    def test_pdop_too_high(self):
        validator = GPSTimeValidator(max_pdop=6.0)
        fix, sky, pps = self._make_good_state()
        sky.pdop = 15.0

        result = validator.validate(fix, sky, pps)
        assert not result["valid"]

    def test_weak_signals(self):
        validator = GPSTimeValidator(min_signal_db=15.0)
        fix = GPSFix(
            mode=FixMode.FIX_3D, time_str="2024-01-15T10:30:00Z",
            timestamp=time.time(), latitude=-35.28, longitude=149.13,
            altitude=580, speed=0, climb=0, ept=0.01, epx=5, epy=5, epv=10,
        )
        sats = [
            Satellite(prn=i, elevation=45, azimuth=i*45, signal_strength=5,
                      used=True, constellation=GNSSConstellation.GPS)
            for i in range(1, 7)
        ]
        sky = SkyView(satellites=sats, pdop=2.0, n_visible=6, n_used=6)
        pps = PPSStatus(present=True, stable=True, last_seen=time.time())

        result = validator.validate(fix, sky, pps)
        assert not result["valid"]

    def test_usable_with_degraded_gps(self):
        """GPS with time and 1 satellite: usable but not valid."""
        validator = GPSTimeValidator()
        fix = GPSFix(
            mode=FixMode.NO_FIX, time_str="2024-01-15T10:30:00Z",
            timestamp=time.time(), latitude=0, longitude=0,
            altitude=0, speed=0, climb=0, ept=0, epx=0, epy=0, epv=0,
        )
        sats = [
            Satellite(prn=1, elevation=45, azimuth=90, signal_strength=25,
                      used=True, constellation=GNSSConstellation.GPS),
        ]
        sky = SkyView(satellites=sats, pdop=99.99, n_visible=1, n_used=1)
        pps = PPSStatus()

        result = validator.validate(fix, sky, pps)
        assert not result["valid"]
        assert result["usable"]


class TestBinaryFilter:
    def test_clean_ascii(self):
        data = b'{"class":"TPV","mode":3}\n'
        result = GPSDataCollector._filter_binary(data)
        assert '{"class":"TPV","mode":3}' in result

    def test_ubx_binary_stripped(self):
        """UBX sync bytes (0xB5 0x62) followed by a message should be stripped."""
        # UBX header: sync(2) + class(1) + id(1) + len(2) + payload + cksum(2)
        # Minimal UBX message with 0-byte payload: B5 62 01 02 00 00 XX XX
        ubx_msg = bytes([0xB5, 0x62, 0x01, 0x02, 0x00, 0x00, 0x03, 0x0A])
        text = b'{"class":"SKY"}\n'
        data = ubx_msg + text

        result = GPSDataCollector._filter_binary(data)
        assert '{"class":"SKY"}' in result

    def test_mixed_binary_and_text(self):
        """Realistic mixed stream."""
        line1 = b'{"class":"TPV"}\n'
        ubx = bytes([0xB5, 0x62, 0x05, 0x01, 0x02, 0x00, 0x06, 0x01, 0x0F, 0x38])
        line2 = b'{"class":"SKY"}\n'
        data = line1 + ubx + line2

        result = GPSDataCollector._filter_binary(data)
        assert "TPV" in result
        assert "SKY" in result

    def test_non_ascii_bytes_stripped(self):
        data = bytes([0x80, 0x90, 0xFF]) + b'HELLO' + bytes([0x00, 0x01])
        result = GPSDataCollector._filter_binary(data)
        assert "HELLO" in result


class TestGPSDataCollectorParsing:
    def _make_collector(self):
        return GPSDataCollector(host="127.0.0.1", port=2947)

    def test_parse_tpv_3d_fix(self):
        collector = self._make_collector()
        line = json.dumps({
            "class": "TPV",
            "mode": 3,
            "time": "2024-06-15T12:00:00.000Z",
            "lat": -35.2809,
            "lon": 149.1300,
            "altHAE": 580.0,
            "speed": 0.0,
            "ept": 0.005,
        })
        collector._parse_line(line)
        assert collector.fix.mode == FixMode.FIX_3D
        assert collector.fix.has_valid_time
        assert abs(collector.fix.latitude - (-35.2809)) < 0.001

    def test_parse_tpv_no_fix_with_time(self):
        """u-blox emitting time without valid fix."""
        collector = self._make_collector()
        line = json.dumps({
            "class": "TPV",
            "mode": 1,
            "time": "2024-06-15T12:00:00.000Z",
        })
        collector._parse_line(line)
        assert collector.fix.mode == FixMode.NO_FIX
        assert collector.fix.has_valid_time

    def test_parse_sky_nsat_26_usat_0(self):
        """26 visible, 0 used - real scenario."""
        collector = self._make_collector()
        sats = [
            {"PRN": i, "el": 30, "az": i*14, "ss": 0, "used": False, "gnssid": 0}
            for i in range(1, 27)
        ]
        line = json.dumps({
            "class": "SKY",
            "nSat": 26,
            "uSat": 0,
            "satellites": sats,
            "pdop": 100.0,
        })
        collector._parse_line(line)
        assert collector.sky.n_visible >= 26
        assert collector.sky.n_used == 0
        assert collector.sky.pdop == 100.0

    def test_parse_sky_mixed_constellations(self):
        collector = self._make_collector()
        sats = [
            {"PRN": 5, "el": 45, "az": 90, "ss": 30, "used": True, "gnssid": 0},
            {"PRN": 72, "el": 30, "az": 180, "ss": 25, "used": True, "gnssid": 6},
            {"PRN": 130, "el": 20, "az": 270, "ss": 15, "used": False, "gnssid": 1},
            {"PRN": 195, "el": 60, "az": 0, "ss": 20, "used": False, "gnssid": 5},
        ]
        line = json.dumps({
            "class": "SKY",
            "satellites": sats,
            "pdop": 3.5,
        })
        collector._parse_line(line)
        constellations = {s.constellation for s in collector.sky.satellites}
        assert GNSSConstellation.GPS in constellations
        assert GNSSConstellation.GLONASS in constellations
        assert GNSSConstellation.SBAS in constellations
        assert GNSSConstellation.QZSS in constellations

    def test_parse_sky_null_values(self):
        """Handle null values in satellite data."""
        collector = self._make_collector()
        line = json.dumps({
            "class": "SKY",
            "satellites": [
                {"PRN": 1, "el": None, "az": None, "ss": None, "used": False},
            ],
        })
        collector._parse_line(line)
        assert len(collector.sky.satellites) == 1
        assert collector.sky.satellites[0].elevation == 0.0
        assert collector.sky.satellites[0].signal_strength == 0.0

    def test_parse_sky_negative_elevation(self):
        """Satellites below horizon."""
        collector = self._make_collector()
        line = json.dumps({
            "class": "SKY",
            "satellites": [
                {"PRN": 5, "el": -3, "az": 90, "ss": 5, "used": False},
            ],
        })
        collector._parse_line(line)
        assert collector.sky.satellites[0].elevation == -3
        assert not collector.sky.satellites[0].is_above_horizon

    def test_parse_pps(self):
        collector = self._make_collector()
        now = time.time()
        line = json.dumps({
            "class": "PPS",
            "real_sec": int(now),
            "real_nsec": 500,
            "clock_sec": int(now),
            "clock_nsec": 200,
        })
        collector._parse_line(line)
        assert collector.pps.present
        assert abs(collector.pps.offset_us) < 1000

    def test_parse_version(self):
        collector = self._make_collector()
        line = json.dumps({
            "class": "VERSION",
            "release": "3.25",
            "rev": "abcdef",
        })
        collector._parse_line(line)
        assert collector._gpsd_version == "3.25"

    def test_parse_invalid_json(self):
        """Non-JSON line should be silently skipped."""
        collector = self._make_collector()
        collector._parse_line("this is not json{{{")
        # Should not raise

    def test_parse_invalid_dop(self):
        collector = self._make_collector()
        line = json.dumps({
            "class": "SKY",
            "satellites": [],
            "pdop": None,
            "hdop": "nan",
            "vdop": -1,
        })
        collector._parse_line(line)
        assert collector.sky.pdop == 99.99
        assert collector.sky.hdop == 99.99
        assert collector.sky.vdop == 99.99

    def test_get_state_returns_dict(self):
        collector = self._make_collector()
        state = collector.get_state()
        assert "connected" in state
        assert "fix" in state
        assert "sky" in state
        assert "pps" in state
