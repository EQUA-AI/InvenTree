import { t } from '@lingui/core/macro';
import {
  Button,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useMemo } from 'react';
import monitoring from '../monitoring.module.css';
import {
  PERFORMANCE_SECTIONS,
  type PerformanceSection,
  matchesChannel,
  sectionOf
} from './channelNavigation';
import { categoryLabel } from './parameters';
import type { ResolvedParameter } from './resolve';

export function sectionTitle(section: PerformanceSection) {
  return section === 'operation'
    ? t`Speed, operation and hydraulics`
    : categoryLabel(section);
}

export function ChannelNavigator({
  parameters,
  search,
  onSearch,
  section,
  onSection,
  onShowAll
}: Readonly<{
  parameters: ResolvedParameter[];
  search: string;
  onSearch: (value: string) => void;
  section: string;
  onSection: (value: string) => void;
  onShowAll: () => void;
}>) {
  const matched = useMemo(
    () => parameters.filter((p) => matchesChannel(p, search)),
    [parameters, search]
  );
  return (
    <Paper withBorder p='sm' data-testid='channel-navigator'>
      <Stack gap='xs'>
        <Group justify='space-between'>
          <Text size='sm' fw={600}>
            {t`Channel overview`} · {parameters.length}
          </Text>
          <Button
            size='compact-xs'
            variant='default'
            onClick={onShowAll}
          >{t`Show all`}</Button>
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            classNames={{ input: monitoring.input }}
            label={t`Find a channel`}
            maxLength={200}
            placeholder={t`Name, tag or unit`}
            value={search}
            onChange={(event) => onSearch(event.currentTarget.value)}
          />
          <Select
            classNames={{ input: monitoring.input }}
            label={t`Section`}
            value={section}
            allowDeselect={false}
            onChange={(value) => onSection(value ?? 'all')}
            data={[
              { value: 'all', label: t`Full overview` },
              ...PERFORMANCE_SECTIONS.map((key) => ({
                value: key,
                label: sectionTitle(key)
              }))
            ]}
          />
        </SimpleGrid>
        <Text
          size='sm'
          className={monitoring.meta}
        >{t`Search by name, tag or unit opens native chart groups; it keeps the history scope and chart selections.`}</Text>
        <Group gap='xs'>
          {PERFORMANCE_SECTIONS.map((key) => {
            const count = matched.filter((p) => sectionOf(p) === key).length;
            return (
              count > 0 && (
                <Button
                  key={key}
                  size='compact-xs'
                  variant={section === key ? 'light' : 'subtle'}
                  aria-pressed={section === key}
                  onClick={() => onSection(key)}
                >
                  {sectionTitle(key)} · {count}
                </Button>
              )
            );
          })}
        </Group>
        {matched.length === 0 && (
          <Text size='sm'>{t`No matching channels. Clear the search or Show all.`}</Text>
        )}
        {search.trim() && (
          <Stack
            gap={2}
            mah={180}
            style={{ overflowY: 'auto' }}
            aria-label={t`Matching channels`}
          >
            {matched.map((p) => (
              <Button
                className={monitoring.link}
                key={p.id}
                size='compact-xs'
                h='auto'
                mih={28}
                py={4}
                variant='subtle'
                justify='flex-start'
                styles={{
                  label: {
                    whiteSpace: 'normal',
                    lineHeight: 1.4,
                    textAlign: 'start',
                    overflowWrap: 'anywhere'
                  }
                }}
                onClick={() => onSection(sectionOf(p))}
              >
                {p.label} · {p.signal.external_key} ·{' '}
                {p.signal.unit || t`no unit`}
              </Button>
            ))}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}
