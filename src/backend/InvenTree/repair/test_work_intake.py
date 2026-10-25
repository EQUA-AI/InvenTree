"""AI is draft-only; real intake is scoped, attributed, atomic and replay-safe."""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient
from tasks.models import WorkOrder, WorkOrderCommand
from tasks.services.work_orders import IdempotencyConflict, assign_work_order

from assets.health_models import MachineAnomaly, MachineSignalState
from assets.models import Client, ClientScopeGrant
from machine_health.tests.fixtures import HealthEnvMixin
from users.models import RuleSet

from .intake_binding import (
    IntakeTimeoutError,
    IntakeUnavailableError,
    draft_fields,
    provider_status,
)
from .models import RepairPacket
from .work_intake import assignable_staff, intake_context, make_ai_draft
from .work_packages import (
    DuplicateRepairConflict,
    InvalidAnomalySource,
    WorkPackageError,
    create_repair_work_package,
)


class WorkIntakeTest(HealthEnvMixin, TestCase):
    """Use actual native commands and disposable source/people fixtures."""

    def setUp(self):
        """Build isolated native workflow fixtures."""
        self.build_health_env()
        self.set_signal(40.8)
        self.actor = get_user_model().objects.create_superuser(
            username='intake-planner', password='pw'
        )
        self.owner = get_user_model().objects.create_superuser(
            username='intake-owner',
            first_name='Avery',
            last_name='Patel',
            password='pw',
        )
        self.source = MachineAnomaly.objects.create(
            machine=self.machine,
            fingerprint=uuid.uuid4().hex,
            title='TEST winding indication',
            detector='test_alarm_ui',
            severity='critical',
            first_observed_at=timezone.now(),
            last_observed_at=timezone.now(),
            metrics={
                'synthetic': True,
                'test_alarm': True,
                'binding_id': self.binding.pk,
                'test_value': 159.5,
                'unit': '°C',
            },
        )
        self.source.bindings.add(self.binding)
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        cache.clear()

    def draft(self, **overrides):
        """Build a source-bound canonical creation request."""
        result = {
            'machine_id': self.machine.pk,
            'title': 'Investigate thermal indication',
            'source': {'anomaly_id': self.source.pk},
            'planning': {'assigned_to_id': self.owner.pk},
        }
        result.update(overrides)
        return result

    def create(self, draft=None, key=None):
        """Execute the permissioned compound creation command."""
        return create_repair_work_package(
            actor=self.actor,
            draft=draft or self.draft(),
            idempotency_key=key or uuid.uuid4().hex,
        )

    def counts(self):
        """Count every durable aggregate and command effect."""
        return tuple(
            model.objects.count()
            for model in (WorkOrder, RepairPacket, WorkOrderCommand)
        )

    def test_typed_assignment_precedes_packet_with_native_handover(self):
        """Verify typed assignment precedes packet with native handover."""
        result = self.create()
        work = WorkOrder.objects.get(pk=result.work_order_id)
        self.assertEqual(work.assigned_to_id, self.owner.pk)
        self.assertEqual(work.assignee, 'Avery Patel')
        self.assertEqual(work.lifecycle_version, 2)
        self.assertEqual(
            list(work.events.values_list('event_type', flat=True)),
            ['CREATED', 'ASSIGNED', 'WORK_PACKAGE_CREATED'],
        )
        self.assertIsNone(work.handovers.get().accepted_at)
        self.assertEqual(work.repair_packet.pk, result.repair_packet_id)
        self.assertEqual(result.anomaly_id, self.source.pk)
        self.assertEqual(result.machine_id, self.machine.pk)

    def test_packet_owner_change_uses_scoped_audited_assignment(self):
        """Personnel reassignment preserves the packet and requires fresh acceptance."""
        result = self.create()
        assign_work_order(
            work_order_id=result.work_order_id,
            assigned_to=self.actor,
            actor=self.actor,
            expected_version=result.lifecycle_version,
            idempotency_key=uuid.uuid4().hex,
        )
        work = WorkOrder.objects.get(pk=result.work_order_id)
        self.assertEqual(work.assigned_to_id, self.actor.pk)
        self.assertEqual(work.repair_packet.pk, result.repair_packet_id)
        handovers = list(work.handovers.order_by('pk'))
        self.assertEqual(len(handovers), 2)
        self.assertIsNotNone(handovers[0].withdrawn_at)
        self.assertIsNone(handovers[1].accepted_at)

    def test_staff_directory_identity_keeps_native_handover(self):
        """The new directory contract retains the existing ownership acceptance gate."""
        result = self.create(self.draft(planning={'assigned_to': self.owner.pk}))
        work = WorkOrder.objects.get(pk=result.work_order_id)
        self.assertEqual(work.assigned_to_id, self.owner.pk)
        self.assertIsNone(work.handovers.get().accepted_at)
        self.assertEqual(work.lifecycle_version, 2)

    def test_conflicting_assignment_contracts_write_nothing(self):
        """Two identity spellings cannot select different people in one command."""
        before = self.counts()
        with self.assertRaises(WorkPackageError):
            self.create(
                self.draft(
                    planning={
                        'assigned_to_id': self.owner.pk,
                        'assigned_to': self.actor.pk,
                    }
                )
            )
        self.assertEqual(self.counts(), before)

    def test_legacy_typed_owner_cannot_bypass_trade_validation(self):
        """The alarm intake alias cannot evade the scoped staff qualification gate."""
        from tasks.services.staff_assignment import AssignmentError

        before = self.counts()
        with self.assertRaises(AssignmentError):
            self.create(
                self.draft(
                    planning={
                        'assigned_to_id': self.owner.pk,
                        'required_trades': ['electrical'],
                        'trade_requirements_confirmed': True,
                    }
                )
            )
        self.assertEqual(self.counts(), before)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.work_order_id)

    def test_assignment_warning_survives_compound_replay(self):
        """A retry retains unknown qualification evidence instead of claiming success."""
        key = uuid.uuid4().hex
        first = self.create(key=key)
        replay = self.create(key=key)
        self.assertIn('ASSIGNMENT_REQUIREMENTS_UNKNOWN', first.warnings[0])
        self.assertEqual(replay.warnings, first.warnings)

    def test_replay_has_no_secondary_writes_or_new_snapshots(self):
        """Verify replay has no secondary writes or new snapshots."""
        key = uuid.uuid4().hex
        first = self.create(key=key)
        before = self.counts()
        with patch('repair.work_packages._link_anomaly') as link:
            replay = self.create(key=key)
        self.assertEqual(first.work_order_id, replay.work_order_id)
        self.assertTrue(replay.replayed)
        self.assertEqual(before, self.counts())
        link.assert_not_called()

    def test_full_compound_replay_identity(self):
        """Verify full compound replay identity."""
        key = uuid.uuid4().hex
        self.create(key=key)
        before = self.counts()
        changes = [
            {'fault': {'symptom': 'changed'}},
            {'create_repair_packet': False},
            {'planning': {'assigned_to_id': self.actor.pk}},
            {'source': {'anomaly_id': self.source.pk + 1000}},
            {'parts': [{'part_id': 9999, 'quantity': 1}]},
            {'planning': {'assigned_to_id': self.owner.pk, 'estimated_minutes': 12}},
            {
                'duplicate_override': True,
                'duplicate_override_reason': 'different fault',
            },
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(IdempotencyConflict):
                self.create(self.draft(**change), key)
        self.assertEqual(before, self.counts())

    def test_inactive_owner_rolls_back_everything(self):
        """Verify inactive owner rolls back everything."""
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        before = self.counts()
        with self.assertRaises(WorkPackageError):
            self.create()
        self.assertEqual(before, self.counts())

    def test_out_of_scope_owner_is_refused(self):
        """Verify out of scope owner is refused."""
        foreign = Client.objects.create(name='Foreign intake', code='foreign-intake')
        ClientScopeGrant.objects.create(user=self.owner, client=foreign)
        ClientScopeGrant.objects.create(user=self.actor, client=self.client_tenant)
        before = self.counts()
        with (
            override_settings(
                AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
            ),
            self.assertRaises(WorkPackageError),
        ):
            self.create()
        self.assertEqual(before, self.counts())

    def test_creator_cannot_assign_without_native_permission(self):
        """Verify creator cannot assign without native permission."""
        actor = get_user_model().objects.create_user(username='add-only-intake')
        group = Group.objects.create(name='intake-add-only')
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True, 'can_add': True}
        )
        actor.groups.add(group)
        before = self.counts()
        with self.assertRaises(PermissionDenied):
            create_repair_work_package(
                actor=actor, draft=self.draft(), idempotency_key=uuid.uuid4().hex
            )
        self.assertEqual(before, self.counts())

    def test_unknown_source_is_not_a_warning_success(self):
        """Verify unknown source is not a warning success."""
        before = self.counts()
        with self.assertRaises(InvalidAnomalySource):
            self.create(self.draft(source={'anomaly_id': self.source.pk + 1000}))
        self.source.status = 'resolved'
        self.source.save(update_fields=['status'])
        with self.assertRaises(InvalidAnomalySource):
            self.create()
        self.assertEqual(before, self.counts())

    def test_override_cannot_steal_existing_event(self):
        """Verify override cannot steal existing event."""
        first = self.create()
        before = self.counts()
        with self.assertRaises(InvalidAnomalySource):
            self.create(
                self.draft(
                    duplicate_override=True, duplicate_override_reason='Separate issue'
                )
            )
        self.source.refresh_from_db()
        self.assertEqual(self.source.work_order_id, first.work_order_id)
        self.assertEqual(before, self.counts())

    def test_bare_bearing_job_is_visible_but_unchanged(self):
        """Verify bare bearing job is visible but unchanged."""
        bearing = WorkOrder.objects.create(
            machine=self.machine, title='Bearing vibration', assigned_to=self.owner
        )
        before = WorkOrder.objects.filter(pk=bearing.pk).values().get()
        with self.assertRaises(DuplicateRepairConflict) as caught:
            self.create()
        self.assertEqual(caught.exception.duplicates[0]['work_order_id'], bearing.pk)
        self.create(
            self.draft(
                duplicate_override=True,
                duplicate_override_reason='Separate winding concern; bearing job unchanged',
            )
        )
        self.assertEqual(before, WorkOrder.objects.filter(pk=bearing.pk).values().get())

    def test_override_reason_is_required_server_side(self):
        """Verify override reason is required server side."""
        before = self.counts()
        with self.assertRaises(WorkPackageError):
            self.create(self.draft(duplicate_override=True))
        self.assertEqual(before, self.counts())

    def test_directory_is_minimal_and_filters_inactive(self):
        """Verify directory is minimal and filters inactive."""
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        rows = assignable_staff(self.actor, self.machine)
        self.assertNotIn(self.owner.pk, [row['id'] for row in rows])
        for row in rows:
            self.assertEqual(set(row), {'id', 'name'})

    def test_prepared_owner_lookup_returns_only_verified_identity(self):
        """Exact username resolution remains server-side and never exposes usernames."""
        rows = assignable_staff(
            self.actor, self.machine, self.owner.username, exact_username=True
        )
        self.assertEqual(rows, [{'id': self.owner.pk, 'name': 'Avery Patel'}])

    def test_assignee_compatibility_api_requires_directory_grant(self):
        """An assignment permission cannot substitute for the separate directory grant."""
        planner = get_user_model().objects.create_user(
            username='directory-denied-planner'
        )
        group = Group.objects.create(name='Directory denied assignment planners')
        role = group.rule_sets.get(name='work_order')
        role.can_view = True
        role.can_assign_workorder = True
        role.save()
        planner.groups.add(group)
        with self.assertRaises(PermissionDenied):
            assignable_staff(planner, self.machine)

    def test_typed_owner_trade_override_is_explicit_and_audited(self):
        """A supervisor can override a mismatch without accepting responsibility."""
        result = create_repair_work_package(
            actor=self.actor,
            draft=self.draft(
                planning={
                    'assigned_to_id': self.owner.pk,
                    'required_trades': ['electrical'],
                    'trade_requirements_confirmed': True,
                    'override_reason': 'Reviewed supervised exercise assignment',
                }
            ),
            idempotency_key=uuid.uuid4().hex,
            allow_assignment_override=True,
        )
        work = WorkOrder.objects.get(pk=result.work_order_id)
        self.assertEqual(work.required_trades, ['electrical'])
        self.assertIsNone(work.handovers.get().accepted_at)
        decision = work.events.get(event_type='ASSIGNED').metadata['result_metadata']
        self.assertEqual(decision['code'], 'ASSIGNMENT_MANUAL_OVERRIDE')
        self.assertEqual(
            decision['override_reason'], 'Reviewed supervised exercise assignment'
        )

    def test_no_provider_is_truthful_and_writes_nothing(self):
        """Verify no provider is truthful and writes nothing."""
        before = self.counts()
        config = SimpleNamespace(
            azure_openai_endpoint='',
            azure_openai_api_key='',
            aimms_openai_keyless=False,
        )
        with patch('ai.core.config.get_settings', return_value=config):
            self.assertEqual(
                provider_status(),
                {'available': False, 'reason': 'provider_not_configured'},
            )
            with self.assertRaises(IntakeUnavailableError):
                draft_fields({}, actor_id=self.actor.pk)
        self.assertEqual(before, self.counts())

    def ai_draft(self):
        """Generate a signed draft with explicit fixture-only provider output."""
        fields = {
            'title': 'Review winding indication',
            'work_scope': 'Investigate source and competing hypotheses; no physical diagnosis established.',
            'symptom': 'Reported winding concern',
            'production_impact': 'Not established',
        }
        with patch(
            'repair.intake_binding.draft_fields',
            return_value=(fields, {'model': 'test-model', 'run_id': 'fixture-only'}),
        ):
            return make_ai_draft(self.actor, self.machine, self.source.pk)

    def test_draft_alone_creates_nothing_and_review_is_attributed(self):
        """Verify draft alone creates nothing and review is attributed."""
        before = self.counts()
        generated = self.ai_draft()
        self.assertEqual(before, self.counts())
        draft = self.draft(
            title=generated['fields']['title'],
            description='Human edited scope',
            source={
                'anomaly_id': self.source.pk,
                'ai_draft_token': generated['draft_token'],
            },
        )
        result = self.create(draft)
        review = (
            WorkOrder.objects
            .get(pk=result.work_order_id)
            .events.get(event_type='WORK_PACKAGE_CREATED')
            .metadata['result_metadata']['ai_review']
        )
        self.assertEqual(review['reviewed_by'], self.actor.pk)
        self.assertIn('work_scope', review['edited_fields'])

    def test_draft_token_cannot_cross_actor_or_source(self):
        """Verify draft token cannot cross actor or source."""
        generated = self.ai_draft()
        before = self.counts()
        with self.assertRaises(WorkPackageError):
            self.create(
                self.draft(
                    source={
                        'anomaly_id': self.source.pk + 1,
                        'ai_draft_token': generated['draft_token'],
                    }
                )
            )
        self.assertEqual(before, self.counts())

    def test_expired_draft_is_refused_but_committed_retry_still_replays(self):
        """Verify expired draft is refused but committed retry still replays."""
        generated = self.ai_draft()
        draft = self.draft(
            source={
                'anomaly_id': self.source.pk,
                'ai_draft_token': generated['draft_token'],
            }
        )
        with (
            patch('django.core.signing.time.time', return_value=time.time() + 1000),
            self.assertRaises(WorkPackageError),
        ):
            self.create(draft)
        key = uuid.uuid4().hex
        first = self.create(draft, key)
        with patch('django.core.signing.time.time', return_value=time.time() + 1000):
            self.assertEqual(self.create(draft, key).work_order_id, first.work_order_id)

    def test_provider_timeout_is_classified_without_any_work_write(self):
        """The bounded provider timeout is distinguishable from unavailable configuration."""
        client = MagicMock()
        client.with_options.return_value.__enter__.return_value = client
        client.chat.completions.create.side_effect = TimeoutError('Provider deadline')
        before = self.counts()
        with (
            patch(
                'repair.intake_binding.provider_status',
                return_value={'available': True},
            ),
            patch(
                'ai.core.integrations.azure_openai_client.build_openai_client',
                return_value=client,
            ),
            self.assertRaises(IntakeTimeoutError),
        ):
            draft_fields({}, actor_id=self.actor.pk)
        self.assertEqual(before, self.counts())
        with patch(
            'repair.work_intake_api.make_ai_draft',
            side_effect=IntakeTimeoutError('Provider deadline'),
        ):
            response = self.api.post(
                '/api/maintenance/work-packages/intake/draft/',
                {'machine_id': self.machine.pk, 'anomaly_id': self.source.pk},
                format='json',
            )
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data['code'], 'AI_INTAKE_TIMEOUT')
        self.assertEqual(before, self.counts())

    def test_provider_response_is_allowlisted_and_bounded(self):
        """Verify provider response is allowlisted and bounded."""
        generated = self.ai_draft()
        client = MagicMock()
        client.with_options.return_value.__enter__.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason='stop',
                    message=SimpleNamespace(
                        content=json.dumps(generated['fields']), refusal=None
                    ),
                )
            ],
            model='fixture-provider',
            id='fixture-run',
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=20, total_tokens=30
            ),
        )
        before = list(MachineSignalState.objects.values())
        with (
            patch(
                'repair.intake_binding.provider_status',
                return_value={'available': True},
            ),
            patch(
                'ai.core.integrations.azure_openai_client.build_openai_client',
                return_value=client,
            ),
        ):
            context = intake_context(self.actor, self.machine, self.source.pk)[
                'context'
            ]
            fields, provenance = draft_fields(context, actor_id=self.actor.pk)
        client.with_options.assert_called_once_with(timeout=20.0, max_retries=0)
        self.assertEqual(provenance['run_id'], 'fixture-run')
        self.assertEqual(fields, generated['fields'])
        serialized = json.loads(
            client.chat.completions.create.call_args.kwargs['messages'][-1]['content']
        )
        self.assertIsInstance(serialized['source']['measurement_observed_at'], str)
        self.assertIsInstance(serialized['reference_channels'][0]['observed_at'], str)
        self.assertEqual(before, list(MachineSignalState.objects.values()))

    def test_extra_model_fields_fail_closed(self):
        """Verify extra model fields fail closed."""
        client = MagicMock()
        client.with_options.return_value.__enter__.return_value = client
        client.chat.completions.create.return_value.choices = [
            SimpleNamespace(
                finish_reason='stop',
                message=SimpleNamespace(
                    content=json.dumps({'assigned_to_id': self.owner.pk}), refusal=None
                ),
            )
        ]
        with (
            patch(
                'repair.intake_binding.provider_status',
                return_value={'available': True},
            ),
            patch(
                'ai.core.integrations.azure_openai_client.build_openai_client',
                return_value=client,
            ),
            self.assertRaises(IntakeUnavailableError),
        ):
            draft_fields({}, actor_id=self.actor.pk)

    def test_nonfinite_context_fails_closed_before_provider_request(self):
        """Reject invalid source values without a provider call or partial draft."""
        with (
            patch(
                'repair.intake_binding.provider_status',
                return_value={'available': True},
            ),
            patch(
                'ai.core.integrations.azure_openai_client.build_openai_client'
            ) as build,
            self.assertRaises(IntakeUnavailableError),
        ):
            draft_fields({'measured_value': float('nan')}, actor_id=self.actor.pk)
        build.assert_not_called()

    def test_http_intake_checks_real_source_and_malformed_create_is_400(self):
        """Verify http intake checks real source and malformed create is 400."""
        response = self.api.get(
            '/api/maintenance/work-packages/intake/',
            {'machine_id': self.machine.pk, 'anomaly_id': self.source.pk},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['context']['source']['test_value'], 159.5)
        self.assertEqual(response.data['context']['source']['measured_value'], 40.8)
        self.assertIsNone(response.data['prepared_scenario'])
        self.set_signal(40.8, quality='bad')
        untrusted = self.api.get(
            '/api/maintenance/work-packages/intake/',
            {'machine_id': self.machine.pk, 'anomaly_id': self.source.pk},
        )
        self.assertIsNone(untrusted.data['context']['source']['measured_value'])
        self.assertEqual(
            untrusted.data['context']['source']['measurement_quality'], 'bad'
        )
        for payload in [[], {'idempotency_key': {}}, {'idempotency_key': ''}]:
            self.assertEqual(
                self.api.post(
                    '/api/maintenance/work-packages/create/', payload, format='json'
                ).status_code,
                400,
            )


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locking')
class WorkIntakeConcurrencyTest(HealthEnvMixin, TransactionTestCase):
    """Concurrent commands serialize on the native machine ownership lock."""

    def setUp(self):
        """Create an isolated source and real scope for independent connections."""
        self.build_health_env()
        self.actor = get_user_model().objects.create_superuser(
            username='concurrent-intake'
        )
        self.anomaly = MachineAnomaly.objects.create(
            machine=self.machine,
            fingerprint=uuid.uuid4().hex,
            title='Concurrent TEST source',
            detector='test_alarm_ui',
            severity='critical',
            first_observed_at=timezone.now(),
            last_observed_at=timezone.now(),
            metrics={'synthetic': True, 'binding_id': self.binding.pk},
        )
        self.anomaly.bindings.add(self.binding)

    def race(self, keys, *, override=False):
        """Start two native commands on separate committed database connections."""
        barrier = Barrier(2)
        draft = {
            'machine_id': self.machine.pk,
            'title': 'Concurrent intake',
            'source': {'anomaly_id': self.anomaly.pk},
            'duplicate_override': override,
            'duplicate_override_reason': 'Independent exercise race'
            if override
            else '',
        }

        def create(key):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=20)
                try:
                    return create_repair_work_package(
                        actor=actor, draft=draft, idempotency_key=key
                    )
                except (DuplicateRepairConflict, InvalidAnomalySource) as exc:
                    return exc
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(create, keys))

    def assert_one_owner(self):
        """Require one complete aggregate and its exclusive source ownership."""
        self.assertEqual(RepairPacket.objects.count(), 1)
        work = WorkOrder.objects.get()
        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.work_order_id, work.pk)
        self.assertEqual(self.anomaly.repair_packet_id, work.repair_packet.pk)
        self.assertEqual(
            work.events.filter(event_type='WORK_PACKAGE_CREATED').count(), 1
        )

    def test_same_key_returns_one_aggregate_and_one_replay(self):
        """An acknowledgement race returns the original committed receipt."""
        key = uuid.uuid4().hex
        results = self.race([key, key])
        self.assertEqual(results[0].work_order_id, results[1].work_order_id)
        self.assertEqual(sum(result.replayed for result in results), 1)
        self.assert_one_owner()

    def test_distinct_keys_require_duplicate_review(self):
        """Different requests cannot bypass duplicate detection concurrently."""
        results = self.race([uuid.uuid4().hex, uuid.uuid4().hex])
        self.assertEqual(
            sum(isinstance(result, DuplicateRepairConflict) for result in results), 1
        )
        self.assert_one_owner()

    def test_override_never_steals_an_owned_anomaly(self):
        """A deliberate duplicate still cannot take another Work's source."""
        results = self.race([uuid.uuid4().hex, uuid.uuid4().hex], override=True)
        self.assertEqual(
            sum(isinstance(result, InvalidAnomalySource) for result in results), 1
        )
        self.assert_one_owner()
