# ISSUE:151 - installing Domoticz-Indevolt-plugin failes

Status: diagnosed; workaround provided; architectural improvements planned.

Reporter:
- `Eddie-BS` opened the issue on 2026-08-17 reporting that installing the Domoticz-Indevolt-plugin failed while installing requirements.
- They noted that manually running `python3 -m pip install -r requirements.txt` on their system Python worked.
- On 2026-09-03, `Eddie-BS` commented that updating the plugin on Domoticz 2025.1 failed with exit code 1 resolving 3 requirement files, while it succeeded on Domoticz 2026.x.
- On 2026-09-04, `Eddie-BS` confirmed the environment is running Python 3.7.3 and provided manual test output showing `solaredge_modbus==0.8.0` from `domoticz-solaredge-modbustcp-plugin` fails because it requires Python >= 3.8.

Assessment:
1. **PEP 668 Restrictions (Initial Issue):**
   - PyPluginStore manages shared plugin dependencies inside an isolated `.shared_deps` generation to prevent multi-plugin library version conflicts.
   - During installation, PyPluginStore invokes `pip install --target` with the system's python interpreter (`sys.executable`).
   - On modern Linux environments (such as Debian Bookworm or Raspberry Pi OS Bookworm) that implement PEP 668 (externally-managed-environment), system Python packages are locked down. Direct invocations of `pip install` on the system Python interpreter are blocked and fail with `error: externally-managed-environment`, even when utilizing `--target` to write to a localized directory.
   - Bypassing the PEP 668 block is safe because PyPluginStore installs packages in a strictly isolated local `.shared_deps` target and does not mutate the system's global site-packages.
2. **Sibling Dependency Failure on Legacy Python 3.7 (Reopened):**
   - `ReleaseDependencySnapshotService` bundles all installed plugins' `requirements.txt` into `.shared_deps`.
   - `domoticz-solaredge-modbustcp-plugin` requires `solaredge_modbus==0.8.0`, which dropped support for Python < 3.8.
   - On Python 3.7.3, pip failed to resolve `solaredge_modbus==0.8.0`, blocking the entire transaction for `Domoticz-Indevolt-plugin`.
   - Because `classify_release_dependency_failure()` did not match version-pinned pip output (`==0.8.0`), it returned `unknown`, masking the offending package and sibling plugin.

Decision:
- Explained root cause and provided workaround to reporter on GitHub issue #151.
- Planned architectural improvements:
  1. Refine regex in `classify_release_dependency_failure` and `release_dependency_failure_packages` to attribute failures to specific sibling plugins and unmask pinned packages.
  2. Implement two-phase dependency resolution with sibling fault pruning so failing sibling requirements do not block healthy plugin updates.

Implementation notes:
- Modified `_ReleaseDependencyCommandRunner.available` in `plugin_core.py` to include `"PIP_BREAK_SYSTEM_PACKAGES": "1"` in the default fallback environment.
- Modified `_installer_environment` in `plugin_core.py` to add `"PIP_BREAK_SYSTEM_PACKAGES": "1"` to the constructed transaction environment.
- Updated unit test assertions in `tests/test_release_dependencies.py` (`test_pip_and_uv_always_target_staging_never_live` and `test_pip_discovery_runs_the_domoticz_python_with_sanitized_environment`) to expect `"PIP_BREAK_SYSTEM_PACKAGES"` in the sanitized environment.
- Regenerated `plugin.py` from `plugin_core.py` using `.github/scripts/generate_plugin.py`.

Verification:
- Local test suite fully passed.
- Checked for trailing spaces and formatting issues with `git diff --check` (clean).
