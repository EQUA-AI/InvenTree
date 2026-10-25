import { t } from '@lingui/core/macro';
import { useEffect } from 'react';
import { useUserState } from '../../states/UserState';
import { useStaffAccess, useStaffTrades } from './useStaffDirectory';

export function StaffTradeLabels({ trades = [] }: { trades?: number[] }) {
  const access = useStaffAccess();
  const admin = useUserState((state) => state.isStaff() || state.isSuperuser());
  const catalogue = useStaffTrades(
    (access.directory || admin) && trades.length > 0
  );
  const names = new Map(
    catalogue.data?.pages
      .flatMap((page) => page.results)
      .map((trade) => [trade.pk, trade.name])
  );
  const missing = trades.some((id) => !names.has(id));
  useEffect(() => {
    if (
      (access.directory || admin) &&
      missing &&
      catalogue.hasNextPage &&
      !catalogue.isFetching &&
      !catalogue.isError
    ) {
      void catalogue.fetchNextPage();
    }
  }, [
    missing,
    access.directory,
    admin,
    catalogue.hasNextPage,
    catalogue.isFetching,
    catalogue.isError,
    catalogue.fetchNextPage
  ]);
  return (
    <>
      {trades.map((id) => names.get(id) || t`Trade #${id}`).join(', ') || '-'}
    </>
  );
}
