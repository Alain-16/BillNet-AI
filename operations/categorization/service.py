from django.db import transaction
from categorization.client import AIUnavailable
from categorization.validation import verify_reference
from categorization import reasoning, retrieval, rules
from categorization import understanding as understanding_mod
from common.enums import (
    AccountingRefType, AuditActorType,AuditEventType,CategorizationStatus,DecisionSource,DecisionStatus,
)
from expenses.validators import blocking_errors
from expenses.services import record_event
from django.conf import settings
from datetime import timezone
from categorization.client import embed

CATEGORIZATION_SCHEMA_VERSION = "categorization.v1"

def _decision(*, status, source, external_id="", name="", **extra) -> dict:
    return {"status": status, "source": source,
            "external_id": external_id or None, "name": name or None, **extra}


def _unresolved(reason: str) -> dict:
    return _decision(status=DecisionStatus.UNRESOLVED, source=DecisionSource.AI,
                     reason=reason)


@transaction.atomic
def categorize(*, expense, actor=None):
    """Run the full pipeline for one expense and store the result.

    Idempotent: re-running replaces the block. Never raises for an AI problem --
    a failure is recorded as FAILED and the expense still reaches a human with
    its extracted fields intact.
    """
    if not settings.CATEGORIZATION_ENABLED:
        return _store(expense, {"status": CategorizationStatus.SKIPPED,
                                "reason": "categorization_disabled"}, actor)

    # Doc 5 puts categorization after validation, and there is a hard-nosed
    # reason to honour that: an expense with a missing total cannot be approved
    # no matter how well it is categorized. Two LLM calls on it is money spent
    # to reach the same review queue.
    if blocking_errors(_as_checks(expense.validations)):
        return _store(expense, {"status": CategorizationStatus.SKIPPED,
                                "reason": "validation_errors_present"}, actor)

    if not (expense.line_items or []):
        return _store(expense, {"status": CategorizationStatus.SKIPPED,
                                "reason": "no_line_items"}, actor)

    telemetry = []
    try:
        # ---- [2] understanding --------------------------------------------
        understanding, tel = understanding_mod.understand(expense)
        telemetry.append({"stage": "understanding", **tel})

        # ---- [1] rules first, per line -------------------------------------
        # Before embedding, so a fully rule-covered receipt costs one LLM call
        # instead of three round trips.
        rule_hits, open_lines = {}, []
        for item in expense.line_items or []:
            hit = rules.line_account_override(expense, item)
            if hit:
                rule_hits[item.get("line")] = hit
            else:
                open_lines.append(item)

        # ---- [3] retrieval --------------------------------------------------
        meaning_by_line = {i["source_line"]: i["meaning"]
                           for i in understanding.get("line_items", [])}

        line_candidates = {}
        if open_lines:
            queries = [meaning_by_line.get(item.get("line"), "")
                       or item.get("description", "") for item in open_lines]
            vectors = embed(queries)
            for item, vector in zip(open_lines, vectors):
                found = retrieval.search_references(
                    company=expense.company,
                    entity_types=[AccountingRefType.ACCOUNT],
                    query_vector=vector,
                )
                line_candidates[item.get("line")] = retrieval.usable_expense_accounts(found)

        vendor_candidates = []
        if expense.vendor_raw_name:
            vendor_candidates = retrieval.search_references(
                company=expense.company, entity_types=[AccountingRefType.VENDOR],
                query_vector=embed([expense.vendor_raw_name])[0])

        # Doc 11 and 24: retrieve projects only when the receipt gives us
        # something to search WITH. Searching on the item list alone returns the
        # nearest project name and invites the model to pick it.
        project_evidence = _project_evidence(expense)
        project_candidates = []
        if project_evidence:
            project_candidates = retrieval.search_references(
                company=expense.company, entity_types=[AccountingRefType.CUSTOMER],
                query_vector=embed([project_evidence])[0])

        # ---- [4] reasoning ---------------------------------------------------
        if line_candidates or vendor_candidates or project_candidates:
            proposal, tel = reasoning.resolve(
                expense=expense, understanding=understanding,
                vendor_candidates=vendor_candidates,
                project_candidates=project_candidates,
                line_candidates=line_candidates,
            )
            telemetry.append({"stage": "reasoning", **tel})
        else:
            proposal = {"vendor_external_id": "", "project_external_id": "",
                        "line_items": []}

    except AIUnavailable as exc:
        return _store(expense, {"status": CategorizationStatus.FAILED,
                                "reason": str(exc)[:300]}, actor)

    block = _assemble(expense, understanding, proposal, rule_hits, telemetry)
    return _store(expense, block, actor)


def _project_evidence(expense) -> str:
    """Project resolution needs evidence about the JOB, not about the items
    (doc 24). PVC cement tells you the account; it tells you nothing about which
    bathroom it was for."""
    parts = [expense.memo or "", expense.receipt_number or ""]
    message = getattr(expense.source_document, "message", None)
    if message is not None:
        parts.append(getattr(message, "subject", "") or "")
    return " ".join(p for p in parts if p).strip()


