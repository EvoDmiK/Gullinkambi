from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "orca_calls.py"
SPEC = importlib.util.spec_from_file_location("orca_calls", MODULE_PATH)
assert SPEC and SPEC.loader
orca_calls = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = orca_calls
SPEC.loader.exec_module(orca_calls)


class NormalizeCallTests(unittest.TestCase):
    def test_codex_input_includes_cache_and_reasoning_not_double_counted(self):
        row = orca_calls.normalize_call("codex", {
            "input_tokens": 1000, "cached_input_tokens": 800,
            "output_tokens": 50, "reasoning_output_tokens": 30,
        })
        self.assertEqual(row["normalized_input_tokens"], 1000)
        self.assertEqual(row["uncached_input_tokens"], 200)
        self.assertEqual(row["processed_total_tokens"], 1050)
        self.assertEqual(row["non_cache_tokens"], 250)
        self.assertEqual(row["reasoning_tokens_subset"], 30)
        self.assertIsNone(row["cache_write_tokens"])
        self.assertEqual(row["unknown_fields"]["cache_write_tokens"], orca_calls.NOT_SEPARATE)

    def test_codex_missing_cached_input_keeps_processed_total_but_not_non_cache(self):
        row = orca_calls.normalize_call("codex", {"input_tokens": 100, "output_tokens": 10})
        self.assertEqual(row["processed_total_tokens"], 110)
        self.assertIsNone(row["non_cache_tokens"])
        self.assertIsNone(row["uncached_input_tokens"])
        self.assertIsNone(row["cache_read_tokens"])

    def test_codex_cached_exceeding_input_is_rejected(self):
        with self.assertRaisesRegex(orca_calls.InvalidUsage, "cached_input_exceeds_input"):
            orca_calls.normalize_call("codex", {
                "input_tokens": 10, "cached_input_tokens": 11, "output_tokens": 1,
            })

    def test_claude_adds_cache_to_input_and_large_cache_read_is_valid(self):
        row = orca_calls.normalize_call("claude", {
            "input_tokens": 5, "cache_read_input_tokens": 90000,
            "cache_creation_input_tokens": 100, "output_tokens": 40,
        })
        self.assertEqual(row["normalized_input_tokens"], 90105)
        self.assertEqual(row["processed_total_tokens"], 90145)
        self.assertEqual(row["non_cache_tokens"], 145)
        self.assertIsNone(row["reasoning_tokens_subset"])

    def test_claude_missing_cache_fields_are_unknown_not_zero(self):
        row = orca_calls.normalize_call("claude", {"input_tokens": 5, "output_tokens": 4})
        self.assertIsNone(row["cache_read_tokens"])
        self.assertIsNone(row["processed_total_tokens"])
        self.assertIsNone(row["non_cache_tokens"])

    def test_known_zero_differs_from_unknown(self):
        zero = orca_calls.normalize_call("claude", {
            "input_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "output_tokens": 0,
        })
        self.assertEqual(zero["processed_total_tokens"], 0)
        self.assertNotIn("processed_total_tokens", zero["unknown_fields"])

    def test_invalid_values_are_rejected_not_clamped(self):
        for bad in (-1, 1.5, "7", True):
            with self.assertRaises(orca_calls.InvalidUsage):
                orca_calls.normalize_call("claude", {"input_tokens": bad})

    def test_unsupported_provider(self):
        with self.assertRaises(orca_calls.InvalidUsage):
            orca_calls.normalize_call("antigravity", {})


class EventKeyTests(unittest.TestCase):
    def test_key_is_stable_and_scoped(self):
        a = orca_calls.event_key("codex", "ns", "s1", "r1")
        self.assertEqual(a, orca_calls.event_key("codex", "ns", "s1", "r1"))
        self.assertNotEqual(a, orca_calls.event_key("codex", "ns2", "s1", "r1"))
        self.assertNotEqual(a, orca_calls.event_key("claude", "ns", "s1", "r1"))


