"""The deployment binding for the closeout extractor seam (S19).

The capability itself is deliberately inert without an injected completion
callable; this binding is the one place that supplies it. These tests pin the
three things a deployment depends on: the dotted path stays importable (the
seam docstring shipped a WRONG path once), the model pin prefers the
Django-plane override, and a missing OpenAI plane fails closed instead of
fabricating a document.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from ai.core.capabilities import closeout_binding
from django.test import override_settings


def test_the_seam_dotted_path_is_importable() -> None:
    from django.utils.module_loading import import_string

    resolved = import_string("ai.core.capabilities.closeout_binding.extract")
    assert resolved is closeout_binding.extract


def test_missing_openai_plane_fails_closed() -> None:
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


@override_settings(AIMMS_CLOSEOUT_EXTRACTION_MODEL="")
def test_extract_records_the_resolved_deployment_on_its_document() -> None:
    """The caller receives provenance from the exact deployment binding used."""
    reply = json.dumps({
        "schema_version": 1,
        "fields": {},
        "part_candidates": [],
        "reading_candidates": [],
        "warnings": [],
    })
    configured = SimpleNamespace(azure_openai_fast_deployment="fast-closeout-deployment")
    with (
        patch("ai.core.config.get_settings", return_value=configured),
        patch.object(closeout_binding, "_complete", return_value=reply) as completed,
    ):
        document = closeout_binding.extract("Nothing to report.", {})

    assert document.model_provenance == {
        "deployment": "fast-closeout-deployment",
        "model": "fast-closeout-deployment",
    }
    assert completed.call_args.kwargs["deployment_name"] == ("fast-closeout-deployment")


def test_complete_records_the_provider_model_and_run_id() -> None:
    """Provider response identity augments (and never erases) the deployment."""
    settings = SimpleNamespace(
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="secret",
        azure_openai_api_version="2024-06-01",
    )
    response = SimpleNamespace(
        id="chatcmpl-closeout-1",
        model="gpt-4o-mini-2024-07-18",
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
    )
    completion = Mock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completion)))
    provenance = {"deployment": "closeout-deployment"}

    with (
        patch("ai.core.config.get_settings", return_value=settings),
        patch("openai.AzureOpenAI", return_value=client),
    ):
        reply = closeout_binding._complete(
            [],
            deployment_name="closeout-deployment",
            provenance=provenance,
        )

    assert reply == '{"ok": true}'
    assert provenance == {
        "deployment": "closeout-deployment",
        "model": "gpt-4o-mini-2024-07-18",
        "run_id": "chatcmpl-closeout-1",
    }
    assert completion.call_args.kwargs["model"] == "closeout-deployment"
