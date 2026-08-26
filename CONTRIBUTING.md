To add a plugin, open a pull request with one complete registry-v2 package
record and its certified Domoticz identity.

## Registry maintenance

Public registry entries live in `registry.json`. Private plugins, local forks,
and test branches should use `registry_local.json`; see
[docs/registry_local.md](docs/registry_local.md).

The public document has `schema_version: 2` and a `packages` array. Package IDs
are explicit values, not JSON object keys:

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

`package_id` is the stable PyPluginStore identity. `domoticz_key` is the exact
root `plugin.py` `<plugin key="...">` and may differ from it. A Domoticz key
change is a compatibility change and needs explicit review. Repository identity
uses one canonical, credential-free HTTPS web URL; use an empty `platforms`
array when support is unknown.

All delivery policies are explicit. GitHub, GitLab, and Codeberg packages
normally use `release_if_indexed`, a stable-tag source-archive policy, and
`git_supported: true`. Self-hosted Forgejo/Gitea records also require reviewed
API and web bases. Generic HTTPS records require a strict versioned manifest
URL and allowed origins. Unknown hosts stay Git-only until a provider policy is
reviewed. See [Release and Git Management](docs/release_management.md).

Do not add keyed package objects, positional arrays, `owner`/`repo` aliases, or
`plugin_key` to new public metadata. Those shapes exist only at explicit host
upgrade boundaries and are never emitted as schema v2.

To audit identities before a registry migration or broad metadata change:

```bash
python .github/scripts/certify_package_identities.py \
  --output /tmp/pypluginstore-package-identities.json
```

The certifier reads the selected root `plugin.py` and records its exact
Domoticz key and SHA-256. Missing or ambiguous keyed plugin tags block release
authorization.

Maintainers can infer and add platform metadata with:

```bash
GITHUB_TOKEN="$(gh auth token)" python .github/scripts/detect_plugin_platforms.py --missing-only
```

Maintainers can audit the public registry for entries whose configured branch no longer contains a root-level `plugin.py` with:

```bash
python .github/scripts/cleanup_registry.py
python .github/scripts/cleanup_registry.py --apply
```

The cleanup script supports GitHub, Codeberg, and GitLab entries. Dry-run is the
default; `--apply` removes missing entries from `registry.json`,
`update_times.json`, and `.github/platform_detection.json`.

## Theme registry maintenance

Theme registry entries live in `themes.json`. Use the following schema:

```json
{
  "my-theme": {
    "display_name": "My Theme",
    "author": "creator",
    "repository": "domoticz-my-theme",
    "branch": "dist",
    "description": "Clean, modern theme.",
    "target_dir": "my-theme",
    "source_path": ".",
    "entry_files": ["custom.css"],
    "contains_javascript": false,
    "requires_restart": "first_install"
  }
}
```

Private themes and local tests use `themes_local.json`.

Themes are compiled and served directly from `www/styles/` in Domoticz. To support different theme structures and compilation needs without complex schema additions, the registry uses two optional configuration parameters:
- `branch`: The checkout reference or build branch (defaults to `master`).
- `source_path`: The directory in the checkout containing the installable files (defaults to `.`).

### Supported Developer Workflows

Theme authors are encouraged to choose the workflow that best fits their project's complexity:

1. **Minimal Hobbyist Workflow (Direct Clone)**
   - **Structure:** A single `custom.css` (and optional theme assets) committed directly to the root of the main branch.
   - **Metadata:** `branch` is set to the default branch (e.g. `main` or `master`), and `source_path` is `.`.
   
2. **Subfolder Compiler Workflow (Single Branch)**
   - **Structure:** The repository contains raw modular source code alongside compiled production assets. Production-ready files (including `custom.css`) are committed to a subfolder (e.g. `dist/`).
   - **Metadata:** `branch` is set to the default branch, and `source_path` is set to the output folder (e.g. `dist`). This prevents dev config files (like `package.json`, `.gitignore`, `webpack.config.js`) from being served directly under `www/styles/`.
   
