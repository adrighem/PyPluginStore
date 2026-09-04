import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPO_ROOT = Path(__file__).resolve().parents[1]


def package_record(
    package_id="ExamplePlugin",
    domoticz_key="EXAMPLE",
    repository_url="https://gitlab.com/example-group/example-plugin",
):
    provider = (
        "github"
        if repository_url.startswith("https://github.com/")
        else "gitlab"
    )
    return {
        "package_id": package_id,
        "domoticz_key": domoticz_key,
        "description": "Example plugin",
        "repository": {
            "url": repository_url,
            "branch": "main",
        },
        "platforms": ["linux", "windows"],
        "delivery": {
            "preferred": "release_if_indexed",
            "git_supported": True,
            "release": {
                "provider": provider,
                "channel": "stable",
                "tag_pattern": r"^v?[0-9]+(?:\.[0-9]+){1,3}$",
                "artifact": "source_zip",
                "source_path": ".",
                "mutable_paths": [],
            },
        },
    }


def registry_document(packages=None):
    return {
        "schema_version": 2,
        "packages": list(
            [package_record()] if packages is None else packages
        ),
    }


def json_bytes(document):
    return (json.dumps(document, sort_keys=True) + "\n").encode("utf-8")


def test_plugin_core_loads_sibling_registry_outside_sys_path(tmp_path):
    plugin_core_path = tmp_path / "plugin_core.py"
    shutil.copy2(REPO_ROOT / "plugin_core.py", plugin_core_path)
    shutil.copy2(REPO_ROOT / "package_registry.py", tmp_path / "package_registry.py")
    shutil.copy2(REPO_ROOT / "package_identity.py", tmp_path / "package_identity.py")
    shutil.copy2(REPO_ROOT / "release_providers.py", tmp_path / "release_providers.py")
    shutil.copy2(REPO_ROOT / "release_domain.py", tmp_path / "release_domain.py")
    script = """
import importlib.util
import sys
import types

domoticz = types.ModuleType("Domoticz")
for name in ("Debug", "Log", "Error", "SendNotification", "Debugging", "Heartbeat"):
    setattr(domoticz, name, lambda *args, **kwargs: None)
sys.modules["Domoticz"] = domoticz
specification = importlib.util.spec_from_file_location("isolated_plugin_core", sys.argv[1])
module = importlib.util.module_from_spec(specification)
specification.loader.exec_module(module)
assert module.PackageRegistry.__module__ == "package_registry"
assert module.UpdateTimesDocument.__module__ == "package_registry"
assert module.certify_plugin_py.__module__ == "package_identity"
"""

    subprocess.run(
        [sys.executable, "-I", "-c", script, str(plugin_core_path)],
        cwd=tmp_path.parent,
        check=True,
        capture_output=True,
        text=True,
    )


def test_release_metadata_state_schema_remains_compatible(
    plugin_core_module, tmp_path
):
    metadata_root = tmp_path / "metadata"
    metadata_root.mkdir()
    watermark_path = metadata_root / "trust-state.json"
    watermark_path.write_text(
        json.dumps({"schema_version": 1, "highest_sequence": 41}),
        encoding="utf-8",
    )
    store = plugin_core_module.ReleaseMetadataStore(str(metadata_root))

    assert store._read_state_sequence(
        str(watermark_path), "highest_sequence"
    ) == ("valid", 41)

    store._write_pointer(41)
    pointer = json.loads(
        (metadata_root / "current.json").read_text(encoding="utf-8")
    )
    assert pointer == {"schema_version": 1, "sequence": 41}


def release_record(package_id="ExamplePlugin"):
    return {
        "package_id": package_id,
        "certified_identity": {
            "domoticz_key": "EXAMPLE",
            "plugin_py_sha256": "2" * 64,
        },
        "revision": 1,
        "release_id": "gitlab:example-group/example-plugin:v1.0.0",
        "supersedes": [],
        "provider": "gitlab",
        "repository_identity": "gitlab.com/example-group/example-plugin",
        "version": "1.0.0",
        "tag": "v1.0.0",
        "released_at": "2026-07-20T18:00:00Z",
        "commit": COMMIT,
        "artifact": {
            "kind": "source_zip",
            "provenance": "forge_source_archive",
            "migration": {
                "mode": "automatic",
                "evidence": "commit_source_archive",
            },
            "url": (
                "https://gitlab.com/example-group/example-plugin/"
                "-/archive/" + COMMIT + "/example-plugin.zip"
            ),
            "sha256": "0" * 64,
            "size": 12345,
            "tree_sha256": "1" * 64,
            "root_prefix": "example-plugin-" + COMMIT,
            "source_path": ".",
        },
    }