class AggregateTests(unittest.TestCase):
    def rows(self):
        return [
            orca_calls.normalize_call("codex", {
                "input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": 10}),
            orca_calls.normalize_call("claude", {
                "input_tokens": 100, "cache_read_input_tokens": 300,
                "cache_creation_input_tokens": 0, "output_tokens": 20}),
        ]

    def test_cache_hit_is_weighted_not_average_of_rates(self):
        result = orca_calls.aggregate(self.rows())
        # (500 + 300) / (1000 + 400) = 57.14%, not mean(50, 75)
        self.assertAlmostEqual(result["cache_hit_rate_percent"], 800 / 1400 * 100)
        self.assertEqual(result["processed_total"]["coverage_state"], "complete")
        self.assertEqual(result["processed_total"]["complete_total"], 1010 + 420)

    def test_unknown_row_blocks_complete_total(self):
        rows = self.rows() + [
            orca_calls.normalize_call("claude", {"input_tokens": 1, "output_tokens": 1}),
        ]
        result = orca_calls.aggregate(rows)
        self.assertEqual(result["processed_total"]["coverage_state"], "partial")
        self.assertIsNone(result["processed_total"]["complete_total"])
        self.assertEqual(result["processed_total"]["known_count"], 2)
        self.assertEqual(result["eligible_count"], 3)
        self.assertEqual(result["cache_hit_cohort_count"], 2)

    def test_zero_denominator_is_not_available(self):
        zero = orca_calls.normalize_call("claude", {
            "input_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "output_tokens": 0})
        self.assertIsNone(orca_calls.aggregate([zero])["cache_hit_rate_percent"])

    def test_other_normalization_versions_are_excluded(self):
        legacy = {"normalization_version": 0, "processed_total_tokens": 9}
        result = orca_calls.aggregate(self.rows() + [legacy])
        self.assertEqual(result["eligible_count"], 2)



def codex_lines(*records, cwd="/w/a"):
    out = [{"type": "session_meta", "payload": {"id": "sess-1", "cwd": cwd}},
           {"type": "turn_context", "payload": {"cwd": cwd, "model": "gpt-x"}}]
    for response_id, usage in records:
        out.append({"type": "token_usage_record", "timestamp": "2026-10-09T01:00:00Z",
                    "payload": {"response_id": response_id, "session_id": "sess-1", "usage": usage}})
    return [__import__("json").dumps(e) for e in out]


def claude_line(message_id, usage, stop=None, cwd="/w/a"):
    return __import__("json").dumps({
        "type": "assistant", "cwd": cwd, "sessionId": "s2", "timestamp": "2026-10-09T01:00:00Z",
        "message": {"id": message_id, "model": "claude-x", "stop_reason": stop, "usage": usage}})


CODEX_USAGE = {"input_tokens": 100, "cached_input_tokens": 60, "cache_write_input_tokens": 0,
               "output_tokens": 5, "reasoning_output_tokens": 1, "total_tokens": 105}
CLAUDE_USAGE = {"input_tokens": 1, "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 0, "output_tokens": 5}


class CodexCacheWriteTests(unittest.TestCase):
    def test_present_zero_cache_write_is_known(self):
        row = orca_calls.normalize_call("codex", CODEX_USAGE)
        self.assertEqual(row["cache_write_tokens"], 0)
        self.assertEqual(row["non_cache_tokens"], 45)
        self.assertNotIn("cache_write_tokens", row["unknown_fields"])

    def test_positive_cache_write_makes_non_cache_unknown(self):
        row = orca_calls.normalize_call("codex", {**CODEX_USAGE, "cache_write_input_tokens": 7})
        self.assertEqual(row["cache_write_tokens"], 7)
        self.assertIsNone(row["non_cache_tokens"])
        self.assertEqual(row["processed_total_tokens"], 105)
        self.assertIn("unverified", row["unknown_fields"]["non_cache_tokens"])


class ExtractCallEventsTests(unittest.TestCase):
    scope = staticmethod(lambda cwd: bool(cwd) and cwd.startswith("/w/"))

    def test_codex_replayed_lines_yield_one_event_per_response(self):
        lines = codex_lines(("r1", CODEX_USAGE), ("r1", CODEX_USAGE), ("r2", CODEX_USAGE))
        events, rejected = orca_calls.extract_call_events("codex", lines, "ns", self.scope)
        self.assertEqual(len(events), 2)
        self.assertEqual(rejected, 0)
        again, _ = orca_calls.extract_call_events("codex", lines + lines, "ns", self.scope)
        self.assertEqual({e["event_key"] for e in events}, {e["event_key"] for e in again})

    def test_out_of_scope_and_invalid_records(self):
        bad = {**CODEX_USAGE, "cached_input_tokens": 999}
        events, rejected = orca_calls.extract_call_events(
            "codex", codex_lines(("r1", bad), ("r2", CODEX_USAGE)), "ns", self.scope)
        self.assertEqual((len(events), rejected), (1, 1))
        none, _ = orca_calls.extract_call_events(
            "codex", codex_lines(("r1", CODEX_USAGE), cwd="/elsewhere"), "ns", self.scope)
        self.assertEqual(none, [])

    def test_claude_partial_then_final_keeps_final_and_ignores_late_partial(self):
        partial = {**CLAUDE_USAGE, "output_tokens": 1}
        lines = [claude_line("m1", partial), claude_line("m1", CLAUDE_USAGE, stop="end_turn"),
                 claude_line("m1", partial)]
        events, _ = orca_calls.extract_call_events("claude", lines, "ns", self.scope)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_status"], "final")
        self.assertEqual(events[0]["output_tokens"], 5)

    def test_claude_without_final_stays_partial_and_truncated_line_is_skipped(self):
        lines = [claude_line("m1", CLAUDE_USAGE), '{"type": "assistant", "mess']
        events, _ = orca_calls.extract_call_events("claude", lines, "ns", self.scope)
        self.assertEqual([e["event_status"] for e in events], ["partial"])

    def test_event_has_no_raw_path_or_text(self):
        events, _ = orca_calls.extract_call_events(
            "claude", [claude_line("m1", CLAUDE_USAGE, stop="end_turn", cwd="/w/secret-project")],
            "ns", self.scope)
        self.assertNotIn("secret-project", repr(events))

    def test_namespace_changes_identity(self):
        a, _ = orca_calls.extract_call_events("codex", codex_lines(("r1", CODEX_USAGE)), "ns1", self.scope)
        b, _ = orca_calls.extract_call_events("codex", codex_lines(("r1", CODEX_USAGE)), "ns2", self.scope)
        self.assertNotEqual(a[0]["event_key"], b[0]["event_key"])


def claude_block(message_id, block, stop=None, cwd="/w/a", usage=None):
    return json.dumps({"type": "assistant", "cwd": cwd, "sessionId": "s2", "timestamp": "2026-10-09T01:00:00Z",
                       "message": {"id": message_id, "model": "claude-x", "stop_reason": stop,
                                   "usage": usage or CLAUDE_USAGE, "content": [block]}})


def claude_result(*results):
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": use_id, "is_error": is_error, "content": "SECRET-RESULT-TEXT"}
        for use_id, is_error in results]}})


