#!/usr/bin/env python3
"""Parse uss_qualifier raw report JSON and emit GitHub-flavoured Markdown summary.

Usage:
    python3 report_to_summary.py <report.json> [<report.json> ...]

The output is written to stdout and can be piped to $GITHUB_STEP_SUMMARY.
Multiple report files are concatenated with a separator.

Report JSON structure (interuss/monitoring v0.30.0):
    {codebase_version, commit_hash, ..., report: {test_suite: {
        successful, start_time, end_time, actions: [
            {test_scenario: {cases: [{steps: [{passed_checks: [], failed_checks: []}]}]}}
            | {test_suite: {actions: [...]}}
        ]
    }}}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _walk_checks(node: dict | list, passed: list, failed: list) -> None:
    """Recursively collect all passed_checks and failed_checks entries.

    Only collects entries that are dicts (check result objects), skipping
    the capability_evaluations section where passed_checks contains strings.
    """
    if isinstance(node, list):
        for item in node:
            _walk_checks(item, passed, failed)
        return
    if not isinstance(node, dict):
        return
    if "passed_checks" in node:
        passed.extend(c for c in node["passed_checks"] if isinstance(c, dict))
    if "failed_checks" in node:
        failed.extend(c for c in node["failed_checks"] if isinstance(c, dict))
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk_checks(value, passed, failed)


def _walk_scenarios(node: dict | list, scenarios: list) -> None:
    """Collect all test_scenario dicts with their name and check counts."""
    if isinstance(node, list):
        for item in node:
            _walk_scenarios(item, scenarios)
        return
    if not isinstance(node, dict):
        return
    if "test_scenario" in node:
        ts = node["test_scenario"]
        passed: list = []
        failed: list = []
        _walk_checks(ts, passed, failed)
        scenarios.append(
            {
                "name": ts.get("name", "?"),
                "passed": len(passed),
                "failed": len(failed),
                "execution_error": ts.get("execution_error"),
            }
        )
        return  # don't recurse inside test_scenario (already walked)
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk_scenarios(value, scenarios)


def _participant_counts(failed_checks: list) -> dict[str, dict]:
    """Aggregate failed check counts per participant."""
    counts: dict[str, dict] = {}
    for check in failed_checks:
        participants = check.get("participants") or []
        for p in participants:
            if p not in counts:
                counts[p] = {"fail": 0, "requirements": set()}
            counts[p]["fail"] += 1
            for req in check.get("requirements") or []:
                counts[p]["requirements"].add(req)
    return counts


def _format_report(report_path: Path) -> list[str]:
    lines: list[str] = []
    try:
        with report_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        lines.append(f"> ⚠️ Could not read `{report_path}`: {exc}\n")
        return lines

    test_run = data.get("report", {})
    test_suite = test_run.get("test_suite", test_run)

    # ---- Header ----
    # Use config name from metadata or a parent directory name as the label
    config_name = report_path.parent.name or report_path.stem
    lines.append(f"## InterUSS uss_qualifier — `{config_name}`\n")

    codebase = data.get("codebase_version", "")
    commit = data.get("commit_hash", "")
    if codebase:
        lines.append(f"**Codebase:** `{codebase}` (commit `{commit[:12]}`)\n")

    start_time = test_suite.get("start_time", "")
    end_time = test_suite.get("end_time", "")
    if start_time:
        lines.append(f"**Started:** {start_time}  ")
    if end_time:
        lines.append(f"**Ended:** {end_time}  ")
    if start_time or end_time:
        lines.append("")

    successful = test_suite.get("successful")
    if successful is True:
        lines.append("**Overall result:** ✅ PASS\n")
    elif successful is False:
        lines.append("**Overall result:** ❌ FAIL\n")

    # ---- Check counts ----
    all_passed: list = []
    all_failed: list = []
    _walk_checks(test_run, all_passed, all_failed)

    total = len(all_passed) + len(all_failed)
    if total:
        pass_pct = round(100 * len(all_passed) / total)
        lines.append("### Check results\n")
        lines.append("| Result | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| ✅ Pass  | {len(all_passed)} |")
        lines.append(f"| ❌ Fail  | {len(all_failed)} |")
        lines.append(f"| **Total** | **{total}** ({pass_pct}% pass) |")
        lines.append("")

    # ---- Per-scenario breakdown ----
    scenarios: list = []
    _walk_scenarios(test_run.get("test_suite", test_run), scenarios)
    if scenarios:
        lines.append("<details><summary>Per-scenario breakdown</summary>\n")
        lines.append("| Scenario | ✅ Pass | ❌ Fail | Execution error |")
        lines.append("|----------|--------|--------|-----------------|")
        for s in scenarios:
            err = "⚠️ Yes" if s["execution_error"] else "No"
            lines.append(f"| {s['name']} | {s['passed']} | {s['failed']} | {err} |")
        lines.append("\n</details>\n")

    # ---- Per-participant failures ----
    participant_counts = _participant_counts(all_failed)
    if participant_counts:
        lines.append("### Failed checks per participant\n")
        lines.append("| Participant | Failed checks | Unique requirements |")
        lines.append("|-------------|---------------|---------------------|")
        for participant, info in sorted(participant_counts.items()):
            lines.append(f"| `{participant}` | {info['fail']} | {len(info['requirements'])} |")
        lines.append("")

    # ---- Sample of failed checks (up to 30) ----
    if all_failed:
        shown = all_failed[:30]
        lines.append(f"<details><summary>Failed checks ({len(all_failed)} total, showing first {len(shown)})</summary>\n")
        lines.append("| Check | Participants | Requirements | Summary |")
        lines.append("|-------|-------------|--------------|---------|")
        for check in shown:
            participants = ", ".join(f"`{p}`" for p in (check.get("participants") or []))
            requirements = ", ".join(f"`{r}`" for r in (check.get("requirements") or []))
            summary = (check.get("summary") or "").replace("|", "\\|")[:80]
            severity = check.get("severity", "")
            sev_icon = "🔴" if severity == "High" else ("🟡" if severity == "Medium" else "🔵")
            lines.append(f"| {sev_icon} {check.get('name', '?')} | {participants} | {requirements} | {summary} |")
        lines.append("\n</details>\n")

    return lines


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: report_to_summary.py <report.json> [<report.json> ...]", file=sys.stderr)
        sys.exit(1)

    all_lines: list[str] = []
    for path_str in sys.argv[1:]:
        path = Path(path_str)
        if not path.exists():
            all_lines.append(f"> ⚠️ Report not found: `{path}`\n")
            continue
        all_lines.extend(_format_report(path))
        all_lines.append("---\n")

    print("\n".join(all_lines))


if __name__ == "__main__":
    main()
