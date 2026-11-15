"""Write selected raw USART bytes and effective GPIO transitions."""

from __future__ import annotations

import csv
from pathlib import Path

from reflight.config import BoardConfig
from reflight.emulation.qiling_runner import ExecutionTrace


def write_configured_outputs(trace: ExecutionTrace, board: BoardConfig,
                             output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / board.telemetry.file).write_bytes(trace.uart_bytes)
    with (destination / board.capture.file).open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("sequence", "instruction_count", "pc", "pin", "level"))
        for sequence, event in enumerate(trace.gpio_transitions, start=1):
            writer.writerow((sequence, event.instruction_count,
                             f"0x{event.pc:08x}", event.pin, event.level))
