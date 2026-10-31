# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Default-off access to profile columns prepared by the bounded importer."""

import datetime
import os


PROFILE_FIELDS_ENV = "OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS"
PROFILE_FIELD_TYPES = {
    "bio": "TEXT",
    "gender": "TEXT",
    "date_of_birth": "DATE",
}
MAX_BIO_UTF8_BYTES = 8192
MAX_GENDER_UTF8_BYTES = 128


class ImportedProfileError(RuntimeError):
    """A prepared-profile failure that must not be treated as bad credentials."""


def user_profile_fields_enabled():
    value = os.environ.get(PROFILE_FIELDS_ENV, "")
    if value in {"", "0"}:
        return False
    if value == "1":
        return True
    raise ImportedProfileError("OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS must be exactly 1 or unset")


def _text(statement):
    from sqlalchemy import text

    return text(statement)


def _validate_profile_values(profile):
    for key, maximum in (("bio", MAX_BIO_UTF8_BYTES), ("gender", MAX_GENDER_UTF8_BYTES)):
        value = profile.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ImportedProfileError("prepared user profile contains an unsupported value")
            try:
                if len(value.encode("utf-8", errors="strict")) > maximum:
                    raise ImportedProfileError("prepared user profile contains an oversized value")
            except UnicodeError:
                raise ImportedProfileError("prepared user profile contains an invalid text value") from None
    value = profile.get("date_of_birth")
    if value is not None:
        if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
            pass
        elif isinstance(value, str):
            try:
                parsed = datetime.date.fromisoformat(value)
            except ValueError:
                raise ImportedProfileError("prepared user profile contains an invalid date") from None
            if parsed.isoformat() != value:
                raise ImportedProfileError("prepared user profile contains an invalid date")
            profile["date_of_birth"] = parsed
        else:
            raise ImportedProfileError("prepared user profile contains an invalid date")
    return profile


def validate_prepared_user_profile_schema(engine=None):
    """Check the already-prepared SQLite copy; never create or repair columns."""
    if not user_profile_fields_enabled():
        return False
    if engine is None:
        from open_webui.internal.db import engine as current_engine

        engine = current_engine
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        raise ImportedProfileError("imported user profile fields require a prepared SQLite database")
    with engine.connect() as connection:
        rows = connection.exec_driver_sql('PRAGMA table_info("user")').fetchall()
    columns = {row[1]: row for row in rows}
    for field, expected_type in PROFILE_FIELD_TYPES.items():
        row = columns.get(field)
        if row is None or str(row[2]).upper() != expected_type or row[3] != 0:
            raise ImportedProfileError(
                "OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS=1 requires prepared nullable user profile columns"
            )
    return True


def read_user_profile_fields(session, user_id):
    try:
        row = session.execute(
            _text('SELECT bio,gender,date_of_birth FROM "user" WHERE id=:user_id'),
            {"user_id": user_id},
        ).first()
        if row is None:
            raise ImportedProfileError("prepared user profile row is unavailable")
        values = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
        return _validate_profile_values(values)
    except ImportedProfileError:
        raise
    except Exception as error:
        raise ImportedProfileError("prepared user profile data is unavailable") from None


def update_user_profile_fields(session, user_id, name, profile_image_url, bio, gender, date_of_birth):
    values = _validate_profile_values(
        {"bio": bio, "gender": gender, "date_of_birth": date_of_birth}
    )
    try:
        result = session.execute(
            _text(
                'UPDATE "user" SET name=:name,profile_image_url=:profile_image_url,'
                'bio=:bio,gender=:gender,date_of_birth=:date_of_birth WHERE id=:user_id'
            ),
            {
                "user_id": user_id,
                "name": name,
                "profile_image_url": profile_image_url,
                "bio": values["bio"],
                "gender": values["gender"],
                "date_of_birth": (
                    values["date_of_birth"].isoformat()
                    if values["date_of_birth"] is not None
                    else None
                ),
            },
        )
        if result.rowcount != 1:
            session.rollback()
            return False
        session.commit()
    except ImportedProfileError:
        session.rollback()
        raise
    except Exception as error:
        session.rollback()
        raise ImportedProfileError("prepared user profile update is unavailable") from None
    return True
