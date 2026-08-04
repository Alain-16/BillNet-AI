import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime

from accounts.providers.base import OAuth2Provider, OAuthTokens, ReferenceDTO, REQUEST_TIMEOUT
from common.enums import AccountingRefType, Provider

# QBO AccountType values that mean "you can pay from this" (Dev Guide decision #1:
# both card and bank supported).
_PAYMENT_ACCOUNT_TYPES = {"Bank", "Credit Card"}


class QuickBooksProvider(OAuth2Provider):
    provider_key = Provider.QUICKBOOKS
    auth_url = "https://appcenter.intuit.com/connect/oauth2"
    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    token_uses_basic_auth = True   # Intuit requires HTTP Basic on the token call

    def __init__(self):
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
            "https://developer.api.intuit.com/v2/oauth2/tokens/revoke",
            json={"token": refresh_token},
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )

    # ---- reference-data sync (AccountingProvider role) ----
    def fetch_references(self, access_token: str, realm_id: str) -> list[ReferenceDTO]:
        """Query QBO for every reference type Feature 1 needs and translate to
        provider-neutral ReferenceDTOs."""
        refs: list[ReferenceDTO] = []
        refs += self._query(access_token, realm_id, "Vendor", self._vendor)
        refs += self._query(access_token, realm_id, "Customer", self._customer)
        refs += self._query(access_token, realm_id, "TaxCode", self._taxcode)
        refs += self._query(access_token, realm_id, "Account", self._account)
        return refs

    def _query(self, access_token, realm_id, entity, mapper):
        url = f"{settings.QBO_API_BASE}/v3/company/{realm_id}/query"
        resp = requests.get(
            url,
            params={"query": f"select * from {entity} maxresults 1000",
                    "minorversion": settings.QBO_MINOR_VERSION},
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        rows = resp.json().get("QueryResponse", {}).get(entity, [])
        out = []
        for row in rows:
            dto = mapper(row)
            if dto:
                out.append(dto)
        return out

    @staticmethod
    def _meta_updated(row):
        return parse_datetime((row.get("MetaData") or {}).get("LastUpdatedTime", "") or "")

    def _vendor(self, row):
        return ReferenceDTO(AccountingRefType.VENDOR, row["Id"], row.get("DisplayName", ""),
                            bool(row.get("Active", True)), row.get("SyncToken", ""),
                            self._meta_updated(row))

    def _customer(self, row):
        return ReferenceDTO(AccountingRefType.CUSTOMER, row["Id"], row.get("DisplayName", ""),
                            bool(row.get("Active", True)), row.get("SyncToken", ""),
                            self._meta_updated(row))

    def _taxcode(self, row):
        return ReferenceDTO(AccountingRefType.TAX_CODE, row["Id"], row.get("Name", ""),
                            bool(row.get("Active", True)), row.get("SyncToken", ""),
                            self._meta_updated(row))

    def _account(self, row):
        # One Account row becomes either a PAYMENT_ACCOUNT (bank/card) or an
        # ACCOUNT (expense target). data carries the QBO AccountType so the
        # posting layer can reason about it later without a re-fetch.
        etype = (AccountingRefType.PAYMENT_ACCOUNT
                 if row.get("AccountType") in _PAYMENT_ACCOUNT_TYPES
                 else AccountingRefType.ACCOUNT)
        return ReferenceDTO(etype, row["Id"], row.get("Name", ""),
                            bool(row.get("Active", True)), row.get("SyncToken", ""),
                            self._meta_updated(row),
                            data={"account_type": row.get("AccountType", "")})