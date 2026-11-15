"""I²C address registry; controller integration is not yet verified."""

from __future__ import annotations

from typing import Protocol


class I2cError(ValueError):
    """Invalid I²C address or unsupported runtime operation."""


class I2cDevice(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, count: int) -> bytes: ...


class I2cBus:
    def __init__(self, name: str = "I2C1") -> None:
        self.name = name
        self.devices: dict[int, I2cDevice] = {}

    def attach(self, address: int, device: I2cDevice) -> None:
        if not 0x08 <= address <= 0x77:
            raise I2cError(f"invalid 7-bit I2C address 0x{address:x}")
        if address in self.devices:
            raise I2cError(f"duplicate I2C address 0x{address:02x}")
        self.devices[address] = device

    def transact(self, address: int, write: bytes, read_count: int) -> bytes:
        raise I2cError(f"{self.name} runtime controller transaction is not verified in Qiling 1.4.6")
