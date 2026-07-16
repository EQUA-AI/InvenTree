"""WS2-T5: smoke-test the provisioned Foundry reasoning path.

Opt-in: skips unless ``AIMMS_AZURE_INTEGRATION=1``. Mirrors the owner's
verified invocation pattern — ``AIProjectClient`` → ``get_openai_client()``
→ ``responses.create(extra_body={"agent_reference": ...})`` against project
``Epcon-AIMMS`` with the pinned agent version. Effort-value policing is
covered deterministically by ``test_luna_diagnostics.py``; this module is
live-estate evidence only. No prompt or output content is asserted beyond
shape, and nothing secret is printed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

MANIFEST_PATH = Path(__file__).parent / "azure_validation_manifest.example.json"
INTEGRATION_ENABLED = os.environ.get("AIMMS_AZURE_INTEGRATION") == "1"

pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="target-host probe; set AIMMS_AZURE_INTEGRATION=1 on the approved host",
)


@pytest.fixture(scope="module")
def manifest() -> dict:
    """Manifest."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def openai_client(manifest):
    """Openai client."""
    azure_identity = pytest.importorskip("azure.identity")
    projects = pytest.importorskip("azure.ai.projects")
    client = projects.AIProjectClient(
        endpoint=manifest["foundry_project_endpoint"],
        credential=azure_identity.DefaultAzureCredential(),
    )
    return client.get_openai_client()


def test_agent_reference_answers(openai_client, manifest):
    """Agent reference answers."""
    reasoning = manifest["reasoning"]
    started = time.monotonic()
    response = openai_client.responses.create(
        input=[{"role": "user", "content": "Reply with the single word: ready"}],
        extra_body={
            "agent_reference": {
                "name": reasoning["agent_name"],
                "version": reasoning["agent_version"],
                "type": "agent_reference",
            }
        },
    )
    elapsed = time.monotonic() - started
    assert getattr(response, "output_text", ""), "agent returned no output_text"
    # Latency is recorded as evidence, not asserted as a gate here.
    print(f"agent_reference smoke latency: {elapsed:.2f}s")


def test_pinned_agent_version_is_required(openai_client, manifest):
    """Pinned agent version is required."""
    reasoning = manifest["reasoning"]
    # The Azure SDK's exact exception type for an unknown agent version is
    # not part of its stable contract, so a broad assertion is intentional.
    with pytest.raises(Exception):  # noqa: B017
        openai_client.responses.create(
            input=[{"role": "user", "content": "ping"}],
            extra_body={
                "agent_reference": {
                    "name": reasoning["agent_name"],
                    "version": "999999",
                    "type": "agent_reference",
                }
            },
        )
