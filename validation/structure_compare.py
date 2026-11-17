# SPDX-License-Identifier: BSD-3-Clause
"""Compare normalized HTML element structures without upstream source code.

The comparator intentionally consumes rendered HTML or observer output.  It
does not inspect Svelte, JavaScript, TypeScript, CSS, source maps, or bundles.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence


SVELTE_HASH_RE = re.compile(r"^svelte-[A-Za-z0-9_-]+$")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
PROVIDER_MESSAGE_ID_RE = re.compile(r"msg_[0-9a-f]{24}", re.IGNORECASE)
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
HIDDEN_VOICE_VISUALIZER_CLASSES = frozenset(
    {
        "bg-indigo-300/10",
        "dark:bg-indigo-500/10",
        "rounded-full",
        "flex",
        "justify-between",
        "p-2.5",
        "w-full",
        "max-w-full",
    }
)


@dataclass(frozen=True)
class ElementSignature:
    tag: str
    classes: tuple[str, ...]

    def render(self) -> str:
        if not self.classes:
            return self.tag
        return f"{self.tag}." + ".".join(self.classes)


@dataclass(frozen=True)
class ContractSignature:
    """One rendered element's externally observable integration contract."""

    tag: str
    classes: tuple[str, ...]
    identifier: str
    attribute_names: tuple[str, ...]
    parent: tuple[str, tuple[str, ...]] | None

    def render(self) -> str:
        rendered = self.tag
        if self.classes:
            rendered += "." + ".".join(self.classes)
        if self.identifier:
            rendered += f"#{self.identifier}"
        if self.attribute_names:
            rendered += " [" + " ".join(self.attribute_names) + "]"
        if self.parent:
            rendered += f" < {self.parent[0]}"
        return rendered


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


def normalize_contract_classes(raw: str | None) -> tuple[str, ...]:
    """Treat a class attribute as a set for the whole-element contract."""

    return tuple(sorted(normalize_identifier(token) for token in normalize_classes(raw)))


def normalize_identifier(raw: str | None) -> str:
    """Mask run-specific UUIDs and provider message IDs, retaining stable text."""

    normalized = UUID_RE.sub("<id>", raw or "")
    return PROVIDER_MESSAGE_ID_RE.sub("<message-id>", normalized)


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


