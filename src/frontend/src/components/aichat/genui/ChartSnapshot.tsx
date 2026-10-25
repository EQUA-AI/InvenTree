import type { PresentationChartV2 } from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import { Box, Table, Text } from '@mantine/core';
import { useId } from 'react';

const colors = ['blue', 'teal', 'orange', 'violet', 'cyan', 'pink'];
const color = (index: number) =>
  `var(--mantine-color-${colors[index % colors.length]}-6)`;

/** All geometry comes from the persisted values. No fetch, code or model execution. */
export function ChartSnapshot({ data }: { data: PresentationChartV2 }) {
  const titleId = useId();
  const values = data.points.flatMap((point) =>
    point.value === null ? [] : [point.value]
  );
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const y = (value: number) => 185 - ((value - min) / span) * 150;
  const x = (index: number) =>
    55 +
    (data.points.length === 1 ? 240 : (index / (data.points.length - 1)) * 480);
  const total = values.reduce((sum, value) => sum + value, 0);
  let angle = -Math.PI / 2;
  let line = '';
  let gap = true;
  for (const [index, point] of data.points.entries()) {
    if (point.value === null) {
      gap = true;
      continue;
    }
    line += `${gap ? 'M' : 'L'}${x(index)},${y(point.value)} `;
    gap = false;
  }
  const percentage = (value: number) =>
    new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(
      (value / total) * 100
    );
  return (
    <Box
      component='figure'
      m={0}
      mt='sm'
      data-testid='presentation-chart'
      data-chart-type={data.chart_type}
      aria-labelledby={titleId}
    >
      <Text component='figcaption' id={titleId} fw={600}>
        {data.title}
      </Text>
      <Text size='sm' fw={600}>
        {data.origin === 'user_supplied'
          ? t`Synthetic / user-provided data`
          : t`Recorded data`}
      </Text>
      <Text size='xs' c='dimmed'>
        {data.definition}
      </Text>
      <Text size='xs' c='dimmed'>
        {data.scope_label}
      </Text>
      <svg
        viewBox='0 0 600 240'
        width='100%'
        role='img'
        aria-label={t`Chart of the values in the supporting table.`}
      >
        {data.chart_type === 'pie' ? (
          data.points.map((point, index) => {
            const value = point.value ?? 0;
            const start = angle;
            const size = (value / total) * Math.PI * 2;
            angle += size;
            if (value === 0) return null;
            return size >= Math.PI * 2 - 1e-10 ? (
              <circle
                key={point.label}
                cx={300}
                cy={118}
                r={90}
                fill={color(index)}
              />
            ) : (
              <path
                key={point.label}
                d={`M300,118 L${300 + 90 * Math.cos(start)},${118 + 90 * Math.sin(start)} A90,90 0 ${size > Math.PI ? 1 : 0},1 ${300 + 90 * Math.cos(angle)},${118 + 90 * Math.sin(angle)} Z`}
                fill={color(index)}
                stroke='var(--mantine-color-body)'
                strokeWidth={1}
              />
            );
          })
        ) : (
          <>
            <path
              d={`M45,25V190H555 M45,${y(0)}H555`}
              fill='none'
              stroke='currentColor'
              opacity={0.4}
            />
            <text
              x={40}
              y={y(max)}
              textAnchor='end'
              fontSize={11}
              fill='currentColor'
            >
              {max}
            </text>
            <text
              x={40}
              y={y(min)}
              textAnchor='end'
              fontSize={11}
              fill='currentColor'
            >
              {min}
            </text>
            <text
              x={300}
              y={236}
              textAnchor='middle'
              fontSize={12}
              fill='currentColor'
            >
              {data.x_label}
            </text>
            <text
              x={12}
              y={115}
              textAnchor='middle'
              transform='rotate(-90 12 115)'
              fontSize={12}
              fill='currentColor'
            >
              {data.y_label}
            </text>
            {data.chart_type === 'line' && (
              <path d={line} stroke={color(0)} strokeWidth={2} fill='none' />
            )}
            {data.points.map((point, index) => {
              const width = 480 / data.points.length;
              const center =
                data.chart_type === 'bar'
                  ? 55 + width * (index + 0.5)
                  : x(index);
              return (
                <g key={point.label}>
                  {point.value !== null &&
                    (data.chart_type === 'bar' ? (
                      <>
                        <rect
                          x={center - width * 0.36}
                          y={Math.min(y(point.value), y(0))}
                          width={width * 0.72}
                          height={Math.abs(y(point.value) - y(0))}
                          fill={color(index)}
                        />
                        {point.value === 0 && (
                          <circle
                            cx={center}
                            cy={y(0)}
                            r={2}
                            fill={color(index)}
                          />
                        )}
                      </>
                    ) : (
                      <circle
                        cx={center}
                        cy={y(point.value)}
                        r={3}
                        fill={color(0)}
                      />
                    ))}
                  {index % Math.ceil(data.points.length / 6) === 0 && (
                    <text
                      x={center}
                      y={210}
                      textAnchor='middle'
                      fontSize={11}
                      fill='currentColor'
                    >
                      {point.label.length > 14
                        ? `${point.label.slice(0, 12)}…`
                        : point.label}
                    </text>
                  )}
                </g>
              );
            })}
          </>
        )}
      </svg>
      <Box
        component='section'
        tabIndex={0}
        aria-label={t`Chart values`}
        style={{ maxHeight: 240, overflow: 'auto' }}
      >
        <Table>
          <Table.Caption>
            {t`Supporting data`} · {data.unit}
          </Table.Caption>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{data.x_label}</Table.Th>
              <Table.Th>{data.y_label}</Table.Th>
              {data.chart_type === 'pie' && (
                <Table.Th>{t`Percentage`}</Table.Th>
              )}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.points.map((point, index) => (
              <Table.Tr key={point.label}>
                <Table.Td>
                  <Box
                    component='span'
                    aria-hidden
                    style={{
                      display: 'inline-block',
                      width: 8,
                      height: 8,
                      borderRadius: 2,
                      background: color(data.chart_type === 'line' ? 0 : index),
                      marginInlineEnd: 6
                    }}
                  />
                  {point.label}
                </Table.Td>
                <Table.Td>
                  {point.value === null ? t`Unavailable` : String(point.value)}
                </Table.Td>
                {data.chart_type === 'pie' && (
                  <Table.Td>{percentage(point.value ?? 0)}%</Table.Td>
                )}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Box>
      {data.origin === 'recorded' && (
        <Text size='xs'>
          {t`Complete population`}: {data.population_count}
        </Text>
      )}
      <Text size='xs' c='dimmed'>
        {t`Source`}: {data.source} · {data.as_of} · {t`Snapshot revision`}:{' '}
        {data.source_revision}
      </Text>
    </Box>
  );
}
