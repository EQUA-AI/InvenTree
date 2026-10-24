import { t } from '@lingui/core/macro';
import {
  Box,
  Button,
  Paper,
  Skeleton,
  Stack,
  Text,
  useComputedColorScheme,
  useMantineTheme
} from '@mantine/core';
import {
  type Ref,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState
} from 'react';
import { ValueTooltip } from '../../../components/items/ValueTooltip';
import { StatusBadge } from '../health/common';
import operations from '../operations.module.css';

import type {
  TwinController,
  TwinMode,
  TwinMotorAlarm,
  TwinPartId,
  TwinView
} from './twinScene.mjs';

/** One-shot camera actions; selection, mode and labels stay props. */
export interface EquipmentSceneHandle {
  setView(view: TwinView): void;
  focus(id: TwinPartId): void;
  reset(): void;
  /** A distance multiplier: below 1 moves closer. */
  zoom(factor: number): void;
}

/** R6 UI03 viewer height; the skeleton uses it too so nothing jumps. */
export const SCENE_HEIGHT = 'clamp(420px, 58vh, 680px)';

type Phase = 'loading' | 'ready' | 'failed';

/**
 * R6 P05: the authored Three.js reference scene behind a controlled React
 * boundary.
 *
 * The scene is loaded on demand and mounted once per `modelKey`; selection,
 * mode and labels are pushed into the running controller rather than
 * rebuilding it, so a parent re-rendering for fresh telemetry never
 * recreates the renderer. Unmounting destroys the controller, which
 * disposes its geometry, materials, renderer, controls and listeners. A
 * renderer that cannot start fails here, with a retry, and nothing else on
 * the page depends on it.
 *
 * Records, permissions and fetching stay outside: this component only draws.
 *
 * The wheel scrolls the page, not the model, until the model has focus or
 * Ctrl/⌘ is held (a trackpad pinch sends Ctrl): the scene's own orbit
 * controls would otherwise capture every scroll that passes over it.
 */
