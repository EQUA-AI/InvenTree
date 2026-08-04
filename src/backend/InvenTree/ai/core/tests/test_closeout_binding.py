"""The deployment binding for the closeout extractor seam (S19).

The capability itself is deliberately inert without an injected completion
callable; this binding is the one place that supplies it. These tests pin the
three things a deployment depends on: the dotted path stays importable (the
seam docstring shipped a WRONG path once), the model pin prefers the
Django-plane override, and a missing OpenAI plane fails closed instead of
fabricating a document.
"""

import json
from unittest.mock import patch

import pytest
from ai.core.capabilities import closeout_binding
from django.test import override_settings


def test_the_seam_dotted_path_is_importable() -> None:
    from django.utils.module_loading import import_string

    resolved = import_string("ai.core.capabilities.closeout_binding.extract")
    assert resolved is closeout_binding.extract


def test_missing_openai_plane_fails_closed() -> None:
    from types import SimpleNamespace

    bare = SimpleNamespace(
        azure_openai_endpoint="",
        azure_openai_api_key="",
        azure_openai_api_version="2024-06-01",
        azure_openai_fast_deployment="gpt-4o-mini",
    )
    with (
        patch("ai.core.config.get_settings", return_value=bare),
        pytest.raises(RuntimeError, match="no configured Azure OpenAI plane"),
    ):
        closeout_binding.extract("Replaced the filter.", {"work_order_type": "repair"})


@override_settings(AIMMS_CLOSEOUT_EXTRACTION_MODEL="pinned-extractor-model")
def test_deployment_name_prefers_the_django_override() -> None:
    assert closeout_binding._deployment_name() == "pinned-extractor-model"


def test_deployment_name_defaults_to_the_fast_deployment() -> None:
    from ai.core.config import get_settings

    assert closeout_binding._deployment_name() == get_settings().azure_openai_fast_deployment


def test_extract_returns_the_parsed_schema_document() -> None:
    reply = json.dumps({
        "schema_version": 1,
        "fields": {},
        "part_candidates": [],
        "reading_candidates": [],
        "warnings": ["not_stated"],
    })
    with patch.object(closeout_binding, "_complete", return_value=reply) as completed:
        document = closeout_binding.extract("Nothing to report.", {})
    assert document["schema_version"] == 1
    messages = completed.call_args.args[0]
    assert messages[0]["role"] == "system"
    assert "NARRATIVE" in messages[1]["content"]
