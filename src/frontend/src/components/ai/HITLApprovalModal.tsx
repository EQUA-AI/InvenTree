import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Code,
  Collapse,
  Group,
  Loader,
  Paper,
  Progress,
  Stack,
  Text,
  Textarea,
  ThemeIcon,
  Tooltip,
  Transition,
  useMantineTheme
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconClock,
  IconFileText,
  IconInfoCircle,
  IconPackage,
  IconShieldCheck,
  IconShoppingCart,
  IconTruck,
  IconX
} from '@tabler/icons-react';
import { useCallback, useEffect, useState } from 'react';

/**
 * HITL Action Types - defines what kind of action needs approval
 */
export type HITLActionType =
  | 'create_purchase_order'
  | 'create_sales_order'
  | 'create_build_order'
  | 'update_stock'
  | 'delete_item'
  | 'bulk_operation'
  | 'send_email'
  | 'external_api'
  | 'custom';

/**
 * HITL Request structure from backend
 */
export interface HITLRequest {
  id: string;
  action: HITLActionType | string;
  title: string;
  description: string;
  details: Record<string, unknown>;
  items?: HITLItem[];
  totalValue?: number;
  currency?: string;
  riskLevel: 'low' | 'medium' | 'high';
  timeoutSeconds: number;
  createdAt: Date;
  threadId: string;
}

/**
 * Individual item in a HITL request (e.g., line items in a PO)
 */
export interface HITLItem {
  id: string;
  name: string;
  quantity: number;
  unitPrice?: number;
  total?: number;
  description?: string;
  metadata?: Record<string, unknown>;
}

/**
 * Props for the HITL Approval Modal
 */
interface HITLApprovalModalProps {
  request: HITLRequest | null;
  onApprove: (requestId: string, comment?: string) => void;
  onReject: (requestId: string, reason: string) => void;
  onDismiss: () => void;
}

/**
 * Get icon for action type
 */
function getActionIcon(action: HITLActionType | string) {
  switch (action) {
    case 'create_purchase_order':
      return IconShoppingCart;
    case 'create_sales_order':
      return IconTruck;
    case 'create_build_order':
      return IconPackage;
    case 'update_stock':
      return IconPackage;
    case 'delete_item':
      return IconAlertTriangle;
    case 'send_email':
      return IconFileText;
    default:
      return IconShieldCheck;
  }
}

/**
 * Get risk level color and label
 */
function getRiskBadge(level: 'low' | 'medium' | 'high') {
  switch (level) {
    case 'low':
      return { color: 'green', label: t`Low Risk` };
    case 'medium':
      return { color: 'yellow', label: t`Medium Risk` };
    case 'high':
      return { color: 'red', label: t`High Risk` };
  }
}

/**
 * Format currency value
 */
function formatCurrency(value: number, currency = 'USD') {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency
  }).format(value);
}

/**
 * Details panel showing expanded information
 */
function DetailsPanel({
  details,
  items
}: {
  details: Record<string, unknown>;
  items?: HITLItem[];
}) {
  const theme = useMantineTheme();

  return (
    <Stack gap='sm'>
      {/* Show line items if present */}
      {items && items.length > 0 && (
        <Box>
          <Text size='xs' fw={600} c='dimmed' mb='xs'>
            {t`Items`}
          </Text>
          <Stack gap='xs'>
            {items.map((item, idx) => (
              <Paper
                key={item.id || idx}
                p='xs'
                radius='sm'
                style={{
                  backgroundColor: theme.colors.gray[0],
                  border: `1px solid ${theme.colors.gray[2]}`
                }}
              >
                <Group justify='space-between' wrap='nowrap'>
                  <Box style={{ flex: 1 }}>
                    <Text size='sm' fw={500} truncate>
                      {item.name}
                    </Text>
                    {item.description && (
                      <Text size='xs' c='dimmed' truncate>
                        {item.description}
                      </Text>
                    )}
                  </Box>
                  <Group gap='md' wrap='nowrap'>
                    <Text size='sm' c='dimmed'>
                      × {item.quantity}
                    </Text>
                    {item.total !== undefined && (
                      <Text size='sm' fw={500}>
                        {formatCurrency(item.total)}
                      </Text>
                    )}
                  </Group>
                </Group>
              </Paper>
            ))}
          </Stack>
        </Box>
      )}

      {/* Show other details as key-value pairs */}
      {Object.keys(details).length > 0 && (
        <Box>
          <Text size='xs' fw={600} c='dimmed' mb='xs'>
            {t`Details`}
          </Text>
          <Code block style={{ fontSize: 11 }}>
            {JSON.stringify(details, null, 2)}
          </Code>
        </Box>
      )}
    </Stack>
  );
}

