from categorization.schemas import RESOLUTION_SCHEMA
from categorization.client import complete_json
from django.conf import settings
import json

_RESOLUTION_SYSTEM = """

You will be given a receipt, the business meaning of each line item, and a list
of candidate QuickBooks references belonging to THIS company.

Rules you must follow:
1. You may ONLY choose an external_id that appears in the candidates given to
   you. Never invent one. Never use an id you remember from elsewhere.
2. If no candidate is a defensible fit, return "" for that field. Returning ""
   is a correct answer, not a failure.
3. For projects: return "" unless the receipt itself carries project evidence —
   a job number, a site address, a customer name, a purchase order. Do NOT pick
   a project merely because one exists.
4. Distinguish reusable equipment from consumable materials. A pipe wrench is a
   tool; PVC cement is a material. They usually belong to different accounts.
5. Each line item is decided on its own. One receipt may use several accounts.

"""


def resolve(*, expense, understanding, vendor_candidates,project_candidates,line_candidates: dict) -> tuple[dict, dict]:

    meaning_by_line = {item["source_line"]: item["meaning"]
                       for item in understanding.get("line_items",[])}
    desc_by_line = {
        item["source_line"]: item["description"]

        for item in understanding.get("line_items", [])
    }

    payload  = {
        "receipt": {
            "vendor_raw_name": expense.vendor_raw_name,
            "transaction_date": str(expense.transaction_date or ""),
            "total": str(expense.total or ""),
            "currency": expense.currency,
            "purchase_summary": understanding.get("purchase_summary", ""),
            "purchase_domain": understanding.get("purchase_domain", ""),  
        },
        "vendor_candidates": vendor_candidates,
        "project_candidates": project_candidates,
        "line_items": [
            {
                "source_line": line,
                "description": desc_by_line.get(line, ""),
                "business_meaning": meaning_by_line.get(line, ""),
                "account_candidates": candidates,
            }
            for line, candidates in line_candidates.items()
        ],
    }

    return complete_json(
        model=settings.OPENAI_REASONING_MODEL,
        system=_RESOLUTION_SYSTEM,
        user=json.dumps(payload,indent=2),
        schema=RESOLUTION_SCHEMA,

    )