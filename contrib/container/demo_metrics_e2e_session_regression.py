"""Regression: defaults initialization must happen BEFORE any browser login.

Background (2026-09 E2E session invalidation): on a fresh dedicated database
whose ``common_inventreesetting`` table does not yet contain the
``LOGIN_ENFORCE_MFA`` row, the first authenticated request that reaches the
MFA middleware (``InvenTreeMfaCheckMiddleware.process_view`` -> ``enforce_2fa``
-> ``get_global_setting('LOGIN_ENFORCE_MFA')``) triggers create-on-read via
``InvenTreeSetting.get_setting_object(create=True)``. ``Setting.save()`` runs
``after_save`` callbacks unconditionally, so the ``enforce_mfa`` callback fires
even though the setting keeps its default (False) and ends ALL allauth user
sessions mid-run (``UserSession.end()`` deletes the django session store entry
and the usersessions row).

Root-cause accuracy: on a real browser the requests that follow the wipe get
genuine 401s. Under this harness's Django test client the in-memory session is
NOT bound to the DB ``usersessions`` rows, so a follow-up request can still
return 200. The reliable, assertable signal of the regression is therefore
UserSession-ROW SURVIVAL, not an exact browser 401.

InvenTree's own test bootstrap (InvenTree/unit_test.py) calls
``InvenTreeSetting.build_default_values()`` before exercising the app; this
harness must do the same in fixture setup BEFORE any login. This script is the
regression for that contract.

Safety posture (DIAGNOSTIC -- disposable ``inventree_dm_e2e_*`` DB only):

  * All DB-identity guards (disposable name, exact NAME match, exact dev DB host
    ``db``, PostgreSQL engine) are enforced BEFORE any query or write, so an
    independent launch that skips the demo_metrics_e2e_settings shim still
    cannot reach a remote / host-mounted / non-postgres / non-disposable target.
  * The diagnostic forces THIS process's default cache to a private LocMemCache
    so the cold probe's single-key cache delete can never touch a shared Redis
    used by a concurrent default server. The application's real cache config is
    not modified (override is process-local and restored on exit).
  * The ``isReadOnlyCommand`` / ``InvenTreeSetting.save`` probe patches are
    restored in a ``finally`` block even on early exceptions.

Modes (run inside the dev container, disposable database only):

  default    GREEN check: assert the fixture setup ALREADY initialized the
             defaults (a missing row is a setup regression this script must NOT
             paper over), log in a real test-client session (harness-only
             ``force_login`` -- no server auth bypass), hit a non-bypassed
             endpoint, and assert the user's UserSession rows survive and every
             request stays authenticated (200s + row survival).
  --cold     Diagnostic RED mode (DESTRUCTIVE, disposable-DB only): simulates
             the fresh-DB state by deleting the LOGIN_ENFORCE_MFA row (+ its
             single process-local cache key), shows the UserSession-row wipe and
             prints the create-on-read call stack (paths / function names / line
             ids ONLY -- never source text). It REFUSES to run when the MFA
             setting is already non-default/enabled (it would overwrite a
             deliberate value) and, in a ``finally`` block, restores the row to
             its ORIGINAL state (absent stays absent). It CANNOT restore the
             session rows it invalidates -- those are gone for good -- so it is
             destructive and must only ever run on a throwaway database.

Observes only row counts / status codes / booleans / paths -- never session
keys, tokens, cookies, passwords, or source snippets.
"""

from __future__ import annotations

import os
import re
import sys
import traceback

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings

from allauth.usersessions.models import UserSession

from common.models import InvenTreeSetting

# ---- DB identity guards: enforced BEFORE any query or write ----------------
DB_NAME = os.environ.get('DM_E2E_DB', 'inventree_dm_e2e_v3')
_DISPOSABLE_DB_RE = re.compile(r'^inventree_dm_e2e_[a-z0-9_]{1,40}$')
_FORBIDDEN_DB = {'inventree', 'postgres', 'template0', 'template1'}
if (
    not _DISPOSABLE_DB_RE.fullmatch(DB_NAME)
    or DB_NAME in _FORBIDDEN_DB
    or DB_NAME.startswith('test_')
):
    raise SystemExit(f'refusing non-disposable database name {DB_NAME!r}')

# Refuse the wrong database BEFORE writing anything anywhere.
_actual = settings.DATABASES['default']['NAME']
if str(_actual) != DB_NAME:
    raise SystemExit(f'refusing to run against database {_actual!r}; want {DB_NAME!r}')

