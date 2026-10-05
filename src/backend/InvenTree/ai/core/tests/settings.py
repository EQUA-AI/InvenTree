"""Test-only Django settings for the isolated AI/core test island.

This module is the complete, coherent test registry for the real InvenTree
migration graph. It registers the production app dependency closure (every
app whose migrations the registered apps reference, plus the model-import
prerequisites such as ``allauth.account`` and ``rest_framework``), so the
real migration files run untouched and in their real history order.

Live startup behavior is suppressed at exactly one seam: the ``plugin`` and
``InvenTree`` app configs below keep the real models and real migrations of
those apps but do not run their ``ready()`` hooks. Those hooks load the
plugin registry, seed accounts and schedule background workers — runtime
effects that must not happen in a test process. Because every dependent
production startup hook gates on ``InvenTree.ready.isPluginRegistryLoaded()``,
skipping the registry load keeps those hooks inert without altering any
model, migration, feature default or permission check.
"""

import hashlib
import tempfile
from dataclasses import dataclass

from django.apps import AppConfig
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseGone

SECRET_KEY = "ai-core-test-only"
USE_TZ = True
ALLOWED_HOSTS = ["testserver"]
DEBUG = False


class TestPluginConfig(AppConfig):
    """Real ``plugin`` models/migrations; startup-only plugin load suppressed.

    ``plugin.apps.PluginAppConfig.ready()`` performs the live plugin
    registry load (maintenance-mode reset, plugin discovery and execution).
    A test process must not load or run plugins; the app's models and
    migrations remain fully real.
    """

    name = "plugin"
    label = "plugin"

    def ready(self):
        """Deliberately empty: no registry load, no plugin execution."""


class TestInvenTreeConfig(AppConfig):
    """Real ``InvenTree`` app; startup seeding/scheduling suppressed.

    ``InvenTree.apps.InvenTreeConfig.ready()`` checks the migration state,
    seeds configured users/accounts and starts background task workers once
    the plugin registry is loaded. None of that may run for tests: no live
    worker, no seeded accounts, no scheduling.
    """

    name = "InvenTree"
    label = "InvenTree"

    def ready(self):
        """Deliberately empty: no account seeding and no worker scheduling."""


INSTALLED_APPS = [
    # Admin site integration
    "django.contrib.admin",
    "django.contrib.admindocs",
    # InvenTree apps
    "build.apps.BuildConfig",
    "common.apps.CommonConfig",
    "ai.core.tests.settings.TestPluginConfig",  # real plugin models/migrations
    "company.apps.CompanyConfig",
    "order.apps.OrderConfig",
    "part.apps.PartConfig",
    "report.apps.ReportConfig",
    "scim.apps.ScimConfig",
    "stock.apps.StockConfig",
    "tasks.apps.TasksConfig",
    "assets.apps.AssetsConfig",
    "approvals.apps.ApprovalsConfig",
    "repair.apps.RepairConfig",
    "aichat.apps.AIChatConfig",
    "voice.apps.VoiceConfig",
    "users.apps.UsersConfig",
    "machine.apps.MachineConfig",
    "data_exporter.apps.DataExporterConfig",
    "importer.apps.ImporterConfig",
    "web",
    "generic",
    "ai.core.tests.settings.TestInvenTreeConfig",  # real InvenTree app
    # Core django modules
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.humanize",
    "whitenoise.runserver_nostatic",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Maintenance
    "maintenance_mode",
    # Third part add-ons
    "django_filters",  # Extended filter functionality
    "rest_framework",  # DRF (Django Rest Framework)
    "corsheaders",  # Cross-origin Resource Sharing for DRF
    "django_cleanup.apps.CleanupConfig",  # Automatically delete orphaned MEDIA files
    "mptt",  # Modified Preorder Tree Traversal
    "markdownify",  # Markdown template rendering
    "djmoney",  # django-money integration
    "djmoney.contrib.exchange",  # django-money exchange rates
    "error_report",  # Error reporting in the admin interface
    "django_q",
    "dbbackup",  # Backups - django-dbbackup
    "taggit",  # Tagging
    "flags",  # Flagging - django-flags
    "django_structlog",  # Structured logging
    "allauth",  # Base app for SSO
    "allauth.account",  # Extend user with accounts
    "allauth.headless",  # APIs for auth
    "allauth.socialaccount",  # Use 'social' providers
    "allauth.mfa",  # MFA for for allauth
    "allauth.usersessions",  # DB sessions
    "django_otp",  # OTP is needed for MFA - base package
    "django_otp.plugins.otp_totp",  # Time based OTP
    "django_otp.plugins.otp_static",  # Backup codes
    "oauth2_provider",  # OAuth2 provider and API access
    "drf_spectacular",  # API documentation
    "django_ical",  # For exporting calendars
    "django_mailbox",  # For email import
    "anymail",  # For email sending/receiving via ESPs
    "storages",
]

# users.models reads this at import time; production default is False and no
# LDAP integration may be configured or reached from tests.
LDAP_AUTH = False

# Declared test environment: database-touching startup hooks stay off and no
# live worker or integration may start.
TESTING_ENV = True

