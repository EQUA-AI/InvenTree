import { t } from '@lingui/core/macro';
import {
  Button,
  Group,
  NativeSelect,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useState } from 'react';
import {
  useVoiceSessionState,
  voiceController
} from '../../../states/VoiceSessionState';

/** Navigation is read-only; completion only presents the shared decision card. */
export function VoiceProcedureControls() {
  const s = useVoiceSessionState();
  const [target, setTarget] = useState('');
  const [position, setPosition] = useState(0);
  const [value, setValue] = useState('');
  const [passed, setPassed] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  if (!s.session || !s.capability?.guided_procedures) return null;
  const request = async (command: string) => {
    setBusy(true);
    try {
      const reply = await voiceController.walkthrough(
        Number(target),
        position,
        command,
        value || undefined,
        passed === '' ? undefined : passed === 'true'
      );
      if (reply) {
        setPosition(reply.position);
        setText(reply.speak_text);
        if (command !== 'done') {
          setValue('');
          setPassed('');
        }
      }
    } catch {
      setText(
        t`The procedure is unavailable. Review the work order on screen.`
      );
    } finally {
      setBusy(false);
    }
  };
  const disabled =
    busy ||
    !/^[1-9]\d*$/.test(target) ||
    s.hidden ||
    s.transport !== 'connected';
  return (
    <Stack gap='xs' data-testid='voice-procedure-controls'>
      <Text fw={700}>{t`Guided procedure`}</Text>
      <TextInput
        label={t`Work order ID`}
        inputMode='numeric'
        value={target}
        disabled={busy}
        onChange={(event) => {
          setTarget(event.currentTarget.value);
          setPosition(0);
          setText('');
          setValue('');
          setPassed('');
        }}
      />
      <Group>
        <Button
          mih={44}
          disabled={disabled}
          onClick={() => void request('repeat')}
        >{t`Read step`}</Button>
        <Button
          mih={44}
          disabled={disabled}
          onClick={() => void request('back')}
        >{t`Previous step`}</Button>
        <Button
          mih={44}
          disabled={disabled}
          onClick={() => void request('next')}
        >{t`Next step`}</Button>
      </Group>
      <Text component='output' style={{ whiteSpace: 'pre-wrap' }}>
        {text}
      </Text>
      {s.capability.procedure_complete && (
        <>
          <TextInput
            label={t`Measured value (literal units)`}
            value={value}
            disabled={busy}
            onChange={(event) => setValue(event.currentTarget.value)}
          />
          <NativeSelect
            label={t`Step result`}
            value={passed}
            disabled={busy}
            onChange={(event) => setPassed(event.currentTarget.value)}
            data={[
              { value: '', label: t`Not supplied` },
              { value: 'true', label: t`Passed` },
              { value: 'false', label: t`Failed` }
            ]}
          />
          <Button
            mih={44}
            disabled={disabled}
            onClick={() => void request('done')}
          >{t`Review step completion`}</Button>
          <Text size='sm'>{t`This only requests a read-back. Confirm the shared decision separately.`}</Text>
        </>
      )}
    </Stack>
  );
}
