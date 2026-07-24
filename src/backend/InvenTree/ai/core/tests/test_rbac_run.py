"""Phase 2: shared RBAC run helper -- voice read-only base + modality detection."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.integrations.email.tools import list_emails, send_email  # noqa: E402
from ai.core.integrations.inventory_tools import INVENTORY_TOOLS, create_part  # noqa: E402
from ai.core.workflows.rbac_run import (  # noqa: E402
    modality_of,
    rbac_base_tools,
    voice_read_tools,
)
from django.test import SimpleTestCase  # noqa: E402


def _settings(read_only: bool):
    return SimpleNamespace(feature_voice_readonly_tools=read_only)


class ModalityTests(SimpleTestCase):
    def test_modality_of(self):
        self.assertEqual(modality_of({"modality": "voice"}), "voice")
        self.assertEqual(modality_of({"modality": "text"}), "text")
        self.assertEqual(modality_of(None), "text")
        self.assertEqual(modality_of({}), "text")


class BaseToolsTests(SimpleTestCase):
    def test_text_gets_full_tools(self):
        with patch("ai.core.config.get_settings", return_value=_settings(True)):
            base = rbac_base_tools(INVENTORY_TOOLS, {"modality": "text"})
        self.assertEqual(base, tuple(INVENTORY_TOOLS))

    def test_voice_gets_read_only_subset(self):
        with patch("ai.core.config.get_settings", return_value=_settings(True)):
            base = rbac_base_tools(INVENTORY_TOOLS, {"modality": "voice"})
        self.assertEqual(base, voice_read_tools(INVENTORY_TOOLS))
        self.assertNotIn(create_part, base)

    def test_voice_preserves_workflow_email_reads_but_not_actions(self):
        tools = [list_emails, send_email]
        with patch("ai.core.config.get_settings", return_value=_settings(True)):
            base = rbac_base_tools(tools, {"modality": "voice"})
        self.assertEqual(base, (list_emails,))

    def test_voice_flag_off_reverts_to_full(self):
        with patch("ai.core.config.get_settings", return_value=_settings(False)):
            base = rbac_base_tools(INVENTORY_TOOLS, {"modality": "voice"})
        self.assertEqual(base, tuple(INVENTORY_TOOLS))
