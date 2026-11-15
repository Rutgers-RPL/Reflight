"""Command-line proof of concept for bounded firmware execution."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reflight")
    commands = parser.add_subparsers(dest="command", required=True)
    poc = commands.add_parser("poc", help="run the STM32F407 USART2/PA5 proof of concept")
    poc.add_argument("--firmware", type=Path, required=True, help="Flash-linked ARM ELF")
    poc.add_argument("--output-dir", type=Path, required=True)
    poc.add_argument("--max-instructions", type=_positive_int, default=1000)
    svd = commands.add_parser("svd", help="inspect CMSIS-SVD register metadata")
    svd_commands = svd.add_subparsers(dest="svd_command", required=True)
    for name in ("peripherals", "registers", "lookup"):
        command = svd_commands.add_parser(name)
        command.add_argument("--file", type=Path, required=True)
        if name == "registers":
            command.add_argument("--peripheral", required=True)
        elif name == "lookup":
            command.add_argument("--address", type=lambda value: int(value, 0), required=True)
    config = commands.add_parser("config", help="validate board configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    validate = config_commands.add_parser("validate")
    validate.add_argument("--board", type=Path, required=True)
    validate.add_argument("--scenario", type=Path)
    configured_run = commands.add_parser("run", help="run firmware with a validated board")
    configured_run.add_argument("--firmware", type=Path, required=True)
    configured_run.add_argument("--board", type=Path, required=True)
    configured_run.add_argument("--scenario", type=Path)
    configured_run.add_argument("--output-dir", type=Path, required=True)
    configured_run.add_argument("--max-instructions", type=_positive_int, default=1000)
    args = parser.parse_args(argv)

    if args.command == "run":
        from reflight.config import load_board
        from reflight.emulation.qiling_runner import run_firmware, validate_board_runtime
        from reflight.outputs import write_configured_outputs
        from reflight.devices import build_spi_bus

        try:
            board = load_board(args.board)
            clock = None
            scenario = None
            if args.scenario:
                from reflight.config.scenario import load_scenario
                scenario = load_scenario(args.scenario, board)
            validate_board_runtime(board)
            spi_bus = build_spi_bus(board)
            if scenario is not None:
                from reflight.simulation.playback import build_simulation
                clock = build_simulation(scenario, spi_bus)
            trace = run_firmware(
                args.firmware, args.max_instructions,
                uart_peripheral=board.telemetry.peripheral,
                gpio_pins=board.capture.pins,
                spi_bus=spi_bus,
                clock=clock,
            )
            write_configured_outputs(trace, board, args.output_dir, spi_bus=spi_bus)
        except Exception as exc:
            print(f"reflight: {exc}", file=sys.stderr)
            return 1
        print(f"Executed {trace.instruction_count} instructions; wrote {args.output_dir}")
        if clock is not None:
            print(f"Simulation time {trace.elapsed_time_s:g} s; stop reason: {trace.stop_reason}")
        return 0

    if args.command == "config":
        from reflight.config import ConfigError, load_board

        try:
            board = load_board(args.board)
            if args.scenario:
                from reflight.config.scenario import load_scenario
                load_scenario(args.scenario, board)
        except (ConfigError, ValueError) as exc:
            print(f"reflight: {exc}", file=sys.stderr)
            return 1
        print(f"valid: {board.model}; SVD {board.svd_path}; "
              f"{len(board.buses)} buses; {len(board.capture.pins)} capture pins")
        return 0

    if args.command == "svd":
        from reflight.svd import RegisterMap

        try:
            index = RegisterMap.from_file(args.file)
            if args.svd_command == "peripherals":
                for peripheral in index.list_peripherals():
                    print(f"{peripheral.name} 0x{peripheral.base_address:08x}")
            elif args.svd_command == "registers":
                for register in index.get_peripheral(args.peripheral).registers:
                    print(f"{register.name} 0x{register.address:08x}")
            else:
                register = index.lookup_address(args.address)
                print(f"{register.peripheral}.{register.name} 0x{register.address:08x}")
        except (OSError, ValueError) as exc:
            print(f"reflight: {exc}", file=sys.stderr)
            return 1
        return 0

    from reflight.emulation.qiling_runner import run_firmware

    try:
        # Execute before creating artifacts so a failed load cannot look successful.
        trace = run_firmware(args.firmware, args.max_instructions)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "uart.bin").write_bytes(trace.uart_bytes)
        with (args.output_dir / "gpio_events.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(("instruction_count", "pc", "pin", "level"))
            for event in trace.gpio_transitions:
                writer.writerow((event.instruction_count, f"0x{event.pc:08x}",
                                 "PA5", event.level))
    except Exception as exc:
        print(f"reflight: {exc}", file=sys.stderr)
        return 1

    print(f"Executed {trace.instruction_count} instructions; wrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
