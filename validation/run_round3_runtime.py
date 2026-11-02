#!/usr/bin/env python3
"""Exercise document, provider, streaming and terminal compatibility paths."""

import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def call(base, path, *, method="GET", token=None, body=None, headers=None):
    request_headers = {"Accept": "application/json", **(headers or {})}
    if token:
        request_headers["Authorization"] = "Bearer " + token
    data = body
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    request = Request(base.rstrip("/") + path, data=data, headers=request_headers, method=method)
    try:
        response = urlopen(request, timeout=300)
    except HTTPError as error:
        response = error
    return response.status, response.headers.get_content_type(), response.read()


def expect(result, statuses=(200,), media="application/json"):
    status, actual_media, content = result
    assert status in statuses, (status, actual_media, content[:512])
    if media is not None:
        assert actual_media == media, (status, actual_media, content[:512])
    return json.loads(content) if actual_media == "application/json" else content


def signin(base):
    value = expect(
        call(
            base,
            "/api/v1/auths/signin",
            method="POST",
            headers={
                "X-Compat-User": "round3-admin@example.invalid",
                "X-Compat-Role": "admin",
            },
            body={"email": "ignored@example.invalid", "password": "synthetic-unused"},
        )
    )
    return value["token"]


def upload(base, token, filename, content, content_type):
    boundary = "openatelierui-round3-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    value = expect(
        call(
            base,
            "/api/v1/files/",
            method="POST",
            token=token,
            body=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        )
    )
    assert not value.get("error"), value.get("error")
    assert value.get("id")
    content_value = expect(
        call(base, f"/api/v1/files/{value['id']}/data/content", token=token)
    )
    assert content_value.get("content")
    return value["id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument(
        "--phase", choices=("exercise", "resume", "verify"), default="exercise"
    )
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    token = signin(args.base)

    if args.phase == "verify":
        state = json.loads(args.state.read_text())
        for file_id in state["file_ids"]:
            expect(call(args.base, f"/api/v1/files/{file_id}", token=token))
        expect(
            call(
                args.base,
                "/api/v1/models/model?" + urlencode({"id": state["model_id"]}),
                token=token,
            )
        )
        expect(call(args.base, "/api/models", token=token))
        completion = expect(
            call(
                args.base,
                "/api/chat/completions",
                method="POST",
                token=token,
                body={"model": state["model_id"], "messages": [{"role": "user", "content": "After restart"}], "stream": False},
            )
        )
        assert completion["choices"][0]["message"]["content"] == "synthetic response"
        print(json.dumps({"status": "round3_restart_verified", "checks": 4, "tokens_printed": False}, sort_keys=True))
        return

    large_upload_seconds = None
    if args.phase == "resume":
        files = expect(call(args.base, "/api/v1/files/", token=token))
        file_items = files.get("items", files) if isinstance(files, dict) else files
        by_name = {item.get("filename"): item.get("id") for item in file_items}
        csv_id = by_name["measures.csv"]
        text_id = by_name["technical-report.txt"]
    else:
        csv_id = upload(args.base, token, "measures.csv", b"day,value\n1,42\n", "text/csv")
        large_upload_started = time.monotonic()
        text_id = upload(
            args.base,
            token,
            "technical-report.txt",
            ("Synthetic compatibility paragraph. " * 30000).encode(),
            "text/plain",
        )
        large_upload_seconds = time.monotonic() - large_upload_started

    models = expect(call(args.base, "/api/models", token=token))
    model_items = models.get("data", models) if isinstance(models, dict) else models
    assert any(item.get("id") == "compat-model" for item in model_items)
    model_id = "round3-compat-model"
    custom_models = expect(call(args.base, "/api/v1/models/", token=token))
    if not any(item.get("id") == model_id for item in custom_models):
        expect(
            call(
                args.base,
                "/api/v1/models/create",
                method="POST",
                token=token,
                body={
                    "id": model_id,
                    "name": "Round 3 compatibility model",
                    "base_model_id": "compat-model",
                    "meta": {},
                    "params": {},
                    "access_control": None,
                    "is_active": True,
                },
            )
        )
    expect(call(args.base, "/api/models", token=token))
    completion = expect(
        call(
            args.base,
            "/api/chat/completions",
            method="POST",
            token=token,
            body={"model": model_id, "messages": [{"role": "user", "content": "Reply"}], "stream": False},
        )
    )
    assert completion["choices"][0]["message"]["content"] == "synthetic response"
    retry = expect(
        call(
            args.base,
            "/api/chat/completions",
            method="POST",
            token=token,
            body={"model": model_id, "messages": [{"role": "user", "content": "Retry"}], "stream": False},
        )
    )
    assert retry["choices"][0]["message"]["content"] == "synthetic response"
    streamed = expect(
        call(
            args.base,
            "/api/chat/completions",
            method="POST",
            token=token,
            body={"model": model_id, "messages": [{"role": "user", "content": "Stream"}], "stream": True},
        ),
        media="text/event-stream",
    )
    assert b"synthetic " in streamed and b"response" in streamed and b"[DONE]" in streamed

    terminals = expect(call(args.base, "/api/v1/terminals/", token=token))
    assert [item["id"] for item in terminals] == ["compat-files"]
    request_id = "12345678-1234-4abc-8def-1234567890ab"
    terminal = expect(
        call(
            args.base,
            "/api/v1/terminals/compat-files/files/serve/home/user/requests/"
            + f"chatcmpl-{request_id}/Community-workshop.txt",
            token=token,
        ),
        media="text/plain",
    )
    assert terminal == b"synthetic terminal attachment\n"
    args.state.write_text(
        json.dumps({"file_ids": [csv_id, text_id], "model_id": model_id}, sort_keys=True)
        + "\n"
    )
    print(
        json.dumps(
            {
                "status": "round3_runtime_verified",
                "checks": 10,
                "csv_file_id_present": bool(csv_id),
                "large_text_file_id_present": bool(text_id),
                "large_text_upload_seconds": (
                    round(large_upload_seconds, 3)
                    if large_upload_seconds is not None
                    else None
                ),
                "tokens_printed": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
