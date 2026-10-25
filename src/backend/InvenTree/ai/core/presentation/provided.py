"""Present explicit supplied values without a model, queries or code execution."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from ai.core.presentation.datasets import PresentationChartV2

_PAIR = re.compile(
    r"(?:^|[,;:]|\b(?:for|comparing|and)\s+)\s*"
    r"([\w][\w ./()-]{0,79}?)\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b",
    re.I,
)


def provided_visualization(text: str, *, turn_id: str) -> tuple[str, list[dict]]:
    """Parse bounded label=value data; never infer or silently omit an input."""
    from ai.core.config import get_settings
    from ai.core.presentation.library import library_available

    if not get_settings().feature_chat_presentation or not library_available():
        return "Chart display is currently disabled. No graphical visualization was generated.", []
    kind = re.search(r"\b(line|bar|pie)\s+(?:chart|graph|plot)\b", text, re.I)
    if kind is None:
        return (
            "Supported supplied-data charts are line, bar and pie. Specify one chart type and label=value pairs.",
            [],
        )
    matches = list(_PAIR.finditer(text))
    if not matches or len(matches) > 64 or text.count("=") != len(matches):
        return (
            "No chart was generated. Supply 1-64 unambiguous label=value pairs, for example Jan=4, Feb=0. Values are never inferred.",
            [],
        )
    axes = re.search(
        r"\baxes\s+([\w ]{1,64}?)\s+and\s+([\w ]{1,64}?)(?:\s+and\s+show|[.;]|$)", text, re.I
    )
    try:
        points = []
        for index, match in enumerate(matches):
            value = float(match[2])
            if not Decimal(match[2]).is_finite() or Decimal(str(value)) != Decimal(match[2]):
                raise ValueError("value cannot be preserved at browser numeric precision")
            label = match[1].strip()
            if index == 0 and re.search(
                r"\b(?:line|bar|pie)\s+(?:chart|graph|plot)\b", label, re.I
            ):
                declaration = re.search(
                    r"\b(?:line|bar|pie)\s+(?:chart|graph|plot)\s+(?:for|comparing)\s+(.+)$",
                    label,
                    re.I,
                )
                if declaration:
                    label = declaration[1].strip()
            points.append({"label": label, "value": int(value) if value.is_integer() else value})
        dataset = PresentationChartV2.model_validate({
            "kind": "chart_dataset",
            "source_key": "provided:series",
            "origin": "user_supplied",
            "chart_type": kind[1].lower(),
            "title": "Synthetic test data",
            "unit": "value",
            "x_label": axes[1].strip() if axes else "Category",
            "y_label": axes[2].strip() if axes else "Value",
            "source": "user_supplied",
            "source_revision": turn_id,
            "as_of": datetime.now(UTC).isoformat(),
            "scope_label": "Values supplied in this message",
            "definition": "Synthetic / user-provided values; not verified operational records.",
            "population_count": None,
            "complete_population": None,
            "points": points,
        })
    except (ValueError, OverflowError):
        return (
            "No chart was generated. Labels must be unique and values finite and bounded. Pie values must be nonnegative with a positive total.",
            [],
        )
    values = "; ".join(f"{point.label}: {point.value}" for point in dataset.points)
    return (
        f"Synthetic test data — {dataset.chart_type} chart. {values}. These are user-provided values, not recorded operational measurements.",
        [dataset.model_dump()],
    )
