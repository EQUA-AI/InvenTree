import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';

export function DraftRecovery({
  title,
  message,
  draft,
  latest,
  onReview,
  onKeep,
  onLatest,
  onRetry,
  busy = false
}: Readonly<{
  title: string;
  message: string;
  draft: Record<string, string>;
  latest?: Record<string, string>;
  onReview?: () => void;
  onKeep?: () => void;
  onLatest?: () => void;
  onRetry?: () => void;
  busy?: boolean;
}>) {
  const fieldLabel = (key: string) =>
    (
      ({
        cause: t`Cause`,
        action: t`Action taken`,
        result: t`Result`,
        verification_summary: t`Verification summary`,
        filename: t`Filename`,
        comment: t`Comment`,
        link: t`Link`
      }) as Record<string, string>
    )[key] ?? key;
  return (
    <Alert
      color='orange'
      title={title}
      data-testid='draft-recovery'
      aria-live='polite'
    >
      <Stack gap='sm'>
        <Text size='sm'>{message}</Text>
        <SimpleGrid cols={{ base: 1, sm: latest ? 2 : 1 }}>
          {[
            { label: t`Retained draft`, values: draft },
            ...(latest
              ? [{ label: t`Latest saved record`, values: latest }]
              : [])
          ].map(({ label, values }) => (
            <Paper key={label} withBorder p='sm'>
              <Text fw={600} size='sm'>
                {label}
              </Text>
              {Object.entries(values).map(([key, value]) => (
                <Text
                  key={key}
                  size='sm'
                  style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}
                >
                  <Text span fw={500}>
                    {fieldLabel(key)}:{' '}
                  </Text>
                  {value || t`Not recorded`}
                </Text>
              ))}
            </Paper>
          ))}
        </SimpleGrid>
        <Group>
          {onReview && (
            <Button
              variant='light'
              onClick={onReview}
              disabled={busy}
            >{t`Review latest record`}</Button>
          )}
          {onRetry && (
            <Button
              variant='light'
              onClick={onRetry}
              disabled={busy}
            >{t`Check original save`}</Button>
          )}
          {onKeep && (
            <Button
              variant='default'
              onClick={onKeep}
              disabled={busy}
            >{t`Keep draft on reviewed version`}</Button>
          )}
          {onLatest && (
            <Button
              variant='default'
              onClick={onLatest}
              disabled={busy}
            >{t`Use latest saved values`}</Button>
          )}
        </Group>
      </Stack>
    </Alert>
  );
}
