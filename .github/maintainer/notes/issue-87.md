# ISSUE:87 - Implement Parallel Theme Store

Status: closed

Reporter:
- `basvdijk` originally requested to have theme support.

Assessment:
- We need to be able to install and update Domoticz frontend themes. Themes need to be hosted in `www/styles/<theme_name>` but we do not want `.git` tracking files or configuration folders littering that directory, and we don't want half-cloned directories blocking the Domoticz UI.
- Domoticz themes can execute dynamic JavaScript via `custom.js` files, meaning themes are essentially executable code in the user's session.

Decision:
- Implement a Staging-and-Mirror Architecture. We clone into `.theme_sources/<theme_key>` and then defensively mirror only the validated `source_path` contents to `www/styles/`.
- Ensure directory traversal is strictly guarded in all path helper resolutions (`resolve_theme_dir`).
- Track theme installations using a `.pypluginstore-theme.json` footprint marker file.
- Scan for `.js` files dynamically inside the theme payload and flag them visually to the user in the UI, marking it as executable code.
- Implement independent UI Tabs in `pypluginstore.html` utilizing a shared catalog pattern.

Implementation Details:
- **`plugin_core.py`**: Added `ThemeRegistryEntry`, `ThemeRegistryService`, and `ThemeDiscoveryService`. Registered `list_themes`, `install_theme`, `remove_theme`, `update_theme` endpoints.
- **`themes.json`**: Created the seed catalog registry with `nightglass` and `osi-dark`.
- **`pypluginstore.html`**: Added `loadThemes`, `filterAndRenderThemes`, `renderThemes`, and the `currentTab` toggling logic. Applied Vincent's CSS custom variables.
- **`tests/test_theme_management.py`**: Thoroughly test safe path handling, discovery, and isolated operations.

Verification:
- The Python backend tests pass (1562 total).
- The Javascript layout mock tests pass.
- Path security features block directory traversal out of `www/styles/`.