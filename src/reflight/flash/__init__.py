"""External NAND storage, separate from sensor inputs and playback.

Chip command models belong here and use the existing buses.spi.SpiDevice
boundary: select(), exchange_byte(), deselect(), and optional tick(count).
Storage itself has no SPI, feature-register, timing, or ECC behavior.
"""

from .storage import NandStorage, NandStorageError
from .spi_nand import FlashError
from .w25n01kv import W25N01KV
from .gd5f1gq5 import GD5F1GQ5UExxH, GD5F1GQ5RExxH

__all__ = ["NandStorage", "NandStorageError", "FlashError", "W25N01KV",
           "GD5F1GQ5UExxH", "GD5F1GQ5RExxH"]
