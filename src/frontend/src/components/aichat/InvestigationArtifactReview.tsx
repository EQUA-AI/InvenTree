import { t } from '@lingui/core/macro';
import { Stack, Text } from '@mantine/core';

const labels: Record<string, string> = {
  text: 'Observation',
  description: 'Description',
  readings: 'Readings',
  results: 'Results',
  observed_at: 'Observed at',
  conditions: 'Conditions',
  sample: 'Sample',
  method: 'Method',
  source: 'Source',
  status: 'Result status'
};

function valueText(value: unknown): string {
  if (value == null) return t`Not recorded`;
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
            {labels[key] ?? key}:{' '}
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
                      {String(row.name)}: {valueText(row.value)}{' '}
                      {String(row.unit)}
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
            : valueText(value)}
        </Text>
      ))}
    </Stack>
  );
}
