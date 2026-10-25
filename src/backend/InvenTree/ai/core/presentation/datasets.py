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


def normalize_datasets(raw: Any) -> list[dict[str, Any]]:
    """Fail closed as a whole; never truncate qualifications or observations."""
    if not isinstance(raw, list) or len(raw) > 4:
        return []
    try:
        datasets = [PresentationTrendV1.model_validate(item).model_dump() for item in raw]
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
