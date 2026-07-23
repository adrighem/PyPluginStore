import copy
import json

import pytest


ARTIFACT_SHA256 = "1" * 64
TREE_SHA256 = "2" * 64
PRESERVED_SHA256 = "3" * 64
PLUGIN_SHA256 = "4" * 64


def install_metadata_document(**overrides):
    document = {
        "schema": 2,
        "package_id": "ExamplePlugin",
        "management_mode": "release",
        "repository_identity": "github.com/owner/example-plugin",
        "version": "1.4.0",
        "tag": "v1.4.0",
        "release_id": "github:owner/example-plugin:v1.4.0",
        "release_revision": 7,
        "released_at": "2026-07-18T07:00:00Z",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "artifact_sha256": ARTIFACT_SHA256,
        "artifact_tree_sha256": TREE_SHA256,
        "artifact_provenance": "forge_source_archive",
        "artifact_files": {
            "plugin.py": {"sha256": PLUGIN_SHA256, "size": 4096},
        },
        "preserved_files": {
            "config/settings.json": PRESERVED_SHA256,
        },
        "index_sequence": 42,
        "installed_at": "2026-07-18T08:00:00Z",
    }
    document.update(overrides)
    return document


def legacy_install_metadata_document(**overrides):
    document = install_metadata_document()
    document["schema"] = 1
    document["plugin_key"] = document.pop("package_id")
    document.update(overrides)
    return document


