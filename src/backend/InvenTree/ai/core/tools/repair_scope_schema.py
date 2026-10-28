"""Provider-visible proposed-scope shape; domain validation remains authoritative."""

from typing import Annotated

from pydantic import ConfigDict, Field, with_config
from typing_extensions import TypedDict

ScopeText = Annotated[str, Field(min_length=1, max_length=2000)]
ScopeItem = Annotated[str, Field(min_length=1, max_length=1000)]
ScopeItems = Annotated[list[ScopeItem], Field(min_length=1, max_length=20)]


@with_config(ConfigDict(extra="forbid", strict=True))
class ProposedRepairScope(TypedDict):
    """The exact native scope fields, preserved as a plain JSON dictionary."""

    suspected_cause: ScopeText
    steps: Annotated[
        ScopeItems,
        Field(
            description=(
                "Ordered proposed future actions. First confirm the fitted motor, cooler construction "
                "and manual applicability; require authorized isolation before physical access. "
                "Choose a cleaning method only conditionally after the equipment/material match "
                "and only when an actual cited passage supports it. Preserve every method-specific "
                "trigger, restriction, prohibition and required follow-up handling from that passage; "
                "never broaden a conditional method into a general alternative. Preserve uncertainty."
            )
        ),
    ]
    tools_and_supplies: Annotated[
        ScopeItems,
        Field(
            description=(
                "Proposed tools and supplies, each subject to the same cited method-specific "
                "conditions and material restrictions as its planned step. An item allowed only "
                "for a particular condition must not become a general-purpose alternative. "
                "Label site-planning inferences separately from actual manual requirements."
            )
        ),
    ]
    required_ppe: ScopeItems
    isolation_checks: Annotated[
        ScopeItems,
        Field(
            description=(
                "Proposed job-specific site authorization, isolation and measurement-access checks "
                "before work. Require authorized personnel to verify effective energy control and "
                "absence of hazardous energy under the approved site procedure before access; "
                "permit or lockout/tagout presence alone is not verification. "
                "No physical isolation or operating permission is asserted."
            )
        ),
    ]
    post_work_checks: Annotated[
        ScopeItems,
        Field(
            description=(
                "Cover every post-service planning category returned by the focused context, "
                "even when an earlier proposed draft omitted it. "
                "Proposed checks after separately authorized service: refit guards and protection; "
                "validate the measurement/protection path; compare the established winding channels "
                "and peers at comparable load; perform only an authorized post-service stability "
                "check against applicable criteria. These are future planning requirements, not "
                "completed tests or manual-specific facts unless the cited passage says so. "
                "Do not invent measurements, elapsed durations or passed outcomes."
            )
        ),
    ]
    replacement_parts: Annotated[list[str], Field(max_length=0)]
    parts_decision: ScopeText
    open_questions: ScopeItems


def scope_planning_requirements():
    """Generic planning requirements, never measured facts or physical authority."""
    return {
        "before_method_selection": [
            "First confirm the installed motor, cooler construction/materials and related-manual applicability.",
            "Require applicable supervisor/site authorization and verified isolation before physical access.",
            "Inspect first; choose a cleaning method only if the fitted equipment and actual cited passage support it.",
        ],
        "manual_method_fidelity": {
            "complete_context": "Read the complete method-specific conditions, material restrictions, prohibitions and required follow-up handling in the actual cited passage before proposing that method.",
            "conditional_steps_and_supplies": "Preserve every source condition in both the planned step and its tools/supplies entry. Do not broaden a method triggered by a particular condition into general alternatives or replace its conditions with generic site approval.",
            "follow_up_handling": "Retain every associated handling, completion and verification caution; selecting a method does not waive the instructions that follow it.",
            "bounded_excerpt_gap": "If a bounded excerpt ends mid-instruction or leaves the method conditions or follow-up handling incomplete, perform a focused follow-up manual search. If the complete supported conditions remain unavailable, omit the method and record the source gap as an open question.",
        },
        "planning_energy_control_verification": "Before physical access, propose identifying applicable energy sources and require authorized personnel to verify effective energy control and absence of hazardous energy under the approved site procedure. Checking permits or lockout/tagout presence alone does not establish effective isolation. This is a future planning requirement, not a completed verification or a claim that equipment is safe.",
        "post_service_check_categories": {
            "guards_and_protection": "Propose checking guards, covers and protective devices are correctly refitted before authorized restart.",
            "measurement_path": "Propose validating the actual temperature measurement and protection path; an alarm channel alone is not a confirmed physical condition.",
            "comparable_winding_readings": "Propose comparing the winding channels and available peers already identified in this Work under comparable load and conditions.",
            "authorized_stability": "Propose an authorized post-service stability check against applicable site/manufacturer criteria before any operating release.",
        },
        "source_boundary": "These are proposed planning categories, not completed checks or assertions from a manual. Site requirements and fitted applicability remain to be verified.",
        "result_boundary": "Do not invent measurements, passed outcomes, physical execution, trial durations or return-to-service evidence. Prepared E05/E06 results belong to the later verification stage and must not be requested to prepare this scope.",
    }
