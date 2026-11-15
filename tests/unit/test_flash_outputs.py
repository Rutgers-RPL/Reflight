"""Strict optional export configuration and bounded main-array artifacts."""
from dataclasses import replace
from pathlib import Path
import time

import pytest
import yaml

from reflight.config import ConfigError, FlashOutputConfig, load_board
from reflight.devices import build_spi_bus
from reflight.emulation.qiling_runner import ExecutionTrace
from reflight.outputs import write_configured_outputs
from reflight.outputs import writers

ROOT = Path(__file__).parents[2]
TRACE = ExecutionTrace((), 0, (), (), None, ())


def board_file(tmp_path, flash=None, devices=None, packet=False):
    data = yaml.safe_load((ROOT / 'example-configs/stm32f407vg.yaml').read_text())
    data['mcu']['svd'] = str(ROOT / 'tests/fixtures/svd/stm32f407-minimal.svd')
    data['buses'] = {'SPI1': {'devices': devices if devices is not None else [
        {'model': 'W25N01KV', 'cs': 'PA7'}, {'model': 'GD5F1GQ5UExxH', 'cs': 'PA6'}]}}
    if flash is not None:
        data['outputs']['flash'] = flash
    if packet:
        definition = tmp_path / 'packet.yaml'
        definition.write_text('sync: [165, 90]\nbyte_order: little\nfields:\n  - {name: x, type: uint8}\n')
        data['outputs']['telemetry']['packet_definition'] = str(definition)
    path = tmp_path / 'board.yaml'
    path.write_text(yaml.safe_dump(data))
    return path


def test_real_full_export_length_offsets_and_erase(tmp_path):
    board = load_board(board_file(tmp_path, [{'device': 'SPI1.PA7', 'file': 'flash.bin'}]))
    bus = build_spi_bus(board)
    storage = bus.devices['PA7'].storage
    for row, column, value in [(0, 0, 0x12), (63, 2047, 0x34), (64, 1, 0x56),
                               (65535, 2047, 0x78), (65, 2048, 0)]:
        storage.program_page(row, column, bytes([value]))
    storage.erase_block(0)
    output = tmp_path / 'output'
    started = time.perf_counter()
    write_configured_outputs(TRACE, board, output, spi_bus=bus)
    path = output / 'flash.bin'
    assert path.stat().st_size == 134217728
    with path.open('rb') as stream:
        for offset, expected in [(0, 255), (63*2048+2047, 255), (64*2048+1, 0x56),
                                  (65*2048, 255), (134217727, 0x78)]:
            stream.seek(offset)
            assert stream.read(1) == bytes([expected])
    assert not list(output.glob('.reflight-flash-*'))
    print(f'128 MiB export: {time.perf_counter() - started:.3f} s')


def test_two_device_exports_bounded_sinks_and_no_spare(tmp_path, monkeypatch):
    board = load_board(board_file(tmp_path, [
        {'device': 'SPI1.PA7', 'file': 'winbond.bin'},
        {'device': 'SPI1.PA6', 'file': 'gd.bin'}]))
    bus = build_spi_bus(board)
    for cs, data in [('PA7', b'\x12'), ('PA6', b'\x34')]:
        bus.devices[cs].storage.program_page(65535, 2047, data + b'\x00')
    results = {}

    class Sink:
        def __init__(self):
            self.total = 0
            self.last = None

        def write(self, data):
            assert len(data) == 2048
            self.total += len(data)
            self.last = data[-1]
            return len(data)

    def export(storage, path):
        sink = Sink()
        writers._write_flash_image(storage, sink)
        results[path.name] = (sink.total, sink.last)

    monkeypatch.setattr(writers, '_export_flash', export)
    write_configured_outputs(TRACE, board, tmp_path / 'output', spi_bus=bus)
    assert results == {'winbond.bin': (134217728, 0x12), 'gd.bin': (134217728, 0x34)}


@pytest.mark.parametrize('filename', ['../flash.bin', '/tmp/flash.bin', 'a/b', 'a\\b',
    'C:flash.bin', '.', '..', '', 'NUL', 'telemetry.bin', 'hardware_events.csv',
    'telemetry.decoded.csv'])
def test_invalid_filenames_and_legacy_collisions(tmp_path, filename):
    path = board_file(tmp_path, [{'device': 'SPI1.PA7', 'file': filename}], packet=True)
    with pytest.raises(ConfigError):
        load_board(path)


