"""Shared SI input preparation and register-oriented SPI framing."""

from __future__ import annotations

import math
from random import Random


class SensorError(ValueError):
    """Command/register outside the documented modeled subset."""


class SimulationInputs:
    """Sensor-axis SI channels; acceleration is specific force, with no added gravity."""

    input_groups: tuple[tuple[str, ...], ...] = ()
    default_inputs: dict[str, float] = {}

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
        """Prepare one SI sample, applying adjustments once in stable channel order.

        Omitted groups retain unadjusted physical inputs, never encoded counts or
        previously adjusted values. A failed publication does not advance them.
        """
        self.validate_input_keys(set(values))
        supported = set(self.default_inputs)
        for settings in (bias, noise):
            if set(settings) - supported:
                raise SensorError(f"unsupported adjustment keys: {sorted(set(settings) - supported)}")
            if any(not math.isfinite(value) for value in settings.values()):
                raise SensorError("adjustments must be finite")
        if any(value < 0 for value in noise.values()):
            raise SensorError("noise standard deviation must be nonnegative")
        inputs = {**getattr(self, "_physical_inputs", self.default_inputs), **values}
        adjusted = {}
        for key, value in sorted(inputs.items()):
            if not math.isfinite(value):
                raise SensorError(f"{key}: physical input must be finite")
            value += bias.get(key, 0)
            if noise.get(key, 0):
                value += rng.gauss(0, noise[key])
            if not math.isfinite(value):
                raise SensorError(f"{key}: adjusted sample must be finite")
            adjusted[key] = value
        self.publish_physical_sample(adjusted)
        self._physical_inputs = inputs

    def publish_physical_sample(self, values: dict[str, float]) -> None:
        """Encode a prepared SI sample inside the receiving sensor model."""
        raise NotImplementedError

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
