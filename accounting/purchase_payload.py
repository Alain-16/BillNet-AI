from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from common.errors import DomainError


PAYMENT_TYPE_MAP = {
    "CREDIT_CARD": ("CreditCard", {"Credit Card"}),
    # "Cash", not "Check": an Interac/debit purchase has no cheque number. The
    # money still comes out of a bank account, which is what AccountRef names.
    "BANK": ("Cash", {"Bank"}),
}


def distribute_tax(lines: list[list], subtotal:Decimal, total:Decimal) -> list[dict]:


    tax = (total - subtotal).quantize(Decimal("0.01"))

    if tax < 0:
        raise DomainError("Total is less than subtotal; refusing to post.",
                          code="negative_tax")
    if subtotal <= 0:
        raise DomainError("Subtotal must be positive to distribute tax.",
                          code="bad_subtotal")
    out, running = [], Decimal("0")
    for index, line in enumerate(lines):
        net = Decimal(str(line["net"]))
        if index < len(lines) -1:
            share = (net / subtotal * tax).quantize(Decimal("0.01"), ROUND_HALF_UP)
            running += share
        else:
            share = tax - running
        out.append({**line, "net": str(net), "tax_share": str(share), "gross": str(net + share)})

    return out


def build_purchase(snapshot:dict) -> dict:
    payment_type = snapshot["payment_type"]
    account = snapshot["payment_account"]

    lines = []
    for line in snapshot["lines"]:
        detail = {
            "AccountRef": {"value": line["account"]["external_id"],
                           "name": line["account"].get("name", "")},
        }
        # CustomerRef is how a Purchase line is costed to a job. QBO's newer
        # minorversions also accept ProjectRef; CustomerRef is accepted
        # everywhere, so it is the safer default. Verify against your
        # minorversion before switching.
        project = snapshot.get("project")
        if project and project.get("external_id"):
            detail["CustomerRef"] = {"value": project["external_id"]}
            detail["BillableStatus"] = "NotBillable"

        lines.append({
            "DetailType": "AccountBasedExpenseLineDetail",
            "Amount": float(Decimal(line["gross"])),   # QBO wants a JSON number
            "Description": line["description"][:4000],
            "AccountBasedExpenseLineDetail": detail,
        })

    body = {
        "PaymentType": payment_type,
        # REQUIRED for every Purchase. This is the account money came OUT of --
        # not an expense account. Expense accounts live on the lines.
        "AccountRef": {"value": account["external_id"],
                       "name": account.get("name", "")},
        "Line": lines,
        "TxnDate": snapshot["transaction_date"],
        "TotalAmt": float(Decimal(snapshot["total"])),
        # The readback anchor. After a timeout we query Purchase by DocNumber to
        # learn whether QuickBooks actually created it -- see SECTION 9.
        "DocNumber": snapshot["doc_number"],
        "PrivateNote": snapshot.get("memo", ""),
    }

    vendor = snapshot.get("vendor")
    if vendor and vendor.get("external_id"):
        # EntityRef is the payee. Optional on a Purchase -- an unmatched vendor
        # must not block posting.
        body["EntityRef"] = {"value": vendor["external_id"],
                             "name": vendor.get("name", ""),
                             "type": "Vendor"}

    # No CurrencyRef: MultiCurrencyEnabled is false for this company, and
    # sending it when multicurrency is off is rejected.
    # No TxnTaxDetail: UsingSalesTax is false, so there are no tax codes.

    _assert_totals_agree(body)
    return body


def _assert_totas_agree(body: dict) -> None:

    line_sum = sum(Decimal(str(l["Amount"])) for l in body["Line"])
    total = Decimal(str(body["TotalAmt"]))
    if line_sum.quantize(Decimal("0.01")) != total.quantize(Decimal("0.01")):
        raise DomainError(
            f"Line amounts sum to {line_sum} but TotalAmt is {total}.",
            code="totals_disagree"
        )