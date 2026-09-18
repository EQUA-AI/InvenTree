"""Native reauthorization regression cases, authored for later DB validation."""

import os
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from ai.core.integrations import retrieval_authority as authority
from aichat.models import ControlledDocument
from users.models import RuleSet


class RetrievalAuthorityTests(TestCase):
    """Revocation must override stale actors and Search metadata."""

    def setUp(self):
        """Actual user/role/document rows, with only provider settings replaced."""
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.user = get_user_model().objects.create_user(username='authority-fixture')
        self.group = Group.objects.create(name='authority-fixture')
        self.user.groups.add(self.group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=self.group, name='work_order', defaults={'can_view': True}
        )
        self.enterContext(
            mock.patch(
                'ai.core.config.get_settings',
                return_value=SimpleNamespace(
                    single_site_policy_key='fixture',
                    azure_search_controlled_documents_index='fixture-index',
                    feature_attachment_rag_retrieval=True,
                    azure_search_attachment_docs_index='attachment-index',
                ),
            )
        )
        self.document = ControlledDocument.objects.create(
            document_id='fixture',
            revision='1',
            scope_key='fixture',
            scope_hash='b' * 64,
            source_sha256='a' * 64,
            search_index_name='fixture-index',
            state='indexed',
            is_current=True,
            access_class='maintenance_authorized',
            title='Fixture',
        )
        self.row = {
            'document_id': 'fixture',
            'document_revision': '1',
            'scope_key': 'fixture',
            'source_sha256': 'a' * 64,
            'is_current': True,
            'access_class': 'maintenance_authorized',
            'asset_id': '',
            'chunk': 'PRIVATE',
        }

    def test_role_and_account_revocation_override_cached_user(self):
        """Session role cache cannot preserve authority after its DB rule changes."""
        self.assertEqual(
            authority.controlled_rows([self.row], user=self.user), [self.row]
        )
        with mock.patch('users.permissions.check_user_role', return_value=True):
            self.rule.can_view = False
            self.rule.save(update_fields=['can_view'])
            self.assertEqual(authority.controlled_rows([self.row], user=self.user), [])
        self.rule.can_view = True
        self.rule.save(update_fields=['can_view'])
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(authority.controlled_rows([self.row], user=self.user), [])

    def test_superseded_registry_and_changed_hash_drop_stale_search_text(self):
        """Search success alone is not current revision evidence."""
        self.row['source_sha256'] = 'c' * 64
        self.assertEqual(authority.controlled_rows([self.row], user=self.user), [])
        self.row['source_sha256'] = 'a' * 64
        ControlledDocument.objects.filter(pk=self.document.pk).update(
            is_current=False, state='superseded'
        )
        self.assertEqual(authority.controlled_rows([self.row], user=self.user), [])

    def test_attachment_policy_and_current_scope_both_required(self):
        """A granted arm cannot override a stale source or a revoked client."""
        row = {
            'id': 'fixture-chunk',
            'model_type': 'assetmachine',
            'client_codes': ['fixture'],
        }
        with (
            mock.patch('tasks.scope.client_codes_for_actor', return_value={'fixture'}),
            mock.patch(
                'ai.core.integrations.projection_gate.recall_allowed', return_value=True
            ),
            mock.patch(
                'aichat.services.projection_authority.projection_row_reason',
                return_value='',
            ) as policy,
        ):
            self.assertEqual(
                authority.attachment_rows([row], user=self.user, corpus='attachment'),
                [row],
            )
            policy.return_value = 'missing_source'
            self.assertEqual(
                authority.attachment_rows([row], user=self.user, corpus='attachment'),
                [],
            )
            policy.return_value = ''
            row['client_codes'] = ['foreign']
            self.assertEqual(
                authority.attachment_rows([row], user=self.user, corpus='attachment'),
                [],
            )

    def test_inventory_rejects_revoked_role_before_reading_registry(self):
        """Unregistered metadata never bypasses actor authorization."""
        from ai.core.analysis.source_gateway import inventory

        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        with mock.patch(
            'ai.core.analysis.source_gateway.controlled_document_inventory'
        ) as registry:
            result = inventory(self.user, source_classes=['controlled_document'])
        registry.assert_not_called()
        self.assertEqual(result['sections'], {})

    def test_unregistered_work_order_source_still_requires_native_scope(self):
        """No ingest stamp is needed for a foreign source to be denied."""
        from tasks.scope import ScopeError

        attachment = SimpleNamespace(model_type='workorder', model_id=77)
        with (
            mock.patch('tasks.models.WorkOrder.objects.select_related') as query,
            mock.patch(
                'tasks.scope.require_work_order_scope', side_effect=ScopeError('denied')
            ) as authorize,
        ):
            query.return_value.filter.return_value.first.return_value = SimpleNamespace(
                pk=77
            )
            self.assertFalse(authority.native_attachment_owner(self.user, attachment))
            authorize.assert_called_once()
