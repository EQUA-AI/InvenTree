import { describe, expect, it } from 'vitest';
import {
  type MaintenanceReceiveRequest,
  retainReceipt,
  validateMaintenanceReceipt
} from './maintenanceReceiving';

function storage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value)
  } as unknown as Storage;
}
const body: MaintenanceReceiveRequest = {
  maintenance_operation_key: 'fixture-receipt',
  maintenance_requirement_version: 7,
  location: 42,
  items: [
    { line_item: 91, quantity: '1', batch_code: 'FIXTURE-BATCH', status: 10 }
  ]
};
describe('native maintenance receipt intent', () => {
  it('retains the exact native body for reload and same-key replay', () => {
    const local = storage();
    expect(retainReceipt(local, 'receipt', body)).toEqual(body);
    const recovered = JSON.parse(local.getItem('receipt')!);
    expect(retainReceipt(local, 'receipt', recovered)).toEqual(body);
  });
  it('refuses to overwrite another uncertain operation or changed quantity', () => {
    const local = storage();
    retainReceipt(local, 'receipt', body);
    expect(() =>
      retainReceipt(local, 'receipt', {
        ...body,
        maintenance_operation_key: 'another'
      })
    ).toThrow();
    expect(() =>
      retainReceipt(local, 'receipt', {
        ...body,
        items: [{ ...body.items[0], quantity: '2' }]
      })
    ).toThrow();
    expect(JSON.parse(local.getItem('receipt')!)).toEqual(body);
  });
  it('refuses storage that silently drops writes', () => {
    expect(() =>
      retainReceipt(
        { getItem: () => null, setItem: () => {} } as unknown as Storage,
        'receipt',
        body
      )
    ).toThrow();
  });
  it('requires one exact line, captured batch and actual location', () => {
    expect(validateMaintenanceReceipt(body, 91)).toBeNull();
    expect(validateMaintenanceReceipt(body, 92)).toBe('line');
    expect(
      validateMaintenanceReceipt(
        { ...body, items: [...body.items, ...body.items] },
        91
      )
    ).toBe('line');
    expect(
      validateMaintenanceReceipt(
        { ...body, items: [{ ...body.items[0], batch_code: '  ' }] },
        91
      )
    ).toBe('batch');
    expect(validateMaintenanceReceipt({ ...body, location: null }, 91)).toBe(
      'location'
    );
    expect(
      validateMaintenanceReceipt(
        { ...body, location: null, items: [{ ...body.items[0], location: 3 }] },
        91
      )
    ).toBeNull();
  });
});
