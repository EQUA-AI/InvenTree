"""Exited-process S7 private recovery controls in the owned SQLite lane only.

These sequential real child interpreters do not prove PostgreSQL concurrency,
production worker failover, authenticated deletion, complete detection or erasure.
"""

from integration_state.tests.assertions import assert_literal_bool

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from django.db import connection
from django.test import TransactionTestCase, override_settings

from integration_state.tests.test_s7_authority_db import NativePlanningContext

_CHILD_BOOTSTRAP = r"""
import json, os, sys
from pathlib import Path
runtime = Path(os.environ['S7_RUNTIME'])
source = runtime.parent / 'source'
database = runtime / 'native-test.sqlite3'
if Path.cwd() != source or not database.is_file() or database.is_symlink():
    raise RuntimeError('Fresh child requires the exact existing owned database')
if Path(os.environ['S7_HIDDEN_ROOT']).exists():
    raise RuntimeError('Fresh child refuses a visible live checkout')
sys.path[:0] = [str(source / 'src/backend/InvenTree'), str(source / 'src/backend')]
os.environ['DJANGO_SETTINGS_MODULE'] = 'ai.core.tests.settings'
sys.argv = ['manage.py', 'test']
from django.conf import settings
if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
    raise RuntimeError('Fresh child refuses another database engine')
settings.DATABASES['default']['NAME'] = str(database)
settings.DATABASES['default']['TEST'] = {'NAME': str(database)}
settings.Q_CLUSTER = {**settings.Q_CLUSTER, 'orm': 'default', 'cluster_name': runtime.parent.name}
settings.S5_NATIVE_MIGRATIONS = False
settings.AIMMS_PLANT_TIMEZONE = ''
settings.AIMMS_MAINTENANCE_SCOPE_RESOLVER = 'tasks.scope.single_site_scope_resolver'
settings.AIMMS_SINGLE_SITE_CLIENT_CODE = 's5-owner'
settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, 'integration_state.apps.IntegrationStateConfig']
from oauth2_provider import settings as oauth_defaults
for name in ('APPLICATION', 'DEVICE_GRANT', 'ACCESS_TOKEN', 'ID_TOKEN', 'GRANT', 'REFRESH_TOKEN'):
    setattr(settings, f'OAUTH2_PROVIDER_{name}_MODEL', getattr(oauth_defaults, f'{name}_MODEL'))
import django
django.setup()
from django.apps import apps
settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
from integration_state.tests.test_s7_concurrency import _child_main
raise SystemExit(_child_main(json.loads(sys.stdin.read())))
"""


def _owned_paths():
    """Resolve only the frozen source and previously created owned test database."""
    runtime = Path(os.environ['S7_RUNTIME'])
    source = Path(__file__).resolve().parents[5]
    expected_base = Path(
        '/home/lokesh/.hermes/cache/scratch/aimms-s7-implementation-20261111T222207Z'
    )
    if (
        runtime.parent.parent != expected_base
        or not runtime.parent.name.startswith('restart-consumer-20261112T165140Z--')
        or source != runtime.parent / 'source'
        or runtime.is_symlink()
        or Path(os.environ['S7_HIDDEN_ROOT']).exists()
    ):
        raise RuntimeError('Refuse an unqualified S7 process-recovery runtime')
    database = runtime / 'native-test.sqlite3'
    if (
        connection.vendor != 'sqlite'
        or Path(connection.settings_dict['NAME']).resolve() != database
        or not database.is_file()
        or database.is_symlink()
        or connection.in_atomic_block
        or not connection.get_autocommit()
    ):
        raise RuntimeError('The child must reuse the committed owned SQLite database')
    return runtime, source, database


def _source_guard(source, runtime):
    """Bind every imported project module and frozen source member independently."""
    manifest = json.loads((runtime.parent / 'source-manifest.json').read_text())
    declared = {row['path']: row['sha256'] for row in manifest['files']}
    if len(declared) != len(manifest['files']):
        raise RuntimeError('Duplicate frozen child inputs')
    for relative, expected in declared.items():
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Frozen child source changed')
    bindings = {}
    for name, module in tuple(sys.modules.items()):
        value = getattr(module, '__file__', None)
        if type(value) is str and Path(value).resolve().is_relative_to(source):
            relative = str(Path(value).resolve().relative_to(source))
            if relative not in declared:
                raise RuntimeError('Fresh child imported an unbound project module')
            bindings[name] = {'path': relative, 'sha256': declared[relative]}
    return bindings


