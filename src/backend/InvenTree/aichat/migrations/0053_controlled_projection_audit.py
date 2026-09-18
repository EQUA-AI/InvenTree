"""Extend audit vocabulary to controlled-document native registry checks."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Choices only; no provider call or automatic audit activation."""

    dependencies = [('aichat', '0052_memory_attachment_severance')]
    operations = [
        migrations.AlterField(
            model_name=name,
            name='corpus',
            field=models.CharField(
                max_length=16,
                choices=[
                    ('attachment', 'Attachment documents'),
                    ('media', 'Evidence media'),
                    ('controlled', 'Controlled documents'),
                ],
                **(
                    {'primary_key': True, 'serialize': False}
                    if name == 'ragprojectiongate'
                    else {}
                ),
            ),
        )
        for name in (
            'ragprojectionaudit',
            'ragprojectiongate',
            'ragprojectionrepair',
            'ragprojectionorphan',
        )
    ]
