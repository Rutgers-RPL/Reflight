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
