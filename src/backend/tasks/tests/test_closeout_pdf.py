"""PDF bytes, immutable provenance and current Work read authorization."""

import io
from unittest import mock

from django.test import TestCase, override_settings

from pypdf import PdfReader
from rest_framework.test import APIClient

from tasks.services.closeout import complete_work_order
from tasks.services.service_reports import latest_report, service_report_command
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, VALID_CLOSEOUT, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutPDFTests(CloseoutEnvMixin, TestCase):
    """Render real PDF bytes without any external resources or owner data."""

    def setUp(self):
        """Use a scoped disposable Work and API client."""
        self.build_env(username='pdf-reader')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.url = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/report.pdf'

    def text(self, response):
        """Validate binary format before checking extracted document text."""
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        return '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)

    def draft(self, **changes):
        """Preview incomplete fields without writing a submission."""
        return self.client.post(self.url, {
            'source': 'draft', 'expected_version': self.work_order.lifecycle_version,
            'account': {'action': 'Checked synthetic motor', 'result': ''}, **changes,
        }, format='json')

    def test_incomplete_draft_is_pdf_and_never_submits(self):
        response = self.draft()
        text = self.text(response)
        self.assertIn('DRAFT', text)
        self.assertIn('Checked synthetic motor', text)
        self.assertIn('Not recorded', text)
        self.assertFalse(self.work_order.events.exists())
        self.assertIsNone(latest_report(self.work_order))

    def test_changed_version_requires_reload(self):
        self.assertEqual(self.draft(expected_version=999).status_code, 409)

    def test_html_is_text_and_never_fetches_urls(self):
        with mock.patch('tasks.services.closeout_pdf.CloseoutPDFFetcher.fetch') as fetch:
            text = self.text(self.draft(account={'action': '<img src="http://127.0.0.1/private">'}))
        fetch.assert_not_called()
        self.assertIn('<img', text)

    def test_submission_uses_retained_account_not_browser_replacement(self):
        service_report_command(work_order_id=self.work_order.pk, actor=self.actor,
                               expected_version=self.work_order.lifecycle_version,
                               idempotency_key='pdf-submit', action='submit', account=VALID_CLOSEOUT)
        self.work_order.refresh_from_db()
        submission = latest_report(self.work_order)
        text = self.text(self.draft(source='submitted', report_id=submission.pk,
                                   account={'action': 'Untrusted replacement'}))
        self.assertIn(VALID_CLOSEOUT['action'], text)
        self.assertNotIn('Untrusted replacement', text)
        self.assertIn('SUBMITTED SNAPSHOT', text)

    def test_wrong_submission_identity_is_not_found(self):
        self.assertEqual(self.draft(source='submitted', report_id=999).status_code, 404)

    def test_frozen_pdf_does_not_reconstruct_from_changed_current_work(self):
        complete_work_order(work_order_id=self.work_order.pk, actor=self.actor,
                            expected_version=self.work_order.lifecycle_version,
                            idempotency_key='pdf-complete', closeout=VALID_CLOSEOUT)
        self.work_order.refresh_from_db()
        report = self.work_order.structured_closeout.reports.get()
        original_title = self.work_order.title
        self.work_order.title = 'Changed after freezing'
        self.work_order.save(update_fields=['title'])
        url = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/reports/{report.pk}/pdf/'
        text = self.text(self.client.get(url))
        self.assertIn(original_title, text)
        self.assertNotIn('Changed after freezing', text)
        self.assertIn(report.content_hash, text.replace('\n', ''))

    def test_anonymous_cannot_download(self):
        self.client.force_authenticate(None)
        self.assertIn(self.draft().status_code, (401, 403))

    def test_foreign_work_is_not_found(self):
        self.assertEqual(self.client.post('/api/tasks/work-orders/999999/closeout/report.pdf', {
            'source': 'draft', 'expected_version': 1,
        }, format='json').status_code, 404)
