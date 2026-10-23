import { t } from '@lingui/core/macro';

export type FaultDictionary = Record<string, { description?: string | null }>;

/** Dictionary descriptions are evidence; an unrecognised code stays visible. */
export function faultDescription(
  code?: string,
  description?: string | null,
  dictionary?: FaultDictionary
): string {
  return (
    description?.trim() ||
    (code && dictionary?.[code]?.description?.trim()) ||
    code ||
    t`Fault classification not recorded`
  );
}
