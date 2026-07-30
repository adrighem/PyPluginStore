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
    "release_domain.py",
)

DOCUMENTATION_FILES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "docs" / "registry_local.md",
    REPO_ROOT / "docs" / "release_management.md",
)


def markdown_heading_anchors(markdown):
    anchors = set()
    duplicates = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", markdown, re.MULTILINE):
        heading = re.sub(r"<[^>]+>", "", heading)
        heading = re.sub(r"[^\w\s-]", "", heading.casefold())
        anchor = re.sub(r"\s+", "-", heading.strip())
        duplicate_index = duplicates.get(anchor, 0)
        duplicates[anchor] = duplicate_index + 1
        if duplicate_index:
            anchor += "-" + str(duplicate_index)
        anchors.add(anchor)
    return anchors


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


def test_documentation_relative_links_and_anchors_are_valid():
    markdown_link = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for document in DOCUMENTATION_FILES:
        contents = document.read_text(encoding="utf-8")
        for destination in markdown_link.findall(contents):
            target, separator, anchor = destination.partition("#")
            if target.startswith(("http://", "https://", "mailto:")):
                continue

            target_file = document if not target else document.parent / target
            assert target_file.is_file(), (
                str(document.relative_to(REPO_ROOT))
                + " links to missing file "
                + destination
            )
            if separator and anchor:
                target_contents = target_file.read_text(encoding="utf-8")
                assert anchor in markdown_heading_anchors(target_contents), (
                    str(document.relative_to(REPO_ROOT))
                    + " links to missing anchor "
                    + destination
                )
