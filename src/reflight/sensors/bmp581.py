"""BMP581 raw register and simulation model."""

from __future__ import annotations

from .base import SensorError, _RegisterSensor


class BMP581(_RegisterSensor):
    """Bosch BMP581 §§5.1, 5.5, 7: startup dummy read, 24-bit little endian data."""

    startup_dummy = True
    input_groups = (("temperature",), ("pressure",))
    sample_rate_hz = 25
    raw_range = (0, 0xFFFFFF)
    default_inputs = {"temperature": 0x7F7F7F, "pressure": 0x7F7F7F}

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
