from __future__ import annotations

import re
from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounting.models import AccountingReference
from common.enums import (
    AccountingRefType, AuditActorType, AuditEventType, ProjectStatus,
)
from common.errors import DomainError
from expenses.models import Project
from operations.services import record_event


# `code` is deliberately absent -- immutable after creation (see header note).
EDITABLE_PROJECT_FIELDS = {"name", "aliases", "active_from", "active_to", "status"}

_WHITESPACE = re.compile(r"\s+")


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