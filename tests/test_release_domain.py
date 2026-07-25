import json
from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from release_domain import (
    ActionDescriptor,
    InstallationChannel,
    LifecycleNotice,
    LifecyclePhase,
    NoticeSeverity,
    ObservedInstallationState,
    PluginManagementView,
    ReleaseCandidateState,
    TransitionState,
)


COMMIT = "1" * 40


def observed_installation(**overrides):
    values = {
        "package_id": "Somfy",
        "installation_folder": "domoticz_somfy",
        "channel": InstallationChannel.GIT,
        "installed_version": "5.3.1",
        "installed_revision": COMMIT,
        "release_id": None,
        "working_tree_clean": True,
    }
    values.update(overrides)
    return ObservedInstallationState(**values)


def candidate(version, *, source, stale=False):
    return ReleaseCandidateState(
        release_id=f"github:example/somfy:v{version}",
        version=version,
        tag=f"v{version}",
        commit=COMMIT,
        source=source,
        certified=True,
        stale=stale,
    )


def idle_transition(**overrides):
    values = {
        "phase": LifecyclePhase.IDLE,
        "operation": None,
        "operation_id": None,
        "restart_required": False,
        "failure": None,
    }
    values.update(overrides)
    return TransitionState(**values)


def actions(*, update_enabled=True, update_reason=None):
    return (
        ActionDescriptor(
            action="update",
            label="Update",
            enabled=update_enabled,
            disabled_reason=update_reason,
            confirmation_required=False,
        ),
        ActionDescriptor(
            action="rollback",
            label="Rollback",
            enabled=False,
            disabled_reason="No verified rollback is available.",
            confirmation_required=True,
        ),
    )


def test_observed_installation_keeps_logical_identity_separate_from_folder():
    state = observed_installation()

    assert is_dataclass(state)
    assert state.package_id == "Somfy"
    assert state.installation_folder == "domoticz_somfy"
    with pytest.raises(FrozenInstanceError):
        state.installation_folder = "Somfy"


def test_view_keeps_certified_migration_and_latest_upstream_distinct():
    migration = candidate("5.3.2", source="host_certification")
    latest = candidate("5.3.3", source="provider_refresh")
    view = PluginManagementView(
        installation=observed_installation(),
        certified_migration_target=migration,
        upstream_latest=latest,
        transition=idle_transition(),
        notice=None,
        actions=actions(),
    )

    assert view.certified_migration_target.version == "5.3.2"
    assert view.upstream_latest.version == "5.3.3"
    assert view.certified_migration_target is not view.upstream_latest


def test_git_install_can_migrate_directly_to_host_certified_latest_release():
    latest = candidate("5.3.2", source="host_certification")
    view = PluginManagementView(
        installation=observed_installation(),
        certified_migration_target=latest,
        upstream_latest=latest,
        transition=idle_transition(),
        notice=None,
        actions=actions(),
    )

    document = view.to_dict()

    assert document["installation"]["channel"] == "git"
    assert document["certified_migration_target"]["version"] == "5.3.2"
    assert document["upstream_latest"]["version"] == "5.3.2"
    assert document["actions"][0] == {
        "action": "update",
        "label": "Update",
        "enabled": True,
        "disabled_reason": None,
        "confirmation_required": False,
    }


def test_stale_verified_latest_candidate_remains_visible():
    latest = candidate(
        "5.3.2",
        source="last_verified_provider_refresh",
        stale=True,
    )
    view = PluginManagementView(
        installation=observed_installation(
            channel=InstallationChannel.RELEASE,
            release_id="github:example/somfy:v5.3.1",
        ),
        certified_migration_target=None,
        upstream_latest=latest,
        transition=idle_transition(),
        notice=LifecycleNotice(
            code="release_refresh_failed",
            severity=NoticeSeverity.WARNING,
            message=(
                "The latest verified release is stale because refresh failed."
            ),
        ),
        actions=actions(update_enabled=False, update_reason="Refresh failed."),
    )

    document = view.to_dict()

    assert document["upstream_latest"]["version"] == "5.3.2"
    assert document["upstream_latest"]["stale"] is True
    assert document["notice"]["code"] == "release_refresh_failed"