def _native_read(payload):
    """Re-evaluate the real ordinary actor/role/scope/grant in this interpreter."""
    from django.contrib.auth import get_user_model

    from assets.integration_machine_location import read_machine_placement
    from assets.models import AssetMachine

    if connection.in_atomic_block:
        raise RuntimeError('Native eligibility cannot run in metadata ownership IO')
    actor = get_user_model().objects.get(pk=payload['reader_pk'])
    if actor.is_superuser:
        return False
    edge = read_machine_placement(
        actor, environment=payload['environment'], machine_pk=payload['machine_pk']
    )
    return bool(
        edge is not None
        and edge.machine.environment == payload['environment']
        and AssetMachine.objects.filter(
            pk=payload['machine_pk'], client_id=payload['client_pk']
        ).exists()
    )


def _scan_inputs(payload):
    from uuid import UUID

    from integration_state.s7_reconciliation import ScanBinding

    binding = ScanBinding(
        environment=payload['environment'],
        epoch=UUID(payload['epoch']),
        entity_kind='machine',
        aspect='placement',
        actor_key=payload['actor_key'],
        client_id=payload['client_pk'],
        filter_key=payload['filter_key'],
        generation=payload['generation'],
    )
    return binding, lambda value: value == binding and _native_read(payload)


def _scan_result(snapshot):
    progress = snapshot.progress
    return {
        'scan_id': snapshot.scan_id,
        'version': snapshot.version,
        'state': progress.state,
        'attempts_used': progress.attempts_used,
        'pending_attempt': None
        if progress.pending_attempt is None
        else str(progress.pending_attempt),
        'last_attempt': None
        if progress.last_attempt is None
        else str(progress.last_attempt),
        'seen_count': len(progress.seen),
        'population_complete': progress.population_complete,
        'removal_complete': progress.removal_complete,
        'deletion_applied': progress.deletion_applied,
        'tombstones': list(progress.tombstones),
    }


def _copy_inputs(payload):
    """Reconstruct explicit fixture claims, never infer copy ownership from a row."""
    from uuid import UUID

    from assets.integration_contracts import ClientSourceIdentity
    from integration_state.s7_contracts import SourceAspectIdentity
    from integration_state.s7_deletion_requests import CopyImpact, DeletionIntent

    client = ClientSourceIdentity(payload['environment'], payload['client_pk'])
    source = SourceAspectIdentity(
        environment=payload['environment'],
        epoch=UUID(payload['epoch']),
        entity_kind='machine',
        aspect='placement',
        source_id=payload['machine_pk'],
    )
    intent = DeletionIntent(
        request_id=UUID(payload['request_id']),
        request_key='private-restart-copy',
        request_revision=1,
        actor_key=payload['actor_key'],
        client=client,
        scope=(source,),
    )
    impact = CopyImpact(
        copy_key='private-restart-copy-claim',
        source=source,
        copy_revision='fixture-only-revision',
        owners=(client,),
        attribution='source_claimed',
        retention='reviewable',
    )
    return intent, impact, lambda value: value == intent and _native_read(payload)


def _copy_result(snapshot):
    progress = snapshot.progress
    return {
        'attempt_pk': snapshot.attempt_pk,
        'impact_pk': snapshot.impact_pk,
        'version': snapshot.version,
        'sequence': progress.sequence,
        'status': progress.status,
        'attempts_used': progress.attempts_used,
        'max_attempts': progress.max_attempts,
        'attempt_history': [str(value) for value in progress.attempt_history],
        'synthetic_only': progress.synthetic_only,
        'native_authorized': snapshot.native_authorized,
        'coverage_complete': snapshot.coverage_complete,
        'deletion_applied': snapshot.deletion_applied,
        'erasure_complete': snapshot.erasure_complete,
    }


