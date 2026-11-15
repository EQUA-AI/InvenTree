"""Real future Work proof plus independent related-machine source authority."""

import json
from unittest.mock import patch

from django.test import TestCase

from tasks import integration_s3_context as context
from tasks.integration_s3_details import read_work_details
from tasks.integration_s3_wire import (
    SourceSerializationError,
    encode_source_record,
    read_work_context_json,
)

from assets.models import AssetMachine, ClientScopeGrant
from company.models import Company
from integration_state import native_authority
from integration_state.tests.test_native_authority import NativeAuthorityFixture


class NativeWorkContextTests(NativeAuthorityFixture, TestCase):
    """No policy/proof replacement makes an inaccessible endpoint readable."""

    def setUp(self):
        """Approve one exact future native Work before projection."""
        super().setUp()
        self.proof = self.attest()
        self.approve(self.proof)

    def read(self, work=None):
        """Read context through the actual parent and target source authorities."""
        return context.read_work_context(
            self.owner,
            environment=self.namespace.environment,
            work_pk=(work or self.new_work).pk,
        )

    def shared(self, machine):
        """Create and independently prove a natively shared Work."""
        customer = Company.objects.create(
            name='Synthetic context shared customer', is_customer=True
        )
        work = self.work('Synthetic context Work', machine=machine, customer=customer)
        self.approve(self.attest(pk=work.pk))
        return work

    def test_native_core_stays_stable_and_context_round_trips_authorized_fields(self):
        """An explicit context schema adds only independently authorized machine facts."""
        core = read_work_details(
            self.owner, environment=self.namespace.environment, work_pk=self.new_work.pk
        )
        self.assertFalse(hasattr(core, 'machine_pk'))
        result = self.read()
        self.assertEqual(result.machine_state, 'available')
        self.assertEqual(result.machine_pk, self.machine.pk)
        self.assertEqual(result.machine_name, self.machine.name)
        self.assertEqual(result.machine_active, self.machine.active)
        self.assertEqual(result.title, core.title)
        payload = read_work_context_json(
            self.owner, environment=self.namespace.environment, work_pk=self.new_work.pk
        )
        encoded = json.loads(payload)['record']
        self.assertEqual(encoded['schema_version'], 'aimms.work.context.v1')
        self.assertEqual(encoded['fields']['machine_state'], 'available')
        self.assertEqual(encoded['fields']['machine_pk'], self.machine.pk)
        self.assertIn('machine_originals', encoded['withheld_fields'])

    def test_shared_work_cannot_borrow_the_foreign_machine_grant(self):
        """Native shared Work visibility alone cannot supply the target grant."""
        foreign = AssetMachine.objects.create(
            name='Synthetic protected foreign machine', client=self.client_b
        )
        work = self.shared(foreign)
        result = self.read(work)
        self.assertEqual(result.title, work.title)
        self.assertEqual(result.machine_state, 'unknown')
        self.assertIsNone(result.machine_pk)
        self.assertIsNone(result.machine_name)
        self.assertIsNone(result.machine_active)
        grant = ClientScopeGrant.objects.create(user=self.owner, client=self.client_b)
        self.assertEqual(self.read(work).machine_pk, foreign.pk)
        grant.delete()
        self.assertEqual(self.read(work).machine_state, 'unknown')

    def test_real_absence_is_distinct_from_unknown(self):
        """A source null has a different state from a withheld target."""
        work = self.shared(None)
        result = self.read(work)
        self.assertEqual(result.machine_state, 'none')
        self.assertIsNone(result.machine_pk)

    def test_late_target_revocation_withholds_the_observation(self):
        """A valid constructor is followed by fresh source and target checks."""
        foreign = AssetMachine.objects.create(
            name='Synthetic late target', client=self.client_b
        )
        work = self.shared(foreign)
        grant = ClientScopeGrant.objects.create(user=self.owner, client=self.client_b)
        constructor = context.WorkContextSourceRecord

        def revoke(*args, **kwargs):
            result = constructor(*args, **kwargs)
            grant.delete()
            return result

        with patch.object(context, 'WorkContextSourceRecord', side_effect=revoke):
            self.assertIsNone(self.read(work))
        self.assertEqual(self.read(work).machine_state, 'unknown')

    def test_source_relink_and_withdrawal_cannot_reuse_earlier_proof(self):
        """New native material needs a new claim, and withdrawal invalidates reads."""
        self.new_work.machine = None
        self.new_work.save()
        self.assertIsNone(self.read())
        proof = self.attest(key='synthetic-relinked-context')
        self.approve(proof)
        self.assertEqual(self.read().machine_state, 'none')
        native_authority.withdraw_authority_request(
            self.request(),
            request_pk=proof.pk,
            expected_request_digest=proof.binding_digest,
            reason_reference='synthetic-context-withdrawal',
        )
        self.assertIsNone(self.read())

    def test_corrupted_unknown_context_cannot_disclose_a_target(self):
        """Pure encoding validates frozen-but-mutable relationship carriers."""
        result = self.read(self.shared(None))
        object.__setattr__(result, 'machine_pk', self.machine.pk)
        with self.assertRaises(SourceSerializationError):
            encode_source_record(result)

    def test_consumer_rejects_inconsistent_relationship_fields(self):
        """A malformed wire payload cannot render a withheld target identity."""
        from ai.core.integrations.integration_s3_content import _source_fields

        record = json.loads(encode_source_record(self.read(self.shared(None))))[
            'record'
        ]
        record['fields']['machine_pk'] = self.machine.pk
        with self.assertRaises(SourceSerializationError):
            _source_fields(record, 'work.context')
