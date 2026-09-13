import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  NativeSelect,
  Stack,
  Switch,
  Text
} from '@mantine/core';
import { useLocalState } from '../../../states/LocalState';
import {
  useVoiceSessionState,
  useVoiceSurfaceState,
  voiceController
} from '../../../states/VoiceSessionState';
import { PushToTalkButton } from './PushToTalkButton';
import type { VoiceNotice } from './types';

export function noticeText(notice: VoiceNotice): string {
  switch (notice) {
    case 'hidden':
      return t`Voice is paused while this tab is hidden. Pending confirmation is set aside.`;
    case 'ended_away':
      return t`Voice ended while you were away. Start a new session and request a fresh read-back.`;
    case 'idle_ended':
      return t`Voice ended after five minutes without activity.`;
    case 'reconnecting':
      return t`Connection dropped. Checking the last request; do not repeat the action yet.`;
    case 'reconnected':
      return t`Voice reconnected. No request was resubmitted. Review the last action status before continuing.`;
    case 'connection_failed':
      return t`Voice could not reconnect. The last action may still be unconfirmed; check its receipt before repeating it.`;
    case 'queue_full':
      return t`I did not catch that. Please wait for the current answer, then say it again.`;
    case 'audio_blocked':
      return t`Audio playback is blocked. Select Resume read-back, or read the text here.`;
    case 'tts_timeout':
      return t`Speech did not start in time. The text remains available; retry playback without resending your request.`;
    case 'mic_silent':
      return t`The microphone is very quiet. Check its placement or your audio route; this warning does not end the session.`;
    case 'route_changed':
      return t`Your audio route changed. If playback paused, select Resume read-back.`;
    case 'sounds_off':
      return t`Status sounds are off. Spoken answers and visible status remain available.`;
    case 'readback_available':
      return t`Confirmation is not armed. Resume available output, or ask for a fresh preview.`;
    default:
      return '';
  }
}
export function VoiceExperienceControls() {
  const s = useVoiceSessionState();
  const p = useLocalState();
  if (!s.session) return s.notice ? <Text>{noticeText(s.notice)}</Text> : null;
  return (
    <Stack gap='xs'>
      <Group>
        <NativeSelect
          label={t`Listening mode`}
          value={s.mode}
          onChange={(event) =>
            voiceController.setMode(
              event.currentTarget.value === 'push_to_talk'
                ? 'push_to_talk'
                : 'continuous'
            )
          }
          data={[
            { value: 'continuous', label: t`Continuous` },
            { value: 'push_to_talk', label: t`Push to talk` }
          ]}
        />
        {s.mode === 'push_to_talk' && (
          <PushToTalkButton
            disabled={s.muted || s.hidden || s.transport !== 'connected'}
          />
        )}
        <Button
          mih={44}
          variant='default'
          onClick={() => void voiceController.help()}
        >{t`What are you waiting for?`}</Button>
        <Button
          mih={44}
          variant='default'
          onClick={() => void voiceController.resumeOutput()}
        >{t`Resume read-back`}</Button>
        {s.capability?.mobile_surface && (
          <Button
            mih={44}
            variant='default'
            onClick={useVoiceSurfaceState.getState().openFullscreen}
          >{t`Hands-free view`}</Button>
        )}
      </Group>
      {!p.voiceSpeakerNoticeSeen && (
        <Alert title={t`Using device speakers?`}>
          <Text>{t`Continuous listening works on every route. If playback interrupts itself, try push to talk.`}</Text>
          <Button
            mih={44}
            variant='subtle'
            onClick={() =>
              p.setVoicePreferences({ voiceSpeakerNoticeSeen: true })
            }
          >{t`Got it`}</Button>
        </Alert>
      )}
      <Group>
        {s.outputSelectionSupported && s.outputs.length > 0 && (
          <NativeSelect
            label={t`Audio output`}
            value={s.outputDevice}
            data={[{ value: '', label: t`System default` }, ...s.outputs]}
            onChange={(event) =>
              void voiceController.selectOutput(event.currentTarget.value)
            }
          />
        )}
        {!s.outputSelectionSupported && (
          <Text size='sm'>{t`This browser manages the output route. Change speakers or headphones in your device settings.`}</Text>
        )}
        <Switch
          label={t`Voice status sounds`}
          checked={p.voiceEarcons}
          onChange={(event) =>
            p.setVoicePreferences({ voiceEarcons: event.currentTarget.checked })
          }
        />
        <Switch
          label={t`Announce transcripts to screen readers`}
          checked={p.voiceSrAnnounceTranscripts}
          onChange={(event) =>
            p.setVoicePreferences({
              voiceSrAnnounceTranscripts: event.currentTarget.checked
            })
          }
        />
      </Group>
      {s.notice && <Text>{noticeText(s.notice)}</Text>}
      {s.presentation && (
        <Group aria-label={t`Spoken answer pages`}>
          <Text>{t`Page ${s.presentation.index + 1} of ${s.presentation.total}`}</Text>
          <Button
            mih={44}
            onClick={() => void voiceController.present('next')}
            disabled={s.presentation.index + 1 >= s.presentation.total}
          >{t`Next three`}</Button>
          <Button
            mih={44}
            onClick={() => void voiceController.present('repeat')}
          >{t`Repeat`}</Button>
          <Button
            mih={44}
            onClick={() => void voiceController.present('slower')}
          >{t`Slower`}</Button>
          <Button
            mih={44}
            onClick={() => void voiceController.present('short')}
          >{t`Short version`}</Button>
        </Group>
      )}
      {s.lastSpoken &&
        ['blocked', 'text_only', 'paused'].includes(s.playback) && (
          <Text data-testid='voice-output-fallback'>
            {s.lastSpoken.spoken_summary}
          </Text>
        )}
      {s.revisions.length > 1 && (
        <Stack gap={2} aria-label={t`Transcript revisions`}>
          {s.revisions.map((item, index) => (
            <Text key={`${item.itemId}:${index}`} size='sm'>
              {t`Revision ${item.revision ?? 1}`}: {item.text}
            </Text>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
