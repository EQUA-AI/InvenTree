"""Minimal Django settings for isolated FastAPI boundary unit tests."""

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

SECRET_KEY = "ai-core-test-only"
USE_TZ = True
ALLOWED_HOSTS = ["testserver"]
INSTALLED_APPS = [
    # Admin site integration: the native root URLconf serves
    # ``admin.site.urls``, ``django.contrib.admindocs.urls`` and
    # ``error_report.urls`` whenever ``INVENTREE_ADMIN_ENABLED`` is true
    # (production default), so the admin app plus its required
    # ``messages``/``staticfiles`` contrib apps must be installed - the
    # same set production ``InvenTree.settings`` installs. Without them
    # ``django.contrib.admin.models.LogEntry`` raises RuntimeError at
    # model-class creation (VOICE-H07 registry-closure).
    "django.contrib.admin",
    "django.contrib.admindocs",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Django contrib used directly by the installed apps' models and graph.
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.humanize",
    "django.contrib.sites",
    # Third-party apps whose models/base classes the repository models
    # import at module load (nothing here is optional once the real apps
    # below are installed).
    "mptt",
    "taggit",
    "error_report",
    "django_q",
    "djmoney",
    "djmoney.contrib.exchange",
    # The native root URLconf imports ``oauth2_provider.urls`` and the
    # plugin-reload/URL-update startup path imports ``oauth2_provider.models``;
    # without the app installed the ``Application`` model class raises
    # RuntimeError and the whole ``InvenTree.urls`` import fails
    # (VOICE-H07 registry-closure). Production installs ``oauth2_provider``
    # as well; its own upstream migrations join the real graph, repository
    # migration files are untouched.
    "oauth2_provider",
    "rest_framework",
    # NOTE: ``rest_framework.authtoken`` is deliberately NOT installed,
    # mirroring the production registry (``InvenTree.settings`` installs
    # ``rest_framework`` only). DRF's ``Token.Meta.abstract`` is computed as
    # ``'rest_framework.authtoken' not in settings.INSTALLED_APPS``: with the
    # app installed the concrete ``Token`` class makes ``users.models.ApiToken``
    # overriding ``key``/``user`` a ``FieldError`` field clash (VOICE-H07).
    # Production's token inheritance contract is abstract-base inheritance.
    "django_cleanup.apps.CleanupConfig",
    "allauth",
    "allauth.account",
    # Full allauth family as in production ``InvenTree.settings``: the native
    # root URLconf builds ``allauth.headless.urls.build_urlpatterns`` and
    # ``InvenTree/admin.py`` (admin autodiscover) imports
    # ``allauth.usersessions.models``, which raises ImproperlyConfigured
    # unless the app is installed; headless/urls likewise resolve
    # socialaccount/mfa endpoints (VOICE-H07 registry-closure).
    "allauth.headless",
    "allauth.socialaccount",
    "allauth.mfa",
    "allauth.usersessions",
    # Real repository apps, in the production dependency order. The
    # migration graph is only complete with the transitive set:
    # ``aichat.0042_memory_consent`` depends on ``assets.0015``, ``assets``
    # on ``tasks``/``part``/``company``, ``common``/``company``/``part`` on
    # each other, and ``repair`` on ``approvals``/``assets``/``tasks``.
    # Installing anything less makes ``MigrationLoader`` raise
    # ``NodeNotFoundError`` (VOICE-H07). Real apps only: no stub models,
    # no disabled migration modules, existing migration history untouched.
    "build.apps.BuildConfig",
    "common.apps.CommonConfig",
    "plugin.apps.PluginAppConfig",
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
    "InvenTree.apps.InvenTreeConfig",
]
# Native conditional Site registry semantics, mirroring production
# ``InvenTree/settings.py`` lines 800-807 verbatim: multi-site is disabled for
# this fixture, so ``SITE_ID`` resolves to ``None`` and the ``sites`` app is
# removed from the registry exactly as production does. Installing it anyway
# made the pre-migration root URL import query the missing ``django_site``
# table (VOICE-H07 clean-startup contract).
SITE_MULTI = False
SITE_ID = 1 if SITE_MULTI else None
if not SITE_MULTI:
    INSTALLED_APPS.remove("django.contrib.sites")

