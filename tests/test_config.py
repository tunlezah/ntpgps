"""Tests for configuration management."""

import os
import tempfile

import pytest
import yaml

from ntpgps.config.settings import Config, DEFAULT_CONFIG, _deep_merge, _validate_config


class TestDeepMerge:
    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"server": {"host": "0.0.0.0", "port": 8800}}
        override = {"server": {"port": 9000}}
        result = _deep_merge(base, override)
        assert result["server"]["host"] == "0.0.0.0"
        assert result["server"]["port"] == 9000

    def test_does_not_mutate_base(self):
        base = {"a": {"b": 1}}
        override = {"a": {"b": 2}}
        _deep_merge(base, override)
        assert base["a"]["b"] == 1

    def test_override_with_non_dict(self):
        base = {"a": {"b": 1}}
        override = {"a": "string"}
        result = _deep_merge(base, override)
        assert result["a"] == "string"


class TestValidateConfig:
    def test_valid_defaults(self):
        errors = _validate_config(DEFAULT_CONFIG)
        assert errors == []

    def test_invalid_port(self):
        config = _deep_merge(DEFAULT_CONFIG, {"server": {"port": 99999}})
        errors = _validate_config(config)
        assert any("port" in e for e in errors)

    def test_invalid_mode(self):
        config = _deep_merge(DEFAULT_CONFIG, {"source_selection": {"mode": "invalid"}})
        errors = _validate_config(config)
        assert any("mode" in e for e in errors)

    def test_invalid_theme(self):
        config = _deep_merge(DEFAULT_CONFIG, {"display": {"theme": "neon"}})
        errors = _validate_config(config)
        assert any("theme" in e for e in errors)

    def test_negative_pdop(self):
        config = _deep_merge(DEFAULT_CONFIG, {"gps": {"max_pdop_for_valid_fix": -1}})
        errors = _validate_config(config)
        assert any("pdop" in e for e in errors)

    def test_zero_timeout(self):
        config = _deep_merge(DEFAULT_CONFIG, {"source_selection": {"gps_loss_timeout_minutes": 0}})
        errors = _validate_config(config)
        assert any("timeout" in e for e in errors)


class TestConfig:
    def test_load_defaults(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            f.write(b"")
            path = f.name
        try:
            config = Config(path)
            assert config.get("server.port") == 8800
            assert config.get("gps.gpsd_port") == 2947
        finally:
            os.unlink(path)

    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.dump({"server": {"port": 9900}}, f)
            path = f.name
        try:
            config = Config(path)
            assert config.get("server.port") == 9900
            assert config.get("gps.gpsd_port") == 2947  # default preserved
        finally:
            os.unlink(path)

    def test_get_dotted_key(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            f.write(b"")
            path = f.name
        try:
            config = Config(path)
            assert config.get("gps.min_satellites_for_valid_fix") == 4
            assert config.get("nonexistent.key", "default") == "default"
        finally:
            os.unlink(path)

    def test_set_and_save(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        try:
            config = Config(path)
            config.set("server.port", 9999)
            assert config.get("server.port") == 9999
            config.save()

            # Reload and verify
            config2 = Config(path)
            assert config2.get("server.port") == 9999
        finally:
            os.unlink(path)

    def test_set_invalid_value_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            f.write(b"")
            path = f.name
        try:
            config = Config(path)
            with pytest.raises(ValueError):
                config.set("server.port", 99999)
        finally:
            os.unlink(path)

    def test_as_flat_dict(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            f.write(b"")
            path = f.name
        try:
            config = Config(path)
            flat = config.as_flat_dict()
            assert "server.port" in flat
            assert flat["server.port"] == 8800
            assert "gps.gpsd_host" in flat
        finally:
            os.unlink(path)

    def test_data_returns_copy(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            f.write(b"")
            path = f.name
        try:
            config = Config(path)
            d1 = config.data
            d2 = config.data
            assert d1 == d2
            d1["server"]["port"] = 1234
            assert config.get("server.port") == 8800
        finally:
            os.unlink(path)
