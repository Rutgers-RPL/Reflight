"""ICM45686 raw register and simulation model."""

from __future__ import annotations

import math

from .base import SensorError, _RegisterSensor, _axes


class ICM45686(_RegisterSensor):
    """TDK DS-000577 §§16.1, 17.1–17.12, 17.17, 17.26–27, 17.79, 17.84."""

    input_groups = (("accel_x", "accel_y", "accel_z"), ("gyro_x", "gyro_y", "gyro_z"))
    sample_rate_hz = 100
    default_inputs = {f"{group}_{axis}": 0 for group in ("accel", "gyro") for axis in "xyz"}

    def publish_physical_sample(self, values: dict[str, float]) -> None:
        # TDK_DS_000577_ICM_45686.md §§3.1–3.2 (pp18–19),
        # §§17.26–17.27 (pp79–80): published 16-bit sensitivities.
        accel_fs = self.registers[0x1B] >> 4
        gyro_fs = self.registers[0x1C] >> 4
        if accel_fs > 4 or gyro_fs > 8:
            raise SensorError("unsupported ICM45686 full scale")
        accel_scale = (1024, 2048, 4096, 8192, 16384)[accel_fs]
        gyro_scale = (8.2, 16.4, 32.8, 65.5, 131, 262, 524.3, 1048.6, 2097.2)[gyro_fs]
        raw = {}
        for axis in "xyz":
            raw[f"accel_{axis}"] = round(max(-32768, min(32767,
                values[f"accel_{axis}"] / (9.80665 / accel_scale))))
            raw[f"gyro_{axis}"] = round(max(-32768, min(32767,
                math.degrees(values[f"gyro_{axis}"]) * gyro_scale)))
        self.publish_sample(raw)

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