# All writable state for this island lives in one scratch directory:
# ``tempfile`` honours the runner's ``TMPDIR`` (per-run disposable /scratch)
# and the repository snapshot itself is read-only. Every derived path below
# stays inside it; nothing writes into the source tree.
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="aicore-tests-"))
(_TEST_DB_DIR / "base").mkdir()

# Explicit owned disposable native-config environment for direct
# production-settings imports: ``users.api`` imports the real production
# module ``InvenTree.settings`` (for ``FRONTEND_URL_BASE``) even while the
# island runs on this fixture, and without these inputs that import falls
# back to production configuration/secret-path defaults. Exact key/value set
# of the inspected native driver fixture (``full_settings_voice_check.py``
# payload, verified by the native-site/env probe), every path re-based onto
# the disposable scratch directory above and the config file written as an
# empty document. Dummy test-only values: no real configuration, secret or
# credential is read and no repository path is written.
_NATIVE_CONFIG_ENV = {
    "HOME": str(_TEST_DB_DIR),
    "INVENTREE_BACKUP_DIR": str(_TEST_DB_DIR / "backup"),
    "INVENTREE_CONFIG_FILE": str(_TEST_DB_DIR / "verification-config.yaml"),
    "INVENTREE_DB_ENGINE": "sqlite3",
    "INVENTREE_DB_HOST": "",
    "INVENTREE_DB_NAME": str(_TEST_DB_DIR / "native-settings.sqlite3"),
    "INVENTREE_DB_PASSWORD": "",
    "INVENTREE_DB_PORT": "",
    "INVENTREE_DB_USER": "",
    "INVENTREE_MEDIA_ROOT": str(_TEST_DB_DIR / "media"),
    "INVENTREE_OIDC_PRIVATE_KEY_FILE": str(_TEST_DB_DIR / "verification-oidc.pem"),
    "INVENTREE_PLUGINS_ENABLED": "False",
    "INVENTREE_PLUGIN_DIR": str(_TEST_DB_DIR / "plugins"),
    "INVENTREE_PLUGIN_FILE": str(_TEST_DB_DIR / "plugins.txt"),
    "INVENTREE_SECRET_KEY": "non-secret-disposable-verification-key",
    "INVENTREE_STATIC_ROOT": str(_TEST_DB_DIR / "static"),
    "TESTING": "1",
    "XDG_CACHE_HOME": str(_TEST_DB_DIR / "cache"),
}
os.environ.update(_NATIVE_CONFIG_ENV)
Path(_NATIVE_CONFIG_ENV["INVENTREE_CONFIG_FILE"]).write_text("{}\n")

