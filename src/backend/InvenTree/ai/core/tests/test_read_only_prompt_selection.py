"""Read-only qualification phrases must not select the write specialist."""

import pytest
from ai.core.tools.capabilities import (
    ToolEffect,
    _write_intent,
    capability_catalog,
    select_capabilities,
)


@pytest.mark.parametrize(
    "query",
    [
        'List machines at "Cedar Creek Houston Regional Site" and their parts; '
        "do not substitute machines from another site and do not create or modify anything.",
        "Reply with READY only. Do not query, create, modify, approve, or send any business records.",
        "Search the parts catalog for bearing. Do not create or change any records.",
        "How many active machines are recorded? Do not change any records.",
        "Look up part ID 954 without changing it.",
        "Show the monthly trend of recorded work orders over the last 12 months. "
        "Use only saved records; do not change anything.",
        "Search parts. Don't ever create, update, or delete records.",
        "List machines without creating or modifying records.",
        "Never send, email, or approve anything. Show stock levels.",
        "Avoid changing records. Show purchase orders.",
        "Do not place an order. List stock.",
        "Search bearing parts. Do not create any records or modify anything.",
    ],
)
def test_prohibitions_keep_read_only_selection(query):
    assert _write_intent(query.lower()) is None
    selection = select_capabilities(query, authenticated=True)
    assert not selection.requires_specialist
    assert all(
        entry.effect is ToolEffect.READ
        for entry in capability_catalog()
        if entry.tool_id in selection.tool_ids
    )


@pytest.mark.parametrize(
    "query",
    [
        "Do not change records. Create a part.",
        "Do not create parts, but approve PO-100.",
        "Do not create parts; send the report.",
        "Without changing the part, archive card 12.",
        "Do not forget to create a part.",
        "Do not hesitate to send the email.",
        "Do not only create parts; approve PO-100 too.",
        "Never fail to update stock.",
        "Don't delete anything. Then transfer stock to bin 3.",
        "Do not query, create, or modify records. Please place an order.",
    ],
)
def test_positive_effects_remain_specialist_bound(query):
    assert _write_intent(query.lower()) is not None
    assert select_capabilities(query, authenticated=True).requires_specialist
