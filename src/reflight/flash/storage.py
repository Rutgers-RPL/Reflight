"""Sparse physical NAND array for the Phase 6 external flash devices.

Geometry: W25N01KV Rev H §§1, 7.2.4, 8.2.8 (2048+96 bytes), and
GD5F1GQ5xExxH Rev 1.4 §§3–4 (2048+64 bytes). Addresses here are effective
indices; command parsers must consume documented dummy bits separately.
"""

from collections.abc import Iterator


class NandStorageError(ValueError):
    """Invalid physical NAND geometry or array access."""


class NandStorage:
    """Store only non-erased pages; all unspecified bytes read as FF.

    Programming applies bitwise AND, including the spare area. Protection,
    ECC-managed bytes, page-program ordering and partial-program limits are
    chip-model responsibilities, not storage policy. Reads return immutable
    bytes so callers cannot alter the array outside program/erase operations.
    """

    block_count = 1024
    pages_per_block = 64
    main_bytes = 2048
    page_count = block_count * pages_per_block
    main_size = page_count * main_bytes

    def __init__(self, *, spare_bytes: int) -> None:
        if type(spare_bytes) is not int or spare_bytes not in (64, 96):
            raise NandStorageError("spare_bytes must be 64 or 96")
        self.spare_bytes = spare_bytes
        self.page_size = self.main_bytes + spare_bytes
        self._erased_page = b"\xff" * self.page_size
        self._pages: dict[int, bytes] = {}

    @property
    def allocated_pages(self) -> int:
        """Number of non-erased physical pages retained in memory."""
        return len(self._pages)

    @staticmethod
    def _index(value: int, limit: int, name: str) -> None:
        if type(value) is not int or not 0 <= value < limit:
            raise NandStorageError(f"{name} must be an integer in 0..{limit - 1}")

    def _region(self, row: int, column: int, length: int) -> None:
        self._index(row, self.page_count, "row")
        self._index(column, self.page_size, "column")
        if type(length) is not int or not 0 <= length <= self.page_size - column:
            raise NandStorageError("length must be nonnegative and remain within one page")

    def read_page(self, row: int, column: int = 0, length: int | None = None) -> bytes:
        """Read a bounded region, defaulting to the rest of main+spare.

        A column must address an existing byte even for a zero-length read.
        """
        self._index(column, self.page_size, "column")
        if length is None:
            length = self.page_size - column
        self._region(row, column, length)
        page = self._pages.get(row, self._erased_page)
        return page[column:column + length]

    def program_page(self, row: int, column: int, data: bytes) -> None:
        """AND bytes into a page; validate the entire region before mutation.

        Empty data and programming FF over an erased region allocate nothing.
        Bytes outside the region are preserved. No address wraps or truncates.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise NandStorageError("program data must be bytes-like")
        data = bytes(data)
        self._region(row, column, len(data))
        if not data:
            return
        page = bytearray(self._pages.get(row, self._erased_page))
        for offset, value in enumerate(data, column):
            page[offset] &= value
        if page != self._erased_page:
            self._pages[row] = bytes(page)

    def erase_block(self, block: int) -> None:
        """Erase exactly one block, releasing its stored main and spare bytes."""
        self._index(block, self.block_count, "block")
        first_row = block * self.pages_per_block
        for row in range(first_row, first_row + self.pages_per_block):
            self._pages.pop(row, None)

    def iter_main_pages(self) -> Iterator[bytes]:
        """Yield 2048-byte main areas in ascending physical row order.

        Includes implicit FF gaps and excludes spare bytes. Iteration needs at
        most one page of temporary data, never a full 128 MiB image. This reads
        the live array; callers must not mutate storage while exporting it.
        """
        erased_main = self._erased_page[:self.main_bytes]
        for row in range(self.page_count):
            page = self._pages.get(row)
            yield erased_main if page is None else page[:self.main_bytes]
