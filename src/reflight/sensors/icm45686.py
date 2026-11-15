"""ICM45686 raw register and simulation model."""

from __future__ import annotations

from .base import SensorError, _RegisterSensor, _axes


class ICM45686(_RegisterSensor):
    """TDK DS-000577 §§16.1, 17.1–17.12, 17.17, 17.26–27, 17.79, 17.84."""

    input_groups = (("accel_x", "accel_y", "accel_z"), ("gyro_x", "gyro_y", "gyro_z"))
    sample_rate_hz = 100
    default_inputs = {f"{group}_{axis}": 0 for group in ("accel", "gyro") for axis in "xyz"}

    def publish_sample(self, values: dict[str, int]) -> None:
        self.set_raw_sample(accel=tuple(values[f"accel_{axis}"] for axis in "xyz"),
                            gyro=tuple(values[f"gyro_{axis}"] for axis in "xyz"))

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
