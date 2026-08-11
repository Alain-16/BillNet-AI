from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from common.enums import ExtractionMethod, PaymentType
from documents.extractors.base import ExtractedFieldDTO
from collections import Counter


_MONEY = re.compile(r"\$?\s*(-?\d[\d,\s]*\.\d{2})\b")

_LABEL_TOTAL = re.compile(r"\b(?:GRAND\s+TOTAL|TOTAL\s+DUE|AMOUNT\s+DUE|TOTAL)\b", re.I)
_LABEL_SUBTOTAL = re.compile(r"\bSUB[\s\-]?TOTAL\b", re.I)
_LABEL_TAX = {
    "GST": re.compile(r"\bG\.?S\.?T\.?(?:\s*/\s*H\.?S\.?T\.?)?\b", re.I),
    "PST": re.compile(r"\bP\.?S\.?T\.?\b|\bPROV(?:INCIAL)?\s+TAX\b", re.I),
    "HST": re.compile(r"\bH\.?S\.?T\.?\b", re.I),
}
_LABEL_RECEIPT_NO = re.compile(
    r"\b(?:INVOICE|RECEIPT|ORDER|TRANSACTION|TRANS|REF(?:ERENCE)?|BILL)\b"
    r"\s*(?:#|NO\.?|NUMBER)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.I)

# "ending in 1234", "****1234", "XXXX 1234"
_LAST_FOUR = re.compile(r"(?:\*{2,}|X{2,}|ENDING(?:\s+IN)?)\s*[-\s]?(\d{4})\b", re.I)
# A bare run of 13-19 digits is a full card number. We must NEVER store it.
_POSSIBLE_PAN = re.compile(r"\b(?:\d[ -]?){13,19}\b")

_CURRENCY_TOKEN = re.compile(r"\b(CAD|USD|EUR|GBP)\b")

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")
_DATE_MONTH_FIRST = re.compile(
    r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_DATE_DAY_FIRST = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")

_PAYMENT_HINTS = [
    (re.compile(r"\b(?:VISA|MASTERCARD|MASTER\s?CARD|AMEX|AMERICAN\s+EXPRESS|"
                r"CREDIT|CREDIT\s+CARD)\b", re.I), PaymentType.CREDIT_CARD),
    (re.compile(r"\b(?:DEBIT|INTERAC|BANK|CHEQUE|CHECK|E-?TRANSFER)\b", re.I),
     PaymentType.BANK),
]

_SPACE_WORD = re.compile(r"\b(?:[A-Za-z]\s){2,}[A-Za-z]\b")

_RATE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")

def _despace(line: str) -> str:
    return _SPACE_WORD.sub(lambda m: m.group(0).replace(" ", ""), line)


def _is_amount_only(line: str) -> str:
    return not _MONEY.sub("", line).strip(" $\t")




def parse_money(token: str) -> Decimal | None:
    """Decimal from a string, ALWAYS. Never float -- Invariant #2. Decimal(str)
    is exact; Decimal(float) inherits the binary rounding error."""
    try:
        return Decimal(token.replace(",", "").replace(" ", "").replace("$", ""))
    except (InvalidOperation, AttributeError):
        return None


def _amounts_on(line: str) -> list[Decimal]:
    return [d for d in (parse_money(m.group(1)) for m in _MONEY.finditer(line))
            if d is not None]


def _find_labeled_amount(lines, label, *, exclude=None, lookahead=2):
    """Scan bottom-up -- totals live at the foot of a receipt.

    Handles BOTH layouts:
      same-line    "TOTAL            40.30"
      two-column   "T O T A L" / "$  218.25"     <- thermal receipts
    """
    for idx in range(len(lines) - 1, -1, -1):
        flat = _despace(lines[idx])
        if not label.search(flat):
            continue
        if exclude and exclude.search(flat):
            continue

        amounts = _amounts_on(lines[idx])
        if amounts:
            return amounts[-1], idx, lines[idx].strip()

        # No amount beside the label -- look ahead for an amount-only line.
        for j in range(idx + 1, min(idx + 1 + lookahead, len(lines))):
            nxt = lines[j]
            if not nxt.strip():
                continue
            if _is_amount_only(nxt):
                amounts = _amounts_on(nxt)
                if amounts:
                    return (amounts[-1], j,
                            f"{lines[idx].strip()} {nxt.strip()}")
            break        # a real non-amount line ends the label/value pairing

    return None, None, None  


def _parse_date(text: str):
    """Returns (iso_string, raw, confidence, evidence).

    AMBIGUITY IS NOT RESOLVED SILENTLY. 05/08/2026 is 5 August in Canada and
    8 May in the US, and receipts print both. When both components are <= 12 we
    take day-first (BC convention) but drop confidence to 0.40 and flag it, so
    the validator forces a human to confirm. PRD Feature 4: dates are
    normalized to ISO 'without discarding the original text'.
    """
    m = _DATE_ISO.search(text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return date(y, mo, d).isoformat(), m.group(0), Decimal("0.95"), {}
        except ValueError:
            pass

    m = _DATE_MONTH_FIRST.search(text)
    if m:
        mon = _MONTHS.get(m.group(1)[:3].lower())
        if mon:
            try:
                return (date(int(m.group(3)), mon, int(m.group(2))).isoformat(),
                        m.group(0), Decimal("0.90"), {})
            except ValueError:
                pass

    m = _DATE_DAY_FIRST.search(text)
    if m:
        mon = _MONTHS.get(m.group(2)[:3].lower())
        if mon:
            try:
                return (date(int(m.group(3)), mon, int(m.group(1))).isoformat(),
                        m.group(0), Decimal("0.90"), {})
            except ValueError:
                pass

    m = _DATE_NUMERIC.search(text)
    if m:
        a, b, y = (int(g) for g in m.groups())
        if y < 100:
            y += 2000
        ambiguous = a <= 12 and b <= 12 and a != b
        day, month = (a, b) if a > 12 or ambiguous else (b, a) if b > 12 else (a, b)
        try:
            iso = date(y, month, day).isoformat()
        except ValueError:
            return None, m.group(0), None, {"reason": "invalid_date"}
        return (iso, m.group(0),
                Decimal("0.40") if ambiguous else Decimal("0.80"),
                {"ambiguous_day_month": ambiguous,
                 "note": "day-first assumed (BC convention); confirm"} if ambiguous else {})

    return None, "", None, {"reason": "no_date_found"}

_VENDOR_NOISE = re.compile(
    r"(?:\bPHONE\b|\bTEL\b|\bFAX\b|\bGST\s*#|\bHST\s*#|\bBN\s*#|"
    r"\bSTORE\s*(?:HRS|HOURS|#)|\bREG\s*#|\bTRANS\s*#|\bDATE\b|"
    r"\bOPERATOR\b|\bCASHIER\b|\bTERMINAL\b|\bINVOICE\b|\bRECEIPT\b|"
    r"WWW\.|HTTP|\.COM|@)", re.I)



def _find_vendor(lines, *, window=12):
    """The store name is usually the first real line of a receipt -- but only
    after skipping watermarks, contact details and register metadata.

    Confidence stays LOW on purpose. However good this heuristic gets, a raw
    string off a receipt is not a canonical vendor; slice 3's matcher decides
    that. This only has to give the reviewer a sensible starting value.
    """
    from collections import Counter
    counts = Counter(ln.strip().upper() for ln in lines if ln.strip())

    def candidates(skip_repeats: bool):
        for idx, line in enumerate(lines[:window]):
            s = line.strip()
            if not (3 <= len(s) <= 60):
                continue
            # A line repeated three or more times is a watermark or a running
            # header ("SAMPLE - NOT VALID" printed five times), not a vendor.
            if skip_repeats and counts[s.upper()] >= 3:
                continue
            if _MONEY.search(s) or _DATE_ISO.search(s) or _DATE_NUMERIC.search(s):
                continue
            if _VENDOR_NOISE.search(s):
                continue
            # Reject separator rules, phone numbers and part codes: a real
            # is mostly letters.
            letters = sum(ch.isalpha() for ch in s)
            if letters < 3 or letters * 2 < len(s.replace(" ", "")):
                continue
            yield s, idx

    # Strict pass first; fall back to allowing repeats rather than returnin
    # nothing (a receipt whose every line repeats still needs a best guess)
    for skip_repeats in (True, False):
        for value, idx in candidates(skip_repeats):
            return value, idx
    return None, None


    




def parse_receipt_text(text: str, *,
                       method: str = ExtractionMethod.EMBEDDED_TEXT
                       ) -> dict[str, ExtractedFieldDTO]:
    """Text -> fields with provenance. Absent fields are simply not in the dict;
    they are never present-but-empty. Downstream code distinguishes 'missing'
    from 'zero' on that basis."""
    lines = [ln for ln in text.splitlines()]
    out: dict[str, ExtractedFieldDTO] = {}

    def put(name, raw, normalized, confidence, line_idx, extra=None):
        out[name] = ExtractedFieldDTO(
            name=name, raw_value=str(raw), normalized_value=normalized,
            confidence=confidence, method=method,
            source={"line": line_idx, **(extra or {})},
        )

    # --- vendor ---
    vendor, v_idx = _find_vendor(lines)
    if vendor:
        put("vendor_raw_name", vendor, vendor, Decimal("0.40"), v_idx)

    # --- totals. exclude= keeps 'SUBTOTAL' from matching the TOTAL label ---
    total, t_idx, t_raw = _find_labeled_amount(lines, _LABEL_TOTAL,
                                               exclude=_LABEL_SUBTOTAL)
    if total is not None:
        put("total", t_raw, total, Decimal("0.85"), t_idx)

    subtotal, s_idx, s_raw = _find_labeled_amount(lines, _LABEL_SUBTOTAL)
    if subtotal is not None:
        put("subtotal", s_raw, subtotal, Decimal("0.85"), s_idx)

    # --- taxes: one field per code found (BC ships GST + PST) ---
    for code, pattern in _LABEL_TAX.items():
        amount, idx, raw = _find_labeled_amount(lines, pattern)
        if amount is not None:
            extra = {"tax_code": code}
            m = _RATE.search(raw or "")
            if m:
                extra["rate_printed"] = str(Decimal(m.group(1)) / 100)
            put(f"tax_{code.lower()}", raw, amount, Decimal("0.85"), idx,
                {"tax_code": code})

    # --- date: search the whole document, prefer an explicitly labeled line ---
    date_source = text
    for idx, line in enumerate(lines):
        if re.search(r"\b(?:DATE|PURCHASED|TRANSACTION)\b", line, re.I):
            date_source, d_idx = line, idx
            break
    else:
        d_idx = None
    iso, raw, conf, evidence = _parse_date(date_source)
    if iso:
        put("transaction_date", raw, iso, conf, d_idx, evidence or None)

    # --- receipt number ---
    for idx, line in enumerate(lines):
        m = _LABEL_RECEIPT_NO.search(line)
        if m:
            put("receipt_number", m.group(0), m.group(1).upper(),
                Decimal("0.70"), idx)
            break

    # --- currency: NEVER default silently on contradictory evidence ---
    tokens = {m.group(1).upper() for m in _CURRENCY_TOKEN.finditer(text)}
    if len(tokens) == 1:
        code = tokens.pop()
        put("currency", code, code, Decimal("0.90"), None)
    elif len(tokens) > 1:
        # Conflict -> record BOTH and let the validator block. PRD Feature 4:
        # "Currency defaults are never silently applied when the document
        # contains contradictory currency evidence."
        out["currency_conflict"] = ExtractedFieldDTO(
            name="currency_conflict", raw_value=", ".join(sorted(tokens)),
            normalized_value=sorted(tokens), confidence=Decimal("1.0"),
            method=method, source={"reason": "multiple_currency_tokens"},
        )

    # --- payment hints ---
    for pattern, ptype in _PAYMENT_HINTS:
        m = pattern.search(text)
        if m:
            put("payment_type", m.group(0), ptype, Decimal("0.60"), None)
            break

    # --- card last four, with a PAN guard ---
    m = _LAST_FOUR.search(text)
    if m:
        put("card_last_four", m.group(0), m.group(1), Decimal("0.85"), None)
    if _POSSIBLE_PAN.search(re.sub(r"\.\d{2}\b", "", text)):
        # Security requirement: "do not intentionally extract or retain full
        # card numbers". We record only that one was SEEN -- never its digits.
        out["pan_warning"] = ExtractedFieldDTO(
            name="pan_warning", raw_value="[redacted]", normalized_value=True,
            confidence=Decimal("1.0"), method=method,
            source={"reason": "possible_full_card_number_in_document"},
        )

    return out


def fields_to_expense_values(fields: dict) -> dict:
    """Translate the stored fields JSON into Expense column values.

    Money comes back through Decimal(str) -- the JSON holds strings precisely so
    a float never touches a persisted amount.
    """
    def norm(name):
        f = fields.get(name)
        return f.get("normalized_value") if f else None

    values: dict = {}
    for column, field_name in (("vendor_raw_name", "vendor_raw_name"),
                               ("receipt_number", "receipt_number"),
                               ("card_last_four", "card_last_four"),
                               ("payment_type", "payment_type")):
        v = norm(field_name)
        if v:
            values[column] = v

    for column in ("subtotal", "total"):
        v = norm(column)
        if v is not None:
            values[column] = Decimal(str(v))

    iso = norm("transaction_date")
    if iso:
        values["transaction_date"] = datetime.strptime(iso, "%Y-%m-%d").date()

    currency = norm("currency")
    if currency:
        values["currency"] = currency

    # tax_breakdown: amounts stay STRINGS in JSON (Decimal-in-JSON discipline,
    # per the Expense model comment); the Decimal columns carry the arithmetic.
    breakdown, tax_total = [], Decimal("0")
    for key, field in fields.items():
        if not key.startswith("tax_"):
            continue
        code = field.get("source", {}).get("tax_code") or key[4:].upper()
        amount = Decimal(str(field["normalized_value"]))
        breakdown.append({"code": code, "amount": str(amount), "rate": field.get("source", {}).get("rate_printed", "")})
        tax_total += amount
    if breakdown:
        values["tax_breakdown"] = breakdown
        values["tax_total"] = tax_total

    return values