"""Full Phase 4 CLI path from unrelated directories, fresh ARM ELF each time."""
import csv
from pathlib import Path
import subprocess
import sysconfig

import pytest
import yaml

ROOT = Path(__file__).parents[2]


def files(tmp_path, *, duration=.08, default_only=False, seed=None):
    board = yaml.safe_load((ROOT / 'example-configs/simulated-flight-board.yaml').read_text())
    board['mcu']['svd'] = str(ROOT / 'tests/fixtures/svd/stm32f407-minimal.svd')
    board['outputs']['telemetry']['packet_definition'] = str(ROOT / 'example-configs/simulated-flight-packet.yaml')
    board_path = tmp_path / 'board.yaml'
    board_path.write_text(yaml.safe_dump(board))
    scenario = yaml.safe_load((ROOT / 'example-configs/raw-flight-scenario.yaml').read_text())
    for source in ('flight', 'atmosphere'):
        scenario['sources'][source]['csv_path'] = str(ROOT / 'example-configs/raw-flight.csv')
    scenario['simulation']['duration_s'] = duration
    if default_only:
        scenario['sensors'] = {}
    if seed is not None:
        scenario['simulation']['random_seed'] = seed
        scenario['sensors']['ICM45686']['noise'] = {'accel_x': .03}
        scenario['sensors']['BMI088Accel']['bias'] = {'accel_y': .07}
    scenario_path = tmp_path / 'scenario.yaml'
    scenario_path.write_text(yaml.safe_dump(scenario))
    return board_path, scenario_path


def invoke(elf, board, scenario, output, cwd, cap=30000):
    cli = Path(sysconfig.get_path('scripts')) / 'reflight'
    return subprocess.run([str(cli), 'run', '--firmware', str(elf), '--board', str(board),
                           '--scenario', str(scenario), '--output-dir', str(output),
                           '--max-instructions', str(cap)], cwd=cwd, capture_output=True, text=True)


def rows(output):
    with (output / 'telemetry.decoded.csv').open(newline='') as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize('duration, default_only, cap, reason, elapsed', [
    (.06, False, 30000, 'duration', .06),
    (.2, False, 30000, 'csv_end', .1),
    (.1, False, 30000, 'duration', .1),
    (.06, True, 30000, 'duration', .06),
    (.2, False, 500, 'instruction_cap', .0025),
])
def test_cli_stop_outcomes(compile_arm, tmp_path, duration, default_only, cap, reason, elapsed):
    elf = compile_arm('simulated_flight.c')
    board, scenario = files(tmp_path, duration=duration, default_only=default_only)
    other = tmp_path / 'unrelated'
    other.mkdir()
    output = tmp_path / 'output'
    result = invoke(elf, board, scenario, output, other, cap)
    assert result.returncode == 0, result.stderr
    assert f'Simulation time {elapsed:g} s; stop reason: {reason}' in result.stdout
    assert f'Executed {round(elapsed * 200000)} instructions' in result.stdout
    decoded = rows(output)
    assert len(decoded) == len((output / 'telemetry.bin').read_bytes()) // 10
    assert all(0 <= float(r['time_s']) < elapsed for r in decoded)
    if reason != 'instruction_cap':
        assert len(decoded) > 5
        assert all(int(r['ms_d1']) == 9155145 for r in decoded)
        if default_only:
            assert all(int(r['icm_accel_x']) == int(r['bmi_accel_x']) == 0 for r in decoded)
            assert (output / 'hardware_events.csv').read_text() == 'sequence,instruction_count,pc,pin,level,time_s\n'
        else:
            def sample(t, scale):
                return round((.1 + 10*t if t <= .03 else .4 + 20*(t-.03))*scale)
            assert all(int(r['icm_accel_x']) in {sample(i / 100, 1024) for i in range(11)} for r in decoded)
            assert all(int(r['bmi_accel_x']) in {sample(i / 200, 32768/6) for i in range(21)} for r in decoded)
            assert len({r['icm_accel_x'] for r in decoded}) > 3


def test_seeded_cli_outputs_identical_and_seed_changes_samples(compile_arm, tmp_path):
    elf = compile_arm('simulated_flight.c')
    board, scenario = files(tmp_path, seed=37)
    outputs = []
    for i in range(3):
        if i == 2:
            raw = yaml.safe_load(scenario.read_text())
            raw['simulation']['random_seed'] = 38
            scenario.write_text(yaml.safe_dump(raw))
        output = tmp_path / f'output{i}'
        result = invoke(elf, board, scenario, output, tmp_path)
        assert result.returncode == 0, result.stderr
        outputs.append({p.name: p.read_bytes() for p in output.iterdir()})
    assert outputs[0] == outputs[1]
    assert outputs[0]['telemetry.bin'] != outputs[2]['telemetry.bin']


@pytest.mark.parametrize('invalid', ['scenario', 'packet', 'collision', 'duplicate_model'])
def test_invalid_inputs_fail_before_firmware_or_outputs(compile_arm, tmp_path, invalid):
    board, scenario = files(tmp_path)
    if invalid == 'scenario':
        raw = yaml.safe_load(scenario.read_text())
        raw['simulation']['instructions_per_second'] = 0
        scenario.write_text(yaml.safe_dump(raw))
        message = 'positive'
    elif invalid == 'packet':
        packet = tmp_path / 'bad-packet.yaml'
        packet.write_text('sync: []\nbyte_order: little\nfields: []')
        raw = yaml.safe_load(board.read_text())
        raw['outputs']['telemetry']['packet_definition'] = str(packet)
        board.write_text(yaml.safe_dump(raw))
        message = 'packet.sync'
    elif invalid == 'duplicate_model':
        raw = yaml.safe_load(board.read_text())
        raw['buses']['SPI1']['devices'].append({'model': 'ICM45686', 'cs': 'PA7'})
        board.write_text(yaml.safe_dump(raw))
        message = 'duplicate model ICM45686 is ambiguous'
    else:
        raw = yaml.safe_load(board.read_text())
        raw['outputs']['capture']['file'] = 'telemetry.decoded.csv'
        board.write_text(yaml.safe_dump(raw))
        message = 'filenames must differ'
    output = tmp_path / 'no-output'
    result = invoke(tmp_path / 'missing.elf', board, scenario, output, tmp_path)
    assert result.returncode != 0
    assert message in result.stderr
    assert not output.exists()


@pytest.mark.parametrize('pressure,temperature', [(0, 293.15), (101325, 100)])
def test_unrealizable_physical_samples_fail_without_outputs(compile_arm, tmp_path, pressure, temperature):
    elf = compile_arm('simulated_flight.c')
    board, scenario = files(tmp_path)
    csv_path = tmp_path / 'impossible.csv'
    csv_path.write_text(f't,p,T\n0,{pressure},{temperature}\n1,{pressure},{temperature}\n')
    raw = yaml.safe_load(scenario.read_text())
    raw['sources']['atmosphere'].update(csv_path=str(csv_path), time_column='t',
                                         columns={'pressure': 'p', 'temperature': 'T'})
    scenario.write_text(yaml.safe_dump(raw))
    output = tmp_path / 'no-output'
    result = invoke(elf, board, scenario, output, tmp_path)
    assert result.returncode != 0
    assert 'MS5611' in result.stderr and 'must be within' in result.stderr
    assert not output.exists()
