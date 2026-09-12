"""Preserve AI tool notes across the API v537 move to separate Note records."""

from typing import Any

import markdown
from ai.core.integrations.inventree.client import BusinessRuleError


async def request_with_notes(
    client,
    method: str,
    endpoint: str,
    *,
    model_type: str,
    json_data: dict[str, Any],
    append_notes: bool = False,
    note_title: str = "Note",
):
    """Save an object and its legacy Markdown notes through the current API.

    Only callers for models whose direct notes field was removed use this
    adapter. Stock movement and order-line notes retain their existing API.
    Updates replace the primary note; append operations create an additional
    note so existing rich text and embedded images remain intact.

    The two API writes cannot share a transaction. If the note fails after the
    object was saved, report that partial outcome explicitly. Never retry the
    entire operation, which could duplicate an already-created object.
    """
    data = dict(json_data)
    notes = data.pop("notes", None)
    content = markdown.markdown(notes) if notes is not None else None
    result = await client._request(method, endpoint, json_data=data)

    if content is None:
        return result

    objects = result if isinstance(result, list) else [result]
    saved_ids = [obj.get("pk") for obj in objects if isinstance(obj, dict)]

    if not objects or len(saved_ids) != len(objects) or not all(saved_ids):
        raise BusinessRuleError(
            "The object request completed without identifying every saved object. "
            "Notes could not be saved; check the outcome before repeating the operation."
        )

    try:
        for model_id in saved_ids:
            existing = None
            if method.upper() != "POST" and not append_notes:
                response = await client._request(
                    "GET",
                    "/note/",
                    params={
                        "model_type": model_type,
                        "model_id": model_id,
                        "template": False,
                        "ordering": "-primary",
                        "limit": 1,
                    },
                )
                rows = response.get("results", []) if isinstance(response, dict) else response
                if rows:
                    existing = rows[0]

            if existing:
                await client._request(
                    "PATCH",
                    f"/note/{existing['pk']}/",
                    json_data={"content": content},
                )
            elif content:
                await client._request(
                    "POST",
                    "/note/",
                    json_data={
                        "model_type": model_type,
                        "model_id": model_id,
                        "title": note_title,
                        "content": content,
                        "primary": not append_notes,
                    },
                )
    except Exception as exc:
        raise BusinessRuleError(
            f"{model_type} object(s) {saved_ids} were saved, but saving their notes "
            f"did not complete. Do not repeat the object operation; check its notes. {exc}"
        ) from exc

    return result
