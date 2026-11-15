"""Fresh Cortex-M firmware exercises flash exclusively through SPI1/GPIO."""
import csv
from pathlib import Path
import subprocess
import sys

import yaml
import pytest

ROOT = Path(__file__).parents[2]


def flash_board(tmp_path, model):
    data = yaml.safe_load((ROOT / 'example-configs/stm32f407vg.yaml').read_text())
    data['mcu']['svd'] = str(ROOT / 'tests/fixtures/svd/stm32f407-minimal.svd')
    data['buses'] = {'SPI1': {'devices': [{'model': model, 'cs': 'PA7'}]}}
    data['outputs']['capture']['sources'][0]['pins'] = ['PB1']
    path = tmp_path / 'board.yaml'
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize('model,identity', [('W25N01KV', 'ef ae'),
    ('GD5F1GQ5UExxH', 'c8 31'), ('GD5F1GQ5RExxH', 'c8 21')])
def test_flash_cli(compile_arm, tmp_path, model, identity):
    elf = compile_arm('spi_nand.c')
    board = flash_board(tmp_path, model)
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, '-m', 'reflight.cli', 'run', '--firmware', str(elf),
        '--board', str(board), '--output-dir', str(output), '--max-instructions', '60000'],
        capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert (output / 'telemetry.bin').read_bytes() == bytes.fromhex(identity) + b'P'
    with (output / 'hardware_events.csv').open() as stream:
        events = list(csv.DictReader(stream))
    assert [(e['pin'], e['level']) for e in events] == [('PB1', '1')]
    assert sorted(p.name for p in output.iterdir()) == ['hardware_events.csv', 'telemetry.bin']


@pytest.mark.parametrize('model', ['W25N01KV', 'GD5F1GQ5UExxH', 'GD5F1GQ5RExxH'])
def test_mixed_flash_and_sensor_scenario_cli(compile_arm, tmp_path, model):
    source = (ROOT / 'tests/fixtures/firmware/spi_nand.c').read_text()
    elf = compile_arm('spi_nand_mixed.c', '#define MIXED_SENSOR\n' + source)
    path = flash_board(tmp_path, model)
    data = yaml.safe_load(path.read_text())
    data['buses']['SPI1']['devices'].append({'model': 'ICM45686', 'cs': 'PA4'})
    path.write_text(yaml.safe_dump(data))
    (tmp_path / 'motion.csv').write_text('t,a\n0,0\n0.5,9.80665\n')
    scenario = tmp_path / 'scenario.yaml'
    scenario.write_text(yaml.safe_dump({
        'simulation': {'instructions_per_second': 200000, 'duration_s': .25},
        'sources': {'motion': {'kind': 'openrocket', 'csv_path': 'motion.csv',
                              'time_column': 't', 'columns': {f'accel_{a}': 'a' for a in 'xyz'}}},
        'default_source': 'motion'}))
    from reflight.config import load_board
    from reflight.config.scenario import load_scenario
    from reflight.devices import build_spi_bus
    from reflight.simulation.playback import build_simulation
    board = load_board(path)
    cfg = load_scenario(scenario, board)
    assert set(cfg.sensors) == {'ICM45686'}
    bus = build_spi_bus(board)
    clock = build_simulation(cfg, bus)
    assert set(clock._sensors) == {'SPI1.PA4'}
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, '-m', 'reflight.cli', 'run', '--firmware', str(elf),
        '--board', str(path), '--scenario', str(scenario), '--output-dir', str(output),
        '--max-instructions', '60000'], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    raw = (output / 'telemetry.bin').read_bytes()
    assert raw[2:3] == b'P'
    assert len(raw) == 15
    assert all(raw[i] == ord('S') for i in range(3, 15, 3))
    values = [int.from_bytes(raw[i+1:i+3], 'big', signed=True) for i in range(3, 15, 3)]
    assert values == sorted(values)
    assert len(set(values)) >= 3
    assert 'Simulation time 0.25 s; stop reason: duration' in result.stdout
    with (output / 'hardware_events.csv').open() as stream:
        events = list(csv.DictReader(stream))
    assert [(e['pin'], e['level']) for e in events] == [('PB1', '1')]
    assert 0 < float(events[0]['time_s']) < .25


def test_firmware_driven_export(compile_arm, tmp_path):
    elf = compile_arm('spi_nand.c')
    path = flash_board(tmp_path, 'W25N01KV')
    data = yaml.safe_load(path.read_text())
    data['outputs']['flash'] = [{'device': 'SPI1.PA7', 'file': 'flash.bin'}]
    path.write_text(yaml.safe_dump(data))
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, '-m', 'reflight.cli', 'run', '--firmware', str(elf),
        '--board', str(path), '--output-dir', str(output), '--max-instructions', '60000'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (output / 'telemetry.bin').read_bytes() == b'\xef\xaeP'
    image = output / 'flash.bin'
    assert image.stat().st_size == 134217728
    with image.open('rb') as stream:
        for offset, expected in [(63*2048+2046, b'\xff\xff'),
                                  (64*2048, b'\xff\x3c\xc3\xff'),
                                  (134217727, b'\xff')]:
            stream.seek(offset)
            assert stream.read(len(expected)) == expected


@pytest.mark.parametrize('opcode,message', [(0x32, 'multi-lane'), (0xAB, 'unsupported flash opcode')])
def test_firmware_unsupported_command_fails_before_export(compile_arm, tmp_path, opcode, message):
    source = (ROOT / 'tests/fixtures/firmware/spi_nand.c').read_text()
    source = source.replace('CS_LOW; spi(0x9f);', f'CS_LOW; spi({opcode}); CS_HIGH; CS_LOW; spi(0x9f);')
    elf = compile_arm('unsupported_nand.c', source)
    path = flash_board(tmp_path, 'W25N01KV')
    data = yaml.safe_load(path.read_text())
    data['outputs']['flash'] = [{'device': 'SPI1.PA7', 'file': 'flash.bin'}]
    path.write_text(yaml.safe_dump(data))
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, '-m', 'reflight.cli', 'run', '--firmware', str(elf),
        '--board', str(path), '--output-dir', str(output), '--max-instructions', '60000'],
        capture_output=True, text=True)
    assert result.returncode == 1
    assert message in result.stderr
    assert not output.exists()
