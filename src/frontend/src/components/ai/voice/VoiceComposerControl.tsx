/**
 * Composer voice controls (plan C3).
 *
 * `VoiceComposerControl` is the compact mic START action embedded in the
 * message composer; `VoiceActiveStrip` is the compact active-session strip
 * (status, mute, stop/end) shown near the composer and on every drawer tab
 * so switching tabs never hides End/Mute. Both reuse the existing
 * controller and consent flow — `onStart` is always `voice.start()`
 * (`useVoiceSurfaceState.requestStart`); nothing here touches microphone
 * capture. The full `VoiceSessionControl` stays for the hands-free surface.
 */
import { t } from '@lingui/core/macro';
import { ActionIcon, Badge, Button, Group, Text, Tooltip } from '@mantine/core';
import { IconMicrophone } from '@tabler/icons-react';
import type { VoiceClientState, VoiceError } from '../../../../lib/types/Voice';
import {
  useVoiceSessionState,
  useVoiceSurfaceState
} from '../../../states/VoiceSessionState';
import {
  type VoiceErrorKey,
  type VoiceStartFeedback,
  voiceErrorKey
} from './voiceFeedback';
import { VOICE_SHORTCUT } from './voiceShortcuts';
import type { VoiceStartBlockReason } from './voiceStartEligibility';

export interface VoiceComposerControlProps {
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
  /** Shared start eligibility (button and keyboard shortcut). */
  startBlockedReason: VoiceStartBlockReason | null;
}

export function voiceStartBlockedLabel(
  reason: VoiceStartBlockReason | null
): string | null {
  switch (reason) {
    case 'read-only':
      return t`Voice cannot start in a read-only conversation.`;
    case 'deletion':
      return t`Voice cannot start while this conversation is being deleted.`;
    case 'applying-scope':
      return t`Voice cannot start while the analysis scope is being applied.`;
    case 'turn-in-flight':
      return t`Voice cannot start while a message is being processed.`;
    case 'syncing':
      return t`Voice cannot start while conversations are syncing.`;
    default:
      return null;
  }
}

/**
 * VOICE-H01: translated explanation plus a safe recovery hint for a mapped
 * error key. Raw codes and server `detail` strings are never rendered — the
 * generic fallback keeps unknown failures useful without disclosure.
 */
export function voiceErrorText(key: VoiceErrorKey): {
  message: string;
  recovery: string;
} {
  switch (key) {
    case 'microphone-denied':
      return {
        message: t`AIMMS can't reach your microphone.`,
        recovery: t`Allow microphone access in your browser, then try again.`
      };
    case 'browser-unsupported':
      return {
        message: t`This browser can't run voice sessions.`,
        recovery: t`Try a current version of Chrome, Edge, Firefox or Safari.`
      };
    case 'session-expired':
      return {
        message: t`The voice session ended.`,
        recovery: t`Start voice again to continue.`
      };
    case 'session-limit':
      return {
        message: t`Too many voice sessions are active right now.`,
        recovery: t`Wait a moment, then try again.`
      };
    case 'not-allowed':
      return {
        message: t`Your account can't use voice in this conversation.`,
        recovery: t`Ask an administrator if you think this is wrong.`
      };
    case 'connection':
      return {
        message: t`Voice can't reach the speech service right now.`,
        recovery: t`Check your connection and try again.`
      };
    case 'consent-required':
      return {
        message: t`Voice needs your consent before it can start.`,
        recovery: t`Review the disclosure and accept it, then try again.`
      };
    case 'locale-unsupported':
      return {
        message: t`That language isn't supported for voice yet.`,
        recovery: t`Choose English (en-US) and try again.`
      };
    case 'context-changed':
      return {
        message: t`The conversation context changed while voice was starting.`,
        recovery: t`Refresh the conversation, then start voice again.`
      };
    case 'incomplete':
      return {
        message: t`Voice didn't finish processing.`,
        recovery: t`Please try again.`
      };
    case 'conflict':
      return {
        message: t`That voice request was already processed.`,
        recovery: t`Refresh the conversation, then try again.`
      };
    case 'sample':
      return {
        message: t`The voice sample couldn't play.`,
        recovery: t`Wait a minute, then try again.`
      };
    default:
      return {
        message: t`Something went wrong with voice.`,
        recovery: t`Please try again.`
      };
  }
}

