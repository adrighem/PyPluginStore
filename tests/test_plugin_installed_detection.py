import json
from contextlib import contextmanager

import pytest

from plugin_core_helpers import configure_home, debug_messages, write_plugin_py


def write_release_metadata(
    plugin_dir,
    *,
    package_id="Somfy",
    repository_identity="github.com/madpatrick/domoticz_somfy",
    schema=2,
    file_name=".pypluginstore.json",
):
    document = {
        "schema": schema,
        "package_id": package_id,
        "management_mode": "release",
        "repository_identity": repository_identity,
        "version": "2.4.0",
        "tag": "v2.4.0",
        "release_id": "github:madpatrick/domoticz_somfy:v2.4.0",
        "release_revision": 1,
        "released_at": "2026-07-22T10:00:00Z",
        "commit": "1" * 40,
        "artifact_sha256": "2" * 64,
        "artifact_tree_sha256": "3" * 64,
        "artifact_provenance": "forge_source_archive",
        "artifact_files": {
            "plugin.py": {
                "sha256": "4" * 64,
                "size": 1024,
            },
        },
        "preserved_files": {},
        "index_sequence": 1,
        "installed_at": "2026-07-22T10:01:00Z",
    }
    if schema == 1:
        document["plugin_key"] = document.pop("package_id")
    metadata_path = plugin_dir / file_name
    metadata_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata_path


def configure_somfy_registry(plugin_core_module, plugin):
    plugin.plugin_data = {
        "Somfy": [
            "MadPatrick",
            "domoticz_somfy",
            "description",
            "master",
            "",
        ],
    }
    plugin.registry_entries["Somfy"] = plugin_core_module.RegistryEntry(
        "Somfy",
        "MadPatrick",
        "domoticz_somfy",
        "description",
        "master",
        domoticz_key="tahomaIO",
    )


def test_list_plugins_detects_repository_named_existing_folder(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "Domoticz-deCONZ",
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/Smanar/Domoticz-deCONZ",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }
    responses = []

    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "list_plugins"})

    response = responses[0]
    assert "deCONZ" in response["installed"]
    assert "Domoticz-deCONZ" in response["installed"]
    assert plugin.installed_plugin_folders["deCONZ"] == "Domoticz-deCONZ"
    assert response["installed_match_details"]["deCONZ"]["source"] == "plugin.py externallink"
    assert response["installed_match_details"]["Domoticz-deCONZ"]["source"] == "local folder alias"


