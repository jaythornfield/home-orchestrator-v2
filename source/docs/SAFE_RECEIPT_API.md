# R6 safe receipt candidate API

Drafting role: `/root/r6_safe_receipt_candidate`. Candidate only; not independently
reviewed, not integrated into W6, and not executed by this role. No old R5 runtime
or collection data was read. The only product reads were the new W6 static files.

## Integration contract

`app/safe_receipts.py` is stdlib only. Importing it performs no filesystem, product,
socket, process, browser or network action. Producers explicitly select safe data;
there is no arbitrary exception/stdout/snapshot/request cleaning entry point.

```python
from app.safe_receipts import SafeReceiptWriter, EmergencyReceiptWriter, issue_alias

context = dict(build_id='HO2-E2E-20261020-R6', source_sha256=source_sha,
               package_sha256=package_sha, case_id='T10_RESET',
               run_alias=issue_alias('run'))
writer = SafeReceiptWriter(exact_review_safe_run_directory, **context)
scene = issue_alias('scene')
ok = writer.emit('REQUEST', 'SERVER', scene_alias=scene, generation=1,
    route='SESSION', result='ACCEPTED', http_status=200,
    entered_session=True, entered_start=False, scene_match=True,
    request_alias=issue_alias('request'))
```

Do not pass arbitrary input values into `build_id`, digests or case identifiers.
Those are trusted build/test configuration; their shape validator does not prove
their provenance. Build identifier is `HO2-` plus at most 63 uppercase identifier
characters; digests are exact 64 hexadecimal digits; case identifiers are T01–T20
or SAFE/LIFECYCLE/MANUAL/SCENE/CORE/PROBE, optionally followed by an underscore and
at most 24 uppercase identifier characters. Package SHA must identify the actual
package under test. During build bootstrapping use a named source/build artifact
identity with an explicit later package binding, not a guessed final ZIP hash.

`issue_alias(kind)` accepts run/scene/request/root/episode/process/artifact only and
uses fresh random UUID bytes. Producers may retain a private in-memory map from
their real roots to generated Alias objects. This module neither accepts that map
nor derives aliases from secrets. Alias objects are required at writer APIs; safe
files contain the public alias string, never native/private IDs.

Each process has its own writer directory and single appender. Do not share an
events file between multiple processes. Correlate their safe run/request/process
aliases explicitly; relative time is `LOCAL_WRITER_MONOTONIC`, so ordering across
writers requires a separate calibration rather than comparing their milliseconds.

## Closed events

The authoritative complete field and enum table is `SCHEMAS` in the module.
Every event carries schema/build/source/package/case/run identity, optional scene
alias, generation, record_origin, local clock category, relative_ms and seq.

| Event | Required fields | Optional fields |
|---|---|---|
| BASELINE | default_deadline_seconds, deadline_clock, accelerated_test, phase | none |
| REQUEST | route, result, http_status, entered_session, entered_start, scene_match | request_alias |
| CLIENT | gesture, result | request_alias |
| BUSINESS | profile, phase, effects, roots, approvals, version | episodes, sources, root_alias, episode_alias, action, status, decision, queries, submissions, setting |
| SCENE | transition, profile, sessions_invalidated | previous_scene_alias |
| STOP | requested_by, initiating_reason, forwarded_reason, observed_reason, acknowledged, stage, cleanup_complete | default_deadline_seconds, remaining_seconds, socket_closed, operator_stop_applicable |
| NATIVE | process_alias, observation_method, native_exit_observed | native_exit_code, socket_closed |
| PORT | observation_method, classification, duration_ms | actual_errno, socket_closed |
| QUALITY | artifact_alias, stage, issue, dropped_records, truncated | none |

`record_origin`: SERVER, CLIENT, OS_HANDLE, APPLICATION, DERIVED, MANUAL.
CLIENT gesture never counts as a server authentication/Start fact. NATIVE with a
code requires OS_HANDLE origin and CREATOR_PROCESS_HANDLE method plus observed
true; missing code is required when observed is false. Self-report remains a
different level. Origin labels rely on the producer providing truthful evidence;
the schema is not an OS observation API.

STOP reasons preserve NONE, OPERATOR_STOP, PAGE_STOP, NATURAL_DEADLINE,
PREPARATION_CANCELLED, PARENT_INPUT_EOF, AUDIT_FAILURE, SAFETY_CONTROL_FAILURE,
STARTUP_FAILURE, WORKER_FAILURE, INTERNAL_ERROR, UNKNOWN. Forwarding must preserve
the cause, not substitute OPERATOR_STOP. All three reason fields are mandatory.

PORT distinguishes CONNECTED, CONNECTION_REFUSED, TIMEOUT, ACCESS_DENIED,
OTHER_ERROR, NOT_TESTED. Refused requires a platform refused errno (Windows 10061;
61/111 are supported for isolated non-Windows schema tests); denied requires
13/10013. Connected requires actual_errno=0. The caller must select the matching
OS result; no port is probed by this module. `socket_closed` describes the owned
socket, independently from a later loopback observation.

Unknown fields, nested objects, free text and implicit scalar conversion are
rejected. Bool is not accepted for an integer. Known enums are exact and ASCII;
no arbitrary profile/error/reason string is serialized. All denial exceptions
contain only a fixed ReceiptError.code. No invalid field name/value is echoed.

## Bounds, failure and stopping

