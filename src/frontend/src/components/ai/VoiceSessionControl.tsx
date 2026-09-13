import { t } from '@lingui/core/macro';
import { Badge, Button, Group, Text } from '@mantine/core';
import type { VoiceClientState, VoiceError } from '../../../lib/types/Voice';
import { useVoiceSessionState } from '../../states/VoiceSessionState';

export interface VoiceSessionControlProps {
  state: VoiceClientState;
  error: VoiceError | null;
  muted: boolean;
  webrtcPreview?: boolean;
  onStart: () => void;
  onEnd: () => void;
  onCancel: () => void;
  onToggleMute: () => void;
  onConfirmTranscript?: () => void;
  onDiscardTranscript?: () => void;
}
export function VoiceSessionControl(props: Readonly<VoiceSessionControlProps>) {
  const split = useVoiceSessionState();
  const active = !['unavailable', 'ready', 'error'].includes(props.state);
  if (props.state === 'unavailable') return null;
  const labels: Record<VoiceClientState, string> = {
    unavailable: t`Voice unavailable`,
    ready: t`Voice ready`,
    connecting: t`Connecting…`,
    listening: t`Listening`,
    confirming: t`Confirm transcript`,
    reviewing: t`Reviewing…`,
    speaking: t`Speaking`,
    error: t`Voice error`
  };
  return (
    <Group gap='xs' data-testid='voice-session-control' data-voice-surface>
      {!active ? (
        <Button
          mih={44}
          onClick={props.onStart}
          data-testid='voice-start'
          disabled={props.error?.code === 'BROWSER_UNSUPPORTED'}
          aria-label={t`Start voice session`}
          aria-keyshortcuts='Control+Shift+V Meta+Shift+V'
        >{t`Voice`}</Button>
      ) : (
        <>
          <Badge data-testid='voice-state-badge'>{labels[props.state]}</Badge>
          <Text size='sm' data-testid='voice-mic-status'>
            {split.mic === 'listening'
              ? t`Microphone listening`
              : split.mic === 'muted'
                ? t`Microphone muted`
                : split.mic === 'ptt_idle'
                  ? t`Microphone waiting for push to talk`
                  : t`Microphone paused`}
          </Text>
          <Text size='sm'>
            {split.playback === 'playing'
              ? t`Audio playing`
              : split.playback === 'pending'
                ? t`Waiting for audio`
                : t`Audio stopped`}
          </Text>
          <Button
            mih={44}
            variant='default'
            onClick={props.onToggleMute}
            data-testid='voice-mute'
            aria-label={props.muted ? t`Unmute microphone` : t`Mute microphone`}
          >
            {props.muted ? t`Unmute` : t`Mute`}
          </Button>
          {props.state === 'confirming' && (
            <>
              <Button
                mih={44}
                color='orange'
                onClick={props.onConfirmTranscript}
                data-testid='voice-confirm-transcript'
              >{t`Confirm transcript`}</Button>
              <Button
                mih={44}
                variant='default'
                onClick={props.onDiscardTranscript}
                data-testid='voice-discard-transcript'
              >{t`Discard transcript`}</Button>
            </>
          )}
          <Button
            mih={44}
            variant='default'
            onClick={props.onCancel}
            data-testid='voice-stop-speaking'
          >{t`Stop speaking`}</Button>
          <Button
            mih={44}
            color='red'
            onClick={props.onEnd}
            data-testid='voice-end'
            aria-keyshortcuts='Control+Shift+V Meta+Shift+V'
          >{t`End voice`}</Button>
        </>
      )}
      {props.webrtcPreview && active && (
        <Badge variant='outline'>{t`Preview`}</Badge>
      )}
      {props.error && (
        <Text c='red' data-testid='voice-error'>
          {props.error.code}
        </Text>
      )}
    </Group>
  );
}
