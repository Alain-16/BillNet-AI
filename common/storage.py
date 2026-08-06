import hashlib
import os
import shutil
from pathlib import Path
from typing import Protocol

from django.conf import settings


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, *, content_type: str = "") -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


class LocalFileSystemStorage:


    def __init__(self, root:str | Path):

        self.root= Path(root)
        self.root.mkdir(parents=True,exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("Storage key escapes the storage root.")
        return p

    def put(self,key:str, data:bytes, *, content_type: str= "") -> str:
        p= self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key:str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:

        self._path(key).unlink(missing_ok=True)

def get_object_storage() -> ObjectStorage:
    return LocalFileSystemStorage(settings.OBJECT_STORAGE_ROOT)

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def build_object_key(company_id, sha256: str, extension: str) -> str:
    return f"companies/{company_id}/documents/{sha256}{extension}"





