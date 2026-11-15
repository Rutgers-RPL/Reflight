"""Fresh ARM compilation for time-driven firmware acceptance tests."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
FIRMWARE = ROOT / 'tests/fixtures/firmware'


@pytest.fixture
def compile_arm(tmp_path):
    def compile_source(name, source=None):
        compiler = os.environ.get('ARM_NONE_EABI_GCC') or shutil.which('arm-none-eabi-gcc')
        assert compiler and Path(compiler).is_file(), 'arm-none-eabi-gcc is required'
        path = FIRMWARE / name
        if source is not None:
            path = tmp_path / name
            path.write_text(source)
        elf = tmp_path / (path.stem + '.elf')
        result = subprocess.run([compiler, '-mcpu=cortex-m4', '-mthumb', '-ffreestanding',
                                 '-fno-builtin', '-Os', '-Wall', '-Wextra', '-Werror',
                                 '-nostdlib', '-Wl,--gc-sections', '-T', str(FIRMWARE / 'linker.ld'),
                                 str(path), '-o', str(elf)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return elf
    return compile_source
