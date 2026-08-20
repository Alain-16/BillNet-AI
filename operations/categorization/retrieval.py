from pgvector.django import CosineDistance
from django.conf import settings
from accounting.models import ReferenceEmbedding


def search_references(*, company, entity_types, query_vector, top_k=None,
                      connection=None, usable_expense_only=False) -> list[dict]:
    """Doc 23: company scope FIRST, similarity LAST.

    The filters are not a performance detail -- they are the tenant boundary.
    Company A's chart of accounts must never surface as a candidate for
    Company B, and no similarity score may override that.
    """
    top_k = top_k or settings.CATEGORIZATION_TOP_K

    queryset = (ReferenceEmbedding.objects
                .filter(company=company,
                        reference__entity_type__in=entity_types,
                        reference__active=True,
                        embedding__isnull=False)
                .select_related("reference"))

    if usable_expense_only:
        queryset = queryset.filter(reference__data__usable_as_expense_account=True)

    if connection is not None:
        queryset = queryset.filter(reference__connection=connection)

    rows = (queryset
            .annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance")[:top_k])

    return [
        {
            "external_id": row.reference.external_id,
            "name": row.reference.name,
            "entity_type": row.reference.entity_type,
            "fully_qualified_name": (row.reference.data or {}).get(
                "fully_qualified_name", "") or row.reference.name,
            "account_type": (row.reference.data or {}).get("account_type", ""),
            "account_subtype": (row.reference.data or {}).get("account_subtype", ""),
            "similarity": round(1.0 - float(row.distance), 4),
        }
        for row in rows
    ]


def usable_expense_accounts(candidates: list[dict]) -> list[dict]:
    """Drop accounts that cannot legally receive an expense line, before the
    model ever sees them. Cheaper than rejecting the answer afterwards, and it
    removes a whole class of mistake the model could otherwise make."""
    return [c for c in candidates
            if c.get("account_type") in {"Expense", "Cost of Goods Sold",
                                         "Other Expense", "Fixed Asset"}]