def tool_use(use_id, name, **inputs):
    return {"type": "tool_use", "id": use_id, "name": name, "input": inputs}


class ToolCountTests(unittest.TestCase):
    scope = staticmethod(lambda cwd: bool(cwd) and cwd.startswith("/w/"))

    def extract(self, agent, lines):
        events, rejected = orca_calls.extract_call_events(agent, lines, "ns", self.scope)
        return {e["source_request_id_hash"]: e for e in events}, rejected

    def one(self, agent, lines):
        events, _ = self.extract(agent, lines)
        self.assertEqual(len(events), 1)
        return next(iter(events.values()))

    def test_claude_collects_tool_use_across_every_line_of_a_request(self):
        lines = [claude_block("m1", {"type": "text", "text": "hi"}),
                 claude_block("m1", tool_use("u1", "Bash", command="ls")),
                 claude_block("m1", tool_use("u2", "Read", file_path="/x"), stop="tool_use")]
        event = self.one("claude", lines)
        self.assertEqual((event["tool_calls"], event["tool_calls_total"]), ({"Bash": 1, "Read": 1}, 2))
        self.assertEqual(event["event_status"], "final")

    def test_claude_failures_come_from_the_matching_tool_result_and_bump_the_revision(self):
        lines = [claude_block("m1", tool_use("u1", "Bash"), stop="tool_use"),
                 claude_block("m1", tool_use("u2", "Bash"), stop="tool_use")]
        before = self.one("claude", lines)
        self.assertEqual(before["tool_failures"], 0)
        after = self.one("claude", lines + [claude_result(("u1", True), ("u2", False))])
        self.assertEqual((after["tool_calls"], after["tool_failures"]), ({"Bash": 2}, 1))
        self.assertGreater(after["revision"], before["revision"])  # so the database accepts the update
        self.assertNotEqual(orca_calls.json.dumps(before, sort_keys=True), orca_calls.json.dumps(after, sort_keys=True))

    def test_claude_request_without_tools_is_a_known_zero_not_unknown(self):
        event = self.one("claude", [claude_block("m1", {"type": "text", "text": "hi"}, stop="end_turn")])
        self.assertEqual((event["tool_calls"], event["tool_calls_total"], event["tool_failures"]), ({}, 0, 0))

    def test_replayed_lines_do_not_double_count(self):
        lines = [claude_block("m1", tool_use("u1", "Bash"), stop="tool_use"), claude_result(("u1", True))]
        event = self.one("claude", lines + lines)
        self.assertEqual((event["tool_calls_total"], event["tool_failures"]), (1, 1))

    def test_names_are_sanitised_and_arguments_never_appear(self):
        lines = [claude_block("m1", tool_use("u1", "Bash", command="rm -rf SECRET-ARGUMENT")),
                 claude_block("m1", tool_use("u2", "mcp__obsidian_vault__vault", path="SECRET-ARGUMENT")),
                 claude_block("m1", tool_use("u3", "weird name; DROP TABLE"), stop="tool_use"),
                 claude_result(("u1", False))]
        event = self.one("claude", lines)
        self.assertEqual(event["tool_calls"], {"Bash": 1, "mcp:obsidian_vault": 1, "other": 1})
        blob = json.dumps(event)
        for marker in ("SECRET-ARGUMENT", "SECRET-RESULT-TEXT", "DROP TABLE", "rm -rf"):
            self.assertNotIn(marker, blob)

    def test_out_of_scope_requests_produce_no_event_and_no_tools(self):
        events, _ = self.extract("claude", [claude_block("m1", tool_use("u1", "Bash"), stop="tool_use", cwd="/elsewhere")])
        self.assertEqual(events, {})

    def test_too_many_distinct_tools_are_folded_into_other(self):
        lines = [claude_block("m1", tool_use(f"u{i}", f"Tool{i}"), stop="tool_use") for i in range(60)]
        event = self.one("claude", lines)
        self.assertEqual(len(event["tool_calls"]), orca_calls.MAX_TOOL_NAMES)
        self.assertEqual(sum(event["tool_calls"].values()), 60)
        self.assertIn("other", event["tool_calls"])

    def codex(self, *items, cwd="/w/a"):
        out = [json.dumps({"type": "session_meta", "payload": {"id": "sess-1", "cwd": cwd}}),
               json.dumps({"type": "turn_context", "payload": {"cwd": cwd, "model": "gpt-x"}})]
        for item in items:
            if item[0] == "call":
                out.append(json.dumps({"type": "response_item", "payload": {"type": item[2] if len(item) > 2 else "function_call",
                                                                          "name": item[1], "call_id": f"c{len(out)}", "arguments": "SECRET-ARGUMENT"}}))
            else:
                out.append(json.dumps({"type": "token_usage_record", "timestamp": "2026-10-09T01:00:00Z",
                                       "payload": {"response_id": item[1], "session_id": "sess-1", "usage": CODEX_USAGE}}))
        return out

    def test_codex_attributes_calls_to_the_next_usage_record_and_resets(self):
        events, _ = self.extract("codex", self.codex(
            ("call", "exec", "custom_tool_call"), ("call", "exec", "custom_tool_call"), ("call", "send_message"), ("rec", "r1"),
            ("call", "wait"), ("rec", "r2"), ("rec", "r3")))
        by_request = {e["source_request_id_hash"]: e for e in events.values()}
        h = orca_calls._hash
        self.assertEqual(by_request[h("r1")]["tool_calls"], {"exec": 2, "send_message": 1})
        self.assertEqual(by_request[h("r1")]["tool_calls_total"], 3)
        self.assertEqual(by_request[h("r2")]["tool_calls"], {"wait": 1})
        self.assertEqual((by_request[h("r3")]["tool_calls"], by_request[h("r3")]["tool_calls_total"]), ({}, 0))

    def test_codex_failures_stay_unknown_and_trailing_calls_are_not_counted(self):
        events, _ = self.extract("codex", self.codex(("call", "exec"), ("rec", "r1"), ("call", "exec")))
        (event,) = events.values()
        self.assertIsNone(event["tool_failures"])
        self.assertEqual(event["tool_calls_total"], 1)  # the call after the last record belongs to a response not yet recorded
        self.assertNotIn("SECRET-ARGUMENT", json.dumps(event))

    def test_codex_calls_before_an_out_of_scope_record_do_not_leak_into_the_next_one(self):
        events, _ = self.extract("codex", self.codex(("call", "exec"), ("rec", "r1"), cwd="/elsewhere"))
        self.assertEqual(events, {})
        lines = self.codex(("call", "exec"), ("rec", "r1"), cwd="/elsewhere") + self.codex(("rec", "r2"))[2:]
        # r2 is also out of scope here; the point is that no pending call survives a record
        events, _ = self.extract("codex", lines)
        self.assertEqual(events, {})


if __name__ == "__main__":
    unittest.main()
