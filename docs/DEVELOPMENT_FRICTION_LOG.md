# Home Orchestrator v2 — Development Friction Log

**Project:** Home Orchestrator v2  
**Track:** Alexa+ — Simulated Experience  
**Repository:** https://github.com/jaythornfield/home-orchestrator-v2

This log documents challenges encountered while developing, testing, and packaging an offline synthetic household coordination prototype. It distinguishes confirmed behavior from unresolved observations.

## 1. Understanding the Available Alexa+ Development Path

**Severity:** Important

**Task attempted:** Identify an appropriate development and demonstration path for an Alexa+-style household coordination concept.

**Steps taken:** Reviewed the hackathon requirements and the publicly documented availability of Alexa+ development tools.

**Expected:** A straightforward development path with clear tooling availability and implementation examples.

**Actual:** Certain official Alexa+ developer tools are restricted to preview partners. The hackathon provides an alternative simulated-experience path, but developers must distinguish this carefully from live Alexa+ integration.

**Workaround:** Built an offline browser-based synthetic demonstration, explicitly identifying all simulated people, observations, permissions, and device effects.

**Actionable suggestion:** Provide a consolidated public quick-start guide comparing live integrations, self-hosted MCP or Agent Skill implementations, and simulated experiences, with examples and clear eligibility criteria.

## 2. Windows Console Input and Shutdown Ambiguity

**Severity:** High

**Task attempted:** Provide reliable, visible operator-controlled shutdown for the portable Windows demonstration.

**Steps taken:** Tested console command input, invalid commands, visible feedback, normal STOP behavior, and application shutdown.

**Expected:** Invalid input should display guidance without stopping the application. A valid STOP command should require only one Enter and should close the application normally.

**Actual:** An early version lacked visible command input feedback. In one manual attempt, the operator reported that submitting STOQ unexpectedly closed the terminal. The application recorded OPERATOR_STOP, but the available diagnostic records did not capture the input decision, so the original cause remains unresolved.

**Workaround:** Implemented bounded visible console input, Backspace and Esc handling, and explicit STOP feedback. A subsequent default-entry test confirmed that STOQ was rejected and STOP was accepted, followed by normal worker shutdown.

**Actionable suggestion:** Provide privacy-preserving diagnostic events for command classification, acceptance counts, and output errors without recording raw input text or credentials.

## 3. Testing Safety When Both Audit Outputs Fail

**Severity:** Critical — potential safety impact if mishandled

**Task attempted:** Verify that autonomous actions stop when both primary and emergency audit-output destinations become unavailable.

**Steps taken:** Created isolated, reversible file-output failure conditions and independently observed the product's behavior before Start and after its first confirmed action.

**Expected:** The system should enter a safe failure state, reject further autonomous activity, preserve already-confirmed effects, and remain capable of normal shutdown.

**Actual:** The ordinary audit outputs could not independently prove correct behavior when both destinations were deliberately made unavailable. Separate observations were necessary.

**Workaround:** Used bounded fault injection and an independent observer. The targeted tests returned FAILED_HOLD, showed no additional effects after the detected failure, and confirmed normal launcher and worker exit.

**Actionable suggestion:** Provide a standard failure-testing pattern with independent observation channels, bounded cleanup, and clearly specified fail-safe invariants.

## 4. Keeping Portable Packages and Documentation Consistent

**Severity:** Important

**Task attempted:** Prepare a reproducible, understandable Windows demonstration and a browsable source repository.

**Steps taken:** Compared package contents, runtime dependencies, third-party notices, build instructions, and supported entry points.

**Expected:** The documentation should accurately distinguish a ready-to-run Windows package from source-code distributions and developer build requirements.

**Actual:** Earlier documentation did not fully distinguish bundled runtime dependencies from externally required build materials. Two legacy import targets were also absent from the supplied source, although the tested demonstration entry point did not depend on those interfaces.

**Workaround:** Corrected the documentation without changing executable code, explicitly documented unsupported legacy interfaces, and validated the final release packages against file manifests and checksums.

**Actionable suggestion:** Add automated release checks that compare documentation claims, included dependency files, licenses, supported entry points, and package manifests before publication.

---

## Scope and Limitations

These entries describe development and testing friction in the local prototype and hackathon workflow. They do not claim failures in AWS services or live Alexa+ APIs.

Home Orchestrator v2 does not currently connect to real Alexa devices, physical household devices, or AWS runtime services. Historical unresolved observations remain identified as such rather than being represented as fully diagnosed defects.