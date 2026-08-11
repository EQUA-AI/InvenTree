"""S30 E5: deterministic closeout-quality coaching on the proposal warnings."""

from django.test import TestCase, override_settings

from tasks.closeout_models import CloseoutPartUsage, CloseoutReading
from tasks.services.closeout_capture import (
    coaching_warnings,
    create_capture,
    request_extraction,
)
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin

_FIXTURES = 'tasks.tests.closeout_fixtures'

THIN = 'Replaced the clogged filter; flow restored to twenty GPM.'
FULL = (
    'Found the intake filter fully clogged with sediment after the storm. '
    'Isolated the pump, replaced the filter cartridge, flushed the line and '
    'verified flow restored to twenty GPM at the discharge gauge.'
)


@override_settings(**CLOSEOUT_FLAGS)
class CoachingWarningTests(CloseoutEnvMixin, TestCase):
    """The deterministic warning set from the work order's own state."""

    def setUp(self):
        self.build_env(username='coach-user')

    def test_thin_narrative_flagged_and_rich_narrative_clean(self):
        self.assertIn(
            'Narrative is thin; add cause, action and result detail',
            coaching_warnings(self.work_order, THIN),
        )
        self.assertEqual(coaching_warnings(self.work_order, FULL), [])

    def test_unresolved_readings_and_usage_are_flagged(self):
        CloseoutReading.objects.create(
            work_order=self.work_order,
            label='Discharge pressure',
            raw_text='42 psi',
            required=True,
            verification_state='pending',
            recorded_by=self.actor,
        )
        CloseoutPartUsage.objects.create(
            work_order=self.work_order,
            planned_quantity=1,
            issued_quantity=1,
            used_quantity=0,
            source='kit',
            state='pending',
        )
        warnings = coaching_warnings(self.work_order, FULL)
        self.assertIn('Required readings are unresolved', warnings)
        self.assertIn('Part usage rows are unresolved', warnings)

    @override_settings(
        AIMMS_CLOSEOUT_EXTRACTION_ENABLED=True,
        AIMMS_CLOSEOUT_EXTRACTOR=f'{_FIXTURES}.extractor_ok',
    )
    def test_extraction_proposal_carries_coaching_warnings(self):
        result = create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative=THIN,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='cap-coach',
        )
        proposal = request_extraction(
            work_order_id=self.work_order.pk,
            capture_id=result.metadata['capture_id'],
            actor=self.actor,
        )
        self.assertIn(
            'Narrative is thin; add cause, action and result detail',
            proposal.warnings,
        )
