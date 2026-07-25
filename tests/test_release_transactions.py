import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from plugin_core_helpers import configure_home
from test_release_migration import GIT, head_commit, initialize_repository


OLD_COMMIT = "1" * 40
OLD_TREE = "2" * 64
NEW_COMMIT = "3" * 40
NEW_TREE = "4" * 64


class SimulatedCrash(BaseException):
    pass


def install_metadata_document(
    commit,
    tree_sha256,
    revision,
    release_id,
    artifact_files=None,
):
    if artifact_files is None:
        artifact_files = {
            "plugin.py": {"sha256": "4" * 64, "size": 4096},
        }
    return {
        "schema": 3,
        "package_id": "ExamplePlugin",
        "management_mode": "release",
        "authority": "release_index",
        "candidate_fingerprint": "",
        "repository_identity": "github.com/owner/example-plugin",
        "version": "1.0.0" if revision == 1 else "2.0.0",
        "tag": "v1.0.0" if revision == 1 else "v2.0.0",
        "release_id": release_id,
        "release_revision": revision,
        "released_at": "2026-07-18T07:00:00Z",
        "commit": commit,
        "artifact_sha256": "5" * 64,
        "artifact_tree_sha256": tree_sha256,
        "artifact_provenance": "forge_source_archive",
        "artifact_files": artifact_files,
        "preserved_files": {},
        "index_sequence": revision,
        "installed_at": "2026-07-18T08:00:00Z",
    }


def legacy_transaction_document(document):
    legacy = copy.deepcopy(document)
    legacy["schema_version"] = 1
    legacy["plugin_key"] = legacy.pop("package_id")
    legacy.pop("live_folder", None)
    return legacy


def previous_transaction_document(document):
    previous = copy.deepcopy(document)
    previous["schema_version"] = 2
    previous.pop("live_folder", None)
    return previous


def legacy_install_metadata_document(document):
    legacy = copy.deepcopy(document)
    legacy["schema"] = 1
    legacy["plugin_key"] = legacy.pop("package_id")
    legacy.pop("authority", None)
    legacy.pop("candidate_fingerprint", None)
    return legacy


def expected_current():
    return {
        "management_mode": "release",
        "commit": OLD_COMMIT,
        "artifact_tree_sha256": OLD_TREE,
    }


def migration_snapshot():
    return {
        "repository_identity": "github.com/owner/example-plugin",
        "release_commit": OLD_COMMIT,
        "relationship": "equal",
        "inventory_sha256": "6" * 64,
        "tracked_changes": [],
        "untracked_files": [],
        "mutable_paths": [],
        "shallow": False,
    }


def migration_expected_current():
    return {
        "management_mode": "git",
        "commit": OLD_COMMIT,
        "migration_snapshot": migration_snapshot(),
    }


def target_release():
    return {
        "management_mode": "release",
        "release_id": "github:owner/example-plugin:v2.0.0",
        "release_revision": 2,
        "commit": NEW_COMMIT,
        "artifact_tree_sha256": NEW_TREE,
    }


def dependency_snapshot():
    return {
        "installer": "none",
        "command": [],
        "compatibility_warnings": [],
        "compatibility_conflicts": [],
        "compatibility_confirmed": False,
    }


def new_manager(plugin_core_module, windows=False):
    plugin = plugin_core_module.BasePlugin()
    if windows:
        plugin.host = plugin_core_module.WindowsHostRuntime(
            plugin_core_module.Parameters
        )
    return plugin_core_module.ReleaseTransactionManager(plugin)


def make_manager(plugin_core_module, tmp_path, windows=False):
    plugins_dir, manager_dir = configure_home(plugin_core_module, tmp_path)
    return (
        new_manager(plugin_core_module, windows=windows),
        plugins_dir,
        manager_dir,
    )


def write_marker(directory, value, extra_name=""):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "marker.txt").write_text(value, encoding="utf-8")
    if extra_name:
        (directory / extra_name).write_text(value, encoding="utf-8")


def read_marker(directory):
    return (Path(directory) / "marker.txt").read_text(encoding="utf-8")


def artifact_inventory(directory):
    directory = Path(directory)
    inventory = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name == ".pypluginstore.json":
            continue
        contents = path.read_bytes()
        inventory[path.relative_to(directory).as_posix()] = {
            "sha256": hashlib.sha256(contents).hexdigest(),
            "size": len(contents),
        }
    return inventory


def install_current_release(
    plugins_dir,
    manager_dir,
    live_folder="ExamplePlugin",
):
    live_code = plugins_dir / live_folder
    live_dependencies = manager_dir / ".shared_deps"
    write_marker(live_code, "old-code", "old-only.py")
    write_marker(live_dependencies, "old-dependencies", "old-only.py")
    (live_code / "plugin.py").write_text(
        "# old plugin\n",
        encoding="utf-8",
    )
    (live_code / ".pypluginstore.json").write_text(
        json.dumps(
            install_metadata_document(
                OLD_COMMIT,
                OLD_TREE,
                1,
                "github:owner/example-plugin:v1.0.0",
                artifact_inventory(live_code),
            )
        ),
        encoding="utf-8",
    )
    return live_code, live_dependencies


def prepare_transaction(
    manager,
    plugins_dir,
    manager_dir,
    operation_id="operation-001",
    *,
    stage_dependencies=True,
    live_folder="ExamplePlugin",
):
    live_code, live_dependencies = install_current_release(
        plugins_dir,
        manager_dir,
        live_folder,
    )

    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id=operation_id,
        operation="release_update",
        expected_current=expected_current(),
        target=target_release(),
        live_folder=live_folder,
    )
    write_marker(transaction.paths.staged_code, "new-code", "new-only.py")
    (Path(transaction.paths.staged_code) / "plugin.py").write_text(
        "# new plugin\n",
        encoding="utf-8",
    )
    shutil.copytree(
        live_dependencies,
        transaction.paths.staged_dependencies,
        dirs_exist_ok=True,
    )
    (
        Path(transaction.paths.staged_code) / ".pypluginstore.json"
    ).write_text(
        json.dumps(
            install_metadata_document(
                NEW_COMMIT,
                NEW_TREE,
                2,
                "github:owner/example-plugin:v2.0.0",
                artifact_inventory(transaction.paths.staged_code),
            )
        ),
        encoding="utf-8",
    )
    verified = manager.mark_staged_verified(operation_id)
    if not stage_dependencies:
        return verified
    return manager.mark_dependencies_staged(
        operation_id,
        dependency_snapshot(),
    )


def prepare_pre_activation_transaction(
    manager,
    plugins_dir,
    manager_dir,
    phase,
):
    """Reach a durable pre-activation phase through the public API."""
    if phase == "created":
        return manager.create_transaction(
            plugin_key="ExamplePlugin",
            operation_id="operation-001",
            operation="release_install",
            expected_current={"management_mode": "absent"},
            target=target_release(),
        )

    transaction = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        stage_dependencies=False,
    )
    if phase == "staged_verified":
        return transaction
    if phase == "dependency_confirmation_required":
        return manager.mark_dependency_confirmation_required(
            transaction.operation_id,
            dependency_snapshot(),
        )
    if phase == "dependencies_staged":
        return manager.mark_dependencies_staged(
            transaction.operation_id,
            dependency_snapshot(),
        )
    if phase == "dependency_blocked":
        return manager.mark_dependency_blocked(
            transaction.operation_id,
            "incompatible_dependencies",
            "Original dependency failure.",
        )
    raise AssertionError("Unsupported test phase: " + phase)


def migration_expected_from_preflight(preflight):
    return {
        "management_mode": "git",
        "commit": preflight.installed_commit,
        "migration_snapshot": {
            "repository_identity": preflight.installed_repository_identity,
            "release_commit": preflight.release_commit,
            "relationship": preflight.relationship,
            "inventory_sha256": preflight.inventory_sha256,
            "tracked_changes": list(preflight.tracked_changes),
            "untracked_files": list(preflight.untracked_files),
            "mutable_paths": list(
                preflight.preservation_inventory.mutable_paths
            ),
            "shallow": preflight.shallow,
        },
    }


def prepare_migration_transaction(
    plugin_core_module,
    manager,
    plugins_dir,
    manager_dir,
    operation_id="operation-001",
):
    live_code, installed_commit = initialize_repository(
        plugins_dir / "ExamplePlugin"
    )
    live_dependencies = manager_dir / ".shared_deps"
    write_marker(live_dependencies, "old-dependencies", "old-only.py")
    preflight = plugin_core_module.GitMigrationPreflight(
        manager.plugin
    ).evaluate(
        plugin_key="ExamplePlugin",
        plugin_dir=str(live_code),
        repository_identity="github.com/owner/example-plugin",
        release_commit=installed_commit,
        trigger="manual",
        mutable_paths=[],
    )
    assert preflight.allowed is True
    assert preflight.preservation_inventory is not None
    target = target_release()
    target["commit"] = installed_commit
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id=operation_id,
        operation="release_migration",
        expected_current=migration_expected_from_preflight(preflight),
        target=target,
    )
    write_marker(transaction.paths.staged_code, "new-code", "new-only.py")
    staged_code = Path(transaction.paths.staged_code)
    (staged_code / "plugin.py").write_text(
        "# release plugin\n",
        encoding="utf-8",
    )
    (staged_code / ".pypluginstore.json").write_text(
        json.dumps(
            install_metadata_document(
                installed_commit,
                NEW_TREE,
                2,
                "github:owner/example-plugin:v2.0.0",
                artifact_inventory(staged_code),
            )
        ),
        encoding="utf-8",
    )
    shutil.copytree(
        live_dependencies,
        transaction.paths.staged_dependencies,
    )
    manager.mark_staged_verified(operation_id)
    transaction = manager.mark_dependencies_staged(
        operation_id,
        dependency_snapshot(),
    )
    return transaction, live_code, installed_commit, preflight


