"""Tests for the flag-gated Right-Part Finder precondition on Job Kit substitution.

``decide_substitution`` must behave exactly as before while
``AIMMS_RPF_JOBKIT_ENFORCEMENT`` is off (the default). With enforcement on,
approving a substitution whose requested part sits in a configured critical
category requires a current, exactly-bound confirmed verification decision.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from common.models import Parameter, ParameterTemplate
from company.models import Company
from part.models import Part, PartCategory, PartRelated
from part.verification import services as verification_services
from part.verification.policy import create_policy_version
from part.verification.scope import VerificationScope
from part.verification_models import PartVerificationUse
from stock.models import StockItem
from tasks.models import (
    FulfillmentMode,
    JobKit,
    JobKitAllocation,
    JobKitLine,
    JobKitSubstitutionStatus,
    KanbanCard,
    ProcedureResourceKind,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.job_kits import (
    JobKitLineError,
    JobKitStateError,
    JobKitVerificationError,
    decide_substitution,
    propose_substitution,
    reserve_job_kit,
)

RPF_FLAGS = {
    'AIMMS_RPF_ENABLED': True,
    'AIMMS_RPF_COLLECTION_ENABLED': True,
    'AIMMS_RPF_EVALUATION_ENABLED': True,
    'AIMMS_RPF_CONFIRMATION_ENABLED': True,
}

POLICY = {
    'schema_version': 1,
    'description': 'job kit verification test policy',
    'requirements': [
        {
            'key': 'electrical.voltage',
            'category': 'electrical',
            'value_kind': 'range',
            'operator': 'range_within',
            'unit': 'V',
            'hard': True,
            'sources': [
                {'kind': 'observation'},
                {'kind': 'parameter', 'template': 'JKVerVoltage'},
            ],
            'missing_blocker': 'NAMEPLATE_REQUIRED',
        },
        {
            'key': 'electrical.phase',
            'value_kind': 'decimal',
            'operator': 'eq',
            'unit': '',
            'hard': True,
            'sources': [{'kind': 'parameter', 'template': 'JKVerPhase'}],
            'candidate_missing': 'exclude',
            'conflict_code': 'PHASE_CONFLICT',
        },
    ],
    'retrieval': {'max_candidates': 50, 'tier_cap': 25},
    'rank_factors': [
        {'id': 'exact_requested_identity', 'max': 25},
        {'id': 'exact_application_relation', 'max': 25},
        {'id': 'evidence_coverage', 'max': 15},
        {'id': 'preferred_representation', 'max': 4},
        {'id': 'freshness', 'max': 3},
    ],
    'revalidation': {
        'non_material_paths': ['requested_part.description'],
        'expiry_hours': 24,
    },
}


class JobKitVerificationTest(TestCase):
    """Exercise the RPF verification precondition around decide_substitution."""

    def setUp(self):
        """Create the customer-scoped work order, kit, line, and RPF fixtures."""
        self.customer = Company.objects.create(name='Ver Cust', is_customer=True)
        self.proposer = get_user_model().objects.create_superuser(
            username='ver-proposer', email='vp@example.com', password='pw'
        )
        self.decider = get_user_model().objects.create_superuser(
            username='ver-decider', email='vd@example.com', password='pw'
        )
        for user in (self.proposer, self.decider):
            user.maintenance_scopes = {
                MaintenanceScope(customer_id=self.customer.pk, site_key=None)
            }
            user.verification_scopes = {
                VerificationScope(customer_id=self.customer.pk, site_key=None)
            }
        self.work_order = KanbanCard.objects.create(
            title='Ver WO',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.kit = JobKit.objects.create(
            work_order=self.work_order, created_by=self.proposer
        )
        self.category = PartCategory.objects.create(
            name='JKVer Critical', description='critical parts'
        )
        self.other_category = PartCategory.objects.create(
            name='JKVer Benign', description='non-critical parts'
        )
        self.requested = Part.objects.create(
            name='JKVer OEM Motor',
            description='o',
            component=True,
            category=self.category,
        )
        self.alternate = Part.objects.create(
            name='JKVer Alt Motor',
            description='a',
            component=True,
            category=self.category,
        )
        self.alternate2 = Part.objects.create(
            name='JKVer Alt Motor 2',
            description='a2',
            component=True,
            category=self.category,
        )
        self.line = JobKitLine.objects.create(
            kit=self.kit,
            sequence=1,
            kind=ProcedureResourceKind.PART,
            requested_part=self.requested,
            selected_part=self.requested,
            required_quantity=Decimal('2'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME,
            source='manual',
            substitution_policy='supervisor',
        )
        # RPF candidate universe: the alternate is reachable via the related
        # tier, and both parts carry the parameters the policy requires.
        PartRelated.objects.create(part_1=self.requested, part_2=self.alternate)
        part_ct = ContentType.objects.get_for_model(Part)
        voltage = ParameterTemplate.objects.create(name='JKVerVoltage', units='V')
        phase = ParameterTemplate.objects.create(name='JKVerPhase')
        for part in (self.requested, self.alternate):
            Parameter.objects.create(
                model_type=part_ct, model_id=part.pk, template=voltage, data='460'
            )
            Parameter.objects.create(
                model_type=part_ct, model_id=part.pk, template=phase, data='3'
            )

    def propose(self, actor=None, part=None):
        """Propose a substitution on the line (defaults: proposer, alternate)."""
        return propose_substitution(
            work_order_id=self.work_order.pk,
            line_id=self.line.pk,
            proposed_part_id=(part or self.alternate).pk,
            actor=actor or self.proposer,
            basis={'reason': 'equivalent spec'},
        )

    def decide(self, sub, actor=None, verification_id=None):
        """Approve a proposed substitution (defaults: decider)."""
        return decide_substitution(
            work_order_id=self.work_order.pk,
            substitution_id=sub.pk,
            actor=actor or self.decider,
            approve=True,
            confirmed_verification_id=verification_id,
        )

    def enforced(self, **extra):
        """Return override_settings enabling RPF plus Job Kit enforcement."""
        settings = {
            'AIMMS_RPF_JOBKIT_ENFORCEMENT': True,
            'AIMMS_RPF_CRITICAL_CATEGORY_IDS': [self.category.pk],
            **RPF_FLAGS,
            **extra,
        }
        return override_settings(**settings)

    def build_confirmed_decision(
        self,
        idem='jkver',
        *,
        purpose='job_kit_substitution',
        work_order=None,
        line=None,
    ):
        """Build a current confirmed RPF decision selecting the alternate part.

        Runs the real verification flow: activate a policy, open a session for
        the supplied work order and Job Kit line, evaluate, and confirm the
        alternate's evaluation. Returns (session, decision).
        """
        work_order = work_order or self.work_order
        line = line or self.line
        create_policy_version(
            key='rpf-core', version=1, definition=POLICY, activate=True
        )
        session = verification_services.create_session(
            purpose=purpose,
            actor=self.decider,
            idempotency_key=f'{idem}-create',
            requested_part_id=self.requested.pk,
            work_order_id=work_order.pk,
            job_kit_line_id=line.pk,
        )
        verification_services.evaluate_session(
            session_id=session.pk,
            actor=self.decider,
            expected_revision=1,
            idempotency_key=f'{idem}-eval',
        )
        session.refresh_from_db()
        self.assertEqual(session.state, 'review_required')
        evaluation = session.candidate_evaluations.get(
            session_revision=1, candidate=self.alternate
        )
        self.assertTrue(evaluation.eligible)
        decision = verification_services.confirm_candidate(
            session_id=session.pk,
            evaluation_id=evaluation.pk,
            actor=self.decider,
            expected_revision=1,
            idempotency_key=f'{idem}-confirm',
            reason='verified equivalent',
        )
        return session, decision

    def create_line(self, kit, sequence):
        """Create another equivalent requested-part line in a Job Kit."""
        return JobKitLine.objects.create(
            kit=kit,
            sequence=sequence,
            kind=ProcedureResourceKind.PART,
            requested_part=self.requested,
            selected_part=self.requested,
            required_quantity=Decimal('2'),
            fulfillment_mode=FulfillmentMode.RESERVE_CONSUME,
            source='manual',
            substitution_policy='supervisor',
        )

    def assert_selection_unchanged(self, expected=None):
        """Assert the line's selected part is still the expected part."""
        self.line.refresh_from_db()
        self.assertEqual(self.line.selected_part_id, (expected or self.requested).pk)

    def assert_verification_blocked(self, sub, decision, expected_code):
        """Assert a verification failure leaves the Job Kit effect untouched."""
        with self.assertRaises(JobKitVerificationError) as ctx:
            self.decide(sub, verification_id=decision.pk)
        self.assertEqual(ctx.exception.code, expected_code)
        self.assert_selection_unchanged()
        sub.refresh_from_db()
        self.assertEqual(sub.status, JobKitSubstitutionStatus.PROPOSED)
        self.assertEqual(PartVerificationUse.objects.count(), 0)

    def test_flag_off_approve_works_without_verification(self):
        """Default deployments approve exactly as before, with no use rows."""
        sub = self.propose()
        decided = self.decide(sub)
        self.assertEqual(decided.status, JobKitSubstitutionStatus.APPROVED)
        self.assert_selection_unchanged(expected=self.alternate)
        self.assertEqual(PartVerificationUse.objects.count(), 0)

    def test_non_critical_category_skips_verification(self):
        """Enforcement only guards the configured critical categories."""
        with self.enforced(AIMMS_RPF_CRITICAL_CATEGORY_IDS=[self.other_category.pk]):
            sub = self.propose()
            decided = self.decide(sub)
        self.assertEqual(decided.status, JobKitSubstitutionStatus.APPROVED)
        self.assert_selection_unchanged(expected=self.alternate)
        self.assertEqual(PartVerificationUse.objects.count(), 0)

    def test_critical_without_verification_is_blocked(self):
        """A critical-category approval without a decision id fails closed."""
        with self.enforced():
            sub = self.propose()
            with self.assertRaises(JobKitVerificationError) as ctx:
                self.decide(sub)
        self.assertEqual(ctx.exception.code, 'PART_VERIFICATION_REQUIRED')
        self.assert_selection_unchanged()
        sub.refresh_from_db()
        self.assertEqual(sub.status, JobKitSubstitutionStatus.PROPOSED)

    def test_uncategorized_requested_part_fails_closed(self):
        """An unresolved category cannot bypass enabled verification enforcement."""
        self.requested.category = None
        self.requested.save(update_fields=['category'])

        with self.enforced():
            sub = self.propose()
            with self.assertRaises(JobKitVerificationError) as ctx:
                self.decide(sub)

        self.assertEqual(ctx.exception.code, 'PART_VERIFICATION_REQUIRED')
        self.assert_selection_unchanged()
        sub.refresh_from_db()
        self.assertEqual(sub.status, JobKitSubstitutionStatus.PROPOSED)
        self.assertEqual(PartVerificationUse.objects.count(), 0)

    def test_manual_purpose_decision_cannot_authorize_job_kit(self):
        """A manual verification with matching parts cannot authorize a Job Kit."""
        with self.enforced():
            sub = self.propose()
            _session, decision = self.build_confirmed_decision(
                idem='jkver-purpose', purpose='manual'
            )
            self.assert_verification_blocked(
                sub, decision, 'PART_VERIFICATION_PURPOSE_MISMATCH'
            )

    def test_other_work_order_decision_cannot_authorize_job_kit(self):
        """A verification for another work order cannot authorize this Job Kit."""
        other_work_order = KanbanCard.objects.create(
            title='Other Ver WO',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_MEDIUM,
            customer=self.customer,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        other_kit = JobKit.objects.create(
            work_order=other_work_order, created_by=self.proposer
        )
        other_line = self.create_line(other_kit, 1)

        with self.enforced():
            sub = self.propose()
            _session, decision = self.build_confirmed_decision(
                idem='jkver-work-order', work_order=other_work_order, line=other_line
            )
            self.assert_verification_blocked(
                sub, decision, 'PART_VERIFICATION_CONTEXT_MISMATCH'
            )

    def test_other_line_decision_cannot_authorize_job_kit(self):
        """A verification for another line cannot authorize this substitution."""
        other_line = self.create_line(self.kit, 2)

        with self.enforced():
            sub = self.propose()
            _session, decision = self.build_confirmed_decision(
                idem='jkver-line', line=other_line
            )
            self.assert_verification_blocked(
                sub, decision, 'PART_VERIFICATION_CONTEXT_MISMATCH'
            )

    def test_scope_resolution_error_uses_consumer_code(self):
        """RPF scope failures are translated to the stable consumer code."""
        with self.enforced():
            sub = self.propose()
            _session, decision = self.build_confirmed_decision(idem='jkver-scope')
            with override_settings(AIMMS_RPF_SCOPE_RESOLVER=lambda _actor: []):
                self.assert_verification_blocked(
                    sub, decision, 'PART_VERIFICATION_SCOPE_MISMATCH'
                )

    def test_confirmed_decision_allows_approval_and_binds_one_use(self):
        """A current confirmed decision unlocks the approval and binds a use.

        Reusing the same decision for a different proposed part must fail with
        the exact-selection mismatch code and leave the selection unchanged.
        """
        with self.enforced():
            sub = self.propose()
            _session, decision = self.build_confirmed_decision()
            decided = self.decide(sub, verification_id=decision.pk)
            self.assertEqual(decided.status, JobKitSubstitutionStatus.APPROVED)
            self.assert_selection_unchanged(expected=self.alternate)
            uses = PartVerificationUse.objects.filter(
                decision=decision,
                consumer_kind='job_kit',
                consumer_action='substitution_decide',
            )
            self.assertEqual(uses.count(), 1)

            # Reuse the decision for a different proposed part: exact-binding
            # must refuse, because the decision selected the first alternate.
            sub2 = self.propose(part=self.alternate2)
            with self.assertRaises(JobKitVerificationError) as ctx:
                self.decide(sub2, verification_id=decision.pk)
            self.assertEqual(
                ctx.exception.code, 'PART_VERIFICATION_SELECTED_PART_MISMATCH'
            )
            self.assert_selection_unchanged(expected=self.alternate)
            self.assertEqual(uses.count(), 1)

    def test_stale_decision_is_blocked(self):
        """An invalidated (stale) session blocks its decision from being used."""
        with self.enforced():
            sub = self.propose()
            session, decision = self.build_confirmed_decision(idem='jkver-stale')
            verification_services.invalidate_session(
                session_id=session.pk,
                actor=self.decider,
                idempotency_key='jkver-stale-inv',
                reason='manual invalidation',
            )
            with self.assertRaises(JobKitVerificationError) as ctx:
                self.decide(sub, verification_id=decision.pk)
        self.assertEqual(ctx.exception.code, 'PART_VERIFICATION_STALE')
        self.assert_selection_unchanged()
        self.assertEqual(PartVerificationUse.objects.count(), 0)

    def test_proposer_still_cannot_decide_under_enforcement(self):
        """Separation of duties survives enforcement, even with a decision."""
        with self.enforced():
            sub = self.propose()
            _session, decision = self.build_confirmed_decision(idem='jkver-sod')
            with self.assertRaises(JobKitLineError):
                self.decide(sub, actor=self.proposer, verification_id=decision.pk)
        self.assert_selection_unchanged()

    def test_active_allocation_still_blocks_under_enforcement(self):
        """Active reservations refuse the approval before verification runs."""
        with self.enforced():
            StockItem.objects.create(part=self.requested, quantity=Decimal('10'))
            reserve_job_kit(
                work_order_id=self.work_order.pk,
                actor=self.proposer,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='reserve',
            )
            self.assertTrue(JobKitAllocation.objects.filter(line=self.line).exists())
            sub = self.propose()
            with self.assertRaises(JobKitStateError):
                self.decide(sub)
        self.assert_selection_unchanged()
