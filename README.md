# Reflight

Reflight is a reusable, Linux-first software-in-the-loop framework. It executes compiled Cortex-M firmware with Qiling, supplies simulated sensor registers, and captures firmware UART and GPIO responses. The verified runtime is STM32F407VG with SPI1 polling; I²C wiring can be described but I²C execution is explicitly rejected.

## Setup

Requires Python 3.12+ and `arm-none-eabi-gcc` on `PATH` (or set `ARM_NONE_EABI_GCC` for tests). From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Installation directly from this Git repository is also supported. Qiling 1.4.6's Cortex-M integration requires the pinned Unicorn 2.0.1.post1 API. Setuptools 80.9.0 supplies its legacy `distutils`/`pkg_resources` imports on Python 3.12; the binding emits a deprecation warning.

## Time-driven example

Build the synthetic firmware fixture freshly:

```bash
mkdir -p tests/fixtures/firmware/build
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin -Os -Wall -Wextra -Werror -nostdlib -Wl,--gc-sections -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/simulated_flight.c -o tests/fixtures/firmware/build/simulated_flight.elf
reflight config validate --board example-configs/simulated-flight-board.yaml --scenario example-configs/raw-flight-scenario.yaml
reflight run --firmware tests/fixtures/firmware/build/simulated_flight.elf --board example-configs/simulated-flight-board.yaml --scenario example-configs/raw-flight-scenario.yaml --output-dir /tmp/reflight-example --max-instructions 30000
```

The fixture reads ICM45686 and BMI088Accel measurements from the same CSV at different model rates, and reads a default-fed MS5611. Its ARM instructions perform SPI1 MMIO transactions, emit binary telemetry containing those readings, and raise PB1 when ICM accel X reaches 50 raw counts. This verifies a synthetic fixture, not production flight firmware.

The example ends at 0.08 seconds with stop reason `duration` after 16000 instructions. It writes:

- `telemetry.bin`: every captured USART2 byte, including an incomplete final frame if execution stops mid-packet.
- `telemetry.decoded.csv`: `time_s,icm_accel_x,bmi_accel_x,ms_d1`; time is the final byte's arrival time. Incomplete frames produce no row.
- `hardware_events.csv`: `sequence,instruction_count,pc,pin,level,time_s`; PB1 transitions reflect the firmware's actual sensor-based decision.

Paths to SVD, CSV and packet-definition files are relative to their containing YAML. CLI paths are relative to the working directory. To run this same example from an unrelated directory, after building from the repository root:

```bash
reflight_repo="$PWD"
cd /tmp
reflight run --firmware "$reflight_repo/tests/fixtures/firmware/build/simulated_flight.elf" --board "$reflight_repo/example-configs/simulated-flight-board.yaml" --scenario "$reflight_repo/example-configs/raw-flight-scenario.yaml" --output-dir /tmp/reflight-external-example --max-instructions 30000
```

Consuming firmware projects supply their own ELF, board YAML, scenario, CSV and output directory; no runtime repository paths are hardcoded.

## Board and scenario configuration

[Board YAML](example-configs/simulated-flight-board.yaml) retains hardware topology, `outputs.telemetry`, and `outputs.capture`. Optional `outputs.telemetry.packet_definition` names an external packet YAML. Playback is configured separately in [scenario YAML](example-configs/raw-flight-scenario.yaml). `--scenario` is optional on both `run` and `config validate`; omitting it retains static Phase 1–3 behavior and legacy output headers.

A scenario requires `simulation.instructions_per_second`, named `sources`, and an explicit `default_source`. `simulation.duration_s` must be finite and positive when supplied, and is required if no active sensor uses CSV. Sources have one of these forms:

```yaml
sources:
  flight:
    kind: openrocket
    csv_path: raw-flight.csv
    time_column: Time (s)
    columns:  # Model input key -> CSV header
      accel_x: Accel X (count)
      accel_y: Accel Y (count)
      accel_z: Accel Z (count)
  built_in: {kind: default}
default_source: built_in
sensors:
  ICM45686:
    source: flight
    bias: {accel_x: 2}
    noise: {accel_x: 0.5}  # Gaussian standard deviation in raw counts
```

`columns` maps sensor input keys to CSV headers. Existing scenarios using the former header-to-key direction must reverse each pair. Multiple input keys may reference the same CSV header.

Scenario sensors are identified by their board model (`ICM45686`, `BMI088Accel`, `MS5611`, etc.). Bus and chip-select wiring stays in the board YAML. Replace former endpoint keys such as `SPI1.PA4` with their model names. Model names must be unique across the board for a scenario; repeated models are rejected as ambiguous. Static runs without a scenario still support repeated models. Omitted sensor entries use `default_source`. Only CSV sources used by attached sensors are loaded and constrain termination; unused CSV sources are not opened.

CSV must be comma-separated with a single header row first, exact configured header names (including spaces/units), and at least one data row. Remove export comment/preamble lines before use. Duplicate headers, malformed rows, missing mapped columns, nonnumeric/nonfinite mapped values, and non-increasing timestamps fail before execution. Unmapped columns are ignored. Timestamps are seconds; each file's first timestamp is normalized to zero. Numeric values interpolate linearly across nonuniform intervals. A single-row file ends at zero. Requests outside the available interval fail; there is no extrapolation.

