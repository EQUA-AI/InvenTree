"""S4 native document actor handoff after real synthetic ASGI authentication.

Uses disposable ORM users and real signing/middleware, not a real login,
network endpoint, strict S1/S3 request bridge or document/grant qualification.
"""

import asyncio
import sys
from importlib import import_module
from types import ModuleType, SimpleNamespace

import pytest
from ai.core import auth, config
from asgiref.sync import sync_to_async
from django.test import override_settings

pytestmark = pytest.mark.django_db(transaction=True)
READERS = (
    "attachment_corpus",
    "media_corpus",
    "controlled_document_corpus",
    "source_inventory_tools",
)


@pytest.fixture
def source_user(django_user_model):
    return django_user_model.objects.create_user(username="synthetic-s4-document-actor")


def _authenticated_reader(source_user, reader, mutation, monkeypatch):
    policy = auth.AIBoundaryPolicy(
        single_site_policy_key="synthetic-site",
        policy_version="synthetic-policy-v1",
        signed_subject_max_age_seconds=60,
        signed_subject_salt="s4-synthetic-signing",
        signed_subject_audience="s4-synthetic",
        allowed_origins=(),
    )
    current = SimpleNamespace(**{
        name: getattr(policy, name) for name in policy.__dataclass_fields__
    })
    monkeypatch.setattr(config, "get_settings", lambda: current)
    observed, messages = {}, []
    other = type(source_user).objects.create_user(username="synthetic-s4-other-actor")

    async def application(scope, _receive, send):
        principal = auth.get_current_principal()
        observed["principal"] = principal
        if mutation == "inactive":
            await sync_to_async(type(source_user).objects.filter(pk=source_user.pk).update)(
                is_active=False
            )
        elif mutation == "site-policy":
            current.single_site_policy_key = "different-synthetic-site"
        elif mutation == "policy-version":
            current.policy_version = "different-synthetic-policy"
        if mutation.endswith("during-lookup"):
            queryset_type = type(type(source_user).objects.all())
            original_first = queryset_type.first
            changed = []

            def first(queryset):
                record = original_first(queryset)
                if queryset.model is type(source_user) and record is not None and not changed:
                    changed.append(True)
                    if mutation == "actor-during-lookup":
                        auth.principal_context.set(auth.principal_for_user(other))
                    elif mutation == "site-during-lookup":
                        current.single_site_policy_key = "different-synthetic-site"
                    else:
                        current.policy_version = "different-synthetic-policy"
                return record

            monkeypatch.setattr(queryset_type, "first", first)
        actor = await sync_to_async(reader)()
        observed["actor_pk"] = actor.pk if actor is not None else None
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"synthetic"})

    async def receive():  # noqa: RUF029 - ASGI protocol fixture
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):  # noqa: RUF029 - ASGI protocol fixture
        messages.append(message)

    with override_settings(SECRET_KEY="s4-synthetic-signing-only"):
        token = auth.sign_interactive_subject(source_user, policy=policy)
        middleware = auth.AIBoundaryAuthMiddleware(application, policy=policy)
        asyncio.run(
            middleware(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/synthetic-document-actor",
                    "scheme": "https",
                    "server": ("testserver", 443),
                    "query_string": b"",
                    "headers": [(b"authorization", ("Bearer " + token).encode())],
                },
                receive,
                send,
            )
        )
    assert messages[0]["status"] == 200
    assert isinstance(observed["principal"], auth.AIPrincipal)
    assert observed["principal"].authentication_method == "signed_subject"
    assert observed["principal"].user_pk == str(source_user.pk)
    assert auth.get_current_principal() is None
    return observed["actor_pk"]


@pytest.mark.parametrize("reader_name", READERS)
@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "inactive",
        "site-policy",
        "policy-version",
        "actor-during-lookup",
        "site-during-lookup",
        "policy-during-lookup",
    ],
)
def test_document_actor_is_current_at_read_after_authenticated_intake(
    source_user, reader_name, mutation, monkeypatch
):
    """A valid intake is not permission to reuse a stale account or policy."""
    reader = import_module("ai.core.integrations." + reader_name)._current_user
    actual = _authenticated_reader(source_user, reader, mutation, monkeypatch)
    assert actual == (source_user.pk if mutation == "none" else None)


@pytest.mark.parametrize("blocker", ["role", "restore-hold"])
def test_registry_authority_is_checked_before_machine_candidate_metadata(
    source_user, monkeypatch, blocker
):
    """Native role/hold gates stop the real tool body before its lookup seam."""
    from ai.core.integrations import source_inventory_tools
    from ai.core.integrations.retrieval_authority import fresh_role
    from django.contrib.auth.models import Group
    from InvenTree.restore_hold import restore_hold_enabled
    from users.models import RuleSet

    policy = auth.AIBoundaryPolicy(
        single_site_policy_key="synthetic-site",
        policy_version="synthetic-policy-v1",
        signed_subject_max_age_seconds=60,
        signed_subject_salt="s4-synthetic-signing",
        signed_subject_audience="s4-synthetic",
        allowed_origins=(),
    )
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(**{
            name: getattr(policy, name) for name in policy.__dataclass_fields__
        }),
    )
    group = Group.objects.create(name="synthetic-s4-document-role")
    source_user.groups.add(group)
    role, _ = RuleSet.objects.update_or_create(
        group=group, name="work_order", defaults={"can_view": True}
    )
    monkeypatch.setenv("INVENTREE_RESTORE_HOLD", "off")
    calls = []

    def candidates(actor, **_kwargs):
        calls.append(actor.pk)
        return [
            SimpleNamespace(pk=41, name="Synthetic alpha"),
            SimpleNamespace(pk=42, name="Synthetic beta"),
        ]

    # Own this explicitly scripted seam: another cohort module installs an
    # import-time assets.ai_read stub. Native resource scope is not claimed.
    lookup = ModuleType("assets.ai_read")
    monkeypatch.setattr(lookup, "machines_in_scope", candidates, raising=False)
    monkeypatch.setitem(sys.modules, "assets.ai_read", lookup)

    def invoke():
        token = auth.principal_context.set(auth.principal_for_user(source_user))
        try:
            return asyncio.run(source_inventory_tools.list_document_sources(machine="Synthetic"))
        finally:
            auth.principal_context.reset(token)

    assert invoke()["machine_candidates"] == ["Synthetic alpha", "Synthetic beta"]
    assert calls == [source_user.pk]
    if blocker == "role":
        role.can_view = False
        role.save(update_fields=["can_view"])
    else:
        monkeypatch.setenv("INVENTREE_RESTORE_HOLD", "on")
    assert fresh_role(source_user, "work_order") is (blocker == "restore-hold")
    assert restore_hold_enabled() is (blocker == "restore-hold")
    calls.clear()
    try:
        denied = invoke()
    finally:
        role.can_view = True
        role.save(update_fields=["can_view"])
        monkeypatch.setenv("INVENTREE_RESTORE_HOLD", "off")
    assert calls == []
    assert denied["success"] is False
    assert "machine_candidates" not in denied
    assert invoke()["machine_candidates"] == ["Synthetic alpha", "Synthetic beta"]
