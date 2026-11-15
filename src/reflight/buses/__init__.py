"""Qiling-independent external bus contracts."""

from .spi import SpiBus, SpiDevice, SpiError
from .i2c import I2cBus, I2cDevice, I2cError

__all__ = ["SpiBus", "SpiDevice", "SpiError", "I2cBus", "I2cDevice", "I2cError"]
