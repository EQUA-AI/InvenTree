import type { Page } from '@playwright/test';
import type { MimicData } from '../../src/pages/assets/health/mimic/types';
import type { LocationNode } from '../../src/pages/assets/locations/locationTypes';
import { fixture } from '../mimic/layoutFixtures';
import { mockExplorer } from '../s1/machineExplorerFixtures';

/** Explicit synthetic states exercise the existing API contract, never live data. */
export function ownerMimicFixture(unit: string | null): MimicData {
  const data = fixture(unit);
  const set = (key: string, id: string, value: number | string) => {
    const bay = data.bays.find((item) => item.key === key)!;
    const element = data.layout.elements.find((item) => item.id === id)!;
    const pointer = element.pointer
      .replaceAll('{pump_number}', key.slice(1))
      .replaceAll('{pump}', key);
    Object.assign(bay.points[pointer], {
      value,
      reason: null,
      quality: 'good'
    });
  };
  for (const key of ['P1', 'P7']) {
    data.bays.find((bay) => bay.key === key)!.state = 'running';
    set(key, 'pump-status', 'R');
    set(key, 'pump-power', 1.25);
    set(key, 'pump-speed', 980);
    set(key, 'pump-flow', 2.4);
  }
  data.bays[1].state = 'fault';
  data.bays[2].state = 'stale';
  data.bays[3].state = 'unknown';
  for (const [id, value] of [
    ['station-status', 'R'],
    ['pumps-running', 2]
  ] as const) {
    const element = data.layout.elements.find((item) => item.id === id)!;
    data.station_points[element.pointer].value = value;
  }
  data.alarm = null;
  data.totals.flow = { ...data.totals.flow, value: null, reason: 'incomplete' };
  const firstBay = data.bays[0];
  firstBay.alarm = 'warning';
  const firstAlarm = Object.values(firstBay.points).find(
    (item) => item.part_code === 'PS-WATER-COOLING'
  )!;
  Object.assign(firstAlarm, {
    value: 82,
    thresholds_configured: true,
    condition: 'warning'
  });
  data.alarms.push({
    ...firstAlarm,
    machine: firstBay.machine,
    anomaly: 900,
    severity: 'warning',
    anomaly_status: 'open'
  });
  const bay = data.bays.find((item) => item.key === 'P7')!;
  bay.alarm = 'critical';
  for (const [part, condition, value] of [
    ['PS-AIR-COOLING', 'normal', 41],
    ['PS-THRUST-BRG', 'warning', 82],
    ['PS-WATER-COOLING', 'critical', 105]
  ] as const) {
    const point = Object.values(bay.points).find(
      (item) => item.part_code === part && item.unit === 'degC'
    )!;
    Object.assign(point, { thresholds_configured: true, condition, value });
    if (condition !== 'normal')
      data.alarms.push({
        ...point,
        machine: bay.machine,
        anomaly: data.alarms.length + 901,
        severity: condition,
        anomaly_status: 'open'
      });
  }
  const unconfigured = Object.values(bay.points).find(
    (item) => item.part_code === 'PS-MOTOR'
  )!;
  Object.assign(unconfigured, {
    value: 200,
    condition: 'critical',
    thresholds_configured: false
  });
  if (unit) data.points = data.bays.find((item) => item.key === unit)!.points;
  const allPoints = new Map(
    [
      ...Object.values(data.station_points),
      ...data.bays.flatMap((item) => Object.values(item.points))
    ].map((point) => [point.pointer, point] as const)
  );
  data.unconfigured_thresholds = [...allPoints.values()].filter(
    (point) => !point.thresholds_configured
  ).length;
  return data;
}

