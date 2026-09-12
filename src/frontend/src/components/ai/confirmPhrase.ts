/** Strict phrases permit case/spacing and final punctuation, never extra words. */
export function matchesConfirmPhrase(value: string, expected: string): boolean {
  const normalize = (text: string) =>
    text
      .trim()
      .toLowerCase()
      .replace(/[.!?]+$/, '')
      .replace(/\s+/g, ' ');
  return !!expected && normalize(value) === normalize(expected);
}
