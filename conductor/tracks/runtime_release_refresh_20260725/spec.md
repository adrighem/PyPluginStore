# Runtime Release Refresh

## Goal

When a user presses **Refresh status**, every eligible release-managed plugin
checks its reviewed upstream release provider directly. A newer stable release
becomes updateable after the host independently resolves its immutable source,
downloads it within strict limits, and validates its archive and Domoticz
identity. The user does not wait for the weekly release-index workflow.

## Requirements

- Keep the central registry and release index authoritative for package
  discovery, entry into Release mode, de-certification tombstones, and fallback.
- Query providers only for installed, non-local plugins with valid
  release-managed install metadata and no active tombstone.
- Reuse one provider-neutral adapter contract for GitHub, GitLab,
  Codeberg/Forgejo, Gitea, and generic manifests.
- Use bounded, pinned-DNS HTTPS for provider JSON and archive downloads. Do not
  use ambient credentials, proxies, private addresses, or unreviewed redirect
  origins.
- Apply the registry's stable tag, artifact, source path, pagination, endpoint,
  asset, and origin policies without learning arbitrary new policies.
- Reject drafts, prereleases, upcoming or future releases, malformed provider
  data, mutable tag resolution, identity mismatches, unsafe ZIPs, and candidates
  that are not newer than the installed release.
- Locally certify archive size, SHA-256, tree digest, selected file inventory,
  root `plugin.py`, Domoticz key, repository identity, and immutable commit or
  source revision before reporting an actionable update.
- Re-download the artifact during Update and require the locally certified
  digest and tree to match before the existing transaction, preservation,
  dependency, rollback, and restart flow activates it.
- Record whether installed metadata came from `release_index` or
  `provider_live`; never claim a provider-live target was certified by an index
  generation.
- Cache successful checks by repository, policy, and installed release for a
  short request-throttling window. Provider failures must preserve the central
  indexed status and installed files.
- Keep Git-managed packages, initial Git-to-Release migration, local overrides,
  and PyPluginStore self-update on their existing paths.
- Show users that an available target was verified directly by the host.

## Acceptance Criteria

- A release-managed plugin whose provider publishes a newer matching stable
  release shows that version immediately after **Refresh status**.
- The Update action installs that exact immutable candidate without a weekly CI
  index update and writes provider-live install authority.
- Replaced assets, changed tags, invalid identities, unsafe archives, provider
  failures, or exhausted request budgets cannot change plugin files.
- An indexed tombstone cannot be bypassed by direct provider discovery.
- Existing indexed installs and metadata schemas remain readable and are
  upgraded safely.
- Provider, runtime HTTP, archive, management, UI, generated-output, Linux, and
  Windows tests pass.

## Out of Scope

- Direct provider discovery for Git-managed or uninstalled packages.
- Automatically changing registry release policies on a Domoticz host.
- Background polling beyond the existing refresh/automation lifecycle.
- Release-based self-update of PyPluginStore itself.
