# Reflight reference

See the [README](../README.md) for setup and a runnable example. Run the commands below from the repository root unless stated otherwise.

## Dependencies

Installation directly from this Git repository is also supported. Qiling 1.4.6's Cortex-M integration requires the pinned Unicorn 2.0.1.post1 API. Setuptools 80.9.0 supplies its legacy `distutils`/`pkg_resources` imports on Python 3.12; the binding emits a deprecation warning.

## Paths and external projects

Paths to SVD, CSV and packet-definition files are relative to their containing YAML. CLI paths are relative to the working directory. To run this same example from an unrelated directory, after building from the repository root:

```bash
reflight_repo="$PWD"
cd /tmp
reflight run --firmware "$reflight_repo/tests/fixtures/firmware/build/simulated_flight.elf" --board "$reflight_repo/example-configs/simulated-flight-board.yaml" --scenario "$reflight_repo/example-configs/raw-flight-scenario.yaml" --output-dir /tmp/reflight-external-example --max-instructions 30000
```

Consuming firmware projects supply their own ELF, board YAML, scenario, CSV and output directory; no runtime repository paths are hardcoded.

## Example outputs

The README example reads ICM45686 and BMI088Accel measurements from one CSV at different model rates, plus an MS5611 using a separate pressure/temperature mapping of that CSV. It ends at 0.08 seconds with stop reason `duration` after 16000 instructions.

- `telemetry.bin`: every captured USART2 byte, including an incomplete final frame if execution stops mid-packet.
- `telemetry.decoded.csv`: `time_s,icm_accel_x,bmi_accel_x,ms_d1`; time is the final byte's arrival time. Incomplete frames produce no row.
- `hardware_events.csv`: `sequence,instruction_count,pc,pin,level,time_s`; PB1 transitions reflect the firmware's sensor-based decision.

## Board and scenario configuration

[Board YAML](../example-configs/simulated-flight-board.yaml) retains hardware topology, `outputs.telemetry`, and `outputs.capture`. Optional `outputs.telemetry.packet_definition` names an external packet YAML. Playback is configured separately in [scenario YAML](../example-configs/raw-flight-scenario.yaml). `--scenario` is optional on both `run` and `config validate`; omitting it retains static Phase 1–3 behavior and legacy output headers.

A scenario requires `simulation.instructions_per_second`, named `sources`, and an explicit `default_source`. `simulation.duration_s` must be finite and positive when supplied, and is required if no active sensor uses CSV. Sources have one of these forms:

```yaml
sources:
  flight:
    kind: openrocket
    csv_path: raw-flight.csv
    time_column: Time (s)
    columns:  # Model input key -> CSV header
      accel_x: Acceleration X (m/s^2)
      accel_y: Acceleration Y (m/s^2)
      accel_z: Acceleration Z (m/s^2)
  built_in: {kind: default}
default_source: built_in
sensors:
  ICM45686:
    source: flight
    bias: {accel_x: 2}
    noise: {accel_x: 0.5}  # Gaussian standard deviation in m/s²
```

`columns` maps sensor input keys to CSV headers. Existing scenarios using the former header-to-key direction must reverse each pair. Multiple input keys may reference the same CSV header.

Scenario sensors are identified by their board model (`ICM45686`, `BMI088Accel`, `MS5611`, etc.). Bus and chip-select wiring stays in the board YAML. Replace former endpoint keys such as `SPI1.PA4` with their model names. Model names must be unique across the board for a scenario; repeated models are rejected as ambiguous. Static runs without a scenario still support repeated models. Omitted sensor entries use `default_source`. Only CSV sources used by attached sensors are loaded and constrain termination; unused CSV sources are not opened.

CSV must be comma-separated with a single header row first, exact configured header names (including spaces/units), and at least one data row. Remove export comment/preamble lines before use. Duplicate headers, malformed rows, missing mapped columns, nonnumeric/nonfinite mapped values, and non-increasing timestamps fail before execution. Unmapped columns are ignored. Timestamps are seconds; each file's first timestamp is normalized to zero. Numeric values interpolate linearly across nonuniform intervals. A single-row file ends at zero. Requests outside the available interval fail; there is no extrapolation.

## Physical inputs and conversion

