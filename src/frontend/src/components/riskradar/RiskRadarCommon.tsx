import { t } from '@lingui/core/macro';
import { Group, Paper, Stack, Text } from '@mantine/core';
import {
  IconAlertCircle,
  IconAlertOctagon,
  IconAlertTriangle,
  IconInfoCircle
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import type { ReactNode } from 'react';

import { formatDate } from '../../defaults/formatters';

/*
 * Shared types and rendering helpers for the Risk Radar / Command Center
 * feature. The REST API contract is defined server-side; these types mirror
 * the DTO shapes returned by the `repair/risk-*` endpoints.
 */

export type RiskSeverity = 'critical' | 'high' | 'medium' | 'low';

export type RiskFindingState =
  | 'open'
  | 'acknowledged'
  | 'snoozed'
  | 'resolved'
  | 'dismissed';

export interface RiskActionLink {
  label: string;
  target_kind: string;
  target_id: number | string;
  route: string;
}

export interface RiskFinding {
  pk: number;
  scope_key: string;
  rule_code: string;
  rule_version: number | string;
  category: string;
  severity: RiskSeverity;
  severity_factors: Record<string, any>;
  source_model: string;
  source_id: number | string;
  title: string;
  summary: string;
  state: RiskFindingState;
  owner: number | null;
  owner_username: string | null;
  first_seen: string;
  last_seen: string;
  condition_started_at: string | null;
  source_as_of: string | null;
  due_at: string | null;
  due_breached: boolean;
  age_hours: number;
  snooze_until: string | null;
  dismiss_recheck_at: string | null;
  reopen_count: number;
  version: number;
}

export interface RiskFindingEvent {
  pk: number;
  event_type: string;
  actor: number | null;
  actor_username: string | null;
  reason: string | null;
  metadata: Record<string, any> | null;
  created_at: string;
}

export interface RiskFindingDetail extends RiskFinding {
  evidence: Record<string, any>;
  events: RiskFindingEvent[];
  action_links: RiskActionLink[];
}

export interface RiskFindingListResponse {
  scope: string;
  as_of: string;
  source_freshness: SourceFreshness[];
  count: number;
  results: RiskFinding[];
}

export interface RuleFreshness {
  rule: string;
  enabled: boolean;
  gate: string | null;
  last_complete: string | null;
  last_status: string | null;
  degraded: boolean;
  source_disabled: boolean;
  dormant: boolean;
}

export interface SourceFreshness {
  source: string;
  as_of: string | null;
  degraded: boolean;
}

export interface CommandCenterQueueEntry {
  finding_id: number;
  severity: RiskSeverity;
  category: string;
  rule: string;
  title: string;
  state: RiskFindingState;
  age_hours: number;
  due_breached: boolean;
  source_as_of: string | null;
}

export interface CommandCenterSummary {
  as_of: string;
  scope: string;
  stale: boolean;
  freshness: RuleFreshness[];
  source_freshness: SourceFreshness[];
  headline: Record<RiskSeverity, number>;
  by_category: Record<string, number>;
  queue: CommandCenterQueueEntry[];
  flow: {
    packets: Record<string, number> | { source_disabled: true };
    work_orders: Record<string, number> | { source_disabled: true };
  };
  aging: {
    approvals_in_review:
      | { p50_hours: number; max_hours: number }
      | { source_disabled: true };
    shortages_open:
      | { p50_days: number; max_days: number }
      | { source_disabled: true };
  };
  return_to_service: {
    finding_id: number;
    packet: string;
    code: string;
    reason_snapshot: string;
    source_as_of: string | null;
  }[];
}

export interface RiskRuleHealthRow {
  rule: string;
  enabled: boolean;
  gate: string | null;
  last_complete: string | null;
  last_status: string | null;
  degraded: boolean;
  source_disabled: boolean;
  dormant: boolean;
  version: number | string;
  cadence: string | null;
  critical_rule: boolean;
  config: Record<string, any> | null;
  enabled_scopes: string[];
  dormant_reason: string | null;
  failure_streak: number;
  recent_runs: {
    status: string;
    started_at: string | null;
    completed_at: string | null;
    error_summary: string | null;
  }[];
  finding_counts: Record<string, number>;
}

export interface RiskRuleHealthResponse {
  scope: string;
  as_of: string;
  rules: RiskRuleHealthRow[];
}

const RISK_SEVERITIES: RiskSeverity[] = ['critical', 'high', 'medium', 'low'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isRiskFinding(value: unknown, scope: string): value is RiskFinding {
  if (!isRecord(value)) {
    return false;
  }
  return (
    typeof value.pk === 'number' &&
    value.scope_key === scope &&
    typeof value.rule_code === 'string' &&
    typeof value.title === 'string' &&
    RISK_SEVERITIES.includes(value.severity as RiskSeverity)
  );
}

/**
 * Reject malformed or cross-scope list payloads instead of rendering them as
 * an empty, clean queue.
 */
export function parseRiskFindingListResponse(
  value: unknown,
  scope: string
): RiskFindingListResponse {
  if (
    !isRecord(value) ||
    value.scope !== scope ||
    typeof value.as_of !== 'string' ||
    !Array.isArray(value.source_freshness) ||
    typeof value.count !== 'number' ||
    !Array.isArray(value.results) ||
    !value.results.every((finding) => isRiskFinding(finding, scope))
  ) {
    throw new Error('Invalid risk finding list response');
  }
  return value as unknown as RiskFindingListResponse;
}

export function parseRiskFindingDetail(
  value: unknown,
  scope: string
): RiskFindingDetail {
  const record = value as Record<string, unknown>;
  if (
    !isRiskFinding(value, scope) ||
    !isRecord(record.evidence) ||
    !Array.isArray(record.events) ||
    !Array.isArray(record.action_links)
  ) {
    throw new Error('Invalid risk finding detail response');
  }
  return value as unknown as RiskFindingDetail;
}

/**
 * Validate the required summary sections so absent data is never converted
 * into a zero count or a source-disabled sentinel by the UI.
 */
export function parseCommandCenterSummary(
  value: unknown,
  scope: string
): CommandCenterSummary {
  if (!isRecord(value) || value.scope !== scope) {
    throw new Error('Invalid command center summary scope');
  }
  const headline = value.headline;
  const flow = value.flow;
  const aging = value.aging;
  if (
    typeof value.as_of !== 'string' ||
    typeof value.stale !== 'boolean' ||
    !Array.isArray(value.freshness) ||
    !Array.isArray(value.source_freshness) ||
    !isRecord(headline) ||
    !RISK_SEVERITIES.every(
      (severity) => typeof headline[severity] === 'number'
    ) ||
    !isRecord(value.by_category) ||
    !Array.isArray(value.queue) ||
    !isRecord(flow) ||
    !isRecord(flow.packets) ||
    !isRecord(flow.work_orders) ||
    !isRecord(aging) ||
    !isRecord(aging.approvals_in_review) ||
    !isRecord(aging.shortages_open) ||
    !Array.isArray(value.return_to_service)
  ) {
    throw new Error('Invalid command center summary response');
  }
  return value as unknown as CommandCenterSummary;
}

export function parseRiskRuleHealthResponse(
  value: unknown,
  scope: string
): RiskRuleHealthResponse {
  if (
    !isRecord(value) ||
    value.scope !== scope ||
    typeof value.as_of !== 'string' ||
    !Array.isArray(value.rules)
  ) {
    throw new Error('Invalid risk rule health response');
  }
  return value as unknown as RiskRuleHealthResponse;
}

const GOVERNED_ACTION_ROUTES: Record<string, (targetId: string) => string> = {
  repair_packet: (targetId) => `/repair/packets/${targetId}/`,
  work_order: (targetId) => `/maintenance/work-orders/${targetId}`,
  purchase_order: (targetId) => `/purchasing/purchase-order/${targetId}/`,
  asset_machine: (targetId) => `/machines/machine/${targetId}/`,
  machine_anomaly: (targetId) => `/machines/machine/${targetId}/health`,
  part: (targetId) => `/part/${targetId}/`
};

/**
 * Reconstruct and verify corrective links from governed target kinds. Unknown
 * targets and server routes which do not exactly match are suppressed.
 */
export function governedRiskActionRoute(link: RiskActionLink): string | null {
  const targetId = `${link.target_id}`;
  const builder = GOVERNED_ACTION_ROUTES[link.target_kind];
  if (!builder || !/^[1-9]\d*$/.test(targetId)) {
    return null;
  }
  const route = builder(targetId);
  return link.route === route ? route : null;
}

/**
 * Human readable (translated) label for a risk severity level.
 */
export function severityLabel(severity: RiskSeverity): string {
  switch (severity) {
    case 'critical':
      return t`Critical`;
    case 'high':
      return t`High`;
    case 'medium':
      return t`Medium`;
    case 'low':
      return t`Low`;
    default:
      return severity;
  }
}

export function severityColor(severity: RiskSeverity): string {
  switch (severity) {
    case 'critical':
      return 'red';
    case 'high':
      return 'orange';
    case 'medium':
      return 'yellow';
    case 'low':
      return 'blue';
    default:
      return 'gray';
  }
}

export function severityIcon(severity: RiskSeverity, size = 16): ReactNode {
  switch (severity) {
    case 'critical':
      return <IconAlertOctagon size={size} aria-hidden />;
    case 'high':
      return <IconAlertTriangle size={size} aria-hidden />;
    case 'medium':
      return <IconAlertCircle size={size} aria-hidden />;
    case 'low':
      return <IconInfoCircle size={size} aria-hidden />;
    default:
      return <IconInfoCircle size={size} aria-hidden />;
  }
}

/**
 * Severity is always rendered as icon + text label (never color alone).
 */
export function SeverityIndicator({
  severity,
  size = 16
}: Readonly<{ severity: RiskSeverity; size?: number }>) {
  return (
    <Group gap={4} wrap='nowrap' c={severityColor(severity)}>
      {severityIcon(severity, size)}
      <Text size='sm' c={severityColor(severity)} fw={500}>
        {severityLabel(severity)}
      </Text>
    </Group>
  );
}

/**
 * Compact age rendering, e.g. '26h' or '3d'.
 */
export function formatAge(ageHours: number | null | undefined): string {
  if (ageHours == null || Number.isNaN(ageHours)) {
    return '-';
  }
  if (ageHours >= 48) {
    return `${Math.round(ageHours / 24)}d`;
  }
  return `${Math.round(ageHours)}h`;
}

export function isSourceDisabled(
  value: Record<string, number> | { source_disabled: true } | undefined
): boolean {
  return !!value && 'source_disabled' in value && !!value.source_disabled;
}

/**
 * Small state-count tiles for a flow lane (packets / work orders).
 * A disabled source is explicitly called out, never rendered as zero.
 */
export function FlowTiles({
  title,
  counts
}: Readonly<{
  title: string;
  counts: Record<string, number> | { source_disabled: true } | undefined;
}>) {
  if (!counts) {
    return (
      <Stack gap={2}>
        <Text size='xs' c='dimmed'>
          {title}
        </Text>
        <Text size='sm' c='dimmed' fs='italic'>
          {t`Data unavailable`}
        </Text>
      </Stack>
    );
  }

  if (isSourceDisabled(counts)) {
    return (
      <Stack gap={2}>
        <Text size='xs' c='dimmed'>
          {title}
        </Text>
        <Text size='sm' c='dimmed' fs='italic'>
          {t`Source disabled`}
        </Text>
      </Stack>
    );
  }

  const entries = Object.entries(counts as Record<string, number>);

  return (
    <Stack gap={2}>
      <Text size='xs' c='dimmed'>
        {title}
      </Text>
      <Group gap='xs' wrap='wrap'>
        {entries.map(([state, count]) => (
          <Paper key={state} withBorder p={6}>
            <Stack gap={0} align='center'>
              <Text size='lg' fw={600}>
                {count}
              </Text>
              <Text size='xs' c='dimmed'>
                {state}
              </Text>
            </Stack>
          </Paper>
        ))}
      </Group>
    </Stack>
  );
}

/**
 * Render a timestamp in the viewer's local timezone, with the UTC ISO
 * representation available via the title attribute (hover).
 */
export function LocalDateTime({
  value
}: Readonly<{ value: string | null | undefined }>) {
  if (!value) {
    return <Text component='span'>-</Text>;
  }
  const parsed = dayjs(value);
  const utcIso = parsed.isValid() ? parsed.toISOString() : value;
  return (
    <Text component='span' title={utcIso}>
      {formatDate(value, { showTime: true })}
    </Text>
  );
}
