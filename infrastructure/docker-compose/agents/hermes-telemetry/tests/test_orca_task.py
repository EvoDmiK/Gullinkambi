from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
SPEC = importlib.util.spec_from_file_location("orca_task_cli", HERE.parent / "orca_task.py")
cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli
SPEC.loader.exec_module(cli)
import orca_attribution  # noqa: E402
import orca_calls  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


class OrcaTaskCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.cwd = str(self.root / "work" / "repo")
        Path(self.cwd).mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *argv, post=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if post is None:
                code = cli.main(list(argv))
            else:
                with mock.patch.object(cli, "post", post), mock.patch.dict(os.environ, {"HERMES_TELEMETRY_WEBHOOK_URL": "https://example.invalid/hook"}):
                    code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def common(self, *extra):
        return ["--state-dir", str(self.state), *extra]

    def test_dry_run_begin_output_is_valid_and_hides_the_raw_session(self):
        code, out, _ = self.run_cli("begin", "--task", "task-1", "--session", SESSION, "--cwd", self.cwd, "--dry-run", *self.common())
        self.assertEqual(code, 0)
        doc = json.loads(out)["orca_task_manifests"][0]
        manifest = orca_attribution.validate_manifest(doc)
        self.assertEqual(manifest["session_hash"], orca_calls._hash(SESSION))
        self.assertNotIn(SESSION, out)
        self.assertNotIn(self.cwd, out)
        self.assertEqual(list(self.state.glob("*")), [])  # dry-run leaves no state

    def test_global_options_work_after_the_subcommand(self):
        code, _, err = self.run_cli("end", "--task", "t", "--status", "completed", "--dry-run", *self.common())
        self.assertEqual(code, 2)  # parsed fine; fails only because begin was never recorded
        self.assertIn("no begin recorded", err)

    def test_auto_session_picks_the_recent_claude_session_for_this_directory(self):
        encoded = "".join(c if c.isalnum() else "-" for c in self.cwd)
        folder = self.root / ".claude" / "projects" / encoded
        folder.mkdir(parents=True)
        (folder / f"{SESSION}.jsonl").write_text("{}\n")
        code, out, _ = self.run_cli("begin", "--task", "t", "--auto-session", "--cwd", self.cwd, "--dry-run", *self.common())
        self.assertEqual(code, 0)
        self.assertEqual(orca_attribution.validate_manifest(json.loads(out)["orca_task_manifests"][0])["session_hash"], orca_calls._hash(SESSION))

    def test_auto_session_finds_a_codex_session_by_working_directory(self):
        now = time.time()
        day = self.root / ".codex" / "sessions" / time.strftime("%Y/%m/%d", time.localtime(now))
        day.mkdir(parents=True)
        (day / "rollout-a.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"id": "codex-sess-1", "cwd": self.cwd}}) + "\n")
        (day / "rollout-b.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"id": "other", "cwd": "/elsewhere"}}) + "\n")
        self.assertEqual(cli.detect_session(self.cwd, self.root, now), "codex-sess-1")

    def test_auto_session_refuses_stale_or_missing_sessions(self):
        encoded = "".join(c if c.isalnum() else "-" for c in self.cwd)
        folder = self.root / ".claude" / "projects" / encoded
        folder.mkdir(parents=True)
        log = folder / "old.jsonl"
        log.write_text("{}\n")
        old = time.time() - 3600
        os.utime(log, (old, old))
        self.assertIsNone(cli.detect_session(self.cwd, self.root, time.time()))
        code, _, err = self.run_cli("begin", "--task", "t", "--auto-session", "--cwd", self.cwd, "--dry-run", *self.common())
        self.assertEqual(code, 2)
        self.assertIn("no session given", err)

    def test_begin_then_end_round_trip(self):
        sent = []
        post = lambda url, token, manifests, results, now: sent.append((manifests, results))
        self.assertEqual(self.run_cli("begin", "--task", "task-1", "--session", SESSION, "--cwd", self.cwd, *self.common(), post=post)[0], 0)
        code, out, _ = self.run_cli("end", "--task", "task-1", "--status", "completed", "--test-id", "unit", "--exit-code", "0",
                                    "--passed", "5", "--failed", "0", *self.common(), post=post)
        self.assertEqual(code, 0)
        self.assertIn("reported, not verified", out)
        (m1, r1), (m2, r2) = sent
        self.assertEqual((m1[0]["revision"], m2[0]["revision"]), (1, 2))
        self.assertEqual(r1, [])
        window1, window2 = m1[0]["window"], m2[0]["window"]
        self.assertEqual(window1["start"], window2["start"])
        self.assertLess(window2["end"], window1["end"])  # the 24h provisional window is closed early
        result = orca_attribution.validate_result(r2[0])
        self.assertEqual((result["reported_status"], result["verification_state"], result["evidence_kind"]), ("completed", "unverified", "real"))
        self.assertEqual(result["test"], {"test_id": "unit", "exit_code": 0, "passed": 5, "failed": 0})
        # ending again is a new revision, never an overwrite
        self.run_cli("end", "--task", "task-1", "--status", "failed", *self.common(), post=post)
        self.assertEqual((sent[2][0][0]["revision"], sent[2][1][0]["result_revision"]), (3, 2))
        self.assertNotEqual(sent[2][1][0]["event_id"], r2[0]["event_id"])

    def test_payload_items_carry_the_fields_the_n8n_node_requires(self):
        sent = []
        post = lambda url, token, manifests, results, now: sent.append((manifests, results))
        self.run_cli("begin", "--task", "task-1", "--session", SESSION, "--cwd", self.cwd, *self.common(), post=post)
        self.run_cli("end", "--task", "task-1", "--status", "blocked", *self.common(), post=post)
        manifest, result = sent[1][0][0], sent[1][1][0]
        for field in ("task_id", "attempt_id", "revision", "producer_id", "project_key"):
            self.assertTrue(manifest.get(field), field)
        for field in ("event_id", "task_id", "attempt_id", "result_revision", "producer_id"):
            self.assertTrue(result.get(field), field)

    def test_failed_delivery_records_no_state_and_reports_only_the_error_class(self):
        def boom(*args):
            raise OSError("secret-host.internal refused")
        code, _, err = self.run_cli("begin", "--task", "t", "--session", SESSION, "--cwd", self.cwd, *self.common(), post=boom)
        self.assertEqual(code, 1)
        self.assertIn("OSError", err)
        self.assertNotIn("secret-host", err)
        self.assertEqual(list(self.state.glob("*")), [])

    def test_invalid_ids_and_incomplete_test_info_are_rejected_before_sending(self):
        sent = []
        post = lambda *a: sent.append(a)
        code, _, err = self.run_cli("begin", "--task", "bad task!", "--session", SESSION, "--cwd", self.cwd, *self.common(), post=post)
        self.assertEqual((code, sent), (2, []))
        self.run_cli("begin", "--task", "ok", "--session", SESSION, "--cwd", self.cwd, *self.common(), post=post)
        code, _, err = self.run_cli("end", "--task", "ok", "--status", "completed", "--test-id", "unit", *self.common(), post=post)
        self.assertEqual(code, 2)
        self.assertIn("test_requires_exit_code", err)
        self.assertEqual(len(sent), 1)

    def test_env_file_reads_only_the_webhook_settings(self):
        env = self.root / "env"
        env.write_text("OTHER_SECRET=abc\nHERMES_TELEMETRY_WEBHOOK_URL='https://h/x'\nHERMES_TELEMETRY_WEBHOOK_TOKEN=tok\n")
        self.assertEqual(cli.read_env_file(env), {"HERMES_TELEMETRY_WEBHOOK_URL": "https://h/x", "HERMES_TELEMETRY_WEBHOOK_TOKEN": "tok"})


if __name__ == "__main__":
    unittest.main()
