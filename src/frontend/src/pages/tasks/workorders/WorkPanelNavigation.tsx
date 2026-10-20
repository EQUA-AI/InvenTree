import { t } from '@lingui/core/macro';
import { Anchor, Box, Tabs, Text } from '@mantine/core';
import { IconShieldQuestion } from '@tabler/icons-react';
import type { Ref } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import classes from './workCompanion.module.css';
import { type WorkPanel, workPanelParams } from './workPanelState';

/** One native tab list: desktop strip and safe-area-aware phone dock. */
export function WorkPanelNavigation({
  panels,
  reference,
  navigationRef
}: Readonly<{
  panels: readonly { name: WorkPanel; label: string }[];
  reference: string;
  navigationRef: Ref<HTMLDivElement>;
}>) {
  const [params] = useSearchParams();
  return (
    <Box
      ref={navigationRef}
      className={classes.navigation}
      data-testid='work-panel-navigation'
    >
      {/* P21 supplies no physical-authority record. Gates, lifecycle, a saved
          scope and a successful maintenance update cannot establish it. */}
      <Anchor
        component={Link}
        to={`?${workPanelParams(params, 'plan')}`}
        className={classes.boundary}
        data-testid='work-phone-boundary'
      >
        <IconShieldQuestion size={20} aria-hidden />
        <Box>
          <Text size='xs' fw={600}>
            {reference} · {t`Physical authority not supplied`}
          </Text>
          <Text size='xs'>
            {t`View Plan & Safety. This does not authorize work.`}
          </Text>
        </Box>
      </Anchor>
      <Tabs.List aria-label={t`Work workflow`} className={classes.tabList}>
        {panels.map((panel) => (
          <Tabs.Tab key={panel.name} value={panel.name} className={classes.tab}>
            {panel.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Box>
  );
}
