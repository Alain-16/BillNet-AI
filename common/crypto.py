from __future__ import annotations

from typing import Protocol
from cryptography.fernet import Fernet
from django.contrib import settings


class TokenCipher(Protocol):

    def encrypt(self,plaintext:str) -> str: ...
    def decrypt(self,ciphertext:str) -> str: ...

class FernetCipher:

    def __init__(self,key:str):
        self._f = Fernet(key.encode() if isinstance(key, str) else key)


    def encrypt(self,plaintext:str) -> str:
        return self._f.encrypt(plaintext.encode()).decode()
    def decrypt(self,ciphertext: str) -> str:
        return self._f.decrypt(ciphertext.encode()).decode()


def get_token_cipher() -> TokenCipher:
    return FernetCipher(settings.TOKEN_ENCRYPTION_KEY) 