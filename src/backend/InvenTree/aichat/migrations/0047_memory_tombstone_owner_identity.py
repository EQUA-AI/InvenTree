"""Bind deletion proof to an account incarnation; never invent historical proof."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Legacy null identities require review before restore replay."""

    dependencies = [('aichat', '0046_memory_fact_jobs')]
    operations = [
        migrations.AddField(
            model_name='memoryfacttombstone',
            name='owner_joined_at',
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.RemoveConstraint(
            model_name='memoryfacttombstone', name='memory_tombstone_claim'
        ),
        migrations.AddConstraint(
            model_name='memoryfacttombstone',
            constraint=models.UniqueConstraint(
                fields=[
                    'owner_id',
                    'client_code',
                    'slot_fingerprint',
                    'claim_fingerprint',
                    'fact_id',
                ],
                name='memory_tombstone_fact_claim',
            ),
        ),
    ]
