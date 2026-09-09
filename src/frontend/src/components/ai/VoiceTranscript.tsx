/**
 * VoiceTranscript (WS5-T4 + WS5-T7): live partial transcript display, with
 * critical-term highlighting and the held-transcript confirmation strip.
 *
 * Partial text is display-only and visually provisional. Completed
 * transcripts are auto-submitted — except when the critical-terms policy
 * holds one: measurements, negations, LOTO/safety terms, identifiers, or a
 * transcript below the ASR confidence floor wait for an explicit on-screen
 * confirmation, because "15 psi" heard as "50 psi" or a dropped "not"
 * changes a repair. Confirm and discard actions live in VoiceSessionControl;
 * this component only shows what was heard, with the critical spans marked.
 */

import { Group, Paper, Text } from '@mantine/core';

import type {
  VoiceFinalTranscript,
  VoiceHoldPrompt,
  VoicePartialTranscript
} from '../../../lib/types/Voice';
import { detectCriticalSpans } from './voiceCriticalTerms';

export interface VoiceTranscriptProps {
  partial: VoicePartialTranscript | null;
  listening: boolean;
  /** Transcript held by the critical-terms policy, awaiting confirmation. */
  pendingConfirm?: VoiceFinalTranscript | null;
  /** A9: the server-spoken review prompt for the held transcript. */
  holdPrompt?: VoiceHoldPrompt | null;
}

function holdPromptLine(prompt: VoiceHoldPrompt | null | undefined): string {
  if (!prompt) {
    return 'Contains critical values — confirm it was heard correctly, or say the correction.';
  }
  if (prompt.playbackState === 'requested') {
    return 'Read back aloud — say confirm, discard, or say the correction.';
  }
  if (prompt.playbackState === 'failed') {
    return 'The spoken prompt could not be played — read it here, then say confirm, discard, or the correction.';
  }
  return 'Waiting for the spoken prompt…';
}

/** Render text with its critical spans emphasised. */
function HighlightedText({ text }: Readonly<{ text: string }>) {
  const spans = detectCriticalSpans(text);
  if (spans.length === 0) {
    return <>{text}</>;
  }
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) {
      parts.push(text.slice(cursor, span.start));
    }
    parts.push(
      <Text
        key={`${span.start}-${span.end}`}
        span
        fw={700}
        td='underline'
        inherit
      >
        {text.slice(span.start, span.end)}
      </Text>
    );
    cursor = span.end;
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return <>{parts}</>;
}

export function VoiceTranscript({
  partial,
  listening,
  pendingConfirm = null,
  holdPrompt = null
}: Readonly<VoiceTranscriptProps>) {
  if (pendingConfirm?.text) {
    return (
      <Paper
        p='xs'
        radius='sm'
        withBorder
        data-testid='voice-pending-transcript'
        aria-live='polite'
      >
        <Group gap='xs' wrap='nowrap' align='flex-start'>
          <Text size='xs' c='orange' fs='italic' style={{ flexShrink: 0 }}>
            {pendingConfirm.revision && pendingConfirm.revision > 1
              ? `confirm (revision ${pendingConfirm.revision}):`
              : 'confirm:'}
          </Text>
          <Text size='sm'>
            <HighlightedText text={pendingConfirm.text} />
          </Text>
        </Group>
        <Text size='xs' c='dimmed' mt={4} data-testid='voice-hold-prompt-state'>
          {holdPromptLine(holdPrompt)}
        </Text>
      </Paper>
    );
  }
  if (!listening || !partial?.text) {
    return null;
  }
  return (
    <Paper
      p='xs'
      radius='sm'
      withBorder
      data-testid='voice-partial-transcript'
      aria-live='polite'
    >
      <Group gap='xs' wrap='nowrap'>
        <Text size='xs' c='dimmed' fs='italic'>
          hearing…
        </Text>
        <Text size='sm' c='dimmed'>
          <HighlightedText text={partial.text} />
        </Text>
      </Group>
    </Paper>
  );
}
