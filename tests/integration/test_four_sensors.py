"""Compiled ARM fixture reads all five SPI endpoints before USART transmission."""

from __future__ import annotations

import copy
import csv
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest
import yaml

from reflight.config import load_board
from reflight.emulation.qiling_runner import run_firmware, validate_board_runtime
from reflight.outputs import write_configured_outputs
from reflight.sensors import build_spi_bus

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "example-configs/stm32f407vg-four-sensors.yaml"
SVD = ROOT / "tests/fixtures/svd/stm32f407-minimal.svd"
SOURCE = ROOT / "tests/fixtures/firmware"
DEFAULT = bytes.fromhex("e9 00 00 9c bf 8a a2 1a 1e 00 00 0f 00 00 50 7f 7f 7f 7f 7f 7f")
CHANGED = bytes.fromhex("e9 12 34 9c bf 01 02 03 1e 67 45 0f 45 23 50 33 22 11 66 55 44")


def _compiler() -> str:
    value = os.environ.get("ARM_NONE_EABI_GCC") or shutil.which("arm-none-eabi-gcc")
    assert value and Path(value).is_file(), "Set ARM_NONE_EABI_GCC or add arm-none-eabi-gcc to PATH"
    return value


@pytest.fixture(scope="module")
def arm_elf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    elf = tmp_path_factory.mktemp("phase3-arm") / "four_sensors.elf"
    build = subprocess.run([_compiler(), "-mcpu=cortex-m4", "-mthumb", "-ffreestanding",
                            "-fno-builtin", "-Os", "-Wall", "-Wextra", "-Werror",
                            "-nostdlib", "-Wl,--gc-sections", "-T", str(SOURCE / "linker.ld"),
                            str(SOURCE / "four_sensors.c"), "-o", str(elf)],
                           capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stderr
    return elf


def _board(tmp_path: Path) -> Path:
    shutil.copyfile(SVD, tmp_path / "minimal.svd")
    content = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    content["mcu"]["svd"] = "minimal.svd"
    path = tmp_path / "board.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def _cli() -> Path:
    path = Path(sysconfig.get_path("scripts")) / ("reflight.exe" if os.name == "nt" else "reflight")
    assert path.is_file(), "Install Reflight before integration tests"
    return path


def _invoke(elf: Path, board: Path, output: Path, cwd: Path,
            budget: int = 1500) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_cli()), "run", "--firmware", str(elf),
                           "--board", str(board), "--output-dir", str(output),
                           "--max-instructions", str(budget)], cwd=cwd,
                          capture_output=True, text=True, check=False)


