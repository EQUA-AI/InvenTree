import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Group,
  Loader,
  Menu,
  Select,
  Stack,
  Table,
  Text,
  Tooltip
} from '@mantine/core';
import { TextInput } from '@mantine/core';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import {
  IconCheck,
  IconClockPause,
  IconDotsVertical,
  IconRefresh,
  IconX
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

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
  dismiss: ApiEndpoints.risk_finding_dismiss,
  recheck: ApiEndpoints.risk_finding_recheck
} as const;

type FindingCommand = keyof typeof COMMAND_ENDPOINTS;

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
  const {
    scopes,
    scope,
    authorizationFingerprint,
    setScope,
    unavailable,
    isLoading
  } = useRiskScope();

  const findingsQuery = useQuery({
    queryKey: ['risk-findings', scope, authorizationFingerprint, 'panel'],
    enabled: !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_finding_list), {
          params: { scope: scope, limit: PANEL_LIMIT, offset: 0 }
        })
        .then((response) => parseRiskFindingListResponse(response.data, scope))
  });

  const runCommand = useCallback(
    (
      finding: RiskFinding,
      command: FindingCommand,
      extra: Record<string, unknown> = {}
    ) => {
      api
        .post(apiUrl(COMMAND_ENDPOINTS[command], finding.pk), {
          expected_version: finding.version,
          idempotency_key: crypto.randomUUID(),
          ...extra
        })
        .then(() => {
          queryClient.invalidateQueries({ queryKey: ['risk-findings'] });
        })
        .catch(() => {
          notifications.show({
            title: t`Command failed`,
            message: t`The finding may have changed - refresh and retry.`,
            color: 'red'
          });
          queryClient.invalidateQueries({ queryKey: ['risk-findings'] });
        });
    },
    [api, queryClient]
  );

  const dismissWithReason = useCallback(
    (finding: RiskFinding) => {
      let reason = '';
      modals.openConfirmModal({
        title: t`Dismiss finding`,
        children: (
          <TextInput
            label={t`Reason`}
            description={t`Recorded on the finding's audit trail.`}
            data-autofocus
            onChange={(event) => {
              reason = event.currentTarget.value;
            }}
          />
        ),
        labels: { confirm: t`Dismiss`, cancel: t`Cancel` },
        confirmProps: { color: 'red' },
        onConfirm: () => runCommand(finding, 'dismiss', { reason: reason })
      });
    },
    [runCommand]
  );

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

  const findings: RiskFinding[] = findingsQuery.data?.results ?? [];
  const degradedSources = (findingsQuery.data?.source_freshness ?? [])
    .filter((source) => source.degraded)
    .map((source) => source.source);

  return (
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
              onClick={() =>
                queryClient.invalidateQueries({ queryKey: ['risk-findings'] })
              }
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
                        onClick={() => runCommand(finding, 'acknowledge')}
                      >
                        {t`Acknowledge`}
                      </Menu.Item>
                      <Menu.Item
                        leftSection={<IconClockPause size={14} />}
                        onClick={() =>
                          runCommand(finding, 'snooze', {
                            snooze_until: new Date(
                              Date.now() + 24 * 60 * 60 * 1000
                            ).toISOString()
                          })
                        }
                      >
                        {t`Snooze 24h`}
                      </Menu.Item>
                      <Menu.Item
                        leftSection={<IconRefresh size={14} />}
                        onClick={() => runCommand(finding, 'recheck')}
                      >
                        {t`Recheck`}
                      </Menu.Item>
                      <Menu.Item
                        color='red'
                        leftSection={<IconX size={14} />}
                        onClick={() => dismissWithReason(finding)}
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
      <Text size='xs' c='dimmed'>
        {t`Data as of`} <LocalDateTime value={findingsQuery.data?.as_of} />
        {' - '}
        {findingsQuery.data?.count ?? 0} {t`findings`}
      </Text>
    </Stack>
  );
}
