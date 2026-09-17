"""Operator-only account disabling, local identity scrubbing and content cleanup.

The user primary key survives for protected business records. External identity
providers, client applications and historical business text have separate owners.
"""

from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from aichat.services import retention
from aichat.services.account_erasure_log import record_erasure
from aichat.services.voice_retention import selected_ids
from users.models import UserProfile

PROFILE_VALUES = {
    'language': None,
    'theme': None,
    'widgets': None,
    'displayname': None,
    'position': None,
    'status': None,
    'location': None,
    'active': False,
    'contact': None,
    'type': 'guest',
    'organisation': None,
    'primary_group_id': None,
    'metadata': {},
}
# Each family is explicitly owner-bound. Optional apps are skipped only when
# absent; a configured/swapped model needs a reviewed adapter, not a guess.
CREDENTIAL_MODELS = (
    ('users', 'ApiToken'),
    ('authtoken', 'Token'),
    ('account', 'EmailAddress'),
    ('socialaccount', 'SocialAccount'),  # cascades its SocialToken credentials
    ('mfa', 'Authenticator'),
    ('otp_totp', 'TOTPDevice'),
    ('otp_static', 'StaticDevice'),  # cascades recovery codes
    ('oauth2_provider', 'RefreshToken'),
    ('oauth2_provider', 'AccessToken'),
    ('oauth2_provider', 'IDToken'),
    ('oauth2_provider', 'Grant'),
    ('oauth2_provider', 'DeviceGrant'),
    ('common', 'InvenTreeUserSetting'),
)


class AccountErasureError(ValueError):
    """Safe configuration/target error with no account or credential contents."""


def _optional_model(label, name):
    if label not in apps.app_configs:
        return None
    model = apps.get_model(label, name)
    if model._meta.swapped:
        raise AccountErasureError(
            'A configured credential model needs an erasure adapter'
        )
    return model


def _credential_models():
    return [
        model
        for label, name in CREDENTIAL_MODELS
        if (model := _optional_model(label, name)) is not None
    ]


def _identity_residuals(user, username, models):
    profile = UserProfile.objects.filter(user_id=user.pk).first()
    sessions = _optional_model('usersessions', 'UserSession')
    applications = _optional_model('oauth2_provider', 'Application')
    return {
        'active_account': int(user.is_active),
        'privileged_account': int(user.is_staff or user.is_superuser),
        'usable_password': int(user.has_usable_password()),
        'identity_fields': sum(
            bool(value)
            for value in (
                user.username != username,
                user.email,
                user.first_name,
                user.last_name,
                user.last_login is not None,
            )
        ),
        'profile_fields': sum(
            getattr(profile, key) != value for key, value in PROFILE_VALUES.items()
        )
        if profile
        else 0,
        'groups': user.groups.count(),
        'direct_permissions': user.user_permissions.count(),
        'tracked_sessions': sessions.objects.filter(user_id=user.pk).count()
        if sessions
        else 0,
        'owned_oauth_applications': applications.objects.filter(user_id=user.pk).count()
        if applications
        else 0,
        **{
            model._meta.label_lower: model.objects.filter(user_id=user.pk).count()
            for model in models
        },
    }


