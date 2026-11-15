from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ai_subscription_exporter", ROOT / "exporter.py")
assert SPEC and SPEC.loader
EXPORTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXPORTER
SPEC.loader.exec_module(EXPORTER)

CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "claude_statusline_capture", ROOT / "claude_statusline_capture.py"
)
assert CAPTURE_SPEC and CAPTURE_SPEC.loader
CAPTURE = importlib.util.module_from_spec(CAPTURE_SPEC)
sys.modules[CAPTURE_SPEC.name] = CAPTURE
CAPTURE_SPEC.loader.exec_module(CAPTURE)


class ExporterTests(unittest.TestCase):
    def test_codex_rate_limits(self) -> None:
        payload = {
            "result": {
                "rateLimits": {
                    "primary": {"usedPercent": 21, "resetsAt": 1_800_000_000},
                    "secondary": {"usedPercent": 47.5, "resetsAt": 1_800_500_000},
                }
            }
        }
        self.assertEqual(
            EXPORTER.parse_codex_rate_limits(payload),
            {
                "session": (21.0, 1_800_000_000.0),
                "weekly": (47.5, 1_800_500_000.0),
            },
        )

    def test_codex_primary_window_uses_duration(self) -> None:
        payload = {
            "result": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 63,
                        "windowDurationMins": 10080,
                        "resetsAt": 1_800_000_000,
                    },
                    "secondary": None,
                }
            }
        }
        self.assertEqual(
            EXPORTER.parse_codex_rate_limits(payload),
            {"weekly": (63.0, 1_800_000_000.0)},
        )

    def test_codex_tokens_deduplicates_nested_totals(self) -> None:
        payload = {
            "result": {
                "usage": {"totalTokens": 100, "inputTokens": 60, "outputTokens": 40},
                "daily": [{"totalTokens": 10}],
            }
        }
        self.assertEqual(
            EXPORTER.parse_codex_tokens(payload),
            {("total", "all"): 100.0, ("input", "all"): 60.0, ("output", "all"): 40.0},
        )

    def test_claude_usage_api(self) -> None:
        payload = {
            "five_hour": {"utilization": 95.0, "resets_at": "2026-10-03T07:49:59.747443+00:00"},
            "seven_day": {"utilization": 6.0, "resets_at": "2026-10-07T15:59:59+00:00"},
            "seven_day_opus": None,
        }
        parsed = EXPORTER.parse_claude_usage(payload)
        self.assertEqual(parsed["session"][0], 95.0)
        self.assertEqual(parsed["weekly"][0], 6.0)
        self.assertAlmostEqual(parsed["session"][1], 1_791_013_799.747443, places=3)
        self.assertEqual(EXPORTER.parse_claude_usage({"five_hour": None}), {})

    def test_claude_falls_back_to_status_file(self) -> None:
        status = {"captured_at": time.time(), "rate_limits": {"five_hour": {"used_percentage": 7.0, "resets_at": 1_900_000_000}}}
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "claude-status.json"
            target.write_text(json.dumps(status), encoding="utf-8")
            env = {"CLAUDE_STATUS_FILE": str(target), "CLAUDE_USAGE_URL": "http://127.0.0.1:1/usage"}
            with patch.dict(os.environ, env), patch.object(EXPORTER, "_claude_access_token", return_value="x"), patch.object(EXPORTER, "_claude_usage_cache", None):
                result = EXPORTER.collect_claude()
        self.assertTrue(result.up)
        self.assertEqual(result.quotas["session"][0], 7.0)

    def test_claude_status(self) -> None:
        payload = {
            "rate_limits": {
                "five_hour": {"used_percentage": 12.5, "resets_at": 1_800_000_000},
                "seven_day": {"used_percentage": 65, "resets_at": 1_800_500_000},
            }
        }
        self.assertEqual(
            EXPORTER.parse_claude_status(payload),
            {
                "session": (12.5, 1_800_000_000.0),
                "weekly": (65.0, 1_800_500_000.0),
            },
        )

    def test_antigravity_usage(self) -> None:
        payload = {
            "command": {
                "name": "usage",
                "data": {
                    "groups": [
                        {
                            "name": "Gemini Models",
                            "buckets": [
                                {
                                    "id": "gemini-weekly",
                                    "window": "weekly",
                                    "remaining_fraction": 0.9957467913627625,
                                    "reset_time": "2026-10-08T07:40:47Z",
                                },
                                {
                                    "id": "gemini-5h",
                                    "window": "5h",
                                    "remaining_fraction": 0.9744806289672852,
                                    "reset_time": "2026-10-01T12:40:47Z",
                                },
                            ],
                        },
                        {
                            "name": "Claude and GPT models",
                            "buckets": [
                                {
                                    "id": "3p-weekly",
                                    "window": "weekly",
                                    "remaining_fraction": 1,
                                    "reset_time": "2026-10-08T07:53:15Z",
                                }
                            ],
                        },
                    ]
                },
            }
        }
        parsed = EXPORTER.parse_antigravity_usage(payload)
        self.assertAlmostEqual(parsed["gemini"]["weekly"][0], 0.42532086372375)
        self.assertEqual(parsed["gemini"]["weekly"][1], 1_791_445_247.0)
        self.assertAlmostEqual(parsed["gemini"]["session"][0], 2.5519371032714844)
        self.assertEqual(parsed["third_party"]["weekly"][0], 0.0)

    def test_antigravity_usage_ignores_unknown_buckets(self) -> None:
        payload = {
            "command": {
                "data": {
                    "groups": [
                        {
                            "buckets": [
                                {
                                    "id": "unknown-daily",
                                    "window": "daily",
                                    "remaining_fraction": 0.5,
                                }
                            ]
                        }
                    ]
                }
            }
        }
        self.assertEqual(EXPORTER.parse_antigravity_usage(payload), {})

    def test_antigravity_failure_keeps_stable_pool_labels(self) -> None:
        with patch.object(
            EXPORTER.subprocess,
            "run",
            side_effect=TimeoutError("controlled timeout"),
        ):
            results = EXPORTER.collect_antigravity()

        self.assertEqual(
            [result.labels for result in results],
            [{"pool": "gemini"}, {"pool": "third_party"}],
        )
        self.assertTrue(all(not result.up for result in results))
        self.assertTrue(all("controlled timeout" in result.error for result in results))

    def test_capture_strips_sensitive_status_fields(self) -> None:
        payload = {
            "session_id": "secret",
            "cwd": "/private/project",
            "model": {"display_name": "Opus"},
            "rate_limits": {"five_hour": {"used_percentage": 7, "resets_at": 99}},
        }
        safe = CAPTURE.sanitized_status(payload)
        self.assertNotIn("session_id", safe)
        self.assertNotIn("cwd", safe)
        self.assertEqual(safe["model"], "Opus")
        self.assertEqual(safe["rate_limits"]["five_hour"]["used_percentage"], 7.0)
        json.dumps(safe)

    def test_capture_preserves_last_valid_status_when_limits_are_missing(self) -> None:
        previous = {
            "captured_at": 1_800_000_000.0,
            "rate_limits": {
                "five_hour": {"used_percentage": 7.0, "resets_at": 1_800_001_000.0}
            },
            "model": "Opus",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "claude-status.json"
            target.write_text(json.dumps(previous), encoding="utf-8")
            with patch.dict(os.environ, {"CLAUDE_STATUS_FILE": str(target)}):
                CAPTURE.write_status(CAPTURE.sanitized_status({"model": {"display_name": "Opus"}}))

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), previous)


if __name__ == "__main__":
    unittest.main()
