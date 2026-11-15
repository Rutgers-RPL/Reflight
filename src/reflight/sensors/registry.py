"""Board model names to the explicit supported external SPI endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from reflight.buses import SpiBus
from .base import SensorError
from .icm45686 import ICM45686
from .ms5611 import MS5611
from .bmi088_accel import BMI088Accel
from .bmi088_gyro import BMI088Gyro
from .bmp581 import BMP581

if TYPE_CHECKING:
    from reflight.config import BoardConfig

_MODELS = {"ICM45686": ICM45686, "MS5611": MS5611,
           "BMI088Accel": BMI088Accel, "BMI088Gyro": BMI088Gyro,
           "BMP581": BMP581}


def make_sensor(model: str):
    try:
        return _MODELS[model]()
    except KeyError as exc:
        raise SensorError(f"unsupported sensor model {model}; supported: {', '.join(_MODELS)}") from exc


def build_spi_bus(board: BoardConfig) -> SpiBus | None:
    """Compatibility wrapper for existing sensor-only library callers."""
    from reflight.devices import build_spi_bus as build_devices
    return build_devices(board)
