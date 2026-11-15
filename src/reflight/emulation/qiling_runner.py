"""Bounded STM32F407 execution with USART2 and GPIOA observation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING

from qiling import Qiling
from qiling.const import QL_ARCH, QL_INTERCEPT, QL_OS, QL_VERBOSE
from qiling.extensions.mcu.stm32f4.stm32f407 import stm32f407

if TYPE_CHECKING:
    from reflight.config import BoardConfig
    from reflight.buses import SpiBus


_USART_CLOCKS = {
    "USART1": ("APB2ENR", 4), "USART2": ("APB1ENR", 17),
    "USART3": ("APB1ENR", 18), "UART4": ("APB1ENR", 19),
    "UART5": ("APB1ENR", 20), "USART6": ("APB2ENR", 5),
}


@dataclass(frozen=True)
class RegisterWrite:
    peripheral: str
    address: int
    size: int
    value: int
    pc: int


@dataclass(frozen=True)
class UARTByteEvent:
    value: int
    pc: int
    instruction_count: int


@dataclass(frozen=True)
class GPIOTransition:
    level: int
    pc: int
    instruction_count: int
    pin: str = "PA5"


@dataclass(frozen=True)
class ExecutionTrace:
    first_pcs: tuple[int, ...]
    instruction_count: int
    writes: tuple[RegisterWrite, ...]
    uart_events: tuple[UARTByteEvent, ...]
    gpio_output_baseline: int | None
    gpio_transitions: tuple[GPIOTransition, ...]

    @property
    def uart_bytes(self) -> bytes:
        return bytes(event.value for event in self.uart_events)


class _GPIOOutputObserver:
    """Track one output pin's driven level after Qiling updates GPIO registers."""

    def __init__(self, pin: int, port: str = "A") -> None:
        self.pin = pin
        self.pin_name = f"P{port}{pin}"
        self.output_baseline: int | None = None
        self.current_level: int | None = None
        self.transitions: list[GPIOTransition] = []

    def observe(self, moder: int, odr: int, *, pc: int,
                instruction_count: int) -> None:
        if ((moder >> (2 * self.pin)) & 0b11) != 0b01:
            self.current_level = None
            return

        level = (odr >> self.pin) & 1
        if self.current_level is None:
            if self.output_baseline is None:
                self.output_baseline = level
        elif level != self.current_level:
            self.transitions.append(GPIOTransition(level, pc, instruction_count,
                                                   self.pin_name))
        self.current_level = level


def validate_board_runtime(board: BoardConfig) -> None:
    """Require selected SVD addresses to match the executable Qiling target."""
    if board.model != "STM32F407VG":
        raise ValueError(f"unsupported executable MCU {board.model}")
    requirements = {
        board.telemetry.peripheral: {"SR": 0x00, "DR": 0x04,
                                     "BRR": 0x08, "CR1": 0x0C},
        "RCC": {"AHB1ENR": 0x30, "APB1ENR": 0x40, "APB2ENR": 0x44},
    }
    for pin in board.capture.pins:
        requirements["GPIO" + pin[1]] = {"MODER": 0x00, "ODR": 0x14,
                                          "BSRR": 0x18}
    for bus in board.buses:
        if bus.kind == "i2c" and bus.devices:
            raise ValueError(f"{bus.name} runtime controller transactions are not verified in Qiling 1.4.6")
        if bus.kind == "spi" and bus.devices:
            if bus.name != "SPI1":
                raise ValueError(f"unsupported runtime SPI controller {bus.name}; tested: SPI1")
            requirements["SPI1"] = {"CR1": 0x00, "CR2": 0x04,
                                    "SR": 0x08, "DR": 0x0C}
            for device in bus.devices:
                assert device.cs is not None
                requirements["GPIO" + device.cs[1]] = {
                    "MODER": 0x00, "ODR": 0x14, "BSRR": 0x18}
    for name, registers in requirements.items():
        profile = stm32f407.get(name)
        expected_type = ("STM32F4xxUsart" if name == board.telemetry.peripheral
                         else "STM32F4xxRccV2" if name == "RCC"
                         else "STM32F4xxSpi" if name == "SPI1"
                         else "STM32F4xxGpio")
        if profile is None or profile.get("struct") != expected_type:
            raise ValueError(f"Qiling cannot observe configured peripheral {name}")
        actual_base = board.register_map.get_peripheral(name).base_address
        if actual_base != profile["base"]:
            raise ValueError(f"SVD/Qiling mismatch for {name} base address: "
                             f"0x{actual_base:08x} != 0x{profile['base']:08x}")
        for register, offset in registers.items():
            actual = board.register_map.get_register(name, register).address
            expected = profile["base"] + offset
            if actual != expected:
                raise ValueError(f"SVD/Qiling mismatch for {name}.{register}: "
                                 f"0x{actual:08x} != 0x{expected:08x}")


