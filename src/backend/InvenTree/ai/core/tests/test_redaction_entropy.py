"""M2 PR 4 (§5.9): the count-only entropy shadow beside the redaction families.

Mirrors ``test_redaction.py``: operational atoms are a zero-hit gate, seeded
high-entropy blobs count, the ``[REDACTED:...]`` markers never count, and a
log line carries the count and never a seed.
"""

from __future__ import annotations

import logging

import pytest
from ai.core.redaction import (
    OPERATIONAL_ATOM_PATTERNS,
    REDACTED,
    REMAINDER_ONLY_ATOMS,
    entropy_bits,
    entropy_flags,
    is_operational_atom,
    redact_payload,
)

#: Operational atoms a compaction payload legitimately carries. Zero flags.
OPERATIONAL_ATOMS = [
    "content_hash=3b6a1f0c9d2e4b8a7c5d6e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b",
    "source_sha256: 9c1185a5c5e9fc54612808977ee8f548b2258d31",
    "md5 d41d8cd98f00b204e9800998ecf8427e",
    "revision --0000065 serving since 2026-09-01",
    "aimms-experimental--0000069 promoted at 2026-09-08T23:59:59.123456+00:00",
    "citation manual:pump3:seals section 4.2",
    "eaits-manuals-v4a:hx200:gasket:datasheet:section:4.2.1",
    "(eaits-manuals-v4a:hx200:gasket), then the seal",
    # A two-segment key is not a citation atom (its remainder has no colon);
    # it stays at zero on entropy alone, like any other lower-case label.
    "eaits-manuals-v4a:hx200 revision 3",
    "serial SN-1234567890 installed 2026-08-13T04:45:00Z",
    "part 12345-678 quantity 4",
    "torque 45 Nm at 120 degrees C, pressure 6.5 bar",
    "work order WO-EVAL-SI3000A-OPEN-BATCH-7 opened by the day shift",
    "(WO-EVAL-SI3000A-OPEN-BATCH-7), then closed",
    "IP 10.0.0.12 port 6432 pool mode transaction",
    "the ambient reading was 47.1 degrees, not 41.7",
    "https://epconchat-pg-dev.postgres.database.azure.com/inventree",
    "python-3.12-inventree_worker_default_group",
]

#: High-entropy blobs no category family names. Each must count once.
ENTROPY_SEEDS = {
    "base64_blob": "aXk3Lm9+Qw2Vb7Rt5Yz1Nc8Pd4Fg6Hj0Ks2Ml5Nx==",  # 42 chars
    "synthetic_api_key": "Q7vX2mLp9RkT4nWz8JbH3cYd6FgS1aE0",  # gitleaks:allow (synthetic entropy seed)
    "mixed_case_key": "R2d5Lq9Xw3Zb7Vn1Mk4Tc8Yp6Hf0Js3Gd7Wq2",  # gitleaks:allow (synthetic entropy seed)
}

#: The same blobs behind a label no category family names: the shape the
#: families miss and the shadow exists to count. A ``label:`` prefix is a
#: key prefix, not a citation, so each must count once.
PREFIXED_SEEDS = {
    "x_colon": "x:" + ENTROPY_SEEDS["synthetic_api_key"],
    "secret_value_colon": "secret_value:" + ENTROPY_SEEDS["synthetic_api_key"],
    "hash_colon": "hash:" + ENTROPY_SEEDS["mixed_case_key"],
    "session_colon": "session:" + ENTROPY_SEEDS["base64_blob"],
    "upper_label_colon": "AAAA:" + ENTROPY_SEEDS["synthetic_api_key"],
    "foo_equals": "foo=" + ENTROPY_SEEDS["synthetic_api_key"],
}

#: Every atom family must be represented by at least one atom above.
ATOM_EXAMPLES = {
    "hex_digest": "9c1185a5c5e9fc54612808977ee8f548b2258d31",
    "revision_id": "aimms-experimental--0000069",
    "iso_timestamp": "2026-08-13T04:45:00Z",
    "serial_ref": "WO-EVAL-SI3000A-OPEN",
    "citation_key": "manual:pump3:seals",
}


def test_entropy_bits_is_shannon_per_char():
    assert entropy_bits("") == pytest.approx(0.0)
    assert entropy_bits("aaaa") == pytest.approx(0.0)
    assert entropy_bits("ab") == pytest.approx(1.0)
    assert entropy_bits("abcd") == pytest.approx(2.0)
    assert entropy_bits("0123456789abcdef") == pytest.approx(4.0)


@pytest.mark.parametrize("name", [name for name, _ in OPERATIONAL_ATOM_PATTERNS])
def test_every_atom_family_has_an_example(name):
    assert is_operational_atom(ATOM_EXAMPLES[name]), name