The generic CSV reader interpolates mapped numbers without sensor-specific knowledge. Scenario **values** and channel bias/noise use these mandatory SI units; header names can be anything:

| Model | Input keys | SI units | Simulation update rate |
| --- | --- | --- | --- |
| ICM45686 | `accel_x`, `accel_y`, `accel_z`; `gyro_x`, `gyro_y`, `gyro_z` | m/s²; rad/s | 100 Hz |
| BMI088Accel | `accel_x`, `accel_y`, `accel_z` | m/s² | 200 Hz |
| BMI088Gyro | `gyro_x`, `gyro_y`, `gyro_z` | rad/s | 100 Hz |
| BMP581 | `pressure`, `temperature` | Pa absolute; K absolute | 25 Hz |
| MS5611 | `pressure`, `temperature` | Pa absolute; K absolute | 50 Hz |

Acceleration means **specific force in sensor axes**, rather than trajectory/world-frame acceleration. A stationary sensor normally measures about 1 g along some axis; a free-falling sensor measures near zero. Reflight performs no coordinate transforms, gravity addition, or implicit OpenRocket flight-state derivation. The scenario author supplies suitable sensor-axis signals. Standard gravity (9.80665 m/s²) is used only as a conversion constant. Angular velocity is also in sensor axes; CSV timestamps are seconds.

Supply complete three-axis groups. Omitted groups retain their last unadjusted physical inputs. Initial scenario defaults are zero acceleration/angular velocity, 101325 Pa and 293.15 K. Raw injection never changes this physical baseline. Without a scenario, static defaults remain unchanged: zero IMU registers, MS5611 D1=9085466/D2=8569150, and BMP581 temperature/pressure bit patterns 0x7f7f7f. Model update rates above are Python-defined and independent of firmware ODR settings; reads return the last encoded sample between updates.

Bias and Gaussian noise apply once per scheduled sample in SI units, including retained/default channels, before model-owned conversion. Neither accumulates into the physical baseline. Noise standard deviations must be finite and nonnegative; biases, inputs and adjusted values must be finite. `simulation.random_seed` is an integer, default zero. A scenario-local RNG, sorted channels, and chronological sample processing with bus/endpoint ordering at simultaneous instants make repeated runs deterministic.

Each model implements `publish_physical_sample(values)` for the complete, validated, adjusted SI sample prepared by `update_measurements(values, *, bias, noise, rng)`. Encoding happens only at publication: a firmware full-scale write changes the next scheduled sample, leaving already encoded bytes intact. Raw `publish_sample`, `set_raw_sample`, and `SpiBus.inject_raw` continue to accept counts/bit patterns for protocol tests. In particular, BMP581 raw arguments retain their unsigned 24-bit convention even for negative Celsius bit patterns, and MS5611 raw arguments remain `d1`/`d2`.

| Model | Conversion and configuration |
| --- | --- |
| ICM45686 accel | `ACCEL_CONFIG0` (0x1b) FS selections 0–4: ±32/16/8/4/2 g, respectively 1024/2048/4096/8192/16384 LSB/g. Signed 16-bit big endian. |
| ICM45686 gyro | `GYRO_CONFIG0` (0x1c) FS selections 0–8: ±4000/2000/1000/500/250/125/62.5/31.25/15.625 degree/s. Published sensitivities: 8.2/16.4/32.8/65.5/131/262/524.3/1048.6/2097.2 LSB/(degree/s). Convert rad/s inside the model; signed 16-bit big endian. |
| BMI088Accel | `ACC_RANGE` (0x41), settings 0–3: ±3/6/12/24 g. Exact equation: counts = (m/s² ÷ 9.80665) × 32768 ÷ (3 × 2**setting). Signed 16-bit little endian. |
| BMI088Gyro | `GYRO_RANGE` (0x0f), settings 0–4: ±2000/1000/500/250/125 degree/s, with 16.384/32.768/65.536/131.072/262.144 LSB/(degree/s). Signed 16-bit little endian. |
| BMP581 | Pressure = round(Pa × 64); temperature = round((K − 273.15) × 65536), signed 24-bit little-endian fields. Supported OSR/ODR writes do not alter these units. Operating limits: 30000–125000 Pa, 233.15–358.15 K. |
| MS5611 | Invert integer PROM compensation using the instance's actual C1–C6: first solve D2 for temperature, then D1 for pressure with compensated offset/sensitivity. Includes below-20°C and below−15°C correction branches. Limits: 1000–120000 Pa, 233.15–358.15 K; reconstruction error ≤1 Pa / ≤0.01 K. |

