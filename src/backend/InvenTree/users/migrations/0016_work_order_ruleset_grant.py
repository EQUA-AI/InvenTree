"""Provision the ``work_order`` ruleset without locking anyone out.

Before this ruleset existed, the Kanban/work-order and asset endpoints guarded
only with ``IsAuthenticatedOrReadScope``: *any* authenticated user could create,
edit, move and archive any card, on any customer's machine. Introducing a ruleset
turns that into a real permission check -- but ``RuleSet`` defaults every
permission to ``False``, and ``update_group_roles`` back-fills missing rulesets
using those defaults.

So without this migration the sequence is: deploy, every group silently gains an
all-``False`` ``work_order`` ruleset, and the task page goes dark for every
non-superuser. There is no feature flag to switch off.

This grants the full ruleset to every existing group, which preserves exactly the
capability those groups already had. It deliberately does *not* try to infer a
tighter policy: narrowing access is a decision for whoever administers the groups,
made deliberately against a UI that shows the new ruleset, not a guess encoded in a
migration. New groups created after this migration get the ``False`` defaults and
must be granted explicitly, which is the correct default for anything created from
here on.

Reversing drops only the rows this migration added, leaving group membership and
every other ruleset untouched.
"""

from django.db import migrations

RULESET_NAME = 'work_order'
PERMISSIONS = ('can_view', 'can_add', 'can_change', 'can_delete')


def grant_work_order_ruleset(apps, schema_editor):
    """Give every existing group the permissions it effectively already had."""
    Group = apps.get_model('auth', 'Group')
    RuleSet = apps.get_model('users', 'RuleSet')

    for group in Group.objects.all():
        ruleset, created = RuleSet.objects.get_or_create(
            group=group,
            name=RULESET_NAME,
            defaults=dict.fromkeys(PERMISSIONS, True),
        )

        if created:
            continue

        # The post_save signal on Group may have already created the row with
        # default False values; upgrade it rather than leaving it locked.
        missing = [field for field in PERMISSIONS if not getattr(ruleset, field)]

        if not missing:
            continue

        for field in missing:
            setattr(ruleset, field, True)

        ruleset.save(update_fields=missing)


def revoke_work_order_ruleset(apps, schema_editor):
    """Remove the rulesets this migration provisioned."""
    RuleSet = apps.get_model('users', 'RuleSet')
    RuleSet.objects.filter(name=RULESET_NAME).delete()


class Migration(migrations.Migration):
    """Data-only migration; ``RuleSet.name`` choices are validated in Python."""

    dependencies = [('users', '0015_alter_userprofile_type')]

    operations = [
        migrations.RunPython(grant_work_order_ruleset, revoke_work_order_ruleset)
    ]
