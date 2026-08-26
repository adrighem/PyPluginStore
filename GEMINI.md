# PyPluginStore Repository Guidelines

This document maps repo-wide development guidelines, conventions, and maintainer workflows.

## Architecture Invariants

### Single Source of Truth
- **Core Runtime:** `plugin_core.py` is the absolute source of truth for all runtime logic.
- **Regeneration:** Always run `python3 .github/scripts/generate_plugin.py` immediately after editing `plugin_core.py` to keep `plugin.py` in parity. Do not edit `plugin.py` directly.

### Sibling Module Decoupling
- Sibling modules (`package_identity.py`, `package_registry.py`, `release_domain.py`, `release_providers.py`) must remain pure, provider-neutral, and completely decoupled from Domoticz, network, or filesystem dependencies.
- **package_identity.py:** Dedicated exclusively to non-executing tokenized certification of `plugin.py` metadata and fingerprints.
- **package_registry.py:** Implements schema v2 registry parsing and strict contract validation.
- **release_domain.py:** Contains pure enums, lifecycle phases, and release candidate definitions.
- **release_providers.py:** Houses provider-agnostic and provider-specific release discovery interfaces.

### Code Integrity & Fingerprinting
- **Module Fingerprinting:** Sibling and core modules use `_capture_loaded_source_fingerprint()` to compute file sizes and SHA-256 hashes at load time to detect runtime tampering.
- Any architectural change to module files must preserve, run, or update these integrity checks.

## Development Standards

### PEP 8, PEP 484 & File Size limits
- **Typing:** Strict PEP 484 static typing is required.
- **Modular Code:** Prefer keeping Python files under 500 lines (with the single exception of `plugin_core.py` / `plugin.py`). Adhere to Single Responsibility and fail loudly rather than suppressing errors.
- **No Trailing Spaces:** Ensure all code and documentation files have no trailing spaces.
- **Local/Runtime Artifacts:** Never commit local database state, cache, or private registry overlays (e.g. `update_times.cache.json`, `update_times.json`).

### Testing Requirements
- Every new feature or bug fix must have a corresponding isolated unit test in `tests/` to verify behavioral correctness.
- Always run the test suite (via `pytest`) after changes to ensure zero regressions.

## Maintainer Workflow

### Release Please & Conventional Commits
- **Do Not Include Maintainer Updates in Releases:** All files under `.github/` are ignored in `release-please-config.json` via `ignore-paths`.
- **Commit Scopes for Maintainer Docs:** When making maintainer-only updates (notes, decisions, runs), use the `chore(maintainer):` or `chore(docs):` prefix.
- **Never Use Feature/Fix Types for Maintenance:** Never use `feat:` or `fix:` for maintainer state files or notes.

### Contribution Policies
- **Prefer Reviewed PRs:** Treat reviewed external PRs as the preferred source of changes when their intent, implementation, provenance, and validation are sound.
- **High-Signal Updates:** Always respond using a concise, pragmatic, and high-signal updates writing style.
