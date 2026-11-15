"""Scheduled raw samples retain device semantics, seeded noise, and defaults."""
from random import Random
from pathlib import Path

import pytest

from reflight.config import load_board
from reflight.config.scenario import load_scenario
from reflight.sensors import make_sensor, build_spi_bus
from reflight.simulation.playback import build_simulation

ROOT = Path(__file__).parents[2]


def frame(sensor, *values):
    sensor.select()
    result = bytes(sensor.exchange_byte(v) for v in values)
    sensor.deselect()
    return result


def test_mixed_sources_latched_independent_rates():
    board = load_board(ROOT / 'example-configs/simulated-flight-board.yaml')
    cfg = load_scenario(ROOT / 'example-configs/raw-flight-scenario.yaml', board)
    bus = build_spi_bus(board)
    clock = build_simulation(cfg, bus)
    icm, bmi, ms = (bus.devices[pin] for pin in ('PA4', 'PA6', 'PA5'))
    frame(bmi, 0x80, 0, 0)  # select SPI mode
    clock.advance(0)
    def x(sensor, addr, dummy=False):
        data = frame(sensor, addr, *([0] * (3 if dummy else 2)))
        return int.from_bytes(data[-2:], 'little' if dummy else 'big', signed=True)
    assert x(icm, 0x80) == x(bmi, 0x92, True) == 10
    for _ in range(3):
        clock.advance(.004)
        assert x(icm, 0x80) == x(bmi, 0x92, True) == 10
    clock.advance(.005)
    assert x(icm, 0x80) == 10
    assert x(bmi, 0x92, True) == 15
    clock.advance(.01)
    assert x(icm, 0x80) == x(bmi, 0x92, True) == 20
    # Conversion consumption/readiness is still independent of sample scheduling.
    frame(ms, 0x48)
    bus.tick(2)
    assert int.from_bytes(frame(ms, 0, 0, 0, 0)[1:], 'big') == 9085466
    assert frame(ms, 0, 0, 0, 0) == bytes(4)


def test_noise_seed_bias_isolation_no_accumulation():
    def run(seed):
        sensor = make_sensor('ICM45686')
        rng = Random(seed)
        results = []
        for _ in range(8):
            sensor.update_measurements({}, bias={'accel_y': 10}, noise={'accel_x': 5}, rng=rng)
            results.append(frame(sensor, 0x80, *([0] * 12))[1:])
        return results
    assert run(0) == run(0)
    assert run(0) != run(1)
    assert len(set(run(0))) > 1
    assert all(row[2:] == bytes.fromhex('00 0a 00 00 00 00 00 00 00 00') for row in run(0))


@pytest.mark.parametrize('model, values, bias, expected', [
    ('ICM45686', {'accel_x': 2.5, 'accel_y': 3.5, 'accel_z': -40000}, {}, (2, 4, -32768)),
    ('BMI088Accel', {'accel_x': 40000, 'accel_y': -3.5, 'accel_z': -2.5}, {}, (32767, -4, -2)),
    ('BMI088Gyro', {}, {'gyro_x': 40000}, (32767, 0, 0)),
])
def test_signed_rounding_clamping(model, values, bias, expected):
    sensor = make_sensor(model)
    sensor.update_measurements(values, bias=bias, noise={}, rng=Random(0))
    if model == 'ICM45686':
        raw = frame(sensor, 0x80, *([0] * 6))[1:]
        endian = 'big'
    elif model == 'BMI088Accel':
        frame(sensor, 0x80, 0, 0)
        raw = frame(sensor, 0x92, *([0] * 7))[2:]
        endian = 'little'
    else:
        raw = frame(sensor, 0x82, *([0] * 6))[1:]
        endian = 'little'
    assert tuple(int.from_bytes(raw[i:i+2], endian, signed=True) for i in (0, 2, 4)) == expected


def test_unsigned_clamp_and_bmp_readiness():
    for name, keys in [('MS5611', ('d1', 'd2')), ('BMP581', ('temperature', 'pressure'))]:
        sensor = make_sensor(name)
        sensor.update_measurements({keys[0]: -10, keys[1]: 2**30}, bias={}, noise={}, rng=Random(0))
        if name == 'MS5611':
            assert (sensor.d1, sensor.d2) == (0, 0xffffff)
        else:
            assert sensor.registers[0x27] & 1 == 0
            sensor.tick(2)
            assert sensor.registers[0x27] & 1
            frame(sensor, 0x81, 0)
            assert frame(sensor, 0x9d, *([0] * 6)) == bytes.fromhex('00 00 00 00 ff ff ff')
