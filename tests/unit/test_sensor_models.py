"""Local datasheets: ICM DS-000577 §§16–17; MS5611 pp10–11/CRC appendix;
BMI088 §§3,5.2,5.4,6.1; BMP581 §§5.1,5.5,7.
"""
from __future__ import annotations

import pytest

from reflight.buses import SpiBus
from reflight.sensors import (ICM45686, MS5611, BMI088Accel, BMI088Gyro,
                              BMP581, SensorError, make_sensor)


def frame(device, *mosi: int) -> bytes:
    device.select()
    result = bytes(device.exchange_byte(value) for value in mosi)
    device.deselect()
    return result


def test_icm_identity_big_endian_axes_config_reset_and_independence() -> None:
    a, b = ICM45686(), ICM45686()
    assert frame(a, 0xF2, 0) == b"\x00\xe9"
    assert frame(a, 0x9B, 0) == b"\x00\x06"
    frame(a, 0x10, 0x0F)
    frame(a, 0x1B, 0x16)
    assert frame(a, 0x90, 0) == b"\x00\x0f"
    assert frame(b, 0x90, 0) == b"\x00\x00"
    a.set_raw_sample(accel=(0x1234, -2, 3), gyro=(-4, 5, -6))
    assert frame(a, 0x80, *([0] * 12)) == bytes.fromhex(
        "00 12 34 ff fe 00 03 ff fc 00 05 ff fa")
    frame(a, 0x7F, 0x02)
    assert frame(a, 0x80, 0, 0) == b"\x00\x00\x00"
    with pytest.raises(SensorError, match="unsupported"):
        frame(a, 0xC0, 0)


def test_ms5611_prom_crc_conversion_readiness_and_reset() -> None:
    a, b = MS5611(), MS5611()
    assert frame(a, 0xA2, 0, 0) == bytes.fromhex("00 9c bf")  # C1=40127
    assert frame(a, 0xAC, 0, 0) == bytes.fromhex("00 6e 98")  # C6=28312
    from reflight.sensors.models import _ms_crc4
    assert _ms_crc4(a.prom) == a.prom[7] & 0x0F
    a.set_raw_sample(d1=0x123456, d2=0xABCDEF)
    frame(a, 0x48)
    assert frame(a, 0x00, 0, 0, 0) == b"\x00\x00\x00\x00"
    a.tick(2)
    assert frame(a, 0x00, 0, 0, 0) == bytes.fromhex("00 12 34 56")
    assert frame(a, 0x00, 0, 0, 0) == b"\x00\x00\x00\x00"
    frame(a, 0x58)
    a.tick(2)
    assert frame(a, 0x00, 0, 0, 0) == bytes.fromhex("00 ab cd ef")
    assert frame(b, 0xA2, 0, 0) == bytes.fromhex("00 9c bf")
    frame(a, 0x1E)
    assert frame(a, 0x00, 0, 0, 0) == b"\x00\x00\x00\x00"
    with pytest.raises(SensorError, match="unsupported"):
        frame(a, 0xFF)


def test_bmi088_separate_cs_startup_framing_and_little_endian() -> None:
    accel, gyro = BMI088Accel(), BMI088Gyro()
    assert frame(accel, 0x80, 0, 0) == b"\x00\x00\x00"
    assert frame(accel, 0x80, 0, 0) == b"\x00\x00\x1e"
    assert frame(gyro, 0x80, 0) == b"\x00\x0f"
    frame(accel, 0x7D, 4)
    frame(gyro, 0x0F, 2)
    assert frame(accel, 0xFD, 0, 0) == b"\x00\x00\x04"
    assert frame(gyro, 0x8F, 0) == b"\x00\x02"
    accel.set_raw_sample(accel=(0x1234, -2, 3))
    gyro.set_raw_sample(gyro=(-4, 5, -6))
    assert frame(accel, 0x92, *([0] * 7)) == bytes.fromhex(
        "00 00 34 12 fe ff 03 00")
    assert frame(gyro, 0x82, *([0] * 6)) == bytes.fromhex(
        "00 fc ff 05 00 fa ff")
    frame(accel, 0x7E, 0xB6)
    assert frame(accel, 0x80, 0, 0) == b"\x00\x00\x00"
    assert frame(accel, 0x80, 0, 0) == b"\x00\x00\x1e"


def test_bmp581_startup_status_config_and_changed_sample() -> None:
    bmp = BMP581()
    assert frame(bmp, 0x81, 0) == b"\x00\x00"
    assert frame(bmp, 0x81, 0) == b"\x00\x50"
    assert frame(bmp, 0xA8, 0) == b"\x00\x02"
    frame(bmp, 0x36, 0x40)
    frame(bmp, 0x37, 0x71)
    frame(bmp, 0x36, 0x40, 0x37, 0x71)  # BMP581 §5.5.2 address/data pairs.
    assert frame(bmp, 0xB6, 0) == b"\x00\x40"
    bmp.set_raw_sample(temperature=0x123456, pressure=0xABCDEF)
    assert frame(bmp, 0x9D, *([0] * 6)) == bytes.fromhex(
        "00 56 34 12 ef cd ab")
    assert frame(bmp, 0xA7, 0) == b"\x00\x10"
    bmp.tick(2)
    assert frame(bmp, 0xA7, 0) == b"\x00\x11"
    bmp.set_raw_sample(pressure=0x010203)
    bmp.tick(2)
    assert frame(bmp, 0xA0, 0, 0, 0) == bytes.fromhex("00 03 02 01")
    frame(bmp, 0x7E, 0xB6)
    assert frame(bmp, 0x81, 0) == b"\x00\x00"
    assert frame(bmp, 0x81, 0) == b"\x00\x50"


def test_registry_bus_injection_and_errors() -> None:
    bus = SpiBus()
    bus.attach("PA4", make_sensor("ICM45686"))
    bus.inject_raw("PA4", accel=(1, 2, 3))
    bus.set_cs("PA4", 0)
    assert [bus.exchange_byte(x) for x in (0x80, 0, 0)] == [0, 0, 1]
    bus.set_cs("PA4", 1)
    with pytest.raises(SensorError, match="unsupported sensor model"):
        make_sensor("Unknown")
    with pytest.raises(SensorError, match="raw axes"):
        bus.inject_raw("PA4", accel=(32768, 0, 0))
