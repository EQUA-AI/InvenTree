"""Bounded historical series from the authorized, deterministic analysis rail."""

from __future__ import annotations

import json
import math
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PresentationSampleV1(BaseModel):
    """A missing/uncertain observation is never converted to zero."""

    model_config = ConfigDict(extra="forbid", strict=True)
    observed_at: str = Field(max_length=64)
    value: float | int | None
    quality: Literal["good", "uncertain", "bad", "missing"]

    @field_validator("value")
    @classmethod
    def finite_value(cls, value):
        if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError("invalid observation")
        return value

    @field_validator("observed_at")
    @classmethod
    def valid_time(cls, value):
        datetime.fromisoformat(value)
        return value


class PresentationTrendV1(BaseModel):
    """A server snapshot, never a query or live-data pointer."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source_key: str = Field(min_length=1, max_length=256)
    kind: Literal["trend_dataset"] = "trend_dataset"
    unit: str = Field(min_length=1, max_length=32)
    source: str = Field(min_length=1, max_length=80)
    source_revision: str = Field(min_length=1, max_length=128)
    as_of: str = Field(min_length=1, max_length=64)
    timezone: str = Field(min_length=1, max_length=80)
    samples: list[PresentationSampleV1] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def ordered_samples(self):
        times = [datetime.fromisoformat(sample.observed_at) for sample in self.samples]
        if any(right <= left for left, right in pairwise(times)):
            raise ValueError("observations must be strictly ordered")
        datetime.fromisoformat(self.as_of)
        return self


class PresentationChartPointV2(BaseModel):
    """An explicit category value; null is a gap, never zero."""

    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(min_length=1, max_length=80)
    value: float | int | None

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value):
        if value is not None and (
            isinstance(value, bool) or not math.isfinite(value) or abs(value) > 1e12
        ):
            raise ValueError("invalid chart value")
        return value

    @field_validator("label")
    @classmethod
    def nonempty_label(cls, value):
        if not value.strip():
            raise ValueError("empty category")
        return value


class PresentationChartV2(BaseModel):
    """A bounded chart snapshot with explicit recorded/provided provenance."""

    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["chart_dataset"] = "chart_dataset"
    source_key: str = Field(min_length=1, max_length=256)
    origin: Literal["recorded", "user_supplied"]
    chart_type: Literal["line", "bar", "pie"]
    title: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=32)
    x_label: str = Field(min_length=1, max_length=64)
    y_label: str = Field(min_length=1, max_length=64)
    source: Literal["work_orders", "user_supplied"]
    source_revision: str = Field(min_length=1, max_length=128)
    as_of: str = Field(min_length=1, max_length=64)
    scope_label: str = Field(min_length=1, max_length=512)
    definition: str = Field(min_length=1, max_length=512)
    population_count: int | None
    complete_population: bool | None
    points: list[PresentationChartPointV2] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def verified_shape(self):
        datetime.fromisoformat(self.as_of)
        if len({point.label for point in self.points}) != len(self.points):
            raise ValueError("duplicate categories")
        if self.origin == "recorded":
            if (
                self.source != "work_orders"
                or self.complete_population is not True
                or type(self.population_count) is not int
                or self.population_count < 0
                or any(type(point.value) is not int or point.value < 0 for point in self.points)
                or sum(point.value for point in self.points) != self.population_count
            ):
                raise ValueError("recorded chart requires a complete reconciled population")
        elif (
            self.source != "user_supplied"
            or self.population_count is not None
            or self.complete_population is not None
        ):
            raise ValueError("provided values cannot claim recorded coverage")
        if self.chart_type == "pie" and (
            any(point.value is None or point.value < 0 for point in self.points)
            or sum(point.value for point in self.points) <= 0
        ):
            raise ValueError("pie values must be nonnegative with a positive total")
        return self


def normalize_datasets(raw: Any) -> list[dict[str, Any]]:
    """Fail closed as a whole; never truncate qualifications or observations."""
    if not isinstance(raw, list) or len(raw) > 4:
        return []
    try:
        datasets = [
            (
                PresentationChartV2
                if isinstance(item, dict) and item.get("kind") == "chart_dataset"
                else PresentationTrendV1
            )
            .model_validate(item)
            .model_dump()
            for item in raw
        ]
        if (
            len({item["source_key"] for item in datasets}) != len(datasets)
            or len(json.dumps(datasets).encode()) > 65536
        ):
            return []
        return datasets
    except Exception:
        return []


def trend_candidate(
    result: dict[str, Any], *, as_of: str, source_revision: str
) -> list[dict[str, Any]]:
    """Copy exact gateway buckets; its complete-population zero-fill is explicit."""
    if not result.get("available") or result.get("complete_population") is not True:
        return []
    return normalize_datasets([
        {
            "source_key": "analysis:timeline",
            "kind": "trend_dataset",
            "unit": "records",
            "source": str(result.get("population_type") or ""),
            "source_revision": source_revision,
            "as_of": as_of,
            "timezone": str(result.get("timezone") or ""),
            "samples": [
                {
                    "observed_at": row.get("bucket"),
                    "value": row.get("group_count"),
                    "quality": "good",
                }
                for row in result.get("buckets", [])
            ],
        }
    ])
