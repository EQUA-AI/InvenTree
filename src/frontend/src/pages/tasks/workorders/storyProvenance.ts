import type { CloseoutProposalField } from '@lib/types/Closeout';

/** Python str offsets count Unicode code points, with an exclusive end. */
export function narrativeExcerpts(
  narrative: string,
  field?: CloseoutProposalField
) {
  const characters = Array.from(narrative);
  return (field?.spans ?? []).flatMap(([start, end]) =>
    Number.isSafeInteger(start) &&
    Number.isSafeInteger(end) &&
    start >= 0 &&
    end > start &&
    end <= characters.length
      ? [{ start, end, text: characters.slice(start, end).join('') }]
      : []
  );
}
