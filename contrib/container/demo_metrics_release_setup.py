"""Disposable-DB fixture setup for the release-image whole-process probe.

Harness-only. Run INSIDE the release candidate image against the DEDICATED
disposable database ``inventree_dm_release_probe_v1`` (refuses anything else,
including the default/shared database and the dev bootstrap probe database).
This is the probe's only write phase: it builds the synthetic fixture
environment and performs the GOVERNED apply through the reviewed
``apply_demo_metrics`` management command with its approved plan hash — the
same entry point the reviewed Azure Job runs. No lifecycle rows are ever
written directly. The later read-only window then has a real session, receipts
and records for plan/verify/cleanup-plan to read.

After the apply it also creates the explicit ``ClientScopeGrant`` for the
probe actor so the fresh probe processes resolve scope through the REAL
granted-scope resolver (no transient in-process scope attributes).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from demo_metrics_release_guard import check_probe_db_name
from tasks.scope import MaintenanceScope

from assets.demo_metrics import contract, planner
from assets.demo_metrics import demo_test_support as dts
from assets.models import AssetLocation, AssetMachine, Client, ClientScopeGrant

SESSION_KEY = 'dm-release-candidate-1'
ACTOR_USERNAME = 'demo-operator'

# Refuse the wrong database BEFORE writing anything anywhere.
check_probe_db_name(settings.DATABASES['default']['NAME'])

os.umask(0o077)
STATE_DIR = Path(os.environ['DM_RELEASE_STATE_DIR'])
STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

SPEC = {
    'session_key': SESSION_KEY,
    'history_import_approved': False,
    'code_prefix': '',
    'location_tree': dts.LOCATION_TREE,
    'machines': dts.MACHINES,
}


class _Env(dts.DemoMetricsEnvMixin):
    """Fixture builder without a TestCase (same pattern as the bootstrap setup)."""

    def build_env(self, *, spec):
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
            username=ACTOR_USERNAME,
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


env = _Env()
env.build_env(spec=SPEC)

mapping_path = STATE_DIR / 'mapping.json'
plan_path = STATE_DIR / 'plan_setup.json'
mapping_path.write_text(json.dumps(env.mapping_data, indent=2))
plan_path.write_text(json.dumps(env.plan, indent=2))

# Governed apply: the reviewed command with the approved plan hash — never a
# direct lifecycle write. Writes are expected here and only here.
call_command(
    'apply_demo_metrics',
    fixture=str(dts.FIXTURE_PATH),
    mapping=str(mapping_path),
    plan=str(plan_path),
    approved_plan_sha256=env.plan['plan_hash'],
    actor=env.actor.username,
    include_history=False,
)

# Durable, DB-backed scope evidence for the fresh probe processes.
ClientScopeGrant.objects.get_or_create(user=env.actor, client=env.client_tenant)

# Small shell-sourced environment file for the orchestrator (no secrets).
env_sh = STATE_DIR / 'probe_env.sh'
env_sh.write_text(
    'FIXTURE_PATH='
    + repr(str(dts.FIXTURE_PATH))
    + '\n'
    + 'SESSION_KEY='
    + repr(SESSION_KEY)
    + '\n'
    + 'ACTOR='
    + repr(env.actor.username)
    + '\n'
)

print('release probe setup complete (write phase)')
print('database:', settings.DATABASES['default']['NAME'])
print('session:', SESSION_KEY)
print('fixture:', dts.FIXTURE_PATH)
print('mapping:', mapping_path)