def metadata_service(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()
    return plugin_core_module.InstallMetadataService(plugin)


def test_install_metadata_parses_release_identity_and_audit_hashes(
    plugin_core_module,
):
    document = install_metadata_document()

    metadata = plugin_core_module.InstallMetadata.from_document(document)

    assert metadata.schema == 2
    assert metadata.package_id == "ExamplePlugin"
    assert metadata.plugin_key == "ExamplePlugin"
    assert metadata.management_mode == "release"
    assert metadata.repository_identity == "github.com/owner/example-plugin"
    assert metadata.version == "1.4.0"
    assert metadata.tag == "v1.4.0"
    assert metadata.release_id == "github:owner/example-plugin:v1.4.0"
    assert metadata.release_revision == 7
    assert metadata.released_at == "2026-07-18T07:00:00Z"
    assert metadata.commit == "0123456789abcdef0123456789abcdef01234567"
    assert metadata.artifact_sha256 == ARTIFACT_SHA256
    assert metadata.artifact_tree_sha256 == TREE_SHA256
    assert metadata.artifact_provenance == "forge_source_archive"
    assert metadata.artifact_files == {
        "plugin.py": {"sha256": PLUGIN_SHA256, "size": 4096},
    }
    assert metadata.preserved_files == {
        "config/settings.json": PRESERVED_SHA256,
    }
    assert metadata.index_sequence == 42
    assert metadata.installed_at == "2026-07-18T08:00:00Z"
    assert metadata.to_document() == document


def test_install_metadata_v1_requires_explicit_normalization(
    plugin_core_module,
):
    legacy_document = legacy_install_metadata_document()

    with pytest.raises(ValueError):
        plugin_core_module.InstallMetadata.from_document(legacy_document)

    metadata = plugin_core_module.InstallMetadata.from_legacy_document(
        legacy_document
    )
    normalized = metadata.to_document()
    assert metadata.schema == 2
    assert metadata.package_id == "ExamplePlugin"
    assert normalized["schema"] == 2
    assert normalized["package_id"] == "ExamplePlugin"
    assert "plugin_key" not in normalized


@pytest.mark.parametrize("legacy_key_mode", ["legacy-only", "both"])
def test_install_metadata_v2_rejects_legacy_identity_key(
    plugin_core_module,
    legacy_key_mode,
):
    document = install_metadata_document()
    document["plugin_key"] = document["package_id"]
    if legacy_key_mode == "legacy-only":
        del document["package_id"]

    with pytest.raises(ValueError):
        plugin_core_module.InstallMetadata.from_document(document)


def test_install_metadata_supports_generic_immutable_source_revision(
    plugin_core_module,
):
    document = install_metadata_document(
        repository_identity="downloads.example.test/owner/example-plugin",
        release_id="generic:owner/example-plugin:v1.4.0",
        tag="",
        commit="",
        source_revision="release-2026-07-18-v1.4.0",
    )

    metadata = plugin_core_module.InstallMetadata.from_document(document)

    assert metadata.commit == ""
    assert metadata.tag == ""
    assert metadata.source_revision == "release-2026-07-18-v1.4.0"
    assert metadata.to_document() == document


def test_install_metadata_records_optional_git_migration_audit(plugin_core_module):
    document = install_metadata_document(
        migration_source_commit="9" * 40,
        migration_inventory_sha256="8" * 64,
    )

    metadata = plugin_core_module.InstallMetadata.from_document(document)

    assert metadata.migration_source_commit == "9" * 40
    assert metadata.migration_inventory_sha256 == "8" * 64
    assert metadata.to_document() == document


def invalid_install_metadata_documents():
    cases = []

    def add(case_id, **updates):
        cases.append(
            pytest.param(
                install_metadata_document(**updates),
                id=case_id,
            )
        )

    add("unsupported-schema", schema=1)
    add("unsafe-package-id", package_id="../ExamplePlugin")
    add("unsupported-management-mode", management_mode="git")
    add(
        "noncanonical-repository-identity",
        repository_identity="https://github.com/owner/example-plugin",
    )
    add("missing-release-identity", release_id="")
    add("invalid-release-revision", release_revision=0)
    add("mutable-commit", commit="main")
    add("invalid-artifact-hash", artifact_sha256="A" * 64)
    add("invalid-tree-hash", artifact_tree_sha256="short")
    add("invalid-provenance", artifact_provenance="mutable_branch")
    add(
        "unsafe-artifact-path",
        artifact_files={"../plugin.py": {"sha256": PLUGIN_SHA256, "size": 1}},
    )
    add(
        "invalid-artifact-file-hash",
        artifact_files={"plugin.py": {"sha256": "A" * 64, "size": 1}},
    )
    add(
        "invalid-artifact-file-size",
        artifact_files={"plugin.py": {"sha256": PLUGIN_SHA256, "size": True}},
    )
    add(
        "unsafe-preserved-path",
        preserved_files={"../settings.json": PRESERVED_SHA256},
    )
    add(
        "invalid-preserved-hash",
        preserved_files={"config/settings.json": "B" * 64},
    )
    add("invalid-index-sequence", index_sequence=0)
    add("invalid-release-timestamp", released_at="not-a-timestamp")
    add("invalid-install-timestamp", installed_at="2026-07-18")
    add(
        "invalid-migration-commit",
        migration_source_commit="main",
        migration_inventory_sha256="8" * 64,
    )
    add(
        "invalid-migration-inventory",
        migration_source_commit="9" * 40,
        migration_inventory_sha256="A" * 64,
    )
    add(
        "empty-migration-audit",
        migration_source_commit="",
        migration_inventory_sha256="",
    )

    return cases


@pytest.mark.parametrize("document", invalid_install_metadata_documents())
def test_install_metadata_rejects_invalid_or_unsafe_documents(
    plugin_core_module, document
):
    with pytest.raises(ValueError):
        plugin_core_module.InstallMetadata.from_document(copy.deepcopy(document))


def test_install_metadata_rejects_boolean_integer_fields(plugin_core_module):
    for field in ("release_revision", "index_sequence"):
        document = install_metadata_document(**{field: True})

        with pytest.raises(ValueError):
            plugin_core_module.InstallMetadata.from_document(document)


def test_install_metadata_requires_complete_migration_audit(plugin_core_module):
    for field, value in (
        ("migration_source_commit", "9" * 40),
        ("migration_inventory_sha256", "8" * 64),
    ):
        with pytest.raises(ValueError, match="migration"):
            plugin_core_module.InstallMetadata.from_document(
                install_metadata_document(**{field: value})
            )


def test_install_metadata_service_returns_none_when_metadata_is_absent(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    service = metadata_service(plugin_core_module)

    assert service.read(str(plugin_dir)) is None


def test_install_metadata_service_rejects_malformed_existing_metadata(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    metadata_file = plugin_dir / ".pypluginstore.json"
    metadata_file.write_text("{not-json", encoding="utf-8")
    service = metadata_service(plugin_core_module)

    with pytest.raises(ValueError):
        service.read(str(plugin_dir))

    assert metadata_file.read_text(encoding="utf-8") == "{not-json"


def test_install_metadata_service_atomically_upgrades_v1_on_read(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    metadata_file = plugin_dir / ".pypluginstore.json"
    metadata_file.write_text(
        json.dumps(legacy_install_metadata_document()), encoding="utf-8"
    )
    service = metadata_service(plugin_core_module)

    loaded = service.read(str(plugin_dir))

    upgraded = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert loaded.schema == 2
    assert loaded.package_id == "ExamplePlugin"
    assert upgraded == install_metadata_document()
    assert "plugin_key" not in upgraded
    assert metadata_file.read_bytes().endswith(b"\n")
    assert not (plugin_dir / ".pypluginstore.json.tmp").exists()


def test_install_metadata_inspection_does_not_upgrade_or_clean_up(
    plugin_core_module,
    tmp_path,
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    metadata_file = plugin_dir / ".pypluginstore.json"
    temporary_file = plugin_dir / ".pypluginstore.json.tmp"
    metadata_file.write_text(
        json.dumps(legacy_install_metadata_document()),
        encoding="utf-8",
    )
    temporary_file.write_text(
        json.dumps(install_metadata_document(version="next")),
        encoding="utf-8",
    )
    original_metadata = metadata_file.read_bytes()
    original_temporary = temporary_file.read_bytes()
    service = metadata_service(plugin_core_module)

    loaded = service.inspect(str(plugin_dir))

    assert loaded.package_id == "ExamplePlugin"
    assert metadata_file.read_bytes() == original_metadata
    assert temporary_file.read_bytes() == original_temporary


def test_install_metadata_service_keeps_malformed_v1_unchanged(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    metadata_file = plugin_dir / ".pypluginstore.json"
    malformed = legacy_install_metadata_document(plugin_key="../unsafe")
    original = (json.dumps(malformed, sort_keys=True) + "\n").encode("utf-8")
    metadata_file.write_bytes(original)
    service = metadata_service(plugin_core_module)

    with pytest.raises(ValueError):
        service.read(str(plugin_dir))

    assert metadata_file.read_bytes() == original
    assert not (plugin_dir / ".pypluginstore.json.tmp").exists()


def test_install_metadata_service_writes_and_reads_atomically(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    metadata = plugin_core_module.InstallMetadata.from_document(
        install_metadata_document()
    )
    service = metadata_service(plugin_core_module)

    service.write(str(plugin_dir), metadata)

    metadata_file = plugin_dir / ".pypluginstore.json"
    assert json.loads(metadata_file.read_text(encoding="utf-8")) == (
        install_metadata_document()
    )
    assert metadata_file.read_bytes().endswith(b"\n")
    assert not (plugin_dir / ".pypluginstore.json.tmp").exists()
    assert service.read(str(plugin_dir)).to_document() == (
        install_metadata_document()
    )


def test_install_metadata_failed_replace_keeps_previous_document(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    metadata_file = plugin_dir / ".pypluginstore.json"
    previous_document = install_metadata_document()
    metadata_file.write_text(
        json.dumps(previous_document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    previous_bytes = metadata_file.read_bytes()
    next_metadata = plugin_core_module.InstallMetadata.from_document(
        install_metadata_document(
            version="1.5.0",
            tag="v1.5.0",
            release_id="github:owner/example-plugin:v1.5.0",
            release_revision=8,
            commit="1" * 40,
            artifact_sha256="4" * 64,
            artifact_tree_sha256="5" * 64,
            index_sequence=43,
        )
    )
    service = metadata_service(plugin_core_module)

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(plugin_core_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        service.write(str(plugin_dir), next_metadata)

    assert metadata_file.read_bytes() == previous_bytes
    assert not (plugin_dir / ".pypluginstore.json.tmp").exists()


def test_install_metadata_read_discards_uncommitted_temp_next_to_valid_file(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    current_document = install_metadata_document()
    next_document = install_metadata_document(
        version="1.5.0",
        tag="v1.5.0",
        release_id="github:owner/example-plugin:v1.5.0",
        release_revision=8,
        commit="1" * 40,
        artifact_sha256="4" * 64,
        artifact_tree_sha256="5" * 64,
        index_sequence=43,
    )
    metadata_file = plugin_dir / ".pypluginstore.json"
    temp_file = plugin_dir / ".pypluginstore.json.tmp"
    metadata_file.write_text(json.dumps(current_document), encoding="utf-8")
    temp_file.write_text(json.dumps(next_document), encoding="utf-8")
    service = metadata_service(plugin_core_module)

    loaded = service.read(str(plugin_dir))

    assert loaded.to_document() == current_document
    assert not temp_file.exists()


def test_install_metadata_read_does_not_promote_orphan_temp_file(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    temp_file = plugin_dir / ".pypluginstore.json.tmp"
    temp_file.write_text(
        json.dumps(install_metadata_document()),
        encoding="utf-8",
    )
    service = metadata_service(plugin_core_module)

    loaded = service.read(str(plugin_dir))

    assert loaded is None
    assert not (plugin_dir / ".pypluginstore.json").exists()
    assert not temp_file.exists()


def test_install_metadata_read_never_uses_temp_to_mask_invalid_committed_file(
    plugin_core_module, tmp_path
):
    plugin_dir = tmp_path / "ExamplePlugin"
    plugin_dir.mkdir()
    metadata_file = plugin_dir / ".pypluginstore.json"
    temp_file = plugin_dir / ".pypluginstore.json.tmp"
    metadata_file.write_text("[]", encoding="utf-8")
    temp_file.write_text(
        json.dumps(install_metadata_document()),
        encoding="utf-8",
    )
    service = metadata_service(plugin_core_module)

    with pytest.raises(ValueError):
        service.read(str(plugin_dir))

    assert metadata_file.read_text(encoding="utf-8") == "[]"
    assert not temp_file.exists()
