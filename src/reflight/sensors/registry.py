"""Board model names to the explicit supported external SPI endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from reflight.buses import SpiBus
from .models import ICM45686, MS5611, BMI088Accel, BMI088Gyro, BMP581, SensorError

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
    bus: SpiBus | None = None
    for spec in board.buses:
        if spec.kind == "i2c" and spec.devices:
            raise SensorError(f"{spec.name} runtime controller transactions are not verified in Qiling 1.4.6")
        if spec.kind != "spi" or not spec.devices:
            continue
        if spec.name != "SPI1":
            raise SensorError(f"unsupported runtime SPI controller {spec.name}; tested: SPI1")
        if bus is not None:
            raise SensorError("multiple runtime SPI buses are unsupported")
        bus = SpiBus(spec.name)
        for device in spec.devices:
            assert device.cs is not None
            bus.attach(device.cs, make_sensor(device.model))
    return bus
