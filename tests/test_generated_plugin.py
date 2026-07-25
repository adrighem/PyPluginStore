import re
import subprocess
import sys

from conftest import REPO_ROOT


SELF_UPDATE_CANDIDATE_PYTHON_FILES = (
    "plugin.py",
    "plugin_core.py",
    "package_registry.py",
    "package_identity.py",
    "release_providers.py",
)


def test_generated_plugin_py_is_current():
    plugin_file = REPO_ROOT / "plugin.py"
    original = plugin_file.read_bytes()

    try:
        result = subprocess.run(
            [sys.executable, ".github/scripts/generate_plugin.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        generated = plugin_file.read_bytes()
        assert result.returncode == 0, result.stderr
        assert generated == original, (
            "plugin.py is stale; run python .github/scripts/generate_plugin.py "
            "and commit the generated file. If this still fails, check that "
            "plugin.py uses LF line endings."
        )
    finally:
        if plugin_file.read_bytes() != original:
            plugin_file.write_bytes(original)


def test_generated_plugin_uses_one_release_version_source():
    generator = (
        REPO_ROOT / ".github" / "scripts" / "generate_plugin.py"
    ).read_text(encoding="utf-8")
    generated = (REPO_ROOT / "plugin.py").read_text(encoding="utf-8")

    generator_version = re.search(
        r'^PLUGIN_VERSION = "([^"]+)"',
        generator,
        flags=re.MULTILINE,
    ).group(1)
    runtime_version = re.search(
        r'^PYPLUGINSTORE_VERSION = "([^"]+)"',
        generated,
        flags=re.MULTILINE,
    ).group(1)
    metadata_version = re.search(
        r'<plugin\b[^>]*\bversion="([^"]+)"',
        generated,
    ).group(1)

    assert generator.count("x-release-please-version") == 1
    assert generated.count("x-release-please-version") == 2
    assert generator_version == runtime_version == metadata_version


def test_self_update_candidate_python_sources_remain_ascii_compatible():
    for filename in SELF_UPDATE_CANDIDATE_PYTHON_FILES:
        contents = (REPO_ROOT / filename).read_bytes()
        try:
            contents.decode("ascii")
        except UnicodeDecodeError as error:
            raise AssertionError(
                filename
                + " must remain ASCII-decodable so legacy self-updaters can "
                "bootstrap; use Python Unicode escapes for non-ASCII text."
            ) from error
