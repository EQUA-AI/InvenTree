"""Migration for KanbanCardPart model."""

import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0001_initial'),
        ('part', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='KanbanCardPart',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'card',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='card_parts',
                        to='tasks.kanbancard',
                    ),
                ),
                (
                    'part',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='kanban_allocations',
                        to='part.part',
                    ),
                ),
                (
                    'quantity',
                    models.DecimalField(
                        decimal_places=5,
                        default=Decimal('1'),
                        help_text='Required quantity of this part for the card',
                        max_digits=15,
                    ),
                ),
                (
                    'allocated_quantity',
                    models.DecimalField(
                        decimal_places=5,
                        default=Decimal('0'),
                        help_text='Quantity successfully reserved/allocated from stock',
                        max_digits=15,
                    ),
                ),
                (
                    'allocation_status',
                    models.CharField(
                        choices=[
                            ('none', 'None'),
                            ('partial', 'Partial'),
                            ('full', 'Full'),
                            ('insufficient', 'Insufficient'),
                        ],
                        db_index=True,
                        default='none',
                        max_length=16,
                    ),
                ),
                (
                    'allocation_note',
                    models.TextField(
                        blank=True,
                        help_text='Notes about stock availability or allocation issues',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['created_at'],
                'unique_together': {('card', 'part')},
            },
        ),
    ]
