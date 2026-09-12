"""Approval-review group checkbox, existing-grant migration and role rebuild."""

import importlib
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from users.models import RuleSet
from users.serializers import RuleSetSerializer
from users.tasks import update_group_roles


class ApprovalReviewRoleTests(TestCase):
    """A named review grant is independent of every model CRUD grant."""

    def setUp(self):
        """Create a local-only group with no privileges."""
        self.group = Group.objects.create(name='VOICE-TEST approval reviewers')
        self.rule = self.group.rule_sets.get(name='work_order')
        self.permission = Permission.objects.get(
            content_type__app_label='approvals',
            content_type__model='approval',
            codename='review',
        )

    def test_checkbox_roundtrip_rebuild_and_revoke(self):
        """The saved checkbox survives rebuilds and grants only review."""
        user = get_user_model().objects.create_user('role-reviewer')
        user.groups.add(self.group)
        self.assertFalse(RuleSetSerializer(self.rule).data['can_review_approvals'])
        serializer = RuleSetSerializer(
            self.rule, data={'can_review_approvals': True}, partial=True
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        update_group_roles(self.group)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertTrue(user.has_perm('approvals.review'))
        self.assertEqual(set(self.group.permissions.all()), {self.permission})
        self.rule.refresh_from_db()
        self.rule.can_review_approvals = False
        self.rule.save()
        update_group_roles(self.group)
        self.assertFalse(
            get_user_model().objects.get(pk=user.pk).has_perm('approvals.review')
        )

    def test_migration_preserves_only_existing_group_grants(self):
        """Backfill is not a new grant, and historical apps bypass live signals."""
        unprivileged = Group.objects.create(name='VOICE-TEST no review')
        self.group.permissions.add(self.permission)
        loader = MigrationLoader(connection)
        historical_apps = loader.project_state([
            ('users', '0019_approval_review_role')
        ]).apps
        migration = importlib.import_module(
            'users.migrations.0019_approval_review_role'
        )
        migration.preserve_review_grants(
            historical_apps, SimpleNamespace(connection=connection)
        )
        self.rule.refresh_from_db()
        self.assertTrue(self.rule.can_review_approvals)
        self.assertFalse(
            RuleSet.objects.get(
                group=unprivileged, name='work_order'
            ).can_review_approvals
        )
        update_group_roles(self.group)
        self.assertTrue(self.group.permissions.filter(pk=self.permission.pk).exists())
