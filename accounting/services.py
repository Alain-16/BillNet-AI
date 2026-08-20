from django.db import transaction
from django.utils import timezone
import secrets
import uuid
from datetime import date as _date
from decimal import Decimal
from datetime import timedelta

from accounts.models import IntegrationConnection
from accounts.providers import get_provider
from accounts.services import get_valid_access_token
from accounting.models import AccountingReference, PostingIntent
from common.enums import (
    AccountingRefType, AuditEventType, ConnectionStatus, Provider,ExpenseState,
    PostingStatus, TransactionPurpose,AuditActorType
)
from operations.services import record_event
from common.errors import DomainError
from expenses.state_machine import transition
from operations.categorization.validation import verify_reference
from accounting.purchase_payload import PAYMENT_TYPE_MAP,distribute_tax
from django.conf import settings
from accounts.providers import get_provider
from accounts.services import get_valid_access_token
from accounts.models import IntegrationConnection
from accounts.providers import get_provider
from accounts.services import get_valid_access_token
from common.storage import get_object_storage
from expenses.tasks import attach_receipt_task
from accounts.models import IntegrationConnection
from accounts.providers import get_provider
from accounts.services import get_valid_access_token
from accounting.purchase_payload import build_purchase
import requests

DUPLICATE_DATE_WINDOW_DAYS = 5
DUPLICATE_AMOUNT_TOLERANCE = Decimal("0.02")

@transaction.atomic
def sync_references(conn: IntegrationConnection) -> dict:
    """Pull QBO reference data and upsert AccountingReference rows. Idempotent:
    keyed on (company, entity_type, external_id) so re-running only updates."""
    if conn.provider != Provider.QUICKBOOKS:
        raise ValueError("Reference sync is QuickBooks-only")

    provider = get_provider(conn.provider)
    try:
        access = get_valid_access_token(conn)
        refs = provider.fetch_references(access, conn.external_account_id)
    except Exception as exc:
        conn.status = ConnectionStatus.SYNC_FAILED
        conn.last_error = {"stage": "reference_sync", "detail": str(exc)[:500]}
        conn.save(update_fields=["status", "last_error", "updated_at"])
        raise

    counts: dict[str, int] = {}
    for r in refs:
        AccountingReference.objects.update_or_create(
            company=conn.company, entity_type=r.entity_type, external_id=r.external_id,
            defaults={"name": r.name, "active": r.active, "sync_token": r.sync_token,
                      "qbo_updated_at": r.updated_at, "connection": conn, "data": r.data},
        )
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1

    _link_tax_codes(conn)          # wire company.tax_codes -> QBO tax code ids
    conn.status = ConnectionStatus.CONNECTED
    conn.last_sync_at = timezone.now()
    conn.last_error = {}
    conn.save(update_fields=["status", "last_sync_at", "last_error", "updated_at"])

    record_event(company=conn.company, event_type=AuditEventType.REFERENCE_SYNCED,
                 aggregate_type="IntegrationConnection", aggregate_id=conn.id,
                 payload={"counts": counts})
    return counts


def _link_tax_codes(conn: IntegrationConnection) -> None:
    """Fill each company.tax_codes entry's qbo_tax_code_id by matching label to a
    synced TAX_CODE reference (e.g. 'GST' -> the QBO GST tax code Id)."""
    company = conn.company
    tax_refs = AccountingReference.objects.filter(
        company=company, entity_type=AccountingRefType.TAX_CODE, active=True,
    )
    by_upper_name = {ref.name.upper(): ref.external_id for ref in tax_refs}
    changed = False
    for tc in company.tax_codes:
        if tc.get("qbo_tax_code_id"):
            continue
        for name_upper, ext_id in by_upper_name.items():
            if tc["label"].upper() in name_upper:   # 'GST' in 'GST/HST'
                tc["qbo_tax_code_id"] = ext_id
                changed = True
                break
    if changed:
        company.save(update_fields=["tax_codes", "updated_at"])



