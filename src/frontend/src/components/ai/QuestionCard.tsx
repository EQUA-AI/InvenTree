import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Group,
  Paper,
  Stack,
  Text,
  TextInput,
  UnstyledButton
} from '@mantine/core';
import { IconArrowUp, IconHelpCircle } from '@tabler/icons-react';
import { useRef, useState } from 'react';
import { useVoiceDecisionState } from '../../states/VoiceDecisionState';
import { shouldSendOnEnter } from '../nav/chatComposer';

import type {
  QuestionPayload,
  QuestionResolution
} from '../../hooks/UseAIChat';

/**
 * Structured question card (S23).
 *
 * Renders a turn that COMPLETED by asking: 2-4 server-derived options plus a
 * host-rendered "Other" free-text row (the server never emits one). Options
 * send their LABEL as ordinary user text — the server validates the answer
 * against its own persisted record, never against anything echoed from here.
 * The card is actionable exactly once while armed; afterwards it freezes,
 * showing the outcome, and is never answerable again (it survives reload in
 * its frozen state via the /threads projection).
 */
export function QuestionCard({
  payload,
  armed,
  resolution,
  answeredExternally,
  onAnswer
}: Readonly<{
  payload: QuestionPayload;
  armed: boolean;
  resolution?: QuestionResolution;
  /** Answered from this client by a typed message rather than a click. */
  answeredExternally?: boolean;
  /** True only after the shared send path has locally admitted this answer. */
  onAnswer: (text: string) => Promise<boolean>;
}>) {
  const [answered, setAnswered] = useState(false);
  const [otherText, setOtherText] = useState('');
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [notSent, setNotSent] = useState(false);

  const decisionArmed = useVoiceDecisionState(
    (state) => state.decision?.state === 'presented'
  );
  const active = armed && !answered && !decisionArmed;

  const submit = async (text: string) => {
    if (!active || pendingRef.current || !text.trim()) return;
    pendingRef.current = true;
    setPending(true);
    setNotSent(false);
    try {
      const admitted = await onAnswer(text.trim());
      if (admitted) setAnswered(true);
      else setNotSent(true);
    } catch {
      setNotSent(true);
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  const selectedId = resolution?.selected_option_id;

  return (
    <Paper
      p='sm'
      radius='md'
      withBorder
      data-testid={active ? 'question-card' : 'question-card-frozen'}
      style={{ opacity: active ? 1 : 0.75 }}
    >
      <Stack gap='xs'>
        <Group gap={6}>
          <IconHelpCircle size={16} aria-hidden />
          <Text size='sm' fw={600}>
            {payload.question_text}
          </Text>
        </Group>
        <Stack gap={6}>
          {payload.options.map((option) => {
            const isSelected = selectedId === option.id;
            return (
              <UnstyledButton
                key={option.id}
                onClick={() => void submit(option.label)}
                disabled={!active || pending}
                data-testid={`question-option-${option.id}`}
              >
                <Paper
                  px='sm'
                  py={6}
                  radius='xl'
                  withBorder
                  style={{
                    borderColor: isSelected
                      ? 'var(--mantine-color-blue-5)'
                      : undefined,
                    cursor: active ? 'pointer' : 'default'
                  }}
                >
                  <Group gap='xs' wrap='nowrap'>
                    <Text size='sm' style={{ flex: 1 }}>
                      {option.label}
                      {option.description ? (
                        <Text component='span' size='xs' c='dimmed'>
                          {' '}
                          — {option.description}
                        </Text>
                      ) : null}
                    </Text>
                    {option.recommended && (
                      <Badge size='xs' variant='light'>
                        {t`Recommended`}
                      </Badge>
                    )}
                    {isSelected && (
                      <Badge size='xs' color='blue'>
                        {t`Selected`}
                      </Badge>
                    )}
                  </Group>
                </Paper>
              </UnstyledButton>
            );
          })}
        </Stack>
        {active ? (
          <Group gap='xs' wrap='nowrap'>
            <TextInput
              size='xs'
              style={{ flex: 1 }}
              placeholder={t`Other...`}
              value={otherText}
              disabled={pending}
              onChange={(event) => setOtherText(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (
                  shouldSendOnEnter({
                    key: event.key,
                    shiftKey: event.shiftKey,
                    isComposing: event.nativeEvent.isComposing,
                    keyCode: event.nativeEvent.keyCode
                  })
                ) {
                  event.preventDefault();
                  void submit(otherText);
                }
              }}
              aria-label='question-other-input'
            />
            <ActionIcon
              size='sm'
              variant='light'
              onClick={() => void submit(otherText)}
              loading={pending}
              disabled={pending || !otherText.trim()}
              aria-label='question-other-send'
            >
              <IconArrowUp size={14} />
            </ActionIcon>
          </Group>
        ) : (
          <Text size='xs' c='dimmed'>
            {resolution?.outcome === 'selected'
              ? t`Answered`
              : resolution?.outcome === 'declined'
                ? t`Declined`
                : answered || answeredExternally
                  ? t`Answered in chat`
                  : t`No longer active — just type your answer`}
          </Text>
        )}
        {active && (pending || notSent) && (
          <Text
            component='output'
            aria-live='polite'
            size='xs'
            c={notSent ? 'red' : 'dimmed'}
          >
            {pending
              ? t`Preparing answer…`
              : t`The answer was not sent. You can try again.`}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}
