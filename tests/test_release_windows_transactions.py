import json
from pathlib import Path

import pytest

from test_release_transactions import (
    SimulatedCrash,
    assert_new_live,
    assert_old_live,
    expected_current,
    install_metadata_document,
    legacy_transaction_document,
    make_manager,
    new_manager,
    prepare_transaction,
    read_marker,
    target_release,
)


def load_pending_document(manager_dir):
    pending_file = manager_dir / ".pypluginstore" / "pending_transactions.json"
    if not pending_file.exists():
        return {"schema_version": 3, "operations": []}
    return json.loads(pending_file.read_text(encoding="utf-8"))


def queue_windows_locked_transaction(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    live_folder="ExamplePlugin",
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path, windows=True
    )
    transaction = prepare_transaction(
        manager,
        plugins_dir,
        manager_dir,
        live_folder=live_folder,
    )
    real_replace = plugin_core_module.os.replace
    locked_once = False

    def lock_first_directory_rename(source, destination):
        nonlocal locked_once
        if Path(source).is_dir() and not locked_once:
            locked_once = True
            error = PermissionError("file is in use")
            error.winerror = 32
            raise error
        return real_replace(source, destination)

    monkeypatch.setattr(
        plugin_core_module.os, "replace", lock_first_directory_rename
    )
    queued = manager.activate(transaction.operation_id)
    return queued, manager_dir


def test_windows_locked_transaction_queues_complete_pinned_descriptor(
    plugin_core_module, tmp_path, monkeypatch
):
    queued, manager_dir = queue_windows_locked_transaction(
        plugin_core_module, tmp_path, monkeypatch
    )

    assert queued.phase == "queued_locked"
    assert_old_live(queued)
    pending = load_pending_document(manager_dir)
    assert pending["schema_version"] == 3
    assert len(pending["operations"]) == 1
    descriptor = pending["operations"][0]
    assert descriptor["operation_id"] == queued.operation_id
    assert descriptor["package_id"] == "ExamplePlugin"
    assert descriptor["live_folder"] == "ExamplePlugin"
    assert "plugin_key" not in descriptor
    assert descriptor["expected_current"] == expected_current()
    assert descriptor["target"] == target_release()
    assert descriptor["paths"] == queued.paths.to_document()


def test_windows_locked_transaction_retries_once_and_recovers_idempotently(
    plugin_core_module, tmp_path, monkeypatch
):
    queued, manager_dir = queue_windows_locked_transaction(
        plugin_core_module, tmp_path, monkeypatch
    )
    recovered_manager = new_manager(plugin_core_module, windows=True)

    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(queued.operation_id)

    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)
    assert load_pending_document(manager_dir)["operations"] == []

    first_journal = Path(recovered.paths.journal).read_bytes()
    recovered_manager.recover_pending()
    assert Path(recovered.paths.journal).read_bytes() == first_journal
    assert_new_live(recovered)


