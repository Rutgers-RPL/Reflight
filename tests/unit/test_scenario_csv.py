"""Scenario binding and strict CSV interpolation, without emulation."""
from pathlib import Path

import pytest
import yaml

from reflight.config import ConfigError, load_board
from reflight.config.scenario import load_scenario
from reflight.simulation.csv_data import CSVData

ROOT = Path(__file__).parents[2]
BOARD = ROOT / "example-configs/stm32f407vg-four-sensors.yaml"
SCENARIO = ROOT / "example-configs/raw-flight-scenario.yaml"


def data(tmp_path, content):
    path = tmp_path / "data.csv"
    path.write_text(content)
    return CSVData.load(path, "Time (s)", {"raw": "Raw (count)"})


def test_nonuniform_normalized_interpolation(tmp_path):
    csv = data(tmp_path, 'Time (s),Raw (count)\n12,0\n12.5,10\n14,70\n')
    assert csv.end_s == 2
    for t, value in [(0, 0), (.25, 5), (.5, 10), (1, 30), (2, 70)]:
        assert csv.values_at(t) == {"raw": value}
    for t in [-1, 2.1, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="outside"):
            csv.values_at(t)
    csv.values_at(0)["raw"] = 90
    assert csv.values_at(0)["raw"] == 0


def test_single_row(tmp_path):
    csv = data(tmp_path, 'Time (s),Raw (count)\n5,7\n')
    assert csv.end_s == 0
    assert csv.values_at(0) == {"raw": 7}
    with pytest.raises(ValueError):
        csv.values_at(.01)


@pytest.mark.parametrize("content, message", [
    ('', 'headers'), ('Time (s),Raw (count)\n', 'no data'),
    ('Time (s),Raw (count),Raw (count)\n0,1,1\n', 'duplicate'),
    ('Time,Raw (count)\n0,1\n', 'missing column'),
    ('Time (s),Raw (count)\n0,1,2\n', 'row 2'),
    ('Time (s),Raw (count)\n0\n', 'columns'),
    ('Time (s),Raw (count)\n0,hello\n', 'numeric'),
    ('Time (s),Raw (count)\n0,nan\n', 'finite'),
    ('Time (s),Raw (count)\ninf,1\n', 'finite'),
    ('Time (s),Raw (count)\n0,1\n0,2\n', 'strictly increase'),
    ('Time (s),Raw (count)\n1,1\n0,2\n', 'strictly increase'),
    ('Time (s),Raw (count)\n0,"oops\n', 'row 2: malformed CSV'),
    ('Time (s),Raw (count)\n0,1\n1e-999,2\n', 'playback precision'),
])
def test_invalid_csv(tmp_path, content, message):
    with pytest.raises(ConfigError, match=message):
        data(tmp_path, content)


def scenario(tmp_path, mutate):
    raw = yaml.safe_load(SCENARIO.read_text())
    raw["sources"]["flight"]["csv_path"] = str(SCENARIO.parent / "raw-flight.csv")
    mutate(raw)
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(raw))
    return load_scenario(path, load_board(BOARD))


def test_binding_paths_active_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_scenario(SCENARIO, load_board(BOARD))
    assert set(cfg.csv_data) == {"flight"}
    assert cfg.sources["flight"].columns["accel_x"] == "Accel X (count)"
    assert cfg.csv_data["flight"].values_at(0)["accel_x"] == 10
    assert cfg.sensors["MS5611"].source == "built_in"
    assert cfg.sensors["ICM45686"].endpoint == "SPI1.PA4"
    assert cfg.sensors["BMI088Accel"].endpoint == "SPI1.PA6"
    cfg = scenario(tmp_path, lambda r: r["sensors"].clear())
    assert not cfg.csv_data
    assert cfg.endpoint == (.08, "duration")


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["simulation"].update(duration_s=0), 'positive'),
    (lambda r: r["simulation"].update(duration_s=float('nan')), 'finite'),
    (lambda r: r["simulation"].update(instructions_per_second=-1), 'positive'),
    (lambda r: r["simulation"].update(instructions_per_second=True), 'number'),
    (lambda r: r["simulation"].update(random_seed=1.5), 'integer'),
    (lambda r: r.update(default_source='missing'), 'default_source'),
    (lambda r: r["sensors"].update({'SPI1.PA0': {}}), 'unknown keys'),
    (lambda r: r["sensors"].update({'MysterySensor': {}}), 'unknown keys'),
    (lambda r: r["sensors"].update({'SPI1.PA4': r["sensors"].pop('ICM45686')}), 'unknown keys'),
    (lambda r: r["sensors"]["ICM45686"].update(source='missing'), 'unknown source'),
    (lambda r: r["sources"]["flight"].update(kind='plugin'), 'unsupported source'),
    (lambda r: r["sources"]["flight"]["columns"].pop('accel_x'), 'complete channel'),
    (lambda r: r["sources"]["flight"]["columns"].update(accel_x='missing header'), 'missing column'),
    (lambda r: r["sources"]["flight"]["columns"].update(accel_x=None), 'CSV header'),
    (lambda r: r["sources"]["flight"].update(columns={header: key for key, header in r["sources"]["flight"]["columns"].items()}), 'unsupported input keys'),
    (lambda r: r["sensors"]["ICM45686"].update(bias={'accel_x': float('inf')}), 'finite'),
    (lambda r: r["sensors"]["ICM45686"].update(noise={'accel_x': -1}), 'nonnegative'),
    (lambda r: r["sensors"]["ICM45686"].update(noise={'pressure': 1}), 'unknown keys'),
    (lambda r: r["sources"]["flight"].update(csv_path='missing.csv'), 'cannot read CSV'),
])
def test_invalid_scenario(tmp_path, mutate, message):
    with pytest.raises(ValueError, match=message):
        scenario(tmp_path, mutate)


