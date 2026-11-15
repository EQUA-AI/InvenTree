"""Characterize the native resolver's client-scope fallback.

These tests preserve native behavior after the owner chose a stricter explicit
grant policy for the separate unwired multi-client integration.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from tasks.scope import scoped_machines

from assets.models import AssetMachine, Client, ClientScopeGrant
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='contract-fallback',
)
class NativeMachineSourceFallbackCharacterizationTests(TestCase):
    """Exercise an active configured fallback with synthetic clients only."""

    def setUp(self):
        """Create one Work viewer with an explicit grant and a fallback client."""
        self.actor = get_user_model().objects.create_user(username='fallback-reader')
        group = Group.objects.create(name='Fallback characterization readers')
        self.actor.groups.add(group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.explicit = Client.objects.create(
            name='Synthetic explicit', code='contract-explicit'
        )
        self.fallback = Client.objects.create(
            name='Synthetic fallback', code='contract-fallback'
        )
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.explicit
        )
        self.explicit_machine = AssetMachine.objects.create(
            name='Synthetic explicit machine', client=self.explicit
        )
        self.fallback_machine = AssetMachine.objects.create(
            name='Synthetic fallback machine', client=self.fallback
        )

    def _read(self, machine):
        return scoped_machines(self.actor).filter(pk=machine.pk).first()

    def test_removing_last_grant_currently_enables_configured_fallback(self):
        """Revocation can change scope to the separately configured client."""
        self.assertIsNotNone(self._read(self.explicit_machine))
        self.assertIsNone(self._read(self.fallback_machine))

        self.grant.delete()

        self.assertIsNone(self._read(self.explicit_machine))
        self.assertIsNotNone(self._read(self.fallback_machine))

    def test_inactive_only_granted_client_currently_enables_fallback(self):
        """A remaining grant row for an inactive client does not suppress fallback."""
        self.assertIsNone(self._read(self.fallback_machine))
        self.explicit.active = False
        self.explicit.save(update_fields=['active'])

        self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
        self.assertIsNone(self._read(self.explicit_machine))
        self.assertIsNotNone(self._read(self.fallback_machine))

    def test_another_active_grant_prevents_fallback(self):
        """An active explicit grant remains authoritative over fallback."""
        other = Client.objects.create(name='Synthetic other', code='contract-other')
        other_machine = AssetMachine.objects.create(
            name='Synthetic other machine', client=other
        )
        ClientScopeGrant.objects.create(user=self.actor, client=other)
        self.explicit.active = False
        self.explicit.save(update_fields=['active'])

        self.assertIsNotNone(self._read(other_machine))
        self.assertIsNone(self._read(self.fallback_machine))

    def test_role_removal_denies_after_a_fallback_read(self):
        """Work-view revocation still denies a previously readable fallback row."""
        self.grant.delete()
        self.assertIsNotNone(self._read(self.fallback_machine))

        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])

        self.assertIsNone(self._read(self.fallback_machine))
