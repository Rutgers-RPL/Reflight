# Reflight

Reflight is a Python-based firmware simulator for testing embedded software without physical hardware. It uses Qiling to execute microcontroller firmware, simulate supported sensors and peripherals, and capture UART output and GPIO activity. The current example targets STM32F407VG, with support for additional microcontrollers planned.

## Setup

Requires Python 3.12 and the Arm GNU Toolchain (`arm-none-eabi-gcc`). Make sure both are installed and the compiler is on your `PATH`.

From the repository root (Linux):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Run

Build the included four-sensor example:

```bash
mkdir -p tests/fixtures/firmware/build
arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -ffreestanding -fno-builtin -Os -Wall -Wextra -Werror -nostdlib -Wl,--gc-sections -T tests/fixtures/firmware/linker.ld tests/fixtures/firmware/four_sensors.c -o tests/fixtures/firmware/build/four_sensors.elf
```

Run the simulation:

```bash
reflight run --firmware tests/fixtures/firmware/build/four_sensors.elf --board example-configs/stm32f407vg-four-sensors.yaml --output-dir results/phase3 --max-instructions 1500
```

Reflight writes captured UART data (`telemetry.bin`) and GPIO transitions (`hardware_events.csv`) to the output directory.

To run your own compatible firmware, replace `tests/fixtures/firmware/build/four_sensors.elf` with the path to your compiled firmware ELF and `example-configs/stm32f407vg-four-sensors.yaml` with your board configuration YAML.
