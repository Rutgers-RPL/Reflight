"""Actual Cortex-M SPI1 MMIO must round-trip through the Python bus."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from reflight.buses import SpiBus, SpiError
from reflight.emulation.qiling_runner import run_firmware
from qiling import Qiling
from qiling.const import QL_ARCH, QL_OS, QL_VERBOSE
from qiling.extensions.mcu.stm32f4.stm32f407 import stm32f407


ROOT = Path(__file__).parents[2]


class XorDevice:
    def __init__(self) -> None:
        self.selected = 0
        self.deselected = 0
        self.received: list[int] = []

    def select(self) -> None:
        self.selected += 1

    def deselect(self) -> None:
        self.deselected += 1

    def exchange_byte(self, value: int) -> int:
        self.received.append(value)
        return value ^ 0x5A


def test_spi_bus_selection_and_order() -> None:
    bus = SpiBus()
    a, b = XorDevice(), XorDevice()
    bus.attach("PA4", a)
    bus.attach("PB0", b)
    try:
        bus.exchange_byte(1)
        assert False, "unselected transfer should fail"
    except SpiError:
        pass
    bus.set_cs("PA4", 0)
    assert [bus.exchange_byte(x) for x in (0x12, 0xA5)] == [0x48, 0xFF]
    bus.set_cs("PB0", 0)
    try:
        bus.exchange_byte(2)
        assert False, "collision should fail"
    except SpiError:
        pass
    bus.set_cs("PA4", 1)
    assert bus.exchange_byte(3) == 0x59
    bus.set_cs("PB0", 1)
    assert (a.received, b.received) == ([0x12, 0xA5], [3])
    assert (a.selected, a.deselected) == (1, 1)


def test_compiled_firmware_spi_roundtrip(tmp_path: Path) -> None:
    compiler = os.environ.get("ARM_NONE_EABI_GCC") or shutil.which("arm-none-eabi-gcc")
    assert compiler and Path(compiler).is_file(), "Set ARM_NONE_EABI_GCC or add arm-none-eabi-gcc to PATH"
    elf = tmp_path / "spi_dummy.elf"
    source = ROOT / "tests/fixtures/firmware"
    build = subprocess.run([compiler, "-mcpu=cortex-m4", "-mthumb", "-ffreestanding",
                            "-fno-builtin", "-Os", "-Wall", "-Wextra", "-Werror",
                            "-nostdlib", "-Wl,--gc-sections", "-T", str(source / "linker.ld"),
                            str(source / "spi_dummy.c"), "-o", str(elf)],
                           capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stderr
    bus = SpiBus()
    device = XorDevice()
    bus.attach("PA4", device)
    trace = run_firmware(elf, 400, spi_bus=bus, gpio_pins=("PA4",))
    assert trace.instruction_count == 400
    assert trace.uart_bytes == b"\x48\xff"
    assert device.received == [0x12, 0xA5]
    assert (device.selected, device.deselected) == (1, 1)
    assert [e.level for e in trace.gpio_transitions] == [0, 1]
    assert any(w.address == 0x4001300C for w in trace.writes)


def test_qiling_i2c_start_has_no_external_device_path(tmp_path: Path) -> None:
    """Genuine ELF probe: profile names a model missing from installed Qiling 1.4.6."""
    compiler = os.environ.get("ARM_NONE_EABI_GCC") or shutil.which("arm-none-eabi-gcc")
    assert compiler and Path(compiler).is_file(), "Set ARM_NONE_EABI_GCC or add arm-none-eabi-gcc to PATH"
    source = ROOT / "tests/fixtures/firmware"
    elf = tmp_path / "i2c_probe.elf"
    build = subprocess.run([compiler, "-mcpu=cortex-m4", "-mthumb", "-ffreestanding",
                            "-fno-builtin", "-Os", "-Wall", "-Wextra", "-Werror",
                            "-nostdlib", "-Wl,--gc-sections", "-T", str(source / "linker.ld"),
                            str(source / "i2c_probe.c"), "-o", str(elf)],
                           capture_output=True, text=True, check=False)
    assert build.returncode == 0, build.stderr
    ql = Qiling([str(elf)], rootfs=".", archtype=QL_ARCH.CORTEX_M,
                ostype=QL_OS.MCU, env=stm32f407.copy(), verbose=QL_VERBOSE.OFF)
    ql.hw.create("rcc")
    assert ql.hw.create("i2c1") is None
    ql.run(count=80)
    assert ql.arch.regs.pc == 0x08000028
    assert ql.hw.i2c1 is None