/**
 * Countdown timer component
 */
function CountdownTimer({
  timeoutSeconds,
  createdAt,
  onTimeout
}: {
  timeoutSeconds: number;
  createdAt: Date;
  onTimeout: () => void;
}) {
  const [remaining, setRemaining] = useState(timeoutSeconds);
  const [progress, setProgress] = useState(100);

  useEffect(() => {
    const elapsed = Math.floor(
      (Date.now() - new Date(createdAt).getTime()) / 1000
    );
    const initialRemaining = Math.max(0, timeoutSeconds - elapsed);
    setRemaining(initialRemaining);
    setProgress((initialRemaining / timeoutSeconds) * 100);

    const interval = setInterval(() => {
      setRemaining((prev) => {
        const newVal = prev - 1;
        if (newVal <= 0) {
          clearInterval(interval);
          onTimeout();
          return 0;
        }
        setProgress((newVal / timeoutSeconds) * 100);
        return newVal;
      });
    }, 1000);

    return () => clearInterval(interval);
  }, [timeoutSeconds, createdAt, onTimeout]);

  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  const timeString = `${minutes}:${seconds.toString().padStart(2, '0')}`;

  return (
    <Group gap='xs'>
      <IconClock size={14} />
      <Text size='xs' c={remaining < 30 ? 'red' : 'dimmed'}>
        {timeString}
      </Text>
      <Progress
        value={progress}
        size='xs'
        w={60}
        color={remaining < 30 ? 'red' : remaining < 60 ? 'yellow' : 'blue'}
        animated={remaining < 30}
      />
    </Group>
  );
}

/**
 * HITL Approval Modal - GitHub Copilot-style inline approval component
 *
 * Renders as an inline card within the chat, not a modal overlay.
 * Features:
 * - Clean, minimal design matching GitHub Copilot aesthetic
 * - Expandable details section
 * - Clear approve/reject actions
 * - Countdown timer for timeout
 * - Risk level indicator
 */
