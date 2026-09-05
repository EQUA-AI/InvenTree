"""CR-2 / GR-06: pre-LLM redaction of memory-path payloads."""

# ruff: noqa: E402

from __future__ import annotations

import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import CONFIG_SECRET_VALUE
from ai.core.redaction import (
    CATEGORIES,
    REDACTED,
    format_counts,
    redact_payload,
    redact_text,
)

#: One seed per category; the seed text must vanish and the marker appear.
SEEDS: dict[str, str] = {
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA7\n-----END RSA PRIVATE KEY-----",
    "connection_string": "Server=tcp:db.example.net;Database=x;User Id=sa;Password=Sup3rS3cret!",
    "url_credentials": "postgres://pgadmin:hunter2pass@epconchat-pg-dev.postgres.database.azure.com/inventree",
    "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",  # gitleaks:allow (synthetic redaction seed)
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "openai_key": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
    "github_token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    "slack_token": "xoxb-1234567890-abcdefghij",  # gitleaks:allow (synthetic redaction seed)
    "sas_signature": "https://acct.blob.core.windows.net/c?sv=2024&sig=AbCdEf123456789%3D",
    "azure_key": "AZURE_SEARCH_KEY=0123456789abcdef0123456789abcdef",
    "password": "the password is hunter2",
    "pin": "gate pin: 4321",
    "otp": "your verification code is 123456",
    "token": "bearer 9f8e7d6c5b4a3c2d1e0f",
    "email": "reach me at tech@example.com",
    "phone": "call +1 (555) 123-4567 tomorrow",
}

#: The exact substrings that must NOT survive redaction.
SECRET_FRAGMENTS: dict[str, str] = {
    "private_key": "MIIEowIBAAKCAQEA7",
    "connection_string": "Sup3rS3cret!",
    "url_credentials": "hunter2pass",
    "jwt": "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV",
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "openai_key": "abcdefghijklmnopqrstuvwxyz0123456789",
    "github_token": "abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    "slack_token": "1234567890-abcdefghij",  # gitleaks:allow (synthetic redaction seed)
    "sas_signature": "AbCdEf123456789",
    "azure_key": "0123456789abcdef0123456789abcdef",  # gitleaks:allow (synthetic redaction seed)
    "password": "hunter2",
    "pin": "4321",
    "otp": "123456",
    "token": "9f8e7d6c5b4a3c2d1e0f",  # gitleaks:allow (synthetic redaction seed)
    "email": "tech@example.com",
    "phone": "123-4567",
}

#: Operational atoms a compaction payload legitimately carries. Zero hits.
OPERATIONAL_ATOMS = [
    "content_hash=3b6a1f0c9d2e4b8a7c5d6e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b",
    "source_sha256: 9c1185a5c5e9fc54612808977ee8f548b2258d31",
    "revision --0000065 serving since 2026-09-01",
    "citation manual:pump3:seals section 4.2",
    "serial SN-1234567890 installed 2026-08-13T04:45:00Z",
    "part 12345-678 quantity 4",
    "torque 45 Nm at 120 degrees C, pressure 6.5 bar",
    "work order WO-EVAL-SI3000A-OPEN opened by the day shift",
    "IP 10.0.0.12 port 6432 pool mode transaction",
    "the ambient reading was 47.1 degrees, not 41.7",
]


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_category_is_seeded(category):
    assert category in SEEDS and category in SECRET_FRAGMENTS


@pytest.mark.parametrize("category", CATEGORIES)
def test_seed_is_replaced_by_its_marker(category):
    redacted, counts = redact_text(SEEDS[category])
    assert REDACTED.format(category=category) in redacted
    assert SECRET_FRAGMENTS[category] not in redacted
    assert counts[category] >= 1


def test_mixed_transcript_reports_the_full_count_map():
    payload = {
        "prior_summary": {"machine_facts": [SEEDS["password"], SEEDS["email"]]},
        "new_messages": [
            {"role": "user", "content": SEEDS["jwt"]},
            {"role": "assistant", "content": " ".join((SEEDS["phone"], SEEDS["url_credentials"]))},
        ],
    }
    result = redact_payload(payload)
    assert result.redacted
    assert result.counts == {
        "password": 1,
        "email": 1,
        "jwt": 1,
        "phone": 1,
        "url_credentials": 1,
    }
    # Keys and roles are schema, never redacted.
    assert set(result.value) == {"prior_summary", "new_messages"}
    assert result.value["new_messages"][0]["role"] == "user"
    assert "hunter2" not in repr(result.value)
    assert format_counts(result.counts) == "url_credentials=1,jwt=1,password=1,email=1,phone=1"


def test_redaction_is_idempotent():
    once = redact_payload({"text": list(SEEDS.values())})
    twice = redact_payload(once.value)
    assert twice.value == once.value
    assert not twice.redacted


@pytest.mark.parametrize("atom", OPERATIONAL_ATOMS)
def test_operational_atoms_are_untouched(atom):
    redacted, counts = redact_text(atom)
    assert redacted == atom
    assert not counts


def test_non_string_scalars_pass_through():
    result = redact_payload({"n": 47.1, "ok": True, "none": None, "seq": (1, 2)})
    assert result.value == {"n": 47.1, "ok": True, "none": None, "seq": (1, 2)}
    assert not result.redacted


def test_parity_with_the_settings_dump_vocabulary():
    """Every value shape ``redact_config`` masks (S44) is also redacted here."""
    samples = [
        "endpoint=https://x.search.windows.net?api-key=abcdef0123456789",  # gitleaks:allow (synthetic redaction seed)
        "sig=AbCdEf%2B123456789",
        "SharedAccessKey=Zm9vYmFyYmF6cXV4",
        "AccountKey=Zm9vYmFyYmF6cXV4Zm9v",
        "password=hunter2",
        "secret=Zm9vYmFyYmF6",
        "token=Zm9vYmFyYmF6",
        "https://user:pass@host.example/path",
    ]
    for sample in samples:
        assert CONFIG_SECRET_VALUE.search(sample), sample
        redacted, counts = redact_text(sample)
        assert counts, f"unredacted: {sample}"
        assert "[REDACTED:" in redacted


def test_markers_survive_the_directive_scrub():
    """``strip_tool_directives`` drops items carrying directive substrings."""
    from aichat.tasks import _TOOL_DIRECTIVE_MARKERS

    for category in CATEGORIES:
        marker = REDACTED.format(category=category).lower()
        assert not any(directive in marker for directive in _TOOL_DIRECTIVE_MARKERS), category


def test_log_line_carries_counts_and_never_the_seed(caplog):
    logger = logging.getLogger("test.redaction")
    result = redact_payload({"content": SEEDS["password"]})
    with caplog.at_level(logging.INFO, logger="test.redaction"):
        logger.info("Thread compaction redaction counts=%s", format_counts(result.counts))
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "password=1" in joined
    assert "hunter2" not in joined
