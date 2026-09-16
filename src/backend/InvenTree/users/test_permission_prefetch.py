"""Empty prefetched memberships must stay empty during permission checks."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from part.models import Part
from users.permissions import check_user_permission, check_user_role
from users.serializers import get_user_roles


class EmptyMembershipTests(TestCase):
    """Keep permission hydration bounded for accounts with no groups."""

    def setUp(self):
        """Create a regular account without memberships."""
        self.user = get_user_model().objects.create_user('empty-membership-test')

    def test_role_hydration_reads_empty_membership_once(self):
        """An account with no groups requires only one membership query."""
        with self.assertNumQueries(1):
            roles = get_user_roles(self.user)
        self.assertTrue(roles)
        self.assertTrue(all(value is None for value in roles.values()))

    def test_empty_prefetch_is_reused_without_removing_native_permissions(self):
        """Reuse the prefetched roles; Django permissions remain authoritative."""
        group = Group.objects.create(name='outside-requested-memberships')
        rule = group.rule_sets.get(name='part')
        rule.can_view = True
        rule.save()
        self.user.groups.add(group)
        groups = self.user.groups.none()

        self.assertFalse(check_user_role(self.user, 'part', 'view', groups=groups))
        self.assertTrue(check_user_permission(self.user, Part, 'view', groups=groups))
