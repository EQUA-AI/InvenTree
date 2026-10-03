"""Tell a database this code can migrate from one it must never touch.

Two long-lived lines of this fork have each added migrations since they split,
and several share a number while being different migrations - ``assets`` 0011
through 0016 exist on both, with nothing in common. An image from one line
pointed at a database the other has migrated would run code that knows nothing
of what that database holds, and ``migrate`` would then apply this line's
history beside the other's without complaint: Django records applied migrations
by name and raises no objection to names it has never heard of.

So the question is asked before ``migrate``, and it is asked from ``manage.py
shell``. A management command cannot ask it: with migrations pending the boot
gate in ``InvenTree.apps`` (``INVE-W8``) exits before ``handle()`` is reached,
and ``shell`` is one of the few entry points that gate lets through.

    python src/backend/InvenTree/manage.py shell -c \
        "from assets.deploy_lineage import report; report()"

``contrib/container/init.sh`` runs exactly that, and then ``migrate``, when
``AIMMS_MIGRATE_ON_START`` is set - which is how a rollout that carries
migrations applies them, since the same gate leaves no running container to
apply them from.

The rule: refuse when this code has migrations to apply *and* the database
already holds migrations this code has no file for. Either half alone is
ordinary. Migrations to apply and nothing unknown is this line's own rollout.
Unknown migrations and nothing to apply is a database ahead of its image, which
is what a rollback looks like, and a rolled-back image has to be able to start.
Both together means this code is about to add to a history it has not seen all
of.

Exits non-zero on a refusal, having changed nothing. One refusal would be
false: if upstream ever deletes the ``replaces`` list from a squashed migration,
the names it replaced become unknown here. Nothing on disk does that today.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass

from django.db import connection as default_connection
from django.db.migrations.executor import MigrationExecutor


@dataclass(frozen=True)
class Lineage:
    """What this code and one database know of each other's migrations."""

    #: Migrations this code would apply, in order.
    pending: tuple
    #: Applied, in an app this code ships migrations for, with no file here.
    unknown: tuple
    #: Applied, in an app this code does not have at all - an uninstalled
    #: plugin or dependency. Says nothing about lineage.
    leftover: tuple

    @property
    def diverged(self) -> bool:
        """Whether migrating would add to a history this code has not seen."""
        return bool(self.pending and self.unknown)


def lineage(connection=None) -> Lineage:
    """Compare the migrations on disk with the ones this database records.

    A plain "applied but not on disk" test is wrong, and was wrong by 17 rows on
    a database that is entirely this line's own. When Django applies a squashed
    migration it also records every migration the squash replaced, so their
    names stay in the table after the files are deleted. They are accounted
    for - by the ``replaces`` list of a migration that is on disk - and are
    excluded here.
    """
    executor = MigrationExecutor(connection or default_connection)
    loader = executor.loader

    on_disk = set(loader.disk_migrations)
    replaced = {
        tuple(key)
        for migration in loader.disk_migrations.values()
        for key in (migration.replaces or [])
    }
    apps_shipped = {app_label for app_label, _name in on_disk}
    stray = sorted(set(loader.applied_migrations) - on_disk - replaced)

    # The same plan the boot gate and ``migrate`` compute.
    plan = executor.migration_plan(loader.graph.leaf_nodes())

    return Lineage(
        pending=tuple(
            (migration.app_label, migration.name) for migration, _backwards in plan
        ),
        unknown=tuple(key for key in stray if key[0] in apps_shipped),
        leftover=tuple(key for key in stray if key[0] not in apps_shipped),
    )


def _by_app(keys) -> str:
    """Summarise migrations as ``assets 6, part 1``."""
    counts = Counter(app_label for app_label, _name in keys)
    return ', '.join(
        f'{app_label} {count}' for app_label, count in sorted(counts.items())
    )


def _named(keys):
    """Yield one line per app, naming its first few migrations."""
    for app_label in sorted({app_label for app_label, _name in keys}):
        names = [name for label, name in keys if label == app_label]
        shown = ', '.join(names[:3]) + (' ...' if len(names) > 3 else '')
        yield f'{app_label} {len(names)} ({shown})'


def report(connection=None, *, out=None) -> bool:
    """Print the verdict and exit non-zero when migrating would fork a history.

    Returns ``True`` when it is safe to migrate. Exits rather than returning
    ``False`` so that a script running under ``set -e``, or an operator chaining
    this with ``&&``, cannot proceed past a refusal by accident.
    """
    out = out or sys.stdout
    found = lineage(connection)

    if found.leftover:
        out.write(
            f'note: {len(found.leftover)} applied migration(s) belong to apps '
            f'this code does not ship ({_by_app(found.leftover)}). Leftovers; '
            'not a lineage problem.\n'
        )

    if found.diverged:
        out.write(
            f'STOP: this code has {len(found.pending)} migration(s) to apply, '
            f'and this database already holds {len(found.unknown)} it has '
            'never seen. Another line of development migrated this database.\n'
        )
        out.write(f'  to apply: {_by_app(found.pending)}\n')
        out.write('  never seen:\n')
        for line in _named(found.unknown):
            out.write(f'    {line}\n')
        out.write(
            'Do not run migrate. Roll the image back; nothing has been changed.\n'
        )
        sys.exit(1)

    if found.unknown:
        out.write(
            f'note: this database holds {len(found.unknown)} migration(s) this '
            f'code has never seen ({_by_app(found.unknown)}). It is ahead of '
            'this image, as after a rollback; with nothing to apply, nothing '
            'forks.\n'
        )

    if found.pending:
        out.write(
            f'lineage ok: {len(found.pending)} migration(s) to apply '
            f'({_by_app(found.pending)}), and every applied migration is one '
            'this code knows.\n'
        )
    else:
        out.write('lineage ok: nothing to apply.\n')
    return True