/**
 * VOICE-H05: accessible explanation for a refused start request — the
 * existing eligibility labels where they apply, and a safe fallback for any
 * reason those labels do not know (the raw reason is never echoed).
 */
export function voiceStartFeedbackText(feedback: VoiceStartFeedback): string {
  if (feedback.key === 'blocked') {
    return (
      voiceStartBlockedLabel(feedback.reason as VoiceStartBlockReason) ??
      t`Voice can't start right now. Please try again.`
    );
  }
  switch (feedback.key) {
    case 'capability-missing':
      return t`Voice isn't ready yet — voice settings are still loading. Please try again in a moment.`;
    case 'capability-disabled':
      return t`Voice is turned off for your account. Ask an administrator to enable it.`;
    default:
      return t`Voice is temporarily unavailable while AI starts or recovers. Please wait, then try again.`;
  }
}

/**
 * The composer-embedded microphone start action. Renders nothing when the
 * capability is off (no misleading control), a disabled action with a
 * readable explanation while voice is unavailable, and the mic otherwise.
 */
export function VoiceComposerControl(
  props: Readonly<VoiceComposerControlProps>
) {
  const split = useVoiceSessionState();
  const startFeedback = useVoiceSurfaceState((s) => s.startFeedback);
  const active = !['unavailable', 'ready', 'error'].includes(props.state);
  if (props.state === 'unavailable') {
    if (!split.capability?.enabled) return null;
    const failed = split.capability.runtime?.state === 'permanently_failed';
    return (
      <Group gap='xs' data-testid='voice-runtime-unavailable'>
        <Tooltip label={t`Start voice session`} withArrow>
          <ActionIcon
            disabled
            size={44}
            radius='xl'
            variant='subtle'
            color='gray'
            data-testid='voice-start'
            aria-label={t`Start voice session`}
            aria-keyshortcuts={VOICE_SHORTCUT.aria}
          >
            <IconMicrophone size={20} />
          </ActionIcon>
        </Tooltip>
        <Text component='output' aria-live='polite' size='sm'>
          {failed
            ? t`Voice is unavailable. An administrator needs to check AI startup.`
            : t`Voice is temporarily unavailable while AI starts or recovers. Please wait.`}
        </Text>
      </Group>
    );
  }
  const blockedLabel = voiceStartBlockedLabel(props.startBlockedReason);
  const errorText = props.error
    ? voiceErrorText(voiceErrorKey(props.error.code))
    : null;
  return (
    <Group gap='xs' data-testid='voice-session-control' data-voice-surface>
      {!active && (
        <Tooltip label={blockedLabel ?? t`Start voice session`} withArrow>
          <ActionIcon
            size={44}
            radius='xl'
            variant='subtle'
            color='gray'
            onClick={props.onStart}
            data-testid='voice-start'
            disabled={
              props.error?.code === 'BROWSER_UNSUPPORTED' ||
              props.startBlockedReason !== null
            }
            aria-label={t`Start voice session`}
            aria-keyshortcuts={VOICE_SHORTCUT.aria}
          >
            <IconMicrophone size={20} />
          </ActionIcon>
        </Tooltip>
      )}
      {!active && errorText && (
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

/**
 * The compact active-session strip: visible state (never color alone),
 * Mute/Unmute, transcript confirmation when needed, and unambiguous
 * "End voice" — kept reachable on every drawer tab.
 */
export function VoiceActiveStrip(props: Readonly<VoiceComposerControlProps>) {
  const split = useVoiceSessionState();
  const errorText = props.error
    ? voiceErrorText(voiceErrorKey(props.error.code))
    : null;
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
    <Group
      gap='xs'
      data-testid='ai-chat-voice-active'
      data-voice-surface
      wrap='wrap'
    >
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
        aria-keyshortcuts={VOICE_SHORTCUT.aria}
      >{t`End voice`}</Button>
      {props.webrtcPreview && <Badge variant='outline'>{t`Preview`}</Badge>}
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
    </Group>
  );
}
