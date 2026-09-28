"""Functions to check if certain parts of InvenTree are ready."""

import functools
import inspect
import os
import sys
import warnings

from django.conf import settings

import structlog

logger = structlog.get_logger('inventree')


# Keep track of loaded apps, to prevent multiple executions of ready functions
_loaded_apps = set()


def clearLoadedApps():
    """Clear the set of loaded apps."""
    global _loaded_apps
    _loaded_apps = set()


def setAppLoaded(app_name: str):
    """Mark an app as loaded."""
    global _loaded_apps
    _loaded_apps.add(app_name)


def isAppLoaded(app_name: str) -> bool:
    """Return True if the app has been marked as loaded."""
    global _loaded_apps
    return app_name in _loaded_apps


def isInTestMode():
    """Returns True if the database is in testing mode."""
    return any(x in sys.argv for x in ['test', 'pytest']) or sys.argv[0].endswith(
        'pytest'
    )


def isWaitingForDatabase():
    """Return True if we are currently waiting for the database to be ready."""
    return 'wait_for_db' in sys.argv


def isSyntheticDemoImport() -> bool:
    """Return True inside a context-local synthetic demo seed window.

    The EQUA synthetic demo seed is a data import: its effect window
    (``assets.demo_metrics.effects.synthetic_effects``) is classified here so
    the existing ``loaddata``/``flush`` import suppression paths (notification
    and email dispatch) apply unchanged. The predicate is strictly
    context-local (a ``contextvars`` window) — it never inspects or spoofs
    ``sys.argv``.
    """
    try:
        from assets.demo_metrics.effects import is_synthetic_context
    except ImportError:  # pragma: no cover
        return False

    return is_synthetic_context()


def isImportingData():
    """Returns True if the database is currently importing (or exporting) data, e.g. 'loaddata' command is performed.

    Also True for the context-local synthetic demo seed window (see
    :func:`isSyntheticDemoImport`), which is a data import like any other.
    """
    return (
        any(x in sys.argv for x in ['flush', 'loaddata', 'bulkloaddata', 'dumpdata'])
        or isSyntheticDemoImport()
    )


def isRunningMigrations():
    """Return True if the database is currently running migrations."""
    return any(
        x in sys.argv
        for x in ['migrate', 'makemigrations', 'showmigrations', 'runmigrations']
    )


def isRebuildingData():
    """Return true if any of the rebuilding commands are being executed."""
    return any(
        x in sys.argv
        for x in [
            'rebuild',
            'rebuild_models',
            'rebuild_thumbnails',
            'remove_stale_contenttypes',
        ]
    )


def isRunningBackup():
    """Return true if any of the backup commands are being executed."""
    return any(
        x in sys.argv
        for x in [
            'backup',
            'restore',
            'dbbackup',
            'dbrestore',
            'mediabackup',
            'mediarestore',
        ]
    )


def isCollectingPlugins():
    """Return True if the 'collectplugins' command is being executed."""
    return 'collectplugins' in sys.argv


#: Demo metrics commands (EQUA synthetic demo). Split by bootstrap behavior:
#: the read-only ones must never trigger startup writes, and the mutating ones
#: perform only their own explicit approved writes — Django startup runs before
#: ``BaseCommand.handle()``, so classification here is a prerequisite, not a
#: dry-run detail.
DEMO_METRICS_COMMANDS = (
    'plan_demo_metrics',
    'apply_demo_metrics',
    'verify_demo_metrics',
    'replay_demo_metrics',
    'stop_demo_metrics',
    'cleanup_demo_metrics',
)

DEMO_METRICS_READ_ONLY = (
    'plan_demo_metrics',
    'verify_demo_metrics',
    'cleanup_demo_metrics',
)


def isDemoMetricsCommand() -> bool:
    """Return True if a demo metrics management command is being executed."""
    return any(command in sys.argv for command in DEMO_METRICS_COMMANDS)


def isReadOnlyDemoMetricsCommand() -> bool:
    """Return True for demo metrics commands that must perform no writes.

    ``cleanup_demo_metrics`` is read-only by default and mutating only with an
    explicit ``--apply`` flag; the flag is part of the same argv, so the
    classification is exact rather than a fake dry-run with rollback.
    """
    for command in DEMO_METRICS_READ_ONLY:
        if command in sys.argv:
            if command == 'cleanup_demo_metrics' and '--apply' in sys.argv:
                continue
            return True
    return False


# This variable is used to cache the result of the isGeneratingSchema function, to prevent multiple executions of the same checks
_IS_GENERATING_SCHEMA: bool | None = None


def _setGeneratingSchema(value: bool):
    """Set the value of the isGeneratingSchema variable."""
    global _IS_GENERATING_SCHEMA
    _IS_GENERATING_SCHEMA = value
    return value


