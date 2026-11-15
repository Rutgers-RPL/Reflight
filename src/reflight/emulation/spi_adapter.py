"""Qiling 1.4.6 STM32F407 SPI1 polling adapter.

Qiling's installed STM32F4xxSpi queues DR writes but does not exchange with
external devices or advance SR. This adapter completes that register path.
RM0090, SPI chapter: CR1/CR2, SR TXE/RXNE/BSY and 8-bit DR access.
"""

from __future__ import annotations

from qiling.const import QL_INTERCEPT

from reflight.buses import SpiBus


class SpiAdapter:
    def __init__(self, peripheral: object, bus: SpiBus) -> None:
        self.peripheral = peripheral
        self.bus = bus
        self.failure: Exception | None = None
        peripheral.instance.SR = 0x02  # TXE, no pending receive byte.
        peripheral.hook_write(lambda *args: self._guard(self._before_write, *args),
                              intercept=QL_INTERCEPT.ENTER)
        peripheral.hook_write(lambda *args: self._guard(self._after_write, *args),
                              intercept=QL_INTERCEPT.EXIT)
        peripheral.hook_read(lambda *args: self._guard(self._after_read, *args),
                             intercept=QL_INTERCEPT.EXIT)

    def _guard(self, callback, *args: object) -> None:
        try:
            callback(*args)
        except Exception as exc:
            self.failure = exc
            raise

    def _check_mode(self) -> None:
        cr1 = self.peripheral.instance.CR1
        cr2 = self.peripheral.instance.CR2
        i2s = self.peripheral.instance.I2SCFGR
        # SPE, MSTR, SSM, SSI. CPOL/CPHA=0, DFF=0, BIDIMODE=0,
        # RXONLY=0, CRCEN=0, LSBFIRST=0. CR2 interrupt/DMA/NSS disabled.
        required = (1 << 6) | (1 << 2) | (1 << 9) | (1 << 8)
        forbidden = (1 << 15) | (1 << 13) | (1 << 11) | (1 << 10) | (1 << 7) | 3
        if (cr1 & required) != required or cr1 & forbidden or cr2 or i2s & (1 << 11):
            raise ValueError("unsupported SPI1 mode: require master, 8-bit, mode 0, four-wire, software CS, polling; no DMA/interrupt/NSS/CRC")

    def _before_write(self, _device: object, _access: object, offset: int,
                      size: int, value: int) -> None:
        if offset == 0x0C:
            if size not in (1, 2) or value > 0xFF:
                raise ValueError("SPI1 supports only 8-bit DR transfers")
            self._check_mode()
        elif offset == 0x04 and value:
            raise ValueError("SPI1 CR2 DMA, interrupts, and hardware NSS are unsupported")
        elif offset == 0x1C and value & (1 << 11):
            raise ValueError("SPI1 I2S mode is unsupported")

    def _after_write(self, _device: object, _access: object, offset: int,
                     _size: int, _value: int) -> None:
        if offset != 0x0C:
            return
        sent = self.peripheral.recv(1)
        if len(sent) != 1:
            raise RuntimeError("Qiling SPI1 DR write produced no byte")
        self.peripheral.instance.SR = 0x80  # BSY while the byte is exchanged.
        received = self.bus.exchange_byte(sent[0])
        self.peripheral.send(bytes((received,)))
        self.peripheral.instance.SR = 0x03  # TXE and RXNE; synchronous byte complete.

    def _after_read(self, _device: object, _access: object, offset: int,
                    _size: int, _value: int) -> None:
        if offset == 0x0C:
            self.peripheral.instance.SR &= ~0x01
