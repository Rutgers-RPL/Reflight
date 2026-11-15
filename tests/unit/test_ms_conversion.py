"""Independent integer datasheet compensation verifies inverse ADC publication."""
from random import Random

import pytest

from reflight.sensors import MS5611, SensorError
from reflight.sensors.ms5611 import _ms_crc4


def compensate(prom, d1, d2):
    # Independent C-like integer evaluation via divmod, not production helpers.
    def div(a, b):
        quotient, remainder = divmod(a, b)
        return quotient + (a < 0 and remainder != 0)
    c1, c2, c3, c4, c5, c6 = prom[1:7]
    delta = d2 - (c5 << 8)
    temp = 2000 + div(delta*c6, 8388608)
    off = (c2 << 16) + div(c4*delta, 128)
    sens = (c1 << 15) + div(c3*delta, 256)
    if temp < 2000:
        cold = (temp-2000)**2
        extra = (temp+1500)**2 if temp < -1500 else 0
        off -= div(5*cold, 2) + 7*extra
        sens -= div(5*cold, 4) + div(11*extra, 2)
        temp -= div(delta*delta, 2147483648)
    pressure = div(div(d1*sens, 2097152) - off, 32768)
    return pressure, temp/100 + 273.15


def frame(sensor, *values):
    sensor.select()
    data = bytes(sensor.exchange_byte(v) for v in values)
    sensor.deselect()
    return data


def update(sensor, **values):
    sensor.update_measurements(values, bias={}, noise={}, rng=Random(0))


def test_independent_reference_matches_datasheet_example():
    assert compensate(MS5611().prom, 9085466, 8569150) == pytest.approx((100009, 293.22))


@pytest.mark.parametrize('changed_prom', [False, True])
@pytest.mark.parametrize('pressure,temperature', [
    (101325, 293.15), (50000, 303.15), (110000, 283.15),
    (90000, 253.15), (1000, 233.15), (120000, 358.15),
    (85000.4, 273.154), (30000, 258.15),
])
def test_inverse_with_real_prom_and_both_adc_sequences(changed_prom, pressure, temperature):
    sensor = MS5611()
    if changed_prom:
        sensor.prom[1:7] = [41000, 35000, 22000, 24000, 33000, 29000]
        sensor.prom[7] = _ms_crc4(sensor.prom)
    update(sensor, pressure=pressure, temperature=temperature)
    first = (sensor.d1, sensor.d2)
    p, t = compensate(sensor.prom, *first)
    assert abs(p-pressure) <= 1
    assert abs(t-temperature) <= .010000001
    update(sensor, pressure=pressure, temperature=temperature)
    assert (sensor.d1, sensor.d2) == first
    for cmd, expected in [(0x48, first[0]), (0x58, first[1])]:
        frame(sensor, cmd)
        assert frame(sensor, 0, 0, 0, 0) == bytes(4)
        sensor.tick(2)
        assert int.from_bytes(frame(sensor, 0, 0, 0, 0)[1:], 'big') == expected
        assert frame(sensor, 0, 0, 0, 0) == bytes(4)
    words = [int.from_bytes(frame(sensor, 0xa0+i*2, 0, 0)[1:], 'big') for i in range(8)]
    assert words == sensor.prom
    assert _ms_crc4(words) == words[7] & 15


def test_temperature_and_pressure_independent_baselines():
    sensor = MS5611()
    update(sensor, temperature=250)
    update(sensor, pressure=80000)
    assert compensate(sensor.prom, sensor.d1, sensor.d2) == pytest.approx((80000, 250))
    sensor.set_raw_sample(d1=1, d2=2)
    update(sensor, pressure=90000)
    assert compensate(sensor.prom, sensor.d1, sensor.d2) == pytest.approx((90000, 250))


@pytest.mark.parametrize('values', [
    {'pressure': 999}, {'pressure': 120001}, {'temperature': 233.14},
    {'temperature': 358.16}, {'pressure': float('nan')}, {'temperature': float('inf')},
])
def test_invalid_samples(values):
    sensor = MS5611()
    before = sensor.d1, sensor.d2
    with pytest.raises(SensorError):
        update(sensor, **values)
    assert (sensor.d1, sensor.d2) == before


@pytest.mark.parametrize('coefficients', [
    [40127, 36924, -1, 23282, 33464, 28312],
    [40127, 36924, 65536, 23282, 33464, 28312],
    [0, 36924, 23317, 23282, 33464, 28312],
    [40127, 36924, 23317, 23282, 33464, 1],  # cannot reach ambient from 20°C
    [1, 65535, 1, 1, 33464, 28312],  # pressure unreachable within 24 bits
])
def test_invalid_or_unrealizable_prom(coefficients):
    sensor = MS5611()
    sensor.prom[1:7] = coefficients
    with pytest.raises(SensorError, match='PROM|sensitivity'):
        update(sensor, temperature=300)


def test_zero_temperature_pressure_coefficients_are_valid():
    # Datasheet p8 allows unsigned 16-bit coefficients, including zero. C3/C4
    # zero simply removes their first-order temperature dependence.
    sensor = MS5611()
    sensor.prom[3:5] = [0, 0]
    sensor.prom[7] = _ms_crc4(sensor.prom)
    update(sensor, pressure=101325, temperature=253.15)
    assert compensate(sensor.prom, sensor.d1, sensor.d2) == pytest.approx((101325, 253.15))
