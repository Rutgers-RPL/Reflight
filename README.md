# Reflight

Reflight is a software-in-the-loop framework for testing firmware. It runs the actual firmware with Qiling, feeds simulated sensor data, and captures UART telemetry and GPIO events.

Currently verified: STM32F407VG with SPI1 polling. I2C execution is not supported.

## Setup

Requires Python 3.12+ and `arm-none-eabi-gcc` on `PATH`. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

## Run the example

Build the synthetic firmware fixture:

```bash
mkdir -p tests/fixtures/firmware/build
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin \
  -Os -Wall -Wextra -Werror -nostdlib -Wl,--gc-sections \
  -T tests/fixtures/firmware/linker.ld \
  tests/fixtures/firmware/simulated_flight.c \
  -o tests/fixtures/firmware/build/simulated_flight.elf
```

Validate the configuration, then run:

```bash
reflight config validate \
  --board example-configs/simulated-flight-board.yaml \
  --scenario example-configs/raw-flight-scenario.yaml

reflight run \
  --firmware tests/fixtures/firmware/build/simulated_flight.elf \
  --board example-configs/simulated-flight-board.yaml \
  --scenario example-configs/raw-flight-scenario.yaml \
  --output-dir /tmp/reflight-example \
  --max-instructions 30000
```

The fixture reads simulated sensors over SPI, emits binary telemetry, and raises PB1 when an accelerometer reading reaches 50 raw counts. The run ends at 0.08 simulated seconds and writes:

- `telemetry.bin` — captured USART2 bytes.
- `telemetry.decoded.csv` — decoded sensor readings with simulation timestamps.
- `hardware_events.csv` — GPIO transitions with simulation timestamps.

This demonstrates a synthetic fixture; production flight firmware has not been verified.

## Use your own firmware

Supply your ELF, board YAML, scenario, CSV inputs, and output directory through the CLI:

- [Board YAML](example-configs/simulated-flight-board.yaml) selects the MCU, bus wiring, and captured outputs.
- [Scenario YAML](example-configs/raw-flight-scenario.yaml) maps sensor input keys to CSV columns and sets simulation timing.
- [Packet YAML](example-configs/simulated-flight-packet.yaml) optionally defines how to decode telemetry.

Paths inside YAML are relative to that YAML file. CLI paths are relative to the working directory. Omit `--scenario` for static sensor inputs.

CSV values are linearly interpolated; current sensor models expect raw register values. Runs stop at the first active CSV endpoint or configured duration, subject to the instruction cap. Set `--max-instructions` high enough for your scenario; the default is 1000.

Timing is deterministic, not cycle-accurate. The supported SPI subset is master, 8-bit, mode 0, software chip select, and polling.

See the [reference](docs/reference.md) for configuration rules, sensor channels and rates, timing, packet decoding, and limitations.

## Tests

```bash
python -m pytest -q
```

Integration tests compile fresh ARM firmware. Set `ARM_NONE_EABI_GCC` to override the test compiler path. A legacy capture test skips unless its fixture is built; see [fixture build and test commands](docs/reference.md#static-example-and-tests).
