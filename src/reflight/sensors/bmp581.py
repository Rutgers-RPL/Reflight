"""BMP581 raw register and simulation model."""

from __future__ import annotations

import math

from .base import SensorError, _RegisterSensor


class BMP581(_RegisterSensor):
    """Bosch BMP581 §§5.1, 5.5, 7: startup dummy read, 24-bit little endian data."""

    startup_dummy = True
    input_groups = (("temperature",), ("pressure",))
    sample_rate_hz = 25
    default_inputs = {"temperature": 293.15, "pressure": 101325.0}

    def publish_physical_sample(self, values: dict[str, float]) -> None:
        # bst-bmp581-ds004.md §4.5 p22, §§7.11–7.16 pp56–57:
        # signed 24-bit fields, pressure Q6 Pa and temperature Q16 Celsius.
        pressure, temperature = values["pressure"], values["temperature"]
        if not math.isfinite(pressure) or not 30000 <= pressure <= 125000:
            raise SensorError("BMP581 pressure must be within 30000–125000 Pa")
        if not math.isfinite(temperature) or not 233.15 <= temperature <= 358.15:
            raise SensorError("BMP581 temperature must be within 233.15–358.15 K")
        self.set_raw_sample(pressure=round(pressure * 64),
                            temperature=round((temperature - 273.15) * 65536) & 0xFFFFFF)

    def publish_sample(self, values: dict[str, int]) -> None:
        self.set_raw_sample(**values)

    def reset(self) -> None:
        self.registers = {0x01: 0x50, 0x02: 0x32, 0x27: 0x10, 0x28: 0x02,
                          **{a: 0x7F for a in range(0x1D, 0x23)},
                          0x36: 0x00, 0x37: 0x70}
        self._ready_in = 0

    def select(self) -> None:
        super().select()
        self._write_expect_address = False

    def exchange_byte(self, mosi: int) -> int:
        # BMP581 §5.5.2: multi-write repeats address/data pairs, unlike
        # auto-incrementing reads. A fresh command byte still starts each CS.
        if self._selected and self._address is not None and not self._read \
                and self._spi_ready:
            self._clocks += 1
            if self._write_expect_address:
                if mosi & 0x80:
                    raise SensorError("BMP581 burst write requires a write address")
                self._address = mosi
                self._write_expect_address = False
            else:
                self.write_register(self._address, mosi)
                self._write_expect_address = True
            return 0
        return super().exchange_byte(mosi)

    def set_raw_sample(self, *, temperature: int | None = None,
                       pressure: int | None = None) -> None:
        """Inject exact unsigned 24-bit bit patterns (including signed temperature)."""
        for base, value in ((0x1D, temperature), (0x20, pressure)):
            if value is not None:
                if not 0 <= value <= 0xFFFFFF:
                    raise SensorError("BMP581 raw sample must be 24-bit unsigned")
                for i, byte in enumerate(value.to_bytes(3, "little")):
                    self.registers[base + i] = byte
        self.registers[0x27] &= ~1
        self._ready_in = 2

    def tick(self, count: int) -> None:
        self._ready_in = max(0, self._ready_in - count)
        if self._ready_in == 0:
            self.registers[0x27] |= 1

    def write_register(self, address: int, value: int) -> None:
        if address == 0x7E and value == 0xB6:
            self.reset()
            self._spi_ready = False
        elif address in (0x36, 0x37):
            self.registers[address] = value
        else:
            super().write_register(address, value)
