import { t } from '@lingui/core/macro';
import { Alert, List, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

/**
 * Prominent, first-thing-you-see alert listing exactly why a packet cannot
 * advance. Uses the backend blocker reasons verbatim so the UI matches
 * enforcement.
 */
export function SafetyBlockerAlert({
  blockers
}: Readonly<{
  blockers: Array<{ pk: number; name: string; reason: string }>;
}>) {
  if (!blockers || blockers.length === 0) {
    return null;
  }

  return (
    <Alert
      color='red'
      icon={<IconAlertTriangle />}
      title={t`This packet cannot advance until ${blockers.length} safety gate(s) are satisfied.`}
    >
      <List size='sm' spacing={4}>
        {blockers.map((blocker) => (
          <List.Item key={blocker.pk}>
            <Text span fw={600}>
              {blocker.name}
            </Text>
            {`: ${blocker.reason}`}
          </List.Item>
        ))}
      </List>
    </Alert>
  );
}
