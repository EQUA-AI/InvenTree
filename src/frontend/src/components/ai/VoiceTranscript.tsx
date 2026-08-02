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
  VoicePartialTranscript
} from '../../../lib/types/Voice';
import { detectCriticalSpans } from './voiceCriticalTerms';

export interface VoiceTranscriptProps {
  partial: VoicePartialTranscript | null;
  listening: boolean;
  /** Transcript held by the critical-terms policy, awaiting confirmation. */
  pendingConfirm?: VoiceFinalTranscript | null;
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
  pendingConfirm = null
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
            confirm:
          </Text>
          <Text size='sm'>
            <HighlightedText text={pendingConfirm.text} />
          </Text>
        </Group>
        <Text size='xs' c='dimmed' mt={4}>
          Contains critical values — confirm it was heard correctly, or discard
          and say it again.
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
