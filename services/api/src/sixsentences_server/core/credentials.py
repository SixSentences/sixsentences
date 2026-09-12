"""Authenticated encryption for persisted third-party connector credentials."""

import base64
import binascii
import hashlib
import hmac
import os
import struct
import time

from Cryptodome.Cipher import AES

from sixsentences_server.config import get_settings


class CredentialError(RuntimeError):
    pass


def _key() -> tuple[bytes, bytes]:
    encoded = get_settings().connector_encryption_key.strip().encode()
    if not encoded:
        raise CredentialError("persistent connectors are not configured on this server")
    try:
        key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialError("the connector encryption key is invalid") from exc
    if len(key) != 32:
        raise CredentialError("the connector encryption key is invalid")
    return key[:16], key[16:]


def _pad(data: bytes) -> bytes:
    padding_length = AES.block_size - (len(data) % AES.block_size)
    return data + bytes([padding_length]) * padding_length


def _unpad(data: bytes) -> bytes:
    if not data:
        raise CredentialError("the stored connector credential cannot be decrypted")
    padding_length = data[-1]
    if padding_length < 1 or padding_length > AES.block_size:
        raise CredentialError("the stored connector credential cannot be decrypted")
    if not hmac.compare_digest(data[-padding_length:], bytes([padding_length]) * padding_length):
        raise CredentialError("the stored connector credential cannot be decrypted")
    return data[:-padding_length]


def encrypt_credential(raw: str) -> str:
    if not raw:
        raise CredentialError("an empty credential cannot be stored")
    signing_key, encryption_key = _key()
    iv = os.urandom(AES.block_size)
    ciphertext = AES.new(encryption_key, AES.MODE_CBC, iv).encrypt(_pad(raw.encode()))
    payload = b"\x80" + struct.pack(">Q", int(time.time())) + iv + ciphertext
    signature = hmac.new(signing_key, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode()


def decrypt_credential(ciphertext: str) -> str:
    signing_key, encryption_key = _key()
    try:
        token = base64.b64decode(ciphertext.encode(), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialError("the stored connector credential cannot be decrypted") from exc
    if len(token) < 73 or token[0] != 0x80 or (len(token) - 57) % AES.block_size != 0:
        raise CredentialError("the stored connector credential cannot be decrypted")
    payload, signature = token[:-32], token[-32:]
    expected_signature = hmac.new(signing_key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise CredentialError("the stored connector credential cannot be decrypted")
    iv = token[9:25]
    encrypted = token[25:-32]
    try:
        plaintext = AES.new(encryption_key, AES.MODE_CBC, iv).decrypt(encrypted)
        return _unpad(plaintext).decode()
    except (UnicodeDecodeError, ValueError) as exc:
        raise CredentialError("the stored connector credential cannot be decrypted") from exc