def prepare_other_release_install(
    manager,
    operation_id="operation-002",
    dependency_marker="newer-shared-dependencies",
):
    transaction = manager.create_transaction(
        plugin_key="OtherPlugin",
        operation_id=operation_id,
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )
    staged_code = Path(transaction.paths.staged_code)
    write_marker(staged_code, "other-code", "other-only.py")
    (staged_code / "plugin.py").write_text(
        "# other plugin\n",
        encoding="utf-8",
    )
    metadata = install_metadata_document(
        NEW_COMMIT,
        NEW_TREE,
        2,
        "github:owner/example-plugin:v2.0.0",
        artifact_inventory(staged_code),
    )
    metadata["package_id"] = "OtherPlugin"
    metadata["repository_identity"] = "github.com/owner/other-plugin"
    (staged_code / ".pypluginstore.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    shutil.copytree(
        transaction.paths.live_dependencies,
        transaction.paths.staged_dependencies,
    )
    write_marker(
        transaction.paths.staged_dependencies,
        dependency_marker,
    )
    manager.mark_staged_verified(transaction.operation_id)
    return manager.mark_dependencies_staged(
        transaction.operation_id,
        dependency_snapshot(),
    )


def prepare_new_release_install(manager, operation_id="operation-001"):
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id=operation_id,
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )
    write_marker(transaction.paths.staged_code, "new-code", "new-only.py")
    staged_code = Path(transaction.paths.staged_code)
    (staged_code / "plugin.py").write_text(
        "# new plugin\n",
        encoding="utf-8",
    )
    (staged_code / ".pypluginstore.json").write_text(
        json.dumps(
            install_metadata_document(
                NEW_COMMIT,
                NEW_TREE,
                2,
                "github:owner/example-plugin:v2.0.0",
                artifact_inventory(staged_code),
            )
        ),
        encoding="utf-8",
    )
    Path(transaction.paths.staged_dependencies).mkdir()
    manager.mark_staged_verified(transaction.operation_id)
    return manager.mark_dependencies_staged(
        transaction.operation_id,
        dependency_snapshot(),
    )


def assert_old_live(transaction):
    assert read_marker(transaction.paths.live_code) == "old-code"
    assert read_marker(transaction.paths.live_dependencies) == "old-dependencies"
    assert (Path(transaction.paths.live_code) / "old-only.py").is_file()
    assert not (Path(transaction.paths.live_code) / "new-only.py").exists()
    assert (Path(transaction.paths.live_dependencies) / "old-only.py").is_file()
    assert not (
        Path(transaction.paths.live_dependencies) / "new-only.py"
    ).exists()


def assert_new_live(transaction):
    assert read_marker(transaction.paths.live_code) == "new-code"
    assert read_marker(transaction.paths.live_dependencies) == "old-dependencies"
    assert (Path(transaction.paths.live_code) / "new-only.py").is_file()
    assert not (Path(transaction.paths.live_code) / "old-only.py").exists()
    assert (Path(transaction.paths.live_dependencies) / "old-only.py").is_file()
    assert not (Path(transaction.paths.live_dependencies) / "new-only.py").exists()


def test_release_transaction_paths_are_manager_owned_and_same_filesystem(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    state_root = manager_dir / ".pypluginstore"

    assert Path(transaction.paths.journal) == (
        state_root / "transactions" / "operation-001.json"
    )
    assert Path(transaction.paths.staged_code) == (
        state_root / "staging" / "ExamplePlugin" / "operation-001" / "code"
    )
    assert Path(transaction.paths.staged_dependencies) == (
        state_root
        / "staging"
        / "ExamplePlugin"
        / "operation-001"
        / "dependencies"
    )
    assert Path(transaction.paths.backup_code) == (
        state_root / "backups" / "ExamplePlugin" / "operation-001" / "code"
    )
    assert Path(transaction.paths.backup_dependencies) == (
        state_root
        / "backups"
        / "ExamplePlugin"
        / "operation-001"
        / "dependencies"
    )
    assert Path(transaction.paths.live_code) == plugins_dir / "ExamplePlugin"
    assert Path(transaction.paths.live_dependencies) == manager_dir / ".shared_deps"

    manager_device = os.stat(manager_dir).st_dev
    assert os.stat(transaction.paths.live_code).st_dev == manager_device
    assert os.stat(transaction.paths.live_dependencies).st_dev == manager_device
    assert os.stat(Path(transaction.paths.staged_code).parent).st_dev == (
        manager_device
    )
    assert os.stat(Path(transaction.paths.backup_code).parent).st_dev == (
        manager_device
    )


def test_release_transaction_persists_alias_for_recovery_and_rollback(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    live_folder = "domoticz_example_plugin"
    transaction = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        live_folder=live_folder,
    )

    reloaded = new_manager(plugin_core_module).load_transaction(
        transaction.operation_id
    )

    assert reloaded.live_folder == live_folder
    assert Path(reloaded.paths.live_code) == plugins_dir / live_folder
    assert not (plugins_dir / "ExamplePlugin").exists()

    manager.activate(transaction.operation_id)
    assert_new_live(transaction)
    manager.rollback(transaction.operation_id)
    assert_old_live(transaction)
    assert not (plugins_dir / "ExamplePlugin").exists()


@pytest.mark.parametrize(
    "plugin_key,operation_id",
    [
        ("../Plugin", "operation-001"),
        (".hidden", "operation-001"),
        ("ExamplePlugin", "../operation"),
        ("ExamplePlugin", "/absolute"),
        ("ExamplePlugin", ""),
    ],
)
def test_release_transaction_rejects_unsafe_path_identifiers(
    plugin_core_module, tmp_path, plugin_key, operation_id
):
    manager, _, manager_dir = make_manager(plugin_core_module, tmp_path)

    with pytest.raises(ValueError):
        manager.create_transaction(
            plugin_key=plugin_key,
            operation_id=operation_id,
            operation="release_update",
            expected_current=expected_current(),
            target=target_release(),
        )

    state_root = manager_dir / ".pypluginstore"
    assert not (tmp_path / "operation").exists()
    if state_root.exists():
        assert not any(path.name == "operation" for path in state_root.rglob("*"))


@pytest.mark.parametrize(
    "live_folder",
    [
        "../OtherPlugin",
        ".hidden",
        "nested/Plugin",
        "Plugin.",
        "CON",
    ],
)
def test_release_transaction_rejects_unsafe_live_folder(
    plugin_core_module,
    tmp_path,
    live_folder,
):
    manager, _, _ = make_manager(plugin_core_module, tmp_path)

    with pytest.raises(ValueError, match="live_folder"):
        manager.create_transaction(
            plugin_key="ExamplePlugin",
            live_folder=live_folder,
            operation_id="operation-001",
            operation="release_update",
            expected_current=expected_current(),
            target=target_release(),
        )


def test_release_install_cannot_target_an_alias_folder(
    plugin_core_module,
    tmp_path,
):
    manager, _, _ = make_manager(plugin_core_module, tmp_path)

    with pytest.raises(ValueError, match="package folder"):
        manager.create_transaction(
            plugin_key="ExamplePlugin",
            live_folder="OtherPlugin",
            operation_id="operation-001",
            operation="release_install",
            expected_current={"management_mode": "absent"},
            target=target_release(),
        )


def test_release_transaction_never_reuses_an_orphan_operation_directory(
    plugin_core_module, tmp_path
):
    manager, _, manager_dir = make_manager(plugin_core_module, tmp_path)
    with manager.operation_lock():
        pass
    state_root = manager_dir / ".pypluginstore"
    orphan = (
        state_root / "staging" / "ExamplePlugin" / "operation-001"
    )
    orphan.mkdir(parents=True)
    (orphan / "untrusted.txt").write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="operation path already exists"):
        manager.create_transaction(
            plugin_key="ExamplePlugin",
            operation_id="operation-001",
            operation="release_update",
            expected_current=expected_current(),
            target=target_release(),
        )

    assert not (
        state_root / "transactions" / "operation-001.json"
    ).exists()


