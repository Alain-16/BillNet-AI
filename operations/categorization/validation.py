from accounting.models import AccountingReference


_EXPENSE_ACCOUNT_TYPES = {"Expense", "Cost of Goods Sold", "Other Expense", "Fixed Asset"}

def verify_reference(*, company,external_id:str, entity_type: str, require_expense_usable:bool=False):

    if not external_id:
        return None, "no_selection"

    reference = AccountingReference.objects.filter(
        company=company, entity_type=entity_type, external_id=str(external_id),
    ).first()

    if reference is None:
        return None, "reference_not_found"
    if not reference.active:
        return None, "reference_inactive"

    if require_expense_usable:
        data = reference.data or {}
        if not data.get("usable_as_expense_account") and \
                data.get("account_type") not in _EXPENSE_ACCOUNT_TYPES:
            return None, "not_usable_as_expense_account"

    return reference, ""