"""Strict version-1 board YAML parsing; wiring is metadata, not emulation."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from reflight.svd import RegisterMap, SVDLoadError, SVDLookupError


class ConfigError(ValueError):
    """Invalid or unsupported board configuration."""


@dataclass(frozen=True)
class DeviceConfig:
    model: str
    cs: str | None = None
    address: int | None = None


@dataclass(frozen=True)
class BusConfig:
    name: str
    kind: str
    devices: tuple[DeviceConfig, ...]


@dataclass(frozen=True)
class TelemetryConfig:
    peripheral: str
    file: str
    handle: str | None = None
    packet_definition: Path | None = None


@dataclass(frozen=True)
class CaptureConfig:
    file: str
    pins: tuple[str, ...]


@dataclass(frozen=True)
class FlashOutputConfig:
    device: str
    file: str


@dataclass(frozen=True)
class BoardConfig:
    path: Path
    model: str
    svd_path: Path
    register_map: RegisterMap
    buses: tuple[BusConfig, ...]
    telemetry: TelemetryConfig
    capture: CaptureConfig
    flash_outputs: tuple[FlashOutputConfig, ...] = ()


def _mapping(value: Any, where: str, required: set[str], allowed: set[str]) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ConfigError(f"{where} must be a mapping with string keys")
    missing = required - value.keys()
    unknown = value.keys() - allowed
    if missing:
        raise ConfigError(f"{where} missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigError(f"{where} unknown keys: {', '.join(sorted(unknown))}")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a nonempty string")
    return value


def _local_file(value: Any, where: str, parent: Path) -> Path:
    name = _string(value, where)
    path = (parent / name).resolve()
    if not path.is_file():
        raise ConfigError(f"{where} file does not exist: {path}")
    return path


def _output_name(value: Any, where: str) -> str:
    name = _string(value, where)
    if (name in (".", "..") or Path(name).name != name or PureWindowsPath(name).is_reserved()
            or "/" in name or "\\" in name or ":" in name):
        raise ConfigError(f"{where} must be a filename inside --output-dir")
    return name


def _pin(value: Any, where: str, index: RegisterMap) -> str:
    pin = _string(value, where)
    match = re.fullmatch(r"P([A-I])([0-9]|1[0-5])", pin)
    if match is None:
        raise ConfigError(f"{where} must be a GPIO pin such as PA5")
    try:
        index.get_peripheral("GPIO" + match.group(1))
    except SVDLookupError as exc:
        raise ConfigError(f"{where}: {exc}") from exc
    return pin


def load_board(path: str | Path) -> BoardConfig:
    source = Path(path).resolve()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read board YAML {source}: {exc}") from exc
    root = _mapping(raw, "board", {"schema_version", "mcu", "outputs"},
                    {"schema_version", "mcu", "buses", "outputs"})
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ConfigError("unsupported schema_version; expected 1")
    mcu = _mapping(root["mcu"], "mcu", {"model", "svd"}, {"model", "svd"})
    model = _string(mcu["model"], "mcu.model")
    if model != "STM32F407VG" or importlib.util.find_spec(
        "qiling.extensions.mcu.stm32f4.stm32f407"
    ) is None:
        raise ConfigError(f"unsupported mcu.model {model}; supported: STM32F407VG")
    svd_path = _local_file(mcu["svd"], "mcu.svd", source.parent)
    try:
        index = RegisterMap.from_file(svd_path)
    except SVDLoadError as exc:
        raise ConfigError(str(exc)) from exc

    bus_values = root.get("buses", {})
    if not isinstance(bus_values, dict) or not all(isinstance(k, str) for k in bus_values):
        raise ConfigError("buses must be a mapping with string keys")
    buses: list[BusConfig] = []
    for name, value in sorted(bus_values.items()):
        kind = "spi" if re.fullmatch(r"SPI[1-9][0-9]*", name) else (
            "i2c" if re.fullmatch(r"I2C[1-9][0-9]*", name) else None
        )
        if kind is None:
            raise ConfigError(f"unsupported bus type/name {name}")
        try:
            index.get_peripheral(name)
        except SVDLookupError as exc:
            raise ConfigError(f"buses.{name}: {exc}") from exc
        spec = _mapping(value, f"buses.{name}", {"devices"}, {"devices"})
        devices_raw = spec["devices"]
        if not isinstance(devices_raw, list):
            raise ConfigError(f"buses.{name}.devices must be a list")
        devices: list[DeviceConfig] = []
        used: set[str | int] = set()
        for number, device_raw in enumerate(devices_raw):
            where = f"buses.{name}.devices[{number}]"
            required = {"model", "cs"} if kind == "spi" else {"model", "address"}
            item = _mapping(device_raw, where, required, required)
            device_model = _string(item["model"], where + ".model")
            if kind == "spi":
                cs = _pin(item["cs"], where + ".cs", index)
                if cs in used:
                    raise ConfigError(f"duplicate chip select {cs} on {name}")
                used.add(cs)
                devices.append(DeviceConfig(device_model, cs=cs))
            else:
                address = item["address"]
                if type(address) is not int or not 0x08 <= address <= 0x77:
                    raise ConfigError(f"{where}.address must be a usable 7-bit I2C address (0x08..0x77)")
                if address in used:
                    raise ConfigError(f"duplicate I2C address 0x{address:02x} on {name}")
                used.add(address)
                devices.append(DeviceConfig(device_model, address=address))
        buses.append(BusConfig(name, kind, tuple(devices)))

    outputs = _mapping(root["outputs"], "outputs", {"telemetry", "capture"},
                       {"telemetry", "capture", "flash"})
    telemetry_raw = _mapping(
        outputs["telemetry"], "outputs.telemetry", {"peripheral", "file", "format"},
        {"peripheral", "file", "format", "handle", "packet_definition"},
    )
    peripheral = _string(telemetry_raw["peripheral"], "outputs.telemetry.peripheral")
    if re.fullmatch(r"(?:USART|UART)[1-9][0-9]*", peripheral) is None:
        raise ConfigError(f"outputs.telemetry.peripheral is not a USART/UART: {peripheral}")
    try:
        index.get_peripheral(peripheral)
    except SVDLookupError as exc:
        raise ConfigError(f"outputs.telemetry: {exc}") from exc
    if telemetry_raw["format"] != "raw":
        raise ConfigError("outputs.telemetry.format supports only raw in Phase 2")
    handle = telemetry_raw.get("handle")
    if handle is not None:
        handle = _string(handle, "outputs.telemetry.handle")
    packet = telemetry_raw.get("packet_definition")
    packet_path = (_local_file(packet, "outputs.telemetry.packet_definition", source.parent)
                   if packet is not None else None)
    telemetry = TelemetryConfig(
        peripheral, _output_name(telemetry_raw["file"], "outputs.telemetry.file"),
        handle, packet_path,
    )
    capture_raw = _mapping(outputs["capture"], "outputs.capture", {"file", "sources"},
                           {"file", "sources"})
    sources = capture_raw["sources"]
    if not isinstance(sources, list) or not sources:
        raise ConfigError("outputs.capture.sources must be a nonempty list")
    pins: list[str] = []
    for number, source_raw in enumerate(sources):
        where = f"outputs.capture.sources[{number}]"
        entry = _mapping(source_raw, where, {"type", "pins", "mode"},
                         {"type", "pins", "mode"})
        if entry["type"] != "gpio":
            raise ConfigError(f"{where}.type unsupported: {entry['type']}")
        if entry["mode"] != "transitions":
            raise ConfigError(f"{where}.mode unsupported: {entry['mode']}")
        if not isinstance(entry["pins"], list) or not entry["pins"]:
            raise ConfigError(f"{where}.pins must be a nonempty list")
        for pin_value in entry["pins"]:
            pin = _pin(pin_value, where + ".pins", index)
            if pin in pins:
                raise ConfigError(f"duplicate capture pin {pin}")
            pins.append(pin)
    capture = CaptureConfig(_output_name(capture_raw["file"], "outputs.capture.file"),
                            tuple(pins))
    if telemetry.file == capture.file:
        raise ConfigError("telemetry and capture filenames must differ")
    if telemetry.packet_definition is not None:
        from reflight.outputs.packets import decoded_filename, load_packet_definition
        load_packet_definition(telemetry.packet_definition)
        if decoded_filename(telemetry.file) in (telemetry.file, capture.file):
            raise ConfigError("decoded telemetry and other output filenames must differ")
    flash_raw = outputs.get("flash", [])
    if not isinstance(flash_raw, list):
        raise ConfigError("outputs.flash must be a list")
    from reflight.devices import is_flash_model
    endpoints = {f"{bus.name}.{device.cs}": device.model
                 for bus in buses if bus.kind == "spi" for device in bus.devices}
    filenames = {telemetry.file, capture.file}
    if telemetry.packet_definition is not None:
        filenames.add(decoded_filename(telemetry.file))
    flash_outputs = []
    for number, raw_output in enumerate(flash_raw):
        where = f"outputs.flash[{number}]"
        item = _mapping(raw_output, where, {"device", "file"}, {"device", "file"})
        endpoint = _string(item["device"], where + ".device")
        if endpoint not in endpoints or not is_flash_model(endpoints[endpoint]):
            raise ConfigError(f"{where}.device must reference a configured SPI flash: {endpoint}")
        filename = _output_name(item["file"], where + ".file")
        if filename in filenames:
            raise ConfigError(f"{where}.file collides with another output: {filename}")
        filenames.add(filename)
        flash_outputs.append(FlashOutputConfig(endpoint, filename))
    return BoardConfig(source, model, svd_path, index, tuple(buses), telemetry, capture,
                       tuple(flash_outputs))
