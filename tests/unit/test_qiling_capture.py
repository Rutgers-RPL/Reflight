"""Focused Task B checks for Qiling-backed USART and GPIO observation."""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from qiling.hw.gpio.stm32f4xx_gpio import STM32F4xxGpio

from reflight.emulation.qiling_runner import _GPIOOutputObserver, run_firmware


def _gpio_after_writes(writes: list[tuple[int, int]]) -> _GPIOOutputObserver:
    gpio = STM32F4xxGpio(SimpleNamespace(log=logging.getLogger(__name__)), "gpioa")
    observer = _GPIOOutputObserver(pin=5)
    for order, (offset, value) in enumerate(writes, start=1):
        gpio.write(offset, 4, value)
        observer.observe(gpio.instance.MODER, gpio.instance.ODR,
                         pc=0x08000100 + 2 * order, instruction_count=order)
    return observer


def test_bsrr_and_odr_only_emit_effective_pa5_changes() -> None:
    observer = _gpio_after_writes([
        (0x00, 1 << 10),       # PA5 output; establish LOW baseline.
        (0x18, 1 << 5),        # BSRR set -> HIGH.
        (0x18, 1 << 5),        # Same-state set -> no transition.
        (0x14, 1 << 5),        # ODR same-state write -> no transition.
        (0x14, 0),             # ODR clear -> LOW.
        (0x14, 0),             # Same-state clear -> no transition.
        (0x18, 1 << 21),       # BSRR same-state reset -> no transition.
    ])

    assert observer.output_baseline == 0
    assert [event.level for event in observer.transitions] == [1, 0]
    assert [event.instruction_count for event in observer.transitions] == [2, 5]


def test_input_latch_is_baseline_when_pin_becomes_output() -> None:
    observer = _gpio_after_writes([
        (0x18, 1 << 5),        # ODR latch set while PA5 is input.
        (0x00, 1 << 10),       # Output begins HIGH; baseline, not edge.
        (0x18, 1 << 21),       # Reset -> LOW transition.
    ])

    assert observer.output_baseline == 1
    assert [event.level for event in observer.transitions] == [0]
    assert observer.transitions[0].instruction_count == 3


def test_compiled_firmware_produces_ordered_uart_and_gpio() -> None:
    firmware = Path(__file__).parents[1] / "fixtures/firmware/build/fixture.elf"
    if not firmware.exists():
        pytest.skip("build the ARM fixture using tests/fixtures/firmware/README.md")

    early = run_firmware(firmware, max_instructions=10)
    assert early.instruction_count == 10
    assert early.uart_bytes == b""
    assert early.gpio_transitions == ()

    trace = run_firmware(firmware, max_instructions=400)

    assert trace.instruction_count == 400
    assert 0x08000000 <= trace.first_pcs[0] < 0x08100000
    assert trace.uart_bytes == b"REFLIGHT A\n"
    assert [event.level for event in trace.gpio_transitions] == [1, 0]
    assert trace.gpio_output_baseline == 0
    assert trace.gpio_transitions[0].instruction_count < \
        trace.gpio_transitions[1].instruction_count
    dr_writes = [write for write in trace.writes if write.address == 0x40004404]
    assert [event.pc for event in trace.uart_events] == \
        [write.pc for write in dr_writes]
    assert max(event.instruction_count for event in trace.uart_events) < \
        trace.gpio_transitions[0].instruction_count