def tombstone_record(package_id="RetiredPlugin"):
    return {
        "package_id": package_id,
        "repository_identity": "github.com/example/retired-plugin",
        "last_revision": 3,
        "release_id": "github:example/retired-plugin:v0.9.0",
        "reason": "Release packaging is no longer maintained.",
        "removed_at": "2026-07-21T08:00:00Z",
    }


def release_index_document(registry_bytes, releases=None, tombstones=None):
    return {
        "schema_version": 2,
        "sequence": 43,
        "generated_at": "2026-07-21T09:00:00Z",
        "expires_at": "2026-07-28T09:00:00Z",
        "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
        "releases": list(
            [release_record()] if releases is None else releases
        ),
        "tombstones": list(tombstones or []),
    }


def test_registry_v2_parses_explicit_package_record(plugin_core_module):
    registry = plugin_core_module.PackageRegistry.from_document(
        registry_document()
    )

    assert registry.schema_version == 2
    assert list(registry.by_package_id) == ["ExamplePlugin"]
    package = registry.by_package_id["ExamplePlugin"]
    assert package.package_id == "ExamplePlugin"
    assert package.domoticz_key == "EXAMPLE"
    assert package.repository_url == (
        "https://gitlab.com/example-group/example-plugin"
    )
    assert package.repository_identity == (
        "gitlab.com/example-group/example-plugin"
    )


def test_update_times_v2_uses_explicit_package_records(plugin_core_module):
    update_times = plugin_core_module.UpdateTimesDocument.from_document(
        {
            "schema_version": 2,
            "updates": [
                {
                    "package_id": "ExamplePlugin",
                    "updated_at": "2026-07-21T12:00:00Z",
                }
            ],
        }
    )

    assert update_times.by_package_id == {
        "ExamplePlugin": "2026-07-21T12:00:00Z"
    }
    assert update_times.to_document() == {
        "schema_version": 2,
        "updates": [
            {
                "package_id": "ExamplePlugin",
                "updated_at": "2026-07-21T12:00:00Z",
            }
        ],
    }


