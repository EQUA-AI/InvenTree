"""Tier-1 voice safety: read-only tools + read-only spoken prompt.

Voice-modality lookups must be restricted to read-only tools (no email or
kanban write tools, which mutate and bypass the InvenTree read-only fence) and
must use the read-only spoken prompt. Gated by ``feature_voice_readonly_tools``
(default on); flipping it off reverts voice to the full text toolset.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.integrations.email.tools import list_emails, send_email  # noqa: E402
from ai.core.integrations.kanban_tools import (  # noqa: E402
    create_kanban_card,
    list_kanban_cards,
)
from ai.core.tools.rbac import read_tools  # noqa: E402
from ai.core.workflows.wf8_lookup import T1LookupWorkflow  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402


def _fake_settings(*, read_only: bool):
    return SimpleNamespace(feature_voice_readonly_tools=read_only)


class VoiceReadOnlyToolsetTests(SimpleTestCase):
    """The voice toolset is a read-only subset; the text toolset is unchanged."""

    def setUp(self):
        self.wf = T1LookupWorkflow()

    def test_voice_toolset_is_read_only(self):
        voice = set(self.wf.VOICE_BASE_TOOLS)
        # No mutating tools that would bypass the InvenTree read-only fence.
        self.assertNotIn(send_email, voice)
        self.assertNotIn(create_kanban_card, voice)
        # Every text-chat read tool remains available, including email reads.
        allowed = set(read_tools(self.wf.BASE_TOOLS))
        self.assertTrue(voice)
        self.assertEqual(voice, allowed)
        self.assertIn(list_emails, voice)
        self.assertIn(list_kanban_cards, voice)

    def test_text_toolset_keeps_full_surface(self):
        text = set(self.wf.BASE_TOOLS)
        self.assertIn(send_email, text)
        self.assertIn(create_kanban_card, text)

    def test_base_tools_for_gates_on_flag_and_modality(self):
        with patch(
            "ai.core.workflows.wf8_lookup.get_settings",
            return_value=_fake_settings(read_only=True),
        ):
            # Voice + flag on -> read-only subset.
            self.assertEqual(self.wf._base_tools_for(is_voice=True), self.wf.VOICE_BASE_TOOLS)
            # Text is never restricted.
            self.assertEqual(self.wf._base_tools_for(is_voice=False), self.wf.BASE_TOOLS)

        with patch(
            "ai.core.workflows.wf8_lookup.get_settings",
            return_value=_fake_settings(read_only=False),
        ):
            # Flag off reverts voice to the full toolset.
            self.assertEqual(self.wf._base_tools_for(is_voice=True), self.wf.BASE_TOOLS)


class VoicePromptTests(SimpleTestCase):
    """The voice prompt is read-only and distinct from the text prompt."""

    def test_voice_prompt_is_read_only_and_distinct(self):
        voice_prompt = T1LookupWorkflow.VOICE_SYSTEM_PROMPT.lower()
        self.assertIn("read-only", voice_prompt)
        self.assertNotEqual(T1LookupWorkflow.VOICE_SYSTEM_PROMPT, T1LookupWorkflow.SYSTEM_PROMPT)
        # The text prompt may describe its governed write tools (kanban, email);
        # the voice prompt must claim no write ability of any kind.
        text_prompt = T1LookupWorkflow.SYSTEM_PROMPT.lower()
        self.assertIn("write authority", text_prompt)
        self.assertNotIn("write authority", voice_prompt)
        self.assertNotIn("write access", voice_prompt)

    def test_no_prompt_carries_an_anti_refusal_directive(self):
        """An instruction to never refuse is a hallucination instruction.

        The old text prompt claimed FULL READ AND WRITE access against a
        toolset with no part/order/stock write tools, and ordered the model to
        never say it cannot create records. Both halves must stay gone, and
        declining must be explicitly permitted.
        """
        for prompt in (
            T1LookupWorkflow.SYSTEM_PROMPT,
            T1LookupWorkflow.READ_SYSTEM_PROMPT,
            T1LookupWorkflow.VOICE_SYSTEM_PROMPT,
            T1LookupWorkflow.CLARIFY_SYSTEM_PROMPT,
        ):
            lowered = prompt.lower()
            self.assertNotIn("full read and write", lowered)
            self.assertNotIn("full write access", lowered)
            self.assertNotIn("never say you cannot", lowered)
        self.assertIn(
            "declining is always acceptable",
            T1LookupWorkflow.READ_SYSTEM_PROMPT.lower(),
        )
        self.assertIn(
            "declining is always acceptable",
            T1LookupWorkflow.VOICE_SYSTEM_PROMPT.lower(),
        )

    def test_read_and_voice_prompts_require_manual_citations(self):
        """Documentation answers must name their source on every prompt path."""
        self.assertIn("cite the source", T1LookupWorkflow.READ_SYSTEM_PROMPT.lower())
        self.assertIn("say which document", T1LookupWorkflow.VOICE_SYSTEM_PROMPT.lower())
