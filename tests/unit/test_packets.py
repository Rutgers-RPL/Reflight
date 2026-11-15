"""Incremental framing, supported primitive types, and output compatibility."""
from dataclasses import replace
from pathlib import Path
import struct

import pytest
import yaml

from reflight.config import ConfigError, load_board
from reflight.emulation.qiling_runner import ExecutionTrace, UARTByteEvent, GPIOTransition
from reflight.outputs import write_configured_outputs
from reflight.outputs.packets import PacketDecoder, load_packet_definition

ROOT = Path(__file__).parents[2]


def packet_file(tmp_path, raw=None):
    path = tmp_path / 'packet.yaml'
    path.write_text(yaml.safe_dump(raw if raw is not None else {
        'sync': [165, 90], 'byte_order': 'little',
        'fields': [{'name': 'value', 'type': 'int16'}]}))
    return path


def test_split_multiple_bad_sync_truncated_and_sync_in_payload(tmp_path):
    decoder = PacketDecoder(load_packet_definition(packet_file(tmp_path)))
    assert decoder.feed(b'noise\xa5', .1) == []
    assert decoder.feed(b'\x01\xa5', .2) == []  # bad sync, then new partial sync
    assert decoder.feed(b'\x5a\x34', .3) == []
    result = decoder.feed(b'\x12\xa5\x5a\xfe\xff\xa5', .4)
    assert [p.values for p in result] == [(0x1234,), (-2,)]
    assert [p.time_s for p in result] == [.4, .4]
    assert decoder.feed(b'\x5a\x01', .5) == []  # truncated payload remains incomplete
    decoder = PacketDecoder(load_packet_definition(packet_file(tmp_path)))
    assert decoder.feed(b'\xa5\x5a\xa5\x5a')[0].values == (0x5aa5,)
    decoder.feed(b'x' * 10000)
    assert len(decoder.buffer) <= 1


@pytest.mark.parametrize('byte_order, prefix', [('little', '<'), ('big', '>')])
def test_all_primitive_types(tmp_path, byte_order, prefix):
    types = ['int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32', 'float32']
    fields = [{'name': f'v{i}', 'type': t} for i, t in enumerate(types)]
    path = packet_file(tmp_path, {'sync': [0x7e], 'byte_order': byte_order, 'fields': fields})
    decoder = PacketDecoder(load_packet_definition(path))
    values = (-3, 250, -2000, 60000, -2000000000, 4000000000, 1.25)
    assert decoder.feed(b'junk' + b'\x7e' + struct.pack(prefix + 'bBhHiIf', *values))[0].values == values
    assert decoder.feed(b'no sync') == []
    assert decoder.buffer == b''


@pytest.mark.parametrize('mutate, message', [
    (lambda r: r.pop('sync'), 'missing'),
    (lambda r: r.update(sync=[]), 'nonempty list'),
    (lambda r: r.update(sync=[256]), 'bytes'),
    (lambda r: r.update(sync=[True]), 'bytes'),
    (lambda r: r.update(sync='a55a'), 'bytes'),
    (lambda r: r.update(byte_order='native'), 'byte_order'),
    (lambda r: r.update(fields=[]), 'ordered list'),
    (lambda r: r.update(fields={}), 'ordered list'),
    (lambda r: r['fields'][0].update(type='float64'), 'unsupported'),
    (lambda r: r['fields'][0].update(type=[]), 'nonempty string'),
    (lambda r: r['fields'][0].update(name='time_s'), 'unique'),
    (lambda r: r['fields'][0].update(name='has spaces'), 'unique'),
    (lambda r: r['fields'].append(r['fields'][0].copy()), 'unique'),
    (lambda r: r.update(checksum='crc'), 'unknown keys'),
])
def test_invalid_definitions(tmp_path, mutate, message):
    path = packet_file(tmp_path)
    raw = yaml.safe_load(path.read_text())
    mutate(raw)
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match=message):
        load_packet_definition(path)


def test_output_files_final_byte_time_and_legacy_headers(tmp_path):
    board = load_board(ROOT / 'example-configs/stm32f407vg.yaml')
    packet = packet_file(tmp_path)
    board = replace(board, telemetry=replace(board.telemetry, packet_definition=packet))
    raw = b'noise\xa5\x5a\xfe\xff\xa5\x5a\x01'
    for timed in (False, True):
        events = tuple(UARTByteEvent(v, 0x08000000, i+1, i / 100 if timed else None)
                       for i, v in enumerate(raw))
        trace = ExecutionTrace((), len(raw), (), events, 0,
                               (GPIOTransition(1, 0x08000000, 1, 'PA5', .01 if timed else None),),
                               .2 if timed else None)
        output = tmp_path / str(timed)
        write_configured_outputs(trace, board, output)
        assert (output / board.telemetry.file).read_bytes() == raw
        assert (output / 'telemetry.decoded.csv').read_text() == (
            'time_s,value\n' + ('0.08' if timed else '') + ',-2\n')
        header = (output / board.capture.file).read_text().splitlines()[0]
        assert header == 'sequence,instruction_count,pc,pin,level' + (',time_s' if timed else '')
        write_configured_outputs(trace, replace(board, telemetry=replace(board.telemetry, packet_definition=None)), tmp_path / f'raw{timed}')
        assert not (tmp_path / f'raw{timed}' / 'telemetry.decoded.csv').exists()


def test_definition_and_filename_collisions_validate_before_run(tmp_path):
    raw = yaml.safe_load((ROOT / 'example-configs/simulated-flight-board.yaml').read_text())
    raw['mcu']['svd'] = str(ROOT / 'tests/fixtures/svd/stm32f407-minimal.svd')
    raw['outputs']['telemetry']['packet_definition'] = str(packet_file(tmp_path))
    raw['outputs']['capture']['file'] = 'telemetry.decoded.csv'
    path = tmp_path / 'board.yaml'
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match='filenames must differ'):
        load_board(path)
    raw['outputs']['capture']['file'] = 'events.csv'
    path.write_text(yaml.safe_dump(raw))
    packet_file(tmp_path).write_text('invalid: true')
    with pytest.raises(ConfigError, match='packet missing'):
        load_board(path)
