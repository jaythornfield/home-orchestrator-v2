"""Closed-schema review receipts. No product, socket, process or network imports.

Producers must select safe scalars at the point of the event. This module never
accepts a request, snapshot, exception message, stdout, or private identity map.
It does not claim to recognize secrets embedded in deliberately mislabelled data.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

SCHEMA_VERSION = "HO2_REVIEW_SAFE_V1"
MAX_RECORD_BYTES = 4096
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 10000
MAX_EXPORT_BYTES = 5 * 1024 * 1024
EXPORT_NAMES = frozenset({"events.jsonl", "identity.json", "lifecycle.json", "emergency.json"})
ORIGINS = frozenset({"SERVER", "CLIENT", "OS_HANDLE", "APPLICATION", "DERIVED", "MANUAL"})
PROFILES = frozenset({"NONE", "UNSUPPORTED", "AUTONOMY", "OBJECTION", "GUEST", "CHILD",
    "REVOKE", "UNKNOWN", "FENCED_RECOVERY", "REVOKE_UNKNOWN", "S5_THIRD_A_REVOKE",
    "S5_THIRD_A", "S5_EXACT_OBJECTION_LIFT", "S5_EXACT_CHILD_LIFT", "S5_THIRD_A_UNKNOWN",
    "S5_EXACT_OBJECTION", "S5_EXACT_GUEST", "S5_EXACT_GUEST_LIFT", "S5_EXACT_CHILD"})
PHASES = frozenset({"NOT_STARTED", "READY", "RUNNING", "HOLD", "FINISHED", "STOPPED",
    "FAILED_HOLD", "STARTING", "CLOSING", "CLOSED", "PREPARING"})
STOP_REASONS = frozenset({"NONE", "OPERATOR_STOP", "PAGE_STOP", "NATURAL_DEADLINE",
    "PREPARATION_CANCELLED", "PARENT_INPUT_EOF", "AUDIT_FAILURE", "SAFETY_CONTROL_FAILURE",
    "STARTUP_FAILURE", "WORKER_FAILURE", "INTERNAL_ERROR", "UNKNOWN"})
ERROR_CODES = frozenset({"UNKNOWN_EVENT", "UNKNOWN_FIELDS", "MISSING_FIELDS", "INVALID_TYPE",
    "INVALID_VALUE", "INVALID_ALIAS", "INVALID_ORIGIN", "INVALID_JSON", "DUPLICATE_FIELD",
    "INVALID_SEQUENCE", "RECORD_LIMIT", "SIZE_LIMIT", "WRITE_FAILED", "WRITER_FAILED",
    "INVALID_PATH", "REPARSE_PATH", "UNAPPROVED_ARTIFACT", "READ_FAILED", "DOCUMENT_EXISTS", "CONTEXT_MISMATCH"})


class ReceiptError(ValueError):
    """Only a fixed code is exposed, never the invalid field/value or OSError."""
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "INVALID_VALUE"
        super().__init__(self.code)


@dataclass(frozen=True)
class Alias:
    kind: str
    value: str


def issue_alias(kind):
    if type(kind) is not str or kind not in {"run", "scene", "request", "root", "episode", "process", "artifact"}:
        raise ReceiptError("INVALID_ALIAS")
    return Alias(kind, kind + "_" + uuid.uuid4().hex)


def _alias(value, kind, serialized=False):
    if serialized:
        text = value
    else:
        if type(value) is not Alias or value.kind != kind:
            raise ReceiptError("INVALID_ALIAS")
        text = value.value
    if type(text) is not str or re.fullmatch(kind + r"_[0-9a-f]{32}", text) is None:
        raise ReceiptError("INVALID_ALIAS")
    return text


def _enum(*values):
    return ("enum", frozenset(values))


def _int(low=0, high=1000000000):
    return ("int", low, high)


BOOL = ("bool",)
ALIAS = lambda kind: ("alias", kind)
REASON = ("enum", STOP_REASONS)
PROFILE = ("enum", PROFILES)
PHASE = ("enum", PHASES)
REQUEST_RESULTS = _enum("RECEIVED", "ACCEPTED", "AUTH_REJECTED", "INVALID_SCHEMA", "RUNTIME_MISMATCH",
    "SESSION_EXPIRED", "PROFILE_REJECTED", "ALREADY_STARTED_OTHER_PROFILE", "CREATED",
    "IDEMPOTENT_EXISTING", "AUDIT_UNAVAILABLE", "AUXILIARY_FAILED", "NETWORK_UNAVAILABLE",
    "TIMEOUT", "STOPPING", "ORIGIN_REJECTED", "REJECTED", "NOT_REACHED", "UNKNOWN")
SCHEMAS = {
    "SOURCE": ({"tick": _int(0, 1000000), "profile": PROFILE, "admitted": BOOL}, {}),
    "BASELINE": ({"default_deadline_seconds": _int(1, 86400),
        "deadline_clock": _enum("LAUNCHER_START", "WORKER_READY", "TEST_START"),
        "accelerated_test": BOOL, "phase": PHASE}, {}),
    "REQUEST": ({"route": _enum("SESSION", "START", "RESET", "STATUS", "STOP_DEMO", "STOP_APP", "CLIENT_EVENT", "HEALTH", "OTHER"),
        "result": REQUEST_RESULTS, "http_status": _int(0, 599), "entered_session": BOOL,
        "entered_start": BOOL, "scene_match": BOOL}, {"request_alias": ALIAS("request")}),
    "CLIENT": ({"gesture": _enum("UNLOCK", "START", "RESET", "STOP_APP", "REFRESH", "SELECT_PROFILE"),
        "result": REQUEST_RESULTS}, {"request_alias": ALIAS("request")}),
    "BUSINESS": ({"profile": PROFILE, "phase": PHASE, "effects": _int(), "roots": _int(),
        "approvals": _int(), "version": _int()}, {"episodes": _int(), "sources": _int(),
        "root_alias": ALIAS("root"), "episode_alias": ALIAS("episode"),
        "action": _enum("NONE", "A", "B", "QUERY", "FENCE", "RECOVER", "REVOKE"),
        "status": _enum("NONE", "APPLIED", "NOT_APPLIED", "UNKNOWN", "PENDING", "HOLD", "REJECTED"),
        "decision": _enum("NONE", "ALLOW", "AUTHORIZATION_HOLD", "AFFECTED_HOLD", "OBJECTION_HOLD",
            "UNKNOWN_HOLD", "FENCE_REQUIRED", "REVOKED", "PRIVACY_HOLD", "SAFETY_HOLD", "OTHER_HOLD"),
        "queries": _int(), "submissions": _int(), "confirmed_version": _int(), "setting": _enum("NONE", "A", "B")}),
    "SCENE": ({"transition": _enum("RESET_BEGIN", "RESET_COMMITTED", "RESET_REJECTED", "SESSION_CLEARED", "IDENTITY_REFRESHED"),
        "profile": PROFILE, "sessions_invalidated": BOOL}, {"previous_scene_alias": ALIAS("scene")}),
    "STOP": ({"requested_by": _enum("NONE", "USER_CONSOLE", "PAGE", "PARENT", "DEADLINE", "AUDIT", "SAFETY", "TEST"),
        "initiating_reason": REASON, "forwarded_reason": REASON, "observed_reason": REASON,
        "acknowledged": BOOL, "stage": _enum("REQUESTED", "CONSUMED", "CLOSING", "CLEANED", "EVIDENCE_MISSING", "FAILED_HOLD"),
        "cleanup_complete": BOOL}, {"default_deadline_seconds": _int(1, 86400),
        "remaining_seconds": _int(0, 86400), "socket_closed": BOOL, "operator_stop_applicable": BOOL,
        "worker_created": BOOL, "prepared_files_checked": _int(), "failure_stage": _enum("CONSTRUCT","VALIDATE","WRITE","READ","EXPORT"),
        "failure_code": ("enum",ERROR_CODES), "artifact_alias": ALIAS("artifact")}),
    "NATIVE": ({"process_alias": ALIAS("process"),
        "observation_method": _enum("CREATOR_PROCESS_HANDLE", "APPLICATION_SELF", "NOT_OBSERVED"),
        "native_exit_observed": BOOL}, {"native_exit_code": _int(-2147483648, 4294967295), "socket_closed": BOOL}),
    "PORT": ({"observation_method": _enum("LOOPBACK_CONNECT", "NOT_TESTED"),
        "classification": _enum("CONNECTED", "CONNECTION_REFUSED", "TIMEOUT", "ACCESS_DENIED", "OTHER_ERROR", "NOT_TESTED"),
        "duration_ms": _int(0, 30000)}, {"actual_errno": _int(0, 2147483647), "socket_closed": BOOL}),
    "QUALITY": ({"artifact_alias": ALIAS("artifact"),
        "stage": _enum("CONSTRUCT", "VALIDATE", "WRITE", "READ", "EXPORT"),
        "issue": _enum("NONE", "UNKNOWN_FIELDS", "MISSING_FIELDS", "INVALID_TYPE", "INVALID_VALUE", "INVALID_JSON",
            "DUPLICATE_FIELD", "INVALID_SEQUENCE", "WRITE_FAILED", "READ_FAILED", "SIZE_LIMIT", "RECORD_LIMIT", "TRUNCATED"),
        "dropped_records": _int(0, MAX_RECORDS), "truncated": BOOL}, {}),
}
BASE_FIELDS = frozenset({"schema_version", "build_id", "source_sha256", "package_sha256", "case_id",
    "run_alias", "scene_alias", "generation", "record_origin", "clock_origin", "relative_ms", "seq", "event", "fields"})


def _value(value, spec, serialized=False):
    tag = spec[0]
    if tag == "alias":
        return _alias(value, spec[1], serialized)
    if tag == "bool":
        if type(value) is not bool:
            raise ReceiptError("INVALID_TYPE")
    elif tag == "int":
        if type(value) is not int:
            raise ReceiptError("INVALID_TYPE")
        if not spec[1] <= value <= spec[2]:
            raise ReceiptError("INVALID_VALUE")
    elif tag == "enum":
        if type(value) is not str:
            raise ReceiptError("INVALID_TYPE")
        if value not in spec[1]:
            raise ReceiptError("INVALID_VALUE")
    return value


def validate_fields(event, origin, fields, *, serialized=False):
    if type(event) is not str or event not in SCHEMAS:
        raise ReceiptError("UNKNOWN_EVENT")
    if type(origin) is not str or origin not in ORIGINS:
        raise ReceiptError("INVALID_ORIGIN")
    if type(fields) is not dict:
        raise ReceiptError("INVALID_TYPE")
    required, optional = SCHEMAS[event]
    if fields.keys() - (required.keys() | optional.keys()):
        raise ReceiptError("UNKNOWN_FIELDS")
    if required.keys() - fields.keys():
        raise ReceiptError("MISSING_FIELDS")
    result = {key: _value(value, (required | optional)[key], serialized) for key, value in fields.items()}
    if event == "CLIENT" and origin != "CLIENT":
        raise ReceiptError("INVALID_ORIGIN")
    if event == "NATIVE":
        observed = result["native_exit_observed"]
        if observed != ("native_exit_code" in result):
            raise ReceiptError("INVALID_VALUE")
        if observed and (origin != "OS_HANDLE" or result["observation_method"] != "CREATOR_PROCESS_HANDLE"):
            raise ReceiptError("INVALID_ORIGIN")
    if event == "BUSINESS" and result.get("status")=="APPLIED" and "root_alias" in result and "confirmed_version" not in result:
        raise ReceiptError("MISSING_FIELDS")
    if event == "PORT":
        classification = result["classification"]
        errno = result.get("actual_errno")
        if classification == "CONNECTION_REFUSED" and errno not in {61, 111, 10061}:
            raise ReceiptError("INVALID_VALUE")
        if classification == "ACCESS_DENIED" and errno not in {13, 10013}:
            raise ReceiptError("INVALID_VALUE")
        if classification == "CONNECTED" and errno != 0:
            raise ReceiptError("INVALID_VALUE")
        if classification == "NOT_TESTED" and (result["observation_method"] != "NOT_TESTED" or errno is not None):
            raise ReceiptError("INVALID_VALUE")
    return result


def _context(build_id, source_sha256, package_sha256, case_id, run_alias, *, serialized=False):
    if type(build_id) is not str or re.fullmatch(r"HO2-[A-Z0-9][A-Z0-9_-]{0,62}", build_id) is None:
        raise ReceiptError("INVALID_VALUE")
    for digest in (source_sha256, package_sha256):
        if type(digest) is not str or re.fullmatch(r"[0-9A-Fa-f]{64}", digest) is None:
            raise ReceiptError("INVALID_VALUE")
    if type(case_id) is not str or re.fullmatch(r"(?:T(?:0[1-9]|1[0-9]|20)|SAFE|LIFECYCLE|MANUAL|SCENE|CORE|PROBE)(?:_[A-Z0-9]{1,24})?", case_id) is None:
        raise ReceiptError("INVALID_VALUE")
    return dict(schema_version=SCHEMA_VERSION, build_id=build_id, source_sha256=source_sha256,
        package_sha256=package_sha256, case_id=case_id, run_alias=_alias(run_alias, "run", serialized))


def validate_record(record):
    if type(record) is not dict:
        raise ReceiptError("INVALID_TYPE")
    if record.keys() - BASE_FIELDS:
        raise ReceiptError("UNKNOWN_FIELDS")
    if BASE_FIELDS - record.keys():
        raise ReceiptError("MISSING_FIELDS")
    if record["schema_version"] != SCHEMA_VERSION or record["clock_origin"] != "LOCAL_WRITER_MONOTONIC":
        raise ReceiptError("INVALID_VALUE")
    _context(record["build_id"], record["source_sha256"], record["package_sha256"], record["case_id"], record["run_alias"], serialized=True)
    if record["scene_alias"] is not None:
        _alias(record["scene_alias"], "scene", serialized=True)
    _value(record["generation"], _int(0, 1000000))
    _value(record["relative_ms"], _int(0, 7 * 86400000))
    _value(record["seq"], _int(1, MAX_RECORDS))
    validate_fields(record["event"], record["record_origin"], record["fields"], serialized=True)
    return record


def _encode(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def _is_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_path(path, *, review=False):
    # Refuse network/device/ADS syntax before any existence, lstat or open operation.
    raw = os.fspath(path)
    if type(raw) is not str or raw.startswith(("\\\\", "//", "\\?\\", "\\.\\")):
        raise ReceiptError("INVALID_PATH")
    path = Path(raw)
    if ".." in path.parts or not path.is_absolute():
        raise ReceiptError("INVALID_PATH")
    if os.name == "nt" and re.fullmatch(r"[A-Za-z]:",path.drive) is None:
        raise ReceiptError("INVALID_PATH")
    if any(":" in part for part in path.parts[1:]):
        raise ReceiptError("INVALID_PATH")
    if review and ("review_safe" not in path.parts or "local_only" in path.parts):
        raise ReceiptError("INVALID_PATH")
    try:
        for parent in reversed((path,) + tuple(path.parents)):
            try:
                if _is_reparse(parent):
                    raise ReceiptError("REPARSE_PATH")
            except FileNotFoundError:
                continue
    except OSError:
        raise ReceiptError("READ_FAILED") from None
    return path


def _new_file(path, data):
    _safe_path(path)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise ReceiptError("DOCUMENT_EXISTS") from None
    except OSError:
        raise ReceiptError("WRITE_FAILED") from None


class SafeReceiptWriter:
    """One writer per process, one events file per writer; no shared appenders.

    emit returns False on a fixed-contract rejection; write failures poison this
    writer, so callers block new effects and invoke an independent stop path.
    """
    def __init__(self, review_root, *, build_id, source_sha256, package_sha256, case_id,
                 run_alias, clock=time.monotonic_ns):
        self.context = _context(build_id, source_sha256, package_sha256, case_id, run_alias)
        self.root = _safe_path(review_root, review=True)
        self.clock = clock
        self.started = clock()
        self.seq = 0
        self.last_ms = 0
        self.bytes_written = 0
        self.dropped_records = 0
        self.failed = False
        self.last_error = None
        self._lock = threading.Lock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            _safe_path(self.root, review=True)
            _new_file(self.root / "identity.json", _encode(self.context))
            _new_file(self.root / "events.jsonl", b"")
        except ReceiptError:
            raise
        except OSError:
            raise ReceiptError("WRITE_FAILED") from None

    def emit(self, event, origin, *, scene_alias=None, generation=0, **fields):
        with self._lock:
            try:
                if self.failed:
                    raise ReceiptError("WRITER_FAILED")
                safe_fields = validate_fields(event, origin, fields)
                scene = None if scene_alias is None else _alias(scene_alias, "scene")
                _value(generation, _int(0, 1000000))
                relative_ms = max(0, (self.clock() - self.started) // 1000000)
                if relative_ms < self.last_ms:
                    raise ReceiptError("INVALID_SEQUENCE")
                if self.seq >= MAX_RECORDS:
                    raise ReceiptError("RECORD_LIMIT")
                record = dict(self.context, scene_alias=scene, generation=generation,
                    record_origin=origin, clock_origin="LOCAL_WRITER_MONOTONIC",
                    relative_ms=relative_ms, seq=self.seq + 1, event=event, fields=safe_fields)
                validate_record(record)
                data = _encode(record)
                if len(data) > MAX_RECORD_BYTES or self.bytes_written + len(data) > MAX_FILE_BYTES:
                    raise ReceiptError("SIZE_LIMIT")
                _safe_path(self.root / "events.jsonl", review=True)
                try:
                    with (self.root / "events.jsonl").open("ab") as stream:
                        count = stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if count != len(data):
                        raise ReceiptError("WRITE_FAILED")
                except OSError:
                    raise ReceiptError("WRITE_FAILED") from None
                self.seq += 1
                self.last_ms = relative_ms
                self.bytes_written += len(data)
                self.last_error = None
                return True
            except ReceiptError as error:
                self.last_error = error.code
                self.dropped_records = min(MAX_RECORDS, self.dropped_records + 1)
                if error.code in {"WRITE_FAILED", "REPARSE_PATH", "SIZE_LIMIT", "RECORD_LIMIT", "INVALID_SEQUENCE", "READ_FAILED"}:
                    self.failed = True
                return False

    def write_lifecycle(self, records):
        """Only already-safe STOP/NATIVE/PORT records, not a private receipt dict."""
        if type(records) is not list or len(records) > 64:
            raise ReceiptError("SIZE_LIMIT")
        for record in records:
            validate_record(record)
            if record["event"] not in {"STOP", "NATIVE", "PORT"} or any(record[k] != v for k, v in self.context.items()):
                raise ReceiptError("INVALID_VALUE")
        data = _encode({"schema_version": SCHEMA_VERSION, "records": records})
        if len(data) > MAX_FILE_BYTES:
            raise ReceiptError("SIZE_LIMIT")
        _new_file(self.root / "lifecycle.json", data)


class EmergencyReceiptWriter:
    """Independent one-shot writer and in-memory state; never retries failed IO.

    The stop coordinator must execute regardless of this method's return value.
    Instantiate early, separately from the main writer; hold its context in memory.
    """
    def __init__(self, review_root, *, build_id, source_sha256, package_sha256, case_id, run_alias):
        self.root = _safe_path(review_root, review=True)
        self.context = _context(build_id, source_sha256, package_sha256, case_id, run_alias)
        self.attempted = False
        self.last_error = None
        self.memory_record = None

    def record(self, *, requested_by, initiating_reason, forwarded_reason, observed_reason,
               acknowledged, stage, cleanup_complete, failure_stage=None, failure_code=None, artifact_alias=None):
        if self.attempted:
            return False
        self.attempted = True
        try:
            extras={k:v for k,v in {"failure_stage":failure_stage,"failure_code":failure_code,"artifact_alias":artifact_alias}.items() if v is not None}
            fields = validate_fields("STOP", "APPLICATION", dict(requested_by=requested_by,
                initiating_reason=initiating_reason, forwarded_reason=forwarded_reason,
                observed_reason=observed_reason, acknowledged=acknowledged, stage=stage,
                cleanup_complete=cleanup_complete,**extras))
            record = dict(self.context, scene_alias=None, generation=0, record_origin="APPLICATION",
                clock_origin="LOCAL_WRITER_MONOTONIC", relative_ms=0, seq=1, event="STOP", fields=fields)
            validate_record(record)
            self.memory_record = record
            _new_file(self.root / "emergency.json", _encode(record))
            return True
        except ReceiptError as error:
            self.last_error = error.code
            return False


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("DUPLICATE_FIELD")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ReceiptError("INVALID_JSON")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ReceiptError("INVALID_JSON") from None


def _record_context(record):
    return {key: record[key] for key in ("schema_version", "build_id", "source_sha256",
        "package_sha256", "case_id", "run_alias")}


def _validate_document(name, data):
    contexts = []
    if name == "events.jsonl":
        if data and not data.endswith(b"\n"):
            raise ReceiptError("INVALID_JSON")
        lines = data.splitlines()
        if len(lines) > MAX_RECORDS:
            raise ReceiptError("RECORD_LIMIT")
        previous = None
        for index, line in enumerate(lines, 1):
            if not line or len(line) + 1 > MAX_RECORD_BYTES:
                raise ReceiptError("SIZE_LIMIT")
            row = validate_record(_json(line))
            if row["seq"] != index or (previous is not None and row["relative_ms"] < previous["relative_ms"]):
                raise ReceiptError("INVALID_SEQUENCE")
            context = _record_context(row)
            if contexts and context != contexts[0]:
                raise ReceiptError("CONTEXT_MISMATCH")
            if not contexts:
                contexts.append(context)
            previous = row
    elif name == "identity.json":
        if len(data) > MAX_RECORD_BYTES:
            raise ReceiptError("SIZE_LIMIT")
        value = _json(data)
        context_keys = {"schema_version", "build_id", "source_sha256", "package_sha256", "case_id", "run_alias"}
        if type(value) is not dict or value.keys() != context_keys or value.get("schema_version") != SCHEMA_VERSION:
            raise ReceiptError("INVALID_VALUE")
        _context(value["build_id"], value["source_sha256"], value["package_sha256"], value["case_id"], value["run_alias"], serialized=True)
        contexts.append(value)
    elif name == "emergency.json":
        if len(data) > MAX_RECORD_BYTES:
            raise ReceiptError("SIZE_LIMIT")
        row = validate_record(_json(data))
        if row["event"] != "STOP":
            raise ReceiptError("INVALID_VALUE")
        contexts.append(_record_context(row))
    else:
        value = _json(data)
        if type(value) is not dict or value.keys() != {"schema_version", "records"} or value["schema_version"] != SCHEMA_VERSION:
            raise ReceiptError("INVALID_VALUE")
        if type(value["records"]) is not list or len(value["records"]) > 64:
            raise ReceiptError("SIZE_LIMIT")
        for row in value["records"]:
            validate_record(row)
            if row["event"] not in {"STOP", "NATIVE", "PORT"}:
                raise ReceiptError("INVALID_VALUE")
            if len(_encode(row)) > MAX_RECORD_BYTES:
                raise ReceiptError("SIZE_LIMIT")
            contexts.append(_record_context(row))
    return contexts

def prepare_review_safe(review_root, names=("identity.json", "events.jsonl")):
    """Return only validated, closed-schema bytes and their common context."""
    root = _safe_path(review_root, review=True)
    if type(names) not in {tuple,list} or not 1 <= len(names) <= len(EXPORT_NAMES):
        raise ReceiptError("UNAPPROVED_ARTIFACT")
    if any(type(name) is not str or name not in EXPORT_NAMES for name in names) or len(set(names)) != len(names):
        raise ReceiptError("UNAPPROVED_ARTIFACT")
    if "identity.json" not in names:
        raise ReceiptError("MISSING_FIELDS")
    try:
        prepared=[]; contexts_by_name={}; total=0
        for name in names:
            source=_safe_path(root/name,review=True)
            if not source.is_file() or source.stat().st_size>MAX_FILE_BYTES:
                raise ReceiptError("SIZE_LIMIT")
            with source.open("rb") as stream:
                data=stream.read(MAX_FILE_BYTES+1)
            total+=len(data)
            if len(data)>MAX_FILE_BYTES or total>MAX_EXPORT_BYTES:
                raise ReceiptError("SIZE_LIMIT")
            contexts_by_name[name]=_validate_document(name,data)
            prepared.append((name,data))
        expected_context=contexts_by_name["identity.json"][0]
        for contexts in contexts_by_name.values():
            if any(context != expected_context for context in contexts):
                raise ReceiptError("CONTEXT_MISMATCH")
        return expected_context,prepared
    except ReceiptError:
        raise
    except OSError:
        raise ReceiptError("READ_FAILED") from None

def export_review_safe(review_root,destination,names=("identity.json","events.jsonl")):
    target=_safe_path(destination)
    if target.exists():
        raise ReceiptError("DOCUMENT_EXISTS")
    context,prepared=prepare_review_safe(review_root,names)
    try:
        target.mkdir(parents=False,exist_ok=False)
        _safe_path(target)
        receipt=[]
        for name,data in prepared:
            _new_file(target/name,data)
            receipt.append({"artifact":name,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest().upper()})
        return receipt
    except ReceiptError:
        raise
    except OSError:
        raise ReceiptError("WRITE_FAILED") from None
