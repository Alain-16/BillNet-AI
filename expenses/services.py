from __future__ import annotations

from decimal import Decimal
from django.db import transaction

import re
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounting.models import AccountingReference
from common.enums import (
    AccountingRefType, AuditActorType, AuditEventType, ProjectStatus,ExpenseState,ExtractionStatus
)
from common.errors import DomainError
from expenses.models import Project, Expense
from operations.services import record_event
from common.errors import DomainError
from documents.models import DocumentInterpretation,SourceDocument
from documents.parsers import fields_to_expense_values
from expenses.state_machine import transition
from expenses.validators import blocking_errors, run_validations



# `code` is deliberately absent -- immutable after creation (see header note).
EDITABLE_PROJECT_FIELDS = {"name", "aliases", "active_from", "active_to", "status"}

_WHITESPACE = re.compile(r"\s+")
CORRECTABLE_FIELDS = {
    "vendor_raw_name", "receipt_number", "transaction_date", "currency",
    "subtotal", "tax_total", "total", "tax_breakdown", "card_last_four",
    "payment_type", "memo", "project",
}
# Amounts must arrive as Decimal, never float (Invariant #2).
_DECIMAL_FIELDS = {"subtotal", "tax_total", "total"}


def normalize_code(code: str) -> str:

    if not code or not code.strip():
        raise DomainError("Project code is required.", code="project_code_required", field="code")
    return _WHITESPACE.sub(" ",code.strip()).upper()


