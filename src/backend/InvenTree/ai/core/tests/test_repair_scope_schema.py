"""Use the installed SDK to pin the native scope proposal's nested JSON contract."""

import json
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from agent_framework import AIFunction  # noqa: E402
from ai.core.tools.repair_scope_draft import (  # noqa: E402
    get_focused_repair_scope_context,
    save_proposed_repair_scope_draft,
)
from ai.core.voice.tool_actions import _capture_proxy  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from tasks.services.repair_scope_drafts import RepairScopeDraftError, _scope  # noqa: E402


def _arguments():
    return {
        "work_order_id": 3993,
        "expected_version": 2,
        "idempotency_key": "schema-test-only",
        "machine_id": 102,
        "repair_packet_id": 35,
        "source_event_id": 17,
        "component_code": "PS-MOTOR:1",
        "e03_attachment_id": 201,
        "manual_attachment_ids": [204],
        "scope": {
            "suspected_cause": "Proposed cause; actual configuration remains unverified.",
            "steps": ["Proposed sequence, subject to the approved site procedure."],
            "tools_and_supplies": ["Proposed tools; confirm against site procedure."],
            "required_ppe": ["Proposed PPE review; confirm against site procedure."],
            "isolation_checks": ["Verify approved isolation before any physical work."],
            "post_work_checks": ["Propose post-work verification; none performed."],
            "replacement_parts": [],
            "parts_decision": "No parts justified by prepared exercise evidence alone.",
            "open_questions": ["Confirm fitted cooler and source applicability."],
        },
    }


def _sdk_capture():
    captured = []
    proxy = _capture_proxy(save_proposed_repair_scope_draft, captured)
    return AIFunction(name="save_proposed_repair_scope_draft", func=proxy), captured


class RepairScopeSchemaTest(TestCase):
    def test_sdk_schema_exposes_exact_nested_fields_and_array_bounds(self):
        sdk, _ = _sdk_capture()
        schema = sdk.input_model.model_json_schema()
        scope = schema["$defs"]["ProposedRepairScope"]
        self.assertFalse(scope["additionalProperties"])
        self.assertEqual(set(scope["required"]), set(_arguments()["scope"]))
        for name in (
            "steps",
            "tools_and_supplies",
            "required_ppe",
            "isolation_checks",
            "post_work_checks",
            "open_questions",
        ):
            field = scope["properties"][name]
            self.assertEqual(field["type"], "array")
            self.assertEqual(field["items"]["type"], "string")
            self.assertEqual((field["minItems"], field["maxItems"]), (1, 20))
            self.assertEqual(field["items"]["maxLength"], 1000)
        self.assertEqual(scope["properties"]["replacement_parts"]["maxItems"], 0)
        self.assertEqual(scope["properties"]["suspected_cause"]["maxLength"], 2000)

    def test_sdk_preserves_planning_guidance_on_typed_fields(self):
        """Nested descriptions must survive the actual SDK's schema conversion."""
        sdk, _ = _sdk_capture()
        fields = sdk.input_model.model_json_schema()["$defs"]["ProposedRepairScope"]["properties"]
        self.assertIn("First confirm the fitted motor", fields["steps"]["description"])
        self.assertIn("only conditionally", fields["steps"]["description"])
        self.assertIn("every method-specific", fields["steps"]["description"])
        self.assertIn("general alternative", fields["steps"]["description"])
        self.assertIn("same cited method-specific", fields["tools_and_supplies"]["description"])
        self.assertIn("material restrictions", fields["tools_and_supplies"]["description"])
        self.assertIn("No physical isolation", fields["isolation_checks"]["description"])
        self.assertIn("verify effective energy control", fields["isolation_checks"]["description"])
        self.assertIn("absence of hazardous energy", fields["isolation_checks"]["description"])
        self.assertIn(
            "presence alone is not verification", fields["isolation_checks"]["description"]
        )
        self.assertIn("refit guards", fields["post_work_checks"]["description"])
        self.assertIn("comparable load", fields["post_work_checks"]["description"])
        self.assertIn("not completed tests", fields["post_work_checks"]["description"])

    def test_real_failed_string_shape_is_rejected_without_coercion(self):
        for name in (
            "steps",
            "tools_and_supplies",
            "required_ppe",
            "isolation_checks",
            "post_work_checks",
            "open_questions",
        ):
            with self.subTest(field=name):
                sdk, captured = _sdk_capture()
                arguments = _arguments()
                arguments["scope"][name] = "This must not become a one-item array."
                with self.assertRaises(ValidationError):
                    sdk.input_model.model_validate(arguments)
                with self.assertRaises(RepairScopeDraftError):
                    _scope(arguments["scope"])
                self.assertEqual(captured, [])

    def test_sdk_and_domain_keep_native_parts_authority_and_bounds(self):
        for change in (
            {"replacement_parts": ["bearing"]},
            {"supervisor_review": "approved"},
            {"site_safety_authorization": "approved"},
            {"physical_execution": "completed"},
            {"post_repair_verification": "passed"},
            {"steps": []},
            {"required_ppe": [123]},
            {"post_work_checks": ["x"] * 21},
            {"suspected_cause": "x" * 2001},
        ):
            with self.subTest(change=change):
                sdk, captured = _sdk_capture()
                arguments = _arguments()
                arguments["scope"].update(change)
                with self.assertRaises(ValidationError):
                    sdk.input_model.model_validate(arguments)
                with self.assertRaises(RepairScopeDraftError):
                    _scope(arguments["scope"])
                self.assertEqual(captured, [])


