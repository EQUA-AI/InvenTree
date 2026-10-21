"""Independent scope gates on repair intake and live AI procedure reads."""

import json
import uuid
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIRequestFactory, force_authenticate
from tasks.models import WorkOrder, WorkOrderCommand
from tasks.procedure_api import ProcedureDetail, _scoped_procedures
from tasks.procedure_models import (
    Procedure,
    ProcedureApplicability,
    ProcedureRevision,
    ProcedureRevisionStatus,
    ProcedureStep,
)
from tasks.scope import MaintenanceScope
from tasks.services.work_orders import WorkOrderScopeError

from assets.models import AssetMachine, Client
from company.models import Company
from users.models import RuleSet

from . import services
from .models import PacketStatus, RepairPacket
from .work_packages import DuplicateRepairConflict, create_repair_work_package


def _capabilities(actor):
    """Supply only the capability being tested; scope uses the real reader."""
    del actor
    return {'diagnostics.playbooks.read'}


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
class RepairIntakeMachineScopeTest(TestCase):
    """A direct service call checks its asset before duplicate disclosures."""

    def setUp(self):
        """Grant a normal creator one client and retain another's open repair."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_user(
            username=f'intake-scope-{suffix}', password='pw'
        )
        group = Group.objects.create(name=f'intake-creators-{suffix}')
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True, 'can_add': True}
        )
        self.actor.groups.add(group)
        self.client_row = Client.objects.create(
            name=f'Own {suffix}', code=f'o-{suffix}'
        )
        self.foreign_client = Client.objects.create(
            name=f'Foreign {suffix}', code=f'f-{suffix}'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(None, None, self.client_row.pk)
        }
        self.machine = AssetMachine.objects.create(
            name='Own pump', client=self.client_row
        )
        self.foreign = AssetMachine.objects.create(
            name='Foreign pump', client=self.foreign_client
        )

    def _create(self, machine, *, key=None, **values):
        """Call the same transactional command used by chat and approvals."""
        return create_repair_work_package(
            actor=self.actor,
            draft={'machine_id': machine.pk, 'title': 'Repair intake', **values},
            idempotency_key=key or uuid.uuid4().hex,
        )

    def _counts(self):
        """Include the durable effect ledger in refusal checks."""
        return tuple(
            model.objects.count()
            for model in (WorkOrder, RepairPacket, WorkOrderCommand)
        )

    def test_foreign_machine_with_no_duplicate_cannot_create(self):
        """A target with no prior repair must not bypass the service boundary."""
        before = self._counts()
        with self.assertRaises(WorkOrderScopeError):
            self._create(self.foreign)
        self.assertEqual(self._counts(), before)

    def test_foreign_duplicate_metadata_is_not_disclosed(self):
        """Scope refusal precedes the duplicate exception and its foreign links."""
        work = WorkOrder.objects.create(
            title='Foreign private repair', machine=self.foreign
        )
        RepairPacket.objects.create(
            machine=self.foreign, work_order=work, status=PacketStatus.DRAFT
        )
        before = self._counts()
        with self.assertRaises(WorkOrderScopeError):
            self._create(self.foreign)
        self.assertEqual(self._counts(), before)

    def test_replay_after_machine_moves_cannot_return_foreign_aggregate(self):
        """The saved intake receipt is not a grant after asset ownership changes."""
        key = uuid.uuid4().hex
        self._create(self.machine, key=key)
        self.machine.client = self.foreign_client
        self.machine.save(update_fields=['client'])
        before = self._counts()
        with self.assertRaises(WorkOrderScopeError):
            self._create(self.machine, key=key)
        self.assertEqual(self._counts(), before)

    def test_authorized_duplicate_keeps_the_existing_conflict_contract(self):
        """An authorized creator still receives the useful prior repair link."""
        first = self._create(self.machine)
        with self.assertRaises(DuplicateRepairConflict) as caught:
            self._create(self.machine)
        self.assertEqual(
            caught.exception.duplicates[0]['work_order_id'], first.work_order_id
        )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=None,
    AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER='repair.test_o24_review_bypasses._capabilities',
    AIMMS_PROCEDURES_ENABLED=True,
)
class DiagnosticProcedureCatalogueScopeTest(TestCase):
    """Machine diagnostic authority cannot widen live catalogue customer scope."""

    def setUp(self):
        """Use real machine authorization plus three applicable live catalogues."""
        suffix = uuid.uuid4().hex[:8]
        self.actor = get_user_model().objects.create_user(
            username=f'playbook-scope-{suffix}', password='pw'
        )
        self.client_row = Client.objects.create(
            name=f'Plant {suffix}', code=f'p-{suffix}'
        )
        self.machine = AssetMachine.objects.create(
            name='Diagnostic pump', client=self.client_row
        )
        self.own_customer = Company.objects.create(
            name='Own catalogue', is_customer=True
        )
        self.foreign_customer = Company.objects.create(
            name='Foreign catalogue', is_customer=True
        )
        self.machine_scope = MaintenanceScope(None, None, self.client_row.pk)
        self.customer_scope = MaintenanceScope(self.own_customer.pk, None)
        self.actor.maintenance_scopes = {self.machine_scope}
        self.internal = self._published(
            None, 'HIDDEN-INTERNAL', 'Private internal live instruction'
        )
        self.own = self._published(
            self.own_customer, 'OWN-CATALOGUE', 'Authorized live instruction'
        )
        self.foreign = self._published(
            self.foreign_customer, 'HIDDEN-FOREIGN', 'Foreign live instruction'
        )

    def _published(self, customer, code, instruction):
        """Create a published procedure with an explicit applicable-machine edge."""
        procedure = Procedure.objects.create(
            customer=customer,
            code=code,
            name=f'{code} live name',
            created_by=self.actor,
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=1,
            status=ProcedureRevisionStatus.PUBLISHED,
            work_order_type='corrective',
            created_by=self.actor,
            published_by=self.actor,
            published_at=timezone.now(),
            content_hash=uuid.uuid4().hex * 2,
        )
        procedure.current_revision = revision
        procedure.save(update_fields=['current_revision'])
        ProcedureApplicability.objects.create(revision=revision, machine=self.machine)
        ProcedureStep.objects.create(
            revision=revision,
            sequence=1,
            step_type='instruction',
            title=f'{code} step',
            instruction=instruction,
        )
        return procedure

    def _authorization(self):
        """Resolve the real machine root and current capability before the read."""
        decision = services.authorize_diagnostic_read(
            actor=self.actor,
            capability='diagnostics.playbooks.read',
            entity_type='machine',
            entity_id=self.machine.pk,
            expected_revision=services._diagnostic_revision(self.machine),
            linked_machine_id=None,
            check_id=uuid.uuid4().hex,
        )
        self.assertIsNotNone(decision)
        return SimpleNamespace(**decision)

    def _read(self, *, query='', limit=10, authorization=None):
        """Read current catalogue content through the diagnostic transfer seam."""
        return services.read_diagnostic_published_playbooks(
            actor=self.actor,
            authorization=authorization or self._authorization(),
            machine_id=self.machine.pk,
            expected_revision=services._diagnostic_revision(self.machine),
            query=query,
            limit=limit,
        )

    def _detail_status(self, procedure):
        """Assert the same actor's actual native procedure-detail permission."""
        request = APIRequestFactory().get(f'/api/tasks/procedures/{procedure.pk}/')
        force_authenticate(request, user=self.actor)
        return ProcedureDetail.as_view()(request, pk=procedure.pk).status_code

    def test_client_only_machine_reader_cannot_read_internal_catalogue(self):
        """Customerless catalogue rows are not implied by a machine client grant."""
        self.assertFalse(_scoped_procedures(self.actor).exists())
        self.assertEqual(self._detail_status(self.internal), 404)
        result = self._read()
        self.assertEqual(result['evidence'], ())
        self.assertTrue(result['abstention_reason'])

    def test_customer_scope_returns_only_the_same_native_catalogue(self):
        """Machine applicability narrows, and never replaces, customer authority."""
        self.actor.maintenance_scopes.add(self.customer_scope)
        self.assertEqual(self._detail_status(self.own), 200)
        self.assertEqual(self._detail_status(self.foreign), 404)
        self.assertEqual(self._detail_status(self.internal), 404)
        result = self._read()
        claims = [json.loads(item['claim']) for item in result['evidence']]
        self.assertEqual([claim['code'] for claim in claims], [self.own.code])
        self.assertEqual(
            claims[0]['steps'][0]['instruction'], 'Authorized live instruction'
        )

    def test_hidden_catalogue_query_does_not_become_a_search_oracle(self):
        """A query matching denied catalogue labels or instructions returns none."""
        self.actor.maintenance_scopes.add(self.customer_scope)
        self.assertEqual(self._detail_status(self.internal), 404)
        self.assertEqual(self._read(query='Private internal')['evidence'], ())
        self.assertEqual(self._read(query='HIDDEN-INTERNAL')['evidence'], ())

    def test_catalogue_grant_revoked_after_root_authorization_is_rechecked(self):
        """A still-authorized machine root retains no earlier catalogue grant."""
        self.actor.maintenance_scopes.add(self.customer_scope)
        authorization = self._authorization()
        self.actor.maintenance_scopes.remove(self.customer_scope)
        self.assertEqual(self._detail_status(self.own), 404)
        self.assertEqual(self._read(authorization=authorization)['evidence'], ())

    def test_hidden_rows_cannot_crowd_the_authorized_result_limit(self):
        """Apply catalogue authorization before the bounded evidence slice."""
        self.actor.maintenance_scopes.add(self.customer_scope)
        result = self._read(limit=1)
        self.assertEqual(
            [json.loads(item['claim'])['code'] for item in result['evidence']],
            [self.own.code],
        )
