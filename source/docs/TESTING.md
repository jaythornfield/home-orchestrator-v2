# Testing the supported synthetic demo

The supported delivery is `START_DEMO.cmd` in the complete Windows package and its local synthetic webpage. The source tree is not a complete general-purpose Python SDK. Public use does not require access to the project's private T01–T20 matrix. That matrix records historical acceptance evidence and limitations; the commands below do not replace real desktop acceptance or certify a release.

## Developer prerequisites and bounded non-UI checks

Use a separate developer environment with Windows x64 CPython 3.12 (the locked runtime distribution is 3.12.10). Do not install test tools into the portable Windows bundle or a global environment. From the Source root, after activating that separate environment:

```powershell
python -m pip install --require-hashes -r requirements-runtime-with-hashes.txt -r requirements-test-only-with-hashes.txt
python -m unittest discover -s tests -p test_console_commands.py
python -m unittest discover -s tests -p test_backend.py
node tests/test_frontend.js
```

The runtime and test-only locks are separate; the latter supplies httpx/httpcore/certifi. The console-command tests use fake I/O rather than real terminal input. The backend suite uses the actual ASGI lifespan with bounded teardown and no listening socket; its 30-second idle observations take real time. The frontend test is a Node.js fixture for the frozen JavaScript, not a real-browser click test. Node.js is required separately and its version is not pinned in this archive. These checks were reviewed as bounded methods, but are not rerun merely for the documentation revision.

Other supplied native/package tests, including `tests/test_package.py`, `tests/test_new_package_faults.py`, `tests/control_probe.py` and `tools/native_supervisor.py`, are advanced, potentially process/listener-creating tools. They require a separate admitted fresh Windows extraction, correctly bound private control and native-handle evidence. Do not execute a blanket discovery run or guess their arguments from this page. Private identity/control data must stay local; existing internal records are not prerequisites for ordinary product use or claims of current test PASS.

## Known legacy API boundary

`app.__getattr__('HomeOrchestrator')` refers to missing `app.orchestrator`. `app.domain.comfort.goal_subjects` refers to missing `app.domain.plan.add_contract`. These legacy library APIs are not supported interfaces in this portable synthetic delivery; direct calls fail with a missing legacy module or its first missing parent; record the declared target separately from the observed exception.name. They are not removed or stubbed. Minimal isolated negative controls and the supported-entry run must be reported separately: a known-failing unsupported call is not a passing product feature. Static absence of a call is not proof that every possible runtime path is unaffected.

The synthetic contract JSON retains original paper/PENDING_USER and earlier gate metadata because code consumes its fixed digest. Those fields are historical input, not current user authorization, product acceptance or release status. No runtime configuration or contract digest changes in DOC1.

## Actual desktop acceptance

Complete acceptance requires the final DOC1 package: default launch, at least 30 seconds of zero effects after Unlock, explicit Start, three A/B/new-A effects, same-code scene change, a visible safety HOLD and supported recovery, page Stop app, native worker/socket closure and a fresh launch. Business safety HOLD is distinct from a launch window reporting an unconfirmed prior exit or failure to close. Do not bypass either safety boundary. A normal STOP check is distinct from natural 30-minute expiry. Worker exit does not by itself prove launcher/CMD exit; record missing evidence explicitly.

Runtime implementation and tests retain R6D bytes; only authorized documentation and generated manifests change for DOC1. Current results, inherited scope and untested items belong in the private release review packet, not an unqualified “all tests pass” claim. The original STOP editor special case and unrelated long suites need not be repeated for a documentation-only change. See [Building](BUILDING.md) and [README](../README.md).