def _child_operation(payload):
    """Execute a declared test-only operation against actual persisted metadata."""
    from integration_state.models import SourceNamespace

    operation = payload['operation']
    if operation == 'read_namespace':
        namespace = SourceNamespace.objects.get(pk=payload['namespace_id'])
        return {'namespace_epoch': str(namespace.epoch), 'active': namespace.active}
    if operation in ('scan_read', 'scan_reserve', 'scan_accept', 'scan_replay'):
        from dataclasses import replace

        from django.contrib.auth import get_user_model

        from tasks.scope import scoped_machines

        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_reconciliation import ScanPage
        from integration_state.shared_reconciliation import (
            accept_scan_page,
            read_scan,
            reserve_scan_page,
        )

        binding, authorize = _scan_inputs(payload)
        snapshot = read_scan(payload['scan_id'], binding=binding, authorize=authorize)
        if operation == 'scan_reserve':
            snapshot = reserve_scan_page(snapshot, authorize=authorize)
        elif operation == 'scan_accept':
            actor = get_user_model().objects.get(pk=payload['reader_pk'])
            keys = tuple(
                scoped_machines(actor)
                .filter(pk=payload['machine_pk'])
                .order_by('pk')
                .values_list('pk', flat=True)
            )
            identity = SourceAspectIdentity(
                environment=binding.environment,
                epoch=binding.epoch,
                entity_kind=binding.entity_kind,
                aspect=binding.aspect,
                source_id=payload['machine_pk'],
            )
            page = ScanPage(
                binding=binding,
                offset=0,
                total=len(keys),
                items=tuple(replace(identity, source_id=key) for key in keys),
                next_offset=None,
            )
            snapshot = accept_scan_page(
                snapshot,
                page,
                attempt=snapshot.progress.pending_attempt,
                authorize=authorize,
            )
        elif operation == 'scan_replay':
            snapshot = accept_scan_page(
                snapshot,
                snapshot.progress.last_page,
                attempt=snapshot.progress.last_attempt,
                authorize=authorize,
            )
        return _scan_result(snapshot)
    if operation in (
        'copy_read',
        'copy_pending',
        'copy_uncertain',
        'copy_replay',
        'copy_retry',
        'copy_reset',
    ):
        from dataclasses import replace

        from integration_state import shared_requests as durable

        intent, impact, authorize = _copy_inputs(payload)
        snapshot = durable.read_private_copy_progress(
            payload['impact_pk'], intent=intent, impact=impact, authorize=authorize
        )
        if operation in ('copy_pending', 'copy_uncertain', 'copy_replay'):
            sequence = snapshot.progress.sequence + 1
            status = 'pending' if operation != 'copy_uncertain' else 'uncertain'
            if operation == 'copy_replay':
                sequence = snapshot.progress.sequence - 1
            proposed = replace(snapshot.progress, sequence=sequence, status=status)
            snapshot = durable.record_private_copy_progress(
                snapshot, proposed, intent=intent, impact=impact, authorize=authorize
            )
        elif operation == 'copy_retry':
            snapshot = durable.retry_private_copy_attempt(
                snapshot,
                intent=intent,
                impact=impact,
                attempt=uuid4(),
                authorize=authorize,
            )
        elif operation == 'copy_reset':
            snapshot = durable.start_private_copy_attempt(
                payload['impact_pk'],
                intent=intent,
                impact=impact,
                attempt=uuid4(),
                max_attempts=2,
                authorize=authorize,
            )
        return _copy_result(snapshot)
    if operation == 'delivery_claim':
        from integration_state.shared_delivery import claim_changes

        claims = claim_changes(
            payload['consumer_id'],
            worker_key='private-restart-worker',
            limit=1,
            lease_seconds=300,
        )
        if len(claims) != 1:
            raise RuntimeError('The committed fixture event needs exactly one claim')
        return {
            'delivery_id': claims[0].delivery_id,
            'change_id': claims[0].change_id,
            'ownership_fence': claims[0].ownership_fence,
        }
    if operation in ('delivery_read', 'delivery_ack'):
        from integration_state.models import EventConsumer, EventDelivery
        from integration_state.s7_changes import delivery_change_from_claim
        from integration_state.shared_delivery import _claim, _configuration, ack_change

        namespace = SourceNamespace.objects.get(pk=payload['namespace_id'])
        consumer = EventConsumer.objects.get(pk=payload['consumer_id'])
        row = EventDelivery.objects.get(pk=payload['delivery_id'], consumer=consumer)
        # Test-only recovery uses S5's actual retained-row carrier constructors.
        # This installs neither a production worker nor a current ownership grant.
        configuration = _configuration(consumer, namespace)
        claim = _claim(row, consumer)
        decoded = delivery_change_from_claim(
            environment=payload['environment'],
            worker_key='private-restart-worker',
            configuration=configuration,
            claim=claim,
        )
        receipt = None
        if operation == 'delivery_ack':
            acknowledged = ack_change(decoded.claim, outcome='applied')
            receipt = {
                'outcome': acknowledged.outcome,
                'advanced': acknowledged.advanced,
                'deletion_applied': acknowledged.deletion_applied,
                'erasure_complete': acknowledged.erasure_complete,
            }
        row.refresh_from_db()
        return {
            'delivery_id': row.pk,
            'change_id': decoded.change.event_id,
            'captured_at': decoded.captured_at.isoformat(),
            'state': row.state,
            'ownership_verified': decoded.ownership_verified,
            'native_authorized': decoded.native_authorized,
            'receipt': receipt,
        }
    raise ValueError('Unadmitted recovery operation')


