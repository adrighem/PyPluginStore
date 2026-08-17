# ISSUE:150 - Release - release metadata unavailable: Release metadata is expired; release changes are paused.

Status: closed; resolved in master.

Reporter:
- `MadPatrick` opened the issue on 2026-08-17 reporting that they see "Release metadata is expired; release changes are paused." on most of their installed plugins.
- `Eddie-BS` confirmed seeing it, but only on the Somfy plugin.

Assessment:
- The previous 7-day validity seconds for the weekly-updated release index left no margin for human delay in reviewing and merging automated PRs.
- The cron job runs weekly on Sunday mornings and PR #149 was created only 35 minutes before the previous index expired.
- Because PR #149 was not merged immediately, master pointed to an expired index, causing freshness checks (`_is_fresh()`) to fail.
- Release-managed plugins (such as Somfy) paused release mutations and reported the expiration error, while Git-managed plugins remained unaffected (which explains why `Eddie-BS` only saw the error on Somfy).

Decision:
- Set `DEFAULT_VALIDITY_SECONDS` to 16 days (a bit over 2 weeks) to provide a comfortable grace period for weekly automated registry updates, preventing premature expiration even if the weekly PR merge is delayed.

Implementation notes:
- Changed `DEFAULT_VALIDITY_SECONDS` to `16 * 24 * 60 * 60` in `.github/scripts/generate_release_index.py`.

Verification:
- Local test suite passed (1557 passed).
- Pushed commit `9ff35e5` with `fixes #150` to `master` branch.
- Commented on issue #150 on GitHub.
