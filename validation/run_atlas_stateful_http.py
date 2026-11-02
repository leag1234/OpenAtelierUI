#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise the restart-safe state contracts required by an unchanged Atlas stack."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FILE_BYTES = b"OpenAtelierUI synthetic Atlas attachment\n"
MODEL_ID = "atlas_solid_model"
FUNCTION_ID = "atlas_solid_filter"


def call(base, path, *, method="GET", token=None, body=None, headers=None):
    request_headers = {"Accept": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = "Bearer " + token
    data = body
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(
        base.rstrip("/") + path,
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        response = urlopen(request, timeout=15)
    except HTTPError as error:
        response = error
    return response.status, response.headers.get_content_type(), response.read()


def as_json(content):
    return json.loads(content.decode("utf-8"))


def expect(result, statuses=(200,), media="application/json"):
    status, actual_media, content = result
    assert status in statuses, (status, actual_media, content[:512])
    if media is not None:
        assert actual_media == media, (status, actual_media, content[:512])
    return as_json(content) if actual_media == "application/json" else content


def signin(base, email, role):
    value = expect(call(
        base,
        "/api/v1/auths/signin",
        method="POST",
        headers={"X-Atlas-User": email, "X-Atlas-User-Role": role},
        body={"email": "ignored@example.invalid", "password": "synthetic-unused"},
    ))
    assert value["email"] == email and value["role"] == role
    assert isinstance(value.get("token"), str) and value["token"]
    return value


def multipart_file():
    boundary = "openatelierui-atlas-solid-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="Community workshop.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode("ascii") + FILE_BYTES + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, {"Content-Type": "multipart/form-data; boundary=" + boundary}


def seed(base, state_path):
    admin = signin(base, "solid-admin@example.invalid", "admin")
    owner = signin(base, "solid-owner@example.invalid", "user")
    foreign = signin(base, "solid-foreign@example.invalid", "user")
    admin_token, owner_token, foreign_token = (
        admin["token"], owner["token"], foreign["token"]
    )

    users = expect(call(base, "/api/v1/users/all", token=admin_token))
    assert users["total"] == len(users["users"])
    assert {admin["id"], owner["id"], foreign["id"]} <= {
        item["id"] for item in users["users"]
    }

    model = expect(call(
        base,
        "/api/v1/models/create",
        method="POST",
        token=admin_token,
        body={
            "id": MODEL_ID,
            "name": "Synthetic Atlas solid model",
            "meta": {"profile_image_url": "/static/favicon.png"},
            "params": {},
            "access_control": None,
            "is_active": True,
        },
    ))
    assert model["id"] == MODEL_ID

    function = expect(call(
        base,
        "/api/v1/functions/create",
        method="POST",
        token=admin_token,
        body={
            "id": FUNCTION_ID,
            "name": "Synthetic Atlas solid filter",
            "content": (
                "class Filter:\n"
                "    async def inlet(self, body: dict, __user__: dict = None):\n"
                "        return body\n"
            ),
            "meta": {"description": "Synthetic stateful compatibility fixture", "manifest": {}},
        },
    ))
    assert function["id"] == FUNCTION_ID

    chat_payload = {
        "title": "Community workshop draft",
        "models": [MODEL_ID],
        "params": {},
        "history": {"messages": {}, "currentId": None},
        "messages": [],
        "tags": [],
    }
    chat = expect(call(
        base, "/api/v1/chats/new", method="POST", token=owner_token,
        body={"chat": chat_payload},
    ))
    chat_id = chat["id"]
    updated_chat = expect(call(
        base, f"/api/v1/chats/{chat_id}", method="POST", token=owner_token,
        body={"chat": {"title": "Community workshop saved"}},
    ))
    assert updated_chat["chat"]["title"] == "Community workshop saved"
    expect(call(base, f"/api/v1/chats/{chat_id}/pin", method="POST", token=owner_token))
    expect(call(base, f"/api/v1/chats/{chat_id}", token=foreign_token), statuses=(401, 403, 404))

    note = expect(call(
        base, "/api/v1/notes/create", method="POST", token=owner_token,
        body={
            "title": "Community workshop note",
            "data": {"content": "Synthetic note before restart"},
            "meta": {"source": "atlas-solid-state"},
        },
    ))
    note_id = note["id"]
    updated_note = expect(call(
        base, f"/api/v1/notes/{note_id}/update", method="POST", token=owner_token,
        body={
            "title": "Community workshop note saved",
            "data": {"content": "Synthetic note preserved after restart"},
            "meta": {"source": "atlas-solid-state"},
        },
    ))
    assert updated_note["title"] == "Community workshop note saved"
    expect(call(base, f"/api/v1/notes/{note_id}/pin", method="POST", token=owner_token))
    expect(call(base, f"/api/v1/notes/{note_id}", token=foreign_token), statuses=(401, 403, 404))

    file_body, file_headers = multipart_file()
    uploaded = expect(call(
        base,
        "/api/v1/files/?" + urlencode({"process": "false"}),
        method="POST",
        token=owner_token,
        body=file_body,
        headers=file_headers,
    ))
    file_id = uploaded["id"]
    content = expect(
        call(base, f"/api/v1/files/{file_id}/content", token=owner_token), media=None
    )
    assert content == FILE_BYTES
    expect(call(base, f"/api/v1/files/{file_id}", token=foreign_token), statuses=(401, 403, 404))

    state = {
        "schema_version": 1,
        "chat_id": chat_id,
        "note_id": note_id,
        "file_id": file_id,
        "model_id": MODEL_ID,
        "function_id": FUNCTION_ID,
        "owner_id": owner["id"],
        "foreign_id": foreign["id"],
        "file_sha256": hashlib.sha256(FILE_BYTES).hexdigest(),
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps({"status": "seeded", "checks": 18, "token_printed": False}, sort_keys=True))


def verify(base, state_path, cleanup):
    state = json.loads(state_path.read_text())
    admin = signin(base, "solid-admin@example.invalid", "admin")
    owner = signin(base, "solid-owner@example.invalid", "user")
    foreign = signin(base, "solid-foreign@example.invalid", "user")
    admin_token, owner_token, foreign_token = (
        admin["token"], owner["token"], foreign["token"]
    )

    chat = expect(call(base, f"/api/v1/chats/{state['chat_id']}", token=owner_token))
    assert chat["chat"]["title"] == "Community workshop saved"
    assert expect(call(
        base, f"/api/v1/chats/{state['chat_id']}/pinned", token=owner_token
    )) is True
    expect(call(base, f"/api/v1/chats/{state['chat_id']}", token=foreign_token), statuses=(401, 403, 404))

    note = expect(call(base, f"/api/v1/notes/{state['note_id']}", token=owner_token))
    assert note["title"] == "Community workshop note saved"
    assert note["data"]["content"] == "Synthetic note preserved after restart"
    assert note.get("is_pinned") is True
    expect(call(base, f"/api/v1/notes/{state['note_id']}", token=foreign_token), statuses=(401, 403, 404))

    file_content = expect(
        call(base, f"/api/v1/files/{state['file_id']}/content", token=owner_token),
        media=None,
    )
    assert hashlib.sha256(file_content).hexdigest() == state["file_sha256"]
    expect(call(base, f"/api/v1/files/{state['file_id']}", token=foreign_token), statuses=(401, 403, 404))

    model = expect(call(
        base, "/api/v1/models/model?" + urlencode({"id": state["model_id"]}),
        token=admin_token,
    ))
    assert model["id"] == state["model_id"]
    function = expect(call(
        base, f"/api/v1/functions/id/{state['function_id']}", token=admin_token
    ))
    assert function["id"] == state["function_id"]

    if cleanup:
        deleted_file = expect(call(
            base, f"/api/v1/files/{state['file_id']}", method="DELETE", token=owner_token
        ))
        assert deleted_file == {"message": "File deleted successfully"}
        expect(
            call(base, f"/api/v1/files/{state['file_id']}", token=owner_token),
            statuses=(404,),
        )
        assert expect(call(
            base, f"/api/v1/chats/{state['chat_id']}", method="DELETE", token=owner_token
        )) is True
        assert expect(call(
            base,
            "/api/v1/models/model/delete",
            method="POST",
            token=admin_token,
            body={"id": state["model_id"]},
        )) is True
        assert expect(call(
            base, f"/api/v1/functions/id/{state['function_id']}/delete",
            method="DELETE", token=admin_token,
        )) is True

    print(json.dumps({
        "status": "verified_after_restart",
        "checks": 13,
        "cleanup": cleanup,
        "token_printed": False,
    }, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--phase", required=True, choices=("seed", "verify"))
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.phase == "seed":
        assert not args.state.exists()
        seed(args.base, args.state)
    else:
        assert args.state.is_file()
        verify(args.base, args.state, args.cleanup)


if __name__ == "__main__":
    main()
