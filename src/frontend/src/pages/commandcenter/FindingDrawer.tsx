import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Divider,
  Drawer,
  Group,
  Loader,
  Modal,
  Popover,
  Stack,
  Table,
  Text,
  Textarea,
  Timeline
} from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import { notifications } from '@mantine/notifications';
import {
  IconCheck,
  IconClockPause,
  IconRefresh,
  IconTrashX,
  IconUserMinus,
  IconUserPlus
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { StylishText } from '@lib/components/StylishText';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { navigateToLink } from '@lib/functions/Navigation';
import {
  LocalDateTime,
  type RiskActionLink,
  SeverityIndicator,
  governedRiskActionRoute,
  parseRiskFindingDetail
} from '../../components/riskradar/RiskRadarCommon';
import { useApi } from '../../contexts/ApiContext';
import { useUserState } from '../../states/UserState';

function stringifyValue(value: any): string {
  if (value == null) {
    return '-';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return `${value}`;
}

function KeyValueTable({
  data
}: Readonly<{ data: Record<string, any> | null | undefined }>) {
  const entries = Object.entries(data ?? {});

  if (entries.length === 0) {
    return (
      <Text size='sm' c='dimmed'>
        -
      </Text>
    );
  }

  return (
    <Table withTableBorder verticalSpacing={4}>
      <Table.Tbody>
        {entries.map(([key, value]) => (
          <Table.Tr key={key}>
            <Table.Td>
              <Text size='sm' fw={500}>
                {key}
              </Text>
            </Table.Td>
            <Table.Td>
              <Text size='sm'>{stringifyValue(value)}</Text>
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

/**
 * Right-hand drawer showing the full detail of a single risk finding,
 * including evidence, event history and ownership actions.
 */
export default function FindingDrawer({
  findingId,
  scope,
  authorizationFingerprint,
  opened,
  onClose
}: Readonly<{
  findingId: number | null;
  scope: string;
  authorizationFingerprint: string;
  opened: boolean;
  onClose: () => void;
}>) {
  const api = useApi();
  const user = useUserState();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [acknowledgeOpen, setAcknowledgeOpen] = useState<boolean>(false);
  const [dismissOpen, setDismissOpen] = useState<boolean>(false);
  const [dismissReason, setDismissReason] = useState<string>('');
  const [snoozeOpen, setSnoozeOpen] = useState<boolean>(false);
  const [snoozeUntil, setSnoozeUntil] = useState<string | null>(null);

  const detailQuery = useQuery({
    queryKey: ['risk-finding', findingId, scope, authorizationFingerprint],
    enabled: opened && !!findingId && !!scope && !!authorizationFingerprint,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_finding_detail, findingId))
        .then((response) => parseRiskFindingDetail(response.data, scope))
  });

  const detail = detailQuery.data;
  const governedLinks = useMemo(
    () =>
      (detail?.action_links ?? [])
        .map((link) => ({ link, route: governedRiskActionRoute(link) }))
        .filter(
          (
            entry
          ): entry is {
            link: RiskActionLink;
            route: string;
          } => entry.route != null
        ),
    [detail]
  );

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['risk-findings'] });
    queryClient.invalidateQueries({ queryKey: ['risk-finding', findingId] });
    queryClient.invalidateQueries({ queryKey: ['command-center-summary'] });
    queryClient.invalidateQueries({ queryKey: ['risk-rule-health'] });
  }, [queryClient, findingId]);

  const handleCommandError = useCallback(
    (error: any) => {
      if (error?.response?.status === 409) {
        notifications.show({
          title: t`Conflict`,
          message: t`Finding changed — refresh and retry`,
          color: 'orange'
        });
        // Refetch so the next attempt carries the current version instead of
        // deterministically repeating the same conflict.
        invalidate();
      } else {
        notifications.show({
          title: t`Action failed`,
          message:
            error?.response?.data?.detail ??
            t`The action could not be completed`,
          color: 'red'
        });
      }
    },
    [invalidate]
  );

  // Run an ownership command against the finding. Every command carries the
  // expected version (optimistic concurrency) and an idempotency key.
  const runCommand = useCallback(
    async (
      endpoint: ApiEndpoints,
      extra: Record<string, any> = {}
    ): Promise<boolean> => {
      if (!detail) {
        return false;
      }
      try {
        await api.post(apiUrl(endpoint, detail.pk), {
          expected_version: detail.version,
          idempotency_key: crypto.randomUUID(),
          ...extra
        });
        invalidate();
        return true;
      } catch (error: any) {
        handleCommandError(error);
        return false;
      }
    },
    [api, detail, invalidate, handleCommandError]
  );

  const recheck = useCallback(async () => {
    if (!detail) {
      return;
    }
    try {
      await api.post(apiUrl(ApiEndpoints.risk_finding_recheck, detail.pk));
      notifications.show({
        title: t`Recheck queued`,
        message: t`The finding will be re-evaluated shortly`,
        color: 'blue'
      });
      invalidate();
    } catch (error: any) {
      handleCommandError(error);
    }
  }, [api, detail, invalidate, handleCommandError]);

  const isOwner = !!detail && detail.owner === user.userId();
  const snoozeMoment = snoozeUntil ? dayjs(snoozeUntil) : null;
  const snoozeInFuture =
    !!snoozeMoment && snoozeMoment.isValid() && snoozeMoment.isAfter(dayjs());

  return (
    <Drawer
      position='right'
      size='40%'
      opened={opened}
      onClose={onClose}
      title={
        <Group gap='xs' wrap='nowrap'>
          <StylishText size='lg'>{detail?.title ?? t`Finding`}</StylishText>
        </Group>
      }
    >
      {detailQuery.isLoading && <Loader />}
      {detailQuery.isError && (
        <Alert color='gray' title={t`Finding unavailable`}>
          <Text size='sm'>{t`This finding does not exist, is out of your scope, or the feature is disabled.`}</Text>
        </Alert>
      )}
      {detail && (
        <Stack gap='sm'>
          <Group gap='xs' wrap='wrap'>
            <SeverityIndicator severity={detail.severity} />
            <Badge variant='light'>{detail.state}</Badge>
            <Text size='sm' data-testid='finding-rule-code'>
              {detail.rule_code}
            </Text>
            <Badge variant='outline' color='gray'>
              {t`Version`} {detail.rule_version}
            </Badge>
          </Group>
          <Group gap='xs'>
            <Text size='sm' c='dimmed'>
              {t`Owner`}:
            </Text>
            <Text size='sm'>{detail.owner_username ?? t`Unassigned`}</Text>
          </Group>
          <Text size='sm'>{detail.summary}</Text>
          <Group gap='xs'>
            <Text size='sm' c='dimmed'>
              {t`Source as of`}:
            </Text>
            <Text size='sm'>
              <LocalDateTime value={detail.source_as_of} />
            </Text>
          </Group>

          <Divider />
          <Group gap='xs' wrap='wrap'>
            <Button
              size='xs'
              leftSection={<IconCheck size={14} />}
              data-testid='finding-acknowledge'
              onClick={() => setAcknowledgeOpen(true)}
            >
              {t`Acknowledge`}
            </Button>
            {isOwner ? (
              <Button
                size='xs'
                variant='default'
                leftSection={<IconUserMinus size={14} />}
                data-testid='finding-unassign'
                onClick={() =>
                  runCommand(ApiEndpoints.risk_finding_assign, {
                    owner_id: null
                  })
                }
              >
                {t`Unassign`}
              </Button>
            ) : (
              <Button
                size='xs'
                variant='default'
                leftSection={<IconUserPlus size={14} />}
                data-testid='finding-assign'
                onClick={() =>
                  runCommand(ApiEndpoints.risk_finding_assign, {
                    owner_id: user.userId()
                  })
                }
              >
                {t`Assign to me`}
              </Button>
            )}
            <Popover
              opened={snoozeOpen}
              onChange={setSnoozeOpen}
              position='bottom'
              withArrow
            >
              <Popover.Target>
                <Button
                  size='xs'
                  variant='default'
                  leftSection={<IconClockPause size={14} />}
                  data-testid='finding-snooze'
                  onClick={() => setSnoozeOpen((open) => !open)}
                >
                  {t`Snooze`}
                </Button>
              </Popover.Target>
              <Popover.Dropdown>
                <Stack gap='xs'>
                  <DateTimePicker
                    label={t`Snooze until`}
                    value={snoozeUntil}
                    onChange={setSnoozeUntil}
                    minDate={new Date()}
                  />
                  <Button
                    size='xs'
                    disabled={!snoozeInFuture}
                    data-testid='finding-snooze-confirm'
                    onClick={async () => {
                      if (!snoozeMoment || !snoozeInFuture) {
                        return;
                      }
                      const ok = await runCommand(
                        ApiEndpoints.risk_finding_snooze,
                        { snooze_until: snoozeMoment.toISOString() }
                      );
                      if (ok) {
                        setSnoozeOpen(false);
                        setSnoozeUntil(null);
                      }
                    }}
                  >
                    {t`Snooze`}
                  </Button>
                </Stack>
              </Popover.Dropdown>
            </Popover>
            <Button
              size='xs'
              variant='default'
              color='red'
              leftSection={<IconTrashX size={14} />}
              data-testid='finding-dismiss'
              onClick={() => setDismissOpen(true)}
            >
              {t`Dismiss`}
            </Button>
            <Button
              size='xs'
              variant='subtle'
              leftSection={<IconRefresh size={14} />}
              data-testid='finding-recheck'
              onClick={recheck}
            >
              {t`Recheck`}
            </Button>
          </Group>
          <Divider />

          {governedLinks.length > 0 && (
            <>
              <StylishText size='sm'>{t`Related`}</StylishText>
              <Group gap='xs' wrap='wrap'>
                {governedLinks.map(({ link, route }) => (
                  <Button
                    key={`${link.target_kind}-${link.target_id}`}
                    size='xs'
                    variant='light'
                    onClick={(event: any) =>
                      navigateToLink(route, navigate, event)
                    }
                  >
                    {link.label}
                  </Button>
                ))}
              </Group>
            </>
          )}

          <StylishText size='sm'>{t`Severity Factors`}</StylishText>
          <KeyValueTable data={detail.severity_factors} />

          <StylishText size='sm'>{t`Evidence`}</StylishText>
          <div data-testid='finding-evidence'>
            <KeyValueTable data={detail.evidence} />
          </div>

          <StylishText size='sm'>{t`History`}</StylishText>
          {detail.events?.length > 0 ? (
            <Timeline bulletSize={16} lineWidth={2}>
              {detail.events.map((event) => (
                <Timeline.Item key={event.pk} title={event.event_type}>
                  <Text size='xs' c='dimmed'>
                    {event.actor_username ?? t`System`}
                    {event.reason ? ` — ${event.reason}` : ''}
                  </Text>
                  <Text size='xs' c='dimmed'>
                    <LocalDateTime value={event.created_at} />
                  </Text>
                </Timeline.Item>
              ))}
            </Timeline>
          ) : (
            <Text size='sm' c='dimmed'>
              {t`No events recorded`}
            </Text>
          )}
        </Stack>
      )}

      <Modal
        opened={acknowledgeOpen}
        onClose={() => setAcknowledgeOpen(false)}
        title={t`Acknowledge Finding`}
      >
        <Stack gap='sm'>
          <Text size='sm'>{t`Acknowledge this finding? You confirm that you have seen it.`}</Text>
          <Group justify='flex-end' gap='xs'>
            <Button
              variant='default'
              onClick={() => setAcknowledgeOpen(false)}
            >{t`Cancel`}</Button>
            <Button
              data-testid='finding-acknowledge-confirm'
              onClick={async () => {
                const ok = await runCommand(
                  ApiEndpoints.risk_finding_acknowledge
                );
                if (ok) {
                  setAcknowledgeOpen(false);
                }
              }}
            >
              {t`Acknowledge`}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={dismissOpen}
        onClose={() => setDismissOpen(false)}
        title={t`Dismiss Finding`}
      >
        <Stack gap='sm'>
          <Textarea
            label={t`Reason`}
            description={t`A reason is required to dismiss a finding`}
            value={dismissReason}
            onChange={(event) => setDismissReason(event.currentTarget.value)}
            data-testid='finding-dismiss-reason'
            required
          />
          <Group justify='flex-end' gap='xs'>
            <Button
              variant='default'
              onClick={() => setDismissOpen(false)}
            >{t`Cancel`}</Button>
            <Button
              color='red'
              disabled={!dismissReason.trim()}
              data-testid='finding-dismiss-confirm'
              onClick={async () => {
                const ok = await runCommand(ApiEndpoints.risk_finding_dismiss, {
                  reason: dismissReason.trim()
                });
                if (ok) {
                  setDismissOpen(false);
                  setDismissReason('');
                }
              }}
            >
              {t`Dismiss`}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Drawer>
  );
}