def test_cli_real_firmware_all_sensors_from_unrelated_cwd(arm_elf: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    board = _board(tmp_path)
    output = tmp_path / "artifacts"
    result = _invoke(arm_elf, board, output, other)
    assert result.returncode == 0, result.stderr
    assert "Executed 1500 instructions" in result.stdout
    assert (output / "telemetry.bin").read_bytes() == DEFAULT
    with (output / "hardware_events.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 42
    assert [row["pin"] for row in rows] == (["PA4"] * 6 + ["PA5"] * 6 +
                                            ["PA6"] * 8 + ["PA7"] * 6 + ["PB0"] * 16)
    assert [int(row["level"]) for row in rows] == [0, 1] * 21
    assert [int(row["sequence"]) for row in rows] == list(range(1, 43))
    assert all(row["pc"].startswith("0x0800") for row in rows)


def test_injected_raw_samples_change_firmware_uart(arm_elf: Path, tmp_path: Path) -> None:
    board = load_board(_board(tmp_path))
    validate_board_runtime(board)
    first_bus = build_spi_bus(board)
    assert first_bus is not None
    first = run_firmware(arm_elf, 1500, gpio_pins=board.capture.pins, spi_bus=first_bus)
    assert first.uart_bytes == DEFAULT
    second_bus = build_spi_bus(board)
    assert second_bus is not None
    second_bus.inject_raw("PA4", accel=(0x1234, 0, 0))
    second_bus.inject_raw("PA5", d1=0x010203)
    second_bus.inject_raw("PA6", accel=(0x4567, 0, 0))
    second_bus.inject_raw("PA7", gyro=(0x2345, 0, 0))
    second_bus.inject_raw("PB0", temperature=0x112233, pressure=0x445566)
    second = run_firmware(arm_elf, 1500, gpio_pins=board.capture.pins, spi_bus=second_bus)
    assert second.instruction_count == 1500
    assert second.uart_bytes == CHANGED
    assert len(second.gpio_transitions) == 42
    assert any(write.address == 0x4001300C for write in second.writes)
    write_configured_outputs(second, board, tmp_path / "changed")
    assert (tmp_path / "changed/telemetry.bin").read_bytes() == CHANGED


def test_only_configured_cs_pins_are_captured(arm_elf: Path, tmp_path: Path) -> None:
    board_path = _board(tmp_path)
    data = yaml.safe_load(board_path.read_text(encoding="utf-8"))
    data["outputs"]["capture"]["sources"][0]["pins"] = ["PA4", "PB0"]
    board_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    board = load_board(board_path)
    bus = build_spi_bus(board)
    assert bus is not None
    trace = run_firmware(arm_elf, 1500, gpio_pins=board.capture.pins, spi_bus=bus)
    assert trace.uart_bytes == DEFAULT
    assert [event.pin for event in trace.gpio_transitions] == ["PA4"] * 6 + ["PB0"] * 16


@pytest.mark.parametrize("case, diagnostic", [
    ("model", "unsupported sensor model"),
    ("collision", "duplicate chip select"),
    ("spi_base", "SVD/Qiling mismatch for SPI1"),
    ("spi_dr", "SVD/Qiling mismatch for SPI1.DR"),
    ("cs_base", "SVD/Qiling mismatch for GPIOB"),
    ("controller", "unsupported runtime SPI controller"),
    ("i2c", "runtime controller transactions are not verified"),
    ("output", "reflight:"),
])
def test_runtime_rejections_before_artifacts(
    arm_elf: Path, tmp_path: Path, case: str, diagnostic: str
) -> None:
    board_path = _board(tmp_path)
    data = yaml.safe_load(board_path.read_text(encoding="utf-8"))
    svd_path = tmp_path / "minimal.svd"
    if case == "model":
        data["buses"]["SPI1"]["devices"][0]["model"] = "MysterySensor"
    elif case == "collision":
        data["buses"]["SPI1"]["devices"][1]["cs"] = "PA4"
    elif case == "controller":
        data["buses"]["SPI2"] = data["buses"].pop("SPI1")
        svd_path.write_text(SVD.read_text().replace("<name>SPI1</name>",
                                                  "<name>SPI2</name>"), encoding="utf-8")
    elif case == "i2c":
        data["buses"] = {"I2C1": {"devices": [{"model": "MS5611", "address": 0x77}]}}
    elif case in ("spi_base", "spi_dr", "cs_base"):
        content = SVD.read_text(encoding="utf-8")
        if case == "spi_base":
            content = content.replace("0x40013000", "0x40013100")
        elif case == "spi_dr":
            content = content.replace(
                "<name>DR</name><description>Data register</description>\n          <addressOffset>0x0C</addressOffset>",
                "<name>DR</name><description>Data register</description>\n          <addressOffset>0x10</addressOffset>")
        else:
            content = content.replace("0x40020400", "0x40020500")
        svd_path.write_text(content, encoding="utf-8")
    board_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    output = tmp_path / "output"
    if case == "output":
        output.write_text("already a file", encoding="utf-8")
    result = _invoke(arm_elf, board_path, output, tmp_path)
    assert result.returncode != 0
    assert diagnostic in result.stderr
    if case != "output":
        assert not output.exists()


def test_unsupported_spi_frame_mode_fails_without_outputs(tmp_path: Path) -> None:
    source = (SOURCE / "four_sensors.c").read_text(encoding="utf-8")
    assert "SPI1_CR1 = 0x344u;" in source
    bad = tmp_path / "bad_mode.c"
    bad.write_text(source.replace("SPI1_CR1 = 0x344u;", "SPI1_CR1 = 0xb44u;"),
                   encoding="utf-8")  # DFF=1, 16-bit frame.
    elf = tmp_path / "bad_mode.elf"
    build = subprocess.run([_compiler(), "-mcpu=cortex-m4", "-mthumb",
                            "-ffreestanding", "-fno-builtin", "-Os", "-Wall",
                            "-Wextra", "-Werror", "-nostdlib", "-Wl,--gc-sections",
                            "-T", str(SOURCE / "linker.ld"), str(bad), "-o", str(elf)],
                           capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stderr
    board = _board(tmp_path)
    output = tmp_path / "no-output"
    result = _invoke(elf, board, output, tmp_path)
    assert result.returncode != 0
    assert "unsupported SPI1 mode" in result.stderr
    assert not output.exists()
