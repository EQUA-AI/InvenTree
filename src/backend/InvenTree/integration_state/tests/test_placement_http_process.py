"""Native HTTP placement metadata composed with independently retained serving IO."""

import json
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from django.db import connection
from django.test import TransactionTestCase, override_settings

from rest_framework.test import APIClient

from assets.integration_contracts import (
    ClientSourceIdentity,
    LocationSourceIdentity,
    MachineSourceIdentity,
)
from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
from assets.integration_s5_contracts import (
    PlacementExpectation,
    PlacementServingObservation,
    ProjectionBinding,
)
from assets.integration_s5_pending import PlacementReadAdapter
from assets.tests.integration_s5_test_support import NativePlacementFixture
from integration_state.models import PlacementAcknowledgement
from integration_state.services import (
    activate_namespace,
    bootstrap_projection,
    disable_namespace,
    prepare_projection,
)


class RetainedServingProcess:
    """The child reads an independently staged serving file, not expected args."""

    def __init__(self, directory):
        """Start one owned serving process with its own retained file."""
        self.path = Path(directory) / 'actually-serving.json'
        self.calls = 0
        self.start()

    def start(self):
        """A restarted serving process reads the same retained generation."""
        code = """import json,pathlib,sys
path=pathlib.Path(sys.argv[1])
for line in sys.stdin:
    try:
        if json.loads(line) != {"operation":"read"}: raise ValueError()
        result={"rows":json.loads(path.read_text())}
    except (ValueError,OSError): result={"unavailable":True}
    print(json.dumps(result),flush=True)
"""
        self.process = subprocess.Popen(
            [sys.executable, '-I', '-u', '-c', code, str(self.path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def close(self):
        """Close only the owned synthetic serving process."""
        self.process.stdin.close()
        self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()

    def stage(self, rows):
        """Publish native HTTP observations independently of later proof requests."""
        temp = self.path.with_suffix('.new')
        temp.write_text(json.dumps(rows))
        temp.replace(self.path)

    def read(self):
        """Serving IO never owns a native source/state transaction."""
        if connection.in_atomic_block:
            raise AssertionError('Serving IO held native ownership')
        self.calls += 1
        self.process.stdin.write('{"operation":"read"}\n')
        self.process.stdin.flush()
        result = json.loads(self.process.stdout.readline())
        if (
            result.get('unavailable')
            or type(result.get('rows')) is not list
            or len(result['rows']) != 1
        ):
            raise ValueError('Serving rows are unavailable')
        row = result['rows'][0]
        binding = ProjectionBinding(
            **{
                k: v
                for k, v in row['binding'].items()
                if k not in ['client', 'namespace_epoch', 'configuration_epoch']
            },
            client=ClientSourceIdentity(**row['binding']['client']),
            namespace_epoch=UUID(row['binding']['namespace_epoch']),
            configuration_epoch=UUID(row['binding']['configuration_epoch']),
        )
        placement = row['placement']
        return PlacementServingObservation(
            binding=binding,
            placement=PlacementExpectation(
                machine=MachineSourceIdentity(**placement['machine']),
                client=ClientSourceIdentity(**placement['client']),
                location=None
                if placement['location'] is None
                else LocationSourceIdentity(**placement['location']),
                native_version=placement['native_version'],
                fence=placement['fence'],
                namespace_epoch=UUID(placement['namespace_epoch']),
            ),
            generation=row['generation'],
        )

    def observe_placement(self, _binding, _machine):
        """Ignore the caller expectation; serve only independently retained rows."""
        return self.read()

    def verify_observation(self, observation):
        """A fresh serving read must agree exactly with the original observation."""
        return self.read() == observation


@override_settings(
    AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'],
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
)
class PlacementHTTPProcessTests(NativePlacementFixture, TransactionTestCase):
    """Native session requests, actual transfers/S5, independent child restart."""

    def setUp(self):
        """Configure a synthetic source/projection only inside the owned DB."""
        super().setUp()
        self.actor.set_password('synthetic-placement-session')
        self.actor.save()
        self.api = APIClient()
        self.assertTrue(
            self.api.login(
                username=self.actor.username, password='synthetic-placement-session'
            )
        )
        self.second = APIClient()
        self.assertTrue(
            self.second.login(
                username=self.actor.username, password='synthetic-placement-session'
            )
        )
        self.namespace = activate_namespace('synthetic-s5')
        self.enroll()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.serving = RetainedServingProcess(self.directory.name)
        self.addCleanup(self.serving.close)
        self.enterContext(
            override_settings(
                AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: PlacementReadAdapter(
                    actor_pk=request.user.pk,
                    namespace_id=self.namespace.pk,
                    projection='synthetic-process',
                    authorize_metadata=lambda request, binding, placement: (
                        request.user.pk == self.actor.pk
                    ),
                    serving=self.serving,
                )
            )
        )

    def enroll(self):
        """Each restore requires a new qualified projection configuration."""
        self.enrollment = prepare_projection(
            self.namespace.pk, 'synthetic-process', self.owner.pk
        )
        bootstrap_projection(self.enrollment.pk)

    def page(self, api=None):
        """Traverse the real native HTTP route and fully rendered response."""
        response = (api or self.api).get(
            '/api/assets/locations/machines/', {'limit': 100}
        )
        self.assertEqual(response.status_code, 200, response.content)
        return next(
            row for row in response.json()['results'] if row['pk'] == self.machine.pk
        )

    def staged(self, row, generation):
        """Bind an actual earlier HTTP observation to its serving configuration."""
        self.namespace.refresh_from_db()
        self.enrollment.refresh_from_db()
        metadata = row['placement_sync']
        value = {
            'binding': asdict(
                ProjectionBinding(
                    environment=self.namespace.environment,
                    namespace_id=self.namespace.pk,
                    namespace_epoch=self.namespace.epoch,
                    enrollment_id=self.enrollment.pk,
                    configuration_epoch=self.enrollment.epoch,
                    projection=self.enrollment.projection,
                    client=ClientSourceIdentity(
                        self.namespace.environment, self.owner.pk
                    ),
                )
            ),
            'placement': asdict(
                PlacementExpectation(
                    machine=MachineSourceIdentity(
                        self.namespace.environment, row['pk']
                    ),
                    client=ClientSourceIdentity(
                        self.namespace.environment, row['client']
                    ),
                    location=None
                    if row['physical_location'] is None
                    else LocationSourceIdentity(
                        self.namespace.environment, row['physical_location']['pk']
                    ),
                    native_version=int(metadata['native_version']),
                    fence=int(metadata['source_fence']),
                    namespace_epoch=self.namespace.epoch,
                )
            ),
            'generation': generation,
        }
        return json.loads(json.dumps(value, default=str))

    def ack(self):
        """Only the trusted server function may attempt an actual serving proof."""
        return acknowledge_latest(
            self.enrollment.pk, self.machine.pk, adapter=self.serving
        )

    def test_latest_native_http_two_sessions_and_process_restart(self):
        """Stale served placement cannot clear pending after a native move."""
        first = self.page()
        self.assertEqual(first['placement_sync']['state'], 'pending')
        self.serving.stage([self.staged(first, 'generation-1')])
        self.assertTrue(self.ack())
        self.assertEqual(self.page()['placement_sync']['state'], 'current')
        self.assertEqual(self.page(self.second)['placement_sync']['state'], 'current')
        old_pid = self.serving.process.pid
        self.serving.close()
        self.serving.start()
        self.assertNotEqual(old_pid, self.serving.process.pid)
        self.assertEqual(self.page()['placement_sync']['state'], 'current')
        self.move()
        moved = self.page()
        self.assertEqual(moved['placement_sync']['state'], 'pending')
        with self.assertRaises(PlacementProofError):
            self.ack()
        self.assertEqual(self.page()['placement_sync']['state'], 'pending')
        self.serving.stage([self.staged(moved, 'generation-2')])
        self.assertTrue(self.ack())
        self.assertEqual(self.page(self.second)['placement_sync']['state'], 'current')

    def test_aba_and_namespace_restore_reject_old_serving_proof(self):
        """A returned location does not erase its version/fence/configuration history."""
        first = self.page()
        self.serving.stage([self.staged(first, 'before-aba')])
        self.move()
        self.move(destination=None)
        self.assertIsNone(self.page()['physical_location'])
        with self.assertRaises(PlacementProofError):
            self.ack()
        disable_namespace(self.namespace.pk)
        activate_namespace(self.namespace.environment)
        self.namespace.refresh_from_db()
        self.enroll()
        with self.assertRaises(PlacementProofError):
            self.ack()
        self.assertEqual(self.page()['placement_sync']['state'], 'pending')
        self.serving.stage([self.staged(self.page(), 'after-restore')])
        self.ack()
        self.assertEqual(self.page()['placement_sync']['state'], 'current')

    def test_missing_duplicate_and_mixed_serving_rows_never_acknowledge(self):
        """Malformed staged data cannot become current or alter native placement."""
        before = self.page()
        good = self.staged(before, 'malformed-control')
        for rows in [
            [],
            [good, good],
            [{**good, 'placement': {**good['placement'], 'native_version': 99}}],
        ]:
            with self.subTest(rows=len(rows)):
                self.serving.stage(rows)
                with self.assertRaises(PlacementProofError):
                    self.ack()
                self.assertFalse(PlacementAcknowledgement.objects.exists())
                self.assertEqual(
                    self.page()['placement_version'], before['placement_version']
                )
                self.assertEqual(self.page()['placement_sync']['state'], 'pending')
