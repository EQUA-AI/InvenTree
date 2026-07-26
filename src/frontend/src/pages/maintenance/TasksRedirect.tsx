import { Navigate, useLocation, useParams } from 'react-router-dom';

/**
 * Compatibility redirect from the old `/tasks/*` URLs to `/maintenance/*`.
 *
 * The workspace was renamed to Maintenance, but bookmarks, saved links and
 * anything that captured a work-order URL must keep resolving. The work-order id
 * and the selected view are both preserved:
 *
 *   /tasks/                      -> /maintenance/
 *   /tasks/kanban/               -> /maintenance/
 *   /tasks/kanban/timeline/      -> /maintenance/timeline/
 *   /tasks/work-orders/42/parts  -> /maintenance/work-orders/42/parts
 *
 * Replaces the history entry so Back does not bounce between the two URLs.
 */
export default function TasksRedirect() {
  const params = useParams();
  const { search, hash } = useLocation();

  const rest = (params['*'] ?? '').replace(/^\/+/, '');

  // `/tasks/kanban` was the board host; its panel segment is now the first
  // segment under `/maintenance/`.
  const target = rest.replace(/^kanban\/?/, '');

  return <Navigate to={`/maintenance/${target}${search}${hash}`} replace />;
}
