/**
 * VoiceSessionControl (WS5-T4): explicit start/stop, truthful state, consent.
 *
 * Starting a session is the consent act (owner decision 2026-07-15): the
 * disclosure is shown on the control itself. The microphone indicator is
 * driven only by real session state — never optimistic.
 */

import { ActionIcon, Badge, Button, Group, Text, Tooltip } from '@mantine/core';
import {
  IconMicrophone,
  IconMicrophoneOff,
  IconPlayerStopFilled,
  IconVolumeOff
} from '@tabler/icons-react';
import { useMemo } from 'react';

import type { VoiceClientState, VoiceError } from '../../../lib/types/Voice';

const STATE_LABELS: Record<VoiceClientState, string> = {
  unavailable: 'Voice unavailable',
  ready: 'Voice ready',
  connecting: 'Connecting…',
  listening: 'Listening',
  reviewing: 'Reviewing…',
  speaking: 'Speaking',
  error: 'Voice error'
};

const STATE_COLORS: Record<VoiceClientState, string> = {
  unavailable: 'gray',
  ready: 'blue',
  connecting: 'yellow',
  listening: 'red',
  reviewing: 'yellow',
  speaking: 'teal',
  error: 'red'
};

const CONSENT_NOTICE =
  'Starting voice transcribes your speech and stores the transcript with ' +
  'your chat history. No audio is kept.';

export interface VoiceSessionControlProps {
  state: VoiceClientState;
  error: VoiceError | null;
  muted: boolean;
  webrtcPreview?: boolean;
  onStart: () => void;
  onEnd: () => void;
  onCancel: () => void;
  onToggleMute: () => void;
}

export function VoiceSessionControl({
  state,
  error,
  muted,
  webrtcPreview = true,
  onStart,
  onEnd,
  onCancel,
  onToggleMute
}: Readonly<VoiceSessionControlProps>) {
  const active = ['connecting', 'listening', 'reviewing', 'speaking'].includes(
    state
  );

  const statusBadge = useMemo(
    () => (
      <Badge
        color={STATE_COLORS[state]}
        variant={state === 'listening' ? 'filled' : 'light'}
        aria-live='polite'
        data-testid='voice-state-badge'
      >
        {STATE_LABELS[state]}
      </Badge>
    ),
    [state]
  );

  if (state === 'unavailable') {
    return null;
  }

  return (
    <Group gap='xs' wrap='nowrap' data-testid='voice-session-control'>
      {!active ? (
        <Tooltip label={CONSENT_NOTICE} multiline w={280} withArrow>
          <Button
            leftSection={<IconMicrophone size={16} />}
            variant='light'
            size='xs'
            onClick={onStart}
            disabled={
              state === 'error' && error?.code === 'BROWSER_UNSUPPORTED'
            }
            data-testid='voice-start'
            aria-label='Start voice session'
          >
            Voice
          </Button>
        </Tooltip>
      ) : (
        <>
          {statusBadge}
          <Tooltip label={muted ? 'Unmute microphone' : 'Mute microphone'}>
            <ActionIcon
              variant='subtle'
              size='sm'
              onClick={onToggleMute}
              aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
              data-testid='voice-mute'
            >
              {muted ? (
                <IconMicrophoneOff size={16} />
              ) : (
                <IconMicrophone size={16} />
              )}
            </ActionIcon>
          </Tooltip>
          {state === 'speaking' && (
            <Tooltip label='Stop speaking'>
              <ActionIcon
                variant='subtle'
                size='sm'
                onClick={onCancel}
                aria-label='Stop speaking'
                data-testid='voice-stop-speaking'
              >
                <IconVolumeOff size={16} />
              </ActionIcon>
            </Tooltip>
          )}
          <Tooltip label='End voice session'>
            <ActionIcon
              variant='subtle'
              color='red'
              size='sm'
              onClick={onEnd}
              aria-label='End voice session'
              data-testid='voice-end'
            >
              <IconPlayerStopFilled size={16} />
            </ActionIcon>
          </Tooltip>
        </>
      )}
      {webrtcPreview && active && (
        <Badge color='grape' variant='outline' size='xs'>
          preview
        </Badge>
      )}
      {state === 'error' && error && (
        <Text size='xs' c='red' data-testid='voice-error'>
          {error.code}
        </Text>
      )}
    </Group>
  );
}
