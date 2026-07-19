import { t } from '@lingui/core/macro';
import { Alert, Badge, Grid, Loader, Stack, Table, Text } from '@mantine/core';
import {
  IconAlertTriangle,
  IconChecklist,
  IconGavel,
  IconInfoCircle,
  IconListCheck
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useMemo } from 'react';
import { useParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import type { PanelType } from '@lib/types/Panel';
import {
  type DetailsField,
  DetailsTable
} from '../../components/details/Details';
import { ItemDetailsGrid } from '../../components/details/ItemDetails';
import InstanceDetail from '../../components/nav/InstanceDetail';
import { PageDetail } from '../../components/nav/PageDetail';
import { PanelGroup } from '../../components/panels/PanelGroup';
import { useApi } from '../../contexts/ApiContext';
import { useInstance } from '../../hooks/UseInstance';
import {
  verificationPurposeLabel,
  verificationStateLabel
} from '../../tables/part/PartVerificationTable';

interface VerificationSession {
  pk: number;
  reference: string;
  purpose: string;
  state: string;
  revision: number;
  policy_key: string;
  policy_version: number;
  universe_complete: boolean;
  stale_reason: string;
  expires_at: string | null;
}

interface VerificationRequirement {
  pk: number;
  key: string;
  operator: string;
  value: unknown;
  unit: string;
  hard_constraint: boolean;
  resolution: string;
  blocker_code: string;
}

interface ReasonRecord {
  reason_code?: string;
}

interface CandidateEvaluation {
  pk: number;
  candidate_name: string;
  candidate_ipn: string | null;
  eligible: boolean;
  rank: number | null;
  rank_value: string | null;
  retrieval_tiers: string[];
  hard_conflicts: ReasonRecord[];
  missing_attributes: ReasonRecord[];
}

interface VerificationDecision {
  pk: number;
  kind: string;
  decided_at: string | null;
  valid_until: string | null;
  reason: string;
  selected_part: number | null;
}

/** Render an arbitrary JSON value as plain text. */
function stringifyValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'string') {
    return value;
  }
  return JSON.stringify(value);
}

/** Render an ISO timestamp as locale text (empty when absent). */
function formatTimestamp(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : '';
}

/** Translated text label for a decision kind value. */
function decisionKindLabel(kind: string): string {
  switch (kind) {
    case 'confirmed':
      return t`Confirmed`;
    case 'no_safe_match':
      return t`No Safe Match`;
    default:
      return kind;
  }
}

/** Loading / empty wrapper for the child data panels. */
function PanelState({
  isLoading,
  isError,
  empty,
  children
}: Readonly<{
  isLoading: boolean;
  isError: boolean;
  empty: boolean;
  children: ReactNode;
}>) {
  if (isLoading) {
    return <Loader />;
  }
  if (isError) {
    return <Text c='red'>{t`Unable to load records`}</Text>;
  }
  if (empty) {
    return <Text c='dimmed'>{t`No records found`}</Text>;
  }
  return <>{children}</>;
}

/**
 * Detail page for a single Right-Part Finder verification session.
 *
 * Read-only slice: no confirm / evaluate affordances are rendered here.
 */
