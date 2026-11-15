"""Byte-oriented four-wire SPI with firmware-controlled active-low CS."""

from __future__ import annotations

from typing import Protocol


class SpiError(ValueError):
    """Invalid SPI wiring, selection, or transfer."""


class SpiDevice(Protocol):
    def select(self) -> None: ...
    def deselect(self) -> None: ...
    def exchange_byte(self, mosi: int) -> int: ...


class SpiBus:
    def __init__(self, name: str = "SPI1") -> None:
        self.name = name
        self.devices: dict[str, SpiDevice] = {}
        self.levels: dict[str, int] = {}

    def attach(self, cs: str, device: SpiDevice) -> None:
        if cs in self.devices:
            raise SpiError(f"duplicate chip select {cs} on {self.name}")
        self.devices[cs] = device
        self.levels[cs] = 1

    def set_cs(self, cs: str, level: int) -> None:
        if cs not in self.devices:
            raise SpiError(f"unknown chip select {cs} on {self.name}")
        if level not in (0, 1):
            raise SpiError("chip select level must be 0 or 1")
        old = self.levels[cs]
        if old == level:
            return
        self.levels[cs] = level
        if level == 0:
            self.devices[cs].select()
        else:
            self.devices[cs].deselect()

    def exchange_byte(self, mosi: int) -> int:
        if not 0 <= mosi <= 0xFF:
            raise SpiError("SPI1 supports only 8-bit frames")
        selected = [cs for cs, level in self.levels.items() if level == 0]
        if len(selected) != 1:
            raise SpiError(f"{self.name} transfer requires exactly one selected device; selected: {selected}")
        miso = self.devices[selected[0]].exchange_byte(mosi)
        if not 0 <= miso <= 0xFF:
            raise SpiError("SPI device returned a non-byte response")
        return miso

    def tick(self, count: int = 1) -> None:
        if count < 0:
            raise SpiError("logical tick count must be nonnegative")
        for device in self.devices.values():
            advance = getattr(device, "tick", None)
            if advance is not None:
                advance(count)

    def inject_raw(self, cs: str, **sample: object) -> None:
        """Test-only raw sample injection by configured CS, independent of Qiling."""
        if cs not in self.devices:
            raise SpiError(f"unknown chip select {cs} on {self.name}")
        setter = getattr(self.devices[cs], "set_raw_sample", None)
        if setter is None:
            raise SpiError(f"device on {cs} has no raw sample API")
        setter(**sample)
