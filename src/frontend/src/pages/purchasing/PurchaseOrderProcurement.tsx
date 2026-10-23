import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { ProcurementRecordedDocument } from '@lib/types/Procurement';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Group,
  Modal,
  Paper,
  SimpleGrid,
  Skeleton,
  Stack,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../App';
import { MissingValue } from '../../components/items/ValueTooltip';
import procurementClasses from '../../components/procurement/Procurement.module.css';
import {
  AcknowledgmentFact,
  DocumentFact,
  PlacedFact,
  SourceFacts,
  procurementTime
} from '../../components/procurement/ProcurementFacts';
import {
  DocumentDownload,
  RecordedDocumentForm
} from '../../components/procurement/RecordedDocument';
import { useUserState } from '../../states/UserState';
import type { MaterialReadout } from '../tasks/workorders/materials';

/** Native PO remains the commercial authority. Work context comes only from an
 * explicit scoped Work ID in a Work → PO link, never a Part or latest-PO guess.
 */
export function PurchaseOrderProcurement({
  orderId,
  issueDate
}: Readonly<{ orderId: number; issueDate?: string | null }>) {
  const [params] = useSearchParams();
  const rawId = params.get('workOrder');
  const workId =
    rawId && /^\d+$/.test(rawId) && Number.isSafeInteger(Number(rawId))
      ? Number(rawId)
      : null;
  const user = useUserState();
  const [record, setRecord] = useState(false);
  const emailVisible =
    user.isSuperuser() || !!user.user?.permissions?.email?.includes('view');
  const work = useQuery({
    queryKey: ['work-order-materials', workId],
    enabled: !!workId && user.hasViewRole(UserRoles.work_order),
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get<MaterialReadout>(
          `/api/tasks/work-orders/${workId}/materials/`,
          { signal }
        )
      ).data
  });
  const identity = useQuery({
    queryKey: ['procurement-work-identity', workId],
    enabled: !!workId && user.hasViewRole(UserRoles.work_order),
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get<{
          reference: string | null;
          machine: number | null;
          machine_name: string | null;
        }>(`/api/tasks/work-orders/${workId}/`, { signal })
      ).data
  });
  const documents = useQuery({
    queryKey: ['procurement-documents', orderId],
    enabled: emailVisible,
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get<ProcurementRecordedDocument[]>(
          `/api/order/po/${orderId}/recorded-evidence/`,
          { signal }
        )
      ).data
  });
  const workStatus = (work.error as { response?: { status?: number } })
    ?.response?.status;
  const documentStatus = (documents.error as { response?: { status?: number } })
    ?.response?.status;
  const identityStatus = (identity.error as { response?: { status?: number } })
    ?.response?.status;
  const canRetainWork =
    user.hasViewRole(UserRoles.work_order) &&
    (!work.isError || ![401, 403, 404].includes(workStatus ?? 0)) &&
    (!identity.isError || ![401, 403, 404].includes(identityStatus ?? 0));
  const canRetainDocuments =
    !documents.isError || ![401, 403, 404].includes(documentStatus ?? 0);
  const matches = canRetainWork
    ? (work.data?.procurement?.lines.flatMap((line) =>
        line.orders
          .filter((order) => order.purchase_order_id === orderId)
          .map((order) => ({ line, order }))
      ) ?? [])
    : [];
  return (
    <Paper
      withBorder
      radius='sm'
      p='sm'
      data-testid='po-procurement'
      className={procurementClasses.context}
    >
      <Stack gap='sm'>
        {(work.isError || identity.isError) && (
          <Alert color='orange' p='xs'>
            <Group justify='space-between'>
              <Text size='sm'>
                {matches.length
                  ? t`Work refresh failed · showing last read`
                  : t`Work context could not be read`}
              </Text>
              <Button
                size='compact-xs'
                variant='light'
                onClick={() => {
                  void work.refetch();
                  void identity.refetch();
                }}
              >{t`Retry`}</Button>
            </Group>
          </Alert>
        )}
        {work.isPending && work.fetchStatus === 'fetching' && (
          <Skeleton height={40} />
        )}
        {matches.map(({ line, order }) => (
          <Stack key={order.purchase_order_line_id} gap='sm'>
            <Group gap='md' align='flex-start'>
              <Anchor
                component={Link}
                to={`${getDetailUrl(ModelType.workorder, workId!)}parts/`}
                size='sm'
                fw={600}
              >
                {identity.data?.reference || t`Work ${workId!}`}
              </Anchor>
              {identity.data?.machine && identity.data.machine_name && (
                <Anchor
                  component={Link}
                  to={getDetailUrl(
                    ModelType.assetmachine,
                    identity.data.machine
                  )}
                  size='sm'
                >
                  {identity.data.machine_name}
                </Anchor>
              )}
              <Text size='sm'>
                {line.required_quantity}
                {line.base_uom ? ` ${line.base_uom}` : ''}
              </Text>
              {line.needed_at && (
                <Text size='sm'>
                  {procurementTime(line.needed_at)}
                  {line.dependent_step ? ` · ${line.dependent_step}` : ''}
                </Text>
              )}
            </Group>
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing='md'>
              <Stack gap={4}>
                <Text size='xs' fw={600} c='dimmed'>{t`Source`}</Text>
                <SourceFacts line={line} workId={workId!} />
              </Stack>
              <Stack gap={4}>
                <Text size='xs' fw={600} c='dimmed'>{t`Placed`}</Text>
                <PlacedFact order={order} />
              </Stack>
              <Stack gap={4}>
                <Text size='xs' fw={600} c='dimmed'>{t`Recorded email`}</Text>
                <DocumentFact
                  order={
                    emailVisible && canRetainDocuments
                      ? order
                      : {
                          ...order,
                          recorded_document: null,
                          recorded_email: null
                        }
                  }
                />
              </Stack>
              <Stack gap={4}>
                <Text size='xs' fw={600} c='dimmed'>{t`Acknowledgment`}</Text>
                <AcknowledgmentFact order={order} workId={workId!} />
              </Stack>
            </SimpleGrid>
          </Stack>
        ))}
        {!matches.length && (
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing='md'>
            <Stack gap={4}>
              <Text size='xs' fw={600} c='dimmed'>{t`Placed`}</Text>
              {issueDate ? (
                <Text size='sm'>{issueDate}</Text>
              ) : (
                <MissingValue
                  reason={t`Native placement date is not recorded`}
                />
              )}
            </Stack>
            <Stack gap={4}>
              <Text size='xs' fw={600} c='dimmed'>{t`Recorded email`}</Text>
              {emailVisible && canRetainDocuments && documents.data?.length ? (
                documents.data.map((document) => (
                  <Stack key={document.pk} gap={2}>
                    <Text size='sm'>{t`Recorded · not sent`}</Text>
                    <Text size='xs'>
                      {procurementTime(document.recorded_at)}
                    </Text>
                    <DocumentDownload document={document} />
                  </Stack>
                ))
              ) : (
                <MissingValue
                  reason={t`No recorded PDF or email available to you`}
                />
              )}
            </Stack>
            <Stack gap={4}>
              <Text size='xs' fw={600} c='dimmed'>{t`Acknowledgment`}</Text>
              <MissingValue
                reason={t`Open this PO from its Work record to read an exact line acknowledgment`}
              />
            </Stack>
          </SimpleGrid>
        )}
        {documents.isError && emailVisible && (
          <Alert color='red' p='xs'>
            <Group justify='space-between'>
              <Text size='sm'>
                {documents.data && canRetainDocuments
                  ? t`Documents refresh failed · showing last read`
                  : t`Documents could not be read`}
              </Text>
              <Button
                size='compact-xs'
                variant='light'
                onClick={() => void documents.refetch()}
              >{t`Retry`}</Button>
            </Group>
          </Alert>
        )}
        {emailVisible && user.hasChangeRole(UserRoles.purchase_order) && (
          <Group justify='flex-end'>
            <Button
              size='xs'
              variant='light'
              onClick={() => setRecord(true)}
            >{t`Record PDF and email`}</Button>
          </Group>
        )}
      </Stack>
      <Modal
        opened={record}
        onClose={() => setRecord(false)}
        title={t`Record PDF and email · no send`}
        size='lg'
      >
        <RecordedDocumentForm
          key={orderId}
          orderId={orderId}
          onSaved={() => setRecord(false)}
        />
      </Modal>
    </Paper>
  );
}
