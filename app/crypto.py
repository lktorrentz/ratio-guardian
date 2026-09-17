"""Cifratura a riposo dei segreti salvati nel DB.

docs/schema.sql marca `tracker.api_token` e `torrent_client.password` come
"cifrato a riposo". Usato dal TypeDecorator EncryptedString in app/models.py.
"""

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


class SecretKeyMissingError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.environ.get("APP_SECRET_KEY")
    if not key:
        raise SecretKeyMissingError(
            "APP_SECRET_KEY non impostata: necessaria per cifrare/decifrare "
            "credenziali tracker/torrent_client. Vedi .env.example."
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Impossibile decifrare il valore: chiave errata o dato corrotto") from exc
