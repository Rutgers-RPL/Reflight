"""Write selected raw USART bytes and effective GPIO transitions."""

from __future__ import annotations

import csv
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, BinaryIO

from reflight.config import BoardConfig
from reflight.emulation.qiling_runner import ExecutionTrace
from .packets import PacketDecoder, decoded_filename, load_packet_definition

if TYPE_CHECKING:
    from reflight.buses import SpiBus
    from reflight.flash import NandStorage


def _write_flash_image(storage: NandStorage, stream: BinaryIO) -> None:
    """Bounded main-only export, also usable with controlled test sinks."""
    total = 0
    for page in storage.iter_main_pages():
        if stream.write(page) != len(page):
            raise OSError("short write exporting flash image")
        total += len(page)
    if total != storage.main_size:
        raise OSError(f"incorrect flash image size: {total}")


def _export_flash(storage: NandStorage, path: Path) -> None:
    """Publish only complete images; preserve an older output on failure."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".reflight-flash-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            _write_flash_image(storage, stream)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_configured_outputs(trace: ExecutionTrace, board: BoardConfig,
                             output_dir: str | Path, *, spi_bus: SpiBus | None = None) -> None:
    destination = Path(output_dir)
    definition = (load_packet_definition(board.telemetry.packet_definition)
                  if board.telemetry.packet_definition is not None else None)
    flash = []
    if board.flash_outputs:
        from reflight.devices import FLASH_MODELS
        for spec in board.flash_outputs:
            bus_name, cs = spec.device.split('.', 1)
            device = (spi_bus.devices.get(cs)
                      if spi_bus is not None and spi_bus.name == bus_name else None)
            if not isinstance(device, tuple(FLASH_MODELS.values())):
                raise ValueError(f"flash output device not attached: {spec.device}")
            flash.append((device.storage, destination / spec.file))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / board.telemetry.file).write_bytes(trace.uart_bytes)
    with (destination / board.capture.file).open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, lineterminator="\n")
        timed = trace.elapsed_time_s is not None
        writer.writerow(("sequence", "instruction_count", "pc", "pin", "level") +
                        (("time_s",) if timed else ()))
        for sequence, event in enumerate(trace.gpio_transitions, start=1):
            writer.writerow((sequence, event.instruction_count,
                             f"0x{event.pc:08x}", event.pin, event.level) +
                            ((event.time_s,) if timed else ()))
    if definition is not None:
        decoder = PacketDecoder(definition)
        with (destination / decoded_filename(board.telemetry.file)).open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(("time_s", *definition.fields))
            for event in trace.uart_events:
                for packet in decoder.feed(bytes((event.value,)), event.time_s):
                    writer.writerow((packet.time_s, *packet.values))
    for storage, path in flash:
        _export_flash(storage, path)
