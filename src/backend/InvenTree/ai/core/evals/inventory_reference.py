"""Frozen live-inventory references for goldens whose demo data can change.

Capture before sending any evaluation question. The snapshot is independent
of the assistant and replaces only mutable counts and stock locations; the
questions, required behavior, tolerance and corpus assertions stay intact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .schema import GoldenItem

VERSION = "live-inventory-reference-v1"
PART_NAME = "R_10K_0402_1%"


def capture_reference() -> dict[str, Any]:
    """Read the database independently of model/tool outputs, without writes."""
    from django.db import transaction
    from django.db.models import Q, Sum
    from django.utils import timezone
    from part.models import Part
    from stock.models import StockItem

    with transaction.atomic():
        part = Part.objects.get(name=PART_NAME, active=True)
        locations = list(
            StockItem.objects
            .filter(part=part)
            .values("location__name")
            .annotate(quantity=Sum("quantity"))
            .order_by("location__name")
        )
        return {
            "version": VERSION,
            "captured_at": timezone.now().isoformat(),
            "part_count": Part.objects.count(),
            "zero_stock_count": Part.objects
            .annotate(total=Sum("stock_items__quantity"))
            .filter(Q(total__isnull=True) | Q(total=0))
            .count(),
            "stock_part": PART_NAME,
            "stock_part_id": part.pk,
            "stock_by_location": [
                {
                    "location": row["location__name"] or "No location",
                    "quantity": str(row["quantity"]),
                }
                for row in locations
            ],
        }


def apply_reference(items: list[GoldenItem], reference: dict[str, Any]) -> list[GoldenItem]:
    """Apply a validated, pre-run snapshot; never consume an evaluated answer."""
    if reference.get("version") != VERSION or reference.get("stock_part") != PART_NAME:
        raise ValueError("Unsupported inventory reference or stock fixture")
    for key in ("part_count", "zero_stock_count"):
        if type(reference.get(key)) is not int or reference[key] < 0:
            raise ValueError(f"Invalid inventory count: {key}")
    if reference["zero_stock_count"] > reference["part_count"]:
        raise ValueError("Zero-stock count exceeds total parts")
    locations = reference.get("stock_by_location")
    if not isinstance(locations, list) or not locations:
        raise ValueError("Stock reference needs its location breakdown")
    quantities = []
    labels = set()
    for row in locations:
        label = row.get("location")
        if not isinstance(label, str) or not label.strip() or label in labels:
            raise ValueError("Invalid or duplicate stock location")
        labels.add(label)
        quantity = Decimal(str(row.get("quantity")))
        if not quantity.is_finite() or quantity < 0:
            raise ValueError("Invalid stock quantity")
        quantities.append(quantity)
    total = sum(quantities, Decimal(0))
    breakdown = "; ".join(
        f"{row['location']}: {quantity} units"
        for row, quantity in zip(locations, quantities, strict=True)
    )
    truths = {
        "part-count-total": f"The frozen live database contains {reference['part_count']} parts.",
        "locale-de-stock": (
            f"Answer in German: the frozen live database contains {reference['part_count']} parts."
        ),
        "zero-stock-count": (
            f"The frozen live database contains {reference['zero_stock_count']} parts "
            "whose summed stock quantity is zero, including parts without stock rows."
        ),
        "stock-r10k-0402": f"{total} units in total for {PART_NAME}. Locations: {breakdown}.",
        "stock-breakdown-location": (
            f"Per-location stock for {PART_NAME}: {breakdown}. Total: {total} units."
        ),
    }
    return [
        replace(item, ground_truth=truths[item.id]) if item.id in truths else item for item in items
    ]


def reference_digest(reference: dict[str, Any]) -> str:
    """Stable digest retained beside the report and original snapshot."""
    return hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()
