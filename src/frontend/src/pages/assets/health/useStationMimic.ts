import { useDocumentVisibility, useInViewport } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';

import { api } from '../../../App';
import type { MimicData } from './mimic/types';

/**
 * A station's mimic, re-read every five seconds while it can be seen.
 *
 * One request serves the station and, when a bay is named, that pump's own
 * readings. It stops while the page is hidden or the mimic is scrolled out of
 * view, and it does not retry: a failed read is shown as one, with nothing
 * cached standing in for it.
 */
export function useStationMimic(stationId: number, unit: string | null) {
  const visibility = useDocumentVisibility();
  const { ref, inViewport } = useInViewport<HTMLDivElement>();
  const query = useQuery({
    queryKey: ['station-mimic', stationId, unit],
    enabled: inViewport && visibility === 'visible',
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
    retry: false,
    queryFn: async () =>
      (
        await api.get<MimicData>(
          `/api/machine-health/station/${stationId}/mimic/`,
          { params: unit ? { unit } : {}, timeout: 10000 }
        )
      ).data
  });
  return { ref, query };
}
