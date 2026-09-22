import { beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), host: 'https://one.example' }));
vi.mock('../App', () => ({ api: { get: mocks.get } }));
vi.mock('../defaults/defaults', () => ({ emptyServerAPI: {} }));
vi.mock('./LocalState', () => ({
  useLocalState: { getState: () => ({ getHost: () => mocks.host }) }
}));
vi.mock('@lib/functions/Api', () => ({ apiUrl: (path: string) => path }));

beforeEach(() => {
  vi.resetModules();
  mocks.get.mockReset();
  mocks.host = 'https://one.example';
  vi.stubGlobal('sessionStorage', {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {}
  });
});

it('retries a failed metadata fetch and coalesces only successful same-host work', async () => {
  const { useServerApiState } = await import('./ServerApiState');
  mocks.get
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue({ data: { data: {} } });
  await useServerApiState.getState().fetchServerApiState();
  expect(mocks.get).toHaveBeenCalledTimes(2);
  await useServerApiState.getState().fetchServerApiState();
  expect(mocks.get).toHaveBeenCalledTimes(4);
  await useServerApiState.getState().fetchServerApiState();
  expect(mocks.get).toHaveBeenCalledTimes(4);
});

it('rejects late metadata from another host and pins each request to its host', async () => {
  const { useServerApiState } = await import('./ServerApiState');
  const finish: ((value: unknown) => void)[] = [];
  mocks.get.mockImplementation(
    () => new Promise((resolve) => finish.push(resolve))
  );
  const first = useServerApiState.getState().fetchServerApiState();
  mocks.host = 'https://two.example';
  const second = useServerApiState.getState().fetchServerApiState();
  finish[2]({ data: { version: 'two' } });
  finish[3]({ data: { data: { marker: 'two' } } });
  await second;
  finish[0]({ data: { version: 'one' } });
  finish[1]({ data: { data: { marker: 'one' } } });
  await first;
  expect(useServerApiState.getState().server.version).toBe('two');
  expect(mocks.get.mock.calls.map((call) => call[1].baseURL)).toEqual([
    'https://one.example',
    'https://one.example',
    'https://two.example',
    'https://two.example'
  ]);
  expect(mocks.get.mock.calls[0][1].signal.aborted).toBe(true);
});
