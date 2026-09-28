"""Narrow synthetic-session side-effect policy.

A synthetic demo session may perform ``database_write`` effects and nothing
else: no email, no industrial control call, no external webhook, no AI
diagnosis/learning call, no plugin dispatch, no external publishing. The
policy is durable (it lives on the session row and in the reviewed mapping),
consumed by every seed wrapper, and re-checked by consumers; it is not a
process-local environment flag that an existing worker could ignore.

The in-process effect window is a :class:`contextvars.ContextVar` re-entrancy
counter, so it is confined to the current logical context. A thread started
outside the window never sees it (threads get their own context), and a job
queued to the shared worker runs in a separate context and never inherits it.
Note the async semantics honestly: an ``asyncio`` task created *inside* the
window copies the current context and therefore carries the window for its own
lifetime; tasks created outside it never observe it.

This module is deliberately import-light so a worker task can consult it
without pulling in the whole adapter. The real dispatch consumers are wired:

* ``InvenTree.tasks.offload_task`` / ``bulk_offload_task`` /
  ``TaskBatch.add`` (covers queued *and* synchronous task dispatch, including
  plugin event dispatch and AI ingestion receivers which funnel through them)
  refuse dispatch via :func:`guard_task_dispatch` while a window is active;
* notification and email effects are suppressed by the *existing* data-import
  suppression in ``common.notifications.trigger_notification`` and
  ``InvenTree.helpers_email.send_email``: ``InvenTree.ready.isImportingData()``
  classifies the seed window as a data import (same path as ``loaddata``).
  Those consumers are deliberately not wrapped, patched or re-guarded here.

Inside a synthetic window every task dispatch is refused: nothing is queued
for the shared worker and nothing runs synchronously inline, so a synthetic
seed cannot leave unowned jobs behind for another consumer to execute.
"""

from __future__ import annotations

import functools
from contextvars import ContextVar

#: The only effect class a synthetic session may cause.
DATABASE_WRITE = 'database_write'

#: Everything a synthetic session must never cause.
DENIED_EFFECTS = frozenset({
    'email',
    'industrial_control',
    'webhook',
    'ai_diagnosis',
    'ai_learning',
    'plugin_dispatch',
    'external_publish',
    'notification',
})

#: Effect class of a refused background/synchronous task dispatch. Task
#: dispatch is never on the ``database_write`` allowlist, so a synthetic
#: session cannot spawn worker jobs of any kind.
TASK_DISPATCH = 'task_dispatch'


class ExternalEffectBlockedError(Exception):
    """A synthetic session attempted an external side effect."""

    code = 'EXTERNAL_EFFECT_BLOCKED'


#: Historical name kept as an alias so existing imports keep working.
ExternalEffectBlocked = ExternalEffectBlockedError


def is_synthetic_context() -> bool:
    """Whether the current call stack runs inside a synthetic session effect.

    Wrappers set this with :class:`SyntheticEffects`. It is a context-local
    re-entrancy counter, not the durable policy: the session row's
    ``effect_policy`` is the durable authority and is re-checked by
    :func:`guard_external_effect`.
    """
    return _SYNTHETIC_DEPTH.get() > 0


_SYNTHETIC_DEPTH: ContextVar[int] = ContextVar(
    'demo_metrics_synthetic_depth', default=0
)


class SyntheticEffects:
    """Context manager marking an in-process synthetic effect window.

    Context-local: other threads and ``asyncio`` tasks created outside the
    window never see it; a task created inside it carries a context copy and
    so keeps the window for its own lifetime. A nested window is
    reference-counted on the same context.
    """

    def __enter__(self):
        """Enter the synthetic effect window."""
        self._token = _SYNTHETIC_DEPTH.set(_SYNTHETIC_DEPTH.get() + 1)
        return self

    def __exit__(self, *exc_info):
        """Leave the synthetic effect window."""
        _SYNTHETIC_DEPTH.reset(self._token)
        return False


#: Historical name kept as an alias so existing imports keep working.
synthetic_effects = SyntheticEffects


def guard_external_effect(effect: str, *, session_policy=None) -> None:
    """Raise unless ``effect`` is allowed for the synthetic session.

    Consumers of notification/outbox/plugin paths call this before dispatching
    any external effect while a synthetic session is active. ``session_policy``
    is the durable allowlist from the session row; when omitted, only the
    ``database_write`` effect class is allowed.
    """
    allowed = set(session_policy or (DATABASE_WRITE,))
    if effect in DENIED_EFFECTS or effect not in allowed:
        raise ExternalEffectBlockedError(
            f'Synthetic demo sessions may not cause {effect!r} effects'
        )


def deny_external_during_synthetic(effect: str) -> None:
    """Guard used by shared dispatch paths (notifications, plugins, outbox).

    Inside a synthetic effect window every external effect is refused; outside
    the window this is a no-op so real user workflows are unchanged.
    """
    if is_synthetic_context():
        guard_external_effect(effect)


def synthetic_dispatch_guard(function):
    """Decorator for dispatch helpers: refuse external effects when synthetic."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        deny_external_during_synthetic(function.__name__)
        return function(*args, **kwargs)

    return wrapper


def task_effect(taskname, group: str = 'inventree') -> str:
    """Classify the effect class of one task dispatch by name/group.

    Known external consumers (email, AI ingestion, plugin event dispatch) get
    their specific denied class for a precise error; everything else is
    refused as generic ``task_dispatch``.
    """
    if group == 'plugin':
        return 'plugin_dispatch'
    if callable(taskname):
        name = (
            f'{getattr(taskname, "__module__", "")}.{getattr(taskname, "__name__", "")}'
        )
    else:
        name = str(taskname)
    module = name.split(':', 1)[0]
    if module.startswith(('aichat', 'ai_')):
        return 'ai_learning'
    if module.startswith('InvenTree.helpers_email') or '.email_user' in name:
        return 'email'
    return TASK_DISPATCH


def guard_task_dispatch(taskname, group: str = 'inventree') -> None:
    """Refuse task dispatch (queued or synchronous) inside a synthetic window.

    Wired into ``InvenTree.tasks.offload_task``, ``bulk_offload_task`` and
    ``TaskBatch.add``. Outside a synthetic window this is a no-op. Inside the
    window *no* task is dispatched at all — the durable session policy allows
    only ``database_write`` — so neither the shared worker nor the inline
    synchronous runner can execute a job spawned by a synthetic seed.
    """
    if not is_synthetic_context():
        return
    guard_external_effect(task_effect(taskname, group))
