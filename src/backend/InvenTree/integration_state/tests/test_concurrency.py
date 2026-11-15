"""Independent PostgreSQL connections over synthetic committed S5 placement."""

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from queue import Queue
from threading import Barrier, Event, Thread
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from assets.integration_contracts import ClientSourceIdentity, MachineSourceIdentity
from assets.integration_s5_ack import PlacementProofError, acknowledge_latest
from assets.integration_s5_contracts import PlacementExpectation, ProjectionBinding
from assets.locations import LocationConflict, transfer_machines
from assets.models import AssetMachine, MachineLocationTransfer, MachinePlacementHistory
from assets.tests.integration_s5_test_support import NativePlacementFixture
from integration_state.models import (
    PlacementAcknowledgement,
    ProjectionEnrollment,
    SourceChange,
    SourceState,
)
from integration_state.services import (
    activate_namespace,
    bootstrap_projection,
    current_expectation,
    prepare_projection,
)
from integration_state.tests.test_acknowledgements import SyntheticServingAdapter
from users.models import RuleSet

PROCESS_BOOTSTRAP = """
import json, os, sys
from pathlib import Path
data = json.load(sys.stdin)
root = Path(data['root']).resolve()
marker = Path('/scratch/s5-postgres-owned.json')
if (root != Path('/source/src/backend/InvenTree')
        or not Path('/.dockerenv').is_file()
        or not marker.is_file()
        or not os.environ.get('S5_POSTGRES_RUN_ID')
        or json.loads(marker.read_text())['run_id'] != os.environ['S5_POSTGRES_RUN_ID']):
    raise RuntimeError('Fresh-process probe requires the exact owned S5 namespace')
sys.path[:0] = [str(root.parent), str(root)]
sys.path.insert(0, '/root/.local/lib/python3.14/site-packages')
os.environ['DJANGO_SETTINGS_MODULE'] = 'ai.core.tests.settings'
sys.argv = ['manage.py', 'test']
from django.conf import settings
settings.DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql', 'NAME': 's5_control_test',
    'USER': 'postgres', 'PASSWORD': '', 'HOST': '/var/run/postgresql', 'PORT': '5432'
}}
settings.INSTALLED_APPS = [*settings.INSTALLED_APPS,
                          'integration_state.apps.IntegrationStateConfig']
settings.Q_CLUSTER = {**settings.Q_CLUSTER, 'orm': 'default'}
settings.AIMMS_MAINTENANCE_SCOPE_RESOLVER = 'tasks.scope.granted_client_scope_resolver'
from oauth2_provider import settings as defaults
for name in ('APPLICATION', 'DEVICE_GRANT', 'ACCESS_TOKEN', 'ID_TOKEN', 'GRANT',
             'REFRESH_TOKEN'):
    setattr(settings, f'OAUTH2_PROVIDER_{name}_MODEL', getattr(defaults, f'{name}_MODEL'))
import django
django.setup()
from integration_state.tests.test_concurrency import process_probe
print('S5_PROCESS_PROBE=' + json.dumps(process_probe(data)))
"""


def process_probe(data):
    """Read/ack real committed state after fresh guarded Django process startup."""
    from rest_framework.test import APIRequestFactory, force_authenticate

    from assets.integration_contracts import LocationSourceIdentity
    from assets.integration_s5_pending import PlacementReadAdapter, metadata_for_machine
    from assets.location_api import LocationMachines
    from integration_state.models import SourceNamespace

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_user, pg_backend_pid()')
        database, user, backend_pid = cursor.fetchone()
    if database != 's5_control_test' or user != 'postgres':
        raise AssertionError('Fresh process reached an unbound database')
    namespace = SourceNamespace.objects.get(pk=data['namespace'])
    enrollment = ProjectionEnrollment.objects.get(pk=data['enrollment'])
    actor = get_user_model().objects.get(pk=data['actor'])
    serving = SyntheticServingAdapter(
        ProjectionBinding(
            environment='synthetic-s5',
            namespace_id=namespace.pk,
            namespace_epoch=namespace.epoch,
            enrollment_id=enrollment.pk,
            configuration_epoch=enrollment.epoch,
            projection='synthetic-projection',
            client=ClientSourceIdentity('synthetic-s5', data['client']),
        ),
        PlacementExpectation(
            machine=MachineSourceIdentity('synthetic-s5', data['machine']),
            client=ClientSourceIdentity('synthetic-s5', data['client']),
            location=None
            if data['location'] is None
            else LocationSourceIdentity('synthetic-s5', data['location']),
            native_version=data['version'],
            fence=data['fence'],
            namespace_epoch=namespace.epoch,
        ),
    )
    serving.generation = data['generation']
    if data['acknowledge']:
        acknowledge_latest(enrollment.pk, data['machine'], adapter=serving)
    raw = APIRequestFactory().get('/api/assets/locations/machines/')
    force_authenticate(raw, user=actor)
    request = LocationMachines().initialize_request(raw)
    metadata = metadata_for_machine(
        request,
        data['machine'],
        adapter=PlacementReadAdapter(
            actor_pk=actor.pk,
            namespace_id=namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=serving,
        ),
    )
    return {'pid': os.getpid(), 'backend_pid': backend_pid, 'metadata': metadata}


