/**
 * VoiceTranscript (WS5-T4/T7): live partial text plus critical-term review.
 *
 * Partial text is display-only and visually provisional. When a completed
 * transcript contains critical terms or falls below the confidence floor it
 * is held here for visible correction and explicit typed/tap confirmation
 * before it becomes an application turn. Confirmation creates text input
 * only; it never approves any effect. Highlighting is never color-only.
 */

import {
  Badge,
  Button,
  Group,
  Mark,
  Paper,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { useEffect, useState } from 'react';

import type {
  VoiceFinalTranscript,
  VoicePartialTranscript
} from '../../../lib/types/Voice';
import {
  DEFAULT_CONFIDENCE_FLOOR,
  detectCriticalSpans
} from './voiceCriticalTerms';

function HighlightedTranscript({ text }: Readonly<{ text: string }>) {
  const spans = detectCriticalSpans(text);
  if (spans.length === 0) {
    return <Text size='sm'>{text}</Text>;
  }
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((span, index) => {
    if (span.start > cursor) {
      parts.push(text.slice(cursor, span.start));
    }
    parts.push(
      <Mark key={`${span.start}-${index}`} data-kind={span.kind}>
        <u>{span.text}</u>
      </Mark>
    );
    cursor = span.end;
  });
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return <Text size='sm'>{parts}</Text>;
}

export interface VoiceTranscriptProps {
  partial: VoicePartialTranscript | null;
  listening: boolean;
  pending?: VoiceFinalTranscript | null;
  confidenceFloor?: number;
  onConfirmPending?: (correctedText?: string) => void;
  onDiscardPending?: () => void;
}

export function VoiceTranscript({
  partial,
  listening,
  pending = null,
  confidenceFloor = DEFAULT_CONFIDENCE_FLOOR,
  onConfirmPending,
  onDiscardPending
}: Readonly<VoiceTranscriptProps>) {
  const [draft, setDraft] = useState<string>('');

  useEffect(() => {
    setDraft(pending?.text ?? '');
  }, [pending]);

  if (pending) {
    const lowConfidence =
      pending.confidence === null || pending.confidence < confidenceFloor;
    return (
      <Paper
        p='xs'
        radius='sm'
        withBorder
        data-testid='voice-pending-transcript'
      >
        <Stack gap={6}>
          <Group gap='xs'>
            <Text size='xs' fw={600}>
              Please confirm what you said
            </Text>
            {lowConfidence && (
              <Badge size='xs' color='yellow' variant='light'>
                low confidence
              </Badge>
            )}
          </Group>
          <HighlightedTranscript text={pending.text} />
          <Textarea
            value={draft}
            onChange={(event) => setDraft(event.currentTarget.value)}
            autosize
            minRows={1}
            maxRows={4}
            aria-label='Correct transcript before sending'
            data-testid='voice-pending-edit'
          />
          <Group gap='xs'>
            <Button
              size='xs'
              onClick={() => onConfirmPending?.(draft)}
              data-testid='voice-pending-confirm'
            >
              Send
            </Button>
            <Button
              size='xs'
              variant='light'
              color='gray'
              onClick={() => onDiscardPending?.()}
              data-testid='voice-pending-discard'
            >
              Discard
            </Button>
          </Group>
        </Stack>
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
          {partial.text}
        </Text>
      </Group>
    </Paper>
  );
}
