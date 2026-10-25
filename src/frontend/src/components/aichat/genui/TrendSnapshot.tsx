import type { PresentationTrendV1 } from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import { Box, Table, Text } from '@mantine/core';
import { trendGeometry } from '../../../pages/assets/health/trendGeometry';

/** Historical timestamp geometry, explicit gaps and a complete accessible table. */
export function TrendSnapshot({ data }: { data: PresentationTrendV1 }) {
  const geometry = trendGeometry(
    data.samples.map((sample) => ({
      ...sample,
      quality: sample.quality === 'missing' ? 'bad' : sample.quality
    })),
    440,
    120
  );
  const qualities = {
    good: t`Good`,
    uncertain: t`Uncertain`,
    bad: t`Bad`,
    missing: t`Missing`
  };
  return (
    <Box component='figure' m={0} mt='sm' data-testid='presentation-trend'>
      <Text component='figcaption' fw={600} size='sm'>
        {t`Recorded trend`} · {data.unit}
      </Text>
      <Text size='xs' c='dimmed'>
        {data.source} · {data.as_of} · {data.timezone}
      </Text>
      <Text size='xs'>
        {data.samples[0].observed_at} – {data.samples.at(-1)?.observed_at}
      </Text>
      {geometry ? (
        <svg
          viewBox='-35 -10 485 155'
          width='100%'
          role='img'
          aria-label={t`Historical observations; values and quality are listed in the table.`}
        >
          <path
            d='M0,0V120H440'
            fill='none'
            stroke='currentColor'
            opacity={0.35}
          />
          <text x={-4} y={4} textAnchor='end' fontSize={10} fill='currentColor'>
            {geometry.max}
          </text>
          <text
            x={-4}
            y={120}
            textAnchor='end'
            fontSize={10}
            fill='currentColor'
          >
            {geometry.min}
          </text>
          <path
            d={geometry.path}
            fill='none'
            stroke='var(--mantine-primary-color-filled)'
            strokeWidth={2}
          />
          {geometry.points.map((point, index) => (
            <circle
              key={`${index}:${point.x}`}
              cx={point.x}
              cy={point.y}
              r={2}
              fill='var(--mantine-primary-color-filled)'
            />
          ))}
        </svg>
      ) : (
        <Text size='sm'>{t`No observations of sufficient quality to plot.`}</Text>
      )}
      <Text
        size='xs'
        c='dimmed'
      >{t`Gaps and uncertain observations are excluded from the line. The table preserves every observation.`}</Text>
      <Box
        component='section'
        style={{ maxHeight: 220, overflow: 'auto' }}
        tabIndex={0}
        aria-label={t`Historical observation values`}
      >
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t`Observation`}</Table.Th>
              <Table.Th>{data.unit}</Table.Th>
              <Table.Th>{t`Quality`}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.samples.map((sample) => (
              <Table.Tr key={sample.observed_at}>
                <Table.Td>{sample.observed_at}</Table.Td>
                <Table.Td>
                  {sample.value === null ? t`Unavailable` : sample.value}
                </Table.Td>
                <Table.Td>{qualities[sample.quality]}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Box>
      <Text size='xs' c='dimmed'>
        {t`Snapshot revision`}: {data.source_revision}
      </Text>
    </Box>
  );
}
