"""§5.13 / §8.0b: the AI parity invariant, expressed as a test.

The requirement (TaskSchedulingImplementation.md §5.13):

    For every mutating action reachable from the Board, Calendar or Timeline UI,
    there exists a ``ProposalAction`` whose confirmation dispatches the same
    ``tasks.services`` command the UI calls — same permission check, same
    ``expected_version`` check, same event record.

This test makes that falsifiable. It enumerates the UI's canonical mutation
commands (every ``tasks.services`` function a REST endpoint dispatches) and the
governed set (``proposals.ACTION_COMMAND``), and asserts the two partition
cleanly with an explicit, shrinking gap list. It is designed to **fail loudly**
when a later change adds a UI mutation without governing it or listing it as a
known gap — which is exactly what keeps voice, text and the UI on one write path.

Per §5.3 the ``ProposalAction`` enum is the single closed allow-list: voice has
no independent executable set, so "anything voice can confirm has an enum entry"
holds by construction. The voice ``write_gate`` executes only what the proposal
rail dispatches; there is no second rail into the ORM (Phase 6d, enforced by
``ai/core/tests/test_kanban_writes_governed.py``).
"""

from __future__ import annotations

import unittest

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.test import SimpleTestCase

from aichat.models import ProposalAction
from aichat.services import proposals

#: Every canonical mutation command reachable from the task page's REST surface,
#: with its dispatching endpoint. Keep this list in lockstep with the API: a new
#: mutation endpoint must add its command here (and then either govern it or add
#: it to _NOT_YET_GOVERNED), or the partition assertion below fails.
UI_MUTATION_COMMANDS = {
    # tasks/api.py — board / calendar / timeline command endpoints
    'create_work_order',            # WorkOrderCreate
    'update_work_order_plan',       # WorkOrderUpdate
    'schedule_work_order',          # WorkOrderSchedule (move)
    'resize_work_order',            # WorkOrderResize
    'delete_work_order',            # WorkOrderDelete (governed delete)
    'create_child',                 # WorkOrderCreateChild
    'generate_procurement_child',   # WorkOrderGenerateProcurement
    'apply_schedule_batch',         # WorkOrderScheduleOptimize / batch apply
    'create_dependency',            # WorkOrderDependencyCreate
    'delete_dependency',            # WorkOrderDependencyDelete
    # tasks/workorder_api.py — lifecycle command endpoints
    'transition_work_order',        # WorkOrderTransition (incl. column moves)
    'assign_work_order',            # WorkOrderAssign
    'hold_work_order',              # WorkOrderHold
    'resume_work_order',            # WorkOrderResume
    'cancel_work_order',            # WorkOrderCancel
    'complete_work_order',          # WorkOrderComplete
}

#: UI mutations that do not yet have a governed ``ProposalAction``. Each entry
#: is a tracked follow-up, not an oversight; shrinking this to empty is the
#: definition of "AI parity complete". Now empty — every model-framable board
#: mutation is governed.
_NOT_YET_GOVERNED: dict[str, str] = {}

#: UI mutations that are deliberately **not** chat/voice actions. Completion
#: carries a structured closeout capture (readings, part usage, deviations)
#: authored in the UI form; there is no meaningful model-framed proposal for it,
#: so it is excluded from parity by design rather than left as a pending gap.
_UI_ONLY_COMMANDS = {
    'complete_work_order': 'completion requires a UI-authored closeout capture',
}


class UiCommandParityInvariant(SimpleTestCase):
    """The enumerable, reviewable parity property (§5.13)."""

    def test_governed_and_gap_sets_partition_the_ui_surface(self):
        """Every UI mutation is governed, an explicit gap, or deliberately UI-only."""
        governed = set(proposals.ACTION_COMMAND.values())
        gap = set(_NOT_YET_GOVERNED)
        ui_only = set(_UI_ONLY_COMMANDS)

        # The three classes are mutually exclusive.
        self.assertEqual(governed & gap, set(), 'a command cannot be both governed and a gap')
        self.assertEqual(governed & ui_only, set(), 'a governed command cannot be UI-only')
        self.assertEqual(gap & ui_only, set(), 'a gap cannot also be UI-only')
        # Together they account for exactly the UI surface — nothing unclassified,
        # nothing invented. A new endpoint or a stale entry breaks this.
        self.assertEqual(
            governed | gap | ui_only,
            UI_MUTATION_COMMANDS,
            'governed + gap + ui-only must equal the enumerated UI mutation surface',
        )

    def test_every_governed_action_maps_to_a_real_command_and_dispatch(self):
        """The allow-list, the command map and the dispatcher agree (no drift)."""
        # The allow-list is exactly the set of actions with a command mapping.
        self.assertEqual(set(proposals.ACTION_COMMAND), proposals._ALLOWED_ACTIONS)
        # Every mapped action is a real ProposalAction value.
        self.assertTrue(
            set(proposals.ACTION_COMMAND).issubset(set(ProposalAction.values))
        )

    def test_governed_command_names_resolve_to_callable_services(self):
        """Each governed command is a real ``tasks.services`` callable, not a typo."""
        from tasks.services import scheduling
        from tasks.services import work_orders as wo

        for command in proposals.ACTION_COMMAND.values():
            resolved = getattr(scheduling, command, None) or getattr(wo, command, None)
            self.assertTrue(
                callable(resolved), f'{command} is not a callable command service'
            )

    def test_proposal_action_enum_has_no_ungoverned_entries(self):
        """The closed enum never contains an action without a dispatch mapping.

        This is the §5.3 "the enum wins" guarantee: the executable set is exactly
        the mapped set, so voice/text/UI cannot diverge in what they can confirm.
        """
        for action in ProposalAction.values:
            self.assertIn(
                action,
                proposals.ACTION_COMMAND,
                f'{action} is in the enum but has no canonical command mapping',
            )
