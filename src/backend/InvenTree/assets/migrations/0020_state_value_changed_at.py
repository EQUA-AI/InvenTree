"""Record when a signal last reported a different value.

Freshness cannot see this. Timestamps advance on every poll whether or not the
reading moves, so a channel reporting punctually and saying the same thing for
ever looks current - which is exactly what an acquisition frozen at its last
good sample looks like. Millbrook Pump 05 holds one value on 35 of its 37
channels across the whole recorded window, including its run status and its
active power, and nothing on the blade says so.

A measurement, not a verdict. "Constant" is correct for a great many channels -
a stopped bay reports MOTOR_ON_STATUS 0 for ever and is right to - so the field
answers how long a reading has been the same number and leaves what that means
to whoever is looking.

No backfill, deliberately. The honest value for a row cached before this field
existed is "unknown": the cache holds one reading, so nothing in the database
says when it last differed. Writing observed_at would assert it changed on the
most recent poll, which is false for precisely the frozen channels this exists
to expose. Null fills in as data flows, and where data does not flow -
``backfill_value_changed`` walks the recorded window and sets it from the
source.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the last-changed instant to cached signal state."""


    dependencies = [
        ('assets', '0019_binding_vote_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='machinesignalstate',
            name='value_changed_at',
            field=models.DateTimeField(blank=True, db_index=True, help_text='When this signal last reported a different value', null=True, verbose_name='Value Changed At'),
        ),
    ]