def _ref(company, external_id, entity_type, *, label, required=True,
         expense_usable=False):
 
    if not external_id:
        if required:
            raise DomainError(f"{label} is required to post.",
                              code=f"{label.lower().replace(' ', '_')}_required")
        return None
    reference, reason = verify_reference(
        company=company, external_id=str(external_id), entity_type=entity_type,
        require_expense_usable=expense_usable)
    if reference is None:
        raise DomainError(f"{label} '{external_id}' is not usable: {reason}.",
                          code="reference_invalid", field=label)
    return {"external_id": reference.external_id, "name": reference.name,
            "_data": reference.data or {}}


@transaction.atomic
def approve_for_posting(*, expense, actor, approval: dict) -> PostingIntent:
   
    if approval.get("version") is not None and approval["version"] != expense.version:
        raise DomainError("This expense changed since you loaded it. Reload.",
                          code="version_conflict", field="version")


    already = PostingIntent.objects.filter(
        expense=expense, qbo_entity_id__gt="").first()
    if already:
        raise DomainError(
            f"This expense is already posted as QuickBooks Purchase "
            f"{already.qbo_entity_id}.", code="already_posted")

    company = expense.company

    # 2. Payment type and account must AGREE, or QuickBooks rejects the create.
    payment_type = approval.get("payment_type") or expense.payment_type
    if payment_type not in PAYMENT_TYPE_MAP:
        raise DomainError(
            "Payment type must be CREDIT_CARD or BANK before posting.",
            code="payment_type_required", field="payment_type")
    qbo_payment_type, allowed_account_types = PAYMENT_TYPE_MAP[payment_type]

    account = _ref(company, approval.get("payment_account_external_id"),
                   AccountingRefType.PAYMENT_ACCOUNT, label="Payment account")
    account_type = account["_data"].get("account_type")
    if account_type not in allowed_account_types:
        raise DomainError(
            f"A {qbo_payment_type} purchase needs a "
            f"{'/'.join(sorted(allowed_account_types))} account, but "
            f"'{account['name']}' is a {account_type}.",
            code="payment_account_type_mismatch", field="payment_account")

    vendor = _ref(company, approval.get("vendor_external_id"),
                  AccountingRefType.VENDOR, label="Vendor", required=False)
    project = _ref(company, approval.get("project_external_id"),
                   AccountingRefType.CUSTOMER, label="Project", required=False)

    # 3. Every line the reviewer confirmed, matched back to the extracted line.
    by_source_line = {item.get("line"): item for item in (expense.line_items or [])}
    approved_lines = approval.get("lines") or []
    if not approved_lines:
        raise DomainError("At least one line must be approved.",
                          code="no_approved_lines")

    missing = set(by_source_line) - {l["source_line"] for l in approved_lines}
    if missing:
        # EVERY line must be accounted for. A silently dropped line means the
        # posted total cannot equal the receipt total, and the bank match fails.
        raise DomainError(
            f"Lines {sorted(missing)} were not approved. Every line must have "
            f"an account before posting.", code="lines_unapproved")

    lines = []
    for approved in approved_lines:
        source = by_source_line.get(approved["source_line"])
        if source is None:
            raise DomainError(f"Unknown line {approved['source_line']}.",
                              code="unknown_line")
        acct = _ref(company, approved.get("account_external_id"),
                    AccountingRefType.ACCOUNT, label="Expense account",
                    expense_usable=True)
        lines.append({
            "source_line": approved["source_line"],
            "description": source.get("description", ""),
            "net": str(Decimal(str(source["amount"]))),
            "account": {"external_id": acct["external_id"], "name": acct["name"]},
        })

    # 4. Money. Option A: tax folded into the lines so they sum to the total.
    subtotal = expense.subtotal or sum(Decimal(l["net"]) for l in lines)
    total = expense.total
    if total is None:
        raise DomainError("Total is required to post.", code="total_required")
    lines = distribute_tax(lines, Decimal(subtotal), Decimal(total))

    # 5. Freeze it.
    doc_number = f"BN-{secrets.token_hex(5).upper()}"    # readback anchor
    snapshot = {
        "payment_type": qbo_payment_type,
        "payment_account": {"external_id": account["external_id"],
                            "name": account["name"]},
        "vendor": {"external_id": vendor["external_id"], "name": vendor["name"]} if vendor else None,
        "project": {"external_id": project["external_id"], "name": project["name"]} if project else None,
        "transaction_date": expense.transaction_date.isoformat(),
        "currency": expense.currency,
        "subtotal": str(subtotal), "tax_total": str(Decimal(total) - Decimal(subtotal)),
        "total": str(total),
        "doc_number": doc_number,
        "memo": (approval.get("memo") or expense.memo or "")[:4000],
        "lines": lines,
        "tax_treatment": "FOLDED_INTO_LINES",   # honest label -- see SECTION 13
        "approved_by": str(actor.id),
        "approved_at": timezone.now().isoformat(),
    }

    intent = PostingIntent.objects.create(
        company=company, expense=expense, expense_version=expense.version,
        transaction_purpose=TransactionPurpose.PURCHASE,
        idempotency_key=(f"organization:{company.id}:expense:{expense.id}"
                         f":create-purchase:v1"),
        request_id=uuid.uuid4(),
        doc_number_token=doc_number,
        minor_version=str(settings.QBO_MINOR_VERSION),
        status=PostingStatus.PENDING,
        approved_payload=snapshot,
    )
 

    expense = transition(expense=expense, to_state=ExpenseState.APPROVED,
                         actor=actor, reason="approved for posting")

    record_event(company=company, event_type=AuditEventType.APPROVAL,
                 aggregate_type="Expense", aggregate_id=expense.id,
                 actor_type=AuditActorType.USER, actor_id=actor.id,
                 object_version=expense.version,
                 payload={"intent_id": str(intent.id), "doc_number": doc_number,
                          "total": str(total), "line_count": len(lines),
                          "accounts": [l["account"]["external_id"] for l in lines]})
    return intent


