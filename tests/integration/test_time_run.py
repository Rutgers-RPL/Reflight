"""The clock bounds actual ARM execution and preserves SysTick/NVIC behavior."""
from fractions import Fraction

import pytest

from reflight.emulation.qiling_runner import run_firmware
from reflight.simulation.clock import VirtualClock


@pytest.mark.parametrize('end, reason, budget, expected, elapsed', [
    (.002, 'duration', 1000, 'duration', .002),
    (.002, 'csv_end', 1000, 'csv_end', .002),
    (.02, 'duration', 100, 'instruction_cap', .001),
    (0, 'csv_end', 100, 'csv_end', 0),
    (.000015, 'duration', 100, 'duration', .000015),
    (.001, 'duration', 100, 'duration', .001),
])
def test_exact_endpoint_and_cap(compile_arm, end, reason, budget, expected, elapsed):
    elf = compile_arm('fixture.c')
    runs = []
    for _ in range(2):
        clock = VirtualClock(100000, end, reason)
        samples = []
        clock.schedule('sensor', 1000, samples.append)
        trace = run_firmware(elf, budget, clock=clock)
        assert trace.stop_reason == expected
        assert trace.elapsed_time_s == elapsed
        assert trace.instruction_count <= budget
        assert all(event.time_s < elapsed for event in trace.uart_events)
        assert clock.now == Fraction(str(elapsed))
        if end == .002:
            assert samples == [0, .001, .002]
            assert trace.instruction_count == 200
        runs.append((trace, samples))
    assert runs[0] == runs[1]


def test_real_systick_interrupt_progress(compile_arm):
    elf = compile_arm('timer_progress.c')
    clock = VirtualClock(100000, .02)
    trace = run_firmware(elf, 5000, clock=clock)
    assert trace.stop_reason == 'duration'
    assert trace.instruction_count == 2000
    assert trace.uart_bytes == b'T'
    assert 0 < trace.uart_events[0].time_s < .02