# ---- Production setting contracts (literal production defaults) ----
# These are read at module import time across the registered apps (serializers,
# permissions, ruleset, schema). Values mirror the literal defaults from
# InvenTree/settings.py; live integrations stay explicitly off.
TESTING = True  # production derives this from pytest/test argv detection
TESTING_PRICING = False
TESTING_TABLE_EVENTS = False
TESTING_BYPASS_MAILCHECK = False
SITE_MULTI = False
SITE_URL = None
SITE_LAX_PROTOCOL_CHECK = True
DOCKER = False
AUTO_UPDATE = False
DJANGO_SILK_ENABLED = False
BACKUP_RESTORE_ALLOW_NEWER_VERSION = False
CUSTOMIZE = {}
CUSTOM_LOGO = None
CUSTOM_SPLASH = None
DB_ENGINE = "django.db.backends.sqlite3"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
MEDIA_URL = "/media/"
STATIC_URL = "/static/"
INVENTREE_BASE_URL = "https://inventree.org"
INVENTREE_NEWS_URL = "https://inventree.org/news/feed.atom"
INVENTREE_ADMIN_ENABLED = True
INVENTREE_ADMIN_URL = "admin"
MFA_ENABLED = True
MFA_TRUST_ENABLED = True
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = False
PLUGINS_ENABLED = False  # no plugin loading in tests
PLUGINS_INSTALL_DISABLED = False  # production default preserved
PLUGINS_MANDATORY = []
PLUGIN_DEV_HOST = None
PLUGIN_DEV_SLUG = None
PLUGIN_RETRY = 3
PLUGIN_SETTING_OVERRIDES = {}
PLUGIN_TESTING = True  # production derives this from TESTING
PLUGIN_TESTING_SETUP = False
PLUGIN_TESTING_EVENTS = False
PLUGIN_TESTING_EVENTS_ASYNC = False
PLUGIN_TESTING_RELOAD = False
REMOTE_LOGIN = False
REMOTE_LOGIN_HEADER = "REMOTE_USER"
SCHEMA_VENDOREXTENSION_LEVEL = 0
SPECTACULAR_SETTINGS = {}
FRONTEND_SETTINGS = {}
FRONTEND_URL_BASE = ""
EXTRA_URL_SCHEMES = []
GLOBAL_SETTINGS_OVERRIDES = {}
OAUTH2_CHECK_EXCLUDED = []  # test island runs the full schema check surface
IGNORED_ERRORS = [Http404, HttpResponseGone, PermissionDenied]
TRACING_ENABLED = False
TRACING_DETAILS = None
SENTRY_ENABLED = False
SENTRY_DSN = None
INTEGRATION_APPS_LOADED = False
USER_ADDED = False
USER_ADDED_FILE = None
# Agent mailbox: no account, no send, no sync from tests.
AGENT_EMAIL_ENABLED = False
AGENT_EMAIL_SEND_PAUSED = True
AGENT_EMAIL_SYNC_PAUSED = True
AGENT_EMAIL_CREDENTIAL_KEYS = []
AGENT_EMAIL_MESSAGE_ID_DOMAIN = "example.test"
AGENT_EMAIL_MICROSOFT_CLIENT_ID = None
AGENT_EMAIL_MICROSOFT_CLIENT_SECRET = None
AGENT_EMAIL_OAUTH_REDIRECT_URI = None
AGENT_EMAIL_CLAMAV_SOCKET = None
# django-flags: identical flag definitions to production (DEBUG is False here).
FLAGS = {
    "EXPERIMENTAL": [
        {"condition": "boolean", "value": False},
        {"condition": "parameter", "value": "experimental="},
    ],
    "NEXT_GEN": [{"condition": "parameter", "value": "ngen="}],
    "OIDC": [{"condition": "boolean", "value": True}],
}

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
BASE_DIR = _TEST_DB_DIR
# allauth.account's AppConfig hard-requires its middleware (checked in ready());
# the rest of the production stack is unnecessary for this test island.
MIDDLEWARE = ["allauth.account.middleware.AccountMiddleware"]
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
ROOT_URLCONF = None
SITE_ID = 1

# Test-safe storage: everything disposable, nothing shared or live.
MEDIA_ROOT = f"{_TEST_DB_DIR}/media"
STATIC_ROOT = f"{_TEST_DB_DIR}/static"
FILE_UPLOAD_TEMP_DIR = f"{_TEST_DB_DIR}/uploads"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": MEDIA_ROOT},
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Test-safe email: in-memory outbox only; nothing may reach an SMTP server.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DEFAULT_FROM_EMAIL = "noreply@example.test"
SERVER_EMAIL = "noreply@example.test"
INTERNAL_EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_HOST = ""
EMAIL_HOST_USER = None
EMAIL_HOST_PASSWORD = None

# Test-safe cache: per-process memory cache, no shared Redis or file cache.
GLOBAL_CACHE_ENABLED = False
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ai-core-tests",
    }
}

# Django-Q must stay valid (retry > timeout or tasks retrigger early) but no
# worker is ever started for this test island.
Q_CLUSTER = {
    "name": "ai-core-tests",
    "workers": 1,
    "timeout": 30,
    "retry": 60,
    "max_attempts": 1,
    "orm": "default",
    "sync": False,
    "poll": 60,
}

# Real authentication behavior (production adapters, safety defaults intact);
# no social providers are configured so no SSO integration can be reached.
ACCOUNT_ADAPTER = "InvenTree.auth_overrides.CustomAccountAdapter"
SOCIALACCOUNT_ADAPTER = "InvenTree.auth_overrides.CustomSocialAccountAdapter"
SOCIALACCOUNT_PROVIDERS = {}
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False
ACCOUNT_EMAIL_NOTIFICATIONS = True
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
ACCOUNT_PREVENT_ENUMERATION = True
SOCIALACCOUNT_STORE_TOKENS = True


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