export default function RightPartFinderDetail() {
  const { id } = useParams();
  const api = useApi();

  const { instance: session, instanceQuery } = useInstance({
    endpoint: ApiEndpoints.part_verification_session_list,
    pk: id,
    params: {}
  });

  const sessionData = session as VerificationSession | undefined;

  const requirementsQuery = useQuery({
    queryKey: ['rpf-session', id, 'requirements'],
    enabled: !!id,
    queryFn: async () => {
      const response = await api.get<VerificationRequirement[]>(
        apiUrl(ApiEndpoints.part_verification_session_requirements, id)
      );
      return response.data;
    }
  });

  const candidatesQuery = useQuery({
    queryKey: ['rpf-session', id, 'candidates'],
    enabled: !!id,
    queryFn: async () => {
      const response = await api.get<CandidateEvaluation[]>(
        apiUrl(ApiEndpoints.part_verification_session_candidates, id)
      );
      return response.data;
    }
  });

  const decisionsQuery = useQuery({
    queryKey: ['rpf-session', id, 'decisions'],
    enabled: !!id,
    queryFn: async () => {
      const response = await api.get<VerificationDecision[]>(
        apiUrl(ApiEndpoints.part_verification_session_decisions, id)
      );
      return response.data;
    }
  });

  // Derived item with text labels so state never renders as a raw code
  const overviewItem = useMemo(() => {
    if (!sessionData?.pk) {
      return sessionData;
    }
    return {
      ...sessionData,
      purpose_label: verificationPurposeLabel(sessionData.purpose),
      state_label: verificationStateLabel(sessionData.state),
      policy_label: `${sessionData.policy_key} v${sessionData.policy_version}`,
      universe_complete_label: sessionData.universe_complete ? t`Yes` : t`No`,
      expires_at_label: formatTimestamp(sessionData.expires_at)
    };
  }, [sessionData]);

  const detailsLeft: DetailsField[] = useMemo(
    () => [
      { name: 'reference', type: 'text', label: t`Reference` },
      { name: 'purpose_label', type: 'text', label: t`Purpose` },
      { name: 'state_label', type: 'text', label: t`State` },
      { name: 'revision', type: 'text', label: t`Revision` }
    ],
    []
  );

  const detailsRight: DetailsField[] = useMemo(
    () => [
      { name: 'policy_label', type: 'text', label: t`Policy` },
      {
        name: 'universe_complete_label',
        type: 'text',
        label: t`Universe Complete`
      },
      { name: 'stale_reason', type: 'text', label: t`Stale Reason` },
      { name: 'expires_at_label', type: 'text', label: t`Expires` }
    ],
    []
  );

  const expired = useMemo(() => {
    if (!sessionData?.expires_at) {
      return false;
    }
    return new Date(sessionData.expires_at).getTime() < Date.now();
  }, [sessionData?.expires_at]);

  const staleWarning = useMemo(() => {
    if (!sessionData?.pk) {
      return null;
    }
    const messages: string[] = [];
    if (sessionData.state === 'stale' || sessionData.stale_reason) {
      messages.push(
        t`This session is stale and must be re-evaluated before its result is used.`
      );
      if (sessionData.stale_reason) {
        messages.push(`${t`Stale reason`}: ${sessionData.stale_reason}`);
      }
    }
    if (expired) {
      messages.push(t`This session has expired and must not be used.`);
    }
    if (messages.length === 0) {
      return null;
    }
    return (
      <Alert
        color='yellow'
        icon={<IconAlertTriangle size={16} />}
        title={t`Not ready for use`}
      >
        <Stack gap={4}>
          {messages.map((message) => (
            <Text key={message} size='sm'>
              {message}
            </Text>
          ))}
        </Stack>
      </Alert>
    );
  }, [sessionData, expired]);

  const requirements = requirementsQuery.data ?? [];
  const candidates = candidatesQuery.data ?? [];
  const decisions = decisionsQuery.data ?? [];

  const panels: PanelType[] = useMemo(() => {
    return [
      {
        name: 'overview',
        label: t`Overview`,
        icon: <IconInfoCircle />,
        content: sessionData?.pk ? (
          <Stack>
            {staleWarning}
            <ItemDetailsGrid>
              <Grid grow>
                <Grid.Col span={6}>
                  <DetailsTable fields={detailsLeft} item={overviewItem} />
                </Grid.Col>
                <Grid.Col span={6}>
                  <DetailsTable fields={detailsRight} item={overviewItem} />
                </Grid.Col>
              </Grid>
            </ItemDetailsGrid>
          </Stack>
        ) : null
      },
      {
        name: 'requirements',
        label: t`Requirements`,
        icon: <IconListCheck />,
        content: (
          <PanelState
            isLoading={requirementsQuery.isLoading}
            isError={requirementsQuery.isError}
            empty={requirements.length === 0}
          >
            <Table.ScrollContainer minWidth={700}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t`Key`}</Table.Th>
                    <Table.Th>{t`Operator`}</Table.Th>
                    <Table.Th>{t`Value`}</Table.Th>
                    <Table.Th>{t`Unit`}</Table.Th>
                    <Table.Th>{t`Constraint`}</Table.Th>
                    <Table.Th>{t`Resolution`}</Table.Th>
                    <Table.Th>{t`Blocker`}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {requirements.map((requirement) => (
                    <Table.Tr key={requirement.pk}>
                      <Table.Td>{requirement.key}</Table.Td>
                      <Table.Td>{requirement.operator}</Table.Td>
                      <Table.Td>{stringifyValue(requirement.value)}</Table.Td>
                      <Table.Td>{requirement.unit}</Table.Td>
                      <Table.Td>
                        {requirement.hard_constraint ? t`Hard` : t`Preference`}
                      </Table.Td>
                      <Table.Td>{requirement.resolution}</Table.Td>
                      <Table.Td>{requirement.blocker_code}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </PanelState>
        )
      },
      {
        name: 'candidates',
        label: t`Candidates`,
        icon: <IconChecklist />,
        content: (
          <PanelState
            isLoading={candidatesQuery.isLoading}
            isError={candidatesQuery.isError}
            empty={candidates.length === 0}
          >
            <Table.ScrollContainer minWidth={900}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t`Candidate`}</Table.Th>
                    <Table.Th>{t`IPN`}</Table.Th>
                    <Table.Th>{t`Status`}</Table.Th>
                    <Table.Th>{t`Rank`}</Table.Th>
                    <Table.Th>{t`Rank Value`}</Table.Th>
                    <Table.Th>{t`Retrieval Tiers`}</Table.Th>
                    <Table.Th>{t`Conflicts`}</Table.Th>
                    <Table.Th>{t`Missing`}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {candidates.map((candidate) => (
                    <Table.Tr key={candidate.pk}>
                      <Table.Td>{candidate.candidate_name}</Table.Td>
                      <Table.Td>{candidate.candidate_ipn}</Table.Td>
                      <Table.Td>
                        <Badge
                          color={candidate.eligible ? 'teal' : 'red'}
                          variant='light'
                        >
                          {candidate.eligible ? t`Eligible` : t`Excluded`}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{candidate.rank}</Table.Td>
                      <Table.Td>{candidate.rank_value}</Table.Td>
                      <Table.Td>
                        {(candidate.retrieval_tiers ?? []).join(', ')}
                      </Table.Td>
                      <Table.Td>
                        {(candidate.hard_conflicts ?? [])
                          .map((conflict) => conflict.reason_code ?? '')
                          .filter(Boolean)
                          .join(', ')}
                      </Table.Td>
                      <Table.Td>
                        {(candidate.missing_attributes ?? [])
                          .map((missing) => missing.reason_code ?? '')
                          .filter(Boolean)
                          .join(', ')}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </PanelState>
        )
      },
      {
        name: 'decisions',
        label: t`Decisions`,
        icon: <IconGavel />,
        content: (
          <PanelState
            isLoading={decisionsQuery.isLoading}
            isError={decisionsQuery.isError}
            empty={decisions.length === 0}
          >
            <Table.ScrollContainer minWidth={700}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t`Kind`}</Table.Th>
                    <Table.Th>{t`Decided At`}</Table.Th>
                    <Table.Th>{t`Valid Until`}</Table.Th>
                    <Table.Th>{t`Reason`}</Table.Th>
                    <Table.Th>{t`Selected Part`}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {decisions.map((decision) => (
                    <Table.Tr key={decision.pk}>
                      <Table.Td>{decisionKindLabel(decision.kind)}</Table.Td>
                      <Table.Td>
                        {formatTimestamp(decision.decided_at)}
                      </Table.Td>
                      <Table.Td>
                        {formatTimestamp(decision.valid_until)}
                      </Table.Td>
                      <Table.Td>{decision.reason}</Table.Td>
                      <Table.Td>{decision.selected_part}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </PanelState>
        )
      }
    ];
  }, [
    sessionData,
    overviewItem,
    staleWarning,
    detailsLeft,
    detailsRight,
    requirements,
    candidates,
    decisions,
    requirementsQuery.isLoading,
    requirementsQuery.isError,
    candidatesQuery.isLoading,
    candidatesQuery.isError,
    decisionsQuery.isLoading,
    decisionsQuery.isError
  ]);

  return (
    <InstanceDetail query={instanceQuery}>
      <Stack>
        <PageDetail
          title={sessionData?.reference ?? t`Verification Session`}
          breadcrumbs={[
            { name: t`Right-Part Finder`, url: '/part-verification/index/' }
          ]}
          actions={[]}
        />
        <PanelGroup
          pageKey='part-verification-detail'
          panels={panels}
          instance={session}
          model={ModelType.partverificationsession}
          id={sessionData?.pk ?? null}
        />
      </Stack>
    </InstanceDetail>
  );
}
