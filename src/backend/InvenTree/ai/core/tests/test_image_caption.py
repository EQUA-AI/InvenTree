"""R3 evidence-photo captions: strict schema, clamping, fail-closed codes."""

# ruff: noqa: E402

from __future__ import annotations

import base64
import json
import logging
import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import Settings
from ai.core.integrations.image_caption import (
    CAPTION_MAX_CHARS,
    ImageCaptionError,
    caption_image,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-pixels"


class _FakeCaptionClient:
    """AzureOpenAI-shaped test double; records chat.completions.create kwargs."""

    def __init__(self, content='{"caption": "Worn gasket on the HX-200 flange."}', error=None):
        self.calls: list[dict] = []
        self._content = content
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


def _settings(**overrides) -> Settings:
    base = {
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
        "AZURE_OPENAI_DEPLOYMENT": "standard-4o",
        "AZURE_OPENAI_FAST_DEPLOYMENT": "fast-mini",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    monkeypatch.setattr("ai.core.config.get_settings", _settings)


def test_caption_request_pins_strict_schema_and_the_standard_deployment():
    fake = _FakeCaptionClient()
    caption_image(PNG_BYTES, mime_type="image/png", client=fake)
    [call] = fake.calls
    # MEDIA_CAPTION routes to the standard (vision-capable) tier.
    assert call["model"] == "standard-4o"
    response_format = call["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["required"] == ["caption"]
    assert schema["additionalProperties"] is False


def test_caption_embeds_the_image_as_a_data_url_with_the_passed_mime():
    fake = _FakeCaptionClient()
    caption_image(PNG_BYTES, mime_type="image/webp", client=fake)
    [call] = fake.calls
    parts = call["messages"][1]["content"]
    image_parts = [p for p in parts if p["type"] == "image_url"]
    assert len(image_parts) == 1
    url = image_parts[0]["image_url"]["url"]
    prefix = "data:image/webp;base64,"
    assert url.startswith(prefix)
    assert base64.b64decode(url[len(prefix) :]) == PNG_BYTES


def test_caption_is_parsed_from_the_json_payload():
    fake = _FakeCaptionClient(content='{"caption": "Worn gasket on the HX-200 flange."}')
    assert (
        caption_image(PNG_BYTES, mime_type="image/png", client=fake)
        == "Worn gasket on the HX-200 flange."
    )


def test_caption_is_collapsed_to_one_line_and_clamped():
    noisy = "  a  caption\nwith\t\tinternal   breaks " + "x " * 300
    fake = _FakeCaptionClient(content=json.dumps({"caption": noisy}))
    caption = caption_image(PNG_BYTES, mime_type="image/png", client=fake)
    assert "\n" not in caption and "\t" not in caption
    assert "  " not in caption
    assert caption.startswith("a caption with internal breaks")
    assert len(caption) == CAPTION_MAX_CHARS


def test_provider_failure_raises_the_bounded_code_and_logs_value_free(caplog):
    fake = _FakeCaptionClient(error=RuntimeError("api_key=sk-secret-key-material"))
    with (
        caplog.at_level(logging.ERROR, logger="ai.core.integrations.image_caption"),
        pytest.raises(ImageCaptionError) as excinfo,
    ):
        caption_image(PNG_BYTES, mime_type="image/png", client=fake)
    assert excinfo.value.code == "ATTACHMENT_CAPTION_FAILED"
    [record] = caplog.records
    rendered = record.getMessage()
    assert "stage=media_caption" in rendered
    assert "error_type=RuntimeError" in rendered
    assert "sk-secret-key-material" not in rendered


def test_unparseable_payload_is_a_caption_failure():
    fake = _FakeCaptionClient(content="not json at all")
    with pytest.raises(ImageCaptionError) as excinfo:
        caption_image(PNG_BYTES, mime_type="image/png", client=fake)
    assert excinfo.value.code == "ATTACHMENT_CAPTION_FAILED"


def test_unconfigured_endpoint_fails_closed_as_unavailable(monkeypatch):
    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: _settings(AZURE_OPENAI_ENDPOINT=""),
    )
    with pytest.raises(ImageCaptionError) as excinfo:
        caption_image(PNG_BYTES, mime_type="image/png", client=None)
    assert excinfo.value.code == "ATTACHMENT_CAPTION_UNAVAILABLE"


def test_non_string_caption_is_an_error():
    fake = _FakeCaptionClient(content='{"caption": 42}')
    with pytest.raises(ImageCaptionError) as excinfo:
        caption_image(PNG_BYTES, mime_type="image/png", client=fake)
    assert excinfo.value.code == "ATTACHMENT_CAPTION_FAILED"
