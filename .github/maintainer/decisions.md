# Maintainer Decisions

## 2026-08-30 - Improve staged release verification errors

Decision:
- Chain and append the underlying error message when staged release verification fails to expose why the verification failed (resolves masking in Issue #169).

Rationale:
- **Error Masking (Issue #169):** When staged verification failed inside `mark_staged_verified`, any `ValueError` or `OSError` was caught and replaced with a generic `"Staged release does not match the pinned target."`. This masked important root causes (such as integrity checks, layout layout errors, or path validation errors). Chaining the original error string makes the client's logs highly diagnostic without changing schemas.

Implementation notes:
- Replaced `except (OSError, ValueError):` with `except (OSError, ValueError) as error:` in `plugin_core.py` (and regenerated `plugin.py`) to chain and append `str(error)` to `"Staged release does not match the pinned target."`.

Verification:
- Ran full test suite (1569 passed).
- Related Issue: #169

## 2026-08-26 - Resolve build branch adoption at the theme registry database layer

Decision: Keep the core runtime `plugin_core.py` lean by rejecting automated CSS parsing, unflattened `@import` scanning, or bare-root clone checks. Fully resolve the build branch request at the registry and documentation layers.

Rationale:
- `.git` folders are already safely omitted during theme mirroring via `shutil.copytree` in `plugin_core.py`. Other developer-only configurations (like `package.json`) present no server execution or data leak risks for Domoticz static styles serving.
- Premature automated validations introduce severe technical debt, potential ReDoS/path traversal vulnerabilities, and bloat the 22k-line runtime core.
- The `themes.json` schema already fully supports custom checkout branches (`branch`) and subdirectory compilation targets (`source_path`).
- Theme maintainers can adopt build branches seamlessly at the database layer without core code changes.

Implementation notes:
- Documented three primary supported developer workflows (Minimal Hobbyist, Subfolder Compiler, and Clean Release Branch) in `CONTRIBUTING.md`.
- Expanded `.github/scripts/validate_plugins.py` to cover strict `themes.json` schema schema validation, safe target directory checks, and git remote branch existence checks as part of the automated CI pull request checks.

Verification:
- Added `themes.json` automated validation test scenarios to verify schemas.
- Ran entire test suite successfully (all 1,563 tests passed).
- Related Issue: #164

## 2026-08-18 - Allow omitted source_revision for forge-based providers to prevent false release_mutation blocks

Decision: Normalize empty or omitted `source_revision` to the `commit` SHA inside `decide()` when evaluating the immutable fields comparison.

Rationale:
- When a user installs a plugin directly from GitHub/GitLab (in `provider_live` mode), the metadata is created with an explicit `source_revision` equal to the commit SHA.
- However, when the weekly update scan indexes that same release, it omits the `source_revision` in `release_index.json` to keep the file size minimal, since for forge-based providers `source_revision` is always identical to `commit`.
- When the client's manager compared the local install metadata (having the explicit SHA) with the indexed descriptor (having an empty `source_revision` default), it triggered a false `release_mutation` block, showing "Verification failed: release_mutation".
- Resolving the blank/explicit mismatch by treating empty forge `source_revision` as equal to its `commit` during verification keeps client installations verified and functional.

Implementation notes:
- Updated the comparison logic in `plugin_core.py` (specifically `decide()` in `ReleaseInstallUpdateStrategy`) to normalize empty/omitted `source_revision` to `commit` for comparison purposes.
- Regenerated `plugin.py` from `plugin_core.py`.
- Added unit test coverage to `tests/test_release_management.py`.

Verification:
- Local test suite passed fully (1558 passed).
- Related Issue: #150

## 2026-08-17 - Prevent premature release index expiration with 16-day validity

Decision: Set `DEFAULT_VALIDITY_SECONDS` to 16 days (instead of 7 days) to provide a comfortable grace period for weekly automated registry updates.

Rationale:
- The previous 7-day expiration left virtually zero margin for human delay in reviewing and merging the automated weekly registry update pull requests.
- Since the weekly cron runs Sunday mornings and the previous index expires Sunday mornings (exactly 7 days after generation), any merge delay past a 35-minute window immediately triggered expiration errors ("Release metadata is expired; release changes are paused") across all client installations using Release-managed plugins.
- Increasing the index validity to 16 days ensures that the index remains functional for over two weeks, preventing client disruptions even if a weekly update PR is delayed or merged late.

Implementation notes:
- Changed `DEFAULT_VALIDITY_SECONDS` to `16 * 24 * 60 * 60` in `.github/scripts/generate_release_index.py`.

Verification:
- Local test suite passed (1557 passed).
- Pushed commit `9ff35e5` with `fixes #150` to `master` branch.
- Commented on issue #150.

## 2026-07-28 - Keep restore button copy compact

Decision: use short, single-line restore labels while keeping the confirmation
dialog explicit about the verified target.

Button mapping:
- Git backup: `Restore Git`.
- Release backup with a known version: `Restore vX`.
- Release backup without a known version: `Rollback`.
- Compatibility fallback without target metadata: `Rollback`.

Rationale:
- Target-specific labels must still fit alongside the other card actions.
- `Rollback` is acceptable as a compact fallback when the exact target cannot
  be named on the button.
- The confirmation dialog remains the safety boundary and continues to say
  `previous Git version`, `previous Release version`, or `previous version`.

Verification:
- Focused lifecycle, management, and UI suite: 102 tests passed.
- Full sanitized suite: 1,526 tests passed.
- Generated-runtime parity, Python compilation, and `git diff --check` passed.

Public action:
- Pushed approved commit `d3529c2` to `master` with `Refs #122`.
- Release Please opened `PR:143` for v2.24.4; it remains unmerged.

## 2026-07-27 - Name verified restore targets in the UI

Decision: keep the internal `rollback` action ID stable, but have the backend
name the verified restore target and have the page render that action
descriptor instead of hardcoding a generic label.

Rationale:
- A retained Git checkout and a previous Release are materially different
  restore targets.
- `Rollback` does not tell a normal user which one will be restored.
- The backend already verifies the retained backup and is the authoritative
  place to describe it.

Implementation notes:
- Migration backups use `Return to previous Git version`.
- Release backups use `Restore vX`, with a version-independent fallback only
  when retained Release metadata cannot name the version.
- Confirmation text names the same target and states that a Domoticz restart
  will be required.
- The UI renders backend action labels through `textContent` and retains a safe
  compatibility fallback for mismatched generations.
- Generic rollback availability text was removed from the status summary.

Verification:
- Focused lifecycle, management, and UI suite: 101 tests passed.
- Full sanitized suite: 1,525 tests passed.
- Generated-runtime parity, Python compilation, and `git diff --check` passed.
- Ubuntu, Windows, generated-runtime, Release Please, and CodeQL workflows
  passed on `cbffb6a`.

Public action:
- Pushed approved commit `cbffb6a` with `Refs #122`.
- Posted the approved explanation to `Eddie-BS`:
  `https://github.com/adrighem/PyPluginStore/issues/122#issuecomment-5094975084`.
- Merged approved release `PR:142` and published v2.24.3:
  `https://github.com/adrighem/PyPluginStore/releases/tag/v2.24.3`.

## 2026-07-27 - Retain dependencies for exact channel conversions

Decision: do not rebuild or rename the global shared dependency generation
when a clean Git checkout switches to a certified Release at the exact same
commit and its requirements file is byte-identical.

Rationale:
- Rebuilding all installed requirements can be blocked by an unrelated plugin
  even though the requested channel conversion changes no executable inputs.
- Omitting an incompatible plugin from a real rebuild would be unsafe because
  it could remove that plugin's working dependencies after restart.
- Explicit `retain_live` state keeps these cases distinct and lets activation,
  recovery, and rollback revalidate the dependency tree without pretending an
  installer produced it.

Implementation notes:
- Retention requires equal Git and Release commits, an `equal` preflight
  relationship, no tracked or untracked changes, and byte-identical target
  requirements.
- Activation rechecks the captured live dependency tree and never creates a
  staged or backup dependency directory in this mode.
- Any failed precondition falls back to the normal complete rebuild.
- Installer failures expose only allowlisted package IDs, sanitized requirement
  owners, and the Python major/minor version.

Public action:
- None.

## 2026-07-26 - Exclude repository templates from the plugin registry

Decision: blocklist `galadril/domoticz-python-plugin-template` by exact,
case-insensitive owner/repository identity and remove it from every active
registry artifact.

Rationale:
- The repository is developer scaffolding, not an installable end-user plugin.
- Its `Your-Plugin-Key` value is a placeholder, not a stable Domoticz package
  identity.
- Keeping it Git-only would still expose an unusable install action.
- Exact owner/repository matching prevents package-ID aliases from bypassing the
  exclusion without blocking legitimate forks under other owners.

Implementation notes:
- The weekly scanner removes an existing blocked entry and its update/platform
  sidecars before network discovery, then prevents rediscovery.
- Registry, update-time, platform, migration, and active release-index records
  were removed.
- No tombstone was added because the template release was never published in an
  accepted index sequence.

Verification:
- Full sanitized suite: 1,497 tests passed.
- All 256 registry repositories and the sequence 7 to 8 transition validated.
- Pull-request and post-merge GitHub workflows passed.

Public action:
- Updated and merged `PR:139` with explicit user approval.

## 2026-07-23 - Verify manager runtime generations before mutations

Decision: identify PyPluginStore with both its semantic version and a
deterministic runtime build ID, compare the browser, deployed custom page,
loaded backend, and installed files, and make mismatched clients read-only.

Rationale:
- A Git update can change executable files without changing the release version.
- Files on disk can advance while Domoticz still runs the previous Python code,
  and `www/templates` or an open browser tab can remain on another generation.
- Git `HEAD` is useful provenance but cannot be the equality key for release
  archives, documentation-only commits, or local builds.
- A stale page must retain enough access to explain and recover through status
  and restart, but it must not issue plugin or Local registry mutations.

Implementation notes:
- The build ID length-frames and hashes `plugin.py`, `package_registry.py`,
  `package_identity.py`, and `pypluginstore.html` in fixed order.
- Runtime source fingerprints are frozen before self-update; cached support
  modules whose source differs from disk make the identity unverifiable.
- Startup renders the frozen identity into the custom page and replaces the
  deployed template atomically by exact content rather than mtime.
- Every frontend command sends the embedded page identity. Backend responses
  return the current verdict and reject all non-recovery actions unless the
  generations are coherent.
- Version, build, Git provenance, self-update state, and recovery guidance are
  composed only through `#pypluginstore-status`; the manager-card
  `self-update-detail` renderer and styles were removed.
- Same-build Git-only updates can be confirmed without restart. A changed bundle
  requires Domoticz restart, followed by a browser hard refresh if needed.
- The build ID detects local generation mismatch; it is not an authenticity
  signature.

Verification:
- Full sanitized suite: 1,355 tests passed.
- Generated runtime parity and Python compilation passed.
- Live validation passed for all 257 registry entries.
- `git diff --check` passed.

Public action:
- None.

## 2026-07-22 - Use local overrides for intentional Git management

Decision: remove the public **Use Git** / `use_git` channel switch. Once a
certified Release is selected, ongoing branch-based Git management requires an
explicit local registry override.

Rationale:
- Certified Releases are the reviewed, pinned, and more stable delivery path for
  public packages.
- A local override makes the user's alternate repository/branch intent explicit
  instead of storing a hidden public-package channel choice.
- Removing only the user-selectable writer preserves upgrade compatibility and
  rollback safety without immediately remigrating a restored Git backup to the
  same Release.
- Applying a Local override does not transform an existing Release folder into
  a Git checkout, so updates must remain blocked until Rollback or reinstall
  provides one.

Implementation notes:
- Removed `use_git` from the UI, confirmation payloads, backend executor, and
  normal API dispatch. Stale clients receive local-override guidance.
- Existing `keep_git` values remain honored, and rollback to a Git backup still
  writes that internal safety hold. No new public action can create one.
- Documentation directs ongoing Git users to the Local registry dialog and
  explains that an existing Release folder needs verified Rollback or
  remove/reinstall before it becomes a Git checkout.
- Release-installed Local overrides now expose an actionable blocked status and
  reject direct update requests before the Git updater is called.
- Current Conductor product guidance reflects the Local-override policy; older
  completed specs are annotated as historical where they describe **Use Git**.

Verification:
- Full sanitized suite: 1,315 tests passed.
- Focused Release policy, lifecycle, management, migration, and UI suite: 126
  tests passed.
- Generated runtime parity and `git diff --check` passed.

Public action:
- Pushed commits `0ad164b`, `691ea74`, and the maintainer record to
  `fix/issue-111-local-overrides` with user approval. Branch-only pushes do not
  match this repository's workflow triggers, so GitHub created no pipeline.
- Pushed review fix `185d62a` and opened `PR:118`; PR checks were started.

## 2026-07-22 - Keep local overrides outside Release management

Decision: treat a valid local registry entry as authoritative Git-only intent,
and fail closed for Release actions while an existing local registry cannot be
loaded.

Rationale:
- A saved public-package Release preference must not override the user's current
  local registry entry.
- Falling back to public Release metadata when `registry_local.json` exists but
  is malformed or unreadable can offer a destructive channel switch that ignores
  the intended local source.
- A missing local registry remains normal; only an existing file that cannot be
  loaded pauses Release management.

Implementation notes:
- Local entries no longer read persisted Release preferences or public Release
  targets.
- An invalid existing local registry makes Release metadata effectively
  unauthorized until a later successful/missing-file reload clears the error.
- Local cards cannot render a **Switch to Release** action, while ordinary Git
  updates and verified rollback recovery remain available.

Verification:
- The original `ISSUE:111` lifecycle fan-out was reproduced before `PR:116` and
  confirmed fixed on current `master`.
- Full sanitized suite: 1,314 tests passed; generated runtime, compilation,
  registry validation, and diff checks passed.

Public action:
- The validated fix was pushed to `fix/issue-111-local-overrides`; no issue
  comment or pull request was created.

## 2026-07-22 - Verify custom-page deployment before changing local-registry UX

Decision: treat `ISSUE:117` first as a stale deployed custom page and request a
Domoticz restart plus browser hard refresh before changing product code.

Rationale:
- The screenshot reports installed version `v2.21.0` but lacks the **Local
  registry** action present in the tagged `v2.21.0` page.
- Installed plugin files can advance before Domoticz restarts and copies
  `pypluginstore.html` into `www/templates`.
- The reported repository mismatch is already served by the global Local registry
  dialog, and focused tests confirm public-package overrides remain Git-managed.

Verification:
- Inspected the issue screenshot, tagged page, startup copy path, and current
  local-registry/release-action tests.
- Focused tests and the full 1,314-test suite passed.

Public action:
- Posted the approved diagnostic reply at
  `https://github.com/adrighem/PyPluginStore/issues/117#issuecomment-5042788930`.

Outcome:
- The reporter confirmed restart/refresh resolved the issue and closed it.

## 2026-07-18 - Minimize GitHub Actions authority

Decision: keep workflow defaults read-only, grant write permissions only to the
trusted jobs that publish releases or repository changes, and pin third-party
actions to reviewed full commit SHAs.

Rationale:
- Pull-request code must be able to verify generated output without receiving a
  repository write token or persisted checkout credentials.
- Per-job elevation makes each publishing path visible and keeps unrelated jobs
  within the repository's read-only token default.
- Immutable action references prevent a moved tag from changing trusted workflow
  code without review.

Implementation notes:
- Added a read-only pull-request freshness job for generated `plugin.py` and kept
  write access only in the trusted `master` push job.
- Scoped Release Please and weekly scanner permissions to their publishing jobs.
- Pinned checkout, setup-python, Release Please, and create-pull-request actions.
- Changed the repository's default workflow token to read-only and disabled
  workflow pull-request review approval.
- Documented the staged default-branch ruleset and delayed SHA enforcement until
  the pinned workflow definitions reach `master`.

Verification:
- `pytest -q -p no:cacheprovider`: 231 passed.
- `actionlint` 1.7.12: passed.

## 2026-07-18 - Use a provider-neutral release index

Decision: make validated, digest-pinned release ZIPs the preferred plugin delivery
channel per certified registry entry while retaining Git as a supported channel.

Rationale:
- Forge discovery in repository automation avoids runtime API limits and prevents
  GitHub-specific behavior from becoming part of the Domoticz plugin protocol.
- A normalized index gives GitHub, GitLab, Forgejo/Codeberg, Gitea, and generic
  HTTPS releases one runtime contract.
- Only 54 of 256 repositories currently report a latest stable release candidate,
  before archive validation, so rollout must be progressive.
- Existing Git checkouts need ancestry, local-data, staged replacement, and
  rollback checks before their `.git` directory can be removed.

Implementation notes:
- Added a Conductor specification and phased implementation plan for `ISSUE:64`.
- V1 prefers forge-generated source ZIPs, validates ZIPs before mutation, hashes
  the canonical tree, and uses durable same-filesystem transaction journals.
- Automatic migration blocks dirty or ambiguous trees and unknown local files;
  reviewed mutable paths can be preserved with separate audit hashes.
- Completed the release-aware runtime, rollback lifecycle, dependency snapshots,
  channel UI, and content-bound Git migration confirmation flow.
- Published an initial 47-entry index: 46 GitHub releases and one GitLab release.
  Both registered Codeberg/Forgejo repositories currently report no release;
  Gitea and generic remain covered by provider contracts without live pilot claims.
- The unsigned v1 index explicitly does not claim protection against compromise
  of the PyPluginStore distribution channel; signed TUF-style metadata is future
  hardening.

Verification:
- Research covered current registry release availability and official GitHub,
  GitLab, Forgejo, Gitea, Python archive, and TUF documentation.
- `pytest -q`: 1109 passed.
- `python .github/scripts/validate_plugins.py`: passed for 257 registry records.
- Generated runtime freshness, Python compilation, workflow YAML linting, and
  diff checks passed. Manual host verification was waived by the user.

## 2026-07-16 - Safe UI management for registry_local.json

Decision: manage local registry entries through backend-owned, revisioned CRUD actions and one accessible native dialog.

Rationale:
- Whole-document browser replacement would exceed the API bridge easily and could overwrite concurrent or malformed files.
- Exact-byte revisions make stale writes visible before mutation.
- Atomic replacement preserves the existing registry when serialization or filesystem writes fail.
- A native dialog provides focus containment and Escape behavior without adding a UI framework or custom modal infrastructure.

Implementation notes:
- `LocalRegistryService` owns structured reads, validation, canonicalization, revisions, and atomic create/update/delete operations.
- The selected public registry is cached so local mutations can immediately reapply overlays without another network request.
- The UI preserves form values on validation or revision conflicts and uses inline deletion confirmation.

Verification:
- `pytest -q`: 226 passed.
- Manual verification on pietje passed all documented steps.

## 2026-07-06 - Bound registry validation Git checks

Decision: registry validation must apply a timeout to each `git ls-remote` repository check.

Rationale:
- During maintenance, `python .github/scripts/validate_plugins.py` blocked in a `subprocess.run()` call while validating `evcc_domoticz`.
- The registry currently has 254 entries; one slow remote should fail that entry cleanly instead of blocking the entire release check.
- Root `plugin.py` HTTP validation already has a timeout, so the Git branch existence check should have the same bounded behavior.

Implementation notes:
- Added `GIT_REMOTE_TIMEOUT_SECONDS = 30`.
- `validate_repository()` now passes that timeout to `subprocess.run()` and returns `False` on `subprocess.TimeoutExpired`.
- Existing command-shape tests now assert the timeout, and a new regression test covers timeout handling.

Verification:
- `pytest tests/test_registry_scripts.py -q`: 67 passed.
- `pytest -q`: 178 passed.
- `python .github/scripts/validate_plugins.py`: passed for 254 plugins.
- `git diff --check`: passed.

## 2026-07-06 - Accept action-less API error responses by transaction ID

Decision: the custom UI bridge should accept backend error responses with a matching `tx_id` even when the response omits `action`.

Rationale:
- Some backend pre-flight and command failures return a valid error payload before an action-specific response can be built.
- The browser command bridge already uses a unique `tx_id`, so matching `status: "error"` plus the same transaction ID is specific enough to avoid consuming unrelated stale responses.
- Requiring `data.action === action` for every response caused valid backend errors to time out in the UI and broke the CI smoke test.

Implementation notes:
- `pollResponse()` now distinguishes normal action responses from same-transaction action-less error responses.
- Both accepted paths clear the API bridge payload after consuming the response.

Verification:
- `pytest -q`: 177 passed.
- `python .github/scripts/validate_plugins.py`: passed for 254 plugins.

## 2026-07-05 - Require root plugin.py for weekly discovery

Decision: weekly registry discovery must only add repositories that expose a non-empty root-level `plugin.py` on the registered branch.

Rationale:
- `PR:88` showed the GitHub discovery path adding repositories such as `domoticz-mcp`, `wiki`, and `ha-domoticz-sync` that mention Domoticz but are not installable Domoticz Python plugins.
- GitLab and Codeberg discovery already checked for a root `plugin.py`; GitHub discovery should use the same gate.
- The add path should defensively re-check candidates so future discovery sources cannot bypass the plugin-file requirement.

Implementation notes:
- Added a shared discovery helper that filters GitHub, GitLab, and Codeberg candidates through the root `plugin.py` check.
- Added a defensive root `plugin.py` gate before writing any newly discovered registry entry.
- Added timeouts to scanner JSON fetches so weekly runs do not hang indefinitely on a slow API response.
- Reran the weekly scan; it added only `Domoticz-Indevolt-plugin` and skipped the non-plugin repositories from `PR:88`.

Verification:
- `pytest -q`: 174 passed.
- `python .github/scripts/validate_plugins.py`: passed for 254 plugins.

Public action:
- Closed `PR:88` with a short explanation.

## 2026-07-01 - Non-Git UI Badges and Branch-Aware Pulls

Decision: Provide clear non-Git visual indicators in the custom web UI and implement branch-aware checkouts/pulls during updates.

Rationale:
- `ISSUE:74`: When a plugin was manually copied/extracted without a `.git` folder (unmanaged plugin), PyPluginStore detected it as installed but could not update it (returning an unknown update status). The web UI rendered it similarly to other up-to-date plugins, offering no clear indication that it was unmanaged. Adding an explicit `"is_git": false` metadata field from the backend and rendering a "Non-Git" badge with a disabled "Update" button clarifies this state.
- `ISSUE:73`: Automatic update checks or manual pulls can fail/fizzle or falsely report "already up-to-date" if a plugin's local branch has no tracked upstream set or is in a detached HEAD state. Transitioning from pull-based updates to a highly deterministic fetch-and-reset flow (`git fetch origin`, `git checkout -B <branch> origin/<branch>`, and `git reset --hard origin/<branch>`) ensures clean updates and absolute state parity with origin, completely bypassing any tracking mismatches or local divergence.

Implementation notes:
- Modified `getInstalledPlugins` in `plugin_core.py` to identify if each installed plugin folder contains a `.git` folder and return `"is_git"` within `"installed_match_details"`.
- Refactored `UpdatePythonPlugin` in `plugin_core.py` to implement a highly robust fetch-and-reset flow: first `git fetch origin`, then compare local `HEAD` and remote branch commit hashes via `git diff --quiet`, switch/create the target tracking branch with `git checkout -B <branch> origin/<branch>`, and hard-reset the workspace to match origin with `git reset --hard origin/<branch>`, ensuring clean updates even in detached HEAD or force-pushed developer states.
- Modified `pypluginstore.html` to define `installedMatchDetailsCache`, extract `isGit`, append a "Non-Git" badge when appropriate, and disable the "Update" button with an informative tooltip for non-Git installations.
- Updated `tests/test_plugin_registry.py` to verify that `is_git` is correctly computed and returned.
- Updated `tests/test_plugin_update_status.py` to assert on the robust fetch-and-reset git command sequence.
- Regenerated `plugin.py` from `plugin_core.py`.

Verification:
- `pytest`: 133 passed.
- `python3 .github/scripts/validate_plugins.py`: passed.

## 2026-07-01 - Docker Git ownership bypass and Luxtronik registry integration

Decision: Use `safe.directory` config overrides as the primary retry mechanism for Git dubious ownership errors, and integrate the Luxtronik plugin registry changes from `PR:71` (changing key to `luxtronikex` and branch to `dist`) while preserving Windows support.

Rationale:
- `ISSUE:70` / `ISSUE:69`: In Docker-based Domoticz installations, files are often mounted from the host and owned by the host user (UID 1000). On startup/restart, Git running inside the container (sometimes as root or a different UID) reports a "dubious ownership" error. PyPluginStore previously attempted to "repair" this by calling `chown` to the container user's UID on the entire plugin folder. This caused a cyclic file ownership fight on restart, reverting user host file permissions to root, causing permission denied errors on the host, and flooding Domoticz logs with Error messages on every single container restart.
- Using `-c safe.directory=<path>` on the first Git retry allows commands to execute perfectly inside the container without modifying any files or permissions on disk, leaving both Docker and host environments stable and eliminating log spam.
- `PR:71`: Switching the Luxtronik plugin to its lean `dist` branch keeps downloads lightweight (no tests/docs), and renaming its registry key/key-folder to `luxtronikex` aligns it with the plugin's own metadata and key name, ensuring proper recognition. Preserving `"windows"` platform support from `PR:66` is crucial.

Implementation notes:
- Modified `handle_git_ownership_failure` in `plugin_core.py` to first retry the command with `-c safe.directory=<repo_dir>` and log a normal informational Log message. If that fails, it falls back to the original `repair_git_repository_ownership` (chown) behavior.
- Updated `tests/test_plugin_update_status.py` with tests for both successful `safe.directory` bypass and fallback.
- Regenerated `plugin.py` from `plugin_core.py`.
- Renamed the Luxtronik plugin key to `luxtronikex` and pointed it to the `dist` branch in `registry.json` and `update_times.json`, keeping Windows support.
- Updated `tests/test_ui_smoke.py` to use temporary files for javascript/node validations to prevent failures on environments using Bun node-shims.

Verification:
- `pytest`: 133 passed.
- `python3 .github/scripts/validate_plugins.py`: passed.

Public action:
- Prepared a draft comment to close `ISSUE:70`.
- Prepared a draft comment to close `PR:71` (which is fully integrated into master now).

## 2026-06-29 - Avoid self-update bridge timeout and accept Luxtronik Windows metadata

Decision: make PyPluginStore self-update asynchronous from the API command handler, and accept the `PR:66` registry metadata from the contributor source.

Rationale:
- `ISSUE:65`: updating PyPluginStore from inside its own command handler can mutate/reload the live plugin before the custom UI bridge receives a response, producing a browser timeout even when the update command starts.
- Normal plugin updates do not have that self-mutation problem and should keep the synchronous behavior because users benefit from immediate success/error feedback.
- A detached self-update helper lets the UI receive a response first, but it must not use `git reset --hard HEAD` or `git pull --force` against the live manager checkout.
- The UI must not immediately reload the plugin list after manager self-update, because that can race the Domoticz plugin reload and look like another timeout.
- Pre-flight cannot make live in-place updates fully atomic, but it can reject known unsafe states before any file mutation starts.
- `PR:66`: adding Windows platform metadata for `luxtronik-domoticz-plugin-v2` is low-risk and supported by the plugin's standard-library TCP implementation. The fetched PR diff is registry-only and matches the local change.

Implementation notes:
- Added a `self_update.log` helper path.
- Added pre-flight checks for git availability, repository root, clean tracked files, upstream presence, fast-forward-only state, required candidate files, and Python syntax in the candidate `plugin.py` and `plugin_core.py`.
- Added a detached self-update helper that repeats the clean tracked-file check and applies the update with `git merge --ff-only`.
- `UpdatePythonPlugin()` now schedules that helper and returns a success message for `00-PyPluginStore`.
- Update API success responses include a `message` when the backend returns one.
- The custom UI displays that message and skips immediate `loadPlugins()` only for manager self-update.
- Added focused regression coverage for self-update scheduling, pre-flight failures, candidate validation, already-current state, and the UI reload guard.
- Fetched `PR:66` as `origin/pr/66` and updated `registry.json` so `luxtronik-domoticz-plugin-v2` supports `linux` and `windows`; committed as `1814323` with the PR author preserved.
- `plugin.py` was regenerated from `plugin_core.py`.

Verification:
- `pytest -q`: 130 passed.
- `python -m py_compile plugin_core.py plugin.py .github/scripts/generate_plugin.py`: passed.
- `git diff --check`: passed.
- `python .github/scripts/validate_plugins.py`: passed for 328 plugins before the code-only pre-flight follow-up.

Public action:
- Pushed `ISSUE:65` fixes as `47e2d73` and `28c7f43`; master workflows passed.
- No public comment or PR close action has been taken for `PR:66`.

## 2026-06-29 - Registry additions and version numbers

Decision: add `Domoticz-Home-Connect-Plugin` to registry and defer `version numbers visible ?`.

Rationale:
- `ISSUE:62`: `mario-peters/Domoticz-Home-Connect-Plugin` is a valid plugin requested by a user.
- `ISSUE:61`: The user requested displaying installed vs available version numbers. However, PyPluginStore uses `git` to check for updates (commits behind/ahead) rather than parsing version strings from the source code, so it doesn't know the "available version" until it downloads it. To read the available version without downloading for over 300 plugins would require excessive GitHub API calls.

Implementation notes:
- Appended `Domoticz-Home-Connect-Plugin` by `mario-peters` to `registry.json`.
- Drafted a response to close or defer `ISSUE:61` explaining the technical limitations.

Verification:
- Manually checked `registry.json` format.

Public action:
- None yet. Requires approval before commenting on issues and committing.

## 2026-06-28 - Treat stale API bridge responses as responses, not commands

Decision: keep the existing two-device custom UI bridge, but explicitly clear and ignore stale response payloads when the trigger fires.

Rationale:
- The browser command payloads are intentionally small, so the 2000-character inbound guard is still useful.
- The plugin responses can be large because `list_plugins` returns the full registry and current UI state.
- The same Domoticz text device currently carries both directions, so a prior response can still be present when the switch trigger fires for the next command.
- Changing the device model would add migration risk for existing installations; clearing and classifying stale responses fixes the reported path with less user-facing change.

Implementation notes:
- Added `API_PAYLOAD_MAX_LENGTH` for the inbound command guard.
- Empty payloads are ignored.
- Large response-looking payloads are cleared and ignored without error, including truncated strings that start with a response `status` field.
- Oversized non-response requests still log `API Payload exceeds length limit.` and are cleared.
- The UI clears the payload device before command send and after matching response receipt.
- `plugin.py` was regenerated from `plugin_core.py`.

Verification:
- `pytest -q`: 123 passed.
- `python -m py_compile plugin_core.py plugin.py .github/scripts/generate_plugin.py`: passed.
- `git diff --check`: passed.
- `python .github/scripts/validate_plugins.py`: passed for 327 plugins.

Public action:
- Product changes committed locally as `66ae709` after maintainer approval to commit and push.
- No issue comment or close action has been taken yet.

## 2026-06-28 - Treat Domoticz native notification API as optional

Decision: guard all direct `Domoticz.SendNotification` calls behind a compatibility wrapper.

Rationale:
- Domoticz `2025.1` build `16682` does not expose `SendNotification` to Python plugins.
- `Mode4=AllNotify` can find an available update during startup, then crash `onStart()` when notification delivery is attempted.
- The notification is useful but not required for plugin startup, update status checks, or custom UI operation.

Implementation notes:
- `sendDomoticzNotification()` checks whether `Domoticz.SendNotification` is callable.
- If missing, it logs that notification delivery was skipped and returns `False`.
- If present, it sends the notification and preserves existing behavior.
- The setup folder warning and update notification path both use the wrapper.
- `plugin.py` was regenerated from `plugin_core.py`.

Verification:
- `pytest -q`: 117 passed.
- `python -m py_compile plugin_core.py plugin.py .github/scripts/generate_plugin.py`: passed.
- `git diff --check`: passed.

Public action:
- None yet. Requires approval before commenting on `ISSUE:57` or shipping.

## 2026-06-28 - Resolve new discovery and bridge regressions locally

Decision: implement a local fix batch for `ISSUE:52`, `ISSUE:53`, `ISSUE:54`, `ISSUE:55`, and `ISSUE:56` before taking public action.

Rationale:
- The missing-plugin reports are valid public registry gaps for live repositories with usable `plugin.py` files.
- The hidden API payload report is caused by the UI bridge searching only `used=true` devices; hidden Domoticz devices must still be discoverable for command transport.
- The Docker icon report is low-risk to improve with a root-relative image fallback while preserving the existing relative image path.
- The `ISSUE:46` follow-up shows that local registry aliases should prevail when they collide with public repository aliases; local overlays are explicit user intent.
- Scheduled update checks should not create error log lines for installed plugins whose git state cannot be checked. The UI can still show `unknown`.

Implementation notes:
- Added public registry entries for `Domoticz-SMA-SunnyBoy` and `NUT_UPS`, plus update timestamps from the current repository heads.
- `build_installed_plugin_lookup()` now prunes public lookup candidates when local registry candidates share the same lookup key.
- `choose_installed_plugin_match()` prefers local candidates when candidate evidence conflicts.
- `CheckForUpdatePythonPlugin()` now uses `getGitUpdateStatus()` and only notifies when status is `available`; `unknown` is debug-only.
- `pypluginstore.html` now queries `getdevices` with `used=all` and adds a fallback icon URL.
- `plugin.py` was regenerated from `plugin_core.py`.

Verification:
- `pytest -q`: 115 passed.
- `python -m py_compile plugin_core.py plugin.py .github/scripts/generate_plugin.py`: passed.
- `git diff --check`: passed.
- `python .github/scripts/validate_plugins.py`: passed for 324 plugins.

Public action:
- None yet. Requires approval before commenting on issues, closing issues, or merging/releasing.

## 2026-06-27 - Preserve and broaden installed plugin detection

Decision: use tiered installed-plugin detection that prefers matching git remotes first, then recognized `plugin.py` `externallink`, then exact registry-key folders, unique repository/archive folder names, and unique `plugin.py` key/name metadata.

Rationale:
- This preserves the pre-`v2.11.0` behavior for plugins installed by PyPluginStore under their canonical registry key.
- Some real plugin folders use local aliases or repository names rather than registry keys, especially local overlay entries.
- The actual git remote is the strongest available identity signal when it matches the loaded registry.
- A recognized `plugin.py` `externallink` is stronger than folder naming and can support private forks whose git remote is not in the public registry.
- The `ISSUE:46` screenshots show missing installed cards consistent with repo-folder aliases and punctuation/case variants.
- Ambiguous normalized matches should still be skipped, and inferred folder matches with clearly conflicting metadata should not be accepted as that inferred plugin.

Implementation notes:
- Matching git remotes no longer require `plugin.py` metadata.
- `plugin.py` `externallink` can identify an arbitrary local folder and overrides exact folder-key and folder-name inference when it points to a unique loaded registry entry.
- A git repo with an unmatched remote can continue through later folder and metadata matching.
- An unknown `plugin.py` `externallink` can continue through later exact folder-key and metadata key/name matching.
- If inferred repository/archive folder matching conflicts with local metadata, the inferred candidate is skipped and matching continues to later metadata key/name matching.
- Repository/archive folder names can match with flexible punctuation/case normalization when the result is unique.
- Repository/archive folder names also index Domoticz-affix-stripped forms, so `APC UPS-main` can match `Domoticz_apc_ups_plugin` on branch `main`.
- `plugin.py` key/name can identify an arbitrary local folder when the result is unique.
- Matching uses structured candidate evidence with source, priority, and detail fields; `match_installed_plugin_key()` remains as a string-returning compatibility wrapper.
- `list_plugins` and `refresh_update_status` responses include `installed_match_details` for diagnostics.
- `plugin.py` was regenerated from `plugin_core.py`.

Verification:
- `pytest -q`: 95 passed.
- `python -m py_compile plugin_core.py plugin.py .github/scripts/generate_plugin.py`: passed.
- `git diff --check`: passed.

Public action:
- None yet. Requires approval before commenting on `ISSUE:46` or shipping.

## 2026-06-20 - Implement local registry overlay from PR:32 intent

Decision: accept the feature direction from `PR:32` but implement it locally instead of merging the contributor branch.

Rationale:
- The feature helps users manage private, forked, or locally modified plugins from the same UI.
- The PR patch removed dynamic remote registry fetching, which would regress a core project feature.
- The PR documented `register_local.json` while implementing `registry_local.json`.
- The PR UI expected `local_plugins` from the backend, but the backend did not provide it.

Implementation notes:
- Shipped to `master` as `36f3bcf`.
- Keep remote `registry.json` fetch as primary source.
- Keep bundled `registry.json` as offline fallback.
- Overlay ignored `registry_local.json` entries after the public registry.
- Return `local_plugins` in API responses so the UI can show a Local badge.
- Support full Git clone URLs for private/local registry entries.
- Report git clone failures through the API instead of always returning install success.

Verification:
- `pytest -q`: 52 passed.
- `python -m py_compile plugin_core.py plugin.py .github/scripts/generate_plugin.py`: passed.
- `git diff --check`: passed.

Public action:
- Commented on and closed `PR:32`.
