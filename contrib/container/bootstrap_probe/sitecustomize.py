"""Interpreter-startup hook for the whole-process bootstrap safety probe.

Python imports ``sitecustomize`` from ``sys.path`` at interpreter startup, so
the external-transport guard is active before Django, InvenTree or any plugin
code runs. Only processes launched with
``PYTHONPATH=.../contrib/container/bootstrap_probe`` and
``DM_BOOTSTRAP_NETLOG`` set are instrumented; every other interpreter is
untouched. A guard-installation failure must never break interpreter startup —
the orchestrator instead verifies the ``guard_installed`` marker in each run's
netlog and fails the probe if it is missing.
"""

try:
    import net_guard

    net_guard.install()
except Exception:  # pragma: no cover - never break interpreter startup
    pass