def test_windows_queued_alias_recovers_before_folder_discovery(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    live_folder = "domoticz_example_plugin"
    queued, _manager_dir = queue_windows_locked_transaction(
        plugin_core_module,
        tmp_path,
        monkeypatch,
        live_folder=live_folder,
    )

    recovered_manager = new_manager(plugin_core_module, windows=True)
    recovered_manager.plugin.installed_plugin_folders = {}
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(queued.operation_id)

    assert recovered.phase == "restart_pending"
    assert recovered.live_folder == live_folder
    assert Path(recovered.paths.live_code).name == live_folder
    assert_new_live(recovered)


def test_windows_restart_upgrades_v1_pending_queue_and_journal(
    plugin_core_module, tmp_path, monkeypatch
):
    queued, manager_dir = queue_windows_locked_transaction(
        plugin_core_module, tmp_path, monkeypatch
    )
    journal_path = Path(queued.paths.journal)
    legacy_journal = legacy_transaction_document(
        json.loads(journal_path.read_text(encoding="utf-8"))
    )
    journal_path.write_text(json.dumps(legacy_journal), encoding="utf-8")
    pending_path = (
        manager_dir / ".pypluginstore" / "pending_transactions.json"
    )
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["schema_version"] = 1
    pending["operations"] = [
        legacy_transaction_document(operation)
        for operation in pending["operations"]
    ]
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    recovered_manager = new_manager(plugin_core_module, windows=True)
    recovered_manager.recover_pending()

    recovered = recovered_manager.load_transaction(queued.operation_id)
    upgraded_journal = json.loads(
        journal_path.read_text(encoding="utf-8")
    )
    upgraded_pending = load_pending_document(manager_dir)
    assert recovered.phase == "restart_pending"
    assert upgraded_journal["schema_version"] == 3
    assert upgraded_journal["package_id"] == "ExamplePlugin"
    assert upgraded_journal["live_folder"] == "ExamplePlugin"
    assert "plugin_key" not in upgraded_journal
    assert upgraded_pending == {"schema_version": 3, "operations": []}


def test_windows_queued_transaction_refuses_a_stale_current_target(
    plugin_core_module, tmp_path, monkeypatch
):
    queued, manager_dir = queue_windows_locked_transaction(
        plugin_core_module, tmp_path, monkeypatch
    )
    current_metadata_file = (
        Path(queued.paths.live_code) / ".pypluginstore.json"
    )
    newer_document = install_metadata_document(
        "6" * 40,
        "7" * 64,
        3,
        "github:owner/example-plugin:v3.0.0",
    )
    current_metadata_file.write_text(
        json.dumps(newer_document), encoding="utf-8"
    )

    recovered_manager = new_manager(plugin_core_module, windows=True)
    recovered_manager.recover_pending()
    stale = recovered_manager.load_transaction(queued.operation_id)

    assert stale.phase == "stale_target"
    assert read_marker(stale.paths.live_code) == "old-code"
    assert read_marker(stale.paths.live_dependencies) == "old-dependencies"
    assert json.loads(current_metadata_file.read_text(encoding="utf-8")) == (
        newer_document
    )
    assert load_pending_document(manager_dir)["operations"] == []


@pytest.mark.parametrize("locked_rename", [1, 2, 3, 4])
def test_windows_lock_at_each_rename_boundary_queues_only_after_rollback(
    plugin_core_module, tmp_path, monkeypatch, locked_rename
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path, windows=True
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    real_replace = plugin_core_module.os.replace
    directory_renames = 0

    def lock_one_boundary(source, destination):
        nonlocal directory_renames
        if Path(source).is_dir():
            directory_renames += 1
            if directory_renames == locked_rename:
                error = PermissionError("file is in use")
                error.winerror = 32
                raise error
        return real_replace(source, destination)

    monkeypatch.setattr(plugin_core_module.os, "replace", lock_one_boundary)

    queued = manager.activate(transaction.operation_id)

    assert queued.phase == "queued_locked"
    assert_old_live(queued)
    assert len(load_pending_document(manager_dir)["operations"]) == 1

    recovered_manager = new_manager(plugin_core_module, windows=True)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)
    assert load_pending_document(manager_dir)["operations"] == []


def test_windows_repeated_lock_keeps_one_queue_entry_until_retry_succeeds(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path, windows=True
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    real_replace = plugin_core_module.os.replace
    locks_remaining = 2

    def lock_twice(source, destination):
        nonlocal locks_remaining
        if Path(source).is_dir() and locks_remaining:
            locks_remaining -= 1
            error = PermissionError("file is in use")
            error.winerror = 32
            raise error
        return real_replace(source, destination)

    monkeypatch.setattr(plugin_core_module.os, "replace", lock_twice)
    manager.activate(transaction.operation_id)
    recovered_manager = new_manager(plugin_core_module, windows=True)

    recovered_manager.recover_pending()
    still_queued = recovered_manager.load_transaction(
        transaction.operation_id
    )
    assert still_queued.phase == "queued_locked"
    assert_old_live(still_queued)
    assert len(load_pending_document(manager_dir)["operations"]) == 1

    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)
    assert load_pending_document(manager_dir)["operations"] == []


def test_windows_queue_descriptor_must_match_its_transaction_journal(
    plugin_core_module, tmp_path, monkeypatch
):
    queued, manager_dir = queue_windows_locked_transaction(
        plugin_core_module, tmp_path, monkeypatch
    )
    pending_path = (
        manager_dir / ".pypluginstore" / "pending_transactions.json"
    )
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    pending["operations"][0]["target"]["release_id"] = (
        "github:owner/example-plugin:v9.9.9"
    )
    pending_path.write_text(json.dumps(pending), encoding="utf-8")

    with pytest.raises(ValueError, match="differs from its journal"):
        new_manager(plugin_core_module, windows=True).recover_pending()

    reloaded_manager = new_manager(plugin_core_module, windows=True)
    unchanged = reloaded_manager.load_transaction(queued.operation_id)
    assert unchanged.phase == "queued_locked"
    assert_old_live(unchanged)


def test_windows_retry_crash_before_first_journal_transition_stays_queued(
    plugin_core_module, tmp_path, monkeypatch
):
    queued, manager_dir = queue_windows_locked_transaction(
        plugin_core_module, tmp_path, monkeypatch
    )
    recovered_manager = new_manager(plugin_core_module, windows=True)

    def crash_during_revalidation(_transaction):
        raise RuntimeError("retry process stopped")

    recovered_manager._staged_metadata_matches_target = (
        crash_during_revalidation
    )
    with pytest.raises(RuntimeError, match="retry process stopped"):
        recovered_manager.recover_pending()

    still_queued = new_manager(
        plugin_core_module,
        windows=True,
    ).load_transaction(queued.operation_id)
    assert still_queued.phase == "queued_locked"
    assert_old_live(still_queued)
    assert len(load_pending_document(manager_dir)["operations"]) == 1

    final_manager = new_manager(plugin_core_module, windows=True)
    final_manager.recover_pending()
    recovered = final_manager.load_transaction(queued.operation_id)
    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)


