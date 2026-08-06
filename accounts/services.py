import copy
import json
import secrets

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from accounts.models import Company, IntegrationConnection
from accounts.providers import get_provider
from accounts.providers.base import OAuthTokens
from common.crypto import get_token_cipher
from common.enums import AuditActorType, AuditEventType, ConnectionStatus
from operations.services import record_event


BC_TAX_CODES = [
    {"qbo_tax_code_id": "", "label": "GST", "rate":"0.05","recoverable":True},
    {"qbo_tax_code_id": "", "label": "PST", "rate":"0.07","recoverable":False},
]

EDITABLE_CONFIG_FIELDS = {"name","currency","timezone","auto_post_enabled"}


def seed_bc_tax_codes() -> list[dict]:
    return copy.deepcopy(BC_TAX_CODES)

@transaction.atomic
def create_company(*, name, currency="CAD", timezone="America/Vancouver") -> Company:
    return Company.objects.create(name=name,currency=currency,timezone=timezone,tax_codes=seed_bc_tax_codes())

@transaction.atomic
def update_company_config(*,company:Company,actor,changes:dict)-> Company:
    applied ={}
    for f, new in changes.items():
        if f not in EDITABLE_CONFIG_FIELDS:
            continue
        old = getattr(company,f)
        if old != new:
            setattr(company,f,new)
            applied[f] = {"from":old,"to":new}
    if applied:
        company.save(update_fields=[*applied.keys(), "updated_at"])
        record_event(company=company,event_type=AuditEventType.CONFIG_UPDATED,aggregate_type="Company",aggregate_id=company.id,actor_type=AuditActorType.USER, actor_id=actor.id,payload={"changes":applied})
    return company


def _store_tokens(conn: IntegrationConnection, tokens:OAuthTokens) -> None:
    cipher = get_token_cipher()
    blob = json.dumps({"access_token":tokens.access_token,"refresh_token":tokens.refresh_token, "scope": tokens.scope, "token_type": tokens.token_type})
    conn.encrypted_secret_ref = cipher.encrypt(blob)
    conn.token_expires_at = tokens.expires_at


def _load_tokens(conn: IntegrationConnection) -> dict:
    return json.loads(get_token_cipher().decrypt(conn.encrypted_secret_ref))

def get_valid_access_token(conn: IntegrationConnection) -> str:
    data = _load_tokens(conn)
    expiring = conn.token_expires_at and conn.token_expires_at <= timezone.now() + timezone.timedelta(seconds=60)
    if expiring:
        new = get_provider(conn.provider).refresh(data["refresh_token"])
        _store_tokens(conn, new)
        conn.status = ConnectionStatus.CONNECTED
        conn.save(update_fields=["encrypted_secret_ref", "token_expires_at","status","updated_at"])
        return new.access_token
    return data["access_token"]


_STATE_TTL = 600

def start_oauth(*,company:Company,provider_key:str,actor) -> str:
    state = secrets.token_urlsafe(32)
    cache.set(f"oauth_state:{state}",
            {"company_id": str(company.id), "provider":provider_key,"user_id": str(actor.id)},
            timeout=_STATE_TTL)
    return get_provider(provider_key).authorization_url(state)


def consume_oauth_state(state:str) -> dict | None:
    meta = cache.get(f"oauth_state:{state}")
    if meta:
        cache.delete(f"oauth_state:{state}")
    return meta

@transaction.atomic
def complete_oauth(*, company:Company,provider_key: str,code:str,realm_id:str | None, actor) -> IntegrationConnection:
    provider = get_provider(provider_key)
    tokens = provider.exchange_code(code)
    external_id = provider.identify(tokens,realm_id=realm_id)

    conn, _ = IntegrationConnection.objects.get_or_create(company=company,
                                                          provider=provider_key,defaults={"external_account_id":external_id},
                                                          )
    conn.external_account_id= external_id
    conn.scopes=(tokens.scope or "").split()
    conn.status = ConnectionStatus.CONNECTED
    conn.connected_at = timezone.now()
    conn.disconnected_at = None
    conn.last_error = {}
    _store_tokens(conn,tokens)
    conn.save()

    record_event(company=company,event_type=AuditEventType.INTEGRATION_CONNECTED,aggregate_type="IntegrationConnection",aggregate_id=conn.id,actor_type=AuditActorType.USER,actor_id=actor.id,payload={"provider": provider_key, "external_account_id":external_id})
    return conn

@transaction.atomic
def revoke_integration(*, conn: IntegrationConnection,actor) -> IntegrationConnection:

    try:
        provider = get_provider(conn.provider)
        provider.revoke(_load_tokens(conn).get("refresh_token",""))
    except Exception:
        pass

    conn.status = ConnectionStatus.DISCONNECTED
    conn.disconnected_at = timezone.now()
    conn.encrypted_secret_ref = ""
    conn.token_expires_at = None
    conn.save(update_fields=["status","disconnected_at","encrypted_secret_ref","token_expires_at","updated_at"])
    record_event(company=conn.company,event_type=AuditEventType.INTEGRATION_REVOKED,aggregate_type="IntegrationConnection",aggregate_id=conn.id,actor_type=AuditActorType.USER,actor_id=actor.id,payload={"provider":conn.provider})
    return conn
