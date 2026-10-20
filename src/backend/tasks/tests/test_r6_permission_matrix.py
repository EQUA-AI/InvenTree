"""P32: real-session permission matrix for the R6 projections and commands.

The table distinguishes additive API flags from legacy reads. Canonical reads
use the native role-aware scope resolver; Closeout and legacy projection views
also require Work view explicitly (403). Media denies a missing role as 404. No superuser or mocked
authorization is used. Each generated method is a separately counted case.
"""

import hashlib
from dataclasses import dataclass
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from aichat.models import (
    AttachmentIngest,
    AttachmentIngestPipeline,
    AttachmentIngestState,
)
from aichat.services.work_focus import (
    WorkFocusError,
    authorized_focus,
    issue_receipt,
    resolve_receipt,
)
from assets.health_models import (
    HealthEvidenceSnapshot,
    HealthSource,
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
)
from assets.models import AssetMachine, Client, ClientScopeGrant
from assets.registry_models import AssetComponent
from common.models import Attachment, InvenTreeSetting
from company.models import Company
from part.models import Part
from part.verification.scope import VerificationScope, VerificationScopeError
from part.verification.scope import scope_for_actor as verification_scopes
from part.verification_models import (
    PartVerificationPolicyVersion,
    PartVerificationSession,
)
from repair.models import RepairPacket, RepairPacketGate
from stock.models import StockItem
from tasks.closeout_models import (
    CloseoutCapture,
    CloseoutCaptureRevision,
    CloseoutProposal,
)
from tasks.investigation_models import InvestigationRequest
from tasks.jobkit_models import JobKit, JobKitAllocation, JobKitLine
from tasks.models import WorkOrder, WorkOrderCommand, WorkOrderEvent
from tasks.scope import (
    MaintenanceScope,
    ScopeError,
    client_codes_for_actor,
    machine_scope_filter,
    maintenance_record_scope_filter,
    require_work_order_scope,
    scope_for_actor,
)
from users.models import RuleSet


@dataclass(frozen=True)
class ReadCase:
    """One public read, including its independently addressable owner."""

    name: str
    path: str
    flag: str | None = None
    no_role: int = 403
    collection: bool = False


READS = (
    ReadCase('positions', '/api/assets/machines/{machine}/positions/'),
    ReadCase('work_summary', '/api/assets/machines/{machine}/work-summary/'),
    ReadCase(
        'occurrences', '/api/assets/machines/{machine}/fault-history/occurrences/'
    ),
    ReadCase(
        'occurrence',
        '/api/assets/machines/{machine}/fault-history/occurrences/{anomaly}/',
    ),
    ReadCase('components', '/api/assets/registry/{machine}/components/'),
    ReadCase('location_machines', '/api/assets/locations/machines/', collection=True),
    ReadCase('health', '/api/machine-health/machines/{machine}/health/'),
    ReadCase('signals', '/api/machine-health/machines/{machine}/health/signals/'),
    ReadCase('snapshots', '/api/machine-health/machines/{machine}/health/snapshots/'),
    ReadCase(
        'retained_snapshot',
        '/api/machine-health/machines/{machine}/health/snapshots/?snapshot={snapshot}',
    ),
    ReadCase(
        'trend',
        '/api/machine-health/machines/{machine}/health/trend/?binding={binding}',
    ),
    ReadCase(
        'trends',
        '/api/machine-health/machines/{machine}/health/trends/?bindings={binding}',
    ),
    ReadCase(
        'series',
        '/api/machine-health/machines/{machine}/health/series/?bindings={binding}',
    ),
    ReadCase('work_list', '/api/kanban/work-orders/list/', collection=True),
    ReadCase('persona', '/api/kanban/work-orders/persona/', collection=True),
    ReadCase('overview', '/api/kanban/work-orders/{work}/overview/'),
    ReadCase('requests', '/api/kanban/work-orders/{work}/investigation-requests/'),
    ReadCase(
        'request', '/api/kanban/work-orders/{work}/investigation-requests/{request}/'
    ),
    ReadCase(
        'materials',
        '/api/tasks/work-orders/{work}/materials/',
        'AIMMS_WORK_ORDERS_ENABLED',
    ),
    ReadCase(
        'captures',
        '/api/tasks/work-orders/{work}/closeout/captures/',
        'AIMMS_CLOSEOUT_WIZARD_ENABLED',
        403,
    ),
    ReadCase(
        'proposal',
        '/api/tasks/work-orders/{work}/closeout/captures/{capture}/proposal/',
        'AIMMS_CLOSEOUT_WIZARD_ENABLED',
        403,
    ),
    ReadCase(
        'readings',
        '/api/tasks/work-orders/{work}/closeout/readings/',
        'AIMMS_CLOSEOUT_WIZARD_ENABLED',
        403,
    ),
    ReadCase(
        'amendments',
        '/api/tasks/work-orders/{work}/closeout/amendments/',
        'AIMMS_CLOSEOUT_WIZARD_ENABLED',
        403,
    ),
    ReadCase(
        'media_metadata',
        '/api/aichat/evidence/media/{attachment}/metadata/',
        no_role=404,
    ),
    ReadCase(
        'media_bytes',
        '/api/aichat/evidence/media/{attachment}/?revision={revision}',
        no_role=404,
    ),
)


def independent_scope(actor):
    """A supported resolver whose scope grant does not itself imply a role."""
    return {
        MaintenanceScope(customer_id=None, site_key=None, client_id=pk)
        for pk in ClientScopeGrant.objects.filter(user=actor).values_list(
            'client_id', flat=True
        )
    }


def sales_scope(actor):
    """An explicit customer resolver, independent of the asset's client."""
    return {
        MaintenanceScope(
            customer_id=Company.objects.get(name='P32 sales owner').pk, site_key=None
        )
    }


def ambiguous_scope(actor):
    """An invalid resolver value must not widen either canonical boundary."""
    return {
        MaintenanceScope(
            customer_id=Company.objects.get(name='P32 sales owner').pk,
            client_id=Client.objects.get(code='p32').pk,
            site_key=None,
        )
    }


def unresolved_scope(actor):
    """An explicitly unresolved actor, without the single-site fallback."""
    return []


