"""Strict, deterministic subsets of four local sensor datasheets.

Each model consumes one MOSI byte at a time while its active-low CS is held.
All measurements are raw register values; no physical trajectory is inferred.
"""

from __future__ import annotations


class SensorError(ValueError):
    """Command/register outside the documented modeled subset."""


def _axes(values: tuple[int, int, int]) -> tuple[int, int, int]:
    if len(values) != 3 or any(not -32768 <= x <= 32767 for x in values):
        raise SensorError("raw axes require three signed 16-bit integers")
    return values


class _RegisterSensor:
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


class ICM45686(_RegisterSensor):
    """TDK DS-000577 §§16.1, 17.1–17.12, 17.17, 17.26–27, 17.79, 17.84."""

    def reset(self) -> None:
        self.registers = {**{a: 0 for a in range(0x00, 0x0E)},
                          0x10: 0x00, 0x19: 0x00, 0x1B: 0x06, 0x1C: 0x06,
                          0x72: 0xE9, 0x7F: 0x01}

    def set_raw_sample(self, *, accel: tuple[int, int, int] | None = None,
                       gyro: tuple[int, int, int] | None = None) -> None:
        for base, axes in ((0, accel), (6, gyro)):
            if axes is not None:
                for axis, raw in enumerate(_axes(axes)):
                    value = raw & 0xFFFF
                    self.registers[base + 2 * axis] = value >> 8
                    self.registers[base + 2 * axis + 1] = value & 0xFF
        if accel is not None or gyro is not None:
            self.registers[0x19] |= 0x04

    def write_register(self, address: int, value: int) -> None:
        if address == 0x7F and value & 0x02:
            self.reset()
        elif address in (0x10, 0x1B, 0x1C):
            if address == 0x1B and ((value >> 4) > 4 or (value & 15) < 3):
                raise SensorError("unsupported ICM45686 accel full scale/ODR")
            if address == 0x1C and ((value >> 4) > 8 or (value & 15) < 3):
                raise SensorError("unsupported ICM45686 gyro full scale/ODR")
            self.registers[address] = value
        else:
            super().write_register(address, value)


def _ms_crc4(words: list[int]) -> int:
    """MS5611 datasheet Appendix, CRC-4 over 8 PROM words."""
    data = words.copy()
    data[7] &= 0xFF00
    remainder = 0
    for i in range(16):
        remainder ^= (data[i >> 1] >> (8 if i % 2 == 0 else 0)) & 0xFF
        for _ in range(8):
            remainder = ((remainder << 1) ^ (0x3000 if remainder & 0x8000 else 0)) & 0xFFFF
    return (remainder >> 12) & 0x0F


