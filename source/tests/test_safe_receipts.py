"""Synthetic-only controls. No real secret, product, network or process use.

This file is intentionally not executed by its drafting role. Standard unittest
runner emits test names and fixed errors; sentinel values are never printed.
"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import safe_receipts as sr


# Fictional in-memory sentinel. Its exact value is never asserted into output.
FICTIONAL = "FICTIONAL_ONLY_DO_NOT_EXPORT_7c3e98ea"


class SafeReceiptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ho2_safe_contract_")
        self.base = Path(self.temp.name)
        self.root = self.base / "review_safe" / "fixture"
        self.run = sr.issue_alias("run")
        self.context = dict(build_id="HO2-E2E-20261020-R6", source_sha256="A" * 64,
            package_sha256="B" * 64, case_id="SAFE_SCHEMA", run_alias=self.run)
        self.writer = sr.SafeReceiptWriter(self.root, **self.context)

    def tearDown(self):
        self.temp.cleanup()

    def business(self, **overrides):
        fields = dict(profile="AUTONOMY", phase="FINISHED", effects=3, roots=3, approvals=0, version=3)
        fields.update(overrides)
        return fields

    def stop(self, **overrides):
        fields = dict(requested_by="DEADLINE", initiating_reason="NATURAL_DEADLINE",
            forwarded_reason="NATURAL_DEADLINE", observed_reason="NATURAL_DEADLINE",
            acknowledged=True, stage="CLEANED", cleanup_complete=True)
        fields.update(overrides)
        return fields

    def reject(self, event, origin, fields, code):
        before = (self.root / "events.jsonl").read_bytes()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = self.writer.emit(event, origin, **fields)
        self.assertFalse(result, "negative control was accepted")
        self.assertEqual(self.writer.last_error, code)
        self.assertTrue(before == (self.root / "events.jsonl").read_bytes(), "rejected input changed export bytes")
        self.assertTrue(FICTIONAL not in out.getvalue() + err.getvalue(), "negative control escaped to output")
        self.assertTrue(FICTIONAL.encode() not in (self.root / "events.jsonl").read_bytes(), "negative control escaped to receipt")

    def test_legitimate_business_and_aliases_are_complete(self):
        root, episode, scene = (sr.issue_alias(k) for k in ("root", "episode", "scene"))
        fields = self.business(root_alias=root, episode_alias=episode, action="A", status="APPLIED", decision="ALLOW", queries=0, submissions=1, confirmed_version=1)
        self.assertTrue(self.writer.emit("BUSINESS", "SERVER", scene_alias=scene, generation=2, **fields))
        value = json.loads((self.root / "events.jsonl").read_bytes())
        self.assertEqual(value["fields"]["effects"], 3)
        self.assertEqual(value["fields"]["roots"], 3)
        self.assertEqual(value["fields"]["approvals"], 0)
        self.assertEqual(value["fields"]["root_alias"], root.value)
        self.assertEqual(value["fields"]["episode_alias"], episode.value)
        self.assertEqual(value["scene_alias"], scene.value)
        self.assertEqual(value["record_origin"], "SERVER")
        self.assertEqual(value["seq"], 1)
        self.assertEqual(value["source_sha256"], "A" * 64)

    def test_legitimate_request_and_client_origins_remain_distinct(self):
        request = sr.issue_alias("request")
        self.assertTrue(self.writer.emit("CLIENT", "CLIENT", gesture="UNLOCK", result="RECEIVED", request_alias=request))
        self.assertTrue(self.writer.emit("REQUEST", "SERVER", route="SESSION", result="ACCEPTED", http_status=200,
            entered_session=True, entered_start=False, scene_match=True, request_alias=request))
        records = [json.loads(line) for line in (self.root / "events.jsonl").read_bytes().splitlines()]
        self.assertEqual([r["record_origin"] for r in records], ["CLIENT", "SERVER"])
        self.assertEqual(records[1]["fields"]["request_alias"], request.value)

    def test_nested_credentials_are_unknown_fields_not_recursively_scrubbed(self):
        self.reject("BUSINESS", "SERVER", self.business(snapshot={"nested": {"token": FICTIONAL}}), "UNKNOWN_FIELDS")

    def test_arbitrary_exception_and_stdout_are_rejected(self):
        self.reject("BUSINESS", "SERVER", self.business(error=RuntimeError(FICTIONAL)), "UNKNOWN_FIELDS")
        self.reject("BUSINESS", "SERVER", self.business(stdout="Preview code: " + FICTIONAL), "UNKNOWN_FIELDS")
        self.reject("BUSINESS", "SERVER", self.business(**{FICTIONAL: "anything"}), "UNKNOWN_FIELDS")

    def test_oversize_non_ascii_and_invalid_enum_are_rejected(self):
        for value in ("X" * 100000, "非ASCII虚构值", FICTIONAL):
            self.reject("BUSINESS", "SERVER", self.business(profile=value), "INVALID_VALUE")

    def test_missing_fields_and_wrong_types_are_rejected(self):
        missing = self.business()
        del missing["effects"]
        self.reject("BUSINESS", "SERVER", missing, "MISSING_FIELDS")
        self.reject("BUSINESS", "SERVER", self.business(effects=True), "INVALID_TYPE")
        self.reject("BUSINESS", "SERVER", self.business(effects={"token": FICTIONAL}), "INVALID_TYPE")
        self.reject("BUSINESS", "SERVER", self.business(effects=-1), "INVALID_VALUE")

    def test_invalid_alias_and_event_do_not_echo_input(self):
        self.reject("BUSINESS", "SERVER", self.business(root_alias=FICTIONAL), "INVALID_ALIAS")
        self.reject("BUSINESS", "SERVER", self.business(root_alias=sr.Alias("root", FICTIONAL)), "INVALID_ALIAS")
        self.reject(FICTIONAL, "SERVER", self.business(), "UNKNOWN_EVENT")
        self.reject("BUSINESS", FICTIONAL, self.business(), "INVALID_ORIGIN")

    def test_aliases_are_random_and_not_authentication_values(self):
        aliases = [sr.issue_alias("root") for _ in range(32)]
        self.assertEqual(len({a.value for a in aliases}), 32)
        self.assertTrue(all(a.value.startswith("root_") and len(a.value) == 37 for a in aliases))
        # No key/token/identity argument exists in the alias issuing API.
        with self.assertRaises(TypeError):
            sr.issue_alias("root", FICTIONAL)

    def test_native_self_report_cannot_become_os_observation(self):
        process = sr.issue_alias("process")
        self.reject("NATIVE", "APPLICATION", dict(process_alias=process, observation_method="APPLICATION_SELF",
            native_exit_observed=True, native_exit_code=0), "INVALID_ORIGIN")
        self.reject("NATIVE", "OS_HANDLE", dict(process_alias=process, observation_method="CREATOR_PROCESS_HANDLE",
            native_exit_observed=False, native_exit_code=0), "INVALID_VALUE")
        self.assertTrue(self.writer.emit("NATIVE", "OS_HANDLE", process_alias=process,
            observation_method="CREATOR_PROCESS_HANDLE", native_exit_observed=True, native_exit_code=0))

    def test_port_refused_denied_timeout_stay_distinct(self):
        for classification, errno in (("CONNECTION_REFUSED", 10061), ("ACCESS_DENIED", 10013), ("TIMEOUT", 10060)):
            self.assertTrue(self.writer.emit("PORT", "OS_HANDLE", observation_method="LOOPBACK_CONNECT",
                classification=classification, actual_errno=errno, duration_ms=25))
        self.reject("PORT", "OS_HANDLE", dict(observation_method="LOOPBACK_CONNECT", classification="CONNECTION_REFUSED",
            actual_errno=10013, duration_ms=25), "INVALID_VALUE")

    def test_stop_preserves_deadline_and_does_not_claim_operator_stop(self):
        self.assertTrue(self.writer.emit("STOP", "APPLICATION", **self.stop(operator_stop_applicable=False)))
        value = json.loads((self.root / "events.jsonl").read_bytes())
        self.assertEqual(value["fields"]["initiating_reason"], "NATURAL_DEADLINE")
        self.assertEqual(value["fields"]["forwarded_reason"], "NATURAL_DEADLINE")
        self.assertEqual(value["fields"]["observed_reason"], "NATURAL_DEADLINE")
        self.assertFalse(value["fields"]["operator_stop_applicable"])

    def test_write_failure_is_sticky_and_emergency_is_independent(self):
        with mock.patch.object(Path, "open", side_effect=OSError(FICTIONAL)):
            self.assertFalse(self.writer.emit("BUSINESS", "SERVER", **self.business()))
        self.assertTrue(self.writer.failed)
        self.assertEqual(self.writer.last_error, "WRITE_FAILED")
        self.assertFalse(self.writer.emit("BUSINESS", "SERVER", **self.business()))
        self.assertEqual(self.writer.last_error, "WRITER_FAILED")
        emergency = sr.EmergencyReceiptWriter(self.root, **self.context)
        self.assertTrue(emergency.record(**self.stop(initiating_reason="AUDIT_FAILURE", forwarded_reason="AUDIT_FAILURE",
            observed_reason="AUDIT_FAILURE", requested_by="AUDIT")))
        self.assertIsNotNone(emergency.memory_record)
        self.assertFalse(emergency.record(**self.stop()))

    def test_emergency_failure_attempts_io_only_once_and_keeps_memory(self):
        emergency = sr.EmergencyReceiptWriter(self.root, **self.context)
        with mock.patch.object(Path, "open", side_effect=OSError(FICTIONAL)) as opened:
            self.assertFalse(emergency.record(**self.stop()))
            self.assertFalse(emergency.record(**self.stop()))
            self.assertEqual(opened.call_count, 1)
        self.assertIsNotNone(emergency.memory_record)
        self.assertEqual(emergency.last_error, "WRITE_FAILED")

    def test_record_and_file_limits_are_bounded(self):
        with mock.patch.object(sr, "MAX_RECORDS", 1):
            self.assertTrue(self.writer.emit("BUSINESS", "SERVER", **self.business()))
            self.assertFalse(self.writer.emit("BUSINESS", "SERVER", **self.business()))
        self.assertTrue(self.writer.failed)
        self.assertEqual(self.writer.last_error, "RECORD_LIMIT")

    def test_monotonic_relative_time_and_sequence(self):
        root = self.base / "review_safe" / "clock"
        values = iter([5000000, 7000000, 6000000])
        writer = sr.SafeReceiptWriter(root, **self.context, clock=lambda: next(values))
        self.assertTrue(writer.emit("BUSINESS", "SERVER", **self.business()))
        self.assertFalse(writer.emit("BUSINESS", "SERVER", **self.business()))
        self.assertEqual(writer.last_error, "INVALID_SEQUENCE")
        self.assertTrue(writer.failed)

    def test_export_preserves_valid_bytes_and_only_named_files(self):
        self.assertTrue(self.writer.emit("BUSINESS", "SERVER", **self.business()))
        (self.root / "unlisted.txt").write_text(FICTIONAL, encoding="utf-8")
        exported = self.base / "export"
        receipt = sr.export_review_safe(self.root, exported)
        self.assertEqual({r["artifact"] for r in receipt}, {"identity.json", "events.jsonl"})
        self.assertEqual({p.name for p in exported.iterdir()}, {"identity.json", "events.jsonl"})
        self.assertTrue((exported / "events.jsonl").read_bytes() == (self.root / "events.jsonl").read_bytes())
        self.assertTrue(FICTIONAL.encode() not in b"".join(p.read_bytes() for p in exported.iterdir()), "unlisted bytes escaped")

    def test_export_rejects_members_absolute_dotdot_and_private_names(self):
        for name in ("../local_only/key", "C:\\private\\key", "/private/key", "preview-control.key", "local_only", "unlisted.txt"):
            with self.assertRaises(sr.ReceiptError) as caught:
                sr.export_review_safe(self.root, self.base / "export", [name])
            self.assertEqual(caught.exception.code, "UNAPPROVED_ARTIFACT")
        self.assertFalse((self.base / "export").exists())

    def test_export_rejects_reparse_ancestor_before_any_read(self):
        original = sr._is_reparse
        with mock.patch.object(sr, "_is_reparse", side_effect=lambda p: p == self.base or original(p)):
            with mock.patch.object(Path, "open") as opened:
                with self.assertRaises(sr.ReceiptError) as caught:
                    sr.export_review_safe(self.root, self.base / "export")
                self.assertEqual(caught.exception.code, "REPARSE_PATH")
                opened.assert_not_called()

    def test_export_rejects_private_root_before_read(self):
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.base / "local_only", self.base / "export")
        self.assertEqual(caught.exception.code, "INVALID_PATH")

    def test_export_rejects_truncated_json_without_output(self):
        (self.root / "events.jsonl").write_bytes(b'{"partial":')
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, self.base / "export")
        self.assertEqual(caught.exception.code, "INVALID_JSON")
        self.assertFalse((self.base / "export").exists())

    def test_export_rejects_nested_unknown_content_without_output(self):
        self.assertTrue(self.writer.emit("BUSINESS", "SERVER", **self.business()))
        row = json.loads((self.root / "events.jsonl").read_bytes())
        row["fields"]["nested"] = {"secret": FICTIONAL}
        (self.root / "events.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, self.base / "export")
        self.assertEqual(caught.exception.code, "UNKNOWN_FIELDS")
        self.assertFalse((self.base / "export").exists())

    def test_duplicate_json_key_is_rejected(self):
        (self.root / "identity.json").write_bytes(b'{"schema_version":"x","schema_version":"y"}\n')
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, self.base / "export")
        self.assertEqual(caught.exception.code, "DUPLICATE_FIELD")

    def test_export_requires_identity_anchor(self):
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, self.base / "export", ["events.jsonl"])
        self.assertEqual(caught.exception.code, "MISSING_FIELDS")
        self.assertFalse((self.base / "export").exists())

    def test_export_rejects_each_identity_context_mismatch_before_writing(self):
        self.assertTrue(self.writer.emit("BUSINESS", "SERVER", **self.business()))
        original = json.loads((self.root / "identity.json").read_bytes())
        changes = {"build_id": "HO2-E2E-OTHER-R6", "source_sha256": "C" * 64,
            "package_sha256": "D" * 64, "case_id": "T10_OTHER", "run_alias": sr.issue_alias("run").value}
        for key, value in changes.items():
            changed = dict(original, **{key: value})
            (self.root / "identity.json").write_text(json.dumps(changed) + "\n", encoding="utf-8")
            with self.assertRaises(sr.ReceiptError) as caught:
                sr.export_review_safe(self.root, self.base / "export")
            self.assertEqual(caught.exception.code, "CONTEXT_MISMATCH")
            self.assertFalse((self.base / "export").exists())
        (self.root / "identity.json").write_text(json.dumps(original) + "\n", encoding="utf-8")

    def test_export_rejects_lifecycle_context_mismatch(self):
        self.assertTrue(self.writer.emit("STOP", "APPLICATION", **self.stop()))
        row = json.loads((self.root / "events.jsonl").read_bytes())
        row["run_alias"] = sr.issue_alias("run").value
        lifecycle = {"schema_version": sr.SCHEMA_VERSION, "records": [row]}
        (self.root / "lifecycle.json").write_text(json.dumps(lifecycle) + "\n", encoding="utf-8")
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, self.base / "export", ["identity.json", "events.jsonl", "lifecycle.json"])
        self.assertEqual(caught.exception.code, "CONTEXT_MISMATCH")
        self.assertFalse((self.base / "export").exists())

    def test_export_rejects_emergency_context_mismatch(self):
        emergency = sr.EmergencyReceiptWriter(self.root, **self.context)
        self.assertTrue(emergency.record(**self.stop()))
        row = json.loads((self.root / "emergency.json").read_bytes())
        row["package_sha256"] = "C" * 64
        (self.root / "emergency.json").write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, self.base / "export", ["identity.json", "emergency.json"])
        self.assertEqual(caught.exception.code, "CONTEXT_MISMATCH")
        self.assertFalse((self.base / "export").exists())

    def test_export_all_four_consistent_artifacts_keeps_same_context(self):
        self.assertTrue(self.writer.emit("STOP", "APPLICATION", **self.stop()))
        row = json.loads((self.root / "events.jsonl").read_bytes())
        self.writer.write_lifecycle([row])
        emergency = sr.EmergencyReceiptWriter(self.root, **self.context)
        self.assertTrue(emergency.record(**self.stop()))
        names = ["emergency.json", "events.jsonl", "lifecycle.json", "identity.json"]
        receipt = sr.export_review_safe(self.root, self.base / "export", names)
        self.assertEqual({r["artifact"] for r in receipt}, set(names))

    def test_lifecycle_record_and_identity_size_bounds(self):
        identity = (self.root / "identity.json").read_bytes()
        (self.root / "identity.json").write_bytes(b" " * sr.MAX_RECORD_BYTES + identity)
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, self.base / "export")
        self.assertEqual(caught.exception.code, "SIZE_LIMIT")
        self.assertFalse((self.base / "export").exists())

    def test_export_does_not_overwrite_existing_output(self):
        exported = self.base / "export"
        exported.mkdir()
        with self.assertRaises(sr.ReceiptError) as caught:
            sr.export_review_safe(self.root, exported)
        self.assertEqual(caught.exception.code, "DOCUMENT_EXISTS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