@pytest.mark.parametrize("atom", OPERATIONAL_ATOMS)
def test_operational_atoms_never_flag(atom):
    assert entropy_flags(atom) == 0
    assert entropy_flags({"prior_summary": {"machine_facts": [atom]}}) == 0


def test_serial_ref_needs_a_digit_and_a_letter():
    assert is_operational_atom("WO-EVAL-SI3000A")
    assert not is_operational_atom("ABCDEF-GHIJKL")
    assert not is_operational_atom("123456-789012")


@pytest.mark.parametrize("name", sorted(ENTROPY_SEEDS))
def test_seeded_blob_counts_once(name):
    seed = ENTROPY_SEEDS[name]
    assert len(seed) >= 20 and entropy_bits(seed) > 4.5, name
    assert not is_operational_atom(seed), name
    assert entropy_flags(f"the value was {seed} yesterday") == 1
    assert entropy_flags({"new_messages": [{"role": "user", "content": seed}]}) == 1


@pytest.mark.parametrize("name", sorted(PREFIXED_SEEDS))
def test_prefixed_blob_is_not_a_citation_and_counts_once(name):
    token = PREFIXED_SEEDS[name]
    assert not is_operational_atom(token), name
    assert entropy_flags(f"the value was {token} yesterday") == 1, name
    assert entropy_flags({"new_messages": [{"role": "user", "content": token}]}) == 1


def test_family_named_prefix_is_the_families_hit_not_the_shadows():
    """``secret:<blob>`` redacts first (token family), so the shadow stays 0."""
    token = "secret:" + ENTROPY_SEEDS["synthetic_api_key"]
    assert entropy_flags(token) == 0
    assert "[REDACTED:token]" in redact_payload(token).value


def test_citation_key_is_lower_case_and_judged_on_the_remainder():
    families = {name for name, _ in OPERATIONAL_ATOM_PATTERNS}
    assert sorted(REMAINDER_ONLY_ATOMS) == ["citation_key"]
    assert REMAINDER_ONLY_ATOMS.issubset(families)
    assert is_operational_atom("manual:pump3:seals")
    assert is_operational_atom("(manual:pump3:seals),")
    assert is_operational_atom("eaits-manuals-v4a:hx200:gasket:datasheet:section:4.2.1")
    # A mixed-case segment is the blob shape, never a corpus key.
    assert not is_operational_atom("Manual:Pump3:Seals")
    assert not is_operational_atom("manual:" + ENTROPY_SEEDS["synthetic_api_key"])
    # The raw token is never judged as a citation: only what follows the
    # first ``label:`` is, so a single-colon token needs its remainder to be
    # a key of its own.
    assert not is_operational_atom("x:" + ENTROPY_SEEDS["synthetic_api_key"])


def test_flags_sum_across_leaves_and_respect_min_len():
    payload = {
        "prior_summary": {"machine_facts": [ENTROPY_SEEDS["base64_blob"]]},
        "new_messages": [
            {"role": "user", "content": " ".join(ENTROPY_SEEDS.values())},
            {"role": "assistant", "content": "seal worn, nothing else"},
        ],
    }
    assert entropy_flags(payload) == 4
    assert entropy_flags(payload, min_len=64) == 0
    assert entropy_flags(payload, threshold_bits=6.0) == 0


def test_markers_never_count():
    """A redacted secret is a marker, not an entropy hit — no double counting."""
    from ai.core.tests.test_redaction import SEEDS

    markers = " ".join(REDACTED.format(category=c) for c in sorted(SEEDS))
    assert entropy_flags(markers) == 0
    assert entropy_flags("password=[REDACTED:password] token=[REDACTED:token]") == 0
    # Raw seeds redact first (idempotent on an already-redacted value): the
    # families own them, so the shadow count stays at the family-free blobs.
    raw = {"content": list(SEEDS.values())}
    assert entropy_flags(raw) == entropy_flags(redact_payload(raw).value)
    assert entropy_flags({"content": [SEEDS["jwt"], SEEDS["openai_key"]]}) == 0


def test_non_string_leaves_are_ignored():
    assert entropy_flags({"n": 47.1, "ok": True, "none": None, "seq": (1, 2), "d": {}}) == 0
    assert entropy_flags([]) == 0
    assert entropy_flags(None) == 0


def test_log_line_carries_the_count_and_never_the_seed(caplog):
    logger = logging.getLogger("test.redaction.entropy")
    seed = ENTROPY_SEEDS["synthetic_api_key"]
    flags = entropy_flags({"content": f"the key was {seed}"})
    with caplog.at_level(logging.INFO, logger="test.redaction.entropy"):
        if flags:
            logger.info("Thread compaction entropy flags=%d", flags)
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "entropy flags=1" in joined
    assert seed not in joined
