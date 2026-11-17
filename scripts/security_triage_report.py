#!/usr/bin/env python3
"""Build the body of the weekly security triage issue.

The security-triage workflow runs the audit-deps gate, reads the open
CodeQL code-scanning alerts, drops raw artifacts in the work directory,
and calls this script. The script writes one plain-English report file.
The workflow then appends that report to the single open triage issue,
or files a new one when none is open and the run has findings or
limits. Stdlib only; no corpus_kb imports.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Show at most this many rule rows and example locations per row.
MAX_RULE_ROWS = 40
MAX_EXAMPLES = 3
MAX_AUDIT_CHARS = 4000
MAX_ERROR_CHARS = 1200

SEVERITY_ORDER = ["critical", "high", "medium", "low", "unknown"]
# Fallback map when rule.security_severity_level is absent.
QUALITY_SEVERITY = {"error": "high", "warning": "medium", "note": "low"}


def read_text(base: Path, name: str) -> str:
    path = base / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def parse_concatenated_json(text: str) -> list[dict[str, object]]:
    """Parse a stream of concatenated JSON documents (gh api --paginate)."""
    decoder = json.JSONDecoder()
    items: list[dict[str, object]] = []
    idx = 0
    while idx < len(text):
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        if idx >= len(text):
            break
        value, end = decoder.raw_decode(text, idx)
        idx = end
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            items.append(value)
    return items


def severity_of(alert: dict[str, object]) -> str:
    rule = alert.get("rule")
    if not isinstance(rule, dict):
        return "unknown"
    level = rule.get("security_severity_level")
    if isinstance(level, str) and level in SEVERITY_ORDER:
        return level
    fallback = rule.get("severity")
    if isinstance(fallback, str):
        return QUALITY_SEVERITY.get(fallback, "unknown")
    return "unknown"


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.index(severity) if severity in SEVERITY_ORDER else 99


def location_of(alert: dict[str, object]) -> str:
    instance = alert.get("most_recent_instance")
    if not isinstance(instance, dict):
        return "location not recorded"
    location = instance.get("location")
    if not isinstance(location, dict):
        return "location not recorded"
    path = location.get("path")
    line = location.get("start_line")
    if isinstance(path, str) and path:
        return f"{path}:{line}" if isinstance(line, int) else path
    return "location not recorded"


def rule_id_of(alert: dict[str, object]) -> str:
    rule = alert.get("rule")
    if isinstance(rule, dict) and isinstance(rule.get("id"), str) and rule.get("id"):
        return str(rule["id"])
    return "unknown-rule"


def codeql_section(alerts: list[dict[str, object]]) -> list[str]:
    lines: list[str] = []
    if not alerts:
        lines.append("**Status: CLEAN.** No open alerts.")
        return lines
    by_rule: dict[str, list[dict[str, object]]] = defaultdict(list)
    for alert in alerts:
        by_rule[rule_id_of(alert)].append(alert)
    lines.append(f"**Status: {len(alerts)} open alert(s) across "
                 f"{len(by_rule)} rule(s).** Sorted by severity, then count.")
    lines.append("")
    lines.append("| Severity | Rule | Alerts | Example locations |")
    lines.append("|---|---|---|---|")
    rows: list[tuple[int, int, str, str, str]] = []
    for rule_id, group in by_rule.items():
        severity = max((severity_of(a) for a in group), key=severity_rank)
        seen: dict[str, None] = {}
        for alert in group:
            seen.setdefault(location_of(alert), None)
            if len(seen) == MAX_EXAMPLES:
                break
        examples = ", ".join(seen)
        rows.append((severity_rank(severity), -len(group), severity, rule_id,
                     examples))
    rows.sort()
    for _, neg_count, severity, rule_id, examples in rows[:MAX_RULE_ROWS]:
        lines.append(f"| {severity} | `{rule_id}` | {-neg_count} | {examples} |")
    if len(rows) > MAX_RULE_ROWS:
        lines.append(f"| ... | ... | {len(rows) - MAX_RULE_ROWS} more rules not shown | ... |")
    lines.append("")
    lines.append("Counts use one entry per alert. The full per-alert list with "
                 "file and line lives in the code-scanning dashboard.")
    return lines


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: security_triage_report.py ARTIFACT_DIR OUTPUT_FILE", file=sys.stderr)
        return 2
    base = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    audit_exit = read_text(base, "audit-exit.txt")
    audit_output = read_text(base, "audit-output.txt")
    codeql_exit = read_text(base, "codeql-exit.txt")
    codeql_error = read_text(base, "codeql-error.txt")
    alerts_raw = read_text(base, "codeql-alerts.json")

    run_url = read_text(base, "run-url.txt")
    ref_name = read_text(base, "ref-name.txt") or "unknown ref"
    sha = read_text(base, "sha.txt")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    alerts: list[dict[str, object]] = []
    codeql_ok = codeql_exit == "0"
    if codeql_ok:
        try:
            alerts = parse_concatenated_json(alerts_raw)
        except json.JSONDecodeError as exc:
            codeql_ok = False
            codeql_error = f"alert JSON did not parse: {exc}"

    audit_ok = audit_exit == "0"

    lines: list[str] = []
    lines.append("## Security triage report (automated sweep)")
    lines.append("")
    lines.append("One report per run. A person reads it and decides. "
                 "No auto-fix PRs.")
    lines.append("")
    lines.append(f"- Run: {run_url}" if run_url else "- Run: (local build)")
    lines.append(f"- Ref: `{ref_name}`" + (f" at `{sha[:10]}`" if sha else ""))
    lines.append(f"- Date: {now}")
    lines.append("")
    lines.append("## Dependency audit (`poe audit-deps` over uv.lock)")
    lines.append("")
    if audit_exit == "":
        lines.append("**Status: NOT RUN.** The audit step produced no result. "
                     "This is a gap in the sweep, not a clean bill.")
    elif audit_ok:
        lines.append("**Status: CLEAN.** No known advisories in the locked set "
                     "(exit 0). The ignore list in uv.toml stays in force.")
    else:
        lines.append(f"**Status: FINDINGS OR ERROR (exit {audit_exit or '?'}).** "
                     "The gate failed. The output below says which advisories "
                     "fired, or which environment error stopped the audit.")
        lines.append("")
        lines.append("```text")
        lines.append(truncate(audit_output, MAX_AUDIT_CHARS))
        lines.append("```")
    lines.append("")
    lines.append("## CodeQL (open code-scanning alerts)")
    lines.append("")
    if codeql_ok:
        lines.append("The Sunday `codeql.yml` run owns the analysis. This "
                     "report only reads its open alerts.")
        lines.append("")
        lines.extend(codeql_section(alerts))
    else:
        lines.append("**Status: COULD NOT READ.** The alerts API call failed. "
                     "The code findings for this run are unknown, not clean.")
        if codeql_error:
            lines.append("")
            lines.append("```text")
            lines.append(truncate(codeql_error, MAX_ERROR_CHARS))
            lines.append("```")
    lines.append("")
    lines.append("## What happens next")
    lines.append("")
    lines.append("Next week's run appends to this issue while it stays open. "
                 "Close it when the findings are triaged; a later run with "
                 "findings opens a fresh one. Fixes are human decisions.")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    audit_state = "clean" if audit_ok else ("not-run" if audit_exit == "" else "findings-or-error")
    codeql_state = "unreadable" if not codeql_ok else f"{len(alerts)} open alert(s)"
    has_report = (not audit_ok) or (not codeql_ok) or bool(alerts)
    summary = (f"audit={audit_state} codeql={codeql_state} "
               f"has_report={'yes' if has_report else 'no'}")
    (base / "triage-summary.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
