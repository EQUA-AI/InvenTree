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

import type { RepairPacketOverview } from '@lib/types/WorkOrderOverview';

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
 * how confident the text sounds.
 */
export function WorkOrderProblemPanel({
  packet
}: Readonly<{ packet: RepairPacketOverview }>) {
  const diagnosis = packet.diagnosis ?? {};
  const likelyCause = String(diagnosis.likely_cause ?? '');
  const confidence = Number(diagnosis.confidence ?? 0);
  const preliminary = packet.diagnosis_is_preliminary;

  return (
    <Card withBorder padding='md'>
      <Stack gap='md'>
        <Group justify='space-between' align='center' wrap='wrap'>
          <Text fw={600}>{t`Problem and impact`}</Text>
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
            <Text size='sm'>{packet.fault_summary || '—'}</Text>
          </Stack>
          <Stack gap={2}>
            <Text size='xs' c='dimmed' fw={500}>{t`Symptom`}</Text>
            <Text size='sm'>{packet.symptom || '—'}</Text>
          </Stack>
          <Stack gap={2}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Production impact`}
            </Text>
            <Text size='sm'>{packet.production_impact || '—'}</Text>
          </Stack>
        </SimpleGrid>

        {likelyCause && (
          <Stack gap='xs'>
            <Group gap='xs'>
              <Text size='xs' c='dimmed' fw={500}>
                {preliminary ? t`Preliminary results` : t`Verified diagnosis`}
              </Text>
              {confidence > 0 && (
                <Badge size='sm' variant='outline' color='gray'>
                  {t`Confidence ${Math.round(confidence * 100)}%`}
                </Badge>
              )}
            </Group>
            <Text size='sm'>{likelyCause}</Text>
            {preliminary && (
              <Alert
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
