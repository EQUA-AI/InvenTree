"""Initial migration for the assets application."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('company', '0001_initial'),
        ('part', '0001_initial'),
        ('tasks', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AssetMachine',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Name of the machine / asset', max_length=255, unique=True, verbose_name='Name')),
                ('description', models.TextField(blank=True, help_text='Description of the machine', verbose_name='Description')),
                ('active', models.BooleanField(db_index=True, default=True, help_text='Is this machine active?', verbose_name='Active')),
                ('location', models.CharField(blank=True, help_text='Free-text location (e.g. "Bay 4", "Sydney")', max_length=255, verbose_name='Location')),
                ('customer', models.ForeignKey(blank=True, help_text='Customer company using this machine (for external installs)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='asset_machines', to='company.company', verbose_name='Customer')),
                ('manufacturer', models.CharField(blank=True, max_length=255, verbose_name='Manufacturer')),
                ('model', models.CharField(blank=True, max_length=255, verbose_name='Model')),
                ('serial', models.CharField(blank=True, max_length=255, verbose_name='Serial Number')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Asset Machine',
                'verbose_name_plural': 'Asset Machines',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='MachinePart',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('machine', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='machine_parts', to='assets.assetmachine', verbose_name='Machine')),
                ('part', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='machine_installations', to='part.part', verbose_name='Part')),
                ('quantity', models.PositiveIntegerField(default=1, verbose_name='Quantity')),
                ('notes', models.TextField(blank=True, verbose_name='Notes')),
            ],
            options={
                'verbose_name': 'Machine Part',
                'verbose_name_plural': 'Machine Parts',
                'ordering': ['part__name'],
                'unique_together': {('machine', 'part')},
            },
        ),
        migrations.CreateModel(
            name='AssetMaintenanceRecord',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('machine', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='maintenance_records', to='assets.assetmachine', verbose_name='Machine')),
                ('date', models.DateField(help_text='Date the maintenance was performed', verbose_name='Date')),
                ('summary', models.CharField(max_length=255, verbose_name='Summary')),
                ('details', models.TextField(blank=True, verbose_name='Details')),
                ('performed_by', models.CharField(blank=True, max_length=255, verbose_name='Performed By')),
                ('work_order', models.ForeignKey(blank=True, help_text='Linked Kanban card / work order (optional)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='maintenance_records', to='tasks.kanbancard', verbose_name='Work Order')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Maintenance Record',
                'verbose_name_plural': 'Maintenance Records',
                'ordering': ['-date'],
            },
        ),
    ]