def find_possible_duplicate(*, conn, snapshot: dict) -> dict | None:
 
    txn_date = _date.fromisoformat(snapshot["transaction_date"])
    provider = get_provider(conn.provider)
    access = get_valid_access_token(conn)

    rows = provider.find_recent_purchases(
        access, conn.external_account_id,
        date_from=txn_date - timedelta(days=DUPLICATE_DATE_WINDOW_DAYS),
        date_to=txn_date + timedelta(days=DUPLICATE_DATE_WINDOW_DAYS),
    )

    target_total = Decimal(snapshot["total"])
    payment_account_id = snapshot["payment_account"]["external_id"]
    vendor_id = (snapshot.get("vendor") or {}).get("external_id")

    for row in rows:
        amount = Decimal(str(row.get("TotalAmt") or "0"))
        if (amount - target_total).copy_abs() > DUPLICATE_AMOUNT_TOLERANCE:
            continue

        signals = ["amount"]
        if (row.get("AccountRef") or {}).get("value") == payment_account_id:
            signals.append("payment_account")
        if vendor_id and (row.get("EntityRef") or {}).get("value") == vendor_id:
            signals.append("vendor")
        if row.get("TxnDate") == snapshot["transaction_date"]:
            signals.append("exact_date")

        # Amount alone is not enough to BLOCK -- a contractor can legitimately
        # buy the same basket twice. Requiring a second independent signal is
        # workflow doc 13.2's "several independent signals", applied here as the
        # threshold for stopping a human's approved action.
        if len(signals) >= 2:
            return {
                "qbo_purchase_id": str(row.get("Id")),
                "txn_date": row.get("TxnDate"),
                "total": str(amount),
                "vendor_name": (row.get("EntityRef") or {}).get("name", ""),
                "matched_on": signals,
            }
    return None



