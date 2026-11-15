"""Byte-exact frames from local Winbond §8.2 and GigaDevice §§6–12."""

import pytest

from reflight.buses import SpiBus
from reflight.flash import FlashError, W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH


def frame(chip, *values):
    chip.select()
    try:
        return bytes(chip.exchange_byte(value) for value in values)
    finally:
        chip.deselect()


def feature(chip, address=0xC0, op=0x0F):
    return frame(chip, op, address, 0)[-1]


def finish(chip):
    chip.tick(10000)
    assert not feature(chip) & 1


def row_frame(chip, op, row):
    return frame(chip, op, row >> 16, (row >> 8) & 255, row & 255)


def load(chip, column, data, op=0x02):
    frame(chip, op, column >> 8, column & 255, *data)


def read(chip, row, column, length, op=0x03):
    row_frame(chip, 0x13, row)
    finish(chip)
    return frame(chip, op, column >> 8, column & 255, 0, *([0] * length))[4:]


@pytest.fixture
def chip(request):
    return getattr(request, 'param', W25N01KV)()


def test_id_dummy_defaults_aliases_and_cs(chip):
    assert frame(chip, 0x9F, 0, 0, 0, 0, 0) == bytes.fromhex('ff ff ef ae 21 ff')
    assert frame(chip, 0x9F, 0) == b'\xff\xff'  # Missing response clocks.
    assert feature(chip, 0xA0) == 0x7C
    assert feature(chip, 0xBF, 0x05) == 0x19
    assert frame(chip, 0x05, 0xCF, 0, 0) == b'\xff\xff\x00\x00'
    assert feature(chip, 0x10) == 0x30
    for address in (0x20, 0x30, 0x40, 0x50):
        assert feature(chip, address) == 0
    chip.select()
    chip.exchange_byte(0x06)
    assert not chip.wel
    chip.deselect()
    assert feature(chip) == 2
    frame(chip, 0x04)
    assert feature(chip) == 0
    frame(chip, 0x01, 0xA7, 0)
    assert feature(chip, 0xA0) == 0


def test_wren_load_order_and_no_array_write_before_execute(chip):
    frame(chip, 0x1F, 0xA0, 0)
    load(chip, 0, b'\x12')
    assert chip.cache[0] == 255
    frame(chip, 0x06)
    load(chip, 0, b'\x12')
    assert chip.storage.read_page(0, 0, 1) == b'\xff'
    frame(chip, 0x04)
    row_frame(chip, 0x10, 0)
    assert feature(chip) == 0
    assert chip.storage.allocated_pages == 0
    frame(chip, 0x06)
    row_frame(chip, 0x10, 0)
    assert feature(chip) == 3
    assert chip.storage.allocated_pages == 0
    chip.tick(chip.program_ticks - 1)
    assert feature(chip) & 1
    chip.tick(1)
    assert feature(chip) == 0
    assert read(chip, 0, 0, 2) == b'\x12\xff'


def test_cache_load_random_load_and_reload_separation(chip):
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    load(chip, 511, b'\x12\x34')
    load(chip, 2047, b'\x56\x78', 0x84)
    row_frame(chip, 0x10, 63)
    finish(chip)
    assert read(chip, 63, 510, 4, 0x0B) == b'\xff\x12\x34\xff'
    assert read(chip, 63, 2046, 4) == b'\xff\x56\x78\xff'
    frame(chip, 0x06)
    load(chip, 1, b'\x00')  # 02 clears the rest of cache to FF.
    assert chip.cache[511] == 255
    assert chip.storage.read_page(63, 511, 1) == b'\x12'
    row_frame(chip, 0x13, 63)
    assert chip.cache[1] == 0
    finish(chip)
    assert chip.cache[511] == 0x12
    assert feature(chip) == 0  # Winbond page read clears WEL.


def test_protection_failures_unlock_and_failure_clear(chip):
    frame(chip, 0x06)
    load(chip, 0, b'\x00')
    row_frame(chip, 0x10, 0)
    finish(chip)
    assert feature(chip) == 8
    assert chip.storage.allocated_pages == 0
    frame(chip, 0x06)
    row_frame(chip, 0xD8, 0)
    finish(chip)
    assert feature(chip) == 12
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    row_frame(chip, 0x10, 0)
    finish(chip)
    assert feature(chip) == 4
    frame(chip, 0x06)
    row_frame(chip, 0xD8, 0)
    finish(chip)
    assert feature(chip) == 0


