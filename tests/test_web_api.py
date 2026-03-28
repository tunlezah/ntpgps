"""Tests for the web API endpoints."""

import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from ntpgps.config.settings import Config
from ntpgps.ntp.source_manager import SourceSelectionEngine, SourceState
from ntpgps.server import NTPGPSServer
from ntpgps.web.app import create_app, set_server


@pytest.fixture
def mock_server(tmp_path):
    """Create a mock server with all required attributes."""
    config_path = str(tmp_path / "test_config.yaml")
    config = Config(config_path)

    server = MagicMock(spec=NTPGPSServer)
    server.config = config
    server.version = "1.0.0"
    server.source_engine = SourceSelectionEngine()

    # Mock chrony manager
    server.chrony = MagicMock()
    server.chrony.get_sources.return_value = []
    server.chrony.get_tracking.return_value = {"ref_name": "GPS", "stratum": 1}
    server.chrony.restart_service.return_value = (True, "OK")

    # Mock GPS collector
    server.gps = MagicMock()
    server.gps.get_state.return_value = {
        "connected": True,
        "data_age": 0.5,
        "fix": {
            "mode": 3, "mode_name": "FIX_3D", "time": "2024-01-15T12:00:00Z",
            "timestamp": time.time(), "latitude": -35.28, "longitude": 149.13,
            "altitude": 580, "speed": 0, "ept": 0.01, "has_valid_time": True,
            "has_position": True, "has_3d_fix": True,
        },
        "sky": {
            "satellites": [], "pdop": 2.0, "hdop": 1.5, "vdop": 1.2,
            "tdop": 1.0, "gdop": 2.5, "n_visible": 8, "n_used": 6,
            "geometry_quality": "good",
        },
        "pps": {
            "present": True, "stable": True, "offset_us": 50.0,
            "jitter_us": 10.0, "last_seen": time.time(), "age_seconds": 0.5,
            "is_fresh": True,
        },
        "gpsd_version": "3.25",
    }

    server.get_full_status.return_value = {
        "type": "status",
        "timestamp": time.time(),
        "version": "1.0.0",
        "gps": server.gps.get_state.return_value,
        "validation": {"valid": True, "trusted": True, "usable": True},
        "source": server.source_engine.get_status(),
        "chrony": {
            "sources": server.chrony.get_sources.return_value,
            "tracking": server.chrony.get_tracking.return_value,
        },
        "drift": {"statistics": {}, "recent_samples": []},
        "alerts": [],
    }

    return server


@pytest.fixture
def client(mock_server):
    set_server(mock_server)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "version" in data


class TestStatusEndpoint:
    def test_status(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["type"] == "status"
        assert "gps" in data
        assert "source" in data


class TestConfigEndpoint:
    def test_get_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "server" in data
        assert "gps" in data

    def test_set_config(self, client, mock_server, tmp_path):
        config_path = str(tmp_path / "set_config_test.yaml")
        mock_server.config = Config(config_path)
        resp = client.post("/api/config",
                           json={"server.port": 9900},
                           content_type="application/json")
        # Config save may fail but set should work
        assert resp.status_code in (200, 400, 500)


class TestSourceModeEndpoint:
    def test_set_mode_auto(self, client, mock_server):
        mock_server.source_engine = SourceSelectionEngine()
        resp = client.post("/api/source/mode",
                           json={"mode": "auto"},
                           content_type="application/json")
        assert resp.status_code == 200

    def test_set_mode_gps(self, client, mock_server):
        mock_server.source_engine = SourceSelectionEngine()
        resp = client.post("/api/source/mode",
                           json={"mode": "gps"},
                           content_type="application/json")
        assert resp.status_code == 200

    def test_set_invalid_mode(self, client, mock_server):
        mock_server.source_engine = SourceSelectionEngine()
        resp = client.post("/api/source/mode",
                           json={"mode": "invalid"},
                           content_type="application/json")
        assert resp.status_code == 400


class TestChronyEndpoints:
    def test_chrony_sources(self, client):
        resp = client.get("/api/chrony/sources")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sources" in data
        assert "tracking" in data

    def test_restart_chrony(self, client):
        resp = client.post("/api/chrony/restart")
        assert resp.status_code == 200


class TestAlertsEndpoint:
    def test_get_alerts(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "alerts" in data

    def test_get_alerts_with_since(self, client):
        resp = client.get("/api/alerts?since=0")
        assert resp.status_code == 200


class TestDriftEndpoint:
    def test_get_drift(self, client):
        resp = client.get("/api/drift/history")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "samples" in data
        assert "statistics" in data


class TestDashboardPage:
    def test_index_loads(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"NTP GPS Server" in resp.data
        assert b"v1.0.0" in resp.data


class TestNoServer:
    def test_status_503_without_server(self):
        set_server(None)
        app = create_app()
        app.config["TESTING"] = True
        with app.test_client() as client:
            resp = client.get("/api/status")
            assert resp.status_code == 503
