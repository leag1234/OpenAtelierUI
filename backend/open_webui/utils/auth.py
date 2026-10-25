import logging
import uuid
import jwt
import base64
import hmac
import hashlib
import requests
import os
from pathlib import Path


from datetime import datetime, timedelta
import pytz
from pytz import UTC
from typing import Optional, Union, List, Dict

from open_webui.models.users import Users

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import (
    WEBUI_SECRET_KEY,
    TRUSTED_SIGNATURE_KEY,
    STATIC_DIR,
    SRC_LOG_LEVELS,
    DATABASE_URL,
)

from fastapi import BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from open_webui.utils.session_epochs import (
    MAX_EPOCH_BYTES,
    MAX_EPOCH_CHARS,
    SessionEpochError,
    issue_epoch,
    rotate_epoch,
    validate_epoch,
)


logging.getLogger("passlib").setLevel(logging.ERROR)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["OAUTH"])

SESSION_SECRET = WEBUI_SECRET_KEY
ALGORITHM = "HS256"
_VALIDATED_SESSION_STATE_MARKER = object()
_VALIDATED_SESSION_STATE_ATTRIBUTE = "_openatelier_validated_session_epoch"

##############
# Auth Utils
##############


def verify_signature(payload: str, signature: str) -> bool:
    """
    Verifies the HMAC signature of the received payload.
    """
    try:
        expected_signature = base64.b64encode(
            hmac.new(TRUSTED_SIGNATURE_KEY, payload.encode(), hashlib.sha256).digest()
        ).decode()

        # Compare securely to prevent timing attacks
        return hmac.compare_digest(expected_signature, signature)

    except Exception:
        return False


def override_static(path: str, content: str):
    # Ensure path is safe
    if "/" in path or ".." in path:
        log.error(f"Invalid path: {path}")
        return

    file_path = os.path.join(STATIC_DIR, path)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    with open(file_path, "wb") as f:
        f.write(base64.b64decode(content))  # Convert Base64 back to raw binary


def get_license_data(app, key):
    if key:
        try:
            res = requests.post(
                "https://api.openwebui.com/api/v1/license/",
                json={"key": key, "version": "1"},
                timeout=5,
            )

            if getattr(res, "ok", False):
                payload = getattr(res, "json", lambda: {})()
                for k, v in payload.items():
                    if k == "resources":
                        for p, c in v.items():
                            globals().get("override_static", lambda a, b: None)(p, c)
                    elif k == "count":
                        setattr(app.state, "USER_COUNT", v)
                    elif k == "name":
                        setattr(app.state, "WEBUI_NAME", v)
                    elif k == "metadata":
                        setattr(app.state, "LICENSE_METADATA", v)
                return True
            else:
                log.error(
                    f"License: retrieval issue: {getattr(res, 'text', 'unknown error')}"
                )
        except Exception as ex:
            log.exception(f"License: Uncaught Exception: {ex}")
    return False