@transaction.atomic
def _claim(intent_id: str, worker: str) -> PostingIntent | None:
    
    intent = (PostingIntent.objects.select_for_update(skip_locked=True)
              .filter(pk=intent_id, status=PostingStatus.PENDING).first())
    if intent is None:
        return None
    intent.status = PostingStatus.CLAIMED
    intent.claimed_at = timezone.now()
    intent.claimed_by = worker
    intent.attempt_count += 1
    intent.save(update_fields=["status", "claimed_at", "claimed_by",
                               "attempt_count", "updated_at"])
    return intent


def post_purchase(*, intent_id: str, worker: str = "celery") -> dict:


    

    intent = _claim(intent_id, worker)
    if intent is None:
        return {"skipped": "not_claimable"}

    expense = intent.expense
    snapshot = intent.approved_payload

    # --- layer 1 -------------------------------------------------------------
    posted = (PostingIntent.objects
              .filter(expense=expense, qbo_entity_id__gt="")
              .exclude(pk=intent.pk).first())
    if posted:
        return _finish_failed(intent, expense,
                              {"code": "already_posted",
                               "qbo_entity_id": posted.qbo_entity_id},
                              retryable=False)

    expense = transition(expense=expense,
                         to_state=ExpenseState.POSTING_IN_PROGRESS,
                         reason="posting started")
    intent.status = PostingStatus.IN_PROGRESS
    intent.save(update_fields=["status", "updated_at"])

    conn = IntegrationConnection.objects.get(
        company=expense.company, provider=Provider.QUICKBOOKS)

    # --- layer 3: ask QuickBooks before creating anything ---------------------
    duplicate = find_possible_duplicate(conn=conn, snapshot=snapshot)
    if duplicate:
        # Do NOT create, and do NOT silently link either -- doc 7 Path A says a
        # reviewer confirms the link. Park it and surface the candidate.
        intent.status = PostingStatus.FAILED
        intent.last_error = {"code": "possible_duplicate", "retryable": False,
                             "candidate": duplicate}
        intent.save(update_fields=["status", "last_error", "updated_at"])
        transition(expense=expense, to_state=ExpenseState.REVIEW_REQUIRED,
                   reason="possible existing purchase found")
        record_event(company=expense.company, event_type=AuditEventType.EXCEPTION,
                     aggregate_type="Expense", aggregate_id=expense.id,
                     actor_type=AuditActorType.SYSTEM,
                     payload={"reason": "possible_duplicate", **duplicate})
        return {"blocked": "possible_duplicate", "candidate": duplicate}

    provider = get_provider(conn.provider)
    access = get_valid_access_token(conn)
    body = build_purchase(snapshot)

    # --- layers 4 and 5 -------------------------------------------------------
    try:
        created = provider.create_purchase(
            access, conn.external_account_id, body,
            request_id=str(intent.request_id))
    except requests.Timeout:
        # THE DANGEROUS CASE. We do not know whether QuickBooks created it.
        # NEVER retry a create here -- ask instead.
        return _resolve_unknown(intent, expense, provider, access, conn)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        retryable = status_code in (429, 500, 502, 503, 504)
        return _finish_failed(intent, expense,
                              {"code": f"http_{status_code}",
                               "detail": (exc.response.text[:500]
                                          if exc.response is not None else str(exc))},
                              retryable=retryable)

    return _finish_posted(intent, expense, created)


def _resolve_unknown(intent, expense, provider, access, conn):
    """A timed-out create, resolved by reading back the DocNumber."""
    intent.status = PostingStatus.UNKNOWN
    intent.save(update_fields=["status", "updated_at"])
    transition(expense=expense, to_state=ExpenseState.POSTING_UNKNOWN,
               reason="create timed out")

    found = provider.find_purchase_by_doc_number(
        access, conn.external_account_id, intent.doc_number_token)
    if found:
        # It landed. Adopt it -- creating another would be the duplicate the
        # whole design exists to prevent.
        return _finish_posted(intent, expense, found)

    # It did not land. Safe to queue another attempt: the DocNumber is unique,
    # so the readback stays meaningful next time round.
    intent.status = PostingStatus.PENDING
    intent.last_error = {"code": "timeout_not_found", "retryable": True}
    intent.save(update_fields=["status", "last_error", "updated_at"])
    transition(expense=expense, to_state=ExpenseState.POSTING_PENDING,
               reason="timeout, purchase not found on readback")
    return {"unknown": "requeued"}