def test_current_release_with_pending_restart_has_notice_and_disabled_update():
    view = PluginManagementView(
        installation=observed_installation(
            channel=InstallationChannel.RELEASE,
            installed_version="5.3.2",
            release_id="github:example/somfy:v5.3.2",
        ),
        certified_migration_target=None,
        upstream_latest=candidate("5.3.2", source="provider_refresh"),
        transition=idle_transition(
            phase=LifecyclePhase.RESTART_REQUIRED,
            operation="release_update",
            operation_id="operation-123",
            restart_required=True,
        ),
        notice=LifecycleNotice(
            code="restart_required",
            severity=NoticeSeverity.WARNING,
            message="Restart Domoticz to finish activating this release.",
        ),
        actions=actions(
            update_enabled=False,
            update_reason="Restart Domoticz before updating again.",
        ),
    )

    document = view.to_dict()

    assert document["transition"]["phase"] == "restart_required"
    assert document["notice"]["code"] == "restart_required"
    assert document["actions"][0]["enabled"] is False
    assert "Restart" in document["actions"][0]["disabled_reason"]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: observed_installation(
            channel=InstallationChannel.GIT,
            release_id="github:example/somfy:v5.3.1",
        ),
        lambda: observed_installation(
            channel=InstallationChannel.RELEASE,
            release_id=None,
        ),
        lambda: idle_transition(
            phase=LifecyclePhase.IDLE,
            restart_required=True,
        ),
        lambda: idle_transition(
            phase=LifecyclePhase.RESTART_REQUIRED,
            operation=None,
            operation_id=None,
            restart_required=True,
        ),
        lambda: ActionDescriptor(
            action="update",
            label="Update",
            enabled=False,
            disabled_reason=None,
            confirmation_required=False,
        ),
        lambda: ActionDescriptor(
            action="update",
            label="Update",
            enabled=True,
            disabled_reason="Contradictory reason.",
            confirmation_required=False,
        ),
    ],
)
def test_contracts_reject_contradictory_states(factory):
    with pytest.raises(ValueError):
        factory()


def test_view_rejects_restart_without_prominent_notice_and_disabled_update():
    transition = idle_transition(
        phase=LifecyclePhase.RESTART_REQUIRED,
        operation="release_update",
        operation_id="operation-123",
        restart_required=True,
    )

    with pytest.raises(ValueError, match="restart"):
        PluginManagementView(
            installation=observed_installation(
                channel=InstallationChannel.RELEASE,
                release_id="github:example/somfy:v5.3.2",
            ),
            certified_migration_target=None,
            upstream_latest=candidate("5.3.2", source="provider_refresh"),
            transition=transition,
            notice=None,
            actions=actions(),
        )


def test_view_serializes_to_frontend_safe_json_without_enum_objects():
    view = PluginManagementView(
        installation=observed_installation(),
        certified_migration_target=candidate(
            "5.3.2",
            source="host_certification",
        ),
        upstream_latest=candidate("5.3.2", source="provider_refresh"),
        transition=idle_transition(),
        notice=None,
        actions=actions(),
    )

    encoded = json.dumps(view.to_dict(), sort_keys=True)
    decoded = json.loads(encoded)

    assert decoded["installation"] == {
        "package_id": "Somfy",
        "installation_folder": "domoticz_somfy",
        "channel": "git",
        "installed_version": "5.3.1",
        "installed_revision": COMMIT,
        "release_id": None,
        "working_tree_clean": True,
    }
    assert decoded["transition"] == {
        "phase": "idle",
        "operation": None,
        "operation_id": None,
        "restart_required": False,
        "failure": None,
    }
