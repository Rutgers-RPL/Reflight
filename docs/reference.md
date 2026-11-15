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

## Sparse NAND storage

The storage layer is available through `reflight.flash.NandStorage`.
It has 1024 blocks of 64 pages, each with 2048 main bytes and a required
`spare_bytes=64` (GigaDevice geometry) or `spare_bytes=96` (Winbond geometry).
Untouched pages are implicit FF bytes; only non-erased pages occupy storage.

```python
from reflight.flash import NandStorage

array = NandStorage(spare_bytes=96)
array.program_page(0, 2047, b"\x12\x34")  # Last main byte, first spare byte.
assert array.read_page(0, 2047, 2) == b"\x12\x34"
array.program_page(0, 2047, b"\xff")
assert array.read_page(0, 2047, 1) == b"\x12"  # Programming only clears bits.
first_main_page = next(array.iter_main_pages())
assert len(first_main_page) == 2048  # Spare bytes are excluded.
array.erase_block(0)  # Block index, not page row; restores main and spare to FF.
```

`read_page(row, column=0, length=None)` defaults to the rest of the page,
including spare bytes. All accesses reject invalid indices or regions extending
past the page with `NandStorageError`; indices never wrap or truncate. A column
must name an existing byte even for an empty read or write.
`iter_main_pages()` streams all 65,536 main pages in physical order, including
FF gaps, totaling 134,217,728 bytes without allocating a full image. Consume
the iterator without retaining every page, and do not mutate the array during
iteration.

This storage layer has no command parsing, ECC, protection, BUSY timing,
persistence, or program-order/partial-program-count policy. The flash models
below own protocol behavior, retaining the existing `select()`,
`exchange_byte()`, `deselect()`, optional `tick(count)` boundary.

## External SPI-NAND

The external-device factory `reflight.devices.make_device()` and
`build_spi_bus()` build sensors and flash on the same generic bus. Existing
`make_sensor()` and sensor-package `build_spi_bus()` callers remain supported.
Flash has no `SimulationInputs` API, CSV source assignment, or sensor schedule.
Repeated flash models are allowed in scenarios; sensor model names still must
be unique. Unknown model names fail explicitly.

| Board identifier | Read-ID bytes after one dummy byte | Main + spare bytes/page | Power-on A0 / B0 |
| --- | --- | --- | --- |
| `W25N01KV` | `EF AE 21` | 2048 + 96 | `7C` / `19` |
| `GD5F1GQ5UExxH` | `C8 31` | 2048 + 64 | `38` / `10` |
| `GD5F1GQ5RExxH` | `C8 21` | 2048 + 64 | `38` / `10` |

There is no ambiguous `GD5F1GQ5xExxH` model alias. See the
[board example](../example-configs/stm32f407vg-flash.yaml): flash is SPI1/PA7,
ICM45686 is SPI1/PA4, telemetry is USART2, and capture observes PB1.
Unconnected WP/HOLD inputs are assumed inactive.

Command frames below list MOSI bytes after CS goes low. Data returned during
headers/dummy clocks is FF. Every CS-low interval starts a fresh command;
state changes commit when CS rises. Incomplete fixed frames are ignored.

| Operation | Standard SPI frame | Behavior |
| --- | --- | --- |
| ID | `9F dummy`, then response clocks | Three Winbond or two GigaDevice ID bytes, then FF |
| Get feature | `0F address`, then response clocks | Repeats current feature value; Winbond also accepts `05` |
| Set feature | `1F address value` | Volatile writable bits only; Winbond also accepts `01` |
| Write enable/disable | `06` / `04` | Sets/clears WEL |
| Page to cache | `13 row[23:16] row[15:8] row[7:0]` | Pending array read; cache updates at completion |
| Read cache | `03` or `0B`, two column bytes, one dummy byte, response clocks | Sequential cache bytes; FF after page end |
| Load cache | `02`, two column bytes, one or more data bytes | Clears unfilled cache bytes to FF |
| Random load | `84`, two column bytes, one or more data bytes | Preserves other cache bytes |
| Program execute | `10`, three row bytes | ANDs cache into physical page after completion |
| Block erase | `D8`, three row bytes | Erases the 64-page block containing that row |
| Soft reset | `FF` | Cancels pending operation; clears WEL/failures; retains cache/features |
| Power-on reset | Separate `66`, then `99` transactions | Restores feature defaults; NAND contents survive |

