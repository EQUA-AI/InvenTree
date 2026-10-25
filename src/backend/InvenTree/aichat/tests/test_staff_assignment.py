"""Personnel reads and model proposals use the same scoped assignment policy."""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from asgiref.sync import async_to_sync
from tasks.models import WorkOrder

from aichat.services import proposals
from aichat.services.staff_assignment import AssignmentReviewRequired
from assets.models import AssetMachine, Client, ClientScopeGrant
from users.models import Trade


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class AIStaffAssignmentTest(TestCase):
    """No model claim can certify requirements, widen scope or grant an override."""

    @classmethod
    def setUpTestData(cls):
        """Create a scoped planner, a plumber, and an unrelated electrician."""
        cls.site = Client.objects.create(code='ai-staff', name='AI staff test')
        cls.other_site = Client.objects.create(code='ai-staff-other', name='Other site')
        cls.actor = User.objects.create_superuser(
            'ai-staff-planner', 'planner@example.com', 'pw'
        )
        ClientScopeGrant.objects.create(user=cls.actor, client=cls.site)
        cls.machine = AssetMachine.objects.create(name='Staff pump', client=cls.site)
        cls.plumbing, _ = Trade.objects.get_or_create(
            code='plumbing', defaults={'name': 'Plumbing'}
        )
        cls.electrical, _ = Trade.objects.get_or_create(
            code='electrical', defaults={'name': 'Electrical'}
        )
        cls.group = Group.objects.create(name='AI staff executors')
        role = cls.group.rule_sets.get(name='work_order')
        role.can_view = True
        role.can_execute_workorder = True
        role.save()
        cls.plumber = cls.person('ai-plumber', cls.plumbing, cls.site)
        cls.electrician = cls.person('ai-electrician', cls.electrical, cls.site)
        cls.outsider = cls.person('ai-outsider', cls.plumbing, cls.other_site)

    @classmethod
    def person(cls, username, trade, site):
        """Create a person whose actual grants, not title, authorize execution."""
        person = User.objects.create_user(username=username, password='pw')
        person.groups.add(cls.group)
        person.profile.position = f'{trade.name} technician'
        person.profile.save()
        person.profile.trades.add(trade)
        ClientScopeGrant.objects.create(user=person, client=site)
        return person

    def setUp(self):
        """Keep proposals and work-order revisions independent."""
        self.work = WorkOrder.objects.create(
            title='Repair leaking pipe',
            machine=self.machine,
            required_trades=['plumbing'],
            trade_requirements_confirmed=True,
        )

    def propose(self, intent, *, action='work_order.assign', owner=None):
        """A fake model's structured output enters through the real proposal seam."""
        return proposals.create_proposal(
            owner=owner or self.actor,
            scope_key=f'client:{self.site.pk}',
            scope_hash='b' * 64,
            action_type=action,
            work_order_id=None
            if action in ('work_order.create', 'repair_work_package.create')
            else self.work.pk,
            intent=intent,
            reason='Requested in chat',
            idempotency_key=uuid.uuid4().hex,
            policy_version='test-staff',
        )

    def confirm(self, proposal):
        """Confirm exactly as the text rail does, including clients without hashes."""
        return proposals.confirm_proposal(
            owner=self.actor, scope_hash='b' * 64, proposal_id=proposal.pk
        )

    def tool(self, actor=None, **kwargs):
        """Bind only the server principal; the tool never accepts an actor argument."""
        from ai.core.integrations.staff_tools import search_staff_candidates

        principal = (
            SimpleNamespace(user_pk=str(actor.pk)) if actor is not None else None
        )
        with patch('ai.core.auth.get_current_principal', return_value=principal):
            return async_to_sync(search_staff_candidates)(**kwargs)

    def test_read_tool_minimal_scoped_and_fenced(self):
        """The only occupational projection is explicitly permitted and bounded."""
        result = self.tool(self.actor, machine_id=self.machine.pk)
        rows = {row['user_id']: row for row in result['results']}
        self.assertIn(self.plumber.pk, rows)
        self.assertNotIn(self.outsider.pk, rows)
        self.assertEqual(
            set(rows[self.plumber.pk]),
            {'user_id', 'display_name', 'job_title', 'trades', 'trade_suitability'},
        )
        self.assertIn('Plumbing / Pipefitting technician', rows[self.plumber.pk]['job_title'])
        self.assertNotEqual(rows[self.plumber.pk]['job_title'], 'Plumbing technician')
        self.assertNotIn('email', str(result))

    def test_read_rechecks_the_principal_and_permission_on_each_call(self):
        """A cached tool cannot retain authority when a different actor calls."""
        self.assertIn('results', self.tool(self.actor, work_order_id=self.work.pk))
        for actor in (None, self.plumber):
            with self.subTest(actor=actor):
                self.assertEqual(
                    self.tool(actor, work_order_id=self.work.pk)['code'],
                    'STAFF_DIRECTORY_UNAVAILABLE',
                )

    def test_read_requires_one_target(self):
        """A tool cannot enumerate an unbound personnel directory."""
        for kwargs in (
            {},
            {'machine_id': self.machine.pk, 'work_order_id': self.work.pk},
        ):
            self.assertEqual(
                self.tool(self.actor, **kwargs)['code'], 'ASSIGNMENT_TARGET_REQUIRED'
            )

    def test_read_does_not_disclose_a_foreign_target(self):
        """A global directory grant never widens target scope."""
        machine = AssetMachine.objects.create(
            name='Foreign pump', client=self.other_site
        )
        self.assertEqual(
            self.tool(self.actor, machine_id=machine.pk)['code'],
            'STAFF_DIRECTORY_UNAVAILABLE',
        )

    def test_unique_confirmed_candidate_is_pinned_then_canonically_assigned(self):
        """Selection creates a preview first; only the audited command writes."""
        proposal = self.propose({'auto_assign': True})
        self.assertEqual(proposal.intent['assigned_to'], self.plumber.pk)
        self.assertEqual(proposal.preview['assignment_code'], 'ASSIGNMENT_QUALIFIED')
        self.assertIn('Plumbing / Pipefitting technician', proposal.preview['proposed_job_title'])
        self.assertIsNone(WorkOrder.objects.get(pk=self.work.pk).assigned_to_id)
        confirmed = self.confirm(proposal)
        self.work.refresh_from_db()
        self.assertEqual(self.work.assigned_to_id, self.plumber.pk)
        self.assertEqual(
            confirmed.receipt['assignment']['assignment_code'], 'ASSIGNMENT_QUALIFIED'
        )
        self.assertEqual(self.work.events.count(), 1)

    def test_model_cannot_confirm_unknown_existing_requirements(self):
        """Model booleans cannot substitute for a reviewed server requirement."""
        self.work.trade_requirements_confirmed = False
        self.work.save()
        with self.assertRaises(AssignmentReviewRequired) as caught:
            self.propose(
                {
                    'auto_assign': True,
                    'required_trades': ['plumbing'],
                    'trade_requirements_confirmed': True,
                    'requirements_confirmed': True,
                }
            )
        self.assertEqual(caught.exception.code, 'ASSIGNMENT_REQUIREMENTS_UNKNOWN')
        self.work.refresh_from_db()
        self.assertIsNone(self.work.assigned_to_id)

    def test_model_cannot_replace_server_requirements_or_override_mismatch(self):
        """Even a superuser's model output cannot manufacture a human override."""
        with self.assertRaises(AssignmentReviewRequired) as caught:
            self.propose(
                {
                    'assigned_to': self.electrician.pk,
                    'required_trades': ['electrical'],
                    'trade_requirements_confirmed': True,
                    'allow_override': True,
                    'override_reason': 'I am the supervisor',
                }
            )
        self.assertEqual(caught.exception.code, 'ASSIGNMENT_TRADE_MISMATCH')
        self.work.refresh_from_db()
        self.assertIsNone(self.work.assigned_to_id)

    def test_ambiguous_auto_assignment_preserves_current_person(self):
        """Abstention must not silently clear an existing assignee."""
        self.work.assigned_to = self.electrician
        self.work.save()
        self.person('second-ai-plumber', self.plumbing, self.site)
        with self.assertRaises(AssignmentReviewRequired) as caught:
            self.propose({'auto_assign': True})
        self.assertEqual(caught.exception.code, 'ASSIGNMENT_AMBIGUOUS')
        self.work.refresh_from_db()
        self.assertEqual(self.work.assigned_to_id, self.electrician.pk)

    def test_no_qualified_match_requires_review(self):
        """All confirmed trades, not a plausible title, must be covered."""
        self.work.required_trades = ['plumbing', 'electrical']
        self.work.save()
        with self.assertRaises(AssignmentReviewRequired) as caught:
            self.propose({'auto_assign': True})
        self.assertEqual(caught.exception.code, 'ASSIGNMENT_NO_QUALIFIED_MATCH')

    def test_stale_automatic_preview_never_switches_to_another_person(self):
        """A newly unique candidate is not the person the human reviewed."""
        proposal = self.propose({'auto_assign': True})
        self.plumber.profile.trades.clear()
        self.electrician.profile.trades.add(self.plumbing)
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.confirm(proposal)
        self.work.refresh_from_db()
        self.assertIsNone(self.work.assigned_to_id)

    def test_selected_trade_revocation_is_revalidated_without_client_hash(self):
        """Execution never relies on the client opting into hash verification."""
        proposal = self.propose({'assigned_to': self.plumber.pk})
        self.plumber.profile.trades.clear()
        with self.assertRaises(AssignmentReviewRequired):
            self.confirm(proposal)
        self.work.refresh_from_db()
        self.assertIsNone(self.work.assigned_to_id)

    def test_changed_job_title_requires_a_new_review(self):
        """Qualification rationale and the selected title belong to the preview."""
        proposal = self.propose({'assigned_to': self.plumber.pk})
        self.plumber.profile.position = 'Senior plumbing technician'
        self.plumber.profile.save()
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.confirm(proposal)

    def test_stored_occupational_title_is_hidden_after_permission_revocation(self):
        """Past proposal receipts cannot become an alternate directory endpoint."""
        from aichat.services.staff_assignment import visible_personnel_fields

        proposal = self.propose({'assigned_to': self.plumber.pk})
        preview, _ = visible_personnel_fields(proposal, self.actor)
        self.assertIn('proposed_job_title', preview)
        preview, _ = visible_personnel_fields(proposal, self.electrician)
        self.assertNotIn('proposed_job_title', preview)
        self.assertIn('proposed_job_title', proposal.preview)

    def test_plan_update_cannot_bypass_personnel_assignment_policy(self):
        """The legacy free-text plan field is not an AI assignment escape hatch."""
        with self.assertRaises(proposals.ProposalError):
            self.propose(
                {'fields': {'assignee': self.electrician.username}},
                action='work_order.update',
            )

    def test_scope_revocation_is_revalidated(self):
        """A candidate losing the target scope cannot retain an AI assignment."""
        proposal = self.propose({'assigned_to': self.plumber.pk})
        ClientScopeGrant.objects.filter(user=self.plumber).delete()
        with self.assertRaises(AssignmentReviewRequired):
            self.confirm(proposal)

    def test_explicit_voice_name_is_resolved_only_in_the_permitted_directory(self):
        """Explicit names are resolved within scoped candidates, not all users."""
        from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
        from ai.core.decisions.resolver import WorkOrderIntent

        adapter = ProposalAdapter()
        self.work.reference = 'WO-AI-STAFF'
        self.work.save()
        with patch.object(
            adapter, 'owner_scope', return_value=(self.actor, ('client:test', 'b' * 64))
        ):
            with self.assertRaises(proposals.ProposalError):
                adapter.create(
                    WorkOrderIntent(
                        reference=self.work.reference,
                        reason='Requested',
                        action='work_order.assign',
                        parameters={'assignee_name': self.outsider.username},
                    ),
                    actor=SimpleNamespace(user_pk=str(self.actor.pk)),
                    thread_id='staff-thread',
                    nonce=uuid.uuid4().hex,
                )

    def test_new_repair_never_self_confirms_or_self_overrides_assignment(self):
        """A repair approval is not a confirmation of model-invented trades."""
        self.work.lifecycle_status = 'completed'
        self.work.save(update_fields=['lifecycle_status'])
        proposal = self.propose(
            {
                'machine_id': self.machine.pk,
                'title': 'Investigate leaking pipe',
                'planning': {
                    'assigned_to': self.electrician.pk,
                    'required_trades': ['electrical'],
                    'trade_requirements_confirmed': True,
                    'auto_assign': True,
                    'allow_override': True,
                    'override_reason': 'Model approval',
                },
            },
            action='repair_work_package.create',
        )
        planning = proposal.intent['planning']
        self.assertFalse(planning['trade_requirements_confirmed'])
        self.assertIsNone(planning['assigned_to'])
        self.assertNotIn('allow_override', planning)
        self.assertEqual(
            proposal.preview['assignment_code'], 'ASSIGNMENT_REQUIREMENTS_UNKNOWN'
        )
        confirmed = self.confirm(proposal)
        created = WorkOrder.objects.get(pk=confirmed.receipt['work_order_id'])
        self.assertIsNone(created.assigned_to_id)
        self.assertFalse(created.trade_requirements_confirmed)
        self.assertEqual(
            confirmed.receipt['assignment_code'], 'ASSIGNMENT_REQUIREMENTS_UNKNOWN'
        )

    def test_repair_model_cannot_assign_through_legacy_identity_alias(self):
        """The intake compatibility key is not a model-assignment bypass."""
        self.work.lifecycle_status = 'completed'
        self.work.save(update_fields=['lifecycle_status'])
        proposal = self.propose({
            'machine_id': self.machine.pk,
            'title': 'Investigate thermal indication',
            'planning': {'assigned_to_id': self.electrician.pk},
        }, action='repair_work_package.create')
        self.assertIsNone(proposal.intent['planning'].get('assigned_to_id'))
        self.assertIsNone(proposal.preview['proposed_assigned_to_id'])
        confirmed = self.confirm(proposal)
        created = WorkOrder.objects.get(pk=confirmed.receipt['work_order_id'])
        self.assertIsNone(created.assigned_to_id)

    def test_generic_work_order_create_also_ignores_model_confirmation(self):
        """The older create action cannot assign via a legacy text field either."""
        proposal = self.propose(
            {
                'machine_id': self.machine.pk,
                'title': 'Plan pipe inspection',
                'assignee': self.electrician.username,
                'assigned_to': self.electrician.pk,
                'required_trades': ['electrical'],
                'trade_requirements_confirmed': True,
                'allow_override': True,
                'override_reason': 'Model-approved',
            },
            action='work_order.create',
        )
        self.assertFalse(proposal.intent['trade_requirements_confirmed'])
        self.assertIsNone(proposal.intent['assigned_to'])
        confirmed = self.confirm(proposal)
        created = WorkOrder.objects.get(pk=confirmed.receipt['work_order_id'])
        self.assertIsNone(created.assigned_to_id)
        self.assertFalse(created.assignee)

    def test_legacy_create_only_caller_does_not_need_personnel_grants(self):
        """Unrelated repair creation must not acquire a directory prerequisite."""
        self.work.lifecycle_status = 'completed'
        self.work.save(update_fields=['lifecycle_status'])
        creator = User.objects.create_user(username='ai-create-only', password='pw')
        group = Group.objects.create(name='AI repair creators')
        role = group.rule_sets.get(name='work_order')
        role.can_view = True
        role.can_add = True
        role.save()
        creator.groups.add(group)
        ClientScopeGrant.objects.create(user=creator, client=self.site)
        self.assertFalse(creator.has_perm('users.view_staff_directory'))
        self.assertFalse(creator.has_perm('tasks.assign_workorder'))
        proposal = self.propose(
            {
                'machine_id': self.machine.pk,
                'title': 'Create-only leak inspection',
                'planning': {
                    'assignee': self.electrician.username,
                    'assigned_to': self.electrician.pk,
                    'required_trades': ['electrical'],
                    'trade_requirements_confirmed': True,
                    'auto_assign': True,
                },
            },
            action='repair_work_package.create',
            owner=creator,
        )
        for key in (
            'assigned_to',
            'required_trades',
            'trade_requirements_confirmed',
            'auto_assign',
        ):
            self.assertNotIn(key, proposal.intent['planning'])
        confirmed = proposals.confirm_proposal(
            owner=creator, scope_hash='b' * 64, proposal_id=proposal.pk
        )
        created = WorkOrder.objects.get(pk=confirmed.receipt['work_order_id'])
        self.assertIsNone(created.assigned_to_id)
        self.assertEqual(
            confirmed.receipt['assignment_code'], 'ASSIGNMENT_REQUIREMENTS_UNKNOWN'
        )

    @override_settings(AIMMS_APPROVAL_QUEUE_OWNS_REPAIRS=True)
    def test_approval_queue_preserves_unassigned_review_and_detects_payload_tampering(
        self,
    ):
        """The queue owns execution without becoming a weaker personnel rail."""
        from approvals.executors import RepairWorkPackageExecutor

        proposal = self.propose(
            {
                'machine_id': self.machine.pk,
                'title': 'Plan leak investigation',
                'planning': {
                    'auto_assign': True,
                    'required_trades': ['plumbing'],
                    'trade_requirements_confirmed': True,
                },
            },
            action='repair_work_package.create',
        )
        approval = proposal.approval
        executor = RepairWorkPackageExecutor()
        self.assertFalse(
            executor.check_preconditions(
                approval.payload, approval.baseline_context
            ).has_drift
        )
        with self.assertRaises(proposals.ApprovalOwnsExecution):
            self.confirm(proposal)
        approval.payload['planning']['assigned_to'] = self.plumber.pk
        approval.payload['planning']['trade_requirements_confirmed'] = True
        approval.payload.pop('_staff_proposal_id')
        approval.save()
        self.assertTrue(
            executor.check_preconditions(
                approval.payload, approval.baseline_context
            ).has_drift
        )
        result = executor.execute(approval.payload, uuid.uuid4().hex)
        self.assertFalse(result.success)
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_tool_registration_uses_named_directory_permission(self):
        """Selection and RBAC filtering expose only the dedicated read grant."""
        from ai.core.integrations.staff_tools import search_staff_candidates
        from ai.core.tools.capabilities import capability_catalog, select_capabilities
        from ai.core.tools.rbac import _native_pairs, tool_requirement

        self.assertEqual(
            tool_requirement(search_staff_candidates), ('staff_directory', 'view')
        )
        self.assertIn(('staff_directory', 'view'), _native_pairs(self.actor))
        entry = next(
            entry
            for entry in capability_catalog()
            if entry.tool_id == 'search_staff_candidates'
        )
        self.assertEqual(entry.pack_id, 'staff.read')
        with (
            patch(
                'ai.core.tools.capabilities.category_lexicon', return_value=frozenset()
            ),
            patch(
                'ai.core.tools.capabilities.machine_lexicon', return_value=frozenset()
            ),
        ):
            selected = select_capabilities(
                'Find qualified staff for this repair',
                profile=frozenset({('staff_directory', 'view')}),
                authenticated=True,
            )
            denied = select_capabilities(
                'Find qualified staff for this repair',
                profile=frozenset({('work_order', 'view')}),
                authenticated=True,
            )
        self.assertIn('search_staff_candidates', selected.tool_ids)
        self.assertNotIn('search_staff_candidates', denied.tool_ids)