Record: 4096 encoded bytes; events file: 4 MiB; writer: 10000 records; export:
5 MiB total, at most four files; lifecycle: at most 64 validated records. Counts
max 1e9; generation max 1e6; relative milliseconds max seven days. Deadlines range
1–86400 seconds; this does not change the product default of 1800 seconds.

`emit(...) -> bool` returns false with fixed `last_error`; dropped_records tracks
rejections. IO, reparse, capacity, record-count or monotonic-order failure poison
the writer (`failed=True`); no repeated failing write loop occurs. The producer
must block new Start/effects after mandatory audit rejection. A later successful
event can follow an invalid input/schema rejection, which preserves recoverability
and does not turn a rejected fake sensitive control into a whole-task block.

`EmergencyReceiptWriter` is independent, has no reference to main writer, and
attempts `emergency.json` once. It keeps `memory_record` when disk writing fails.
Its record API accepts only the seven mandatory STOP fields. The stop coordinator
must proceed regardless of persistence success and report evidence missing; do
not make shutdown conditional on writing either normal or emergency receipts.

`write_lifecycle(records)` permits only already validated STOP/NATIVE/PORT records
matching the writer context. It does not accept the private worker/launcher raw
receipt. Immutable output files use CreateNew semantics; events append only to the
new writer's file. A partial IO failure poisons the writer, and exporter rejects
truncated JSON, invalid records or event ordering.

## Export

`export_review_safe(root, destination, names=('identity.json','events.jsonl'))`
reads only named members and returns fixed artifact name, byte count and SHA256.
Names are restricted to identity.json/events.jsonl/lifecycle.json/emergency.json;
no arbitrary relative or absolute member, `..`, stdout or local_only is accepted.
Root must be an exact directory under a `review_safe` ancestor, with no local_only
ancestor. Every existing path ancestor is checked via lstat for symlink or Windows
reparse flag, including source member and destination. All selected file bytes
are bounded and schema-validated in memory before an output directory is created.
Output must be new. There is no recursive enumeration or source deletion.

These path checks protect against accidental reparse/alias escape within the
task's trusted directory ownership boundary; they do not assert resistance to a
same-user adversary racing filesystem replacement between checks and file open.
No ACL is changed by this module. The exporter hashes only approved safe files.

## Tests and state

From an isolated candidate root that places this candidate app module on Python's
import path, use standard unittest discovery for `tests/test_safe_receipts.py`.
Main integrator may copy it to W6 and run `python -m unittest discover -s tests
-p test_safe_receipts.py -v` with the approved bundled interpreter.

The tests use only temporary synthetic files, no real credentials, private IDs,
network, processes, product imports or old R5 data. Negative controls cover nested
credentials, unknown field names, exception/fake stdout, oversized/non-ASCII enums,
missing/invalid types, aliases, native self-report, port misclassification,
truncated/duplicate JSON, write failure, independent emergency persistence,
limits, monotonic order, export path/name policy and simulated reparse ancestors.
Assertions use fixed messages and do not print fictional sentinels. Synthetic
reparse injection is explicitly a method test, not a real Windows Junction test.

**EXECUTION_STATUS=NOT_RUN_BY_DRAFTING_ROLE**. Parent integration and non-author
review remain required; this document does not claim product or schema PASS.

## R2 context-binding delta

This new candidate preserves R1. Export always requires identity.json as its safe
identity anchor. Before creating a destination, every selected identity/event/
lifecycle/emergency record must agree on schema_version, build_id, source_sha256,
package_sha256, case_id and run_alias. A mismatch raises fixed CONTEXT_MISMATCH
without revealing any value and without creating output. Selection order does
not affect validation. Empty events/lifecycle documents retain the identity anchor.
Identity and emergency files are limited to MAX_RECORD_BYTES; every lifecycle
record has the same encoded record bound. Events still have per-line, file,
sequence/count and nonnegative integer checks. Six additional synthetic tests
cover identity presence, each identity key mismatch, lifecycle/emergency mismatch,
valid four-file export with identity last, and record-size bounds.

R2 tests are authored only, not executed by this drafting role. Main role must run
under a process-local TEMP/TMP pointing to approved B6 test storage (do not modify
system environment variables), then obtain non-author review.

## Integrated R6 refinements

The packaged `docs/SAFE_RECEIPT_SCHEMA.json` is the full actual field/enum contract.
`package_sha256` is the SHA256 of PACKAGE_INTEGRITY.json bytes, not a self-referential ZIP digest.
The external controller binds the final ZIP digest separately. BUSINESS action rows retain each
root's independent `confirmed_version`; resource `version` is not substituted for it. Unchanged
action states are not re-appended for every status poll. Validation failures never assert handler
entry. STOP carries actual forwarding and cleanup facts, optional worker_created and fixed
failure stage/code/artifact metadata. Emergency audit failures are recorded at failure time,
independent of the normal writer; a failed emergency write stays evidence-missing.

`prepare_review_safe` returns only already-validated closed-schema bytes and their context.
The package exporter validates directory aliases, current build/source/manifest identity and
cross-role context before creating any export output; it does not read local_only. UNC/device/
ADS syntax is refused before filesystem lookup. Reparse components, including dangling links,
are refused. OS/native evidence always comes from a retained creation handle; application
self-report remains a different origin. Only actual Windows errno 10061 proves refused port
connection in this Windows-only package. Test acceleration never proves a real 30-minute wait.
