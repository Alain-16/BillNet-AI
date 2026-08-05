from django.db import transaction
from django.utils import timezone

from accounts.models import IntegrationConnection
from accounts.providers import get_provider
from accounts.services import get_valid_access_token
from accounting.models import AccountingReference
from common.enums import (
    AccountingRefType, AuditEventType, ConnectionStatus, Provider,
)
from operations.services import record_event


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