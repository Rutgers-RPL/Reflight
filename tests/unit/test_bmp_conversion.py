"""BMP581 Q6 Pa/Q16 Celsius golden bit patterns, independent of encoder."""
from random import Random

import pytest

from reflight.sensors import BMP581, SensorError


def frame(sensor, *values):
    sensor.select()
    data = bytes(sensor.exchange_byte(v) for v in values)
    sensor.deselect()
    return data


def update(sensor, **values):
    sensor.update_measurements(values, bias={}, noise={}, rng=Random(0))


@pytest.mark.parametrize('pressure,temperature,golden', [
    (101325, 293.15, '00 00 14 40 f3 62'),
    (100000, 263.15, '00 00 f6 00 a8 61'),
    (30000, 233.15, '00 00 d8 00 4c 1d'),
    (125000, 358.15, '00 00 55 00 12 7a'),
    (100000.015625, 273.15001525878904, '01 00 00 01 a8 61'),
])
def test_bmp_golden_spi_and_readiness(pressure, temperature, golden):
    sensor = BMP581()
    frame(sensor, 0x81, 0)
    frame(sensor, 0x36, 0x40, 0x37, 0x71)
    update(sensor, pressure=pressure, temperature=temperature)
    assert frame(sensor, 0x9d, *([0]*6))[1:] == bytes.fromhex(golden)
    assert sensor.registers[0x27] & 1 == 0
    sensor.tick(2)
    assert sensor.registers[0x27] & 1
    assert (sensor.registers[0x36], sensor.registers[0x37]) == (0x40, 0x71)


def test_independent_channels_and_raw_api():
    sensor = BMP581()
    update(sensor, temperature=263.15)
    update(sensor, pressure=90000)
    frame(sensor, 0x81, 0)
    assert frame(sensor, 0x9d, 0, 0, 0)[1:] == bytes.fromhex('00 00 f6')
    sensor.set_raw_sample(temperature=0xabcdef, pressure=0xffffff)
    assert frame(sensor, 0x9d, *([0]*6))[1:] == bytes.fromhex('ef cd ab ff ff ff')
    update(sensor, pressure=100000)
    assert frame(sensor, 0x9d, 0, 0, 0)[1:] == bytes.fromhex('00 00 f6')


@pytest.mark.parametrize('values', [
    {'pressure': 29999.9}, {'pressure': 125000.1}, {'temperature': 233.14},
    {'temperature': 358.16}, {'pressure': float('inf')}, {'temperature': float('nan')},
])
def test_invalid_bmp_sample(values):
    sensor = BMP581()
    old = sensor.registers.copy()
    with pytest.raises(SensorError):
        update(sensor, **values)
    assert sensor.registers == old
