# PyPluginStore Repository Guidelines

This document maps repo-wide development guidelines, conventions, and maintainer workflows.

## Maintainer Workflow

### Release Please & Conventional Commits
- **Do Not Include Maintainer Updates in Releases:** All files under `.github/` are ignored in `release-please-config.json` via `ignore-paths`.
- **Commit Scopes for Maintainer Docs:** When making maintainer-only updates (notes, decisions, runs), use the `chore(maintainer):` or `chore(docs):` prefix.
- **Never Use Feature/Fix Types for Maintenance:** Never use `feat:` or `fix:` for maintainer state files or notes.

### File and Code Integrity
- **Single Source of Truth:** `plugin_core.py` is the absolute source of truth for all runtime logic.
- **Regenerate Generated Files:** Always run `python3 .github/scripts/generate_plugin.py` immediately after editing `plugin_core.py` to keep `plugin.py` in parity.
- **Strict PEP 8 & Typing:** Adhere to PEP 8 standards, apply strict type hints, and fail loudly instead of using broad `except-pass` blocks.
- **No Trailing Spaces:** Ensure all code and documentation files have no trailing spaces.
- **Local/Runtime Artifacts:** Never commit local database state, cache, or private registry overlays (e.g. `update_times.cache.json`).

### Contribution Policies
- **Prefer Reviewed PRs:** Treat reviewed external PRs as the preferred source of changes when their intent, implementation, provenance, and validation are sound.
- **High-Signal Updates:** Always respond using a concise, pragmatic, and high-signal updates writing style.
