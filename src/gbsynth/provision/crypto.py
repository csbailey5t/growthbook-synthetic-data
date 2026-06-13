"""crypto-js-compatible AES, matching GrowthBook's data-source credential encryption.

GrowthBook encrypts data-source params with `AES.encrypt(json, ENCRYPTION_KEY)` from
crypto-js (services/datasource.ts). That is OpenSSL passphrase mode: a random 8-byte salt,
key+IV derived via the MD5 EVP_BytesToKey KDF, AES-256-CBC, output as base64 of
`Salted__` + salt + ciphertext. Reproducing it exactly lets bootstrap.py seed credentials
the running app can decrypt. Validated against a real GrowthBook-encrypted blob.
"""

from __future__ import annotations

import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

_SALT_MAGIC = b"Salted__"
_KEY_LEN = 32  # AES-256
_IV_LEN = 16
_BLOCK = 16


def _evp_bytes_to_key(passphrase: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """OpenSSL EVP_BytesToKey with MD5 (what crypto-js uses for passphrase mode)."""
    data = b""
    prev = b""
    while len(data) < _KEY_LEN + _IV_LEN:
        prev = hashlib.md5(prev + passphrase + salt).digest()
        data += prev
    return data[:_KEY_LEN], data[_KEY_LEN : _KEY_LEN + _IV_LEN]


def _pkcs7_pad(data: bytes) -> bytes:
    pad = _BLOCK - (len(data) % _BLOCK)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    return data[: -data[-1]]


def encrypt(plaintext: str, passphrase: str) -> str:
    salt = get_random_bytes(8)
    key, iv = _evp_bytes_to_key(passphrase.encode(), salt)
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(_pkcs7_pad(plaintext.encode()))
    return base64.b64encode(_SALT_MAGIC + salt + ct).decode()


def decrypt(blob_b64: str, passphrase: str) -> str:
    raw = base64.b64decode(blob_b64)
    if raw[:8] != _SALT_MAGIC:
        raise ValueError("not an OpenSSL 'Salted__' blob")
    salt, ct = raw[8:16], raw[16:]
    key, iv = _evp_bytes_to_key(passphrase.encode(), salt)
    return _pkcs7_unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ct)).decode()
