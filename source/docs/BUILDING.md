# Building and the DOC1 documentation revision

The public supported product is the Windows 11 x64 portable synthetic demo. Source building is a separate developer activity. Runtime implementation, dependencies, configuration, tests and license texts remain R6D bytes; DOC1 revises four documentation files and generated manifests. The supplied DOC1 archives were made by **DOC_ONLY_REPACK** from the exact R6D archives, not by a new upstream acquisition/clean build.

## Build prerequisites and exact inputs

Use Windows x64, Windows PowerShell 5.1 or a compatible PowerShell, and the .NET ZIP APIs used by `tools/build_portable.ps1`. The portable end user's no-install instructions do not provision this developer build/test environment.

Acquire the official CPython 3.12.10 x64 embeddable ZIP and the thirteen runtime wheels named in `RUNTIME_SOURCE_LOCK.json`, using each recorded upstream URL. Place the runtime ZIP directly under a separate `artifacts/` directory and the wheels under `artifacts/wheels/`. The script requires the source tree, that runtime ZIP and every locked wheel; it validates their recorded filename, byte count and SHA-256 before extraction. It preserves upstream runtime/wheel license files and creates a package-local Python path and integrity manifests. It does not run pip during startup.

Independently validate provenance using the official CPython SPDX/release information and PyPI metadata recorded in the lock; a self-computed hash is not upstream authentication. `DOWNLOAD_VERIFICATION.json` is an external provenance-review record used by the original project workflow. It is not shipped or hash-locked here, and this build script neither consumes nor requires it. Do not invent such a record. The lock's saved Sigstore bundle status is not a claim of cryptographic verification.

## Minimal command

From the extracted Source root, with the locked files already acquired in sibling `artifacts/`, choose a new output directory and a new Build ID:

```powershell
& .\tools\build_portable.ps1 -SourceDirectory . -ArtifactDirectory ..\artifacts -OutputDirectory ..\out-new -BuildId MY_NEW_BUILD_ID
```

All four parameters are mandatory. `SourceDirectory` is this tree; `ArtifactDirectory` holds the exact original artifacts; `OutputDirectory` must not exist; `BuildId` must be new and match the script's allowed characters. This example is a future developer build, not the method used to create DOC1. Preserve failed evidence; never reuse an earlier build's results for changed bytes.

The builder uses ordinal relative-path inventory and `path TAB bytes TAB uppercase-SHA LF` aggregates. DOC1 used that same rule to regenerate `SOURCE_MANIFEST.json` and `PACKAGE_INTEGRITY.json` with Build ID `HO2-E2E-20261021-R6D-DOC1`; the app aggregate remains unchanged. No runtime code, locks, business JSON, safety schema or third-party license text was edited. Complete runtime acceptance is separately recorded, not asserted by successful packaging.

See [Testing](TESTING.md), [README](../README.md) and [licenses](../THIRD_PARTY_NOTICES.md). Source test tools are separate prerequisites; do not install them into the Windows user bundle.