def test_windows_queue_intent_is_durable_before_locked_rollback(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path, windows=True
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    real_replace = plugin_core_module.os.replace
    locked_once = False

    def lock_first_rename(source, destination):
        nonlocal locked_once
        if Path(source).is_dir() and not locked_once:
            locked_once = True
            error = PermissionError("file is in use")
            error.winerror = 32
            raise error
        return real_replace(source, destination)

    def stop_before_rollback(*_args, **_kwargs):
        raise SimulatedCrash("before locked rollback")

    monkeypatch.setattr(plugin_core_module.os, "replace", lock_first_rename)
    manager._rollback_locked = stop_before_rollback

    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)

    queued = new_manager(
        plugin_core_module,
        windows=True,
    ).load_transaction(transaction.operation_id)
    assert queued.phase == "queued_locked"
    assert queued.rollback_from == "code_backup_pending"
    assert_old_live(queued)
    assert len(load_pending_document(manager_dir)["operations"]) == 1

    recovered_manager = new_manager(plugin_core_module, windows=True)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)


def test_windows_pending_intent_survives_a_crash_during_retry_activation(
    plugin_core_module, tmp_path, monkeypatch
):
    queued, manager_dir = queue_windows_locked_transaction(
        plugin_core_module, tmp_path, monkeypatch
    )
    retrying_manager = new_manager(plugin_core_module, windows=True)

    def crash_after_code_backup(phase, _transaction):
        if phase == "code_backed_up":
            raise SimulatedCrash(phase)

    retrying_manager.fault_injector = crash_after_code_backup
    with pytest.raises(SimulatedCrash):
        retrying_manager.recover_pending()

    interrupted = new_manager(
        plugin_core_module,
        windows=True,
    ).load_transaction(queued.operation_id)
    assert interrupted.phase == "code_backed_up"
    assert len(load_pending_document(manager_dir)["operations"]) == 1

    recovered_manager = new_manager(plugin_core_module, windows=True)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(queued.operation_id)
    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)
    assert load_pending_document(manager_dir)["operations"] == []


