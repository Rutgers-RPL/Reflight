"""Configured outputs verified against actual compiled ARM execution."""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[2]
FIRMWARE = ROOT / "tests/fixtures/firmware"
SVD = ROOT / "tests/fixtures/svd/stm32f407-minimal.svd"
EXAMPLE = ROOT / "example-configs/stm32f407vg.yaml"


def _cli() -> Path:
    name = "reflight.exe" if os.name == "nt" else "reflight"
    path = Path(sysconfig.get_path("scripts")) / name
    assert path.is_file(), "Install Reflight before running integration tests"
    return path


@pytest.fixture(scope="module")
def arm_elf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = os.environ.get("ARM_NONE_EABI_GCC") or shutil.which("arm-none-eabi-gcc")
    assert compiler and Path(compiler).is_file(), "Set ARM_NONE_EABI_GCC or add arm-none-eabi-gcc to PATH"
    target = tmp_path_factory.mktemp("phase2-arm") / "fixture.elf"
    build = subprocess.run(
        [compiler, "-mcpu=cortex-m4", "-mthumb", "-ffreestanding", "-fno-builtin",
         "-Os", "-Wall", "-Wextra", "-Werror", "-nostdlib", "-Wl,--gc-sections",
         "-T", str(FIRMWARE / "linker.ld"), str(FIRMWARE / "fixture.c"),
         "-o", str(target)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return target


def _board(tmp_path: Path, *, uart: str = "USART2",
           pins: tuple[str, ...] = ("PA5",), mismatch: bool = False) -> Path:
    source = tmp_path / "minimal.svd"
    contents = SVD.read_text(encoding="utf-8")
    if mismatch:
        contents = contents.replace("0x40004400", "0x40004500")
    source.write_text(contents, encoding="utf-8")
    board = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    board["mcu"]["svd"] = "minimal.svd"
    board["outputs"]["telemetry"]["peripheral"] = uart
    board["outputs"]["telemetry"]["file"] = "chosen-uart.bin"
    board["outputs"]["capture"]["file"] = "chosen-gpio.csv"
    board["outputs"]["capture"]["sources"][0]["pins"] = list(pins)
    path = tmp_path / "board.yaml"
    path.write_text(yaml.safe_dump(board), encoding="utf-8")
    return path


def _run(elf: Path, board: Path, output_dir: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_cli()), "run", "--firmware", str(elf), "--board", str(board),
         "--output-dir", str(output_dir), "--max-instructions", "400"],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def test_real_firmware_selected_outputs_and_paths(arm_elf: Path, tmp_path: Path) -> None:
    board = _board(tmp_path, pins=("PA4", "PA5", "PB0"))
    other = tmp_path / "other"
    other.mkdir()
    output = tmp_path / "chosen-output"
    result = _run(arm_elf, board, output, other)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Executed 400 instructions" in result.stdout
    assert (output / "chosen-uart.bin").read_bytes() == b"REFLIGHT A\n"
    with (output / "chosen-gpio.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [(row["sequence"], row["instruction_count"], row["pc"],
             row["pin"], row["level"]) for row in rows] == [
                 ("1", "114", "0x08000076", "PA5", "1"),
                 ("2", "116", "0x0800007c", "PA5", "0"),
             ]
    assert not (output / "uart.bin").exists()
    assert not (output / "gpio_events.csv").exists()


def test_other_selected_peripherals_capture_nothing(arm_elf: Path, tmp_path: Path) -> None:
    board = _board(tmp_path, uart="USART1", pins=("PA4", "PB0"))
    output = tmp_path / "output"
    result = _run(arm_elf, board, output, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (output / "chosen-uart.bin").read_bytes() == b""
    assert (output / "chosen-gpio.csv").read_text(encoding="utf-8") == \
        "sequence,instruction_count,pc,pin,level\n"


@pytest.mark.parametrize("case, message", [
    ("mismatch", "SVD/Qiling mismatch"),
    ("unknown", "unknown SVD peripheral"),
    ("firmware", "reflight:"),
    ("output", "reflight:"),
])
def test_errors_return_nonzero_without_artifacts(
    arm_elf: Path, tmp_path: Path, case: str, message: str
) -> None:
    board = _board(tmp_path, uart="USART9" if case == "unknown" else "USART2",
                   mismatch=case == "mismatch")
    output = tmp_path / "output"
    firmware = arm_elf
    if case == "firmware":
        firmware = tmp_path / "missing.elf"
    if case == "output":
        output.write_text("existing file", encoding="utf-8")
    result = _run(firmware, board, output, tmp_path)
    assert result.returncode != 0
    assert message in result.stderr
    if case == "output":
        assert output.is_file()
    else:
        assert not output.exists()


def test_repeated_firmware_gpio_writes_emit_only_transitions(tmp_path: Path) -> None:
    compiler = os.environ.get("ARM_NONE_EABI_GCC") or shutil.which("arm-none-eabi-gcc")
    assert compiler and Path(compiler).is_file()
    source = (FIRMWARE / "fixture.c").read_text(encoding="utf-8")
    source = source.replace(
        "GPIOA_BSRR = 1u << 5;",
        "GPIOA_BSRR = 1u << 5; GPIOA_BSRR = 1u << 5;",
    ).replace(
        "GPIOA_BSRR = 1u << (5 + 16);",
        "GPIOA_BSRR = 1u << (5 + 16); GPIOA_BSRR = 1u << (5 + 16);",
    )
    assert source.count("GPIOA_BSRR = 1u << 5;") == 2
    repeated_source = tmp_path / "repeated.c"
    repeated_source.write_text(source, encoding="utf-8")
    firmware = tmp_path / "repeated.elf"
    build = subprocess.run(
        [compiler, "-mcpu=cortex-m4", "-mthumb", "-ffreestanding", "-fno-builtin",
         "-Os", "-Wall", "-Wextra", "-Werror", "-nostdlib", "-Wl,--gc-sections",
         "-T", str(FIRMWARE / "linker.ld"), str(repeated_source), "-o", str(firmware)],
        capture_output=True, text=True, check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    board = _board(tmp_path)
    output = tmp_path / "output"
    result = _run(firmware, board, output, tmp_path)
    assert result.returncode == 0, result.stderr
    with (output / "chosen-gpio.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["level"] for row in rows] == ["1", "0"]


def test_phase1_poc_remains_compatible(arm_elf: Path, tmp_path: Path) -> None:
    output = tmp_path / "legacy"
    result = subprocess.run(
        [str(_cli()), "poc", "--firmware", str(arm_elf),
         "--output-dir", str(output), "--max-instructions", "400"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (output / "uart.bin").read_bytes() == b"REFLIGHT A\n"
    assert (output / "gpio_events.csv").read_text(encoding="utf-8") == (
        "instruction_count,pc,pin,level\n"
        "114,0x08000076,PA5,1\n"
        "116,0x0800007c,PA5,0\n"
    )
