import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Modal,
  NativeSelect,
  Stack,
  Switch,
  Text
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useEffect, useRef, useState } from 'react';
import { useLocalState } from '../../../states/LocalState';
import {
  useVoiceSessionState,
  useVoiceSurfaceState,
  voiceController
} from '../../../states/VoiceSessionState';
import { voiceHeaders, voiceUrl } from './voiceHttp';

/** Shared disclosure for live voice and transcript-only capture/closeout. */
export function VoiceConsentDisclosure() {
  return (
    <Stack gap='xs'>
      <Text>{t`Starting voice sends your speech to the speech service for live transcription. AIMMS keeps only the text transcript, stored with your chat history; it never keeps a recording of your voice.`}</Text>
      <Text>{t`If your client has enabled memory and you separately acknowledge the memory notice, eligible voice transcripts can produce memory suggestions for future conversations. Shared conversations are excluded. Review and forget memories on What AIMMS remembers, or turn off learning for a conversation. Accepting this voice disclosure alone does not enable memory.`}</Text>
      <Text>{t`Your voice is never used to identify you or sign you in. Actions run under the account you are logged in with, and every business change still needs your explicit confirmation.`}</Text>
      <Text>{t`AIMMS listens only inside a session you start; there is no wake word and it never listens in the background.`}</Text>
      <Text>{t`Voice stays on while this tab is visible. If you switch apps or lock the screen, AIMMS stops listening and pauses playback; if the tab stays hidden for 5 minutes the voice session ends. Anything waiting for your confirmation is set aside, nothing is executed, and it is read back again for a fresh confirmation when you return.`}</Text>
    </Stack>
  );
}
export function VoiceConsentDialog() {
  const { consent: opened, closeConsent } = useVoiceSurfaceState();
  const capability = useVoiceSessionState((s) => s.capability);
  const preferences = useLocalState();
  const [sampleState, setSampleState] = useState<'idle' | 'loading' | 'failed'>(
    'idle'
  );
  const audio = useRef<HTMLAudioElement | null>(null);
  const sampleAbort = useRef<AbortController | null>(null);
  const objectUrl = useRef<string | null>(null);
  const stopSample = () => {
    sampleAbort.current?.abort();
    audio.current?.pause();
    audio.current = null;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = null;
  };
  const form = useForm({
    initialValues: {
      locale: preferences.voiceLocale,
      accepted: false,
      sounds: preferences.voiceEarcons
    },
    validate: {
      accepted: (value) =>
        value ? null : t`Please review and accept the disclosure.`,
      locale: (value) =>
        capability?.voices.some((pair) => pair.locale === value)
          ? null
          : t`Choose an available language and voice.`
    }
  });
  useEffect(() => {
    if (opened) {
      form.setValues({
        locale: useLocalState.getState().voiceLocale,
        accepted: false,
        sounds: useLocalState.getState().voiceEarcons
      });
      form.clearErrors();
    } else stopSample();
    return stopSample;
  }, [opened]);
  const sample = async () => {
    const pair = capability?.voices.find(
      (pair) => pair.locale === form.values.locale
    );
    if (!pair) return;
    stopSample();
    const abort = new AbortController();
    sampleAbort.current = abort;
    setSampleState('loading');
    try {
      const host = new URL(
        'api/ai/',
        `${preferences.getHost().replace(/\/$/, '')}/`
      ).toString();
      const response = await fetch(voiceUrl(host, 'sample'), {
        method: 'POST',
        headers: voiceHeaders(),
        credentials: 'include',
        body: JSON.stringify(pair),
        signal: abort.signal
      });
      if (!response.ok) throw new Error('sample unavailable');
      const blob = await response.blob();
      if (abort.signal.aborted) return;
      objectUrl.current = URL.createObjectURL(blob);
      audio.current = new Audio(objectUrl.current);
      await audio.current.play();
      setSampleState('idle');
    } catch {
      if (!abort.signal.aborted) setSampleState('failed');
    }
  };
  return (
    <Modal
      opened={opened}
      onClose={closeConsent}
      title={t`Start a voice session`}
      size='lg'
    >
      <form
        onSubmit={form.onSubmit((values) => {
          const pair = capability?.voices.find(
            (pair) => pair.locale === values.locale
          );
          if (
            !pair ||
            capability?.consent_version !== 'consent-v3-memory' ||
            capability.idle_timeout_s !== 300
          )
            return;
          stopSample();
          preferences.setVoicePreferences({
            voiceLocale: pair.locale,
            voiceOutputVoice: pair.voice,
            voiceConsentVersion: capability.consent_version,
            voiceEarcons: values.sounds
          });
          closeConsent();
          void voiceController.start();
        })}
      >
        <Stack>
          <VoiceConsentDisclosure />
          <Text size='sm'>{t`You can interrupt, say “stop speaking,” or ask “what can I say?”`}</Text>
          <NativeSelect
            label={t`Language and output voice`}
            data={
              capability?.voices.map((pair) => ({
                value: pair.locale,
                label: `${pair.locale} — ${pair.voice}`
              })) ?? []
            }
            {...form.getInputProps('locale')}
          />
          {form.values.locale !== 'en-US' && (
            <Alert>{t`Voice changes are available only in English during this pilot. Use on-screen review in other languages.`}</Alert>
          )}
          {capability?.foreground_session && (
            <Button
              mih={44}
              variant='default'
              loading={sampleState === 'loading'}
              onClick={() => void sample()}
            >{t`Hear a voice sample`}</Button>
          )}
          {sampleState === 'failed' && (
            <Text component='output'>{t`The sample could not play. Please wait a minute and try again.`}</Text>
          )}
          <Switch
            label={t`Play voice status sounds`}
            {...form.getInputProps('sounds', { type: 'checkbox' })}
          />
          <Checkbox
            label={t`I have read the disclosure and want to start voice.`}
            {...form.getInputProps('accepted', { type: 'checkbox' })}
          />
          <Group justify='end'>
            <Button
              variant='default'
              mih={44}
              onClick={closeConsent}
            >{t`Cancel`}</Button>
            <Button
              type='submit'
              mih={44}
              data-testid='voice-consent-start'
            >{t`Start voice`}</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