def test_windows_pending_intent_survives_crash_after_locked_rollback(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path, windows=True
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    real_replace = plugin_core_module.os.replace
    real_set_phase = manager._set_phase
    locked_once = False
    queued_phase_writes = 0

    def lock_first_rename(source, destination):
        nonlocal locked_once
        if Path(source).is_dir() and not locked_once:
            locked_once = True
            error = PermissionError("file is in use")
            error.winerror = 32
            raise error
        return real_replace(source, destination)

    def crash_before_second_queue_write(
        current_transaction,
        phase,
        error=None,
        inject=False,
    ):
        nonlocal queued_phase_writes
        if phase == "queued_locked":
            queued_phase_writes += 1
            if queued_phase_writes == 2:
                raise SimulatedCrash("after locked rollback")
        return real_set_phase(
            current_transaction,
            phase,
            error=error,
            inject=inject,
        )

    monkeypatch.setattr(plugin_core_module.os, "replace", lock_first_rename)
    manager._set_phase = crash_before_second_queue_write

    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)

    rolled_back = new_manager(
        plugin_core_module,
        windows=True,
    ).load_transaction(transaction.operation_id)
    assert rolled_back.phase == "rolled_back"
    assert_old_live(rolled_back)
    assert len(load_pending_document(manager_dir)["operations"]) == 1

    recovered_manager = new_manager(plugin_core_module, windows=True)
    recovered_manager.recover_pending()
    recovered = recovered_manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)
    assert load_pending_document(manager_dir)["operations"] == []


def test_windows_recovery_recreates_missing_intent_before_rollback(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, plugins_dir, manager_dir = make_manager(
        plugin_core_module, tmp_path, windows=True
    )
    transaction = prepare_transaction(manager, plugins_dir, manager_dir)
    real_replace = plugin_core_module.os.replace
    locked_once = False

    def lock_first_rename(source, destination):
        nonlocal locked_once
        if Path(source).is_dir() and not locked_once:
            locked_once = True
            error = PermissionError("file is in use")
            error.winerror = 32
            raise error
        return real_replace(source, destination)

    def crash_before_initial_pending_write():
        raise SimulatedCrash("before pending file")

    monkeypatch.setattr(plugin_core_module.os, "replace", lock_first_rename)
    manager._sync_pending_transactions_locked = crash_before_initial_pending_write
    with pytest.raises(SimulatedCrash):
        manager.activate(transaction.operation_id)

    journal_only = new_manager(
        plugin_core_module,
        windows=True,
    ).load_transaction(transaction.operation_id)
    assert journal_only.phase == "queued_locked"
    assert journal_only.rollback_from == "code_backup_pending"
    assert not (
        manager_dir / ".pypluginstore" / "pending_transactions.json"
    ).exists()

    recovering_manager = new_manager(plugin_core_module, windows=True)
    real_set_phase = recovering_manager._set_phase

    def crash_before_requeue(
        current_transaction,
        phase,
        error=None,
        inject=False,
    ):
        if phase == "queued_locked":
            raise SimulatedCrash("after recovery rollback")
        return real_set_phase(
            current_transaction,
            phase,
            error=error,
            inject=inject,
        )

    recovering_manager._set_phase = crash_before_requeue
    with pytest.raises(SimulatedCrash):
        recovering_manager.recover_pending()

    rolled_back = new_manager(
        plugin_core_module,
        windows=True,
    ).load_transaction(transaction.operation_id)
    assert rolled_back.phase == "rolled_back"
    assert_old_live(rolled_back)
    assert len(load_pending_document(manager_dir)["operations"]) == 1

    final_manager = new_manager(plugin_core_module, windows=True)
    final_manager.recover_pending()
    recovered = final_manager.load_transaction(transaction.operation_id)
    assert recovered.phase == "restart_pending"
    assert_new_live(recovered)
    assert load_pending_document(manager_dir)["operations"] == []
