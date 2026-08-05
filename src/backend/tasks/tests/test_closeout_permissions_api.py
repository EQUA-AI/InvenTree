"""The staff-gated closeout permission surface.

The closeout permissions are Meta permissions outside the generic role table,
so the platform UI could not grant them (found live 2026-08-05: no frontend
method existed and closeout could never run end-to-end). These tests pin the
narrow admin surface that closes that gap: catalog-allowlisted, staff-gated,
direct-grants only, audited.
"""

import uuid

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from InvenTree.unit_test import InvenTreeAPITestCase
from tasks.closeout_models import CloseoutCapture
from tasks.closeout_permissions_api import closeout_permission_catalog


def _closeout_permission(codename):
    return Permission.objects.get(
        content_type=ContentType.objects.get_for_model(CloseoutCapture),
        codename=codename,
    )


class CloseoutPermissionApiTest(InvenTreeAPITestCase):
    """Grant/revoke lifecycle, gating, and reporting."""

    roles = 'all'

    def setUp(self):
        """Elevate the requesting account to the staff+admin surface."""
        super().setUp()
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        # roles='all' only sets can_view (the harness's assign_all branch never
        # reaches the other flags); the write gate needs admin.change explicitly.
        self.assignRole('admin.change')
        self.target = get_user_model().objects.create_user(
            username=f'tech-{uuid.uuid4().hex[:8]}', password='x'
        )
        self.url = reverse(
            'closeout-permission-detail', kwargs={'user_pk': self.target.pk}
        )

    def test_catalog_lists_all_six_permissions_ungranted(self):
        """The GET surface mirrors the model Meta and starts empty."""
        resp = self.get(self.url, expected_code=200)
        codenames = [row['codename'] for row in resp.data['permissions']]
        self.assertEqual(
            codenames, [codename for codename, _ in closeout_permission_catalog()]
        )
        self.assertIn('capture_closeout', codenames)
        self.assertTrue(
            all(
                not row['granted_direct'] and not row['effective']
                for row in resp.data['permissions']
            )
        )

    def test_grant_revoke_roundtrip_is_audited(self):
        """A direct grant becomes effective, is idempotent, and leaves a trail."""
        resp = self.post(
            self.url,
            {'codename': 'capture_closeout', 'granted': True},
            expected_code=200,
        )
        self.assertTrue(resp.data['changed'])
        row = next(
            r for r in resp.data['permissions'] if r['codename'] == 'capture_closeout'
        )
        self.assertTrue(row['granted_direct'])
        self.assertTrue(row['effective'])

        # The grant is real: a fresh user instance holds the Django permission.
        fresh = get_user_model().objects.get(pk=self.target.pk)
        self.assertTrue(fresh.has_perm('tasks.capture_closeout'))

        # Idempotent replay changes nothing and writes no second audit row.
        resp = self.post(
            self.url,
            {'codename': 'capture_closeout', 'granted': True},
            expected_code=200,
        )
        self.assertFalse(resp.data['changed'])
        self.assertEqual(
            LogEntry.objects.filter(object_id=str(self.target.pk)).count(), 1
        )

        # Revoke restores the initial state.
        resp = self.post(
            self.url,
            {'codename': 'capture_closeout', 'granted': False},
            expected_code=200,
        )
        self.assertTrue(resp.data['changed'])
        fresh = get_user_model().objects.get(pk=self.target.pk)
        self.assertFalse(fresh.has_perm('tasks.capture_closeout'))
        self.assertEqual(
            LogEntry.objects.filter(object_id=str(self.target.pk)).count(), 2
        )

    def test_group_conferred_grants_are_reported_not_editable(self):
        """A group grant shows as effective via_groups; revoking directly is a no-op."""
        group = Group.objects.create(name=f'closeout-{uuid.uuid4().hex[:6]}')
        group.permissions.add(_closeout_permission('verify_closeout'))
        self.target.groups.add(group)

        resp = self.get(self.url, expected_code=200)
        row = next(
            r for r in resp.data['permissions'] if r['codename'] == 'verify_closeout'
        )
        self.assertFalse(row['granted_direct'])
        self.assertEqual(row['via_groups'], [group.name])
        self.assertTrue(row['effective'])

        # "Revoking" cannot reach through to the group grant.
        resp = self.post(
            self.url,
            {'codename': 'verify_closeout', 'granted': False},
            expected_code=200,
        )
        self.assertFalse(resp.data['changed'])
        row = next(
            r for r in resp.data['permissions'] if r['codename'] == 'verify_closeout'
        )
        self.assertTrue(row['effective'])

    def test_only_catalog_codenames_are_reachable(self):
        """The allowlist stops the surface from granting anything else."""
        for bad in ('delete_user', 'add_user', 'change_repairpacket', ''):
            resp = self.post(
                self.url, {'codename': bad, 'granted': True}, expected_code=400
            )
            self.assertIn('Unknown closeout permission', str(resp.data['detail']))
        resp = self.post(
            self.url,
            {'codename': 'capture_closeout', 'granted': 'yes'},
            expected_code=400,
        )
        self.assertIn('boolean', str(resp.data['detail']))

    def test_non_staff_cannot_even_read(self):
        """Which colleagues hold closeout authority is admin material."""
        self.user.is_staff = False
        self.user.save(update_fields=['is_staff'])
        self.get(self.url, expected_code=403)
        self.post(
            self.url,
            {'codename': 'capture_closeout', 'granted': True},
            expected_code=403,
        )

    def test_unknown_user_is_a_404(self):
        """A missing target user is a plain 404, not a silent empty catalog."""
        url = reverse('closeout-permission-detail', kwargs={'user_pk': 99999999})
        self.get(url, expected_code=404)
