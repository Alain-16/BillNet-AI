from __future__ import annotations

from decimal import Decimal
from django.db import transaction

import re
from datetime import date
from rapidfuzz import fuzz

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounting.models import AccountingReference
from common.enums import (
    AccountingRefType, AuditActorType, AuditEventType, ProjectStatus,ExpenseState,ExtractionStatus,MappingScope,PaymentType,VendorStatus
)
from common.errors import DomainError
from expenses.models import Project, Expense,Vendor,MappingRule
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
    "payment_type", "memo", "project","line_items"
}
# Amounts must arrive as Decimal, never float (Invariant #2).
_DECIMAL_FIELDS = {"subtotal", "tax_total", "total"}
RECOMMENDATION_SCHEMA_VERSION = "expense_recommendations.v1"
RESOLVED_SCORE = Decimal("0.75")
AMBIGUITY_GAP = Decimal("0.10")

_WORD_SPLIT = re.compile(r"[^a-z0-9]+", re.I)

ACCOUNT_KEYWORD_RULES = [
    {
        "key": "materials_supplies",
        "labels": ["materials", "job supplies", "construction supplies"],
        "keywords": [
            "lumber", "wood", "plywood", "drywall", "cement", "concrete",
            "paint", "primer", "pipe", "plumbing", "wire", "electrical",
            "screws", "nails", "caulk", "adhesive", "insulation",
        ],
    },
    {
        "key": "tools_equipment",
        "labels": ["tools", "equipment", "small tools"],
        "keywords": [
            "drill", "bit", "saw", "blade", "wrench", "hammer", "level",
            "ladder", "tool", "grinder", "sander", "battery", "charger",
        ],
    },
    {
        "key": "safety_supplies",
        "labels": ["safety", "safety supplies", "ppe"],
        "keywords": [
            "gloves", "goggles", "helmet", "hardhat", "mask", "respirator",
            "vest", "earplugs", "safety", "ppe",
        ],
    },
    {
        "key": "fuel_vehicle",
        "labels": ["fuel", "vehicle", "auto", "gas"],
        "keywords": [
            "fuel", "gas", "diesel", "petro", "shell", "chevron",
            "parking", "toll", "car wash",
        ],
    },
    {
        "key": "office_supplies",
        "labels": ["office", "office supplies"],
        "keywords": [
            "paper", "printer", "ink", "toner", "staples", "notebook",
            "pen", "folder", "envelope",
        ],
    },
]

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

