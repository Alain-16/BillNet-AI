import hashlib
from accounting.models import AccountingReference, ReferenceEmbedding
from categorization.client import embed
from common.enums import AccountingRefType
from django.conf import settings

def build_reference_text(reference: AccountingReference) -> str:
    """Doc 22: do NOT embed the raw JSON. Build a compact description.

    The account TYPE and SUBTYPE matter more than they look. "Small Tools" and
    "Plumbing Materials" are similar strings; "Cost of Goods Sold /
    SuppliesMaterialsCogs" versus "Expense / Equipment" is what actually
    separates a consumable from a tool.
    """
    data = reference.data or {}

    if reference.entity_type in (AccountingRefType.ACCOUNT,
                                 AccountingRefType.PAYMENT_ACCOUNT):
        parts = [
            "Entity type: Account",
            f"Name: {reference.name}",
            f"Full name: {data.get('fully_qualified_name', '') or reference.name}",
            f"Account type: {data.get('account_type', '')}",
            f"Account subtype: {data.get('account_subtype', '')}",
            f"Classification: {data.get('classification', '')}",
        ]
    elif reference.entity_type == AccountingRefType.VENDOR:
        parts = [
            "Entity type: Vendor",
            f"Name: {reference.name}",
            f"Company name: {data.get('company_name', '')}",
        ]
    elif reference.entity_type == AccountingRefType.CUSTOMER:
        parts = [
            "Entity type: Project" if data.get("is_project") else "Entity type: Customer",
            f"Name: {reference.name}",
            f"Full name: {data.get('fully_qualified_name', '') or reference.name}",
            f"Customer: {data.get('parent_name', '')}",
            f"Status: {'Active' if reference.active else 'Inactive'}",
        ]
    else:
        parts = [f"Entity type: {reference.entity_type}", f"Name: {reference.name}"]

    return "\n".join(p for p in parts if not p.endswith(": "))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


EMBEDDABLE_TYPES = (
    AccountingRefType.ACCOUNT,
    AccountingRefType.VENDOR,
    AccountingRefType.CUSTOMER,
    AccountingRefType.PAYMENT_ACCOUNT,
)


def refresh_embeddings(company, *, batch_size: int = 100) -> dict:
    """Embed every reference whose searchable text changed. Idempotent.

    Call after sync_references. Re-running costs nothing when nothing changed --
    that is what content_hash buys.
    """
    references = list(
        AccountingReference.objects
        .filter(company=company, entity_type__in=EMBEDDABLE_TYPES, active=True)
        .select_related("embedding_row")
    )

    stale = []
    for reference in references:
        text = build_reference_text(reference)
        row = getattr(reference, "embedding_row", None)
        if (row and row.content_hash == _hash(text)
                and row.model == settings.OPENAI_EMBEDDING_MODEL
                and row.embedding is not None):
            continue
        stale.append((reference, text))

    created = updated = 0
    for start in range(0, len(stale), batch_size):
        chunk = stale[start:start + batch_size]
        vectors = embed([text for _, text in chunk])
        for (reference, text), vector in zip(chunk, vectors):
            _, was_created = ReferenceEmbedding.objects.update_or_create(
                reference=reference,
                defaults={"company": reference.company, "content": text,
                          "content_hash": _hash(text),
                          "model": settings.OPENAI_EMBEDDING_MODEL,
                          "embedding": vector},
            )
            created += bool(was_created)
            updated += (not was_created)

    return {"checked": len(references), "created": created, "updated": updated}