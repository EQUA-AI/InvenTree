/** Owner observations reconcile output scheduling, never business authority. */
export interface VoiceLifecycleObservation {
  type: 'response.created' | 'response.done' | 'response.cancelled';
  response_id: string;
  status?:
    | 'in_progress'
    | 'completed'
    | 'cancelled'
    | 'canceled'
    | 'failed'
    | 'incomplete';
}

export class VoiceLifecycleRelay {
  private queue = { pending: 0, tail: Promise.resolve() };

  reset() {
    this.queue = { pending: 0, tail: Promise.resolve() };
  }

  observe(
    event: Record<string, unknown>,
    deliver: (observation: VoiceLifecycleObservation) => Promise<unknown>
  ) {
    const type = event.type;
    if (
      type !== 'response.created' &&
      type !== 'response.done' &&
      type !== 'response.cancelled'
    )
      return;
    const response = event.response;
    const fields =
      response && typeof response === 'object'
        ? (response as Record<string, unknown>)
        : {};
    const id = fields.id ?? event.response_id;
    if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(id)) return;
    const status = fields.status;
    if (
      status != null &&
      ![
        'in_progress',
        'completed',
        'cancelled',
        'canceled',
        'failed',
        'incomplete'
      ].includes(String(status))
    )
      return;
    const observation: VoiceLifecycleObservation = {
      type,
      response_id: id,
      ...(status == null
        ? {}
        : { status: status as VoiceLifecycleObservation['status'] })
    };
    // Preserve created -> done order even while the business HTTP request is
    // running. Bound retained work and invalidate unsent reports on session end.
    const queue = this.queue;
    if (queue.pending >= 16) return;
    queue.pending++;
    queue.tail = queue.tail
      .then(async () => {
        if (queue === this.queue) await deliver(observation);
      })
      .catch(() => {})
      .finally(() => {
        queue.pending--;
      });
  }
}
