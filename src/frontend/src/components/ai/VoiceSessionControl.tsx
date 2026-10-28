import { t } from '@lingui/core/macro';
import { Badge, Button, Group, Text } from '@mantine/core';
import type { VoiceClientState, VoiceError } from '../../../lib/types/Voice';
import {
  useVoiceSessionState,
  useVoiceSurfaceState
} from '../../states/VoiceSessionState';
import {
  voiceErrorText,
  voiceStartFeedbackText
} from './voice/VoiceComposerControl';
import { voiceErrorKey } from './voice/voiceFeedback';
import { VOICE_SHORTCUT } from './voice/voiceShortcuts';

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
  const startFeedback = useVoiceSurfaceState((s) => s.startFeedback);
  const errorText = props.error
    ? voiceErrorText(voiceErrorKey(props.error.code))
    : null;
  const active = !['unavailable', 'ready', 'error'].includes(props.state);
  if (props.state === 'unavailable') {
    if (!split.capability?.enabled) return null;
    const failed = split.capability.runtime?.state === 'permanently_failed';
    return (
      <Group gap='xs' data-testid='voice-runtime-unavailable'>
        <Button
          disabled
          mih={44}
          data-testid='voice-start'
        >{t`Start voice`}</Button>
        <Text component='output' aria-live='polite' size='sm'>
          {failed
            ? t`Voice is unavailable. An administrator needs to check AI startup.`
            : t`Voice is temporarily unavailable while AI starts or recovers. Please wait.`}
        </Text>
      </Group>
    );
  }
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
          aria-keyshortcuts={VOICE_SHORTCUT.aria}
        >{t`Start voice`}</Button>
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
                disabled={!!split.pendingConfirm?.reviewIssue}
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
            aria-keyshortcuts={VOICE_SHORTCUT.aria}
          >{t`Stop voice`}</Button>
        </>
      )}
      {props.webrtcPreview && active && (
        <Badge variant='outline'>{t`Preview`}</Badge>
      )}
      {errorText && (
        <Text
          component='output'
          c='red'
          data-testid='voice-error'
          aria-live='polite'
        >
          {errorText.message} {errorText.recovery}
        </Text>
      )}
      {startFeedback && (
        <Group gap='xs' data-testid='voice-start-feedback'>
          <Text component='output' aria-live='polite' size='sm'>
            {voiceStartFeedbackText(startFeedback)}
          </Text>
          <Button
            mih={44}
            variant='default'
            data-testid='voice-start-feedback-retry'
            onClick={props.onStart}
          >{t`Try again`}</Button>
        </Group>
      )}
    </Group>
  );
}
