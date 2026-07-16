"""WS2-T4: managed-identity token acquisition from the target host.

Opt-in: skips unless ``AIMMS_AZURE_INTEGRATION=1``. Run only from the
approved hosting environment. Tokens are asserted on, never printed,
logged, or persisted.
"""

from __future__ import annotations

import os
import time

import pytest
from ai.core.voice.endpoints import TOKEN_SCOPE

INTEGRATION_ENABLED = os.environ.get("AIMMS_AZURE_INTEGRATION") == "1"

pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="target-host probe; set AIMMS_AZURE_INTEGRATION=1 on the approved host",
)


def _credential():
    azure_identity = pytest.importorskip("azure.identity")
    return azure_identity.DefaultAzureCredential()


def test_token_scope_constant_is_current():
    assert TOKEN_SCOPE == "https://ai.azure.com/.default"


def test_managed_identity_acquires_scoped_token():
    token = _credential().get_token(TOKEN_SCOPE)
    assert token.token, "credential returned an empty token"
    assert token.expires_on > time.time() + 60, "token is already (nearly) expired"
    # Deliberately no output of any token material.


def test_legacy_cognitiveservices_scope_still_resolves():
    token = _credential().get_token("https://cognitiveservices.azure.com/.default")
    assert token.token
    assert token.expires_on > time.time() + 60
