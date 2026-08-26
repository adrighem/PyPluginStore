# ISSUE:164 - Theme registry: consider preferring a build branch over a bare repo root

Status: closed/resolved at metadata layer

Reporter:
- `Rouzax` requested build branch adoption to isolate production code, avoid unflattened modular `@import` requests, and validate theme output before shipping.

Assessment:
- The concern regarding exposed development files and unflattened modular `@import` chains is valid for large/complex themes.
- However, `.git` directories are already explicitly excluded by `shutil.copytree` in the core runtime `plugin_core.py`.
- Other non-theme text files (like `package.json` or `webpack.config.js`) do not represent execution or leakage security risks for Domoticz static asset serving.
- Premature automated CSS parsing and bare-directory checking in `plugin_core.py` would add severe technical debt (regex ReDoS risks, path parsing complexity) to an already large codebase (~22k lines) without tangible performance benefits over local low-latency networks.
- Most importantly, the `themes.json` schema already fully supports custom `branch` and `source_path` properties, meaning theme build branches can be integrated directly today without any runtime core code changes.

Decision:
- Keep the runtime implementation in `plugin_core.py` lean. Reject any new core parsing, validation regexes, or UI blocking warning banners to preserve a lightweight developer experience for basic/hobbyist theme authors.
- Address the request completely at the registry database and documentation layer by documenting the theme compilation workflows and metadata fields.
- Document three primary supported developer workflows (Minimal Hobbyist, Subfolder Compiler, Clean Release Branch) in `CONTRIBUTING.md`.
- Ensure incoming pull requests for new themes are manually reviewed for layout correctness.
- Implement CI validation checks in `.github/scripts/validate_plugins.py` to assert that `themes.json` remains schema-valid, structurally safe, and free from broken repository or branch links on check-in.

Verification:
- Added `themes.json` offline schema validation and git remote existence checks in `.github/scripts/validate_plugins.py` under the automated CI workflow.
- Updated `CONTRIBUTING.md` to document Theme Submission Guidelines and the different supported developer workflows.
- Ran entire test suite successfully (all 1,563 tests passed).
