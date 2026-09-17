"""Record future local account-erasure intents without inferring past erasures."""

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add a content-free restore obligation independent of the user FK."""

    dependencies = [('aichat', '0040_alter_chatactionproposal_action_type')]

    operations = [
        migrations.CreateModel(
            name='AccountErasureTombstone',
            fields=[
                (
                    'user_id',
                    models.PositiveBigIntegerField(primary_key=True, serialize=False),
                ),
                ('user_joined_at', models.DateTimeField()),
                (
                    'requested_at',
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
            ],
        )
    ]
