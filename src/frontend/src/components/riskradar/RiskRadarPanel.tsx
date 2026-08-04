import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Menu,
  Modal,
  Pagination,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  Tooltip
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconCheck,
  IconClockPause,
  IconDotsVertical,
  IconRefresh,
  IconX
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';

import { StylishText } from '@lib/components/StylishText';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { useRiskScope } from '../../hooks/UseRiskScope';
import {
  LocalDateTime,
  type RiskFinding,
  SeverityIndicator,
  formatAge,
  parseRiskFindingListResponse
} from './RiskRadarCommon';

const PANEL_LIMIT = 50;

const COMMAND_ENDPOINTS = {
  acknowledge: ApiEndpoints.risk_finding_acknowledge,
  snooze: ApiEndpoints.risk_finding_snooze,
  dismiss: ApiEndpoints.risk_finding_dismiss
} as const;

type FindingCommand = keyof typeof COMMAND_ENDPOINTS;
type CommandResult =
  | { ok: true }
  | {
      ok: false;
      message: string;
      code?: string;
    };

interface CommandErrorDetails {
  message: string;
  code?: string;
  hasServerResponse: boolean;
}

const RECHECK_REFRESH_DELAYS_MS = [3_000, 10_000, 30_000] as const;

/** Generate an opaque command key on secure and insecure browser origins. */
function newIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return `risk-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Prefer the governed API's stable error detail over a generic status label. */
function commandErrorDetails(error: unknown): CommandErrorDetails {
  const response = (error as { response?: { data?: unknown } } | null)
    ?.response;
  const rawData = response?.data;
  const data =
    rawData && typeof rawData === 'object'
      ? (rawData as Record<string, unknown>)
      : undefined;
  if (typeof data?.detail === 'string' && data.detail.trim()) {
    return {
      message: data.detail,
      code: typeof data.code === 'string' ? data.code : undefined,
      hasServerResponse: response !== undefined
    };
  }
  if (typeof data?.code === 'string' && data.code.trim()) {
    return {
      message: data.code,
      code: data.code,
      hasServerResponse: response !== undefined
    };
  }
  return {
    message: t`The command could not be completed.`,
    hasServerResponse: response !== undefined
  };
}

/**
 * The full Risk Radar findings queue for the Maintenance workspace.
 *
 * Ranking, visibility, and scope authorization are entirely server-side; the
 * panel renders response order and issues lifecycle commands through the
 * governed endpoints with the finding's expected version, so a stale row can
 * never overwrite a newer state.
 */
export default function RiskRadarPanel() {
  const api = useApi();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [dismissFinding, setDismissFinding] = useState<RiskFinding | null>(
    null
  );
  const [dismissReason, setDismissReason] = useState('');
  const [dismissError, setDismissError] = useState<string | null>(null);
  const [dismissConflict, setDismissConflict] = useState(false);
  const [dismissSubmitting, setDismissSubmitting] = useState(false);
  const [recheckingFindingId, setRecheckingFindingId] = useState<number | null>(
    null
  );
  const recheckRefreshTimers = useRef<number[]>([]);
  const recheckRetryKeys = useRef(
    new Map<number, { version: number; key: string }>()
  );
  const {
    scopes,
    scope,
    authorizationFingerprint,
    setScope,
    unavailable,
    isLoading
  } = useRiskScope();

  const findingsQuery = useQuery({
    queryKey: ['risk-findings', scope, authorizationFingerprint, 'panel', page],
    enabled: !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_finding_list), {
          params: {
            scope: scope,
            limit: PANEL_LIMIT,
            offset: (page - 1) * PANEL_LIMIT
          }
        })
        .then((response) => parseRiskFindingListResponse(response.data, scope))
  });

  const invalidateFindings = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ['risk-findings'] }),
    [queryClient]
  );

  const clearRecheckRefreshes = useCallback(() => {
    recheckRefreshTimers.current.forEach((timer) => window.clearTimeout(timer));
    recheckRefreshTimers.current = [];
  }, []);

  const scheduleRecheckRefreshes = useCallback(() => {
    clearRecheckRefreshes();
    recheckRefreshTimers.current = RECHECK_REFRESH_DELAYS_MS.map((delay) =>
      window.setTimeout(() => {
        void invalidateFindings();
      }, delay)
    );
  }, [clearRecheckRefreshes, invalidateFindings]);

  useEffect(() => clearRecheckRefreshes, [clearRecheckRefreshes]);

  useEffect(() => {
    setPage(1);
    clearRecheckRefreshes();
    recheckRetryKeys.current.clear();
  }, [scope, clearRecheckRefreshes]);

  useEffect(() => {
    if (
      page > 1 &&
      findingsQuery.data &&
      findingsQuery.data.count > 0 &&
      findingsQuery.data.results.length === 0
    ) {
      setPage(1);
    }
  }, [findingsQuery.data, page]);

  const runCommand = useCallback(
    async (
      finding: RiskFinding,
      command: FindingCommand,
      extra: Record<string, unknown> = {}
    ): Promise<CommandResult> => {
      try {
        await api.post(apiUrl(COMMAND_ENDPOINTS[command], finding.pk), {
          expected_version: finding.version,
          idempotency_key: newIdempotencyKey(),
          ...extra
        });
        void invalidateFindings();
        return { ok: true };
      } catch (error) {
        const details = commandErrorDetails(error);
        notifications.show({
          title: t`Command failed`,
          message: details.message,
          color: 'red'
        });
        void invalidateFindings();
        return {
          ok: false,
          message: details.message,
          code: details.code
        };
      }
    },
    [api, invalidateFindings]
  );

  const queueRecheck = useCallback(
    async (finding: RiskFinding) => {
      const prior = recheckRetryKeys.current.get(finding.pk);
      const retry = prior ?? {
        version: finding.version,
        key: newIdempotencyKey()
      };
      recheckRetryKeys.current.set(finding.pk, retry);
      setRecheckingFindingId(finding.pk);
      try {
        await api.post(apiUrl(ApiEndpoints.risk_finding_recheck, finding.pk), {
          expected_version: retry.version,
          idempotency_key: retry.key
        });
        recheckRetryKeys.current.delete(finding.pk);
        notifications.show({
          title: t`Recheck queued`,
          message: t`The finding will be refreshed as the scan completes.`,
          color: 'blue'
        });
        scheduleRecheckRefreshes();
      } catch (error) {
        const details = commandErrorDetails(error);
        if (details.hasServerResponse) {
          recheckRetryKeys.current.delete(finding.pk);
        }
        notifications.show({
          title: t`Recheck failed`,
          message: details.message,
          color: 'red'
        });
        void invalidateFindings();
      } finally {
        setRecheckingFindingId(null);
      }
    },
    [api, invalidateFindings, scheduleRecheckRefreshes]
  );

  const openDismiss = useCallback((finding: RiskFinding) => {
    setDismissFinding(finding);
    setDismissReason('');
    setDismissError(null);
    setDismissConflict(false);
  }, []);

  const resetDismiss = useCallback(() => {
    setDismissFinding(null);
    setDismissReason('');
    setDismissError(null);
    setDismissConflict(false);
    setDismissSubmitting(false);
  }, []);

  const submitDismiss = useCallback(async () => {
    const reason = dismissReason.trim();
    if (!dismissFinding || !reason || dismissSubmitting) {
      return;
    }
    setDismissSubmitting(true);
    setDismissError(null);
    const result = await runCommand(dismissFinding, 'dismiss', { reason });
    if (result.ok) {
      resetDismiss();
    } else {
      setDismissError(result.message);
      setDismissConflict(result.code === 'FINDING_STATE_CONFLICT');
      setDismissSubmitting(false);
    }
  }, [
    dismissFinding,
    dismissReason,
    dismissSubmitting,
    resetDismiss,
    runCommand
  ]);

  const closeDismiss = useCallback(() => {
    if (!dismissSubmitting) {
      resetDismiss();
    }
  }, [dismissSubmitting, resetDismiss]);

  const findings: RiskFinding[] = findingsQuery.data?.results ?? [];
  const count = findingsQuery.data?.count ?? 0;
  const totalPages = Math.max(1, Math.ceil(count / PANEL_LIMIT));
  const firstVisibleFinding = count === 0 ? 0 : (page - 1) * PANEL_LIMIT + 1;
  const lastVisibleFinding = Math.min(page * PANEL_LIMIT, count);

  const degradedSources = (findingsQuery.data?.source_freshness ?? [])
    .filter((source) => source.degraded)
    .map((source) => source.source);

  if (isLoading || (!!scope && findingsQuery.isLoading)) {
    return <Loader size='sm' />;
  }

  // Flag-off deployments (endpoint 404) and scope-less viewers see one honest
  // line rather than an empty queue that implies "no risk".
  if (unavailable || findingsQuery.isError) {
    return (
      <Alert color='gray' title={t`Risk Radar unavailable`}>
        <Text size='sm'>
          {t`Risk radar is not enabled on this deployment, or you have no authorized scopes.`}
        </Text>
      </Alert>
    );
  }

  return (
    <>
      <Stack gap='xs'>
        <Group justify='space-between' wrap='nowrap'>
          <StylishText size='lg'>{t`Risk Radar`}</StylishText>
          <Group gap='xs' wrap='nowrap'>
            <Select
              size='xs'
              aria-label='risk-radar-panel-scope-select'
              data={scopes}
              value={scope}
              onChange={(value) => {
                if (value) {
                  setScope(value);
                }
              }}
              allowDeselect={false}
            />
            <Tooltip label={t`Refresh`}>
              <ActionIcon
                variant='subtle'
                aria-label='risk-radar-refresh'
                onClick={() => void invalidateFindings()}
              >
                <IconRefresh size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        {degradedSources.length > 0 && (
          <Alert color='orange' title={t`Risk data degraded`}>
            <Text size='sm'>
              {t`Unavailable sources`}: {degradedSources.join(', ')}
            </Text>
          </Alert>
        )}
        {findings.length === 0 ? (
          <Alert color='green' title={t`No open findings`}>
            <Text size='sm'>{t`There are no risk findings for this scope`}</Text>
          </Alert>
        ) : (
          <Table verticalSpacing={6} horizontalSpacing='xs' highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`Severity`}</Table.Th>
                <Table.Th>{t`Finding`}</Table.Th>
                <Table.Th>{t`Rule`}</Table.Th>
                <Table.Th>{t`State`}</Table.Th>
                <Table.Th>{t`Age`}</Table.Th>
                <Table.Th aria-label='actions' />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {findings.map((finding) => (
                <Table.Tr key={finding.pk} data-testid='risk-finding-row'>
                  <Table.Td>
                    <SeverityIndicator severity={finding.severity} />
                  </Table.Td>
                  <Table.Td>
                    <Stack gap={0}>
                      <Text size='sm' fw={500} lineClamp={1}>
                        {finding.title}
                      </Text>
                      <Text size='xs' c='dimmed' lineClamp={1}>
                        {finding.summary}
                      </Text>
                    </Stack>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant='light' color='gray'>
                      {finding.rule_code}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap='nowrap'>
                      <Badge
                        variant='light'
                        color={finding.state === 'open' ? 'red' : 'gray'}
                      >
                        {finding.state}
                      </Badge>
                      {finding.due_breached && (
                        <Badge variant='filled' color='red'>
                          {t`Overdue`}
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm' c='dimmed'>
                      {formatAge(finding.age_hours)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Menu position='bottom-end' withinPortal>
                      <Menu.Target>
                        <ActionIcon
                          variant='subtle'
                          aria-label={`risk-finding-actions-${finding.pk}`}
                        >
                          <IconDotsVertical size={16} />
                        </ActionIcon>
                      </Menu.Target>
                      <Menu.Dropdown>
                        <Menu.Item
                          leftSection={<IconCheck size={14} />}
                          onClick={() => {
                            void runCommand(finding, 'acknowledge');
                          }}
                        >
                          {t`Acknowledge`}
                        </Menu.Item>
                        <Menu.Item
                          leftSection={<IconClockPause size={14} />}
                          onClick={() => {
                            void runCommand(finding, 'snooze', {
                              snooze_until: new Date(
                                Date.now() + 24 * 60 * 60 * 1000
                              ).toISOString()
                            });
                          }}
                        >
                          {t`Snooze 24h`}
                        </Menu.Item>
                        <Menu.Item
                          leftSection={<IconRefresh size={14} />}
                          disabled={recheckingFindingId === finding.pk}
                          onClick={() => {
                            void queueRecheck(finding);
                          }}
                        >
                          {t`Recheck`}
                        </Menu.Item>
                        <Menu.Item
                          color='red'
                          leftSection={<IconX size={14} />}
                          onClick={() => openDismiss(finding)}
                        >
                          {t`Dismiss`}
                        </Menu.Item>
                      </Menu.Dropdown>
                    </Menu>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {totalPages > 1 && (
          <Pagination
            data-testid='risk-radar-pagination'
            total={totalPages}
            value={page}
            onChange={setPage}
            size='sm'
          />
        )}
        <Text size='xs' c='dimmed'>
          {t`Data as of`} <LocalDateTime value={findingsQuery.data?.as_of} />
          {' - '}
          {count} {t`findings`}
          {count > 0 && (
            <>
              {' - '}
              {t`Showing`} {firstVisibleFinding}-{lastVisibleFinding}
            </>
          )}
        </Text>
      </Stack>

      <Modal
        opened={dismissFinding != null}
        onClose={closeDismiss}
        title={t`Dismiss finding`}
        closeOnClickOutside={!dismissSubmitting}
        closeOnEscape={!dismissSubmitting}
      >
        <Stack gap='sm'>
          <Textarea
            label={t`Reason`}
            description={t`Recorded on the finding's audit trail.`}
            data-autofocus
            required
            maxLength={2000}
            value={dismissReason}
            error={dismissError}
            onChange={(event) => {
              setDismissReason(event.currentTarget.value);
              if (!dismissConflict) {
                setDismissError(null);
              }
            }}
          />
          <Group justify='flex-end' gap='xs'>
            <Button
              variant='default'
              disabled={dismissSubmitting}
              onClick={closeDismiss}
            >
              {t`Cancel`}
            </Button>
            <Button
              color='red'
              loading={dismissSubmitting}
              disabled={!dismissReason.trim() || dismissConflict}
              onClick={() => void submitDismiss()}
            >
              {t`Dismiss`}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
