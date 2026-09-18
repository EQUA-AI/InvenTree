"""Persist the optional memory-forgetting choice through retry and restore."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Existing transcript deletions preserve the original retain-confirmed policy."""

    dependencies = [('aichat', '0048_rag_projection_audit')]
    operations = [
        migrations.AddField(
            model_name='chatthreadtombstone',
            name='forget_confirmed_memories',
            field=models.BooleanField(default=False, db_default=False),
        )
    ]
