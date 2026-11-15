"""BMI088 accelerometer raw register and simulation model."""

from __future__ import annotations

from .base import SensorError, _RegisterSensor, _axes


class BMI088Accel(_RegisterSensor):
    """Bosch BMI088 §§3, 5.2, 6.1.2: startup CS edge and read dummy byte."""

    startup_dummy = True
    read_dummy = True
    input_groups = (("accel_x", "accel_y", "accel_z"),)
    sample_rate_hz = 200
    default_inputs = {f"accel_{axis}": 0 for axis in "xyz"}

    def publish_physical_sample(self, values: dict[str, float]) -> None:
        # bst-bmi088-ds001.md §5.3.4 p27, §5.3.11 p30:
        # g = raw / 32768 * 2**(ACC_RANGE + 1) * 1.5.
        setting = self.registers[0x41]
        if setting not in range(4):
            raise SensorError("unsupported BMI088Accel full scale")
        full_scale_g = 3 * 2**setting
        self.publish_sample({key: round(max(-32768, min(32767,
            value / 9.80665 / full_scale_g * 32768))) for key, value in values.items()})

    def publish_sample(self, values: dict[str, int]) -> None:
        self.set_raw_sample(accel=tuple(values[f"accel_{axis}"] for axis in "xyz"))

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
            if address == 0x41 and value not in range(4):
                raise SensorError("unsupported BMI088Accel full scale")
            self.registers[address] = value
        else:
            super().write_register(address, value)
