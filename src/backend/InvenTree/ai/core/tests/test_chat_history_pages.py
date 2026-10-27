"""Native transcript cursor fixtures consumed unchanged by browser replay."""

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest
from ai.core import app
from ai.core.auth import principal_context
from ai.core.tests.focus_test_db import ensure_island_tables
from ai.core.tests.test_capability_invocation_guard import _principal
from aichat.models import ChatMessage
from aichat.services.threads import ThreadRepository
from django.contrib.auth import get_user_model
from fastapi import HTTPException


def test_native_older_pages_exhaust_without_overlap_and_reauthorize():
    ensure_island_tables()
    user = get_user_model().objects.create_user(username=f"history-{uuid.uuid4().hex[:8]}")
    outsider = get_user_model().objects.create_user(
        username=f"history-other-{uuid.uuid4().hex[:8]}"
    )
    principal = _principal(str(user.pk))
    repo = ThreadRepository(user.pk, principal.scope)
    thread, _ = repo.get_or_create(title="Native cursor qualification")
    ChatMessage.objects.bulk_create([
        ChatMessage(
            thread=thread, sequence=n, role="assistant", content=f"Native saved message {n}"
        )
        for n in range(1, 8)
    ])

    async def read(before=None, actor=principal):
        token = principal_context.set(actor)
        try:
            return await app.get_thread(thread.pk, message_limit=3, before_sequence=before)
        finally:
            principal_context.reset(token)

    try:
        latest = asyncio.run(read())
        middle = asyncio.run(read(latest["next_before_sequence"]))
        earliest = asyncio.run(read(middle["next_before_sequence"]))
        assert [m["sequence"] for p in (earliest, middle, latest) for m in p["messages"]] == list(
            range(1, 8)
        )
        assert [p["next_before_sequence"] for p in (latest, middle, earliest)] == [5, 2, None]
        assert latest["has_earlier"] and middle["has_earlier"] and not earliest["has_earlier"]
        assert asyncio.run(read(5)) == middle
        with pytest.raises(HTTPException) as denied:
            asyncio.run(read(5, _principal(str(outsider.pk))))
        assert denied.value.status_code == 404
        if directory := os.environ.get("AIMMS_TEST_PRESENTATION_ARTIFACT_DIR"):
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            (path / "native-history.json").write_text(
                json.dumps({"latest": latest, "middle": middle, "earliest": earliest})
            )
    finally:
        thread.delete()
        user.delete()
        outsider.delete()
