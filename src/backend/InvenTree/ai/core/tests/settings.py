"""Minimal Django settings for isolated FastAPI boundary unit tests."""

import hashlib
import tempfile
from dataclasses import dataclass

SECRET_KEY = "ai-core-test-only"
USE_TZ = True
ALLOWED_HOSTS = ["testserver"]
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    # ``aichat`` links a proposal to the approval that executes it, so its
    # migrations reference this app. Leaving it out makes the graph
    # inconsistent and every test that builds a database error out before it
    # runs. ``approvals`` itself depends on nothing but the user model.
    "approvals.apps.ApprovalsConfig",
    "aichat.apps.AIChatConfig",
    "voice.apps.VoiceConfig",
]
# File-backed so sync_to_async executor threads share one database; an
# in-memory SQLite database exists per connection and would vanish across
# the thread hop the ASGI voice routes perform.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="aicore-tests-")
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": f"{_TEST_DB_DIR}/test.sqlite3",
    }
}
MIDDLEWARE = []
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "ai.core.tests.settings.InMemoryAuthenticationBackend",
]
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_NAME = "sessionid"
CSRF_COOKIE_NAME = "csrftoken"
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"
CSRF_TRUSTED_ORIGINS = ["https://app.example.test"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@dataclass
class TestUser:
    """Minimal authenticated subject for isolated async boundary tests."""

    __test__ = False

    pk: str
    username: str
    password: str = "password"
    is_active: bool = True
    is_staff: bool = False
    is_superuser: bool = False
    is_authenticated: bool = True

    def get_username(self) -> str:
        return self.username

    def get_session_auth_hash(self) -> str:
        return hashlib.sha256(self.password.encode()).hexdigest()

    def set_password(self, password: str) -> None:
        self.password = password


TEST_USERS: dict[str, TestUser] = {}


class InMemoryAuthenticationBackend:
    """Public async backend used to isolate SessionStore/aget_user behavior."""

    async def aget_user(self, user_id):
        user = TEST_USERS.get(str(user_id))
        return user if user and user.is_active else None


class TestUserModel:
    """Minimal async user manager for signed-subject boundary tests."""

    __test__ = False

    class DoesNotExist(Exception):
        pass

    class _Manager:
        async def aget(self, *, pk):
            try:
                return TEST_USERS[str(pk)]
            except KeyError as exc:
                raise TestUserModel.DoesNotExist from exc

    objects = _Manager()
