"""BMI088 gyroscope raw register and simulation model."""

from __future__ import annotations

import math

from .base import SensorError, _RegisterSensor, _axes


class BMI088Gyro(_RegisterSensor):
    """Bosch BMI088 §§5.4, 6.1.1: no dummy read byte; little-endian rates."""

    input_groups = (("gyro_x", "gyro_y", "gyro_z"),)
    sample_rate_hz = 100
    default_inputs = {f"gyro_{axis}": 0 for axis in "xyz"}

    def publish_physical_sample(self, values: dict[str, float]) -> None:
        # bst-bmi088-ds001.md §5.5.5 p39: LSB/(degree/s).
        setting = self.registers[0x0F]
        if setting not in range(5):
            raise SensorError("unsupported BMI088Gyro full scale")
        scale = (16.384, 32.768, 65.536, 131.072, 262.144)[setting]
        self.publish_sample({key: round(max(-32768, min(32767,
            math.degrees(value) * scale))) for key, value in values.items()})

    def publish_sample(self, values: dict[str, int]) -> None:
        self.set_raw_sample(gyro=tuple(values[f"gyro_{axis}"] for axis in "xyz"))

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
            if address == 0x0F and value not in range(5):
                raise SensorError("unsupported BMI088Gyro full scale")
            self.registers[address] = value
        else:
            super().write_register(address, value)
