"""Flash endpoints never receive CSV mappings, samples or sensor schedules."""
from pathlib import Path

import pytest
import yaml

from reflight.config import ConfigError, load_board
from reflight.config.scenario import load_scenario
from reflight.devices import build_spi_bus
from reflight.simulation.playback import build_simulation

ROOT = Path(__file__).parents[2]


def configs(tmp_path, models, overrides=None):
    data = yaml.safe_load((ROOT / 'example-configs/stm32f407vg.yaml').read_text())
    data['mcu']['svd'] = str(ROOT / 'tests/fixtures/svd/stm32f407-minimal.svd')
    data['buses'] = {'SPI1': {'devices': [
        {'cs': f'PA{i + 4}', 'model': model} for i, model in enumerate(models)]}}
    board_path = tmp_path / 'board.yaml'
    board_path.write_text(yaml.safe_dump(data))
    # Deliberately missing CSV: no active sensor means no CSV load/validation.
    scenario = {'simulation': {'instructions_per_second': 1000, 'duration_s': .1},
        'default_source': 'motion', 'sources': {'motion': {'kind': 'openrocket',
        'csv_path': 'missing.csv', 'time_column': 't', 'columns': {'accel_x': 'a'}}}}
    if overrides is not None:
        scenario['sensors'] = overrides
    path = tmp_path / 'scenario.yaml'
    path.write_text(yaml.safe_dump(scenario))
    return load_board(board_path), path


def test_repeated_flash_models_excluded_from_uniqueness_and_csv(tmp_path):
    board, path = configs(tmp_path, ['W25N01KV', 'W25N01KV', 'GD5F1GQ5UExxH'])
    cfg = load_scenario(path, board)
    assert cfg.sensors == {}
    assert cfg.csv_data == {}
    bus = build_spi_bus(board)
    clock = build_simulation(cfg, bus)
    assert clock._sensors == {}
    clock.advance(.1)
    assert clock.finished
    assert all(device.storage.allocated_pages == 0 for device in bus.devices.values())


def test_flash_override_not_a_sensor(tmp_path):
    board, path = configs(tmp_path, ['W25N01KV'], {'W25N01KV': {'source': 'motion'}})
    with pytest.raises(ConfigError, match='unknown keys'):
        load_scenario(path, board)


def test_unknown_model_not_silently_excluded(tmp_path):
    board, path = configs(tmp_path, ['Unknown'])
    with pytest.raises(ConfigError, match='unsupported sensor model Unknown'):
        load_scenario(path, board)


def test_real_sensor_duplicates_still_rejected(tmp_path):
    board, path = configs(tmp_path, ['W25N01KV', 'ICM45686', 'ICM45686'])
    with pytest.raises(ConfigError, match='unique sensor models'):
        load_scenario(path, board)