def erase_account(
    user_id,
    *,
    dry_run=False,
    batch_size=500,
    expected_joined_at=None,
    requested_at=None,
):
    """Disable first, scrub local credentials/profile, then compose purge_user.

    Deactivation commits before cleanup. Later failures never restore login.
    Success is scoped to implemented local stores, not full account erasure.
    In-flight non-chat operations and external writers must be drained by the
    operator; inactive-user auth checks do not cancel existing work.
    """
    if type(user_id) is not int or user_id < 1:
        raise AccountErasureError('A positive user id is required')
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise AccountErasureError('Batch size must be between 1 and 1000')
    users = get_user_model().objects
    user = users.filter(pk=user_id).first()
    if user is None:
        raise AccountErasureError('Unknown erasure owner')
    expected_joined_at = expected_joined_at or user.date_joined
    if user.date_joined != expected_joined_at:
        raise AccountErasureError('Account erasure identity mismatch')
    requested_at = requested_at or timezone.now()
    if (
        timezone.is_naive(expected_joined_at)
        or timezone.is_naive(requested_at)
        or not expected_joined_at <= requested_at <= timezone.now()
    ):
        raise AccountErasureError('Invalid account erasure timestamps')
    models = _credential_models()
    username = (
        'erased_' + salted_hmac('aichat.erased-username.v1', str(user_id)).hexdigest()
    )
    report = {
        'schema_version': 1,
        'scope': 'local_account_and_current_chat_voice',
        'subject_hash': salted_hmac('aichat.user-erasure.v1', str(user_id)).hexdigest(),
        'started_at': timezone.now().isoformat(),
        'account_erasure_complete': False,
        'backup_window': {'status': 'unverified', 'days': None},
        'not_covered': [
            'external_identity_provider_deprovisioning',
            'upstream_token_revocation',
            'untracked_session_storage',
            'owned_oauth_application_transfer',
            'in_flight_non_chat_writers',
            'legacy_conversations',
            'retrieval_and_usage_detail',
            'mailbox_and_other_ai_domains',
            'business_record_text',
            'unregistered_plugin_credentials',
            'external_telemetry',
            'backups',
        ],
        'before': _identity_residuals(user, username, models),
    }
    if dry_run:
        return {
            **report,
            'status': 'dry_run',
            'content': retention.purge_user(
                user_id, dry_run=True, batch_size=batch_size
            ),
            'finished_at': timezone.now().isoformat(),
        }

    # Short independent commit: subsequent store failures cannot restore access.
    with transaction.atomic(durable=True):
        user = users.select_for_update().get(pk=user_id)
        if user.date_joined != expected_joined_at:
            raise AccountErasureError('Account erasure identity mismatch')
        stone = record_erasure(
            user_id=user_id,
            user_joined_at=expected_joined_at,
            requested_at=requested_at,
        )
        user.set_unusable_password()
        users.filter(pk=user_id).update(
            is_active=False, is_staff=False, is_superuser=False, password=user.password
        )
    report['erasure_intent_recorded'] = True
    report['erasure_requested_at'] = stone.requested_at.isoformat()
    failures = []
    try:
        with transaction.atomic():
            user = users.select_for_update().get(pk=user_id)
            if users.filter(username=username).exclude(pk=user_id).exists():
                raise AccountErasureError('Anonymized identity collision')
            # Direct updates avoid identity-provider/profile save hooks repopulating
            # personal data. The existing profile and all business FK ids survive.
            users.filter(pk=user_id).update(
                username=username,
                email='',
                first_name='',
                last_name='',
                last_login=None,
            )
            UserProfile.objects.filter(user_id=user_id).update(**PROFILE_VALUES)
            user.groups.clear()
            user.user_permissions.clear()
    except Exception:
        failures.append('identity_profile')

    sessions = _optional_model('usersessions', 'UserSession')
    if sessions:
        for pk in selected_ids(
            sessions.objects.filter(user_id=user_id), batch_size=batch_size
        ):
            try:
                session = sessions.objects.filter(pk=pk, user_id=user_id).first()
                if session is not None:
                    # Use the configured backend (DB/cache), then remove its ledger.
                    store = import_module(settings.SESSION_ENGINE).SessionStore()
                    store.delete(session.session_key)
                    session.delete()
            except Exception:
                if 'tracked_sessions' not in failures:
                    failures.append('tracked_sessions')
    for model in models:
        try:
            retention._batched_delete(
                model.objects.filter(user_id=user_id),
                family=model._meta.label_lower,
                batch_size=batch_size,
                dry_run=False,
            )
        except Exception:
            failures.append(model._meta.label_lower)
    try:
        content = retention.purge_user(user_id, batch_size=batch_size)
    except Exception:
        failures.append('content_store')
        content = {'status': 'purge_incomplete'}
    user = users.get(pk=user_id)
    residuals = _identity_residuals(user, username, models)
    local_complete = not failures and not any(residuals.values())
    return {
        **report,
        'status': 'purged'
        if local_complete and content['status'] == 'purged'
        else 'purge_incomplete',
        'local_login_disabled': not user.is_active and not user.has_usable_password(),
        'local_identity_cleanup_complete': local_complete,
        'failed_families': failures,
        'residuals': residuals,
        'content': content,
        'finished_at': timezone.now().isoformat(),
    }
