"""Minimal fixed-size telemetry packets; no checksum or protocol inference."""

from dataclasses import dataclass
from pathlib import Path
import re
import struct

import yaml

from reflight.config.board import ConfigError, _mapping, _string

_TYPES = {'int8': 'b', 'uint8': 'B', 'int16': 'h', 'uint16': 'H',
          'int32': 'i', 'uint32': 'I', 'float32': 'f'}


@dataclass(frozen=True)
class PacketDefinition:
    sync: bytes
    fields: tuple[str, ...]
    payload: struct.Struct


@dataclass(frozen=True)
class DecodedPacket:
    time_s: float | None
    values: tuple[int | float, ...]


def decoded_filename(raw_file: str) -> str:
    return Path(raw_file).stem + '.decoded.csv'


def load_packet_definition(path: Path) -> PacketDefinition:
    try:
        raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f'cannot read packet definition {path}: {exc}') from exc
    item = _mapping(raw, 'packet', {'sync', 'byte_order', 'fields'}, {'sync', 'byte_order', 'fields'})
    sync = item['sync']
    if not isinstance(sync, list) or not sync or any(type(b) is not int or not 0 <= b <= 255 for b in sync):
        raise ConfigError('packet.sync must be a nonempty list of bytes (0..255)')
    if item['byte_order'] not in ('little', 'big'):
        raise ConfigError('packet.byte_order must be little or big')
    if not isinstance(item['fields'], list) or not item['fields']:
        raise ConfigError('packet.fields must be a nonempty ordered list')
    names, formats = [], []
    for entry in item['fields']:
        field = _mapping(entry, 'packet field', {'name', 'type'}, {'name', 'type'})
        name = _string(field['name'], 'packet field name')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or name == 'time_s' or name in names:
            raise ConfigError(f'packet field name must be unique, valid and not time_s: {name}')
        field_type = _string(field['type'], 'packet field type')
        if field_type not in _TYPES:
            raise ConfigError(f'unsupported packet field type {field_type}; supported: {", ".join(_TYPES)}')
        names.append(name)
        formats.append(_TYPES[field_type])
    payload = struct.Struct(('<' if item['byte_order'] == 'little' else '>') + ''.join(formats))
    return PacketDefinition(bytes(sync), tuple(names), payload)


class PacketDecoder:
    """First sync wins; consume its fixed payload, preserving a partial sync tail.

    Without a checksum, sync bytes inside a payload are data. Corrupt payloads
    cannot be identified; framing resumes at the next sync after that payload.
    """

    def __init__(self, definition: PacketDefinition):
        self.definition = definition
        self.buffer = bytearray()

    def feed(self, data: bytes, time_s: float | None = None) -> list[DecodedPacket]:
        self.buffer.extend(data)
        definition = self.definition
        size = len(definition.sync) + definition.payload.size
        packets = []
        while True:
            start = self.buffer.find(definition.sync)
            if start < 0:
                # At most len(sync)-1 bytes can begin the next sync sequence.
                keep = len(definition.sync) - 1
                if keep:
                    del self.buffer[:-keep]
                else:
                    self.buffer.clear()
                return packets
            if start:
                del self.buffer[:start]
            if len(self.buffer) < size:
                return packets
            values = definition.payload.unpack(self.buffer[len(definition.sync):size])
            packets.append(DecodedPacket(time_s, values))
            del self.buffer[:size]
