import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime

from accounts.providers.base import OAuth2Provider, OAuthTokens, ReferenceDTO, REQUEST_TIMEOUT
from common.enums import AccountingRefType, Provider

# QBO AccountType values that mean "you can pay from this" (Dev Guide decision #1:
# both card and bank supported).
_PAYMENT_ACCOUNT_TYPES = {"Bank", "Credit Card"}
_MAX_RESULTS = 1000


class QuickBooksProvider(OAuth2Provider):
    provider_key = Provider.QUICKBOOKS
    token_uses_basic_auth = True   # Intuit requires HTTP Basic on the token call

    def __init__(self):
        self.auth_url = settings.INTUIT_AUTH_URL
        self.token_url = settings.INTUIT_TOKEN_URL
        self.client_id = settings.INTUIT_CLIENT_ID
        self.client_secret = settings.INTUIT_CLIENT_SECRET
        self.redirect_uri = settings.INTUIT_REDIRECT_URI
        self.scopes = settings.INTUIT_SCOPES

    def identify(self, tokens: OAuthTokens, *, realm_id=None) -> str:
        # Intuit hands us the realmId as a query param on the callback — it IS
        # the external account id. (There's no userinfo call to make.)
        if not realm_id:
            raise ValueError("QuickBooks callback missing realmId")
        return realm_id

    def revoke(self, refresh_token: str) -> None:
        requests.post(
            settings.INTUIT_REVOKE_URL,
            json={"token": refresh_token},
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )

    # ---- reference-data sync (AccountingProvider role) ----
    def fetch_references(self, access_token: str, realm_id: str) -> list[ReferenceDTO]:
       
        refs: list[ReferenceDTO] = []
        refs += self._company_info(access_token, realm_id)
        refs += self._query(access_token, realm_id, "Account", self._account)
        refs += self._query(access_token, realm_id, "Vendor", self._vendor)
        refs += self._query(access_token, realm_id, "Customer", self._customer)
        refs += self._query(access_token, realm_id, "Item", self._item)
        refs += self._query(access_token, realm_id, "TaxCode", self._taxcode)
        return refs

    def _query(self, access_token, realm_id, entity, mapper):
       
        url = f"{settings.QBO_API_BASE}/v3/company/{realm_id}/query"
        out = []
        start_position = 1

        while True:
            resp = requests.get(
                url,
                params={
                    "query": self._reference_query(entity, start_position),
                    "minorversion": settings.QBO_MINOR_VERSION,
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json().get("QueryResponse", {}).get(entity, [])

            for row in rows:
                dto = mapper(row)
                if dto:
                    out.append(dto)

            if len(rows) < _MAX_RESULTS:
                break
            start_position += _MAX_RESULTS

        return out

    @staticmethod
    def _reference_query(entity, start_position):
        return (
            f"select * from {entity} where Active in (true, false) "
            f"startposition {start_position} maxresults {_MAX_RESULTS}"
        )

    @staticmethod
    def _meta_updated(row):
        return parse_datetime((row.get("MetaData") or {}).get("LastUpdatedTime", "") or "")

    def _company_info(self, access_token, realm_id):
        url = f"{settings.QBO_API_BASE}/v3/company/{realm_id}/companyinfo/{realm_id}"
        resp = requests.get(
            url,
            params={"minorversion": settings.QBO_MINOR_VERSION},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        row = resp.json().get("CompanyInfo", {})
        if not row:
            return []

        company_addr = row.get("CompanyAddr") or {}
        return [
            ReferenceDTO(
                AccountingRefType.COMPANY_INFO,
                row.get("Id") or realm_id,
                row.get("CompanyName") or row.get("LegalName") or "QuickBooks company",
                True,
                row.get("SyncToken", ""),
                self._meta_updated(row),
                data={
                    "realm_id": realm_id,
                    "company_name": row.get("CompanyName", ""),
                    "legal_name": row.get("LegalName", ""),
                    "country": row.get("Country", "") or company_addr.get("Country", ""),
                    "email": (row.get("Email") or {}).get("Address", ""),
                    "supported_languages": row.get("SupportedLanguages", ""),
                    "fiscal_year_start_month": row.get("FiscalYearStartMonth", ""),
                    "tax_year_start_month": row.get("TaxYearStartMonth", ""),
                    "raw": row,
                },
            )
        ]

    def _vendor(self, row):
        return ReferenceDTO(
            AccountingRefType.VENDOR,
            row["Id"],
            row.get("DisplayName") or row.get("PrintOnCheckName") or row.get("CompanyName", ""),
            bool(row.get("Active", True)),
            row.get("SyncToken", ""),
            self._meta_updated(row),
            data={
                "display_name": row.get("DisplayName", ""),
                "company_name": row.get("CompanyName", ""),
                "print_on_check_name": row.get("PrintOnCheckName", ""),
                "given_name": row.get("GivenName", ""),
                "family_name": row.get("FamilyName", ""),
                "primary_email": (row.get("PrimaryEmailAddr") or {}).get("Address", ""),
                "primary_phone": (row.get("PrimaryPhone") or {}).get("FreeFormNumber", ""),
                "tax_identifier": row.get("TaxIdentifier", ""),
                "vendor_1099": row.get("Vendor1099", False),
                "raw": row,
            },
        )

    def _customer(self, row):
        parent_ref = row.get("ParentRef") or {}
        return ReferenceDTO(
            AccountingRefType.CUSTOMER,
            row["Id"],
            row.get("DisplayName") or row.get("FullyQualifiedName") or row.get("CompanyName", ""),
            bool(row.get("Active", True)),
            row.get("SyncToken", ""),
            self._meta_updated(row),
            data={
                "display_name": row.get("DisplayName", ""),
                "fully_qualified_name": row.get("FullyQualifiedName", ""),
                "company_name": row.get("CompanyName", ""),
                "job": row.get("Job", False),
                "is_project": bool(row.get("Job") or parent_ref),
                "parent_id": parent_ref.get("value", ""),
                "parent_name": parent_ref.get("name", ""),
                "bill_with_parent": row.get("BillWithParent", False),
                "primary_email": (row.get("PrimaryEmailAddr") or {}).get("Address", ""),
                "primary_phone": (row.get("PrimaryPhone") or {}).get("FreeFormNumber", ""),
                "raw": row,
            },
        )

    def _taxcode(self, row):
        return ReferenceDTO(
            AccountingRefType.TAX_CODE,
            row["Id"],
            row.get("Name", ""),
            bool(row.get("Active", True)),
            row.get("SyncToken", ""),
            self._meta_updated(row),
            data={
                "name": row.get("Name", ""),
                "description": row.get("Description", ""),
                "taxable": row.get("Taxable", None),
                "hidden": row.get("Hidden", None),
                "purchase_tax_rate_list": row.get("PurchaseTaxRateList", {}),
                "sales_tax_rate_list": row.get("SalesTaxRateList", {}),
                "raw": row,
            },
        )

    def _item(self, row):
        income_ref = row.get("IncomeAccountRef") or {}
        expense_ref = row.get("ExpenseAccountRef") or {}
        asset_ref = row.get("AssetAccountRef") or {}
        return ReferenceDTO(
            AccountingRefType.ITEM,
            row["Id"],
            row.get("Name", ""),
            bool(row.get("Active", True)),
            row.get("SyncToken", ""),
            self._meta_updated(row),
            data={
                "name": row.get("Name", ""),
                "fully_qualified_name": row.get("FullyQualifiedName", ""),
                "type": row.get("Type", ""),
                "description": row.get("Description", ""),
                "sku": row.get("Sku", ""),
                "income_account_id": income_ref.get("value", ""),
                "income_account_name": income_ref.get("name", ""),
                "expense_account_id": expense_ref.get("value", ""),
                "expense_account_name": expense_ref.get("name", ""),
                "asset_account_id": asset_ref.get("value", ""),
                "asset_account_name": asset_ref.get("name", ""),
                "taxable": row.get("Taxable", None),
                "unit_price": row.get("UnitPrice", None),
                "raw": row,
            },
        )

    def _account(self, row):
        # One Account row becomes either a PAYMENT_ACCOUNT (bank/card) or an
        # ACCOUNT (expense target). data carries the QBO AccountType so the
        # posting layer can reason about it later without a re-fetch.
        account_type = row.get("AccountType", "")
        account_subtype = row.get("AccountSubType", "")
        etype = (AccountingRefType.PAYMENT_ACCOUNT
                 if account_type in _PAYMENT_ACCOUNT_TYPES
                 else AccountingRefType.ACCOUNT)
        return ReferenceDTO(
            etype,
            row["Id"],
            row.get("Name") or row.get("FullyQualifiedName", ""),
            bool(row.get("Active", True)),
            row.get("SyncToken", ""),
            self._meta_updated(row),
            data={
                "name": row.get("Name", ""),
                "fully_qualified_name": row.get("FullyQualifiedName", ""),
                "account_type": account_type,
                "account_subtype": account_subtype,
                "classification": row.get("Classification", ""),
                "account_number": row.get("AcctNum", ""),
                "currency": (row.get("CurrencyRef") or {}).get("value", ""),
                "current_balance": row.get("CurrentBalance", None),
                "current_balance_with_sub_accounts": row.get(
                    "CurrentBalanceWithSubAccounts", None
                ),
                "usable_as_payment_account": account_type in _PAYMENT_ACCOUNT_TYPES,
                "usable_as_expense_account": account_type in {
                    "Expense",
                    "Cost of Goods Sold",
                    "Other Expense",
                },
                "raw": row,
            },
        )
