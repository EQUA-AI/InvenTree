export type DecisionSectionPreview =
  | { kind: 'text'; text: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'fields'; fields: [string, string][] };

/** Read-only projection. Unsupported values retain their original preview bytes. */
export function decisionSectionPreview(text: string): DecisionSectionPreview {
  const fallback: DecisionSectionPreview = { kind: 'text', text };
  try {
    const value: unknown = JSON.parse(text);
    // Reject duplicate keys and any round-trip change, including numeric precision.
    const compact = text.replace(/"(?:\\.|[^"\\])*"|\s+/g, (token) =>
      token.startsWith('"') ? token : ''
    );
    if (JSON.stringify(value) !== compact) return fallback;
    if (
      Array.isArray(value) &&
      value.length > 0 &&
      value.every((item) => typeof item === 'string' && item.length > 0)
    ) {
      return { kind: 'list', items: value };
    }
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      const fields = Object.entries(value);
      if (
        fields.length > 0 &&
        fields.every(
          ([key, entry]) => key.length > 0 && typeof entry === 'string'
        )
      ) {
        return { kind: 'fields', fields: fields as [string, string][] };
      }
    }
  } catch {
    // Plain text and malformed/unknown JSON are displayed without alteration.
  }
  return fallback;
}
