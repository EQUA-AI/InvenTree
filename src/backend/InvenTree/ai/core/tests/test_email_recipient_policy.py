"""P0-11c: the outbound-email recipient allow-list is a pure, fail-closed policy."""

from __future__ import annotations

import pytest
from ai.core.integrations.email.policy import (
    ENV_VAR,
    check_recipients,
    normalize_recipients,
    recipient_allowlist,
)


def test_unset_variable_means_policy_inactive():
    decision = check_recipients("anyone@example.com", environ={})
    assert decision.policy_active is False
    assert decision.allowed is True
    assert decision.blocked == ()


def test_empty_value_blocks_every_recipient():
    decision = check_recipients("anyone@example.com", environ={ENV_VAR: ""})
    assert decision.policy_active is True
    assert decision.allowed is False
    assert decision.blocked == ("anyone@example.com",)


def test_exact_address_and_domain_suffix_are_allowed():
    env = {ENV_VAR: "Ops@Example.com, @sandbox.test"}
    assert recipient_allowlist(env) == ("ops@example.com", "@sandbox.test")
    decision = check_recipients(["OPS@example.com"], cc="tech@sandbox.test", environ=env)
    assert decision.allowed is True
    assert decision.blocked == ()


def test_one_disallowed_recipient_anywhere_blocks_the_send():
    env = {ENV_VAR: "@sandbox.test"}
    decision = check_recipients("tech@sandbox.test", bcc=["boss@example.com"], environ=env)
    assert decision.allowed is False
    assert decision.blocked == ("boss@example.com",)


def test_domain_entry_does_not_match_a_lookalike_domain():
    env = {ENV_VAR: "@sandbox.test"}
    decision = check_recipients("x@notsandbox.test", environ=env)
    assert decision.allowed is False


def test_no_recipients_is_not_allowed_under_an_active_policy():
    decision = check_recipients([], environ={ENV_VAR: "@sandbox.test"})
    assert decision.allowed is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a@x.test", ("a@x.test",)),
        ("a@x.test, B@y.test", ("a@x.test", "b@y.test")),
        (["a@x.test", "b@y.test"], ("a@x.test", "b@y.test")),
        (["a@x.test,b@y.test"], ("a@x.test", "b@y.test")),
        (None, ()),
    ],
)
def test_normalize_recipients(value, expected):
    assert normalize_recipients(value) == expected


@pytest.mark.parametrize(
    "address",
    [
        "bad@evil.test <good@equa.work>",
        "good@equa.work <bad@evil.test>",
        "bad@evil.test (good@equa.work)",
        "good@equa.work\r\nBcc: bad@evil.test",
        "good@equa.work.evil.test",
        "good@notequa.work",
        "good@sub.equa.work",
        "good@@equa.work",
        "group:good@equa.work;",
        "good@equa.work\x00",
    ],
)
def test_spoofed_mailboxes_are_blocked_in_every_envelope_field(address):
    for field in ("to", "cc", "bcc"):
        payload = {"to": "good@equa.work", field: address}
        assert not check_recipients(**payload, environ={ENV_VAR: "@equa.work"}).allowed


def test_malformed_syntax_is_invalid_even_without_an_allowlist():
    assert not check_recipients("good@equa.work\nBcc: bad@evil.test", environ={}).allowed


def test_case_insensitive_exact_domain_with_plus_addressing():
    assert check_recipients("LOKESH+voice@EQUA.WORK", environ={ENV_VAR: "@equa.work"}).allowed
