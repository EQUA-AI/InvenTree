"""Synthetic ORM role controls for fast-path pre-dispatch authorization."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from ai.core.agents.routing import FastPathRouter
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TransactionTestCase
from users.models import RuleSet


class FastPathAuthorityDatabaseTests(TransactionTestCase):
    """Resolve real role rows for a synthetic active non-superuser."""

    def test_role_removal_and_actor_deactivation_prevent_provider_reads(self):
        actor = get_user_model().objects.create_user(username="synthetic-fastpath-reader")
        group = Group.objects.create(name="synthetic-fastpath-roles")
        actor.groups.add(group)
        RuleSet.objects.update_or_create(group=group, name="part", defaults={"can_view": True})
        stock, _ = RuleSet.objects.update_or_create(
            group=group, name="stock", defaults={"can_view": True}
        )
        events = []

        class Provider:
            async def search_parts_page(self, **kwargs):
                events.append("provider")
                return {
                    "results": [{"pk": 1, "in_stock": 4}],
                    "population_count": 1,
                    "has_more": False,
                }

        def read():
            router = FastPathRouter()
            router._data_provider = Provider()
            with patch(
                "ai.core.auth.get_current_principal",
                return_value=SimpleNamespace(user_pk=str(actor.pk)),
            ):
                return asyncio.run(
                    router.try_fast_path("How many resistors do we have in stock?", "synthetic")
                )

        self.assertIsNotNone(read())
        self.assertEqual(events, ["provider"])
        stock.can_view = False
        stock.save(update_fields=["can_view"])
        events.clear()
        self.assertIsNone(read())
        self.assertEqual(events, [])
        stock.can_view = True
        stock.save(update_fields=["can_view"])
        self.assertIsNotNone(read())
        events.clear()
        get_user_model().objects.filter(pk=actor.pk).update(is_active=False)
        self.assertIsNone(read())
        self.assertEqual(events, [])
