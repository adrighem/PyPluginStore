# ISSUE:87 - Feature Idea: Theme Management

Status: open; design/backlog.

Author:
- `adrighem` opened the issue on 2026-07-04 after splitting the theme-management idea from `ISSUE:30`.

Intent:
- Explore managing Domoticz themes from PyPluginStore.
- Define theme packaging, discovery, installation, update, and UI behavior.

Assessment:
- The existing issue comment proposes a direct plugin-management parallel, but deeper local research in `.github/maintainer/work/issue-87-theme-architecture.md` shows themes need a separate catalog and install model.
- Key finding: modern Domoticz themes use `custom.css`, not `style.css`, and some theme repositories require copying a subdirectory rather than cloning directly into `www/styles`.
- Theme repositories can include JavaScript, so UI copy and trust boundaries should be explicit.
- This should remain a larger product backlog item while smaller release and styling work is handled.

Recommended next step:
- Keep open.
- When picked up, start with path helpers, theme key/path validation, protected built-in theme handling, and backend tests before building the UI tabs.

Public action:
- None taken.
