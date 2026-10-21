import { t } from '@lingui/core/macro';
import {
  Pagination,
  Paper,
  Stack,
  Table,
  Text,
  useComputedColorScheme,
  useMantineTheme
} from '@mantine/core';
import { useEffect, useState } from 'react';
import { MetaText } from '../../health/common';
import type { WindowInfo } from '../SensorGroupCard';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import { ObservedAt, qualityLabel } from '../../health/common';
import { useHistoryReadFailures } from '../HistoryReadContext';
import { Sparkline } from '../KpiStrip';
import { formatValue } from '../format';
import type { ResolvedParameter } from '../resolve';

/**
 * Signals the catalogue does not know, in a compact table so nothing mapped
 * is hidden. A tag that turns up here is a candidate for a catalogue entry.
 */
export function OtherSection({
  parameters,
  series,
  window
}: Readonly<{
  parameters: ResolvedParameter[];
  series: Map<number, SeriesEntry>;
  window?: WindowInfo;
}>) {
  const failed = useHistoryReadFailures();
  const theme = useMantineTheme();
  const scheme = useComputedColorScheme('light');
  const [page, setPage] = useState(1);
  const identity = parameters.map((p) => p.id).join(',');
  useEffect(() => setPage(1), [identity]);
  const count = Math.ceil(parameters.length / 50);
  const currentPage = Math.min(page, Math.max(1, count));
  if (parameters.length === 0) {
    return null;
  }
  return (
    <Paper withBorder p={0} component='section'>
      <Stack gap='xs' p='sm'>
        <MetaText size='sm'>{t`Showing ${(currentPage - 1) * 50 + 1}–${Math.min(currentPage * 50, parameters.length)} of ${parameters.length}`}</MetaText>
        <Pagination
          hideWithOnePage
          getControlProps={(control) => ({
            'aria-label': {
              first: t`First channel page`,
              previous: t`Previous channel page`,
              next: t`Next channel page`,
              last: t`Last channel page`
            }[control]
          })}
          color={`${theme.primaryColor}.${scheme === 'dark' ? 8 : 7}`}
          autoContrast
          value={currentPage}
          onChange={setPage}
          total={count}
          aria-label={t`Other channels pages`}
        />
      </Stack>
      <Table.ScrollContainer
        minWidth={600}
        scrollAreaProps={{
          viewportProps: {
            tabIndex: 0,
            role: 'region',
            'aria-label': t`Other channels; scroll for all columns`
          }
        }}
      >
        <Table striped highlightOnHover aria-label={t`Other channels`}>
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
            {parameters
              .slice((currentPage - 1) * 50, currentPage * 50)
              .map((p) => {
                const entry = series.get(p.signal.binding_id);
                const value = p.signal.value;
                return (
                  <Table.Tr key={p.id}>
                    <Table.Td>
                      <Text size='sm'>{p.label}</Text>
                      <MetaText size='xs'>{p.signal.external_key}</MetaText>
                    </Table.Td>
                    <Table.Td>
                      <Text size='sm' fw={500}>
                        {typeof value === 'number'
                          ? formatValue(
                              value,
                              p.definition.decimals,
                              p.signal.unit
                            )
                          : value === null || value === undefined
                            ? t`Unavailable · no observation`
                            : String(value)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <ObservedAt
                        observedAt={p.signal.observed_at}
                        stale={p.signal.stale}
                        thresholdSeconds={p.signal.freshness_threshold_seconds}
                      />
                    </Table.Td>
                    <Table.Td>
                      {failed.bindings.has(p.signal.binding_id) ? (
                        <Text size='xs'>{t`History read failed; coverage unknown.`}</Text>
                      ) : entry ? (
                        <Sparkline entry={entry} window={window} />
                      ) : failed.missing ? (
                        <Text size='xs'>{t`History has not been returned for this window.`}</Text>
                      ) : null}
                    </Table.Td>
                    <Table.Td>
                      <Text size='sm'>{qualityLabel(p.signal.quality)}</Text>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

export default OtherSection;