def test_update_times_v2_rejects_legacy_or_ambiguous_records(
    plugin_core_module,
):
    with pytest.raises(ValueError):
        plugin_core_module.UpdateTimesDocument.from_document(
            {"ExamplePlugin": "2026-07-21T12:00:00Z"}
        )

    with pytest.raises(ValueError, match="duplicate package_id"):
        plugin_core_module.UpdateTimesDocument.from_document(
            {
                "schema_version": 2,
                "updates": [
                    {
                        "package_id": "ExamplePlugin",
                        "updated_at": "2026-07-21T12:00:00Z",
                    },
                    {
                        "package_id": "exampleplugin",
                        "updated_at": "2026-07-21T12:00:00Z",
                    },
                ],
            }
        )

    with pytest.raises(ValueError, match="canonical UTC"):
        plugin_core_module.UpdateTimesDocument.from_document(
            {
                "schema_version": 2,
                "updates": [
                    {
                        "package_id": "ExamplePlugin",
                        "updated_at": "2026-07-21T12:00:00+00:00",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "package_ids",
    [
        ["DuplicatePlugin", "DuplicatePlugin"],
        ["CaseSensitivePlugin", "casesensitiveplugin"],
    ],
)
def test_registry_v2_rejects_duplicate_package_ids(
    plugin_core_module,
    package_ids,
):
    packages = [
        package_record(
            package_id=package_id,
            domoticz_key="KEY" + str(index),
            repository_url="https://github.com/example/repo-" + str(index),
        )
        for index, package_id in enumerate(package_ids)
    ]

    with pytest.raises(ValueError, match="(?i)duplicate.*package"):
        plugin_core_module.PackageRegistry.from_document(
            registry_document(packages)
        )


@pytest.mark.parametrize("unknown_location", ["document", "package"])
def test_registry_v2_rejects_unknown_fields(
    plugin_core_module,
    unknown_location,
):
    document = registry_document()
    if unknown_location == "document":
        document["legacy_packages"] = {}
    else:
        document["packages"][0]["plugin_key"] = "legacy-name"

    with pytest.raises(ValueError, match="(?i)unknown|unexpected"):
        plugin_core_module.PackageRegistry.from_document(document)


def test_release_index_v2_parses_release_and_tombstone_arrays(
    plugin_core_module,
):
    registry_bytes = json_bytes(
        registry_document(
            [
                package_record(),
                package_record(
                    package_id="RetiredPlugin",
                    domoticz_key="RETIRED",
                    repository_url=(
                        "https://github.com/example/retired-plugin"
                    ),
                ),
            ]
        )
    )
    index = plugin_core_module.ReleaseIndex.from_document(
        release_index_document(
            registry_bytes,
            tombstones=[tombstone_record()],
        ),
        registry_bytes=registry_bytes,
        now=NOW,
    )

    assert index.schema_version == 2
    release = index.releases["ExamplePlugin"]
    assert release.package_id == "ExamplePlugin"
    assert release.domoticz_key == "EXAMPLE"
    assert release.plugin_py_sha256 == "2" * 64
    assert index.tombstones["RetiredPlugin"].package_id == "RetiredPlugin"


def test_release_index_v2_rejects_unknown_active_package_id(
    plugin_core_module,
):
    registry_bytes = json_bytes(registry_document())

    with pytest.raises(ValueError, match="(?i)unknown.*package"):
        plugin_core_module.ReleaseIndex.from_document(
            release_index_document(
                registry_bytes,
                releases=[release_record(package_id="UnknownPlugin")],
            ),
            registry_bytes=registry_bytes,
            now=NOW,
        )


def test_release_index_v2_allows_tombstone_after_registry_removal(
    plugin_core_module,
):
    registry_bytes = json_bytes(registry_document(packages=[]))
    tombstone = tombstone_record(package_id="RetiredPlugin")

    index = plugin_core_module.ReleaseIndex.from_document(
        release_index_document(
            registry_bytes,
            releases=[],
            tombstones=[tombstone],
        ),
        registry_bytes=registry_bytes,
        now=NOW,
    )

    assert index.releases == {}
    assert index.tombstones["RetiredPlugin"].repository_identity == (
        tombstone["repository_identity"]
    )


def test_release_index_v2_requires_matching_tombstone_when_package_disappears(
    plugin_core_module,
):
    previous_registry_bytes = json_bytes(
        {
            "ExamplePlugin": [
                "example-group",
                "example-plugin",
                "Example plugin",
                "main",
            ]
        }
    )
    previous_release = release_record()
    previous_release.pop("package_id")
    previous_release.pop("certified_identity")
    previous_release["provider"] = "github"
    previous_release["repository_identity"] = (
        "github.com/example-group/example-plugin"
    )
    previous_release["release_id"] = (
        "github:example-group/example-plugin:v1.0.0"
    )
    previous_release["artifact"]["migration_eligible"] = True
    previous_release["artifact"].pop("migration")
    previous_document = {
        "schema_version": 1,
        "sequence": 42,
        "generated_at": "2026-07-20T09:00:00Z",
        "expires_at": "2026-07-27T09:00:00Z",
        "registry_sha256": hashlib.sha256(
            previous_registry_bytes
        ).hexdigest(),
        "plugins": {"ExamplePlugin": previous_release},
        "tombstones": {},
    }
    previous = plugin_core_module.ReleaseIndex.from_legacy_document(
        previous_document,
        registry_bytes=previous_registry_bytes,
        now=NOW,
    )
    current_registry_bytes = json_bytes(registry_document(packages=[]))
    current_tombstone = {
        "package_id": "ExamplePlugin",
        "repository_identity": "github.com/example-group/example-plugin",
        "last_revision": 1,
        "release_id": "github:example-group/example-plugin:v1.0.0",
        "reason": "Package no longer has a valid Domoticz plugin identity.",
        "removed_at": "2026-07-21T08:00:00Z",
    }

    current = plugin_core_module.ReleaseIndex.from_document(
        release_index_document(
            current_registry_bytes,
            releases=[],
            tombstones=[current_tombstone],
        ),
        registry_bytes=current_registry_bytes,
        now=NOW,
        previous=previous,
    )

    assert current.releases == {}
    assert current.tombstones["ExamplePlugin"].last_revision == 1


TOMBSTONED_RELEASE_ID = "gitlab:example-group/example-plugin:v1.0.0"
REACTIVATED_RELEASE_ID = "gitlab:example-group/example-plugin:v1.1.0"
REACTIVATED_COMMIT = "89abcdef0123456789abcdef0123456789abcdef"


def previous_tombstoned_release_index(plugin_core_module):
    previous_registry_bytes = json_bytes(registry_document(packages=[]))
    previous_document = release_index_document(
        previous_registry_bytes,
        releases=[],
        tombstones=[
            {
                "package_id": "ExamplePlugin",
                "repository_identity": (
                    "gitlab.com/example-group/example-plugin"
                ),
                "last_revision": 3,
                "release_id": TOMBSTONED_RELEASE_ID,
                "reason": "Package was removed.",
                "removed_at": "2026-07-20T08:00:00Z",
            }
        ],
    )
    previous_document["sequence"] = 42
    return plugin_core_module.ReleaseIndex.from_document(
        previous_document,
        registry_bytes=previous_registry_bytes,
        now=NOW,
    )


def reactivated_release():
    release = release_record()
    release.update(
        revision=4,
        release_id=REACTIVATED_RELEASE_ID,
        supersedes=[TOMBSTONED_RELEASE_ID],
        version="1.1.0",
        tag="v1.1.0",
        released_at="2026-07-21T08:30:00Z",
        commit=REACTIVATED_COMMIT,
    )
    release["artifact"].update(
        url=(
            "https://gitlab.com/example-group/example-plugin/"
            "-/archive/"
            + REACTIVATED_COMMIT
            + "/example-plugin.zip"
        ),
        sha256="3" * 64,
        tree_sha256="4" * 64,
        root_prefix="example-plugin-" + REACTIVATED_COMMIT,
    )
    return release


def parse_reactivated_release(
    plugin_core_module,
    release,
    repository_url="https://gitlab.com/example-group/example-plugin",
):
    current_registry_bytes = json_bytes(
        registry_document(
            [package_record(repository_url=repository_url)]
        )
    )
    return plugin_core_module.ReleaseIndex.from_document(
        release_index_document(
            current_registry_bytes,
            releases=[release],
        ),
        registry_bytes=current_registry_bytes,
        now=NOW,
        previous=previous_tombstoned_release_index(plugin_core_module),
    )


def test_release_index_v2_allows_safe_tombstone_reactivation(
    plugin_core_module,
):
    current = parse_reactivated_release(
        plugin_core_module,
        reactivated_release(),
    )

    release = current.releases["ExamplePlugin"]
    assert release.revision == 4
    assert release.release_id == REACTIVATED_RELEASE_ID
    assert release.supersedes == [TOMBSTONED_RELEASE_ID]
    assert release.domoticz_key == "EXAMPLE"
    assert release.plugin_py_sha256 == "2" * 64
    assert current.tombstones == {}


@pytest.mark.parametrize(
    "case",
    [
        "same-release-id",
        "missing-predecessor",
        "repository-mismatch",
        "non-incremented-revision",
    ],
)
def test_release_index_v2_rejects_unsafe_tombstone_reactivation(
    plugin_core_module,
    case,
):
    release = reactivated_release()
    repository_url = "https://gitlab.com/example-group/example-plugin"
    if case == "same-release-id":
        release["release_id"] = TOMBSTONED_RELEASE_ID
        release["supersedes"] = []
    elif case == "missing-predecessor":
        release["supersedes"] = []
    elif case == "repository-mismatch":
        repository_url = "https://gitlab.com/other-group/example-plugin"
        release["repository_identity"] = (
            "gitlab.com/other-group/example-plugin"
        )
        release["release_id"] = (
            "gitlab:other-group/example-plugin:v1.1.0"
        )
        release["artifact"]["url"] = (
            "https://gitlab.com/other-group/example-plugin/"
            "-/archive/"
            + REACTIVATED_COMMIT
            + "/example-plugin.zip"
        )
    elif case == "non-incremented-revision":
        release["revision"] = 3

    with pytest.raises(ValueError):
        parse_reactivated_release(
            plugin_core_module,
            release,
            repository_url=repository_url,
        )


def test_v1_documents_require_explicit_normalization_boundary(
    plugin_core_module,
):
    legacy_registry = {
        "ExamplePlugin": [
            "example-group",
            "example-plugin",
            "Example plugin",
            "main",
        ]
    }
    legacy_registry_bytes = json_bytes(legacy_registry)
    legacy_release = release_record()
    for field in ("package_id", "certified_identity"):
        legacy_release.pop(field)
    legacy_release["artifact"]["migration_eligible"] = True
    legacy_release["artifact"].pop("migration")
    legacy_release.update(
        {
            "provider": "github",
            "repository_identity": "github.com/example-group/example-plugin",
            "release_id": "github:example-group/example-plugin:v1.0.0",
        }
    )
    legacy_index = {
        "schema_version": 1,
        "sequence": 42,
        "generated_at": "2026-07-20T09:00:00Z",
        "expires_at": "2026-07-27T09:00:00Z",
        "registry_sha256": hashlib.sha256(
            legacy_registry_bytes
        ).hexdigest(),
        "plugins": {"ExamplePlugin": legacy_release},
        "tombstones": {},
    }

    with pytest.raises(ValueError):
        plugin_core_module.PackageRegistry.from_document(legacy_registry)
    registry = plugin_core_module.PackageRegistry.from_legacy_document(
        legacy_registry
    )
    assert registry.by_package_id["ExamplePlugin"].package_id == "ExamplePlugin"
    assert registry.by_package_id["ExamplePlugin"].repository_url == (
        "https://github.com/example-group/example-plugin"
    )

    with pytest.raises(ValueError):
        plugin_core_module.ReleaseIndex.from_document(
            legacy_index,
            registry_bytes=legacy_registry_bytes,
            now=NOW,
        )
    index = plugin_core_module.ReleaseIndex.from_legacy_document(
        legacy_index,
        registry_bytes=legacy_registry_bytes,
        now=NOW,
    )
    assert index.releases["ExamplePlugin"].package_id == "ExamplePlugin"


def test_package_record_requires_python_support(plugin_core_module):
    import package_registry

    raw = package_record(
        package_id="ModernPlugin",
        domoticz_key="ModernKey",
    )
    raw["requires_python"] = ">=3.8"
    record = package_registry.PackageRecord.from_document(raw)
    assert record.requires_python == ">=3.8"
    doc = record.to_document()
    assert doc["requires_python"] == ">=3.8"

    # From annotations fallback
    raw2 = package_record(
        package_id="AnnotatedPlugin",
        domoticz_key="AnnotatedKey",
    )
    raw2["annotations"] = {"requires_python": ">=3.10"}
    record2 = package_registry.PackageRecord.from_document(raw2)
    assert record2.requires_python == ">=3.10"


@pytest.mark.parametrize(
    ("specifier", "version", "expected"),
    [
        (">=3.8", (3, 7, 3), False),
        (">=3.8", (3, 8, 0), True),
        (">=3.8", (3, 11, 2), True),
        (">=3.8, <4.0", (3, 11, 2), True),
        (">=3.8, <4.0", (4, 0, 0), False),
        ("~=3.8.0", (3, 8, 5), True),
        ("~=3.8.0", (3, 9, 0), False),
        ("==3.8.*", (3, 8, 2), True),
        ("==3.8.*", (3, 9, 0), False),
        ("<3.12", (3, 14, 0), False),
        ("<3.12", (3, 11, 0), True),
        ("!=3.9.0", (3, 9, 0), False),
        ("!=3.9.0", (3, 9, 1), True),
        ("", (3, 7, 3), True),
    ],
)
def test_is_python_version_compatible(
    plugin_core_module,
    specifier,
    version,
    expected,
):
    func = getattr(
        plugin_core_module,
        "is_python_version_compatible",
        None,
    )
    if func is None:
        import package_registry

        func = package_registry.is_python_version_compatible
    assert func(specifier, version) == expected
