"""Actual compiled ARM reads SI-driven samples at all five existing endpoints."""
import math
from pathlib import Path

import yaml

from reflight.config import load_board
from reflight.config.scenario import load_scenario
from reflight.emulation.qiling_runner import run_firmware
from reflight.sensors import build_spi_bus
from reflight.simulation.playback import build_simulation

ROOT = Path(__file__).parents[2]


def test_csv_si_to_all_firmware_endpoints(compile_arm, tmp_path):
    elf = compile_arm('four_sensors.c')
    board = load_board(ROOT / 'example-configs/stm32f407vg-four-sensors.yaml')
    (tmp_path / 'si.csv').write_text(
        't,a,w,p,T\n' + ''.join(f'{t},9.80665,{math.pi/18},101325,293.15\n' for t in (0, 1)))
    accel = {f'accel_{axis}': 'a' for axis in 'xyz'}
    gyro = {f'gyro_{axis}': 'w' for axis in 'xyz'}
    sources = {name: dict(kind='openrocket', csv_path='si.csv', time_column='t', columns=columns)
               for name, columns in dict(motion=accel | gyro, accel=accel, gyro=gyro,
                                         atmosphere={'pressure': 'p', 'temperature': 'T'}).items()}
    scenario_path = tmp_path / 'scenario.yaml'
    scenario_path.write_text(yaml.safe_dump(dict(
        simulation=dict(instructions_per_second=200000, duration_s=.01),
        sources=sources, default_source='atmosphere',
        sensors={'ICM45686': {'source': 'motion'}, 'BMI088Accel': {'source': 'accel'},
                 'BMI088Gyro': {'source': 'gyro'}})))
    cfg = load_scenario(scenario_path, board)
    bus = build_spi_bus(board)
    trace = run_firmware(elf, 1500, spi_bus=bus, clock=build_simulation(cfg, bus))
    # ICM ±32g: 1g=1024 BE; BMI ±6g: 1g=5461 LE; gyro 10°/s=164 LE.
    # Ambient D1=9155145 from independent integer compensation (dt=-296,
    # SENS=40127*32768-trunc(23317*296/256), OFF analogously); BMP Q16/Q6.
    expected = bytes.fromhex('e9 04 00 9c bf') + (9155145).to_bytes(3, 'big')
    expected += bytes.fromhex('1e 55 15 0f a4 00 50 00 00 14 40 f3 62')
    assert trace.uart_bytes == expected
    assert trace.stop_reason == 'instruction_cap'
    assert any(w.address == 0x4001300c for w in trace.writes)
