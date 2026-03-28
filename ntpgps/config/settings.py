"""Configuration management for NTP GPS Server.

Reads/writes YAML configuration. All settings are validated and have sensible defaults.
Configuration is stored at /etc/ntpgps/config.yaml (system) or ./config.yaml (dev).
"""

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "version": "1.0.0",
    "server": {
        "host": "0.0.0.0",
        "port": 8800,
        "debug": False,
    },
    "gps": {
        "gpsd_host": "127.0.0.1",
        "gpsd_port": 2947,
        "device": "/dev/ttyACM0",
        "min_satellites_for_valid_fix": 4,
        "max_pdop_for_valid_fix": 6.0,
        "min_signal_strength_db": 15,
        "fix_timeout_seconds": 10,
        "pps_jitter_threshold_us": 500,
        "constellations": {
            "gps": True,
            "glonass": True,
            "sbas": True,
            "galileo": False,
            "beidou": False,
            "qzss": False,
        },
    },
    "ntp": {
        "chrony_config_path": "/etc/chrony/chrony.conf",
        "chrony_socket": "/run/chrony/chronyd.sock",
        "gps_refclock_shm_unit": 0,
        "pps_refclock_shm_unit": 1,
        "gps_refid": "GPS",
        "pps_refid": "PPS",
        "gps_offset": 0.0,
        "gps_delay": 0.2,
        "gps_precision": 1e-1,
        "pps_precision": 1e-7,
        "network_servers": [
            "0.au.pool.ntp.org",
            "1.au.pool.ntp.org",
            "2.au.pool.ntp.org",
            "3.au.pool.ntp.org",
        ],
        "local_stratum": 10,
    },
    "source_selection": {
        "mode": "auto",
        "gps_loss_timeout_minutes": 15,
        "flap_hold_time_minutes": 10,
        "drift_alert_threshold_ms": 50,
        "holdover_max_minutes": 120,
        "degraded_stratum": 2,
        "holdover_stratum": 3,
    },
    "storage": {
        "data_dir": "/var/lib/ntpgps",
        "max_storage_mb": 1024,
        "drift_history_retention_hours": 168,
    },
    "alerts": {
        "drift_threshold_ms": 50,
        "pps_jitter_threshold_us": 500,
        "min_satellites_warning": 6,
        "max_alerts_retained": 500,
    },
    "display": {
        "timezone": "Australia/Canberra",
        "theme": "system",
        "refresh_interval_ms": 1000,
    },
}

_VALID_MODES = {"auto", "gps", "network"}
_VALID_THEMES = {"dark", "light", "system"}


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base, returning new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_config(config: dict) -> list[str]:
    """Validate configuration values. Returns list of error messages."""
    errors = []

    port = config.get("server", {}).get("port", 8800)
    if not isinstance(port, int) or port < 1 or port > 65535:
        errors.append(f"server.port must be 1-65535, got {port}")

    gpsd_port = config.get("gps", {}).get("gpsd_port", 2947)
    if not isinstance(gpsd_port, int) or gpsd_port < 1 or gpsd_port > 65535:
        errors.append(f"gps.gpsd_port must be 1-65535, got {gpsd_port}")

    min_sats = config.get("gps", {}).get("min_satellites_for_valid_fix", 4)
    if not isinstance(min_sats, int) or min_sats < 1 or min_sats > 50:
        errors.append(f"gps.min_satellites_for_valid_fix must be 1-50, got {min_sats}")

    max_pdop = config.get("gps", {}).get("max_pdop_for_valid_fix", 6.0)
    if not isinstance(max_pdop, (int, float)) or max_pdop <= 0:
        errors.append(f"gps.max_pdop_for_valid_fix must be > 0, got {max_pdop}")

    mode = config.get("source_selection", {}).get("mode", "auto")
    if mode not in _VALID_MODES:
        errors.append(f"source_selection.mode must be one of {_VALID_MODES}, got {mode}")

    theme = config.get("display", {}).get("theme", "system")
    if theme not in _VALID_THEMES:
        errors.append(f"display.theme must be one of {_VALID_THEMES}, got {theme}")

    timeout = config.get("source_selection", {}).get("gps_loss_timeout_minutes", 15)
    if not isinstance(timeout, (int, float)) or timeout < 1:
        errors.append(f"source_selection.gps_loss_timeout_minutes must be >= 1, got {timeout}")

    max_storage = config.get("storage", {}).get("max_storage_mb", 1024)
    if not isinstance(max_storage, (int, float)) or max_storage < 10:
        errors.append(f"storage.max_storage_mb must be >= 10, got {max_storage}")

    return errors


class Config:
    """Thread-safe configuration manager."""

    def __init__(self, config_path: str | None = None):
        self._config_path = self._resolve_path(config_path)
        self._config: dict = {}
        self.load()

    @staticmethod
    def _resolve_path(config_path: str | None) -> Path:
        if config_path:
            return Path(config_path)
        system_path = Path("/etc/ntpgps/config.yaml")
        if system_path.exists():
            return system_path
        local_path = Path("config.yaml")
        return local_path

    def load(self) -> None:
        """Load configuration from file, merging with defaults."""
        file_config = {}
        if self._config_path.exists():
            try:
                with open(self._config_path, "r") as f:
                    file_config = yaml.safe_load(f) or {}
                logger.info("Loaded configuration from %s", self._config_path)
            except (yaml.YAMLError, OSError) as e:
                logger.warning("Failed to load config from %s: %s. Using defaults.", self._config_path, e)
        else:
            logger.info("No config file at %s, using defaults", self._config_path)

        self._config = _deep_merge(DEFAULT_CONFIG, file_config)

        errors = _validate_config(self._config)
        if errors:
            for err in errors:
                logger.error("Config validation error: %s", err)
            raise ValueError(f"Configuration validation failed: {'; '.join(errors)}")

    def save(self) -> None:
        """Save current configuration to file."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w") as f:
            yaml.dump(self._config, f, default_flow_style=False, sort_keys=False)
        logger.info("Saved configuration to %s", self._config_path)

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Get a config value using dotted notation (e.g. 'gps.gpsd_port')."""
        keys = dotted_key.split(".")
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, dotted_key: str, value: Any) -> None:
        """Set a config value using dotted notation."""
        keys = dotted_key.split(".")
        target = self._config
        for key in keys[:-1]:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value

        errors = _validate_config(self._config)
        if errors:
            raise ValueError(f"Invalid value: {'; '.join(errors)}")

    @property
    def data(self) -> dict:
        """Return a deep copy of the full configuration."""
        return copy.deepcopy(self._config)

    def as_flat_dict(self) -> dict[str, Any]:
        """Return config as flat dotted-key dictionary for API responses."""
        result = {}

        def _flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, full_key)
                else:
                    result[full_key] = v

        _flatten(self._config)
        return result
