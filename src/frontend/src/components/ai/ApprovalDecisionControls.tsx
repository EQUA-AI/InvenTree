import { t } from '@lingui/core/macro';
import { Button, Checkbox, Group, Stack, Textarea } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';
import { useVoiceDecisionState } from '../../states/VoiceDecisionState';

/** Controls inside the sole decision card; no duplicate source-action authority. */
export function ApprovalDecisionControls({
  busy,
  act
}: { busy: boolean; act: (action: string, phrase?: string) => Promise<void> }) {
  const decision = useVoiceDecisionState((state) => state.decision);
  const [checked, setChecked] = useState(false);
  const form = useForm({
    initialValues: { reason: '' },
    validate: {
      reason: (value) =>
        value.trim() && value.length <= 400
          ? null
          : t`Enter a reason of up to 400 characters.`
    }
  });
  if (!decision) return null;
  if (decision.kind === 'selection')
    return (
      <Group>
        {decision.sections.map((section) => (
          <Button
            key={section.id}
            disabled={busy}
            variant='light'
            onClick={() => void act('respond', section.id)}
          >
            {section.label}
          </Button>
        ))}
        <Button
          disabled={busy}
          onClick={() => void act('respond', 'next page')}
        >{t`Next page`}</Button>
      </Group>
    );
  if (decision.kind !== 'approval_review') return null;
  const reasonAction = (command: string) => {
    if (!form.validate().hasErrors)
      void act('respond', `${command}: ${form.values.reason.trim()}`);
  };
  return (
    <Stack data-testid='approval-decision-controls'>
      <Group>
        <Button
          disabled={busy}
          variant='light'
          onClick={() => void act('respond', 'next section')}
        >{t`Next section`}</Button>
        <Button
          disabled={busy}
          variant='subtle'
          onClick={() => void act('respond', 'skip')}
        >{t`Skip request`}</Button>
      </Group>
      <Checkbox
        checked={checked}
        onChange={(event) => setChecked(event.currentTarget.checked)}
        label={t`I have reviewed every required section of this request.`}
      />
      <Button
        disabled={!checked || busy}
        onClick={() => void act('acknowledge-review')}
      >{t`Confirm reviewed`}</Button>
      <Button
        disabled={!decision.review_acknowledged || busy}
        onClick={() =>
          void act('respond', `approve ${decision.source_id.slice(0, 8)}`)
        }
      >{t`Prepare approval`}</Button>
      <Textarea
        label={t`Reason or requested changes`}
        {...form.getInputProps('reason')}
      />
      <Group>
        <Button
          disabled={busy}
          color='red'
          onClick={() => reasonAction('deny')}
        >{t`Prepare rejection`}</Button>
        <Button
          disabled={busy}
          variant='light'
          onClick={() => reasonAction('request changes')}
        >{t`Request changes`}</Button>
        <Button
          disabled={busy}
          variant='subtle'
          onClick={() => void act('respond', 'cancel the request')}
        >{t`Prepare cancellation`}</Button>
      </Group>
    </Stack>
  );
}
