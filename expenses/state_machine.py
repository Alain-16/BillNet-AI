from __future__ import annotations

from django.db import transaction

from common.enums import (
    AuditActorType, AuditEventType, ExpenseState,
)
from common.errors import DomainError
from operations.services import record_event

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ExpenseState.DISCOVERED: {ExpenseState.FILE_PENDING,
                              ExpenseState.EXTRACTION_PENDING,
                              ExpenseState.BLOCKED, ExpenseState.REJECTED},
    ExpenseState.FILE_PENDING: {ExpenseState.CLASSIFICATION_PENDING,
                                ExpenseState.EXTRACTION_PENDING,
                                ExpenseState.BLOCKED, ExpenseState.REJECTED},
    ExpenseState.CLASSIFICATION_PENDING: {ExpenseState.EXTRACTION_PENDING,
                                          ExpenseState.BLOCKED, ExpenseState.REJECTED},
    ExpenseState.EXTRACTION_PENDING: {ExpenseState.VALIDATION_PENDING,
                                      ExpenseState.BLOCKED, ExpenseState.REJECTED},
    ExpenseState.VALIDATION_PENDING: {ExpenseState.MATCHING_PENDING,
                                      ExpenseState.REVIEW_REQUIRED,
                                      ExpenseState.BLOCKED, ExpenseState.REJECTED},
    ExpenseState.MATCHING_PENDING: {ExpenseState.POLICY_PENDING,
                                    ExpenseState.REVIEW_REQUIRED,
                                    ExpenseState.BLOCKED, ExpenseState.REJECTED},
    ExpenseState.POLICY_PENDING: {ExpenseState.REVIEW_REQUIRED,
                                  ExpenseState.AUTO_POST_ELIGIBLE,
                                  ExpenseState.BLOCKED, ExpenseState.REJECTED},
    # A correction sends the expense back through validation -- that is why
    # REVIEW_REQUIRED can return to VALIDATION_PENDING.
    ExpenseState.REVIEW_REQUIRED: {ExpenseState.VALIDATION_PENDING,
                                   ExpenseState.APPROVED, ExpenseState.REJECTED,
                                   ExpenseState.BLOCKED},
    ExpenseState.AUTO_POST_ELIGIBLE: {ExpenseState.APPROVED, ExpenseState.BLOCKED,
                                      ExpenseState.REJECTED},
    ExpenseState.BLOCKED: {ExpenseState.VALIDATION_PENDING,
                           ExpenseState.REVIEW_REQUIRED, ExpenseState.REJECTED},
    ExpenseState.APPROVED: {ExpenseState.POSTING_PENDING,
                            ExpenseState.REVIEW_REQUIRED},
    ExpenseState.POSTING_PENDING: {ExpenseState.POSTING_IN_PROGRESS,
                                   ExpenseState.POSTING_FAILED},
    ExpenseState.POSTING_IN_PROGRESS: {ExpenseState.POSTED,
                                       ExpenseState.POSTING_FAILED,
                                       ExpenseState.POSTING_UNKNOWN},
    ExpenseState.POSTING_FAILED: {ExpenseState.POSTING_PENDING,
                                  ExpenseState.REVIEW_REQUIRED},
    # Never blind-retry a create (Dev Guide 3.e Layer 4): readback decides.
    ExpenseState.POSTING_UNKNOWN: {ExpenseState.POSTED, ExpenseState.POSTING_PENDING},
    ExpenseState.POSTED: set(),        # terminal
    ExpenseState.REJECTED: set(),      # terminal
}


def _require_interpretation(expense):
    if expense.interpretation_id is None:
        raise DomainError("The expense has no extraction to review.",
                          code="no_interpretation")


def _require_no_blocking_errors(expense):
    failing = [c for c in (expense.validations or [])
               if c.get("severity") == "ERROR" and not c.get("passed")]
    if failing:
        codes = ", ".join(c["code"] for c in failing)
        raise DomainError(
            f"Cannot approve while validation errors remain: {codes}.",
            code="validation_errors_present")


# PRD Feature 10: "Approve-and-post is unavailable until all mandatory posting
# fields pass validation." That rule is enforced HERE, not in the UI.
PREREQUISITES = {
    ExpenseState.REVIEW_REQUIRED: [_require_interpretation],
    ExpenseState.APPROVED: [_require_interpretation, _require_no_blocking_errors],
    ExpenseState.AUTO_POST_ELIGIBLE: [_require_interpretation,
                                      _require_no_blocking_errors],
}


@transaction.atomic
def transition(*, expense, to_state: str, actor=None, reason: str = ""):
    """The only legal way to change Expense.state."""
    from expenses.models import Expense

    # Lock first: two workers must not evaluate the same transition
    # concurrently (queue delivery is at-least-once).
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    from_state = expense.state

    if from_state == to_state:
        return expense                     # idempotent (Invariant #3)

    if to_state not in ALLOWED_TRANSITIONS.get(from_state, set()):
        raise DomainError(
            f"Cannot move an expense from {from_state} to {to_state}.",
            code="illegal_transition",
            field="state")

    for check in PREREQUISITES.get(to_state, []):
        check(expense)

    expense.state = to_state
    expense.version += 1
    expense.save(update_fields=["state", "version", "updated_at"])

    record_event(
        company=expense.company, event_type=AuditEventType.STATE_CHANGED,
        aggregate_type="Expense", aggregate_id=expense.id,
        actor_type=AuditActorType.USER if actor else AuditActorType.SYSTEM,
        actor_id=getattr(actor, "id", ""), object_version=expense.version,
        payload={"from": from_state, "to": to_state, "reason": reason},
    )
    return expense