def _finish_posted(intent, expense, created: dict) -> dict:
    intent.status = PostingStatus.POSTED
    intent.qbo_entity_type = "Purchase"
    intent.qbo_entity_id = str(created.get("Id", ""))
    intent.qbo_sync_token = str(created.get("SyncToken", ""))
    intent.posted_at = timezone.now()
    intent.last_error = {}
    intent.response_summary = {           # safe metadata only, never the whole body
        "Id": created.get("Id"), "TotalAmt": created.get("TotalAmt"),
        "TxnDate": created.get("TxnDate"), "DocNumber": created.get("DocNumber"),
    }
    intent.save(update_fields=["status", "qbo_entity_type", "qbo_entity_id",
                               "qbo_sync_token", "posted_at", "last_error",
                               "response_summary", "updated_at"])

    expense = transition(expense=expense, to_state=ExpenseState.POSTED,
                         reason=f"created QBO Purchase {intent.qbo_entity_id}")
    # POSTED is not the end. The user still has to match the bank row.
    expense = transition(expense=expense,
                         to_state=ExpenseState.AWAITING_BANK_MATCH,
                         reason="waiting for the user to match the bank feed")

    record_event(company=expense.company, event_type=AuditEventType.POSTING_RESULT,
                 aggregate_type="Expense", aggregate_id=expense.id,
                 actor_type=AuditActorType.SYSTEM, object_version=expense.version,
                 payload={"qbo_purchase_id": intent.qbo_entity_id,
                          "doc_number": intent.doc_number_token,
                          "attempt": intent.attempt_count})

    # Attachment is a SEPARATE queued step. Rule 13: an attachment failure must
    # not undo a successful posting.
    
    attach_receipt_task.delay(str(intent.id))
    return {"posted": intent.qbo_entity_id}


def _finish_failed(intent, expense, error: dict, *, retryable: bool) -> dict:
    intent.status = PostingStatus.PENDING if retryable else PostingStatus.FAILED
    intent.last_error = {**error, "retryable": retryable}
    intent.save(update_fields=["status", "last_error", "updated_at"])
    transition(expense=expense,
               to_state=(ExpenseState.POSTING_PENDING if retryable
                         else ExpenseState.POSTING_FAILED),
               reason=error.get("code", "posting failed"))
    return {"failed": error.get("code"), "retryable": retryable}


def attach_receipt(*, intent_id: str) -> dict:
   
    intent = PostingIntent.objects.get(pk=intent_id)
    if not intent.qbo_entity_id:
        return {"skipped": "not_posted"}
    if intent.response_summary.get("attachment_id"):
        return {"skipped": "already_attached"}      # idempotent

    document = intent.expense.source_document
    conn = IntegrationConnection.objects.get(
        company=intent.company, provider=Provider.QUICKBOOKS)
    provider = get_provider(conn.provider)

    result = provider.attach_file(
        get_valid_access_token(conn), conn.external_account_id,
        entity_type="Purchase", entity_id=intent.qbo_entity_id,
        filename=document.filename or "receipt.pdf",
        content_type=document.mime_type or "application/pdf",
        data=get_object_storage().get(document.object_key),
    )

    attachable = (result.get("AttachableResponse") or [{}])[0].get("Attachable", {})
    intent.response_summary = {**intent.response_summary,
                               "attachment_id": attachable.get("Id", "")}
    intent.save(update_fields=["response_summary", "updated_at"])
    return {"attached": attachable.get("Id", "")}