# Required existing settings consumed at import time by the installed
# applications' models/apps modules. Values are sanitized test defaults
# mirroring the production defaults (all optional features off); no
# production settings module, environment or business configuration is
# read here.
DEBUG = False
TESTING = True
# Native default is ``False`` (production ``InvenTree.settings``). True would
# make ``InvenTree/apps.py`` bypass native startup suppression
# (``ensure_migrations_done`` / ``add_user_on_startup``) against an empty
# database; startup classification must come from the genuine test/migration
# argv instead (VOICE-H07).
TESTING_ENV = False
TESTING_BYPASS_MAILCHECK = True
TESTING_PRICING = False
DOCKER = False
LDAP_AUTH = False
REMOTE_LOGIN = False
REMOTE_LOGIN_HEADER = "REMOTE_USER"
MFA_ENABLED = False
PLUGINS_ENABLED = False
# Production derives ``PLUGINS_INSTALL_DISABLED = True`` whenever
# ``PLUGINS_ENABLED`` is False: external plugin installation and the
# ``PLUGIN_ON_STARTUP`` installer run stay disabled, built-in collection only.
PLUGINS_INSTALL_DISABLED = True
PLUGINS_MANDATORY = []
PLUGIN_TESTING = False
# Static-startup plugin settings with exact production defaults
# (``InvenTree.settings``); a missing one aborts app-registry startup.
PLUGIN_TESTING_SETUP = False
PLUGIN_TESTING_EVENTS = False
PLUGIN_TESTING_EVENTS_ASYNC = False
PLUGIN_TESTING_RELOAD = False
PLUGIN_RETRY = 3
# Production ``config.get_plugin_file()`` equivalent: a Path inside the
# scratch state directory, deliberately never created - ``plugins_file_hash()``
# then returns None and plugin installation no-ops.
PLUGIN_FILE = _TEST_DB_DIR / "plugins.txt"
INVENTREE_ADMIN_URL = "admin"
# Static settings read at import time by the URLconf/permissions/ruleset
# import chain (``InvenTree.urls`` -> ``tasks.api`` -> ``InvenTree.permissions``
# -> ``users.ruleset``), exact production defaults (``InvenTree.settings``).
INVENTREE_ADMIN_ENABLED = True
DJANGO_SILK_ENABLED = False
SITE_LAX_PROTOCOL_CHECK = True
INTERNAL_EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
GLOBAL_SETTINGS_OVERRIDES = {}
PLUGIN_SETTING_OVERRIDES = {}
INVENTREE_BASE_URL = "https://inventree.org"
INVENTREE_NEWS_URL = f"{INVENTREE_BASE_URL}/news/feed.atom"
# ``plugin.base.event.events``: production default suppresses table events
# outside dedicated table-event test runs.
TESTING_TABLE_EVENTS = False
SENTRY_ENABLED = False
SENTRY_DSN = ""
TRACING_ENABLED = False
GLOBAL_CACHE_ENABLED = False
AUTO_UPDATE = False
CORS_ALLOW_ALL_ORIGINS = True
EXTRA_URL_SCHEMES = []
# Feature flags: exact production literal (``InvenTree.settings`` lines
# 1187-1197, audited verbatim - not invented). ``common.api.FlagList`` reads
# ``settings.FLAGS`` at module import, which the root URLconf performs
# (VOICE-H07 registry-closure). ``OIDC`` is ``True`` so the
# ``flagged_path('OIDC', ...)`` OAuth2 URLs are actively included and
# exercised by the root URL import gate.
FLAGS = {
    "EXPERIMENTAL": [
        {"condition": "boolean", "value": DEBUG},
        {"condition": "parameter", "value": "experimental="},
    ],  # Should experimental features be turned on?
    "NEXT_GEN": [
        {"condition": "parameter", "value": "ngen="}
    ],  # Should next-gen features be turned on?
    "OIDC": [{"condition": "boolean", "value": True}],
}
IGNORED_ERRORS = []
CUSTOMIZE = {}
CUSTOM_LOGO = None
CUSTOM_SPLASH = None
FRONTEND_SETTINGS = {}
FRONTEND_URL_BASE = "https://app.example.test"
SITE_URL = "https://app.example.test"
BASE_DIR = _TEST_DB_DIR / "base"
MEDIA_ROOT = _TEST_DB_DIR / "media"
MEDIA_URL = "/media/"
STATIC_ROOT = _TEST_DB_DIR / "static"
STATIC_URL = "/static/"
# Static-file closure (production ``InvenTree.settings``): explicit empty
# finder dirs; STATIC_URL/STATIC_ROOT above resolve inside scratch state.
STATICFILES_DIRS = []
DEFAULT_FROM_EMAIL = "test@example.test"
EMAIL_HOST = "localhost"
EMAIL_HOST_USER = ""
EMAIL_HOST_PASSWORD = ""
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
Q_CLUSTER = {"retry": 120, "timeout": 60, "workers": 1}
# Maintenance mode: production parity - ``InvenTreeMaintenanceModeBackend``
# stores its state in a global setting and tolerates an inaccessible/empty
# database, so startup never writes files (the maintenance_mode FileBackend
# default attempted an atomic temp write beside the settings module).
MAINTENANCE_MODE_RETRY_AFTER = 10
MAINTENANCE_MODE_STATE_BACKEND = "InvenTree.backends.InvenTreeMaintenanceModeBackend"
MAINTENANCE_MODE_STATE_FILE_PATH = _TEST_DB_DIR / "maintenance_mode_state"
# File-backed so sync_to_async executor threads share one database; an
# in-memory SQLite database exists per connection and would vanish across
# the thread hop the ASGI voice routes perform.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": f"{_TEST_DB_DIR}/test.sqlite3",
    }
}
# allauth.account's AppConfig.ready() refuses to load without its middleware
# registered (production's MIDDLEWARE carries it as well). No other middleware
# is needed for the isolated island.
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
