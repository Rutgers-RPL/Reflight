"""Flatten CMSIS-SVD metadata into immutable, unambiguous register lookups."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cmsis_svd.model import SVDRegisterCluster, SVDRegisterClusterArray
from cmsis_svd.parser import SVDParser


class SVDLoadError(ValueError):
    """An SVD cannot be represented accurately by this index."""


class SVDLookupError(ValueError):
    """A peripheral, register, or address is absent from the index."""


@dataclass(frozen=True)
class FieldInfo:
    name: str
    bit_offset: int
    bit_width: int


@dataclass(frozen=True)
class RegisterInfo:
    peripheral: str
    name: str
    address: int
    offset: int
    size: int | None
    access: str | None
    reset_value: int | None
    fields: tuple[FieldInfo, ...]


@dataclass(frozen=True)
class PeripheralInfo:
    name: str
    base_address: int
    registers: tuple[RegisterInfo, ...]


class RegisterMap:
    """Parsed SVD values only; no emulation or mutable register state."""

    def __init__(self, device_name: str, peripherals: dict[str, PeripheralInfo]) -> None:
        self.device_name = device_name
        self._peripherals = peripherals
        self._addresses: dict[int, RegisterInfo] = {}
        for peripheral in peripherals.values():
            for register in peripheral.registers:
                previous = self._addresses.get(register.address)
                if previous is not None:
                    raise SVDLoadError(
                        f"ambiguous SVD address 0x{register.address:08x}: "
                        f"{previous.peripheral}.{previous.name} and "
                        f"{register.peripheral}.{register.name}"
                    )
                self._addresses[register.address] = register

    @classmethod
    def from_file(cls, path: str | Path) -> RegisterMap:
        source = Path(path).resolve()
        try:
            device = SVDParser.for_xml_file(source).get_device()
        except Exception as exc:
            raise SVDLoadError(f"cannot read SVD {source}: {exc}") from exc
        if not device.name:
            raise SVDLoadError(f"SVD {source} has no device name")

        peripherals: dict[str, PeripheralInfo] = {}
        for item in device.peripherals:
            if not item.name or item.base_address is None:
                raise SVDLoadError("SVD peripheral lacks a name or base address")
            if item.name in peripherals:
                raise SVDLoadError(f"duplicate SVD peripheral {item.name}")
            if any(isinstance(register, (SVDRegisterCluster, SVDRegisterClusterArray))
                   for register in (item.registers or [])):
                raise SVDLoadError(
                    f"unsupported SVD register cluster in {item.name}; "
                    "cluster addresses cannot be indexed safely"
                )
            registers: list[RegisterInfo] = []
            seen: set[str] = set()
            for register in item.get_registers():
                if not register.name or register.address_offset is None:
                    raise SVDLoadError(f"unresolved register in {item.name}")
                if register.name in seen:
                    raise SVDLoadError(f"duplicate/unresolved register {item.name}.{register.name}")
                seen.add(register.name)
                fields: list[FieldInfo] = []
                for field in register.get_fields():
                    if not field.name or field.bit_offset is None or field.bit_width is None:
                        raise SVDLoadError(f"unresolved field in {item.name}.{register.name}")
                    fields.append(FieldInfo(field.name, field.bit_offset, field.bit_width))
                access = register.access
                registers.append(RegisterInfo(
                    item.name, register.name,
                    item.base_address + register.address_offset,
                    register.address_offset, register.size,
                    access.value if hasattr(access, "value") else access,
                    register.reset_value, tuple(fields),
                ))
            peripherals[item.name] = PeripheralInfo(
                item.name, item.base_address, tuple(sorted(registers, key=lambda r: (r.address, r.name)))
            )
        return cls(device.name, peripherals)

    def list_peripherals(self) -> tuple[PeripheralInfo, ...]:
        return tuple(self._peripherals[name] for name in sorted(self._peripherals))

    def get_peripheral(self, name: str) -> PeripheralInfo:
        try:
            return self._peripherals[name]
        except KeyError as exc:
            raise SVDLookupError(f"unknown SVD peripheral {name}") from exc

    def get_register(self, peripheral: str, name: str) -> RegisterInfo:
        for register in self.get_peripheral(peripheral).registers:
            if register.name == name:
                return register
        raise SVDLookupError(f"unknown SVD register {peripheral}.{name}")

    def lookup_address(self, address: int) -> RegisterInfo:
        try:
            return self._addresses[address]
        except KeyError as exc:
            raise SVDLookupError(f"unknown SVD address 0x{address:08x}") from exc
