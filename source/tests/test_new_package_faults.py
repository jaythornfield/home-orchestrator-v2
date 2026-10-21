"""Draft: new-package faults, bounded retained handles, source-safe evidence only.

Not executed by its author. Intended for tests/ in the reviewed R6 source tree.
Every product child is isolated, browser=False, with a <=60s test deadline.
No kill, process discovery, private file read, stdout export or arbitrary cleanup.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
import zipfile


ASSERTIONS = frozenset({
    "INVALID_ARGUMENT_ROOT", "NEW_DIRECTORY_REQUIRED", "REPARSE_REJECTED", "ZIP_MEMBER_REJECTED",
    "ZIP_LAYOUT_REJECTED", "PAYLOAD_TARGET_REJECTED", "PACKAGE_IDENTITY_CHANGED", "NATIVE_IDENTITY_REQUIRED",
    "LOCK_ACQUISITION_FAILED", "HTTP_503_REQUIRED", "NO_NEW_EFFECTS_REQUIRED", "EMERGENCY_REQUIRED",
    "EMERGENCY_FAILURE_CAUSE", "NATIVE_EXIT_CODE", "NATIVE_DRAIN_NOT_JOINED", "WORKER_NATIVE_EXIT_CODE",
    "PORT_NOT_REFUSED_10061", "CLEANUP_NOT_CONFIRMED", "STOP_CAUSE_MISMATCH", "SAFE_RUN_NOT_UNIQUE",
    "PREPARATION_CANCEL_CODE", "PREPARATION_CANCEL_REASON", "WORKER_CREATED_UNEXPECTEDLY",
    "CORRUPTION_NOT_APPLIED", "CORRUPTION_NOT_REJECTED", "NEW_OWNED_PROCESS_HOLD", "UNEXPECTED_FAILURE",
    "PREPARATION_PROGRESS_REQUIRED", "FAULT_COUNTS_NOT_OBSERVED",
})


class CheckFailure(Exception):
    def __init__(self, code):
        self.code = code if code in ASSERTIONS else "UNEXPECTED_FAILURE"
        super().__init__(self.code)


def require(value, code):
    if not value:
        raise CheckFailure(code)


def guarded(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "INVALID_ARGUMENT_ROOT")
    for ancestor in reversed((path,) + tuple(path.parents)):
        if ancestor.exists():
            info = ancestor.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400), "REPARSE_REJECTED")
    return path


class ShareReadOnlyHandle:
    """Hold one exact newly created safe event file open without FILE_SHARE_WRITE.

    This is a transient owned-file share lock, not an ACL mutation. No bytes are
    read through the handle. It must be released before normal cooperative STOP.
    """
    def __init__(self, path):
        self.path = guarded(path)
        require(self.path.name == "events.jsonl" and "review_safe" in self.path.parts
                and "local_only" not in self.path.parts, "LOCK_ACQUISITION_FAILED")
        self.handle = None

    def __enter__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL.
        handle = kernel.CreateFileW(str(self.path), 0x80000000, 0x1, None, 3, 0x80, None)
        require(handle not in (None, ctypes.c_void_p(-1).value), "LOCK_ACQUISITION_FAILED")
        self.handle = handle
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            ok = self.kernel.CloseHandle(self.handle)
            self.handle = None
            require(bool(ok), "LOCK_ACQUISITION_FAILED")


class FaultSuite:
    def __init__(self, windows_zip, case_root, evidence_root, harness_root):
        require(os.name == "nt", "INVALID_ARGUMENT_ROOT")
        self.windows_zip = guarded(windows_zip)
        self.case_root = guarded(case_root)
        self.output = guarded(evidence_root)
        require(self.windows_zip.is_file() and not self.case_root.exists() and not self.output.exists(), "NEW_DIRECTORY_REQUIRED")
        self.case_root.mkdir(parents=False, exist_ok=False)
        self.output.mkdir(parents=False, exist_ok=False)
        self.package_sha = hashlib.sha256(self.windows_zip.read_bytes()).hexdigest().upper()
        self.harness_root = guarded(harness_root)
        sys.path.insert(0, str(self.harness_root))
        from app.safe_receipts import export_review_safe, SafeReceiptWriter, Alias, issue_alias
        from tools.native_supervisor import OwnedProcess
        spec = importlib.util.spec_from_file_location("r6_reviewed_package_harness", self.harness_root / "tests/test_package.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.PackageRun = module.PackageRun
        self.OwnedProcess = OwnedProcess
        self.exporter = export_review_safe
        self.SafeWriter = SafeReceiptWriter
        self.Alias = Alias
        self.issue_alias = issue_alias
        self.owned = None
        self.active_run = None
        self.summary = {"kind": "R6_SCOPED_NEW_PACKAGE_FAULTS", "scoped_checks_pass": False,
            "complete_T14_T15": False, "desktop_evidence": False, "accelerated_test": True,
            "windows_zip_sha256": self.package_sha, "runs": [], "coverage_notes": [
                "PREPARATION_VERIFY_CANCEL_IS_CONTROLLED_HOOK_INJECTION_NOT_STDIN_OR_CONSOLE",
                "T14_T15_HAVE_OTHER_UNCOVERED_VARIANTS_OUTSIDE_THIS_SCOPED_SUITE"]}

    def extract(self, case):
        require(case in {"audit_write_failure", "prepare_immediate_stop", "prepare_controlled_hook", "corrupt_payload", "fresh_recovery", "stdin_eof"}, "INVALID_ARGUMENT_ROOT")
        extraction = guarded(self.case_root / case)
        require(not extraction.exists(), "NEW_DIRECTORY_REQUIRED")
        with zipfile.ZipFile(self.windows_zip) as archive:
            members = archive.infolist()
            require(len(members) <= 2000 and sum(m.file_size for m in members) <= 128 * 1024 * 1024, "ZIP_LAYOUT_REJECTED")
            seen = set()
            manifests = []
            for member in members:
                name = member.filename
                parts = PurePosixPath(name).parts
                require(not name.startswith(("/", "\\")) and "\\" not in name and ":" not in name
                    and all(p not in {"..", ".local", "local_only", "preview-control.key"} for p in parts)
                    and not stat.S_ISLNK(member.external_attr >> 16), "ZIP_MEMBER_REJECTED")
                normalized = name.rstrip("/").casefold()
                require(normalized not in seen, "ZIP_MEMBER_REJECTED")
                seen.add(normalized)
                if parts and parts[-1] == "PACKAGE_INTEGRITY.json":
                    manifests.append(parts)
            require(len(manifests) == 1 and len(manifests[0]) in {1, 2}, "ZIP_LAYOUT_REJECTED")
            extraction.mkdir(exist_ok=False)
            archive.extractall(extraction)
        package = extraction.joinpath(*manifests[0][:-1])
        require((package / "runtime/python.exe").is_file(), "ZIP_LAYOUT_REJECTED")
        return guarded(package)

    def command(self, package, deadline, *, preparation_hook=False):
        require(type(deadline) is int and 1 <= deadline <= 60, "INVALID_ARGUMENT_ROOT")
        hook = ",preparation_hook=lambda index,control:control.request(\'OPERATOR_STOP\',\'TEST\') if index==2 else None" if preparation_hook else ""
        source = "import sys;from tools.portable_launcher import main;sys.exit(main(deadline_seconds=" + str(deadline) + ",browser=False,interval=.1,control_client=True" + hook + "))"
        return [str(package / "runtime/python.exe"), "-I", "-X", "utf8", "-c", source]

    def direct(self, package, *, preparation_hook=False):
        require(self.owned is None, "NEW_OWNED_PROCESS_HOLD")
        self.owned = self.OwnedProcess(self.command(package, 30, preparation_hook=preparation_hook), str(package))
        require(self.owned.identity_error is None, "NATIVE_IDENTITY_REQUIRED")
        return self.owned

    def ready(self, package, case):
        require(self.owned is None, "NEW_OWNED_PROCESS_HOLD")
        self.active_run = self.PackageRun(package, self.output / case, "T14" if case == "audit_write_failure" else "T15", deadline=60)
        self.owned = self.active_run.owned
        require(self.owned.identity_error is None, "NATIVE_IDENTITY_REQUIRED")
        return self.active_run

    def observe(self, expected_code, *, timeout=25):
        native = self.owned.wait(timeout)
        # Never return/export the raw result dict: it contains private stdout.
        require(native["native_exit_code"] == expected_code, "NATIVE_EXIT_CODE")
        require(native["output_drain_joined"] and native["output_error"] is None, "NATIVE_DRAIN_NOT_JOINED")
        return {"native_exit_observed": True, "native_exit_code": native["native_exit_code"],
            "observation_method": "CREATION_RETAINED_PROCESS_HANDLE", "output_drain_joined": True}

    def export_role(self, package, run_alias, role, case, *, emergency=False):
        require(re.fullmatch(r"run_[0-9a-f]{32}", run_alias) is not None and role in {"launcher", "worker"}, "SAFE_RUN_NOT_UNIQUE")
        source = package / ".local/review_safe" / run_alias / role
        destination = self.output / case / "exported" / run_alias / role
        destination.parent.mkdir(parents=True, exist_ok=True)
        names = ["identity.json", "events.jsonl"] + (["emergency.json"] if emergency else [])
        artifacts = self.exporter(source, destination, names)
        rows = [json.loads(line) for line in (destination / "events.jsonl").read_bytes().splitlines()]
        emergency_row = json.loads((destination / "emergency.json").read_bytes()) if emergency else None
        return rows, emergency_row, artifacts

    def sole_safe_alias(self, package):
        root = guarded(package / ".local/review_safe")
        aliases = [p.name for p in root.iterdir() if p.is_dir() and re.fullmatch(r"run_[0-9a-f]{32}", p.name)]
        require(len(aliases) == 1, "SAFE_RUN_NOT_UNIQUE")
        return aliases[0]

    def outer_safe_observation(self, package, run_alias, case, native):
        manifest_bytes = (package / "PACKAGE_INTEGRITY.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        writer = self.SafeWriter(self.output / case / "review_safe" / run_alias / "outer",
            build_id=manifest["build_id"], source_sha256=manifest["source_aggregate_sha256"],
            package_sha256=hashlib.sha256(manifest_bytes).hexdigest().upper(),
            case_id="T14" if case == "audit_write_failure" else "T15", run_alias=self.Alias("run", run_alias))
        require(writer.emit("NATIVE", "OS_HANDLE", process_alias=self.issue_alias("process"),
            observation_method="CREATOR_PROCESS_HANDLE", native_exit_observed=True,
            native_exit_code=native["native_exit_code"]), "UNEXPECTED_FAILURE")

    def cleared(self):
        self.owned = None
        self.active_run = None
        self.PackageRun.pending = None

    def completed_running(self, run, case, *, reason, expected_code, audit_missing=False):
        native = self.observe(expected_code)
        launcher, _, launcher_artifacts = self.export_role(run.package, run.run_alias, "launcher", case)
        worker, emergency, worker_artifacts = self.export_role(run.package, run.run_alias, "worker", case, emergency=audit_missing)
        worker_codes = [r["fields"]["native_exit_code"] for r in launcher if r["event"] == "NATIVE"
            and r["record_origin"] == "OS_HANDLE" and r["fields"]["native_exit_observed"]]
        require(worker_codes == [expected_code], "WORKER_NATIVE_EXIT_CODE")
        require(any(r["event"] == "PORT" and r["fields"]["classification"] == "CONNECTION_REFUSED"
            and r["fields"].get("actual_errno") == 10061 for r in launcher), "PORT_NOT_REFUSED_10061")
        final_launcher = [r["fields"] for r in launcher if r["event"] == "STOP" and r["fields"]["stage"] == "CLEANED"]
        require(final_launcher and final_launcher[-1]["cleanup_complete"], "CLEANUP_NOT_CONFIRMED")
        require(final_launcher[-1]["initiating_reason"] == reason and final_launcher[-1]["observed_reason"] == reason, "STOP_CAUSE_MISMATCH")
        if audit_missing:
            require(emergency is not None, "EMERGENCY_REQUIRED")
            facts = emergency["fields"]
            require(facts["initiating_reason"] == "AUDIT_FAILURE" and facts["failure_stage"] == "WRITE"
                and facts["failure_code"] == "WRITE_FAILED", "EMERGENCY_FAILURE_CAUSE")
            require(not any(r["event"] == "BUSINESS" and r["fields"]["effects"] != 0 for r in worker), "NO_NEW_EFFECTS_REQUIRED")
        else:
            final_worker = [r["fields"] for r in worker if r["event"] == "STOP" and r["fields"]["stage"] == "CLEANED"]
            require(final_worker and final_worker[-1]["cleanup_complete"], "CLEANUP_NOT_CONFIRMED")
            require(final_worker[-1]["initiating_reason"] == reason and final_worker[-1]["observed_reason"] == reason, "STOP_CAUSE_MISMATCH")
        self.outer_safe_observation(run.package, run.run_alias, case, native)
        self.summary["runs"].append({"case": case, "run_alias": run.run_alias, "pass": True,
            "accelerated_test": True, "deadline_seconds": 60, "desktop_evidence": False,
            "native_launcher": native, "native_worker_exit_code": worker_codes[0], "port_10061": True,
            "stop_reason": reason, "normal_stop_receipts_complete": not audit_missing,
            "worker_audit_evidence_missing_as_expected": audit_missing,
            "artifacts": launcher_artifacts + worker_artifacts})
        self.cleared()

    def audit_write_failure(self):
        package = self.extract("audit_write_failure")
        run = self.ready(package, "audit_write_failure")
        run.unlock()
        initial = run.status()
        require(initial["autonomous_effects"] == 0 and initial["distinct_command_roots"] == 0, "NO_NEW_EFFECTS_REQUIRED")
        events = package / ".local/review_safe" / run.run_alias / "worker/events.jsonl"
        with ShareReadOnlyHandle(events):
            def project(body, point):
                require(type(body) is dict and set(body) == {"error", "phase", "autonomous_effects",
                    "distinct_command_roots", "per_action_approvals"}, "FAULT_COUNTS_NOT_OBSERVED")
                require(body["error"] == "local_audit_unavailable" and body["phase"] == "FAILED_HOLD", "FAULT_COUNTS_NOT_OBSERVED")
                require(all(type(body[key]) is int for key in ("autonomous_effects", "distinct_command_roots", "per_action_approvals")), "FAULT_COUNTS_NOT_OBSERVED")
                require(body["autonomous_effects"] == body["distinct_command_roots"] == body["per_action_approvals"] == 0, "NO_NEW_EFFECTS_REQUIRED")
                return {"point": point, "evidence_source": "SERVER_HTTP_SAFE_PROJECTION", "http_status": 503,
                    "phase": "FAILED_HOLD", "effects": body["autonomous_effects"], "roots": body["distinct_command_roots"],
                    "approvals": body["per_action_approvals"]}
            started = time.monotonic()
            first = project(run.request("/api/demo/start", {"runtime_id": run.runtime, "profile": "S5_THIRD_A"}, expected=503), "FAULT_START_RESPONSE")
            time.sleep(.2)
            second = project(run.request("/api/demo/status", expected=503), "AFTER_SHORT_INTERVAL")
            observed_interval = round(time.monotonic() - started, 4)
        # Unlock persistence has recovered at the filesystem, but the mandatory
        # writer remains poisoned. Normal stopping must not depend on it.
        run.owned.send_stop()
        self.completed_running(run, "audit_write_failure", reason="OPERATOR_STOP", expected_code=0, audit_missing=True)
        self.summary["runs"][-1]["http_safe_projections"] = [
            {"point":"BEFORE_FAULT","evidence_source":"SERVER_HTTP_SAFE_PROJECTION","http_status":200,
             "phase":initial["phase"],"effects":initial["autonomous_effects"],"roots":initial["distinct_command_roots"],
             "approvals":initial["per_action_approvals"]}, first, second]
        self.summary["runs"][-1]["fault_observation_interval_seconds"] = observed_interval

    def immediate_prepare_stop(self):
        package = self.extract("prepare_immediate_stop")
        owned = self.direct(package)
        owned.send_stop()
        native = self.observe(3)
        alias = self.sole_safe_alias(package)
        launcher, _, artifacts = self.export_role(package, alias, "launcher", "prepare_immediate_stop")
        final = [r["fields"] for r in launcher if r["event"] == "STOP"]
        require(final and final[-1]["observed_reason"] == "PREPARATION_CANCELLED", "PREPARATION_CANCEL_REASON")
        require(final[-1].get("worker_created") is False
            and not any(r["event"] == "NATIVE" for r in launcher), "WORKER_CREATED_UNEXPECTEDLY")
        require(final[-1]["cleanup_complete"], "CLEANUP_NOT_CONFIRMED")
        self.outer_safe_observation(package, alias, "prepare_immediate_stop", native)
        self.summary["runs"].append({"case": "prepare_immediate_stop", "run_alias": alias, "pass": True,
            "native_launcher": native, "worker_created": False, "initiating_reason": final[-1]["initiating_reason"],
            "observed_reason": "PREPARATION_CANCELLED", "verification_in_progress_proven": False,
            "normal_operator_stop_applicable": False, "accelerated_test": True, "deadline_seconds": 30, "artifacts": artifacts})
        self.cleared()

    def controlled_verify_cancel(self):
        package = self.extract("prepare_controlled_hook")
        self.direct(package, preparation_hook=True)
        native = self.observe(3)
        alias = self.sole_safe_alias(package)
        launcher, _, artifacts = self.export_role(package, alias, "launcher", "prepare_controlled_hook")
        final = [r["fields"] for r in launcher if r["event"] == "STOP"]
        require(final and final[-1]["observed_reason"] == "PREPARATION_CANCELLED", "PREPARATION_CANCEL_REASON")
        require(final[-1].get("worker_created") is False
            and not any(r["event"] == "NATIVE" for r in launcher), "WORKER_CREATED_UNEXPECTEDLY")
        require(final[-1].get("prepared_files_checked") == 2, "PREPARATION_PROGRESS_REQUIRED")
        require(final[-1]["cleanup_complete"], "CLEANUP_NOT_CONFIRMED")
        self.outer_safe_observation(package, alias, "prepare_controlled_hook", native)
        self.summary["runs"].append({"case": "prepare_controlled_hook", "run_alias": alias, "pass": True,
            "native_launcher": native, "worker_created": False, "prepared_files_checked": 2,
            "observed_reason": "PREPARATION_CANCELLED", "method": "CONTROLLED_HOOK_INJECTION",
            "stdin_or_console_evidence": False, "normal_operator_stop_applicable": False,
            "accelerated_test": True, "deadline_seconds": 30, "artifacts": artifacts})
        self.cleared()

    def corrupt_then_fresh_recovery(self):
        package = self.extract("corrupt_payload")
        target = guarded(package / "app/static/synthetic_demo.css")
        manifest = json.loads((package / "PACKAGE_INTEGRITY.json").read_bytes())
        matching = [r for r in manifest["files"] if r["path"] == "app/static/synthetic_demo.css"]
        require(len(matching) == 1 and target.is_file(), "PAYLOAD_TARGET_REJECTED")
        before = target.read_bytes()
        require(len(before) == matching[0]["bytes"] and hashlib.sha256(before).hexdigest().upper() == matching[0]["sha256"], "PACKAGE_IDENTITY_CHANGED")
        # Mutation is isolated to this new discardable test extraction. Neither
        # final ZIP nor the current final package nor the manifest is modified.
        target.write_bytes(before + b"\n/* R6 synthetic corruption control */\n")
        require(hashlib.sha256(target.read_bytes()).hexdigest().upper() != matching[0]["sha256"], "CORRUPTION_NOT_APPLIED")
        self.direct(package)
        native = self.observe(1)
        alias = self.sole_safe_alias(package)
        launcher, _, artifacts = self.export_role(package, alias, "launcher", "corrupt_payload")
        final = [r["fields"] for r in launcher if r["event"] == "STOP"]
        require(final and final[-1].get("worker_created") is False
            and not any(r["event"] == "NATIVE" for r in launcher), "CORRUPTION_NOT_REJECTED")
        self.outer_safe_observation(package, alias, "corrupt_payload", native)
        self.summary["runs"].append({"case": "corrupt_payload", "run_alias": alias, "pass": True,
            "native_launcher": native, "worker_created": False, "normal_stop_applicable": False,
            "payload_alias": "DEMO_CSS", "original_sha256": matching[0]["sha256"], "artifacts": artifacts})
        self.cleared()
        recovery = self.extract("fresh_recovery")
        run = self.ready(recovery, "fresh_recovery")
        run.unlock()
        run.start()
        run.finished("S5_THIRD_A")
        run.owned.send_stop()
        self.completed_running(run, "fresh_recovery", reason="OPERATOR_STOP", expected_code=0)

    def eof(self):
        package = self.extract("stdin_eof")
        run = self.ready(package, "stdin_eof")
        run.unlock()
        state = run.status()
        require(state["autonomous_effects"] == 0, "NO_NEW_EFFECTS_REQUIRED")
        run.owned.process.stdin.close()
        self.completed_running(run, "stdin_eof", reason="PARENT_INPUT_EOF", expected_code=1)

    def failure_cleanup(self):
        # If PackageRun.__init__ failed, its early pending registration still
        # retains the only created handle. Never discover a replacement by PID.
        if self.owned is None and self.PackageRun.pending is not None:
            self.owned = self.PackageRun.pending.owned
        if self.owned is None:
            return {"created_object": False, "new_spawn_permitted": False}
        try:
            if self.owned.process.poll() is None and self.owned.identity_error is None:
                self.owned.send_stop()
            native = self.owned.wait(25)
            if native["output_drain_joined"]:
                return {"native_exit_observed": True, "native_exit_code": native["native_exit_code"],
                    "forced_cleanup": False, "new_spawn_permitted": False}
        except BaseException:
            pass
        return {"status": "NEW_OWNED_PROCESS_HOLD", "native_exit_observed": False,
            "forced_cleanup": False, "new_spawn_permitted": False}

    def run(self):
        try:
            self.audit_write_failure()
            self.immediate_prepare_stop()
            self.controlled_verify_cancel()
            self.corrupt_then_fresh_recovery()
            self.eof()
            self.summary["scoped_checks_pass"] = True
        except BaseException as error:
            self.summary["failure_code"] = error.code if type(error) is CheckFailure else "UNEXPECTED_FAILURE"
            self.summary["failure_cleanup"] = self.failure_cleanup()
            # No further cases are started after *any* failure, including a
            # cleanly observed failure; this is one bounded fault-suite pass.
        with (self.output / "SCOPED_FAULT_SUMMARY.json").open("x", encoding="ascii") as stream:
            json.dump(self.summary, stream, ensure_ascii=True, indent=2)
        print(json.dumps({"scoped_checks_pass": self.summary["scoped_checks_pass"],
            "complete_T14_T15": False, "completed_runs": len(self.summary["runs"]),
            "failure_code": self.summary.get("failure_code"),
            "hold": self.summary.get("failure_cleanup", {}).get("status"), "desktop_evidence": False}))
        return 0 if self.summary["scoped_checks_pass"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("windows_zip", type=Path)
    parser.add_argument("new_case_root", type=Path)
    parser.add_argument("new_evidence_root", type=Path)
    parser.add_argument("--harness-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        suite = FaultSuite(args.windows_zip, args.new_case_root, args.new_evidence_root, args.harness_root)
        return suite.run()
    except BaseException:
        # Startup failures happen before any product spawn. No arbitrary error
        # object, traceback, caller path, process identity or stdout is exposed.
        print(json.dumps({"scoped_checks_pass": False, "failure_code": "UNEXPECTED_FAILURE", "desktop_evidence": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
