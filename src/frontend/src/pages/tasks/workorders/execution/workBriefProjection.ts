import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { effectiveScope } from './scopeRevision';
import { testEvaluation } from './verificationProjection';

/** Read existing projections without deriving physical success from lifecycle. */
export function workBrief(work: WorkOrderOverview) {
  const investigation =
    work.investigation?.rule === 'investigation.v1'
      ? work.investigation
      : undefined;
  const requests =
    investigation?.requests.available &&
    Array.isArray(investigation.requests.items) &&
    investigation.requests.items.every(
      (request) => request.work_order === work.id
    )
      ? investigation.requests.items
      : null;
  const unresolved =
    requests?.filter((request) => request.status !== 'resolved') ?? null;
  const next =
    unresolved
      ?.filter(
        (request) =>
          Number.isSafeInteger(request.priority) &&
          request.priority !== null &&
          request.priority >= 0
      )
      .sort((a, b) => a.priority! - b.priority! || a.id - b.id)[0] ?? null;
  const verification =
    work.verification_return?.rule === 'verification_return.v1'
      ? work.verification_return
      : undefined;
  const tests =
    verification?.procedures_state === 'read' ? verification.tests : null;
  const prepared =
    work.prepared_package?.rule === 'prepared_package.v1'
      ? work.prepared_package
      : undefined;
  const scopes = prepared?.scopes ?? [];
  const current = scopes.filter((scope) => scope.current);
  const scope = effectiveScope(
    scopes,
    prepared?.revision_review
      ? prepared.revision_review.current_scope_id
      : current.length === 1
        ? current[0].id
        : null
  );
  return {
    investigation,
    requests,
    unresolved,
    next,
    tests,
    failed: tests?.filter((test) => testEvaluation(test) === 'failed') ?? null,
    passed:
      tests?.filter((test) => testEvaluation(test) === 'recorded_pass') ?? null,
    scope
  };
}
