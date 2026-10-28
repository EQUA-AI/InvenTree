export function matchesPacketWorkReceipt(
  response: unknown,
  retained: { url: string; body: Record<string, unknown> },
  packetId: number,
  workId: number
): boolean {
  if (!response || typeof response !== 'object') return false;
  const envelope = response as Record<string, unknown>;
  const start = retained.url.endsWith('/start/');
  if (
    start &&
    (envelope.pk !== packetId ||
      envelope.ok !== true ||
      envelope.work_order_id !== workId)
  )
    return false;
  const result = start ? envelope.result : envelope;
  if (!result || typeof result !== 'object') return false;
  const receipt = result as Record<string, unknown>;
  return (
    receipt.work_order_id === workId &&
    receipt.command === 'transition' &&
    Number.isSafeInteger(receipt.event_id) &&
    Number(receipt.event_id) > 0 &&
    receipt.idempotency_key === retained.body.idempotency_key &&
    receipt.lifecycle_version === Number(retained.body.expected_version) + 1 &&
    receipt.lifecycle_status ===
      (start ? 'in_progress' : retained.body.to_status)
  );
}
