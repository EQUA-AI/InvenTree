"""S35: the DB-backed user_profile context provider reads real profile rows."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from ai.core.memory.providers.db_user_profile import DBUserProfileProvider


class DBUserProfileProviderTest(TestCase):
    """The provider returns real users.UserProfile facts, or None."""

    @classmethod
    def setUpTestData(cls):
        """Create a user; the post_save signal creates the profile row."""
        cls.user = get_user_model().objects.create_user(
            username='tech-anna', first_name='Anna', last_name='Ruiz'
        )

    def test_reads_profile_fields(self):
        """displayname/position/language come from the profile row."""
        profile = self.user.profile
        profile.displayname = 'Anna R.'
        profile.position = 'Maintenance Technician'
        profile.language = 'de'
        profile.save()

        result = DBUserProfileProvider._read(self.user.pk)

        self.assertEqual(
            result,
            {
                'username': 'tech-anna',
                'display_name': 'Anna R.',
                'position': 'Maintenance Technician',
                'language': 'de',
            },
        )

    def test_falls_back_to_user_names_when_profile_is_bare(self):
        """Blank profile fields degrade to the auth user's own names."""
        result = DBUserProfileProvider._read(self.user.pk)

        self.assertEqual(result['display_name'], 'Anna Ruiz')
        self.assertEqual(result['position'], '')
        self.assertEqual(result['language'], 'en')

    def test_unknown_user_returns_none(self):
        """An unresolvable pk is an absent context key, never a default profile."""
        self.assertIsNone(DBUserProfileProvider._read(999999))

    async def test_non_numeric_actor_returns_none(self):
        """'anonymous' and other non-pk actors simply have no profile."""
        provider = DBUserProfileProvider()
        self.assertIsNone(await provider.get_profile('anonymous'))
