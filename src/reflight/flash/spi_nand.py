"""Shared single-lane framing and array operations, with chip-owned policies.

Winbond Rev H §8.1.2/§8.2 and GD Rev 1.4 §6/§§7–12:
9F dummy -> ID; 0F address -> repeated feature; 1F address value;
13/10/D8 + three row bytes; 03/0B + two column bytes + dummy -> cache;
02/84 + two column bytes + data. Mutations commit at rising CS, never on
an incomplete frame. Array operations finish only after deterministic ticks.
"""

from .storage import NandStorage


class FlashError(ValueError):
    """Invalid flash access or explicitly unsupported command/feature mode."""


class _SpiNand:
    aliases: dict[int, int] = {}
    read_ticks = 512
    program_ticks = 1024
    erase_ticks = 2048
    reset_ticks = 128
    commands = {0x9F, 0x0F, 0x1F, 0x06, 0x04, 0xFF, 0x66, 0x99,
                0x13, 0x03, 0x0B, 0x02, 0x84, 0x10, 0xD8}
    multi_lane = {0x3B, 0x6B, 0xBB, 0xEB, 0xEE, 0x32, 0x34, 0xC4}

    def __init__(self, spare_bytes: int) -> None:
        self.storage = NandStorage(spare_bytes=spare_bytes)
        self.cache = bytearray(b"\xff" * self.storage.page_size)
        self._selected = False
        self._pending = None
        self._remaining = 0
        self._reset_enabled = False
        self.wel = False
        self.failures = 0
        self._power_on_features()

    @property
    def busy(self) -> bool:
        return self._pending is not None

    @property
    def status(self) -> int:
        return self.failures | (2 if self.wel else 0) | int(self.busy)

    def select(self) -> None:
        if self._selected:
            raise FlashError("flash selected twice")
        self._selected = True
        self._frame = bytearray()
        self._count = 0
        self._ignored = False

    def exchange_byte(self, mosi: int) -> int:
        if not self._selected or type(mosi) is not int or not 0 <= mosi <= 255:
            raise FlashError("flash transfer requires CS low and one byte")
        pos = self._count
        self._count += 1
        if pos == 0:
            op = self.aliases.get(mosi, mosi)
            self._frame.append(op)
            # Latch BUSY eligibility for the entire CS interval. Reset can
            # cancel an operation; chip policies differ for identification.
            self._ignored = self.busy and not self._allowed_busy(op)
            if self._ignored:
                return 0xFF
            if op in self.multi_lane:
                self._ignored = True
                raise FlashError(f"unsupported multi-lane flash opcode 0x{mosi:02x}")
            if op not in self.commands:
                self._ignored = True
                raise FlashError(f"unsupported flash opcode 0x{mosi:02x}")
            return 0xFF
        if self._ignored:
            return 0xFF
        op = self._frame[0]
        # Bound the transaction buffer, including oversized program loads.
        if len(self._frame) < self.storage.page_size + 3:
            self._frame.append(mosi)
        if op == 0x9F and pos >= 2:
            offset = pos - 2
            return self.jedec_id[offset] if offset < len(self.jedec_id) else 0xFF
        if op == 0x0F and pos >= 2:
            return self._read_feature(self._frame[1])
        if op in (0x03, 0x0B) and pos >= 4:
            column = self._column() + pos - 4
            return self.cache[column] if column < len(self.cache) else 0xFF
        return 0xFF  # Includes all documented dummy/header clocks.

    def deselect(self) -> None:
        if not self._selected:
            raise FlashError("flash deselected without selection")
        self._selected = False
        if not self._frame or self._ignored:
            return
        self._commit()

    def _column(self) -> int:
        # Both command tables specify CA[15:12] as dummy, not address bits.
        column = ((self._frame[1] << 8) | self._frame[2]) & 0xFFF
        if column >= len(self.cache):
            raise FlashError(f"flash column {column} outside page of {len(self.cache)} bytes")
        return column

    def _commit(self) -> None:
        op, size = self._frame[0], self._count
        armed = self._reset_enabled
        self._reset_enabled = False
        if op == 0x66 and size == 1:
            self._reset_enabled = True
        elif (op == 0xFF or (op == 0x99 and armed)) and size == 1:
            self._pending = None  # Atomic cancellation; no power-cut corruption.
            self.wel = False
            self.failures = 0
            if op == 0x99:
                self._power_on_features()
                self._power_on_cache()
            else:
                self._soft_reset()
            self._start("reset", None, self.reset_ticks)
        elif op == 0x06 and size == 1:
            self.wel = True
        elif op == 0x04 and size == 1:
            self.wel = False
        elif op == 0x1F and size == 3:
            self._write_feature(self._frame[1], self._frame[2])
        elif op in (0x02, 0x84) and size >= 4:
            if not self._can_load():
                return
            column = self._column()
            data = self._frame[3:3 + len(self.cache) - column]
            if op == 0x02:
                self.cache[:] = b"\xff" * len(self.cache)
            self.cache[column:column + len(data)] = data
        elif op in (0x13, 0x10, 0xD8) and size == 4:
            if op != 0x13 and not self.wel:
                return  # Missing WREN is ignored; no invented failure bit.
            row = self._row()
            if op == 0x13:
                self._before_read(row)
                self._start("read", row, self.read_ticks)
            else:
                self._modify_array(op, row)

    def _start(self, operation: str, argument, ticks: int) -> None:
        self._pending = (operation, argument)
        self._remaining = ticks

    def tick(self, count: int = 1) -> None:
        if type(count) is not int or count < 0:
            raise FlashError("flash tick count must be a nonnegative integer")
        if not self.busy:
            return
        self._remaining -= count
        if self._remaining > 0:
            return
        operation, argument = self._pending
        self._pending = None
        if operation == "read":
            self.cache[:] = self.storage.read_page(argument)
        elif operation == "program":
            row, data = argument
            self.storage.program_page(row, 0, data)
        elif operation == "erase":
            self.storage.erase_block(argument // self.storage.pages_per_block)
        elif operation == "failure":
            self.failures |= argument
        if operation in ("program", "erase", "failure"):
            self.wel = False

    def _modify_array(self, op: int, row: int) -> None:
        raise NotImplementedError

    def _power_on_features(self) -> None:
        raise NotImplementedError

    def _read_feature(self, address: int) -> int:
        raise NotImplementedError

    def _write_feature(self, address: int, value: int) -> None:
        raise NotImplementedError

    def _allowed_busy(self, op: int) -> bool:
        raise NotImplementedError

    def _can_load(self) -> bool:
        raise NotImplementedError

    def _row(self) -> int:
        raise NotImplementedError

    def _before_read(self, row: int) -> None:
        pass

    def _soft_reset(self) -> None:
        pass

    def _power_on_cache(self) -> None:
        pass