Encoding uses nearest-integer, ties-to-even quantization of scaled floating point values (ordinary binary representation error can affect values very near a half-count). IMUs saturate at −32768/32767, never wrap. Unsupported range writes raise `SensorError`. Barometers reject out-of-range or unrealizable samples rather than clamping. MS5611 first quantizes requested temperature to centi-Celsius and pressure to Pa, uses integer divisions truncating toward zero, and deterministically searches the bounded unsigned 24-bit ADC domain. Equal reconstruction errors choose the lower ADC code; exact output plateaus select their first code. Invalid PROM coefficients, nonpositive compensated sensitivity, or targets outside the instance's representable domain fail explicitly.

**SI migration:** existing count-based CSVs and bias/noise must be converted to SI; there is no unit selector or raw-CSV fallback. MS5611 `d1`/`d2` scenario mappings are rejected: use `pressure`/`temperature`. Example filenames retain `raw-flight` for path compatibility; their contents now carry SI values. The example uses one acceleration mapping shared by two models and a separate pressure/temperature mapping to the same CSV for MS5611.

Conversion constants are traceable to the supplied Markdown datasheets under `.agents/datasheets/` (original datasheet page numbers):

| Model | Local reference |
| --- | --- |
| ICM45686 | `TDK_DS_000577_ICM_45686.md` §§3.1–3.2, pp18–19; §§17.26–17.27, pp79–80 |
| BMI088Accel | `bst-bmi088-ds001.md` §5.3.4 p27 (exact equation), §5.3.11 p30 |
| BMI088Gyro | `bst-bmi088-ds001.md` §5.5.5 p39 |
| BMP581 | `bst-bmp581-ds004.md` operating/specification tables pp2,8–10; §4.5 p22; §§7.11–7.16 pp56–57 |
| MS5611 | `ENG_DS_MS5611-01BA03_B3.md` operating limits pp1,8; integer compensation p8; second-order compensation p9 |

## Virtual time and termination

One virtual clock uses the explicitly configured instruction-to-second conversion. Before each firmware instruction, it dispatches all sensor samples crossed since the previous instruction, at their own scheduled timestamps. Updates begin at zero and use exact periods and integer sample indices. UART/GPIO events use this clock. Qiling's normal hardware stepping, SysTick/NVIC progression, and existing sensor readiness ticks remain active.

Execution stops at the earliest active CSV endpoint or duration, includes sensor updates exactly at that endpoint, and starts no instruction at or after it. A final instruction's time interval is clipped to the endpoint when it falls between instruction boundaries. Ties favor `duration`. The instruction cap remains mandatory protection; its CLI default is 1000, so select a sufficient budget for longer scenarios. The CLI prints elapsed simulation seconds and one of `duration`, `csv_end`, `instruction_cap`, or `firmware_exit`. Emulation errors return nonzero and produce no new run outputs.

This is deterministic timing, not cycle-accurate MCU emulation. Qiling controls its hardware/interrupt stepping; MS5611 conversion and BMP581 readiness still use two logical execution ticks. SPI transfers are synchronous. Samples can change during a multi-byte register read crossing an update boundary; no new transaction-wide snapshot behavior is promised. The supported SPI subset is master, 8-bit, mode 0, software CS, polling, without DMA, interrupts, hardware NSS or CRC.

## Packet definitions

The [example packet definition](../example-configs/simulated-flight-packet.yaml) describes only the synthetic fixture:

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

Integration tests compile fresh ARM firmware and verify SPI reads, independent sample rates, mixed sources, seeded output repeatability, firmware GPIO decisions, decoder timestamps, SysTick interrupts, duration-first/CSV-first/tied/default-only stops, and the instruction cap. All five endpoints also have datasheet-derived golden-value conversion tests, and compiled firmware reads SI-driven samples from every endpoint. Broader MCU/I²C runtime support, physical flight modeling, external flash, Phase 6 assertions/reports, and Phase 7 packaging/CI remain outside this implementation.
