"""Strict, path-stable board configuration tests."""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from reflight.config import ConfigError, load_board


ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "tests/fixtures/boards/phase2-wiring.yaml"
SVD = ROOT / "tests/fixtures/svd/stm32f407-minimal.svd"


def _board(tmp_path: Path, data: dict | None = None) -> tuple[Path, dict]:
    content = copy.deepcopy(data if data is not None else yaml.safe_load(EXAMPLE.read_text()))
    shutil.copyfile(SVD, tmp_path / "minimal.svd")
    content["mcu"]["svd"] = "minimal.svd"
    path = tmp_path / "board.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path, content


def test_valid_board_is_structured_and_cwd_independent(tmp_path: Path, monkeypatch) -> None:
    path, content = _board(tmp_path)
    packet = tmp_path / "packet.yaml"
    packet.write_text("future: only\n", encoding="utf-8")
    content["outputs"]["telemetry"]["packet_definition"] = "packet.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    board = load_board(path)
    assert board.model == "STM32F407VG"
    assert board.svd_path == (tmp_path / "minimal.svd").resolve()
    assert board.telemetry.packet_definition == packet.resolve()
    assert board.telemetry.peripheral == "USART2"
    assert board.telemetry.handle == "huart2"
    assert board.capture.pins == ("PA5",)
    buses = {bus.name: bus for bus in board.buses}
    assert set(buses) == {"SPI1", "I2C1"}
    assert buses["SPI1"].kind == "spi"
    assert buses["SPI1"].devices[0].model == "ExampleSpiDevice"
    assert buses["SPI1"].devices[0].cs == "PA4"
    assert buses["I2C1"].kind == "i2c"
    assert buses["I2C1"].devices[0].address == 0x77


@pytest.mark.parametrize("case, message", [
    ("schema", "schema_version"), ("svd", "mcu.svd"),
    ("model", "unsupported mcu.model"), ("bus", "unknown SVD peripheral"),
    ("cs", "duplicate chip select"), ("address", "duplicate I2C address"),
    ("pin", "GPIO pin"), ("format", "supports only raw"),
    ("capture_type", "unsupported"), ("file", "inside --output-dir"),
    ("collision", "filenames must differ"), ("unknown", "unknown keys"),
    ("reserved", "inside --output-dir"),
])
def test_invalid_board_rejected(tmp_path: Path, case: str, message: str) -> None:
    path, content = _board(tmp_path)
    buses = content["buses"]
    outputs = content["outputs"]
    if case == "schema":
        content["schema_version"] = 2
    elif case == "svd":
        content["mcu"]["svd"] = "missing.svd"
    elif case == "model":
        content["mcu"]["model"] = "STM32F405RG"
    elif case == "bus":
        buses["SPI99"] = buses.pop("SPI1")
    elif case == "cs":
        buses["SPI1"]["devices"].append(copy.deepcopy(buses["SPI1"]["devices"][0]))
    elif case == "address":
        buses["I2C1"]["devices"].append(copy.deepcopy(buses["I2C1"]["devices"][0]))
    elif case == "pin":
        outputs["capture"]["sources"][0]["pins"] = ["PZ20"]
    elif case == "format":
        outputs["telemetry"]["format"] = "csv"
    elif case == "capture_type":
        outputs["capture"]["sources"][0]["type"] = "spi"
    elif case == "file":
        outputs["telemetry"]["file"] = "../escape.bin"
    elif case == "collision":
        outputs["capture"]["file"] = outputs["telemetry"]["file"]
    elif case == "reserved":
        outputs["capture"]["file"] = "CON"
    elif case == "unknown":
        content["unexpected"] = True
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_board(path)


def test_validation_cli_from_unrelated_directory(tmp_path: Path) -> None:
    path, _ = _board(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "reflight.cli", "config", "validate", "--board", str(path)],
        cwd=other, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "valid: STM32F407VG" in result.stdout


def test_handle_is_optional_and_buses_can_be_empty(tmp_path: Path) -> None:
    path, content = _board(tmp_path)
    content["buses"] = {}
    del content["outputs"]["telemetry"]["handle"]
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    board = load_board(path)
    assert board.buses == ()
    assert board.telemetry.handle is None
    assert board.telemetry.peripheral == "USART2"
