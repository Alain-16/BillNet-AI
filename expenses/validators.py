from __future__ import annotations
from dataclasses import dataclass, field as dc_field
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from common.enums import ExtractionStatus


class Severity:
    ERROR = "ERROR"       # blocks approval
    WARNING = "WARNING"   # needs a human look, does not block


@dataclass
class Check:
    code: str
    severity: str
    passed: bool
    message: str
    expected: str = ""
    actual: str = ""
    meta: dict = dc_field(default_factory=dict)

    def to_json(self) -> dict:
        return {"code": self.code, "severity": self.severity, "passed": self.passed,
                "message": self.message, "expected": self.expected,
                "actual": self.actual, "meta": self.meta}


def _tolerance() -> Decimal:
    return Decimal(str(getattr(settings, "MONEY_ROUNDING_TOLERANCE", "0.02")))


def check_required_fields(expense) -> list[Check]:
    required = {"vendor_raw_name": "Vendor", "transaction_date": "Transaction date",
                "total": "Total"}
    checks = []
    for attr, label in required.items():
        present = getattr(expense, attr, None) not in (None, "")
        checks.append(Check(
            code=f"REQUIRED_{attr.upper()}", severity=Severity.ERROR, passed=present,
            message=f"{label} is present." if present else f"{label} is missing.",
        ))
    return checks


def check_tax_arithmetic(expense) -> list[Check]:
    """THE BC GATE (Dev Guide 3.f): subtotal + GST + PST == total, within the
    configured rounding tolerance. Two tax lines, not one."""
    if expense.subtotal is None or expense.total is None:
        return [Check(code="TAX_ARITHMETIC", severity=Severity.ERROR, passed=False,
                      message="Cannot check arithmetic: subtotal or total is missing.")]

    taxes = sum((Decimal(str(t["amount"])) for t in (expense.tax_breakdown or [])),
                Decimal("0"))
    computed = expense.subtotal + taxes
    delta = abs(computed - expense.total)
    ok = delta <= _tolerance()
    return [Check(
        code="TAX_ARITHMETIC", severity=Severity.ERROR, passed=ok,
        message=("Subtotal plus taxes equals the total."
                 if ok else
                 f"Subtotal ({expense.subtotal}) plus taxes ({taxes}) is {computed}, "
                 f"but the total reads {expense.total} -- a difference of {delta}."),
        expected=str(expense.total), actual=str(computed),
        meta={"taxes": str(taxes), "delta": str(delta),
              "tolerance": str(_tolerance())},
    )]


def check_tax_codes_supported(expense) -> list[Check]:
    """Every tax code on the receipt must exist in the company's configured tax
    profile. An unfamiliar code means the receipt came from a province we are
    not configured for -- review, do not guess."""
    configured = {tc["label"].upper() for tc in (expense.company.tax_codes or [])}
    found = {str(t["code"]).upper() for t in (expense.tax_breakdown or [])}
    unknown = found - configured
    return [Check(
        code="TAX_CODES_SUPPORTED", severity=Severity.WARNING, passed=not unknown,
        message=("All tax codes are configured for this company."
                 if not unknown else
                 f"Unrecognised tax code(s): {', '.join(sorted(unknown))}. "
                 f"Configured: {', '.join(sorted(configured)) or 'none'}."),
        meta={"unknown": sorted(unknown)},
    )]


def check_currency(expense) -> list[Check]:
    checks = []
    company_currency = expense.company.currency
    matches = expense.currency == company_currency
    checks.append(Check(
        code="CURRENCY_SUPPORTED", severity=Severity.ERROR, passed=matches,
        message=(f"Currency is {company_currency}." if matches else
                 f"Receipt currency {expense.currency} does not match the company "
                 f"currency {company_currency}. Foreign currency is out of MVP scope."),
        expected=company_currency, actual=expense.currency,
    ))

    interpretation = expense.interpretation
    conflict = (interpretation.fields or {}).get("currency_conflict") if interpretation else None
    if conflict:
        checks.append(Check(
            code="CURRENCY_CONFLICT", severity=Severity.ERROR, passed=False,
            message=f"The document shows conflicting currencies "
                    f"({conflict['raw_value']}). Confirm which applies.",
            meta={"tokens": conflict.get("normalized_value")},
        ))
    return checks


