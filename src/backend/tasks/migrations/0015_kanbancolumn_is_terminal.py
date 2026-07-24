"""Add the is_terminal flag to KanbanColumn (S6c)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the terminal-column flag."""

    """Add the terminal-column flag."""

    dependencies = [('tasks', '0014_kanbancarddependency')]

    operations = [
        migrations.AddField(
            model_name='kanbancolumn',
            name='is_terminal',
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text='The "done" column; a card enters it only via closeout',
                verbose_name='Terminal',
            ),
        )
    ]