def _decimal_score(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


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

def _human_corrected_fields(expense) -> set[str]:
    """Columns a person has explicitly changed. The audit trail already records
    this -- CORRECTION events carry payload["changes"] keyed by column name --
    so there is no need for a second source of truth on the model."""
    from operations.models import AuditEvent
    events = AuditEvent.objects.filter(
        company=expense.company, aggregate_type="Expense",
        aggregate_id=str(expense.id), event_type=AuditEventType.CORRECTION,
    ).values_list("payload", flat=True)
    corrected: set[str] = set()
    for payload in events:
        corrected.update((payload or {}).get("changes", {}).keys())
    return corrected

def apply_interpretation_fields(expense: Expense) -> Expense:
  
    interpretation = expense.interpretation
    if interpretation is None or interpretation.extraction_status != ExtractionStatus.COMPLETED:
        return expense                     # ABSTAINED/FAILED -> leave blank for manual entry

    protected = _human_corrected_fields(expense)
    values = fields_to_expense_values(interpretation.fields or {})
    changed = []
    for column, value in values.items():
        if column in protected:
            continue
        if getattr(expense, column) != value:
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
            if not new:
                new = None
            else:

                pk= getattr(new, "pk", new)

                new = Project.objects.filter(company=expense.company, pk=pk).first()

                if new is None:
                    raise DomainError("Unknown project.", code="unknown_project", field="project")
                
               
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

#------------------ document metadata ----------------#

def _candidate(
    *,
    object_type: str,
    label: str,
    score,
    source: str,
    object_id: str = "",
    external_id: str = "",
    evidence: dict | None = None,
    action_required: str = "",
    data: dict | None = None,
) -> dict:

    return {
        "object_type": object_type,
        "object_id": str(object_id or ""),
        "external_id": str(external_id or ""),
        "label": label,
        "score": str(_decimal_score(score)),
        "source": source,
        "evidence": evidence or {},
        "action_required": action_required,
        "data": data or {},
    }


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:

    best: dict[tuple[str, str, str], dict] = {}

    for candidate in candidates:
        key = (
            candidate["object_type"],
            candidate.get("object_id") or "",
            candidate.get("external_id") or "",
        )
        current = best.get(key)
        if current is None:
            best[key] = candidate
            continue
        if Decimal(candidate["score"]) > Decimal(current["score"]):
            merged = dict(candidate)
            merged["evidence"] = {
                **(current.get("evidence") or {}),
                **(candidate.get("evidence") or {}),
            }
            best[key] = merged

    return sorted(
        best.values(),
        key=lambda row: Decimal(row["score"]),
        reverse=True,
    )

def _recommendation_block(
    *,
    kind: str,
    candidates: list[dict],
    evidence: dict,
    unresolved_reason: str,
) -> dict:
    """Turn ranked candidates into a final recommendation block.

    Why:
        Matching functions should only produce evidence and scores. This helper
        applies the same conservative decision rule to every recommendation.
    """
    ranked = _dedupe_candidates(candidates)

    if not ranked:
        return {
            "kind": kind,
            "status": "unresolved",
            "selected": None,
            "candidates": [],
            "reason": unresolved_reason,
            "evidence": evidence,
        }

    top = ranked[0]
    top_score = Decimal(top["score"])
    second_score = Decimal(ranked[1]["score"]) if len(ranked) > 1 else Decimal("0")
    close_second = second_score and (top_score - second_score) <= AMBIGUITY_GAP

    if top_score < RESOLVED_SCORE:
        status = "unresolved"
        selected = None
        reason = f"Best {kind} candidate is below the confidence threshold."
    elif close_second:
        status = "ambiguous"
        selected = None
        reason = f"Multiple {kind} candidates are close; reviewer must choose."
    elif top.get("action_required"):
        status = "review_required"
        selected = top
        reason = top["action_required"]
    else:
        status = "resolved"
        selected = top
        reason = f"Strong {kind} recommendation found."

    return {
        "kind": kind,
        "status": status,
        "selected": selected,
        "candidates": ranked[:5],
        "reason": reason,
        "evidence": evidence,
    }

def _source_message(expense: Expense):
    document = expense.source_document
    return document.message if document and document.message_id else None

def _line_item_text(expense: Expense):
    parts: list[str] = []

    for item in expense.line_items or []:
        if isinstance(item, dict):
            parts.append(str(item.get("description") or ""))
        else:
            parts.append(str(item))
    return " ".join(p for p in parts if p).strip()

def _collect_recommendation_evidence(expense: Expense) -> dict:
    message = _source_message(expense)
    email_subject = message.subject if message else ""
    sender = message.sender if message else ""

    line_items = _line_item_text(expense)
    receipt_text = " ".join(
        str(value or "")
        for value in [
            expense.vendor_raw_name,
            expense.receipt_number,
            expense.memo,
            line_items,
        ]
    ).strip()

    project_text = " ".join(
        str(value or "")
        for value in [email_subject, expense.memo, line_items, expense.vendor_raw_name]
    ).strip()

    account_text = " ".join(
        str(value or "")
        for value in [line_items, expense.memo, expense.vendor_raw_name, email_subject]
    ).strip()

    all_text = " ".join(
        part
        for part in [receipt_text, email_subject, sender]
        if part
    ).strip()

    return {
        "vendor_raw_name": expense.vendor_raw_name or "",
        "email_subject": email_subject,
        "sender": sender,
        "line_item_text": line_items,
        "receipt_text": receipt_text,
        "project_text": project_text,
        "account_text": account_text,
        "all_text": all_text,
        "card_last_four": expense.card_last_four or "",
        "payment_type": expense.payment_type or "",
    }


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _WORD_SPLIT.split(text or "") if token}

def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase and phrase.lower() in (text or "").lower())

def _similarity(a: str, b: str) -> Decimal:
    if not a or not b:
        return Decimal("0")
    return _decimal_score(fuzz.token_set_ratio(a, b) / 100)

def _project_action_required(project: Project) -> str:
    if project.status != ProjectStatus.ACTIVE:
        return "Matched internal project is not active; reviewer must confirm."
    if project.qbo_customer_id is None:
        return "Matched internal project is not linked to a QuickBooks customer."
    if not project.qbo_customer.active:
        return "Matched project's QuickBooks customer is inactive."
    return ""

def _score_internal_project(project: Project, text: str) -> tuple[Decimal, dict]:
    evidence: dict = {}

    if project.code and _contains_phrase(text, project.code):
        return Decimal("0.95"), {"matched": "project_code", "value": project.code}

    for alias in project.aliases or []:
        if _contains_phrase(text, alias):
            return Decimal("0.90"), {"matched": "project_alias", "value": alias}

    if _contains_phrase(text, project.name):
        return Decimal("0.84"), {"matched": "project_name", "value": project.name}

    score = _similarity(text, f"{project.code} {project.name}")
    if score >= Decimal("0.78"):
        evidence = {"matched": "fuzzy_project_name", "value": project.name}
        return score, evidence

    return Decimal("0"), {}