@skipUnless(connection.vendor == 'postgresql', 'Requires independent PostgreSQL locks')
@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PlacementConcurrencyTests(NativePlacementFixture, TransactionTestCase):
    """Separate backend PIDs, actual native commits, bounded barriers and readback."""

    def setUp(self):
        """Use a committed real baseline and an independently staged serving store."""
        super().setUp()
        self.worker_pids = set()
        self.namespace = activate_namespace('synthetic-s5')
        self.enrollment = prepare_projection(
            self.namespace.pk, 'synthetic-projection', self.owner.pk
        )
        bootstrap_projection(self.enrollment.pk)
        self.binding = ProjectionBinding(
            environment='synthetic-s5',
            namespace_id=self.namespace.pk,
            namespace_epoch=self.namespace.epoch,
            enrollment_id=self.enrollment.pk,
            configuration_epoch=self.enrollment.epoch,
            projection='synthetic-projection',
            client=ClientSourceIdentity('synthetic-s5', self.owner.pk),
        )
        self.served = SyntheticServingAdapter(
            self.binding,
            PlacementExpectation(
                machine=MachineSourceIdentity('synthetic-s5', self.machine.pk),
                client=ClientSourceIdentity('synthetic-s5', self.owner.pk),
                location=None,
                native_version=0,
                fence=1,
                namespace_epoch=self.namespace.epoch,
            ),
        )

    def start_connection(self, operation):
        """Record actual DB session identity, not merely Python thread identity."""
        outcome = Queue()

        def run():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET statement_timeout = 8000')
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                try:
                    outcome.put((pid, operation(), None))
                except Exception as exc:
                    outcome.put((pid, None, exc))
            finally:
                connection.close()

        thread = Thread(target=run, daemon=True)
        thread.start()
        return thread, outcome

    def finish_connection(self, task):
        """No owned test thread may survive into destructive fixture cleanup."""
        thread, outcome = task
        thread.join(12)
        self.assertFalse(thread.is_alive(), 'Owned DB worker did not terminate')
        self.assertFalse(outcome.empty(), 'Owned DB worker returned no outcome')
        pid, value, error = outcome.get_nowait()
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            self.assertNotEqual(pid, cursor.fetchone()[0])
        self.assertNotIn(pid, self.worker_pids)
        self.worker_pids.add(pid)
        return value, error

    def test_native_transfer_commits_while_old_serving_io_is_held(self):
        """A separate writer stays usable and its newer fence rejects delayed proof."""
        entered, release = Event(), Event()
        observe = self.served.observe_placement

        def held(binding, machine):
            observation = observe(binding, machine)
            entered.set()
            if not release.wait(10):
                raise AssertionError('Owned serving barrier timed out')
            return observation

        self.served.observe_placement = held
        task = self.start_connection(
            lambda: acknowledge_latest(
                self.enrollment.pk, self.machine.pk, adapter=self.served
            )
        )
        try:
            self.assertTrue(entered.wait(10), 'Serving observation was not reached')
            with connection.cursor() as cursor:
                cursor.execute('SET statement_timeout = 8000')
            self.move(version=0)
            self.machine.refresh_from_db()
            self.assertEqual(self.machine.physical_location_id, self.destination.pk)
            self.assertEqual(self.machine.placement_version, 1)
        finally:
            release.set()
            with connection.cursor() as cursor:
                cursor.execute('SET statement_timeout TO DEFAULT')
            value, error = self.finish_connection(task)
        self.assertIsNone(value)
        self.assertIsInstance(error, PlacementProofError)
        self.assertFalse(PlacementAcknowledgement.objects.exists())
        expected = current_expectation(self.namespace.pk, self.machine.pk)
        if expected is None or expected.location is None:
            self.fail('The committed native placement must remain qualified')
        self.assertEqual(expected.fence, 2)
        self.assertEqual(expected.native_version, 1)
        self.assertEqual(expected.location.pk, self.destination.pk)
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)

    def test_later_connection_ack_wins_over_delayed_observation(self):
        """A slower earlier reservation cannot replace a later accepted generation."""
        entered, release = Event(), Event()
        observe = self.served.observe_placement

        def held(binding, machine):
            observation = observe(binding, machine)
            entered.set()
            if not release.wait(10):
                raise AssertionError('Owned serving barrier timed out')
            return observation

        self.served.observe_placement = held
        task = self.start_connection(
            lambda: acknowledge_latest(
                self.enrollment.pk, self.machine.pk, adapter=self.served
            )
        )
        latest = SyntheticServingAdapter(self.binding, self.served.placement)
        latest.generation = 'synthetic-generation-newer'
        try:
            self.assertTrue(entered.wait(10), 'Earlier reservation was not reached')
            self.assertTrue(
                acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=latest)
            )
        finally:
            release.set()
            value, error = self.finish_connection(task)
        self.assertIsNone(value)
        self.assertIsInstance(error, PlacementProofError)
        accepted = PlacementAcknowledgement.objects.get()
        self.assertEqual(accepted.source_revision, 1)
        self.assertEqual(accepted.native_version, 0)
        self.assertEqual(accepted.serving_generation, latest.generation)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.observation_revision, 2)
        self.assertTrue(self.enrollment.generation_known)
        self.assertEqual(self.enrollment.served_generation, latest.generation)
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertFalse(MachineLocationTransfer.objects.exists())
        self.assertFalse(MachinePlacementHistory.objects.exists())

    def race_transfers(self, payloads):
        """Release actual native commands together, each with a freshly read actor."""
        gate = Barrier(len(payloads) + 1)

        def run(payload):
            actor = get_user_model().objects.get(pk=self.actor.pk)
            gate.wait(timeout=10)
            return transfer_machines(actor, payload)

        tasks = [
            self.start_connection(lambda payload=payload: run(payload))
            for payload in payloads
        ]
        try:
            gate.wait(timeout=10)
        finally:
            outcomes = [self.finish_connection(task) for task in tasks]
        self.assertEqual(len(self.worker_pids), len(payloads))
        return outcomes

    def transfer_payload(self, *, key, destination):
        """Both callers independently submit the same original native version."""
        return {
            'machines': [
                {'machine_id': self.machine.pk, 'expected_placement_version': 0}
            ],
            'destination_location_id': destination,
            'reason': 'Synthetic independent transfer',
            'idempotency_key': key,
        }

    def assert_one_native_effect(self):
        """Reconcile source, history, receipt and durable capture after both commits."""
        self.machine.refresh_from_db()
        expected = current_expectation(self.namespace.pk, self.machine.pk)
        if expected is None:
            self.fail('The committed source must retain its captured placement')
        self.assertEqual(self.machine.placement_version, 1)
        self.assertEqual(expected.native_version, 1)
        self.assertEqual(expected.fence, 2)
        self.assertEqual(
            None if expected.location is None else expected.location.pk,
            self.machine.physical_location_id,
        )
        history = MachinePlacementHistory.objects.get()
        self.assertEqual(history.location_id, self.machine.physical_location_id)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_concurrent_same_key_replay_has_one_native_and_capture_effect(self):
        """Duplicate real commands share one committed receipt, history and fence."""
        payload = self.transfer_payload(key=uuid4(), destination=self.destination.pk)
        outcomes = self.race_transfers([payload, dict(payload)])
        self.assertTrue(all(error is None for _, error in outcomes), outcomes)
        results = [value for value, _ in outcomes]
        self.assertCountEqual([value['replayed'] for value in results], [False, True])
        self.assertEqual(
            {**results[0], 'replayed': True}, {**results[1], 'replayed': True}
        )
        self.assert_one_native_effect()

    def test_concurrent_stale_transfers_leave_one_qualified_winner(self):
        """Native conflict semantics remain authoritative with active capture."""
        outcomes = self.race_transfers([
            self.transfer_payload(key=uuid4(), destination=self.destination.pk),
            self.transfer_payload(key=uuid4(), destination=None),
        ])
        winners = [value for value, error in outcomes if error is None]
        failures = [error for _, error in outcomes if error is not None]
        self.assertEqual(len(winners), 1, outcomes)
        self.assertEqual(len(failures), 1, outcomes)
        self.assertIsInstance(failures[0], LocationConflict)
        self.assertEqual(failures[0].status_code, 409)
        self.assert_one_native_effect()

    def test_bootstrap_final_population_observes_an_earlier_page_native_commit(self):
        """An actual separate writer advances an earlier page before activation."""
        from integration_state import services

        second = AssetMachine.objects.create(
            name='Synthetic later bootstrap page', client=self.owner
        )
        previous_enrollment = self.enrollment.pk
        self.enrollment = prepare_projection(
            self.namespace.pk, 'synthetic-projection', self.owner.pk
        )
        first = bootstrap_projection(self.enrollment.pk, batch_size=1)
        self.assertEqual(first, {'processed': 1, 'complete': False})
        entered, release = Event(), Event()
        population_complete = services._population_complete

        def held(enrollment):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Owned bootstrap barrier timed out')
            return population_complete(enrollment)

        with patch.object(services, '_population_complete', side_effect=held):
            task = self.start_connection(
                lambda: bootstrap_projection(self.enrollment.pk, batch_size=1)
            )
            try:
                self.assertTrue(
                    entered.wait(10), 'Final baseline check was not reached'
                )
                with connection.cursor() as cursor:
                    cursor.execute('SET statement_timeout = 8000')
                self.move(version=0)
            finally:
                release.set()
                with connection.cursor() as cursor:
                    cursor.execute('SET statement_timeout TO DEFAULT')
                value, error = self.finish_connection(task)
        self.assertIsNone(error)
        self.assertEqual(value, {'processed': 1, 'complete': True})
        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.active)
        self.assertTrue(self.enrollment.bootstrapped)
        self.assertEqual(self.enrollment.bootstrap_status, 'ready')
        self.assertEqual(self.enrollment.bootstrap_cursor, second.pk)
        self.assertFalse(
            ProjectionEnrollment.objects.get(pk=previous_enrollment).active
        )
        expected = current_expectation(self.namespace.pk, self.machine.pk)
        if expected is None or expected.location is None:
            self.fail('The earlier page must retain its concurrent native change')
        self.assertEqual(expected.native_version, 1)
        self.assertEqual(expected.fence, 2)
        self.assertEqual(expected.location.pk, self.destination.pk)
        self.assertEqual(SourceChange.objects.count(), 3)
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_duplicate_bootstrap_connections_do_not_duplicate_baseline(self):
        """Concurrent work on one preparation preserves one source event and epoch."""
        self.enrollment = prepare_projection(
            self.namespace.pk, 'synthetic-projection', self.owner.pk
        )
        gate = Barrier(3)

        def bootstrap():
            gate.wait(timeout=10)
            return bootstrap_projection(self.enrollment.pk, batch_size=1)

        tasks = [self.start_connection(bootstrap) for _ in range(2)]
        try:
            gate.wait(timeout=10)
        finally:
            outcomes = [self.finish_connection(task) for task in tasks]
        self.assertTrue(all(error is None for _, error in outcomes), outcomes)
        self.assertTrue(all(value['complete'] for value, _ in outcomes))
        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.active)
        self.assertEqual(self.enrollment.bootstrap_status, 'ready')
        self.assertEqual(SourceState.objects.count(), 1)
        self.assertEqual(SourceChange.objects.count(), 1)
        self.assertEqual(len(self.worker_pids), 2)
        self.assertFalse(MachinePlacementHistory.objects.exists())
        self.assertFalse(PlacementAcknowledgement.objects.exists())

    def test_fresh_django_processes_recover_pending_and_current_across_later_move(self):
        """Exited interpreters cannot be the memory owner of source or accepted ack."""
        self.assertEqual(connection.settings_dict['NAME'], 's5_control_test')
        self.assertEqual(connection.settings_dict['HOST'], '/var/run/postgresql')
        evidence = Path(os.environ['TMPDIR']) / f's5-process-probes-{uuid4().hex}'
        evidence.mkdir()
        observations = []

        def probe(*, acknowledge=False, version=0, fence=1, location=None):
            generation = f'synthetic-process-generation-{fence}'
            payload = {
                'root': str(Path(__file__).resolve().parents[2]),
                'namespace': self.namespace.pk,
                'enrollment': self.enrollment.pk,
                'actor': self.actor.pk,
                'client': self.owner.pk,
                'machine': self.machine.pk,
                'location': location,
                'version': version,
                'fence': fence,
                'generation': generation,
                'acknowledge': acknowledge,
            }
            completed = subprocess.run(
                [sys.executable, '-B', '-c', PROCESS_BOOTSTRAP],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            receipt = {
                'native_exit': completed.returncode,
                'stdout': completed.stdout,
                'stderr': completed.stderr,
            }
            (evidence / f'{len(observations):02d}.json').write_text(
                json.dumps(receipt, indent=2) + '\n'
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            returned = [
                line.removeprefix('S5_PROCESS_PROBE=')
                for line in completed.stdout.splitlines()
                if line.startswith('S5_PROCESS_PROBE=')
            ]
            self.assertEqual(len(returned), 1, completed.stdout)
            result = json.loads(returned[0])
            self.assertNotEqual(result['pid'], os.getpid())
            self.assertNotIn(result['pid'], [row['pid'] for row in observations])
            self.assertNotIn(
                result['backend_pid'], [row['backend_pid'] for row in observations]
            )
            observations.append(result)
            return result['metadata']

        self.assertEqual(probe()['state'], 'pending')
        self.assertEqual(probe(acknowledge=True)['state'], 'current')
        self.assertEqual(probe()['state'], 'current')
        self.move(version=0)
        later = probe(version=1, fence=2, location=self.destination.pk)
        self.assertEqual(later['state'], 'pending')
        self.assertEqual(later['source_fence'], '2')
        self.assertEqual(later['native_version'], '1')
        self.assertEqual(
            probe(acknowledge=True, version=1, fence=2, location=self.destination.pk)[
                'state'
            ],
            'current',
        )
        self.assertEqual(
            probe(version=1, fence=2, location=self.destination.pk)['state'], 'current'
        )
        accepted = PlacementAcknowledgement.objects.get()
        self.assertEqual(accepted.source_revision, 2)
        self.assertEqual(accepted.native_version, 1)
        self.assertEqual(SourceChange.objects.count(), 2)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 1)

    def ordinary_reader(self):
        """Use real native roles/grants; no superuser or fake permission verdict."""
        from assets.models import ClientScopeGrant

        reader = get_user_model().objects.create_user(username='synthetic-s5-reader')
        group = Group.objects.create(name='Synthetic S5 ordinary readers')
        reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        # Same reduced-registry fixture as the qualified native S2 role controls;
        # this does not qualify optional admin-role provisioning signals.
        RuleSet.objects.filter(group=group, name='admin').update(can_view=True)
        ClientScopeGrant.objects.create(user=reader, client=self.owner)
        self.assertFalse(reader.is_superuser)
        return reader, group

    def assert_late_http_change_withholds_rows(
        self, change, *, resolver=None, oauth_change=None, field_allowed=None
    ):
        """A positive real native GET precedes a separately committed authority loss."""
        from assets.integration_s5_pending import PlacementReadAdapter
        from assets.location_api import LocationView

        reader, group = self.ordinary_reader()
        acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        authentication = nullcontext()
        token = None
        if oauth_change is not None:
            from oauth2_provider.contrib.rest_framework.authentication import (
                OAuth2Authentication,
            )
            from oauth2_provider.models import get_access_token_model

            from users.oauth2_scopes import get_granular_scope

            token = get_access_token_model().objects.create(
                user=reader,
                token=f's5-synthetic-{uuid4().hex}',
                scope=' '.join((
                    get_granular_scope('view', 'work_order'),
                    get_granular_scope('view', 'admin'),
                )),
                expires=timezone.now() + timedelta(hours=1),
            )

            class SyntheticOAuth(OAuth2Authentication):
                """Only credential transport is scripted; stored scope is real."""

                def authenticate(self, request):
                    """Retain the initially valid principal/token across late revocation."""
                    return reader, token

            authentication = patch.object(
                LocationView, 'authentication_classes', [SyntheticOAuth]
            )

        def factory(request):
            return PlacementReadAdapter(
                actor_pk=reader.pk,
                namespace_id=self.namespace.pk,
                projection='synthetic-projection',
                authorize_metadata=lambda request, binding, placement: (
                    True if field_allowed is None else field_allowed[0]
                ),
                serving=self.served,
            )

        def read():
            api = APIClient()
            if oauth_change is None:
                api.force_authenticate(reader)
            response = api.get('/api/assets/locations/machines/')
            return response.status_code, response.json()

        overrides = {'AIMMS_PLACEMENT_STATE_ADAPTER': factory}
        if resolver is not None:
            overrides['AIMMS_MAINTENANCE_SCOPE_RESOLVER'] = resolver
        with override_settings(**overrides), authentication:
            status, data = read()
            self.assertEqual(status, 200, data)
            self.assertEqual(data['count'], 1)
            self.assertEqual(data['results'][0]['placement_sync']['state'], 'current')
            entered, release = Event(), Event()
            observe = self.served.observe_placement

            def held(binding, machine):
                observation = observe(binding, machine)
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Owned disclosure barrier timed out')
                return observation

            self.served.observe_placement = held
            task = self.start_connection(read)
            try:
                self.assertTrue(entered.wait(10), 'Authorized serving read not reached')
                if oauth_change is None:
                    change(reader, group)
                else:
                    oauth_change(token)
            finally:
                release.set()
                result, error = self.finish_connection(task)
                self.served.observe_placement = observe
        self.assertIsNone(error)
        status, data = result
        self.assertIn(status, (403, 503), data)
        self.assertNotIn('results', data)
        self.assertNotIn('placement_sync', data)
        self.assertNotIn(self.machine.name, json.dumps(data))
        self.assertNotIn(self.owner.name, json.dumps(data))

    def test_ordinary_role_revocation_during_independent_http_serving_read(self):
        """The native role is refreshed after IO, not inherited from request entry."""
        self.assert_late_http_change_withholds_rows(
            lambda reader, group: RuleSet.objects.filter(
                group=group, name='work_order'
            ).update(can_view=False)
        )

    def test_account_revocation_during_independent_http_serving_read(self):
        """A still-authenticated request cannot retain a disabled account's rows."""
        self.assert_late_http_change_withholds_rows(
            lambda reader, group: (
                get_user_model().objects.filter(pk=reader.pk).update(is_active=False)
            )
        )

    def test_grant_revocation_during_independent_http_serving_read(self):
        """Native and explicit grant loss cannot leave cached metadata or rows."""
        from assets.models import ClientScopeGrant

        self.assert_late_http_change_withholds_rows(
            lambda reader, group: ClientScopeGrant.objects.filter(
                user=reader, client=self.owner
            ).delete()
        )

    def test_client_revocation_during_independent_http_serving_read(self):
        """Client deactivation fails closed even with the account and grant intact."""
        from assets.models import Client

        self.assert_late_http_change_withholds_rows(
            lambda reader, group: Client.objects.filter(pk=self.owner.pk).update(
                active=False
            )
        )

    def test_transferred_out_source_during_independent_http_serving_read(self):
        """The old client's reader cannot learn the newly assigned owner/location."""
        from assets.models import Client

        foreign = Client.objects.create(name='Synthetic private owner', code='s5-other')
        self.assert_late_http_change_withholds_rows(
            lambda reader, group: AssetMachine.objects.filter(
                pk=self.machine.pk
            ).update(client=foreign)
        )

    def test_foreign_location_during_independent_http_serving_read(self):
        """An invalid native relationship is withheld, not copied as serving truth."""
        from assets.models import AssetLocation, Client

        foreign = Client.objects.create(name='Synthetic private owner', code='s5-other')
        location = AssetLocation.objects.create(
            client=foreign,
            name='Synthetic private location',
            name_key='synthetic private location',
            code='s5-private-location',
            sibling_key='root',
            timezone='UTC',
        )
        self.assert_late_http_change_withholds_rows(
            lambda reader, group: AssetMachine.objects.filter(
                pk=self.machine.pk
            ).update(physical_location=location)
        )

    def test_native_scope_narrowing_with_grant_retained_during_http_read(self):
        """A scripted native-scope seam independently narrows with a real grant."""
        from assets.models import ClientScopeGrant

        allowed = [True]

        def resolver(actor):
            return [{'client_id': self.owner.pk}] if allowed[0] else []

        def narrow(reader, group):
            self.assertTrue(
                ClientScopeGrant.objects.filter(user=reader, client=self.owner).exists()
            )
            allowed[0] = False

        self.assert_late_http_change_withholds_rows(narrow, resolver=resolver)

    def test_oauth_scope_narrowing_during_independent_http_serving_read(self):
        """A cached valid OAuth intake cannot override current stored read scope."""
        from users.oauth2_scopes import get_granular_scope

        self.assert_late_http_change_withholds_rows(
            None,
            oauth_change=lambda token: (
                type(token)
                .objects.filter(pk=token.pk)
                .update(scope=get_granular_scope('view', 'part'))
            ),
        )

    def test_oauth_token_removal_during_independent_http_serving_read(self):
        """A deleted credential cannot keep emitting authorized-looking rows."""
        self.assert_late_http_change_withholds_rows(
            None,
            oauth_change=lambda token: type(token).objects.filter(pk=token.pk).delete(),
        )

    def test_metadata_field_policy_loss_during_independent_http_serving_read(self):
        """Source access alone cannot authorize revoked projection bookkeeping."""
        allowed = [True]
        self.assert_late_http_change_withholds_rows(
            lambda reader, group: allowed.__setitem__(0, False), field_allowed=allowed
        )

    def test_proof_timeout_does_not_prevent_committed_native_transfer_or_read(self):
        """A failed remote observation is typed; source writes have no remote gate."""
        from assets.integration_s5_ack import PlacementProofUnavailableError
        from assets.integration_s5_pending import PlacementReadAdapter

        def offline(binding, machine):
            raise TimeoutError('Synthetic serving outage')

        self.served.observe_placement = offline
        with self.assertRaises(PlacementProofUnavailableError):
            acknowledge_latest(self.enrollment.pk, self.machine.pk, adapter=self.served)
        self.move(version=0)
        self.assert_one_native_effect()
        policy = PlacementReadAdapter(
            actor_pk=self.actor.pk,
            namespace_id=self.namespace.pk,
            projection='synthetic-projection',
            authorize_metadata=lambda request, binding, placement: True,
            serving=self.served,
        )
        with override_settings(AIMMS_PLACEMENT_STATE_ADAPTER=lambda request: policy):
            api = APIClient()
            api.force_authenticate(self.actor)
            response = api.get('/api/assets/locations/machines/')
        self.assertEqual(response.status_code, 200)
        row = response.json()['results'][0]
        self.assertEqual(row['placement_version'], 1)
        self.assertEqual(row['physical_location']['pk'], self.destination.pk)
        self.assertEqual(row['placement_sync']['state'], 'pending')
        self.assertEqual(row['placement_sync']['source_fence'], '2')


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class PlacementBatchCommitTests(NativePlacementFixture, TransactionTestCase):
    """Native multi-machine commits and rollback with source capture enabled."""

    def setUp(self):
        """Keep native command execution and real committed capture rows."""
        super().setUp()
        self.namespace = activate_namespace('synthetic-s5')
        self.enrollment = prepare_projection(
            self.namespace.pk, 'synthetic-projection', self.owner.pk
        )
        bootstrap_projection(self.enrollment.pk)
        self.second = AssetMachine.objects.create(
            name='Synthetic second batch member', client=self.owner
        )

    def batch(self, *, second_version=0, second_id=None):
        """Submit independently specified versions to the unchanged native writer."""
        return transfer_machines(
            self.actor,
            {
                'machines': [
                    {'machine_id': self.machine.pk, 'expected_placement_version': 0},
                    {
                        'machine_id': self.second.pk
                        if second_id is None
                        else second_id,
                        'expected_placement_version': second_version,
                    },
                ],
                'destination_location_id': self.destination.pk,
                'reason': 'Synthetic committed batch',
                'idempotency_key': uuid4(),
            },
        )

    def test_native_batch_commits_both_exact_expectations_and_histories(self):
        """Two native placements, not one selected row, share the atomic receipt."""
        result = self.batch()
        self.assertFalse(result['replayed'])
        self.assertEqual(len(result['machines']), 2)
        for machine in (self.machine, self.second):
            machine.refresh_from_db()
            expected = current_expectation(self.namespace.pk, machine.pk)
            if expected is None or expected.location is None:
                self.fail('Every committed batch member needs its exact expectation')
            self.assertEqual(machine.physical_location_id, self.destination.pk)
            self.assertEqual(machine.placement_version, 1)
            self.assertEqual(expected.client.pk, self.owner.pk)
            self.assertEqual(expected.location.pk, self.destination.pk)
            self.assertEqual(expected.native_version, 1)
            self.assertEqual(expected.fence, 2)
            self.assertEqual(expected.namespace_epoch, self.namespace.epoch)
        self.assertEqual(MachineLocationTransfer.objects.count(), 1)
        self.assertEqual(MachinePlacementHistory.objects.count(), 2)
        self.assertEqual(SourceChange.objects.count(), 4)

    def test_second_member_event_failure_rolls_back_first_captured_member(self):
        """A real later trigger failure leaves no earlier operational or marker effect."""
        from django.db import IntegrityError

        SourceChange.objects.create(
            namespace=self.namespace,
            epoch=self.namespace.epoch,
            entity_kind='machine',
            aspect='placement',
            source_id=self.second.pk,
            revision=2,
            operation='baseline',
            previous=None,
            current=None,
        )
        save = AssetMachine.save
        observed = []

        def observe(machine, *args, **kwargs):
            result = save(machine, *args, **kwargs)
            if machine.pk == self.machine.pk:
                observed.append(
                    SourceState.objects.get(
                        namespace=self.namespace, source_id=machine.pk
                    ).revision
                )
            return result

        with (
            patch.object(AssetMachine, 'save', autospec=True, side_effect=observe),
            self.assertRaises(IntegrityError),
        ):
            self.batch()
        self.assertEqual(observed, [2])
        for machine in (self.machine, self.second):
            machine.refresh_from_db()
            self.assertEqual(machine.placement_version, 0)
            self.assertIsNone(machine.physical_location_id)
            expected = current_expectation(self.namespace.pk, machine.pk)
            if expected is None:
                self.fail('Rollback must retain the original captured expectation')
            self.assertEqual(expected.native_version, 0)
            self.assertEqual(expected.fence, 1)
        self.assertFalse(MachineLocationTransfer.objects.exists())
        self.assertFalse(MachinePlacementHistory.objects.exists())
        self.assertEqual(SourceChange.objects.count(), 3)
        self.assertFalse(
            SourceChange.objects.filter(source_id=self.machine.pk, revision=2).exists()
        )

    def test_stale_and_foreign_members_refuse_without_partial_capture(self):
        """Both validation failures preserve every original source and marker."""
        from rest_framework.exceptions import ValidationError

        from assets.models import Client

        foreign = Client.objects.create(name='Synthetic batch foreign', code='s5-batch')
        hidden = AssetMachine.objects.create(
            name='Synthetic hidden member', client=foreign
        )
        before = list(SourceState.objects.order_by('pk').values())
        for kwargs, error in (
            ({'second_version': 1}, LocationConflict),
            ({'second_id': hidden.pk}, ValidationError),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                self.batch(**kwargs)
            self.assertEqual(list(SourceState.objects.order_by('pk').values()), before)
            self.assertFalse(MachineLocationTransfer.objects.exists())
            self.assertFalse(MachinePlacementHistory.objects.exists())
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.placement_version, 0)
        self.assertIsNone(self.machine.physical_location_id)

    def test_actual_s5_database_rows_roundtrip_through_s7_observation_adapter(self):
        """Private producer/consumer compatibility is not delivery or disclosure."""
        from integration_state.s7_changes import (
            PLACEMENT_CHANGE_FIELDS,
            placement_change_from_values,
            placement_state_from_expectation,
        )

        self.batch()
        expected = current_expectation(self.namespace.pk, self.machine.pk)
        observed = placement_state_from_expectation(expected)
        self.assertEqual(observed.revision, 2)
        self.assertEqual(observed.placement.location_id, self.destination.pk)
        record = SourceChange.objects.values(*PLACEMENT_CHANGE_FIELDS).get(
            namespace=self.namespace, source_id=self.machine.pk, revision=2
        )
        change = placement_change_from_values(
            environment='synthetic-s5',
            namespace_id=self.namespace.pk,
            epoch=self.namespace.epoch,
            record=record,
        )
        self.assertEqual(change.event_id, record['id'])
        self.assertEqual(change.revision, 2)
        self.assertIsNone(change.previous.location_id)
        self.assertEqual(change.current.location_id, self.destination.pk)
        self.assertEqual(change.current.native_version, 1)
        self.assertEqual(change.tombstones, ())
        self.assertFalse(change.removal_complete)
        self.assertFalse(change.deletion_applied)
