import { t } from '@lingui/core/macro';
import {
  Accordion,
  Button,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useEffect, useMemo, useState } from 'react';
import monitoring from '../monitoring.module.css';
import {
  PERFORMANCE_SECTIONS,
  type PerformanceSection,
  matchesChannel,
  sectionOf
} from './channelNavigation';
import { unitLabel } from './format';
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
  onShowAll,
  collapsed = false
}: Readonly<{
  parameters: ResolvedParameter[];
  search: string;
  onSearch: (value: string) => void;
  section: string;
  onSection: (value: string) => void;
  onShowAll: () => void;
  collapsed?: boolean;
}>) {
  const phone = useMediaQuery('(max-width: 47.99em)');
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (collapsed) setOpen(false);
  }, [collapsed]);
  const matched = useMemo(
    () => parameters.filter((p) => matchesChannel(p, search)),
    [parameters, search]
  );
  const content = (
    <Stack gap='xs'>
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
          classNames={{ input: monitoring.input, option: monitoring.option }}
          label={t`Section`}
          value={search.trim() ? 'search' : section}
          allowDeselect={false}
          onOptionSubmit={(value) =>
            value === 'all'
              ? onShowAll()
              : value !== 'search' && onSection(value)
          }
          data={[
            { value: 'all', label: t`Full overview` },
            ...PERFORMANCE_SECTIONS.filter((key) =>
              parameters.some((p) => sectionOf(p) === key)
            ).map((key) => ({
              value: key,
              label: `${sectionTitle(key)} · ${parameters.filter((p) => sectionOf(p) === key).length}`
            })),
            ...(search.trim()
              ? [
                  {
                    value: 'search',
                    label: t`Search results · ${matched.length}`
                  }
                ]
              : [])
          ]}
        />
      </SimpleGrid>

      {!matched.length && (
        <Text size='sm'>{t`No matching channels. Clear the search or select Full overview.`}</Text>
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
              key={p.id}
              className={monitoring.link}
              size='compact-xs'
              h='auto'
              py={4}
              style={{ flexShrink: 0 }}
              variant='subtle'
              justify='flex-start'
              styles={{
                inner: { height: 'auto' },
                label: {
                  // Wrapping results need a full line box, not compact-button cap height.
                  textBoxTrim: 'none',
                  lineHeight: 1.5,
                  height: 'auto',
                  whiteSpace: 'normal',
                  textAlign: 'start',
                  overflowWrap: 'anywhere'
                }
              }}
              onClick={() => onSection(sectionOf(p))}
            >
              {p.label} · {p.signal.external_key} · {unitLabel(p.signal.unit)}
            </Button>
          ))}
        </Stack>
      )}
    </Stack>
  );
  return (
    <div data-testid='channel-navigator'>
      {phone || collapsed ? (
        <Accordion
          value={open ? 'channels' : null}
          onChange={(value) => setOpen(value === 'channels')}
          keepMounted
          keepMountedMode='display-none'
        >
          <Accordion.Item value='channels'>
            <Accordion.Control>
              {t`Channels`} · {parameters.length}
            </Accordion.Control>
            <Accordion.Panel>{content}</Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      ) : (
        <Paper withBorder p='sm'>
          {content}
        </Paper>
      )}
    </div>
  );
}