def _score_qbo_customer(reference: AccountingReference, text: str) -> tuple[Decimal, dict]:
    if _contains_phrase(text, reference.name):
        return Decimal("0.82"), {
            "matched": "qbo_customer_name",
            "value": reference.name,
        }

    score = _similarity(text, reference.name)
    if score >= Decimal("0.78"):
        return score, {
            "matched": "fuzzy_qbo_customer_name",
            "value": reference.name,
        }

    return Decimal("0"), {}

def _recommend_project(expense: Expense, evidence: dict) -> dict:
    text = evidence["project_text"]
    candidates: list[dict] = []

    internal_projects = (
        Project.objects
        .filter(company=expense.company)
        .select_related("qbo_customer")
    )

    for project in internal_projects:
        score, match_evidence = _score_internal_project(project, text)
        if not score:
            continue
        candidates.append(
            _candidate(
                object_type="internal_project",
                object_id=project.id,
                external_id=project.qbo_customer.external_id if project.qbo_customer_id else "",
                label=f"{project.code} - {project.name}",
                score=score,
                source=match_evidence["matched"],
                evidence=match_evidence,
                action_required=_project_action_required(project),
                data={
                    "project_status": project.status,
                    "qbo_customer_id": str(project.qbo_customer_id or ""),
                },
            )
        )

    linked_customer_ids = {
        str(project.qbo_customer_id)
        for project in internal_projects
        if project.qbo_customer_id
    }

    qbo_customers = AccountingReference.objects.filter(
        company=expense.company,
        entity_type=AccountingRefType.CUSTOMER,
        active=True,
    )

    for reference in qbo_customers:
        score, match_evidence = _score_qbo_customer(reference, text)
        if not score:
            continue

        already_linked = str(reference.id) in linked_customer_ids
        candidates.append(
            _candidate(
                object_type="qbo_customer",
                object_id=reference.id,
                external_id=reference.external_id,
                label=reference.name,
                score=score,
                source=match_evidence["matched"],
                evidence=match_evidence,
                action_required=(
                    "" if already_linked
                    else "QuickBooks customer exists, but no internal project is linked to it."
                ),
                data={"already_linked_to_internal_project": already_linked},
            )
        )

    return _recommendation_block(
        kind="project",
        candidates=candidates,
        evidence={
            "text_used": text,
            "email_subject": evidence["email_subject"],
        },
        unresolved_reason=(
            "No project code, alias, internal project, or synced QuickBooks "
            "customer matched the receipt evidence."
        ),
    )

def _active_expense_accounts(company):

    return AccountingReference.objects.filter(
        company=company,
        entity_type=AccountingRefType.ACCOUNT,
        active=True,
    )

def _account_from_mapping_rules(rule: MappingRule, expense: Expense):
    outputs = rule.outputs or {}
    raw_id = (
        outputs.get("expense_account_id")
        or outputs.get("account_id")
        or outputs.get("accounting_reference_id")

    )
    if not raw_id:
        return None

    return AccountingReference.objects.filter(
        company=expense.company,
        pk=raw_id,
        entity_type=AccountingRefType.ACCOUNT,
        active=True

    ).first()


def _mapping_rule_matches(rule: MappingRule, expense: Expense, evidence: dict) -> tuple[bool, dict]:
    """Evaluate a deliberately small MappingRule condition language.

    Supported condition keys:
        vendor_name       - phrase match against receipt vendor
        vendor_id         - current internal Vendor id
        sender_domain     - domain contained in sender
        keywords          - all keywords must appear in account text
        any_keywords      - at least one keyword must appear in account text
        project_id        - current internal Project id

    Why:
        MappingRule is user-approved memory. It should be explicit and easy to
        audit, not a hidden learning system.
    """
    conditions = rule.conditions or {}
    matched: dict = {}

    vendor_name = conditions.get("vendor_name")
    if vendor_name:
        if not _contains_phrase(evidence["vendor_raw_name"], vendor_name):
            return False, {}
        matched["vendor_name"] = vendor_name

    vendor_id = conditions.get("vendor_id")
    if vendor_id:
        if str(expense.vendor_id or "") != str(vendor_id):
            return False, {}
        matched["vendor_id"] = str(vendor_id)

    sender_domain = conditions.get("sender_domain")
    if sender_domain:
        if sender_domain.lower() not in evidence["sender"].lower():
            return False, {}
        matched["sender_domain"] = sender_domain

    text_lower = evidence["account_text"].lower()

    keywords = [str(k).lower() for k in conditions.get("keywords", [])]
    if keywords:
        missing = [keyword for keyword in keywords if keyword not in text_lower]
        if missing:
            return False, {}
        matched["keywords"] = keywords

    any_keywords = [str(k).lower() for k in conditions.get("any_keywords", [])]
    if any_keywords:
        found = [keyword for keyword in any_keywords if keyword in text_lower]
        if not found:
            return False, {}
        matched["any_keywords"] = found

    project_id = conditions.get("project_id")
    if project_id:
        if str(expense.project_id or "") != str(project_id):
            return False, {}
        matched["project_id"] = str(project_id)

    return True, matched

