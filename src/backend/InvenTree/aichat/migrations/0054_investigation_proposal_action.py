"""Register the O14 evidence proposal on the existing approval rail."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Choices only; no publication or shared data is written by migration."""

    dependencies = [('aichat', '0053_controlled_projection_audit')]
    operations = [
        migrations.AlterField(
            model_name='chatactionproposal',
            name='action_type',
            field=models.CharField(
                max_length=32,
                choices=[
                    ('memory.remember', 'Remember a memory'),
                    ('memory.update', 'Correct a memory'),
                    ('memory.forget', 'Forget a memory'),
                    ('memory.forget_all', 'Forget all memories'),
                    ('work_order.hold', 'Hold work order'),
                    ('work_order.resume', 'Resume work order'),
                    ('work_order.schedule', 'Schedule work order'),
                    ('work_order.resize', 'Resize work order'),
                    ('work_order.update', 'Update work order plan'),
                    ('work_order.assign', 'Assign work order'),
                    ('work_order.delete', 'Delete work order'),
                    ('work_order.cancel', 'Cancel work order'),
                    ('work_order.transition', 'Transition work order lifecycle'),
                    ('work_order.create', 'Create work order'),
                    ('work_order.create_child', 'Create child work order'),
                    ('repair_work_package.create', 'Create repair work package'),
                    ('work_order.generate_procurement', 'Generate procurement child'),
                    ('dependency.create', 'Create dependency'),
                    ('dependency.delete', 'Delete dependency'),
                    ('schedule.optimize', 'Optimize schedule (bulk)'),
                    ('stock.add', 'Add stock'),
                    ('stock.remove', 'Remove stock'),
                    ('stock.transfer', 'Transfer stock'),
                    ('stock.count', 'Count stock'),
                    ('investigation.artifact', 'Publish investigation evidence'),
                    ('procedure.complete', 'Complete procedure step'),
                    ('closeout.consent', 'Consent to closeout dictation'),
                    ('closeout.accept', 'Accept closeout note'),
                    ('closeout.handoff', 'Hand off accepted closeout note'),
                ],
            ),
        )
    ]
