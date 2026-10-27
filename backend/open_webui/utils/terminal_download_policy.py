# SPDX-License-Identifier: BSD-3-Clause
"""Pure policy for one configured, read-only terminal file download."""

import json
import re
from urllib.parse import quote, unquote_to_bytes, urlsplit


MAX_CONFIG_BYTES = 65536
MAX_CONNECTIONS = 16
MAX_URL_BYTES = 2048
MAX_KEY_BYTES = 4096
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CHAT = re.compile(r"^files/serve/home/user/requests/chatcmpl-([0-9a-f]{32})/(.+)$")
_ENCODING = re.compile(r"%(?![0-9A-Fa-f]{2})")
_GRANT_KEYS = {"principal_type", "principal_id", "permission"}


def _text_ok(value, maximum, *, nonempty=False, controls=False):
    if type(value) is not str or (nonempty and not value):
        return False
    try:
        if len(value.encode("utf-8")) > maximum:
            return False
    except UnicodeError:
        return False
    return not controls or not any(ord(char) < 32 or ord(char) == 127 for char in value)


class TerminalPolicyError(ValueError):
    def __init__(self):
        super().__init__("Terminal download is unavailable.")


class DownloadRequest:
    __slots__ = ("connection_id", "url", "headers", "path")

    def __init__(self, connection_id, url, headers, path):
        self.connection_id = connection_id
        self.url = url
        self.headers = dict(headers)
        self.path = path

    def __repr__(self):
        return "DownloadRequest(connection_id={!r}, url={!r}, path={!r})".format(
            self.connection_id, self.url, self.path
        )


def _reject(*_args, **_kwargs): raise TerminalPolicyError()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: _reject()
        result[key] = value
    return result


def _constant(_value): _reject()


def _bounded_json(value):
    if not _text_ok(value, MAX_CONFIG_BYTES, nonempty=True):
        _reject()
    try:
        parsed = json.loads(value, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _reject()
    return parsed


def _valid_url(value):
    if (not _text_ok(value, MAX_URL_BYTES, nonempty=True, controls=True)
            or any(char.isspace() for char in value) or "?" in value or "#" in value):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme in ("http", "https") and bool(parts.hostname) and parts.username is None
                and parts.password is None and parts.port != 0 and "[" not in parts.netloc
                and "]" not in parts.netloc and "\\" not in value and "%" not in parts.netloc)
    except ValueError: return False


def _grants(connection):
    config = connection["config"]
    if (set(config) != {"chat_uploads", "access_grants"}
            or config.get("chat_uploads") not in ("default", "filesystem")):
        _reject()
    grants = config.get("access_grants")
    if type(grants) is not list or len(grants) > 32:
        _reject()
    result = []
    seen = set()
    for grant in grants:
        if type(grant) is not dict or set(grant) != _GRANT_KEYS:
            _reject()
        principal_type = grant.get("principal_type")
        principal_id = grant.get("principal_id")
        permission = grant.get("permission")
        if principal_type != "user" or permission != "read": _reject()
        if (not _text_ok(principal_id, 256, nonempty=True, controls=True)
                or (principal_id != "*" and not principal_id.strip())):
            _reject()
        if principal_id in seen: _reject()
        seen.add(principal_id)
        result.append(principal_id)
    if "*" in seen and len(seen) != 1: _reject()
    return result


def parse_terminal_connections(raw):
    connections = _bounded_json(raw)
    if type(connections) is not list or len(connections) > MAX_CONNECTIONS:
        _reject()
    seen = set()
    validated = []
    fields = {"id", "url", "key", "auth_type", "enabled", "name", "config"}
    for connection in connections:
        if (type(connection) is not dict or not {"id", "url", "key", "auth_type", "enabled", "config"} <= set(connection)
                or set(connection) - fields): _reject()
        identity = connection["id"]
        key = connection["key"]
        config = connection["config"]
        if (not _text_ok(identity, 128, nonempty=True) or not _ID.fullmatch(identity)
                or identity in seen):
            _reject()
        if (not _text_ok(key, MAX_KEY_BYTES, nonempty=True, controls=True)
                or any(char.isspace() for char in key)):
            _reject()
        if (connection["auth_type"] != "bearer" or type(connection["enabled"]) is not bool
                or not _valid_url(connection["url"]) or type(config) is not dict):
            _reject()
        if "name" in connection and not _text_ok(connection["name"], 256, controls=True): _reject()
        grants = _grants(connection)
        copied = dict(connection)
        copied["config"] = dict(config)
        copied["_read_user_ids"] = tuple(grants)
        validated.append(copied)
        seen.add(identity)
    return validated


def _connection_allowed(connection, verified_user):
    user_id = getattr(verified_user, "id", None)
    role = getattr(verified_user, "role", None)
    return (connection["enabled"] is True and role in ("user", "admin")
            and _text_ok(user_id, 256, nonempty=True, controls=True) and bool(user_id.strip())
            and ("*" in connection["_read_user_ids"] or user_id in connection["_read_user_ids"]))


def public_terminal_connections(raw, verified_user):
    result = []
    for connection in parse_terminal_connections(raw):
        if not _connection_allowed(connection, verified_user):
            continue
        public = {
            "id": connection["id"],
            "name": connection.get("name", ""),
            "url": connection["url"],
            "contexts": {},
            "config": {"chat_uploads": connection["config"]["chat_uploads"]},
        }
        result.append(public)
    return result


def _validated_path(value):
    if not _text_ok(value, 4096, nonempty=True): _reject()
    if value.startswith("//") or "://" in value or "\\" in value or "?" in value or "#" in value:
        _reject()
    if _ENCODING.search(value):
        _reject()
    for match in re.finditer(r"%([0-9A-Fa-f]{2})", value):
        if int(match.group(1), 16) in (0x2f, 0x5c, 0x25): _reject()
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", "strict")
    except (UnicodeError, ValueError):
        _reject()
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded): _reject()
    match = _CHAT.fullmatch(decoded.lstrip("/"))
    if not match: _reject()
    filename = match.group(2)
    if ("/" in filename or "\\" in filename or filename in (".", "..")
            or filename.startswith(".") or ":" in filename or "%" in filename): _reject()
    return "files/serve/home/user/requests/chatcmpl-{}/{}".format(
        match.group(1), quote(filename, safe="")
    )


def build_download_request(raw, connection_id, file_path, verified_user):
    """Create an internal GET spec for a pre-authenticated application user.

    File ownership is not inferred from connection grants; the terminal must
    enforce X-User-Id against its own verified namespace and file metadata.
    """
    connections = parse_terminal_connections(raw)
    if type(connection_id) is not str or not connection_id:
        _reject()
    matches = [item for item in connections if item["id"] == connection_id]
    if len(matches) != 1: _reject()
    connection = matches[0]
    if not _connection_allowed(connection, verified_user):
        _reject()
    user_id = verified_user.id
    path = _validated_path(file_path)
    url = connection["url"].rstrip("/") + "/" + path
    return DownloadRequest(
        connection_id,
        url,
        {"Authorization": "Bearer " + connection["key"], "X-User-Id": user_id},
        path,
    )
