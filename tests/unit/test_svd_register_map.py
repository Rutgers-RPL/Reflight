"""CMSIS-SVD metadata tests; no Qiling execution is involved."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from reflight.svd import RegisterMap, SVDLoadError, SVDLookupError


SVD = Path(__file__).parents[1] / "fixtures/svd/stm32f407-minimal.svd"


def test_named_and_reverse_lookups_include_fields() -> None:
    index = RegisterMap.from_file(SVD)
    assert index.get_peripheral("SPI1").base_address == 0x40013000
    data = index.get_register("SPI1", "DR")
    assert data.address == 0x4001300C
    assert [(field.name, field.bit_offset, field.bit_width)
            for field in data.fields] == [("DATA", 0, 16)]
    assert index.lookup_address(0x4001300C) == data


def test_inheritance_and_register_array_are_flattened() -> None:
    index = RegisterMap.from_file(SVD)
    assert index.get_register("USART1", "DR").address == 0x40011004
    assert index.get_register("GPIOB", "BSRR").address == 0x40020418
    assert index.get_register("SPI1", "TEST0").address == 0x40013020
    assert index.get_register("SPI1", "TEST1").address == 0x40013024


def test_missing_malformed_and_unknown_items_fail(tmp_path: Path) -> None:
    with pytest.raises(SVDLoadError, match="cannot read SVD"):
        RegisterMap.from_file(tmp_path / "missing.svd")
    invalid = tmp_path / "invalid.svd"
    invalid.write_text("<not-xml", encoding="utf-8")
    with pytest.raises(SVDLoadError, match="cannot read SVD"):
        RegisterMap.from_file(invalid)
    index = RegisterMap.from_file(SVD)
    with pytest.raises(SVDLookupError, match="unknown SVD peripheral"):
        index.get_peripheral("SPI99")
    with pytest.raises(SVDLookupError, match="unknown SVD register"):
        index.get_register("SPI1", "NOPE")
    with pytest.raises(SVDLookupError, match="unknown SVD address"):
        index.lookup_address(0x12345678)


def test_unsupported_cluster_fails_instead_of_disappearing(tmp_path: Path) -> None:
    source = SVD.read_text(encoding="utf-8")
    cluster = ("<cluster><name>GROUP</name><description>Nested registers</description>"
               "<addressOffset>0x40</addressOffset><register>"
               "<name>VALUE</name><addressOffset>0x04</addressOffset>"
               "</register></cluster>")
    source = source.replace("</registers>\n    </peripheral>",
                            cluster + "</registers>\n    </peripheral>", 1)
    path = tmp_path / "cluster.svd"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(SVDLoadError, match="unsupported SVD register cluster"):
        RegisterMap.from_file(path)


def test_cli_lookup_and_no_qiling_import() -> None:
    command = [sys.executable, "-m", "reflight.cli", "svd"]
    found = subprocess.run(
        command + ["lookup", "--file", str(SVD), "--address", "0x4001300c"],
        capture_output=True, text=True, check=False,
    )
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == "SPI1.DR 0x4001300c"
    missing = subprocess.run(
        command + ["registers", "--file", str(SVD), "--peripheral", "BAD"],
        capture_output=True, text=True, check=False,
    )
    assert missing.returncode != 0
    assert "unknown SVD peripheral" in missing.stderr
    independent = subprocess.run(
        [sys.executable, "-c", "import sys; from reflight.svd import RegisterMap; "
         "assert 'qiling' not in sys.modules; print('independent')"],
        capture_output=True, text=True, check=False,
    )
    assert independent.returncode == 0, independent.stderr