3. **Clean Release Branch Workflow (Branch Split)**
   - **Structure:** The developer maintains a clean default branch for source files. A CI/CD workflow (like GitHub Actions) compiles, flattens, and publishes only the production-ready theme assets directly into the root of a dedicated build branch (e.g. `release` or `build`).
   - **Metadata:** `branch` is pointed to the build branch (e.g. `release`), and `source_path` is `.`. This is the most secure and performant option, keeping development files out of the repository checkout and avoiding sequential `@import` latencies for the end-user by shipping flattened CSS.

## Release index maintenance

Stable release discovery uses GitHub, GitLab, Codeberg/Forgejo, Gitea, and
generic HTTPS adapters that emit one provider-neutral `release_index.json`
bound to the exact `registry.json` bytes. The runtime uses the same provider
contracts for explicit status refreshes.

Release-index schema v2 likewise uses `releases` and `tombstones` arrays whose
records contain `package_id`; it does not serialize package IDs as object keys
or emit the legacy `plugin_key` identity field.

The weekly workflow checks every package whose explicit delivery policy is
release-eligible, including packages with no previously indexed release. A
maintainer can therefore publish releases after years of Git-only development:
the next successful scan can certify and propose the release without changing
`package_id` or editing the registry again.

Tombstoned releases remain blocked by release ID. A newer fully certified
release may reactivate the package only with an incremented revision and an
explicit `supersedes` link to the tombstone; that transition is reviewed in the
same weekly pull request.

Use report-only mode to inspect the same full scan without changing the index:

1. Inspect candidates without changing the tracked index. A cache avoids repeating provider and archive requests, and an output file keeps the provider-specific coverage and exclusion report for review:

```bash
python .github/scripts/generate_release_index.py \
  --report-only \
  --cache /tmp/pypluginstore-release-candidates.json \
  --report-output /tmp/pypluginstore-release-report.json
```

Review repository and Domoticz identities, tag policy, immutable source commit,
archive layout and hashes, mutable paths, migration evidence, predecessor
lineage, provider failures, and explicit no-release results. Then generate and
validate the exact registry/index pair:

```bash
python .github/scripts/generate_release_index.py \
  --update \
  --cache /tmp/pypluginstore-release-candidates.json
python .github/scripts/validate_plugins.py
```

Review the generated diff through a pull request. Weekly automation performs
the preview and generation, but does not publish runtime metadata without that
review and merge.

Commit-addressed source archives provide automatic Git-migration evidence. For
an attached ZIP, compare its canonical selected tree with the source archive at
the same commit; exact equivalence can authorize automatic migration. A
different or unverifiable asset remains manual. Generic manifest artifacts are
manual unless a stronger reviewed source-continuity contract authorizes them.

Do not hand forge API responses directly to the runtime or silently fall back
to Git after a release failure. A release-managed installation with unavailable
metadata remains blocked; a `release_if_indexed` package that has never
activated Release continues on Git. Existing compatibility/rollback safety
holds, notify-only mode, dirty or diverged checkouts, repository mismatches, and
insufficient migration evidence must all prevent automatic channel changes. New
intentional Git use for a public package requires a local registry override.

The public registry and release index are strict v2. Do not reintroduce a hybrid
document with legacy identity keys. Approved host-local legacy metadata remains
limited to explicit upgrade boundaries. Lagging installations retain their last
trusted metadata pair and update the manager through its independent Git
self-update path. PyPluginStore self-update stays Git-based and is intentionally
outside the release index.

## Generated plugin.py

`plugin.py` is generated from `plugin_core.py` plus the Domoticz XML header. If you edit `plugin_core.py`, run:

```bash
python .github/scripts/generate_plugin.py
```

Commit both `plugin_core.py` and the regenerated `plugin.py`. The generated file should use LF line endings; the repository `.gitattributes` file is configured to help keep this consistent.

## Releases

Release PRs are generated by release-please from Conventional Commit subjects on `master`.
Use `feat: ...` for features and `fix: ...` for user-facing fixes; plain subjects such as
`Install ...` are ignored and will not open a release PR.
