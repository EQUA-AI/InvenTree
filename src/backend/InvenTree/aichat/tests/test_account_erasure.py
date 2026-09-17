"""Deferred account identity, credential and chat-write exclusion regressions."""

import io
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from aichat.models import AccountErasureTombstone, ChatThread, ChatThreadGrant
from aichat.services import ThreadNotFound, ThreadRepository, retention
from aichat.services.account_erasure import (
    PROFILE_VALUES,
    AccountErasureError,
    erase_account,
)
from aichat.services.account_erasure_log import ErasureIdentityConflictError
from users.models import ApiToken, UserProfile


@override_settings(
    FEATURE_THREAD_SHARING=True, SESSION_ENGINE='django.contrib.sessions.backends.db'
)
class AccountErasureTests(TestCase):
    """Local identity is scrubbed without deleting protected user references."""

    def setUp(self):
        """Create independent accounts and a disposable upload directory."""
        users = get_user_model().objects
        self.owner = users.create_user(
            username='account-owner',
            email='fixture@example.invalid',
            first_name='Fixture',
            last_name='Owner',
            is_staff=True,
        )
        self.owner.set_password(uuid.uuid4().hex)
        self.owner.save(update_fields=['password'])
        self.other = users.create_user(username='account-other')
        self.repo = ThreadRepository(self.owner, 'site:main')
        self.thread, _ = self.repo.get_or_create()
        self.repo.append(self.thread.pk, role='user', content='Private transcript')
        self.files = tempfile.TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        patcher = mock.patch.object(
            retention, '_upload_root', return_value=Path(self.files.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def model(self, label, name):
        """Qualify optional credential families only when their app is installed."""
        if label not in apps.app_configs:
            self.skipTest('Optional credential app is not installed')
        return apps.get_model(label, name)

    def test_anonymizes_profile_revokes_permissions_and_preserves_protected_user(self):
        """Grant audit survives with the same user FK and a scrubbed identity."""
        foreign_repo = ThreadRepository(self.other.pk, 'site:main')
        foreign, _ = foreign_repo.get_or_create()
        foreign_repo.share(foreign.pk, grantee_id=self.owner.pk)
        group = Group.objects.create(name='fixture-group')
        self.owner.groups.add(group)
        permission = Permission.objects.first()
        self.assertIsNotNone(permission)
        self.owner.user_permissions.add(permission)
        UserProfile.objects.filter(user=self.owner).update(
            displayname='Private display name',
            contact='Private contact',
            metadata={'private': 'value'},
            primary_group=group,
        )
        old_hash = self.owner.get_session_auth_hash()
        result = erase_account(self.owner.pk, batch_size=1)
        self.assertEqual(result['status'], 'purged')
        self.assertFalse(result['account_erasure_complete'])
        self.assertTrue(result['local_identity_cleanup_complete'])
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)
        self.assertFalse(self.owner.is_staff)
        self.assertFalse(self.owner.has_usable_password())
        self.assertNotEqual(self.owner.get_session_auth_hash(), old_hash)
        self.assertTrue(self.owner.username.startswith('erased_'))
        self.assertEqual(
            (self.owner.email, self.owner.first_name, self.owner.last_name),
            ('', '', ''),
        )
        profile = UserProfile.objects.get(user=self.owner)
        for key, value in PROFILE_VALUES.items():
            self.assertEqual(getattr(profile, key), value)
        grant = ChatThreadGrant.objects.get(thread=foreign, grantee=self.owner)
        self.assertIsNotNone(grant.revoked_at)
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.owner.delete()
        self.assertTrue(ChatThread.objects.filter(pk=foreign.pk).exists())
        for private in (
            'account-owner',
            'fixture@example.invalid',
            'Private',
            self.thread.pk,
        ):
            self.assertNotIn(private, json.dumps(result))
        username = self.owner.username
        self.assertEqual(erase_account(self.owner.pk)['status'], 'purged')
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.username, username)

    def test_local_credentials_are_removed_only_for_target_owner(self):
        """Email bindings, social tokens, MFA and API tokens share target scoping."""
        email = self.model('account', 'EmailAddress')
        social = self.model('socialaccount', 'SocialAccount')
        social_token = self.model('socialaccount', 'SocialToken')
        mfa = self.model('mfa', 'Authenticator')
        email.objects.create(user=self.owner, email=self.owner.email, verified=True)
        account = social.objects.create(
            user=self.owner, provider='fixture', uid='private-id'
        )
        token = social_token.objects.create(account=account, token=uuid.uuid4().hex)
        mfa.objects.create(
            user=self.owner, type='totp', data={'secret': uuid.uuid4().hex}
        )
        own_token = ApiToken.objects.create(user=self.owner, name='Private token name')
        foreign_token = ApiToken.objects.create(user=self.other)
        result = erase_account(self.owner.pk)
        self.assertEqual(result['status'], 'purged')
        self.assertFalse(ApiToken.objects.filter(pk=own_token.pk).exists())
        self.assertFalse(social_token.objects.filter(pk=token.pk).exists())
        self.assertFalse(mfa.objects.filter(user=self.owner).exists())
        self.assertFalse(email.objects.filter(user=self.owner).exists())
        self.assertTrue(ApiToken.objects.filter(pk=foreign_token.pk).exists())

    def test_tracked_session_is_deleted_from_its_configured_store(self):
        """Tracked sessions lose both the backend entry and allauth ledger."""
        ledger = self.model('usersessions', 'UserSession')
        store = SessionStore()
        store['_auth_user_id'] = str(self.owner.pk)
        store['_auth_user_hash'] = self.owner.get_session_auth_hash()
        store.save()
        session_key = store.session_key
        ledger.objects.create(
            user=self.owner,
            session_key=session_key,
            ip='127.0.0.1',
            user_agent='fixture',
        )
        self.assertEqual(erase_account(self.owner.pk)['status'], 'purged')
        self.assertFalse(ledger.objects.filter(user=self.owner).exists())
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

    def test_oauth_user_tokens_are_removed_but_owned_shared_client_is_a_blocker(self):
        """Application ownership must be transferred without deleting other users' access."""
        application = self.model('oauth2_provider', 'Application')
        access = self.model('oauth2_provider', 'AccessToken')
        client = application.objects.create(
            user=self.owner,
            name='Shared client',
            client_type='confidential',
            authorization_grant_type='client-credentials',
        )
        own = access.objects.create(
            user=self.owner,
            application=client,
            token=uuid.uuid4().hex,
            expires=timezone.now() + timedelta(hours=1),
        )
        foreign = access.objects.create(
            user=self.other,
            application=client,
            token=uuid.uuid4().hex,
            expires=timezone.now() + timedelta(hours=1),
        )
        result = erase_account(self.owner.pk)
        self.assertEqual(result['status'], 'purge_incomplete')
        self.assertEqual(result['residuals']['owned_oauth_applications'], 1)
        self.assertFalse(access.objects.filter(pk=own.pk).exists())
        self.assertTrue(access.objects.filter(pk=foreign.pk).exists())
        self.assertTrue(application.objects.filter(pk=client.pk).exists())
        application.objects.filter(pk=client.pk).update(user=self.other)
        self.assertEqual(erase_account(self.owner.pk)['status'], 'purged')

    def test_credential_failure_cannot_undo_disabling_and_retry_finishes(self):
        """An API-token store failure remains visible while login stays disabled."""
        token = ApiToken.objects.create(user=self.owner)
        original = retention._batched_delete

        def fail_one(queryset, **kwargs):
            if kwargs['family'] == 'users.apitoken':
                raise RuntimeError('PRIVATE_STORE_ERROR')
            return original(queryset, **kwargs)

        with mock.patch.object(retention, '_batched_delete', side_effect=fail_one):
            result = erase_account(self.owner.pk)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)
        self.assertEqual(result['status'], 'purge_incomplete')
        self.assertIn('users.apitoken', result['failed_families'])
        self.assertNotIn('PRIVATE_STORE_ERROR', json.dumps(result))
        self.assertTrue(ApiToken.objects.filter(pk=token.pk).exists())
        self.assertEqual(erase_account(self.owner.pk)['status'], 'purged')

    def test_cached_actor_cannot_create_append_rename_or_share_after_disabling(self):
        """User-object authentication cached by an in-flight request is insufficient."""
        with mock.patch.object(
            retention, 'purge_user', side_effect=RuntimeError('fixture')
        ):
            result = erase_account(self.owner.pk)
        self.assertTrue(self.owner.is_active)  # Deliberately stale Python object.
        self.assertTrue(result['local_login_disabled'])
        for action in (
            self.repo.get_or_create,
            lambda: self.repo.append(
                self.thread.pk, role='user', content='Must not persist'
            ),
            lambda: self.repo.rename(self.thread.pk, 'Must not persist'),
            lambda: self.repo.share(self.thread.pk, grantee_id=self.other.pk),
        ):
            with self.assertRaises(ThreadNotFound):
                action()
        self.assertEqual(erase_account(self.owner.pk)['status'], 'purged')

    def test_durable_disabling_refuses_an_outer_application_transaction(self):
        """A later caller rollback must not be able to restore account login."""
        with transaction.atomic():
            with self.assertRaises(RuntimeError):
                erase_account(self.owner.pk)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_intent_survives_cleanup_failure_and_retry_preserves_first_clock(self):
        """Partial cleanup still supplies an independent restore obligation."""
        with mock.patch.object(retention, 'purge_user', side_effect=RuntimeError):
            result = erase_account(self.owner.pk)
        stone = AccountErasureTombstone.objects.get(user_id=self.owner.pk)
        requested = stone.requested_at
        self.assertTrue(result['erasure_intent_recorded'])
        self.assertEqual(stone.user_joined_at, self.owner.date_joined)
        self.assertEqual(result['status'], 'purge_incomplete')
        self.assertEqual(erase_account(self.owner.pk)['status'], 'purged')
        stone.refresh_from_db()
        self.assertEqual(stone.requested_at, requested)

    def test_intent_and_disabling_roll_back_together(self):
        """Neither half of the durable transaction can commit on its own."""
        with mock.patch.object(
            get_user_model(), 'set_unusable_password', side_effect=RuntimeError
        ):
            with self.assertRaises(RuntimeError):
                erase_account(self.owner.pk)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertFalse(AccountErasureTombstone.objects.exists())
        with mock.patch(
            'aichat.services.account_erasure.record_erasure', side_effect=RuntimeError
        ):
            with self.assertRaises(RuntimeError):
                erase_account(self.owner.pk)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertTrue(self.owner.has_usable_password())

    def test_marker_identity_conflict_refuses_account_mutation(self):
        """A reused primary key cannot inherit another account's erase intent."""
        AccountErasureTombstone.objects.create(
            user_id=self.owner.pk,
            user_joined_at=self.owner.date_joined - timedelta(days=1),
        )
        with self.assertRaises(ErasureIdentityConflictError):
            erase_account(self.owner.pk)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertEqual(self.owner.username, 'account-owner')

    def test_preview_and_cli_require_explicit_execution(self):
        """Preview reads counts without changing identity or credentials."""
        token = ApiToken.objects.create(user=self.owner)
        output = io.StringIO()
        call_command('ai_erase_account', user_id=self.owner.pk, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['status'], 'dry_run')
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertTrue(ApiToken.objects.filter(pk=token.pk).exists())
        self.assertFalse(AccountErasureTombstone.objects.exists())
        output = io.StringIO()
        with mock.patch.object(
            retention, 'purge_user', return_value={'status': 'purge_incomplete'}
        ):
            with self.assertRaises(CommandError):
                call_command(
                    'ai_erase_account',
                    user_id=self.owner.pk,
                    execute=True,
                    stdout=output,
                )
        self.assertTrue(json.loads(output.getvalue())['local_login_disabled'])

    def test_invalid_target_never_changes_an_account(self):
        """Reject ambiguous operator identifiers before touching identity."""
        for user_id in (True, -1, None, '1'):
            with self.assertRaises(AccountErasureError):
                erase_account(user_id)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