def check_date_sane(expense) -> list[Check]:
    if expense.transaction_date is None:
        return []                       # covered by REQUIRED_TRANSACTION_DATE
    today = timezone.localdate()
    future = (expense.transaction_date - today).days > 1   # 1 day of TZ slop
    very_old = (today - expense.transaction_date).days > 365 * 7
    checks = [Check(
        code="DATE_NOT_FUTURE", severity=Severity.ERROR, passed=not future,
        message=("Transaction date is not in the future." if not future else
                 f"Transaction date {expense.transaction_date} is in the future."),
    )]
    if very_old:
        checks.append(Check(
            code="DATE_VERY_OLD", severity=Severity.WARNING, passed=False,
            message=f"Transaction date {expense.transaction_date} is more than "
                    f"seven years old; confirm it is correct.",
        ))
    return checks


def check_ambiguous_date(expense) -> list[Check]:
    """The parser flags day/month ambiguity rather than guessing. Surface it as
    a warning so a human confirms before this ever reaches the ledger."""
    interpretation = expense.interpretation
    field = (interpretation.fields or {}).get("transaction_date") if interpretation else None
    ambiguous = bool((field or {}).get("source", {}).get("ambiguous_day_month"))
    if not ambiguous:
        return []
    return [Check(
        code="DATE_AMBIGUOUS", severity=Severity.WARNING, passed=False,
        message=f"The printed date '{field['raw_value']}' could be day-first or "
                f"month-first. Day-first was assumed -- please confirm.",
    )]


def check_total_positive(expense) -> list[Check]:
    if expense.total is None:
        return []
    ok = expense.total > 0
    return [Check(
        code="TOTAL_POSITIVE", severity=Severity.ERROR, passed=ok,
        message=("Total is a positive amount." if ok else
                 "Total is zero or negative. Refunds and credits are out of MVP scope."),
        actual=str(expense.total),
    )]


def check_no_full_card_number(expense) -> list[Check]:
    checks = []
    if expense.card_last_four and len(expense.card_last_four) != 4:
        checks.append(Check(
            code="CARD_LAST_FOUR_INVALID", severity=Severity.ERROR, passed=False,
            message="Only the last four digits of a card may be stored.",
        ))
    interpretation = expense.interpretation
    if interpretation and (interpretation.fields or {}).get("pan_warning"):
        checks.append(Check(
            code="POSSIBLE_FULL_CARD_NUMBER", severity=Severity.WARNING, passed=False,
            message="The document appears to contain a full card number. It was "
                    "not stored, but review the evidence before sharing it.",
        ))
    return checks


def check_extraction_usable(expense) -> list[Check]:
    """An ABSTAINED extraction is not an error -- it is a request for manual
    entry. Distinguishing it from FAILED is what keeps photo receipts usable."""
    interpretation = expense.interpretation
    if interpretation is None:
        return [Check(code="EXTRACTION_PRESENT", severity=Severity.ERROR,
                      passed=False, message="No extraction has been run.")]
    if interpretation.extraction_status == ExtractionStatus.ABSTAINED:
        reason = (interpretation.evidence or {}).get("detail", "")
        return [Check(
            code="EXTRACTION_ABSTAINED", severity=Severity.WARNING, passed=False,
            message=f"Automatic extraction could not read this document, so the "
                    f"fields must be entered by hand. {reason}".strip(),
        )]
    if interpretation.extraction_status == ExtractionStatus.FAILED:
        reason = (interpretation.evidence or {}).get("detail", "")
        return [Check(
            code="EXTRACTION_FAILED", severity=Severity.ERROR, passed=False,
            message=f"The file could not be read. {reason}".strip(),
        )]
    return [Check(code="EXTRACTION_PRESENT", severity=Severity.ERROR, passed=True,
                  message="Extraction completed.")]


ALL_CHECKS = (
    check_extraction_usable,
    check_required_fields,
    check_tax_arithmetic,
    check_tax_codes_supported,
    check_currency,
    check_date_sane,
    check_ambiguous_date,
    check_total_positive,
    check_no_full_card_number,
)


def run_validations(expense) -> list[Check]:
    checks: list[Check] = []
    for fn in ALL_CHECKS:
        checks.extend(fn(expense))
    return checks


def blocking_errors(checks) -> list:
    return [c for c in checks if c.severity == Severity.ERROR and not c.passed]