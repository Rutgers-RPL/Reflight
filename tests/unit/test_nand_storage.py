"""Physical NAND semantics; no chip protocol or firmware behavior is implied."""

from itertools import islice
import tracemalloc

import pytest

from reflight.flash import NandStorage, NandStorageError


@pytest.fixture(params=[64, 96], ids=["gd-spare", "winbond-spare"])
def nand(request):
    return NandStorage(spare_bytes=request.param)


def test_geometry_erased_first_last_and_sparse_reads(nand):
    assert nand.block_count == 1024
    assert nand.pages_per_block == 64
    assert nand.page_count == 65536
    assert nand.main_size == 134217728
    for row in (0, 63, 64, 65535):
        assert nand.read_page(row) == b"\xff" * (2048 + nand.spare_bytes)
        assert nand.read_page(row, 2048) == b"\xff" * nand.spare_bytes
        assert nand.read_page(row, nand.page_size - 1) == b"\xff"
    assert nand.allocated_pages == 0


def test_partial_program_and_preserves_neighbors_and_only_clears_bits(nand):
    nand.program_page(0, 0, b"\x00\xf0\xaa")
    nand.program_page(0, 1, b"\x0f\xcc\x12")
    nand.program_page(0, 0, b"\xff" * 5)
    assert nand.read_page(0, 0, 6) == b"\x00\x00\x88\x12\xff\xff"
    assert nand.read_page(1) == b"\xff" * nand.page_size
    assert nand.read_page(65535) == b"\xff" * nand.page_size
    assert nand.allocated_pages == 1


def test_main_spare_boundary_and_last_byte_last_row(nand):
    nand.program_page(65535, 2047, b"\x12\x34\x56")
    nand.program_page(65535, nand.page_size - 1, b"\x80")
    assert nand.read_page(65535, 2046, 5) == b"\xff\x12\x34\x56\xff"
    assert nand.read_page(65535, nand.page_size - 2) == b"\xff\x80"
    assert nand.read_page(65534) == b"\xff" * nand.page_size


def test_erase_entire_block_including_spare_and_preserve_neighbors(nand):
    for row in range(63, 129):
        nand.program_page(row, 0, b"\x00")
        nand.program_page(row, 2048, b"\x55" * nand.spare_bytes)
    assert nand.allocated_pages == 66
    nand.erase_block(1)
    for row in range(64, 128):
        assert nand.read_page(row) == b"\xff" * nand.page_size
    for row in (63, 128):
        assert nand.read_page(row, 0, 1) == b"\x00"
        assert nand.read_page(row, 2048) == b"\x55" * nand.spare_bytes
    assert nand.allocated_pages == 2
    nand.erase_block(1)  # Re-erasing an implicit block is harmless.
    assert nand.allocated_pages == 2


@pytest.mark.parametrize("block", [0, 1023])
def test_first_last_block_erase_and_reprogram(nand, block):
    row = block * 64 + 63
    nand.program_page(row, nand.page_size - 1, b"\x00")
    nand.erase_block(block)
    assert nand.read_page(row) == b"\xff" * nand.page_size
    assert nand.allocated_pages == 0
    nand.program_page(row, nand.page_size - 1, b"\xf0")
    assert nand.read_page(row, nand.page_size - 1) == b"\xf0"


def test_no_allocation_for_empty_or_erased_program_and_reads_are_immutable(nand):
    nand.program_page(0, 0, b"")
    nand.program_page(0, 0, b"\xff" * nand.page_size)
    assert nand.read_page(0, 0, 0) == b""
    assert nand.allocated_pages == 0
    sample = bytearray(b"\x12")
    nand.program_page(0, 0, sample)
    before = nand.read_page(0)
    sample[0] = 0
    nand.program_page(0, 0, memoryview(b"\x00"))
    assert isinstance(before, bytes)
    assert before[0] == 0x12
    assert nand.read_page(0, 0, 1) == b"\x00"


