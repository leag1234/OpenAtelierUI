# SPDX-License-Identifier: BSD-3-Clause
"""Bounded validation and merging for operator-owned OpenAI connection headers."""

import re


MAX_HEADER_COUNT = 32
MAX_HEADER_VALUE_BYTES = 4096
MAX_HEADER_TOTAL_BYTES = 16 * 1024
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def validate_static_headers(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_HEADER_COUNT:
        raise ValueError("OpenAI connection headers are invalid")
    result = {}
    seen = set()
    total = 0
    for name, content in value.items():
        if not isinstance(name, str) or not name.isascii() or not _HEADER_NAME.fullmatch(name):
            raise ValueError("OpenAI connection headers are invalid")
        folded = name.lower()
        if folded in seen or not isinstance(content, str):
            raise ValueError("OpenAI connection headers are invalid")
        seen.add(folded)
        try:
            size = len(content.encode("utf-8", errors="strict"))
        except UnicodeError:
            raise ValueError("OpenAI connection headers are invalid") from None
        if size > MAX_HEADER_VALUE_BYTES:
            raise ValueError("OpenAI connection headers are invalid")
        if any(
            (ord(char) < 32 and char != "\t") or 127 <= ord(char) <= 159
            for char in content
        ):
            raise ValueError("OpenAI connection headers are invalid")
        if any(marker in content for marker in ("{{", "}}", "${")):
            raise ValueError("OpenAI connection header templates are unsupported")
        total += len(name.encode("ascii")) + size
        if total > MAX_HEADER_TOTAL_BYTES:
            raise ValueError("OpenAI connection headers are invalid")
        result[name] = content
    return result


def configured_static_headers(configs, index, url):
    if configs is None:
        return {}
    if not isinstance(configs, dict):
        raise ValueError("OpenAI connection configuration is invalid")
    selected = configs.get(str(index), configs.get(url, {}))
    if not isinstance(selected, dict):
        raise ValueError("OpenAI connection configuration is invalid")
    return validate_static_headers(selected.get("headers"))


def merge_static_headers(base, configured):
    result = dict(base)
    for name, value in validate_static_headers(configured).items():
        for existing in tuple(result):
            if existing.lower() == name.lower():
                del result[existing]
        result[name] = value
    return result
