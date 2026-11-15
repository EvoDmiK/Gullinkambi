from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


calls = load("orca_calls", HERE.parent / "orca_calls.py")
attr = load("orca_attribution", HERE.parent / "orca_attribution.py")
SCHEMAS = HERE.parent / "schemas"

MANIFEST = json.loads((SCHEMAS / "example-manifest.json").read_text())
RESULT = json.loads((SCHEMAS / "example-result.json").read_text())


def event(request="r1", session="sess-abc123", at="2026-10-09T01:30:00Z"):
    return {"source_request_id_hash": calls._hash(request),
            "session_id_hash": calls._hash(session), "occurred_at": at}


def manifest(**overrides):
    doc = {**MANIFEST, **overrides}
    return attr.validate_manifest(doc)


def by_request_manifest(**overrides):
    doc = {k: v for k, v in {**MANIFEST, "request_ids": ["r1"], **overrides}.items()
           if k not in ("window", "session_id")}
    return attr.validate_manifest(doc)


class SchemaConsistencyTests(unittest.TestCase):
    def test_examples_validate(self):
        attr.validate_manifest(MANIFEST)
        attr.validate_result(RESULT)

    def test_schema_property_names_match_validator_allowlists(self):
        for name, allowed in (("orca-manifest", attr.MANIFEST_KEYS), ("orca-result", attr.RESULT_KEYS)):
            schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
            self.assertEqual(set(schema["properties"]), allowed, name)
        result_schema = json.loads((SCHEMAS / "orca-result.schema.json").read_text())
        self.assertEqual(set(result_schema["properties"]["test"]["properties"]), attr.TEST_KEYS)
        id_pattern = json.loads((SCHEMAS / "orca-result.schema.json").read_text())["$defs"]["id"]["pattern"]
        self.assertEqual(id_pattern, "^" + attr.ID_RE.pattern + "$")


class ValidationTests(unittest.TestCase):
    def reject(self, fn, doc, code):
        with self.assertRaisesRegex(attr.InvalidInput, re.escape(code)):
            fn(doc)

    def test_manifest_rejects_free_text_and_paths(self):
        self.reject(attr.validate_manifest, {**MANIFEST, "title": "fix bug"}, "unknown_field:title")
        self.reject(attr.validate_manifest, {**MANIFEST, "task_id": "/Users/x/proj"}, "task_id_invalid")
        self.reject(attr.validate_manifest, {**MANIFEST, "project_key": "my project"}, "project_key_invalid")

    def test_manifest_needs_exactly_one_scope(self):
        self.reject(attr.validate_manifest, {**MANIFEST, "request_ids": ["a"]}, "exactly_one_of")
        no_scope = {k: v for k, v in MANIFEST.items() if k != "window"}
        self.reject(attr.validate_manifest, no_scope, "exactly_one_of")

    def test_window_must_be_utc_and_non_empty(self):
        naive = {**MANIFEST, "window": {"start": "2026-10-09T01:00:00", "end": "2026-10-09T02:00:00"}}
        self.reject(attr.validate_manifest, naive, "not_utc")
        empty = {**MANIFEST, "window": {"start": "2026-10-09T01:00:00Z", "end": "2026-10-09T01:00:00Z"}}
        self.reject(attr.validate_manifest, empty, "window_empty")

    def test_result_rejects_bad_status_and_missing_checksum(self):
        self.reject(attr.validate_result, {**RESULT, "reported_status": "passed"}, "reported_status_invalid")
        no_sum = {k: v for k, v in RESULT.items() if k != "artifact_sha256"}
        self.reject(attr.validate_result, no_sum, "artifact_sha256_invalid")
        self.reject(attr.validate_result, {**RESULT, "test": {"test_id": "u", "exit_code": 0, "log": "x"}},
                    "unknown_field:log")

    def test_verified_requires_approved_real_verifier(self):
        result = attr.validate_result(RESULT)  # fixture evidence
        self.assertEqual(attr.effective_verification(result, {"verifier-1"}), "unverified")
        real = attr.validate_result({**RESULT, "evidence_kind": "real"})
        self.assertEqual(attr.effective_verification(real, {"verifier-1"}), "evidence_verified")
        self.assertEqual(attr.effective_verification(real, set()), "unverified")
        agent = attr.validate_result({**RESULT, "evidence_kind": "real", "producer_type": "agent"})
        self.assertEqual(attr.effective_verification(agent, {"verifier-1"}), "unverified")
        failed = attr.validate_result({**RESULT, "verification_state": "failed"})
        self.assertEqual(attr.effective_verification(failed, set()), "failed")


