"""Scoped approval inbox, exact auditory review, and canonical decisions.

The coordinator owns focus/CAS/playback. This adapter owns only approval-domain
interpretation; it never invokes a model, provider, or a second business executor.
"""

import hashlib
import json
import re
from datetime import timedelta
from uuid import uuid4

from ai.core.decisions.adapters.approval_reference import reference, spoken_reference
from ai.core.decisions.coordinator import DecisionConflict, DecisionReply
from ai.core.decisions.grammar import _normalize_command
from ai.core.decisions.models import PendingDecision

ACTIVE = ("pending", "in_review", "changes_requested")
INBOX_COMMANDS = frozenset((
    "what needs my approval",
    "show my approvals",
    "read my approvals",
    "approval inbox",
    "list approvals",
))
ORDINALS = {
    "one": 1,
    "first": 1,
    "two": 2,
    "second": 2,
    "three": 3,
    "third": 3,
    "four": 4,
    "fourth": 4,
    "five": 5,
    "fifth": 5,
    "six": 6,
    "sixth": 6,
    "seven": 7,
    "seventh": 7,
    "eight": 8,
    "eighth": 8,
    "nine": 9,
    "ninth": 9,
}


class ApprovalAdapter:
    """No email adapter: the owner's mailbox implementation remains screen-owned."""

    def owner(self, actor):
        from ai.core.decisions.adapters.proposal_adapter import ProposalAdapter
        from approvals.services import _require_reviewer

        owner, (_, scope_hash) = ProposalAdapter().owner_scope(actor)
        return _require_reviewer(owner), scope_hash

    def queryset(self, owner):
        from approvals.access import visible_approvals
        from approvals.models import Approval

        return visible_approvals(Approval.objects.all(), owner, force_scoped=True)

    def read(self, decision, actor):
        """Every interaction rechecks assignment, scope, revision and full content."""
        from approvals.review_sections import compute_review_hash

        owner, scope_hash = self.owner(actor)
        if scope_hash != decision.scope_hash:
            raise DecisionConflict("Your review scope changed. Open the inbox again.")
        if decision.kind == "selection":
            ids = list((decision.executable or {}).get("ids", ()))
            rows = {
                str(a.pk): a for a in self.queryset(owner).filter(pk__in=ids, status__in=ACTIVE)
            }
            fingerprints = decision.executable.get("fingerprints", {})
            if len(rows) != len(ids) or any(
                compute_review_hash(rows[key]) != fingerprints.get(key) for key in ids
            ):
                raise DecisionConflict("The inbox changed. Ask for your approvals again.")
            return None
        approval = self.queryset(owner).filter(pk=decision.source_id).first()
        if approval is None:
            raise DecisionConflict("This request is no longer available to you.")
        if decision.state == "presented" and (
            approval.status not in ACTIVE
            or approval.current_revision_number != decision.revision
            or compute_review_hash(approval) != decision.preview_hash
        ):
            raise DecisionConflict("This request changed. Read its current review again.")
        return approval

    def _install(
        self,
        c,
        *,
        actor,
        session_id,
        thread_id,
        nonce,
        expected,
        source_id,
        label,
        kind,
        spoken,
        sections=(),
        revision=0,
        preview_hash=None,
        required_phrase=None,
        executable=None,
        eligible=True,
        reason=None,
        acknowledged=False,
    ):
        _, scope_hash = self.owner(actor)
        now = c.now()
        continuing = (
            expected is not None
            and expected.state == "presented"
            and expected.source_id == source_id
            and expected.preview_hash == preview_hash
        )
        armed_at = expected.armed_at if continuing else now
        expires_at = expected.expires_at if continuing else now + timedelta(seconds=c.max_armed_s)
        decision = PendingDecision(
            decision_id=str(uuid4()),
            kind=kind,
            source_id=source_id,
            revision=revision,
            state="presented",
            target_label=label[:255],
            expires_at=expires_at,
            sequence=1,
            actor_user_pk=str(actor.user_pk),
            session_id=str(session_id),
            thread_id=str(thread_id),
            scope_hash=scope_hash,
            nonce=str(nonce),
            armed_at=armed_at,
            source_content=spoken[:4000],
            sections=tuple(sections),
            required_review_sections=tuple(s["id"] for s in sections if s.get("required")),
            locale="en-US",
            preview_hash=preview_hash,
            required_phrase=required_phrase,
            allowed_responses=tuple(
                filter(None, (required_phrase, "repeat", "cancel", "next section", "skip"))
            ),
            executable={"adapter": "approval", **(executable or {})},
            voice_eligible=eligible,
            voice_ineligible_reason=reason,
            review_acknowledged=acknowledged,
            spoken_summary=spoken,
            review_turns=expected.review_turns if continuing else 0,
        )
        if not c.store.install(decision, expected):
            raise DecisionConflict("A newer decision is active. Read its current preview.")
        return DecisionReply(spoken, decision, "presented")

    def begin(self, c, content, **arguments):
        command = _normalize_command(content)
        if command in INBOX_COMMANDS:
            return self.inbox(c, **arguments, expected=c.store.read(arguments["thread_id"]))
        match = re.fullmatch(r"(?:read|review|open) (?:approval|request) (.+)", command)
        if not match:
            from ai.core.decisions.adapters.purchasing_adapter import begin

            return begin(c, self, content, **arguments)
        owner, _ = self.owner(arguments["actor"])
        candidates = list(self.queryset(owner).filter(status__in=ACTIVE))
        requested = reference(match[1])
        matches = [a for a in candidates if requested and a.pk.hex.startswith(requested)]
        if len(matches) != 1:
            return DecisionReply(
                "I could not find one assigned request. Ask for your approval inbox.",
                event="refused",
            )
        return self.review(
            c, matches[0], expected=c.store.read(arguments["thread_id"]), **arguments
        )

    def inbox(self, c, *, actor, session_id, thread_id, nonce, expected, offset=0):
        from approvals.review_sections import compute_review_hash

        owner, _ = self.owner(actor)
        rows = list(
            self
            .queryset(owner)
            .filter(status__in=ACTIVE)
            .order_by("created_at", "pk")[offset : offset + 10]
        )
        if not rows:
            c.disarm(thread_id, "inbox_empty")
            return DecisionReply(
                "There are no more assigned requests available in this inbox. Email review is separate.",
                event="inbox_empty",
            )
        page = rows[:9]
        sections = [
            {"id": str(i), "label": f"Request {i}", "text": f"{str(a.pk)[:8]}: {a.summary[:180]}"}
            for i, a in enumerate(page, 1)
        ]
        spoken = " ".join(f"{s['label']}: {s['text']}." for s in sections)
        spoken += " Which should I read? Say its number, one through nine. During review say next section, repeat, or skip."
        if len(rows) > 9:
            spoken += " Say next page for more requests."
        return self._install(
            c,
            actor=actor,
            session_id=session_id,
            thread_id=thread_id,
            nonce=nonce,
            expected=expected,
            source_id=f"inbox:{owner.pk}",
            label="Your assigned approval requests",
            kind="selection",
            spoken=spoken,
            sections=sections,
            preview_hash=hashlib.sha256(json.dumps(sections, sort_keys=True).encode()).hexdigest(),
            executable={
                "action": "select",
                "ids": [str(a.pk) for a in page],
                "fingerprints": {str(a.pk): compute_review_hash(a) for a in page},
                "offset": offset,
                "has_more": len(rows) > 9,
            },
        )

    def review(self, c, approval, *, actor, session_id, thread_id, nonce, expected):
        from ai.core.config import get_settings
        from approvals import services
        from approvals.review_evidence import review_units
        from approvals.review_sections import (
            build_review_sections,
            compute_review_hash,
            voice_eligibility,
        )

        owner, _ = self.owner(actor)
        if approval.status in ("pending", "changes_requested"):
            services.open_approval(approval.pk, actor=owner, channel="voice")
            approval.refresh_from_db()
        units = review_units(approval)
        eligible, reason = voice_eligibility(approval)
        if not get_settings().feature_voice_auditory_review:
            eligible, reason = False, "Auditory review is disabled. Review this request on screen."
        spoken = units[0]["text"] if eligible else reason
        return self._install(
            c,
            actor=actor,
            session_id=session_id,
            thread_id=thread_id,
            nonce=nonce,
            expected=expected,
            source_id=str(approval.pk),
            label=f"Request {str(approval.pk)[:8]}: {approval.summary}",
            kind="approval_review",
            spoken=spoken,
            sections=build_review_sections(approval),
            revision=approval.current_revision_number,
            preview_hash=compute_review_hash(approval),
            executable={"action": "review", "page": 0},
            eligible=eligible,
            reason=reason,
        )

    def bind_delivery(self, decision, utterance_id):
        """Only exact persisted review-page text can become delivery evidence."""
        if decision.kind != "approval_review" or not decision.voice_eligible:
            return
        from ai.core.auth import principal_for_user
        from approvals.review_evidence import record_delivery, review_units
        from voice.models import VoiceUtterance

        utterance = VoiceUtterance.objects.select_related("session__owner").get(pk=utterance_id)
        if str(utterance.session_id) != decision.session_id:
            raise DecisionConflict("The playback session changed.")
        approval = self.read(decision, principal_for_user(utterance.session.owner))
        unit = review_units(approval)[int(decision.executable["page"])]
        record_delivery(
            approval, actor=utterance.session.owner, utterance=utterance, unit_ids=[unit["id"]]
        )

    def resolve(
        self,
        c,
        decision,
        content,
        *,
        actor,
        session_id,
        thread_id,
        nonce,
        context,
        provider_active=False,
        touch=False,
    ):
        """Selection, review, acknowledgment and execution are distinct transitions."""
        from approvals import services
        from approvals.review_evidence import required_sections, review_units

        if str(actor.user_pk) != decision.actor_user_pk:
            raise DecisionConflict("The session changed. Open your approval inbox again.")
        command = _normalize_command(content)
        if command.startswith("approve "):
            parsed_reference = reference(command.removeprefix("approve "))
            if parsed_reference is not None:
                command = f"approve {parsed_reference}"
        if decision.state in ("executing", "resolved"):
            return (
                c.history(decision, actor)
                if command
                in (
                    "repeat",
                    "what changed",
                    "last action status",
                    "did that complete",
                    "yes",
                    "cancel",
                    decision.required_phrase,
                )
                else None
            )
        if decision.state != "presented":
            if command in ("yes", decision.required_phrase):
                # A cached inactive preview is still private after revocation.
                self.read(decision, actor)
                return DecisionReply(
                    "Nothing is armed. Read the request again.", decision, "disarmed"
                )
            return None
        # A same-owner reconnect can read terminal receipts or explicitly open
        # a new request. Only an active prompt carries session-bound authority.
        if str(session_id) != decision.session_id:
            raise DecisionConflict("The session changed. Open your approval inbox again.")
        if c.now() >= decision.expires_at:
            return DecisionReply(
                "This review expired. Read the request again.",
                c.advance(decision, state="expired"),
                "expired",
            )
        try:
            approval = self.read(decision, actor)
        except Exception:
            c.disarm(thread_id, "source_invalidated")
            raise
        args = {
            "actor": actor,
            "session_id": session_id,
            "thread_id": thread_id,
            "nonce": nonce,
            "expected": decision,
        }
        if command in ("cancel", "no", "set aside", "cancel that", "set that aside"):
            return c.disarm(thread_id, "declined")
        if command in ("skip", "skip this request"):
            owner, _ = self.owner(actor)
            ids = [
                str(pk)
                for pk in self
                .queryset(owner)
                .filter(status__in=ACTIVE)
                .order_by("created_at", "pk")
                .values_list("pk", flat=True)
            ]
            offset = (
                ids.index(decision.source_id) + 1
                if decision.source_id in ids
                else int(decision.executable.get("offset", 0)) + 9
            )
            return self.inbox(c, **args, offset=offset)
        if decision.kind == "selection":
            if command == "next page" and decision.executable.get("has_more"):
                return self.inbox(c, **args, offset=int(decision.executable.get("offset", 0)) + 9)
            normalized = re.sub(
                r"^(?:read |review |open |select )?(?:the )?(?:request |number |option )?",
                "",
                command,
            )
            normalized = re.sub(
                r"(?: one| request)?(?:[,.]? read (?:the )?details)?$", "", normalized
            )
            ordinal = int(normalized) if normalized.isdecimal() else ORDINALS.get(normalized)
            ids = decision.executable["ids"]
            selected_id = ids[ordinal - 1] if ordinal and 1 <= ordinal <= len(ids) else None
            if selected_id is None:
                requested = reference(normalized)
                matches = [
                    pk for pk in ids if requested and pk.replace("-", "").startswith(requested)
                ]
                if len(matches) == 1:
                    selected_id = matches[0]
            if selected_id is not None:
                owner, _ = self.owner(actor)
                selected = self.queryset(owner).get(pk=selected_id)
                return self.review(c, selected, **args)
            return DecisionReply(decision.spoken_summary, decision, "review")
        if command == "what changed":
            latest = approval.revisions.order_by("-revision_number").first()
            diff = latest.diff_summary if latest else None
            return DecisionReply(
                f"Current revision {approval.current_revision_number}. Changes: {json.dumps(diff, ensure_ascii=False)}. Read the full request again before deciding."
                if diff
                else "No revision changes are recorded. The full current request still requires review.",
                decision,
                "status",
            )
        if command in ("repeat", "repeat that", "read it back", "read more"):
            if decision.review_turns < c.max_review_turns:
                decision = c.advance(
                    decision,
                    review_turns=decision.review_turns + 1,
                    expires_at=min(
                        c.now() + timedelta(seconds=c.ttl_s),
                        decision.armed_at + timedelta(seconds=c.max_armed_s),
                    ),
                )
            return DecisionReply(decision.spoken_summary, decision, "review")
        if command in ("next", "next section", "continue") and decision.kind == "approval_review":
            units = review_units(approval)
            page = int(decision.executable["page"]) + 1
            if page >= len(units):
                return DecisionReply(
                    "That was the last section. When you have reviewed every section, say I have reviewed this request.",
                    decision,
                    "status",
                )
            return self._install(
                c,
                **args,
                source_id=decision.source_id,
                label=decision.target_label,
                kind="approval_review",
                spoken=units[page]["text"],
                sections=decision.sections,
                revision=decision.revision,
                preview_hash=decision.preview_hash,
                executable={"action": "review", "page": page},
                eligible=decision.voice_eligible,
                reason=decision.voice_ineligible_reason,
                acknowledged=decision.review_acknowledged,
            )
        if command == "i have reviewed this request" and decision.kind == "approval_review":
            owner, _ = self.owner(actor)
            try:
                services.confirm_viewed(
                    approval.pk,
                    actor=owner,
                    channel="screen" if touch else "voice",
                    evidence={
                        "revision": decision.revision,
                        "review_hash": decision.preview_hash,
                        "sections": required_sections(approval),
                        "acknowledgment": "I have reviewed this request",
                    },
                )
            except services.ApprovalServiceError:
                return DecisionReply(
                    "Review is incomplete or changed. Every required section must finish playing before voice acknowledgment; otherwise review on screen.",
                    decision,
                    "review_incomplete",
                )
            updated = c.advance(decision, review_acknowledged=True)
            return DecisionReply(
                f"Review acknowledged, not approved. Say approve {spoken_reference(approval.pk)} to request the final confirmation.",
                updated,
                "acknowledged",
            )
        desired = None
        reason = ""
        required = ""
        if command == f"approve {str(approval.pk)[:8]}":
            if (
                decision.kind == "approval_decision"
                and decision.executable["action"] == "approval.approve"
            ):
                desired = None  # handled by the exact final phrase below
            elif not decision.review_acknowledged:
                return DecisionReply(
                    "Review and explicitly acknowledge the complete request before approval.",
                    decision,
                    "review_incomplete",
                )
            else:
                desired, required = "approve", command
        match = re.fullmatch(
            r"(?:deny|reject)(?: (?:this )?request)?[ :]+(.+)", content.strip(), re.IGNORECASE
        )
        change = re.fullmatch(r"request changes[ :]+(.+)", content.strip(), re.IGNORECASE)
        if match:
            desired, required, reason = "deny", "confirm rejection", match[1].strip()
        elif change:
            desired, required, reason = (
                "request_changes",
                "confirm request changes",
                change[1].strip(),
            )
        elif command in ("cancel the request", "cancel request"):
            desired, required = "cancel", "confirm cancel request"
        if desired:
            if len(reason) > 400:
                return DecisionReply(
                    "That reason needs screen review. No decision was submitted.",
                    decision,
                    "ineligible",
                )
            from approvals.policy import effective_risk_tier

            spoken = f"Request {spoken_reference(approval.pk)}: {desired.replace('_', ' ')}. "
            if reason:
                spoken += f"Reason: {reason}. "
            spoken_phrase = (
                f"approve {spoken_reference(approval.pk)}" if desired == "approve" else required
            )
            spoken += f"Say {spoken_phrase} to confirm, or cancel to set it aside."
            return self._install(
                c,
                **args,
                source_id=decision.source_id,
                label=decision.target_label,
                kind="approval_decision",
                spoken=spoken,
                sections=decision.sections,
                revision=decision.revision,
                preview_hash=decision.preview_hash,
                required_phrase=required,
                executable={"action": f"approval.{desired}", "reason": reason},
                eligible=decision.voice_eligible
                if desired == "approve"
                else effective_risk_tier(approval) < 3,
                reason=decision.voice_ineligible_reason
                if desired == "approve"
                else "Tier-3 decisions require screen review."
                if effective_risk_tier(approval) >= 3
                else None,
                acknowledged=decision.review_acknowledged,
            )
        if decision.kind == "approval_decision" and command == decision.required_phrase:
            if not touch and (
                not decision.voice_eligible
                or c.echo_window(decision, provider_active=provider_active)
            ):
                return DecisionReply(
                    "Wait until the read-back finishes, or review this request on screen.",
                    decision,
                    "echo_refused",
                )
            return self.execute(c, decision, actor, channel="screen" if touch else "voice")
        if command == "yes" or command.startswith(("yes ", "approve ", "confirm ")):
            if command.startswith("yes "):
                c.disarm(thread_id, "qualified_assent")
                return DecisionReply(
                    "That sounded qualified. The request was set aside; read a fresh request before confirming.",
                    c.store.read(thread_id),
                    "refused",
                )
            return DecisionReply(
                "That is not the exact confirmation for this request. No decision was submitted.",
                decision,
                "refused",
            )
        c.disarm(thread_id, "unrelated")
        return DecisionReply(
            "The request was set aside. No decision was submitted.",
            c.store.read(thread_id),
            "unrelated",
            True,
        )

    def execute(self, c, decision, actor, *, channel):
        from ai.core.decisions.receipts import finish_operation, start_operation
        from ai.core.tools.read_only import confirmed_write_exception
        from approvals import services
        from approvals.permissions import ApprovalDecisionThrottle
        from approvals.policy import ApprovalPolicyError, require_policy

        approval = self.read(decision, actor)
        owner, _ = self.owner(actor)
        action = decision.executable["action"].removeprefix("approval.")
        if action == "approve":
            from ai.core.config import get_settings

            if (
                approval.action_type == "purchase_order"
                and not get_settings().feature_voice_external_actions
            ):
                return DecisionReply(
                    "Purchasing voice actions are disabled. Review this on screen.",
                    decision,
                    "ineligible",
                )
            try:
                require_policy(approval, actor=owner, channel=channel)
            except ApprovalPolicyError as exc:
                return DecisionReply(str(exc), decision, "ineligible")
        if not ApprovalDecisionThrottle().allow_actor(owner):
            return DecisionReply(
                "The decision rate limit is 30 per minute. Wait before confirming again.",
                decision,
                "throttled",
            )
        decision = c.advance(decision, state="executing", execution_state="executing")
        operation, created = start_operation(decision)
        decision = c.advance(decision, operation_id=str(operation.pk))
        if not created:
            return c.history(decision, actor)
        try:
            from ai.core.config import get_settings

            if get_settings().voice_action_dry_run:
                finish_operation(
                    operation,
                    state="failed_before_effect",
                    detail="Dry run: no approval action was submitted.",
                )
            else:
                data = {
                    "instructions"
                    if action == "request_changes"
                    else "reason": decision.executable.get("reason", ""),
                    "revision": decision.revision,
                    "review_hash": decision.preview_hash,
                }
                method = {
                    "approve": services.approve,
                    "deny": services.deny,
                    "request_changes": services.request_changes,
                    "cancel": services.cancel,
                }[action]
                with confirmed_write_exception():
                    result = method(approval.pk, actor=owner, channel=channel, data=data)
                payload = result.data
                from approvals.models import ApprovalEvent

                event_type = {
                    "deny": "denied",
                    "request_changes": "changes_requested",
                    "cancel": "canceled",
                }.get(action)
                event = (
                    ApprovalEvent.objects
                    .filter(approval=approval, actor_user=owner, event_type=event_type)
                    .order_by("-timestamp")
                    .first()
                    if event_type
                    else None
                )
                state = (
                    (payload.get("execution_result") or {}).get("execution_state")
                    if action == "approve"
                    else "succeeded"
                )
                if action == "approve" and payload["status"] != "succeeded":
                    state = state or "unknown"
                finish_operation(
                    operation,
                    state=state or "succeeded",
                    receipt={
                        "approval_id": str(approval.pk),
                        "status": payload["status"],
                        "action": action,
                        "execution_result": payload.get("execution_result"),
                        "event_id": str(event.pk) if event else None,
                    },
                )
        except services.ApprovalServiceError as exc:
            finish_operation(operation, state="failed_before_effect", detail=exc.detail)
        except Exception:
            finish_operation(operation, state="unknown")
        decision = c.advance(
            decision,
            state="resolved",
            execution_state=operation.state,
            receipt_ref=operation.receipt_ref or None,
        )
        return c.history(decision, actor)