def run_firmware(
    firmware: str | Path, max_instructions: int = 1000, *,
    uart_peripheral: str = "USART2", gpio_pins: tuple[str, ...] = ("PA5",),
    spi_bus: SpiBus | None = None,
) -> ExecutionTrace:
    """Run a Flash-linked STM32F407 ELF, stopping after a fixed instruction budget.

    UART bytes come from the selected Qiling USART TX queue after firmware DR writes.
    GPIO transitions reflect selected pins' effective output level after writes.
    """
    if max_instructions <= 0:
        raise ValueError("max_instructions must be positive")
    if uart_peripheral not in _USART_CLOCKS or \
            stm32f407.get(uart_peripheral, {}).get("struct") != "STM32F4xxUsart":
        raise ValueError(f"unsupported Qiling USART/UART {uart_peripheral}")
    observers: dict[str, _GPIOOutputObserver] = {}
    gpio_ports: dict[str, list[_GPIOOutputObserver]] = {}
    tracked_pins = tuple(dict.fromkeys((*gpio_pins, *(spi_bus.devices if spi_bus else ()))))
    for pin_name in tracked_pins:
        match = re.fullmatch(r"P([A-I])([0-9]|1[0-5])", pin_name)
        if match is None or pin_name in observers:
            raise ValueError(f"invalid or duplicate GPIO pin {pin_name}")
        port = "GPIO" + match.group(1)
        if stm32f407.get(port, {}).get("struct") != "STM32F4xxGpio":
            raise ValueError(f"unsupported Qiling GPIO port {port}")
        observer = _GPIOOutputObserver(int(match.group(2)), match.group(1))
        observers[pin_name] = observer
        gpio_ports.setdefault(port, []).append(observer)

    firmware_path = Path(firmware).resolve(strict=True)
    ql = Qiling(
        [str(firmware_path)],
        rootfs=".",
        archtype=QL_ARCH.CORTEX_M,
        ostype=QL_OS.MCU,
        env=stm32f407.copy(),
        verbose=QL_VERBOSE.OFF,
    )
    if ql.loader.filetype != "elf":
        raise ValueError("STM32F407 runner requires an ELF image")
    first_pcs: list[int] = []
    writes: list[RegisterWrite] = []
    uart_events: list[UARTByteEvent] = []
    instruction_count = 0

    def on_instruction(_ql: Qiling, address: int, _size: int) -> None:
        nonlocal instruction_count
        instruction_count += 1
        if spi_bus is not None:
            spi_bus.tick()
        if len(first_pcs) < 8:
            first_pcs.append(address)

    ql.hook_code(on_instruction)

    for name in dict.fromkeys(("rcc", uart_peripheral.lower(),
                               *(port.lower() for port in gpio_ports),
                               *((spi_bus.name.lower(),) if spi_bus else ()))):
        peripheral = ql.hw.create(name)
        if peripheral is None:
            raise RuntimeError(f"Qiling profile could not create {name}")

        def on_write(device: object, _access: object, offset: int,
                     size: int, value: int, *, label: str = name) -> None:
            writes.append(RegisterWrite(
                peripheral=label.upper(),
                address=ql.env[label.upper()]["base"] + offset,
                size=size,
                value=value,
                pc=ql.arch.regs.pc,
            ))

        peripheral.hook_write(on_write)

    rcc = ql.hw.rcc
    usart = getattr(ql.hw, uart_peripheral.lower())
    spi_adapter = None
    if spi_bus is not None:
        from reflight.emulation.spi_adapter import SpiAdapter
        spi_adapter = SpiAdapter(getattr(ql.hw, spi_bus.name.lower()), spi_bus)

    def on_usart_write(_device: object, _access: object, offset: int,
                       _size: int, _value: int) -> None:
        if offset != 0x04:  # DR
            return
        data = usart.recv(1)
        if len(data) != 1:
            raise RuntimeError(f"{uart_peripheral} DR write produced no TX byte")
        clock_register, clock_bit = _USART_CLOCKS[uart_peripheral]
        if (getattr(rcc.instance, clock_register) & (1 << clock_bit)) and \
                (usart.instance.CR1 & 0x2008) == 0x2008:
            uart_events.append(UARTByteEvent(data[0], ql.arch.regs.pc,
                                             instruction_count))

    def on_gpio_write(device: object, _access: object, offset: int,
                      _size: int, _value: int, *, port: str) -> None:
        port_bit = ord(port[-1]) - ord("A")
        if offset in (0x00, 0x14, 0x18) and (rcc.instance.AHB1ENR & (1 << port_bit)):
            for observer in gpio_ports[port]:
                observer.observe(device.instance.MODER, device.instance.ODR,
                                 pc=ql.arch.regs.pc,
                                 instruction_count=instruction_count)
                if spi_bus is not None and observer.pin_name in spi_bus.devices \
                        and observer.current_level is not None:
                    spi_bus.set_cs(observer.pin_name, observer.current_level)

    usart.hook_write(on_usart_write, intercept=QL_INTERCEPT.EXIT)
    for port in gpio_ports:
        peripheral = getattr(ql.hw, port.lower())
        peripheral.hook_write(
            lambda device, access, offset, size, value, *, label=port:
                on_gpio_write(device, access, offset, size, value, port=label),
            intercept=QL_INTERCEPT.EXIT,
        )

    ql.run(count=max_instructions)
    if spi_adapter is not None and spi_adapter.failure is not None:
        raise RuntimeError(f"SPI1 transfer failed: {spi_adapter.failure}") from spi_adapter.failure
    transitions = sorted(
        (event for pin in gpio_pins for event in observers[pin].transitions),
        key=lambda event: (event.instruction_count, gpio_pins.index(event.pin)),
    )
    return ExecutionTrace(tuple(first_pcs), instruction_count, tuple(writes),
                          tuple(uart_events),
                          observers["PA5"].output_baseline if "PA5" in observers else None,
                          tuple(transitions))
