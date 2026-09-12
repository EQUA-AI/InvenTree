"""Exercise AI note compatibility against the real InvenTree REST endpoints."""

from types import SimpleNamespace

from asgiref.sync import async_to_sync, sync_to_async

from ai.core.integrations.inventree.notes import request_with_notes
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part


class InventoryNoteAPITests(InvenTreeAPITestCase):
    """An authorized tool write must persist both object changes and note content."""

    roles = ['part.add', 'part.change']

    def _request(self, method, endpoint, *, json_data=None, params=None):
        response = getattr(self.client, method.lower())(
            f'/api{endpoint}',
            data=params if method == 'GET' else json_data,
            format='json',
        )
        self.assertLess(response.status_code, 400, response.content)
        return response.json()

    def _save(self, method, endpoint, data, **kwargs):
        client = SimpleNamespace(_request=sync_to_async(self._request))
        return async_to_sync(request_with_notes)(
            client, method, endpoint, model_type='part', json_data=data, **kwargs
        )

    def test_create_and_update_primary_note(self):
        """The adapter uses valid model identifiers and preserves the note identity."""
        result = self._save(
            'POST', '/part/', {'name': 'Merge note test', 'notes': '**Assembly**'}
        )
        part = Part.objects.get(pk=result['pk'])
        note = part.primary_note
        self.assertIn('<strong>Assembly</strong>', note.content)

        self._save('PATCH', f'/part/{part.pk}/', {'notes': 'Updated instructions'})

        self.assertEqual(part.notes.count(), 1)
        note.refresh_from_db()
        self.assertIn('Updated instructions', note.content)
        self.assertEqual(part.primary_note.pk, note.pk)

    def test_deactivation_keeps_original_note_and_records_reason(self):
        """A new deactivation reason does not replace existing rich-text instructions."""
        result = self._save(
            'POST',
            '/part/',
            {'name': 'Retired part', 'notes': '**Keep these instructions**'},
        )
        part = Part.objects.get(pk=result['pk'])
        original = part.primary_note

        self._save(
            'PATCH',
            f'/part/{part.pk}/',
            {'active': False, 'notes': 'Replaced by the revised design'},
            append_notes=True,
            note_title='Deactivation reason',
        )

        part.refresh_from_db()
        original.refresh_from_db()
        self.assertFalse(part.active)
        self.assertIn('<strong>Keep these instructions</strong>', original.content)
        self.assertEqual(part.notes.count(), 2)
        self.assertIn(
            'Replaced by the revised design',
            part.notes.get(title='Deactivation reason').content,
        )
