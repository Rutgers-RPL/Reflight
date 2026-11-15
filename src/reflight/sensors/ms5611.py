"""MS5611 command, conversion, and simulation model."""

from __future__ import annotations

from .base import SensorError, SimulationInputs


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


class MS5611(SimulationInputs):
    """TE MS5611 §§SPI commands, conversion/PROM sequences, CRC-4 appendix."""

    input_groups = (("d1",), ("d2",))
    sample_rate_hz = 50
    raw_range = (0, 0xFFFFFF)
    default_inputs = {"d1": 9085466, "d2": 8569150}

    def publish_sample(self, values: dict[str, int]) -> None:
        self.set_raw_sample(**values)

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