export function HITLApprovalCard({
  request,
  onApprove,
  onReject,
  onDismiss
}: HITLApprovalModalProps) {
  const theme = useMantineTheme();
  const [detailsOpened, { toggle: toggleDetails }] = useDisclosure(false);
  const [rejectReason, setRejectReason] = useState('');
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isTimedOut, setIsTimedOut] = useState(false);

  const handleApprove = useCallback(async () => {
    if (!request) return;
    setIsSubmitting(true);
    try {
      await onApprove(request.id);
    } finally {
      setIsSubmitting(false);
    }
  }, [request, onApprove]);

  const handleReject = useCallback(async () => {
    if (!request) return;
    if (!showRejectInput) {
      setShowRejectInput(true);
      return;
    }
    setIsSubmitting(true);
    try {
      await onReject(request.id, rejectReason);
    } finally {
      setIsSubmitting(false);
    }
  }, [request, rejectReason, showRejectInput, onReject]);

  const handleTimeout = useCallback(() => {
    setIsTimedOut(true);
  }, []);

  if (!request) return null;

  const ActionIcon_ = getActionIcon(request.action);
  const riskBadge = getRiskBadge(request.riskLevel);

  if (isTimedOut) {
    return (
      <Card
        radius='md'
        p='md'
        withBorder
        style={{
          borderColor: theme.colors.gray[3],
          backgroundColor: theme.colors.gray[0]
        }}
      >
        <Group gap='sm'>
          <ThemeIcon size='lg' radius='xl' color='gray' variant='light'>
            <IconClock size={20} />
          </ThemeIcon>
          <Box style={{ flex: 1 }}>
            <Text size='sm' fw={500}>
              {t`Approval request timed out`}
            </Text>
            <Text size='xs' c='dimmed'>
              {request.title}
            </Text>
          </Box>
          <Button size='xs' variant='subtle' onClick={onDismiss}>
            {t`Dismiss`}
          </Button>
        </Group>
      </Card>
    );
  }

  return (
    <Transition mounted transition='slide-up' duration={200}>
      {(styles) => (
        <Card
          radius='md'
          p={0}
          withBorder
          style={{
            ...styles,
            borderColor: theme.colors.blue[3],
            overflow: 'hidden',
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.08)'
          }}
        >
          {/* Header */}
          <Box
            p='sm'
            style={{
              background: `linear-gradient(135deg, ${theme.colors.blue[0]}, ${theme.colors.violet[0]})`,
              borderBottom: `1px solid ${theme.colors.gray[2]}`
            }}
          >
            <Group justify='space-between' wrap='nowrap'>
              <Group gap='sm'>
                <ThemeIcon
                  size='lg'
                  radius='xl'
                  variant='gradient'
                  gradient={{ from: 'blue', to: 'violet' }}
                >
                  <IconShieldCheck size={18} />
                </ThemeIcon>
                <Box>
                  <Group gap='xs'>
                    <Text size='sm' fw={600}>
                      {t`Approval Required`}
                    </Text>
                    <Badge size='xs' color={riskBadge.color} variant='light'>
                      {riskBadge.label}
                    </Badge>
                  </Group>
                  <CountdownTimer
                    timeoutSeconds={request.timeoutSeconds}
                    createdAt={request.createdAt}
                    onTimeout={handleTimeout}
                  />
                </Box>
              </Group>
              <Tooltip label={t`Dismiss`}>
                <ActionIcon
                  size='sm'
                  variant='subtle'
                  color='gray'
                  onClick={onDismiss}
                >
                  <IconX size={14} />
                </ActionIcon>
              </Tooltip>
            </Group>
          </Box>

          {/* Content */}
          <Box p='md'>
            <Group gap='sm' mb='sm'>
              <ThemeIcon size='md' radius='sm' color='gray' variant='light'>
                <ActionIcon_ size={16} />
              </ThemeIcon>
              <Box style={{ flex: 1 }}>
                <Text size='sm' fw={500}>
                  {request.title}
                </Text>
                <Text size='xs' c='dimmed'>
                  {request.description}
                </Text>
              </Box>
              {request.totalValue !== undefined && (
                <Text size='lg' fw={600} c='blue'>
                  {formatCurrency(request.totalValue, request.currency)}
                </Text>
              )}
            </Group>

            {/* Expandable details */}
            {(request.items?.length ||
              Object.keys(request.details).length > 0) && (
              <>
                <Button
                  variant='subtle'
                  size='xs'
                  color='gray'
                  onClick={toggleDetails}
                  leftSection={
                    detailsOpened ? (
                      <IconChevronDown size={14} />
                    ) : (
                      <IconChevronRight size={14} />
                    )
                  }
                  mb='sm'
                >
                  {detailsOpened ? t`Hide details` : t`Show details`}
                </Button>
                <Collapse expanded={detailsOpened}>
                  <Box
                    mb='sm'
                    p='sm'
                    style={{
                      backgroundColor: theme.colors.gray[0],
                      borderRadius: theme.radius.sm
                    }}
                  >
                    <DetailsPanel
                      details={request.details}
                      items={request.items}
                    />
                  </Box>
                </Collapse>
              </>
            )}

            {/* Reject reason input */}
            <Collapse expanded={showRejectInput}>
              <Textarea
                placeholder={t`Why are you rejecting this? (optional)`}
                size='xs'
                mb='sm'
                value={rejectReason}
                onChange={(e) => setRejectReason(e.currentTarget.value)}
                minRows={2}
                autoFocus
              />
            </Collapse>

            {/* Action buttons - GitHub style */}
            <Group gap='sm'>
              <Button
                size='sm'
                variant='filled'
                color='green'
                leftSection={
                  isSubmitting ? (
                    <Loader size={14} color='white' />
                  ) : (
                    <IconCheck size={16} />
                  )
                }
                onClick={handleApprove}
                disabled={isSubmitting}
                style={{ flex: 1 }}
              >
                {t`Approve`}
              </Button>
              <Button
                size='sm'
                variant='light'
                color='red'
                leftSection={
                  isSubmitting ? (
                    <Loader size={14} />
                  ) : (
                    <IconX size={16} />
                  )
                }
                onClick={handleReject}
                disabled={isSubmitting}
                style={{ flex: 1 }}
              >
                {showRejectInput ? t`Confirm Reject` : t`Reject`}
              </Button>
            </Group>

            {/* Info text */}
            <Group gap={4} mt='xs' justify='center'>
              <IconInfoCircle size={12} color={theme.colors.gray[5]} />
              <Text size='xs' c='dimmed'>
                {t`This action requires your approval before proceeding`}
              </Text>
            </Group>
          </Box>
        </Card>
      )}
    </Transition>
  );
}

