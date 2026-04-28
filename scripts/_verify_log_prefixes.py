"""One-off: find logger info/warning/error with first string arg not starting with '['.

Exempt: logger.debug, logger.exception, f"[{{var}}]..." dynamic prefix.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CALL_RE = re.compile(
    r"logger\.(info|warning|error)\(",
    re.MULTILINE,
)


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    issues: list[tuple[int, str, str]] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        if "logger.exception(" in line:
            continue
        m = CALL_RE.search(line)
        if not m:
            continue
        method = m.group(1)
        if method == "debug":
            continue
        # Skip continuation lines (message on previous physical line)
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # f"[{var}] dynamic prefix
        if re.search(r'logger\.(info|warning|error)\(\s*f"\[\{', line):
            continue
        # First string literal in call (naive: one line)
        sm = re.search(r'logger\.(?:info|warning|error)\(\s*(f?)"', line)
        if not sm:
            continue
        quote_i = sm.end() - 1
        rest = line[quote_i + 1 :]
        if not rest.startswith("["):
            issues.append((i, method, line.strip()[:140]))
    return issues


def main() -> int:
    all_issues: list[tuple[str, int, str, str]] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if "tests" in rel.parts or rel.parts[0] == "docs":
            continue
        if rel.parts[0] == "scripts" and path.name.startswith("_verify"):
            continue
        for line_no, method, preview in scan_file(path):
            all_issues.append((str(rel), line_no, method, preview))

    for p, ln, method, preview in all_issues:
        print(f"{p}:{ln}: [{method}] {preview}")
    print(f"TOTAL {len(all_issues)}")
    return 0 if not all_issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
