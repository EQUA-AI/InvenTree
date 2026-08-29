/**
 * The always-visible active-analysis-scope banner (S2).
 *
 * Renders the server-confirmed scope of the current thread above the
 * composer. Labels are derived locally from the typed mode (the server's
 * display_label is English-only free text and is used only for explicit
 * selections, where it names the assets). The banner never invents a
 * scope: an unconfirmed thread says so, and the menu offers only the
 * requestable modes — `legacy_unconfirmed` is read-side state and cannot
 * be requested back, so there is deliberately no "clear" item.
 */

import { t } from '@lingui/core/macro';
import { ActionIcon, Badge, Group, Menu, Text, Tooltip } from '@mantine/core';
import { IconChevronDown, IconFocus2, IconWorld } from '@tabler/icons-react';

import type { ActiveThreadScope } from '../../hooks/UseAIChat';

function scopeLabel(scope: ActiveThreadScope | null): string {
  if (!scope || scope.mode === 'legacy_unconfirmed') {
    return t`Scope unconfirmed`;
  }
  if (scope.mode === 'all_authorized_assets') {
    return t`Authorized fleet`;
  }
  if (scope.mode === 'explicit_assets') {
    if (scope.displayLabel) {
      return scope.displayLabel;
    }
    const count = scope.machineCount ?? 0;
    return count === 1 ? t`1 selected asset` : t`${count} selected assets`;
  }
  // site_group never renders as active (fail-closed server-side).
  return t`Scope unconfirmed`;
}

export function ActiveScopeBanner({
  scope,
  readOnly,
  busy,
  hint,
  onSelectFleet,
  onSelectHintMachine
}: Readonly<{
  scope: ActiveThreadScope | null;
  readOnly: boolean;
  busy: boolean;
  hint?: { machineId: number; machineName: string };
  onSelectFleet: () => void;
  onSelectHintMachine?: () => void;
}>) {
  const unconfirmed = !scope || scope.mode === 'legacy_unconfirmed';
  const label = scopeLabel(scope);

  return (
    <Group gap={6} mb={6} data-testid='ai-chat-scope-banner'>
      <Text size='xs' c='dimmed' fw={600}>
        {t`Scope`}
      </Text>
      <Badge
        size='sm'
        variant='light'
        color={unconfirmed ? 'yellow' : 'blue'}
        leftSection={
          unconfirmed ? <IconFocus2 size={12} /> : <IconWorld size={12} />
        }
        data-testid='ai-chat-scope-label'
      >
        {label}
      </Badge>
      {!readOnly && (
        <Menu shadow='md' width={230} position='top-start'>
          <Menu.Target>
            <Tooltip label={t`Change the analysis scope`}>
              <ActionIcon
                size='xs'
                variant='subtle'
                color='gray'
                disabled={busy}
                aria-label='change-ai-chat-scope'
              >
                <IconChevronDown size={14} />
              </ActionIcon>
            </Tooltip>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={onSelectFleet} data-testid='scope-option-fleet'>
              {t`Authorized fleet`}
            </Menu.Item>
            {hint && onSelectHintMachine && (
              <Menu.Item
                onClick={onSelectHintMachine}
                data-testid='scope-option-machine'
              >
                {t`This machine: ${hint.machineName}`}
              </Menu.Item>
            )}
          </Menu.Dropdown>
        </Menu>
      )}
    </Group>
  );
}
