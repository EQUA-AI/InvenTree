import { t } from '@lingui/core/macro';
import { Button, Checkbox, MultiSelect, Stack, Text } from '@mantine/core';
import { useStaffAccess, useStaffTrades } from './useStaffDirectory';

export function TradeRequirementsFields({
  value,
  confirmed,
  onChange,
  onConfirm,
  disabled = false
}: {
  value: string[];
  confirmed: boolean;
  onChange: (value: string[]) => void;
  onConfirm: (value: boolean) => void;
  disabled?: boolean;
}) {
  const access = useStaffAccess();
  const trades = useStaffTrades(access.directory && !disabled);
  const catalogue = trades.data?.pages.flatMap((page) => page.results) ?? [];
  if (!access.directory) return null;
  return (
    <Stack gap='xs'>
      <MultiSelect
        label={t`Required trades`}
        description={t`Recorded assignment requirements, not a license or safety certification.`}
        data={[
          ...catalogue
            .filter((trade) => trade.active || value.includes(trade.code))
            .map((trade) => ({
              value: trade.code,
              label: trade.name,
              disabled: !trade.active
            })),
          ...value
            .filter((code) => !catalogue.some((trade) => trade.code === code))
            .map((code) => ({ value: code, label: code, disabled: true }))
        ]}
        value={value}
        onChange={(next) => {
          onChange(next);
          onConfirm(false);
        }}
        searchable
        disabled={disabled || trades.isError}
        error={trades.isError ? t`Trade catalogue unavailable` : undefined}
      />
      {trades.hasNextPage && (
        <Button
          variant='subtle'
          size='xs'
          onClick={() => trades.fetchNextPage()}
        >{t`Load more trades`}</Button>
      )}
      {trades.isError && (
        <Button
          variant='subtle'
          size='xs'
          onClick={() => trades.refetch()}
        >{t`Retry trade catalogue`}</Button>
      )}
      <Checkbox
        label={t`I have reviewed and confirmed these trade requirements`}
        checked={confirmed}
        disabled={disabled || value.length === 0 || trades.isError}
        onChange={(event) => onConfirm(event.currentTarget.checked)}
      />
      {!confirmed && (
        <Text
          size='xs'
          c='dimmed'
        >{t`Assignment review required until the work requirements are confirmed.`}</Text>
      )}
    </Stack>
  );
}
