"""Frozen live-inventory references for goldens whose demo data can change.

Capture before sending any evaluation question. The snapshot is independent
of the assistant and replaces mutable inventory counts, stock locations and
BOM quantities; questions, required behavior, tolerance and corpus assertions
stay intact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .schema import GoldenItem

VERSION = "live-inventory-reference-v6"
PART_NAME = "R_10K_0402_1%"
BOM_PART_NAME = "Widget Assembly"


def _indexed_markdown(attachment: Any, indexed_hashes: set[str]) -> dict[str, str]:
    """Accept source text only when stored bytes match an indexed revision."""
    with attachment.attachment.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Supplementary evaluation source exceeds 64 KiB")
    digest = hashlib.sha256(data).hexdigest()
    if digest not in indexed_hashes:
        raise ValueError("Supplementary evaluation source differs from indexed bytes")
    return {"source_sha256": digest, "source_text": data.decode("utf-8")}


def _capture_fixture_sources() -> list[dict[str, Any]]:
    """Resolve immutable fixture hashes to their actual stored names and owners."""
    from aichat.models import AttachmentIngest
    from common.models import Attachment
    from django.db.models import Q
    from tasks.models import WorkOrder

    fixtures = Path(__file__).parent / "golden" / "fixtures"
    sources = []
    for directory in ("attachments", "media"):
        for fixture in sorted((fixtures / directory).glob("eval-hx200*")):
            digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
            ingests = AttachmentIngest.objects.filter(source_sha256=digest)
            corpus = (
                "aimms-attachment-fixtures-v2"
                if directory == "attachments"
                else "aimms-video-fixtures-v2"
                if fixture.suffix == ".mp4"
                else "aimms-media-fixtures-v2"
            )
            for attachment in Attachment.objects.filter(
                pk__in=ingests.values("attachment_id")
            ).order_by("pk"):
                owner_reference = None
                if attachment.model_type == "workorder":
                    owner_reference = (
                        WorkOrder.objects
                        .filter(pk=attachment.model_id)
                        .values_list("reference", flat=True)
                        .first()
                    )
                sources.append({
                    "corpus_version": corpus,
                    "canonical_filename": fixture.name,
                    "source_sha256": digest,
                    "attachment_id": attachment.pk,
                    "stored_filename": Path(attachment.attachment.name).name,
                    "owner_type": attachment.model_type,
                    "owner_id": attachment.model_id,
                    "owner_reference": owner_reference,
                })
    # Questions refer to uploaded documents on the fixture entities, which
    # can legitimately include non-canonical uploads. Freeze those independently
    # before evaluation; never infer their existence or contents from an answer.
    owners = {(source["owner_type"], source["owner_id"]) for source in sources}
    canonical_ids = {source["attachment_id"] for source in sources}
    owner_filter = Q()
    for owner_type, owner_id in sorted(owners):
        owner_filter |= Q(model_type=owner_type, model_id=owner_id)
    if owners:
        for attachment in (
            Attachment.objects.filter(owner_filter).exclude(pk__in=canonical_ids).order_by("pk")
        ):
            filename = Path(attachment.attachment.name).name
            if not filename.lower().endswith(".md"):
                continue
            hashes = set(
                AttachmentIngest.objects.filter(
                    attachment_id=attachment.pk, state="indexed"
                ).values_list("source_sha256", flat=True)
            )
            if not hashes:
                continue
            sources.append({
                "corpus_version": "aimms-attachment-fixtures-v2",
                "supplementary_upload": True,
                "attachment_id": attachment.pk,
                "stored_filename": filename,
                "owner_type": attachment.model_type,
                "owner_id": attachment.model_id,
                **_indexed_markdown(attachment, hashes),
            })
    return sources


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
            .values("location__pathstring")
            .annotate(quantity=Sum("quantity"))
            .order_by("location__pathstring")
        )
        assembly = Part.objects.get(name=BOM_PART_NAME, active=True)
        bom_items = list(assembly.get_bom_items().order_by("pk"))
        # Stock API's part filter includes descendant variants by default.
        # Capture that same scope independently, including every stock row.
        component_stock = {
            row.sub_part_id: StockItem.objects.filter(
                part__in=row.sub_part.get_descendants(include_self=True)
            ).aggregate(quantity=Sum("quantity"))["quantity"]
            or Decimal(0)
            for row in bom_items
        }
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
            "corpus_sources": _capture_fixture_sources(),
            "stock_by_location": [
                {
                    "location": row["location__pathstring"] or "No location",
                    "quantity": str(row["quantity"]),
                }
                for row in locations
            ],
            "bom": {
                "part_name": BOM_PART_NAME,
                "part_id": assembly.pk,
                "ipn": assembly.IPN or "",
                "is_template": assembly.is_template,
                "variant_of_id": assembly.variant_of_id,
                "variants": list(
                    assembly.get_descendants(include_self=False).values(
                        "pk", "name", "IPN", "variant_of_id"
                    )
                ),
                "items": [
                    {
                        "part_id": row.sub_part_id,
                        "name": row.sub_part.name,
                        "ipn": row.sub_part.IPN or "",
                        "reference": row.reference,
                        "defined_on_part_id": row.part_id,
                        "inherited_by_variants": row.inherited,
                        "allow_variants": row.allow_variants,
                        "consumable": row.consumable,
                        "validated": row.validated,
                        "quantity": str(row.quantity),
                        "optional": row.optional,
                        "stock_quantity": str(component_stock.get(row.sub_part_id, 0)),
                    }
                    for row in bom_items
                ],
            },
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
    contexts = {}
    if "bom" in reference:
        bom = reference["bom"]
        if (
            bom.get("part_name") != BOM_PART_NAME
            or not isinstance(bom.get("items"), list)
            or not bom["items"]
        ):
            raise ValueError("Invalid BOM reference")
        lines = []
        build_limits = []
        for row in bom["items"]:
            if (
                not isinstance(row.get("name"), str)
                or not row["name"].strip()
                or type(row.get("optional")) is not bool
            ):
                raise ValueError("Invalid BOM line")
            quantity = Decimal(str(row.get("quantity")))
            stock = Decimal(str(row.get("stock_quantity")))
            if any(not value.is_finite() or value < 0 for value in (quantity, stock)):
                raise ValueError("Invalid BOM quantity")
            lines.append(row["name"])
            if quantity > 0 and not row["optional"]:
                build_limits.append(int(stock / quantity))
        truths["bom-widget-assembly"] = (
            f"The frozen live BOM for {BOM_PART_NAME} has {len(lines)} lines: "
            + "; ".join(lines)
            + "."
        )
        # The original golden asks for the BOM's seven components. Additional
        # verified tool facts must not silently become new answer requirements.
        contexts["bom-widget-assembly"] = json.dumps(
            {
                **bom,
                "arithmetic_buildable_quantity": min(build_limits) if build_limits else None,
                "stock_scope": "all stock rows including descendant variants",
                "field_definitions": {
                    "inherited_by_variants": "BOM line applies to descendant assembly variants",
                    "allow_variants": "a variant of the component may substitute for it; independent of line inheritance",
                },
                "build_limit_meaning": "component-stock arithmetic, not approval, production validation or reservation checks",
            },
            sort_keys=True,
        )
    updated_items = []
    for item in items:
        updated = replace(
            item,
            ground_truth=truths.get(item.id, item.ground_truth),
            reference_context=contexts.get(item.id, item.reference_context),
        )
        sources = [
            source
            for source in reference.get("corpus_sources", [])
            if source["corpus_version"] in item.corpus_pins
        ]
        if sources:
            updated = replace(
                updated,
                reference_context=updated.reference_context
                + "\nStored source identities:\n"
                + "Field definitions: owner_type is the domain model of the record "
                "to which the attachment is attached. owner_id is that record's "
                "database primary key in owner_type; owner_reference is the "
                "human-readable reference of that same record. For owner_type "
                "workorder, owner_id is the work order ID. attachment_id is the "
                "separate attachment record ID.\n" + json.dumps(sources, sort_keys=True),
            )
        updated_items.append(updated)
    return updated_items


def reference_digest(reference: dict[str, Any]) -> str:
    """Stable digest retained beside the report and original snapshot."""
    return hashlib.sha256(json.dumps(reference, sort_keys=True).encode()).hexdigest()