/**
 * Minimal inline approval prompt - even more compact like GitHub Copilot
 * For simpler approval scenarios
 */
export function HITLInlinePrompt({
  request,
  onApprove,
  onReject,
  onDismiss
}: HITLApprovalModalProps) {
  const theme = useMantineTheme();
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!request) return null;

  const handleApprove = async () => {
    setIsSubmitting(true);
    try {
      await onApprove(request.id);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReject = async () => {
    setIsSubmitting(true);
    try {
      await onReject(request.id, '');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Paper
      p='sm'
      radius='md'
      style={{
        backgroundColor: theme.colors.blue[0],
        border: `1px solid ${theme.colors.blue[2]}`
      }}
    >
      <Group justify='space-between' wrap='nowrap'>
        <Group gap='sm'>
          <ThemeIcon size='sm' radius='xl' color='blue' variant='light'>
            <IconShieldCheck size={14} />
          </ThemeIcon>
          <Box>
            <Text size='sm' fw={500}>
              {request.title}
            </Text>
            {request.totalValue !== undefined && (
              <Text size='xs' c='dimmed'>
                {formatCurrency(request.totalValue, request.currency)}
              </Text>
            )}
          </Box>
        </Group>
        <Group gap='xs'>
          <Button
            size='xs'
            variant='filled'
            color='green'
            onClick={handleApprove}
            disabled={isSubmitting}
            loading={isSubmitting}
          >
            {t`Approve`}
          </Button>
          <Button
            size='xs'
            variant='light'
            color='red'
            onClick={handleReject}
            disabled={isSubmitting}
          >
            {t`Reject`}
          </Button>
          <ActionIcon size='sm' variant='subtle' onClick={onDismiss}>
            <IconX size={14} />
          </ActionIcon>
        </Group>
      </Group>
    </Paper>
  );
}

/**
 * Approval result banner - shows after approval/rejection
 */
export function HITLResultBanner({
  approved,
  action,
  onDismiss
}: {
  approved: boolean;
  action: string;
  onDismiss: () => void;
}) {
  const theme = useMantineTheme();

  return (
    <Paper
      p='sm'
      radius='md'
      style={{
        backgroundColor: approved ? theme.colors.green[0] : theme.colors.red[0],
        border: `1px solid ${approved ? theme.colors.green[3] : theme.colors.red[3]}`
      }}
    >
      <Group justify='space-between'>
        <Group gap='sm'>
          <ThemeIcon
            size='sm'
            radius='xl'
            color={approved ? 'green' : 'red'}
            variant='light'
          >
            {approved ? <IconCheck size={14} /> : <IconX size={14} />}
          </ThemeIcon>
          <Text size='sm' fw={500} c={approved ? 'green' : 'red'}>
            {approved ? t`Approved: ${action}` : t`Rejected: ${action}`}
          </Text>
        </Group>
        <ActionIcon size='sm' variant='subtle' onClick={onDismiss}>
          <IconX size={14} />
        </ActionIcon>
      </Group>
    </Paper>
  );
}

export default HITLApprovalCard;
