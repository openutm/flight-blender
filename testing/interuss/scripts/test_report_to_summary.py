"""Tests for testing/interuss/scripts/report_to_summary.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from io import StringIO
from pathlib import Path

# Import the standalone script module by absolute path so pytest can discover
# this test file without requiring a package __init__.py in this directory.
_SCRIPT_PATH = Path(__file__).parent / "report_to_summary.py"
_spec = importlib.util.spec_from_file_location("report_to_summary", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_walk_checks = _mod._walk_checks
_walk_scenarios = _mod._walk_scenarios
_participant_counts = _mod._participant_counts
_format_report = _mod._format_report
main = _mod.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_check(name="check-name", passed=True, participants=None, requirements=None, severity="Low", summary=""):
    """Return a minimal check result dict."""
    return {
        "name": name,
        "summary": summary,
        "severity": severity,
        "participants": participants or [],
        "requirements": requirements or [],
    }


def _make_scenario(name, passed_checks, failed_checks, execution_error=None):
    return {
        "test_scenario": {
            "name": name,
            "passed_checks": passed_checks,
            "failed_checks": failed_checks,
            "execution_error": execution_error,
        }
    }


def _make_report_data(successful=True, scenarios=None):
    """Minimal report.json structure matching uss_qualifier v0.30.0 output."""
    actions = scenarios or [
        _make_scenario(
            "scenario_a",
            passed_checks=[_make_check("p1"), _make_check("p2")],
            failed_checks=[],
        )
    ]
    return {
        "codebase_version": "interuss/monitoring/v0.30.0",
        "commit_hash": "abc123def456",
        "report": {
            "test_suite": {
                "successful": successful,
                "start_time": "2026-01-01T10:00:00Z",
                "end_time": "2026-01-01T10:05:00Z",
                "actions": actions,
            }
        },
    }


# ---------------------------------------------------------------------------
# _walk_checks
# ---------------------------------------------------------------------------


class WalkChecksTests(unittest.TestCase):
    def test_empty_node_yields_nothing(self):
        passed, failed = [], []
        _walk_checks({}, passed, failed)
        self.assertEqual(passed, [])
        self.assertEqual(failed, [])

    def test_flat_dict_with_checks(self):
        p = _make_check("p1")
        f = _make_check("f1")
        node = {"passed_checks": [p], "failed_checks": [f]}
        passed, failed = [], []
        _walk_checks(node, passed, failed)
        self.assertEqual(passed, [p])
        self.assertEqual(failed, [f])

    def test_nested_structure(self):
        inner_p = _make_check("inner-pass")
        outer_f = _make_check("outer-fail")
        node = {
            "level1": {
                "passed_checks": [inner_p],
                "failed_checks": [],
            },
            "failed_checks": [outer_f],
            "passed_checks": [],
        }
        passed, failed = [], []
        _walk_checks(node, passed, failed)
        self.assertIn(inner_p, passed)
        self.assertIn(outer_f, failed)

    def test_skips_string_entries_in_passed_checks(self):
        """capability_evaluations section uses strings in passed_checks — they must be skipped."""
        node = {"passed_checks": ["STRING_CAPABILITY", _make_check("real")]}
        passed, failed = [], []
        _walk_checks(node, passed, failed)
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0]["name"], "real")

    def test_list_input(self):
        p = _make_check("p")
        node = [{"passed_checks": [p], "failed_checks": []}]
        passed, failed = [], []
        _walk_checks(node, passed, failed)
        self.assertEqual(passed, [p])

    def test_non_dict_non_list_is_ignored(self):
        passed, failed = [], []
        _walk_checks("just-a-string", passed, failed)
        _walk_checks(42, passed, failed)
        self.assertEqual(passed, [])
        self.assertEqual(failed, [])


# ---------------------------------------------------------------------------
# _walk_scenarios
# ---------------------------------------------------------------------------


class WalkScenariosTests(unittest.TestCase):
    def test_single_scenario(self):
        node = _make_scenario(
            "my-scenario",
            passed_checks=[_make_check()],
            failed_checks=[_make_check()],
        )
        scenarios = []
        _walk_scenarios(node, scenarios)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["name"], "my-scenario")
        self.assertEqual(scenarios[0]["passed"], 1)
        self.assertEqual(scenarios[0]["failed"], 1)

    def test_nested_scenarios_in_list(self):
        actions = [
            _make_scenario("s1", [_make_check()], []),
            _make_scenario("s2", [], [_make_check()]),
        ]
        scenarios = []
        _walk_scenarios(actions, scenarios)
        names = [s["name"] for s in scenarios]
        self.assertIn("s1", names)
        self.assertIn("s2", names)

    def test_execution_error_recorded(self):
        node = _make_scenario(
            "bad-scenario",
            passed_checks=[],
            failed_checks=[],
            execution_error={"message": "boom"},
        )
        scenarios = []
        _walk_scenarios(node, scenarios)
        self.assertIsNotNone(scenarios[0]["execution_error"])

    def test_no_scenarios(self):
        scenarios = []
        _walk_scenarios({}, scenarios)
        self.assertEqual(scenarios, [])

    def test_suite_nesting(self):
        """Scenarios nested inside a test_suite wrapper should be collected."""
        suite_node = {
            "test_suite": {
                "actions": [
                    _make_scenario("nested", [_make_check()], [_make_check()]),
                ]
            }
        }
        scenarios = []
        _walk_scenarios(suite_node, scenarios)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["name"], "nested")


# ---------------------------------------------------------------------------
# _participant_counts
# ---------------------------------------------------------------------------


class ParticipantCountsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_participant_counts([]), {})

    def test_single_participant(self):
        check = _make_check(participants=["uss1"], requirements=["REQ-1"])
        result = _participant_counts([check])
        self.assertIn("uss1", result)
        self.assertEqual(result["uss1"]["fail"], 1)
        self.assertIn("REQ-1", result["uss1"]["requirements"])

    def test_multiple_participants(self):
        check = _make_check(participants=["uss1", "uss2"], requirements=["REQ-X"])
        result = _participant_counts([check])
        self.assertEqual(result["uss1"]["fail"], 1)
        self.assertEqual(result["uss2"]["fail"], 1)

    def test_accumulates_across_checks(self):
        checks = [
            _make_check(participants=["uss1"], requirements=["R1"]),
            _make_check(participants=["uss1"], requirements=["R2"]),
        ]
        result = _participant_counts(checks)
        self.assertEqual(result["uss1"]["fail"], 2)
        self.assertEqual(len(result["uss1"]["requirements"]), 2)

    def test_check_without_participants_does_not_crash(self):
        check = {"name": "c", "participants": None, "requirements": []}
        result = _participant_counts([check])
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# _format_report
# ---------------------------------------------------------------------------


class FormatReportTests(unittest.TestCase):
    def _write_json(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "report.json"
        p.write_text(json.dumps(data))
        return p

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_nonexistent_file_returns_warning(self):
        lines = _format_report(self.tmp_path / "missing.json")
        self.assertTrue(any("Could not read" in line or "not found" in line for line in lines))

    def test_malformed_json_returns_warning(self):
        p = self.tmp_path / "bad.json"
        p.write_text("{not valid json")
        lines = _format_report(p)
        self.assertTrue(any("Could not read" in line for line in lines))

    def test_passing_report_shows_pass(self):
        data = _make_report_data(successful=True)
        p = self._write_json(self.tmp_path, data)
        lines = _format_report(p)
        full = "\n".join(lines)
        self.assertIn("PASS", full)

    def test_failing_report_shows_fail(self):
        data = _make_report_data(
            successful=False,
            scenarios=[
                _make_scenario(
                    "s1",
                    passed_checks=[_make_check()],
                    failed_checks=[_make_check(participants=["fb"], requirements=["R1"])],
                )
            ],
        )
        p = self._write_json(self.tmp_path, data)
        lines = _format_report(p)
        full = "\n".join(lines)
        self.assertIn("FAIL", full)

    def test_check_counts_shown(self):
        data = _make_report_data(
            successful=False,
            scenarios=[
                _make_scenario(
                    "s1",
                    passed_checks=[_make_check(), _make_check()],
                    failed_checks=[_make_check()],
                )
            ],
        )
        p = self._write_json(self.tmp_path, data)
        lines = _format_report(p)
        full = "\n".join(lines)
        self.assertIn("2", full)  # 2 passed
        self.assertIn("1", full)  # 1 failed

    def test_codebase_version_shown(self):
        data = _make_report_data()
        p = self._write_json(self.tmp_path, data)
        lines = _format_report(p)
        full = "\n".join(lines)
        self.assertIn("interuss/monitoring/v0.30.0", full)

    def test_empty_report_object_does_not_crash(self):
        p = self.tmp_path / "empty.json"
        p.write_text("{}")
        lines = _format_report(p)
        self.assertIsInstance(lines, list)

    def test_participant_failures_section(self):
        data = _make_report_data(
            successful=False,
            scenarios=[
                _make_scenario(
                    "s1",
                    passed_checks=[],
                    failed_checks=[
                        _make_check(participants=["flight_blender"], requirements=["ASTM-F3548"]),
                    ],
                )
            ],
        )
        p = self._write_json(self.tmp_path, data)
        lines = _format_report(p)
        full = "\n".join(lines)
        self.assertIn("flight_blender", full)


# ---------------------------------------------------------------------------
# main() — CLI entry point
# ---------------------------------------------------------------------------


class MainTests(unittest.TestCase):
    def _capture_main(self, args):
        old_argv = sys.argv
        old_stdout = sys.stdout
        sys.argv = ["report_to_summary.py"] + args
        sys.stdout = StringIO()
        try:
            main()
            return sys.stdout.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout

    def test_missing_file_prints_warning(self):
        output = self._capture_main(["/nonexistent/report.json"])
        self.assertIn("not found", output)

    def test_multiple_files_concatenated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "f3548" / "report.json"
            p1.parent.mkdir()
            p1.write_text(json.dumps(_make_report_data(successful=True)))

            p2 = Path(tmpdir) / "netrid_v22a" / "report.json"
            p2.parent.mkdir()
            p2.write_text(json.dumps(_make_report_data(successful=False)))

            output = self._capture_main([str(p1), str(p2)])
            self.assertIn("f3548", output)
            self.assertIn("netrid_v22a", output)

    def test_no_args_exits(self):
        old_argv = sys.argv
        sys.argv = ["report_to_summary.py"]
        try:
            with self.assertRaises(SystemExit):
                main()
        finally:
            sys.argv = old_argv
