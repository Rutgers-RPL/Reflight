"""Strict mapped CSV data, normalized to the first timestamp; no sensor semantics."""

from bisect import bisect_left
import csv
from dataclasses import dataclass
from fractions import Fraction
import math
from pathlib import Path

from reflight.config.board import ConfigError


@dataclass(frozen=True)
class CSVData:
    times: tuple[float, ...]
    rows: tuple[dict[str, float], ...]

    @property
    def end_s(self) -> float:
        return self.times[-1]

    @classmethod
    def load(cls, path: Path, time_column: str, columns: dict[str, str]) -> "CSVData":
        """Load columns mapped from input keys to exact CSV header names."""
        times, rows = [], []
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.reader(stream, strict=True)
                headers = next(reader, None)
                if not headers or any(not h.strip() for h in headers):
                    raise ConfigError(f"{path}: missing CSV headers")
                if len(set(headers)) != len(headers):
                    raise ConfigError(f"{path}: duplicate CSV headers")
                for name in (time_column, *columns.values()):
                    if name not in headers:
                        raise ConfigError(f"{path}: missing column {name!r}")
                for fields in reader:
                    where = f"{path}: row {reader.line_num}"
                    if len(fields) != len(headers):
                        raise ConfigError(f"{where}: expected {len(headers)} columns, got {len(fields)}")
                    record = dict(zip(headers, fields))
                    values = {}
                    for name in (time_column, *columns.values()):
                        try:
                            number = float(record[name])
                        except ValueError as exc:
                            raise ConfigError(f"{where}, column {name!r}: expected numeric value") from exc
                        if not math.isfinite(number):
                            raise ConfigError(f"{where}, column {name!r}: expected finite value")
                        values[name] = number
                    time = Fraction(record[time_column])
                    if times and time <= times[-1]:
                        raise ConfigError(f"{where}, column {time_column!r}: timestamps must strictly increase")
                    times.append(time)
                    rows.append({key: values[header] for key, header in columns.items()})
        except csv.Error as exc:
            raise ConfigError(f"{path}: row {reader.line_num}: malformed CSV: {exc}") from exc
        except (OSError, UnicodeError) as exc:
            raise ConfigError(f"cannot read CSV {path}: {exc}") from exc
        if not rows:
            raise ConfigError(f"{path}: CSV has no data rows")
        try:
            normalized = tuple(float(t - times[0]) for t in times)
        except OverflowError as exc:
            raise ConfigError(f"{path}: normalized timestamps must be finite") from exc
        if any(not math.isfinite(t) for t in normalized):
            raise ConfigError(f"{path}: normalized timestamps must be finite")
        if any(right <= left for left, right in zip(normalized, normalized[1:])):
            raise ConfigError(f"{path}: normalized timestamps must strictly increase at playback precision")
        return cls(normalized, tuple(rows))

    def values_at(self, time_s: float) -> dict[str, float]:
        if not math.isfinite(time_s) or time_s < 0 or time_s > self.end_s:
            raise ValueError(f"CSV time {time_s} outside [0, {self.end_s}]")
        right = bisect_left(self.times, time_s)
        if self.times[right] == time_s:
            return self.rows[right].copy()
        left = right - 1
        fraction = (time_s - self.times[left]) / (self.times[right] - self.times[left])
        return {key: (1 - fraction) * value + fraction * self.rows[right][key]
                for key, value in self.rows[left].items()}