@pytest.mark.parametrize('device', ['SPI1.PA4', 'SPI2.PA7', 'W25N01KV', 'SPI1.PA6'])
def test_invalid_or_sensor_references(tmp_path, device):
    path = board_file(tmp_path, [{'device': device, 'file': 'flash.bin'}], devices=[
        {'model': 'W25N01KV', 'cs': 'PA7'}, {'model': 'ICM45686', 'cs': 'PA6'}])
    with pytest.raises(ConfigError, match='configured SPI flash'):
        load_board(path)


@pytest.mark.parametrize('flash', [None, {}, 'flash.bin', [0], [{'device': 'SPI1.PA7'}],
    [{'device': 'SPI1.PA7', 'file': 'a.bin', 'other': 1}]])
def test_invalid_export_shapes(tmp_path, flash):
    path = board_file(tmp_path)
    data = yaml.safe_load(path.read_text())
    data['outputs']['flash'] = flash
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError):
        load_board(path)


def test_duplicate_filenames_rejected_before_execution(tmp_path, monkeypatch, capsys):
    path = board_file(tmp_path, [{'device': 'SPI1.PA7', 'file': 'same.bin'},
                                {'device': 'SPI1.PA6', 'file': 'same.bin'}])
    from reflight.cli import main
    import reflight.emulation.qiling_runner as runner
    def forbidden(*args, **kwargs):
        pytest.fail('firmware ran before output validation')
    monkeypatch.setattr(runner, 'run_firmware', forbidden)
    output = tmp_path / 'output'
    assert main(['run', '--firmware', 'unused.elf', '--board', str(path),
                 '--output-dir', str(output)]) == 1
    assert 'collides' in capsys.readouterr().err
    assert not output.exists()


def test_no_flash_outputs_legacy_call_and_missing_live_device(tmp_path):
    board = load_board(board_file(tmp_path))
    assert board.flash_outputs == ()
    output = tmp_path / 'legacy'
    write_configured_outputs(TRACE, board, output)
    assert {p.name for p in output.iterdir()} == {'telemetry.bin', 'hardware_events.csv'}
    assert (output / 'telemetry.bin').read_bytes() == b''
    assert (output / 'hardware_events.csv').read_text() == 'sequence,instruction_count,pc,pin,level\n'
    board = replace(board, flash_outputs=(FlashOutputConfig('SPI1.PA7', 'flash.bin'),))
    with pytest.raises(ValueError, match='not attached'):
        write_configured_outputs(TRACE, board, tmp_path / 'missing')
    assert not (tmp_path / 'missing').exists()


def test_failed_export_removes_partial_and_preserves_older_image(tmp_path, monkeypatch):
    board = load_board(board_file(tmp_path, [{'device': 'SPI1.PA7', 'file': 'flash.bin'}]))
    output = tmp_path / 'output'
    output.mkdir()
    image = output / 'flash.bin'
    image.write_bytes(b'previous image')
    def fail(storage, stream):
        stream.write(b'partial')
        raise OSError('simulated disk full')
    monkeypatch.setattr(writers, '_write_flash_image', fail)
    with pytest.raises(OSError, match='disk full'):
        write_configured_outputs(TRACE, board, output, spi_bus=build_spi_bus(board))
    assert image.read_bytes() == b'previous image'
    assert not list(output.glob('.reflight-flash-*'))


def test_short_write_and_cli_reports_export_failure(tmp_path, monkeypatch, capsys):
    class ShortSink:
        def write(self, data):
            return len(data) - 1
    from reflight.flash import NandStorage
    with pytest.raises(OSError, match='short write'):
        writers._write_flash_image(NandStorage(spare_bytes=64), ShortSink())
    path = board_file(tmp_path, [{'device': 'SPI1.PA7', 'file': 'flash.bin'}])
    import reflight.emulation.qiling_runner as runner
    monkeypatch.setattr(runner, 'run_firmware', lambda *a, **kw: TRACE)
    def fail(storage, path):
        raise OSError('simulated export error')
    monkeypatch.setattr(writers, '_export_flash', fail)
    from reflight.cli import main
    assert main(['run', '--firmware', 'unused.elf', '--board', str(path),
                 '--output-dir', str(tmp_path / 'output')]) == 1
    assert 'export error' in capsys.readouterr().err
