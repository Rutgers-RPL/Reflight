"""Golden register values from local TDK/Bosch sensitivity tables."""
import math
from random import Random

import pytest

from reflight.sensors import ICM45686, BMI088Accel, BMI088Gyro, SensorError
from reflight.simulation.clock import VirtualClock


def frame(sensor, *values):
    sensor.select()
    result = bytes(sensor.exchange_byte(v) for v in values)
    sensor.deselect()
    return result


def publish(sensor, **values):
    sensor.update_measurements(values, bias={}, noise={}, rng=Random(0))


def axes(sensor, base, endian, dummy=0):
    data = frame(sensor, base | 0x80, *([0] * (6 + dummy)))[1 + dummy:]
    return tuple(int.from_bytes(data[i:i+2], endian, signed=True) for i in (0, 2, 4))


@pytest.mark.parametrize('setting,expected', enumerate([1024, 2048, 4096, 8192, 16384]))
def test_icm_accel_ranges(setting, expected):
    sensor = ICM45686()
    frame(sensor, 0x1b, setting << 4 | 6)
    publish(sensor, accel_x=9.80665, accel_y=-9.80665, accel_z=0)
    assert axes(sensor, 0, 'big') == (expected, -expected, 0)
    assert sensor.registers[0x19] & 4


@pytest.mark.parametrize('setting,expected', enumerate([82, 164, 328, 655, 1310, 2620, 5243, 10486, 20972]))
def test_icm_gyro_ranges(setting, expected):
    sensor = ICM45686()
    frame(sensor, 0x1c, setting << 4 | 6)
    publish(sensor, gyro_x=math.pi/18, gyro_y=-math.pi/18, gyro_z=0)
    assert axes(sensor, 6, 'big') == (expected, -expected, 0)


@pytest.mark.parametrize('setting,expected', enumerate([10923, 5461, 2731, 1365]))
def test_bmi_accel_exact_equation(setting, expected):
    sensor = BMI088Accel()
    frame(sensor, 0x80, 0, 0)
    frame(sensor, 0x41, setting)
    publish(sensor, accel_x=9.80665, accel_y=-9.80665, accel_z=0)
    assert axes(sensor, 0x12, 'little', 1) == (expected, -expected, 0)
    assert sensor.registers[0x1d] == 0x80


@pytest.mark.parametrize('setting,expected', enumerate([8192, 16384, 32767, 32767, 32767]))
def test_bmi_gyro_ranges_and_full_scale(setting, expected):
    sensor = BMI088Gyro()
    frame(sensor, 0xf, setting)
    publish(sensor, gyro_x=math.radians(500), gyro_y=-math.radians(500), gyro_z=0)
    assert axes(sensor, 2, 'little') == (expected, -32768 if setting >= 2 else -expected, 0)


@pytest.mark.parametrize('factory,group,base,endian,dummy', [
    (ICM45686, 'accel', 0, 'big', 0),
    (ICM45686, 'gyro', 6, 'big', 0),
    (BMI088Accel, 'accel', 0x12, 'little', 1),
    (BMI088Gyro, 'gyro', 2, 'little', 0),
])
def test_extreme_finite_inputs_saturate(factory, group, base, endian, dummy):
    sensor = factory()
    if dummy:
        frame(sensor, 0x80, 0, 0)
    publish(sensor, **{f'{group}_x': 1e308, f'{group}_y': -1e308, f'{group}_z': 0})
    assert axes(sensor, base, endian, dummy) == (32767, -32768, 0)


def test_quantization_ties_even():
    sensor = ICM45686()
    unit = 9.80665 / 1024
    publish(sensor, accel_x=2.5*unit, accel_y=1.5*unit, accel_z=-2.5*unit)
    assert axes(sensor, 0, 'big') == (2, 2, -2)


@pytest.mark.parametrize('factory,reg,invalid', [
    (ICM45686, 0x1b, 0x56), (ICM45686, 0x1c, 0x96),
    (BMI088Accel, 0x41, 4), (BMI088Gyro, 0xf, 5),
])
def test_invalid_range_write(factory, reg, invalid):
    sensor = factory()
    if sensor.startup_dummy:
        frame(sensor, 0x80, 0, 0)
    with pytest.raises(SensorError, match='full scale'):
        frame(sensor, reg, invalid)


@pytest.mark.parametrize('factory,reg,new,base,endian,dummy,group,initial,after', [
    (ICM45686, 0x1b, 0x16, 0, 'big', 0, 'accel', 1024, 2048),
    (ICM45686, 0x1c, 0x16, 6, 'big', 0, 'gyro', 82, 164),
    (BMI088Accel, 0x41, 0, 0x12, 'little', 1, 'accel', 5461, 10923),
    (BMI088Gyro, 0xf, 1, 2, 'little', 0, 'gyro', 164, 328),
])
def test_range_changes_only_next_scheduled_sample(factory, reg, new, base, endian, dummy, group, initial, after):
    sensor = factory()
    if sensor.startup_dummy:
        frame(sensor, 0x80, 0, 0)
    value = 9.80665 if group == 'accel' else math.pi/18
    sample = {f'{group}_{axis}': value if axis == 'x' else 0 for axis in 'xyz'}
    clock = VirtualClock(1000, .03, 'duration')
    clock.schedule('sensor', 100, lambda t: publish(sensor, **sample))
    clock.advance(0)
    assert axes(sensor, base, endian, dummy)[0] == initial
    frame(sensor, reg, new)
    clock.advance(.009)
    assert axes(sensor, base, endian, dummy)[0] == initial
    clock.advance(.01)
    assert axes(sensor, base, endian, dummy)[0] == after


@pytest.mark.parametrize('setting,expected', enumerate([164, 328, 655, 1311, 2621]))
def test_bmi_gyro_nonclipped_sensitivity(setting, expected):
    sensor = BMI088Gyro()
    frame(sensor, 0xf, setting)
    publish(sensor, gyro_x=math.pi/18, gyro_y=-math.pi/18, gyro_z=0)
    assert axes(sensor, 2, 'little') == (expected, -expected, 0)
