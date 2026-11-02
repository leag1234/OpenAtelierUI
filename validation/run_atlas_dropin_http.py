#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise the reported Atlas HTTP contracts against an isolated runtime."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def call(base, path, *, method="GET", token=None, body=None, trusted=False):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if trusted:
        headers.update({
            "X-Atlas-User": "dropin-admin@example.invalid",
            "X-Atlas-User-Role": "admin",
        })
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    return response.status, response.headers.get_content_type(), response.read()


def as_json(content):
    return json.loads(content.decode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--expected-image", type=Path, required=True)
    parser.add_argument("--image-url", default="/static/atlas/model.png")
    args = parser.parse_args()

    status, media, content = call(
        args.base,
        "/api/v1/auths/signin",
        method="POST",
        trusted=True,
        body={"email": "ignored@example.invalid", "password": "ignored"},
    )
    signed_in = as_json(content)
    assert status == 200 and media == "application/json", (
        status, media, content[:200]
    )
    assert signed_in["role"] == "admin"
    token = signed_in["token"]

    status, media, content = call(
        args.base, "/api/v1/models/model?" + urlencode({"id": "missing-model"}), token=token
    )
    assert status == 404 and media == "application/json"
    assert as_json(content)["detail"] == "We could not find what you're looking for :/"

    status, media, content = call(args.base, "/api/v1/terminals/", token=token)
    terminals = as_json(content)
    assert status == 200 and media == "application/json", (
        status, media, content[:200]
    )
    assert [item["id"] for item in terminals] == ["atlas-files"]
    assert "key" not in terminals[0]

    status, media, content = call(args.base, "/api/v1/notes/", token=token)
    assert status == 200 and media == "application/json" and as_json(content) == []

    status, media, content = call(args.base, "/api/v1/users/all", token=token)
    users = as_json(content)
    assert status == 200 and media == "application/json" and len(users) == 1
    assert users[0]["role"] == "admin" and "api_key" not in users[0]

    status, media, content = call(args.base, "/api/v1/report-unknown", token=token)
    assert status == 404 and media == "application/json"
    assert as_json(content)["detail"] == "Not Found"

    status, media, content = call(
        args.base,
        "/api/v1/models/create",
        method="POST",
        token=token,
        body={
            "id": "atlas-avatar-model",
            "name": "Synthetic Atlas avatar",
            "meta": {"profile_image_url": args.image_url},
            "params": {},
            "access_control": None,
            "is_active": True,
        },
    )
    assert status == 200 and media == "application/json", content[:200]

    status, media, content = call(
        args.base,
        "/api/v1/models/model/profile/image?" + urlencode({"id": "atlas-avatar-model"}),
        token=token,
    )
    assert status == 200 and media == "image/png"
    assert content == args.expected_image.read_bytes()

    print(json.dumps({
        "status": "pass",
        "checks": 7,
        "trusted_role": "admin",
        "terminal_count": len(terminals),
        "user_count": len(users),
        "token_printed": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