def _mapping_rule_account_candidates(expense: Expense, evidence: dict) -> list[dict]:

    candidates: list[dict] = []

    rules = MappingRule.objects.filter(
        company= expense.company,
        scope= MappingScope.CATEGORY,
        active=True,
    ).order_by("priority")

    for rule in rules:
        matched, matched_evidence= _mapping_rule_matches(rule,expense,evidence)

        if not matched:
            continue

        account = _account_from_mapping_rules(rule, expense)
        if account is None:
            continue

        candidates.append(
            _candidate(
                object_type="qbo_expense_account",
                object_id=account.id,
                external_id=account.external_id,
                label=account.name,
                score=Decimal("0.98"),
                source="mapping_rule",
                evidence={
                    "mapping_rule_id": str(rule.id),
                    "mapping_rule_name": rule.name,
                    "matched_conditions": matched_evidence,
                },
            )
        )
    return candidates


def _best_account_for_keyword_rule(company, rule: dict, matched_keywords: list[str]):
    accounts = list(_active_expense_accounts(company))

    if not accounts:
        return None, Decimal("0")

    best_account = None
    best_name_score = Decimal("0")
    search_label = " ".join([*rule["labels"], *matched_keywords])

    for account in accounts:
        score= _similarity(search_label, account.name)
        if score > best_name_score:
            best_account = account
            best_name_score = score
    return best_account, best_name_score


def _keyword_account_candidates(expense: Expense, evidence: dict) -> list[dict]:
    candidates: list[dict] = []
    text = evidence["account_text"].lower()

    for rule in ACCOUNT_KEYWORD_RULES:
        matched_keywords = [
            keyword
            for keyword in rule["keywords"]
            if keyword.lower() in text
        ]
        if not matched_keywords:
            continue

        account, account_name_score = _best_account_for_keyword_rule(
            expense.company,
            rule,
            matched_keywords,
        )
        if account is None:
            continue

        keyword_strength = min(Decimal("0.15"), Decimal("0.03") * len(matched_keywords))
        score = Decimal("0.68") + keyword_strength + (account_name_score * Decimal("0.15"))
        score = min(score, Decimal("0.92"))

        candidates.append(
            _candidate(
                object_type="qbo_expense_account",
                object_id=account.id,
                external_id=account.external_id,
                label=account.name,
                score=score,
                source="keyword_rule",
                evidence={
                    "rule": rule["key"],
                    "matched_keywords": matched_keywords,
                    "account_name_score": str(account_name_score),
                },
            )
        )

    return candidates

def _qbo_account_name_candidates(expense: Expense, evidence: dict) -> list[dict]:
    """Weak fallback: match receipt text directly to QBO account names.

    Why weak:
        A receipt saying "drill bits" and an account called "Tools" may not be
        a high fuzzy score. This rule is useful as supporting evidence, but
        MappingRule and explicit keywords should usually win.
    """
    candidates: list[dict] = []
    text = evidence["account_text"]

    for account in _active_expense_accounts(expense.company):
        score = _similarity(text, account.name)
        if score < Decimal("0.65"):
            continue

        candidates.append(
            _candidate(
                object_type="qbo_expense_account",
                object_id=account.id,
                external_id=account.external_id,
                label=account.name,
                score=min(score, Decimal("0.78")),
                source="qbo_account_name_similarity",
                evidence={
                    "account_name": account.name,
                    "text_used": text[:300],
                },
            )
        )

    return candidates


def _recommend_expense_account(expense: Expense, evidence: dict) -> dict:
    candidates = []
    candidates.extend(_mapping_rule_account_candidates(expense, evidence))
    candidates.extend(_keyword_account_candidates(expense, evidence))
    candidates.extend(_qbo_account_name_candidates(expense, evidence))

    return _recommendation_block(
        kind="expense_account",
        candidates=candidates,
        evidence={
            "text_used": evidence["account_text"],
            "line_item_text": evidence["line_item_text"],
        },
        unresolved_reason=(
            "No approved mapping rule, receipt keyword rule, or QuickBooks "
            "account-name match produced a defensible expense account."
        ),
    )