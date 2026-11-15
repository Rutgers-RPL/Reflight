"""MS5611 command, conversion, and simulation model."""

from __future__ import annotations

import math

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


def _trunc_div(numerator: int, denominator: int) -> int:
    """Integer division with C-style truncation toward zero, including negatives."""
    return (1 if numerator >= 0 else -1) * (abs(numerator) // denominator)


def _compensation(d2: int, prom: list[int]) -> tuple[int, int, int]:
    """Return centi-Celsius, offset, sensitivity; MS5611 datasheet pp8–9.

    Every division truncates toward zero. Corrections use first-order TEMP,
    including the additional below −15°C branch, before correcting TEMP itself.
    """
    _, c1, c2, c3, c4, c5, c6, _ = prom
    dt = d2 - c5 * 256
    temperature = 2000 + _trunc_div(dt * c6, 2**23)
    offset = c2 * 2**16 + _trunc_div(c4 * dt, 2**7)
    sensitivity = c1 * 2**15 + _trunc_div(c3 * dt, 2**8)
    if temperature < 2000:
        t2 = _trunc_div(dt * dt, 2**31)
        off2 = _trunc_div(5 * (temperature - 2000)**2, 2)
        sens2 = _trunc_div(5 * (temperature - 2000)**2, 4)
        if temperature < -1500:
            off2 += 7 * (temperature + 1500)**2
            sens2 += _trunc_div(11 * (temperature + 1500)**2, 2)
        temperature -= t2
        offset -= off2
        sensitivity -= sens2
    return temperature, offset, sensitivity


def _nearest_adc(target: int, evaluate) -> int:
    """Bounded monotone integer search; equal output error selects lower ADC.

    At most 24 bisections over the unsigned 24-bit ADC domain. An exact target
    selects its first ADC code; no unbounded root-finding or extrapolation.
    """
    low, high = 0, 0xFFFFFF
    if not evaluate(low) <= target <= evaluate(high):
        raise SensorError("MS5611 target cannot be represented with this PROM")
    while low < high:
        middle = (low + high) // 2
        if evaluate(middle) < target:
            low = middle + 1
        else:
            high = middle
    return min({low, max(0, low - 1)}, key=lambda adc: (abs(evaluate(adc) - target), adc))


class MS5611(SimulationInputs):
    """TE MS5611 §§SPI commands, conversion/PROM sequences, CRC-4 appendix."""

    input_groups = (("pressure",), ("temperature",))
    sample_rate_hz = 50
    default_inputs = {"pressure": 101325.0, "temperature": 293.15}

    def publish_physical_sample(self, values: dict[str, float]) -> None:
        # ENG_DS_MS5611-01BA03_B3.md pp8–9: inverse of integer firmware
        # compensation, using the same coefficients returned by PROM reads.
        pressure, temperature = values["pressure"], values["temperature"]
        if not math.isfinite(pressure) or not 1000 <= pressure <= 120000:
            raise SensorError("MS5611 pressure must be within 1000–120000 Pa")
        if not math.isfinite(temperature) or not 233.15 <= temperature <= 358.15:
            raise SensorError("MS5611 temperature must be within 233.15–358.15 K")
        if len(self.prom) != 8 or any(type(c) is not int or not 0 <= c <= 0xFFFF
                                      for c in self.prom[1:7]):
            raise SensorError("MS5611 invalid PROM calibration coefficients")
        target_t = round((temperature - 273.15) * 100)
        target_p = round(pressure)
        d2 = _nearest_adc(target_t, lambda adc: _compensation(adc, self.prom)[0])
        actual_t, offset, sensitivity = _compensation(d2, self.prom)
        if sensitivity <= 0:
            raise SensorError("MS5611 PROM gives nonpositive compensated sensitivity")

        def compensate_pressure(adc):
            return _trunc_div(_trunc_div(adc * sensitivity, 2**21) - offset, 2**15)

        d1 = _nearest_adc(target_p, compensate_pressure)
        if abs(actual_t / 100 + 273.15 - temperature) > .010000001 \
                or abs(compensate_pressure(d1) - pressure) > 1:
            raise SensorError("MS5611 target cannot be represented within 0.01 K / 1 Pa")
        self.set_raw_sample(d1=d1, d2=d2)

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
