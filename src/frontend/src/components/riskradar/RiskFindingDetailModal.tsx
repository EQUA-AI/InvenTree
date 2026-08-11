import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Code,
  Divider,
  Group,
  Loader,
  Modal,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconTool } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import {
  LocalDateTime,
  type RiskFindingDetail,
  SeverityIndicator,
  governedRiskActionRoute,
  parseRiskFindingDetail
} from './RiskRadarCommon';

/** Server-derived proposal payload from the aichat proposals rail. */
interface ProposalPayload {
  id: string;
  action_type: string;
  state: string;
  preview: Record<string, any>;
  expires_at: string;
  receipt: Record<string, any> | null;
  failure_code: string | null;
}

/** Findings whose evidence can seed a repair work-package draft. */
const DRAFTABLE_RULE = 'ANOMALY_UNADDRESSED';

function scalarEvidence(evidence: Record<string, any>): [string, string][] {
  return Object.entries(evidence)
    .filter(([, value]) => value !== null && typeof value !== 'object')
    .map(([key, value]) => [key, `${value}`]);
}

/**
 * Read-only finding detail plus the governed corrective actions: verified
 * deep links from the server's action_links, and (for unaddressed-anomaly
 * findings) a draft-repair flow through the aichat proposals rail. The
 * proposal is created AND confirmed by the clicking user — the server never
 * self-approves; the preview shown before confirmation is entirely
 * server-derived.
 */
