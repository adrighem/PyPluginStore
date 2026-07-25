# Tech Stack

- Runtime plugin: Python, generated from `plugin_core.py` into `plugin.py`.
- UI: static HTML, CSS, and JavaScript in `pypluginstore.html`.
- Registry: `registry.json` plus optional local overrides.
- Planned release delivery: a generated `release_index.json` containing normalized, checksum-pinned release snapshots, with `registry.json` retaining curated source and policy metadata.
- Automation: GitHub Actions workflows in `.github/workflows/`.
- Registry and release scanners plus validation scripts: Python scripts in `.github/scripts/`, with host adapters for GitHub, GitLab, Forgejo/Codeberg, Gitea, and generic manifests.
- Tests: `pytest` test suite under `tests/`.

Release archives will initially use ZIP only so one hardened, dependency-free
extractor can behave consistently on the supported Python versions and on both
Linux and Windows. Git remains part of the runtime for legacy and explicitly
Git-managed installations.

Release-managed plugins may also resolve stable releases on demand through
shared provider adapters. Runtime provider JSON and archive downloads use the
same pinned-DNS, bounded HTTPS and archive-validation primitives as indexed
release delivery. Locally certified targets carry explicit provider-live
authority in install metadata and never mutate the central release-index
watermark.

Development commands:

```bash
python .github/scripts/generate_plugin.py
pytest
```