def test_get_installed_plugins_detects_matching_git_remote(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    renamed_dir = plugins_dir / "MyZigbeePlugin"
    (renamed_dir / ".git").mkdir(parents=True)
    write_plugin_py(renamed_dir, key="DECONZ", name="deCONZ")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "git remote"


def test_git_worktree_control_file_is_detected_as_git(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    renamed_dir = plugins_dir / "MyZigbeeWorktree"
    renamed_dir.mkdir()
    (renamed_dir / ".git").write_text(
        "gitdir: /tmp/example-worktree\n",
        encoding="utf-8",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": [
            "Smanar",
            "Domoticz-deCONZ",
            "description",
            "master",
            "",
        ],
    }

    class FakeGitResult:
        stdout = (
            "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
        )
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == renamed_dir.name
    assert plugin.installed_plugin_match_details["deCONZ"]["is_git"] is True


def test_release_metadata_keeps_somfy_mapped_to_its_physical_folder(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "domoticz_somfy"
    write_plugin_py(
        plugin_dir,
        key="tahomaIO",
        name="Somfy",
        externallink="https://github.com/MadPatrick/somfy",
    )
    write_release_metadata(plugin_dir)
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" in installed
    assert "domoticz_somfy" in installed
    assert plugin.installed_plugin_folders["Somfy"] == "domoticz_somfy"
    assert (
        plugin.installed_plugin_match_details["Somfy"]["source"]
        == "release install metadata"
    )
    assert (
        plugin.installed_plugin_match_details["Somfy"]["management_mode"]
        == "release"
    )
    assert plugin.resolve_installed_plugin_dir("Somfy") == str(plugin_dir)
    with pytest.raises(ValueError, match="Plugin folder already exists"):
        plugin.release_install_update_strategy._expected_current(
            plugin.get_registry_entry("Somfy"),
            "release_install",
        )
    assert not (plugins_dir / "Somfy").exists()


def test_legacy_release_metadata_is_read_only_during_discovery(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "legacy-somfy-folder"
    write_plugin_py(plugin_dir, key="tahomaIO", name="Somfy")
    metadata_path = write_release_metadata(plugin_dir, schema=1)
    original_metadata = metadata_path.read_bytes()
    temporary_path = write_release_metadata(
        plugin_dir,
        file_name=".pypluginstore.json.tmp",
    )
    original_temporary = temporary_path.read_bytes()
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" in installed
    assert plugin.installed_plugin_folders["Somfy"] == plugin_dir.name
    assert metadata_path.read_bytes() == original_metadata
    assert temporary_path.read_bytes() == original_temporary

    def fail_upgrade(*args, **kwargs):
        raise OSError("read-only metadata")

    monkeypatch.setattr(
        plugin.install_metadata_service,
        "write",
        fail_upgrade,
    )
    management = plugin.getPluginManagementMap(
        installed,
        {},
        {},
        plugins_dir,
    )

    assert management["Somfy"]["status"] == "verification_failed"
    assert "upgraded" in management["Somfy"]["verification_message"]


def test_invalid_release_metadata_stays_visible_but_blocks_changes(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "domoticz_somfy"
    write_plugin_py(plugin_dir, key="tahomaIO", name="Somfy")
    (plugin_dir / ".pypluginstore.json").write_text(
        "{not-json",
        encoding="utf-8",
    )
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" in installed
    detail = plugin.installed_plugin_match_details["Somfy"]
    assert detail["source"] == "repository/archive folder name"
    assert detail["release_metadata_state"] == "invalid"
    assert "management_error" in detail
    with pytest.raises(ValueError, match="invalid or unreadable"):
        plugin.resolve_installed_plugin_dir("Somfy")
    management = plugin.getPluginManagementMap(
        installed,
        {},
        {},
        plugins_dir,
    )
    assert management["Somfy"]["status"] == "verification_failed"
    assert management["Somfy"]["updateable"] is False
    assert (
        "invalid or unreadable"
        in management["Somfy"]["verification_message"]
    )


@pytest.mark.parametrize(
    ("repository_identity", "with_git", "expected_state", "error"),
    [
        (
            "github.com/another-owner/domoticz_somfy",
            False,
            "repository_mismatch",
            "does not match the current registry repository",
        ),
        (
            "github.com/madpatrick/domoticz_somfy",
            True,
            "mixed",
            "both Release metadata and Git control data",
        ),
    ],
)
def test_conflicting_release_metadata_maps_identity_but_blocks_changes(
    plugin_core_module,
    tmp_path,
    repository_identity,
    with_git,
    expected_state,
    error,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "domoticz_somfy"
    write_plugin_py(plugin_dir, key="tahomaIO", name="Somfy")
    write_release_metadata(
        plugin_dir,
        repository_identity=repository_identity,
    )
    if with_git:
        (plugin_dir / ".git").mkdir()
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" in installed
    assert plugin.installed_plugin_folders["Somfy"] == plugin_dir.name
    detail = plugin.installed_plugin_match_details["Somfy"]
    assert detail["release_metadata_state"] == expected_state
    with pytest.raises(ValueError, match=error):
        plugin.resolve_installed_plugin_dir("Somfy")


def test_orphan_release_metadata_is_not_remapped_by_repository(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "domoticz_somfy"
    write_plugin_py(plugin_dir, key="tahomaIO", name="Somfy")
    write_release_metadata(plugin_dir, package_id="RemovedSomfyPackage")
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" not in installed
    assert plugin_dir.name in installed
    detail = plugin.installed_plugin_match_details[plugin_dir.name]
    assert detail["source"] == "orphan release install metadata"
    assert detail["package_id"] == "RemovedSomfyPackage"
    with pytest.raises(ValueError, match="not present in the current registry"):
        plugin.resolve_installed_plugin_dir(plugin_dir.name)
    with pytest.raises(ValueError, match="already uses this repository"):
        plugin.release_install_update_strategy._expected_current(
            plugin.get_registry_entry("Somfy"),
            "release_install",
        )
    assert not (plugins_dir / "Somfy").exists()


def test_orphan_release_metadata_surfaces_registry_install_conflict(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Somfy"
    write_plugin_py(plugin_dir, key="tahomaIO", name="Somfy")
    write_release_metadata(plugin_dir, package_id="RemovedSomfyPackage")
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" not in installed
    detail = plugin.installed_plugin_install_conflicts["Somfy"]
    assert detail["source"] == "orphan release install metadata"
    assert detail["package_id"] == "RemovedSomfyPackage"
    with pytest.raises(ValueError, match="already uses this repository"):
        plugin.resolve_installed_plugin_dir("Somfy")

    responses = []
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)
    plugin.handleApiCommand({"action": "list_plugins"})

    response = responses[0]
    assert "Somfy" not in response["installed"]
    assert (
        response["installation_conflicts"]["Somfy"]["package_id"]
        == "RemovedSomfyPackage"
    )


def test_same_name_orphan_with_different_repository_blocks_install(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Somfy"
    write_plugin_py(plugin_dir, key="removed", name="Removed package")
    write_release_metadata(
        plugin_dir,
        package_id="RemovedSomfyPackage",
        repository_identity="github.com/other/removed-somfy",
    )
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" not in installed
    conflict = plugin.installed_plugin_install_conflicts["Somfy"]
    assert conflict["reasons"] == ["package folder"]
    assert conflict["repository_identity"] == (
        "github.com/other/removed-somfy"
    )
    with pytest.raises(ValueError, match="package folder"):
        plugin.resolve_installed_plugin_dir("Somfy")


def test_orphan_repository_blocks_all_public_and_local_registry_owners(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "legacy-somfy"
    write_plugin_py(plugin_dir, key="removed", name="Removed package")
    write_release_metadata(
        plugin_dir,
        package_id="RemovedSomfyPackage",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "PublicSomfy": [
            "MadPatrick",
            "domoticz_somfy",
            "Public entry",
            "master",
            "",
        ],
        "LocalSomfy": [
            "MadPatrick",
            "domoticz_somfy",
            "Local entry",
            "main",
            "",
        ],
    }
    plugin.registry_entries = {
        key: plugin_core_module.RegistryEntry(
            key,
            "MadPatrick",
            "domoticz_somfy",
            description,
            branch,
            local=(key == "LocalSomfy"),
        )
        for key, description, branch in (
            ("PublicSomfy", "Public entry", "master"),
            ("LocalSomfy", "Local entry", "main"),
        )
    }
    plugin.local_plugin_keys = ["LocalSomfy"]

    plugin.getInstalledPlugins(plugins_dir)

    assert set(plugin.installed_plugin_install_conflicts) == {
        "LocalSomfy",
        "PublicSomfy",
    }
    assert all(
        conflict["reasons"] == ["repository identity"]
        for conflict in plugin.installed_plugin_install_conflicts.values()
    )


def test_orphan_registry_folder_cannot_overwrite_valid_release_mapping(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    valid_dir = plugins_dir / "domoticz_somfy"
    write_plugin_py(valid_dir, key="tahomaIO", name="Somfy")
    write_release_metadata(valid_dir)
    orphan_dir = plugins_dir / "Somfy"
    write_plugin_py(orphan_dir, key="tahomaIO", name="Somfy")
    write_release_metadata(orphan_dir, package_id="RemovedSomfyPackage")
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" in installed
    assert plugin.installed_plugin_folders["Somfy"] == valid_dir.name
    assert plugin.installed_plugin_install_conflicts["Somfy"]["folders"] == [
        orphan_dir.name
    ]
    with pytest.raises(ValueError, match="already uses this repository"):
        plugin.resolve_installed_plugin_dir("Somfy")


def test_duplicate_release_metadata_folders_remain_ambiguous(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    for folder in ("somfy-copy-a", "somfy-copy-b"):
        plugin_dir = plugins_dir / folder
        write_plugin_py(plugin_dir, key="tahomaIO", name="Somfy")
        write_release_metadata(plugin_dir)
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" in installed
    assert "Somfy" not in plugin.installed_plugin_folders
    assert plugin.ambiguous_installed_plugin_folders["Somfy"] == [
        "somfy-copy-a",
        "somfy-copy-b",
    ]
    with pytest.raises(ValueError, match="Multiple installed folders"):
        plugin.resolve_installed_plugin_dir("Somfy")


def test_orphan_temporary_metadata_is_not_discovery_evidence(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "unmanaged-somfy"
    write_plugin_py(plugin_dir, key="OTHER", name="Other")
    temporary_path = write_release_metadata(
        plugin_dir,
        file_name=".pypluginstore.json.tmp",
    )
    original_temporary = temporary_path.read_bytes()
    plugin = plugin_core_module.BasePlugin()
    configure_somfy_registry(plugin_core_module, plugin)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Somfy" not in installed
    assert plugin_dir.name in installed
    assert (
        plugin.installed_plugin_match_details[plugin_dir.name]["source"]
        == "local folder"
    )
    assert temporary_path.read_bytes() == original_temporary


def test_installed_scan_holds_release_operation_lock(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    lock_events = []

    @contextmanager
    def tracking_lock(*, blocking=True):
        assert blocking is False
        lock_events.append("entered")
        yield
        lock_events.append("exited")

    monkeypatch.setattr(
        plugin.release_transaction_manager,
        "operation_lock",
        tracking_lock,
    )

    plugin.getInstalledPlugins(plugins_dir)

    assert lock_events == ["entered", "exited"]


def test_failed_installed_scan_retains_last_successful_snapshot(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "deCONZ"
    plugin_dir.mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": [
            "Smanar",
            "Domoticz-deCONZ",
            "description",
            "master",
            "",
        ],
    }
    installed = plugin.getInstalledPlugins(plugins_dir)
    folders = dict(plugin.installed_plugin_folders)
    details = dict(plugin.installed_plugin_match_details)

    def fail_scan(path):
        raise ValueError("simulated scan failure")

    monkeypatch.setattr(
        plugin,
        "_get_installed_plugins_unlocked",
        fail_scan,
    )

    rescanned = plugin.getInstalledPlugins(plugins_dir)

    assert rescanned == installed
    assert plugin.installed_plugin_folders == folders
    assert plugin.installed_plugin_match_details == details
    assert "last successful" in plugin.installed_plugin_scan_error


def test_lock_failure_lists_plugins_but_pauses_changes(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "deCONZ"
    plugin_dir.mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": [
            "Smanar",
            "Domoticz-deCONZ",
            "description",
            "master",
            "",
        ],
    }

    @contextmanager
    def unavailable_lock(*, blocking=True):
        assert blocking is False
        raise OSError("read-only manager state")
        yield

    monkeypatch.setattr(
        plugin.release_transaction_manager,
        "operation_lock",
        unavailable_lock,
    )

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders == {}
    assert plugin.installed_plugin_match_details == {}
    assert plugin.installed_plugins_snapshot == []
    assert plugin.installed_plugins_snapshot_available is False
    assert "changes are paused" in plugin.installed_plugin_scan_error
    assert "read-only manager state" in plugin.installed_plugin_scan_error
    with pytest.raises(ValueError, match="scanned safely"):
        plugin.resolve_installed_plugin_dir("deCONZ", refresh=True)


def test_noncanonical_inventory_scan_does_not_create_manager_lock_state(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    _plugins_dir, manager_dir = configure_home(
        plugin_core_module,
        tmp_path,
    )
    inventory_dir = tmp_path / "offline-inventory"
    (inventory_dir / "LoosePlugin").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()

    def unexpected_lock(*args, **kwargs):
        raise AssertionError("noncanonical scan acquired transaction lock")

    monkeypatch.setattr(
        plugin.release_transaction_manager,
        "operation_lock",
        unexpected_lock,
    )

    installed = plugin.getInstalledPlugins(inventory_dir)

    assert "LoosePlugin" in installed
    assert plugin.installed_plugin_folders == {}
    assert plugin.installed_plugin_match_details == {}
    assert plugin.installed_plugins_snapshot == []
    assert plugin.installed_plugins_snapshot_available is False
    assert not (manager_dir / ".pypluginstore").exists()


def test_lock_failure_preserves_a_trusted_empty_snapshot(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()

    plugin.getInstalledPlugins(plugins_dir)
    plugin.installed_plugins_snapshot = []
    plugin.installed_plugin_folders = {}
    plugin.installed_plugin_match_details = {}
    assert plugin.installed_plugins_snapshot_available is True
    (plugins_dir / "AppearedDuringOperation").mkdir()

    @contextmanager
    def busy_lock(*, blocking=True):
        assert blocking is False
        raise RuntimeError("another operation is active")
        yield

    monkeypatch.setattr(
        plugin.release_transaction_manager,
        "operation_lock",
        busy_lock,
    )

    assert plugin.getInstalledPlugins(plugins_dir) == []
    assert plugin.installed_plugins_snapshot == []
    assert "another operation is active" in plugin.installed_plugin_scan_error


def test_exact_registry_folder_without_metadata_is_installed(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "deCONZ").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "deCONZ"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "exact folder key"


def test_exact_registry_folder_with_conflicting_metadata_is_installed(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "deCONZ",
        key="OTHER",
        name="OtherPlugin",
        externallink="https://github.com/example/OtherPlugin",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "deCONZ"


def test_externallink_overrides_exact_registry_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "deCONZ",
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/MadPatrick/Domoticz-BMW-plugin",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Bmw" in installed
    assert "deCONZ" not in installed
    assert plugin.installed_plugin_folders["Bmw"] == "deCONZ"
    assert plugin.installed_plugin_match_details["Bmw"]["source"] == "plugin.py externallink"


def test_repository_named_folder_without_metadata_is_inferred(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Domoticz-deCONZ").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "Domoticz-deCONZ"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "repository/archive folder name"


def test_multiple_physical_folders_for_one_plugin_are_blocked(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    canonical = plugins_dir / "deCONZ"
    repository_named = plugins_dir / "Domoticz-deCONZ"
    canonical.mkdir()
    repository_named.mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": [
            "Smanar",
            "Domoticz-deCONZ",
            "description",
            "master",
            "",
        ],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert "deCONZ" not in plugin.installed_plugin_folders
    assert plugin.ambiguous_installed_plugin_folders["deCONZ"] == [
        "deCONZ",
        "Domoticz-deCONZ",
    ]
    detail = plugin.installed_plugin_match_details["deCONZ"]
    assert detail["source"] == "ambiguous physical folders"
    assert detail["folders"] == ["deCONZ", "Domoticz-deCONZ"]
    with pytest.raises(ValueError, match="Multiple installed folders"):
        plugin.resolve_installed_plugin_dir("deCONZ")

    repository_named.rmdir()

    assert plugin.resolve_installed_plugin_dir(
        "deCONZ",
        refresh=True,
    ) == str(canonical)
    assert plugin.ambiguous_installed_plugin_folders == {}


def test_repository_named_folder_matches_flexible_punctuation(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Domoticz-HP-iLo").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "HP_iLo": ["MadPatrick", "Domoticz_HP_ilo", "description", "main", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "HP_iLo" in installed
    assert plugin.installed_plugin_folders["HP_iLo"] == "Domoticz-HP-iLo"
    assert plugin.installed_plugin_match_details["HP_iLo"]["source"] == "normalized folder name"


def test_local_alias_detects_repository_named_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "domoticz-apc-ups-plugin").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "APC_UPS": ["MadPatrick", "domoticz-apc-ups-plugin", "description", "main", ""],
    }
    plugin.local_plugin_keys = ["APC_UPS"]

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "APC_UPS" in installed
    assert plugin.installed_plugin_folders["APC_UPS"] == "domoticz-apc-ups-plugin"


def test_local_alias_preferred_when_repository_name_collides(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Domoticz-BMW-plugin").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
        "Domoticz-BMW-plugin": ["FilipDem", "Domoticz-BMW-plugin", "description", "main", ""],
    }
    plugin.local_plugin_keys = ["Bmw"]

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Bmw" in installed
    assert "Domoticz-BMW-plugin" not in installed
    assert plugin.installed_plugin_folders["Bmw"] == "Domoticz-BMW-plugin"
    assert plugin.installed_plugin_match_details["Bmw"]["source"] == "repository/archive folder name"


def test_domoticz_affixed_repo_matches_short_branch_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "APC UPS-main").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "APC_UPS": ["MadPatrick", "Domoticz_apc_ups_plugin", "description", "main", ""],
    }
    plugin.local_plugin_keys = ["APC_UPS"]

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "APC_UPS" in installed
    assert plugin.installed_plugin_folders["APC_UPS"] == "APC UPS-main"
    assert plugin.installed_plugin_match_details["APC_UPS"]["source"] == "normalized folder name"


def test_domoticz_affixed_archive_accepts_stripped_metadata_key(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "Domoticz_Marstek_Modbus-main",
        key="Marstek_modbus",
        name="Marstek Venus Modbus",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Domoticz_Marstek_Modbus": [
            "hopSilentSimon",
            "Domoticz_Marstek_Modbus",
            "description",
            "main",
            "",
        ],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Domoticz_Marstek_Modbus" in installed
    assert plugin.installed_plugin_folders["Domoticz_Marstek_Modbus"] == "Domoticz_Marstek_Modbus-main"
    assert plugin.installed_plugin_match_details["Domoticz_Marstek_Modbus"]["source"] == "repository/archive folder name"


def test_git_remote_match_does_not_require_plugin_metadata(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    renamed_dir = plugins_dir / "MyZigbeePlugin"
    (renamed_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"


def test_git_remote_match_overrides_conflicting_externallink(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    renamed_dir = plugins_dir / "MyZigbeePlugin"
    (renamed_dir / ".git").mkdir(parents=True)
    write_plugin_py(
        renamed_dir,
        key="BMW",
        name="BMW",
        externallink="https://github.com/MadPatrick/Domoticz-BMW-plugin",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert "Bmw" not in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "git remote"


def test_unmatched_git_remote_falls_back_to_externallink(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    fork_dir = plugins_dir / "MyPrivateFork"
    (fork_dir / ".git").mkdir(parents=True)
    write_plugin_py(
        fork_dir,
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/Smanar/Domoticz-deCONZ",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:private/Domoticz-deCONZ-fork.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyPrivateFork"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "plugin.py externallink"


def test_unmatched_git_remote_allows_exact_folder_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "deCONZ"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:private/Domoticz-deCONZ-fork.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "deCONZ"


def test_installed_fork_branch_reports_registry_mismatch(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "domoticz-solaredge-modbustcp-plugin"
    (plugin_dir / ".git").mkdir(parents=True)
    write_plugin_py(
        plugin_dir,
        key="SolarEdge_ModbusTCP",
        name="SolarEdge ModbusTCP",
        externallink="https://github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "domoticz-solaredge-modbustcp-plugin": [
            "addiejanssen",
            "domoticz-solaredge-modbustcp-plugin",
            "description",
            "meters",
            "",
        ],
    }

    class FakeGitResult:
        stderr = ""
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_git(plugin_dir_arg, command, timeout=15):
        if command == ["git", "remote", "-v"]:
            return FakeGitResult(
                "origin\thttps://github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin.git (fetch)\n"
            )
        if command == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return FakeGitResult("MetersDev\n")
        if command == ["git", "config", "--get", "branch.MetersDev.remote"]:
            return FakeGitResult("origin\n")
        if command == ["git", "remote", "get-url", "origin"]:
            return FakeGitResult("https://github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin.git\n")
        raise AssertionError("unexpected git command: " + repr(command))

    plugin.run_git_command = fake_git

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "domoticz-solaredge-modbustcp-plugin" in installed
    details = plugin.installed_plugin_match_details["domoticz-solaredge-modbustcp-plugin"]
    assert details["registry_mismatch"] is True
    assert details["repo_mismatch"] is True
    assert details["branch_mismatch"] is True
    assert details["configured_repo"] == "github.com/addiejanssen/domoticz-solaredge-modbustcp-plugin"
    assert details["configured_branch"] == "meters"
    assert details["installed_repo"] == "github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin"
    assert details["installed_branch"] == "MetersDev"
    assert plugin.getCachedUpdateStatuses(installed)["domoticz-solaredge-modbustcp-plugin"] == "mismatch"


def test_unmatched_git_remote_allows_repository_folder_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Domoticz-deCONZ"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:private/Domoticz-deCONZ-fork.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "Domoticz-deCONZ"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "repository/archive folder name"


def test_unknown_externallink_allows_metadata_name_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "MyZigbeePlugin",
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/private/Domoticz-deCONZ-fork",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "plugin.py key/name"
    assert any("externallink" in message and "does not match the registry" in message for message in debug_messages(plugin_core_module))


def test_externallink_match_detects_arbitrary_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "MyZigbeePlugin",
        key="OTHER",
        name="OtherPlugin",
        externallink="https://github.com/Smanar/Domoticz-deCONZ",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"


def test_plugin_metadata_name_detects_arbitrary_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "MyZigbeePlugin", key="DECONZ", name="deCONZ")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"


def test_invalid_folder_inference_falls_back_to_metadata_name(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "Domoticz-deCONZ", key="BMW", name="BMW")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Bmw" in installed
    assert "deCONZ" not in installed
    assert plugin.installed_plugin_folders["Bmw"] == "Domoticz-deCONZ"
    assert plugin.installed_plugin_match_details["Bmw"]["source"] == "plugin.py key/name"
    assert any("continuing with lower priority evidence" in message for message in debug_messages(plugin_core_module))


def test_flexible_folder_match_rejects_ambiguous_names(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Shared Plugin").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "FirstPlugin": ["owner-a", "Shared-Plugin", "description", "master", ""],
        "SecondPlugin": ["owner-b", "Shared_Plugin", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "FirstPlugin" not in installed
    assert "SecondPlugin" not in installed
    assert plugin.installed_plugin_match_details["Shared Plugin"]["source"] == "local folder"
    assert any("normalized folder name" in message and "multiple registry entries" in message for message in debug_messages(plugin_core_module))


def test_archive_folder_rejects_ambiguous_repository_name(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "SharedRepo-master", key="SHARED", name="SharedRepo")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "FirstPlugin": ["owner-a", "SharedRepo", "description", "master", ""],
        "SecondPlugin": ["owner-b", "SharedRepo", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "FirstPlugin" not in installed
    assert "SecondPlugin" not in installed
    assert plugin.installed_plugin_match_details["SharedRepo-master"]["source"] == "local folder"
    assert any("folder name" in message and "multiple registry entries" in message for message in debug_messages(plugin_core_module))


def test_archive_folder_does_not_accept_author_only_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Domoticz-deCONZ-master"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        '"""\n<plugin key="OTHER" name="OtherPlugin" author="Smanar">\n</plugin>\n"""\n'
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" not in installed
