# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Persistent, encrypted multi-factor authentication state."""

import json
import time
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Column, String, Text

from open_webui.internal.db import Base, get_db


class MfaCredential(Base):
    __tablename__ = "mfa_credential"

    user_id = Column(String, primary_key=True)
    secret = Column(Text, nullable=True)
    pending_secret = Column(Text, nullable=True)
    recovery_codes = Column(Text, nullable=False, default="[]")
    last_totp_step = Column(BigInteger, nullable=False, default=-1)
    challenge_nonce = Column(String, nullable=True)
    challenge_step = Column(String, nullable=True)
    challenge_expires_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=False)


class MfaCredentialModel(BaseModel):
    user_id: str
    secret: Optional[str] = None
    pending_secret: Optional[str] = None
    recovery_codes: list[str] = Field(default_factory=list)
    last_totp_step: int = -1
    challenge_nonce: Optional[str] = None
    challenge_step: Optional[str] = None
    challenge_expires_at: Optional[int] = None
    updated_at: int


def _model(row: Optional[MfaCredential]) -> Optional[MfaCredentialModel]:
    if row is None:
        return None
    try:
        recovery_codes = json.loads(row.recovery_codes or "[]")
    except (TypeError, ValueError):
        recovery_codes = []
    return MfaCredentialModel(
        user_id=row.user_id,
        secret=row.secret,
        pending_secret=row.pending_secret,
        recovery_codes=recovery_codes if isinstance(recovery_codes, list) else [],
        last_totp_step=row.last_totp_step,
        challenge_nonce=row.challenge_nonce,
        challenge_step=row.challenge_step,
        challenge_expires_at=row.challenge_expires_at,
        updated_at=row.updated_at,
    )


def get_mfa_credential(user_id: str) -> Optional[MfaCredentialModel]:
    with get_db() as db:
        return _model(db.query(MfaCredential).filter_by(user_id=user_id).first())


def list_mfa_credentials() -> list[MfaCredentialModel]:
    with get_db() as db:
        return [_model(row) for row in db.query(MfaCredential).all()]


def update_mfa_credential(user_id: str, **values) -> MfaCredentialModel:
    allowed = {
        "secret", "pending_secret", "recovery_codes", "last_totp_step",
        "challenge_nonce", "challenge_step", "challenge_expires_at",
    }
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"unsupported MFA fields: {sorted(unknown)}")
    with get_db() as db:
        row = db.query(MfaCredential).filter_by(user_id=user_id).first()
        if row is None:
            row = MfaCredential(
                user_id=user_id,
                recovery_codes="[]",
                last_totp_step=-1,
                updated_at=int(time.time()),
            )
            db.add(row)
        for key, value in values.items():
            setattr(row, key, json.dumps(value) if key == "recovery_codes" else value)
        row.updated_at = int(time.time())
        db.commit()
        db.refresh(row)
        return _model(row)


def claim_mfa_totp_step(user_id: str, step: int) -> bool:
    """Atomically prevent concurrent or repeated use of a TOTP time step."""
    with get_db() as db:
        updated = (
            db.query(MfaCredential)
            .filter(
                MfaCredential.user_id == user_id,
                MfaCredential.last_totp_step < step,
            )
            .update(
                {"last_totp_step": step, "updated_at": int(time.time())},
                synchronize_session=False,
            )
        )
        db.commit()
        return updated == 1


def consume_mfa_recovery_hash(user_id: str, candidate: str) -> bool:
    """Atomically consume one recovery-code hash using compare-and-swap."""
    with get_db() as db:
        row = db.query(MfaCredential).filter_by(user_id=user_id).first()
        if row is None:
            return False
        try:
            original = json.loads(row.recovery_codes or "[]")
        except (TypeError, ValueError):
            return False
        if not isinstance(original, list) or candidate not in original:
            return False
        remaining = list(original)
        remaining.remove(candidate)
        updated = (
            db.query(MfaCredential)
            .filter(
                MfaCredential.user_id == user_id,
                MfaCredential.recovery_codes == row.recovery_codes,
            )
            .update(
                {
                    "recovery_codes": json.dumps(remaining),
                    "updated_at": int(time.time()),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return updated == 1
