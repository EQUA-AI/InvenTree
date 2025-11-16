from django.db import migrations, models
import django.contrib.postgres.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='KanbanCard',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(db_index=True, max_length=32)),
                (
                    'priority',
                    models.CharField(
                        choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High')],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ('due_date', models.DateField(blank=True, null=True)),
                ('assignee', models.CharField(blank=True, max_length=120)),
                (
                    'tags',
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=32), blank=True, default=list, size=None
                    ),
                ),
                ('company', models.CharField(blank=True, max_length=120)),
                ('company_contact_name', models.CharField(blank=True, max_length=120)),
                ('company_contact_phone', models.CharField(blank=True, max_length=64)),
                ('job_number', models.CharField(blank=True, max_length=64)),
                ('service_quote', models.CharField(blank=True, max_length=64)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]