"""Give ``can_record_operating_authority`` a database default of false.

``0025`` added the column NOT NULL and Django dropped the default it used to
fill existing rows. Code that predates the column creates rulesets without it,
so the database has to supply the value. No row is changed.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Expand only: SET DEFAULT false on one column."""

    dependencies = [('users', '0025_operating_authority_role')]

    operations = [
        migrations.AlterField(
            model_name='ruleset',
            name='can_record_operating_authority',
            field=models.BooleanField(
                db_default=False,
                default=False,
                verbose_name='Can record operating authority and service state',
            ),
        )
    ]
