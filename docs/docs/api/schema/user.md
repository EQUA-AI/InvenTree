---
title: User Management
---

The *User Management* section of the InvenTree API schema is documented below.

## Occupational profiles and staff directory

- `GET /api/user/staff-directory/` returns a bounded `{count, results}`
  collection. Results contain only `user_id`, `display_name`, `job_title`, and
  approved `trades` (`pk`, `code`, `name`, `active`). It requires
  `users.view_staff_directory` and resolved maintenance scope.
- Filter by `machine_id` or `work_order_id` (not both), optionally `user_id`
  and `search`, or an active `trade` code. A linked `kanban_card_id` may
  accompany `work_order_id` when looking up a board-task assignee.
  `limit` is between 1 and 100; `offset` is nonnegative.
  Candidate selections are revalidated at assignment time.
- `GET/PATCH /api/user/<pk>/occupational-profile/` exposes only `position`
  and `trades` (trade primary-key list) to authorized user-management
  administrators. Ordinary self-profile endpoints cannot change these fields.
- `GET/POST /api/user/trades/` and
  `GET/PATCH /api/user/trades/<pk>/` expose the trade catalogue.
  Directory readers may list trades; creating or editing trades requires
  user-management administration. Existing codes cannot be renamed; deactivate
  a trade instead of deleting historical qualifications.
- `POST /api/user/staff-directory/assignment/` accepts an explicit human
  assignment: `work_order_id`, nullable `assigned_to` user ID,
  `expected_version`, and `idempotency_key`. Optional fields are `reason`,
  `override_reason`, `required_trades` (trade codes),
  `trade_requirements_confirmed`, and `kanban_card_id` for a board-task
  override. Changing requirements additionally needs planning authority.
  A mismatch override requires its named permission and a non-empty reason.

Personnel reads never grant user-management authority. Existing other-user API
profile projections withhold job titles and trades from callers without
directory permission or a shared maintenance scope. Ordinary name/username
labels and the current user's own occupational display remain available.

Directory errors are not represented as successful empty searches. Assignment
errors include a stable `code` and `detail`; an uncertain automatic selection
leaves new work unassigned for review rather than choosing an arbitrary person.
These endpoints do not enable unattended work-order monitoring or bypass
creation approvals and packet safety authority.

[OAD(./docs/docs/api/schema/user.yml)]