@pytest.mark.parametrize('bp', range(16))
@pytest.mark.parametrize('bottom', [False, True])
def test_winbond_all_protection_ranges(chip, bp, bottom):
    frame(chip, 0x1F, 0xA0, (bp << 3) | (4 if bottom else 0))
    for block in range(1024):
        expected = bp >= 10 or (bp > 0 and (block < 2**bp if bottom else block >= 1024 - 2**bp))
        assert chip.is_protected(block * 64) == expected


def test_feature_masks_lock_and_reset_differences(chip):
    frame(chip, 0x1F, 0xC0, 255)
    assert feature(chip) == 0
    frame(chip, 0x1F, 0xB0, 7)
    assert feature(chip, 0xB0) == 15  # BUF fixed, ECC off.
    frame(chip, 0x1F, 0x10, 0x2F)
    assert feature(chip, 0x10) == 0x20
    frame(chip, 0x1F, 0xA0, 1)  # SRP1=1/SRP0=0: power lockdown.
    frame(chip, 0x1F, 0xA0, 0)
    assert feature(chip, 0xA0) == 1
    frame(chip, 0xFF)
    finish(chip)
    assert feature(chip, 0xA0) == 1
    assert feature(chip, 0xB0) == 15
    assert feature(chip, 0x10) == 0x20
    frame(chip, 0x99)  # Not armed: ignore.
    assert not chip.busy
    frame(chip, 0x66)
    frame(chip, 0x99)
    finish(chip)
    assert feature(chip, 0xA0) == 0x7C
    assert feature(chip, 0xB0) == 0x19
    assert feature(chip, 0x10) == 0x30


@pytest.mark.parametrize('row', [0, 63, 64, 65535])
@pytest.mark.parametrize('chip', [W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH], indirect=True)
def test_row_bounds_nand_bits_and_block_erase(chip, row):
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    load(chip, 2047, b'\x00\x55')
    row_frame(chip, 0x10, row)
    finish(chip)
    frame(chip, 0x06)
    load(chip, 2047, b'\xff\xff')
    row_frame(chip, 0x10, row)
    finish(chip)
    assert read(chip, row, 2047, 2) == b'\x00\x55'
    frame(chip, 0x06)
    row_frame(chip, 0xD8, row)
    finish(chip)
    assert read(chip, row, 2047, 2) == b'\xff\xff'


def test_ecc_enabled_ignores_managed_parity_disabled_keeps_all_spare(chip):
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    load(chip, 2048, b'\x00' * 96)
    row_frame(chip, 0x10, 0)
    finish(chip)
    assert read(chip, 0, 2048, 96) == b'\x00' * 64 + b'\xff' * 32
    frame(chip, 0x1F, 0xB0, 0)
    frame(chip, 0x06)
    load(chip, 2048, bytes(range(96)))
    row_frame(chip, 0x10, 65535)
    finish(chip)
    assert read(chip, 65535, 2048, 96) == bytes(range(96))


def test_dummy_bits_and_no_effective_column_wrapping(chip):
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    load(chip, 0xF001, b'\x12')  # High four bits dummy.
    row_frame(chip, 0x10, 0xFE0000)  # Winbond high row byte ignored.
    finish(chip)
    assert read(chip, 0, 0xF001, 1) == b'\x12'
    for op in (0x02, 0x84, 0x03, 0x0B):
        frame(chip, 0x06)
        with pytest.raises(FlashError, match='column'):
            frame(chip, op, 0x08, 0x60, 0, 0)
    frame(chip, 0x06)
    load(chip, 2143, b'\x12\x34')  # Oversized tail ignored, never wrapped.
    assert chip.cache[0] == 255
    assert chip.cache[-1] == 0x12
    assert frame(chip, 0x03, 8, 95, 0, 0, 0)[4:] == b'\x12\xff'


@pytest.mark.parametrize('values', [(0x1F,), (0x1F, 0xA0), (0x1F, 0xA0, 0, 0),
    (0x13,), (0x13, 0, 0), (0x10, 0, 0), (0xD8, 0, 0), (0x02, 0, 0), (0x84, 0),
    (0x06, 0), (0x04, 0), (0xFF, 0)])
