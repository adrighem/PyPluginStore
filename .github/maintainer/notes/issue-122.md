# ISSUE:122 - Git-to-Release notification and lifecycle failures

Status: closed; v2.24.2 is published and field-confirmed by Eddie-BS.

Reporters:
- `MadPatrick` reported repeated update notifications for Git-managed plugins.
- `Eddie-BS` and `MadPatrick` then reproduced failed or confusing Git-to-Release
  switching, an older indexed target, a missing update action, and state that
  appeared to revert after restarting Domoticz.

Released resolution:
- `v2.22.1` distinguishes genuine updates from Release-channel availability.
- `v2.22.3` fixes the reported `expected_current` and folder identity failures.
- `v2.23.0` refreshes the configured provider directly for plugins that are
  already managed through the Release channel.
- `v2.24.0` adds direct Git-to-latest-Release evaluation, durable dependency
  and activation recovery, backend-owned actions, actionable status, and
  bounded restart recovery.
- `v2.24.1` hides expected internal Git fallback reasons and reports safe,
  actionable dependency failure categories without exposing installer output.
- `v2.24.2` avoids rebuilding unchanged dependencies during a clean,
  same-commit Git-to-Release switch and adds safe package-owner diagnostics for
  real dependency failures.
- `v2.24.3` identifies the retained Git or Release target in restore actions
  and confirmations.
- `v2.24.4` keeps those confirmations explicit while shortening the card
  buttons to `Restore Git`, `Restore vX`, or `Rollback`.

Released behavior:
- Git installations can refresh the provider, host-certify the latest release,
  display its version, and switch directly to it without first installing an
  older indexed release.
- Release discovery, transitions, notices, and actions use explicit lifecycle
  domain contracts.
- Dependency installs use immutable shared generations, forced uv copy mode,
  sanitized environments, deterministic manifests, atomic swaps, and recovery.
- Activation and dependency transitions are journaled and recovered at startup.
- Self-update restart recovery is bounded and reports inline actionable state.
- Healthy manager status stays silent; mismatches request either a Domoticz
  restart or a browser hard refresh.

2026-07-27 Eddie-BS diagnosis:
- Eddie's credential-safe check used the Domoticz Python 3.7.3 interpreter.
- Somfy passed by itself.
- `domoticz-solaredge-modbustcp-plugin` failed by itself and named
  `solaredge_modbus`; the combined requirement set failed on the same package.
- The current SolarEdge Git branch pins `solaredge_modbus==0.8.0` and
  `pymodbus==3.6.9`. Both published distributions require Python 3.8 or newer,
  which is consistent with Eddie's result. The diagnostic did not reveal his
  exact local pin, so that version match remains an evidence-backed inference.
- The global fresh-generation contract correctly refuses to omit an
  incompatible plugin. Skipping SolarEdge during a real dependency change
  could silently break it after restart.
- A clean Git-to-Release switch at the exact same commit and with identical
  requirements is different: it changes management metadata, not executable
  dependencies. v2.24.2 records `retain_live`, revalidates the live dependency
  tree at activation, and performs no installer run or global dependency
  rename.
- Real rebuild failures now reduce installer output to safe package IDs, map a
  direct requirement to its sanitized plugin owner, and include the Domoticz
  Python major/minor version. Raw output, requirement lines, index URLs, and
  environment values remain withheld.
- The generated runtime is current. The focused dependency/transaction suite
  passed 145 tests and the full sanitized suite passed 1,521 tests.
- Eddie-BS confirmed the switch works after upgrading to v2.24.2.

Remaining uncertainty:
- Eddie-BS needed to clear Firefox's cache manually. This needs reproduction
  before changing cache behavior.
- The card's generic `Rollback` action is ambiguous. A migration backup restores
  the retained Git checkout and intentionally has no `rollback_version`; a
  Release-to-Release backup can name its version.
- The later SolarEdge fork discussion is off-topic. Both alternatives are
  already in the registry and share a Domoticz plugin key, but no selection or
  display defect has been established.
- Current `master` and the v2.24.4 tag point at release merge `5814216`.

2026-07-26 follow-up diagnosis:
- Somfy 5.3.2 only requires `requests` and `urllib3`.
- The dependency transaction resolves requirements for every installed plugin
  in one command. Another installed plugin, host package-index access, Python
  compatibility, permissions, or disk state can therefore surface as a Somfy
  operation failure.
- A disposable resolver check on `pietje.vanadrighem.lan` passed with its
  Python 3.13 aarch64-musl runtime, uv, its installed requirement set, and the
  Somfy requirements. Eddie's failure is not reproduced there.
