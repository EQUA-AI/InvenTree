"""Unit tests for the user model database migrations."""

from django_test_migrations.contrib.unittest_case import MigratorTestCase

from InvenTree import unit_test


class TestForwardMigrations(MigratorTestCase):
    """Test entire schema migration sequence for the users app."""

    migrate_from = ('users', unit_test.getOldestMigrationFile('users'))
    migrate_to = ('users', unit_test.getNewestMigrationFile('users'))

    def prepare(self):
        """Setup the initial state of the database before migrations."""
        User = self.old_state.apps.get_model('auth', 'user')

        User.objects.create(username='fred', email='fred@fred.com', password='password')

        User.objects.create(username='brad', email='brad@fred.com', password='password')

    def test_users_exist(self):
        """Test that users exist in the database."""
        User = self.new_state.apps.get_model('auth', 'user')

        self.assertEqual(User.objects.count(), 2)


class TestCloseoutRuleSetPermissions(MigratorTestCase):
    """Test migration of existing group closeout permissions to RuleSet fields."""

    migrate_from = ('users', '0016_work_order_ruleset_grant')
    migrate_to = ('users', '0017_ruleset_closeout_permissions')

    def prepare(self):
        """Create a group with a closeout permission before the fields exist."""
        ContentType = self.old_state.apps.get_model('contenttypes', 'ContentType')
        Group = self.old_state.apps.get_model('auth', 'Group')
        Permission = self.old_state.apps.get_model('auth', 'Permission')

        group = Group.objects.create(name='Closeout technicians')
        content_type, _created = ContentType.objects.get_or_create(
            app_label='tasks', model='closeoutcapture'
        )
        permission, _created = Permission.objects.get_or_create(
            content_type=content_type,
            codename='capture_closeout',
            defaults={'name': 'Can capture closeout narratives'},
        )
        group.permissions.add(permission)

    def test_existing_permission_is_preserved(self):
        """The work-order ruleset reflects the group's existing grant."""
        Group = self.new_state.apps.get_model('auth', 'Group')
        RuleSet = self.new_state.apps.get_model('users', 'RuleSet')

        group = Group.objects.get(name='Closeout technicians')
        ruleset = RuleSet.objects.get(group=group, name='work_order')

        self.assertTrue(ruleset.can_capture_closeout)
        self.assertFalse(ruleset.can_review_closeout)
        self.assertTrue(
            group.permissions.filter(
                content_type__app_label='tasks', codename='capture_closeout'
            ).exists()
        )