@pytest.mark.parametrize('chip', [W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH], indirect=True)
def test_incomplete_or_oversized_fixed_frames_do_not_mutate(chip, values):
    before = bytes(chip.cache), feature(chip, 0xA0)
    frame(chip, *values)
    assert (bytes(chip.cache), feature(chip, 0xA0)) == before
    assert chip.storage.allocated_pages == 0
    assert feature(chip) == 0


def test_busy_ignores_writes_id_available_reset_cancels_and_preserves_array(chip):
    bus = SpiBus()
    bus.attach('PA7', chip)
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    load(chip, 0, b'\x12')
    row_frame(chip, 0x10, 0)
    frame(chip, 0x04)
    frame(chip, 0x1F, 0xA0, 0x7C)
    assert feature(chip, 0xA0) == 0
    assert feature(chip) == 3
    assert frame(chip, 0x9F, 0, 0)[-1] == 0xEF
    bus.tick(chip.program_ticks)
    assert read(chip, 0, 0, 1) == b'\x12'
    frame(chip, 0x06)
    row_frame(chip, 0xD8, 0)
    frame(chip, 0xFF)
    finish(chip)
    assert read(chip, 0, 0, 1) == b'\x12'
    assert feature(chip) == 0
    frame(chip, 0x66)
    frame(chip, 0x99)
    finish(chip)
    assert read(chip, 0, 0, 1) == b'\x12'


@pytest.mark.parametrize('op', [0x3B, 0x6B, 0xBB, 0xEB, 0xEE, 0x32, 0x34, 0xC4, 0xAB])
@pytest.mark.parametrize('chip', [W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH], indirect=True)
def test_unsupported_commands_explicit(chip, op):
    with pytest.raises(FlashError, match='unsupported'):
        frame(chip, op)


def test_unsupported_feature_modes(chip):
    for value in (0x20, 0x40, 0x80):
        with pytest.raises(FlashError, match='unsupported'):
            frame(chip, 0x1F, 0xB0, value)
    with pytest.raises(FlashError, match='feature'):
        feature(chip, 0xD0)


@pytest.mark.parametrize('factory,did', [(GD5F1GQ5UExxH, 0x31), (GD5F1GQ5RExxH, 0x21)])
def test_gd_id_features_masks_and_reset(factory, did):
    gd = factory()
    assert frame(gd, 0x9F, 0, 0, 0, 0) == bytes([255, 255, 0xC8, did, 255])
    assert feature(gd, 0xA0) == 0x38
    assert feature(gd, 0xB0) == 0x10
    assert feature(gd, 0xF0) == 8
    frame(gd, 0x1F, 0xA0, 0xC7)
    assert feature(gd, 0xA0) == 0x86  # Reserved bits 6/0 masked.
    frame(gd, 0x1F, 0xD0, 255)
    assert feature(gd, 0xD0) == 0x60
    frame(gd, 0x1F, 0xB0, 0x2F)
    assert feature(gd, 0xB0) == 9  # QE + sticky BPL; reserved bits zero.
    frame(gd, 0x1F, 0xB0, 0)
    assert feature(gd, 0xB0) == 8
    frame(gd, 0x1F, 0xA0, 0)
    assert feature(gd, 0xA0) == 0x86  # BPL locked.
    for address in (0xC0, 0xF0):
        before = feature(gd, address)
        frame(gd, 0x1F, address, 255)
        assert feature(gd, address) == before
    load(gd, 1, b'\x12')
    frame(gd, 0xFF)
    finish(gd)
    assert gd.cache[1] == 0x12
    assert feature(gd, 0xD0) == 0x60
    assert feature(gd, 0xB0) == 8
    frame(gd, 0x66)
    frame(gd, 0x99)
    finish(gd)
    assert feature(gd, 0xA0) == 0x38
    assert feature(gd, 0xB0) == 0x10
    assert feature(gd, 0xD0) == 0
    assert gd.cache == b'\xff' * 2112
    for value in (0x40, 0x80):
        with pytest.raises(FlashError, match='OTP'):
            frame(gd, 0x1F, 0xB0, value)
    for alias in (0x01, 0x05):
        with pytest.raises(FlashError, match='opcode'):
            frame(gd, alias)


