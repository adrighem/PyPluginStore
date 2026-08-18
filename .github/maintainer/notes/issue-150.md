# ISSUE:150 - Release - release metadata unavailable: Release metadata is expired; release changes are paused.

Status: resolved in master.

Reporter:
- `MadPatrick` opened the issue on 2026-08-17 reporting that they see "Release metadata is expired; release changes are paused." on most of their installed plugins.
- `Eddie-BS` confirmed seeing it, but only on the Somfy plugin.
- `MadPatrick` commented on 2026-08-18 that after the expiration fix, they see "Release - verification failed: release_mutation" on the FullyKiosk plugin.

Assessment:
1. **Index Expiration:**
   - The previous 7-day validity seconds for the weekly-updated release index left no margin for human delay in reviewing and merging automated PRs.
   - The cron job runs weekly on Sunday mornings and PR #149 was created only 35 minutes before the previous index expired.
   - Because PR #149 was not merged immediately, master pointed to an expired index, causing freshness checks (`_is_fresh()`) to fail.
   - Release-managed plugins (such as Somfy) paused release mutations and reported the expiration error.
2. **Release Mutation Mismatch (FullyKiosk):**
   - When a plugin is installed from GitHub directly (in `provider_live` mode), `.pypluginstore.json` is written with an explicit `source_revision` equal to the commit SHA.
   - However, when the weekly scanner indexes that release, it omits `source_revision` in `release_index.json` because for forge-based providers `source_revision` is identical to `commit`.
   - On client load, the manager compared the local explicit SHA with the empty string default, triggering a false `release_mutation` verification block.

Decision:
- Set `DEFAULT_VALIDITY_SECONDS` to 16 days (a bit over 2 weeks) to provide a comfortable grace period for weekly automated registry updates, preventing premature expiration even if the weekly PR merge is delayed.
- Normalize empty or omitted `source_revision` to `commit` for forge-based providers during verification inside `decide()`.

Implementation notes:
- Changed `DEFAULT_VALIDITY_SECONDS` to `16 * 24 * 60 * 60` in `.github/scripts/generate_release_index.py`.
- Updated verification comparison in `plugin_core.py` (and regenerated `plugin.py` via `.github/scripts/generate_generate.py`).
- Added regression test `test_equal_revision_accepts_omitted_vs_explicit_commit_source_revision` in `tests/test_release_management.py`.

Verification:
- Local test suite passed (1558 passed).
