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
import { useState } from 'react';

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
  onAnswer
}: Readonly<{
  payload: QuestionPayload;
  armed: boolean;
  resolution?: QuestionResolution;
  onAnswer: (text: string) => void;
}>) {
  const [answered, setAnswered] = useState(false);
  const [otherText, setOtherText] = useState('');

  const active = armed && !answered;

  const submit = (text: string) => {
    if (!active || !text.trim()) return;
    setAnswered(true);
    onAnswer(text.trim());
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
                onClick={() => submit(option.label)}
                disabled={!active}
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
              onChange={(event) => setOtherText(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') submit(otherText);
              }}
              aria-label='question-other-input'
            />
            <ActionIcon
              size='sm'
              variant='light'
              onClick={() => submit(otherText)}
              disabled={!otherText.trim()}
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
                : answered
                  ? t`Answered in chat`
                  : t`No longer active — just type your answer`}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}
