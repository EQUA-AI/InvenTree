"""Shared disposable test environment for the EQUA demo metrics suites.

Not collected by the test runner (name does not match ``test*.py``). Builds the
six fixture machines/locations in a disposable test database, an operator actor
with explicit maintenance scopes, loader-style synthetic ownership provenance
(managed demo parts carrying the ``asset_demo_data`` metadata marker plus
``MachinePart`` links — exactly what ``load_asset_demo_data`` establishes), and
a fully resolved mapping whose target fingerprint matches the live test
database and whose ownership evidence is verified, never asserted.

The runtime code/image identity attestation (``AIMMS_APPROVED_COMMIT_SHA`` /
``AIMMS_APPROVED_IMAGE_DIGEST``) is set to the mapping's approved values for
the duration of each test, mirroring what the reviewed Job environment does.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.utils import timezone

from tasks.scope import MaintenanceScope

from assets.demo_metrics_models import DemoMetricsSession

from . import contract, fingerprint, planner

RESOURCES = Path(__file__).parent / 'resources'
FIXTURE_PATH = RESOURCES / 'demo_fixture.json'

#: Fixture alias -> (label, parent alias or None, machine scenario).
LOCATION_TREE = [
    ('SITE-A', 'DEMO Site A', None),
    ('A-LINE', 'DEMO Line A', 'SITE-A'),
    ('A-UTIL', 'DEMO Utilities A', 'SITE-A'),
    ('SITE-B', 'DEMO Site B', None),
    ('B-LINE', 'DEMO Line B', 'SITE-B'),
    ('B-UTIL', 'DEMO Utilities B', 'SITE-B'),
]

MACHINES = [
    ('A01', 'DEMO Machine A01', 'SITE-A'),
    ('A02', 'DEMO Machine A02', 'A-LINE'),
    ('A03', 'DEMO Machine A03', 'A-UTIL'),
    ('B01', 'DEMO Machine B01', 'SITE-B'),
    ('B02', 'DEMO Machine B02', 'B-LINE'),
    ('B03', 'DEMO Machine B03', 'B-UTIL'),
]

#: The reviewed code/image identity the test runtime attests.
APPROVED_COMMIT_SHA = 'dcc82279407f16a1623e6166b32ed7eacef9a74c'
APPROVED_IMAGE_DIGEST = 'sha256:test'

#: The approved security site key for the synthetic sources. This is a
#: security-scope value from the reviewed mapping — deliberately *not* one of
#: the fixture's physical location aliases.
SECURITY_SITE_KEY = 'epcon-experimental'


#: Managed demo part IPN per machine alias (loader-style ownership evidence).
def demo_part_ipn(alias: str) -> str:
    """Return the managed demo part IPN proving ownership for one alias."""
    return f'DEMO-PART-{alias}'


def attest_runtime_identity():
    """Attest the approved code/image identity for this process.

    Returns a restore callback; tests that exercise unverified/mismatched
    identity pop or override these environment values themselves.
    """
    previous = {
        fingerprint.CODE_IDENTITY_ENV: os.environ.get(fingerprint.CODE_IDENTITY_ENV),
        fingerprint.IMAGE_IDENTITY_ENV: os.environ.get(fingerprint.IMAGE_IDENTITY_ENV),
    }
    os.environ[fingerprint.CODE_IDENTITY_ENV] = APPROVED_COMMIT_SHA
    os.environ[fingerprint.IMAGE_IDENTITY_ENV] = APPROVED_IMAGE_DIGEST

    def restore():
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return restore


class DemoMetricsEnvMixin:
    """Builds the fixture environment plus resolved mapping and plan."""

    def build_demo_env(
        self, *, session_key='equa-demo-test-1', history_import_approved=False
    ):
        """Create the disposable environment; returns (fixture, mapping, plan)."""
        from assets.models import AssetLocation, AssetMachine, Client

        add_cleanup = getattr(self, 'addCleanup', None)
        if add_cleanup is not None:
            add_cleanup(attest_runtime_identity())
        else:  # pragma: no cover - mixin always used with TestCase
            attest_runtime_identity()

        # Data migrations seed a default internal client on a fresh database;
        # reuse it (name is unique) and normalize the code the mapping asserts.
        self.client_tenant, _created = Client.objects.get_or_create(
            name='Internal', defaults={'code': 'internal'}
        )
        if self.client_tenant.code != 'internal':
            self.client_tenant.code = 'internal'
            self.client_tenant.save(update_fields=['code'])
        self.locations = {}
        for alias, label, parent in LOCATION_TREE:
            node = AssetLocation(
                client=self.client_tenant,
                parent=self.locations[parent] if parent else None,
                name=label,
                code=alias.lower(),
                # The column is NOT NULL; effective timezone resolution
                # happens in the location service on read.
                timezone='UTC',
            )
            # name_key/sibling_key are computed by the location service's
            # validator; set them explicitly for these direct test inserts.
            node.name_key = ' '.join(label.split()).casefold()
            node.sibling_key = str(self.locations[parent].pk) if parent else 'root'
            node.save()
            self.locations[alias] = node

        # One managed demo part per machine, carrying the loader's ownership
        # marker, linked with MachinePart — the database provenance the
        # existing loaders treat as synthetic-ownership proof.
        self.demo_parts = {}
        part_category = self._demo_part_category()
        self.machines = {}
        for alias, label, location in MACHINES:
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

        self.fixture = contract.load_fixture(FIXTURE_PATH)
        self.mapping_data = build_mapping_dict(
            self.fixture,
            machine_ids={alias: machine.pk for alias, machine in self.machines.items()},
            location_ids={alias: node.pk for alias, node in self.locations.items()},
            session_key=session_key,
            assignee_username=self.actor.username,
            history_import_approved=history_import_approved,
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

    def _demo_part_category(self):
        """Reuse one part category for the managed demo parts."""
        from part.models import PartCategory

        category, _created = PartCategory.objects.get_or_create(
            name='DEMO Managed Parts', defaults={'description': 'Synthetic demo parts'}
        )
        return category

    def _managed_demo_part(self, category, alias, machine):
        """Create a managed demo part and link it to the machine.

        The part carries ``asset_demo_data`` metadata with ``kind: 'part'`` —
        exactly the marker ``load_asset_demo_data`` writes and its ownership
        rule reads.
        """
        from assets.models import MachinePart
        from part.models import Part

        part, _created = Part.objects.get_or_create(
            IPN=demo_part_ipn(alias),
            defaults={
                'name': f'DEMO managed part {alias}',
                'category': category,
                'description': 'Synthetic demo provenance part',
                'metadata': {'asset_demo_data': {'kind': 'part', 'schema_version': 1}},
            },
        )
        MachinePart.objects.get_or_create(
            machine=machine, part=part, defaults={'quantity': 1}
        )
        return part

    def apply_demo(self, *, now=None, include_history=False):
        """Run the governed apply against the prepared environment."""
        from . import apply_service

        return apply_service.apply_session(
            fixture=self.fixture,
            mapping=self.mapping,
            plan=self.plan,
            actor=self.actor,
            now=now,
            include_history=include_history,
        )

    def session(self):
        """Return the applied session row."""
        return DemoMetricsSession.objects.get(session_key=self.mapping.session_key)


def build_mapping_dict(
    fixture,
    *,
    machine_ids,
    location_ids,
    session_key,
    fingerprint=None,
    assignee_username=None,
    history_import_approved=False,
):
    """Build a fully resolved mapping dict for the given target identities.

    ``assignee_username`` resolves typed assignees for the work orders whose
    governed transitions require one (start/resume/complete); the readiness
    gate is never bypassed and no assignee is fabricated. Ownership evidence
    names the managed demo parts (loader rules); a bare boolean is never
    evidence. ``source_scope`` records the approved security scope — never the
    fixture's physical location aliases.
    """
    from . import fingerprint as fingerprint_module

    needs_assignee = ('WO-03', 'WO-04', 'WO-05')
    return {
        'schema_version': 'equa.demo-target-mapping/1',
        'ready_for_apply': True,
        'warning': 'Synthetic demo mapping; resolved for a disposable test target.',
        'dataset_key': fixture.dataset_key,
        'session_key': session_key,
        'target': {
            'subscription_id': 'test-subscription',
            'resource_group': 'epconchat',
            'app_name': 'aimms-experimental',
            'region_reported': 'eastus2',
            'environment_resource_id': '/test/environment',
            'database_identity_fingerprint': fingerprint
            or fingerprint_module.fingerprint_id(),
            'approved_commit_sha': APPROVED_COMMIT_SHA,
            'approved_image_digest': APPROVED_IMAGE_DIGEST,
            'demo_owner_identity': 'demo-operator',
        },
        'locations': {
            alias: {
                'target_location_id': location_ids[alias],
                'expected_version': 0,
                'create': False,
                'reparent': False,
            }
            for alias in location_ids
        },
        'machines': {
            alias: {
                'target_machine_id': machine_ids[alias],
                'expected_version': 0,
                'verified_synthetic_ownership': True,
                'ownership_evidence': {
                    'method': 'managed_demo_part',
                    'part_ipns': [demo_part_ipn(alias)],
                },
                'create': False,
                'move': False,
                'rename': False,
                'client_code': 'internal',
            }
            for alias in machine_ids
        },
        'state_mapping': dict(contract.STATE_TRANSLATION),
        'priority_mapping': dict(contract.PRIORITY_TRANSLATION),
        'ingestion': {
            'adapter_id': 'equa-demo-metrics-v1',
            'verified_signal_identifiers': {},
            'verified_units': {
                signal['key']: signal['unit']
                for signal in fixture.data['signal_definitions']
            },
            'verified_stale_threshold_seconds': 300,
            'external_side_effects_isolated': True,
        },
        'reporting_timezone': 'UTC',
        'side_effect_policy': ['database_write'],
        'expires_at': '2030-01-01T00:00:00Z',
        'expected_counts': contract.expected_counts(fixture),
        'input_hashes': {
            'fixture_canonical_sha256': fixture.canonical_sha256,
            'fixture_file_sha256': fixture.file_sha256,
        },
        'source_scope': {
            'DEMO-SOURCE-A': {'client_code': 'internal', 'site_key': SECURITY_SITE_KEY},
            'DEMO-SOURCE-B': {'client_code': 'internal', 'site_key': SECURITY_SITE_KEY},
        },
        'assignees': {
            key: {'assigned_to_username': assignee_username} for key in needs_assignee
        }
        if assignee_username
        else {},
        'control_records': {
            'WO-08': {'mode': 'synthetic_import', 'ownership': 'session_created'},
            'WO-09': {'mode': 'synthetic_import', 'ownership': 'session_created'},
        },
        'history_import_approved': history_import_approved,
        'approval': {'plan_sha256': None, 'approved_by': None, 'approved_at': None},
    }
