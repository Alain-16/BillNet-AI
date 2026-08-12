from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from common.enums import ExtractionMethod, PaymentType
from documents.extractors.base import ExtractedFieldDTO
from collections import Counter
from django.conf import settings


_O = r"[O0Q]"          # O <-> zero, Q
_L = r"[L1I|\]!]"      # l <-> one, I, pipe, bracket, bang   <- the common one
_S = r"[S5$]"
_A = r"[A4@]"
_T = r"[T7]"
_G = r"[G6]"
_B = r"[B8]"
_U = r"[UV]"

_MONEY = re.compile(r"\$?\s*(-?\d[\d,\s]*\.\d{2})\b")

_LABEL_TOTAL = re.compile(rf"{_T}{_O}{_T}{_A}{_L}(?![A-Za-z])", re.I)
_LABEL_SUBTOTAL = re.compile(
    rf"{_S}{_U}{_B}[\s\-]?{_T}{_O}{_T}{_A}{_L}(?![A-Za-z])", re.I)
_LABEL_TAX = {
    "GST": re.compile(
        rf"\b{_G}\.?{_S}\.?{_T}\.?(?:\s*/\s*H\.?{_S}\.?{_T}\.?)?(?![A-Za-z])", re.I),
    "PST": re.compile(
        rf"\bP\.?{_S}\.?{_T}\.?(?![A-Za-z])|\bPR{_O}V(?:INCIA{_L})?\s+{_T}{_A}X\b", re.I),
    "HST": re.compile(rf"\bH\.?{_S}\.?{_T}\.?(?![A-Za-z])", re.I),
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

_DEPT_PREFIX = re.compile(r"^\d{1,3}\s*[-–]\s*")

_PAYMENT_HINTS = [
    (re.compile(r"\b(?:VISA|MASTERCARD|MASTER\s?CARD|AMEX|AMERICAN\s+EXPRESS|"
                r"CREDIT|CREDIT\s+CARD)\b", re.I), PaymentType.CREDIT_CARD),
    (re.compile(r"\b(?:DEBIT|INTERAC|BANK|CHEQUE|CHECK|E-?TRANSFER)\b", re.I),
     PaymentType.BANK),
]

_TRAILING_SKU = re.compile(r"\s+\d{4,}$")

_SPACE_WORD = re.compile(r"\b(?:[A-Za-z]\s){2,}[A-Za-z]\b")

_RATE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")

_TOTALS_LABELS = (_LABEL_TOTAL, _LABEL_SUBTOTAL, *_LABEL_TAX.values())
_DEPT_CODE = re.compile(r"^\d{3,}\s+")
_LEADERS = re.compile(r"[.\-_\s]+$")
_QTY_PREFIX = re.compile(r"^(\d{1,3})\s*(?:@|X)\s+", re.I)
_QTY_LINE = re.compile(
    r"^\s*(\d{1,3})\s*(?:@|X)\s*\$?\s*(\d[\d,]*\.\d{2})\s*(?:ea\.?|each)?\s*$", re.I)

def _item_tolerance() -> Decimal:
    return Decimal(str(getattr(settings, "MONEY_ROUNDING_TOLERANCE", "0,02")))

def _money_matches(line: str) -> list:
    return [m for m in _MONEY.finditer(line) if parse_money(m.group(1)) is not None]

def _is_totals_line(flat:str)-> bool:
    return any(pattern.search(flat) for pattern in _TOTALS_LABELS)

def _item_description(text:str) -> str:
    return _LEADERS.sub("",_DEPT_CODE.sub("", text.strip())).strip()

def _has_description(desc: str) -> bool:
    return sum(ch.isalpha() for ch in desc) >= 2

def _build_item(description: str, amount, line_idx:int) -> dict:
    item = {"description":description, "amount": str(amount), "line":line_idx}

    qty = _QTY_PREFIX.match(description)
    if qty:
        item["quantity"] = int(qty.group(1))
        item["description"] = description[qty.end():].strip()
    return item

def _despace(line: str) -> str:
    return _SPACE_WORD.sub(lambda m: m.group(0).replace(" ", ""), line)


def _is_amount_only(line: str) -> bool:
    return not _MONEY.sub("", line).strip(" $\t")

def _item_description(text: str) -> str:
    """Strip everything that surrounds a real description on a receipt line.

    Order matters: leaders come off before the trailing SKU, so
    "WIDGET 948976 ......" loses the dots first and the SKU second.
    """
    desc = _DEPT_PREFIX.sub("", text.strip())
    desc = _DEPT_CODE.sub("", desc)
    desc = _LEADERS.sub("", desc).strip()
    desc = _TRAILING_SKU.sub("", desc)
    return desc.strip()


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


    
def _reconcile_items(items: list[dict], reconcile_to):

    if not items:
        return None, {}

    items_sum = sum((Decimal(i["amount"]) for i in items), Decimal("0"))
    evidence = {"item_count": len(items), "items_sum": str(items_sum)}

    if reconcile_to is None:
        evidence.update(reconciled=False,reason="no_subtotal_or_total_to_check")
        return Decimal("0.60"), evidence

    gap = (items_sum - reconcile_to).copy_abs()
    evidence.update(expected=str(reconcile_to), gap=str(gap))

    if gap <= _item_tolerance():
        evidence["reconciled"] = True
        return Decimal("0.85"), evidence

    evidence.update(reconciled=False, reason="items_do_not_sum_to_subtotal")
    return Decimal("0.45"),evidence

def _is_candidate_description(line: str) -> bool:
 
    if _money_matches(line):
        return False
    if _QTY_LINE.match(line):
        return False
    flat = _despace(line)
    if _is_totals_line(flat) or _VENDOR_NOISE.search(flat):
        return False
    return _has_description(_item_description(line))


def _nearest_description(lines, amount_idx: int, end_idx: int,
                         consumed: set, window: int = 2):
   
    for j in range(amount_idx + 1, min(amount_idx + 1 + window, end_idx)):
        if j in consumed:
            break
        if not lines[j].strip():
            continue
        if _is_candidate_description(lines[j]):
            return j
        break

    for j in range(amount_idx - 1, max(amount_idx - 1 - window, -1), -1):
        if j in consumed:
            break
        if not lines[j].strip():
            continue
        if _is_candidate_description(lines[j]):
            return j
        break

    return None

def _find_line_items(lines: list[str], *, end_idx: int, reconcile_to=None):
   
    items: list[dict] = []
    consumed: set[int] = set()      # description lines already claimed
    idx = 0

    while idx < end_idx:
        if idx in consumed:
            idx += 1
            continue

        line = lines[idx]
        if not line.strip():
            idx += 1
            continue

        # A quantity line is NEVER an item -- it is extra evidence for the item
        # above it. Checked FIRST, before the noise filter, because "@" is in
        # _VENDOR_NOISE (it is there to catch email addresses) and would
        # otherwise discard "2 @ $ 8.49 ea." before we could read it.
        qty_line = _QTY_LINE.match(line)
        if qty_line:
            if items:
                items[-1]["quantity"] = int(qty_line.group(1))
                items[-1]["unit_price"] = str(parse_money(qty_line.group(2)))
            idx += 1
            continue

        flat = _despace(line)
        # Exclude labelled and noise lines BEFORE looking for money -- otherwise
        # a "GST 9.74" line printed above the totals block becomes a phantom
        # item and reconciliation fails on a perfectly good receipt.
        if _is_totals_line(flat) or _VENDOR_NOISE.search(flat):
            idx += 1
            continue

        matches = _money_matches(line)
        if not matches:
            # A SKU line, a footer string, or a description whose amount has
            # not been reached yet. Not an anchor -- move on. If it really is a
            # description, the amount line will come back and claim it.
            idx += 1
            continue

        # Rightmost amount = the extended price. Same convention as
        # _find_labeled_amount's `amounts[-1]`: in a three-column layout the
        # quantity and unit price sit to its LEFT.
        m = matches[-1]
        amount = parse_money(m.group(1))

        # --- Layout A: description on the same line, left of the amount ---
        desc = _item_description(line[:m.start()])
        if _has_description(desc):
            items.append(_build_item(desc, amount, idx))
            idx += 1
            continue

        # --- Layouts B and C: amount-only line, description adjacent ---
        desc_idx = _nearest_description(lines, idx, end_idx, consumed)
        if desc_idx is not None:
            items.append(_build_item(_item_description(lines[desc_idx]), amount, idx))
            consumed.add(desc_idx)
        # No description found: the amount is deliberately DROPPED rather than
        # stored as a nameless item. A nameless item helps nobody, and it would
        # corrupt the reconciliation that earns this parse its confidence.

        idx += 1

    confidence, evidence = _reconcile_items(items, reconcile_to)
    return items, confidence, evidence

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
    end_idx = s_idx if s_idx is not None else (t_idx if t_idx is not None else len(lines))
    items, items_conf, items_evidence = _find_line_items(
        lines,
        end_idx=end_idx,
        # Prefer the subtotal: items sum to the PRE-tax figure. Fall back to
        # total only so a receipt with no printed subtotal still gets checked
        # (it will usually mismatch by the tax, and correctly score 0.45).
        reconcile_to=subtotal if subtotal is not None else total,
    )
    if items:
        # ABSENT != EMPTY. An empty list is never stored -- this parser's
        # contract is "absent fields are simply not in the dict", and
        # fields_to_expense_values relies on it to tell "no items found" from
        # "items found, none of them".
        put("line_items", f"{len(items)} line item(s)", items,
            items_conf, None, items_evidence)

    

    # --- taxes: one field per code found (BC ships GST + PST) ---
    for code, pattern in _LABEL_TAX.items():
        amount, idx, raw = _find_labeled_amount(lines, pattern)
        if amount is not None:
            extra = {"tax_code": code}
            m = _RATE.search(raw or "")
            if m:
                extra["rate_printed"] = str(Decimal(m.group(1)) / 100)
            put(f"tax_{code.lower()}", raw, amount, Decimal("0.85"), idx,
                extra)

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

    items = norm("line_items")
    if items:
        values["line_items"] = items

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