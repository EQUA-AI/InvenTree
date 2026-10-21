import { t } from '@lingui/core/macro';
import { Stack, Text } from '@mantine/core';
import { formatDate } from '../../defaults/formatters';

function label(key: string): string {
  const labels: Record<string, string> = {
    text: t`Observation`,
    description: t`Description`,
    readings: t`Readings`,
    results: t`Results`,
    observed_at: t`Observed at`,
    recording_started_at: t`Recording started`,
    recording_duration_ms: t`Recording duration (ms)`,
    conditions: t`Conditions`,
    sample: t`Sample`,
    method: t`Method`,
    source: t`Source`,
    status: t`Result status`
  };
  return labels[key] ?? key;
}

function valueText(value: unknown, key?: string): string {
  if (value == null || value === '') return t`Not recorded`;
  if (
    (key === 'observed_at' || key === 'recording_started_at') &&
    typeof value === 'string'
  )
    return formatDate(value, { showTime: true });
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** All proposed facts are visible before the existing approval action. */
export function InvestigationArtifactReview({
  payload
}: Readonly<{ payload: unknown }>) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload))
    return null;
  return (
    <Stack gap={3} style={{ overflowWrap: 'anywhere' }}>
      {Object.entries(payload).map(([key, value]) => (
        <Text key={key} size='sm'>
          <Text span fw={600}>
            {label(key)}:{' '}
          </Text>
          {Array.isArray(value)
            ? value.map((row, i) => {
                if (
                  row &&
                  typeof row === 'object' &&
                  'name' in row &&
                  'value' in row &&
                  'unit' in row
                ) {
                  const extra = Object.fromEntries(
                    Object.entries(row).filter(
                      ([k]) => !['name', 'value', 'unit'].includes(k)
                    )
                  );
                  return (
                    <Text component='span' display='block' key={i}>
                      {String(row.name)}: {valueText(row.value)}
                      {row.value != null && row.value !== '' && row.unit
                        ? ` ${String(row.unit)}`
                        : ''}
                      {Object.keys(extra).length
                        ? ` · ${valueText(extra)}`
                        : ''}
                    </Text>
                  );
                }
                return (
                  <Text component='span' display='block' key={i}>
                    {valueText(row)}
                  </Text>
                );
              })
            : valueText(value, key)}
        </Text>
      ))}
    </Stack>
  );
}
