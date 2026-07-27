import json
import shutil
import sys
from pathlib import Path

import pytest

from plugin_core_helpers import configure_home, write_plugin_py
from test_release_transactions import (
    GIT,
    NEW_COMMIT,
    NEW_TREE,
    artifact_inventory,
    assert_old_live,
    dependency_snapshot,
    install_metadata_document,
    make_manager,
    prepare_migration_transaction,
    prepare_transaction,
    read_marker,
    write_marker,
)


PLUGIN_KEY = "ExamplePlugin"
REPOSITORY_IDENTITY = "github.com/owner/example-plugin"
THIRD_COMMIT = "6" * 40
THIRD_TREE = "7" * 64


class FakeDevice:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def Create(self):
        return None


def configure_registry_entry(plugin_core_module, plugin):
    entry = plugin_core_module.RegistryEntry(
        PLUGIN_KEY,
        "owner",
        "example-plugin",
        "Example plugin",
        "main",
        delivery=plugin_core_module.DeliveryPolicy.implicit(),
    )
    plugin.registry_entries[PLUGIN_KEY] = entry
    plugin.plugin_data[PLUGIN_KEY] = entry.to_legacy_list()
    plugin.plugin_platforms[PLUGIN_KEY] = ["linux", "windows"]
    plugin.installed_plugin_folders[PLUGIN_KEY] = PLUGIN_KEY
    return entry


def prepare_third_release(manager, operation_id="operation-002"):
    transaction = manager.create_transaction(
        plugin_key=PLUGIN_KEY,
        operation_id=operation_id,
        operation="release_update",
        expected_current={
            "management_mode": "release",
            "release_id": "github:owner/example-plugin:v2.0.0",
            "release_revision": 2,
            "commit": NEW_COMMIT,
            "artifact_tree_sha256": NEW_TREE,
        },
        target={
            "management_mode": "release",
            "release_id": "github:owner/example-plugin:v3.0.0",
            "release_revision": 3,
            "commit": THIRD_COMMIT,
            "artifact_tree_sha256": THIRD_TREE,
        },
    )
    staged_code = Path(transaction.paths.staged_code)
    write_marker(staged_code, "third-code", "third-only.py")
    (staged_code / "plugin.py").write_text(
        "# third plugin\n",
        encoding="utf-8",
    )
    shutil.copytree(
        transaction.paths.live_dependencies,
        transaction.paths.staged_dependencies,
    )
    metadata = install_metadata_document(
        THIRD_COMMIT,
        THIRD_TREE,
        3,
        "github:owner/example-plugin:v3.0.0",
        artifact_inventory(staged_code),
    )
    metadata["version"] = "3.0.0"
    metadata["tag"] = "v3.0.0"
    (staged_code / ".pypluginstore.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    manager.mark_staged_verified(operation_id)
    return manager.mark_dependencies_staged(
        operation_id,
        dependency_snapshot(),
    )


def call_action(plugin, monkeypatch, action, token=""):
    responses = []
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)
    payload = {"action": action, "plugin_key": PLUGIN_KEY}
    if token:
        payload["confirmation_token"] = token
    plugin.handleApiCommand(payload)
    assert len(responses) == 1
    return responses[0]


def test_on_start_recovers_and_finalizes_release_before_other_pending_work(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, _plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, _plugins_dir, manager_dir)
    manager.activate(transaction.operation_id)
    plugin = manager.plugin
    calls = []
    plugin_core_module.Devices = {}
    monkeypatch.setattr(
        plugin_core_module.Domoticz,
        "Device",
        FakeDevice,
        raising=False,
    )
    monkeypatch.setattr(
        plugin,
        "fetch_registry",
        lambda: calls.append("fetch_registry"),
    )
    monkeypatch.setattr(
        plugin,
        "processPendingOperations",
        lambda: calls.append("legacy_pending"),
    )
    shared_deps = str(manager_dir / ".shared_deps")

    try:
        plugin.onStart()
    finally:
        while shared_deps in sys.path:
            sys.path.remove(shared_deps)

    recovered = manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "release_managed"
    assert calls == ["fetch_registry", "legacy_pending"]
    assert read_marker(recovered.paths.live_code) == "new-code"
    assert Path(recovered.paths.backup_code).is_dir()


