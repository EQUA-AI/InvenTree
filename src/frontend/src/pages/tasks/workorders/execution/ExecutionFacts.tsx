import { t } from '@lingui/core/macro';
import { Card, Group, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import dayjs from 'dayjs';
import type { ReactNode } from 'react';
import {
  InfoTooltip,
  MissingValue
} from '../../../../components/items/ValueTooltip';

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
      <Text component='div' size='sm' style={{ overflowWrap: 'anywhere' }}>
        {children === null ||
        children === undefined ||
        children === '' ||
        children === t`Not recorded` ? (
          <MissingValue label={label} reason={t`No value recorded`} />
        ) : (
          children
        )}
      </Text>
    </Stack>
  );
}

export function ExecutionSection({
  title,
  children,
  testId,
  info
}: Readonly<{
  title: string;
  children: ReactNode;
  testId?: string;
  info?: string;
}>) {
  return (
    <Card
      withBorder
      padding='md'
      data-testid={testId}
      style={{ overflowWrap: 'anywhere' }}
    >
      <Stack gap='sm'>
        <Group justify='space-between' gap='xs' wrap='nowrap'>
          <Title order={2} size='h4'>
            {title}
          </Title>
          {info && <InfoTooltip label={info} />}
        </Group>
        {children}
      </Stack>
    </Card>
  );
}

export const executionBadgeStyles = {
  root: {
    height: 'auto',
    paddingBlock: 2,
    maxWidth: '100%',
    textTransform: 'none' as const
  },
  label: { whiteSpace: 'normal' as const, overflow: 'visible' }
};

/** Server says not preliminary, but the verifier and time are not projected. */
export function unattributedVerificationLabel(): string {
  return t`Verification recorded · verifier and time not supplied`;
}

/**
 * A warning that is not colour alone: icon, a translated prefix and weight.
 * Text keeps the primary text colour, so contrast follows the native theme.
 */
export function WarningText({
  children,
  size = 'sm'
}: Readonly<{ children: ReactNode; size?: 'xs' | 'sm' }>) {
  return (
    <Group
      gap={6}
      wrap='nowrap'
      align='flex-start'
      data-work-warning
      style={{ minWidth: 0 }}
    >
      <IconAlertTriangle
        size={size === 'xs' ? 14 : 16}
        aria-hidden
        style={{
          flexShrink: 0,
          marginTop: 2,
          color: 'var(--aimms-work-warning-icon, currentColor)'
        }}
      />
      <Text
        size={size}
        fw={600}
        component='span'
        style={{ overflowWrap: 'anywhere', minWidth: 0 }}
      >
        {t`Warning:`} {children}
      </Text>
    </Group>
  );
}

/** Native dependency types from tasks.KanbanCardDependency.TYPE_CHOICES. */
export function dependencyLabel(value: string): string {
  return (
    (
      {
        FS: t`Finish to start`,
        SS: t`Start to start`,
        FF: t`Finish to finish`,
        SF: t`Start to finish`
      } as Record<string, string>
    )[value] ?? t`Dependency`
  );
}