@pytest.mark.parametrize('bp', range(8))
@pytest.mark.parametrize('inv,cmp', [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_gd_protection_table_ranges(bp, inv, cmp):
    gd = GD5F1GQ5UExxH()
    frame(gd, 0x1F, 0xA0, bp << 3 | inv << 2 | cmp << 1)
    for block in range(1024):
        if bp == 0:
            expected = False
        elif bp == 7:
            expected = True
        elif cmp and bp == 6:
            expected = block == 0
        else:
            end = (16, 32, 64, 128, 256, 512)[bp - 1]
            expected = (block < end if inv else block >= 1024 - end) != bool(cmp)
        assert gd.is_protected(block * 64) == expected


def test_gd_load_before_wren_protected_failure_is_not_busy_and_bounds():
    gd = GD5F1GQ5UExxH()
    load(gd, 511, b'\x12\x34')
    load(gd, 2048, bytes(range(64)), 0x84)
    assert gd.cache[511:513] == b'\x12\x34'
    row_frame(gd, 0x10, 0)  # Missing WREN: ignore, no fake P_FAIL.
    assert feature(gd) == 0
    frame(gd, 0x06)
    row_frame(gd, 0x10, 0)  # Default locked: fail without OIP.
    assert feature(gd) == 8
    frame(gd, 0x06)
    row_frame(gd, 0xD8, 0)
    assert feature(gd) == 12
    frame(gd, 0x1F, 0xA0, 0)
    frame(gd, 0x06)
    row_frame(gd, 0x10, 65535)
    assert feature(gd) == 7  # WEL, OIP, old E_FAIL; P_FAIL cleared.
    assert frame(gd, 0x9F, 0, 0, 0) == b'\xff' * 4  # ID ignored while busy.
    finish(gd)
    assert read(gd, 65535, 511, 2) == b'\x12\x34'
    assert read(gd, 65535, 2048, 64, 0x0B) == bytes(range(64))
    assert feature(gd, 0xF0) == 0
    frame(gd, 0x06)
    row_frame(gd, 0x13, 65535)
    finish(gd)
    assert feature(gd) & 2  # Unlike Winbond, page read retains WEL.
    for op in (0x13, 0x10, 0xD8):
        with pytest.raises(FlashError, match='row'):
            row_frame(gd, op, 65536)
    for op in (0x02, 0x84, 0x03, 0x0B):
        with pytest.raises(FlashError, match='column'):
            frame(gd, op, 8, 64, 0, 0)
    frame(gd, 0xFF)
    finish(gd)
    assert read(gd, 65535, 511, 2) == b'\x12\x34'


def test_coexistence_generic_factory_and_independent_chip_selects():
    from reflight.devices import make_device
    from reflight.sensors import SensorError, make_sensor
    bus = SpiBus()
    for cs, model in [('PA7', 'W25N01KV'), ('PA6', 'GD5F1GQ5UExxH'), ('PA5', 'GD5F1GQ5RExxH')]:
        bus.attach(cs, make_device(model))
    for cs, expected in [('PA7', b'\xef\xae\x21'), ('PA6', b'\xc8\x31\xff'), ('PA5', b'\xc8\x21\xff')]:
        bus.set_cs(cs, 0)
        result = bytes(bus.exchange_byte(v) for v in (0x9F, 0, 0, 0, 0))
        bus.set_cs(cs, 1)
        assert result[2:] == expected
    a, b = bus.devices['PA7'], bus.devices['PA6']
    frame(a, 0x1F, 0xA0, 0)
    frame(a, 0x06)
    load(a, 0, b'\x12')
    row_frame(a, 0x10, 0)
    bus.tick(10000)
    assert a.storage.read_page(0, 0, 1) == b'\x12'
    assert b.storage.read_page(0, 0, 1) == b'\xff'
    assert feature(b, 0xA0) == 0x38
    for model in ('GD5F1GQ5xExxH', 'Unknown'):
        with pytest.raises(SensorError, match='unsupported'):
            make_device(model)
    with pytest.raises(SensorError, match='unsupported sensor'):
        make_sensor('W25N01KV')


@pytest.mark.parametrize('chip', [W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH], indirect=True)
def test_reset_aborts_pending_program_and_erase_preserving_committed_data(chip):
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    load(chip, 0, b'\x12')
    row_frame(chip, 0x10, 0)
    finish(chip)
    frame(chip, 0x06)
    load(chip, 0, b'\x00')
    row_frame(chip, 0x10, 0)
    chip.tick(chip.program_ticks - 1)
    frame(chip, 0xFF)
    finish(chip)
    assert read(chip, 0, 0, 1) == b'\x12'
    row_frame(chip, 0xD8, 0)  # Missing WREN must not erase committed contents.
    assert not chip.busy
    assert read(chip, 0, 0, 1) == b'\x12'
    frame(chip, 0x06)
    row_frame(chip, 0xD8, 0)
    frame(chip, 0x66)
    frame(chip, 0x99)
    finish(chip)
    assert read(chip, 0, 0, 1) == b'\x12'


@pytest.mark.parametrize('chip', [W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH], indirect=True)
def test_address_range_protection_blocks_real_program_and_erase(chip):
    # BP=1 protects the upper range on both chips (different range sizes).
    frame(chip, 0x1F, 0xA0, 8)
    for row, protected in [(0, False), (65535, True)]:
        frame(chip, 0x06)
        load(chip, 0, b'\x12')
        row_frame(chip, 0x10, row)
        finish(chip)
        assert bool(feature(chip) & 8) == protected
        assert read(chip, row, 0, 1) == (b'\xff' if protected else b'\x12')
    # Now protect the lower range and verify its existing data cannot erase.
    frame(chip, 0x1F, 0xA0, 12)
    frame(chip, 0x06)
    row_frame(chip, 0xD8, 0)
    finish(chip)
    assert feature(chip) & 4
    assert read(chip, 0, 0, 1) == b'\x12'


def test_busy_command_is_ignored_even_if_operation_finishes_mid_frame():
    chip = W25N01KV()
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    row_frame(chip, 0x13, 0)
    chip.select()
    chip.exchange_byte(0x1F)
    chip.tick(chip.read_ticks)
    chip.exchange_byte(0xA0)
    chip.exchange_byte(0x7C)
    chip.deselect()
    assert feature(chip, 0xA0) == 0


def test_simultaneous_pending_operations_independent_on_one_bus():
    a, b = W25N01KV(), GD5F1GQ5UExxH()
    bus = SpiBus()
    bus.attach('PA7', a)
    bus.attach('PA6', b)
    for chip, data in [(a, b'\x12'), (b, b'\x34')]:
        frame(chip, 0x1F, 0xA0, 0)
        frame(chip, 0x06)
        load(chip, 0, data)
        row_frame(chip, 0x10, 0)
    bus.tick(1023)
    assert a.busy and b.busy
    assert a.storage.allocated_pages == b.storage.allocated_pages == 0
    bus.tick()
    assert read(a, 0, 0, 1) == b'\x12'
    assert read(b, 0, 0, 1) == b'\x34'


@pytest.mark.parametrize('chip', [W25N01KV, GD5F1GQ5UExxH, GD5F1GQ5RExxH], indirect=True)
def test_partial_frames_with_wel_do_not_corrupt_existing_cache_or_array(chip):
    frame(chip, 0x1F, 0xA0, 0)
    frame(chip, 0x06)
    load(chip, 17, b'\x12')
    row_frame(chip, 0x10, 0)
    finish(chip)
    frame(chip, 0x06)
    load(chip, 17, b'\x34')
    before = bytes(chip.cache)
    for values in [(0x10, 0, 0), (0xD8, 0, 0), (0x13, 0, 0),
                   (0x02, 0, 0), (0x84, 0, 0), (0x1F, 0xA0)]:
        frame(chip, *values)
        assert bytes(chip.cache) == before
        assert chip.storage.read_page(0, 17, 1) == b'\x12'
        assert feature(chip) == 2
        assert feature(chip, 0xA0) == 0
    # One dummy byte is mandatory before cache response bytes.
    assert frame(chip, 0x03, 0, 17, 0) == b'\xff' * 4
    assert frame(chip, 0x0B, 0, 17, 0, 0)[-1] == 0x34
    load(chip, 18, b'\x56')
    assert chip.cache[17:19] == b'\xff\x56'