# Exact host check: the dev container's DB host literally. NO environment opt-out
# -- an independent launch without the settings shim must never reach an
# arbitrary / remote / host-mounted database.
_EXPECTED_DB_HOST = 'db'
_actual_host = str(settings.DATABASES['default']['HOST'])
if _actual_host != _EXPECTED_DB_HOST:
    raise SystemExit(
        f'refusing database host {_actual_host!r}; want exactly {_EXPECTED_DB_HOST!r}'
    )

# PostgreSQL engine only.
_actual_engine = str(settings.DATABASES['default']['ENGINE'])
if _actual_engine != 'django.db.backends.postgresql':
    raise SystemExit(f'refusing non-postgresql engine {_actual_engine!r}')

# ---- process-local cache isolation ----------------------------------------
# The cold probe's single-key cache delete must never reach a shared Redis that
# a concurrent default server may be using. Force THIS process's default cache
# to a private LocMemCache so any delete is process-local. The application's
# persisted cache configuration is NOT changed; the override is process-local
# and is restored below.
_cache_override = override_settings(
    CACHES={
        **settings.CACHES,
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'dm-e2e-session-regression',
        },
    }
)

COLD = '--cold' in sys.argv or os.environ.get('DM_E2E_COLD') == '1'
PROBE_USER = os.environ.get('DM_E2E_REUSER', 'dm_e2e_operator')
MFA_KEY = 'LOGIN_ENFORCE_MFA'
CACHE_KEY = f'InvenTreeSetting:{MFA_KEY}'


def _is_default_false(raw) -> bool:
    """LOGIN_ENFORCE_MFA defaults to False; these spell that default value."""
    return str(raw).strip().lower() in {'', '0', 'false', 'no', 'off'}


def setting_row_present() -> bool:
    """Non-mutating presence check (never create-on-read)."""
    return InvenTreeSetting.objects.filter(key__iexact=MFA_KEY).exists()


User = get_user_model()
user = User.objects.filter(username=PROBE_USER, is_active=True).first()
if user is None:
    raise SystemExit(f'probe user {PROBE_USER!r} not present (run fixture setup first)')


def user_session_count() -> int:
    """Non-mutating count of the probe user's tracked sessions."""
    return UserSession.objects.filter(user=user).count()


report = {
    'mode': 'cold' if COLD else 'green',
    'mfa_enabled': bool(settings.MFA_ENABLED),
}

# Capture the ORIGINAL MFA row state before any mutation, so cold can restore it
# exactly (including leaving it absent when it started absent).
_orig_row = InvenTreeSetting.objects.filter(key__iexact=MFA_KEY).first()
_orig_present = _orig_row is not None
_orig_value = '' if _orig_row is None else str(getattr(_orig_row, 'value', ''))
report['row_present_before'] = _orig_present

if not COLD and not _orig_present:
    raise SystemExit('fixture setup must initialize LOGIN_ENFORCE_MFA before login')

# Cold REFUSES a non-default / enabled / preconfigured MFA value: re-materializing
# the default would silently overwrite a deliberate setting.
if COLD and _orig_present and not _is_default_false(_orig_value):
    raise SystemExit(
        'refusing cold probe: LOGIN_ENFORCE_MFA is non-default/enabled '
        'and cold would overwrite a deliberate setting'
    )

# IMPORTANT: under `manage.py shell`, InvenTree.ready.isReadOnlyCommand() is
# True ('shell' is a read-only command) and get_global_setting() then refuses
# database creates. The real harness runs under `runserver`, where the
# create-on-read path IS active. Disable the read-only guard here so the probe
# exercises exactly the write behavior of the real server (probe-only patch,
# restored in the finally block below).
import InvenTree.ready as inventree_ready

report['shell_would_be_readonly'] = bool(inventree_ready.isReadOnlyCommand())
_orig_is_read_only = inventree_ready.isReadOnlyCommand
_orig_setting_save = InvenTreeSetting.save
_save_patched = False
stack_box = []

