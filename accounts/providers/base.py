from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass,field
from datetime import datetime,timedelta

import requests
from django.utils import timezone
from decimal import Decimal
from datetime import date


REQUEST_TIMEOUT = 30

@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: str = ""
    token_type: str = "Bearer"


@dataclass
class ReferenceDTO:
    entity_type: str
    external_id: str
    name: str
    active: bool = True
    sync_token: str = ""
    updated_at: datetime | None = None
    data: dict = field(default_factory=dict)


class OAuth2Provider(ABC):
    provider_key: str
    auth_url: str
    token_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    token_uses_basic_auth: bool = False


    def authorization_url(self,state: str) -> str:
        params ={
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": self.scopes,
            "state": state,
            **self.extra_authorize_params(),
        }
        return f"{self.auth_url}?{requests.compat.urlencode(params)}"

    def extra_authorize_params(self) -> dict:
        return {}

    def _token_request(self,data:dict) -> OAuthTokens:
        auth = None
        if self.token_uses_basic_auth:
            auth= (self.client_id,self.client_secret)
        else:
            data = {**data, "client_id":self.client_id,"client_secret":self.client_secret}
        resp = requests.post(
            self.token_url, data=data , auth=auth,
            headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        j = resp.json()

        return OAuthTokens(
            access_token=j["access_token"],
            refresh_token=j.get("refresh_token", ""),
            expires_at=timezone.now() + timedelta(seconds=int(j.get("expires_in",3600))),
            scope=j.get("scope",self.scopes),
            token_type=j.get("token_type","Bearer"),
        )

    def exchange_code(self, code: str) -> OAuthTokens:
        return self._token_request({
            "grant_type": "authorization_code",
            "code":code,
            "redirect_uri": self.redirect_uri,
        })

    def refresh(self,refresh_token: str) -> OAuthTokens:
        tokens = self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })

        if not tokens.refresh_token:
            tokens.refresh_token = refresh_token

        return tokens

    @abstractmethod
    def identify(self, tokens: OAuthTokens, *, realm_id: str | None = None) -> str:
        """Return the external account id (Gmail address / QBO realmId)."""

    @abstractmethod
    def revoke(self, refresh_token: str) -> None: ...


@dataclass
class PurchaseLineDTO:
        line_number: int
        amount: Decimal | None = None
        description: str = ""
        detail_type: str = ""
        qbo_line_id: str = ""
        expense_account_id: str = ""
        expense_account_name: str = ""
        customer_id: str = ""
        customer_name: str = ""
        class_id: str = ""
        class_name: str = ""
        tax_code_id: str = ""
        item_id: str = ""
        item_name: str = ""
        raw: dict = field(default_factory=dict)

@dataclass
class PurchaseDTO:
    external_id: str
    sync_token: str = ""
    transaction_date: date | None = None
    total_amount: Decimal | None = None
    total_tax: Decimal | None = None
    currency: str = ""
    vendor_id: str = ""
    vendor_name: str = ""
    vendor_type: str = ""
    payment_account_id: str = ""
    payment_account_name: str = ""
    payment_type: str = ""
    doc_number: str = ""
    memo: str = ""
    is_credit: bool = False
    is_voided: bool = False
    updated_at: datetime | None = None
    lines: list[PurchaseLineDTO] = field(default_factory=list)
    raw: dict = field(default_factory=dict)