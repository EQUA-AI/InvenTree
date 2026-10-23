import { t } from '@lingui/core/macro';
import {
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import {
  InfoTooltip,
  MissingValue
} from '../../../components/items/ValueTooltip';

/** Availability needs validated operating-time and downtime history. No such read is connected. */
export function MachineHistoryPanel() {
  return (
    <Paper withBorder p='md' radius='md' data-testid='machine-history'>
      <Stack gap='sm'>
        <Group justify='space-between'>
          <Title order={4}>{t`History`}</Title>
          <Group gap={6}>
            <Text size='sm' c='dimmed'>{t`Historical data unavailable`}</Text>
            <InfoTooltip
              label={t`Validated operating-time and downtime history is not connected. Current placement and signal trends do not establish historical availability.`}
            />
          </Group>
        </Group>
        <Select
          aria-label={t`History period`}
          placeholder='—'
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
              <MissingValue
                label={label}
                reason={t`Historical data unavailable`}
              />
            </Stack>
          ))}
        </SimpleGrid>
      </Stack>
    </Paper>
  );
}