def _child_main(payload):
    """Retain actual child identity, unchanged source bindings and terminal exit."""
    from django.db import connections

    from integration_state.capture import capture_is_installed
    from integration_state.shared_events import SharedStateError

    runtime, source, database = _owned_paths()
    probe = Path(os.environ['S7_PROBE_DIRECTORY'])
    if (
        probe.parent != runtime / 'restart-probes'
        or not probe.is_dir()
        or probe.is_symlink()
    ):
        raise RuntimeError('Fresh child refuses an unowned receipt target')
    _source_guard(source, runtime)
    if capture_is_installed(connection) is not True:
        raise RuntimeError('Fresh child lost the existing capture installation')
    code = 0
    try:
        result = _child_operation(payload)
    except (PermissionError, SharedStateError) as error:
        result = {'refused': True, 'exception_type': type(error).__name__}
        code = 4
    abnormal = payload.get('terminate_after_commit') is True
    if abnormal:
        if payload['operation'] != 'scan_reserve' or code != 0:
            raise RuntimeError(
                'Abnormal exit requires its actual committed reservation'
            )
        code = 23
    bindings = _source_guard(source, runtime)
    stat = database.stat()
    record = {
        'pid': os.getpid(),
        'parent_pid': os.getppid(),
        'nonce': str(uuid4()),
        'network_namespace': os.readlink('/proc/self/ns/net'),
        'pid_namespace': os.readlink('/proc/self/ns/pid'),
        'database': str(database),
        'database_device': stat.st_dev,
        'database_inode': stat.st_ino,
        'autocommit': connection.get_autocommit(),
        'operation': payload['operation'],
        'result': result,
        'module_bindings': bindings,
        'input_guard': 'passed',
        'intended_exit': code,
    }
    with (probe / 'receipt.json').open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    if abnormal:
        os._exit(code)
    connections.close_all()
    return code