export function EquipmentScene({
  modelKey,
  selected,
  mode,
  labels,
  motorAlarm = null,
  onSelect,
  rotate = true,
  fitViewport = false,
  onOccluded,
  ref
}: Readonly<{
  modelKey: string;
  selected: TwinPartId;
  mode: TwinMode;
  labels: boolean;
  motorAlarm?: TwinMotorAlarm | null;
  onSelect?: (id: TwinPartId) => void;
  ref?: Ref<EquipmentSceneHandle>;
  rotate?: boolean;
  fitViewport?: boolean;
  onOccluded?: (
    parts: { id: TwinPartId; number: number; label: string }[]
  ) => void;
}>) {
  const scheme = useComputedColorScheme();
  const theme = useMantineTheme();
  const frame = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<string | number>(SCENE_HEIGHT);
  const host = useRef<HTMLDivElement>(null);
  const controller = useRef<TwinController | null>(null);
  const latest = useRef({
    selected,
    mode,
    labels,
    motorAlarm,
    onSelect,
    onOccluded
  });
  // Kept current after each commit, so the scene's callbacks and a scene
  // created later both see the latest props without rebuilding it.
  useLayoutEffect(() => {
    latest.current = {
      selected,
      mode,
      labels,
      motorAlarm,
      onSelect,
      onOccluded
    };
  });
  const [phase, setPhase] = useState<Phase>('loading');
  const [attempt, setAttempt] = useState(0);

  useLayoutEffect(() => {
    if (!fitViewport) return;
    const layout = frame.current?.closest<HTMLElement>('[data-model-layout]');
    const fit = () => {
      // A side-by-side inspector needs no height concession. Fit only the
      // stacked layout, so its inspector heading can remain above the fold.
      if (
        layout &&
        layout.clientWidth >=
          52 * Number.parseFloat(getComputedStyle(layout).fontSize)
      ) {
        setHeight(SCENE_HEIGHT);
        return;
      }
      // Selection can change the inspector's height after the page scrolls.
      // Keep the scene size tied to its document position, not scroll offset.
      const top =
        (frame.current?.getBoundingClientRect().top ?? 0) + window.scrollY;
      setHeight(
        Math.min(
          Math.max(420, window.innerHeight * 0.58),
          680,
          Math.max(160, window.innerHeight - top - 140)
        )
      );
    };
    fit();
    const observer = new ResizeObserver(fit);
    if (layout) observer.observe(layout);
    window.addEventListener('resize', fit);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', fit);
    };
  }, [fitViewport]);

  useImperativeHandle(
    ref,
    () => ({
      setView: (view) => controller.current?.setView(view),
      focus: (id) => controller.current?.focus(id),
      reset: () => controller.current?.reset(),
      zoom: (factor) => controller.current?.zoom(factor)
    }),
    []
  );

  // Capture phase on the host runs before the canvas's own listener, so
  // stopping here leaves the page's default scroll untouched.
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const guard = (event: WheelEvent) => {
      const focused = element.contains(document.activeElement);
      if (!focused && !event.ctrlKey && !event.metaKey) event.stopPropagation();
    };
    element.addEventListener('wheel', guard, { capture: true, passive: true });
    return () => element.removeEventListener('wheel', guard, { capture: true });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setPhase('loading');
    import('./twinScene.mjs')
      .then(({ create }) => {
        if (cancelled || !host.current) return;
        const initial = latest.current;
        let failedWhileStarting = false;
        let starting = true;
        const created = create(host.current, {
          selected: initial.selected,
          mode: initial.mode,
          labels: initial.labels,
          motorAlarm: initial.motorAlarm,
          onSelect: (id) => latest.current.onSelect?.(id),
          onOccluded: (parts) => latest.current.onOccluded?.(parts),
          onReady: () => {
            if (!cancelled) setPhase('ready');
          },
          onError: () => {
            if (starting) failedWhileStarting = true;
            if (!cancelled) setPhase('failed');
          }
        });
        starting = false;
        // A renderer that could not start is torn down at once, whatever
        // order React applies the phase changes in.
        if (failedWhileStarting) created.destroy();
        else controller.current = created;
      })
      .catch(() => {
        if (!cancelled) setPhase('failed');
      });
    return () => {
      cancelled = true;
      controller.current?.destroy();
      controller.current = null;
    };
  }, [modelKey, attempt]);

  // A failed renderer is torn down so only this component's own message
  // shows, and Retry starts from nothing.
  useEffect(() => {
    if (phase === 'failed') {
      controller.current?.destroy();
      controller.current = null;
    }
  }, [phase]);

  useEffect(() => {
    controller.current?.select(selected);
  }, [selected]);
  useEffect(() => {
    controller.current?.setMode(mode);
  }, [mode]);
  useEffect(() => {
    controller.current?.setLabels(labels);
  }, [labels]);
  useEffect(() => {
    controller.current?.setMotorAlarm(motorAlarm);
  }, [motorAlarm?.severity, motorAlarm?.attention, motorAlarm?.label, phase]);

  useEffect(() => {
    controller.current?.setTheme();
  }, [scheme, theme.primaryColor, phase]);
  useEffect(() => {
    controller.current?.setRotate(rotate);
  }, [rotate, phase]);

  return (
    <>
      <Box data-reference-model>
        <ValueTooltip
          label={t`Vertical reference model. Use the process schematic for this machine. Drag to rotate; arrow keys rotate, + and − zoom.`}
        >
          <StatusBadge
            size='sm'
            color='gray'
            variant='outline'
            bg='var(--mantine-color-body)'
          >{t`Reference model`}</StatusBadge>
        </ValueTooltip>
      </Box>
      <Box
        ref={frame}
        pos='relative'
        h={height}
        data-scene-phase={phase}
        style={{
          borderRadius: 'var(--mantine-radius-default)',
          overflow: 'hidden'
        }}
      >
        <Box ref={host} pos='absolute' inset={0} />
        {phase === 'loading' && (
          <Skeleton
            pos='absolute'
            inset={0}
            h='100%'
            aria-label={t`Loading 3D view`}
          />
        )}
        {phase === 'failed' && (
          <Paper
            pos='absolute'
            inset={0}
            withBorder
            p='lg'
            // A status the screen reader announces, not just a picture of one.
            component='output'
            style={{ display: 'grid', placeItems: 'center' }}
          >
            <Stack gap='xs' align='center' maw={360} ta='center'>
              <Text fw={600}>{t`3D view unavailable`}</Text>
              <Text size='sm' className={operations.meta}>
                {t`The 3D renderer could not start in this browser. Nothing else on this machine depends on it.`}
              </Text>
              <Button
                variant='light'
                size='xs'
                onClick={() => setAttempt((value) => value + 1)}
              >
                {t`Retry 3D view`}
              </Button>
            </Stack>
          </Paper>
        )}
      </Box>
    </>
  );
}
