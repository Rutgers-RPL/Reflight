"""Small external-device factory; flash is never a sensor input endpoint."""

from typing import TYPE_CHECKING

from reflight.buses import SpiBus
from reflight.flash import W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH
from reflight.sensors import SensorError, make_sensor

if TYPE_CHECKING:
    from reflight.config import BoardConfig

FLASH_MODELS = {"W25N01KV": W25N01KV, "GD5F1GQ5UExxH": GD5F1GQ5UExxH,
                "GD5F1GQ5RExxH": GD5F1GQ5RExxH}


def is_flash_model(model: str) -> bool:
    return model in FLASH_MODELS


def make_device(model: str):
    if is_flash_model(model):
        return FLASH_MODELS[model]()
    return make_sensor(model)  # Unknown names still fail explicitly.


def build_spi_bus(board: "BoardConfig") -> SpiBus | None:
    bus = None
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
            bus.attach(device.cs, make_device(device.model))
    return bus
