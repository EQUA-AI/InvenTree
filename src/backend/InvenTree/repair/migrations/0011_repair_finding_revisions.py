"""P42: preserve observations before correction, local/offline migration only."""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Install the local/offline investigation schema; never an Azure command."""

    dependencies = [
        ('repair', '0010_backfill_gate_blocking_authority'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name='RepairFindingRevision',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('snapshot', models.JSONField()),
                ('replaced_at', models.DateTimeField(auto_now_add=True)),
                (
                    'finding',
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name='revisions',
                        to='repair.repairinvestigationfinding',
                    ),
                ),
                (
                    'replaced_by',
                    models.ForeignKey(
                        null=True,
                        on_delete=models.PROTECT,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        )
    ]
