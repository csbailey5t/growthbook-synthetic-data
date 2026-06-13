"""crypto-js-compatibility tests for bootstrap credential encryption.

The known-answer vector was produced independently by OpenSSL's legacy mode
(`openssl enc -aes-256-cbc -md md5 -pass pass:test-passphrase -a`), which uses the same
salted MD5-KDF format as crypto-js. Decrypting it proves gbsynth matches GrowthBook's
encryption byte-for-byte — so seeded credentials are decryptable by the running app.
"""

from __future__ import annotations

from gbsynth.provision.crypto import decrypt, encrypt

# openssl enc -aes-256-cbc -md md5 -pass pass:test-passphrase -a  of  {"host":"db","port":5432}
_OPENSSL_VECTOR = "U2FsdGVkX1+LZ4q/cjSZFNa+3zxYzy6rPBItXvCQSpNfHZ5l1gC/k0k0Ng99xHBQ"


def test_decrypts_openssl_legacy_vector() -> None:
    assert decrypt(_OPENSSL_VECTOR, "test-passphrase") == '{"host":"db","port":5432}'


def test_round_trip() -> None:
    secret = '{"host":"postgres","password":"gbsynth"}'
    assert decrypt(encrypt(secret, "key-123"), "key-123") == secret


def test_each_encrypt_uses_a_fresh_salt() -> None:
    # Random salt => different ciphertext each call, both decrypting to the same plaintext.
    a, b = encrypt("same", "k"), encrypt("same", "k")
    assert a != b
    assert decrypt(a, "k") == decrypt(b, "k") == "same"
