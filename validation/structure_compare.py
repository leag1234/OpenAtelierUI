# SPDX-License-Identifier: BSD-3-Clause
"""Compare normalized HTML element structures without upstream source code.

The comparator intentionally consumes rendered HTML or observer output.  It
does not inspect Svelte, JavaScript, TypeScript, CSS, source maps, or bundles.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence


SVELTE_HASH_RE = re.compile(r"^svelte-[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class ElementSignature:
    tag: str
    classes: tuple[str, ...]

    def render(self) -> str:
        if not self.classes:
            return self.tag
        return f"{self.tag}." + ".".join(self.classes)


@dataclass(frozen=True)
class Comparison:
    left_count: int
    right_count: int
    ratio: float
    percentage: float
    threshold: float
    reference_floor: float | None
    passed: bool
    opcodes: tuple[dict[str, object], ...]


def normalize_classes(raw: str | None) -> tuple[str, ...]:
    """Preserve class-list order while dropping compiler-generated hashes."""

    if not raw:
        return ()
    return tuple(
        token
        for token in raw.split()
        if token and SVELTE_HASH_RE.fullmatch(token) is None
    )


class _StructureParser(HTMLParser):
    def __init__(self, excluded_classes: frozenset[str], scope: str) -> None:
        super().__init__(convert_charrefs=True)
        self.excluded_classes = excluded_classes
        self.scope = scope
        self.signatures: list[ElementSignature] = []
        self._skip_depth = 0
        self._in_scope = scope == "document"

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized_tag = tag.lower()
        if self.scope == "body" and normalized_tag == "body":
            self._in_scope = True
        if not self._in_scope:
            return
        attributes = dict(attrs)
        classes = normalize_classes(attributes.get("class"))
        should_skip = bool(self.excluded_classes.intersection(classes))
        if self._skip_depth:
            self._skip_depth += 1
            return
        if should_skip:
            self._skip_depth = 1
            return
        self.signatures.append(ElementSignature(normalized_tag, classes))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if not self._in_scope or self._skip_depth:
            return
        attributes = dict(attrs)
        classes = normalize_classes(attributes.get("class"))
        if self.excluded_classes.intersection(classes):
            return
        self.signatures.append(ElementSignature(tag.lower(), classes))

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
        if self.scope == "body" and tag.lower() == "body":
            self._in_scope = False


def signatures_from_html(
    html: str, *, excluded_classes: Iterable[str] = (), scope: str = "document"
) -> tuple[ElementSignature, ...]:
    if scope not in {"document", "body"}:
        raise ValueError("scope must be 'document' or 'body'")
    parser = _StructureParser(frozenset(excluded_classes), scope)
    parser.feed(html)
    parser.close()
    if parser._skip_depth:
        raise ValueError("unclosed excluded subtree in rendered HTML")
    return tuple(parser.signatures)


def _opcode_records(
    matcher: SequenceMatcher[ElementSignature],
    left: Sequence[ElementSignature],
    right: Sequence[ElementSignature],
) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        records.append(
            {
                "operation": tag,
                "left": {"start": i1, "end": i2},
                "right": {"start": j1, "end": j2},
                "left_preview": [item.render() for item in left[i1 : min(i2, i1 + 8)]],
                "right_preview": [item.render() for item in right[j1 : min(j2, j1 + 8)]],
            }
        )
    return tuple(records)


def compare_signatures(
    left: Sequence[ElementSignature],
    right: Sequence[ElementSignature],
    *,
    threshold: float = 99.0,
    reference_floor: float | None = None,
) -> Comparison:
    if not 0.0 <= threshold <= 100.0:
        raise ValueError("threshold must be between 0 and 100")
    if reference_floor is not None and not 0.0 <= reference_floor <= 100.0:
        raise ValueError("reference floor must be between 0 and 100")
    matcher = SequenceMatcher(a=left, b=right, autojunk=False)
    ratio = matcher.ratio()
    percentage = ratio * 100.0
    required = max(threshold, reference_floor or 0.0)
    return Comparison(
        left_count=len(left),
        right_count=len(right),
        ratio=ratio,
        percentage=percentage,
        threshold=threshold,
        reference_floor=reference_floor,
        passed=percentage >= required,
        opcodes=_opcode_records(matcher, left, right),
    )


def compare_html(
    left_html: str,
    right_html: str,
    *,
    excluded_classes: Iterable[str] = (),
    scope: str = "document",
    threshold: float = 99.0,
    reference_floor: float | None = None,
) -> Comparison:
    left = signatures_from_html(
        left_html, excluded_classes=excluded_classes, scope=scope
    )
    right = signatures_from_html(
        right_html, excluded_classes=excluded_classes, scope=scope
    )
    return compare_signatures(
        left,
        right,
        threshold=threshold,
        reference_floor=reference_floor,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare rendered HTML tag/class structure."
    )
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--threshold", type=float, default=99.0)
    parser.add_argument("--reference-floor", type=float)
    parser.add_argument("--exclude-class", action="append", default=[])
    parser.add_argument(
        "--scope",
        choices=("document", "body"),
        default="document",
        help="Compare the full document or only the rendered body hierarchy.",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    comparison = compare_html(
        args.left.read_text(encoding="utf-8"),
        args.right.read_text(encoding="utf-8"),
        excluded_classes=args.exclude_class,
        scope=args.scope,
        threshold=args.threshold,
        reference_floor=args.reference_floor,
    )
    payload = asdict(comparison)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if comparison.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

