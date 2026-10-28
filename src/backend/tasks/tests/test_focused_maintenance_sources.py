"""Native ACL, actual E05 file ownership, and optimistic source consistency."""

import hashlib
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from common.models import Attachment
from tasks.models import WorkOrder
from tasks.services.focused_maintenance_sources import read_focused_maintenance_sources
from tasks.tests.test_repair_scope_drafts import DraftFixture


@override_settings(
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_TEST_ALARMS_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class FocusedMaintenanceSourcesTest(DraftFixture, TestCase):
    """Every fixture stays inside the disposable native test database."""

    def read(self, **changes):
        """Read only the current fixture's exact native Work and machine."""
        args = {
            'actor_id': self.actor.pk,
            'work_order_id': self.work.pk,
            'machine_id': self.machine.pk,
            'include_history': True,
            'include_e05': True,
        }
        args.update(changes)
        return read_focused_maintenance_sources(**args)

    def test_e05_is_actual_owned_bytes_and_never_promotes_prepared_values(self):
        """Read E05 bytes while keeping every current check unrecorded."""
        event = self.work.events.get(event_type='EXERCISE_EVIDENCE_ATTACHED')
        expected = event.metadata['result_metadata']['evidence']['P08-TEMP-E05']
        before = (
            self.work.lifecycle_version,
            self.work.events.count(),
            self.packet.status,
        )
        result = self.read()
        source = result['sources'][
            'tasks.services.focused_maintenance_sources._prepared_e05'
        ]
        self.assertEqual(source['status'], 'ok')
        self.assertEqual(source['id'], expected['id'])
        self.assertEqual(source['sha256'], expected['sha256'])
        self.assertEqual(
            hashlib.sha256(source['content'].encode()).hexdigest(), expected['sha256']
        )
        self.assertTrue(source['prepared'])
        self.assertNotIn('P08-TEMP-E06', source['content'])
        checks = result['sources']['tasks.ai_read.work_order_repair_state'][
            'exercise_verification'
        ]['checks']
        self.assertTrue(all(check['recorded'] is False for check in checks))
        self.work.refresh_from_db()
        self.assertEqual(
            before,
            (self.work.lifecycle_version, self.work.events.count(), self.packet.status),
        )

    def test_foreign_machine_and_inactive_actor_are_denied(self):
        """Reject a mismatched root and freshly revoked actor."""
        with self.assertRaises(PermissionDenied):
            self.read(machine_id=self.station.pk)
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.read()

    def test_tampered_or_reowned_e05_is_unavailable_without_alternate_content(self):
        """Withhold reowned E05 instead of using a template or another file."""
        event = self.work.events.get(event_type='EXERCISE_EVIDENCE_ATTACHED')
        evidence = event.metadata['result_metadata']['evidence']['P08-TEMP-E05']
        Attachment.objects.filter(pk=evidence['id']).update(
            model_id=self.work.pk + 1000
        )
        source = self.read()['sources'][
            'tasks.services.focused_maintenance_sources._prepared_e05'
        ]
        self.assertEqual(source, {'status': 'unavailable', 'label': 'P08-TEMP-E05'})

    def test_lifecycle_change_during_final_file_read_discards_whole_bundle(self):
        """Discard all sources when Work changes after E05 retrieval."""
        from tasks.services.focused_maintenance_sources import _prepared_e05

        def changing(work, actor):
            result = _prepared_e05(work, actor)
            WorkOrder.objects.filter(pk=work.pk).update(
                lifecycle_version=work.lifecycle_version + 1
            )
            return result

        with (
            patch(
                'tasks.services.focused_maintenance_sources._prepared_e05',
                side_effect=changing,
            ),
            self.assertRaises(PermissionDenied),
        ):
            self.read()

    def test_permission_revocation_after_file_read_discards_whole_bundle(self):
        """Reauthorize the actor after the final prepared-source file read."""
        from tasks.services.focused_maintenance_sources import _prepared_e05

        def revoked(work, actor):
            result = _prepared_e05(work, actor)
            type(actor).objects.filter(pk=actor.pk).update(is_active=False)
            return result

        with (
            patch(
                'tasks.services.focused_maintenance_sources._prepared_e05',
                side_effect=revoked,
            ),
            self.assertRaises(PermissionDenied),
        ):
            self.read()