@pytest.mark.parametrize("bad", [-1, 65536, 0x1000000, 1.5, True, "0", None])
def test_invalid_rows_do_not_alias_or_mutate(nand, bad):
    nand.program_page(0, 0, b"\x12")
    with pytest.raises(NandStorageError, match="row"):
        nand.read_page(bad)
    with pytest.raises(NandStorageError, match="row"):
        nand.program_page(bad, 0, b"\x00")
    assert nand.read_page(0, 0, 1) == b"\x12"
    assert nand.allocated_pages == 1


@pytest.mark.parametrize("bad", [-1, 1024, 65536, 1.5, True, "0", None])
def test_invalid_blocks_do_not_erase(nand, bad):
    nand.program_page(0, 0, b"\x12")
    with pytest.raises(NandStorageError, match="block"):
        nand.erase_block(bad)
    assert nand.read_page(0, 0, 1) == b"\x12"


def test_invalid_columns_and_cross_page_regions_are_atomic(nand):
    nand.program_page(0, nand.page_size - 1, b"\x12")
    for column in (-1, nand.page_size, 4095, 65536, 1.5, True, "0", None):
        with pytest.raises(NandStorageError, match="column"):
            nand.read_page(0, column, 0)
        with pytest.raises(NandStorageError, match="column"):
            nand.program_page(0, column, b"")
    for length in (-1, nand.page_size + 1, 1.5, True):
        with pytest.raises(NandStorageError, match="length"):
            nand.read_page(0, 0, length)
    with pytest.raises(NandStorageError, match="length"):
        nand.read_page(0, nand.page_size - 1, 2)
    with pytest.raises(NandStorageError, match="length"):
        nand.program_page(0, nand.page_size - 1, b"\x00\x00")
    assert nand.read_page(0, nand.page_size - 1) == b"\x12"
    assert nand.read_page(1, 0, 1) == b"\xff"
    assert nand.allocated_pages == 1


@pytest.mark.parametrize("data", [3, "abc", [0, 1], None])
def test_invalid_program_data(nand, data):
    with pytest.raises(NandStorageError, match="bytes-like"):
        nand.program_page(0, 0, data)
    assert nand.allocated_pages == 0


@pytest.mark.parametrize("spare", [-1, 0, 63, 65, 97, 64.0, True, "64", None])
def test_invalid_geometry(spare):
    with pytest.raises(NandStorageError, match="spare_bytes"):
        NandStorage(spare_bytes=spare)


def test_instances_are_independent(nand):
    other = NandStorage(spare_bytes=nand.spare_bytes)
    nand.program_page(0, 0, b"\x12")
    other.erase_block(0)
    assert nand.read_page(0, 0, 1) == b"\x12"
    assert other.read_page(0, 0, 1) == b"\xff"


def test_stream_order_length_gaps_erasure_and_omits_spare(nand):
    markers = {0: 0x12, 63: 0x34, 64: 0x56, 65535: 0x78}
    for row, marker in markers.items():
        nand.program_page(row, 2047, bytes([marker, 0x00]))
    nand.erase_block(1)
    markers.pop(64)
    total = 0
    # A bounded sink consumes every page, checking the full image without
    # retaining it or creating a 128 MiB file.
    for row, page in enumerate(nand.iter_main_pages()):
        assert len(page) == 2048
        assert page[:-1] == b"\xff" * 2047
        assert page[-1] == markers.get(row, 0xFF)
        total += len(page)
    assert total == 134217728
    assert nand.allocated_pages == 3


def test_creation_and_partial_stream_use_bounded_memory(nand):
    tracemalloc.start()
    try:
        storage = NandStorage(spare_bytes=nand.spare_bytes)
        storage.program_page(0, 0, b"\x12")
        pages = list(islice(storage.iter_main_pages(), 3))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert pages == [b"\x12" + b"\xff" * 2047, b"\xff" * 2048, b"\xff" * 2048]
    assert peak < 1024 * 1024  # Accommodate interpreter overhead, exclude full dump.
    assert storage.allocated_pages == 1