def _assemble(expense, understanding, proposal, rule_hits, telemetry) -> dict:
    company = expense.company
    meaning_by_line = {i["source_line"]: i["meaning"]
                       for i in understanding.get("line_items", [])}
    ai_by_line = {i["source_line"]: i for i in proposal.get("line_items", [])}

    # --- vendor ---
    reference, reason = verify_reference(
        company=company, external_id=proposal.get("vendor_external_id", ""),
        entity_type=AccountingRefType.VENDOR)
    vendor = (_decision(status=DecisionStatus.SUGGESTED, source=DecisionSource.AI,
                        external_id=reference.external_id, name=reference.name)
              if reference else _unresolved(reason))

    # --- project ---
    reference, reason = verify_reference(
        company=company, external_id=proposal.get("project_external_id", ""),
        entity_type=AccountingRefType.CUSTOMER)
    project = (_decision(status=DecisionStatus.SUGGESTED, source=DecisionSource.AI,
                         external_id=reference.external_id, name=reference.name)
               if reference else _unresolved(reason))

    # --- line items ---
    lines, resolved_count = [], 0
    for item in expense.line_items or []:
        line = item.get("line")
        hit = rule_hits.get(line)

        if hit:
            # A rule fired -- verify it ANYWAY. A rule saved last year can point
            # at an account since deactivated in QuickBooks.
            reference, reason = verify_reference(
                company=company, external_id=hit["external_id"],
                entity_type=hit.get("entity_type", AccountingRefType.ACCOUNT),
                require_expense_usable=True)
            if reference:
                account = _decision(
                    status=DecisionStatus.MATCHED,
                    source=DecisionSource.MAPPING_RULE,   # <- surfaced, per your call
                    external_id=reference.external_id, name=reference.name,
                    rule_id=hit["rule_id"], rule_name=hit["rule_name"],
                    matched_on=hit["evidence"],
                )
                resolved_count += 1
            else:
                account = _decision(status=DecisionStatus.UNRESOLVED,
                                    source=DecisionSource.MAPPING_RULE,
                                    reason=f"rule_target_invalid:{reason}",
                                    rule_id=hit["rule_id"], rule_name=hit["rule_name"])
        else:
            decision = ai_by_line.get(line, {})
            reference, reason = verify_reference(
                company=company, external_id=decision.get("account_external_id", ""),
                entity_type=AccountingRefType.ACCOUNT, require_expense_usable=True)
            if reference:
                account = _decision(status=DecisionStatus.SUGGESTED,
                                    source=DecisionSource.AI,
                                    external_id=reference.external_id,
                                    name=reference.name,
                                    rationale=decision.get("rationale", ""))
                resolved_count += 1
            else:
                account = _decision(status=DecisionStatus.UNRESOLVED,
                                    source=DecisionSource.AI, reason=reason)

        lines.append({
            "source_line": line,
            "description": item.get("description", ""),
            "amount": item.get("amount", ""),
            "business_meaning": meaning_by_line.get(line, ""),
            "account": account,
        })

    total_lines = len(lines)
    if total_lines and resolved_count == total_lines:
        status = CategorizationStatus.COMPLETED
    elif resolved_count:
        status = CategorizationStatus.PARTIAL
    else:
        status = CategorizationStatus.FAILED

    return {
        "schema_version": CATEGORIZATION_SCHEMA_VERSION,
        "status": status,
        "generated_at": timezone.now().isoformat(),
        "purchase_summary": understanding.get("purchase_summary", ""),
        "purchase_domain": understanding.get("purchase_domain", ""),
        "vendor": vendor,
        "project": project,
        "line_items": lines,
        "telemetry": telemetry,
    }


def _as_checks(validations):
    """blocking_errors() expects Check objects; the stored form is dicts."""
    from expenses.validators import Check
    return [Check(**{k: v for k, v in row.items() if k in Check.__dataclass_fields__})
            for row in (validations or [])]


def _store(expense, block: dict, actor):
    expense.categorization = block
    expense.save(update_fields=["categorization", "updated_at"])

    record_event(
        company=expense.company, event_type=AuditEventType.CATEGORIZATION,
        aggregate_type="Expense", aggregate_id=expense.id,
        actor_type=AuditActorType.AI if actor is None else AuditActorType.USER,
        actor_id=getattr(actor, "id", ""), object_version=expense.version,
        payload={
            "status": block.get("status"),
            "reason": block.get("reason", ""),
            "rule_lines": [l["source_line"] for l in block.get("line_items", [])
                           if (l.get("account") or {}).get("source") == DecisionSource.MAPPING_RULE],
            "telemetry": block.get("telemetry", []),
        },
    )
    return expense