_cache_override.enable()
try:
    inventree_ready.isReadOnlyCommand = lambda: False

    if COLD:
        # Simulate the fresh-DB state: the default row is not materialized yet.
        InvenTreeSetting.objects.filter(key__iexact=MFA_KEY).delete()
        # Process-local single-key delete (never a global cache.clear, and never
        # the shared Redis -- the default cache was overridden to LocMem above).
        cache.delete(CACHE_KEY)

        # Scratch-only instrumentation: capture the create-on-read stack.
        def traced_save(self, *args, **kwargs):
            """Record the call stack when the MFA row is created on read."""
            if str(self.key).upper() == MFA_KEY and not self.pk:
                stack_box.append(traceback.extract_stack()[:-1])
            return _orig_setting_save(self, *args, **kwargs)

        InvenTreeSetting.save = traced_save
        _save_patched = True

    if not COLD:
        # The harness fix: supported defaults initialization BEFORE login. This
        # only creates MISSING rows (bulk_create, no after_save callbacks). On a
        # correctly set-up fixture the row already exists, so this is a no-op;
        # a missing row is surfaced by the row_present_before assertion below.
        InvenTreeSetting.build_default_values()
    report['row_present_after_init'] = setting_row_present()

    client = Client()
    client.force_login(user)  # real django test-client session (harness-only)

    r_me = client.get('/api/user/me/')
    report['me_status'] = r_me.status_code

    # Guarantee at least one tracked user session (the row type the wipe
    # destroys). Uses the app's own create_from_request, exactly what the
    # UserSessionsMiddleware runs per request on the real server.
    if user_session_count() == 0:
        from django.test import RequestFactory

        track_req = RequestFactory().get('/')
        track_req.user = user
        track_req.session = client.session
        UserSession.objects.create_from_request(track_req)

    report['sessions_before_trigger'] = user_session_count()

    # First authenticated request that is NOT in pages_mfa_bypass: this is where
    # the MFA middleware evaluates enforce_2fa -> get_global_setting.
    r_trigger = client.get('/api/settings/user/')
    report['trigger_status'] = r_trigger.status_code
    report['sessions_after_trigger'] = user_session_count()
    report['session_rows_survived'] = (
        report['sessions_after_trigger'] >= report['sessions_before_trigger'] > 0
    )

    # A follow-up request must still be authenticated (200). Note the test
    # client's in-memory session is not bound to the DB usersessions rows, so
    # this is a weaker signal than row survival -- see module docstring.
    r_follow = client.get('/api/user/me/')
    report['me_status_after'] = r_follow.status_code

finally:
    # Always restore probe patches, even on early exceptions.
    inventree_ready.isReadOnlyCommand = _orig_is_read_only
    if _save_patched:
        InvenTreeSetting.save = _orig_setting_save
    try:
        if COLD:
            # Restore the original object, including primary key and key casing.
            # QuerySet.delete() above leaves this in-memory object intact.
            # bulk_create avoids the destructive save()/after_save callback.
            InvenTreeSetting.objects.filter(key__iexact=MFA_KEY).delete()
            if _orig_present:
                InvenTreeSetting.objects.bulk_create([_orig_row])
            cache.delete(CACHE_KEY)
            report['row_restored_to'] = 'present' if _orig_present else 'absent'
            report['row_state_restored'] = setting_row_present() == _orig_present
            report['invalidated_sessions_restorable'] = False
    finally:
        # Restore Django's cache configuration AND instantiated backends, even
        # when database restoration fails. Do not mutate private cache internals.
        _cache_override.disable()

# ---- report & assertions -------------------------------------------------
if COLD:
    report['create_on_read_stack_captured'] = bool(stack_box)
    if stack_box:
        # paths / function names / line ids ONLY -- never source text.
        report['create_on_read_stack'] = [
            f'{frame.filename}:{frame.lineno}:{frame.name}'
            for frame in stack_box[0]
            if '/site-packages/' not in frame.filename
        ][-14:]
    report['destructive'] = True
    report['sessions_restorable'] = False

print('REGRESSION_REPORT ' + repr(report))

if not COLD:
    ok = (
        report['row_present_before']  # setup ALREADY initialized the defaults
        and report['row_present_after_init']
        and report['me_status'] == 200
        and report['trigger_status'] == 200  # trigger request also authenticated
        and report['sessions_before_trigger'] > 0
        and report['session_rows_survived']
        and report['me_status_after'] == 200
    )
    print('REGRESSION_GREEN' if ok else 'REGRESSION_RED')
    sys.exit(0 if ok else 1)
else:
    wiped = report['sessions_after_trigger'] < report['sessions_before_trigger']
    print('COLD_WIPE_REPRODUCED' if wiped else 'COLD_WIPE_NOT_OBSERVED')
    sys.exit(0 if wiped else 1)
