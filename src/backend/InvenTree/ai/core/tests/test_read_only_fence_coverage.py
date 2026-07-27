"""V3: the read-only fence must cover every mutating tool, not one transport.

The fence was enforced only at the InvenTree REST client, on the documented
premise that "every live write tool ultimately calls it". The AIMMS-native tools
break that premise: kanban writes go straight to the Django ORM and email goes to
the mail backend, so neither passed the funnel. Verified in production during the
2026-07-26 voice test -- an archive reached the ORM with the fence active, which
also made ``confirmed_write_exception()`` vacuous for exactly the tool class the
test exercised.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.tools.read_only import (  # noqa: E402
    confirmed_write_exception,
    read_only_tool_fence,
)


@pytest.fixture(autouse=True)
def _stub_tasks_models():
    """Keep the ORM out of reach: a leak must fail the test, not write data."""
    module = types.ModuleType("tasks.models")
    module.WorkOrder = object
    module.WorkOrderPart = object
    sys.modules.setdefault("tasks", types.ModuleType("tasks"))
    previous = sys.modules.get("tasks.models")
    sys.modules["tasks.models"] = module
    yield
    if previous is not None:
        sys.modules["tasks.models"] = previous
    else:
        sys.modules.pop("tasks.models", None)


async def _call(tool, **kwargs):
    result = tool(**kwargs)
    return await result if hasattr(result, "__await__") else result


KANBAN_WRITES = [
    ("create_kanban_card", {"title": "x"}),
    ("update_kanban_card", {"card_id": 1}),
    ("move_kanban_card", {"card_id": 1, "status": "done"}),
    ("archive_kanban_card", {"card_id": 127}),
    ("restore_kanban_card", {"card_id": 127}),
    ("delete_kanban_card", {"card_id": 127}),
    ("add_parts_to_kanban_card", {"card_id": 1, "parts": []}),
    ("remove_part_from_kanban_card", {"card_id": 1, "part_id": 1}),
]
EMAIL_WRITES = [
    ("mark_email_processed", {"message_id": "1"}),
    ("send_email", {"to": "a@b.c", "subject": "s", "body": "b"}),
    ("generate_and_send_document", {"document_type": "quote", "to": "a@b.c"}),
]


@pytest.mark.parametrize(("name", "kwargs"), KANBAN_WRITES)
def test_kanban_writes_are_fenced(name, kwargs):
    from ai.core.integrations import kanban_tools

    tool = getattr(kanban_tools, name)
    with read_only_tool_fence(), pytest.raises(PermissionError):
        asyncio.run(_call(tool, **kwargs))


@pytest.mark.parametrize(("name", "kwargs"), EMAIL_WRITES)
def test_email_writes_are_fenced(name, kwargs):
    from ai.core.integrations.email import tools as email_tools

    tool = getattr(email_tools, name)
    with read_only_tool_fence(), pytest.raises(PermissionError):
        asyncio.run(_call(tool, **kwargs))


def test_reads_are_not_fenced():
    """The fence blocks effects, never lookups."""
    from ai.core.integrations import kanban_tools

    with read_only_tool_fence():
        try:
            asyncio.run(_call(kanban_tools.get_kanban_summary))
        except PermissionError:  # pragma: no cover - would be the defect
            pytest.fail("a read tool was blocked by the read-only fence")
        except Exception:
            pass  # any other failure is the stubbed ORM, not the fence


def test_confirmed_write_exception_reopens_the_fence_for_one_call():
    """A confirmed Tier-3 write must still be able to execute."""
    from ai.core.integrations import kanban_tools

    with read_only_tool_fence(), confirmed_write_exception():
        try:
            asyncio.run(_call(kanban_tools.archive_kanban_card, work_order_id=127))
        except PermissionError:  # pragma: no cover - would be the defect
            pytest.fail("confirmed write was blocked by the fence")
        except Exception:
            pass  # reached the (stubbed) implementation, which is the point


def test_every_exposed_kanban_and_email_write_is_guarded():
    """A new mutating tool cannot be added without the fence guard."""
    from ai.core.integrations.email.tools import EMAIL_TOOLS
    from ai.core.integrations.kanban_tools import KANBAN_READ_TOOLS, KANBAN_TOOLS
    from ai.core.tools.capabilities import tool_name

    read_names = {tool_name(tool) for tool in KANBAN_READ_TOOLS} | {
        "list_emails",
        "get_email_details",
        "download_attachment",
    }
    unguarded = []
    for tool in list(KANBAN_TOOLS) + list(EMAIL_TOOLS):
        name = tool_name(tool)
        if name in read_names:
            continue
        with read_only_tool_fence():
            try:
                asyncio.run(_call(tool))
            except PermissionError:
                continue
            except TypeError:
                unguarded.append(name)  # reached arg binding => past the guard
            except Exception:
                unguarded.append(name)
    assert unguarded == [], f"mutating tools missing the fence guard: {unguarded}"