Both chips use only the low 12 column bits: high four bits are documented
dummy bits. Effective columns past 2143/2111 raise `FlashError`, never wrap.
Loads starting in bounds ignore excess tail data beyond the cache, as
specified by both datasheets. Winbond explicitly ignores the high row byte;
GigaDevice's 24-bit row frame rejects values above 65535. This deliberately
distinguishes explicit dummy bits from invalid effective addresses.

Both power up fully protected. Firmware unlocks with `1F A0 00` (no WREN
required for feature writes). For Winbond, send `06` **before** `02`/`84`,
then `10 row`; for GigaDevice, `02`/`84` may precede `06`, which is required
before `10`. Both require `06` before `D8`. Missing WREN ignores the operation.
The fixture detects the chip and exercises its respective ordering.

Read C0 with `0F C0`: bit 0 is BUSY/OIP, bit 1 WEL, bit 2 E_FAIL, bit 3
P_FAIL; ECC error bits remain zero for these uncorrupted models. Accepted
program/erase completion clears WEL. Winbond also clears WEL on page reads;
GigaDevice retains it. Protected attempts set the corresponding failure bit.
GigaDevice leaves OIP clear on protected attempts (its §12.5); Winbond uses a
finite pending failure. A subsequent unprotected corresponding operation
clears the failure bit; soft reset clears both.

Winbond supports all BP3:0/TB protection ranges (including power lock through
SRP1/SRP0), ECC-E, ODS/H-DIS, fixed BUF=1, and extended ECC feature reads
10/20/30/40/50. BFD in 10 is writable with thresholds 1–3; error reports are
zero. A/B/C feature addresses accept the documented low-nibble don't-care
bits. GigaDevice uses its separate BP2:0/INV/CMP table, including the table's
CMP=1/BP=110 block-0 case. Its A0 mask is BE, B0 supports ECC_EN/BPL/QE,
D0 supports driver strength bits 6:5, and F0 reports the selected row's block
protection status in bit 3. BPL is modeled as the datasheet's special-order
power lock feature; it stays set until power-on reset. Read-only status bits
ignore feature writes; reserved GigaDevice bits read zero. OTP modes and
Winbond SR1-L OTP locking raise explicit unsupported errors.

`FF` keeps volatile feature settings and cache contents, with no page-0 reload.
`66` followed immediately by `99` restores power-on defaults; GigaDevice also
reloads page 0 into cache, while Winbond uses the default no-reload option.
Reset atomically cancels pending writes/erases without introducing simulated
power-cut corruption or deleting committed NAND bytes.

BUSY advances through the existing `SpiBus.tick(count)` hook: read 512 ticks,
program 1024, erase 2048, reset 128. Runtime ticks once before each executed
instruction, with no new simulation clock. These are deterministic logical
delays, **not cycle-accurate datasheet timings**. Polling GET FEATURES is
available throughout pending operations, including reset. Winbond also permits
ID while busy; GigaDevice ignores ID. Other busy commands are ignored except
reset commands. A command ignored at its opcode stays ignored until CS rises,
even if BUSY clears mid-frame.

With ECC enabled, Winbond ignores user programming of IC-managed columns
840–85F, leaving erased parity bytes FF; it does not generate parity. Data
previously programmed there with ECC disabled stays until erase. With ECC
disabled all 96 spare bytes are available; all 64 GigaDevice spare bytes are
available in either setting. No parity generation or bit correction is claimed.
Both models have no stochastic errors, wear, bad-block generation, program
ordering/partial-program-count enforcement, OTP/UID/parameter pages, image
import, or cross-run persistence. Multi-lane/DTR opcodes 3B/6B/BB/EB/EE and
32/34/C4 fail explicitly; other unimplemented opcodes fail too. Hardware reset
signaling, WP/HOLD pin changes, and production-driver compatibility are
unverified. Only standard single-lane SPI1 polling is supported.

Protocol references are local Markdown datasheets: Winbond W25N01KV Rev H
§§7–8.2 (Figure 11 reset defaults, §7.5 protection, §7.2.4 parity area), and
GigaDevice GD5F1GQ5xExxH Rev 1.4 §§3–4, 6–12 (Tables 12-1/2/7).

