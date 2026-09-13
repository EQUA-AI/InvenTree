"""Atomic persisted presentation; retries cannot invoke business turn execution."""

from django.db import transaction

from ai.core.voice.presentation import POLICY_VERSION, chunks, short_text
from ai.core.voice.speech import spoken_summary_hash
from voice.models import VoicePresentation, VoiceUtteranceType
from voice.services.realtime import ExactSpeechConflict, persist_utterance


def payload(presentation):
    """A cursor binds the UI to this exact immutable answer."""
    return {
        'id': str(presentation.pk),
        'source_hash': presentation.source_hash,
        'index': presentation.position,
        'total': len(presentation.utterance_ids),
    }


@transaction.atomic
def create(*, session, turn_id, text, layout='', safety_boundary=''):
    """Persist all chunks before dispatching the first; replay is immutable."""
    # Session lock serializes competing first creates on PostgreSQL as well.
    type(session).objects.select_for_update().get(pk=session.pk)
    presentation = VoicePresentation.objects.filter(
        session=session, turn_id=turn_id
    ).first()
    digest = spoken_summary_hash(text)
    if presentation:
        if presentation.source_hash != digest:
            raise ExactSpeechConflict('presentation source changed')
    else:
        utterances = [
            persist_utterance(
                session=session,
                utterance_type=VoiceUtteranceType.PROMPT,
                spoken_summary=chunk,
                turn_id=turn_id,
                policy_version=POLICY_VERSION,
            )
            for chunk in chunks(text, layout=layout, safety_boundary=safety_boundary)
        ]
        presentation = VoicePresentation.objects.create(
            session=session,
            turn_id=turn_id,
            source_hash=digest,
            utterance_ids=[str(row.pk) for row in utterances],
        )
    return presentation, session.utterances.get(
        pk=presentation.utterance_ids[presentation.position]
    )


@transaction.atomic
def command(*, session, presentation_id, source_hash, index, action):
    """A stale cursor refuses; an output request never visits a decision adapter."""
    presentation = VoicePresentation.objects.select_for_update().get(
        pk=presentation_id, session=session
    )
    if presentation.source_hash != source_hash or presentation.position != index:
        raise ExactSpeechConflict('presentation cursor changed')
    if action == 'next':
        presentation.position = min(index + 1, len(presentation.utterance_ids) - 1)
        presentation.save(update_fields=['position'])
    elif action not in ('repeat', 'slower', 'short'):
        raise ValueError('unknown presentation command')
    utterance = session.utterances.get(
        pk=presentation.utterance_ids[presentation.position]
    )
    # Each output attempt has truthful fresh delivery state, including when no
    # provider channel exists. Never reuse an earlier 'requested' as new proof.
    utterance = persist_utterance(
        session=session,
        utterance_type=VoiceUtteranceType.PROMPT,
        spoken_summary=short_text(utterance.spoken_summary)
        if action == 'short'
        else utterance.spoken_summary,
        turn_id=presentation.turn_id,
        policy_version=POLICY_VERSION,
    )
    return presentation, utterance
