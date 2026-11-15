"""SI preparation is independent of register representation."""
from random import Random

import pytest

from reflight.sensors import MS5611, SensorError
from reflight.sensors.base import SimulationInputs


class Recorder(SimulationInputs):
    input_groups = (("accel_x", "accel_y", "accel_z"), ("temperature",))
    default_inputs = dict(accel_x=0., accel_y=0., accel_z=0., temperature=293.15)

    def publish_physical_sample(self, values):
        self.sample = values


def test_preparation_retains_unadjusted_groups_and_does_not_round():
    sensor = Recorder()
    rng = Random(0)
    sensor.update_measurements(dict(accel_x=1.25, accel_y=-2., accel_z=3.),
                               bias={'accel_x': .5}, noise={}, rng=rng)
    assert sensor.sample == dict(accel_x=1.75, accel_y=-2., accel_z=3., temperature=293.15)
    for _ in range(2):
        sensor.update_measurements({'temperature': 280.25}, bias={'accel_x': .5}, noise={}, rng=rng)
        assert sensor.sample == dict(accel_x=1.75, accel_y=-2., accel_z=3., temperature=280.25)


def test_noise_order_and_si_units():
    sensor = Recorder()
    noise = dict(accel_z=.3, accel_x=.1)
    expected = Random(21)
    sensor.update_measurements({}, bias={'accel_y': .25}, noise=noise, rng=Random(21))
    assert sensor.sample['accel_x'] == expected.gauss(0, .1)
    assert sensor.sample['accel_z'] == expected.gauss(0, .3)
    assert sensor.sample['accel_y'] == .25


@pytest.mark.parametrize('values,bias,noise', [
    ({'temperature': float('nan')}, {}, {}),
    ({'temperature': float('inf')}, {}, {}),
    ({'temperature': 1e308}, {'temperature': 1e308}, {}),
    ({}, {'temperature': float('inf')}, {}),
    ({}, {}, {'temperature': -1}),
    ({}, {}, {'unknown': 1}),
    ({'accel_x': 1}, {}, {}),
])
def test_invalid_preparation(values, bias, noise):
    with pytest.raises(SensorError):
        Recorder().update_measurements(values, bias=bias, noise=noise, rng=Random(0))


def test_ms_physical_keys_replace_adc_keys():
    MS5611.validate_input_keys({'pressure', 'temperature'})
    with pytest.raises(SensorError, match='unsupported input keys'):
        MS5611.validate_input_keys({'d1', 'd2'})




def test_noise_overflow_is_rejected():
    with pytest.raises(SensorError, match='finite'):
        Recorder().update_measurements({'temperature': 1e308}, bias={},
                                       noise={'temperature': 1e308}, rng=Random(0))
