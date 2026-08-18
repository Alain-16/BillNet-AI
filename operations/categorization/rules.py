from django.utils import timezone
from common.enums import MappingScope
from expenses.models import MappingRule


def _matches(conditions: dict, *, expense, line=None) -> tuple[bool, dict]:
    """Every present key must match. An empty conditions dict never matches --
    a rule with no conditions would silently capture every line on every
    receipt, which is the worst failure available to an override.
    """
    if not conditions:
        return False, {}

    evidence = {}
    vendor_raw = (expense.vendor_raw_name or "").upper()

    if "vendor_raw_contains" in conditions:
        needle = str(conditions["vendor_raw_contains"]).upper()
        if needle not in vendor_raw:
            return False, {}
        evidence["vendor_raw_contains"] = conditions["vendor_raw_contains"]

    if "vendor_id" in conditions:
        if str(expense.vendor_id or "") != str(conditions["vendor_id"]):
            return False, {}
        evidence["vendor_id"] = conditions["vendor_id"]

    if "line_description_contains" in conditions:
        if line is None:
            return False, {}
        needle = str(conditions["line_description_contains"]).upper()
        if needle not in str(line.get("description", "")).upper():
            return False, {}
        evidence["line_description_contains"] = conditions["line_description_contains"]

    return True, evidence


def line_account_override(expense, line) -> dict | None:
    """The highest-priority active rule matching this line, or None.

    A hit means the LLM is never asked about this line -- that is the guarantee
    the reviewer was promised when they saved the rule, and it is one database
    query instead of two API calls.
    """
    now = timezone.now()
    rules = MappingRule.objects.filter(
        company=expense.company, active=True,
        scope__in=[MappingScope.LINE_CATEGORY, MappingScope.CATEGORY],
    ).order_by("priority")

    for rule in rules:
        if rule.effective_from and rule.effective_from > now:
            continue
        if rule.effective_to and rule.effective_to < now:
            continue

        matched, evidence = _matches(rule.conditions or {}, expense=expense, line=line)
        if not matched:
            continue

        outputs = rule.outputs or {}
        if not outputs.get("external_id"):
            continue                  # malformed rule -- ignore it, do not crash

        return {
            "entity_type": outputs.get("entity_type", "ACCOUNT"),
            "external_id": str(outputs["external_id"]),
            "rule_id": str(rule.id),
            "rule_name": rule.name,
            "evidence": evidence,
        }
    return None