class AttributionTests(unittest.TestCase):
    h = staticmethod(calls._hash)

    def test_window_is_half_open(self):
        m = [manifest()]
        self.assertEqual(attr.attribute(event(at="2026-10-09T01:00:00Z"), m, self.h)["attribution_status"], "explicit")
        self.assertEqual(attr.attribute(event(at="2026-10-09T02:30:00Z"), m, self.h)["attribution_status"], "unassigned")

    def test_request_id_mapping_and_other_session_unassigned(self):
        m = [by_request_manifest()]
        got = attr.attribute(event(request="r1", session="other"), m, self.h)
        self.assertEqual((got["task_id"], got["attribution_method"]), ("task-0001", "explicit_request"))
        self.assertEqual(attr.attribute(event(request="r2", session="other"), m, self.h)["attribution_status"], "unassigned")

    def test_overlapping_attempts_are_ambiguous_not_double_counted(self):
        m = [manifest(), manifest(task_id="task-0002")]
        got = attr.attribute(event(), m, self.h)
        self.assertEqual((got["attribution_status"], got["task_id"]), ("ambiguous", None))

    def test_missing_timestamp_is_ambiguous_for_window_claims(self):
        got = attr.attribute(event(at=None), [manifest()], self.h)
        self.assertEqual(got["attribution_status"], "ambiguous")

    def test_higher_revision_supersedes_and_corrects(self):
        old = manifest()
        moved = manifest(revision=2, window={"start": "2026-10-09T03:00:00Z", "end": "2026-10-09T04:00:00Z"})
        self.assertEqual(attr.attribute(event(), [old, moved], self.h)["attribution_status"], "unassigned")
        self.assertEqual(attr.attribute(event(), [moved, old], self.h)["attribution_status"], "unassigned")
        self.assertEqual(attr.attribute(event(), [old], self.h)["attribution_status"], "explicit")

    def test_conflicting_request_and_window_claims_are_ambiguous(self):
        by_request = by_request_manifest(task_id="task-0002")
        got = attr.attribute(event(request="r1"), [manifest(), by_request], self.h)
        self.assertEqual(got["attribution_status"], "ambiguous")

    def test_same_attempt_claimed_twice_is_not_a_conflict(self):
        # same task/attempt, higher revision replaces the window manifest entirely
        by_request = by_request_manifest(revision=2)
        got = attr.attribute(event(request="r1"), [manifest(), by_request], self.h)
        self.assertEqual((got["attribution_status"], got["attribution_method"]), ("explicit", "explicit_request"))


class HashedManifestTests(unittest.TestCase):
    """The CLI sends hashes only, so raw session/request IDs never reach the webhook or DB."""

    def hashed(self, **overrides):
        doc = {k: v for k, v in MANIFEST.items() if k != "session_id"}
        doc.update(session_id_hash=calls._hash("sess-abc123"), **overrides)
        return attr.validate_manifest(doc)

    def test_hashed_and_raw_manifests_attribute_identically(self):
        raw, hashed = attr.validate_manifest(MANIFEST), self.hashed()
        self.assertEqual(raw["session_hash"], hashed["session_hash"])
        self.assertEqual(attr.attribute(event(), [raw]), attr.attribute(event(), [hashed]))
        self.assertEqual(attr.attribute(event(), [hashed])["attribution_status"], "explicit")

    def test_request_hashes_match_calls(self):
        doc = {k: v for k, v in MANIFEST.items() if k not in ("window", "session_id")}
        m = attr.validate_manifest({**doc, "request_id_hashes": [calls._hash("r1")]})
        self.assertEqual(attr.attribute(event(request="r1", session="x"), [m])["attribution_method"], "explicit_request")
        self.assertEqual(attr.attribute(event(request="r2", session="x"), [m])["attribution_status"], "unassigned")

    def test_raw_values_do_not_survive_validation(self):
        m = attr.validate_manifest(MANIFEST)
        self.assertNotIn("sess-abc123", repr(m))

    def test_invalid_hash_inputs_are_rejected(self):
        for bad in ({"session_id_hash": "not-a-hash"}, {"session_id_hash": calls._hash("a"), "session_id": "sess-1"}):
            doc = {k: v for k, v in MANIFEST.items() if k != "session_id"}
            with self.assertRaises(attr.InvalidInput):
                attr.validate_manifest({**doc, **bad})
        no_session = {k: v for k, v in MANIFEST.items() if k != "session_id"}
        with self.assertRaisesRegex(attr.InvalidInput, "session_id_invalid"):
            attr.validate_manifest(no_session)
        both = {k: v for k, v in MANIFEST.items() if k not in ("window", "session_id")}
        with self.assertRaisesRegex(attr.InvalidInput, "both_given"):
            attr.validate_manifest({**both, "request_ids": ["r"], "request_id_hashes": [calls._hash("r")]})


if __name__ == "__main__":
    unittest.main()