def test_release_transaction_journal_contains_complete_recovery_descriptor(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    document = json.loads(
        Path(transaction.paths.journal).read_text(encoding="utf-8")
    )

    assert document["schema_version"] == 3
    assert document["operation_id"] == "operation-001"
    assert document["package_id"] == "ExamplePlugin"
    assert document["live_folder"] == "ExamplePlugin"
    assert "plugin_key" not in document
    assert document["operation"] == "release_update"
    assert document["phase"] == "dependencies_staged"
    assert document["expected_current"] == expected_current()
    assert document["target"] == target_release()
    assert document["dependency_snapshot"] == dependency_snapshot()
    assert document["dependency_state"]["expected"]["present"] is True
    assert document["dependency_state"]["target"]["present"] is True
    assert document["staged_snapshot"]["artifact_files"] == (
        install_metadata_document(
            NEW_COMMIT,
            NEW_TREE,
            2,
            "github:owner/example-plugin:v2.0.0",
            artifact_inventory(transaction.paths.staged_code),
        )["artifact_files"]
    )
    assert document["rollback_from"] == ""
    assert document["paths"] == transaction.paths.to_document()
    assert document["created_at"].endswith("Z")
    assert document["updated_at"].endswith("Z")


def test_release_transaction_v1_requires_explicit_normalization_and_upgrades_on_load(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    current = json.loads(journal_path.read_text(encoding="utf-8"))
    legacy = legacy_transaction_document(current)

    for legacy_key_document in (
        {
            **current,
            "plugin_key": current["package_id"],
        },
        {
            **{
                key: value
                for key, value in current.items()
                if key != "package_id"
            },
            "plugin_key": current["package_id"],
        },
    ):
        with pytest.raises(ValueError):
            plugin_core_module.ReleaseTransaction.from_document(
                legacy_key_document, transaction.paths
            )

    with pytest.raises(ValueError):
        plugin_core_module.ReleaseTransaction.from_document(
            legacy, transaction.paths
        )
    normalized = plugin_core_module.ReleaseTransaction.from_legacy_document(
        legacy, transaction.paths
    )
    assert normalized.package_id == "ExamplePlugin"
    assert "plugin_key" not in normalized.to_document()

    journal_path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = new_manager(plugin_core_module).load_transaction(
        transaction.operation_id
    )
    upgraded = json.loads(journal_path.read_text(encoding="utf-8"))

    assert loaded.package_id == "ExamplePlugin"
    assert upgraded["schema_version"] == 3
    assert upgraded["package_id"] == "ExamplePlugin"
    assert upgraded["live_folder"] == "ExamplePlugin"
    assert "plugin_key" not in upgraded
    assert upgraded["created_at"] == legacy["created_at"]
    assert upgraded["updated_at"] == legacy["updated_at"]
    assert not journal_path.with_suffix(".json.tmp").exists()


def test_release_transaction_v2_upgrades_with_package_folder_default(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    previous = previous_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(previous), encoding="utf-8")

    loaded = new_manager(plugin_core_module).load_transaction(
        transaction.operation_id
    )
    upgraded = json.loads(journal_path.read_text(encoding="utf-8"))

    assert loaded.live_folder == "ExamplePlugin"
    assert upgraded["schema_version"] == 3
    assert upgraded["live_folder"] == "ExamplePlugin"
    assert upgraded["created_at"] == previous["created_at"]
    assert upgraded["updated_at"] == previous["updated_at"]


def test_startup_cleans_v2_stale_pre_activation_payloads(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    previous = previous_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    previous["phase"] = "stale_target"
    previous["rollback_from"] = ""
    previous["error"] = (
        "Installed state no longer matches expected_current."
    )
    journal_path.write_text(json.dumps(previous), encoding="utf-8")

    new_manager(plugin_core_module).recover_pending()

    recovered = manager.load_transaction(transaction.operation_id)
    upgraded = json.loads(journal_path.read_text(encoding="utf-8"))
    assert recovered.phase == "stale_target"
    assert_old_live(recovered)
    assert list(Path(recovered.paths.staging_root).iterdir()) == []
    assert list(Path(recovered.paths.backup_root).iterdir()) == []
    assert upgraded["schema_version"] == 3
    assert upgraded["live_folder"] == "ExamplePlugin"


def test_release_transaction_malformed_v1_journal_is_not_rewritten(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    legacy = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    legacy["phase"] = "invented_phase"
    original = (json.dumps(legacy, sort_keys=True) + "\n").encode("utf-8")
    journal_path.write_bytes(original)

    with pytest.raises(ValueError, match="phase is unsupported"):
        new_manager(plugin_core_module).load_transaction(
            transaction.operation_id
        )

    assert journal_path.read_bytes() == original
    assert not Path(str(journal_path) + ".tmp").exists()


def test_restart_recovery_upgrades_v1_journal_before_rollback(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    legacy = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(legacy), encoding="utf-8")

    new_manager(plugin_core_module).recover_pending()

    recovered = manager.load_transaction(transaction.operation_id)
    document = json.loads(journal_path.read_text(encoding="utf-8"))
    assert recovered.phase == "rolled_back"
    assert_old_live(recovered)
    assert document["schema_version"] == 3
    assert document["package_id"] == "ExamplePlugin"
    assert document["live_folder"] == "ExamplePlugin"
    assert "plugin_key" not in document


@pytest.mark.parametrize(
    "phase",
    [
        "created",
        "staged_verified",
        "dependency_confirmation_required",
        "dependencies_staged",
        "dependency_blocked",
    ],
)
def test_startup_repairs_v1_pre_activation_journal_with_missing_staging_parent(
    plugin_core_module,
    tmp_path,
    phase,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_pre_activation_transaction(
        manager,
        plugins_dir,
        manager_dir,
        phase,
    )
    journal_path = Path(transaction.paths.journal)
    document = json.loads(journal_path.read_text(encoding="utf-8"))
    expected_error = document["error"] or (
        "Recovered an interrupted pre-activation release operation."
    )
    journal_path.write_text(
        json.dumps(legacy_transaction_document(document)),
        encoding="utf-8",
    )
    staging_parent = Path(transaction.paths.staged_code).parent
    backup_parent = Path(transaction.paths.backup_code).parent
    shutil.rmtree(staging_parent)

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.finalize_startup()
    recovered = recovered_manager.load_transaction(
        transaction.operation_id
    )
    upgraded = json.loads(journal_path.read_text(encoding="utf-8"))

    assert recovered.phase == "rolled_back"
    assert recovered.rollback_from == phase
    assert recovered.error == expected_error
    assert staging_parent.is_dir()
    assert backup_parent.is_dir()
    assert list(staging_parent.iterdir()) == []
    assert list(backup_parent.iterdir()) == []
    assert upgraded["schema_version"] == 3
    assert upgraded["package_id"] == "ExamplePlugin"
    assert upgraded["live_folder"] == "ExamplePlugin"
    assert "plugin_key" not in upgraded
    journal_after_recovery = journal_path.read_bytes()

    recovered_manager.recover_pending()

    assert journal_path.read_bytes() == journal_after_recovery


def test_legacy_pre_activation_repair_marks_changed_live_state_stale(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_pre_activation_transaction(
        manager,
        plugins_dir,
        manager_dir,
        "created",
    )
    journal_path = Path(transaction.paths.journal)
    document = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(document), encoding="utf-8")
    staging_parent = Path(transaction.paths.staged_code).parent
    backup_parent = Path(transaction.paths.backup_code).parent
    shutil.rmtree(staging_parent)
    write_marker(transaction.paths.live_code, "later install")

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.finalize_startup()
    recovered = recovered_manager.load_transaction(
        transaction.operation_id
    )

    assert recovered.phase == "stale_target"
    assert recovered.rollback_from == "created"
    assert recovered.error == (
        "Installed state changed after an interrupted pre-activation "
        "release operation."
    )
    assert read_marker(transaction.paths.live_code) == "later install"
    assert staging_parent.is_dir()
    assert backup_parent.is_dir()
    assert list(staging_parent.iterdir()) == []
    assert list(backup_parent.iterdir()) == []


def test_legacy_pre_activation_repair_resumes_after_container_creation_crash(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_pre_activation_transaction(
        manager,
        plugins_dir,
        manager_dir,
        "created",
    )
    journal_path = Path(transaction.paths.journal)
    document = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(document), encoding="utf-8")
    staging_parent = Path(transaction.paths.staged_code).parent
    shutil.rmtree(staging_parent)
    real_mkdir = plugin_core_module.os.mkdir

    def crash_before_container_creation(path, *args, **kwargs):
        if Path(path) == staging_parent:
            raise SimulatedCrash("container creation")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(
        plugin_core_module.os,
        "mkdir",
        crash_before_container_creation,
    )
    with pytest.raises(SimulatedCrash, match="container creation"):
        new_manager(plugin_core_module).finalize_startup()

    interrupted = json.loads(journal_path.read_text(encoding="utf-8"))
    assert interrupted["schema_version"] == 3
    assert interrupted["phase"] == "rollback_pending"
    assert interrupted["rollback_from"] == "created"
    assert interrupted["error"] == (
        "Recovered an interrupted pre-activation release operation."
    )
    assert not staging_parent.exists()

    monkeypatch.setattr(plugin_core_module.os, "mkdir", real_mkdir)
    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.finalize_startup()
    recovered = recovered_manager.load_transaction(
        transaction.operation_id
    )

    assert recovered.phase == "rolled_back"
    assert recovered.rollback_from == "created"
    assert recovered.error == interrupted["error"]
    assert staging_parent.is_dir()


def test_changed_state_repair_resumes_after_staging_root_was_recreated(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_pre_activation_transaction(
        manager,
        plugins_dir,
        manager_dir,
        "created",
    )
    journal_path = Path(transaction.paths.journal)
    document = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(document), encoding="utf-8")
    staging_parent = Path(transaction.paths.staged_code).parent
    shutil.rmtree(staging_parent)
    write_marker(transaction.paths.live_code, "later install")

    interrupted_manager = new_manager(plugin_core_module)

    def crash_before_payload_discard(_transaction):
        raise SimulatedCrash("payload discard")

    monkeypatch.setattr(
        interrupted_manager,
        "_discard_transaction_payloads",
        crash_before_payload_discard,
    )
    with pytest.raises(SimulatedCrash, match="payload discard"):
        interrupted_manager.finalize_startup()

    interrupted = json.loads(journal_path.read_text(encoding="utf-8"))
    assert interrupted["schema_version"] == 3
    assert interrupted["phase"] == "rollback_pending"
    assert interrupted["rollback_from"] == "created"
    assert staging_parent.is_dir()
    assert read_marker(transaction.paths.live_code) == "later install"

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.finalize_startup()
    recovered = recovered_manager.load_transaction(
        transaction.operation_id
    )
    journal_after_recovery = journal_path.read_bytes()

    assert recovered.phase == "stale_target"
    assert recovered.rollback_from == "created"
    assert recovered.error == interrupted["error"]
    assert read_marker(transaction.paths.live_code) == "later install"
    assert list(staging_parent.iterdir()) == []

    recovered_manager.finalize_startup()

    assert journal_path.read_bytes() == journal_after_recovery


def test_missing_active_transaction_path_fails_closed_only_for_owning_package(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    def crash_after_code_backup(phase, _transaction):
        if phase == "code_backed_up":
            raise SimulatedCrash(phase)

    manager.fault_injector = crash_after_code_backup
    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)

    journal_path = Path(transaction.paths.journal)
    document = json.loads(journal_path.read_text(encoding="utf-8"))
    assert document["phase"] == "code_backed_up"
    journal_path.write_text(
        json.dumps(legacy_transaction_document(document)),
        encoding="utf-8",
    )
    journal_before = journal_path.read_bytes()
    staging_parent = Path(transaction.paths.staged_code).parent
    backup_code = Path(transaction.paths.backup_code)
    backup_before = {
        path.relative_to(backup_code).as_posix(): path.read_bytes()
        for path in backup_code.rglob("*")
        if path.is_file()
    }
    live_code_before = Path(transaction.paths.live_code).exists()
    shutil.rmtree(staging_parent)

    unrelated = manager.plugin_lifecycle_state("UnrelatedPlugin")

    assert unrelated["rollback_available"] is False
    assert unrelated["restart_pending"] is False
    with pytest.raises(ValueError, match="path must be a real directory"):
        manager.plugin_lifecycle_state("ExamplePlugin")
    with pytest.raises(ValueError, match="path must be a real directory"):
        new_manager(plugin_core_module).recover_pending()
    assert not staging_parent.exists()
    assert Path(transaction.paths.live_code).exists() is live_code_before
    assert {
        path.relative_to(backup_code).as_posix(): path.read_bytes()
        for path in backup_code.rglob("*")
        if path.is_file()
    } == backup_before
    assert journal_path.read_bytes() == journal_before


def test_legacy_recovery_never_replaces_an_unsafe_staging_path(
    plugin_core_module,
    tmp_path,
):
    manager, _plugins_dir, _manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )
    journal_path = Path(transaction.paths.journal)
    document = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(document), encoding="utf-8")
    journal_before = journal_path.read_bytes()
    staging_parent = Path(transaction.paths.staged_code).parent
    shutil.rmtree(staging_parent)
    staging_parent.write_text("untrusted replacement", encoding="utf-8")

    with pytest.raises(ValueError, match="path must be a real directory"):
        new_manager(plugin_core_module).recover_pending()

    assert staging_parent.read_text(encoding="utf-8") == (
        "untrusted replacement"
    )
    assert journal_path.read_bytes() == journal_before


def test_legacy_recovery_does_not_discard_an_unexpected_backup(
    plugin_core_module,
    tmp_path,
):
    manager, _plugins_dir, _manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )
    journal_path = Path(transaction.paths.journal)
    document = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(document), encoding="utf-8")
    journal_before = journal_path.read_bytes()
    staging_parent = Path(transaction.paths.staged_code).parent
    backup_parent = Path(transaction.paths.backup_code).parent
    shutil.rmtree(staging_parent)
    write_marker(backup_parent / "unexpected", "retained backup")

    with pytest.raises(RuntimeError, match="backup is not empty"):
        new_manager(plugin_core_module).recover_pending()

    assert not staging_parent.exists()
    assert read_marker(backup_parent / "unexpected") == "retained backup"
    assert journal_path.read_bytes() == journal_before


def test_release_transaction_refuses_activation_before_dependencies_are_staged(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        stage_dependencies=False,
    )

    with pytest.raises(RuntimeError, match="dependencies_staged"):
        manager.activate(transaction.operation_id)

    unchanged = manager.load_transaction(transaction.operation_id)
    assert unchanged.phase == "staged_verified"
    assert unchanged.dependency_snapshot is None
    assert_old_live(unchanged)


def test_startup_recovery_before_activation_preserves_current_install(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    install_current_release(plugins_dir, manager_dir)
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation="release_update",
        expected_current=expected_current(),
        target=target_release(),
    )

    new_manager(plugin_core_module).recover_pending()

    recovered = manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "rolled_back"
    assert_old_live(recovered)


def test_pre_activation_abort_keeps_journal_paths_valid_and_is_idempotent(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    staging_parent = Path(transaction.paths.staged_code).parent
    backup_parent = Path(transaction.paths.backup_code).parent

    aborted = manager.abort(transaction.operation_id, "download failed")

    assert aborted.phase == "rolled_back"
    assert aborted.error == "download failed"
    assert staging_parent.is_dir()
    assert backup_parent.is_dir()
    for leaf in (
        aborted.paths.staged_code,
        aborted.paths.staged_dependencies,
        aborted.paths.backup_code,
        aborted.paths.backup_dependencies,
    ):
        assert not Path(leaf).exists()
    assert Path(aborted.paths.journal).is_file()
    assert manager.load_transaction(aborted.operation_id).phase == "rolled_back"
    assert manager.abort(aborted.operation_id, "retry").phase == "rolled_back"
    assert_old_live(aborted)


def test_created_release_install_abort_cleans_staging_without_losing_journal(
    plugin_core_module,
    tmp_path,
):
    manager, _plugins_dir, _manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )
    write_marker(transaction.paths.staged_code, "partial download")
    staging_parent = Path(transaction.paths.staged_code).parent
    archive_path = staging_parent / "artifact.zip"
    extraction_path = staging_parent / "extracted"
    archive_path.write_bytes(b"partial archive")
    write_marker(extraction_path, "partially extracted")

    aborted = manager.abort(
        transaction.operation_id,
        "staged identity mismatch",
    )

    assert aborted.phase == "rolled_back"
    assert Path(aborted.paths.staged_code).parent.is_dir()
    assert Path(aborted.paths.backup_code).parent.is_dir()
    assert not Path(aborted.paths.staged_code).exists()
    assert list(Path(aborted.paths.staged_code).parent.iterdir()) == []
    assert list(Path(aborted.paths.backup_code).parent.iterdir()) == []
    assert Path(aborted.paths.journal).is_file()
    assert manager.load_transaction(aborted.operation_id).phase == "rolled_back"


def test_pre_activation_abort_retry_preserves_original_error(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    original_remove = manager._remove_transaction_path
    calls = []

    def interrupt_second_leaf(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("injected cleanup interruption")
        original_remove(path)

    monkeypatch.setattr(
        manager,
        "_remove_transaction_path",
        interrupt_second_leaf,
    )
    with pytest.raises(OSError, match="injected cleanup interruption"):
        manager.abort(transaction.operation_id, "download failed")

    interrupted = manager.load_transaction(transaction.operation_id)
    assert interrupted.phase == "rollback_pending"
    assert interrupted.rollback_from == "dependencies_staged"
    assert interrupted.error == "download failed"
    assert Path(interrupted.paths.staged_code).parent.is_dir()
    assert Path(interrupted.paths.backup_code).parent.is_dir()

    monkeypatch.setattr(
        manager,
        "_remove_transaction_path",
        original_remove,
    )
    retried_abort = manager.abort(
        transaction.operation_id,
        "retry cleanup",
    )
    assert retried_abort.phase == "rolled_back"
    assert retried_abort.error == "download failed"
    assert_old_live(retried_abort)


def test_startup_recovery_resumes_interrupted_pre_activation_abort(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    original_remove = manager._remove_transaction_path
    calls = []

    def interrupt_second_leaf(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("injected cleanup interruption")
        original_remove(path)

    monkeypatch.setattr(
        manager,
        "_remove_transaction_path",
        interrupt_second_leaf,
    )
    with pytest.raises(OSError, match="injected cleanup interruption"):
        manager.abort(transaction.operation_id, "download failed")

    interrupted = manager.load_transaction(transaction.operation_id)
    assert interrupted.phase == "rollback_pending"
    assert interrupted.rollback_from == "dependencies_staged"
    assert interrupted.error == "download failed"

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()
    retried = recovered_manager.load_transaction(transaction.operation_id)

    assert retried.phase == "rolled_back"
    assert retried.error == "download failed"
    assert recovered_manager.abort(
        retried.operation_id,
        "retry",
    ).phase == "rolled_back"
    assert_old_live(retried)


def test_activation_rejects_staged_metadata_outside_pinned_target(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    metadata_path = Path(transaction.paths.staged_code) / ".pypluginstore.json"
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    document["release_id"] = "github:owner/example-plugin:v2.0.0-repacked"
    metadata_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="pinned target"):
        manager.activate(transaction.operation_id)

    rejected = manager.load_transaction(transaction.operation_id)
    assert rejected.phase == "rolled_back"
    assert_old_live(rejected)


def test_activation_rejects_staged_code_changed_after_verification(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    (Path(transaction.paths.staged_code) / "plugin.py").write_text(
        "# changed after verification\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pinned target"):
        manager.activate(transaction.operation_id)

    rejected = manager.load_transaction(transaction.operation_id)
    assert rejected.phase == "rolled_back"
    assert_old_live(rejected)


def test_activation_rejects_code_and_self_reported_inventory_changed_together(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    staged_code = Path(transaction.paths.staged_code)
    (staged_code / "plugin.py").write_text(
        "# changed with matching self-reported hash\n",
        encoding="utf-8",
    )
    metadata_path = staged_code / ".pypluginstore.json"
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    document["artifact_files"] = artifact_inventory(staged_code)
    metadata_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="pinned target"):
        manager.activate(transaction.operation_id)

    rejected = manager.load_transaction(transaction.operation_id)
    assert rejected.phase == "rolled_back"
    assert_old_live(rejected)


def test_activation_rejects_dependencies_changed_after_staging(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    (Path(transaction.paths.staged_dependencies) / "marker.txt").write_text(
        "changed dependencies",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pinned snapshot"):
        manager.activate(transaction.operation_id)

    rejected = manager.load_transaction(transaction.operation_id)
    assert rejected.phase == "rolled_back"
    assert_old_live(rejected)


def test_activation_refuses_changed_live_dependencies(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    (Path(transaction.paths.live_dependencies) / "marker.txt").write_text(
        "newer live dependency state",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="expected_current"):
        manager.activate(transaction.operation_id)

    stale = manager.load_transaction(transaction.operation_id)
    assert stale.phase == "stale_target"
    assert stale.rollback_from == "dependencies_staged"
    assert read_marker(stale.paths.live_code) == "old-code"
    assert read_marker(stale.paths.live_dependencies) == (
        "newer live dependency state"
    )
    assert list(Path(stale.paths.staging_root).iterdir()) == []
    assert list(Path(stale.paths.backup_root).iterdir()) == []

    aborted = manager.abort(
        transaction.operation_id,
        "A retry reached the same stale target.",
    )

    assert aborted.phase == "stale_target"
    assert aborted.error == stale.error


def test_dependency_cache_churn_does_not_invalidate_release_activation(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    live_cache = (
        Path(transaction.paths.live_dependencies)
        / "__pycache__"
    )
    staged_cache = (
        Path(transaction.paths.staged_dependencies)
        / "__pycache__"
    )
    live_cache.mkdir(parents=True)
    staged_cache.mkdir(parents=True)
    (live_cache / "module.pyc").write_bytes(b"runtime cache v1")
    (staged_cache / "module.pyc").write_bytes(b"staged cache")

    activated = manager.activate(transaction.operation_id)

    assert activated.phase == "restart_pending"
    (Path(activated.paths.live_dependencies) / "__pycache__").mkdir(
        parents=True,
        exist_ok=True,
    )
    (
        Path(activated.paths.live_dependencies)
        / "__pycache__"
        / "module.pyc"
    ).write_bytes(b"runtime cache v2")

    completed = new_manager(plugin_core_module).mark_release_managed(
        transaction.operation_id
    )

    assert completed.phase == "release_managed"
    assert_new_live(completed)


def test_release_code_cache_churn_does_not_invalidate_restart_completion(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    activated = manager.activate(transaction.operation_id)
    cache = Path(activated.paths.live_code) / "__pycache__"
    cache.mkdir()
    (cache / "plugin.cpython-runtime.pyc").write_bytes(
        b"runtime cache"
    )

    completed = new_manager(plugin_core_module).mark_release_managed(
        transaction.operation_id
    )

    assert completed.phase == "release_managed"
    assert_new_live(completed)


def test_activation_never_trusts_a_preseeded_backup_leaf(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    write_marker(transaction.paths.backup_code, "untrusted-backup")

    with pytest.raises(ValueError, match="backup destination"):
        manager.activate(transaction.operation_id)

    unchanged = manager.load_transaction(transaction.operation_id)
    assert unchanged.phase == "dependencies_staged"
    assert_old_live(unchanged)


@pytest.mark.parametrize(
    ("operation", "operation_expected_current"),
    [
        pytest.param(
            "release_install",
            {"management_mode": "absent"},
            id="new-install-binds-absence",
        ),
        pytest.param(
            "release_update",
            expected_current(),
            id="release-update-binds-installed-release",
        ),
        pytest.param(
            "release_migration",
            migration_expected_current(),
            id="git-migration-binds-content-snapshot",
        ),
    ],
)
def test_release_transaction_records_operation_specific_expected_current_state(
    plugin_core_module,
    tmp_path,
    operation,
    operation_expected_current,
):
    manager, _, _ = make_manager(plugin_core_module, tmp_path)

    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation=operation,
        expected_current=operation_expected_current,
        target=target_release(),
    )
    document = json.loads(
        Path(transaction.paths.journal).read_text(encoding="utf-8")
    )

    assert transaction.operation == operation
    assert transaction.expected_current == operation_expected_current
    assert document["operation"] == operation
    assert document["expected_current"] == operation_expected_current


@pytest.mark.skipif(GIT is None, reason="Git is required")
@pytest.mark.parametrize(
    "change_kind",
    [
        pytest.param("tracked", id="same-head-tracked-change"),
        pytest.param("untracked", id="same-head-untracked-file"),
    ],
)
def test_git_migration_activation_rejects_same_head_content_changes(
    plugin_core_module,
    tmp_path,
    change_kind,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction, live_code, installed_commit, _preflight = (
        prepare_migration_transaction(
            plugin_core_module,
            manager,
            plugins_dir,
            manager_dir,
        )
    )
    if change_kind == "tracked":
        (live_code / "README.md").write_text(
            "changed after approval\n",
            encoding="utf-8",
        )
    else:
        runtime_file = live_code / "runtime" / "state.json"
        runtime_file.parent.mkdir()
        runtime_file.write_text('{"counter": 1}\n', encoding="utf-8")

    assert head_commit(live_code) == installed_commit

    with pytest.raises(RuntimeError, match="expected_current"):
        manager.activate(transaction.operation_id)

    stale = manager.load_transaction(transaction.operation_id)
    assert stale.phase == "stale_target"
    assert stale.rollback_from == "dependencies_staged"
    assert Path(stale.paths.live_code) == live_code
    assert (live_code / ".git").is_dir()
    assert head_commit(live_code) == installed_commit
    assert not Path(stale.paths.backup_code).exists()
    assert list(Path(stale.paths.staging_root).iterdir()) == []
    assert list(Path(stale.paths.backup_root).iterdir()) == []


@pytest.mark.skipif(GIT is None, reason="Git is required")
def test_migration_backup_revalidation_restores_checkout_changed_during_rename(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction, live_code, installed_commit, _preflight = (
        prepare_migration_transaction(
            plugin_core_module,
            manager,
            plugins_dir,
            manager_dir,
        )
    )
    changed_contents = "changed inside rename window\n"

    def change_moved_checkout(phase, current_transaction):
        if phase == "code_backed_up":
            Path(
                current_transaction.paths.backup_code,
                "README.md",
            ).write_text(changed_contents, encoding="utf-8")

    manager.fault_injector = change_moved_checkout

    with pytest.raises(
        RuntimeError,
        match="changed after migration approval",
    ):
        manager.activate(transaction.operation_id)

    stale = manager.load_transaction(transaction.operation_id)
    assert stale.phase == "stale_target"
    assert stale.rollback_from == "dependencies_staged"
    assert Path(stale.paths.live_code) == live_code
    assert live_code.is_dir()
    assert (live_code / ".git").is_dir()
    assert (live_code / "README.md").read_text(encoding="utf-8") == (
        changed_contents
    )
    assert head_commit(live_code) == installed_commit
    assert not Path(stale.paths.backup_code).exists()
    assert read_marker(stale.paths.live_dependencies) == "old-dependencies"
    assert not Path(stale.paths.backup_dependencies).exists()
    assert list(Path(stale.paths.staging_root).iterdir()) == []
    assert list(Path(stale.paths.backup_root).iterdir()) == []


@pytest.mark.skipif(GIT is None, reason="Git is required")
def test_migration_revalidation_preserves_both_recreated_live_and_backup(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction, live_code, _installed_commit, _preflight = (
        prepare_migration_transaction(
            plugin_core_module,
            manager,
            plugins_dir,
            manager_dir,
        )
    )

    def recreate_live_after_backup(phase, current_transaction):
        if phase != "code_backed_up":
            return
        Path(current_transaction.paths.backup_code, "README.md").write_text(
            "changed retained checkout\n",
            encoding="utf-8",
        )
        recreated = Path(current_transaction.paths.live_code)
        recreated.mkdir()
        (recreated / "plugin.py").write_text(
            "# concurrently recreated plugin\n",
            encoding="utf-8",
        )

    manager.fault_injector = recreate_live_after_backup

    with pytest.raises(RuntimeError, match="changed after migration approval"):
        manager.activate(transaction.operation_id)

    blocked = manager.load_transaction(transaction.operation_id)
    assert blocked.phase == "rollback_pending"
    assert blocked.rollback_from == "code_backed_up"
    assert Path(blocked.paths.live_code, "plugin.py").is_file()
    assert Path(blocked.paths.backup_code, ".git").is_dir()
    assert Path(blocked.paths.staged_code).is_dir()

    with pytest.raises(RuntimeError, match="could not restore"):
        new_manager(plugin_core_module).recover_pending()

    assert Path(live_code, "plugin.py").is_file()
    assert Path(blocked.paths.backup_code, ".git").is_dir()
    assert Path(blocked.paths.staged_code).is_dir()


@pytest.mark.skipif(GIT is None, reason="Git is required")
def test_migration_restore_recovers_crash_after_backup_returns_live(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction, live_code, _installed_commit, _preflight = (
        prepare_migration_transaction(
            plugin_core_module,
            manager,
            plugins_dir,
            manager_dir,
        )
    )
    changed_contents = "changed during restore\n"

    def crash_after_restore(phase, current_transaction):
        if phase == "code_backed_up":
            Path(
                current_transaction.paths.backup_code,
                "README.md",
            ).write_text(changed_contents, encoding="utf-8")
        elif phase == "migration_restore_completed":
            raise SimulatedCrash(phase)

    manager.fault_injector = crash_after_restore

    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)

    interrupted = manager.load_transaction(transaction.operation_id)
    assert interrupted.phase == "migration_restore_completed"
    assert live_code.is_dir()
    assert not Path(interrupted.paths.backup_code).exists()

    new_manager(plugin_core_module).recover_pending()

    recovered = manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "stale_target"
    assert recovered.rollback_from == "dependencies_staged"
    assert (live_code / "README.md").read_text(encoding="utf-8") == (
        changed_contents
    )
    assert (live_code / ".git").is_dir()
    assert list(Path(recovered.paths.staging_root).iterdir()) == []
    assert list(Path(recovered.paths.backup_root).iterdir()) == []


@pytest.mark.parametrize(
    ("field", "invalid_value", "error"),
    [
        ("phase", "invented_phase", "phase is unsupported"),
        ("schema_version", True, "schema is unsupported"),
        ("target.release_revision", 0, "release_revision"),
    ],
)
def test_release_transaction_revalidates_loaded_journal_descriptors(
    plugin_core_module,
    tmp_path,
    field,
    invalid_value,
    error,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    document = json.loads(journal_path.read_text(encoding="utf-8"))
    if field == "target.release_revision":
        document["target"]["release_revision"] = invalid_value
    else:
        document[field] = invalid_value
    journal_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        manager.load_transaction(transaction.operation_id)


def test_release_transaction_rejects_live_folder_changed_without_paths(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    document = json.loads(journal_path.read_text(encoding="utf-8"))
    document["live_folder"] = "OtherPlugin"
    journal_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="paths are not manager-owned"):
        manager.load_transaction(transaction.operation_id)


def test_release_transaction_rejects_unsafe_folder_and_path_tampering(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    document = json.loads(journal_path.read_text(encoding="utf-8"))
    document["live_folder"] = "../OtherPlugin"
    document["paths"]["live_code"] = str(plugins_dir.parent / "OtherPlugin")
    journal_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="live_folder"):
        manager.load_transaction(transaction.operation_id)


def test_restore_phase_requires_migration_operation_and_origin(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    journal_path = Path(transaction.paths.journal)
    document = json.loads(journal_path.read_text(encoding="utf-8"))
    document["phase"] = "migration_restore_pending"
    document["rollback_from"] = "code_backed_up"
    document["error"] = "Interrupted restore."
    journal_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="descriptor is inconsistent"):
        manager.load_transaction(transaction.operation_id)


@pytest.mark.skipif(GIT is None, reason="Git is required")
def test_v2_journal_cannot_claim_a_v3_migration_restore_phase(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction, _live_code, _commit, _preflight = (
        prepare_migration_transaction(
            plugin_core_module,
            manager,
            plugins_dir,
            manager_dir,
        )
    )
    journal_path = Path(transaction.paths.journal)
    document = previous_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    document["phase"] = "migration_restore_pending"
    document["rollback_from"] = "code_backed_up"
    document["error"] = "Impossible legacy restore."
    journal_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="current schema"):
        manager.load_transaction(transaction.operation_id)


def test_release_transaction_fsyncs_journal_before_each_directory_rename(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    events = []
    rename_phases = []
    real_replace = plugin_core_module.os.replace

    def record_fsync(file_descriptor):
        events.append("fsync")

    def record_replace(source, destination):
        source_path = Path(source)
        if source_path.is_dir():
            journal = json.loads(
                Path(transaction.paths.journal).read_text(encoding="utf-8")
            )
            events.append("directory_rename")
            rename_phases.append(journal["phase"])
        return real_replace(source, destination)

    monkeypatch.setattr(plugin_core_module.os, "fsync", record_fsync)
    monkeypatch.setattr(plugin_core_module.os, "replace", record_replace)

    manager.activate(transaction.operation_id)

    assert rename_phases == [
        "code_backup_pending",
        "dependencies_backup_pending",
        "dependencies_activation_pending",
        "code_activation_pending",
    ]
    previous_rename = -1
    for index, event in enumerate(events):
        if event != "directory_rename":
            continue
        assert "fsync" in events[previous_rename + 1 : index]
        previous_rename = index


def test_release_tree_fsync_uses_write_descriptor_on_windows(
    plugin_core_module, tmp_path, monkeypatch
):
    manager = new_manager(plugin_core_module)
    tree = tmp_path / "release-tree"
    write_marker(tree, "staged")
    marker = os.fspath(tree / "marker.txt")
    opened_flags = []
    real_open = plugin_core_module.os.open

    def record_open(path, flags, *args):
        if os.fspath(path) == marker:
            opened_flags.append(flags)
        return real_open(path, flags, *args)

    monkeypatch.setattr(plugin_core_module.os, "open", record_open)
    monkeypatch.setattr(plugin_core_module.os, "name", "nt")

    manager._sync_staged_tree(os.fspath(tree), "Release tree")

    assert opened_flags
    access_mask = plugin_core_module.os.O_WRONLY | plugin_core_module.os.O_RDWR
    assert all(
        flags & access_mask == plugin_core_module.os.O_RDWR
        for flags in opened_flags
    )


def test_release_transaction_activates_code_and_dependency_snapshots(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    activated = manager.activate(transaction.operation_id)

    assert activated.phase == "restart_pending"
    assert_new_live(activated)
    assert read_marker(activated.paths.backup_code) == "old-code"
    assert read_marker(activated.paths.backup_dependencies) == "old-dependencies"
    assert (Path(activated.paths.backup_code) / "old-only.py").is_file()
    assert (
        Path(activated.paths.backup_dependencies) / "old-only.py"
    ).is_file()
    assert not Path(activated.paths.staged_code).exists()
    assert not Path(activated.paths.staged_dependencies).exists()


@pytest.mark.parametrize("failure_rename", [1, 2, 3, 4])
def test_release_transaction_immediately_rolls_back_every_rename_failure(
    plugin_core_module, tmp_path, monkeypatch, failure_rename
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    real_replace = plugin_core_module.os.replace
    directory_rename_count = 0

    def fail_one_directory_rename(source, destination):
        nonlocal directory_rename_count
        if Path(source).is_dir():
            directory_rename_count += 1
            if directory_rename_count == failure_rename:
                raise OSError("injected rename failure {}".format(failure_rename))
        return real_replace(source, destination)

    monkeypatch.setattr(
        plugin_core_module.os, "replace", fail_one_directory_rename
    )

    with pytest.raises(OSError, match="injected rename failure"):
        manager.activate(transaction.operation_id)

    rolled_back = manager.load_transaction(transaction.operation_id)
    assert rolled_back.phase == "rolled_back"
    assert "injected rename failure" in rolled_back.error
    assert_old_live(rolled_back)


@pytest.mark.parametrize(
    "crash_phase,expected_recovery_phase,expected_marker",
    [
        ("code_backed_up", "rolled_back", "old-code"),
        ("dependencies_backed_up", "rolled_back", "old-code"),
        ("dependencies_activated", "rolled_back", "old-code"),
        ("release_activated", "restart_pending", "new-code"),
    ],
)
def test_release_transaction_startup_recovery_is_idempotent_at_each_boundary(
    plugin_core_module,
    tmp_path,
    crash_phase,
    expected_recovery_phase,
    expected_marker,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    def crash_after_phase(phase, current_transaction):
        if phase == crash_phase:
            raise SimulatedCrash(phase)

    manager.fault_injector = crash_after_phase

    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)
    first_journal = Path(recovered.paths.journal).read_bytes()
    first_code = read_marker(recovered.paths.live_code)
    first_dependencies = read_marker(recovered.paths.live_dependencies)

    assert recovered.phase == expected_recovery_phase
    assert first_code == expected_marker
    assert first_dependencies == "old-dependencies"

    recovered_manager.recover_pending()

    assert Path(recovered.paths.journal).read_bytes() == first_journal
    assert read_marker(recovered.paths.live_code) == first_code
    assert read_marker(recovered.paths.live_dependencies) == first_dependencies


def test_forward_recovery_accepts_and_upgrades_v1_metadata_digest(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    def crash_after_release_activation(phase, _transaction):
        if phase == "release_activated":
            raise SimulatedCrash(phase)

    manager.fault_injector = crash_after_release_activation
    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)

    metadata_path = Path(transaction.paths.live_code) / ".pypluginstore.json"
    legacy_metadata = legacy_install_metadata_document(
        json.loads(metadata_path.read_text(encoding="utf-8"))
    )
    metadata_path.write_text(json.dumps(legacy_metadata), encoding="utf-8")
    legacy_digest = hashlib.sha256(
        json.dumps(
            legacy_metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    journal_path = Path(transaction.paths.journal)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["staged_snapshot"]["install_metadata_sha256"] = legacy_digest
    journal_path.write_text(
        json.dumps(legacy_transaction_document(journal)), encoding="utf-8"
    )

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)
    upgraded_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    upgraded_journal = json.loads(journal_path.read_text(encoding="utf-8"))

    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)
    assert upgraded_metadata["schema"] == 3
    assert upgraded_metadata["package_id"] == "ExamplePlugin"
    assert "plugin_key" not in upgraded_metadata
    assert upgraded_journal["schema_version"] == 3
    assert upgraded_journal["package_id"] == "ExamplePlugin"
    assert upgraded_journal["live_folder"] == "ExamplePlugin"
    assert "plugin_key" not in upgraded_journal


def test_recovery_never_claims_rollback_when_required_backup_is_missing(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    def crash_after_code_backup(phase, _transaction):
        if phase == "code_backed_up":
            raise SimulatedCrash(phase)

    manager.fault_injector = crash_after_code_backup
    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)
    shutil.rmtree(transaction.paths.backup_code)

    with pytest.raises(RuntimeError, match="could not restore"):
        new_manager(plugin_core_module).recover_pending()

    blocked = manager.load_transaction(transaction.operation_id)
    assert blocked.phase == "rollback_pending"
    assert "could not restore" in blocked.error


def test_forward_recovery_requires_the_activated_dependency_snapshot(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)

    def crash_after_release_activation(phase, _transaction):
        if phase == "release_activated":
            raise SimulatedCrash(phase)

    manager.fault_injector = crash_after_release_activation
    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)
    shutil.rmtree(transaction.paths.live_dependencies)

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)

    assert recovered.phase == "rolled_back"
    assert_old_live(recovered)


def test_release_transaction_retains_known_good_backup_until_completion(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(transaction.operation_id)

    completed = manager.mark_release_managed(transaction.operation_id)
    reloaded = new_manager(plugin_core_module).load_transaction(
        transaction.operation_id
    )

    assert completed.phase == "release_managed"
    assert reloaded.phase == "release_managed"
    assert read_marker(reloaded.paths.backup_code) == "old-code"
    assert read_marker(reloaded.paths.backup_dependencies) == "old-dependencies"


def test_startup_never_rolls_back_a_newer_shared_dependency_generation(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    first = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(first.operation_id)
    manager.mark_release_managed(first.operation_id)

    second = prepare_other_release_install(manager)
    manager.activate(second.operation_id)

    first_journal = Path(first.paths.journal)
    first_document = json.loads(
        first_journal.read_text(encoding="utf-8")
    )
    first_document["phase"] = "restart_pending"
    first_document["error"] = ""
    first_journal.write_text(
        json.dumps(first_document),
        encoding="utf-8",
    )

    finalized = new_manager(plugin_core_module).finalize_startup()

    first_after = manager.load_transaction(first.operation_id)
    second_after = manager.load_transaction(second.operation_id)
    assert {transaction.operation_id for transaction in finalized} == {
        first.operation_id,
        second.operation_id,
    }
    assert first_after.phase == "release_managed"
    assert "superseded" in first_after.error
    assert second_after.phase == "release_managed"
    assert read_marker(second_after.paths.live_dependencies) == (
        "newer-shared-dependencies"
    )
    assert read_marker(first_after.paths.live_code) == "new-code"
    assert read_marker(second_after.paths.live_code) == "other-code"
    assert (
        manager.plugin_lifecycle_state(first.plugin_key)[
            "rollback_available"
        ]
        is False
    )


def test_recovery_never_rolls_back_a_newer_active_dependency_generation(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    first = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        operation_id="a-older-operation",
    )
    manager.activate(first.operation_id)
    manager.mark_release_managed(first.operation_id)
    second = prepare_other_release_install(
        manager,
        operation_id="z-newer-operation",
    )
    manager.activate(second.operation_id)

    for transaction, created_at in (
        (first, "2026-07-23T10:00:00Z"),
        (second, "2026-07-23T10:01:00Z"),
    ):
        journal_path = Path(transaction.paths.journal)
        document = json.loads(journal_path.read_text(encoding="utf-8"))
        document["phase"] = "release_activated"
        document["created_at"] = created_at
        document["updated_at"] = created_at
        journal_path.write_text(json.dumps(document), encoding="utf-8")

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()

    first_recovered = manager.load_transaction(first.operation_id)
    second_recovered = manager.load_transaction(second.operation_id)
    assert first_recovered.phase == "restart_pending"
    assert "newer shared dependencies" in first_recovered.error
    assert second_recovered.phase == "restart_pending"
    assert read_marker(second.paths.live_dependencies) == (
        "newer-shared-dependencies"
    )

    recovered_manager.finalize_startup()

    assert manager.load_transaction(first.operation_id).phase == (
        "release_managed"
    )
    assert manager.load_transaction(second.operation_id).phase == (
        "release_managed"
    )
    assert read_marker(second.paths.live_dependencies) == (
        "newer-shared-dependencies"
    )


def test_queued_recovery_never_rolls_back_a_newer_dependency_generation(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    first = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        operation_id="a-older-operation",
    )
    manager.activate(first.operation_id)
    manager.mark_release_managed(first.operation_id)
    second = prepare_other_release_install(
        manager,
        operation_id="z-newer-operation",
    )
    manager.activate(second.operation_id)

    first_journal = Path(first.paths.journal)
    first_document = json.loads(
        first_journal.read_text(encoding="utf-8")
    )
    first_document["phase"] = "queued_locked"
    first_document["rollback_from"] = "release_activated"
    first_document["error"] = (
        "Plugin files are locked; activation is queued."
    )
    first_journal.write_text(
        json.dumps(first_document),
        encoding="utf-8",
    )

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()

    first_recovered = manager.load_transaction(first.operation_id)
    assert first_recovered.phase == "restart_pending"
    assert first_recovered.rollback_from == ""
    assert "newer shared dependencies" in first_recovered.error
    assert read_marker(second.paths.live_dependencies) == (
        "newer-shared-dependencies"
    )


def test_recovery_does_not_restore_older_dependencies_with_same_target(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    first = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        operation_id="a-older-operation",
        stage_dependencies=False,
    )
    write_marker(
        first.paths.staged_dependencies,
        "shared-target-dependencies",
    )
    first = manager.mark_dependencies_staged(
        first.operation_id,
        dependency_snapshot(),
    )
    manager.activate(first.operation_id)
    manager.mark_release_managed(first.operation_id)
    second = prepare_other_release_install(
        manager,
        operation_id="z-newer-operation",
        dependency_marker="shared-target-dependencies",
    )
    manager.activate(second.operation_id)

    first_journal = Path(first.paths.journal)
    first_document = json.loads(
        first_journal.read_text(encoding="utf-8")
    )
    first_document["phase"] = "dependencies_activated"
    first_document["error"] = ""
    first_journal.write_text(
        json.dumps(first_document),
        encoding="utf-8",
    )

    new_manager(plugin_core_module).recover_pending()

    first_recovered = manager.load_transaction(first.operation_id)
    assert first_recovered.phase == "restart_pending"
    assert first_recovered.rollback_from == ""
    assert "newer shared dependencies" in first_recovered.error
    assert read_marker(second.paths.live_dependencies) == (
        "shared-target-dependencies"
    )
    assert read_marker(first.paths.backup_dependencies) == (
        "old-dependencies"
    )


def test_recovery_fails_closed_when_dependency_ownership_is_ambiguous(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    first = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        operation_id="a-older-operation",
    )
    manager.activate(first.operation_id)
    manager.mark_release_managed(first.operation_id)
    second = prepare_other_release_install(
        manager,
        operation_id="z-newer-operation",
    )
    manager.activate(second.operation_id)

    for transaction in (first, second):
        journal_path = Path(transaction.paths.journal)
        document = json.loads(journal_path.read_text(encoding="utf-8"))
        document["phase"] = "release_activated"
        document["error"] = ""
        journal_path.write_text(json.dumps(document), encoding="utf-8")
    write_marker(
        second.paths.live_dependencies,
        "unidentified-shared-dependencies",
    )
    replace_calls = []

    def reject_rename(source, destination):
        replace_calls.append((source, destination))
        raise AssertionError("Recovery must not rename files.")

    monkeypatch.setattr(
        plugin_core_module.os,
        "replace",
        reject_rename,
    )

    with pytest.raises(RuntimeError, match="multiple unfinished"):
        new_manager(plugin_core_module).recover_pending()

    assert replace_calls == []
    assert read_marker(second.paths.live_dependencies) == (
        "unidentified-shared-dependencies"
    )


def test_release_transaction_can_roll_back_from_retained_backup(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(transaction.operation_id)

    rolled_back = manager.rollback(transaction.operation_id)

    assert rolled_back.phase == "rolled_back"
    assert_old_live(rolled_back)


@pytest.mark.parametrize("corrupt_component", ["code", "dependencies"])
def test_explicit_rollback_never_overwrites_live_with_a_corrupt_backup(
    plugin_core_module, tmp_path, corrupt_component
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(transaction.operation_id)
    if corrupt_component == "code":
        corrupt_path = Path(transaction.paths.backup_code) / "plugin.py"
    else:
        corrupt_path = (
            Path(transaction.paths.backup_dependencies) / "marker.txt"
        )
    corrupt_path.write_text("corrupt retained backup", encoding="utf-8")

    with pytest.raises(RuntimeError, match="snapshots"):
        manager.rollback(transaction.operation_id)

    unchanged = manager.load_transaction(transaction.operation_id)
    assert unchanged.phase == "restart_pending"
    assert_new_live(unchanged)


def test_rollback_origin_survives_a_crash_without_preexisting_live_trees(
    plugin_core_module, tmp_path
):
    manager, _, _ = make_manager(plugin_core_module, tmp_path)
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )
    write_marker(transaction.paths.staged_code, "new-code", "new-only.py")
    staged_code = Path(transaction.paths.staged_code)
    (staged_code / "plugin.py").write_text("# new plugin\n", encoding="utf-8")
    (staged_code / ".pypluginstore.json").write_text(
        json.dumps(
            install_metadata_document(
                NEW_COMMIT,
                NEW_TREE,
                2,
                "github:owner/example-plugin:v2.0.0",
                artifact_inventory(staged_code),
            )
        ),
        encoding="utf-8",
    )
    Path(transaction.paths.staged_dependencies).mkdir()
    manager.mark_staged_verified(transaction.operation_id)
    manager.mark_dependencies_staged(
        transaction.operation_id,
        dependency_snapshot(),
    )
    manager.activate(transaction.operation_id)

    def crash_before_restore(*_args, **_kwargs):
        raise SimulatedCrash("rollback_pending")

    manager._restore_component = crash_before_restore
    with pytest.raises(SimulatedCrash):
        manager.rollback(transaction.operation_id)

    interrupted = manager.load_transaction(transaction.operation_id)
    assert interrupted.phase == "rollback_pending"
    assert interrupted.rollback_from == "restart_pending"

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "rolled_back"
    assert not Path(recovered.paths.live_code).exists()
    assert not Path(recovered.paths.live_dependencies).exists()


def test_release_install_rechecks_repository_alias_before_activation(
    plugin_core_module,
    tmp_path,
):
    manager, plugins_dir, _ = make_manager(plugin_core_module, tmp_path)
    manager.plugin.plugin_data = {
        "ExamplePlugin": [
            "owner",
            "example-plugin",
            "Example",
            "main",
            "",
        ],
    }
    manager.plugin.registry_entries["ExamplePlugin"] = (
        plugin_core_module.RegistryEntry(
            "ExamplePlugin",
            "owner",
            "example-plugin",
            "Example",
            "main",
        )
    )
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )
    write_marker(transaction.paths.staged_code, "new-code", "new-only.py")
    staged_code = Path(transaction.paths.staged_code)
    (staged_code / "plugin.py").write_text(
        "# new plugin\n",
        encoding="utf-8",
    )
    (staged_code / ".pypluginstore.json").write_text(
        json.dumps(
            install_metadata_document(
                NEW_COMMIT,
                NEW_TREE,
                2,
                "github:owner/example-plugin:v2.0.0",
                artifact_inventory(staged_code),
            )
        ),
        encoding="utf-8",
    )
    Path(transaction.paths.staged_dependencies).mkdir()
    manager.mark_staged_verified(transaction.operation_id)
    manager.mark_dependencies_staged(
        transaction.operation_id,
        dependency_snapshot(),
    )

    alias_dir = plugins_dir / "legacy-example-folder"
    alias_dir.mkdir()
    alias_metadata = install_metadata_document(
        OLD_COMMIT,
        OLD_TREE,
        1,
        "github:owner/example-plugin:v1.0.0",
    )
    alias_metadata["package_id"] = "RemovedExamplePlugin"
    (alias_dir / ".pypluginstore.json").write_text(
        json.dumps(alias_metadata),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match="another installed folder now claims",
    ):
        manager.activate(transaction.operation_id)

    rolled_back = manager.load_transaction(transaction.operation_id)
    assert rolled_back.phase == "rolled_back"
    assert alias_dir.is_dir()
    assert not (plugins_dir / "ExamplePlugin").exists()


def test_release_install_never_backs_up_a_late_canonical_folder(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    manager, plugins_dir, _ = make_manager(plugin_core_module, tmp_path)
    transaction = prepare_new_release_install(manager)
    original_replace = manager._replace_directory

    def inject_late_folder(
        current,
        source,
        destination,
        pending_phase,
        completed_phase,
    ):
        if pending_phase == "code_backup_pending":
            write_marker(source, "late-writer")
        return original_replace(
            current,
            source,
            destination,
            pending_phase,
            completed_phase,
        )

    monkeypatch.setattr(manager, "_replace_directory", inject_late_folder)

    with pytest.raises(ValueError, match="appeared"):
        manager.activate(transaction.operation_id)

    stale = manager.load_transaction(transaction.operation_id)
    assert stale.phase == "stale_target"
    assert read_marker(plugins_dir / "ExamplePlugin") == "late-writer"
    assert not Path(stale.paths.backup_code).exists()


def test_recovery_resumes_after_one_rollback_component_was_restored(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(transaction.operation_id)
    real_restore = manager._restore_component
    restore_calls = 0

    def crash_after_first_restore(*args, **kwargs):
        nonlocal restore_calls
        real_restore(*args, **kwargs)
        restore_calls += 1
        if restore_calls == 1:
            raise SimulatedCrash("code restored")

    manager._restore_component = crash_after_first_restore
    with pytest.raises(SimulatedCrash):
        manager.rollback(transaction.operation_id)

    recovered_manager = new_manager(plugin_core_module)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)

    assert recovered.phase == "rolled_back"
    assert_old_live(recovered)


def test_release_transactions_are_globally_serialized(
    plugin_core_module, tmp_path
):
    first_manager, _, _ = make_manager(plugin_core_module, tmp_path)
    second_manager = new_manager(plugin_core_module)

    with first_manager.operation_lock(blocking=False):
        with pytest.raises(RuntimeError):
            second_manager.recover_pending(blocking=False)

    second_manager.recover_pending(blocking=False)


def test_full_release_workflows_are_globally_serialized(
    plugin_core_module,
    tmp_path,
):
    first_manager, _, _ = make_manager(plugin_core_module, tmp_path)
    second_manager = new_manager(plugin_core_module)

    with first_manager.workflow_lock(blocking=False):
        with pytest.raises(RuntimeError):
            second_manager.recover_pending(blocking=False)

    with second_manager.workflow_lock(blocking=False):
        pass


def test_active_release_must_finish_before_another_snapshot_starts(
    plugin_core_module,
    tmp_path,
):
    manager, _, _ = make_manager(plugin_core_module, tmp_path)
    first = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )

    with pytest.raises(RuntimeError, match="still active"):
        manager.create_transaction(
            plugin_key="OtherPlugin",
            operation_id="operation-002",
            operation="release_install",
            expected_current={"management_mode": "absent"},
            target=target_release(),
        )

    manager.abort(first.operation_id, "Cancelled before staging.")
    second = manager.create_transaction(
        plugin_key="OtherPlugin",
        operation_id="operation-002",
        operation="release_install",
        expected_current={"management_mode": "absent"},
        target=target_release(),
    )

    assert second.phase == "created"


def test_release_transaction_rejects_a_symlink_lock_file(
    plugin_core_module, tmp_path
):
    manager, _, manager_dir = make_manager(plugin_core_module, tmp_path)
    with manager.operation_lock():
        pass
    lock_path = manager_dir / ".pypluginstore" / "transactions.lock"
    external_file = tmp_path / "external-lock-target"
    external_file.write_text("do-not-touch", encoding="utf-8")
    lock_path.unlink()
    lock_path.symlink_to(external_file)

    with pytest.raises(ValueError, match="regular file"):
        with manager.operation_lock():
            pass

    assert external_file.read_text(encoding="utf-8") == "do-not-touch"
