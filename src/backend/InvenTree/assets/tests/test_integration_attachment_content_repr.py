"""Content records must not display bytes in their generated representation."""

import hashlib
import unittest
from dataclasses import asdict, replace

from tasks.integration_attachments import WorkAttachmentContentRecord
from tasks.integration_sources import WorkSourceIdentity

from assets.integration_contracts import (
    AttachmentSourceIdentity,
    MachineAttachmentContentRecord,
    MachineSourceIdentity,
)


class AttachmentContentRepresentationTests(unittest.TestCase):
    """Repr redaction does not change the actual content contract."""

    def test_machine_content_not_visible_in_representation(self):
        """Repr and str must not print the synthetic file bytes."""
        content = b'C1-synthetic-sensitive-content-4927'
        record = MachineAttachmentContentRecord(
            identity=AttachmentSourceIdentity(environment='test', pk=1),
            machine=MachineSourceIdentity(environment='test', pk=2),
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
        self.assertNotIn(content.decode(), repr(record))
        self.assertNotIn(content.decode(), str(record))
        self.assertNotIn(content.decode(), repr([record]))
        self.assertEqual(record.content, content)
        self.assertEqual(asdict(record)['content'], content)
        self.assertEqual(record, record)

    def test_work_content_not_visible_in_representation(self):
        """The separate Work content record must follow the same rule."""
        content = b'C1-synthetic-sensitive-content-4927'
        record = WorkAttachmentContentRecord(
            identity=AttachmentSourceIdentity(environment='test', pk=1),
            work=WorkSourceIdentity(environment='test', pk=3),
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
        self.assertNotIn(content.decode(), repr(record))
        self.assertNotIn(content.decode(), str(record))
        self.assertNotIn(content.decode(), repr([record]))
        self.assertEqual(record.content, content)
        self.assertEqual(asdict(record)['content'], content)
        self.assertEqual(record, record)

    def test_content_remains_part_of_equality_and_hash(self):
        """Hiding a representation must not change content comparison."""
        attachment = AttachmentSourceIdentity(environment='test', pk=1)
        content = b'synthetic-first'
        digest = hashlib.sha256(content).hexdigest()
        records = (
            MachineAttachmentContentRecord(
                identity=attachment,
                machine=MachineSourceIdentity(environment='test', pk=2),
                content=content,
                content_sha256=digest,
            ),
            WorkAttachmentContentRecord(
                identity=attachment,
                work=WorkSourceIdentity(environment='test', pk=3),
                content=content,
                content_sha256=digest,
            ),
        )
        for record in records:
            with self.subTest(record_type=type(record).__name__):
                same = replace(record)
                different = replace(record, content=b'synthetic-second')
                self.assertEqual(record, same)
                self.assertEqual(hash(record), hash(same))
                self.assertNotEqual(record, different)
                self.assertIn(digest, repr(record))
