#!/usr/bin/env python3
"""Generate SVG badges from InterUSS uss_qualifier report JSON files."""

from __future__ import annotations

import json
from pathlib import Path

BADGES = [
    (
        Path("testing/interuss/output/f3548/report.json"),
        Path("badges/interuss-f3548.svg"),
        "ASTM F3548-21",
    ),
    (
        Path("testing/interuss/output/netrid_v22a/report.json"),
        Path("badges/interuss-f3411.svg"),
        "ASTM F3411-22a",
    ),
]


def _walk_checks(node: dict | list, failed: list) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_checks(item, failed)
        return
    if not isinstance(node, dict):
        return
    if "failed_checks" in node:
        failed.extend(c for c in node["failed_checks"] if isinstance(c, dict))
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk_checks(value, failed)


def _has_execution_error(node: dict | list) -> bool:
    if isinstance(node, list):
        return any(_has_execution_error(item) for item in node)
    if not isinstance(node, dict):
        return False
    if node.get("execution_error"):
        return True
    return any(_has_execution_error(value) for value in node.values() if isinstance(value, (dict, list)))


def _suite_result(report_path: Path) -> str | None:
    if not report_path.exists():
        return None
    data = json.loads(report_path.read_text())
    test_run = data.get("report", data.get("test_run", data))
    test_suite = test_run.get("test_suite", test_run)
    if test_suite.get("successful") is True:
        return "passing"

    failed: list[dict] = []
    _walk_checks(test_run, failed)
    if not failed and not _has_execution_error(test_run):
        return None

    severities = {}
    for check in failed:
        severity = (check.get("severity") or "Unknown").lower()
        severities[severity] = severities.get(severity, 0) + 1

    parts = [
        f"{severity} {severities[severity]}"
        for severity in ("high", "medium", "low", "unknown")
        if severity in severities
    ]
    if _has_execution_error(test_run):
        parts.insert(0, "execution error")
    return ", ".join(parts) or "failing"


def _write_badge(path: Path, label: str, result: str | None) -> None:
    if result is None:
        result = "unknown"

    if result == "passing":
        color = "#4c1"
    elif "high " in result or result == "execution error" or result.startswith("execution error,"):
        color = "#e05d44"
    elif result != "unknown":
        color = "#dfb317"
    else:
        result = "unknown"
        color = "#9f9f9f"

    label_width = max(88, len(label) * 7 + 12)
    message_width = max(56, len(result) * 8 + 12)
    width = label_width + message_width
    x_label = label_width / 2
    x_message = label_width + message_width / 2

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" aria-label="{label}: {result}">
<title>{label}: {result}</title>
<linearGradient id="s" x2="0" y2="100%">
  <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
  <stop offset="1" stop-opacity=".1"/>
</linearGradient>
<clipPath id="r">
  <rect width="{width}" height="20" rx="3" fill="#fff"/>
</clipPath>
<g clip-path="url(#r)">
  <rect width="{label_width}" height="20" fill="#555"/>
  <rect x="{label_width}" width="{message_width}" height="20" fill="{color}"/>
  <rect width="{width}" height="20" fill="url(#s)"/>
</g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="110">
  <text aria-hidden="true" x="{x_label * 10:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(label_width - 12) * 10:.0f}">{label}</text>
  <text x="{x_label * 10:.0f}" y="140" transform="scale(.1)" textLength="{(label_width - 12) * 10:.0f}">{label}</text>
  <text aria-hidden="true" x="{x_message * 10:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(message_width - 12) * 10:.0f}">{result}</text>
  <text x="{x_message * 10:.0f}" y="140" transform="scale(.1)" textLength="{(message_width - 12) * 10:.0f}">{result}</text>
</g>
</svg>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg)


def main() -> None:
    for report_path, badge_path, label in BADGES:
        _write_badge(badge_path, label, _suite_result(report_path))


if __name__ == "__main__":
    main()
