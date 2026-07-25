# Release and Git Management

PyPluginStore is release-first when a package has a fresh, certified entry in
`release_index.json`. Once a plugin is Release-managed, an explicit
**Refresh status** also checks its configured upstream release provider
directly. Git remains a supported channel: it is used while no release is
indexed, when the package policy selects Git, or when a local registry override
explicitly selects a different source. Existing keep-Git state remains honored
for upgrade and rollback safety, but no public action creates a new general
Git-channel preference.

## Package identity

The registry keeps three identities separate:

- `package_id` is the stable PyPluginStore identity. It links registry,
  release-index, installed-state, and transaction records. It remains unchanged
  when an upstream project moves from Git-only development to releases.
- `domoticz_key` is the exact `<plugin key="...">` value in `plugin.py`.
  Domoticz uses it to bind the plugin to its hardware configuration. It may
  differ from `package_id`, and changing it is a reviewed compatibility event.
- `repository.url` identifies the upstream source. It is used to verify that an
  existing Git checkout belongs to the registered package.

Neither the repository name nor the Domoticz key is inferred as another
identity. Release certification records the observed `domoticz_key` and
`plugin.py` SHA-256 so the runtime can repeat the same check before activation.

Public registry schema v2 is record-based. Package IDs are values, not JSON
object keys, and the old owner/repository split, positional arrays, and
`plugin_key` identity field are not part of the schema:

```json
{
  "schema_version": 2,
  "packages": [
    {
      "package_id": "ExamplePlugin",
      "domoticz_key": "EXAMPLE",
      "description": "Example plugin for Domoticz",
      "repository": {
        "url": "https://github.com/example-owner/domoticz-example-plugin",
        "branch": "main"
      },
      "platforms": ["linux", "windows"],
      "delivery": {
        "preferred": "release_if_indexed",
        "git_supported": true,
        "release": {
          "provider": "github",
          "channel": "stable",
          "tag_pattern": "^v?[0-9]+(?:\\.[0-9]+){1,3}$",
          "artifact": "source_zip",
          "source_path": ".",
          "mutable_paths": []
        }
      }
    }
  ]
}
```

## How a Git-only package becomes release-first

`release_if_indexed` is the transition policy. A package with this policy uses
Git when `release_index.json` has no accepted release. The weekly scan checks
every eligible package, including packages that did not have a release on the
previous run. When a maintainer later publishes a stable release that matches
the package policy, automation resolves and downloads it, certifies its archive
and identities, and proposes the updated index in the weekly pull request.
After review and merge, no registry identity change is needed: new installs use
the release, and existing Git installs can choose **Use Release channel**
instead of continuing with Git commits.

Publishing a ZIP therefore can cause an automatic channel transition on a later
weekly scan, but it never bypasses certification or pull-request review. The
reviewed release index is the trust anchor for the first transition. After that
anchor exists, **Refresh status** may certify a newer provider release directly
on the host and offer that latest release for the Git-to-Release transition.

For eligible Git and Release-managed installations, **Refresh status** uses the
reviewed provider and artifact policy from the registry to resolve the latest
stable release. The host downloads the immutable candidate, verifies its
archive digest, safely extracts it, validates its canonical tree and plugin
identity, and caches that certified target in memory. The UI shows the
available version without exposing where certification happened. Pressing
**Update** or **Use Release** downloads the same immutable artifact again and
repeats those checks before activation, so this path does not wait for the next
weekly index run.

Installed metadata records whether a release was authorized by
`release_index` or certified locally as `provider_live`. Provider-live records
also store a candidate fingerprint. An indexed de-certification tombstone
always overrides a cached direct-provider result.

If a release was previously de-certified, the scanner keeps its tombstone and
will not reconsider the same release ID. A later release can reactivate the
package only after full certification; it receives a higher index revision and
records the tombstoned release as its predecessor. Runtime transition checks
enforce that lineage, so a provider cannot make an old rejected ZIP active again.

Provider policies are explicit in registry v2:

| Provider | Policy notes |
| --- | --- |
| GitHub | Stable, published, non-draft/non-prerelease releases; commit-addressed source ZIP by default. |
| GitLab | Stable, non-upcoming releases; commit-addressed repository archive by default. |
| Codeberg/Forgejo | Forge release API with reviewed tag, pagination, and archive policy. |
| Gitea | Gitea release API with explicit API and web bases for self-hosted instances. |
| Generic HTTPS | A strict, versioned manifest URL, allowed origins, immutable source revision, and ZIP metadata. |