- The live card message on pietje was isolated to
  `domoticz-kpn-experia-v10`: Git status was not yet checked and no reviewed
  Release entry exists. The Git fallback is correct, but the backend exposed
  the internal `release_entry_missing` reason in the card summary.
- A local patch preserves that reason internally while hiding it from the Git
  card. It also classifies dependency failures into allowlisted safe causes,
  reports how many plugin requirement files were resolved, never logs raw
  installer output, and makes uv discovery use the same sanitized path as
  execution.
- `Eddie-BS` then supplied four screenshots. They confirm:
  - PyPluginStore v2.24.0 is loaded.
  - Somfy is a Git-managed v5.3.2 checkout.
  - The card correctly offers a management-mode migration to the v5.3.2
    Release and disables the ordinary Git Update action.
  - The dependency failure occurs after confirmation and before activation.
  - The screenshots contain no additional resolver detail, so they isolate the
    failing stage but not its cause.
- The screenshots also expose a separate presentation defect: schema-v2 cards
  show `Repo: (master)` because the full repository URL occupies the normalized
  author slot while the legacy repository-name slot is empty. Repository
  resolution and operations still use the full URL, so this does not explain
  the dependency failure.
- They expose a second presentation defect: the backend migration summary takes
  precedence over the frontend formatter but omits `available_version`, so the
  card says a Release migration is available without naming target v5.3.2.
  Existing backend and frontend tests cover the two layers separately and miss
  this integrated presentation result.
- Follow-up commit `1af52fd` was pushed to `master`.
- Release `PR:140` was corrected to reference rather than close `ISSUE:122`,
  merged as `8b316f6`, and published as v2.24.1.

Verification:
- Full sanitized suite on exact `PR:138` head: 1,497 tests passed.
- Generated-runtime parity, Python compilation, and live validation of all 256
  registry repositories passed.
- Final PR and post-merge workflows are green.
- Local follow-up patch: 1,509 tests passed in a sanitized environment.
- Exact corrected `PR:140` head: 1,509 tests passed in a sanitized environment.
- All `PR:140` and post-merge release workflows passed.

2026-07-28 compact-label follow-up:
- Shortened the Git backup button to `Restore Git`.
- Kept `Restore vX` when the Release version is known.
- Use `Rollback` when the Release version or target metadata cannot be shown.
- Confirmation dialogs remain explicit about the previous Git, Release, or
  generic version before the action runs.
- The full sanitized suite passes 1,526 tests.
- Pushed as `d3529c2`; all post-push workflows passed and Release Please opened
  `PR:143` for v2.24.4.

Recommended next step:
- Keep `ISSUE:122` closed because the reported failure is fixed and confirmed.
- Treat Firefox caching and SolarEdge discoverability as separate follow-ups
  only if they can be reproduced or specified.

Public action:
- Posted the approved v2.24.0 upgrade and restart-verification request:
  `https://github.com/adrighem/PyPluginStore/issues/122#issuecomment-5083304310`.
- Posted the approved screenshot request to `MadPatrick`:
  `https://github.com/adrighem/PyPluginStore/issues/122#issuecomment-5085048291`.
- Pushed approved fix commit `1af52fd` to `master`.
- Corrected and merged `PR:140`, then verified the v2.24.1 release:
  `https://github.com/adrighem/PyPluginStore/releases/tag/v2.24.1`.
- Posted the approved v2.24.1 retry request to `Eddie-BS`:
  `https://github.com/adrighem/PyPluginStore/issues/122#issuecomment-5085501855`.
- Pushed the approved exact-migration fix as `9b4442d`, corrected its
  cross-platform test as `1897f56`, and released v2.24.2 through `PR:141`.
- Posted the approved explanation and upgrade request:
  `https://github.com/adrighem/PyPluginStore/issues/122#issuecomment-5091280280`.
- `Eddie-BS` confirmed the switch works and asked what `Rollback` restores:
  `https://github.com/adrighem/PyPluginStore/issues/122#issuecomment-5091862026`.
- The release closed the issue. No new public action was taken during the
  follow-up maintenance audit.
- Pushed the approved target-specific restore wording as `cbffb6a`; all
  post-push workflows passed and Release Please opened `PR:142`.
- Posted the approved explanation:
  `https://github.com/adrighem/PyPluginStore/issues/122#issuecomment-5094975084`.
- Reviewed and merged `PR:142` as `6a926e6`, published v2.24.3, and verified
  all post-merge workflows:
  `https://github.com/adrighem/PyPluginStore/releases/tag/v2.24.3`.
- Reviewed and merged `PR:143` as `5814216`, published v2.24.4, and verified
  all post-merge workflows:
  `https://github.com/adrighem/PyPluginStore/releases/tag/v2.24.4`.
