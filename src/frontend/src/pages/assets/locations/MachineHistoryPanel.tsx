import { t } from '@lingui/core/macro';
import {
  Alert,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';

/** Availability needs validated operating-time and downtime history. No such read is connected. */
export function MachineHistoryPanel() {
  return (
    <Paper withBorder p='md' radius='md' data-testid='machine-history'>
      <Stack gap='sm'>
        <Title order={4}>{t`Scoped history`}</Title>
        <Alert color='gray' title={t`Historical data unavailable`}>
          {t`Validated operating-time and downtime history is not connected. Current placement and signal trends do not establish historical availability.`}
        </Alert>
        <Select
          aria-label={t`History period`}
          placeholder={t`Historical source required`}
          data={[]}
          disabled
        />
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          {[
            t`Downtime (machine-minutes)`,
            t`Observed planned time (machine-minutes)`,
            t`Measured availability`
          ].map((label) => (
            <Stack gap={2} key={label}>
              <Text size='sm' c='dimmed'>
                {label}
              </Text>
              <Text fw={600} aria-label={t`Unavailable`}>
                —
              </Text>
            </Stack>
          ))}
        </SimpleGrid>
      </Stack>
    </Paper>
  );
}
