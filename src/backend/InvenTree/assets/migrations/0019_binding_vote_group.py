"""Record which detectors corroborate one another.

Two standards say a single detector is not an alarm: IEEE Std 3004.8-2016
cl. 8.5.2.2 recommends RTD voting so damaged and open-circuit inputs are
ignored, and API Std 670 cl. 5.4.6.4 makes dual voting standard where two
sensors share a bearing's load zone - while keeping single-violation logic for
every other configuration, which is why a null ``vote_minimum`` means no voting
rather than a default.

The grouping needs storing because nothing already stored can express it. Every
detector carries its own ParameterTemplate ("Motor Winding RTD 7"), so templates
give one group per detector; ``signal_kind`` is blank on every binding in this
estate; and a component pools winding with core detectors on 16 of 29 machines,
which would vote across two different measurements. The values are written from
the reviewed limits file by ``apply_signal_limits``, and cleared both when a
channel is disarmed and when activation changes a point's meaning.

No data migration: an unset group is single-violation logic, which is exactly
the behaviour every binding had before this field existed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the voting group and minimum to signal bindings."""

    dependencies = [
        ('assets', '0018_railed_state_quality'),
    ]

    operations = [
        migrations.AddField(
            model_name='machinesignalbinding',
            name='vote_group',
            field=models.CharField(blank=True, db_index=True, help_text='Detectors that corroborate one another, from the limits file', max_length=128, verbose_name='Vote Group'),
        ),
        migrations.AddField(
            model_name='machinesignalbinding',
            name='vote_minimum',
            field=models.PositiveSmallIntegerField(blank=True, help_text='Detectors required to confirm a critical condition', null=True, verbose_name='Vote Minimum'),
        ),
    ]
