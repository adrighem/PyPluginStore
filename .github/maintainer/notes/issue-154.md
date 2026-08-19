# ISSUE:154 - False positive update button for manually installed Git plugins when versions match

Status: resolved in master.

Reporter:
- Vincent van Adrighem opened the issue on 2026-08-19.

Assessment:
- For Git-managed plugins, PyPluginStore determines update availability by evaluating if the local clone is behind the remote branch (using git ahead/behind count).
- Often, open-source developers push bug fixes and commits without incrementing the semantic version inside their `<plugin version="...">` metadata.
- This results in a confusing situation where both the local and remote plugin declare the exact same version (e.g. `v2.4.3`), but there are available commits to pull. PyPluginStore presented `Installed: v2.4.3 | Available: v2.4.3` with the status `Git - update available`.
- Stripping or hiding the update button when versions match is dangerous because it would block users from pulling these critical intermediate hotfixes.

Decision:
- Implement Option C: Revision-Aware UI States.
- If there are remote commits available but the semantic versions are identical, update the UI status label to read `Git - new commits available` (instead of `Git - update available`), and set the Update button's hover tooltip to `Update to latest commits`.

Implementation notes:
- Updated `formatReleaseManagementStatus` in `pypluginstore.html` to push `'new commits available'` if `state.status === 'git_available'` and `state.installed_version === state.available_version`.
- Updated `updateTitle` button calculation in `pypluginstore.html` to output `'Update to latest commits'` when `hasNewCommitsOnly` is true.

Verification:
- Pushed and successfully deployed to live `pietje` testing environment over IPv6 link.
- Verified that the card layout accurately reflects the commit-behind state without version mismatches.