def test_defaults_need_duration_and_unused_csv_not_loaded(tmp_path):
    def change(raw):
        raw["sensors"].clear()
        raw["sources"]["flight"]["csv_path"] = 'missing.csv'
    assert scenario(tmp_path, change).csv_data == {}
    def no_duration(raw):
        change(raw)
        del raw["simulation"]["duration_s"]
    with pytest.raises(ConfigError, match='duration_s is required'):
        scenario(tmp_path, no_duration)


@pytest.mark.parametrize('duration, expected', [
    (.04, (.04, 'duration')), (.1, (.1, 'duration')), (.2, (.1, 'csv_end')),
    (None, (.1, 'csv_end')),
])
def test_termination_selection(tmp_path, duration, expected):
    def change(raw):
        if duration is None:
            del raw['simulation']['duration_s']
        else:
            raw['simulation']['duration_s'] = duration
    assert scenario(tmp_path, change).endpoint == expected


def test_multiple_active_csv_endpoints_and_unused_short_source(tmp_path):
    (tmp_path / 'short.csv').write_text('t,p\n5,1\n5.015,2\n')
    def change(raw):
        raw['sources']['short'] = {'kind': 'openrocket', 'csv_path': 'short.csv',
                                   'time_column': 't', 'columns': {'d1': 'p'}}
        raw['sources']['unused'] = {'kind': 'openrocket', 'csv_path': 'does-not-exist.csv',
                                    'time_column': 't', 'columns': {'d1': 'p'}}
        raw['sensors']['MS5611'] = {'source': 'short'}
    cfg = scenario(tmp_path, change)
    assert set(cfg.csv_data) == {'short', 'flight'}
    assert cfg.endpoint == (.015, 'csv_end')


def test_model_binding_follows_board_wiring(tmp_path):
    from dataclasses import replace
    from reflight.config import DeviceConfig
    from reflight.sensors import build_spi_bus
    from reflight.simulation.playback import build_simulation
    board = load_board(BOARD)
    bus = board.buses[0]
    devices = tuple(DeviceConfig(device.model, cs='PA3') if device.model == 'ICM45686'
                    else device for device in bus.devices)
    board = replace(board, buses=(replace(bus, devices=devices),))
    cfg = load_scenario(SCENARIO, board)
    assert cfg.sensors['ICM45686'].endpoint == 'SPI1.PA3'
    runtime_bus = build_spi_bus(board)
    clock = build_simulation(cfg, runtime_bus)
    clock.advance(0)
    assert runtime_bus.devices['PA3'].registers[1] == 10
    assert 'PA4' not in runtime_bus.devices


@pytest.mark.parametrize('other_bus', [False, True])
def test_repeated_models_are_ambiguous_for_scenarios(other_bus):
    from dataclasses import replace
    from reflight.config import BusConfig, DeviceConfig
    from reflight.sensors import build_spi_bus
    board = load_board(BOARD)
    bus = board.buses[0]
    first = replace(bus, devices=(DeviceConfig('ICM45686', cs='PA4'),))
    if other_bus:
        second = BusConfig('SPI2', 'spi', (DeviceConfig('ICM45686', cs='PB0'),))
        board = replace(board, buses=(first, second))
    else:
        board = replace(board, buses=(replace(first, devices=(*first.devices, DeviceConfig('ICM45686', cs='PA6'))),))
        # Static bus construction still permits two independent devices.
        runtime_bus = build_spi_bus(board)
        assert runtime_bus.devices['PA4'] is not runtime_bus.devices['PA6']
    with pytest.raises(ConfigError, match='duplicate model ICM45686 is ambiguous'):
        load_scenario(SCENARIO, board)


def test_multiple_input_keys_can_share_csv_header(tmp_path):
    path = tmp_path / 'shared.csv'
    path.write_text('t,Raw (count)\n0,10\n1,20\n')
    csv = CSVData.load(path, 't', {'first': 'Raw (count)', 'second': 'Raw (count)'})
    assert csv.values_at(.5) == {'first': 15, 'second': 15}

    def change(raw):
        raw['sources']['flight']['columns'] = {
            key: 'Accel X (count)' for key in ('accel_x', 'accel_y', 'accel_z')}
    cfg = scenario(tmp_path, change)
    assert cfg.csv_data['flight'].values_at(0) == {'accel_x': 10, 'accel_y': 10, 'accel_z': 10}