The generic reader assigns no physical meaning to values. The current models accept these **raw register channels**, not inferred flight state or physical-unit conversions:

| Model | Input keys | Simulation update rate | Raw range |
| --- | --- | --- | --- |
| ICM45686 | `accel_x/y/z`, `gyro_x/y/z` | 100 Hz | signed 16-bit |
| MS5611 | `d1`, `d2` | 50 Hz | unsigned 24-bit |
| BMI088Accel | `accel_x/y/z` | 200 Hz | signed 16-bit |
| BMI088Gyro | `gyro_x/y/z` | 100 Hz | signed 16-bit |
| BMP581 | `temperature`, `pressure` | 25 Hz | unsigned 24-bit |

Supply complete three-axis groups; omitted groups retain built-in inputs. Axis defaults are zero; MS5611 defaults are D1=9085466/D2=8569150, BMP581 temperature/pressure defaults are 0x7f7f7f. These Python-defined simulation rates are independent of firmware ODR register settings. Firmware reads the last published sample between updates.

Bias and Gaussian noise are optional per channel, applied once per scheduled sample before register conversion. Both use the channel's raw units. Noise standard deviations must be finite and nonnegative; biases must be finite. Values round with Python `round` (nearest integer, ties to even) and clamp to the model range. `simulation.random_seed` is an integer, default zero. A scenario-local RNG, sorted channels, and chronological sample processing with bus/endpoint ordering at simultaneous instants make repeated runs deterministic.

## Virtual time and termination

One virtual clock uses the explicitly configured instruction-to-second conversion. Before each firmware instruction, it dispatches all sensor samples crossed since the previous instruction, at their own scheduled timestamps. Updates begin at zero and use exact periods and integer sample indices. UART/GPIO events use this clock. Qiling's normal hardware stepping, SysTick/NVIC progression, and existing sensor readiness ticks remain active.

Execution stops at the earliest active CSV endpoint or duration, includes sensor updates exactly at that endpoint, and starts no instruction at or after it. A final instruction's time interval is clipped to the endpoint when it falls between instruction boundaries. Ties favor `duration`. The instruction cap remains mandatory protection; its CLI default is 1000, so select a sufficient budget for longer scenarios. The CLI prints elapsed simulation seconds and one of `duration`, `csv_end`, `instruction_cap`, or `firmware_exit`. Emulation errors return nonzero and produce no new run outputs.

This is deterministic timing, not cycle-accurate MCU emulation. Qiling controls its hardware/interrupt stepping; MS5611 conversion and BMP581 readiness still use two logical execution ticks. SPI transfers are synchronous. Samples can change during a multi-byte register read crossing an update boundary; no new transaction-wide snapshot behavior is promised. The supported SPI subset is master, 8-bit, mode 0, software CS, polling, without DMA, interrupts, hardware NSS or CRC.

## Packet definitions

The [example packet definition](example-configs/simulated-flight-packet.yaml) describes only the synthetic fixture:

```yaml
sync: [0xa5, 0x5a]
byte_order: little
fields:
  - {name: icm_accel_x, type: int16}
  - {name: bmi_accel_x, type: int16}
  - {name: ms_d1, type: uint32}
```

Required keys are `sync` (nonempty byte list), `byte_order` (`little` or `big`), and an ordered nonempty `fields` list. Field names must be unique identifiers and cannot be `time_s`. Types are `int8`, `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, with no padding. Definitions and filename collisions are validated before firmware execution.

The decoder incrementally consumes captured UART byte arrivals while writing outputs. It discards bytes before sync, consumes a complete fixed-size payload after sync, and preserves partial sync/frame bytes until more arrive. Sync inside a payload is data. There are no checksums or corruption detection: a corrupt payload with valid sync can decode as a frame. The decoded name is `<raw-file-stem>.decoded.csv`. Without a definition, capture is raw-only. Legacy runs with a definition decode with blank time cells; their GPIO headers remain unchanged.

## Static example and tests

The prior five-endpoint fixture remains usable:

```bash
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin -Os -Wall -Wextra -Werror -nostdlib -Wl,--gc-sections -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/four_sensors.c -o tests/fixtures/firmware/build/four_sensors.elf
reflight run --firmware tests/fixtures/firmware/build/four_sensors.elf --board example-configs/stm32f407vg-four-sensors.yaml --output-dir /tmp/reflight-static --max-instructions 1500
```

Build the optional legacy capture-test fixture to run every test without skips:

```bash
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin -Os -Wall -Wextra -Werror -nostdlib -Wl,--gc-sections -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/fixture.c -o tests/fixtures/firmware/build/fixture.elf
python -m pytest -q tests/unit
python -m pytest -q tests/integration
python -m pytest -q
```

Integration tests compile fresh ARM firmware and verify SPI reads, independent sample rates, mixed sources, seeded output repeatability, firmware GPIO decisions, decoder timestamps, SysTick interrupts, duration-first/CSV-first/tied/default-only stops, and the instruction cap. Broader MCU/I²C runtime support, physical flight modeling, external flash, Phase 5 assertions/reports, and Phase 6 CI/release work remain outside Phase 4.
