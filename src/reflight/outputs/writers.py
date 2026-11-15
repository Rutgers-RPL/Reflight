"""Write selected raw USART bytes and effective GPIO transitions."""

from __future__ import annotations

import csv
from pathlib import Path

from reflight.config import BoardConfig
from reflight.emulation.qiling_runner import ExecutionTrace
from .packets import PacketDecoder, decoded_filename, load_packet_definition


def write_configured_outputs(trace: ExecutionTrace, board: BoardConfig,
                             output_dir: str | Path) -> None:
    destination = Path(output_dir)
    definition = (load_packet_definition(board.telemetry.packet_definition)
                  if board.telemetry.packet_definition is not None else None)
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
