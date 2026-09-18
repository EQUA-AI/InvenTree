"""Private, explicitly scoped account export over existing owner read contracts."""

from django.contrib.auth import get_user_model
from django.utils import timezone

from aichat.email_models import ConnectedMailbox
from aichat.models import RetrievalMiss, UserMemorySettings
from aichat.services import ThreadRepository, memory_reads
from users.models import UserProfile

MAX_RECORDS = 50000
MAX_BYTES = 64 * 1024 * 1024
EXCLUDED = (
    'other_server_scopes',
    'shared_threads',
    'shared_mail_correspondence',
    'voice_audio_and_capture_revisions',
    'native_operational_records',
    'attachment_bodies',
    'provider_state',
    'external_telemetry',
    'backups',
    'credentials',
    'vectors',
    'unavailable_or_expired_memories',
)


def records(user_id, *, scope_key):
    """Yield whitelisted records, with current fact authorization on every page.

    Operator-only. A scoped artifact is not a full-account DSAR or a snapshot.
    No arbitrary model metadata, OAuth options or credentials are serialized.
    """
    if (
        type(user_id) is not int
        or user_id < 1
        or not isinstance(scope_key, str)
        or not scope_key.strip()
    ):
        raise ValueError('Invalid export boundary')
    user = get_user_model().objects.get(pk=user_id, is_active=True)
    birth = user.date_joined
    cutoff = timezone.now()
    yield {
        'type': 'manifest',
        'schema_version': 1,
        'scope': 'account_with_scoped_chat',
        'complete_account_export': False,
        'consistency': 'live_read_with_per_store_cutoffs',
        'started_at': cutoff,
        'excludes': list(EXCLUDED),
    }
    yield {
        'type': 'identity',
        'data': {
            key: getattr(user, key)
            for key in ('username', 'first_name', 'last_name', 'email', 'date_joined')
        },
    }
    profile = (
        UserProfile.objects
        .filter(user_id=user_id)
        .values('language', 'theme', 'displayname', 'position', 'organisation')
        .first()
    )
    yield {'type': 'profile', 'data': profile or {}}
    preference = (
        UserMemorySettings.objects.filter(user_id=user_id).values('opted_out').first()
    )
    yield {'type': 'memory_preferences', 'data': preference or {'opted_out': False}}
    for row in ThreadRepository(user_id, scope_key).export_transcript_records():
        yield {'type': 'chat_record', 'data': row}
    for state in ('active', 'proposed'):
        cursor = ''
        while True:
            page = memory_reads.list_facts(user, state=state, cursor=cursor)
            for fact in page['results']:
                yield {'type': 'memory', 'data': fact}
            cursor = page['next_cursor']
            if not cursor:
                break
    for row in (
        RetrievalMiss.objects
        .filter(user_id=user_id, created_at__lte=cutoff)
        .order_by('pk')
        .values('query', 'hit_count', 'corpus', 'created_at')
        .iterator(chunk_size=200)
    ):
        yield {'type': 'retrieval_query', 'data': row}
    for row in (
        ConnectedMailbox.objects
        .filter(owner_id=user_id, created_at__lte=cutoff)
        .order_by('pk')
        .values(
            'name',
            'provider',
            'address',
            'enabled',
            'send_enabled',
            'receive_enabled',
            'created_at',
        )
        .iterator(chunk_size=200)
    ):
        yield {'type': 'owned_mailbox_metadata', 'data': row}
    if (
        not get_user_model()
        .objects.filter(pk=user_id, is_active=True, date_joined=birth)
        .exists()
    ):
        raise ValueError('Export owner changed')
    yield {
        'type': 'complete',
        'complete_account_export': False,
        'finished_at': timezone.now(),
    }
