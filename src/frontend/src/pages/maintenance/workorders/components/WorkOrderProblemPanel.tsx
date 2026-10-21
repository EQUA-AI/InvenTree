import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Card,
  Group,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

import type { InvestigationExplanation } from '@lib/types/WorkExecution';
import type { RepairPacketOverview } from '@lib/types/WorkOrderOverview';
import { unattributedVerificationLabel } from '../../../tasks/workorders/execution/ExecutionFacts';
import { diagnosisVerification } from '../../../tasks/workorders/execution/investigationProjection';

function analysisStatusLabel(status: string | null): string {
  switch (status) {
    case 'available':
      return t`Evidence available`;
    case 'stale':
      return t`Data is stale`;
    case 'insufficient':
      return t`Data is insufficient`;
    case 'unavailable':
      return t`No data available`;
    default:
      return t`Not analyzed`;
  }
}

/**
 * Problem and impact for a packet-owned work order.
 *
 * The likely cause is labelled *Preliminary results* until a technician verifies
 * it, and never "Diagnosis" before then. Calling an unverified model output a
 * diagnosis is the mistake that gets a machine worked on for the wrong reason,
 * so the label is derived from the server's verification flag rather than from
 * how confident the text sounds. A non-preliminary flag without a projected
 * verifier and time is shown as exactly that, not as preliminary. The generator's confidence number is not
 * shown (R6 A08.2): certainty is read from cited evidence on Diagnose.
 */
export function WorkOrderProblemPanel({
  packet,
  explanation
}: Readonly<{
  packet: RepairPacketOverview;
  explanation?: InvestigationExplanation | null;
}>) {
  const diagnosis = packet.diagnosis ?? {};
  const likelyCause = String(diagnosis.likely_cause ?? '');
  const verification = diagnosisVerification(
    packet.diagnosis_is_preliminary,
    explanation?.verified_by,
    explanation?.verified_at
  );
  const preliminary = verification === 'preliminary';

  return (
    <Card withBorder padding='md'>
      <Stack gap='md'>
        <Group justify='space-between' align='center' wrap='wrap'>
          <Text component='h2' size='h4' fw={600}>{t`Problem and impact`}</Text>
          <Group gap='xs'>
            <Badge variant='light' color='gray'>
              {t`Criticality: ${packet.criticality}`}
            </Badge>
            <Badge variant='outline' color='gray'>
              {analysisStatusLabel(packet.diagnosis_status)}
            </Badge>
          </Group>
        </Group>

        <SimpleGrid cols={{ base: 1, md: 3 }}>
          <Stack gap={2}>
            <Text size='xs' c='dimmed' fw={500}>{t`Fault`}</Text>
            <Text size='sm'>{packet.fault_summary || t`Not recorded`}</Text>
          </Stack>
          <Stack gap={2}>
            <Text size='xs' c='dimmed' fw={500}>{t`Symptom`}</Text>
            <Text size='sm'>{packet.symptom || t`Not recorded`}</Text>
          </Stack>
          <Stack gap={2}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Production impact`}
            </Text>
            <Text size='sm'>{packet.production_impact || t`Not recorded`}</Text>
          </Stack>
        </SimpleGrid>

        {likelyCause && (
          <Stack gap='xs'>
            <Group gap='xs'>
              <Text size='xs' c='dimmed' fw={500}>
                {verification === 'verified'
                  ? t`Verified diagnosis`
                  : verification === 'unattributed'
                    ? unattributedVerificationLabel()
                    : t`Preliminary results`}
              </Text>
            </Group>
            <Text size='sm'>{likelyCause}</Text>
            {preliminary && (
              <Alert
                role='note'
                color='yellow'
                variant='light'
                icon={<IconAlertTriangle size={16} />}
              >
                {t`Preliminary — not technician verified. This restates what the evidence shows; it is not a confirmed cause and it authorizes nothing.`}
              </Alert>
            )}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

export default WorkOrderProblemPanel;
