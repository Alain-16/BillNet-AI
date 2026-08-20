from operations.categorization.client import complete_json
from operations.categorization.schemas import UNDERSTANDING_SCHEMA
from django.conf import settings
import json



_UNDERSTANDING_SYSTEM = """

You interpret purchase receipts for a trade contractor's bookkeeper.

For each line item, say what the item IS in business terms — whether it is a
reusable tool, a consumable material used up on a job, personal protective
equipment, storage, a service, a fee, or a deposit.

Rules:
- Do NOT name or suggest an accounting account. That is a later step.
- Do NOT invent items that are not in the list.
- Return one entry for every line item you are given, with its source_line unchanged.
- If an item is unclear, say so plainly rather than guessing a purpose.

"""

def understand(expense) -> tuple[dict, dict]:

    items = [
        {
            "source_line": item.get("line"),
            "description": item.get("description", ""),
            "amount": item.get("amount", "")
        }
        for item in (expense.line_items or [])
    ]

    payload = {
        "vendor_raw_name": expense.vendor_raw_name,
        "transaction_date": str(expense.transaction_date or ""),
        "currency": expense.currency,
        "total": str(expense.total or ""),
        "line_items": items,
    }

    return complete_json(
        model= settings.OPENAI_UNDERSTANDING_MODEL,
        system=_UNDERSTANDING_SYSTEM,
        user=json.dumps(payload, indent=2),
        schema=UNDERSTANDING_SCHEMA,
    )

