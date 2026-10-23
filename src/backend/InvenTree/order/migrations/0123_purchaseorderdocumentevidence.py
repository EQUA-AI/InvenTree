"""Store exact procurement documents without modifying existing order columns."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Add a narrow immutable evidence table for recorded, never sent documents."""

    dependencies = [
        ('tasks', '0041_db_defaults_for_old_code'),
        ('order', '0122_remove_purchaseorder_notes_remove_returnorder_notes_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PurchaseOrderDocumentEvidence',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operation_key', models.CharField(max_length=128, unique=True)),
                ('request_hash', models.CharField(max_length=64)),
                ('context', models.JSONField()),
                ('pdf_bytes', models.BinaryField()),
                ('email_bytes', models.BinaryField()),
                ('pdf_sha256', models.CharField(max_length=64)),
                ('email_sha256', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='recorded_documents', to='order.purchaseorder')),
            ],
        ),
    ]
