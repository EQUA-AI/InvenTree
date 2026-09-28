"""Real-DB demo-metrics E2E fixture setup (harness-only, disposable).

Run inside the dev container against a DEDICATED disposable database
``inventree_dm_e2e_*`` (``DM_E2E_DB``, default ``inventree_dm_e2e_v3``; the
name is allowlist-checked and the database host must be exactly the dev
container's ``db`` — anything else is refused). Idempotent: each environment
is built+applied only if its session is absent, and the run state is
re-derived from the database either way. With ``DM_E2E_REQUIRE_FRESH_APPLY=1``
a present session is refused instead of reused, so a "fresh governed apply"
run can never silently take the reuse path.

1. Builds the disposable fixture environment with
   ``assets.demo_metrics.demo_test_support`` — synthetic machines/locations,
   loader-style managed demo part ownership evidence, and a fully resolved
   target mapping whose database fingerprint matches the live database and
   whose code/image identity attestation mirrors the reviewed Job runtime.
2. Applies the dataset ONLY through the governed apply path: the reviewed
   ``apply_demo_metrics`` management command with the approved plan hash — a
   SHA-256 integrity check over the canonical plan body (an approval hash,
   NOT a cryptographic signature) — the same entry point the reviewed Azure
   Job runs. No lifecycle rows are ever written directly.
3. Builds real identity/scope mapping for the browser users: explicit
   ``ClientScopeGrant`` rows consumed by
   ``tasks.scope.granted_client_scope_resolver`` (enabled by
   ``demo_metrics_e2e_settings``).
4. Writes a run-state JSON (ids only) and a credentials file with
   runtime-generated passwords into the run scratch dir (0700). The
   credentials file is created 0600 at birth — before any secret content is
   materialized — under umask 077. Passwords are NEVER printed and never
   leave that file; the harness teardown deletes it after the run.

Environments:
- hist session  : six DEMO machines/locations with bounded synthetic history
  imported (history endpoint reports values).
- nohist session: six DEMO2 machines/locations (disjoint names and location
  codes), applied without history (history endpoint stays honestly
  unavailable).
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from tasks.scope import MaintenanceScope

from assets.demo_metrics import contract, planner
from assets.demo_metrics import demo_test_support as dts
from assets.demo_metrics_models import DemoMetricsReceipt, DemoMetricsSession
from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant
from users.models import UserProfile

# Disposable database allowlist: only purpose-made `inventree_dm_e2e_*`
# databases qualify. The real application database (`inventree`), any
# `test_*` database, template databases and anything else are refused BEFORE
# writing anything anywhere. Nothing is ever dropped.
DB_NAME = os.environ.get('DM_E2E_DB', 'inventree_dm_e2e_v3')
_DISPOSABLE_DB_RE = re.compile(r'^inventree_dm_e2e_[a-z0-9_]{1,40}$')
_FORBIDDEN_DB = {'inventree', 'postgres', 'template0', 'template1'}
if (
    not _DISPOSABLE_DB_RE.fullmatch(DB_NAME)
    or DB_NAME in _FORBIDDEN_DB
    or DB_NAME.startswith('test_')
):
    raise SystemExit(f'refusing non-disposable database name {DB_NAME!r}')

# Refuse the wrong database BEFORE writing anything anywhere.
_actual = settings.DATABASES['default']['NAME']
if str(_actual) != DB_NAME:
    raise SystemExit(f'refusing to run against database {_actual!r}; want {DB_NAME!r}')

# Exact host check: the harness only runs against the dev container's own
# database host (default `db`), never an arbitrary/remote/host-mounted target.
_EXPECTED_DB_HOST = os.environ.get('DM_E2E_EXPECTED_DB_HOST', 'db')
_actual_host = str(settings.DATABASES['default']['HOST'])
if _actual_host != _EXPECTED_DB_HOST:
    raise SystemExit(
        f'refusing database host {_actual_host!r}; want exactly {_EXPECTED_DB_HOST!r}'
    )

# Everything materialized here is private from birth: 0700 dirs and 0600
# files under umask 077 — never widened, never chmod'ed only afterwards.
os.umask(0o077)
STATE_DIR = Path(os.environ['DM_E2E_STATE_DIR'])
STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

ENVS = {
    'hist': {
        'session_key': 'dm-e2e-hist-1',
        'history_import_approved': True,
        'code_prefix': '',
        'location_tree': dts.LOCATION_TREE,
        'machines': dts.MACHINES,
    },
    'nohist': {
        'session_key': 'dm-e2e-nohist-1',
        'history_import_approved': False,
        'code_prefix': 'b-',
        'location_tree': [
            ('SITE-A', 'DEMO2 Site A', None),
            ('A-LINE', 'DEMO2 Line A', 'SITE-A'),
            ('A-UTIL', 'DEMO2 Utilities A', 'SITE-A'),
            ('SITE-B', 'DEMO2 Site B', None),
            ('B-LINE', 'DEMO2 Line B', 'SITE-B'),
            ('B-UTIL', 'DEMO2 Utilities B', 'SITE-B'),
        ],
        'machines': [
            ('A01', 'DEMO2 Machine A01', 'SITE-A'),
            ('A02', 'DEMO2 Machine A02', 'A-LINE'),
            ('A03', 'DEMO2 Machine A03', 'A-UTIL'),
            ('B01', 'DEMO2 Machine B01', 'SITE-B'),
            ('B02', 'DEMO2 Machine B02', 'B-LINE'),
            ('B03', 'DEMO2 Machine B03', 'B-UTIL'),
        ],
    },
}


class _Env(dts.DemoMetricsEnvMixin):
    """Fixture builder without a TestCase.

    Location codes get a prefix so two disposable environments can share the
    one deployment client.
    """

    def build_prefixed_env(self, *, spec):
        add_cleanup = getattr(self, 'addCleanup', None)
        if add_cleanup is not None:
            add_cleanup(dts.attest_runtime_identity())
        else:
            dts.attest_runtime_identity()

        self.client_tenant, _created = Client.objects.get_or_create(
            name='Internal', defaults={'code': 'internal'}
        )
        if self.client_tenant.code != 'internal':
            self.client_tenant.code = 'internal'
            self.client_tenant.save(update_fields=['code'])

        self.locations = {}
        for alias, label, parent in spec['location_tree']:
            node = AssetLocation(
                client=self.client_tenant,
                parent=self.locations[parent] if parent else None,
                name=label,
                code=f'{spec["code_prefix"]}{alias.lower()}',
                timezone='UTC',
            )
            node.name_key = ' '.join(label.split()).casefold()
            node.sibling_key = str(self.locations[parent].pk) if parent else 'root'
            node.save()
            self.locations[alias] = node

        self.demo_parts = {}
        part_category = self._demo_part_category()
        self.machines = {}
        for alias, label, location in spec['machines']:
            machine = AssetMachine.objects.create(
                name=label,
                client=self.client_tenant,
                physical_location=self.locations[location],
                description='Synthetic demo machine',
            )
            self.machines[alias] = machine
            self.demo_parts[alias] = self._managed_demo_part(
                part_category, alias, machine
            )

        self.actor, _created = get_user_model().objects.get_or_create(
            username='demo-operator',
            defaults={
                'email': 'demo@example.com',
                'is_staff': True,
                'is_superuser': True,
            },
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(
                customer_id=None, site_key=None, client_id=self.client_tenant.pk
            )
        }

        self.fixture = contract.load_fixture(dts.FIXTURE_PATH)
        self.mapping_data = dts.build_mapping_dict(
            self.fixture,
            machine_ids={alias: machine.pk for alias, machine in self.machines.items()},
            location_ids={alias: node.pk for alias, node in self.locations.items()},
            session_key=spec['session_key'],
            assignee_username=self.actor.username,
            history_import_approved=spec['history_import_approved'],
        )
        self.mapping = contract.validate_mapping(
            self.mapping_data, self.fixture, require_ready=True
        )
        self.resolutions, conflicts = planner.resolve_targets(
            self.fixture, self.mapping, self.actor
        )
        assert not conflicts, f'unexpected plan conflicts: {conflicts}'
        self.plan_body = planner.build_plan(
            fixture=self.fixture,
            mapping=self.mapping,
            actor=self.actor,
            session_anchor=timezone.now(),
            resolutions=self.resolutions,
            conflicts=conflicts,
        )
        self.plan = planner.sign_plan(self.plan_body)
        return self.fixture, self.mapping, self.plan


def _apply_via_governed_command(env, tag: str, *, include_history: bool):
    """Write mapping/plan artifacts and run the reviewed apply command."""
    mapping_path = STATE_DIR / f'mapping_{tag}.json'
    plan_path = STATE_DIR / f'plan_{tag}.json'
    mapping_path.write_text(json.dumps(env.mapping_data, indent=2))
    plan_path.write_text(json.dumps(env.plan, indent=2))
    call_command(
        'apply_demo_metrics',
        fixture=str(dts.FIXTURE_PATH),
        mapping=str(mapping_path),
        plan=str(plan_path),
        approved_plan_sha256=env.plan['plan_hash'],
        actor=env.actor.username,
        include_history=include_history,
    )


REQUIRE_FRESH_APPLY = os.environ.get('DM_E2E_REQUIRE_FRESH_APPLY') == '1'


def ensure_env(tag: str):
    """Build+apply the environment, or recover its ids if already applied.

    The apply decision is recorded in the returned evidence: a fresh run must
    show ``executed: true`` with governed-command receipts; a re-run shows
    ``executed: false`` (reuse) so idempotency is observable, never silent.
    """
    spec = ENVS[tag]
    if DemoMetricsSession.objects.filter(session_key=spec['session_key']).exists():
        if REQUIRE_FRESH_APPLY:
            raise SystemExit(
                f'fresh governed apply required but session {spec["session_key"]!r} '
                f'already exists in {DB_NAME!r}; refusing to silently reuse'
            )
        session = DemoMetricsSession.objects.get(session_key=spec['session_key'])
        locations = {
            alias: AssetLocation.objects.get(name=label).pk
            for alias, label, _parent in spec['location_tree']
        }
        machines = {
            alias: AssetMachine.objects.get(name=label).pk
            for alias, label, _loc in spec['machines']
        }
        print(
            f'session {spec["session_key"]!r} already present: reuse, apply NOT re-run'
        )
        return {
            'locations': locations,
            'machines': machines,
            'apply': {
                'executed': False,
                'mode': 'reuse',
                'receipts': DemoMetricsReceipt.objects.filter(session=session).count(),
            },
        }

    env = _Env()
    env.build_prefixed_env(spec=spec)
    _apply_via_governed_command(
        env, tag, include_history=spec['history_import_approved']
    )
    session = DemoMetricsSession.objects.get(session_key=spec['session_key'])
    receipts = DemoMetricsReceipt.objects.filter(session=session).count()
    if receipts < 1:
        raise SystemExit(
            f'governed apply wrote no receipts for {spec["session_key"]!r}'
        )
    print(
        f'governed apply EXECUTED (fresh) for {spec["session_key"]!r}: '
        f'{receipts} receipts'
    )
    return {
        'locations': {alias: node.pk for alias, node in env.locations.items()},
        'machines': {alias: machine.pk for alias, machine in env.machines.items()},
        'apply': {'executed': True, 'mode': 'fresh-apply', 'receipts': receipts},
    }


# Materialize persisted global settings defaults BEFORE any browser login.
# This is the supported bulk initialization also used by InvenTree's own test
# bootstrap (InvenTree/unit_test.py): missing default rows are created with
# bulk_create, i.e. WITHOUT running per-setting after_save callbacks. If a
# default row such as LOGIN_ENFORCE_MFA is instead created lazily during a
# live request (create-on-read via InvenTreeSetting.get_setting_object under
# `runserver`), its after_save callback (enforce_mfa) unconditionally ends ALL
# user sessions mid-run - the exact invalidation observed on fresh databases.
# Regression: demo_metrics_e2e_session_regression.py
from common.models import InvenTreeSetting

InvenTreeSetting.build_default_values()

hist_ids = ensure_env('hist')
nohist_ids = ensure_env('nohist')

# --- real identity/scope mapping for the browser actors -------------------
User = get_user_model()
internal = Client.objects.get(code='internal')
other_client, _created = Client.objects.get_or_create(
    name='DM E2E Other Client', defaults={'code': 'dm-e2e-other', 'active': True}
)


def ensure_user(username: str):
    """Create or rotate a disposable browser user; returns (user, password)."""
    user = User.objects.filter(username=username).first()
    password = secrets.token_urlsafe(18)
    if user is None:
        user = User.objects.create_user(
            username=username, password=password, is_active=True
        )
    else:
        user.set_password(password)
        user.is_active = True
        user.save(update_fields=['password', 'is_active'])
    # Full role authority for both actors: the ONLY difference between them is
    # the ClientScopeGrant mapping, so a denial can only come from the real
    # scope control (never a role trick or a UI pretense).
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    # `manage.py shell` is a read-only command for the profile post_save
    # signal, so the profile must exist before any login can save the user.
    UserProfile.objects.get_or_create(user=user)
    return user, password


operator, operator_password = ensure_user('dm_e2e_operator')
denied, denied_password = ensure_user('dm_e2e_denied')

# Positive-grant scope mapping: the operator is scoped to exactly the demo
# client; the denied actor is scoped to a client that owns no machines, so
# every scoped demo surface must deny (403) — the scope control is the
# authority, not a role trick or a UI pretense.
ClientScopeGrant.objects.get_or_create(user=operator, client=internal)
ClientScopeGrant.objects.get_or_create(user=denied, client=other_client)

hist_session = DemoMetricsSession.objects.get(session_key=ENVS['hist']['session_key'])
nohist_session = DemoMetricsSession.objects.get(
    session_key=ENVS['nohist']['session_key']
)

state = {
    'database': DB_NAME,
    'apply': {'hist': hist_ids['apply'], 'nohist': nohist_ids['apply']},
    'sessions': {
        'hist': {
            'id': str(hist_session.pk),
            'session_key': hist_session.session_key,
            'anchor_at': hist_session.anchor_at.isoformat(),
        },
        'nohist': {
            'id': str(nohist_session.pk),
            'session_key': nohist_session.session_key,
            'anchor_at': nohist_session.anchor_at.isoformat(),
        },
    },
    'locations_hist': hist_ids['locations'],
    'machines_hist': hist_ids['machines'],
    'locations_nohist': nohist_ids['locations'],
    'machines_nohist': nohist_ids['machines'],
    'users': {'operator': operator.username, 'denied': denied.username},
}
(STATE_DIR / 'state.json').write_text(json.dumps(state, indent=2))

creds_path = STATE_DIR / 'credentials.json'
# Created 0600 at open time, before any password content exists in the file.
creds_fd = os.open(creds_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(creds_fd, 'w', encoding='utf-8') as creds_stream:
    json.dump(
        {
            'operator': {'username': operator.username, 'password': operator_password},
            'denied': {'username': denied.username, 'password': denied_password},
        },
        creds_stream,
    )
os.chmod(creds_path, 0o600)

print('demo-metrics e2e setup complete')
print('state:', STATE_DIR / 'state.json')
print('hist session id:', hist_session.pk)
print('nohist session id:', nohist_session.pk)
print('usernames (passwords withheld):', operator.username, denied.username)
