"""Deterministic terminal layouts over verified canonical attachments only."""

from __future__ import annotations

import json
from typing import Any

from ai.core.presentation.wire import normalize_presentation


def language_literal(value: str) -> str:
    """Serialize a language string, including delimiter-like and Unicode text."""
    return json.dumps(value, ensure_ascii=True)


def build_presentation(canonical: dict[str, Any]) -> dict[str, Any] | None:
    """Build a bounded read-only layout, without a model pass or data reads.

    Canonical prose remains outside the program. References identify existing
    attachments, never labels, URLs, queries or authorization claims. A stage-A
    layout is flat (depth two) and below both graph and envelope budgets.
    """
    if canonical.get("question") or canonical.get("response_state") not in ("complete", "partial"):
        return None
    if not canonical.get("message"):
        return None
    calls = ["AnswerSummary()"]
    references = []

    def add(component: str, kind: str, source_key: str) -> None:
        ref_id = f"r{len(references)}"
        references.append({"ref_id": ref_id, "kind": kind, "source_key": source_key})
        calls.append(f"{component}({language_literal(ref_id)})")

    for entity in (canonical.get("entities") or [])[:12]:
        if not isinstance(entity, dict):
            continue
        model, pk = entity.get("model"), entity.get("pk")
        if model in ("assetmachine", "workorder", "repairpacket") and type(pk) is int and pk > 0:
            add("AssetSummary", "entity", f"entity:{model}:{pk}")
    for media in (canonical.get("media_evidence") or [])[:24]:
        if not isinstance(media, dict):
            continue
        attachment, segment = media.get("attachment_id"), media.get("segment_index")
        if type(attachment) is int and attachment > 0 and (segment is None or type(segment) is int):
            add("EvidenceList", "evidence", f"media:{attachment}:{segment}")
    analysis = canonical.get("evidence_analysis")
    if isinstance(analysis, dict):
        calls.append("CoverageNotice()")
        coverage = analysis.get("coverage")
        if isinstance(coverage, dict) and all(
            type(coverage.get(key)) is int and coverage[key] >= 0
            for key in ("population_count", "returned_count")
        ):
            add("MetricComparison", "metric_dataset", "coverage")
    for dataset in canonical.get("presentation_data") or []:
        if dataset.get("kind") == "trend_dataset":
            add("TrendPreview", "trend_dataset", dataset["source_key"])
    if len(calls) == 1:
        return None  # Plain prose gains nothing from loading the renderer.
    envelope = normalize_presentation({
        "schema_version": 1,
        "format": "openui-lang",
        "library": "aimms-chat@1",
        "program": f"root = AnswerLayout([{', '.join(calls)}])",
        "references": references,
    })
    return envelope.model_dump() if envelope else None


def project_presentation(raw: object, *, shared: bool) -> dict[str, Any] | None:
    """Conservative shared-reader policy: canonical text, no new references.

    The existing readable-thread authorization remains the outer boundary.
    Shared readers use the established canonical projection rather than
    inheriting the owner's optional presentation graph.
    """
    if shared:
        return None
    envelope = normalize_presentation(raw)
    return envelope.model_dump() if envelope else None
