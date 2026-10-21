import type { SeriesResponse } from '@lib/types/MachineHealth';
import { Stack, type StackProps } from '@mantine/core';
import { type ReactNode, createContext, useContext, useMemo } from 'react';
import { failedHistoryTargets } from './historyBatches';

const HistoryReadContext = createContext({
  ...failedHistoryTargets(undefined),
  missing: false
});

/** Scope failure identity to the read actually supplying these charts. */
export function HistoryReadStack({
  response,
  children,
  ...props
}: Readonly<
  {
    response: SeriesResponse | undefined;
    children: ReactNode;
  } & StackProps
>) {
  const failed = useMemo(
    () => ({ ...failedHistoryTargets(response), missing: !response }),
    [response]
  );
  return (
    <HistoryReadContext.Provider value={failed}>
      <Stack component='section' {...props}>
        {children}
      </Stack>
    </HistoryReadContext.Provider>
  );
}

export function useHistoryReadFailures() {
  return useContext(HistoryReadContext);
}
