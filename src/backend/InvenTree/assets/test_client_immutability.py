"""Client.code immutability (review finding F-12).

The code is the scope-token identifier: actor grants and every stamped RAG
``client_codes`` value name it. All three write layers must refuse a rename.
"""

from django.test import TestCase

from assets.models import Client
from assets.serializers import ClientSerializer


class ClientCodeImmutabilityTests(TestCase):
    """Serializer, admin, and ORM all refuse code changes after creation."""

    @classmethod
    def setUpTestData(cls):
        """One tenant to attempt renames against."""
        cls.client_row = Client.objects.create(name='Acme', code='acme')

    def test_serializer_refuses_code_change(self):
        """PATCHing a new code is a validation error."""
        serializer = ClientSerializer(
            instance=self.client_row, data={'code': 'acme-renamed'}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('code', serializer.errors)

    def test_serializer_allows_same_code_and_other_fields(self):
        """Idempotent code plus name edits stay legal."""
        serializer = ClientSerializer(
            instance=self.client_row,
            data={'code': 'acme', 'name': 'Acme Industrial'},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_serializer_allows_code_on_create(self):
        """Creation still sets a code freely."""
        serializer = ClientSerializer(data={'name': 'Zeta', 'code': 'zeta'})
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_model_save_refuses_code_change(self):
        """Direct ORM writes hit the model-level belt."""
        self.client_row.code = 'acme-renamed'
        with self.assertRaises(ValueError):
            self.client_row.save()

    def test_model_save_allows_other_field_changes(self):
        """Non-code edits save normally."""
        self.client_row.refresh_from_db()
        self.client_row.name = 'Acme Industrial'
        self.client_row.save()
        self.client_row.refresh_from_db()
        self.assertEqual(self.client_row.name, 'Acme Industrial')
        self.assertEqual(self.client_row.code, 'acme')

    def test_admin_marks_code_readonly_when_editing(self):
        """The admin change form locks the code field."""
        from django.contrib.admin.sites import AdminSite

        from assets.admin import ClientAdmin

        admin = ClientAdmin(Client, AdminSite())
        self.assertIn('code', admin.get_readonly_fields(None, obj=self.client_row))
        self.assertNotIn('code', admin.get_readonly_fields(None, obj=None))
