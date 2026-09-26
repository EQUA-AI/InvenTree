import { t } from '@lingui/core/macro';
import { Paper, Table, Text } from '@mantine/core';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import { ObservedAt, qualityLabel } from '../../health/common';
import { Sparkline } from '../KpiStrip';
import { formatValue } from '../format';
import type { ResolvedParameter } from '../resolve';

/**
 * Signals the catalogue does not know, in a compact table so nothing mapped
 * is hidden. A tag that turns up here is a candidate for a catalogue entry.
 */
export function OtherSection({
  parameters,
  series
}: Readonly<{
  parameters: ResolvedParameter[];
  series: Map<number, SeriesEntry>;
}>) {
  if (parameters.length === 0) {
    return null;
  }
  return (
    <Paper withBorder radius='md' p={0} style={{ overflowX: 'auto' }}>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Signal`}</Table.Th>
            <Table.Th>{t`Value`}</Table.Th>
            <Table.Th>{t`Observed`}</Table.Th>
            <Table.Th>{t`Window`}</Table.Th>
            <Table.Th>{t`Quality`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {parameters.map((p) => {
            const entry = series.get(p.signal.binding_id);
            const value = p.signal.value;
            return (
              <Table.Tr key={p.id}>
                <Table.Td>
                  <Text size='sm'>{p.label}</Text>
                  <Text size='xs' c='dimmed'>
                    {p.signal.external_key}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size='sm' fw={500}>
                    {typeof value === 'number'
                      ? formatValue(value, p.definition.decimals, p.signal.unit)
                      : value === null || value === undefined
                        ? '—'
                        : String(value)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <ObservedAt
                    observedAt={p.signal.observed_at}
                    stale={p.signal.stale}
                  />
                </Table.Td>
                <Table.Td>
                  {entry ? <Sparkline entry={entry} /> : null}
                </Table.Td>
                <Table.Td>
                  <Text size='sm'>{qualityLabel(p.signal.quality)}</Text>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Paper>
  );
}

export default OtherSection;
