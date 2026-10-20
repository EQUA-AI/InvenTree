"""Compare-and-set token for native Work attachment metadata."""

import hashlib
import json


def attachment_revision(attachment):
    """Identify the editable snapshot without exposing storage paths."""
    snapshot = [
        attachment.pk,
        attachment.model_type,
        attachment.model_id,
        str(attachment.attachment),
        attachment.link,
        attachment.comment,
    ]
    return hashlib.sha256(json.dumps(snapshot).encode()).hexdigest()
