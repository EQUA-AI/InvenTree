import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';

import type { Breadcrumb } from '../../components/nav/BreadcrumbList';

/** What a machine page knows about where its machine sits. */
export interface TrailMachine {
  pk?: number;
  name?: string;
  asset_type?: string;
  parent?: number | null;
  parent_name?: string | null;
}

export interface MachineTrail {
  /** Crumbs below the Machines index, always drawn. */
  breadcrumbs: Breadcrumb[];
  /** The machine itself, drawn only when the user asks for a last crumb. */
  lastCrumb: Breadcrumb[];
}

/**
 * Place a machine in its hierarchy, as crumbs below the Machines index.
 *
 * A station holds pumps, so it stands in its own trail, the way a stock
 * location does. A pump is reached from its station, so its trail runs back
 * through it - without that the only way up from a pump is the browser's back
 * button.
 *
 * Every crumb keeps the tab being looked at. A link with no tab opens on the
 * one last *clicked*, and a pump's Performance tab is usually arrived at from
 * the station's table rather than clicked - so the way back would land on
 * whichever tab was clicked some time before, not on the table it came from.
 * A station has every tab a pump has.
 *
 * A pump is registered as "<station> / Pump 01". Its own crumb drops the
 * station, which the crumb before it has just said.
 */
export function machineTrail(
  machine?: TrailMachine | null,
  panel?: string
): MachineTrail {
  if (!machine?.pk) {
    return { breadcrumbs: [], lastCrumb: [] };
  }

  const pageOf = (pk: number) =>
    `${getDetailUrl(ModelType.assetmachine, pk)}${panel ?? ''}`;

  const self: Breadcrumb = {
    name: machine.name ?? '',
    url: pageOf(machine.pk)
  };

  if (machine.asset_type === 'pumphouse') {
    return { breadcrumbs: [self], lastCrumb: [] };
  }

  if (!machine.parent || !machine.parent_name) {
    return { breadcrumbs: [], lastCrumb: [self] };
  }

  const station = machine.parent_name;
  const prefix = `${station} / `;

  return {
    breadcrumbs: [{ name: station, url: pageOf(machine.parent) }],
    lastCrumb: [
      {
        ...self,
        name: self.name.startsWith(prefix)
          ? self.name.slice(prefix.length)
          : self.name
      }
    ]
  };
}
