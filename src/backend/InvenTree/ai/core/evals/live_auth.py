"""D1 (M1 gate): authenticate the battery runner as a NON-STAFF principal.

The AI boundary (``ai.core.auth``) accepts only a short-lived signed subject
(``sign_interactive_subject``, ~120 s), while the Django endpoints the
runner also calls (asset search, proposals) take a DRF ``Token``. This
``httpx.Auth`` mints the signed subject PER REQUEST — so the 409
turn-serialization retry loop never re-sends an expired one — and attaches
the token everywhere else. Enabled by ``AIMMS_BATTERY_SIGNED_SUBJECT_USER``;
the legacy ``AIMMS_BATTERY_BEARER`` / ``COOKIE`` envs stay for CI fakes.

Django is configured lazily on first use (the runner runs on the worker
where ``manage.py`` lives), never at import — ``run_battery`` must keep
importing without Django for the CI fake-transport tests.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

AI_PREFIX = "/api/ai/"


def _unshadow_django_apps(path: list[str] | None = None) -> list[str]:
    """Move the ``ai/core`` directory behind the project root on ``sys.path``.

    The runner is launched as ``python -m evals.run_campaign`` from ``ai/core``,
    which puts that directory FIRST on ``sys.path``; ``ai/core/voice`` then
    shadows the Django app ``voice`` and ``django.setup()`` dies with
    ``No module named 'voice.apps'``. The ``evals`` package still resolves
    from the demoted entry, so nothing else moves.
    """
    import sys
    from pathlib import Path

    entries = sys.path if path is None else path
    core_dir = Path(__file__).resolve().parents[1]
    kept: list[str] = []
    demoted: list[str] = []
    for entry in entries:
        try:
            same = Path(entry or ".").resolve() == core_dir
        except OSError:  # pragma: no cover - unreadable entry
            same = False
        (demoted if same else kept).append(entry)
    entries[:] = [*kept, *demoted]
    return entries


def _django_user(username: str) -> Any:
    """The Django user row for ``username`` (Django set up on first call)."""
    import django
    from django.conf import settings as django_settings

    if not django_settings.configured:  # pragma: no cover - worker exec path
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "InvenTree.settings")
        _unshadow_django_apps()
        django.setup()
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise RuntimeError(f"AIMMS_BATTERY_SIGNED_SUBJECT_USER {username!r} does not exist")
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        # The gate measures what a technician sees; a staff subject would
        # widen every projection and silently inflate the baseline.
        raise RuntimeError("the battery principal must be non-staff (Q-non-staff route facts)")
    return user


def _default_signer() -> Callable[[Any], str]:
    from ai.core.auth import sign_interactive_subject

    return sign_interactive_subject


class SignedSubjectAuth(httpx.Auth):
    """Per-request signed subject for ``/api/ai/``; DRF token elsewhere."""

    requires_request_body = False

    def __init__(
        self,
        username: str,
        *,
        django_token: str = "",
        signer: Callable[[Any], str] | None = None,
        user_loader: Callable[[str], Any] | None = None,
    ):
        self.username = username
        self.django_token = django_token
        self._signer = signer
        self._user_loader = user_loader or _django_user
        self._user: Any = None
        self.minted = 0

    def _sign(self) -> str:
        if self._user is None:
            self._user = self._user_loader(self.username)
        if self._signer is None:
            self._signer = _default_signer()
        self.minted += 1
        return self._signer(self._user)

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        """Fresh subject on every AI request; the token on everything else."""
        if request.url.path.startswith(AI_PREFIX):
            request.headers["Authorization"] = f"Bearer {self._sign()}"
        elif self.django_token:
            request.headers["Authorization"] = f"Token {self.django_token}"
        yield request


def auth_from_env() -> httpx.Auth | None:
    """The configured auth object, or None when the signed-subject env is unset."""
    username = os.environ.get("AIMMS_BATTERY_SIGNED_SUBJECT_USER", "").strip()
    if not username:
        return None
    return SignedSubjectAuth(
        username, django_token=os.environ.get("AIMMS_BATTERY_DJANGO_TOKEN", "").strip()
    )


__all__ = ["AI_PREFIX", "SignedSubjectAuth", "auth_from_env"]
