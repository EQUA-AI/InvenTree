"""Phase 2: permission-map completeness + AIMMS-native (email) RBAC.

Every tool any workflow exposes must be mapped (no silent pass-through) except
the database tools that self-enforce. Email uses a group-gated AIMMS-native
permission because Gmail has no InvenTree model; superusers get everything,
ungrouped users get nothing (fail-closed). Kanban is NOT native -- its cards are
work orders, governed by the InvenTree WORK_ORDER ruleset.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.integrations.controlled_document_corpus import (  # noqa: E402
    CONTROLLED_CORPUS_TOOLS,
)
from ai.core.integrations.document_search import DOCUMENT_SEARCH_TOOLS  # noqa: E402
from ai.core.integrations.email.tools import EMAIL_TOOLS, send_email  # noqa: E402
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS  # noqa: E402
from ai.core.integrations.kanban_tools import (  # noqa: E402
    KANBAN_TOOLS,
    list_kanban_cards,
)
from ai.core.tools.capabilities import tool_name  # noqa: E402
from ai.core.tools.inventree.write.purchase_orders import (  # noqa: E402
    PURCHASE_ORDER_WRITE_TOOLS,
)
from ai.core.tools.rbac import (  # noqa: E402
    _AIMMS_NATIVE_GROUPS,
    _filter_map_cached,
    _native_pairs,
    filter_tools,
    permission_profile,
)
from django.test import SimpleTestCase  # noqa: E402

# Deliberately unmapped in the list filter: query_database/list_database_tables
# self-enforce per-table RBAC; search_part_documents is gated by the wf8 catalog
# resource-authorizer and is a low-risk read elsewhere.
_UNMAPPED_ALLOWED = {"query_database", "list_database_tables", "search_part_documents"}


def _fake_user(*, active=True, superuser=False, groups=()):
    user = SimpleNamespace(is_active=active, is_superuser=superuser)
    manager = MagicMock()
    manager.values_list.return_value = list(groups)
    user.groups = manager
    return user


class PermissionMapCompletenessTests(SimpleTestCase):
    def test_no_workflow_tool_is_silently_pass_through(self):
        mapping = _filter_map_cached()
        all_tools = (
            set(INVENTORY_TOOLS)
            | set(EMAIL_TOOLS)
            | set(KANBAN_TOOLS)
            | set(DOCUMENT_SEARCH_TOOLS)
            # search_manuals must stay mapped (work_order:view), never a
            # silent pass-through like the allowed database self-enforcers.
            | set(CONTROLLED_CORPUS_TOOLS)
            | set(PURCHASE_ORDER_WRITE_TOOLS)
        )
        unmapped = [
            tool_name(t)
            for t in all_tools
            if t not in mapping and tool_name(t) not in _UNMAPPED_ALLOWED
        ]
        self.assertEqual(sorted(unmapped), [], f"Unmapped pass-through tools: {sorted(unmapped)}")


class NativePermissionTests(SimpleTestCase):
    def test_superuser_gets_all_native(self):
        self.assertEqual(_native_pairs(_fake_user(superuser=True)), frozenset(_AIMMS_NATIVE_GROUPS))

    def test_none_and_inactive_get_nothing(self):
        self.assertEqual(_native_pairs(None), frozenset())
        self.assertEqual(_native_pairs(_fake_user(active=False)), frozenset())

    def test_group_membership_grants_only_that_pair(self):
        pairs = _native_pairs(_fake_user(groups=["aimms.email.view"]))
        self.assertIn(("email", "view"), pairs)
        self.assertNotIn(("email", "send"), pairs)

    def test_kanban_is_not_an_aimms_native_capability(self):
        """Kanban cards are InvenTree work orders, governed by the WORK_ORDER
        ruleset -- not by an aimms.kanban.* group that no migration creates."""
        self.assertNotIn(("kanban", "view"), _AIMMS_NATIVE_GROUPS)
        self.assertNotIn(("kanban", "change"), _AIMMS_NATIVE_GROUPS)
        self.assertEqual(_native_pairs(_fake_user(groups=["aimms.kanban.view"])), frozenset())


class FilterWithNativeTests(SimpleTestCase):
    def test_superuser_keeps_kanban_and_email(self):
        profile = permission_profile(_fake_user(superuser=True))
        self.assertIn(("work_order", "view"), profile)
        self.assertIn(("email", "send"), profile)
        kept = filter_tools([list_kanban_cards, send_email], profile)
        self.assertIn(list_kanban_cards, kept)
        self.assertIn(send_email, kept)

    def test_ungrouped_user_loses_kanban_and_email(self):
        # No native groups, no InvenTree pairs -> mapped kanban/email dropped.
        profile = _native_pairs(_fake_user(groups=[]))
        self.assertEqual(filter_tools([list_kanban_cards, send_email], profile), [])
