"""Separate playback settings keyed by model and bound to board wiring."""

from dataclasses import dataclass
import math
from pathlib import Path

import yaml

from .board import BoardConfig, ConfigError, _mapping, _string
from reflight.sensors import SensorError, make_sensor
from reflight.devices import is_flash_model
from reflight.simulation.csv_data import CSVData


@dataclass(frozen=True)
class SourceConfig:
    kind: str
    csv_path: Path | None = None
    time_column: str | None = None
    columns: dict[str, str] | None = None  # Sensor input key -> CSV header.


@dataclass(frozen=True)
class SensorConfig:
    source: str
    bias: dict[str, float]
    noise: dict[str, float]
    endpoint: str


@dataclass(frozen=True)
class ScenarioConfig:
    path: Path
    duration_s: float | None
    instructions_per_second: float
    random_seed: int
    default_source: str
    sources: dict[str, SourceConfig]
    sensors: dict[str, SensorConfig]
    csv_data: dict[str, CSVData]

    @property
    def endpoint(self) -> tuple[float, str]:
        ends = [(data.end_s, "csv_end") for data in self.csv_data.values()]
        if self.duration_s is not None:
            ends.append((self.duration_s, "duration"))
        return min(ends, key=lambda item: (item[0], item[1] != "duration"))


def _number(value, where: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise ConfigError(f"{where} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ConfigError(f"{where} must be finite") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ConfigError(f"{where} must be finite{' and positive' if positive else ''}")
    return number


def load_scenario(path: str | Path, board: BoardConfig) -> ScenarioConfig:
    path = Path(path).resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read scenario YAML {path}: {exc}") from exc
    root = _mapping(raw, "scenario", {"simulation", "sources", "default_source"},
                    {"simulation", "sources", "default_source", "sensors"})
    sim = _mapping(root["simulation"], "simulation", {"instructions_per_second"},
                   {"instructions_per_second", "duration_s", "random_seed"})
    ips = _number(sim["instructions_per_second"], "instructions_per_second", positive=True)
    duration = (_number(sim["duration_s"], "duration_s", positive=True)
                if "duration_s" in sim else None)
    seed = sim.get("random_seed", 0)
    if type(seed) is not int:
        raise ConfigError("random_seed must be an integer")
    sources_raw = _mapping(root["sources"], "sources", set(), set(root["sources"]) if isinstance(root["sources"], dict) else set())
    sources = {}
    for name, entry in sources_raw.items():
        _string(name, "source name")
        item = _mapping(entry, f"sources.{name}", {"kind"}, {"kind", "csv_path", "time_column", "columns"})
        kind = item["kind"]
        if kind == "default":
            _mapping(item, f"sources.{name}", {"kind"}, {"kind"})
            sources[name] = SourceConfig(kind)
        elif kind == "openrocket":
            _mapping(item, f"sources.{name}", {"kind", "csv_path", "time_column", "columns"}, set(item))
            csv_path = (path.parent / _string(item["csv_path"], "csv_path")).resolve()
            time_column = _string(item["time_column"], "time_column")
            columns = _mapping(item["columns"], "columns", set(), set(item["columns"]) if isinstance(item["columns"], dict) else set())
            if not columns:
                raise ConfigError(f"sources.{name}.columns must not be empty")
            for key, header in columns.items():
                _string(key, "input key")
                _string(header, "CSV header")
            sources[name] = SourceConfig(kind, csv_path, time_column, columns)
        else:
            raise ConfigError(f"sources.{name}: unsupported source kind {kind!r}")
    default = _string(root["default_source"], "default_source")
    if default not in sources:
        raise ConfigError(f"unknown default_source {default}")
    endpoints = {f"{bus.name}.{device.cs if bus.kind == 'spi' else f'0x{device.address:02x}'}": device.model
                 for bus in board.buses for device in bus.devices
                 if not is_flash_model(device.model)}
    models = set()
    for model in endpoints.values():
        if model in models:
            raise ConfigError(f"scenario requires unique sensor models; duplicate model {model} is ambiguous")
        models.add(model)
    overrides = root.get("sensors", {})
    overrides = _mapping(overrides, "sensors", set(), models)
    sensors = {}
    for endpoint, model in sorted(endpoints.items()):
        item = _mapping(overrides.get(model, {}), f"sensors.{model}", set(), {"source", "bias", "noise"})
        source = _string(item.get("source", default), f"{model}.source")
        if source not in sources:
            raise ConfigError(f"{model}: unknown source {source}")
        try:
            sensor = make_sensor(model)
            sensor.validate_input_keys(set(sources[source].columns or {}))
        except SensorError as exc:
            raise ConfigError(f"sensors.{model}: {exc}") from exc
        adjustments = {}
        supported = {key for group in sensor.input_groups for key in group}
        for setting in ("bias", "noise"):
            values = _mapping(item.get(setting, {}), f"{model}.{setting}", set(), supported)
            adjustments[setting] = {key: _number(value, f"{model}.{setting}.{key}") for key, value in values.items()}
            if setting == "noise" and any(value < 0 for value in adjustments[setting].values()):
                raise ConfigError(f"{model}.noise standard deviation must be nonnegative")
        sensors[model] = SensorConfig(source, adjustments["bias"], adjustments["noise"], endpoint)
    active = {sensor.source for sensor in sensors.values()}
    csv_data = {name: CSVData.load(spec.csv_path, spec.time_column, spec.columns)
                for name, spec in sorted(sources.items()) if name in active and spec.kind == "openrocket"}
    if not csv_data and duration is None:
        raise ConfigError("duration_s is required when no active sensor uses CSV")
    return ScenarioConfig(path, duration, ips, seed, default, sources, sensors, csv_data)
