"""GD5F1GQ5U/RExxH Rev 1.4: command/state differences in §§7–12.

Only the two explicitly named U/R variants are selectable; no family alias.
"""

from .spi_nand import FlashError, _SpiNand


class _GD5F1GQ5(_SpiNand):
    def __init__(self) -> None:
        super().__init__(64)

    def _power_on_features(self) -> None:
        # Tables 12-1/2: BP2:0=111, ECC_EN=1, all other writable bits=0.
        self.protection = 0x38
        self.configuration = 0x10
        self.driver = 0
        self.selected_row = 0

    def _power_on_cache(self) -> None:
        self.cache[:] = self.storage.read_page(0)  # §2: power-on page-0 read.

    def _allowed_busy(self, op: int) -> bool:
        # §§8–11 polling path: GET FEATURES, or reset to abort operations.
        return op in (0x0F, 0xFF, 0x66, 0x99)

    def _read_feature(self, address: int) -> int:
        if address == 0xA0:
            return self.protection
        if address == 0xB0:
            return self.configuration
        if address == 0xC0:
            return self.status
        if address == 0xD0:
            return self.driver
        if address == 0xF0:
            return 8 if self.is_protected(self.selected_row) else 0
        raise FlashError(f"unsupported GigaDevice feature 0x{address:02x}")

    def _write_feature(self, address: int, value: int) -> None:
        if address == 0xA0:
            # §12.6 BPL locks protection until power-on reset. WP# inactive.
            if not self.configuration & 8:
                self.protection = value & 0xBE
        elif address == 0xB0:
            if value & 0xC0:
                raise FlashError("unsupported GigaDevice OTP mode")
            # Reserved bits zero; BPL cannot be cleared by software.
            self.configuration = (value & 0x19) | (self.configuration & 8)
        elif address == 0xD0:
            self.driver = value & 0x60
        else:
            self._read_feature(address)

    def _row(self) -> int:
        row = int.from_bytes(self._frame[1:4], 'big')
        # §3: only 65536 effective rows. Unlike Winbond's explicit PA23:16
        # dummy note, GD's command table supplies a 24-bit row; reject excess.
        if row >= self.storage.page_count:
            raise FlashError(f"GigaDevice row {row} outside array")
        return row

    def _can_load(self) -> bool:
        return True  # §9.1: 02 load -> 06 WREN -> 10 execute.

    def is_protected(self, row: int) -> bool:
        # Table 12-7, including its unusual CMP=1/BP=110 block-0 entry.
        bp = (self.protection >> 3) & 7
        if bp == 0:
            return False
        if bp == 7:
            return True
        block = row // 64
        inverted, complement = bool(self.protection & 4), bool(self.protection & 2)
        if complement and bp == 6:
            return block == 0
        count = 1 << (bp + 3)
        protected = block < count if inverted else block >= 1024 - count
        return not protected if complement else protected

    def _before_read(self, row: int) -> None:
        self.selected_row = row  # WEL is retained; §7.2 doesn't clear on read.

    def _soft_reset(self) -> None:
        # §9.1 note 1 / Table 12-2: cache and feature settings retained.
        pass

    def _modify_array(self, op: int, row: int) -> None:
        self.selected_row = row
        failure = 8 if op == 0x10 else 4
        self.failures &= ~failure
        if self.is_protected(row):
            # §12.5 explicitly requires OIP remains zero for protected writes.
            self.failures |= failure
            self.wel = False
            return
        if op == 0x10:
            # §2 / §12.7: all 64 spare bytes are available with ECC enabled.
            self._start("program", (row, bytes(self.cache)), self.program_ticks)
        else:
            self._start("erase", row, self.erase_ticks)


class GD5F1GQ5UExxH(_GD5F1GQ5):
    jedec_id = bytes.fromhex("c8 31")


class GD5F1GQ5RExxH(_GD5F1GQ5):
    jedec_id = bytes.fromhex("c8 21")