class _ContractParser(HTMLParser):
    """Parse ids, attribute names and immediate parents as well as tags/classes."""

    def __init__(
        self,
        excluded_classes: frozenset[str],
        scope: str,
        *,
        exclude_hidden_voice_visualizer: bool,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.excluded_classes = excluded_classes
        self.scope = scope
        self.exclude_hidden_voice_visualizer = exclude_hidden_voice_visualizer
        self.signatures: list[ContractSignature] = []
        self._stack: list[tuple[str, tuple[str, ...]]] = []
        self._skip_depth = 0
        self._in_scope = scope == "document"

    def _is_excluded(self, classes: tuple[str, ...]) -> bool:
        if self.excluded_classes.intersection(classes):
            return True
        return (
            self.exclude_hidden_voice_visualizer
            and frozenset(classes) == HIDDEN_VOICE_VISUALIZER_CLASSES
        )

    def _signature(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> ContractSignature:
        attributes = dict(attrs)
        classes = normalize_contract_classes(attributes.get("class"))
        names = tuple(
            sorted(name for name, _value in attrs if name not in {"class", "style"})
        )
        return ContractSignature(
            tag=tag,
            classes=classes,
            identifier=normalize_identifier(attributes.get("id")),
            attribute_names=names,
            parent=self._stack[-1] if self._stack else None,
        )

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        normalized_tag = tag.lower()
        if self.scope == "body" and normalized_tag == "body":
            self._in_scope = True
        if not self._in_scope:
            return
        if self._skip_depth:
            if normalized_tag not in VOID_TAGS:
                self._skip_depth += 1
            return
        classes = normalize_contract_classes(dict(attrs).get("class"))
        if self._is_excluded(classes):
            if normalized_tag not in VOID_TAGS:
                self._skip_depth = 1
            return
        signature = self._signature(normalized_tag, attrs)
        self.signatures.append(signature)
        if normalized_tag not in VOID_TAGS:
            self._stack.append((normalized_tag, classes))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if not self._in_scope or self._skip_depth:
            return
        classes = normalize_contract_classes(dict(attrs).get("class"))
        if self._is_excluded(classes):
            return
        self.signatures.append(self._signature(tag.lower(), attrs))

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if self._skip_depth and normalized_tag not in VOID_TAGS:
            self._skip_depth -= 1
            return
        if self._in_scope and normalized_tag not in VOID_TAGS:
            for index in range(len(self._stack) - 1, -1, -1):
                if self._stack[index][0] == normalized_tag:
                    del self._stack[index:]
                    break
        if self.scope == "body" and normalized_tag == "body":
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


def contract_signatures_from_html(
    html: str,
    *,
    excluded_classes: Iterable[str] = (),
    scope: str = "body",
    exclude_hidden_voice_visualizer: bool = True,
) -> tuple[ContractSignature, ...]:
    if scope not in {"document", "body"}:
        raise ValueError("scope must be 'document' or 'body'")
    parser = _ContractParser(
        frozenset(excluded_classes),
        scope,
        exclude_hidden_voice_visualizer=exclude_hidden_voice_visualizer,
    )
    parser.feed(html)
    parser.close()
    if parser._skip_depth:
        raise ValueError("unclosed excluded subtree in rendered HTML")
    return tuple(parser.signatures)


def _opcode_records(
    matcher: SequenceMatcher,
    left: Sequence[ElementSignature | ContractSignature],
    right: Sequence[ElementSignature | ContractSignature],
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


def compare_contract_signatures(
    left: Sequence[ContractSignature],
    right: Sequence[ContractSignature],
    *,
    threshold: float = 99.0,
    reference_floor: float | None = None,
) -> Comparison:
    """Compare the unordered whole-element contract used by the release gate."""

    if not 0.0 <= threshold <= 100.0:
        raise ValueError("threshold must be between 0 and 100")
    if reference_floor is not None and not 0.0 <= reference_floor <= 100.0:
        raise ValueError("reference floor must be between 0 and 100")
    largest = max(len(left), len(right))
    common = sum((Counter(left) & Counter(right)).values())
    ratio = common / largest if largest else 1.0
    percentage = ratio * 100.0
    required = max(threshold, reference_floor or 0.0)
    matcher = SequenceMatcher(a=left, b=right, autojunk=False)
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


def contract_gap_summary(
    left: Sequence[ContractSignature], right: Sequence[ContractSignature]
) -> dict[str, int]:
    """Classify unmatched right-side elements without hiding exact mismatches."""

    left_remaining = Counter(left) - Counter(right)
    right_remaining = Counter(right) - Counter(left)
    causes = {
        "id_or_attributes_only": 0,
        "parent_only": 0,
        "mixed_contract": 0,
        "missing_tag_or_class": 0,
    }

    for right_signature, count in sorted(
        right_remaining.items(), key=lambda item: item[0].render()
    ):
        for _ in range(count):
            candidates = [
                candidate
                for candidate, available in left_remaining.items()
                if available
                and candidate.tag == right_signature.tag
                and candidate.classes == right_signature.classes
            ]
            if not candidates:
                causes["missing_tag_or_class"] += 1
                continue

            def priority(candidate: ContractSignature) -> tuple[int, str]:
                same_identity = (
                    candidate.identifier == right_signature.identifier
                    and candidate.attribute_names == right_signature.attribute_names
                )
                same_parent = candidate.parent == right_signature.parent
                if same_parent and not same_identity:
                    rank = 0
                elif same_identity and not same_parent:
                    rank = 1
                else:
                    rank = 2
                return rank, candidate.render()

            selected = min(candidates, key=priority)
            left_remaining[selected] -= 1
            same_identity = (
                selected.identifier == right_signature.identifier
                and selected.attribute_names == right_signature.attribute_names
            )
            same_parent = selected.parent == right_signature.parent
            if same_parent and not same_identity:
                causes["id_or_attributes_only"] += 1
            elif same_identity and not same_parent:
                causes["parent_only"] += 1
            else:
                causes["mixed_contract"] += 1

    exact = sum((Counter(left) & Counter(right)).values())
    return {
        "exact_elements": exact,
        "left_elements": len(left),
        "right_elements": len(right),
        "left_unmatched": sum((Counter(left) - Counter(right)).values()),
        "right_unmatched": sum((Counter(right) - Counter(left)).values()),
        **causes,
    }


def compare_contract_html(
    left_html: str,
    right_html: str,
    *,
    excluded_classes: Iterable[str] = (),
    scope: str = "body",
    exclude_hidden_voice_visualizer: bool = True,
    threshold: float = 99.0,
    reference_floor: float | None = None,
) -> Comparison:
    left = contract_signatures_from_html(
        left_html,
        excluded_classes=excluded_classes,
        scope=scope,
        exclude_hidden_voice_visualizer=exclude_hidden_voice_visualizer,
    )
    right = contract_signatures_from_html(
        right_html,
        excluded_classes=excluded_classes,
        scope=scope,
        exclude_hidden_voice_visualizer=exclude_hidden_voice_visualizer,
    )
    return compare_contract_signatures(
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
        "--contract",
        action="store_true",
        help="Gate tag/class set, stable id, attribute names and immediate parent.",
    )
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
    left_html = args.left.read_text(encoding="utf-8")
    right_html = args.right.read_text(encoding="utf-8")
    compare = compare_contract_html if args.contract else compare_html
    comparison = compare(
        left_html,
        right_html,
        excluded_classes=args.exclude_class,
        scope=args.scope,
        threshold=args.threshold,
        reference_floor=args.reference_floor,
    )
    payload = asdict(comparison)
    payload["comparison_mode"] = (
        "whole_element_contract" if args.contract else "ordered_tag_class"
    )
    if args.contract:
        payload["contract_gap_summary"] = contract_gap_summary(
            contract_signatures_from_html(
                left_html,
                excluded_classes=args.exclude_class,
                scope=args.scope,
            ),
            contract_signatures_from_html(
                right_html,
                excluded_classes=args.exclude_class,
                scope=args.scope,
            ),
        )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if comparison.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

