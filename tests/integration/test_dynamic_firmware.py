"""Compiled polling firmware sees changing latched sensor values through SPI MMIO."""
from pathlib import Path
import csv
import struct

from reflight.config import load_board
from reflight.config.scenario import load_scenario
from reflight.emulation.qiling_runner import run_firmware
from reflight.sensors import build_spi_bus
from reflight.simulation.playback import build_simulation
from reflight.outputs import write_configured_outputs

ROOT = Path(__file__).parents[2]


def test_dynamic_reads_and_firmware_gpio(compile_arm, tmp_path):
    elf = compile_arm('simulated_flight.c')
    board = load_board(ROOT / 'example-configs/simulated-flight-board.yaml')
    scenario = load_scenario(ROOT / 'example-configs/raw-flight-scenario.yaml', board)
    bus = build_spi_bus(board)
    trace = run_firmware(elf, 30000, spi_bus=bus, gpio_pins=board.capture.pins,
                         clock=build_simulation(scenario, bus))
    raw = trace.uart_bytes
    packets = [struct.unpack('<hhI', raw[i+2:i+10]) for i in range(0, len(raw)-9, 10)]
    assert len(packets) >= 10
    assert all(raw[i:i+2] == b'\xa5\x5a' for i in range(0, len(packets)*10, 10))
    for channel in (0, 1):
        values = [p[channel] for p in packets]
        assert values == sorted(values)
        assert values[0] == (102 if channel == 0 else 546)
        assert values[-1] >= (1100 if channel == 0 else 6000)
        assert len(set(values)) > 5
    for channel, (rate, scale) in enumerate(((100, 1024), (200, 32768/6))):
        # Independent physical interpolation and each model's datasheet sensitivity.
        golden = {round((.1 + 10*t if t <= .03 else .4 + 20*(t-.03))*scale)
                  for t in (i/rate for i in range(int(.08*rate)+1))}
        assert {p[channel] for p in packets} <= golden
    assert any(a[0] == b[0] for a, b in zip(packets, packets[1:]))
    assert any(a[0] == b[0] and a[1] != b[1] for a, b in zip(packets, packets[1:]))  # 100 Hz vs 200 Hz
    assert all(ms == 9155145 for _, _, ms in packets)
    assert [(e.pin, e.level) for e in trace.gpio_transitions] == [('PB1', 1)]
    assert .04 <= trace.gpio_transitions[0].time_s < .05
    assert trace.stop_reason == 'duration'
    assert trace.elapsed_time_s == .08
    assert any(w.address == 0x4001300c for w in trace.writes)
    first = tmp_path / 'first'
    write_configured_outputs(trace, board, first)
    with (first / 'telemetry.decoded.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert [(int(r['icm_accel_x']), int(r['bmi_accel_x']), int(r['ms_d1'])) for r in rows] == packets
    assert [float(r['time_s']) for r in rows] == [trace.uart_events[10*i+9].time_s for i in range(len(packets))]
    with (first / 'hardware_events.csv').open(newline='') as stream:
        gpio = list(csv.DictReader(stream))
    assert [(r['pin'], r['level']) for r in gpio] == [('PB1', '1')]
    assert float(gpio[0]['time_s']) == trace.gpio_transitions[0].time_s
    bus = build_spi_bus(board)
    rerun = run_firmware(elf, 30000, spi_bus=bus, gpio_pins=board.capture.pins,
                          clock=build_simulation(scenario, bus))
    second = tmp_path / 'second'
    write_configured_outputs(rerun, board, second)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == {p.name: p.read_bytes() for p in second.iterdir()}