def isGeneratingSchema():
    """Return true if schema generation is being executed."""
    global _IS_GENERATING_SCHEMA

    if _IS_GENERATING_SCHEMA is not None:
        return _IS_GENERATING_SCHEMA

    if isInServerThread() or isInWorkerThread():
        return _setGeneratingSchema(False)

    if isRunningMigrations() or isRunningBackup() or isRebuildingData():
        return _setGeneratingSchema(False)

    if isImportingData():
        return _setGeneratingSchema(False)

    if isInTestMode():
        return _setGeneratingSchema(False)

    if isWaitingForDatabase():
        return _setGeneratingSchema(False)

    if isCollectingPlugins():
        return _setGeneratingSchema(False)

    # Additional set of commands which should not trigger schema generation
    excluded_commands = [
        'compilemessages',
        'createsuperuser',
        'clean_settings',
        'collectstatic',
        'makemessages',
        'wait_for_db',
        'list_apps',
        'gunicorn',
        'sqlflush',
        'qcluster',
        'check',
        'shell',
        'help',
    ]

    if any(cmd in sys.argv for cmd in excluded_commands):
        return _setGeneratingSchema(False)

    included_commands = [
        'schema',
        'spectactular',
        # schema adjacent calls
        'export_settings_definitions',
        'export_tags',
        'export_filters',
        'export_report_context',
    ]

    if any(cmd in sys.argv for cmd in included_commands):
        return _setGeneratingSchema(True)

    # This is a very inefficient call - so we only use it as a last resort
    result = any('drf_spectacular' in frame.filename for frame in inspect.stack())

    if not result:
        # We should only get here if we *are* generating schema
        # Raise a warning, so that developers can add extra checks above

        if settings.DEBUG:
            logger.warning(
                'isGeneratingSchema called outside of expected contexts - this may be a sign of a problem with the ready() function'
            )
            logger.warning('sys.argv: %s', sys.argv)

    return _setGeneratingSchema(result)


def isInWorkerThread():
    """Returns True if the current thread is a background worker thread."""
    return 'qcluster' in sys.argv


def isInServerThread():
    """Returns True if the current thread is a server thread."""
    if isInWorkerThread():
        return False

    if 'runserver' in sys.argv:
        return True

    return 'gunicorn' in sys.argv[0]


def isInMainThread():
    """Django runserver starts two processes, one for the actual dev server and the other to reload the application.

    - The RUN_MAIN env is set in that case. However if --noreload is applied, this variable
    is not set because there are no different threads.
    """
    if 'runserver' in sys.argv and '--noreload' not in sys.argv:
        return os.environ.get('RUN_MAIN', None) == 'true'

    return not isInWorkerThread()


def readOnlyCommands():
    """Return a list of read-only management commands which should not trigger database writes."""
    return [
        'audit_role_permissions',
        'help',
        'check',
        'shell',
        'sqlflush',
        'list_apps',
        'wait_for_db',
        'spectactular',
        'makemessages',
        'collectstatic',
        'showmigrations',
        'compilemessages',
    ]


def isReadOnlyCommand():
    """Return True if the current command is a read-only command, which should not trigger any database writes."""
    if (
        isImportingData()
        or isRunningMigrations()
        or isRebuildingData()
        or isRunningBackup()
    ):
        return True

    # Demo metrics plan/verify/cleanup-plan are read-only for the whole
    # process: startup must skip every write path for them.
    if isReadOnlyDemoMetricsCommand():
        return True

    return any(cmd in sys.argv for cmd in readOnlyCommands())


def canAppAccessDatabase(
    allow_test: bool = False, allow_plugins: bool = False, allow_shell: bool = False
):
    """Returns True if the apps.py file can access database records.

    Arguments:
        allow_test: If True, override checks and allow database access during testing mode
        allow_plugins: If True, override checks and allow database access during plugin loading
        allow_shell: If True, override checks and allow database access during shell sessions

    There are some circumstances where we don't want the ready function in apps.py
    to touch the database
    """
    # Prevent database access if we are running backups
    if isRunningBackup():
        return False

    # Prevent database access if we are importing data
    if not allow_plugins and isImportingData():
        return False

    # Prevent database access if we are rebuilding data
    if isRebuildingData():
        return False

    # Prevent database access if we are running migrations
    if not allow_plugins and isRunningMigrations():
        return False

    # If any of the following management commands are being executed,
    # prevent custom "on load" code from running!
    excluded_commands = [
        'audit_role_permissions',
        'compilemessages',
        'createsuperuser',
        'collectstatic',
        'makemessages',
        'spectactular',
        'wait_for_db',
        'check',
    ]

    # Demo metrics commands skip ALL unrelated startup work (migration,
    # schedule, user initialization, task enqueue). The mutating commands
    # perform only their own explicit approved writes inside handle(); startup
    # side effects are never theirs to trigger.
    excluded_commands.extend(DEMO_METRICS_COMMANDS)

    if not allow_shell:
        excluded_commands.append('shell')

    if not allow_test:
        # Override for testing mode?
        excluded_commands.append('test')

    if not allow_plugins:
        excluded_commands.extend(['collectplugins', 'list_apps'])

    return all(cmd not in sys.argv for cmd in excluded_commands)


def isPluginRegistryLoaded():
    """Ensures that the plugin registry is already loaded.

    The plugin registry reloads all apps onetime after starting if there are AppMixin plugins,
    so that the discovered AppConfigs are added to Django. This triggers the ready function of
    AppConfig to execute twice. Add this check to prevent from running two times.

    Note: All apps using this check need to be registered after the plugins app in settings.py

    Returns: 'False' if the registry has not fully loaded the plugins yet.
    """
    from plugin import registry

    return registry.plugins_loaded


def ignore_ready_warning(func):
    """Decorator to ignore 'AppRegistryNotReady' warnings in functions called during app ready phase.

    Ref: https://github.com/inventree/InvenTree/issues/10806
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                'ignore',
                message='Accessing the database during app initialization is discouraged',
                category=RuntimeWarning,
            )
            return func(*args, **kwargs)

    return wrapper
