"""Winbond W25N01KV Rev H: features §7, protection §7.5, commands §8.2."""

from .spi_nand import FlashError, _SpiNand


class W25N01KV(_SpiNand):
    jedec_id = bytes.fromhex("ef ae 21")
    aliases = {0x05: 0x0F, 0x01: 0x1F}

    def __init__(self) -> None:
        super().__init__(96)

    def _power_on_features(self) -> None:
        # Figure 11: BP[3:0],TB=1; ECC-E,BUF,H-DIS=1; BFD=3.
        self.protection = 0x7C
        self.configuration = 0x19
        self.bfd = 0x30

    def _allowed_busy(self, op: int) -> bool:
        return op in (0x0F, 0x9F, 0xFF, 0x66, 0x99)

    def _read_feature(self, address: int) -> int:
        register = address & 0xF0 if address >= 0xA0 else address
        if register == 0xA0:
            return self.protection
        if register == 0xB0:
            return self.configuration
        if register == 0xC0:
            return self.status
        if register == 0x10:
            return self.bfd
        if register in (0x20, 0x30, 0x40, 0x50):
            return 0  # No injected errors; no ECC correction/parity generation.
        raise FlashError(f"unsupported Winbond feature 0x{address:02x}")

    def _write_feature(self, address: int, value: int) -> None:
        register = address & 0xF0 if address >= 0xA0 else address
        if register == 0xA0:
            # §7.1.3: SRP1/SRP0=(1,0) locks SR-1 until power-on reset.
            if self.protection & 0x81 != 0x01:
                self.protection = value
        elif register == 0xB0:
            if value & 0xE0:
                raise FlashError("unsupported Winbond OTP/SR1-L mode")
            # §7.2: volatile ECC-E, ODS, H-DIS; BUF fixed to one.
            self.configuration = (value & 0x17) | 0x08
        elif register == 0x10:
            if (value >> 4) & 7 not in (1, 2, 3):
                raise FlashError("unsupported Winbond ECC detection threshold")
            self.bfd = value & 0x70
        else:
            self._read_feature(address)  # Known read-only fields ignore writes.

    def _row(self) -> int:
        # §8.1.2 note: PA[23:16] is explicitly ignored on this chip.
        return (self._frame[2] << 8) | self._frame[3]

    def _can_load(self) -> bool:
        return self.wel  # §8.2.5/8.2.8, unlike GigaDevice.

    def is_protected(self, row: int) -> bool:
        bp = (self.protection >> 3) & 15
        if bp == 0:
            return False
        if bp >= 10:
            return True
        blocks = 1 << bp
        block = row // 64
        return block < blocks if self.protection & 4 else block >= 1024 - blocks

    def _before_read(self, row: int) -> None:
        self.wel = False  # §7.3.4 and §8.2.6.

    def _soft_reset(self) -> None:
        # Figure 11: feature settings retained; no page-0 reload for FF.
        # Cache retained, consistent with no reload. NAND always survives.
        pass

    def _modify_array(self, op: int, row: int) -> None:
        failure = 8 if op == 0x10 else 4
        if self.is_protected(row):
            # §7.3.2/3: protected attempts set FAIL. Finite modeled BUSY.
            self._start("failure", failure, self.program_ticks if op == 0x10 else self.erase_ticks)
            return
        self.failures &= ~failure
        if op == 0x10:
            data = bytes(self.cache)
            if self.configuration & 0x10:
                # §7.2.4: 840h–85Fh is IC-managed. Approximation: ignore
                # user parity programming and keep erased parity FF, no ECC.
                data = data[:0x840] + b"\xff" * 32
            self._start("program", (row, data), self.program_ticks)
        else:
            self._start("erase", row, self.erase_ticks)