@override_settings(
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_CLOSEOUT_WIZARD_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
)
class R6PermissionMatrix(TestCase):
    """Persisted roles, grants and fresh Django sessions on disposable SQLite."""

    @classmethod
    def setUpTestData(cls):
        """Populate every read branch with synthetic records, including media."""
        InvenTreeSetting.build_default_values()
        InvenTreeSetting.get_setting('_PENDING_MIGRATIONS')
        cls.group = Group.objects.create(name='P32 readers')
        RuleSet.objects.update_or_create(
            group=cls.group, name='work_order', defaults={'can_view': True}
        )
        cls.reader = get_user_model().objects.create_user(username='p32-reader')
        cls.reader.groups.add(cls.group)
        cls.no_role = get_user_model().objects.create_user(username='p32-no-role')
        cls.outside = get_user_model().objects.create_user(username='p32-outside')
        cls.outside.groups.add(cls.group)
        cls.customer_reader = get_user_model().objects.create_user(
            username='p32-customer'
        )
        cls.customer_reader.groups.add(cls.group)
        cls.tenant = Client.objects.create(code='p32', name='P32 scoped tenant')
        other = Client.objects.create(code='p32-other', name='P32 outside tenant')
        for user in (cls.reader, cls.no_role):
            ClientScopeGrant.objects.create(user=user, client=cls.tenant)
        ClientScopeGrant.objects.create(user=cls.outside, client=other)
        cls.customer = Company.objects.create(name='P32 sales owner', is_customer=True)
        cls.owners = {}
        now = timezone.now()
        source = HealthSource.objects.create(
            name='P32 disabled source',
            source_type='scada',
            connector_type='webhook',
            active=False,
        )
        part = Part.objects.create(name='P32 motor', IPN='PS-MOTOR', component=True)
        StockItem.objects.create(part=part, quantity=7)
        for name, client, customer in (
            ('own', cls.tenant, None),
            ('foreign', other, None),
            ('customer', cls.tenant, cls.customer),
            ('unresolved', None, None),
        ):
            machine = AssetMachine.objects.create(
                name=f'P32 {name} machine', client=client
            )
            work = WorkOrder.objects.create(
                title=f'P32 {name} work',
                machine=machine,
                customer=customer,
                lifecycle_status='in_progress',
                assigned_to=cls.reader,
                affected_component_ref='PS-MOTOR:1',
            )
            AssetComponent.objects.create(
                machine=machine, part=part, code='PS-MOTOR:1', name=f'P32 {name} motor'
            )
            binding = MachineSignalBinding.objects.create(
                machine=machine,
                source=source,
                external_key=name,
                display_name=f'P32 {name} signal',
            )
            MachineSignalState.objects.create(
                binding=binding,
                value={'value': 7},
                observed_at=now,
                received_at=now,
                quality='good',
            )
            anomaly = MachineAnomaly.objects.create(
                machine=machine,
                source=source,
                fingerprint=name,
                work_order=work,
                title=f'P32 {name} anomaly',
                first_observed_at=now,
                last_observed_at=now,
            )
            anomaly.bindings.add(binding)
            snapshot = HealthEvidenceSnapshot.objects.create(
                machine=machine,
                source=source,
                binding=binding,
                window_start=now,
                window_end=now,
                statistics={'latest': 7},
                content_hash='a' * 64,
                anomaly=anomaly,
            )
            request = InvestigationRequest.objects.create(
                work_order=work,
                purpose=f'P32 {name} decision',
                next_check='Recorded check',
                created_by=cls.reader,
                updated_by=cls.reader,
            )
            capture = CloseoutCapture.objects.create(
                work_order=work, created_by=cls.reader
            )
            revision = CloseoutCaptureRevision.objects.create(
                capture=capture,
                revision=1,
                narrative=f'P32 {name} retained narrative',
                source_content_hash='b' * 64,
                work_order_version=1,
                created_by=cls.reader,
            )
            capture.current_revision = revision
            capture.save(update_fields=['current_revision'])
            CloseoutProposal.objects.create(
                capture_revision=revision,
                schema_version=1,
                extractor='manual',
                fields={},
                content_hash='c' * 64,
            )
            body = f'P32 {name} indexed bytes'.encode()
            with mock.patch('InvenTree.tasks.offload_task', return_value=True):
                attachment = Attachment.objects.create(
                    model_type='workorder',
                    model_id=work.pk,
                    attachment=SimpleUploadedFile(f'p32-{name}.png', body),
                )
            digest = hashlib.sha256(body).hexdigest()
            AttachmentIngest.objects.create(
                attachment_id=attachment.pk,
                model_type='workorder',
                model_id=work.pk,
                source_sha256=digest,
                pipeline=AttachmentIngestPipeline.IMAGE,
                state=AttachmentIngestState.INDEXED,
            )
            cls.owners[name] = {
                'machine': machine.pk,
                'work': work.pk,
                'binding': binding.pk,
                'anomaly': anomaly.pk,
                'snapshot': snapshot.pk,
                'request': request.pk,
                'capture': capture.pk,
                'attachment': attachment.pk,
                'revision': digest,
            }

    def setUp(self):
        """Authenticate before observing application SQL (session writes excluded)."""
        self.api = APIClient()
        self.api.force_login(self.reader)

    def read_only(self, execute, sql, params, many, context):
        """Reject writes, including updates that would leave row counts unchanged."""
        self.assertNotRegex(
            sql.lstrip().upper(),
            r'^(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b',
            sql,
        )
        return execute(sql, params, many, context)

    def get_case(self, case, owner='own'):
        """Read with no model/service/provider mocks; consume streamed originals."""
        with connection.execute_wrapper(self.read_only):
            response = self.api.get(case.path.format(**self.owners[owner]))
            if getattr(response, 'streaming', False):
                b''.join(response.streaming_content)
        return response

    def grant(self, *permissions):
        """Grant native Django command permission(s), independently of CRUD."""
        self.reader.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='tasks', codename__in=permissions
            )
        )

    def test_materials_requires_view_even_when_scope_resolver_is_independent(self):
        """An explicit client grant alone must not disclose a new Work projection."""
        self.api.force_login(self.no_role)
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.independent_scope'
        ):
            self.assertEqual(self.get_case(READS[18]).status_code, 403)

    def test_nested_record_ids_cannot_borrow_an_authorized_parent(self):
        """Exact retained/request/capture ids remain bound to their own owner."""
        own, foreign = self.owners['own'], self.owners['foreign']
        for pattern in (READS[3].path, READS[9].path, READS[17].path, READS[20].path):
            ids = {
                **own,
                **{
                    key: foreign[key]
                    for key in ('anomaly', 'snapshot', 'request', 'capture')
                },
            }
            with (
                self.subTest(pattern=pattern),
                connection.execute_wrapper(self.read_only),
            ):
                self.assertEqual(self.api.get(pattern.format(**ids)).status_code, 404)

    def test_sales_scope_authorizes_only_its_work_not_the_asset(self):
        """Sales and software tenants are separate identities, in both directions."""
        self.api.force_login(self.customer_reader)
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.sales_scope'
        ):
            for case in (READS[15], READS[16], READS[18], READS[19]):
                with self.subTest(case=case.name):
                    self.assertEqual(self.get_case(case, 'customer').status_code, 200)
                    self.assertEqual(self.get_case(case, 'own').status_code, 404)
            self.assertEqual(self.get_case(READS[0], 'customer').status_code, 404)

    def test_closeout_history_requires_view_with_an_independent_scope(self):
        """Scope alone cannot disclose R6 narratives, names or retained history."""
        self.api.force_login(self.no_role)
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.independent_scope'
        ):
            for case in READS[19:23]:
                with self.subTest(case=case.name):
                    self.assertEqual(self.get_case(case).status_code, 403)

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.ambiguous_scope'
    )
    def test_ambiguous_scope_cannot_disclose_customer_work(self):
        """Queryset reads must agree with the existing exact-record denial."""
        work = WorkOrder.objects.get(pk=self.owners['customer']['work'])
        with self.assertRaises(ScopeError):
            require_work_order_scope(self.reader, work)
        for case in (READS[15], READS[16], READS[18], READS[19]):
            with self.subTest(case=case.name):
                self.assertEqual(self.get_case(case, 'customer').status_code, 404)
        response = self.get_case(READS[13])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])
        with self.assertRaises(WorkFocusError):
            authorized_focus(self.reader.pk, work.pk)

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.ambiguous_scope'
    )
    def test_ambiguous_scope_cannot_disclose_machine_collections_or_rag(self):
        """Invalid combined identity grants no machine, history or search codes."""
        for resolver in (
            machine_scope_filter,
            maintenance_record_scope_filter,
            client_codes_for_actor,
        ):
            with (
                self.subTest(resolver=resolver.__name__),
                self.assertRaises(ScopeError),
            ):
                resolver(self.reader)

    def test_ambiguous_resolver_shapes_and_valid_neighbor_fail_closed(self):
        """Malformed values stay invalid even beside an otherwise valid grant."""
        mixed = next(iter(ambiguous_scope(self.reader)))
        valid = next(iter(independent_scope(self.reader)))
        for shape in (
            mixed,
            {
                'customer_id': mixed.customer_id,
                'client_id': mixed.client_id,
                'site_key': None,
            },
            (mixed.customer_id, None, mixed.client_id),
        ):
            for values in ([shape], [shape, valid]):
                with (
                    self.subTest(values=values),
                    override_settings(
                        AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor, values=values: (
                            values
                        )
                    ),
                    self.assertRaises(ScopeError),
                ):
                    scope_for_actor(self.reader)

    def test_separate_customer_and_client_grants_remain_authorized(self):
        """Two explicit valid boundaries retain both existing access paths."""
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: (
                sales_scope(actor) | independent_scope(actor)
            )
        ):
            for owner in ('own', 'customer'):
                self.assertEqual(self.get_case(READS[15], owner).status_code, 200)
                self.assertEqual(self.get_case(READS[18], owner).status_code, 200)
            self.assertEqual(self.get_case(READS[0]).status_code, 200)
            self.assertEqual(client_codes_for_actor(self.reader), frozenset({'p32'}))

    def test_each_closeout_parent_flag_hides_the_read_surfaces(self):
        """Disabling either native parent gate is enough; both are required."""
        for flag in ('AIMMS_WORK_ORDERS_ENABLED', 'AIMMS_CLOSEOUT_WIZARD_ENABLED'):
            with override_settings(**{flag: False}):
                for case in READS[19:23]:
                    with self.subTest(flag=flag, case=case.name):
                        self.assertEqual(self.get_case(case).status_code, 404)

    def test_procedure_projection_and_jobkit_source_flags_are_independent(self):
        """An unavailable provider stays labelled; its absence is not no work."""
        for enabled in (True, False):
            with override_settings(AIMMS_PROCEDURES_ENABLED=enabled):
                package = self.get_case(READS[15]).data['prepared_package']
                self.assertEqual(
                    package['procedures_state'], 'read' if enabled else 'not_enabled'
                )
                self.assertFalse(package['can_record_deviation'])
            with override_settings(AIMMS_JOB_KITS_ENABLED=enabled):
                materials = self.get_case(READS[18]).data
                self.assertEqual(materials['kits_enabled'], enabled)
                self.assertEqual(materials['source'], 'requirements')

    def test_investigation_replay_reauthorizes_current_role_and_scope(self):
        """A saved approval cannot keep writing after native authority is lost."""
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True
        )
        path = READS[16].path.format(**self.owners['own'])
        body = {
            'operation': 'create',
            'fields': {
                'purpose': 'P32 replay decision',
                'next_check': 'P32 replay check',
                'required_artifacts': [],
            },
            'expected_version': 1,
            'idempotency_key': 'p32-revocable',  # gitleaks:allow
        }
        self.assertEqual(self.api.post(path, body, format='json').status_code, 200)
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=False
        )
        with connection.execute_wrapper(self.read_only):
            self.assertEqual(self.api.post(path, body, format='json').status_code, 403)
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True
        )
        ClientScopeGrant.objects.filter(user=self.reader).delete()
        with connection.execute_wrapper(self.read_only):
            self.assertEqual(self.api.post(path, body, format='json').status_code, 404)

    def test_planning_rejects_invalid_intent_keys_before_any_write(self):
        """Blank, mistyped and oversized keys cannot bypass the stable-key rule."""
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True
        )
        path = f'/api/kanban/work-orders/{self.owners["own"]["work"]}/commands/update/'
        for key in ('', ' ', 123, 'x' * 129):
            with self.subTest(key=key), connection.execute_wrapper(self.read_only):
                response = self.api.post(
                    path,
                    {
                        'fields': {'title': 'P32 invalid-key mutation'},
                        'expected_version': 1,
                        'idempotency_key': key,
                    },
                    format='json',
                )
                self.assertEqual(response.status_code, 400)

    def test_repair_packet_reads_and_actions_keep_native_roles_and_scope(self):
        """Every changed packet adapter authorizes before reading or dispatching."""
        packet = RepairPacket.objects.create(
            work_order_id=self.owners['own']['work'],
            machine_id=self.owners['own']['machine'],
        )
        gate = RepairPacketGate.objects.create(
            packet=packet, gate_type='loto', name='P32 real gate'
        )
        reads = ('', 'generation-status/', 'gates/', 'findings/', 'approved-scope/')
        actions = (
            'generate/',
            'verify-diagnosis/',
            'resolve-gates/',
            f'gates/{gate.pk}/confirm/',
            f'gates/{gate.pk}/verify/',
            f'gates/{gate.pk}/waive/',
            f'gates/{gate.pk}/proofs/',
            f'gates/{gate.pk}/lockout/',
            'advance/',
            'close/',
            'start/',
            'findings/',
            'approved-scope/',
            'cancel/',
        )
        base = f'/api/repair/packets/{packet.pk}/'
        for actor, expected in (
            (None, 401),
            (self.no_role, 403),
            (self.reader, 200),
            (self.outside, 404),
        ):
            self.api.logout() if actor is None else self.api.force_login(actor)
            for suffix in reads:
                with (
                    self.subTest(actor=getattr(actor, 'pk', None), read=suffix),
                    connection.execute_wrapper(self.read_only),
                ):
                    self.assertEqual(self.api.get(base + suffix).status_code, expected)
            for suffix in actions:
                with (
                    self.subTest(actor=getattr(actor, 'pk', None), action=suffix),
                    connection.execute_wrapper(self.read_only),
                ):
                    self.assertEqual(
                        self.api.post(base + suffix, {}, format='json').status_code,
                        401 if actor is None else 403,
                    )
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True
        )
        self.api.force_login(self.outside)
        for suffix in actions:
            with (
                self.subTest(action=suffix),
                connection.execute_wrapper(self.read_only),
            ):
                self.assertEqual(
                    self.api.post(base + suffix, {}, format='json').status_code, 404
                )

    def test_finding_foreign_snapshot_matches_missing_snapshot_denial(self):
        """An authorized finding writer cannot probe another machine's evidence."""
        packet = RepairPacket.objects.create(
            work_order_id=self.owners['own']['work'],
            machine_id=self.owners['own']['machine'],
        )
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True
        )
        for snapshot in (self.owners['foreign']['snapshot'], uuid4()):
            with (
                self.subTest(snapshot=snapshot),
                connection.execute_wrapper(self.read_only),
            ):
                response = self.api.post(
                    f'/api/repair/packets/{packet.pk}/findings/',
                    {
                        'finding_key': 'p32-finding',
                        'observation': 'P32 scoped observation',
                        'snapshot_id': str(snapshot),
                    },
                    format='json',
                )
                self.assertEqual(response.status_code, 404)

        allowed = self.api.post(
            f'/api/repair/packets/{packet.pk}/findings/',
            {
                'finding_key': 'p32-finding',
                'observation': 'P32 scoped observation',
                'snapshot_id': str(self.owners['own']['snapshot']),
            },
            format='json',
        )
        self.assertEqual(allowed.status_code, 201)

    def test_stock_and_command_projections_do_not_inherit_view_authority(self):
        """Stock is hidden; execute and source flags control their own branches."""
        own = self.owners['own']
        parts = self.get_case(READS[4]).data['results']
        self.assertIsNone(parts[0]['stock'])
        overview = self.get_case(READS[15]).data
        self.assertFalse(overview['work_continuity']['commands']['hold'])
        self.assertFalse(overview['work_continuity']['commands']['resume'])
        self.grant('execute_workorder')
        self.assertTrue(
            self.get_case(READS[15]).data['work_continuity']['commands']['hold']
        )
        with override_settings(
            AIMMS_WORK_ORDERS_ENABLED=False,
            AIMMS_CLOSEOUT_WIZARD_ENABLED=False,
            AIMMS_CLOSEOUT_EFFECTS_ENABLED=False,
        ):
            value = self.get_case(READS[15]).data
            self.assertFalse(value['work_continuity']['commands']['hold'])
            self.assertFalse(value['canonical_commands_enabled'])
            self.assertFalse(
                any(
                    row['state'] == 'read'
                    for row in value['handled_actions']['sources']
                    if row['key'] in ('closeout_drafting', 'closeout_follow_up')
                )
            )
        self.assertEqual(
            WorkOrderCommand.objects.filter(work_order_id=own['work']).count(), 0
        )

    def test_stock_permission_is_an_independent_positive_control(self):
        """An always-null stock implementation cannot pass the redaction test."""
        RuleSet.objects.update_or_create(
            group=self.group, name='stock', defaults={'can_view': True}
        )
        self.assertEqual(
            self.get_case(READS[4]).data['results'][0]['stock'], {'quantity': 7.0}
        )

    def test_disabled_source_is_not_reported_as_connected(self):
        """Saved signal/evidence values survive without claiming source success."""
        MachineAnomaly.objects.filter(machine_id=self.owners['own']['machine']).update(
            status='resolved'
        )
        MachineSignalBinding.objects.filter(pk=self.owners['own']['binding']).update(
            normal_max=10, warn_max=20, critical_max=30
        )
        HealthSource.objects.update(active=True)
        self.assertEqual(self.get_case(READS[6]).data['state'], 'normal')
        HealthSource.objects.update(active=False)
        health = self.get_case(READS[6]).data
        self.assertFalse(health['sources'][0]['healthy'])
        self.assertFalse(health['sources'][0]['active'])
        # Intake state does not invalidate an authorized retained observation.
        self.assertEqual(health['state'], 'normal')
        self.assertEqual(self.get_case(READS[7]).data['results'][0]['value'], 7)

    def test_focus_receipt_reauthorizes_actor_role_scope_and_version(self):
        """Chat focus is a signed identity, never a lasting Work capability."""
        work = self.owners['own']['work']
        with connection.execute_wrapper(self.read_only):
            issued = issue_receipt(self.reader.pk, 'p32-policy', 'p32-thread', work, 1)
            self.assertEqual(
                resolve_receipt(
                    self.reader.pk, 'p32-policy', 'p32-thread', issued['receipt']
                )['work_order_id'],
                work,
            )
            for actor, target in (
                (self.no_role, work),
                (self.outside, work),
                (self.reader, self.owners['customer']['work']),
                (self.reader, self.owners['unresolved']['work']),
            ):
                with (
                    self.subTest(actor=actor.pk, work=target),
                    self.assertRaises(WorkFocusError),
                ):
                    authorized_focus(actor.pk, target)
            for actor, policy, thread in (
                (self.outside.pk, 'p32-policy', 'p32-thread'),
                (self.reader.pk, 'changed', 'p32-thread'),
                (self.reader.pk, 'p32-policy', 'other'),
            ):
                with self.subTest(actor=actor), self.assertRaises(WorkFocusError):
                    resolve_receipt(actor, policy, thread, issued['receipt'])
        WorkOrder.objects.filter(pk=work).update(lifecycle_version=2)
        with self.assertRaises(WorkFocusError):
            resolve_receipt(
                self.reader.pk, 'p32-policy', 'p32-thread', issued['receipt']
            )
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_view=False
        )
        with self.assertRaises(WorkFocusError):
            authorized_focus(self.reader.pk, work)

    def test_followup_packet_candidate_machine_is_bound_before_validation(self):
        """Foreign and nonexistent machine ids are equally hidden on POST/PATCH."""
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True, can_add=True
        )
        own, foreign = self.owners['own'], self.owners['foreign']
        packet = RepairPacket.objects.create(
            work_order_id=own['work'], machine_id=own['machine']
        )
        for method, url in (
            ('patch', f'/api/repair/packets/{packet.pk}/'),
            ('post', '/api/repair/packets/'),
        ):
            for machine in (foreign['machine'], 999999):
                with (
                    self.subTest(method=method, machine=machine),
                    connection.execute_wrapper(self.read_only),
                ):
                    response = getattr(self.api, method)(
                        url,
                        {'work_order': own['work'], 'machine': machine},
                        format='json',
                    )
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn('P32 foreign machine', str(response.data))
        allowed = self.api.patch(
            f'/api/repair/packets/{packet.pk}/',
            {'machine': own['machine']},
            format='json',
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        new_work = WorkOrder.objects.create(
            title='P32 additional own work', machine_id=own['machine']
        )
        created = self.api.post(
            '/api/repair/packets/',
            {'work_order': new_work.pk, 'machine': own['machine']},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['machine_name'], 'P32 own machine')

    def test_followup_corrupt_linked_packet_cannot_authorize_foreign_machine_or_evidence(
        self,
    ):
        """Pre-existing mismatched rows cannot widen reads or snapshot ownership."""
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True
        )
        own, foreign = self.owners['own'], self.owners['foreign']
        packet = RepairPacket.objects.create(
            work_order_id=own['work'], machine_id=foreign['machine']
        )
        with connection.execute_wrapper(self.read_only):
            self.assertEqual(
                self.api.get(f'/api/repair/packets/{packet.pk}/').status_code, 404
            )
            self.assertEqual(
                self.api.get(
                    f'/api/repair/machines/{foreign["machine"]}/open-repairs/'
                ).status_code,
                404,
            )
            response = self.api.post(
                f'/api/repair/packets/{packet.pk}/findings/',
                {
                    'finding_key': 'p32-corrupt-packet',
                    'observation': 'Synthetic observation',
                    'snapshot_id': str(foreign['snapshot']),
                },
                format='json',
            )
            self.assertEqual(response.status_code, 404)

    def make_job_kit(self):
        """A real reservation with location and tracking identity, not serial/batch."""
        kit = JobKit.objects.create(
            work_order_id=self.owners['own']['work'], created_by=self.reader
        )
        stock = StockItem.objects.get(part__IPN='PS-MOTOR')
        line = JobKitLine.objects.create(
            kit=kit,
            sequence=1,
            kind='part',
            requested_part=stock.part,
            selected_part=stock.part,
            required_quantity=1,
            fulfillment_mode='stock',
            source='manual',
        )
        allocation = JobKitAllocation.objects.create(
            line=line,
            stock_item=stock,
            quantity=1,
            reserved_by=self.reader,
            source_location_snapshot={
                'location_id': 12345,
                'location_name': 'P32 private bay',
            },
            stock_tracking_id=54321,
            idempotency_key='p32-reservation',  # gitleaks:allow
        )
        return kit, allocation

    @override_settings(AIMMS_JOB_KITS_ENABLED=True)
    def test_followup_job_kit_stock_redaction_and_positive_access(self):
        """Sibling allocation reads use the same stock-view boundary as Materials."""
        kit, allocation = self.make_job_kit()
        path = f'/api/tasks/work-orders/{kit.work_order_id}/job-kit/allocations/'
        with connection.execute_wrapper(self.read_only):
            response = self.api.get(path)
        self.assertEqual(response.status_code, 200)
        row = response.data['results'][0]
        for key in ('stock_item', 'source_location_snapshot', 'stock_tracking_id'):
            self.assertIsNone(row[key], key)
        self.assertEqual(row['reserved_by'], self.reader.pk)
        RuleSet.objects.update_or_create(
            group=self.group, name='stock', defaults={'can_view': True}
        )
        with connection.execute_wrapper(self.read_only):
            row = self.api.get(path).data['results'][0]
        self.assertEqual(row['stock_item'], allocation.stock_item_id)
        self.assertEqual(
            row['source_location_snapshot']['location_name'], 'P32 private bay'
        )
        self.assertEqual(row['stock_tracking_id'], 54321)

    @override_settings(
        AIMMS_JOB_KITS_ENABLED=True,
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.independent_scope',
    )
    def test_followup_job_kit_reads_require_work_view_independently(self):
        """All sibling reads deny the role alone, retaining scope and real data."""
        kit, _ = self.make_job_kit()
        self.api.force_login(self.no_role)
        for suffix in ('', 'allocations/', 'shortages/', 'events/'):
            with (
                self.subTest(suffix=suffix),
                connection.execute_wrapper(self.read_only),
            ):
                self.assertEqual(
                    self.api.get(
                        f'/api/tasks/work-orders/{kit.work_order_id}/job-kit/{suffix}'
                    ).status_code,
                    403,
                )

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.sales_scope'
    )
    def test_followup_customer_work_machine_changes_require_client_authority(self):
        """Customer ownership never authorizes naming another machine on a write."""
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True, can_add=True
        )
        self.reader.user_permissions.add(
            Permission.objects.get(codename='plan_workorder')
        )
        own = self.owners['customer']
        work = WorkOrder.objects.get(pk=own['work'])
        for machine in (
            self.owners['foreign']['machine'],
            self.owners['own']['machine'],
            999999,
        ):
            for method, path, body in (
                (
                    'post',
                    f'/api/kanban/work-orders/{work.pk}/commands/update/',
                    {
                        'expected_version': 1,
                        'idempotency_key': 'p32-customer-move',
                        'fields': {'machine_id': machine},
                    },
                ),
                ('patch', f'/api/kanban/work-orders/{work.pk}/', {'machine': machine}),
                ('patch', f'/api/tasks/work-orders/{work.pk}/', {'machine': machine}),
                (
                    'post',
                    '/api/tasks/work-orders/',
                    {
                        'machine': machine,
                        'customer': self.customer.pk,
                        'title': 'P32 canonical proposed job',
                    },
                ),
                (
                    'post',
                    '/api/kanban/work-orders/',
                    {'machine': machine, 'title': 'P32 proposed job'},
                ),
            ):
                with (
                    self.subTest(method=method, machine=machine),
                    connection.execute_wrapper(self.read_only),
                ):
                    response = getattr(self.api, method)(path, body, format='json')
                    self.assertEqual(
                        response.status_code, 404, getattr(response, 'data', None)
                    )
        allowed = self.api.post(
            f'/api/kanban/work-orders/{work.pk}/commands/update/',
            {
                'expected_version': 1,
                'idempotency_key': 'p32-customer-title',
                'fields': {'title': 'P32 allowed customer edit'},
            },
            format='json',
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: (
                sales_scope(actor) | independent_scope(actor)
            )
        ):
            allowed = self.api.post(
                f'/api/kanban/work-orders/{work.pk}/commands/update/',
                {
                    'expected_version': 2,
                    'idempotency_key': 'p32-authorized-machine',
                    'fields': {'machine_id': self.owners['own']['machine']},
                },
                format='json',
            )
            self.assertEqual(allowed.status_code, 200, allowed.data)

    @override_settings(AIMMS_RPF_ENABLED=True, AIMMS_RPF_SCOPE_RESOLVER=None)
    def test_followup_rpf_exact_client_customer_and_global_reads(self):
        """Every RPF child lookup inherits both identity columns, including global."""
        policy = PartVerificationPolicyVersion.objects.create(
            key='p32-scope', definition={}
        )
        rows = {}
        for owner in ('own', 'foreign', 'customer', 'global'):
            ids = self.owners.get(owner, {})
            rows[owner] = PartVerificationSession.objects.create(
                reference=f'P32-{owner}',
                purpose='maintenance',
                policy=policy,
                scope_client_id=AssetMachine.objects.get(pk=ids['machine']).client_id
                if owner in ('own', 'foreign')
                else None,
                scope_customer=self.customer if owner == 'customer' else None,
                scope_fingerprint='e' * 64,
                machine_id=ids.get('machine'),
            )
        with connection.execute_wrapper(self.read_only):
            response = self.api.get('/api/part/verification/sessions/')
            self.assertEqual(response.status_code, 200)
            data = (
                response.data['results']
                if isinstance(response.data, dict)
                else response.data
            )
            self.assertEqual({row['pk'] for row in data}, {rows['own'].pk})
            for suffix in (
                '',
                'readiness/',
                'evidence/',
                'candidates/',
                'decisions/',
                'events/',
            ):
                with self.subTest(suffix=suffix):
                    self.assertEqual(
                        self.api.get(
                            f'/api/part/verification/sessions/{rows["foreign"].pk}/{suffix}'
                        ).status_code,
                        404,
                    )
        for scope, owner in (
            (VerificationScope(None), 'global'),
            (VerificationScope(self.customer.pk), 'customer'),
        ):
            with (
                override_settings(
                    AIMMS_RPF_SCOPE_RESOLVER=lambda actor, scope=scope: {scope}
                ),
                connection.execute_wrapper(self.read_only),
            ):
                response = self.api.get('/api/part/verification/sessions/')
                self.assertEqual(
                    {
                        row['pk']
                        for row in (
                            response.data['results']
                            if isinstance(response.data, dict)
                            else response.data
                        )
                    },
                    {rows[owner].pk},
                )

    def test_followup_rpf_rejects_mixed_scope_with_valid_neighbor(self):
        """RPF keeps its explicit global scope but rejects contradictory identity."""
        for scopes in (
            ambiguous_scope(self.reader),
            ambiguous_scope(self.reader) | independent_scope(self.reader),
        ):
            with (
                override_settings(
                    AIMMS_RPF_SCOPE_RESOLVER=lambda actor, scopes=scopes: scopes
                ),
                self.assertRaises(VerificationScopeError),
            ):
                verification_scopes(self.reader)

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.independent_scope'
    )
    def test_followup_media_requires_role_without_removing_scope(self):
        """Removing the fresh Work role check must expose bytes and fail this case."""
        self.api.force_login(self.no_role)
        for case in READS[23:25]:
            self.assertEqual(self.get_case(case).status_code, 404)
        self.api.force_login(self.reader)
        for case in READS[23:25]:
            self.assertEqual(self.get_case(case).status_code, 200)

    @override_settings(
        AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_r6_permission_matrix.independent_scope'
    )
    def test_followup_focus_requires_and_rechecks_role_with_scope_retained(self):
        """Role-only loss invalidates focus and existing receipts, not the grant."""
        own = self.owners['own']['work']
        for resolve in (
            lambda: authorized_focus(self.no_role.pk, own),
            lambda: issue_receipt(self.no_role.pk, 'p32-policy', 'p32-thread', own, 1),
        ):
            with self.assertRaises(WorkFocusError):
                resolve()
        issued = issue_receipt(self.reader.pk, 'p32-policy', 'p32-thread', own, 1)
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_view=False
        )
        self.assertTrue(
            ClientScopeGrant.objects.filter(
                user=self.reader, client=self.tenant
            ).exists()
        )
        for resolve in (
            lambda: authorized_focus(self.reader.pk, own),
            lambda: resolve_receipt(
                self.reader.pk, 'p32-policy', 'p32-thread', issued['receipt']
            ),
        ):
            with self.assertRaises(WorkFocusError):
                resolve()

    def test_followup_real_gate_parent_binding_and_positive_controls(self):
        """An own packet URL must never dispatch a foreign packet's real gate."""
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True
        )
        packets = {
            owner: RepairPacket.objects.create(
                work_order_id=self.owners[owner]['work'],
                machine_id=self.owners[owner]['machine'],
            )
            for owner in ('own', 'foreign')
        }
        gates = {
            owner: RepairPacketGate.objects.create(
                packet=packet, name=f'P32 {owner} gate', gate_type='loto'
            )
            for owner, packet in packets.items()
        }
        before = list(RepairPacketGate.objects.values())
        for suffix in ('confirm/', 'verify/', 'waive/', 'proofs/', 'lockout/'):
            with (
                self.subTest(suffix=suffix),
                connection.execute_wrapper(self.read_only),
            ):
                response = self.api.post(
                    f'/api/repair/packets/{packets["own"].pk}/gates/{gates["foreign"].pk}/{suffix}',
                    {},
                    format='json',
                )
                self.assertEqual(response.status_code, 404)
        self.assertEqual(list(RepairPacketGate.objects.values()), before)
        # Empty/incomplete proof may be invalid, but it must get past owner/role gates.
        response = self.api.post(
            f'/api/repair/packets/{packets["own"].pk}/gates/{gates["own"].pk}/proofs/',
            {},
            format='json',
        )
        self.assertNotIn(response.status_code, (403, 404))

    def grant_planner(self):
        """Grant only the native planning and CRUD permissions these edits need."""
        RuleSet.objects.filter(group=self.group, name='work_order').update(
            can_change=True, can_add=True
        )
        self.reader.user_permissions.add(
            Permission.objects.get(codename='plan_workorder')
        )

    def test_round2_canonical_machine_ids_hide_foreign_and_missing(self):
        """The canonical FK must authorize before disclosing whether it exists."""
        self.grant_planner()
        work = self.owners['own']['work']
        for machine in (self.owners['foreign']['machine'], 999999):
            for method, path in (
                ('post', '/api/tasks/work-orders/'),
                ('patch', f'/api/tasks/work-orders/{work}/'),
            ):
                with (
                    self.subTest(method=method, machine=machine),
                    connection.execute_wrapper(self.read_only),
                ):
                    response = getattr(self.api, method)(
                        path,
                        {'title': 'P32 canonical', 'machine': machine},
                        format='json',
                    )
                    self.assertEqual(
                        response.status_code, 404, getattr(response, 'data', None)
                    )

    def test_round2_packet_asset_noop_and_standalone_moves_remain_allowed(self):
        """The packet restriction permits ordinary edits and non-packet moves."""
        self.grant_planner()
        work = WorkOrder.objects.get(pk=self.owners['own']['work'])
        packet = RepairPacket.objects.create(work_order=work, machine=work.machine)
        for method, path in (
            ('put', f'/api/kanban/work-orders/{work.pk}/'),
            ('patch', f'/api/tasks/work-orders/{work.pk}/'),
        ):
            response = getattr(self.api, method)(
                path,
                {
                    'title': 'P32 unchanged asset',
                    'machine': work.machine_id,
                    'status': WorkOrder.STATUS_BACKLOG,
                    'priority': WorkOrder.PRIORITY_MEDIUM,
                },
                format='json',
            )
            self.assertEqual(response.status_code, 200, response.data)
        response = self.api.post(
            f'/api/kanban/work-orders/{work.pk}/commands/update/',
            {
                'expected_version': 1,
                'idempotency_key': 'p32-noop-asset',
                'fields': {'machine_id': work.machine_id},
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            self.api.get(f'/api/repair/packets/{packet.pk}/').status_code, 200
        )
        other = AssetMachine.objects.create(
            name='P32 alternate authorized asset', client=self.tenant
        )
        packet.delete()
        response = self.api.patch(
            f'/api/tasks/work-orders/{work.pk}/', {'machine': other.pk}, format='json'
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['machine'], other.pk)
        response = self.api.post(
            '/api/tasks/work-orders/',
            {
                'title': 'P32 canonical authorized create',
                'machine': other.pk,
                'status': WorkOrder.STATUS_BACKLOG,
                'priority': WorkOrder.PRIORITY_MEDIUM,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_round2_malformed_resource_parent_ids_are_400_without_writes(self):
        """Early authorization preserves serializer-style errors for bad input."""
        self.grant_planner()
        work = self.owners['own']['work']
        packet = RepairPacket.objects.create(
            work_order_id=work, machine_id=self.owners['own']['machine']
        )
        routes = (
            ('post', '/api/kanban/work-orders/', 'machine'),
            ('patch', f'/api/kanban/work-orders/{work}/', 'machine'),
            ('post', '/api/tasks/work-orders/', 'machine'),
            ('patch', f'/api/tasks/work-orders/{work}/', 'machine'),
            ('post', '/api/repair/packets/', 'machine'),
            ('patch', f'/api/repair/packets/{packet.pk}/', 'machine'),
            ('post', '/api/repair/packets/', 'work_order'),
            ('patch', f'/api/repair/packets/{packet.pk}/', 'work_order'),
        )
        for method, path, field in routes:
            for value in ('abc', '', {}, []):
                with (
                    self.subTest(path=path, field=field, value=value),
                    connection.execute_wrapper(self.read_only),
                ):
                    response = getattr(self.api, method)(
                        path, {field: value}, format='json'
                    )
                    self.assertEqual(
                        response.status_code, 400, getattr(response, 'data', None)
                    )
            with (
                self.subTest(path=path, body='array'),
                connection.execute_wrapper(self.read_only),
            ):
                response = getattr(self.api, method)(path, [], format='json')
                self.assertEqual(
                    response.status_code, 400, getattr(response, 'data', None)
                )

    def test_round2_malformed_command_machine_ids_are_400(self):
        """Planning aliases and object bodies reject malformed shapes cleanly."""
        self.grant_planner()
        path = f'/api/kanban/work-orders/{self.owners["own"]["work"]}/commands/update/'
        bodies = [[], {'fields': []}]
        for field in ('machine', 'machine_id'):
            bodies.extend({'fields': {field: value}} for value in ('abc', '', {}, []))
        for body in bodies:
            with self.subTest(body=body), connection.execute_wrapper(self.read_only):
                response = self.api.post(path, body, format='json')
                self.assertEqual(
                    response.status_code, 400, getattr(response, 'data', None)
                )

    def test_round2_diagnostic_packets_share_work_owner_and_consistency_scope(self):
        """Tool authorization and discovery cannot broaden the packet API ACL."""
        from repair import services

        packets = {}
        for owner in ('own', 'customer'):
            ids = self.owners[owner]
            packets[owner] = RepairPacket.objects.create(
                work_order_id=ids['work'], machine_id=ids['machine']
            )
        corrupt_work = WorkOrder.objects.create(
            title='P32 inconsistent packet Work',
            machine_id=self.owners['own']['machine'],
        )
        other_machine = AssetMachine.objects.create(
            name='P32 inconsistent same-client machine', client=self.tenant
        )
        packets['corrupt'] = RepairPacket.objects.create(
            work_order=corrupt_work, machine=other_machine
        )

        def decision(packet):
            return services.authorize_diagnostic_read(
                actor=self.reader,
                capability='diagnostics.packet.read',
                entity_type='repair_packet',
                entity_id=packet.pk,
                expected_revision=services._diagnostic_revision(packet),
                linked_machine_id=packet.machine_id,
                check_id='p32-packet-boundary',
            )

        with (
            override_settings(
                AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=lambda actor: {
                    'diagnostics.packet.read'
                }
            ),
            connection.execute_wrapper(self.read_only),
        ):
            self.assertIsNotNone(decision(packets['own']))
            self.assertIsNone(decision(packets['customer']))
            self.assertIsNone(decision(packets['corrupt']))
            roots = services.list_diagnostic_record_roots(self.reader)
            self.assertEqual(
                {r['entity_id'] for r in roots if r['entity_type'] == 'repair_packet'},
                {packets['own'].pk},
            )
            with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=sales_scope):
                self.assertIsNotNone(decision(packets['customer']))
                self.assertIsNone(decision(packets['own']))
                roots = services.list_diagnostic_record_roots(self.reader)
                self.assertEqual(
                    {
                        r['entity_id']
                        for r in roots
                        if r['entity_type'] == 'repair_packet'
                    },
                    {packets['customer'].pk},
                )


def read_test(case, variant):
    """Parametrize each route/authority pair without another test dependency."""

    def test(self):
        owner = 'own'
        expected = 200
        if variant == 'anonymous':
            self.api.logout()
            expected = 401
        elif variant == 'no_role':
            self.api.force_login(self.no_role)
            expected = case.no_role
        elif variant in ('foreign', 'unresolved'):
            owner = variant
            expected = 200 if case.collection else 404
            if variant == 'unresolved' and case.name in (
                'components',
                'location_machines',
            ):
                expected = 403  # Native configuration denial, no data.
            if case.collection and variant == 'foreign':
                self.api.force_login(self.outside)
        elif variant == 'customer':
            # Explicit sales ownership wins over the machine's client.
            owner = 'customer'
            expected = 200 if case.collection or '{machine}' in case.path else 404
        with override_settings(
            **(
                {
                    'AIMMS_MAINTENANCE_SCOPE_RESOLVER': 'tasks.tests.test_r6_permission_matrix.unresolved_scope'
                }
                if variant == 'unresolved'
                else {}
            ),
            **(
                {case.flag: False}
                if variant == 'flag_off' and case.flag
                else {'AIMMS_WORK_ORDERS_ENABLED': variant != 'flag_off'}
            ),
        ):
            if variant == 'flag_off' and (
                case.flag
                or case.name in ('captures', 'proposal', 'readings', 'amendments')
            ):
                expected = 404
            response = self.get_case(case, owner)
        self.assertEqual(
            response.status_code,
            expected,
            (case.name, variant, getattr(response, 'data', None)),
        )
        if case.collection and variant in ('foreign', 'unresolved'):
            self.assertNotIn('P32 own work', str(response.data))
            self.assertNotIn('P32 own machine', str(response.data))
        if expected == 404:
            self.assertNotIn('P32 own retained narrative', response.content.decode())
        if (
            expected == 200
            and variant in ('reader', 'customer', 'foreign', 'unresolved')
            and case.name in ('work_list', 'location_machines')
        ):
            results = response.data['results']
            visible = (
                set()
                if variant == 'unresolved'
                else (
                    {
                        self.owners['foreign'][
                            'work' if case.name == 'work_list' else 'machine'
                        ]
                    }
                    if variant == 'foreign'
                    else {
                        self.owners['own'][
                            'work' if case.name == 'work_list' else 'machine'
                        ],
                        *(
                            []
                            if case.name == 'work_list'
                            else [self.owners['customer']['machine']]
                        ),
                    }
                )
            )
            self.assertEqual({row.get('id', row.get('pk')) for row in results}, visible)
        if variant == 'customer' and '{machine}' in case.path:
            self.assertNotIn('P32 customer work', str(response.data))
            if case.name == 'work_summary':
                self.assertEqual(response.data['open']['count'], 0)
            elif case.name == 'positions':
                self.assertEqual(response.data['machine_work']['count'], 0)
                populated = [
                    row['work']
                    for row in response.data['positions']
                    if row['work'] is not None
                ]
                self.assertTrue(populated)
                self.assertTrue(
                    all(row['count'] == 0 and row['results'] == [] for row in populated)
                )
            elif case.name == 'components':
                rows = response.data.get('results', response.data)
                self.assertTrue(rows)
                self.assertTrue(all(row['open_work']['count'] == 0 for row in rows))
            elif case.name == 'occurrence':
                self.assertEqual(response.data['work'], [])

    return test


for _case in READS:
    for _variant in (
        'anonymous',
        'no_role',
        'reader',
        'foreign',
        'customer',
        'unresolved',
        'flag_off',
    ):
        setattr(
            R6PermissionMatrix,
            f'test_read_{_case.name}_{_variant}',
            read_test(_case, _variant),
        )


WRITES = (
    (
        'hold',
        '/api/tasks/work-orders/{work}/hold/',
        {'reason': 'P32 recorded hold'},
        'execute_workorder',
    ),
    ('resume', '/api/tasks/work-orders/{work}/resume/', {}, 'execute_workorder'),
    (
        'capture',
        '/api/tasks/work-orders/{work}/closeout/captures/',
        {'narrative': 'P32 synthetic account. ' * 10},
        'capture_closeout',
    ),
    (
        'investigation_create',
        '/api/kanban/work-orders/{work}/investigation-requests/',
        {
            'operation': 'create',
            'fields': {
                'purpose': 'P32 new decision',
                'next_check': 'P32 next check',
                'required_artifacts': [],
            },
        },
        None,
    ),
    (
        'investigation_artifact',
        '/api/kanban/work-orders/{work}/investigation-requests/{request}/',
        {
            'operation': 'artifact',
            'request_version': 1,
            'fields': {
                'kind': 'observation',
                'payload': {'text': 'P32 observed evidence'},
            },
        },
        None,
    ),
    (
        'investigation_revise',
        '/api/kanban/work-orders/{work}/investigation-requests/{request}/',
        {
            'operation': 'revise',
            'request_version': 1,
            'fields': {'next_check': 'P32 changed check'},
        },
        None,
    ),
    (
        'planning',
        '/api/kanban/work-orders/{work}/commands/update/',
        {'fields': {'title': 'P32 changed title'}},
        None,
    ),
)


def write_test(name, path, fields, permission, variant):
    """Prove denied commands do not write, and authorized intents need keys."""

    def test(self):
        own = self.owners['own']
        work = WorkOrder.objects.get(pk=own['work'])
        # A hold can act now; resume has the same real readiness as native Work.
        if name == 'resume':
            work.lifecycle_status = 'on_hold'
            work.hold_reason = 'P32 prior hold'
            work.save(update_fields=['lifecycle_status', 'hold_reason'])
        if name == 'capture':
            work.closeout_captures.update(status='abandoned')
        body = {'expected_version': 1, 'idempotency_key': f'p32-{name}', **fields}
        path_ids = self.owners[variant] if variant in ('foreign', 'customer') else own
        authorized = variant in ('missing_key', 'replay', 'conflict') or (
            variant == 'flag_off' and '/api/tasks/' not in path
        )
        if authorized:
            if permission:
                self.grant(permission)
            else:
                RuleSet.objects.filter(group=self.group, name='work_order').update(
                    can_change=True
                )
        expected = 403
        if variant == 'anonymous':
            self.api.logout()
            expected = 401
        elif variant in ('foreign', 'customer'):
            # Give the role/command permission so a 404 proves scope specifically.
            if permission:
                self.grant(permission)
            else:
                RuleSet.objects.filter(group=self.group, name='work_order').update(
                    can_change=True
                )
            expected = 404
        elif variant == 'missing_key':
            body.pop('idempotency_key')
            expected = 400
        elif variant == 'flag_off':
            expected = 404 if '/api/tasks/' in path else 403
        url = path.format(**path_ids)
        if not authorized or variant == 'missing_key':
            with (
                override_settings(AIMMS_WORK_ORDERS_ENABLED=variant != 'flag_off'),
                connection.execute_wrapper(self.read_only),
            ):
                response = self.api.post(url, body, format='json')
            self.assertEqual(
                response.status_code, expected, getattr(response, 'data', None)
            )
            return
        with override_settings(AIMMS_WORK_ORDERS_ENABLED=variant != 'flag_off'):
            first = self.api.post(url, body, format='json')
        self.assertEqual(first.status_code, 200, getattr(first, 'data', None))
        if variant == 'flag_off':
            return
        before = list(WorkOrderCommand.objects.values())
        events = list(WorkOrderEvent.objects.values())
        if variant == 'conflict':
            body = (
                {**body, 'reason': 'P32 different intent'}
                if name in ('hold', 'resume')
                else {
                    **body,
                    **(
                        {'narrative': 'P32 different narrative. ' * 10}
                        if name == 'capture'
                        else {
                            'fields': {
                                **fields.get('fields', {}),
                                'title'
                                if name == 'planning'
                                else 'next_check': 'P32 changed intent',
                            }
                        }
                    ),
                }
            )
        second = self.api.post(url, body, format='json')
        self.assertEqual(
            second.status_code,
            409 if variant == 'conflict' else 200,
            getattr(second, 'data', None),
        )
        if variant == 'replay':
            self.assertEqual(second.data, first.data)
        self.assertEqual(list(WorkOrderCommand.objects.values()), before)
        self.assertEqual(list(WorkOrderEvent.objects.values()), events)

    return test


for _name, _path, _fields, _permission in WRITES:
    for _variant in (
        'anonymous',
        'viewer',
        'foreign',
        'customer',
        'missing_key',
        'flag_off',
        'replay',
        'conflict',
    ):
        setattr(
            R6PermissionMatrix,
            f'test_write_{_name}_{_variant}',
            write_test(_name, _path, _fields, _permission, _variant),
        )


PLANNING_KEY_CALLERS = (
    (
        'create',
        '/api/kanban/work-orders/commands/create/',
        {'title': 'P32 new work', 'machine': '{machine}'},
        'add',
    ),
    (
        'update',
        '/api/kanban/work-orders/{work}/commands/update/',
        {'fields': {'title': 'P32 changed'}},
        'change',
    ),
    (
        'schedule',
        '/api/kanban/work-orders/{work}/commands/schedule/',
        {
            'scheduled_start': '2026-08-03T09:00:00Z',
            'scheduled_end': '2026-08-03T11:00:00Z',
        },
        'change',
    ),
    (
        'resize',
        '/api/kanban/work-orders/{work}/commands/resize/',
        {'estimated_minutes': 120},
        'change',
    ),
    (
        'delete',
        '/api/kanban/work-orders/{work}/commands/delete/',
        {'reason': 'P32 test'},
        'delete',
    ),
    (
        'create_child',
        '/api/kanban/work-orders/{work}/commands/create-child/',
        {'title': 'P32 child', 'card_kind': 'subtask'},
        'add',
    ),
    ('apply', '/api/kanban/schedule/apply/', {}, 'change'),
    ('optimize', '/api/kanban/schedule/optimize/', {}, 'change'),
)


def planning_key_test(name, path, fields, role):
    """Every mutating planning adapter must demand caller-owned intent."""

    @override_settings(USE_TZ=True)
    def test(self):
        own = self.owners['own']
        RuleSet.objects.filter(group=self.group, name='work_order').update(**{
            f'can_{role}': True
        })
        WorkOrder.objects.filter(pk=own['work']).update(
            estimated_minutes=120, lifecycle_status='draft'
        )
        body = {'expected_version': 1, **fields}
        if name == 'create':
            body['machine'] = own['machine']
        elif name == 'apply':
            body['operations'] = [
                {
                    'card_id': own['work'],
                    'expected_version': 1,
                    'scheduled_start': '2026-08-03T09:00:00Z',
                    'scheduled_end': '2026-08-03T11:00:00Z',
                }
            ]
        elif name == 'optimize':
            body.update(
                candidate_ids=[own['work']], horizon_start='2026-08-03T09:00:00Z'
            )
        with connection.execute_wrapper(self.read_only):
            response = self.api.post(path.format(**own), body, format='json')
            self.assertEqual(response.status_code, 400, getattr(response, 'data', None))
            self.assertIn('idempotency_key', str(response.data))

    return test


for _name, _path, _fields, _role in PLANNING_KEY_CALLERS:
    setattr(
        R6PermissionMatrix,
        f'test_planning_key_{_name}',
        planning_key_test(_name, _path, _fields, _role),
    )


HEALTH_ACTIONS = ('acknowledge', 'dismiss', 'evidence', 'preliminary-analysis')


def health_action_test(action, variant):
    """A real anomaly is required to prove role and both parent boundaries."""

    def test(self):
        ids = self.owners['own']
        expected = 403
        if variant not in ('viewer', 'add_only'):
            RuleSet.objects.filter(group=self.group, name='work_order').update(
                can_change=True
            )
        if variant == 'add_only':
            RuleSet.objects.filter(group=self.group, name='work_order').update(
                can_add=True
            )
        if variant == 'anonymous':
            self.api.logout()
            expected = 401
        elif variant == 'foreign_machine':
            ids = self.owners['foreign']
            expected = 404
        elif variant == 'foreign_anomaly':
            ids = {**ids, 'anomaly': self.owners['foreign']['anomaly']}
            expected = 404
        path = f'/api/machine-health/machines/{ids["machine"]}/health/anomalies/{ids["anomaly"]}/{action}/'
        body = {'note': 'P32 synthetic operator note', 'reason': 'anomaly_repair'}
        if variant == 'writer':
            count = HealthEvidenceSnapshot.objects.count()
            response = self.api.post(path, body, format='json')
            self.assertIn(
                response.status_code, (200, 201), getattr(response, 'data', None)
            )
            if action in ('evidence', 'preliminary-analysis'):
                self.assertGreater(HealthEvidenceSnapshot.objects.count(), count)
        else:
            before = list(HealthEvidenceSnapshot.objects.values())
            with connection.execute_wrapper(self.read_only):
                response = self.api.post(path, body, format='json')
                self.assertEqual(
                    response.status_code, expected, getattr(response, 'data', None)
                )
            self.assertEqual(list(HealthEvidenceSnapshot.objects.values()), before)

    return test


for _action in HEALTH_ACTIONS:
    for _variant in (
        'anonymous',
        'viewer',
        'add_only',
        'foreign_machine',
        'foreign_anomaly',
        'writer',
    ):
        setattr(
            R6PermissionMatrix,
            f'test_health_action_{_action.replace("-", "_")}_{_variant}',
            health_action_test(_action, _variant),
        )


def packet_asset_change_test(writer):
    """Every Work edit adapter preserves a linked packet's lifecycle owner."""

    def test(self):
        self.grant_planner()
        work = WorkOrder.objects.get(pk=self.owners['own']['work'])
        packet = RepairPacket.objects.create(work_order=work, machine=work.machine)
        alternate = AssetMachine.objects.create(
            name='P32 authorized replacement', client=self.tenant
        )
        method, path, body = {
            'board': (
                'put',
                f'/api/kanban/work-orders/{work.pk}/',
                {
                    'title': work.title,
                    'machine': alternate.pk,
                    'status': WorkOrder.STATUS_BACKLOG,
                    'priority': WorkOrder.PRIORITY_MEDIUM,
                },
            ),
            'canonical': (
                'patch',
                f'/api/tasks/work-orders/{work.pk}/',
                {'machine': alternate.pk},
            ),
            'command': (
                'post',
                f'/api/kanban/work-orders/{work.pk}/commands/update/',
                {
                    'expected_version': 1,
                    'idempotency_key': 'p32-packet-machine',
                    'fields': {'machine_id': alternate.pk},
                },
            ),
        }[writer]
        with connection.execute_wrapper(self.read_only):
            response = getattr(self.api, method)(path, body, format='json')
            self.assertEqual(response.status_code, 409, getattr(response, 'data', None))
            self.assertIn('packet', str(response.data).lower())
            self.assertEqual(
                self.api.get(f'/api/repair/packets/{packet.pk}/').status_code, 200
            )
        work.refresh_from_db()
        self.assertEqual(work.machine_id, packet.machine_id)

    return test


for _writer in ('board', 'canonical', 'command'):
    setattr(
        R6PermissionMatrix,
        f'test_round2_packet_asset_change_{_writer}',
        packet_asset_change_test(_writer),
    )