GitHub, GitLab, and Codeberg packages receive an explicit standard stable-release
policy during registry migration. Self-hosted Forgejo/Gitea and generic HTTPS
sources need an explicit reviewed endpoint policy; unknown hosts remain Git-only
until one is added. The scanner and Domoticz runtime share the same provider
resolution contracts; the runtime only invokes them for an explicit refresh of
an existing Release-managed installation.

## Source archives, attached ZIPs, and migration evidence

The release index records an explicit migration mode and evidence instead of a
single eligibility flag:

- A commit-addressed forge source archive proves source continuity and may use
  automatic migration.
- An attached release ZIP is compared with the selected tree from the immutable
  source archive for the same release commit. Canonically equivalent trees may
  use automatic migration.
- A different or unverifiable attached ZIP remains a manual channel switch.
- A generic manifest ZIP is independently hash-, layout-, and identity-checked,
  but remains a manual migration unless a stronger reviewed continuity contract
  explicitly authorizes it.
- Missing source identity or contradictory evidence blocks migration.

All accepted archives are pinned by byte length and SHA-256, safely inspected
and extracted, checked by canonical tree digest, compiled, and checked against
the registered package and Domoticz identities. A release failure never falls
back to a branch update.

## Using the Release channel for an existing Git installation

The channel change appears as **Use Release channel**, separate from a normal
plugin update. Internally, PyPluginStore runs a migration preflight. It may fetch
object metadata only from the configured remote so it can inspect a newly
released commit; it does not reset, clean, stash, switch, or rewrite the working
branch during preflight.

Automatic migration requires all of the following:

- the checkout remote matches the registered repository;
- the release carries automatic continuity evidence and an immutable commit;
- installed `HEAD` equals that commit or is its ancestor;
- there is no Git lock, unresolved operation, submodule, tracked change, or
  unknown untracked file;
- every preserved path is permitted by the reviewed mutable-path policy.

Dirty, ahead, diverged, mismatched, locked, or insufficiently proven checkouts
stay on Git and show the reason. A matching local registry override keeps the
package Git-managed, while an existing safety hold prevents an immediate repeat
of a rolled-back migration.
Notification-only mode announces that the Release channel is available instead
of reporting a newer plugin version, and it does not change files.
Automatic-update mode executes only a fully proven transition; evidence marked
manual requires an explicit, content-bound approval.

Release operations stage code and a complete dependency generation, activate
them atomically, retain the previous state for rollback, and then require a
Domoticz restart. Git changes use the same dependency builder and workflow
lock. A generation resolves every installed plugin's requirements together,
uses copy mode with `uv`, records `.pypluginstore-environment.json`, and can be
recovered after interruption. Local executable changes are never silently
carried into a release.

## Using Git through a local override

Public registry packages do not offer a Release-to-Git channel switch. To use
branch-based Git updates, add a matching `registry_local.json` override through
the **Local registry** dialog. The local entry becomes the authoritative source
and remains Git-managed.

A verified migration backup may still be restored through **Rollback**. Rollback
records an internal keep-Git safety hold so the same Release is not immediately
reapplied; it is not the general way to choose Git. Add the local override for
ongoing Git updates. The override does not recreate `.git` in an existing
Release installation: restore a verified Git backup first, or remove and
reinstall the plugin after adding the override.

Private repositories, forks, local/LAN repositories, and `registry_local.json`
entries stay Git-managed. See [`registry_local.json` How-To](registry_local.md).

PyPluginStore's own self-update also intentionally stays Git-based. It is not
selected from `release_index.json`.

The manager uses a deterministic runtime build ID in addition to its semantic
version. After self-update, a changed runtime bundle requires a Domoticz
restart; a Git change outside that bundle does not. Until the loaded backend,
installed files, deployed custom page, and browser page agree, the Plugin Store
reports recovery guidance in its main status and keeps mutations read-only.

## Deployment upgrade path

The public v2 cutover is staged. First, a manager version that reads both legacy
metadata and strict v2 records is distributed while the public files remain on
the old shape. After an upgrade window, the repository publishes v2-only public
metadata. It does not publish a hybrid document with legacy identity keys.

An existing installation can keep using its last trusted cached registry/index
pair during the transition and use the independent Git self-update path to
install the v2-capable manager. Host-local v1 install metadata and transaction
journals are upgraded lazily and atomically when used. A valid legacy
`registry_local.json` is backed up and rewritten as v2 on its first successful
load; invalid input is left untouched.

## Metadata security

The runtime accepts the registry and release index as one digest-bound pair. A
monotonic sequence and expiry prevent stale operational metadata; artifact and
canonical-tree hashes detect mutation relative to the accepted index. These
checks do not make third-party plugin code trustworthy or defend against a
compromised registry distribution channel.
