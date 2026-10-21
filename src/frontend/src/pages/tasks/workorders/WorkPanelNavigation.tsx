import type { OperationalAuthority } from '@lib/types/OperationalAuthority';
import { t } from '@lingui/core/macro';
import { Anchor, Box, Tabs, Text } from '@mantine/core';
import { IconShieldQuestion } from '@tabler/icons-react';
import { type Ref, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import classes from './workCompanion.module.css';
import { type WorkPanel, workPanelParams } from './workPanelState';

/** One native tab list: desktop strip and safe-area-aware phone dock. */
export function WorkPanelNavigation({
  panels,
  reference,
  navigationRef,
  authority,
  inline = false
}: Readonly<{
  panels: readonly { name: WorkPanel; label: string }[];
  reference: string;
  navigationRef: Ref<HTMLDivElement>;
  authority?: OperationalAuthority;
  inline?: boolean;
}>) {
  const [params] = useSearchParams();
  const [now, setNow] = useState(Date.now);
  const physical =
    authority?.rule === 'operational_authority.v1'
      ? authority.physical.filter((record) => record.current)
      : [];
  // Match Plan & Safety: expiry removes currency even before the next read.
  useEffect(() => {
    const expiry = Math.min(
      ...physical
        .map((record) => Date.parse(record.effective_until ?? ''))
        .filter((time) => Number.isFinite(time) && time > now)
    );
    const updateClock = () => setNow(Date.now());
    const timer = Number.isFinite(expiry)
      ? window.setTimeout(
          updateClock,
          Math.min(2_147_483_647, Math.max(0, expiry - Date.now()))
        )
      : undefined;
    document.addEventListener('visibilitychange', updateClock);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', updateClock);
    };
  }, [authority, now]);
  const expired = physical.some(
    (record) =>
      record.effective_state === 'expired' ||
      (record.effective_until !== null &&
        Date.parse(record.effective_until) <= now)
  );
  const notYetEffective = physical.some(
    (record) => Date.parse(record.effective_from) > now
  );
  const awaitingVerification = physical.some(
    (record) => record.effective_state === 'awaiting_verification'
  );
  const outsideWindow = physical.some(
    (record) =>
      record.effective_state !== 'within_window' ||
      Date.parse(record.effective_from) > now
  );
  const label = !physical.length
    ? t`Physical authority not supplied`
    : expired
      ? t`Physical authority recorded · expired`
      : notYetEffective
        ? t`Physical authority recorded · not yet effective`
        : awaitingVerification
          ? t`Physical authority recorded · awaiting independent verification`
          : outsideWindow
            ? t`Physical authority recorded · current checks required`
            : t`Physical authority recorded`;
  return (
    <Box
      ref={navigationRef}
      className={classes.navigation}
      data-testid='work-panel-navigation'
      data-inline-navigation={inline || undefined}
    >
      {/* Recorded facts are not permission to work; Plan & Safety owns the checks. */}
      <Anchor
        replace
        component={Link}
        to={`?${workPanelParams(params, 'plan')}`}
        className={classes.boundary}
        data-testid='work-phone-boundary'
      >
        <IconShieldQuestion size={20} aria-hidden />
        <Box>
          <Text size='xs' fw={600}>
            {reference} · {label}
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
