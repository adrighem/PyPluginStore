# Unified Release Lifecycle Refactor

## Goal

Replace the fragmented release-management paths with one explicit lifecycle
that lets a Git-managed plugin move directly to the latest host-certified
stable release, activates code and dependencies through recoverable immutable
generations, and gives the UI one authoritative management model.

## Requirements

- Observe an installed plugin once per request and keep its logical package ID
  separate from its physical installation folder.
- Use the reviewed registry and release-index policy as the trust anchor, while
  allowing the host to discover and certify the latest eligible upstream
  release for both Git-managed and Release-managed installations.
- Migrate a clean, compatible Git checkout directly to that latest certified
  release. Do not require an intermediate migration to an older indexed
  release.
- Keep tombstones, repository identity, provider policy, stable-tag policy,
  artifact policy, and migration evidence authoritative.
- Preserve a last verified host candidate when a later provider refresh fails;
  mark it stale rather than silently reverting to an older indexed target.
- Build dependencies through one service used by Git and Release operations.
- Run installers with a minimal allowlisted environment and redact unsafe
  diagnostic data.
- Make uv produce independent regular files. Normalize legacy hardlinked
  regular files by verified content copying, while continuing to reject
  symbolic links and special files.
- Build dependency generations without stale package files and record their
  requirements, owners, resolved distributions, and tree digest.
- Retain the verified live dependency generation for a clean Git-to-Release
  migration only when Git HEAD equals the Release commit and the target
  requirements are byte-identical. Revalidate that generation at activation.
- Serialize Git and Release dependency changes through the same workflow lock.
- Model durable transitions as explicit outcomes with idempotent cancellation,
  recovery, and rollback behavior.
- Treat runtime cache directories consistently during release verification.
- Return one backend-owned plugin management view containing installation,
  latest release, transition, lifecycle notice, and explicit action
  descriptors.
- Keep installed and latest versions visible. A current release may be visually
  quiet, but restart, rollback, verification, and failure notices must never be
  hidden.
- Split release-domain, dependency, transaction, and presentation logic into
  focused source modules where runtime packaging remains reliable.
- Keep generated `plugin.py`, manager identity checks, self-update candidate
  checks, documentation, and compatibility readers synchronized.

## Acceptance Criteria

- Git v5.3.2 with indexed v5.3.1 and upstream v5.3.2 offers one certified
  migration directly to v5.3.2.
- A package named `Somfy` installed in `domoticz_somfy` retains that physical
  folder through activation, restart finalization, and rollback.
- A preactivation mismatch returns one structured conflict, leaves live state
  untouched, and can be cancelled repeatedly without a secondary cleanup
  failure.
- `current + restart_required` renders a prominent restart notice and disables
  update with a reason.
- A failed refresh keeps the last verified host candidate as stale.
- Real uv output in dependency staging has link count one.
- Legacy regular hardlinks import into independent files; symlinks, FIFOs,
  devices, cross-filesystem escapes, and mutation races remain blocked.
- Dependency upgrades, downgrades, and removals leave no stale distribution
  metadata.
- A same-commit, same-requirements Git-to-Release migration does not invoke an
  installer or rename the global dependency directory.
- Conflicting requirements report the owning plugins and do not offer an
  unsafe confirmation override.
- Git and Release dependency mutations cannot race.
- Recovery is idempotent across injected failures at every durable transition.
- The complete API-to-DOM flow is covered by regression tests.

## Out of Scope

- A public switch from Release management back to Git.
- Unreviewed provider configuration or ambient provider credentials.
- Posting to issue #122, opening a pull request, pushing the branch, or
  deploying to a Domoticz host without separate authorization.
