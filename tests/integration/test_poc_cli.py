"""End-to-end proof using freshly compiled ARM firmware and the installed CLI."""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).parents[2]
FIXTURE = REPOSITORY / "tests" / "fixtures" / "firmware"


def _cli() -> Path:
    name = "reflight.exe" if os.name == "nt" else "reflight"
    command = Path(sysconfig.get_path("scripts")) / name
    assert command.is_file(), "Install the package with: python -m pip install -e '.[test]'"
    return command


def _compiler() -> str:
    override = os.environ.get("ARM_NONE_EABI_GCC")
    command = override or shutil.which("arm-none-eabi-gcc")
    assert command, (
        "arm-none-eabi-gcc is required; set ARM_NONE_EABI_GCC to its executable "
        "or add it to PATH"
    )
    assert Path(command).is_file(), f"ARM compiler not found: {command}"
    return command


def test_installed_cli_runs_fresh_external_elf(tmp_path: Path) -> None:
    firmware = tmp_path / "firmware.elf"
    build = subprocess.run(
        [
            _compiler(), "-mcpu=cortex-m4", "-mthumb", "-ffreestanding",
            "-fno-builtin", "-Os", "-Wall", "-Wextra", "-Werror", "-nostdlib",
            "-Wl,--gc-sections", "-T", str(FIXTURE / "linker.ld"),
            str(FIXTURE / "fixture.c"), "-o", str(firmware),
        ],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert firmware.is_file()

    output_dir = tmp_path / "chosen-output"
    run = subprocess.run(
        [str(_cli()), "poc", "--firmware", str(firmware),
         "--output-dir", str(output_dir), "--max-instructions", "400"],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Executed 400 instructions" in run.stdout
    assert (output_dir / "uart.bin").read_bytes() == b"REFLIGHT A\n"

    with (output_dir / "gpio_events.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert [(row["pin"], row["level"]) for row in rows] == [
        ("PA5", "1"), ("PA5", "0")
    ]
    assert [int(row["instruction_count"]) for row in rows] == sorted(
        int(row["instruction_count"]) for row in rows
    )
    assert all(0x08000000 <= int(row["pc"], 16) < 0x08100000 for row in rows)


@pytest.mark.parametrize("budget", ["0", "-1", "abc"])
def test_invalid_budget_does_not_create_outputs(tmp_path: Path, budget: str) -> None:
    output_dir = tmp_path / "output"
    run = subprocess.run(
        [str(_cli()), "poc", "--firmware", str(tmp_path / "missing.elf"),
         "--output-dir", str(output_dir), "--max-instructions", budget],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode != 0
    assert "positive integer" in run.stderr
    assert not output_dir.exists()


@pytest.mark.parametrize("invalid_file", [False, True])
def test_missing_or_invalid_firmware_does_not_create_outputs(
    tmp_path: Path, invalid_file: bool
) -> None:
    firmware = tmp_path / "invalid.elf"
    if invalid_file:
        firmware.write_bytes(b"not an ARM ELF")
    output_dir = tmp_path / "output"
    run = subprocess.run(
        [str(_cli()), "poc", "--firmware", str(firmware),
         "--output-dir", str(output_dir)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode != 0
    assert "reflight:" in run.stderr
    assert not output_dir.exists()