class RepairScopeSDKCaptureTest(IsolatedAsyncioTestCase):
    async def test_native_context_keeps_facts_separate_from_planning_requirements(self):
        """Required future checks cannot be mistaken for stored observations."""
        native = {
            "work_order_id": 3993,
            "lifecycle_version": 3,
            "draft": None,
            "sources": {"e03": {"id": 201, "prepared": True}, "manuals": [{"id": 204}]},
        }
        with (
            patch(
                "ai.core.tools.repair_scope_draft._focused_actor",
                return_value=(object(), SimpleNamespace(work_order_id=3993)),
            ),
            patch(
                "tasks.services.repair_scope_drafts.repair_scope_draft_context", return_value=native
            ) as read,
        ):
            result = await get_focused_repair_scope_context()
        self.assertEqual(read.call_count, 1)
        self.assertEqual(result["work_order_id"], native["work_order_id"])
        self.assertEqual(result["sources"], native["sources"])
        self.assertIsNone(result["draft"])
        self.assertNotIn("planning_requirements", native)
        requirements = result["planning_requirements"]
        self.assertEqual(
            set(requirements["post_service_check_categories"]),
            {
                "guards_and_protection",
                "measurement_path",
                "comparable_winding_readings",
                "authorized_stability",
            },
        )
        self.assertNotIn("measurements", result["sources"])
        fidelity = requirements["manual_method_fidelity"]
        self.assertEqual(
            set(fidelity),
            {
                "complete_context",
                "conditional_steps_and_supplies",
                "follow_up_handling",
                "bounded_excerpt_gap",
            },
        )
        self.assertIn(
            "both the planned step and its tools/supplies",
            fidelity["conditional_steps_and_supplies"],
        )
        self.assertIn("omit the method", fidelity["bounded_excerpt_gap"])
        self.assertIn("focused follow-up manual search", fidelity["bounded_excerpt_gap"])
        self.assertIn(
            "verify effective energy control", requirements["planning_energy_control_verification"]
        )
        self.assertIn(
            "absence of hazardous energy", requirements["planning_energy_control_verification"]
        )
        self.assertNotIn("manual_method_fidelity", result["sources"])
        # Guidance is generic, rather than a replacement model answer or a
        # hardcoded method from this particular attached reference.
        self.assertNotIn("petroleum", json.dumps(requirements).lower())
        self.assertNotIn("3993", json.dumps(requirements))

    async def test_sdk_capture_keeps_plain_dict_and_never_calls_domain_save(self):
        sdk, captured = _sdk_capture()
        expected = _arguments()
        parsed = sdk.input_model.model_validate(expected)
        self.assertIs(type(parsed.scope), dict)
        self.assertEqual(parsed.scope, expected["scope"])
        with patch("tasks.services.repair_scope_drafts.save_repair_scope_draft") as save:
            result = await sdk.invoke(arguments=parsed)
        save.assert_not_called()
        self.assertEqual(result, {"proposal_captured": True, "executed": False})
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].arguments, expected)
        self.assertEqual(json.loads(json.dumps(captured[0].arguments)), expected)
        self.assertEqual(_scope(captured[0].arguments["scope"]), expected["scope"])
