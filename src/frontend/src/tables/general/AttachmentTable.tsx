import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Group,
  Paper,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { notifications } from '@mantine/notifications';
import {
  IconCircleCheck,
  IconExclamationCircle,
  IconExternalLink,
  IconEye,
  IconFileUpload,
  IconUpload,
  IconX
} from '@tabler/icons-react';
import { type ReactNode, useCallback, useMemo, useState } from 'react';
import { useWorkTableScroll } from '../../components/tables/WorkTableScroll';

import { ActionButton } from '@lib/components/ActionButton';
import { ProgressBar } from '@lib/components/ProgressBar';
import {
  type RowAction,
  RowDeleteAction,
  RowEditAction
} from '@lib/components/RowActions';
import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { ApiFormFieldSet } from '@lib/types/Forms';
import type { TableColumn } from '@lib/types/Tables';
import { AttachmentLink } from '../../components/items/AttachmentLink';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';
import { useApi } from '../../contexts/ApiContext';
import { formatFileSize } from '../../defaults/formatters';
import {
  useCreateApiFormModal,
  useDeleteApiFormModal,
  useEditApiFormModal
} from '../../hooks/UseForm';
import { useEvidenceViewerState } from '../../states/EvidenceViewerState';
import { useGlobalSettingsState } from '../../states/SettingsStates';
import { useUserState } from '../../states/UserState';
import {
  type AttachmentPreviewRecord,
  attachmentPreviewItem
} from './attachmentPreview';
import { useAttachmentRecovery } from './useAttachmentRecovery';

/**
 * Define set of columns to display for the attachment table
 */
