import { t } from '@lingui/core/macro';
import { Card, Stack, Text, Title } from '@mantine/core';
import dayjs from 'dayjs';
import type { ReactNode } from 'react';

export function recordedTime(value: string | null | undefined): string {
  return value && dayjs(value).isValid()
    ? dayjs(value).format('DD MMM YYYY, HH:mm Z')
    : t`Not recorded`;
}

export function ExecutionFact({
  label,
  children
}: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <Stack gap={0}>
      <Text size='xs' c='dimmed'>
        {label}
      </Text>
      <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
        {children === null || children === undefined || children === ''
          ? t`Not recorded`
          : children}
      </Text>
    </Stack>
  );
}

export function ExecutionSection({
  title,
  children,
  testId
}: Readonly<{ title: string; children: ReactNode; testId?: string }>) {
  return (
    <Card
      withBorder
      padding='md'
      data-testid={testId}
      style={{ overflowWrap: 'anywhere' }}
    >
      <Stack gap='sm'>
        <Title order={4}>{title}</Title>
        {children}
      </Stack>
    </Card>
  );
}

export const executionBadgeStyles = {
  root: { height: 'auto', paddingBlock: 2, maxWidth: '100%' },
  label: { whiteSpace: 'normal' as const, overflow: 'visible' }
};