### Optional flash image

Add this optional entry under board `outputs`, alongside telemetry/capture:

```yaml
flash:
  - {device: SPI1.PA7, file: flash.bin}
```

Each reference must name a configured flash device. Filenames must be single
safe names under `--output-dir` and must differ from raw/decoded telemetry,
capture, and all other flash output names. Validation happens before firmware
execution. Library callers pass the live bus as
`write_configured_outputs(trace, board, output_dir, spi_bus=bus)`; the optional
keyword preserves prior callers when no flash export is configured.

After successful execution, export streams exactly **134,217,728 bytes** per
selected device, main data only, ascending physical page order, with FF gaps.
It includes completed program/erase results, excludes spare bytes, and does
not finish a pending flash operation after firmware execution stops. Images
are written to temporary files in the destination directory and renamed only
after completion; errors fail the run and remove incomplete flash artifacts.
An older image is preserved on write failure. Earlier completed legacy outputs
or another completed image may remain if a later export fails. No whole-image
allocation or automatic dump occurs. The
[export example board](../example-configs/stm32f407vg-flash-export.yaml) enables
one image explicitly.

### Mixed flash and sensor example

From the repository root after setup:

```bash
mkdir -p tests/fixtures/firmware/build
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin \
  -DMIXED_SENSOR -Os -Wall -Wextra -Werror -nostdlib -Wl,--gc-sections \
  -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/spi_nand.c \
  -o tests/fixtures/firmware/build/spi_nand_mixed.elf
reflight config validate --board example-configs/stm32f407vg-flash.yaml \
  --scenario example-configs/flash-sensor-scenario.yaml
reflight run --firmware tests/fixtures/firmware/build/spi_nand_mixed.elf \
  --board example-configs/stm32f407vg-flash.yaml \
  --scenario example-configs/flash-sensor-scenario.yaml \
  --output-dir /tmp/reflight-flash-mixed --max-instructions 60000
```

UART contains the two ID bytes and `P`, followed by four `S` + big-endian
signed 16-bit ICM accel-X readings. The CSV ramps from zero to 1 g over 0.5 s;
the 100 Hz sensor publishes changing counts independently of flash operations.
PB1 rises after the NAND checks pass. UART/GPIO use simulation timestamps;
the run ends at 0.25 s. No flash sensor entry or input keys exist.

To run outside the repository, after building:

```bash
reflight_repo="$PWD"
cd /tmp
"$reflight_repo/.venv/bin/reflight" run \
  --firmware "$reflight_repo/tests/fixtures/firmware/build/spi_nand_mixed.elf" \
  --board "$reflight_repo/example-configs/stm32f407vg-flash.yaml" \
  --scenario "$reflight_repo/example-configs/flash-sensor-scenario.yaml" \
  --output-dir /tmp/reflight-flash-external --max-instructions 60000
```

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

Scenario sensors are identified by their board model (`ICM45686`, `BMI088Accel`, `MS5611`, etc.). Bus and chip-select wiring stays in the board YAML. Replace former endpoint keys such as `SPI1.PA4` with their model names. Sensor model names must be unique across the board for a scenario; repeated sensor models are rejected as ambiguous. Flash models are excluded from this requirement and from source assignment. Static runs without a scenario still support repeated models. Omitted sensor entries use `default_source`. Only CSV sources used by attached sensors are loaded and constrain termination; unused CSV sources are not opened.

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

Integration tests compile fresh ARM firmware and verify SPI reads, independent sample rates, mixed sources, seeded output repeatability, firmware GPIO decisions, decoder timestamps, SysTick interrupts, duration-first/CSV-first/tied/default-only stops, and the instruction cap. All five sensor endpoints have datasheet-derived golden-value conversion tests, and compiled firmware reads SI-driven samples from every endpoint. NAND fixtures verify all three part identifiers, erase/program/read/status, 0→1 prevention, reset preservation, mixed sensor playback, UART/GPIO, and optional main-array export. Broader MCU/I²C runtime support, physical flight modeling, Phase 7 assertions/reports, and Phase 8 packaging/CI remain outside this implementation.
