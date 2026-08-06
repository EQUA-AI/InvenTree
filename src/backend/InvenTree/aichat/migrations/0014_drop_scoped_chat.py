# S14(c): drop the scoped-chat tables. DESTRUCTIVE and gated twice — the
# operator runs a zero-row check in BOTH environments before this deploys
# (gate G3), and the migration itself re-asserts zero rows and ABORTS if any
# exist, so the procedural gate is also structural. With zero rows the drop
# loses nothing; against live rows it refuses instead of destroying.
#
# Forward: assert-zero, drop ChatCitation/ChatToolInvocation/
# ScopedConversationGrant/ScopedConversation (children first), retire the
# SCOPED namespace choice and tighten the thread-namespace constraint.
# Backward: the schema operations reverse mechanically (Django re-creates the
# tables from migration state), but the rail's code is deleted, so a reverse
# is a schema artifact only — documented as not-planned.
#
# The B6 replacement for ScopedConversationGrant (thread sharing on
# ChatThread) lands in S32; the gap is accepted per the execution plan.

from django.db import migrations, models
from django.db.models import Q


def _assert_no_scoped_rows(apps, schema_editor):
    """Abort the migration unless the scoped-chat rail is verifiably empty."""
    counts = {
        'ScopedConversation': apps.get_model('aichat', 'ScopedConversation'),
        'ScopedConversationGrant': apps.get_model('aichat', 'ScopedConversationGrant'),
        'ChatCitation': apps.get_model('aichat', 'ChatCitation'),
        'ChatToolInvocation': apps.get_model('aichat', 'ChatToolInvocation'),
    }
    populated = {
        name: model.objects.count()
        for name, model in counts.items()
        if model.objects.exists()
    }
    thread_model = apps.get_model('aichat', 'ChatThread')
    scoped_threads = thread_model.objects.filter(namespace='scoped').count()
    if scoped_threads:
        populated['ChatThread[namespace=scoped]'] = scoped_threads
    if populated:
        raise RuntimeError(
            'Refusing to drop scoped-chat tables: live rows exist '
            f'({populated}). Resolve them explicitly before migrating.'
        )


class Migration(migrations.Migration):
    """Drop the scoped-chat rail's tables behind a structural zero-row gate."""

    dependencies = [
        ('aichat', '0013_retrievalmiss'),
    ]

    operations = [
        migrations.RunPython(_assert_no_scoped_rows, migrations.RunPython.noop),
        migrations.DeleteModel(name='ChatCitation'),
        migrations.DeleteModel(name='ChatToolInvocation'),
        migrations.DeleteModel(name='ScopedConversationGrant'),
        migrations.DeleteModel(name='ScopedConversation'),
        migrations.AlterField(
            model_name='chatthread',
            name='namespace',
            field=models.CharField(
                choices=[('unscoped', 'Unscoped')],
                default='unscoped',
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name='chatthread',
            name='aichat_thread_namespace_id',
        ),
        migrations.AddConstraint(
            model_name='chatthread',
            constraint=models.CheckConstraint(
                condition=(
                    Q(namespace='unscoped') & ~Q(id__startswith='scoped_')
                ),
                name='aichat_thread_namespace_id',
            ),
        ),
    ]
