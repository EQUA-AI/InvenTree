"""Phase 3b: voice diagnostic context factory -- fail-closed + server-owned roots.

The repair domain (models/scope) is not installed in the AIMMS test settings, so
the repair.services seam is faked here; these tests cover the AI-layer factory
logic (fail-closed gates + DiagnosticContext construction), not the domain
queries (those belong to the repair app's own test suite).
"""

from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.reasoning.diagnostic_context import _build_sync  # noqa: E402
from ai.core.tests.test_normalized_turn_service import _principal  # noqa: E402
from ai.core.tools.diagnostics import DiagnosticContext  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402

_CAPS = ("diagnostics.machine.read", "diagnostics.maintenance.read")


def _fake_repair_services(*, actor=True, capabilities=_CAPS, roots=()):
    module = types.ModuleType("repair.services")
    resolved_actor = object() if actor else None
    module.diagnostic_rehydrate_actor = lambda *_: resolved_actor
    module.diagnostic_capabilities_for_actor = lambda *_: frozenset(capabilities)
    module.list_diagnostic_record_roots = lambda *_: list(roots)
    return module


def _install(module):
    sys.modules.setdefault("repair", types.ModuleType("repair"))
    sys.modules["repair.services"] = module


def _machine_root(machine_id=42, revision="2026-01-01T00:00:00+00:00"):
    return {
        "entity_type": "machine",
        "entity_id": machine_id,
        "expected_revision": revision,
        "linked_machine_id": None,
        "authorization_class": "maintenance_scope",
    }


class DiagnosticContextFactoryTests(SimpleTestCase):
    def tearDown(self):
        sys.modules.pop("repair.services", None)
        sys.modules.pop("repair", None)

    def test_no_actor_yields_none(self):
        _install(_fake_repair_services(actor=False))
        self.assertIsNone(_build_sync(_principal()))

    def test_no_capabilities_yields_none(self):
        _install(_fake_repair_services(capabilities=()))
        self.assertIsNone(_build_sync(_principal()))

    def test_no_roots_yields_none(self):
        _install(_fake_repair_services(roots=()))
        self.assertIsNone(_build_sync(_principal()))

    def test_happy_path_builds_scoped_context(self):
        roots = [
            _machine_root(42),
            {
                "entity_type": "repair_packet",
                "entity_id": 7,
                "expected_revision": "2026-01-02T00:00:00+00:00",
                "linked_machine_id": 42,
                "authorization_class": "maintenance_scope",
            },
        ]
        _install(_fake_repair_services(roots=roots))
        ctx = _build_sync(_principal())
        self.assertIsInstance(ctx, DiagnosticContext)
        self.assertEqual(set(ctx.capabilities), set(_CAPS))
        self.assertIsNotNone(ctx.root_for("machine", 42))
        self.assertIsNotNone(ctx.root_for("repair_packet", 7))
        # Server-owned scope: a machine not in the resolved roots is never reachable.
        self.assertIsNone(ctx.root_for("machine", 999))

    def test_invalid_root_fails_closed(self):
        # A repair_packet root missing its linked machine violates the strict
        # DiagnosticRecordRoot invariant -> the whole context fails closed.
        bad = {
            "entity_type": "repair_packet",
            "entity_id": 7,
            "expected_revision": "2026-01-02T00:00:00+00:00",
            "linked_machine_id": None,
            "authorization_class": "maintenance_scope",
        }
        _install(_fake_repair_services(roots=[bad]))
        self.assertIsNone(_build_sync(_principal()))
