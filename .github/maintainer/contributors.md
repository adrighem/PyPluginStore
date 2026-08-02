# Contributor Notes

## MadPatrick

- Has contributed UI polish, default plugin exceptions, update-time behavior, and local/private registry ideas.
- Often provides screenshots and concrete Domoticz user workflow context.
- May need clear guidance around generated `plugin.py`, branch focus, and avoiding runtime/local files in PR diffs.
- Reported the `v2.11.0` installed-detection regression in `ISSUE:46` with before/after screenshots from `v2.9.1` and `v2.11.0`.
- Clarified after `ISSUE:46` that local registry entries should override public entries when repository aliases collide.
- Reported the release-switch transaction failures in `ISSUE:111` and identified
  a public/local registry overlap while validating the released recovery.
- Reported the repeated channel notifications, stale indexed Release target,
  and missing latest-version display in `ISSUE:122`.

## mvveelen

- Reported concrete installed-plugin and UI bridge issues in `ISSUE:52` and `ISSUE:53`.
- Useful reports include exact repository URLs and screenshots.

## Eddie-BS

- Reported Docker/icon, missing NUT UPS, and startup update-check log issues in `ISSUE:54`, `ISSUE:55`, and `ISSUE:56`.
- Reported Domoticz 2025.1 compatibility and UI update-status behavior in `ISSUE:57`, plus the custom UI API payload length log in `ISSUE:60`.
- Reported Docker volume permissions conflict on restart in `ISSUE:70`.
- Reported the missing Local registry action after upgrading to `v2.21.0` in
  `ISSUE:117`; the screenshot usefully exposed an older deployed custom page.
- Reproduced the `ISSUE:122` Somfy Git-to-Release transaction failure and the
  apparent return to Git after a Domoticz restart.
- Confirmed the v2.24.2 fix in the field and exposed that the generic
  `Rollback` label did not identify the retained Git restore target.
- Provides concise issue reports with screenshots or log excerpts.

## Rouzax

- Author of `PR:66` (Windows platform metadata for Luxtronik) and `PR:71` (lean dist branch and key alignment to `luxtronikex`).
- Highly structured and clear contributions regarding Luxtronik plugin maintenance.
