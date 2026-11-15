# Reflight

Reflight is a software-in-the-loop framework for testing firmware. It runs the actual firmware with Qiling, feeds simulated sensor data, and captures UART telemetry and GPIO events.

Currently verified: STM32F407VG with SPI1 polling, sensor playback, and external SPI-NAND read/program/erase. I2C execution is not supported.

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

The fixture reads simulated sensors over SPI, emits binary telemetry, and raises PB1 when the ICM acceleration reaches 512 counts (4.903325 m/s² at its configured ±32 g range). The run ends at 0.08 simulated seconds and writes:

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

Scenario CSV values are physical SI measurements, linearly interpolated before each sensor model converts them to firmware-visible registers:

| Input keys | Units |
| --- | --- |
| `accel_x`, `accel_y`, `accel_z` | m/s², sensor-frame specific force |
| `gyro_x`, `gyro_y`, `gyro_z` | rad/s, sensor-frame angular velocity |
| `pressure`, `temperature` | Pa absolute, K absolute |
| Configured time column | seconds |

Supply complete axis groups. Reflight adds no gravity and rotates no axes: a stationary accelerometer normally measures about 9.80665 m/s² along an axis; free fall measures near zero. Bias and noise use the same SI units and apply once before conversion. Omitted groups retain their last unadjusted physical input; initial scenario defaults are zero axes and 101325 Pa / 293.15 K. Without a scenario, static register/ADC defaults remain available.

IMU sensitivity follows the firmware-written full-scale registers at the next scheduled sample. Counts round to nearest, ties to even, and saturate to signed 16-bit limits. Barometers reject nonfinite, out-of-range or unrealizable samples with `SensorError`; supported ranges and formulas are in the [reference](docs/reference.md#physical-inputs-and-conversion). `set_raw_sample`, raw `publish_sample`, and `SpiBus.inject_raw` remain separate register/protocol APIs.

**Migration:** the historical `raw-flight.csv` / `raw-flight-scenario.yaml` filenames are retained, but their values are now SI. Convert former count-based CSV values and bias/noise to physical units. MS5611 scenario keys are `pressure` / `temperature`; `d1` / `d2` are accepted only by raw APIs. CSV header names are arbitrary and mapped explicitly; there is no unit guessing or raw-CSV mode.

Runs stop at the first active CSV endpoint or configured duration, subject to the instruction cap. Set `--max-instructions` high enough for your scenario; the default is 1000.

Timing is deterministic, not cycle-accurate. The supported SPI subset is master, 8-bit, mode 0, software chip select, and polling.

See the [reference](docs/reference.md) for configuration rules, sensor channels and rates, timing, packet decoding, and limitations.

## External SPI-NAND

Board model identifiers are `W25N01KV`, `GD5F1GQ5UExxH` (3.3 V ID), and
`GD5F1GQ5RExxH` (1.8 V ID). Flash can share SPI1 with sensors on separate CS
pins; it receives no sensor CSV data or sensor-rate scheduling.

Build and run the synthetic NAND acceptance fixture from the repository root
after setup:

```bash
mkdir -p tests/fixtures/firmware/build
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin \
  -Os -Wall -Wextra -Werror -nostdlib -Wl,--gc-sections \
  -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/spi_nand.c \
  -o tests/fixtures/firmware/build/spi_nand.elf
reflight run --firmware tests/fixtures/firmware/build/spi_nand.elf \
  --board example-configs/stm32f407vg-flash.yaml \
  --output-dir /tmp/reflight-flash --max-instructions 60000
```

Expected USART bytes for the example Winbond board are `EF AE 50` (`50` is
the fixture's `P` success marker), with one PB1 rising event. The fixture
detects either supported GigaDevice variant too; change the board's flash
`model` to select it. The [mixed firmware example](docs/reference.md#mixed-flash-and-sensor-example)
adds changing sensor measurements and simulation timestamps.

For an explicit 128 MiB main-array dump, use
[the export board](example-configs/stm32f407vg-flash-export.yaml) instead.
It adds `outputs.flash: [{device: SPI1.PA7, file: flash.bin}]`. No flash image
is written by default, and spare bytes are excluded. See the
[flash reference](docs/reference.md#external-spi-nand) for commands, unlock
sequences, timing, feature differences, and limitations. These synthetic
fixtures do not establish compatibility with production firmware drivers.

## Tests

```bash
python -m pytest -q
```

Integration tests compile fresh ARM firmware. Set `ARM_NONE_EABI_GCC` to override the test compiler path. A legacy capture test skips unless its fixture is built; see [fixture build and test commands](docs/reference.md#static-example-and-tests).
