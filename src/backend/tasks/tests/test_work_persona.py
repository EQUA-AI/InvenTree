"""R6 UI10 first-use Work-list view from an explicit, admin-named persona."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from tasks.persona import WORK_PERSONA_RULE
from users.models import RuleSet

URL = '/api/kanban/work-orders/persona/'


@override_settings(
    AIMMS_WORK_SUPERVISOR_GROUPS=['Supervisors', 'Shift leads'],
    AIMMS_WORK_TECHNICIAN_GROUPS=['Technicians'],
)
class WorkPersonaTests(TestCase):
    """Normal reader with a stored Work role; persona groups added per case."""

    def setUp(self):
        """A Work reader who holds no persona group yet."""
        self.reader = get_user_model().objects.create_user(
            username='persona-reader', password='synthetic-only'
        )
        role = Group.objects.create(name='Work editors')
        RuleSet.objects.update_or_create(
            name='work_order',
            group=role,
            defaults={
                'can_view': True,
                'can_add': True,
                'can_change': True,
                'can_delete': True,
            },
        )
        self.reader.groups.add(role)
        self.groups = {
            name: Group.objects.create(name=name)
            for name in (
                'Supervisors',
                'Shift leads',
                'Technicians',
                'Technicians East',
            )
        }
        self.api = APIClient()
        self.assertTrue(
            self.api.login(username=self.reader.username, password='synthetic-only')
        )

    def join(self, *names):
        """Add the reader to persona groups."""
        self.reader.groups.add(*(self.groups[name] for name in names))

    def persona(self):
        """Read the persona as the Work list does."""
        response = self.api.get(URL)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return response.json()

    def test_no_persona_group_starts_on_all_work(self):
        """A Work role alone, even with edit rights, is not a persona."""
        self.assertEqual(
            self.persona(),
            {'rule': WORK_PERSONA_RULE, 'persona': None, 'default_scope': 'all'},
        )
        self.assertEqual(WORK_PERSONA_RULE, 'work_persona.v1')

    def test_technician_starts_on_my_work(self):
        """A named technician group maps to My work."""
        self.join('Technicians')
        body = self.persona()
        self.assertEqual(
            (body['persona'], body['default_scope']), ('technician', 'mine')
        )

    def test_any_named_supervisor_group_starts_on_needs_attention(self):
        """Each listed supervisor group maps to Needs attention."""
        for name in ('Supervisors', 'Shift leads'):
            with self.subTest(group=name):
                self.reader.groups.remove(*self.groups.values())
                self.join(name)
                body = self.persona()
                self.assertEqual(
                    (body['persona'], body['default_scope']),
                    ('supervisor', 'attention'),
                )

    def test_mixed_account_is_always_a_supervisor(self):
        """Both personas resolve the same way on every read: no view flip."""
        self.join('Technicians', 'Shift leads')
        reads = {self.persona()['persona'] for _ in range(3)}
        self.assertEqual(reads, {'supervisor'})

    def test_a_group_named_in_both_lists_is_a_supervisor(self):
        """A misconfigured overlap still resolves by the precedence rule."""
        with override_settings(
            AIMMS_WORK_SUPERVISOR_GROUPS=['Technicians'],
            AIMMS_WORK_TECHNICIAN_GROUPS=['Technicians'],
        ):
            self.join('Technicians')
            self.assertEqual(self.persona()['persona'], 'supervisor')

    def test_only_listed_names_count(self):
        """A similarly named group is not a persona group."""
        self.join('Technicians East')
        self.assertIsNone(self.persona()['persona'])

    def test_membership_is_read_live(self):
        """Joining or leaving a group changes the next read."""
        self.join('Technicians')
        self.assertEqual(self.persona()['persona'], 'technician')
        self.reader.groups.remove(self.groups['Technicians'])
        self.assertIsNone(self.persona()['persona'])

    def test_unmapped_deployment_has_no_personas(self):
        """With no group names configured, everyone starts on All work."""
        self.join('Supervisors', 'Technicians')
        with override_settings(
            AIMMS_WORK_SUPERVISOR_GROUPS=[], AIMMS_WORK_TECHNICIAN_GROUPS=[]
        ):
            body = self.persona()
        self.assertEqual((body['persona'], body['default_scope']), (None, 'all'))

    def test_superuser_and_staff_are_not_personas(self):
        """Administrative status says what someone may do, not their job."""
        admin = get_user_model().objects.create_superuser(
            username='persona-admin', password='synthetic-only', email=''
        )
        admin.is_staff = True
        admin.save()
        api = APIClient()
        api.force_authenticate(admin)
        response = api.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['persona'])

    def test_needs_the_work_role_and_is_read_only(self):
        """No Work role, no answer; even a full Work editor cannot write."""
        outsider = get_user_model().objects.create_user(username='persona-outsider')
        outsider.groups.add(self.groups['Supervisors'])
        api = APIClient()
        api.force_authenticate(outsider)
        self.assertEqual(api.get(URL).status_code, 403)
        self.assertIn(APIClient().get(URL).status_code, (401, 403))
        self.join('Supervisors')
        for method in ('post', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.api, method)(URL).status_code, 405)
