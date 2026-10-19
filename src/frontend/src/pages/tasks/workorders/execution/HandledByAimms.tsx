import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Card,
  Collapse,
  Divider,
  Group,
  SimpleGrid,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { IconChevronDown, IconChevronUp, IconRobot } from '@tabler/icons-react';
import dayjs from 'dayjs';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import type {
  HandledAction,
  HandledActionState
} from '@lib/types/WorkExecution';

import { executionBadgeStyles } from './ExecutionFacts';
import {
  HANDLED_PREVIEW_ROWS,
  handledLinkTarget,
  readHandledActions
} from './handledActions';

const STATE_COLOR: Record<HandledActionState, string> = {
  completed: 'teal',
  active: 'indigo',
  queued: 'gray',
  failed: 'red',
  unknown: 'orange'
};

function stateLabel(row: HandledAction): string {
  switch (row.state) {
    case 'completed':
      return t`Handled`;
    case 'active':
      return t`Working`;
    case 'queued':
      return t`Queued, not running`;
    case 'failed':
      return t`Could not complete`;
    default:
      return t`Outcome unknown`;
  }
}

function sourceLabel(key: string): string {
  switch (key) {
    case 'packet_preparation':
      return t`repair packet preparation`;
    case 'closeout_drafting':
      return t`maintenance account drafting`;
    case 'closeout_follow_up':
      return t`closeout follow-ups`;
    default:
      return key;
  }
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Plain-language action and its saved result, from the recorded fields only. */
function describe(row: HandledAction): { action: string; result: string } {
  const result = row.result ?? {};
  switch (row.kind) {
    case 'packet_prepared': {
      if (row.state === 'active')
        return { action: t`Preparing the repair packet`, result: '' };
      if (row.state === 'failed')
        return {
          action: t`Repair packet preparation`,
          result: t`No diagnosis, parts or gates were saved by this run.`
        };
      const parts = count(result.parts);
      const gates = count(result.gates_created);
      return {
        action: t`Repair packet prepared`,
        result:
          parts !== null && gates !== null
            ? t`Preliminary diagnosis saved · ${parts} part suggestion(s) · ${gates} safety gate(s) created`
            : t`Preliminary diagnosis saved`
      };
    }
    case 'work_order_prepared':
      return {
        action: t`Work order created from the repair packet`,
        result: result.reference
          ? t`${String(result.reference)} with its required parts list`
          : t`This work order and its required parts list`
      };
    case 'account_drafted': {
      const fields = count(result.fields) ?? 0;
      const parts = count(result.part_candidates) ?? 0;
      const readings = count(result.reading_candidates) ?? 0;
      const revision = count(result.capture_revision);
      const review =
        result.capture_status === 'abandoned'
          ? t`narrative abandoned`
          : result.current_revision === false
            ? t`for an earlier narrative revision`
            : result.capture_status === 'consumed'
              ? t`narrative consumed by completion`
              : row.status === 'reviewed'
                ? t`reviewed by a person`
                : t`Recorded proposal status: ${row.status}`;
      return {
        action: t`Maintenance account drafted from the technician narrative`,
        result:
          revision !== null
            ? t`Narrative revision ${revision}: ${fields} field(s), ${parts} part mention(s), ${readings} reading(s) proposed · ${review}`
            : t`${fields} field(s) proposed · ${review}`
      };
    }
    case 'account_drafting':
      return {
        action: t`Drafting the maintenance account from the narrative`,
        result: ''
      };
    case 'closeout_notification': {
      if (row.state !== 'completed')
        return { action: t`Closeout notification`, result: '' };
      if (result.no_targets)
        return {
          action: t`Closeout notification`,
          result: t`No one was notified: no assignee or requester is recorded`
        };
      const notified = count(result.notified);
      return {
        action: t`Closeout notification sent`,
        result:
          notified !== null
            ? t`${notified} person(s) notified`
            : t`Delivery recorded`
      };
    }
    case 'learning_drafted':
      return {
        action:
          row.state === 'completed'
            ? t`Learning candidate drafted`
            : t`Learning candidate`,
        result:
          row.state === 'completed' && result.learning_draft
            ? t`Draft ${String(result.learning_draft)} — not a procedure change`
            : ''
      };
    default:
      return { action: t`Closeout follow-up`, result: '' };
  }
}

function linkLabel(row: HandledAction): string {
  const link = row.link;
  if (!link) return '';
  if (link.kind === 'repair_packet') return t`Open ${link.reference}`;
  switch (link.panel) {
    case 'parts':
      return t`Open Parts`;
    case 'closeout':
      return t`Open Closeout`;
    case 'diagnose':
      return t`Open Diagnose`;
    default:
      return t`Open`;
  }
}

function when(value: string | null): string {
  return value ? dayjs(value).format('DD MMM YYYY, HH:mm') : t`Not recorded`;
}

function sourceRecord(row: HandledAction): string {
  switch (row.source.record) {
    case 'repair_packet_generation_run':
      return t`Repair packet preparation run #${row.source.id}`;
    case 'repair_packet_event':
      return t`Repair packet event #${row.source.id}`;
    case 'closeout_proposal':
      return t`Closeout extraction #${row.source.id}`;
    case 'closeout_capture':
      return t`Closeout narrative #${row.source.id}`;
    case 'closeout_effect':
      return t`Closeout follow-up #${row.source.id}`;
    default:
      return `${row.source.record} #${row.source.id}`;
  }
}

function triggerLabel(row: HandledAction): string {
  const trigger = row.trigger ?? {};
  if (trigger.requested_by) return t`Requested by ${trigger.requested_by}`;
  if (trigger.amendment_id)
    return trigger.amendment_applied_at
      ? t`Amendment ${trigger.amendment_id} applied ${when(trigger.amendment_applied_at)}`
      : t`Amendment ${trigger.amendment_id} · applied time not recorded`;
  if (trigger.closeout_completed_at)
    return t`Closeout recorded ${when(trigger.closeout_completed_at)}`;
  return t`Not recorded`;
}

function Detail({ label, value }: Readonly<{ label: string; value: string }>) {
  return (
    <Stack gap={0}>
      <Text size='xs' c='dimmed'>
        {label}
      </Text>
      <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
        {value}
      </Text>
    </Stack>
  );
}

function ActionRow({ row }: Readonly<{ row: HandledAction }>) {
  const [params] = useSearchParams();
  const [open, setOpen] = useState(false);
  const { action, result } = describe(row);
  const target = handledLinkTarget(row.link, params);
  const detailId = `handled-detail-${row.id.replace(/[^a-z0-9-]/gi, '-')}`;
  return (
    <Box
      py='xs'
      data-testid='handled-action'
      style={{ borderTop: '1px solid var(--mantine-color-default-border)' }}
    >
      <Group justify='space-between' align='flex-start' gap='xs'>
        <Stack gap={2} style={{ flex: '1 1 260px', minWidth: 0 }}>
          <Text size='sm' fw={600} style={{ overflowWrap: 'anywhere' }}>
            {action}
          </Text>
          {result && (
            <Text size='sm' c='dimmed' style={{ overflowWrap: 'anywhere' }}>
              {result}
            </Text>
          )}
        </Stack>
        <Group gap='xs' wrap='wrap' style={{ maxWidth: '100%' }}>
          <Badge
            variant='light'
            color={STATE_COLOR[row.state]}
            styles={executionBadgeStyles}
          >
            {stateLabel(row)}
          </Badge>
          <Text size='xs' c='dimmed' style={{ whiteSpace: 'nowrap' }}>
            {when(row.occurred_at)}
          </Text>
        </Group>
      </Group>
      <Group gap='md' mt={4}>
        {target && (
          <Anchor component={Link} to={target} size='sm'>
            {linkLabel(row)}
          </Anchor>
        )}
        <Button
          variant='subtle'
          size='compact-xs'
          aria-expanded={open}
          aria-controls={open ? detailId : undefined}
          rightSection={
            open ? <IconChevronUp size={14} /> : <IconChevronDown size={14} />
          }
          onClick={() => setOpen((value) => !value)}
        >
          {open ? t`Hide details` : t`Details`}
        </Button>
      </Group>
      <Collapse expanded={open} id={detailId} keepMounted={false}>
        <SimpleGrid cols={{ base: 1, sm: 2 }} mt='xs' spacing='xs'>
          <Detail label={t`Trigger`} value={triggerLabel(row)} />
          <Detail label={t`Source record`} value={sourceRecord(row)} />
          <Detail
            label={t`Saved result`}
            value={
              row.state === 'completed'
                ? result || t`Recorded`
                : t`No confirmed result`
            }
          />
          <Detail
            label={t`Recorded status`}
            value={row.status.replaceAll('_', ' ')}
          />
          <Detail label={t`Started`} value={when(row.started_at)} />
          <Detail label={t`Finished`} value={when(row.finished_at)} />
          {row.attempts !== null && (
            <Detail label={t`Attempts`} value={String(row.attempts)} />
          )}
          {row.state !== 'completed' && (
            <Detail label={t`Next owner`} value={t`Not recorded`} />
          )}
        </SimpleGrid>
      </Collapse>
    </Box>
  );
}

/**
 * P18 / UI11: what AIMMS has handled for this work, from saved records only.
 *
 * Rows come from the overview's `handled_actions.v1` projection: packet
 * preparation runs, the work order they created, drafted maintenance accounts
 * and closeout follow-ups. A chat answer or tool call is not a row. Opening the
 * Work panel reads this list; it never starts preparation.
 */
export function HandledByAimms({ value }: Readonly<{ value: unknown }>) {
  const reading = readHandledActions(value);
  const [showAll, setShowAll] = useState(false);

  if (reading.kind === 'unsupported') {
    return (
      <Card withBorder padding='md'>
        <Stack gap='xs'>
          <Group gap='xs'>
            <IconRobot size={18} />
            <Title order={4}>{t`Handled by AIMMS`}</Title>
          </Group>
          <Text size='sm' c='dimmed'>
            {t`This server does not supply a record of AIMMS digital work for this work order. Nothing here means AIMMS did or did not act.`}
          </Text>
        </Stack>
      </Card>
    );
  }

  const { projection, handling, recorded, notEnabled } = reading;
  const { counts } = projection;
  const visible = showAll
    ? recorded
    : recorded.slice(0, Math.max(HANDLED_PREVIEW_ROWS - handling.length, 2));
  const checked = projection.sources
    .filter((source) => source.state === 'read')
    .map((source) => sourceLabel(source.key));

  return (
    <Card withBorder padding='md' aria-labelledby='handled-by-aimms-title'>
      <Stack gap='sm'>
        <Group justify='space-between' align='center' gap='xs'>
          <Group gap='xs'>
            <IconRobot size={18} />
            <Title order={4} id='handled-by-aimms-title'>
              {t`Handled by AIMMS`}
            </Title>
            {checked.length > 0 && (
              <Badge variant='light' color='teal'>
                {t`${counts.completed} handled`}
              </Badge>
            )}
          </Group>
          <Group gap='xs'>
            {checked.length > 0 && (counts.queued ?? 0) > 0 && (
              <Badge variant='light' color='gray'>
                {t`${counts.queued ?? 0} queued`}
              </Badge>
            )}
            {checked.length > 0 && counts.active > 0 && (
              <Badge variant='light' color='indigo'>
                {t`${counts.active} in progress`}
              </Badge>
            )}
            {checked.length > 0 && counts.failed > 0 && (
              <Badge variant='light' color='red'>
                {t`${counts.failed} could not complete`}
              </Badge>
            )}
            {checked.length > 0 && counts.unknown > 0 && (
              <Badge variant='light' color='orange'>
                {t`${counts.unknown} outcome unknown`}
              </Badge>
            )}
          </Group>
        </Group>

        {handling.length > 0 && (
          <Stack gap={0}>
            <Text size='sm' fw={600}>
              {t`AIMMS is handling`}
            </Text>
            {handling.map((row) => (
              <ActionRow key={row.id} row={row} />
            ))}
          </Stack>
        )}

        {recorded.length === 0 && handling.length === 0 ? (
          <Text size='sm' c='dimmed'>
            {checked.length > 0
              ? t`No recorded AIMMS actions for this work order.`
              : t`No AIMMS record source applies to this work order.`}
          </Text>
        ) : (
          recorded.length > 0 && (
            <Stack gap={0}>
              {handling.length > 0 && (
                <Text size='sm' fw={600}>
                  {t`Recorded activity`}
                </Text>
              )}
              {visible.map((row) => (
                <ActionRow key={row.id} row={row} />
              ))}
            </Stack>
          )
        )}

        {recorded.length > visible.length || showAll ? (
          <Button
            variant='subtle'
            size='compact-sm'
            w='fit-content'
            onClick={() => setShowAll((value) => !value)}
          >
            {showAll
              ? t`Show fewer`
              : t`Show all activity (${recorded.length + handling.length})`}
          </Button>
        ) : null}

        <Divider />
        <Text size='xs' c='dimmed'>
          {checked.length > 0
            ? t`Checked: ${checked.join(', ')}.`
            : t`No AIMMS record source was read.`}
          {notEnabled.length > 0 &&
            ` ${t`Not enabled on this server: ${notEnabled.map(sourceLabel).join(', ')}.`}`}
        </Text>
        {counts.failed + counts.unknown > 0 && (
          <Alert color='gray' variant='light' p='xs'>
            <Text size='sm'>
              {t`No confirmed result or next owner is recorded here for failed or unknown actions.`}
            </Text>
          </Alert>
        )}
      </Stack>
    </Card>
  );
}

export default HandledByAimms;
