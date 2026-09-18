"""Revival/audit identity and native memory permission; no enrollment or data writes."""

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    """All additions are nullable or have constant defaults for dark rollout."""

    dependencies = [('aichat', '0043_memory_facts')]
    operations = [
        migrations.AddField(
            model_name='memoryfact',
            name='revives',
            field=models.UUIDField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='memoryfactevent',
            name='claim_fingerprint',
            field=models.CharField(
                max_length=64, blank=True, default='', db_default=''
            ),
        ),
        migrations.AlterModelOptions(
            name='memoryfact',
            options={
                'permissions': [
                    ('write_memory', 'Can propose and confirm own memories')
                ]
            },
        ),
        migrations.AlterField(
            model_name='memoryfacttombstone',
            name='reason',
            field=models.CharField(
                max_length=16,
                choices=[
                    ('forget', 'Forget'),
                    ('supersede', 'Supersede'),
                    ('opt_out', 'Opt out'),
                    ('erasure', 'Erasure'),
                    ('client_purge', 'Client purge'),
                ],
            ),
        ),
        migrations.RemoveConstraint(
            model_name='memoryfacttombstone', name='memory_tombstone_reason'
        ),
        migrations.AddConstraint(
            model_name='memoryfacttombstone',
            constraint=models.CheckConstraint(
                condition=Q(
                    reason__in=[
                        'forget',
                        'supersede',
                        'opt_out',
                        'erasure',
                        'client_purge',
                    ]
                ),
                name='memory_tombstone_reason',
            ),
        ),
    ]
