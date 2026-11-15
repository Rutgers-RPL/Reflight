"""Independent exact rates, chronological catch-up, and shared event time."""
from fractions import Fraction

import pytest

from reflight.simulation.clock import VirtualClock
from reflight.sensors import make_sensor


def test_crossed_samples_and_simultaneous_order():
    clock = VirtualClock(1000, .04)
    seen = []
    for endpoint, rate in [('SPI1.PA6', 200), ('SPI1.PA4', 100)]:
        clock.schedule(endpoint, rate, lambda t, ep=endpoint: seen.append((t, ep, clock.time_s)))
    clock.advance(0)
    assert seen == [(0, 'SPI1.PA4', 0), (0, 'SPI1.PA6', 0)]
    clock.advance(.039)
    assert [t for t, ep, _ in seen if ep == 'SPI1.PA4'] == [0, .01, .02, .03]
    assert [t for t, ep, _ in seen if ep == 'SPI1.PA6'] == [i / 200 for i in range(8)]
    assert all(t == observed for t, _, observed in seen)
    assert seen == sorted(seen)
    clock.advance(1)
    assert clock.finished
    assert seen[-2:] == [(.04, 'SPI1.PA4', .04), (.04, 'SPI1.PA6', .04)]
    with pytest.raises(ValueError, match='monotonic'):
        clock.advance(.03)


def test_no_cumulative_drift():
    clock = VirtualClock(3, 1)
    seen = []
    clock.schedule('sensor', 3, seen.append)
    for n in range(4):
        clock.before_instruction(n)
    assert clock.now == Fraction(1)
    assert seen == [0, 1 / 3, 2 / 3, 1]


def test_model_rates():
    assert [make_sensor(name).sample_rate_hz for name in
            ['ICM45686', 'MS5611', 'BMI088Accel', 'BMI088Gyro', 'BMP581']] == [100, 50, 200, 100, 25]
