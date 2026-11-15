# Reflight firmware execution and sensor proof

Reflight executes a compiled STM32F407 Cortex-M firmware ELF with Qiling and
captures firmware-driven USART2 bytes and PA5 output transitions. The current
proof uses the small fixture in `tests/fixtures/firmware/`; another compatible
Flash-linked ELF can be supplied by path. Outputs are virtual only.

## Set up and run (PowerShell)

Install Python 3.12 and an ARM GNU Toolchain with `arm-none-eabi-gcc`. From the
repository root, create an environment and install the local package and tests:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e '.[test]'
```

Set the compiler path. If it is already on `PATH`, this command works:

```powershell
$env:ARM_NONE_EABI_GCC = (Get-Command arm-none-eabi-gcc).Source
```

For the tested GNU Tools for STM32 installation, use:

```powershell
$env:ARM_NONE_EABI_GCC = 'C:\ST\STM32CubeIDE_1.19.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.13.3.rel1.win32_1.0.0.202411081344\tools\bin\arm-none-eabi-gcc.exe'
```

Build the fixture and run the installed command:

```powershell
New-Item -ItemType Directory -Force tests/fixtures/firmware/build | Out-Null
& $env:ARM_NONE_EABI_GCC -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin -Os -Wall -Wextra -Werror -nostdlib '-Wl,--gc-sections' -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/fixture.c -o tests/fixtures/firmware/build/fixture.elf
.venv/Scripts/reflight.exe poc --firmware tests/fixtures/firmware/build/fixture.elf --output-dir results/poc --max-instructions 400
.venv/Scripts/python.exe -m pytest -q
```

The compiler must be available through `ARM_NONE_EABI_GCC` or `PATH` for the
integration test. That test builds its own ELF in a temporary directory and
fails clearly if the compiler or installed command is unavailable. The
fixture's intentional final loop is stopped at the instruction budget.

`uart.bin` contains raw USART2 transmit bytes (`REFLIGHT A\n` for this fixture).
`gpio_events.csv` contains `instruction_count,pc,pin,level` with PA5 transition
levels `1` (HIGH) then `0` (LOW). The initial LOW baseline is not a transition
row. Instruction counts and PCs are ordering evidence, not physical time.

The proof does not establish full STM32 clock or pin-mux fidelity, baud timing,
interrupts, DMA, HAL startup, or compatibility with arbitrary firmware images.
Qiling is the only execution backend.

## SVD metadata inspection

Phase 2 can inspect an explicitly supplied local CMSIS-SVD file without
starting Qiling. The small file in `tests/fixtures/svd/` is self-authored test
metadata, not a full vendor SVD:

```powershell
.venv/Scripts/reflight.exe svd peripherals --file tests/fixtures/svd/stm32f407-minimal.svd
.venv/Scripts/reflight.exe svd registers --file tests/fixtures/svd/stm32f407-minimal.svd --peripheral SPI1
.venv/Scripts/reflight.exe svd lookup --file tests/fixtures/svd/stm32f407-minimal.svd --address 0x4001300c
```

These commands report register metadata and addresses; they do not emulate
peripheral behavior.

## Board configuration validation

The version-1 example describes an STM32F407VG board, a local SVD path, and
UART/GPIO outputs. Its `buses: {}` setting keeps the Phase 2 run usable.

```powershell
.venv/Scripts/reflight.exe config validate --board example-configs/stm32f407vg.yaml
```

SVD and optional packet-definition paths resolve relative to the board YAML.
Telemetry format is `raw` in Phase 2; binary packet decoding is not available.

## Run with a board configuration

The Phase 2 command selects the USART and GPIO pins from the validated board
YAML. This example uses the compiled fixture from the setup section:

```powershell
.venv/Scripts/reflight.exe run --firmware tests/fixtures/firmware/build/fixture.elf --board example-configs/stm32f407vg.yaml --output-dir results/board --max-instructions 400
```

The example writes `results/board/telemetry.bin` with the firmware's raw
`REFLIGHT A\n` bytes and `results/board/hardware_events.csv` with PA5 HIGH and
LOW transitions. The CSV columns are
`sequence,instruction_count,pc,pin,level`; levels are numeric `1` and `0`.
The output filenames come from the board YAML and must stay inside the chosen
output directory. The Phase 1 `reflight poc` command and its file format remain
available.

Only the STM32F407VG Qiling target is runnable. The explicit SVD file supplies
register metadata and must agree with Qiling's selected USART, GPIO, RCC, and
SPI1 addresses. `packet_definition` is retained but not decoded.

## Phase 3: five SPI endpoints, four sensors

Build the direct-register test driver and run its board configuration:

```powershell
& $env:ARM_NONE_EABI_GCC -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin -Os -Wall -Wextra -Werror -nostdlib '-Wl,--gc-sections' -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/four_sensors.c -o tests/fixtures/firmware/build/four_sensors.elf
.venv/Scripts/reflight.exe config validate --board example-configs/stm32f407vg-four-sensors.yaml
.venv/Scripts/reflight.exe run --firmware tests/fixtures/firmware/build/four_sensors.elf --board example-configs/stm32f407vg-four-sensors.yaml --output-dir results/phase3 --max-instructions 1500
```

The fixture reads ICM-45686, MS5611, BMI088 accelerometer and gyro, and
BMP581 through **firmware SPI1 DR accesses**. It sends the bytes it read via
USART2. The example writes raw `telemetry.bin` and the configured
`hardware_events.csv`, including effective PA4–PA7/PB0 CS transitions.
`tests/integration/test_four_sensors.py` repeats the run with different raw
samples injected by bus and CS; its exact UART assertion proves the bytes
change. There is no physical sample clock or telemetry decoder.

To use Reflight from another project, install this checkout with
`python -m pip install -e C:\RRPL\reflight` (or install a Git tag when one is
published). Pass absolute paths for your own Flash-linked STM32F407VG ELF,
board YAML, and output directory to `reflight run`. Board-relative SVD and
packet-definition paths resolve from the YAML location, independent of the
shell's working directory. The minimal checked-in SVD serves the tests and
example; use your own accurate local SVD for another board.

| Component | Tested Phase 3 behavior | Unsupported or unverified |
| --- | --- | --- |
| STM32F407VG SPI1 | 8-bit, mode 0, four-wire master; software GPIO CS; polling TXE/RXNE/BSY | SPI2/3, 16-bit, DMA, interrupts, hardware NSS, I2S, full HAL timing |
| ICM-45686 | WHO_AM_I, power/ODR/full-scale config, raw accel and gyro | FIFO, indirect banks, interrupts |
| MS5611 | Reset, PROM/CRC, D1/D2 conversion, 24-bit ADC; logical readiness ticks | Physical conversion timing, compensation in Python |
| BMI088 | Separate accel/gyro CS, startup and read dummy for accel, identity/config/raw axes | FIFO, self-test, interrupts |
| BMP581 | Startup SPI dummy, identity/config/status, raw temperature and pressure | FIFO, NVM, I3C, physical timing |
| I²C | Typed wiring contract and explicit runtime diagnostic | Controller transactions: Qiling 1.4.6 STM32F407 profile cannot instantiate its named I²C model |

The firmware fixture contains a small direct-register driver subset because
no application sensor drivers are in this repository. Passing these tests
does not establish compatibility with a full flight firmware or sensor driver.
