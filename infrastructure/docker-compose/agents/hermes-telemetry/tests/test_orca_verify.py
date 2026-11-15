from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
SPEC = importlib.util.spec_from_file_location("orca_verify_cli", HERE.parent / "orca_verify.py")
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)
import orca_attribution as attr  # noqa: E402
import orca_calls  # noqa: E402
import orca_snapshot  # noqa: E402

KEY = "k" * 32
PY = sys.executable


class VerifyCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        patch = mock.patch.dict(os.environ, {"ORCA_VERIFIER_KEY": KEY, "HOME": str(self.root)})
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, command, *extra, post=None, key=True):
        argv = ["--task", "task-1", "--test-id", "unit", "--state-dir", str(self.state), "--cwd", str(self.root), *extra, "--", *command]
        out, err = io.StringIO(), io.StringIO()
        env = {} if key else {"ORCA_VERIFIER_KEY": ""}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if post is None:
                code = verify.main(argv)
            else:
                with mock.patch.object(verify.orca_task, "post", post), mock.patch.dict(os.environ, {"HERMES_TELEMETRY_WEBHOOK_URL": "https://example.invalid/h"}):
                    code = verify.main(argv)
        return code, out.getvalue(), err.getvalue()

    def sent_result(self, command, *extra):
        sent = []
        code, _, err = self.run_cli(command, *extra, post=lambda url, token, manifests, results, now: sent.append(results))
        return code, (sent[0][0] if sent else None), err

    def test_passing_command_yields_signed_verified_evidence(self):
        code, doc, _ = self.sent_result([PY, "-c", "raise SystemExit(0)"])
        self.assertEqual(code, 0)  # passing command and successful delivery
        self.assertEqual((doc["producer_type"], doc["verification_state"], doc["reported_status"], doc["evidence_kind"]),
                         ("verifier", "evidence_verified", "completed", "real"))
        result = attr.validate_result(doc)
        self.assertTrue(attr.signature_valid(result, KEY))
        self.assertEqual(attr.effective_verification(result, {"orca-verifier"}, KEY), "evidence_verified")

    def test_failing_command_is_reported_failed_with_its_real_exit_code(self):
        code, doc, _ = self.sent_result([PY, "-c", "raise SystemExit(3)"])
        self.assertEqual(code, 1)  # like the wrapped command: usable as `orca_verify ... -- tests && deploy`
        self.assertEqual((doc["verification_state"], doc["reported_status"], doc["test"]["exit_code"]), ("failed", "failed", 3))

    def test_payload_has_the_output_hash_but_neither_command_nor_output(self):
        secret_arg = "SECRET-ARG-MARKER"
        code, doc, _ = self.sent_result([PY, "-c", "import sys;print('SECRET-OUTPUT-MARKER');sys.exit(0)", secret_arg])
        blob = json.dumps(doc)
        for marker in (secret_arg, "SECRET-OUTPUT-MARKER", str(self.root)):
            self.assertNotIn(marker, blob)
        log = next((self.state / "evidence").glob("*.log"))
        self.assertEqual(hashlib.sha256(log.read_bytes()).hexdigest(), doc["artifact_sha256"])
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_unittest_and_pytest_summaries_become_counts(self):
        unittest_out = "....\n----------------------------------------------------------------------\nRan 10 tests in 0.1s\n\nFAILED (failures=2, errors=1, skipped=3)\n"
        self.assertEqual(verify.parse_counts(unittest_out), {"passed": 4, "failed": 3, "skipped": 3})
        self.assertEqual(verify.parse_counts("Ran 5 tests in 0.0s\n\nOK\n"), {"passed": 5, "failed": 0, "skipped": 0})
        self.assertEqual(verify.parse_counts("collected\n== 7 passed, 2 skipped in 1.2s ==\n"), {"passed": 7, "skipped": 2})
        self.assertEqual(verify.parse_counts("no recognisable summary"), {})

    def test_timeout_and_missing_command_are_failures(self):
        code, doc, _ = self.sent_result([PY, "-c", "import time;time.sleep(30)"], "--timeout", "1")
        self.assertEqual((doc["test"]["exit_code"], doc["verification_state"]), (124, "failed"))
        code, doc, _ = self.sent_result(["/definitely/not/a/command"])
        self.assertEqual((doc["test"]["exit_code"], doc["verification_state"]), (127, "failed"))

    def test_signing_key_is_required(self):
        code, _, err = self.run_cli([PY, "-c", "pass"], "--key-file", str(self.root / "missing.key"), "--dry-run", key=False)
        self.assertEqual(code, 2)
        self.assertIn("no signing key", err)

    def test_revision_advances_only_after_delivery(self):
        sent = []
        post = lambda url, token, manifests, results, now: sent.append(results[0])
        self.run_cli([PY, "-c", "pass"], post=post)
        self.run_cli([PY, "-c", "pass"], post=post)
        self.assertEqual([r["result_revision"] for r in sent], [1, 2])
        self.assertEqual(len({r["event_id"] for r in sent}), 2)

        def boom(*args):
            raise OSError("down")
        before = json.loads((self.state / "task-1__a1.verify.json").read_text())["revision"]
        code, _, _ = self.run_cli([PY, "-c", "pass"], post=boom)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((self.state / "task-1__a1.verify.json").read_text())["revision"], before)

    def test_dry_run_sends_nothing_and_records_nothing(self):
        code, out, _ = self.run_cli([PY, "-c", "pass"], "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(attr.signature_valid(attr.validate_result(json.loads(out)["orca_task_results"][0]), KEY))
        self.assertFalse(self.state.exists())


def signed(doc_overrides=None, key=KEY, producer="orca-verifier"):
    doc = {"schema_version": 1, "event_id": "ver-1", "task_id": "t", "attempt_id": "a", "result_revision": 1,
           "occurred_at": "2026-10-09T02:00:00Z", "producer_id": producer, "producer_type": "verifier",
           "evidence_kind": "real", "reported_status": "completed", "verification_state": "evidence_verified",
           "test": {"test_id": "unit", "exit_code": 0, "passed": 5}}
    doc.update(doc_overrides or {})
    if key:
        doc["signature"] = attr.sign_result(doc, key)
    return doc


class SignatureTrustTests(unittest.TestCase):
    approved = {"orca-verifier"}

    def effective(self, doc, key=KEY, approved=None):
        return attr.effective_verification(attr.validate_result(doc), self.approved if approved is None else approved, key)

    def test_valid_signature_from_approved_producer_is_trusted(self):
        self.assertEqual(self.effective(signed()), "evidence_verified")

    def test_claiming_the_verifier_id_without_a_signature_is_not_enough(self):
        self.assertEqual(self.effective(signed(key=None)), "unverified")

    def test_tampering_after_signing_is_detected(self):
        doc = signed()
        doc["test"]["passed"] = 999
        self.assertEqual(self.effective(doc), "unverified")
        doc = signed()
        doc["reported_status"] = "failed"
        self.assertEqual(self.effective(doc), "unverified")

    def test_wrong_key_unapproved_producer_and_agent_type_are_rejected(self):
        self.assertEqual(self.effective(signed(key="x" * 32)), "unverified")
        self.assertEqual(self.effective(signed(producer="someone-else")), "unverified")
        self.assertEqual(self.effective(signed({"producer_type": "agent"})), "unverified")
        self.assertEqual(self.effective(signed({"evidence_kind": "fixture"})), "unverified")

    def test_without_a_configured_key_the_previous_producer_id_rule_still_applies(self):
        self.assertEqual(self.effective(signed(key=None), key=None), "evidence_verified")
        self.assertEqual(self.effective(signed(key=None), key=None, approved=set()), "unverified")

    def test_signature_shape_is_validated(self):
        with self.assertRaises(attr.InvalidInput):
            attr.validate_result({**signed(key=None), "signature": "not-hex"})


class SnapshotStreamTests(unittest.TestCase):
    def snap(self, report=None, verification=None, key=KEY):
        r = attr.validate_result
        return orca_snapshot.build_snapshot("t", "a", "p", [], r(report) if report else None, {"orca-verifier"},
                                            verification_result=r(verification) if verification else None, verifier_key=key)

    def agent(self, status="completed", revision=2):
        return {"schema_version": 1, "event_id": f"res-{revision}", "task_id": "t", "attempt_id": "a", "result_revision": revision,
                "occurred_at": "2026-10-09T03:00:00Z", "producer_id": "orca-task-cli", "producer_type": "agent",
                "evidence_kind": "real", "reported_status": status, "verification_state": "unverified"}

    def test_a_later_agent_report_does_not_erase_the_verification(self):
        snap = self.snap(report=self.agent(revision=9), verification=signed())
        self.assertEqual((snap["reported_status"], snap["verification_state"]), ("completed", "evidence_verified"))
        self.assertEqual(snap["test"]["passed"], 5)  # the evidence, not the agent's claim
        self.assertIn("verification_artifact", snap) if "artifact_handle" in signed() else None

    def test_failed_verification_is_shown_even_if_the_agent_says_completed(self):
        snap = self.snap(report=self.agent(), verification=signed({"verification_state": "failed", "reported_status": "failed"}))
        self.assertEqual((snap["reported_status"], snap["verification_state"]), ("completed", "failed"))

    def test_agent_only_stays_unverified_and_forged_verifier_result_is_downgraded(self):
        self.assertEqual(self.snap(report=self.agent())["verification_state"], "unverified")
        forged = self.snap(report=self.agent(), verification=signed(key=None))
        self.assertEqual(forged["verification_state"], "unverified")

    def test_verifier_only_still_reports_a_status(self):
        snap = self.snap(verification=signed())
        self.assertEqual((snap["reported_status"], snap["verification_state"]), ("completed", "evidence_verified"))


class PipelineStreamTests(unittest.TestCase):
    def test_latest_result_is_kept_per_stream_not_overall(self):
        import orca_pipeline
        a = SnapshotStreamTests().agent
        results = [attr.validate_result(a(revision=2)), attr.validate_result(a(status="failed", revision=9)),
                   attr.validate_result(signed({"result_revision": 1})), attr.validate_result(signed({"result_revision": 3, "event_id": "ver-3"})),
                   attr.validate_result({**a(), "task_id": "other", "event_id": "o-1"})]
        streams = orca_pipeline._latest_results(results)[("t", "a")]
        self.assertEqual((streams["report"]["result_revision"], streams["report"]["reported_status"]), (9, "failed"))
        self.assertEqual(streams["verify"]["result_revision"], 3)
        self.assertEqual(set(orca_pipeline._latest_results(results)), {("t", "a"), ("other", "a")})
        self.assertNotIn("verify", orca_pipeline._latest_results(results)[("other", "a")])


    def test_with_a_key_an_unsigned_verifier_result_cannot_displace_the_genuine_one(self):
        import orca_pipeline
        genuine = signed({"result_revision": 1})
        forged = signed({"result_revision": 99, "event_id": "ver-99", "verification_state": "failed"}, key=None)
        loaded = orca_pipeline._validated([], [genuine, forged], False, KEY)
        stream = loaded["results"][("t", "a")]["verify"]
        self.assertEqual((stream["result_revision"], stream["verification_state"]), (1, "evidence_verified"))
        self.assertEqual(loaded["rejected"], {"result:verifier_signature_invalid": 1})
        # without a configured key the previous behaviour is unchanged
        legacy = orca_pipeline._validated([], [genuine, forged], False, None)
        self.assertEqual(legacy["results"][("t", "a")]["verify"]["result_revision"], 99)


if __name__ == "__main__":
    unittest.main()