class MS5611:
    """TE MS5611 §§SPI commands, conversion/PROM sequences, CRC-4 appendix."""

    def __init__(self) -> None:
        self.prom = [0, 40127, 36924, 23317, 23282, 33464, 28312, 0]
        self.prom[7] = _ms_crc4(self.prom)
        self.d1 = 9085466
        self.d2 = 8569150
        self.reset()

    def reset(self) -> None:
        self._selected = False
        self._command: int | None = None
        self._output = b""
        self._index = 0
        self._pending: str | None = None
        self._ready_in = 0
        self._adc = 0

    def set_raw_sample(self, *, d1: int | None = None, d2: int | None = None) -> None:
        for name, value in (("d1", d1), ("d2", d2)):
            if value is not None:
                if not 0 <= value <= 0xFFFFFF:
                    raise SensorError("MS5611 ADC raw sample must be 24-bit unsigned")
                setattr(self, name, value)

    def tick(self, count: int) -> None:
        if self._pending is not None:
            self._ready_in = max(0, self._ready_in - count)
            if self._ready_in == 0:
                self._adc = self.d1 if self._pending == "d1" else self.d2
                self._pending = None

    def select(self) -> None:
        if self._selected:
            raise SensorError("MS5611 selected twice")
        self._selected = True
        self._command = None
        self._output = b""
        self._index = 0

    def deselect(self) -> None:
        if not self._selected:
            raise SensorError("MS5611 deselected without selection")
        self._selected = False

    def exchange_byte(self, mosi: int) -> int:
        if not self._selected or not 0 <= mosi <= 0xFF:
            raise SensorError("MS5611 transfer requires CS low and one byte")
        if self._command is None:
            self._command = mosi
            if mosi == 0x1E:
                self._pending = None
                self._ready_in = 0
                self._adc = 0
            elif mosi in (0x40, 0x42, 0x44, 0x46, 0x48,
                          0x50, 0x52, 0x54, 0x56, 0x58):
                if self._pending is not None:
                    raise SensorError("MS5611 conversion already in progress")
                self._pending = "d1" if mosi < 0x50 else "d2"
                self._ready_in = 2  # logical execution ticks; no physical time claim.
                self._adc = 0
            elif mosi == 0x00:
                self._output = (0 if self._pending else self._adc).to_bytes(3, "big")
                if self._pending is None:
                    self._adc = 0  # repeated ADC read returns zero, datasheet p11.
            elif 0xA0 <= mosi <= 0xAE and mosi % 2 == 0:
                self._output = self.prom[(mosi - 0xA0) // 2].to_bytes(2, "big")
            else:
                raise SensorError(f"unsupported MS5611 command 0x{mosi:02x}")
            return 0
        if self._index >= len(self._output):
            raise SensorError(f"unexpected MS5611 clock after command 0x{self._command:02x}")
        value = self._output[self._index]
        self._index += 1
        return value


class BMI088Accel(_RegisterSensor):
    """Bosch BMI088 §§3, 5.2, 6.1.2: startup CS edge and read dummy byte."""

    startup_dummy = True
    read_dummy = True

    def reset(self) -> None:
        self.registers = {0x00: 0x1E, 0x03: 0x10, 0x1D: 0,
                          **{a: 0 for a in range(0x12, 0x18)},
                          0x40: 0xA8, 0x41: 0x01, 0x7C: 0x03, 0x7D: 0x00}

    def set_raw_sample(self, *, accel: tuple[int, int, int]) -> None:
        for axis, raw in enumerate(_axes(accel)):
            value = raw & 0xFFFF
            self.registers[0x12 + axis * 2] = value & 0xFF
            self.registers[0x13 + axis * 2] = value >> 8
        self.registers[0x1D] = 0x80

    def write_register(self, address: int, value: int) -> None:
        if address == 0x7E and value == 0xB6:
            self.reset()
            self._spi_ready = False
        elif address in (0x40, 0x41, 0x7C, 0x7D):
            self.registers[address] = value
        else:
            super().write_register(address, value)


class BMI088Gyro(_RegisterSensor):
    """Bosch BMI088 §§5.4, 6.1.1: no dummy read byte; little-endian rates."""

    def reset(self) -> None:
        self.registers = {0x00: 0x0F, **{a: 0 for a in range(0x02, 0x08)},
                          0x0A: 0, 0x0F: 0, 0x10: 0x80, 0x11: 0}

    def set_raw_sample(self, *, gyro: tuple[int, int, int]) -> None:
        for axis, raw in enumerate(_axes(gyro)):
            value = raw & 0xFFFF
            self.registers[0x02 + axis * 2] = value & 0xFF
            self.registers[0x03 + axis * 2] = value >> 8
        self.registers[0x0A] = 0x80

    def write_register(self, address: int, value: int) -> None:
        if address == 0x14 and value == 0xB6:
            self.reset()
        elif address in (0x0F, 0x10, 0x11):
            self.registers[address] = value
        else:
            super().write_register(address, value)


class BMP581(_RegisterSensor):
    """Bosch BMP581 §§5.1, 5.5, 7: startup dummy read, 24-bit little endian data."""

    startup_dummy = True

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
