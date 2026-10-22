import { t } from '@lingui/core/macro';
import { ActionIcon, Box, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconPlayerPause, IconPlayerPlay } from '@tabler/icons-react';
import { type ReactNode, createContext, use, useState } from 'react';
import tokens from '../OperationalTokens.module.css';
import visual from './mimicVisuals.module.css';

const Motion = createContext({ paused: true, reduced: true, toggle: () => {} });
export function MimicMotion({ children }: { children: ReactNode }) {
  const reduced = useMediaQuery('(prefers-reduced-motion: reduce)', true);
  const [chosenPause, setPause] = useState(false);
  const paused = reduced || chosenPause;
  return (
    <Motion
      value={{ paused, reduced, toggle: () => setPause((value) => !value) }}
    >
      <Box
        data-mimic-motion
        data-motion-paused={paused}
        className={`${visual.motion} ${tokens.warning}`}
      >
        {children}
      </Box>
    </Motion>
  );
}
export function MimicMotionControl() {
  const { paused, reduced, toggle } = use(Motion);
  const label = reduced
    ? t`Animations paused by reduced motion preference`
    : paused
      ? t`Resume animations`
      : t`Pause animations`;
  return (
    <Tooltip label={label} events={{ hover: true, focus: true, touch: true }}>
      <ActionIcon
        variant='default'
        size={36}
        className={visual.motionControl}
        aria-label={t`Pause animations`}
        aria-pressed={paused}
        disabled={reduced}
        onClick={toggle}
      >
        {paused ? <IconPlayerPlay size={16} /> : <IconPlayerPause size={16} />}
      </ActionIcon>
    </Tooltip>
  );
}
/** The outer ring persists without motion; acknowledgement removes that ring. */
export function AlarmAttentionRings({
  x,
  y,
  radius,
  color,
  attention,
  scale = 1
}: {
  x: number;
  y: number;
  radius: number;
  color: string;
  attention: 'unacknowledged' | 'acknowledged';
  scale?: number;
}) {
  return (
    <g fill='none' stroke={color}>
      {attention === 'unacknowledged' && (
        <circle
          data-unacknowledged-ring
          cx={x}
          cy={y}
          r={(radius + 2.5) * scale}
          strokeWidth={scale}
        />
      )}
      <circle
        cx={x}
        cy={y}
        r={radius * scale}
        strokeWidth={2 * scale}
        className={visual.attention}
        data-attention={attention}
      />
    </g>
  );
}
