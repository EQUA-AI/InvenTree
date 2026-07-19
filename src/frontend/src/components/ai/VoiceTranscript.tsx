/**
 * VoiceTranscript (WS5-T4): live partial transcript display.
 *
 * Partial text is display-only and visually provisional. The voice loop is
 * hands-free: completed transcripts are submitted automatically and the
 * spoken answer is the correction loop, so no confirmation UI is shown
 * here. Critical-value confirmation remains a structured-use (capture)
 * requirement and lives with those flows, not in advisory chat.
 */

import { Group, Paper, Text } from '@mantine/core';

import type { VoicePartialTranscript } from '../../../lib/types/Voice';

export interface VoiceTranscriptProps {
  partial: VoicePartialTranscript | null;
  listening: boolean;
}

export function VoiceTranscript({
  partial,
  listening
}: Readonly<VoiceTranscriptProps>) {
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
