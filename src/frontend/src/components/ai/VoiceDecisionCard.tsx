import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../../App';
import { useVoiceDecisionState } from '../../states/VoiceDecisionState';
import { matchesConfirmPhrase } from './confirmPhrase';
import { decisionOutcome } from './decisionOutcome';

/** The sole shared mount: no tab owns its own copy or confirmation authority. */
export function VoiceDecisionCard() {
  const decision = useVoiceDecisionState((state) => state.decision);
  const event = useVoiceDecisionState((state) => state.event);
  const decide = useVoiceDecisionState((state) => state.decide);
  const sessionId = useVoiceDecisionState((state) => state.sessionId);
  const operation = useQuery({
    queryKey: ['voice-operation', sessionId, decision?.operation_id],
    queryFn: async () =>
      (
        await api.get(
          `/api/ai/voice/sessions/${sessionId}/operations/${decision?.operation_id}`
        )
      ).data as { receipt: Record<string, unknown> },
    enabled: !!sessionId && !!decision?.operation_id,
    refetchInterval:
      decision?.execution_state === 'unknown' ||
      decision?.execution_state === 'executing'
        ? 5000
        : false
  });
  const [phrase, setPhrase] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    setPhrase('');
    setError('');
  }, [decision?.decision_id]);
  if (!decision) return null;
  const pending = decision.state === 'presented';
  const expired = now >= Date.parse(decision.expires_at);
  const outcome = decisionOutcome(operation.data?.receipt);
  const confirm =
    decision.allowed_responses.find((response) =>
      response.startsWith('confirm ')
    ) ?? (decision.allowed_responses.includes('yes') ? 'yes' : null);
  const act = async (action: string, value?: string) => {
    setBusy(true);
    setError('');
    try {
      await decide(action, value);
    } catch {
      setError(
        t`The response is not verified. Refresh the decision before trying again.`
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      withBorder
      p='sm'
      data-testid='voice-decision-card'
      data-decision-id={decision.decision_id}
      data-source-id={decision.source_id}
    >
      <Stack gap='xs'>
        <Group justify='space-between'>
          <Text fw={600}>{decision.target_label}</Text>
          <Badge>{expired && pending ? t`Expired` : decision.state}</Badge>
        </Group>
        {decision.sections.map((section) => (
          <Text key={section.id} size='sm'>
            <strong>{section.label}: </strong>
            {section.text}
          </Text>
        ))}
        <Text size='sm' data-testid='voice-decision-spoken-summary'>
          {decision.spoken_summary}
        </Text>
        {pending && (
          <Text size='xs'>
            {t`Playback`}: {decision.delivery_state} · {t`Expires`}:{' '}
            {new Date(decision.expires_at).toLocaleTimeString()}
          </Text>
        )}
        {!decision.voice_eligible && (
          <Alert color='yellow'>{decision.voice_ineligible_reason}</Alert>
        )}
        {decision.execution_state && (
          <Alert
            color={decision.execution_state === 'succeeded' ? 'teal' : 'yellow'}
            data-testid='voice-decision-outcome'
          >
            {decision.execution_state === 'succeeded'
              ? t`Change recorded`
              : decision.execution_state === 'failed_before_effect'
                ? t`Not applied`
                : t`Result not verified — reconciling, do not retry`}
            {decision.receipt_ref && (
              <Text size='xs'>
                {t`Receipt`}: {decision.receipt_ref}
              </Text>
            )}
          </Alert>
        )}
        {event?.message && (
          <Text size='sm' component='output'>
            {event.message}
          </Text>
        )}
        {outcome.completed.map((item, index) => (
          <Text size='sm' key={`done-${index}`}>
            {t`Completed`}: {item}
          </Text>
        ))}
        {outcome.failed.map((item, index) => (
          <Text size='sm' c='red' key={`failed-${index}`}>
            {t`Failed`}: {item}
          </Text>
        ))}
        {outcome.unknown.map((item, index) => (
          <Text size='sm' c='yellow' key={`unknown-${index}`}>
            {t`Unverified`}: {item}
          </Text>
        ))}
        {pending && decision.required_phrase && (
          <TextInput
            label={t`Type the required confirmation phrase`}
            placeholder={decision.required_phrase}
            value={phrase}
            onChange={(e) => setPhrase(e.currentTarget.value)}
          />
        )}
        {pending && (
          <Group gap='xs'>
            {confirm && (
              <Button
                size='xs'
                loading={busy}
                disabled={
                  expired ||
                  (!!decision.required_phrase &&
                    !matchesConfirmPhrase(phrase, decision.required_phrase))
                }
                onClick={() =>
                  void act(
                    'confirm',
                    decision.required_phrase ? phrase : confirm
                  )
                }
                data-testid='voice-decision-confirm'
              >{t`Confirm`}</Button>
            )}
            {decision.allowed_responses.includes('change that') && (
              <Button
                size='xs'
                variant='light'
                disabled={busy}
                onClick={() => void act('disarm')}
              >{t`Change that`}</Button>
            )}
            {decision.allowed_responses.includes('cancel') && (
              <Button
                size='xs'
                variant='default'
                disabled={busy}
                onClick={() => void act('cancel')}
              >{t`Set aside`}</Button>
            )}
            {decision.allowed_responses.includes('cancel the action') && (
              <Button
                size='xs'
                variant='subtle'
                disabled={busy}
                onClick={() => void act('cancel-action')}
              >{t`Cancel the action`}</Button>
            )}
            <Button
              size='xs'
              variant='subtle'
              disabled={busy || expired}
              onClick={() => void act('repeat')}
            >{t`Read back`}</Button>
          </Group>
        )}
        {error && <Alert color='red'>{error}</Alert>}
      </Stack>
    </Card>
  );
}
