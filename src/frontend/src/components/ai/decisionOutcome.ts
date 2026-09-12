/** Only recorded business receipts describe effects; HTTP success is irrelevant. */
export function decisionOutcome(
  receipt: Record<string, unknown> | null | undefined
) {
  const labels = (value: unknown) =>
    Array.isArray(value)
      ? value.map((item) =>
          typeof item === 'string' ? item : JSON.stringify(item)
        )
      : [];
  return {
    completed: labels(receipt?.completed ?? receipt?.applied),
    failed: labels(receipt?.failed),
    unknown: labels(receipt?.unknown)
  };
}