function attachmentTableColumns(
  owner: { model_type: ModelType; model_id: number },
  onPreview: (record: AttachmentPreviewRecord, trigger: HTMLElement) => void
): TableColumn[] {
  return [
    {
      accessor: 'attachment',
      sortable: false,
      switchable: false,
      noWrap: true,
      render: (record: any) => {
        if (record.attachment) {
          return (
            <Group gap='xs' wrap='nowrap'>
              <AttachmentLink
                thumbnail={record.thumbnail}
                attachment={record.attachment}
              />
              {attachmentPreviewItem(record, owner) && (
                <Tooltip
                  label={t`Preview ${record.filename}`}
                  multiline={owner.model_type === ModelType.workorder}
                  maw={
                    owner.model_type === ModelType.workorder
                      ? 'calc(100vw - 32px)'
                      : undefined
                  }
                  styles={
                    owner.model_type === ModelType.workorder
                      ? { tooltip: { overflowWrap: 'anywhere' } }
                      : undefined
                  }
                >
                  <ActionIcon
                    variant='subtle'
                    size='lg'
                    aria-label={t`Preview ${record.filename}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onPreview(record, event.currentTarget);
                    }}
                  >
                    <IconEye size={18} />
                  </ActionIcon>
                </Tooltip>
              )}
            </Group>
          );
        } else if (record.link) {
          return <AttachmentLink attachment={record.link} external />;
        } else {
          return '-';
        }
      },
      noContext: true
    },
    {
      accessor: 'comment',
      sortable: false,

      render: (record: any) => {
        return record.comment;
      }
    },
    {
      accessor: 'upload_date',
      sortable: true,

      render: (record: any) => {
        return (
          <Group justify='space-between'>
            <Text>{record.upload_date}</Text>
            {record.user_detail && (
              <Badge size='xs'>{record.user_detail.username}</Badge>
            )}
          </Group>
        );
      }
    },
    {
      accessor: 'file_size',
      sortable: true,
      switchable: true,
      render: (record: any) => {
        if (!record.attachment) {
          return '-';
        } else {
          return formatFileSize(record.file_size);
        }
      }
    }
  ];
}

function UploadProgress({
  filename,
  progress
}: {
  filename: string;
  progress: number;
}) {
  return (
    <Stack gap='xs'>
      <Text size='sm'>{t`Uploading file ${filename}`}</Text>
      <ProgressBar value={progress} progressLabel={false} />
    </Stack>
  );
}

/**
 * Construct a table for displaying uploaded attachments
 */
export function AttachmentTable({
  model_type,
  model_id,
  onChange
}: Readonly<{
  model_type: ModelType;
  model_id: number;
  onChange?: () => void;
}>): ReactNode {
  const workScrollArea = useWorkTableScroll(
    t`Work attachments table, scroll for all columns`
  );
  const api = useApi();
  const user = useUserState();
  const globalSettings = useGlobalSettingsState();
  const uploadMaxMb = Number(
    globalSettings.getSetting('INVENTREE_UPLOAD_MAX_SIZE', '10') || '10'
  );
  const table = useTable(`${model_type}-attachments`);

  const openEvidence = useEvidenceViewerState((state) => state.open);
  const previewAttachment = useCallback(
    (record: AttachmentPreviewRecord, trigger: HTMLElement) => {
      const item = attachmentPreviewItem(record, { model_type, model_id });
      if (item) openEvidence(item, trigger);
    },
    [model_type, model_id, openEvidence]
  );
  const tableColumns = useMemo(
    () => attachmentTableColumns({ model_type, model_id }, previewAttachment),
    [model_type, model_id, previewAttachment]
  );

  const url = apiUrl(ApiEndpoints.attachment_list);

  const validPk = useMemo(() => model_id > 0, [model_id]);

  const canDelete = useMemo(
    () => user.hasDeletePermission(model_type),
    [user, model_type]
  );

  const [isUploading, setIsUploading] = useState<boolean>(false);

  const allowDragAndDrop: boolean = useMemo(() => {
    return user.hasAddPermission(model_type);
  }, [user, model_type]);

  // Callback to upload file attachment(s)
  function uploadFiles(files: File[]) {
    files.forEach((file) => {
      const formData = new FormData();
      formData.append('attachment', file);
      formData.append('model_type', model_type);
      formData.append('model_id', model_id.toString());

      setIsUploading(true);

      const name: string = file.name;
      const id: string = `attachment-upload-${model_type}-${model_id}-${file.name}`;

      notifications.show({
        id: id,
        title: t`Uploading File`,
        message: <UploadProgress filename={name} progress={0} />,
        color: 'blue',
        loading: true,
        autoClose: false
      });

      api
        .post(url, formData, {
          // R4: size-derived ceiling (30 s base + 1 s/MiB ~= a 1 MiB/s
          // floor) — a flat 30 s cannot complete a large video upload.
          timeout: 30_000 + Math.ceil(file.size / (1024 * 1024)) * 1000,
          onUploadProgress: (progressEvent) => {
            const progress = 100 * (progressEvent?.progress ?? 0);
            notifications.update({
              id: id,
              title: t`Uploading File`,
              color: 'blue',
              loading: true,
              autoClose: false,
              message: <UploadProgress filename={name} progress={progress} />
            });
          }
        })
        .then((response) => {
          notifications.update({
            id: id,
            title: t`File Uploaded`,
            message: t`File ${name} uploaded successfully`,
            color: 'green',
            autoClose: 3500,
            icon: <IconCircleCheck />,
            loading: false
          });

          table.refreshTable();
          onChange?.();

          return response;
        })
        .catch((error) => {
          console.error('Error uploading attachment:', file, '->', error);
          notifications.update({
            id: id,
            title: t`Upload Error`,
            message: t`File could not be uploaded`,
            color: 'red',
            autoClose: 5000,
            icon: <IconExclamationCircle />,
            loading: false
          });
          return error;
        })
        .finally(() => {
          setIsUploading(false);
        });
    });
  }

  const [attachmentType, setAttachmentType] = useState<'attachment' | 'link'>(
    'attachment'
  );

  const [selectedAttachment, setSelectedAttachment] = useState<
    number | undefined
  >(undefined);

  const { onRevision, ...attachmentRecovery } = useAttachmentRecovery(
    model_type === 'workorder',
    url,
    selectedAttachment
  );
  const uploadFields: ApiFormFieldSet = useMemo(() => {
    const fields: ApiFormFieldSet = {
      model_type: {
        value: model_type,
        hidden: true
      },
      model_id: {
        value: model_id,
        hidden: true
      },
      attachment: {},
      filename: {},
      link: {},
      comment: {}
    };
    if (model_type === 'workorder' && selectedAttachment)
      fields.expected_revision = { hidden: true, onValueChange: onRevision };

    if (attachmentType != 'link') {
      delete fields['link'];
    } else {
      delete fields['attachment'];
      delete fields['filename'];
    }

    // Remove the 'attachment' field if we are editing an existing attachment, or uploading a link
    if (attachmentType != 'attachment' || !!selectedAttachment) {
      delete fields['attachment'];
    }

    if (!selectedAttachment) {
      // Cannot edit the filename during creation
      delete fields['filename'];
    }

    return fields;
  }, [model_type, model_id, attachmentType, selectedAttachment, onRevision]);

  const [uploadFailure, setUploadFailure] = useState<
    'refused' | 'uncertain' | null
  >(null);
  const uploadAttachment = useCreateApiFormModal({
    url: url,
    title: t`Upload Attachment`,
    fields: uploadFields,
    onOpen: () => setUploadFailure(null),
    submitDisabled: model_type === 'workorder' && uploadFailure === 'uncertain',
    hideErrorNotification: model_type === 'workorder',
    preserveDraftOnRefresh: model_type === 'workorder',
    preFormContent:
      model_type === 'workorder' && uploadFailure ? (
        <Alert color='orange'>
          {uploadFailure === 'uncertain'
            ? t`Attachment save not confirmed. This form keeps your file and metadata. Reconcile the Work attachments before another upload; this request may have saved.`
            : t`Attachment save refused. Your file and metadata are retained. Check access and required fields before retrying.`}
        </Alert>
      ) : undefined,
    onFormError: (error: any) => {
      if (
        model_type !== 'workorder' ||
        (!error?.isAxiosError &&
          !error?.response &&
          typeof error?.status !== 'number')
      )
        return;
      const status = error?.response?.status ?? error?.status;
      setUploadFailure(
        status >= 400 && status < 500 && ![408, 429].includes(status)
          ? 'refused'
          : 'uncertain'
      );
    },
    onFormSuccess: () => {
      table.refreshTable();
      onChange?.();
    }
  });

  const editAttachment = useEditApiFormModal({
    ...attachmentRecovery,
    url: url,
    pk: selectedAttachment,
    title: t`Edit Attachment`,
    fields: uploadFields,
    onFormSuccess: (record: any) => {
      if (record.pk) {
        table.updateRecord(record);
      } else {
        table.refreshTable();
      }
      onChange?.();
    }
  });

  const deleteAttachment = useDeleteApiFormModal({
    url: url,
    pk: selectedAttachment,
    title: t`Delete Attachment`,
    onFormSuccess: () => {
      table.refreshTable();
      onChange?.();
    }
  });

  const tableFilters: TableFilter[] = useMemo(() => {
    return [
      {
        name: 'is_link',
        label: t`Is Link`,
        description: t`Show link attachments`
      },
      {
        name: 'is_file',
        label: t`Is File`,
        description: t`Show file attachments`
      },
      {
        name: 'is_image',
        label: t`Is Image`,
        description: t`Show image attachments`
      }
    ];
  }, []);

  const tableActions: ReactNode[] = useMemo(() => {
    return [
      <ActionButton
        key='add-attachment'
        tooltip={t`Add attachment`}
        hidden={!user.hasAddPermission(model_type)}
        icon={<IconFileUpload />}
        onClick={() => {
          setAttachmentType('attachment');
          setSelectedAttachment(undefined);
          uploadAttachment.open();
        }}
      />,
      <ActionButton
        key='add-external-link'
        tooltip={t`Add external link`}
        hidden={!user.hasAddPermission(model_type)}
        icon={<IconExternalLink />}
        onClick={() => {
          setAttachmentType('link');
          setSelectedAttachment(undefined);
          uploadAttachment.open();
        }}
      />
    ];
  }, [user, model_type]);

  // Construct row actions for the attachment table
  const rowActions = useCallback(
    (record: any): RowAction[] => {
      return [
        RowEditAction({
          hidden: !user.hasChangePermission(model_type),
          onClick: () => {
            if (record.attachment) {
              setAttachmentType('attachment');
            } else {
              setAttachmentType('link');
            }
            setSelectedAttachment(record.pk);
            editAttachment.open();
          }
        }),
        RowDeleteAction({
          hidden: !canDelete,
          onClick: () => {
            setSelectedAttachment(record.pk);
            deleteAttachment.open();
          }
        })
      ];
    },
    [user, model_type]
  );

  return (
    <>
      {uploadAttachment.modal}
      {editAttachment.modal}
      {deleteAttachment.modal}
      <Stack gap='xs'>
        {validPk && (
          <InvenTreeTable
            key='attachment-table'
            url={url}
            tableState={table}
            columns={tableColumns}
            props={{
              scrollAreaProps:
                model_type === ModelType.workorder ? workScrollArea : undefined,
              noRecordsText: t`No attachments found`,
              enableSelection: canDelete,
              enableBulkDelete: canDelete,
              enableQueryFilters: false,
              afterBulkDelete: onChange,
              tableActions: tableActions,
              tableFilters: tableFilters,
              rowActions: rowActions,
              rowActionsTitle:
                model_type === ModelType.workorder ? t`Actions` : undefined,
              params: {
                model_type: model_type,
                model_id: model_id
              }
            }}
          />
        )}
        {allowDragAndDrop && validPk && (
          <Paper p='md' shadow='xs' radius='md'>
            <Dropzone
              onDrop={uploadFiles}
              onReject={() => {
                notifications.show({
                  title: t`File too large`,
                  message: t`The file exceeds the configured upload size limit`,
                  color: 'red'
                });
              }}
              maxSize={uploadMaxMb * 1024 * 1024}
              loading={isUploading}
              key='attachment-dropzone'
            >
              <Group justify='center' gap='lg' mih={100}>
                <Dropzone.Accept>
                  <IconUpload
                    style={{ color: 'var(--mantine-color-blue-6)' }}
                    stroke={1.5}
                  />
                </Dropzone.Accept>
                <Dropzone.Reject>
                  <IconX
                    style={{ color: 'var(--mantine-color-red-6)' }}
                    stroke={1.5}
                  />
                </Dropzone.Reject>
                <Dropzone.Idle>
                  <IconUpload
                    style={{ color: 'var(--mantine-color-dimmed)' }}
                    stroke={1.5}
                  />
                </Dropzone.Idle>
                <Text size='sm'>{t`Drag attachment file here to upload`}</Text>
              </Group>
            </Dropzone>
          </Paper>
        )}
      </Stack>
    </>
  );
}
