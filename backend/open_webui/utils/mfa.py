# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Security primitives for local TOTP multi-factor authentication."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.models.mfa import (
    claim_mfa_totp_step,
    consume_mfa_recovery_hash,
    get_mfa_credential,
    list_mfa_credentials,
    update_mfa_credential,
)


CHALLENGE_TTL_SECONDS = 300
RECOVERY_CODE_COUNT = 10


class MfaError(Exception):
    pass


def _fernet() -> Fernet:
    configured = os.environ.get("MFA_ENCRYPTION_KEY", "").strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, UnicodeError) as error:
            raise MfaError("MFA_ENCRYPTION_KEY is not a valid Fernet key") from error
    derived = hashlib.sha256(("openatelier-mfa:" + WEBUI_SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode("ascii")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError) as error:
        raise MfaError("stored MFA secret cannot be decrypted") from error


def validate_mfa_storage():
    """Fail startup when the configured key cannot decrypt retained secrets."""
    for credential in list_mfa_credentials():
        if credential.secret:
            decrypt_secret(credential.secret)
        if credential.pending_secret:
            decrypt_secret(credential.pending_secret)


def _challenge_key() -> bytes:
    return hashlib.sha256(("openatelier-mfa-challenge:" + WEBUI_SECRET_KEY).encode()).digest()


def issue_challenge(user_id: str, step: str) -> dict:
    if step not in {"enroll", "verify", "recover"}:
        raise MfaError("invalid MFA challenge step")
    nonce = secrets.token_urlsafe(6)
    issued_at = int(time.time())
    payload = {"u": user_id.replace("-", ""), "s": step, "n": nonce, "i": issued_at}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.new(_challenge_key(), encoded.encode(), hashlib.sha256).digest()[:16]).decode().rstrip("=")
    token = f"{encoded}.{signature}"
    update_mfa_credential(
        user_id,
        challenge_nonce=nonce,
        challenge_step=step,
        challenge_expires_at=issued_at + CHALLENGE_TTL_SECONDS,
    )
    return {"next_step": step, "challenge_token": token, "expires_in": CHALLENGE_TTL_SECONDS}


def validate_challenge(token: str, expected_step: str | None = None):
    try:
        encoded, signature = token.split(".", 1)
        expected = base64.urlsafe_b64encode(hmac.new(_challenge_key(), encoded.encode(), hashlib.sha256).digest()[:16]).decode().rstrip("=")
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4)))
        compact_user_id = payload["u"]
        user_id = f"{compact_user_id[:8]}-{compact_user_id[8:12]}-{compact_user_id[12:16]}-{compact_user_id[16:20]}-{compact_user_id[20:]}"
        credential = get_mfa_credential(user_id)
        if (
            credential is None
            or credential.challenge_nonce != payload["n"]
            or credential.challenge_step != payload["s"]
            or credential.challenge_expires_at is None
            or credential.challenge_expires_at < int(time.time())
            or (expected_step is not None and payload["s"] != expected_step)
        ):
            raise ValueError
        return user_id, credential
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise MfaError("expired MFA challenge") from error


def consume_challenge(user_id: str):
    update_mfa_credential(
        user_id,
        challenge_nonce=None,
        challenge_step=None,
        challenge_expires_at=None,
    )


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_value(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000
    return f"{value:06d}"


def verify_totp(user_id: str, secret: str, code: str) -> bool:
    credential = get_mfa_credential(user_id)
    if credential is None:
        return False
    current = int(time.time()) // 30
    for step in (current - 1, current, current + 1):
        if step <= credential.last_totp_step:
            continue
        if hmac.compare_digest(_totp_value(secret, step), code):
            return claim_mfa_totp_step(user_id, step)
    return False


def _recovery_hash(user_id: str, code: str) -> str:
    normalized = code.strip().lower().replace("-", "")
    return hmac.new(_challenge_key(), f"{user_id}:{normalized}".encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(user_id: str) -> tuple[list[str], list[str]]:
    codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(2)}" for _ in range(RECOVERY_CODE_COUNT)]
    return codes, [_recovery_hash(user_id, code) for code in codes]


def consume_recovery_code(user_id: str, code: str) -> bool:
    candidate = _recovery_hash(user_id, code)
    return consume_mfa_recovery_hash(user_id, candidate)


def qr_code_data_uri(email: str, issuer: str, secret: str) -> str:
    uri = f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    import cv2

    image = cv2.QRCodeEncoder_create().encode(uri)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise MfaError("failed to encode MFA QR code")
    return "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")
