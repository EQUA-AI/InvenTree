"""Fail-closed contract for the focused island schema bootstrap (RV13).

``ai.core.tests.focus_test_db.ensure_island_tables`` creates installed apps'
managed tables directly through Django's schema editor (NO migrations) for the
per-run owned disposable SQLite in ``ai.core.tests.settings``. This module
proves the bootstrap FAILS CLOSED: when a required managed table genuinely
remains absent, ``ensure_island_tables`` must raise and ``_BOOTSTRAPPED`` must
stay ``False`` — success is only accepted against fresh introspection showing
every required managed table, never against the bootstrap flag alone.

Failure injection happens at the REAL schema editor's ``create_model``
side effect for one real required model in disposable state (a genuinely
dropped table in the owned test database). The helper under test is never
faked or replaced.
"""

# ruff: noqa: E402

from __future__ import annotations

import contextlib
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import pytest
from ai.core.tests import focus_test_db
from ai.core.tests.focus_test_db import ensure_island_tables
from django.apps import apps
from django.db import connection


def _required_tables() -> set[str]:
    """db_table names the gate must cover: registered managed models.

    The cohort is built with ``include_auto_created=True`` so it also covers
    automatically created managed models (implicit many-to-many through
    tables): plain ``apps.get_models()`` silently excludes them, which would
    let a missing required table pass the gate. Unmanaged models are a
    deliberate exclusion (their tables are owned by other provisioning,
    not this bootstrap).
    """
    return {
        model._meta.db_table
        for model in apps.get_models(include_auto_created=True)
        if model._meta.managed
    }


def _present_tables() -> set[str]:
    """Fresh introspection evidence of the actual schema."""
    return set(connection.introspection.table_names())


def _target_model():
    """A real required managed model with no inbound foreign keys."""
    return apps.get_model("sessions", "Session")


def _drop_required_table(target_model) -> None:
    """Disposable state: make one real required table genuinely absent."""
    with connection.schema_editor() as schema_editor:
        schema_editor.delete_model(target_model)


@contextlib.contextmanager
def _schema_editor_failure_for(target_model):
    """Inject a failure at the schema editor's create_model side effect.

    Only the schema editor that actually emits CREATE TABLE is made to fail
    for one real model; ``ensure_island_tables`` itself is untouched.
    """
    real_schema_editor = connection.schema_editor

    @contextlib.contextmanager
    def failing_schema_editor(*args, **kwargs):
        with real_schema_editor(*args, **kwargs) as schema_editor:
            real_create_model = schema_editor.create_model

            def create_model(model, *create_args, **create_kwargs):
                if model is target_model:
                    raise RuntimeError(
                        "injected schema-editor failure for " + target_model._meta.db_table
                    )
                return real_create_model(model, *create_args, **create_kwargs)

            schema_editor.create_model = create_model
            yield schema_editor

    connection.schema_editor = failing_schema_editor
    try:
        yield
    finally:
        del connection.schema_editor


@pytest.fixture(autouse=True)
def _complete_schema_afterwards():
    """Every test exits with the disposable schema complete again."""
    yield
    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()


def test_complete_creation_reconciles_every_required_table():
    """Real complete creation: all required managed tables actually exist."""
    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()
    assert focus_test_db._BOOTSTRAPPED is True
    missing = _required_tables() - _present_tables()
    assert missing == set()


def test_second_call_is_idempotent_and_reconciled():
    """The second call is a no-op and the schema stays complete."""
    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()
    before = _present_tables()
    ensure_island_tables()  # early return, must not raise
    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()  # re-entry with a complete schema
    assert _present_tables() == before
    assert _required_tables() <= _present_tables()
    assert focus_test_db._BOOTSTRAPPED is True


def test_fail_closed_when_required_table_unresolved():
    """A genuinely unresolved required table must fail the bootstrap."""
    target = _target_model()
    table = target._meta.db_table
    assert table in _required_tables()

    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()  # real complete creation baseline
    _drop_required_table(target)
    assert table not in _present_tables()

    with _schema_editor_failure_for(target):
        focus_test_db._BOOTSTRAPPED = False
        with pytest.raises(RuntimeError) as excinfo:
            ensure_island_tables()

    message = str(excinfo.value)
    assert table in message
    # Errors must not leak connection/config values.
    assert str(connection.settings_dict.get("NAME") or "") not in message
    assert focus_test_db._BOOTSTRAPPED is False
    assert table not in _present_tables()  # fresh introspection, not the flag


def test_recovery_after_injected_failure_creates_missing_table():
    """After the injected failure is removed, a retry resolves the table."""
    target = _target_model()
    table = target._meta.db_table

    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()
    _drop_required_table(target)

    with _schema_editor_failure_for(target):
        focus_test_db._BOOTSTRAPPED = False
        with pytest.raises(RuntimeError):
            ensure_island_tables()
        assert focus_test_db._BOOTSTRAPPED is False
        assert table not in _present_tables()

    # Subsequent recovery with the real schema editor available.
    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()
    assert focus_test_db._BOOTSTRAPPED is True
    assert table in _present_tables()
    assert _required_tables() <= _present_tables()


def test_required_implicit_through_table_cannot_be_silently_absent():
    """A missing managed implicit M2M through table must fail the gate.

    ``apps.get_models()`` excludes automatically created managed through
    models, so a verification cohort built from it would silently accept a
    genuinely missing required table. The real ORM target is the auto-created
    managed through model of ``auth.Group.permissions``.
    """
    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()  # real complete creation baseline
    required = _required_tables()
    present = _present_tables()
    assert required <= present, "Complete creation omitted registered managed tables"

    group = apps.get_model("auth", "Group")
    through = group._meta.get_field("permissions").remote_field.through
    assert through._meta.managed and through._meta.auto_created
    implicit = {
        model._meta.db_table
        for model in apps.get_models(include_auto_created=True)
        if model._meta.managed and model._meta.auto_created
    }
    table = through._meta.db_table
    assert implicit, "Complete cohort must include managed implicit tables"
    assert table in implicit
    assert table in required and table in present

    _drop_required_table(through)  # delete ONLY the through table
    assert table not in _present_tables()
    try:
        focus_test_db._BOOTSTRAPPED = False
        with pytest.raises(RuntimeError) as excinfo:
            ensure_island_tables()
        message = str(excinfo.value)
        assert table in message
        # Errors must not leak connection/config values.
        assert str(connection.settings_dict.get("NAME") or "") not in message
        assert focus_test_db._BOOTSTRAPPED is False
        assert table not in _present_tables()  # fresh introspection, not the flag
    finally:
        # Actual repair: recreate only the dropped through table.
        if table not in _present_tables():
            with connection.schema_editor() as schema_editor:
                schema_editor.create_model(through)

    # Retry after repair with the real schema editor available.
    focus_test_db._BOOTSTRAPPED = False
    ensure_island_tables()
    assert focus_test_db._BOOTSTRAPPED is True
    assert table in _present_tables()
    assert _required_tables() <= _present_tables()
