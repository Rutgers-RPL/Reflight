# STM32F407 Task A fixture

This is a bare metal Cortex-M4 STM32F407VG fixture, selected because the
production MCU is not yet known. It uses the 1 MiB Flash and first 128 KiB
SRAM regions in Qiling 1.4.6's `stm32f407` MCU profile. Reset starts
at the vector table at `0x08000000`, with the stack top at `0x20020000`.
USART2 (`0x40004400`) transmits on PA2 (AF7); PA5 is an output driven high
and then low through GPIOA BSRR. The fixture assumes the reset HSI clock of
16 MHz, enables the relevant peripheral clocks, and sets USART2 for roughly
115200 baud. The virtual run proves register access, not physical baud timing
or pin multiplexing.

Build with ARM GNU Toolchain (tested with Arm GCC 13.3.1):

```powershell
$toolchain = 'C:\ST\STM32CubeIDE_1.19.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.13.3.rel1.win32_1.0.0.202411081344\tools\bin'
New-Item -ItemType Directory -Force tests/fixtures/firmware/build | Out-Null
& "$toolchain/arm-none-eabi-gcc.exe" -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin -Os -Wall -Wextra -Werror -nostdlib '-Wl,--gc-sections' -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/fixture.c -o tests/fixtures/firmware/build/fixture.elf
& "$toolchain/arm-none-eabi-objdump.exe" -d tests/fixtures/firmware/build/fixture.elf
```

The runner passes this **ELF directly** to Qiling's MCU loader. No raw image
conversion is used. The ELF is linked for Flash at `0x08000000`; its first
two vector words supply initial SP and Thumb reset PC. Build products are
ignored by Git.

After building, run the Task B tests from the repository root:

```powershell
$env:PYTHONPATH = 'src'
.venv/Scripts/python.exe -m pytest -q tests/unit/test_qiling_capture.py
```

The test that runs the compiled fixture skips if `build/fixture.elf` is absent;
build it first to exercise the actual ARM instructions. The other two tests
exercise repeated BSRR/ODR writes against Qiling's STM32F4 GPIO model.

## Scenario fixtures

`simulated_flight.c` polls ICM45686 accel X (PA4), BMI088Accel accel X (PA6),
and MS5611 D1 (PA5) through SPI1. It emits `A5 5A`, then two signed 16-bit
little-endian accel readings and one unsigned 32-bit little-endian ADC reading.
The firmware explicitly programs ±32 g on ICM45686 and ±6 g on BMI088Accel.
PB1 goes high at 512 ICM counts, equivalent to 4.903325 m/s² sensor-axis
specific force. The SI CSV begins at 0.980665 m/s²: ICM reports 102 counts,
BMI reports 546; their independent 100 Hz / 200 Hz updates produce different
latched readings as the acceleration increases. MS5611 maps 101325 Pa /
293.15 K through its PROM compensation inverse to D1=9155145, D2=8566488;
this differs from its unchanged static/raw default. Telemetry fields remain raw
sensor values, while scenario CSV inputs and adjustments are physical SI.
No gravity or coordinate transform is applied. The
matching board, CSV scenario, packet definition, and Linux build/run commands
are linked from the [root README](../../../README.md); timing limits are in the
[reference](../../../docs/reference.md#virtual-time-and-termination). This is a synthetic
acceptance fixture, not production flight firmware.

`timer_progress.c` waits for three actual emulated SysTick interrupts before
emitting `T`. Its integration test verifies that virtual sensor scheduling
preserves Qiling's hardware/NVIC progression. The new integration tests
compile these fixtures freshly rather than depending on build artifacts.

`test_si_all_sensors.py` compiles the unchanged `four_sensors.c` polling fixture
and supplies SI CSV data to all five endpoints through explicit per-model
mappings. It checks UART bytes against independent datasheet values (1 g,
10 degree/s expressed in rad/s, ambient pressure/temperature), while the static
and explicit raw-injection tests retain their original expectations. Unit tests
cover negative Celsius BMP581 encoding, MS5611 cold compensation and changed
PROM coefficients, every IMU range, saturation and invalid inputs.

## External NAND fixture

`spi_nand.c` uses direct STM32F407 SPI1 registers with flash CS on PA7,
USART2 output, and PB1 success indication. It detects Winbond `EF AE 21` or
GigaDevice `C8 31`/`C8 21`, unlocks the chip, erases block 0, programs and
reads nontrivial data across the 2047/2048 main/spare boundary, polls status,
checks default protection failure, missing-WREN erase rejection, attempted
0→1 programming, soft-reset preservation, and a second erase. It asserts
BUSY is observed before successful read/program/erase completion and follows
the detected chip's WREN-before-load versus load-before-WREN order.
It then leaves `3C C3` at row 64, columns 1–2 for export checks. UART emits
the first two ID bytes and `P` on success; `F` indicates a failed check and
`T` a polling timeout. Firmware loops after completion; select the documented
instruction budget so all checks execute.

Build with `-DMIXED_SENSOR` to add ICM45686 on PA4 at ±32 g, followed by
four `S` + signed big-endian 16-bit accel-X records. Its matching scenario
maps only the sensor to changing CSV inputs. See the root
[build/run example](../../../README.md#external-spi-nand) and
[mixed example](../../../docs/reference.md#mixed-flash-and-sensor-example).

Run `.venv/bin/python -m pytest -q tests/integration/test_flash_cli.py` from
the root: tests compile fresh ARM ELFs and invoke the public CLI for all
three part identifiers, static and mixed playback, plus a firmware-driven
128 MiB export. Direct Python storage mutation is used only by unit tests.
These are synthetic fixtures, not executions of a production flash driver.
