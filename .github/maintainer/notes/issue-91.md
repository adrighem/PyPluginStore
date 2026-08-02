# ISSUE:91 - Make PyPluginStore follow Domoticz theme styling

Status: open; focused UX follow-up.

Author:
- `adrighem` opened the issue on 2026-07-06.

Intent:
- Make `pypluginstore.html` adapt to the active Domoticz theme.
- Preserve readability across light, dark, and custom themes.
- Keep fallback styling for themes that do not expose expected variables.

Assessment:
- This is concrete and user-visible, but it should not block the generated `v2.16.0` release unless theme support is unreadable in common Domoticz themes.
- A public comment already asks `MadPatrick` for Domoticz theme variable/class guidance.
- The safest implementation path is to derive a small set of local CSS custom properties from Domoticz/body theme styles, then keep existing PyPluginStore component classes pointed at those local tokens.

Recommended next step:
- After `PR:90` is released, inspect current Domoticz theme CSS and implement a focused `pypluginstore.html` styling pass with UI smoke tests.
- If contributor guidance arrives first, use it to avoid guessing at private theme conventions.

Public action:
- None taken.
