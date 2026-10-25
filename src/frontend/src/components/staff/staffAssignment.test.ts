import type { StaffMember } from '@lib/types/Staff';
import { describe, expect, it } from 'vitest';
import {
  assignmentReview,
  buildAssignmentCommand,
  personnelPlanning,
  resolveCardAssignment,
  staffCapabilities,
  staffScopeParams
} from './staffAssignment';

const plumber: StaffMember = {
  user_id: 12,
  display_name: 'Alex Lee',
  job_title: 'Technician',
  trades: [{ pk: 1, code: 'plumbing', name: 'Plumbing', active: true }]
};

describe('staff authority', () => {
  it('does not infer authority from account administration or group labels', () => {
    expect(
      staffCapabilities({ permissions: { user: ['view', 'change'] } })
    ).toEqual({
      directory: false,
      assign: false,
      plan: false,
      override: false
    });
  });
  it('uses the separate real permission codenames, not work-order edit authority', () => {
    expect(
      staffCapabilities({
        permissions: {
          staff_directory: ['view'],
          workorder: ['change', 'assign', 'plan'],
          assignment_trade_mismatch: ['override']
        }
      })
    ).toEqual({ directory: true, assign: true, plan: true, override: true });
    expect(
      staffCapabilities({ permissions: { workorder: ['change'] } }).assign
    ).toBe(false);
  });
  it('honors superusers and fails closed without a user', () => {
    expect(staffCapabilities({ is_superuser: true }).directory).toBe(true);
    expect(staffCapabilities().directory).toBe(false);
  });
  it('scopes directory queries to exactly one authoritative target', () => {
    expect(staffScopeParams({ machineId: 5 })).toEqual({ machine_id: 5 });
    expect(staffScopeParams({ workOrderId: 40 })).toEqual({
      work_order_id: 40
    });
  });
});

describe('assignment qualification warnings', () => {
  it('does not treat a job title as qualification', () => {
    expect(
      assignmentReview(
        { ...plumber, job_title: 'Electrician', trades: [] },
        ['electrical'],
        true
      )
    ).toBe('mismatch');
  });
  it('requires every confirmed trade and ignores inactive qualifications', () => {
    expect(assignmentReview(plumber, ['plumbing'], true)).toBe('match');
    expect(assignmentReview(plumber, ['plumbing', 'electrical'], true)).toBe(
      'mismatch'
    );
    expect(
      assignmentReview(
        { ...plumber, trades: [{ ...plumber.trades[0], active: false }] },
        ['plumbing'],
        true
      )
    ).toBe('mismatch');
  });
  it('keeps unknown requirements explicit and does not auto-select anyone', () => {
    expect(assignmentReview(plumber, ['electrical'], false)).toBe('unknown');
    expect(assignmentReview(plumber, [], true)).toBe('unknown');
    expect(assignmentReview(undefined, ['plumbing'], true)).toBe('unassigned');
  });
});

describe('card identity and legacy text', () => {
  const parent = {
    assigned_to: 12,
    assigned_to_name: 'Alex Lee',
    assignee: 'Old text'
  };
  it('inherits the parent identity and restores it after clearing a card override', () => {
    expect(
      resolveCardAssignment(
        { assigned_to: null, effective_assignee: 12, assignee: '' },
        parent
      )
    ).toEqual({ userId: 12, label: 'Alex Lee', inherited: true });
  });
  it('never shows the parent name for another typed card assignee', () => {
    expect(
      resolveCardAssignment(
        { assigned_to: 21, effective_assignee: 21, assignee: 'Old text' },
        parent
      )
    ).toEqual({ userId: 21, label: '', inherited: false });
  });
  it('uses a server-provided override name without guessing identities', () => {
    expect(
      resolveCardAssignment(
        {
          assigned_to: 21,
          assigned_to_name: 'Alex Lee',
          effective_assignee: 21,
          assignee: ''
        },
        parent
      ).userId
    ).toBe(21);
    expect(
      resolveCardAssignment({
        assigned_to: null,
        effective_assignee: null,
        assignee: 'Alex Lee'
      }).userId
    ).toBeNull();
    expect(
      resolveCardAssignment({
        assigned_to: null,
        effective_assignee: null,
        assignee: 'Alex Lee'
      }).label
    ).toBe('Alex Lee');
  });
});

describe('audited manual assignment command', () => {
  const command = {
    work_order_id: 40,
    assigned_to: 12,
    expected_version: 3,
    idempotency_key: 'retry-key',
    reason: ' Reviewed repair scope ',
    required_trades: ['plumbing', 'plumbing'],
    trade_requirements_confirmed: true
  };
  it('carries the exact reviewed identity, version, requirements and retry key', () => {
    expect(buildAssignmentCommand(command)).toEqual({
      ...command,
      reason: 'Reviewed repair scope',
      required_trades: ['plumbing']
    });
  });

  describe('work-package planning compatibility', () => {
    const input = {
      canAssign: true,
      canPlan: true,
      assignedTo: 12,
      requiredTrades: ['plumbing'],
      confirmed: true,
      legacyAssignee: 'Old text'
    };
    it('omits all new keys for callers without the directory/assignment grant', () => {
      expect(
        personnelPlanning({ ...input, canAssign: false, canPlan: false })
      ).toEqual({ assignee: 'Old text' });
    });
    it('retains the old contract when no typed personnel choice was made', () => {
      expect(
        personnelPlanning({
          ...input,
          assignedTo: null,
          requiredTrades: [],
          confirmed: false
        })
      ).toEqual({ assignee: 'Old text' });
    });
    it('does not submit legacy text alongside a new typed identity', () => {
      expect(personnelPlanning(input)).toEqual({
        assigned_to: 12,
        required_trades: ['plumbing'],
        trade_requirements_confirmed: true
      });
    });
    it('does not submit new planning keys without plan permission', () => {
      expect(personnelPlanning({ ...input, canPlan: false })).toEqual({
        assigned_to: 12
      });
    });
    it('uses one explicit intake owner key without changing the assignment API', () => {
      const planning = personnelPlanning({
        ...input,
        ownerKey: 'assigned_to_id'
      });
      expect(planning).toHaveProperty('assigned_to_id', 12);
      expect(planning).not.toHaveProperty('assigned_to');
    });
  });
  it('preserves explicit unassignment and card inheritance commands', () => {
    expect(
      buildAssignmentCommand({
        ...command,
        assigned_to: null,
        kanban_card_id: 81
      })
    ).toMatchObject({ assigned_to: null, kanban_card_id: 81 });
  });
  it('requires meaningful assignment and override reasons', () => {
    expect(() => buildAssignmentCommand({ ...command, reason: ' ' })).toThrow();
    expect(() =>
      buildAssignmentCommand({ ...command, override_reason: ' ' })
    ).toThrow();
    expect(
      buildAssignmentCommand({
        ...command,
        override_reason: ' Supervised exception '
      }).override_reason
    ).toBe('Supervised exception');
  });
  it('never invents a human override or caller-controlled automation provenance', () => {
    const payload = buildAssignmentCommand(command);
    expect(payload).not.toHaveProperty('override_reason');
    expect(payload).not.toHaveProperty('is_ai');
    expect(payload).not.toHaveProperty('source');
  });
});
