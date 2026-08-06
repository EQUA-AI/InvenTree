"""S7 A2: the actor-scoped ASR lexicon respects the tenancy boundary.

This is the regression pin for the reverted first build, which exported the
deliberately unscoped routing lexicon into per-user provider sessions —
cross-tenant machine-name egress. The lexicon here must be derived through
the same scope filters the tools use, so actor A's hints can never carry
actor B's machine names or work-order references.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

from ai.core.tools.capabilities import (
    _ACTOR_HINTS_CACHE_PREFIX,
    _ACTOR_HINTS_CAP,
    actor_phrase_hints,
)
from assets.models import AssetMachine, Client

_SCOPES: dict[str, set[MaintenanceScope]] = {}


def _test_scope_resolver(actor):
    """Deployment-seam resolver reading the per-test scope table."""
    return _SCOPES.get(actor.get_username(), set())


READ_FLAGS = {
    'AIMMS_MACHINE_AI_READ_ENABLED': True,
    'AIMMS_MAINTENANCE_SCOPE_RESOLVER': f'{__name__}._test_scope_resolver',
}


@override_settings(**READ_FLAGS)
class ActorPhraseHintTests(TestCase):
    """Two tenants; each actor's hints stay inside their own client scope."""

    @classmethod
    def setUpTestData(cls):
        """Create two clients, two actors, and machines on each side."""
        cls.plant_a = Client.objects.create(name='Hint Plant A', code='hint-plant-a')
        cls.plant_b = Client.objects.create(name='Hint Plant B', code='hint-plant-b')
        users = get_user_model().objects
        cls.actor_a = users.create_user(username='hints-a', password='pw')
        cls.actor_b = users.create_user(username='hints-b', password='pw')
        cls.pump = AssetMachine.objects.create(
            name='Influent Pump 1', client=cls.plant_a
        )
        cls.drive = AssetMachine.objects.create(
            name='Clarifier Drive 2', client=cls.plant_a
        )
        cls.foreign = AssetMachine.objects.create(
            name='Foreign Digester 9', client=cls.plant_b
        )
        cls.wo_open = WorkOrder.objects.create(
            title='Rebuild coupling', status=WorkOrder.STATUS_BACKLOG, machine=cls.pump
        )
        cls.wo_done = WorkOrder.objects.create(
            title='Old job', status=WorkOrder.STATUS_DONE, machine=cls.pump
        )
        cls.wo_foreign = WorkOrder.objects.create(
            title='Foreign job',
            status=WorkOrder.STATUS_IN_PROGRESS,
            machine=cls.foreign,
        )

    def setUp(self):
        """Reset scope grants and the per-actor hint cache."""
        _SCOPES.clear()
        _SCOPES['hints-a'] = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.plant_a.pk)
        }
        _SCOPES['hints-b'] = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.plant_b.pk)
        }
        cache.delete(f'{_ACTOR_HINTS_CACHE_PREFIX}:{self.actor_a.pk}')
        cache.delete(f'{_ACTOR_HINTS_CACHE_PREFIX}:{self.actor_b.pk}')

    def test_actor_hints_never_cross_the_tenancy_boundary(self):
        """The revert regression: A's session must not carry B's names."""
        hints_a = actor_phrase_hints(self.actor_a.pk)
        hints_b = actor_phrase_hints(self.actor_b.pk)
        self.assertIn('Influent Pump 1', hints_a)
        self.assertIn('Clarifier Drive 2', hints_a)
        self.assertNotIn('Foreign Digester 9', hints_a)
        self.assertNotIn(self.wo_foreign.reference, hints_a)
        self.assertIn('Foreign Digester 9', hints_b)
        self.assertNotIn('Influent Pump 1', hints_b)
        self.assertNotIn('Clarifier Drive 2', hints_b)

    def test_open_work_order_references_included_done_excluded(self):
        """Open job references are speakable; completed jobs are not."""
        hints = actor_phrase_hints(self.actor_a.pk)
        self.assertIn(self.wo_open.reference, hints)
        self.assertNotIn(self.wo_done.reference, hints)

    def test_hints_are_bounded_and_cached(self):
        """The lexicon respects the provider cap and the per-actor cache."""
        first = actor_phrase_hints(self.actor_a.pk)
        self.assertLessEqual(len(first), _ACTOR_HINTS_CAP)
        # Second read comes from cache: delete a machine and expect no change
        # until the TTL/invalidation clears it.
        self.drive.delete()
        second = actor_phrase_hints(self.actor_a.pk)
        self.assertEqual(first, second)

    def test_unknown_or_inactive_user_gets_nothing(self):
        """Missing or deactivated actors resolve to an empty lexicon."""
        self.assertEqual(actor_phrase_hints(None), [])
        self.assertEqual(actor_phrase_hints(999999), [])
        self.actor_b.is_active = False
        self.actor_b.save(update_fields=['is_active'])
        cache.delete(f'{_ACTOR_HINTS_CACHE_PREFIX}:{self.actor_b.pk}')
        self.assertEqual(actor_phrase_hints(self.actor_b.pk), [])
