/** Bounded bulk deletion. Keep the prepared token before sending any mutation. */
export interface OwnedDeletionPlan {
  scope: 'owned_threads';
  cutoff: string;
  request_token: string;
}

export interface OwnedDeletionPage extends OwnedDeletionPlan {
  status: 'deleted' | 'purge_incomplete';
  next_cursor: string | null;
  processed: number;
  incomplete: number;
  remaining_threads: number;
  results: { thread_id: string; status: 'deleted' | 'purge_incomplete' }[];
}

function isPlan(value: any): value is OwnedDeletionPlan {
  return (
    value?.scope === 'owned_threads' &&
    typeof value.cutoff === 'string' &&
    Number.isFinite(Date.parse(value.cutoff)) &&
    typeof value.request_token === 'string' &&
    value.request_token.length > 0 &&
    value.request_token.length <= 2048
  );
}

export async function prepareOwnedDeletion(
  host: string,
  headers: Record<string, string>,
  signal: AbortSignal
): Promise<OwnedDeletionPlan> {
  const response = await fetch(`${host}/threads/deletion-plan`, {
    method: 'POST',
    credentials: 'include',
    headers,
    signal
  });
  if (!response.ok) throw new Error('Deletion preparation unavailable');
  const value = await response.json();
  if (!isPlan(value)) throw new Error('Invalid deletion preparation');
  return value;
}

export async function deleteOwnedPage(
  host: string,
  plan: OwnedDeletionPlan,
  cursor: string | null,
  headers: Record<string, string>,
  signal: AbortSignal
): Promise<OwnedDeletionPage> {
  const response = await fetch(`${host}/threads`, {
    method: 'DELETE',
    credentials: 'include',
    headers: { ...headers, 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      confirm: true,
      limit: 20,
      request_token: plan.request_token,
      cursor
    })
  });
  if (!response.ok) throw new Error('Deletion unavailable');
  const value = await response.json();
  if (
    !isPlan(value) ||
    value.request_token !== plan.request_token ||
    value.cutoff !== plan.cutoff
  )
    throw new Error('Deletion boundary changed');
  const page = value as OwnedDeletionPage;
  if (
    !['deleted', 'purge_incomplete'].includes(page.status) ||
    !(
      page.next_cursor === null ||
      (typeof page.next_cursor === 'string' &&
        page.next_cursor.length > 0 &&
        page.next_cursor.length <= 4096)
    ) ||
    ![page.processed, page.incomplete, page.remaining_threads].every(
      (count) => Number.isSafeInteger(count) && count >= 0
    ) ||
    page.incomplete > page.processed ||
    !Array.isArray(page.results) ||
    page.results.length > 20 ||
    page.processed < page.results.length ||
    !page.results.every(
      (row) =>
        typeof row?.thread_id === 'string' &&
        /^[A-Za-z0-9_-]{1,80}$/.test(row.thread_id) &&
        ['deleted', 'purge_incomplete'].includes(row.status)
    ) ||
    (page.status === 'deleted' &&
      (page.results.some((row) => row.status !== 'deleted') ||
        page.next_cursor !== null ||
        page.incomplete !== 0 ||
        page.remaining_threads !== 0))
  )
    throw new Error('Invalid deletion receipt');
  return page;
}
