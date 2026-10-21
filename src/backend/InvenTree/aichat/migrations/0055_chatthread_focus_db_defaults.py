"""Keep focus defaults in PostgreSQL for pre-focus application writers."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Allow old and new application versions to insert chat threads."""

    dependencies = [('aichat', '0054_chatthread_conversation_focus')]

    operations = [
        migrations.AlterField(
            model_name='chatthread',
            name='conversation_focus',
            field=models.JSONField(blank=True, db_default={}, default=dict),
        ),
        migrations.AlterField(
            model_name='chatthread',
            name='focus_version',
            field=models.PositiveBigIntegerField(db_default=0, default=0),
        ),
    ]