def test_management_status_discovers_latest_verified_rollback_and_restart(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    first = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(first.operation_id)
    manager.mark_release_managed(first.operation_id)
    second = prepare_third_release(manager)
    manager.activate(second.operation_id)
    plugin = manager.plugin
    configure_registry_entry(plugin_core_module, plugin)

    state = plugin.getPluginManagementMap(
        [PLUGIN_KEY],
        {PLUGIN_KEY: "unknown"},
        {},
        str(plugins_dir),
    )[PLUGIN_KEY]

    assert state["rollback_available"] is True
    assert state["rollback_channel"] == "release"
    assert state["rollback_version"] == "2.0.0"
    assert state["rollback_revision"] == 2
    assert state["restart_pending"] is True
    assert state["summary"].startswith("Release - restart required")
    assert "rollback" not in state["summary"].lower()
    rollback_action = next(
        action for action in state["actions"] if action["id"] == "rollback"
    )
    assert rollback_action["label"] == "Restore v2.0.0"


def test_rollback_api_uses_latest_verified_backup_and_requires_restart(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(transaction.operation_id)
    manager.mark_release_managed(transaction.operation_id)
    plugin = manager.plugin
    configure_registry_entry(plugin_core_module, plugin)

    challenge_response = call_action(plugin, monkeypatch, "rollback")
    assert challenge_response["status"] == "confirmation_required"
    challenge = challenge_response["challenge"]
    assert challenge["kind"] == "rollback"
    assert challenge["token"]
    assert transaction.operation_id not in challenge["token"]
    assert challenge["message"] == (
        "Restore v1.0.0 for ExamplePlugin? "
        "A Domoticz restart will be required."
    )

    response = call_action(
        plugin,
        monkeypatch,
        "rollback",
        challenge["token"],
    )

    assert response["status"] == "success"
    assert response["restart_pending"] is True
    assert_old_live(manager.load_transaction(transaction.operation_id))


@pytest.mark.skipif(GIT is None, reason="Git is required")
def test_rollback_to_git_preserves_internal_safety_hold(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    transaction, _live_code, _installed_commit, _preflight = (
        prepare_migration_transaction(
            plugin_core_module,
            manager,
            plugins_dir,
            manager_dir,
        )
    )
    manager.activate(transaction.operation_id)
    manager.mark_release_managed(transaction.operation_id)
    plugin = manager.plugin
    configure_registry_entry(plugin_core_module, plugin)

    lifecycle = manager.plugin_lifecycle_state(PLUGIN_KEY)
    assert lifecycle["rollback_channel"] == "git"
    assert lifecycle["rollback_version"] == ""

    challenge_response = call_action(plugin, monkeypatch, "rollback")
    assert challenge_response["challenge"]["message"] == (
        "Return to previous Git version for ExamplePlugin? "
        "A Domoticz restart will be required."
    )
    response = call_action(
        plugin,
        monkeypatch,
        "rollback",
        challenge_response["challenge"]["token"],
    )

    assert response["status"] == "success"
    assert plugin.channel_preference_service.get(
        REPOSITORY_IDENTITY
    ) == "keep_git"


@pytest.mark.skipif(GIT is None, reason="Git is required")
def test_rollback_reports_success_when_preference_persistence_fails(
    plugin_core_module,
    tmp_path,
    monkeypatch,
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
    manager.activate(transaction.operation_id)
    manager.mark_release_managed(transaction.operation_id)
    plugin = manager.plugin
    configure_registry_entry(plugin_core_module, plugin)
    challenge = call_action(
        plugin,
        monkeypatch,
        "rollback",
    )["challenge"]

    def fail_preference_write(*_arguments):
        raise OSError("preference storage unavailable")

    monkeypatch.setattr(
        plugin.channel_preference_service,
        "set",
        fail_preference_write,
    )
    response = call_action(
        plugin,
        monkeypatch,
        "rollback",
        challenge["token"],
    )

    assert response["status"] == "success"
    assert response["restart_pending"] is True
    assert "could not be saved" in response["message"]
    assert (live_code / ".git").is_dir()


def test_older_backup_is_pruned_only_after_newer_release_is_managed(
    plugin_core_module, tmp_path
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module,
        tmp_path,
    )
    first = prepare_transaction(manager, plugins_dir, manager_dir)
    manager.activate(first.operation_id)
    manager.mark_release_managed(first.operation_id)
    second = prepare_third_release(manager)
    manager.activate(second.operation_id)

    assert Path(first.paths.backup_code).is_dir()
    assert Path(first.paths.backup_dependencies).is_dir()
    assert Path(second.paths.backup_code).is_dir()
    assert Path(second.paths.backup_dependencies).is_dir()

    manager.mark_release_managed(second.operation_id)

    assert not Path(first.paths.backup_code).exists()
    assert not Path(first.paths.backup_dependencies).exists()
    assert Path(second.paths.backup_code).is_dir()
    assert Path(second.paths.backup_dependencies).is_dir()


def test_use_git_action_is_unsupported_and_does_not_touch_checkout(
    plugin_core_module, tmp_path, monkeypatch
):
    plugins_dir, _manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    configure_registry_entry(plugin_core_module, plugin)
    plugin_dir = plugins_dir / PLUGIN_KEY
    write_plugin_py(
        plugin_dir,
        key=PLUGIN_KEY,
        name="Example plugin",
        externallink="https://github.com/owner/example-plugin",
    )
    (plugin_dir / ".git").mkdir()
    before = (plugin_dir / "plugin.py").read_bytes()

    response = call_action(plugin, monkeypatch, "use_git")
    direct_response = plugin.executeReleaseManagementAction(
        action="use_git",
        plugin_key=PLUGIN_KEY,
    )

    assert response["status"] == "error"
    assert "local registry override" in response["message"].lower()
    assert direct_response["status"] == "error"
    assert "unsupported" in direct_response["message"].lower()
    assert plugin.channel_preference_service.get(REPOSITORY_IDENTITY) is None
    assert (plugin_dir / "plugin.py").read_bytes() == before
    assert (plugin_dir / ".git").is_dir()


def test_use_release_unavailable_does_not_clear_git_preference_or_touch_checkout(
    plugin_core_module, tmp_path, monkeypatch
):
    plugins_dir, _manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    configure_registry_entry(plugin_core_module, plugin)
    plugin_dir = plugins_dir / PLUGIN_KEY
    write_plugin_py(
        plugin_dir,
        key=PLUGIN_KEY,
        name="Example plugin",
        externallink="https://github.com/owner/example-plugin",
    )
    (plugin_dir / ".git").mkdir()
    plugin.channel_preference_service.set(
        REPOSITORY_IDENTITY,
        "keep_git",
    )
    before = (plugin_dir / "plugin.py").read_bytes()

    response = call_action(plugin, monkeypatch, "use_release")

    assert response["status"] == "error"
    assert plugin.channel_preference_service.get(REPOSITORY_IDENTITY) == (
        "keep_git"
    )
    assert (plugin_dir / "plugin.py").read_bytes() == before
    assert (plugin_dir / ".git").is_dir()