def normalize_aliases(aliases) -> list[str]:
    """Dedupe case-insensitively but PRESERVE the owner's casing. The aliases
    are shown back to a human in the review UI; matching casefolds at compare
    time, so we lose nothing by keeping 'Maple St' instead of 'MAPLE ST'."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in aliases or []:
        cleaned = _WHITESPACE.sub(" ", str(raw).strip())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _validate_dates(active_from, active_to) -> None:
    if active_from and active_to and active_to < active_from:
        raise DomainError("Project end date cannot precede its start date.",
                          code="project_dates_invalid", field="active_to")


def _json_safe(value):
    """AuditEvent.payload is a plain JSONField -- a `date` object raises
    TypeError on serialize. Convert at the boundary, not at every call site."""
    if isinstance(value, date):
        return value.isoformat()
    return value


# ---------- lifecycle ----------

@transaction.atomic
def create_project(*, company, actor, code: str, name: str, aliases=None,
                   active_from=None, active_to=None,
                   status: str = ProjectStatus.ACTIVE) -> Project:
    code = normalize_code(code)
    name = (name or "").strip()
    if not name:
        raise DomainError("Project name is required.",
                          code="project_name_required", field="name")
    aliases = normalize_aliases(aliases)
    _validate_dates(active_from, active_to)

    try:
        # NESTED atomic == a savepoint. Without it, the IntegrityError marks the
        # OUTER transaction for rollback and every later query in this request
        # raises TransactionManagementError. This is the standard Django gotcha
        # when you catch an IntegrityError instead of pre-checking with
        # .exists() -- and we want the constraint, not the pre-check, because
        # .exists() is a race between two concurrent creates.
        with transaction.atomic():
            project = Project.objects.create(
                company=company, code=code, name=name, aliases=aliases,
                status=status, active_from=active_from, active_to=active_to,
            )
    except IntegrityError:
        raise DomainError(f"A project with code '{code}' already exists.",
                          code="project_code_taken", field="code")

    record_event(
        company=company, event_type=AuditEventType.PROJECT_CREATED,
        aggregate_type="Project", aggregate_id=project.id,
        actor_type=AuditActorType.USER, actor_id=actor.id,
        object_version=project.version,
        payload={"code": code, "name": name, "aliases": aliases, "status": status,
                 "active_from": _json_safe(active_from),
                 "active_to": _json_safe(active_to)},
    )
    return project


@transaction.atomic
def update_project(*, project: Project, actor, changes: dict,
                   expected_version: int | None = None) -> Project:
    """Same allow-list + before/after shape as accounts.services.update_company_config.

    `expected_version` is the optimistic-lock precondition the PRD requires for
    mutating requests. Optional here (projects are rarely edited concurrently),
    but the pattern is established now because Feature 10 mandates it:
    "Two conflicting review updates are prevented through optimistic locking."
    """
    if expected_version is not None and expected_version != project.version:
        raise DomainError(
            "This project was modified by someone else. Reload and try again.",
            code="version_conflict", field="version",
        )

    applied: dict[str, dict] = {}
    for field, new in changes.items():
        if field not in EDITABLE_PROJECT_FIELDS:
            continue                       # silently ignore, incl. any 'code'
        if field == "aliases":
            new = normalize_aliases(new)
        elif field == "name":
            new = (new or "").strip()
            if not new:
                raise DomainError("Project name cannot be blank.",
                                  code="project_name_required", field="name")
        old = getattr(project, field)
        if old != new:
            setattr(project, field, new)
            applied[field] = {"from": _json_safe(old), "to": _json_safe(new)}

    # Validate the MERGED state, not just the incoming fields: changing only
    # active_from must still be checked against the already-stored active_to.
    _validate_dates(project.active_from, project.active_to)

    if applied:
        project.version += 1
        project.save(update_fields=[*applied.keys(), "version", "updated_at"])
        record_event(
            company=project.company, event_type=AuditEventType.PROJECT_UPDATED,
            aggregate_type="Project", aggregate_id=project.id,
            actor_type=AuditActorType.USER, actor_id=actor.id,
            object_version=project.version, payload={"changes": applied},
        )
    return project


@transaction.atomic
def close_project(*, project: Project, actor, closed_on=None) -> Project:
    """Closing is a status change, never a delete: the PRD requires that soft
    deletion preserve financially relevant records, and posted expenses hold a
    PROTECT FK to the project anyway -- a delete would fail at the database."""
    if project.status == ProjectStatus.CLOSED:
        return project                      # idempotent (Invariant #3)

    project.status = ProjectStatus.CLOSED
    fields = ["status", "version", "updated_at"]

    # Stamp an end date ONLY if the owner never set one. Do not overwrite a
    # deliberate value with today's date.
    if project.active_to is None:
        project.active_to = closed_on or timezone.localdate()
        fields.append("active_to")

    project.version += 1
    project.save(update_fields=fields)
    record_event(
        company=project.company, event_type=AuditEventType.PROJECT_CLOSED,
        aggregate_type="Project", aggregate_id=project.id,
        actor_type=AuditActorType.USER, actor_id=actor.id,
        object_version=project.version,
        payload={"active_to": _json_safe(project.active_to)},
    )
    return project


@transaction.atomic
def link_qbo_customer(*, project: Project, actor,
                      reference: AccountingReference) -> Project:
    """The guard function. Project.qbo_customer is a generic FK to
    AccountingReference, so NOTHING at the database level stops you linking a
    project to a tax code. This is the only place that check can live -- the
    model comment on the parallel Vendor.qbo_vendor field says as much."""
    # Object-level authorization: the PRD demands it on every company-owned
    # resource. Without this line a crafted reference id crosses companies.
    if reference.company_id != project.company_id:
        raise DomainError("That reference belongs to a different company.",
                          code="reference_cross_company", field="qbo_customer")
    if reference.entity_type != AccountingRefType.CUSTOMER:
        raise DomainError(
            f"Expected a QuickBooks customer, got {reference.entity_type}.",
            code="reference_wrong_type", field="qbo_customer")
    if not reference.active:
        raise DomainError("That QuickBooks customer is inactive in QuickBooks.",
                          code="reference_inactive", field="qbo_customer")

    project.qbo_customer = reference
    project.version += 1
    project.save(update_fields=["qbo_customer", "version", "updated_at"])
    record_event(
        company=project.company, event_type=AuditEventType.PROJECT_LINKED,
        aggregate_type="Project", aggregate_id=project.id,
        actor_type=AuditActorType.USER, actor_id=actor.id,
        object_version=project.version,
        payload={"qbo_customer_id": reference.external_id,
                 "qbo_customer_name": reference.name},
    )
    return project




def projects_active_on(company, on: date):
    """The FILTER, not the discriminator. Concurrent jobs overlap by
    definition, so this narrows candidates for Feature 6's matcher -- it can
    never pick a winner. Codes and aliases do that.

    A null active_from/active_to means "open-ended", so it always qualifies.
    """
    from django.db.models import Q
    return (Project.objects
            .filter(company=company, status=ProjectStatus.ACTIVE)
            .filter(Q(active_from__isnull=True) | Q(active_from__lte=on))
            .filter(Q(active_to__isnull=True) | Q(active_to__gte=on)))


def assert_project_postable(project: Project) -> None:

    if project.status != ProjectStatus.ACTIVE:
        raise DomainError(f"Project '{project.code}' is {project.status}.",
                          code="project_not_active", field="project")
    if project.qbo_customer_id is None:
        raise DomainError(
            f"Project '{project.code}' is not linked to a QuickBooks customer.",
            code="project_unlinked", field="project")
    if not project.qbo_customer.active:
        raise DomainError(
            f"The QuickBooks customer for '{project.code}' is now inactive.",
            code="project_customer_inactive", field="project")



@transaction.atomic
def create_expense_from_interpretation(*, interpretation: DocumentInterpretation,
                                       actor=None) -> Expense:
    """One Expense per SourceDocument. Re-extraction repoints the existing
    expense at the new interpretation version rather than creating a second.

    IDEMPOTENCY NOTE: this is enforced in code, not by the schema. Consider
    adding UniqueConstraint(fields=["company", "source_document"]) to Expense
    to make it airtight -- it needs a migration, so it is flagged, not assumed.
    """
    document = interpretation.document
    expense = (Expense.objects.select_for_update()
               .filter(company=document.company, source_document=document).first())

    if expense is None:
        expense = Expense.objects.create(
            company=document.company, source_document=document,
            interpretation=interpretation, state=ExpenseState.DISCOVERED,
            currency=document.company.currency,
        )
        record_event(company=document.company, event_type=AuditEventType.DISCOVERY,
                     aggregate_type="Expense", aggregate_id=expense.id,
                     actor_type=AuditActorType.USER if actor else AuditActorType.SYSTEM,
                     actor_id=getattr(actor, "id", ""),
                     payload={"source_document_id": str(document.id)})
    else:
        expense.interpretation = interpretation
        expense.save(update_fields=["interpretation", "updated_at"])

    return expense


def apply_interpretation_fields(expense: Expense) -> Expense:
    """Copy parsed values onto the Expense columns.

    NEVER overwrites a value a human already corrected: if the field differs
    from what the previous extraction produced, the human wins. That is what
    makes re-extraction safe after a correction.
    """
    interpretation = expense.interpretation
    if interpretation is None or interpretation.extraction_status != ExtractionStatus.COMPLETED:
        return expense                     # ABSTAINED/FAILED -> leave blank for manual entry

    values = fields_to_expense_values(interpretation.fields or {})
    changed = []
    for column, value in values.items():
        if getattr(expense, column) in (None, "", [], Decimal("0")):
            setattr(expense, column, value)
            changed.append(column)
    if changed:
        expense.save(update_fields=[*changed, "updated_at"])
    return expense


@transaction.atomic
def revalidate(*, expense: Expense, actor=None) -> Expense:
    """Run every deterministic check and route the expense accordingly.

    Called after extraction AND after every correction, so a human who fixes a
    total gets the arithmetic gate re-run on their own numbers.
    """
    checks = run_validations(expense)
    expense.validations = [c.to_json() for c in checks]
    expense.save(update_fields=["validations", "updated_at"])

    record_event(company=expense.company, event_type=AuditEventType.VALIDATION,
                 aggregate_type="Expense", aggregate_id=expense.id,
                 actor_type=AuditActorType.SYSTEM, object_version=expense.version,
                 payload={"failed": [c.code for c in checks if not c.passed],
                          "error_count": len(blocking_errors(checks))})

    # Slice 2 ends at REVIEW_REQUIRED. Matching (slice 3) and the policy engine
    # (slice 8) will insert MATCHING_PENDING / POLICY_PENDING ahead of it later.
    if expense.state in (ExpenseState.DISCOVERED, ExpenseState.EXTRACTION_PENDING,
                         ExpenseState.REVIEW_REQUIRED, ExpenseState.BLOCKED):
        if expense.state != ExpenseState.VALIDATION_PENDING:
            expense = transition(expense=expense,
                                 to_state=ExpenseState.VALIDATION_PENDING,
                                 actor=actor, reason="validation run")
    return transition(expense=expense, to_state=ExpenseState.REVIEW_REQUIRED,
                      actor=actor, reason="awaiting human review")


@transaction.atomic
def process_document(*, document: SourceDocument, actor=None) -> Expense:
    """The slice-2 pipeline in one call: extract -> expense -> fields -> validate
    -> review. Idempotent, so it is safe as a Celery task body.

    Runs synchronously from the upload endpoint for now; moving it to
    .delay() later changes nothing in this function.
    """
    from documents.services import run_extraction

    interpretation = run_extraction(document=document, actor=actor)
    expense = create_expense_from_interpretation(interpretation=interpretation,
                                                 actor=actor)
    if expense.state == ExpenseState.DISCOVERED:
        expense = transition(expense=expense, to_state=ExpenseState.EXTRACTION_PENDING,
                             actor=actor, reason="extraction complete")
    expense = apply_interpretation_fields(expense)
    return revalidate(expense=expense, actor=actor)


@transaction.atomic
def apply_corrections(*, expense: Expense, actor, changes: dict,
                      expected_version: int | None = None,
                      reason: str = "") -> Expense:
    """PRD Feature 10: 'Correct-and-post records original value, corrected
    value, user, timestamp, and optional correction reason.'"""
    if expected_version is not None and expected_version != expense.version:
        raise DomainError(
            "This expense was changed by someone else. Reload and try again.",
            code="version_conflict", field="version")

    if expense.state in (ExpenseState.POSTED, ExpenseState.REJECTED):
        raise DomainError(f"A {expense.state} expense cannot be edited.",
                          code="expense_immutable")

    applied: dict[str, dict] = {}
    for field, new in changes.items():
        if field not in CORRECTABLE_FIELDS:
            continue
        if field in _DECIMAL_FIELDS and new is not None:
            new = Decimal(str(new))        # str() first: a float would poison it
        if field == "project":
            new = (Project.objects.filter(company=expense.company, pk=new).first()
                   if new else None)
            if new is None and changes["project"]:
                raise DomainError("Unknown project.", code="unknown_project",
                                  field="project")
        old = getattr(expense, field)
        if old != new:
            setattr(expense, field, new)
            applied[field] = {"from": _audit_value(old), "to": _audit_value(new)}

    if not applied:
        return expense

    expense.version += 1
    expense.save(update_fields=[*applied.keys(), "version", "updated_at"])

    record_event(company=expense.company, event_type=AuditEventType.CORRECTION,
                 aggregate_type="Expense", aggregate_id=expense.id,
                 actor_type=AuditActorType.USER, actor_id=actor.id,
                 object_version=expense.version,
                 payload={"changes": applied, "reason": reason})

    # A correction re-opens validation. The reviewer's own numbers get the same
    # arithmetic gate the parser's did.
    return revalidate(expense=expense, actor=actor)


def _audit_value(value):
    """JSONField cannot serialize Decimal, date, or a model instance."""
    from datetime import date as _date
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, _date):
        return value.isoformat()
    if isinstance(value, Project):
        return {"id": str(value.id), "code": value.code}
    return value