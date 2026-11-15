from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import mlflow_exporter  # noqa: E402
import orca_calls  # noqa: E402
import orca_snapshot  # noqa: E402


def row(tools, failures=0, agent="claude"):
    base = orca_calls.normalize_call("claude", {"input_tokens": 1, "cache_read_input_tokens": 9,
                                                "cache_creation_input_tokens": 0, "output_tokens": 2})
    total = None if tools is None else sum(tools.values())
    return {**base, "agent": agent, "source_kind": "raw_call", "event_status": "final",
            "tool_calls": tools, "tool_calls_total": total, "tool_failures": failures}


def snap(rows):
    return orca_snapshot.build_snapshot("t", "a", "p", rows, None, set())


class ToolAggregationTests(unittest.TestCase):
    def test_totals_failures_and_breakdown_when_everything_is_observed(self):
        s = snap([row({"Bash": 3, "Read": 1}, 1), row({"Bash": 2}, 0), row({}, 0)])
        self.assertEqual((s["metrics"]["tool_calls_total"], s["metrics"]["tool_failures"]), (6, 1))
        self.assertEqual(s["tools"], {"Bash": 5, "Read": 1})
        self.assertEqual((s["availability"]["tool_calls_total"], s["availability"]["tool_failures"]), ("complete", "complete"))

    def test_codex_style_rows_count_calls_but_leave_failures_unknown(self):
        s = snap([row({"exec": 4}, None, "codex"), row({"exec": 1}, None, "codex")])
        self.assertEqual(s["metrics"]["tool_calls_total"], 5)
        self.assertNotIn("tool_failures", s["metrics"])  # unknown is not 0
        self.assertEqual(s["availability"]["tool_failures"], "unknown")

    def test_a_call_without_tool_info_blocks_the_total_instead_of_producing_a_partial_sum(self):
        s = snap([row({"Bash": 3}), row(None, None)])
        self.assertNotIn("tool_calls_total", s["metrics"])
        self.assertNotIn("tools", s)
        self.assertEqual(s["availability"]["tool_calls_total"], "partial")

    def test_known_zero_tools_is_not_the_same_as_unknown(self):
        s = snap([row({}, 0)])
        self.assertEqual((s["metrics"]["tool_calls_total"], s["tools"]), (0, {}))

    def test_no_calls_means_unknown(self):
        s = snap([])
        self.assertEqual(s["availability"]["tool_calls_total"], "unknown")
        self.assertNotIn("tools", s)

    def test_breakdown_keeps_the_top_tools_and_folds_the_rest(self):
        tools = {f"Tool{i}": 100 - i for i in range(40)}
        s = snap([row(tools)])
        self.assertEqual(len(s["tools"]), orca_snapshot.TOP_TOOLS + 1)
        self.assertEqual(sum(s["tools"].values()), sum(tools.values()))
        self.assertIn("other", s["tools"])

    def test_snapshot_fingerprint_changes_when_tool_use_changes(self):
        self.assertNotEqual(snap([row({"Bash": 1})])["fingerprint"], snap([row({"Bash": 2})])["fingerprint"])


class ToolMetricTests(unittest.TestCase):
    def test_top_ten_tools_become_safe_metric_names_and_totals_are_kept(self):
        tools = {"Bash": 50, "mcp:obsidian_vault": 7, "weird/name x": 3, **{f"T{i}": 1 for i in range(12)}}
        metrics = mlflow_exporter.snapshot_metrics(snap([row(tools, 2)]))
        per_tool = {k for k in metrics if k.startswith("tool.")}
        self.assertEqual(len(per_tool), 10)
        self.assertEqual(metrics["tool.Bash"], 50.0)
        self.assertIn("tool.mcp:obsidian_vault", per_tool)
        self.assertEqual((metrics["tool_calls_total"], metrics["tool_failures"]), (float(sum(tools.values())), 2.0))
        for name in per_tool:
            self.assertRegex(name, r"^[A-Za-z0-9_.\- :/]+$")  # what MLflow accepts

    def test_no_tool_metrics_when_not_observed(self):
        metrics = mlflow_exporter.snapshot_metrics(snap([row(None, None)]))
        self.assertFalse([k for k in metrics if k.startswith("tool")])


if __name__ == "__main__":
    unittest.main()
