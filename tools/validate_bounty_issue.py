#!/usr/bin/env python3
"""Validate bounty issue bodies before publishing them."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


REQUIRED_SECTIONS = [
    "**Bounty:**",
    "Acceptance criteria:",
    "Commissions:",
    "Required validation:",
]

SECTION_PATTERNS = {
    "**Bounty:**": re.compile(r"^\*\*Bounty:\*\*"),
    "Acceptance criteria:": re.compile(r"^Acceptance criteria:\s*$"),
    "Commissions:": re.compile(r"^Commissions:\s*$"),
    "Required validation:": re.compile(r"^Required validation:\s*$"),
}

REQUIRED_COMMISSIONS_TEXT = (
    "You can earn an extra $5 for every bounty issue you create on your own "
    "fork of the repo, provided you keep up with submissions. We will payout "
    "the bounty you place between $5 and $25 to the submitter and you will earn "
    "an extra $5 on your payout, after you merge the best submission for your "
    "bounty. You are required to use this exact issue template & description "
    "format, including this message and the required validation. Payouts will "
    "not be given to you or other submitters without a valid build diagnostic "
    "log (not build-00000000). You are required to rename your fork to something "
    "fun and unique, and provide this exact message in your issue description."
)


@dataclass
class ValidationResult:
    path: str
    ok: bool
    errors: list[str]


def normalize_paragraph(text: str) -> str:
    """Collapse Markdown wrapping while keeping words and punctuation exact."""
    return re.sub(r"\s+", " ", text.strip())


def section_body(text: str, section: str) -> str:
    positions: list[tuple[str, int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        for name, pattern in SECTION_PATTERNS.items():
            if pattern.match(stripped):
                positions.append((name, offset, offset + len(line)))
                break
        offset += len(line)

    current_index = next((idx for idx, item in enumerate(positions) if item[0] == section), None)
    if current_index is None:
        return ""

    body_start = positions[current_index][2]
    body_end = positions[current_index + 1][1] if current_index + 1 < len(positions) else len(text)
    return text[body_start:body_end].strip()


def validation_mentions_real_diagnostic_log(validation_text: str) -> bool:
    lowered = validation_text.lower()
    mentions_logd = ".logd" in lowered
    mentions_generated_or_real = "generated diagnostic" in lowered or "real diagnostic" in lowered
    excludes_stub = "build-00000000" in lowered and any(
        phrase in lowered
        for phrase in [
            "must not be",
            "not build-00000000",
            "reject build-00000000",
            "exclude build-00000000",
        ]
    )
    return mentions_logd and mentions_generated_or_real and excludes_stub


def validate_issue_body(text: str, path: str = "<string>") -> ValidationResult:
    errors: list[str] = []

    for section, pattern in SECTION_PATTERNS.items():
        if not any(pattern.match(line.rstrip("\r\n")) for line in text.splitlines()):
            errors.append(f"missing required section: {section}")

    commissions_text = section_body(text, "Commissions:")
    if commissions_text:
        normalized = normalize_paragraph(commissions_text)
        if normalized != REQUIRED_COMMISSIONS_TEXT:
            errors.append("commissions paragraph does not exactly match required text")

    validation_text = section_body(text, "Required validation:")
    if validation_text and not validation_mentions_real_diagnostic_log(validation_text):
        errors.append(
            "required validation must mention a generated/real .logd diagnostic and exclude build-00000000"
        )

    acceptance_text = section_body(text, "Acceptance criteria:")
    if "Acceptance criteria:" in text and not acceptance_text:
        errors.append("acceptance criteria section is empty")

    return ValidationResult(path=path, ok=not errors, errors=errors)


def read_issue_body(path: str) -> tuple[str, str]:
    if path == "-":
        return sys.stdin.read(), "<stdin>"
    issue_path = Path(path)
    return issue_path.read_text(encoding="utf-8"), str(issue_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue_body", nargs="+", help="Markdown issue body path, or '-' for stdin")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable validation results")
    args = parser.parse_args(argv)

    results: list[ValidationResult] = []
    for path in args.issue_body:
        try:
            text, display_path = read_issue_body(path)
            results.append(validate_issue_body(text, display_path))
        except OSError as exc:
            results.append(ValidationResult(path=path, ok=False, errors=[str(exc)]))

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2, sort_keys=True))
    else:
        for result in results:
            status = "PASS" if result.ok else "FAIL"
            print(f"{status} {result.path}")
            for error in result.errors:
                print(f"  - {error}")

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
