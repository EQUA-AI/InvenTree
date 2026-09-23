import { AxiosError, CanceledError } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readSession } from './sessionRead';

describe('session read recovery', () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'performance'] }));
  afterEach(() => vi.useRealTimers());

  it.each(['ECONNABORTED', 'ETIMEDOUT', 'ERR_NETWORK', '503'])(
    'recovers from %s without exhausting the shared budget',
    async (code) => {
      const error = Object.assign(new AxiosError('temporary', code), {
        response: code === '503' ? { status: 503 } : undefined
      });
      const read = vi.fn().mockRejectedValueOnce(error).mockResolvedValue('ok');
      const result = readSession(read, () => true, 20_000);
      await vi.advanceTimersByTimeAsync(250);
      await expect(result).resolves.toBe('ok');
      expect(read).toHaveBeenCalledTimes(2);
    }
  );

  it.each([401, 403, 404, 409, 429])(
    'does not retry HTTP %s',
    async (status) => {
      const error = Object.assign(new AxiosError('rejected'), {
        response: { status }
      });
      const read = vi.fn().mockRejectedValue(error);
      await expect(readSession(read, () => true, 20_000)).rejects.toBe(error);
      expect(read).toHaveBeenCalledTimes(1);
    }
  );

  it('does not retry cancellation', async () => {
    const read = vi.fn().mockRejectedValue(new CanceledError());
    await expect(readSession(read, () => true, 20_000)).rejects.toBeInstanceOf(
      CanceledError
    );
    expect(read).toHaveBeenCalledTimes(1);
  });

  it('limits attempts and shares the deadline across sequential reads', async () => {
    const slow = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 8000));
      return 'session';
    });
    const session = readSession(slow, () => true, 20_000);
    await vi.advanceTimersByTimeAsync(8000);
    await session;
    const timeouts: number[] = [];
    const profile = vi.fn(async (timeout: number) => {
      timeouts.push(timeout);
      await new Promise((resolve) => setTimeout(resolve, timeout));
      throw new AxiosError('timeout', 'ECONNABORTED');
    });
    const result = expect(
      readSession(profile, () => true, 20_000)
    ).rejects.toBeInstanceOf(AxiosError);
    await vi.advanceTimersByTimeAsync(12_000);
    await result;
    expect(timeouts).toEqual([10_000, 1750]);
    expect(performance.now()).toBe(20_000);
  });

  it('does not retry after logout or a host change during backoff', async () => {
    let current = true;
    const read = vi
      .fn()
      .mockRejectedValue(new AxiosError('offline', 'ERR_NETWORK'));
    const result = expect(
      readSession(read, () => current, 20_000)
    ).rejects.toThrow('superseded');
    await vi.advanceTimersByTimeAsync(100);
    current = false;
    await vi.advanceTimersByTimeAsync(150);
    await result;
    expect(read).toHaveBeenCalledTimes(1);
  });
});
