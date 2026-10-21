# Home Orchestrator v2

A local, deterministic synthetic prototype: no real Alexa or household devices, and no runtime LLM. The Windows launcher and synthetic webpage are the supported entrypoints. Instructions describe intended operation; they do not certify complete acceptance.

## Quick start

1. **Extract.** On Windows 11 x64, use an ordinary account. Obtain the complete `HO_V2_Windows_x64.zip` attachment when published; extract everything to a writable local folder outside network/cloud-synced storage. GitHub **Code → Download ZIP** provides repository source, not the self-contained Windows package.
2. **Open.** Double-click `START_DEMO.cmd`; keep its window open. No separate Python, PowerShell 7, pip or Codex installation is required for the portable package. If the browser does not open, use the local address shown in that window; the port may change.
3. **Unlock, then Start.** Enter this launch window's preview code and select **Unlock**; keep the code private. Unlocking alone starts nothing. Choose a scene, then explicitly click **Start synthetic preview**. The default scene is designed to apply A → B → a genuinely new A: three virtual effects without per-action approvals.
4. **Change scenes.** **Stop current demo** stops the event source and retains completed effects. After finishing/stopping, **Choose a new scene** creates a fresh synthetic world. Unlock with the same code for this app launch, then explicitly Start again. A webpage's business safety **HOLD** is a scene state, not a startup failure; do not bypass it.
5. **Close.** **Stop app** closes the whole service. Alternatively type `STOP` and press Enter once in the launch window: no blank Enter or second confirmation. Wait for **App closed normally**. The default deadline is 30 minutes from app launch; scene changes do not extend it. Natural expiry is reported separately.
6. **Restart.** After normal closure, double-click the entry for a fresh run and new code. Do not deliberately open extra copies while one is running.

Troubleshooting: browser missing—use the displayed address; damaged files—extract the complete original ZIP again; launch window reports an unconfirmed previous exit, or this app cannot close normally—preserve the copy/diagnostics and seek support. Do not force-close processes, delete locks or bypass protections.

[Building](docs/BUILDING.md) · [Testing](docs/TESTING.md)

Legacy `app.HomeOrchestrator` and `app.domain.comfort.goal_subjects` are unsupported here; direct calls fail because their old modules are absent. See Testing for boundaries. R6D-DOC1 changes documentation/manifests; runtime implementation remains R6D.

Lawfully licensable original contributions use [MIT](LICENSE), Copyright (c) 2026 AurenLoop. [Third-party terms](THIRD_PARTY_NOTICES.md) remain separate, including CPython's retained licenses.
