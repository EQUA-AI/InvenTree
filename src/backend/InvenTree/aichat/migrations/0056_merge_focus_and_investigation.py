"""Merge the conversation-focus and deployed investigation migration branches."""

from django.db import migrations


class Migration(migrations.Migration):
    """Retain both existing migration histories without schema operations."""

    dependencies = [
        ('aichat', '0054_investigation_proposal_action'),
        ('aichat', '0055_chatthread_focus_db_defaults'),
    ]

    operations = []
