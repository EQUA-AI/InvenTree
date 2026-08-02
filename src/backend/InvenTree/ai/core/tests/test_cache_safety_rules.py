"""HITLSafetyRules must actually exclude what it claims to exclude.

The never-cache workflow list originally held module names ("wf4_procurement")
while every live caller passes registry ids ("wf4"), so the workflow branch of
``can_cache`` could never fire — and diagnostic/safety queries were not
excluded at all. A replayed diagnosis for a different machine or fault is a
physical-safety hazard, so both gaps are pinned here.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
django.setup()

from ai.core.memory.semantic_cache import HITLSafetyRules  # noqa: E402
from ai.core.workflows.registry import get_workflow_registry  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402


class NeverCacheWorkflowTests(SimpleTestCase):
    """The workflow branch of can_cache fires for registry-sourced ids."""

    def test_registry_ids_for_hitl_and_diagnostic_workflows_are_excluded(self):
        """wf1/wf4/wf6/wf7 are refused under their REGISTRY ids, not module names."""
        for workflow_id in ("wf1", "wf4", "wf6", "wf7"):
            can_cache, reason = HITLSafetyRules.can_cache(
                "show me motor specifications", workflow_id=workflow_id
            )
            self.assertFalse(can_cache, workflow_id)
            self.assertIn(workflow_id, reason)

    def test_legacy_module_names_stay_excluded(self):
        """Callers still passing module-style names keep their exclusion."""
        for workflow_id in ("wf4_procurement", "wf6_documents", "wf1_diagnostics"):
            can_cache, _ = HITLSafetyRules.can_cache(
                "show me motor specifications", workflow_id=workflow_id
            )
            self.assertFalse(can_cache, workflow_id)

    def test_lookup_workflows_remain_cacheable_by_workflow_id(self):
        """The exclusion is targeted: plain lookups are not swept up."""
        for workflow_id in ("wf8", "wf2", "general", ""):
            can_cache, _ = HITLSafetyRules.can_cache(
                "show me motor specifications", workflow_id=workflow_id
            )
            self.assertTrue(can_cache, workflow_id)

    def test_registry_agrees_diagnostics_are_not_cacheable(self):
        """The registry declaration and the safety rules cannot drift apart."""
        registry = get_workflow_registry()
        for workflow_id in ("wf1", "wf7"):
            definition = registry.get_definition(workflow_id)
            self.assertFalse(definition.cacheable, workflow_id)
            self.assertIn(workflow_id, HITLSafetyRules.NEVER_CACHE_WORKFLOWS)


class DiagnosticQueryPatternTests(SimpleTestCase):
    """Diagnostic and safety wording is never cacheable regardless of workflow."""

    def test_diagnostic_and_safety_queries_are_excluded(self):
        """Symptom, repair and isolation questions never enter the cache."""
        queries = (
            "Diagnose why press 4 keeps tripping",
            "Troubleshoot the conveyor fault",
            "What is the root cause of the bearing failure?",
            "How do I repair the hydraulic pump?",
            "Walk me through the lockout tagout for the mixer",
            "Is it safe to open the guard while isolated?",
            "What is the LOTO procedure for the compressor?",
        )
        for query in queries:
            can_cache, _ = HITLSafetyRules.can_cache(query, workflow_id="wf8")
            self.assertFalse(can_cache, query)

    def test_plain_inventory_queries_stay_cacheable(self):
        """The new patterns must not swallow ordinary lookups."""
        queries = (
            "Show me motor specifications",
            "How many M8 bolts are in stock?",
            "Which supplier provides the 0402 capacitors?",
        )
        for query in queries:
            can_cache, reason = HITLSafetyRules.can_cache(query, workflow_id="wf8")
            self.assertTrue(can_cache, f"{query}: {reason}")