bearer_security = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _session_epochs_enabled() -> bool:
    return os.environ.get("OPENATELIER_SESSION_EPOCHS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _session_epoch_database_path() -> Path:
    try:
        if not isinstance(DATABASE_URL, str):
            raise ValueError("unsupported SQLite URL")
        driver, separator, database_path = DATABASE_URL.partition(":///")
        if not separator or (driver != "sqlite" and not driver.startswith("sqlite+")):
            raise ValueError("unsupported SQLite URL")
        if (
            not database_path
            or database_path == ":memory:"
            or "?" in database_path
            or "#" in database_path
        ):
            raise ValueError("unsupported SQLite URL")
        path = Path(database_path)
        if not path.is_absolute():
            raise ValueError("SQLite path must be absolute")
        return path
    except Exception as error:
        raise SessionEpochError(
            "unsupported_database_url",
            "session epochs require an absolute file-backed SQLite DATABASE_URL",
        ) from error


def _session_state_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Session state unavailable")


def _valid_session_stamp_claim(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > MAX_EPOCH_CHARS:
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= MAX_EPOCH_BYTES
    except UnicodeEncodeError:
        return False


def get_validated_session_epoch(request: Request, user_id: str) -> Optional[str]:
    """Return the epoch validated by this request's JWT dependency, if any."""
    state = getattr(request, "state", None)
    validated = getattr(state, _VALIDATED_SESSION_STATE_ATTRIBUTE, None)
    if (
        isinstance(validated, tuple)
        and len(validated) == 3
        and validated[0] is _VALIDATED_SESSION_STATE_MARKER
        and validated[1] == user_id
        and _valid_session_stamp_claim(validated[2])
    ):
        return validated[2]
    return None


def revoke_user_session_epoch(user_id: str) -> None:
    """Rotate one existing account epoch in the opt-in copied-profile experiment."""
    if not _session_epochs_enabled():
        raise _session_state_unavailable()
    try:
        rotate_epoch(_session_epoch_database_path(), user_id)
    except SessionEpochError as error:
        if error.code == "user_not_found":
            raise HTTPException(status_code=404, detail="User not found.") from error
        raise _session_state_unavailable() from error
    except Exception as error:
        raise _session_state_unavailable() from error


def verify_password(plain_password, hashed_password):
    return (
        pwd_context.verify(plain_password, hashed_password) if hashed_password else None
    )


def get_password_hash(password):
    return pwd_context.hash(password)


def create_token(
    data: dict,
    expires_delta: Union[timedelta, None] = None,
    *,
    expected_session_stamp: Optional[str] = None,
) -> str:
    payload = data.copy()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
        payload.update({"exp": expire})

    if _session_epochs_enabled():
        user_id = payload.get("id")
        if not isinstance(user_id, str) or not user_id or len(user_id) > 256:
            raise _session_state_unavailable()
        try:
            database_path = _session_epoch_database_path()
            if expected_session_stamp is None:
                payload["session_stamp"] = issue_epoch(database_path, user_id)
            else:
                payload["session_stamp"] = issue_epoch(
                    database_path, user_id, expected_epoch=expected_session_stamp
                )
        except SessionEpochError as error:
            if error.code == "stale_session":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                ) from error
            raise _session_state_unavailable() from error
        except Exception as error:
            raise _session_state_unavailable() from error
        payload["jti"] = str(uuid.uuid4())

    encoded_jwt = jwt.encode(payload, SESSION_SECRET, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    try:
        decoded = jwt.decode(token, SESSION_SECRET, algorithms=[ALGORITHM])
        return decoded
    except Exception:
        return None


def extract_token_from_auth_header(auth_header: str):
    return auth_header[len("Bearer ") :]


def create_api_key():
    key = str(uuid.uuid4()).replace("-", "")
    return f"sk-{key}"


def get_http_authorization_cred(auth_header: Optional[str]):
    if not auth_header:
        return None
    try:
        scheme, credentials = auth_header.split(" ")
        return HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials)
    except Exception:
        return None


def get_current_user(
    request: Request,
    background_tasks: BackgroundTasks,
    auth_token: HTTPAuthorizationCredentials = Depends(bearer_security),
):
    if _session_epochs_enabled():
        state = getattr(request, "state", None)
        if state is not None:
            setattr(state, _VALIDATED_SESSION_STATE_ATTRIBUTE, None)

    token = None

    if auth_token is not None:
        token = auth_token.credentials

    if token is None and "token" in request.cookies:
        token = request.cookies.get("token")

    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # auth by api key
    if token.startswith("sk-"):
        if not request.state.enable_api_key:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.API_KEY_NOT_ALLOWED
            )

        if request.app.state.config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS:
            allowed_paths = [
                path.strip()
                for path in str(
                    request.app.state.config.API_KEY_ALLOWED_ENDPOINTS
                ).split(",")
            ]

            # Check if the request path matches any allowed endpoint.
            if not any(
                request.url.path == allowed
                or request.url.path.startswith(allowed + "/")
                for allowed in allowed_paths
            ):
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.API_KEY_NOT_ALLOWED
                )

        return get_current_user_by_api_key(token)

    # auth by jwt token
    try:
        data = decode_token(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if data is not None and "id" in data:
        if _session_epochs_enabled():
            user_id = data.get("id")
            stamp_claim = data.get("session_stamp")
            if (
                not isinstance(user_id, str)
                or not user_id
                or len(user_id) > 256
                or not _valid_session_stamp_claim(stamp_claim)
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                )
            try:
                stamp_is_current = validate_epoch(
                    _session_epoch_database_path(), user_id, stamp_claim
                )
            except SessionEpochError as error:
                if error.code == "user_not_found":
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid token",
                    ) from error
                raise _session_state_unavailable() from error
            except Exception as error:
                raise _session_state_unavailable() from error
            if not stamp_is_current:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                )
        user = Users.get_user_by_id(data["id"])
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Invalid token"
                    if _session_epochs_enabled()
                    else ERROR_MESSAGES.INVALID_TOKEN
                ),
            )
        else:
            # Refresh the user's last active timestamp asynchronously
            # to prevent blocking the request
            if background_tasks:
                background_tasks.add_task(Users.update_user_last_active_by_id, user.id)
            if _session_epochs_enabled():
                setattr(
                    request.state,
                    _VALIDATED_SESSION_STATE_ATTRIBUTE,
                    (_VALIDATED_SESSION_STATE_MARKER, data["id"], stamp_claim),
                )
        return user
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


def get_current_user_by_api_key(api_key: str):
    user = Users.get_user_by_api_key(api_key)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.INVALID_TOKEN,
        )
    else:
        Users.update_user_last_active_by_id(user.id)

    return user


def get_verified_user(user=Depends(get_current_user)):
    if user.role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user


def get_admin_user(user=Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return user
