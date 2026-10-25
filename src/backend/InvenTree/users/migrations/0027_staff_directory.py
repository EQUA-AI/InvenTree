"""Add personnel trades and default-off grants after the observed boundary."""

from django.db import migrations, models


def seed_trades(apps, schema_editor):
    """Add catalogue labels without approving any person's qualifications."""
    trade = apps.get_model('users', 'Trade')
    for code, name in (
        ('electrical', 'Electrical'),
        ('mechanical', 'Mechanical'),
        ('plumbing', 'Plumbing / Pipefitting'),
        ('instrumentation', 'Instrumentation'),
        ('hvac', 'HVAC'),
        ('welding', 'Welding'),
    ):
        trade.objects.using(schema_editor.connection.alias).get_or_create(
            code=code, defaults={'name': name}
        )


class Migration(migrations.Migration):
    """Extend active personnel state without replaying retained R8 storage."""

    dependencies = [
        ('users', '0024_notification_publish_permission'),
        ('legacy_boundary', '0001_observed_schema_checkpoint'),
    ]

    operations = [
        migrations.CreateModel(
            name='Trade',
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
                ('code', models.SlugField(max_length=64, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name', 'pk']},
        ),
        migrations.AlterModelOptions(
            name='ruleset',
            options={
                'permissions': [
                    ('view_email', 'Can read emails and attachments'),
                    ('send_email', 'Can send emails'),
                    ('view_staff_directory', 'Can view staff names and job titles'),
                ]
            },
        ),
        migrations.AddField(
            model_name='ruleset',
            name='can_override_assignment_trade_mismatch',
            field=models.BooleanField(
                db_default=False,
                default=False,
                verbose_name='Can override assignment trade mismatch',
            ),
        ),
        migrations.AddField(
            model_name='ruleset',
            name='can_view_staff_directory',
            field=models.BooleanField(
                db_default=False, default=False, verbose_name='Can view staff directory'
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='trades',
            field=models.ManyToManyField(
                blank=True,
                related_name='profiles',
                to='users.trade',
                verbose_name='Approved trades',
            ),
        ),
        migrations.RunPython(seed_trades, migrations.RunPython.noop),
    ]
