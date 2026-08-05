import requests
from django.conf import settings
from accounts.providers.base import OAuthTokens,OAuth2Provider,REQUEST_TIMEOUT
from common.enums import Provider


class GoogleProvider(OAuth2Provider):
    provider_key = Provider.GMAIL
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    token_uses_basic_auth = False

    def __init__(self):
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI
        self.scopes = settings.GOOGLE_OAUTH_SCOPES


    def extra_authorize_params(self) -> dict:
        return {"access_type":"offline","prompt":"consent","include_granted_scopes":"true"}

    def identify(self, tokens: OAuthTokens, *, realm_id = None) -> str:

        resp = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["emailAddress"]

    def revoke(self, refresh_token: str) -> None:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": refresh_token},
            timeout=REQUEST_TIMEOUT,
        )

