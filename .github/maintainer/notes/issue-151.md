# ISSUE:151 - installing Domoticz-Indevolt-plugin failes

Status: resolved in master.

Reporter:
- `Eddie-BS` opened the issue on 2026-08-17 reporting that installing the Domoticz-Indevolt-plugin failed while installing requirements.
- They noted that manually running `python3 -m pip install -r requirements.txt` on their system Python worked.

Assessment:
1. **PEP 668 Restrictions:**
   - PyPluginStore manages shared plugin dependencies inside an isolated `.shared_deps` generation to prevent multi-plugin library version conflicts.
   - During installation, PyPluginStore invokes `pip install --target` with the system's python interpreter (`sys.executable`).
   - On modern Linux environments (such as Debian Bookworm or Raspberry Pi OS Bookworm) that implement PEP 668 (externally-managed-environment), system Python packages are locked down. Direct invocations of `pip install` on the system Python interpreter are blocked and fail with `error: externally-managed-environment`, even when utilizing `--target` to write to a localized directory.
   - Because the `domoticz` system service user lacks user-level pip bypass overrides (such as in `~/.config/pip/pip.conf`), PyPluginStore's dependency resolution attempts failed completely, triggering browser alerts.
   - Bypassing the PEP 668 block is perfectly safe in this context, because PyPluginStore installs packages in a strictly isolated local `.shared_deps` target and does not mutate the system's global site-packages.

Decision:
- Explicitly pass `"PIP_BREAK_SYSTEM_PACKAGES": "1"` inside the environment dictionary when validating or running `pip` to bypass PEP 668 and allow isolated `--target` installations.

Implementation notes:
- Modified `_ReleaseDependencyCommandRunner.available` in `plugin_core.py` to include `"PIP_BREAK_SYSTEM_PACKAGES": "1"` in the default fallback environment.
- Modified `_installer_environment` in `plugin_core.py` to add `"PIP_BREAK_SYSTEM_PACKAGES": "1"` to the constructed transaction environment.
- Updated unit test assertions in `tests/test_release_dependencies.py` (`test_pip_and_uv_always_target_staging_never_live` and `test_pip_discovery_runs_the_domoticz_python_with_sanitized_environment`) to expect `"PIP_BREAK_SYSTEM_PACKAGES"` in the sanitized environment.
- Regenerated `plugin.py` from `plugin_core.py` using `.github/scripts/generate_plugin.py`.

Verification:
- Local test suite fully passed (1558 passed).
- Checked for trailing spaces and formatting issues with `git diff --check` (clean).
