"""Legacy AI note inputs must persist through the separate Note API."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ai.core.integrations.inventree.client import BusinessRuleError
from ai.core.integrations.inventree.notes import request_with_notes


@pytest.mark.asyncio
async def test_create_part_saves_markdown_as_a_linked_note():
    """Notes are sent to the Note API, not silently ignored by the Part API."""
    client = SimpleNamespace(_request=AsyncMock(side_effect=[{"pk": 42}, {"pk": 7}]))
    payload = {"name": "Bracket", "notes": "**Assembly instructions**"}

    result = await request_with_notes(
        client, "POST", "/part/", model_type="part", json_data=payload
    )

    assert result == {"pk": 42}
    assert payload["notes"] == "**Assembly instructions**"
    calls = client._request.call_args_list
    assert calls[0].kwargs["json_data"] == {"name": "Bracket"}
    assert calls[1].args == ("POST", "/notes/")
    assert calls[1].kwargs["json_data"] == {
        "model_type": "part",
        "model_id": 42,
        "title": "Note",
        "content": "<p><strong>Assembly instructions</strong></p>",
        "primary": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("notes", ["Replacement", ""])
async def test_update_replaces_or_clears_existing_primary_content(notes):
    """Updating a note retains its identity, title, and attached images."""
    client = SimpleNamespace(
        _request=AsyncMock(side_effect=[{"pk": 42}, {"results": [{"pk": 7}]}, {"pk": 7}])
    )

    await request_with_notes(
        client, "PATCH", "/part/42/", model_type="part", json_data={"notes": notes}
    )

    lookup = client._request.call_args_list[1]
    assert lookup.kwargs["params"]["model_type"] == "part"
    assert lookup.kwargs["params"]["model_id"] == 42
    assert lookup.kwargs["params"]["ordering"] == "-primary"
    client._request.assert_awaited_with(
        "PATCH", "/notes/7/", json_data={"content": "<p>Replacement</p>" if notes else ""}
    )


@pytest.mark.asyncio
async def test_deactivation_reason_preserves_existing_notes():
    """An appended reason becomes an additional note without overwriting rich text."""
    client = SimpleNamespace(_request=AsyncMock(side_effect=[{"pk": 42}, {"pk": 8}]))

    await request_with_notes(
        client,
        "PATCH",
        "/part/42/",
        model_type="part",
        json_data={"active": False, "notes": "Replaced by new design"},
        append_notes=True,
        note_title="Deactivation reason",
    )

    calls = client._request.call_args_list
    assert calls[0].kwargs["json_data"] == {"active": False}
    assert calls[1].args == ("POST", "/notes/")
    assert calls[1].kwargs["json_data"]["primary"] is False
    assert calls[1].kwargs["json_data"]["title"] == "Deactivation reason"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_serialized_stock_notes_cover_every_created_item():
    """Stock creation can return several records, each of which needs its note."""
    items = [{"pk": 100}, {"pk": 101}]
    client = SimpleNamespace(_request=AsyncMock(side_effect=[items, {"pk": 7}, {"pk": 8}]))

    result = await request_with_notes(
        client, "POST", "/stock/", model_type="stockitem", json_data={"notes": "Inspected"}
    )

    assert result is items
    assert [c.kwargs["json_data"]["model_id"] for c in client._request.call_args_list[1:]] == [
        100,
        101,
    ]


@pytest.mark.asyncio
async def test_note_failure_reports_saved_object_without_repeating_creation():
    """A partial effect must neither look successful nor cause a duplicate object."""
    client = SimpleNamespace(
        _request=AsyncMock(side_effect=[{"pk": 42}, BusinessRuleError("Permission denied")])
    )

    with pytest.raises(BusinessRuleError, match=r"42.*were saved.*notes"):
        await request_with_notes(
            client, "POST", "/part/", model_type="part", json_data={"notes": "Instructions"}
        )

    assert client._request.await_count == 2
    assert sum(c.args[1] == "/part/" for c in client._request.call_args_list) == 1


@pytest.mark.asyncio
async def test_requests_without_notes_keep_existing_behavior():
    """Ordinary updates require no additional permission or round trip."""
    client = SimpleNamespace(_request=AsyncMock(return_value={"pk": 42}))

    await request_with_notes(
        client, "PATCH", "/part/42/", model_type="part", json_data={"active": False}
    )

    client._request.assert_awaited_once_with("PATCH", "/part/42/", json_data={"active": False})


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, []])
async def test_missing_object_ids_report_uncertainty_without_writing_notes(response):
    """An incomplete response cannot establish that an object or note was saved."""
    client = SimpleNamespace(_request=AsyncMock(return_value=response))

    with pytest.raises(BusinessRuleError, match="check the outcome"):
        await request_with_notes(
            client, "POST", "/part/", model_type="part", json_data={"notes": "Instructions"}
        )

    client._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_object_write_does_not_attempt_a_note():
    """A denied or invalid primary operation has no follow-up effect."""
    client = SimpleNamespace(_request=AsyncMock(side_effect=BusinessRuleError("Denied")))

    with pytest.raises(BusinessRuleError, match="Denied"):
        await request_with_notes(
            client, "POST", "/part/", model_type="part", json_data={"notes": "Instructions"}
        )

    client._request.assert_awaited_once()
