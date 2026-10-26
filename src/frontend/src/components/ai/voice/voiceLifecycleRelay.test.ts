import { expect, it, vi } from 'vitest';
import { VoiceLifecycleRelay } from './voiceLifecycleRelay';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

it('relays only bounded content-free lifecycle observations in arrival order', async () => {
  const relay = new VoiceLifecycleRelay();
  const send = vi.fn(async () => {});
  relay.observe(
    {
      type: 'response.created',
      response: { id: 'resp-1', status: 'in_progress', output: ['private'] }
    },
    send
  );
  relay.observe(
    {
      type: 'response.done',
      response: { id: 'resp-1', status: 'completed', output: ['private'] },
      transcript: 'private'
    },
    send
  );
  relay.observe(
    { type: 'response.audio_transcript.done', transcript: 'private' },
    send
  );
  await flush();
  expect(send.mock.calls).toEqual([
    [
      { type: 'response.created', response_id: 'resp-1', status: 'in_progress' }
    ],
    [{ type: 'response.done', response_id: 'resp-1', status: 'completed' }]
  ]);
});

it('invalidates queued observations on end and permits a fresh session', async () => {
  const relay = new VoiceLifecycleRelay();
  const send = vi.fn(async () => {});
  relay.observe({ type: 'response.created', response: { id: 'old' } }, send);
  relay.reset();
  relay.observe({ type: 'response.created', response: { id: 'new' } }, send);
  await flush();
  expect(send.mock.calls).toEqual([
    [{ type: 'response.created', response_id: 'new' }]
  ]);
});

it('does not block later observations after a failed report or retain unlimited work', async () => {
  const relay = new VoiceLifecycleRelay();
  const send = vi
    .fn()
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue(undefined);
  for (let n = 0; n < 100; n++)
    relay.observe(
      { type: 'response.created', response: { id: `resp-${n}` } },
      send
    );
  await flush();
  expect(send).toHaveBeenCalledTimes(16);
  relay.observe(
    { type: 'response.done', response: { id: 'resp-15', status: 'completed' } },
    send
  );
  await flush();
  expect(send).toHaveBeenCalledTimes(17);
});

it('rejects malformed identifiers and unknown terminal statuses', async () => {
  const relay = new VoiceLifecycleRelay();
  const send = vi.fn(async () => {});
  for (const response of [
    { id: 'x'.repeat(129) },
    { id: 'private text' },
    { id: 'ok', status: 'unknown' },
    null
  ])
    relay.observe({ type: 'response.done', response }, send);
  await flush();
  expect(send).not.toHaveBeenCalled();
});
