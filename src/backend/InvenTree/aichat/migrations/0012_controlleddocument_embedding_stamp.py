# S17 A4: stamp which embedding model (and dimensionality) produced each
# indexed revision's vectors. Additive with defaults; reverse is a clean drop.
# Pre-existing rows keep blank/0, which readers treat as "indexed before the
# stamp existed", never as a match.

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the embedding model/dimensions stamp to ControlledDocument."""

    dependencies = [
        ('aichat', '0011_messagefeedback'),
    ]

    operations = [
        migrations.AddField(
            model_name='controlleddocument',
            name='embedding_model',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='controlleddocument',
            name='embedding_dimensions',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
