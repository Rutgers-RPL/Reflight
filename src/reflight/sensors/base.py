"""Shared raw input validation, sample publication, and register-oriented SPI framing."""

from __future__ import annotations

import math
from random import Random


class SensorError(ValueError):
    """Command/register outside the documented modeled subset."""


class SimulationInputs:
    """Raw measurement channels accepted by a model; groups must be complete."""

    input_groups: tuple[tuple[str, ...], ...] = ()
    raw_range = (-32768, 32767)
    default_inputs: dict[str, int] = {}

    @classmethod
    def validate_input_keys(cls, keys: set[str]) -> None:
        supported = {key for group in cls.input_groups for key in group}
        if keys - supported:
            raise SensorError(f"{cls.__name__}: unsupported input keys: {sorted(keys - supported)}")
        for group in cls.input_groups:
            if keys.intersection(group) and not set(group) <= keys:
                raise SensorError(f"{cls.__name__}: complete channel group required: {', '.join(group)}")

    def update_measurements(self, values: dict[str, float], *,
                            bias: dict[str, float], noise: dict[str, float],
                            rng: Random) -> None:
        """Latch one raw sample. Python round uses nearest integer, ties to even."""
        self.validate_input_keys(set(values))
        raw = {}
        low, high = self.raw_range
        for key, default in sorted(self.default_inputs.items()):
            value = values.get(key, default) + bias.get(key, 0)
            if noise.get(key, 0):
                value += rng.gauss(0, noise[key])
            if math.isnan(value):
                raise SensorError(f"{key}: sample became NaN")
            raw[key] = round(min(high, max(low, value)))
        self.publish_sample(raw)

    def publish_sample(self, values: dict[str, int]) -> None:
        raise NotImplementedError


def _axes(values: tuple[int, int, int]) -> tuple[int, int, int]:
    if len(values) != 3 or any(not -32768 <= x <= 32767 for x in values):
        raise SensorError("raw axes require three signed 16-bit integers")
    return values


class _RegisterSensor(SimulationInputs):
    """Common address-bit SPI frame; per-model startup and read latency vary."""

    startup_dummy = False
    read_dummy = False

    def __init__(self) -> None:
        self._spi_ready = not self.startup_dummy
        self._selected = False
        self._address: int | None = None
        self._read = False
        self._dummy_pending = False
        self._clocks = 0
        self.reset()

    def reset(self) -> None:
        self.registers: dict[int, int] = {}

    def select(self) -> None:
        if self._selected:
            raise SensorError("sensor selected twice")
        self._selected = True
        self._address = None
        self._clocks = 0
        self._dummy_pending = False

    def deselect(self) -> None:
        if not self._selected:
            raise SensorError("sensor deselected without selection")
        if self.startup_dummy and not self._spi_ready and self._read and self._clocks >= 2:
            # BMI088 §3/6.1; BMP581 §5.1: first read + rising CS selects SPI.
            self._spi_ready = True
        self._selected = False
        self._address = None

    def exchange_byte(self, mosi: int) -> int:
        if not self._selected or not 0 <= mosi <= 0xFF:
            raise SensorError("sensor transfer requires CS low and one byte")
        self._clocks += 1
        if self._address is None:
            self._address = mosi & 0x7F
            self._read = bool(mosi & 0x80)
            self._dummy_pending = self._read and self.read_dummy
            return 0
        if not self._spi_ready:
            return 0
        if self._read:
            if self._dummy_pending:
                self._dummy_pending = False
                return 0
            value = self.read_register(self._address)
            self._address += 1
            return value
        self.write_register(self._address, mosi)
        self._address += 1
        return 0

    def read_register(self, address: int) -> int:
        try:
            return self.registers[address]
        except KeyError as exc:
            raise SensorError(f"unsupported {type(self).__name__} register read 0x{address:02x}") from exc

    def write_register(self, address: int, value: int) -> None:
        raise SensorError(f"unsupported {type(self).__name__} register write 0x{address:02x}")