export async function mockOwnerLocations(page: Page) {
  const base = await mockExplorer(page);
  const entries: [number, number | null, string, string, string][] = [
    [
      10,
      null,
      'ACME Manufacturing and Quality Operations Campus',
      'demo-acme-manufacturing',
      'site'
    ],
    [11, 10, 'Austin, TX', 'demo-acme-austin', 'area'],
    [13, 11, 'Quality Lab', 'demo-acme-quality-lab', 'room'],
    [14, 13, 'Cell A-01', 'demo-acme-cell-a01', 'cell'],
    [12, null, 'Cedar Creek Houston Regional Site', 'cc-site-hou', 'site'],
    [20, 12, 'Cedar Creek Water Reclamation Facility', 'cc-wrf', 'facility'],
    [30, 20, 'Cedar Creek Final Effluent Pump Station', 'cc-feps', 'building'],
    [31, 30, 'Discharge Gallery', 'cc-feps-discharge-gallery', 'room'],
    [
      32,
      30,
      'Final Effluent Distribution Area',
      'cc-feps-final-effluent-area',
      'area'
    ],
    [33, 30, 'HVAC and Ventilation Room', 'cc-feps-ventilation', 'room'],
    [34, 30, 'Hydraulic Power Systems Room', 'cc-feps-hydraulic-power', 'room'],
    [
      35,
      30,
      'Instrumentation and Control Room',
      'cc-feps-instrumentation-control',
      'room'
    ],
    [36, 30, 'Low Voltage Electrical Room', 'cc-feps-low-voltage', 'room'],
    [37, 30, 'Main Pump Gallery', 'cc-feps-main-pump-gallery', 'room'],
    [50, null, 'Collection System', 'demo-collection-system', 'site'],
    [60, null, 'Industrial Water Plant', 'demo-industrial-water-plant', 'site'],
    [70, null, 'Plant A', 'demo-plant-a', 'site'],
    [80, null, 'Power Site', 'power-site', 'site'],
    [
      90,
      null,
      'Tomahawk Creek Water Reclamation Facility',
      'demo-tomahawk-creek-wrf',
      'site'
    ],
    ...Array.from(
      { length: 14 },
      (_, i): [number, number, string, string, string] => [
        101 + i,
        37,
        `Pump Bay ${String(i + 1).padStart(2, '0')}`,
        `cc-feps-pump-bay-${String(i + 1).padStart(2, '0')}`,
        'area'
      ]
    )
  ];
  const nodes = new Map<number, LocationNode>();
  for (const [pk, parent, name, code, kind] of entries) {
    nodes.set(pk, {
      ...base.location,
      pk,
      parent,
      name,
      code,
      kind,
      path: [...(parent == null ? [] : nodes.get(parent)!.path), { pk, name }],
      has_children: entries.some((row) => row[1] === pk),
      counts: {
        direct_machines: 0,
        total_machines: 0,
        direct_open_work_orders: 0,
        total_open_work_orders: 0
      }
    });
  }
  // The root list uses complete API counts, independently of its loaded page.
  const machines = base.machines.slice(0, 86).map((machine, index) => ({
    ...machine,
    active: true,
    physical_location: nodes.get(
      index < 63 ? 101 + (index % 14) : index < 68 ? 10 : index < 81 ? 50 : 60
    )!
  }));
  const counts = (node: LocationNode, activeOnly: boolean) => {
    const placed = machines.filter(
      (machine) =>
        (!activeOnly || machine.active) &&
        machine.physical_location.path.some((part) => part.pk === node.pk)
    );
    return {
      direct_machines: placed.filter(
        (machine) => machine.physical_location.pk === node.pk
      ).length,
      total_machines: placed.length
    };
  };
  await page.route('**/api/assets/locations/?*', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const search = (params.get('search') ?? '').toLowerCase();
    const parent = params.get('parent');
    const rows = [...nodes.values()].filter((node) =>
      search
        ? `${node.name} ${node.code}`.toLowerCase().includes(search)
        : parent === 'root'
          ? node.parent === null
          : node.parent === Number(parent)
    );
    const offset = Number(params.get('offset') ?? 0);
    const limit = Number(params.get('limit') ?? 10);
    return route.fulfill({
      json: {
        count: rows.length,
        results: rows.slice(offset, offset + limit).map((node) => ({
          ...node,
          counts:
            params.get('include_counts') === 'true'
              ? counts(node, params.get('active') === 'true')
              : undefined
        })),
        next: offset + limit < rows.length ? 'next' : null
      }
    });
  });
  await page.route(/\/api\/assets\/locations\/\d+\/(?:\?|$)/, (route) => {
    const pk = Number(
      new URL(route.request().url()).pathname.split('/').filter(Boolean).at(-1)
    );
    const node = nodes.get(pk)!;
    const params = new URL(route.request().url()).searchParams;
    return route.fulfill({
      json: {
        ...node,
        counts: {
          ...counts(node, params.get('active') === 'true'),
          direct_open_work_orders: 0,
          total_open_work_orders: 0
        }
      }
    });
  });
  await page.route('**/api/assets/locations/machines/**', (route) => {
    const params = new URL(route.request().url()).searchParams;
    const search = (params.get('search') ?? '').toLowerCase();
    const rows = machines.filter((machine) => {
      if (params.get('unassigned') === 'true') return false;
      if (params.has('location')) {
        const pk = Number(params.get('location'));
        if (
          params.get('include_descendants') === 'false'
            ? machine.physical_location.pk !== pk
            : !machine.physical_location.path.some((part) => part.pk === pk)
        )
          return false;
      }
      if (params.get('has_alarms') === 'true' && !machine.open_alarms)
        return false;
      if (
        params.has('criticality') &&
        machine.criticality !== params.get('criticality')
      )
        return false;
      return `${machine.name} ${machine.serial} ${machine.manufacturer}`
        .toLowerCase()
        .includes(search);
    });
    const offset = Number(params.get('offset') ?? 0);
    const limit = Number(params.get('limit') ?? 25);
    return route.fulfill({
      json: {
        count: rows.length,
        results: rows.slice(offset, offset + limit),
        next: offset + limit < rows.length ? 'next' : null
      }
    });
  });
  return nodes;
}