@unittest.skipUnless(
    os.environ.get('S7_RUNTIME'), 'Requires the owned S7 SQLite recovery profile'
)
@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='s5-owner',
)
class FreshProcessRecoveryTests(NativePlanningContext, TransactionTestCase):
    """Use real exited interpreters, never a connection reopen labelled restart."""

    def run_child(self, operation, *, expected_exit=0, **values):
        """Keep each child HOME/TMP/log/receipt private and preserve its actual exit."""
        runtime, source, database = _owned_paths()
        directory = runtime / 'restart-probes' / uuid4().hex
        directory.mkdir(parents=True)
        environment = {
            'PATH': '/usr/bin:/bin',
            'LANG': 'C.UTF-8',
            'HOME': str(directory),
            'TMPDIR': str(directory),
            'XDG_CACHE_HOME': str(directory / 'cache'),
            'PYTHONDONTWRITEBYTECODE': '1',
            'TESTING': '1',
            'S7_RUNTIME': str(runtime),
            'S7_HIDDEN_ROOT': os.environ['S7_HIDDEN_ROOT'],
            'S7_PROBE_DIRECTORY': str(directory),
        }
        completed = subprocess.run(
            [sys.executable, '-I', '-B', '-c', _CHILD_BOOTSTRAP],
            input=json.dumps({'operation': operation, **values}),
            cwd=source,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=40,
        )
        (directory / 'stdout.log').write_text(completed.stdout)
        (directory / 'stderr.log').write_text(completed.stderr)
        (directory / 'exit.json').write_text(
            json.dumps({'actual_exit': completed.returncode}) + '\n'
        )
        self.assertEqual(completed.returncode, expected_exit, completed.stderr)
        record = json.loads((directory / 'receipt.json').read_text())
        self.assertEqual(record['intended_exit'], completed.returncode)
        self.assertNotEqual(record['pid'], os.getpid())
        self.assertEqual(record['parent_pid'], os.getpid())
        self.assertEqual(record['database'], str(database))
        self.assertEqual(
            (record['database_device'], record['database_inode']),
            (database.stat().st_dev, database.stat().st_ino),
        )
        self.assertEqual(record['network_namespace'], os.readlink('/proc/self/ns/net'))
        self.assertEqual(record['pid_namespace'], os.readlink('/proc/self/ns/pid'))
        assert_literal_bool(self, record['autocommit'], True)
        self.assertEqual(record['input_guard'], 'passed')
        return record

    def test_committed_namespace_is_visible_in_distinct_exited_interpreters(self):
        """A qualified child observes the actual existing file and persisted epoch."""
        first = self.run_child('read_namespace', namespace_id=self.namespace.pk)
        second = self.run_child('read_namespace', namespace_id=self.namespace.pk)
        self.assertNotEqual(first['pid'], second['pid'])
        self.assertNotEqual(first['nonce'], second['nonce'])
        self.assertEqual(first['result'], second['result'])
        self.assertEqual(
            first['result'],
            {'namespace_epoch': str(self.namespace.epoch), 'active': True},
        )

    def recovery_coordinates(self):
        """Pass independently created fixture binding, never another actor's data."""
        return {
            'environment': self.namespace.environment,
            'epoch': str(self.namespace.epoch),
            'reader_pk': self.reader.pk,
            'machine_pk': self.machine.pk,
            'client_pk': self.owner.pk,
            'actor_key': self.actor_key,
            'filter_key': self.binding.filter_key,
            'generation': self.binding.generation,
        }

    def test_pending_scan_survives_abnormal_exit_and_fresh_accept_replay(self):
        """A committed reservation survives process death without a false acknowledgment."""
        from integration_state.models import ReconciliationAttempt
        from integration_state.shared_reconciliation import begin_scan, read_scan

        initial = begin_scan(
            self.namespace.pk,
            'private-restart-scan',
            self.binding,
            max_pages=2,
            max_records=1,
            authorize=self.scan_read_eligible,
        )
        values = {**self.recovery_coordinates(), 'scan_id': initial.scan_id}
        interrupted = self.run_child(
            'scan_reserve', expected_exit=23, terminate_after_commit=True, **values
        )
        retained = read_scan(
            initial.scan_id, binding=self.binding, authorize=self.scan_read_eligible
        )
        self.assertEqual(retained.progress.attempts_used, 1)
        self.assertEqual(
            interrupted['result']['pending_attempt'],
            str(retained.progress.pending_attempt),
        )
        self.assertEqual(ReconciliationAttempt.objects.get().state, 'pending')
        accepted = self.run_child('scan_accept', **values)
        replayed = self.run_child('scan_replay', **values)
        observed = self.run_child('scan_read', **values)
        self.assertEqual(accepted['result'], replayed['result'])
        self.assertEqual(accepted['result'], observed['result'])
        self.assertEqual(accepted['result']['state'], 'transport_complete')
        self.assertEqual(accepted['result']['seen_count'], 1)
        self.assertFalse(accepted['result']['population_complete'])
        self.assertFalse(accepted['result']['removal_complete'])
        self.assertFalse(accepted['result']['deletion_applied'])
        self.assertEqual(accepted['result']['tombstones'], [])
        final = read_scan(
            initial.scan_id, binding=self.binding, authorize=self.scan_read_eligible
        )
        self.assertEqual(final.progress.seen, frozenset((self.source,)))
        self.assertEqual(final.progress.attempts_used, 1)
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)
        self.assertEqual(ReconciliationAttempt.objects.get().state, 'accepted')
        self.assertEqual(
            len({row['pid'] for row in (interrupted, accepted, replayed, observed)}), 4
        )
        self.assert_native_untouched()

    def test_fresh_scan_reader_rechecks_independent_grant_and_ordinary_role(self):
        """A new interpreter cannot restore a prior reader's revoked eligibility."""
        from tasks.scope import scoped_machines

        from assets.integration_authority import explicitly_granted_client_ids
        from assets.models import ClientScopeGrant
        from integration_state.models import ReconciliationAttempt
        from integration_state.shared_reconciliation import (
            begin_scan,
            reserve_scan_page,
        )
        from users.models import RuleSet

        initial = begin_scan(
            self.namespace.pk,
            'private-revocation-scan',
            self.binding,
            max_pages=2,
            max_records=1,
            authorize=self.scan_read_eligible,
        )
        reserved = reserve_scan_page(initial, authorize=self.scan_read_eligible)
        values = {**self.recovery_coordinates(), 'scan_id': initial.scan_id}
        allowed = self.run_child('scan_read', **values)
        ClientScopeGrant.objects.filter(user=self.reader, client=self.owner).delete()
        try:
            self.assertTrue(
                scoped_machines(self.reader).filter(pk=self.machine.pk).exists()
            )
            self.assertFalse(explicitly_granted_client_ids(self.reader).exists())
            denied = self.run_child('scan_read', expected_exit=4, **values)
            self.assertEqual(
                denied['result'], {'refused': True, 'exception_type': 'PermissionError'}
            )
        finally:
            ClientScopeGrant.objects.create(user=self.reader, client=self.owner)
        restored = self.run_child('scan_read', **values)
        self.assertEqual(restored['result'], allowed['result'])
        role = RuleSet.objects.filter(group=self.group, name='work_order')
        role.update(can_view=False)
        try:
            self.assertTrue(
                explicitly_granted_client_ids(self.reader)
                .filter(client_id=self.owner.pk)
                .exists()
            )
            denied_role = self.run_child('scan_read', expected_exit=4, **values)
            self.assertEqual(
                denied_role['result'],
                {'refused': True, 'exception_type': 'PermissionError'},
            )
        finally:
            role.update(can_view=True)
        self.assertEqual(
            self.run_child('scan_read', **values)['result'], allowed['result']
        )
        self.assertEqual(allowed['result']['version'], reserved.version)
        self.assertEqual(ReconciliationAttempt.objects.count(), 1)
        self.assertEqual(ReconciliationAttempt.objects.get().state, 'pending')
        self.assert_native_untouched()

    def test_fresh_scan_reader_refuses_changed_namespace_epoch(self):
        """Persisted progress does not turn an old binding into a current epoch."""
        from integration_state.models import ReconciliationAttempt, SourceNamespace
        from integration_state.shared_reconciliation import (
            begin_scan,
            reserve_scan_page,
        )

        initial = begin_scan(
            self.namespace.pk,
            'private-epoch-scan',
            self.binding,
            max_pages=2,
            max_records=1,
            authorize=self.scan_read_eligible,
        )
        reserved = reserve_scan_page(initial, authorize=self.scan_read_eligible)
        values = {**self.recovery_coordinates(), 'scan_id': initial.scan_id}
        allowed = self.run_child('scan_read', **values)
        SourceNamespace.objects.filter(pk=self.namespace.pk).update(epoch=uuid4())
        try:
            self.assertTrue(self.read_eligible())
            denied = self.run_child('scan_read', expected_exit=4, **values)
            self.assertEqual(
                denied['result'],
                {'refused': True, 'exception_type': 'SharedStateError'},
            )
        finally:
            SourceNamespace.objects.filter(pk=self.namespace.pk).update(
                epoch=self.namespace.epoch
            )
        self.assertEqual(
            self.run_child('scan_read', **values)['result'], allowed['result']
        )
        self.assertEqual(allowed['result']['version'], reserved.version)
        self.assertEqual(ReconciliationAttempt.objects.get().state, 'pending')
        self.assert_native_untouched()

    def test_fresh_copy_progress_preserves_history_nonregression_and_retry_budget(self):
        """Synthetic copy claims persist across exits without real approval or erasure."""
        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt

        values = {**self.recovery_coordinates(), 'request_id': str(uuid4())}
        intent, impact, _ = _copy_inputs(values)

        def planning_read(value):
            return value == intent and self.read_eligible()

        request = durable.record_private_intent(
            self.namespace.pk, intent, authorize=planning_read
        )
        stored = durable.record_copy_impact(
            request.request_pk,
            impact,
            intent=intent,
            copy_kind='unknown',
            authorize=planning_read,
        )
        initial = durable.start_private_copy_attempt(
            stored.impact_pk,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            max_attempts=2,
            authorize=planning_read,
        )
        values['impact_pk'] = stored.impact_pk
        pending = self.run_child('copy_pending', **values)
        uncertain = self.run_child('copy_uncertain', **values)
        replayed = self.run_child('copy_replay', **values)
        self.assertEqual(uncertain['result'], replayed['result'])
        self.assertEqual(pending['result']['sequence'], 1)
        self.assertEqual(uncertain['result']['sequence'], 2)
        self.assertEqual(uncertain['result']['status'], 'uncertain')
        retried = self.run_child('copy_retry', **values)
        observed = self.run_child('copy_read', **values)
        self.assertEqual(retried['result'], observed['result'])
        self.assertEqual(observed['result']['attempts_used'], 2)
        self.assertEqual(observed['result']['max_attempts'], 2)
        self.assertEqual(len(observed['result']['attempt_history']), 2)
        self.assertEqual(
            observed['result']['attempt_history'][0], str(initial.progress.attempt_id)
        )
        reset = self.run_child('copy_reset', expected_exit=4, **values)
        self.assertEqual(
            reset['result'], {'refused': True, 'exception_type': 'SharedConflictError'}
        )
        self.assertEqual(PrivateCopyAttempt.objects.count(), 2)
        current = durable.read_private_copy_progress(
            stored.impact_pk, intent=intent, impact=impact, authorize=planning_read
        )
        self.assertEqual(_copy_result(current), observed['result'])
        self.assertEqual(intent.default_retention, 'indefinite')
        self.assertTrue(observed['result']['synthetic_only'])
        for name in (
            'native_authorized',
            'coverage_complete',
            'deletion_applied',
            'erasure_complete',
        ):
            assert_literal_bool(self, observed['result'][name], False)
        self.assert_native_untouched()

    def test_fresh_delivery_claim_decode_ack_and_exact_replay_are_metadata_only(self):
        """Exited workers recover committed private ownership without another native effect."""
        from integration_state.models import ConsumerAspectProgress, EventDelivery
        from integration_state.shared_delivery import configure_consumer

        self.assertTrue(self.read_eligible())
        (native,) = self.changes()
        configuration = configure_consumer(
            self.namespace.pk,
            'private-restart-consumer',
            aspects=(('machine', 'placement'),),
            configuration_epoch=uuid4(),
        )
        values = {
            **self.recovery_coordinates(),
            'namespace_id': self.namespace.pk,
            'consumer_id': configuration.consumer_id,
        }
        claimed = self.run_child('delivery_claim', **values)
        self.assertEqual(claimed['result']['change_id'], native.event_id)
        self.assertEqual(EventDelivery.objects.get().state, 'claimed')
        self.assertFalse(ConsumerAspectProgress.objects.exists())
        values['delivery_id'] = claimed['result']['delivery_id']
        recovered = self.run_child('delivery_read', **values)
        self.assertEqual(recovered['result']['state'], 'claimed')
        self.assertIsNone(recovered['result']['receipt'])
        self.assertFalse(recovered['result']['ownership_verified'])
        self.assertEqual(
            recovered['result']['captured_at'], native.captured_at.isoformat()
        )
        acknowledged = self.run_child('delivery_ack', **values)
        replayed = self.run_child('delivery_ack', **values)
        self.assertEqual(acknowledged['result'], replayed['result'])
        self.assertEqual(acknowledged['result']['state'], 'acknowledged')
        self.assertEqual(
            acknowledged['result']['receipt'],
            {
                'outcome': 'applied',
                'advanced': True,
                'deletion_applied': False,
                'erasure_complete': False,
            },
        )
        self.assertFalse(acknowledged['result']['ownership_verified'])
        self.assertFalse(acknowledged['result']['native_authorized'])
        self.assertEqual(EventDelivery.objects.count(), 1)
        self.assertEqual(ConsumerAspectProgress.objects.count(), 1)
        self.assertEqual(
            ConsumerAspectProgress.objects.get().change_id, native.event_id
        )
        self.assert_native_untouched()
