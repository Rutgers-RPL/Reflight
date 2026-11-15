"""Validated board configuration for the Phase 2 runner."""

from .board import (BoardConfig, BusConfig, CaptureConfig, ConfigError,
                    DeviceConfig, TelemetryConfig, load_board)

__all__ = ["BoardConfig", "BusConfig", "CaptureConfig", "ConfigError",
           "DeviceConfig", "TelemetryConfig", "load_board"]