export default function RiskFindingDetailModal({
  findingPk,
  scope,
  onClose
}: Readonly<{
  findingPk: number | null;
  scope: string;
  onClose: () => void;
}>) {
  const api = useApi();
  const navigate = useNavigate();
  const [proposal, setProposal] = useState<ProposalPayload | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [deciding, setDeciding] = useState(false);

  useEffect(() => {
    setProposal(null);
    setDrafting(false);
    setDeciding(false);
  }, [findingPk]);

  const detailQuery = useQuery({
    queryKey: ['risk-finding-detail', scope, findingPk],
    enabled: findingPk != null && !!scope,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.risk_finding_detail, findingPk), {
          params: { scope }
        })
        .then((response) => parseRiskFindingDetail(response.data, scope))
  });

  const detail: RiskFindingDetail | undefined = detailQuery.data;

  const anomalyEvidence = detail?.evidence ?? {};
  const canDraftRepair =
    detail?.rule_code === DRAFTABLE_RULE &&
    Number.isInteger(anomalyEvidence.machine_id) &&
    Number.isInteger(anomalyEvidence.anomaly_id);

  const draftRepair = useCallback(async () => {
    if (!detail || drafting) return;
    setDrafting(true);
    try {
      const evidence = detail.evidence;
      const fault: Record<string, string> = {
        summary: `${evidence.preliminary?.likely_cause ?? detail.title}`.slice(
          0,
          2000
        )
      };
      if (evidence.alarm_code) {
        fault.symptom = `${evidence.alarm_code}`.slice(0, 255);
      }
      const response = await api.post(
        apiUrl(ApiEndpoints.aichat_proposal_list),
        {
          action_type: 'repair_work_package.create',
          reason: `Drafted from Risk Radar finding #${detail.pk}`,
          intent: {
            machine_id: evidence.machine_id,
            title: `${detail.title}`.slice(0, 200),
            origin: 'anomaly',
            source: { anomaly_id: evidence.anomaly_id },
            fault
          }
        }
      );
      setProposal(response.data as ProposalPayload);
    } catch (error: any) {
      notifications.show({
        title: t`Draft failed`,
        message:
          error?.response?.data?.detail ??
          t`The repair work package could not be drafted.`,
        color: 'red'
      });
    } finally {
      setDrafting(false);
    }
  }, [api, detail, drafting]);

  const decide = useCallback(
    async (decision: 'confirm' | 'reject') => {
      if (!proposal || deciding) return;
      setDeciding(true);
      try {
        const endpoint =
          decision === 'confirm'
            ? ApiEndpoints.aichat_proposal_confirm
            : ApiEndpoints.aichat_proposal_reject;
        const response = await api.post(apiUrl(endpoint, proposal.id), {});
        setProposal(response.data as ProposalPayload);
        if (decision === 'confirm') {
          notifications.show({
            title: t`Repair work package created`,
            message: t`The planned work order and repair packet were created.`,
            color: 'green'
          });
        }
      } catch (error: any) {
        notifications.show({
          title: decision === 'confirm' ? t`Confirm failed` : t`Reject failed`,
          message:
            error?.response?.data?.detail ?? t`The decision was not applied.`,
          color: 'red'
        });
      } finally {
        setDeciding(false);
      }
    },
    [api, proposal, deciding]
  );

  const receiptWorkOrder = proposal?.receipt?.work_order_id;

  return (
    <Modal
      opened={findingPk != null}
      onClose={onClose}
      title={t`Finding details`}
      size='lg'
    >
      {detailQuery.isLoading && <Loader size='sm' />}
      {detailQuery.isError && (
        <Alert color='red' icon={<IconAlertTriangle size={16} />}>
          {t`The finding could not be loaded.`}
        </Alert>
      )}
      {detail && (
        <Stack gap='sm' data-testid='risk-finding-detail'>
          <Group gap='xs'>
            <SeverityIndicator severity={detail.severity} />
            <Badge variant='light' color='gray'>
              {detail.rule_code}
            </Badge>
            <Badge
              variant='light'
              color={detail.state === 'open' ? 'red' : 'gray'}
            >
              {detail.state}
            </Badge>
          </Group>
          <Text fw={600}>{detail.title}</Text>
          <Text size='sm'>{detail.summary}</Text>
          <Text size='xs' c='dimmed'>
            {t`Condition since`}{' '}
            <LocalDateTime value={detail.condition_started_at} /> {' - '}
            {t`last seen`} <LocalDateTime value={detail.last_seen} />
          </Text>

          {detail.action_links.length > 0 && (
            <Group gap='xs'>
              {detail.action_links.map((link) => {
                const route = governedRiskActionRoute(link);
                if (!route) return null;
                return (
                  <Button
                    key={`${link.target_kind}-${link.target_id}`}
                    size='xs'
                    variant='light'
                    onClick={() => navigate(route)}
                  >
                    {link.label}
                  </Button>
                );
              })}
            </Group>
          )}

          <Divider label={t`Evidence`} labelPosition='left' />
          <Table withRowBorders={false} verticalSpacing={2}>
            <Table.Tbody>
              {scalarEvidence(detail.evidence).map(([key, value]) => (
                <Table.Tr key={key}>
                  <Table.Td>
                    <Text size='xs' c='dimmed'>
                      {key}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='xs'>{value}</Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {detail.evidence.preliminary?.likely_cause && (
            <Alert color='blue' variant='light'>
              <Text size='xs'>
                {t`Preliminary analysis`}:{' '}
                {`${detail.evidence.preliminary.likely_cause}`}
              </Text>
            </Alert>
          )}

          {canDraftRepair && !proposal && (
            <Button
              leftSection={<IconTool size={16} />}
              loading={drafting}
              data-testid='draft-repair-package'
              onClick={() => void draftRepair()}
            >
              {t`Draft repair work package`}
            </Button>
          )}

          {proposal && (
            <Stack gap='xs' data-testid='repair-draft-proposal'>
              <Divider
                label={t`Repair work package draft`}
                labelPosition='left'
              />
              {proposal.preview?.note && (
                <Text size='xs' c='dimmed'>
                  {proposal.preview.note}
                </Text>
              )}
              <Text size='sm'>
                {t`Machine`}: {proposal.preview?.machine_name ?? '—'}
              </Text>
              <Text size='sm'>
                {t`Title`}: {proposal.preview?.proposed_title ?? '—'}
              </Text>
              {(proposal.preview?.duplicate_open_repairs?.length ?? 0) > 0 && (
                <Alert color='yellow' icon={<IconAlertTriangle size={16} />}>
                  {t`Open repair work already exists for this machine — confirm only if this is genuinely new work.`}
                </Alert>
              )}
              {proposal.state === 'pending' && (
                <Group gap='xs'>
                  <Button
                    color='green'
                    size='xs'
                    loading={deciding}
                    data-testid='confirm-repair-draft'
                    onClick={() => void decide('confirm')}
                  >
                    {t`Confirm`}
                  </Button>
                  <Button
                    variant='default'
                    size='xs'
                    disabled={deciding}
                    onClick={() => void decide('reject')}
                  >
                    {t`Discard draft`}
                  </Button>
                </Group>
              )}
              {proposal.state === 'executed' && (
                <Group gap='xs'>
                  <Badge color='green'>{t`Created`}</Badge>
                  {Number.isInteger(receiptWorkOrder) && (
                    <Button
                      size='xs'
                      variant='light'
                      onClick={() =>
                        navigate(`/maintenance/work-orders/${receiptWorkOrder}`)
                      }
                    >
                      {t`Open work order`}
                    </Button>
                  )}
                </Group>
              )}
              {proposal.state === 'rejected' && (
                <Badge color='gray'>{t`Draft discarded`}</Badge>
              )}
              {proposal.failure_code && (
                <Alert color='red'>
                  <Code>{proposal.failure_code}</Code>
                </Alert>
              )}
            </Stack>
          )}
        </Stack>
      )}
    </Modal>
  );
}
