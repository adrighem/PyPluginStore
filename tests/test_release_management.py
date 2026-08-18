import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from plugin_core_helpers import configure_home
from test_release_transactions import install_metadata_document


COMMIT_1 = "1" * 40
COMMIT_2 = "2" * 40
TREE_1 = "a" * 64
TREE_2 = "b" * 64
ARTIFACT_1 = "c" * 64
ARTIFACT_2 = "d" * 64


class RecordingGitStrategy:
    def __init__(self):
        self.calls = []

    def install(self, entry):
        self.calls.append(("install", entry.key))
        return True, "git install"

    def update(self, entry, queue_on_lock=True):
        self.calls.append(("update", entry.key, queue_on_lock))
        return True, "git update"

    def check_for_update(self, entry):
        self.calls.append(("check_for_update", entry.key))
        return "git status"


class RecordingReleaseStrategy:
    def __init__(self, result=(True, "release operation")):
        self.calls = []
        self.result = result

    def install(self, entry, release, trigger):
        self.calls.append(
            ("install", entry.key, release.release_id, trigger)
        )
        return self.result

    def update(self, entry, release, trigger):
        self.calls.append(
            ("update", entry.key, release.release_id, trigger)
        )
        return self.result

    def migrate(self, entry, release, trigger):
        self.calls.append(
            ("migrate", entry.key, release.release_id, trigger)
        )
        return self.result


def release_descriptor(
    plugin_core_module,
    *,
    revision=7,
    release_id="github:owner/example-plugin:v1.4.0",
    supersedes=None,
    version="1.4.0",
    tag="v1.4.0",
    commit=COMMIT_1,
    tree_sha256=TREE_1,
    artifact_sha256=ARTIFACT_1,
    artifact_size=1000,
    root_prefix="example-plugin-v1.4.0",
    provenance="forge_source_archive",
    migration_mode="automatic",
    migration_evidence="commit_source_archive",
):
    kind = "source_zip" if provenance == "forge_source_archive" else "asset_zip"
    return plugin_core_module.ReleaseDescriptor.from_document(
        {
            "revision": revision,
            "release_id": release_id,
            "supersedes": list(supersedes or []),
            "provider": "github",
            "repository_identity": "github.com/owner/example-plugin",
            "version": version,
            "tag": tag,
            "released_at": "2026-07-18T07:00:00Z",
            "commit": commit,
            "artifact": {
                "kind": kind,
                "provenance": provenance,
                "migration": {
                    "mode": migration_mode,
                    "evidence": migration_evidence,
                },
                "url": (
                    "https://downloads.example.test/"
                    + artifact_sha256
                    + "/plugin.zip"
                ),
                "sha256": artifact_sha256,
                "size": artifact_size,
                "tree_sha256": tree_sha256,
                "root_prefix": root_prefix,
                "source_path": ".",
            },
        }
    )


def installed_release_state(descriptor):
    return SimpleNamespace(
        plugin_key="ExamplePlugin",
        package_id="ExamplePlugin",
        management_mode="release",
        repository_identity="github.com/owner/example-plugin",
        version=descriptor.version,
        tag=descriptor.tag,
        released_at=descriptor.released_at,
        release_revision=descriptor.revision,
        release_id=descriptor.release_id,
        commit=descriptor.commit,
        source_revision=descriptor.source_revision,
        artifact_sha256=descriptor.artifact.sha256,
        artifact_tree_sha256=descriptor.artifact.tree_sha256,
        artifact_provenance=descriptor.artifact.provenance,
        index_sequence=42,
        authority=descriptor.authority,
        candidate_fingerprint=descriptor.candidate_fingerprint,
        supersedes=list(descriptor.supersedes),
        lineage_complete=descriptor.lineage_complete,
        anchor_release_id=descriptor.anchor_release_id,
        anchor_revision=descriptor.anchor_revision,
        anchor_authority=descriptor.anchor_authority,
        anchor_index_sequence=descriptor.anchor_index_sequence,
    )


def delivery_policy(plugin_core_module, preferred, git_supported=True):
    release = None
    if preferred in ("release", "release_if_indexed"):
        release = {
            "provider": "github",
            "channel": "stable",
            "tag_pattern": r"^v[0-9]+\.[0-9]+\.[0-9]+$",
            "artifact": "source_zip",
            "source_path": ".",
            "mutable_paths": [],
        }
    document = {
        "schema_version": 1,
        "preferred": preferred,
        "git_supported": git_supported,
    }
    if release is not None:
        document["release"] = release
    return plugin_core_module.DeliveryPolicy.from_document(document)


def registry_entry(
    plugin_core_module,
    *,
    preferred="release_if_indexed",
    git_supported=True,
    implicit=False,
):
    policy = (
        plugin_core_module.DeliveryPolicy.implicit()
        if implicit
        else delivery_policy(
            plugin_core_module,
            preferred,
            git_supported=git_supported,
        )
    )
    return plugin_core_module.RegistryEntry(
        "ExamplePlugin",
        "owner",
        "example-plugin",
        "Example plugin",
        "main",
        delivery=policy,
    )


def release_tombstone(plugin_core_module):
    return plugin_core_module.ReleaseTombstone.from_document(
        {
            "repository_identity": "github.com/owner/example-plugin",
            "last_revision": 7,
            "release_id": "github:owner/example-plugin:v1.4.0",
            "reason": "Release packaging was withdrawn.",
            "removed_at": "2026-07-18T09:00:00Z",
        }
    )


def make_coordinator(plugin_core_module, release_result=(True, "release operation")):
    plugin = plugin_core_module.BasePlugin()
    git_strategy = RecordingGitStrategy()
    release_strategy = RecordingReleaseStrategy(release_result)
    coordinator = plugin_core_module.ReleaseManagementCoordinator(
        plugin,
        git_strategy=git_strategy,
        release_strategy=release_strategy,
    )
    return coordinator, git_strategy, release_strategy


def decide(
    coordinator,
    entry,
    *,
    operation,
    installed_mode,
    release=None,
    tombstone=None,
    metadata_authorized=True,
    metadata_reason="",
    installed_release=None,
    preference=None,
    trigger="manual",
    downgrade_confirmed=False,
    release_was_activated=False,
    git_status="unknown",
    runtime_observation_state="",
    runtime_observation_message="",
):
    return coordinator.decide(
        entry,
        operation=operation,
        installed_mode=installed_mode,
        release=release,
        tombstone=tombstone,
        metadata_authorized=metadata_authorized,
        metadata_reason=metadata_reason,
        installed_release=installed_release,
        channel_preference=preference,
        trigger=trigger,
        downgrade_confirmed=downgrade_confirmed,
        release_was_activated=release_was_activated,
        git_status=git_status,
        runtime_observation_state=runtime_observation_state,
        runtime_observation_message=runtime_observation_message,
    )


@pytest.mark.parametrize(
    (
        "preferred",
        "git_supported",
        "preference",
        "has_release",
        "expected_route",
        "expected_status",
    ),
    [
        (
            "release_if_indexed",
            True,
            None,
            True,
            "release_install",
            "available",
        ),
        ("release", True, None, True, "release_install", "available"),
        (
            "release_if_indexed",
            True,
            None,
            False,
            "git_install",
            "git_available",
        ),
        (
            "release",
            True,
            None,
            False,
            "blocked",
            "release_metadata_unavailable",
        ),
        ("git", True, None, True, "git_install", "git_available"),
        (
            "release_if_indexed",
            True,
            "keep_git",
            True,
            "git_install",
            "git_available",
        ),
        (
            "release_if_indexed",
            True,
            "release",
            True,
            "release_install",
            "available",
        ),
        (
            "release_if_indexed",
            True,
            "release",
            False,
            "blocked",
            "release_metadata_unavailable",
        ),
        (
            "release",
            False,
            "keep_git",
            True,
            "blocked",
            "release_metadata_unavailable",
        ),
    ],
)
def test_activation_honors_registry_policy_and_explicit_channel_choice(
    plugin_core_module,
    preferred,
    git_supported,
    preference,
    has_release,
    expected_route,
    expected_status,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(
        plugin_core_module,
        preferred=preferred,
        git_supported=git_supported,
    )
    target = release_descriptor(plugin_core_module) if has_release else None

    decision = decide(
        coordinator,
        entry,
        operation="install",
        installed_mode="absent",
        release=target,
        preference=preference,
    )

    assert decision.route == expected_route
    assert decision.status == expected_status
    assert decision.trigger == "manual"
    if expected_route.startswith("release_"):
        assert decision.release is target


@pytest.mark.parametrize("installed_mode", ["git", "release"])
def test_install_never_creates_a_second_copy_of_an_installed_plugin(
    plugin_core_module,
    installed_mode,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)

    decision = decide(
        coordinator,
        entry,
        operation="install",
        installed_mode=installed_mode,
        release=release_descriptor(plugin_core_module),
    )

    assert decision.route == "none"
    assert decision.status == "current"
    assert decision.reason == "plugin_already_installed"


@pytest.mark.parametrize(
    (
        "preferred",
        "installed_mode",
        "preference",
        "release_was_activated",
        "expected_route",
    ),
    [
        ("release_if_indexed", "absent", None, False, "git_install"),
        ("release_if_indexed", "git", None, False, "git_update"),
        ("release_if_indexed", "git", "keep_git", True, "git_update"),
        ("release_if_indexed", "git", None, True, "blocked"),
        ("release_if_indexed", "release", None, True, "blocked"),
        ("release", "absent", None, False, "blocked"),
        ("release_if_indexed", "absent", "release", False, "blocked"),
    ],
)
def test_unavailable_metadata_never_changes_an_activated_release_to_git(
    plugin_core_module,
    preferred,
    installed_mode,
    preference,
    release_was_activated,
    expected_route,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module, preferred=preferred)
    operation = "install" if installed_mode == "absent" else "update"

    decision = decide(
        coordinator,
        entry,
        operation=operation,
        installed_mode=installed_mode,
        metadata_authorized=False,
        metadata_reason="expired",
        preference=preference,
        release_was_activated=release_was_activated,
        git_status="available",
    )

    assert decision.route == expected_route
    if expected_route == "blocked":
        assert decision.status == "release_metadata_unavailable"
        assert decision.reason == "expired"


@pytest.mark.parametrize(
    (
        "installed_mode",
        "preference",
        "expected_route",
        "expected_status",
    ),
    [
        ("absent", None, "git_install", "git_available"),
        ("git", None, "git_update", "git_available"),
        (
            "absent",
            "release",
            "blocked",
            "release_metadata_unavailable",
        ),
        (
            "release",
            None,
            "blocked",
            "release_metadata_unavailable",
        ),
    ],
)
def test_decertification_is_explicit_and_never_a_silent_release_fallback(
    plugin_core_module,
    installed_mode,
    preference,
    expected_route,
    expected_status,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)

    decision = decide(
        coordinator,
        entry,
        operation="install" if installed_mode == "absent" else "update",
        installed_mode=installed_mode,
        tombstone=release_tombstone(plugin_core_module),
        preference=preference,
        release_was_activated=installed_mode == "release",
        git_status="available",
    )

    assert decision.route == expected_route
    assert decision.status == expected_status
    assert decision.reason == "release_decertified"


def test_fresh_index_missing_previously_activated_entry_still_fails_closed(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(plugin_core_module)

    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=None,
        metadata_authorized=True,
        installed_release=installed_release_state(current),
        release_was_activated=True,
        git_status="available",
    )

    assert decision.route == "blocked"
    assert decision.status == "release_metadata_unavailable"
    assert decision.reason == "release_entry_missing"


def test_expected_git_fallback_does_not_expose_missing_release_reason(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="git",
        release=None,
        git_status="unknown",
    )

    assert decision.route == "git_status"
    assert decision.status == "git_unknown"
    assert decision.reason == "release_entry_missing"

    state = {
        "channel": "git",
        "status": decision.status,
        "updateable": True,
        "verification_message": decision.reason,
        "migration_message": "",
        "restart_pending": False,
        "rollback_available": False,
        "git_supported": True,
        "release_available": False,
        "migration_action_state": "blocked",
    }
    presented = plugin_core_module.BasePlugin()._management_presentation(
        state,
        entry,
        is_manager=False,
    )

    assert presented["summary"] == "Git - update status unknown"
    assert "release_entry_missing" not in presented["summary"]
    assert presented["verification_message"] == "release_entry_missing"
    assert presented["actions"] == [
        {
            "id": "update",
            "label": "Update",
            "enabled": True,
            "reason": "",
        }
    ]


@pytest.mark.parametrize(
    (
        "rollback_channel",
        "rollback_version",
        "expected_label",
        "expected_confirmation",
    ),
    [
        (
            "git",
            "",
            "Restore Git",
            "Return to previous Git version",
        ),
        (
            "release",
            "1.3.0",
            "Restore v1.3.0",
            "Restore v1.3.0",
        ),
        (
            "release",
            "",
            "Rollback",
            "Restore previous Release version",
        ),
        ("", "", "Rollback", "Restore previous version"),
    ],
)
def test_management_presentation_names_verified_restore_target(
    plugin_core_module,
    rollback_channel,
    rollback_version,
    expected_label,
    expected_confirmation,
):
    entry = registry_entry(plugin_core_module)
    state = {
        "channel": "release",
        "status": "current",
        "updateable": False,
        "verification_message": "",
        "migration_message": "",
        "restart_pending": False,
        "rollback_available": True,
        "rollback_channel": rollback_channel,
        "rollback_version": rollback_version,
        "migration_action_state": "blocked",
    }

    presented = plugin_core_module.BasePlugin()._management_presentation(
        state,
        entry,
        is_manager=False,
    )

    rollback_action = next(
        action
        for action in presented["actions"]
        if action["id"] == "rollback"
    )
    assert rollback_action == {
        "id": "rollback",
        "label": expected_label,
        "enabled": True,
        "reason": "",
    }
    assert plugin_core_module.BasePlugin._rollback_confirmation_message(
        state,
        "ExamplePlugin",
    ) == (
        expected_confirmation
        + " for ExamplePlugin? A Domoticz restart will be required."
    )


def test_higher_release_requires_complete_predecessor_lineage(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(plugin_core_module)
    installed = installed_release_state(current)
    accepted_target = release_descriptor(
        plugin_core_module,
        revision=8,
        release_id="github:owner/example-plugin:v1.5.0",
        supersedes=[current.release_id],
        version="1.5.0",
        tag="v1.5.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v1.5.0",
    )
    gap_target = release_descriptor(
        plugin_core_module,
        revision=8,
        release_id="github:owner/example-plugin:v1.5.0",
        supersedes=[],
        version="1.5.0",
        tag="v1.5.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v1.5.0",
    )

    accepted = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=accepted_target,
        installed_release=installed,
        release_was_activated=True,
    )
    rejected = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=gap_target,
        installed_release=installed,
        release_was_activated=True,
    )

    assert accepted.route == "release_update"
    assert accepted.status == "available"
    assert rejected.route == "blocked"
    assert rejected.status == "verification_failed"
    assert rejected.reason == "predecessor_gap"


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ({"commit": COMMIT_2}, "release_mutation"),
        ({"tree_sha256": TREE_2}, "release_mutation"),
        (
            {
                "release_id": "github:owner/example-plugin:v1.4.0-repacked",
            },
            "release_mutation",
        ),
    ],
)
def test_equal_revision_rejects_identity_commit_or_tree_mutation(
    plugin_core_module, change, expected_reason
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(plugin_core_module)
    installed = installed_release_state(current)
    target = release_descriptor(plugin_core_module, **change)

    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=target,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "blocked"
    assert decision.status == "verification_failed"
    assert decision.reason == expected_reason


def test_recompressed_source_zip_with_same_commit_and_tree_is_already_current(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(plugin_core_module)
    installed = installed_release_state(current)
    recompressed = release_descriptor(
        plugin_core_module,
        artifact_sha256=ARTIFACT_2,
        artifact_size=1200,
        root_prefix="different-provider-wrapper",
    )

    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=recompressed,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "none"
    assert decision.status == "current"
    assert decision.reason == "equivalent_recompressed_source"


def test_equal_revision_accepts_omitted_vs_explicit_commit_source_revision(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(plugin_core_module)
    current.source_revision = ""
    installed = installed_release_state(current)
    installed.source_revision = current.commit

    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=current,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "none"
    assert decision.status == "current"


def test_equal_revision_attached_asset_digest_change_is_a_mutation(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(
        plugin_core_module,
        provenance="attached_asset",
        migration_mode="manual",
        migration_evidence="unverified_asset",
    )
    installed = installed_release_state(current)
    changed = release_descriptor(
        plugin_core_module,
        provenance="attached_asset",
        migration_mode="manual",
        migration_evidence="unverified_asset",
        artifact_sha256=ARTIFACT_2,
    )

    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=changed,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "blocked"
    assert decision.status == "verification_failed"
    assert decision.reason == "release_mutation"


@pytest.mark.parametrize(
    ("trigger", "confirmed", "expected_route", "requires_confirmation"),
    [
        ("automatic", False, "blocked", False),
        ("manual", False, "confirmation_required", True),
        ("manual", True, "release_update", False),
    ],
)
def test_release_downgrade_requires_explicit_manual_confirmation(
    plugin_core_module,
    trigger,
    confirmed,
    expected_route,
    requires_confirmation,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(
        plugin_core_module,
        revision=8,
        release_id="github:owner/example-plugin:v1.5.0",
        supersedes=["github:owner/example-plugin:v1.4.0"],
        version="1.5.0",
        tag="v1.5.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v1.5.0",
    )
    installed = installed_release_state(current)
    older_target = release_descriptor(plugin_core_module)

    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        release=older_target,
        installed_release=installed,
        trigger=trigger,
        downgrade_confirmed=confirmed,
        release_was_activated=True,
    )

    assert decision.route == expected_route
    assert decision.requires_confirmation is requires_confirmation
    assert decision.reason == "release_downgrade"
    if trigger == "automatic":
        assert decision.status == "verification_failed"


def test_same_immutable_release_is_current_across_authorities_and_revisions(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    indexed = release_descriptor(plugin_core_module, revision=1)
    installed = installed_release_state(indexed)
    installed.release_revision = 2
    installed.authority = "provider_live"
    installed.candidate_fingerprint = "e" * 64

    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="release",
        release=indexed,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "none"
    assert decision.status == "current"


def test_cross_authority_lineage_ignores_unrelated_revision_numbers(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    indexed = release_descriptor(plugin_core_module, revision=20)
    installed = installed_release_state(indexed)
    provider = release_descriptor(
        plugin_core_module,
        revision=1,
        release_id="github:owner/example-plugin:v2.0.0",
        supersedes=[indexed.release_id],
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v2.0.0",
    )
    provider.authority = "provider_live"
    provider.candidate_fingerprint = "e" * 64

    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="release",
        release=provider,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "release_update"
    assert decision.status == "available"


def test_index_ancestor_of_provider_install_is_not_a_downgrade(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    indexed = release_descriptor(plugin_core_module, revision=99)
    provider_release = release_descriptor(
        plugin_core_module,
        revision=1,
        release_id="github:owner/example-plugin:v2.0.0",
        supersedes=[indexed.release_id],
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v2.0.0",
    )
    provider_release.authority = "provider_live"
    provider_release.candidate_fingerprint = "e" * 64
    installed = installed_release_state(provider_release)

    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="release",
        release=indexed,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "blocked"
    assert decision.status == "index_behind"
    assert decision.requires_confirmation is False


def test_provider_refresh_confirms_current_despite_stale_index(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    indexed = release_descriptor(plugin_core_module, revision=99)
    provider_release = release_descriptor(
        plugin_core_module,
        revision=1,
        release_id="github:owner/example-plugin:v2.0.0",
        supersedes=[indexed.release_id],
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v2.0.0",
    )
    provider_release.authority = "provider_live"
    provider_release.candidate_fingerprint = "e" * 64
    installed = installed_release_state(provider_release)

    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="release",
        release=indexed,
        installed_release=installed,
        release_was_activated=True,
        runtime_observation_state="current",
    )

    assert decision.route == "none"
    assert decision.status == "current"


def test_legacy_provider_install_without_observation_has_unknown_status(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    indexed = release_descriptor(plugin_core_module, revision=99)
    provider_release = release_descriptor(
        plugin_core_module,
        revision=1,
        release_id="github:owner/example-plugin:v2.0.0",
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v2.0.0",
    )
    installed = installed_release_state(provider_release)
    installed.authority = "provider_live"
    installed.candidate_fingerprint = "e" * 64
    installed.lineage_complete = False

    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="release",
        release=indexed,
        installed_release=installed,
        release_was_activated=True,
    )

    assert decision.route == "blocked"
    assert decision.status == "provider_status_unknown"


def test_mutated_provider_tag_blocks_cross_authority_reconciliation(
    plugin_core_module,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    indexed = release_descriptor(plugin_core_module)
    installed = installed_release_state(indexed)
    installed.authority = "provider_live"
    installed.candidate_fingerprint = "e" * 64

    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="release",
        release=indexed,
        installed_release=installed,
        release_was_activated=True,
        runtime_observation_state="tag_mutated",
    )

    assert decision.route == "blocked"
    assert decision.status == "verification_failed"
    assert decision.reason == "tag_mutated"


@pytest.mark.parametrize(
    ("git_status", "expected_status"),
    [("current", "git_current"), ("available", "git_available")],
)
def test_git_channel_status_remains_available_through_coordinator(
    plugin_core_module, git_status, expected_status
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module, preferred="git")

    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="git",
        git_status=git_status,
    )

    assert decision.route == "git_status"
    assert decision.status == expected_status


@pytest.mark.parametrize(
    (
        "migration_mode",
        "migration_evidence",
        "trigger",
        "expected_route",
        "expected_reason",
    ),
    [
        (
            "automatic",
            "commit_source_archive",
            "automatic",
            "release_migration",
            "",
        ),
        (
            "automatic",
            "commit_source_archive",
            "manual",
            "release_migration",
            "",
        ),
        (
            "manual",
            "unverified_asset",
            "manual",
            "release_migration",
            "",
        ),
        (
            "manual",
            "unverified_asset",
            "automatic",
            "blocked",
            "release_requires_manual_migration",
        ),
        (
            "blocked",
            "unverified_asset",
            "manual",
            "blocked",
            "release_not_migration_eligible",
        ),
        (
            "blocked",
            "unverified_asset",
            "automatic",
            "blocked",
            "release_not_migration_eligible",
        ),
    ],
)
def test_git_install_uses_release_migration_routing_matrix(
    plugin_core_module,
    migration_mode,
    migration_evidence,
    trigger,
    expected_route,
    expected_reason,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    release = release_descriptor(
        plugin_core_module,
        provenance=(
            "forge_source_archive"
            if migration_evidence == "commit_source_archive"
            else "attached_asset"
        ),
        migration_mode=migration_mode,
        migration_evidence=migration_evidence,
    )

    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="git",
        release=release,
        git_status="available",
        trigger=trigger,
    )

    assert decision.route == expected_route
    assert decision.reason == expected_reason
    assert decision.status == (
        "migration_available"
        if expected_route == "release_migration"
        else "migration_waiting_for_release"
    )


@pytest.mark.parametrize("trigger", ["manual", "automatic"])
def test_migration_preserves_explicit_manual_or_automatic_trigger(
    plugin_core_module, trigger
):
    coordinator, git_strategy, release_strategy = make_coordinator(
        plugin_core_module
    )
    entry = registry_entry(plugin_core_module)
    target = release_descriptor(plugin_core_module)
    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="git",
        release=target,
        trigger=trigger,
    )

    result = coordinator.execute(entry, decision)

    assert decision.route == "release_migration"
    assert decision.trigger == trigger
    assert release_strategy.calls == [
        ("migrate", "ExamplePlugin", target.release_id, trigger)
    ]
    assert git_strategy.calls == []
    assert result == (True, "release operation")


def test_coordinator_rejects_ambiguous_trigger_values(plugin_core_module):
    coordinator, _, _ = make_coordinator(plugin_core_module)

    with pytest.raises(ValueError):
        decide(
            coordinator,
            registry_entry(plugin_core_module),
            operation="update",
            installed_mode="git",
            release=release_descriptor(plugin_core_module),
            trigger="scheduled",
        )


@pytest.mark.parametrize(
    (
        "operation",
        "installed_mode",
        "has_release",
        "expected_call",
    ),
    [
        ("install", "absent", True, "release_install"),
        ("install", "absent", False, "git_install"),
        ("update", "release", True, "release_update"),
        ("update", "git", True, "release_migration"),
        ("update", "git", False, "git_update"),
    ],
)
def test_execute_routes_new_install_update_and_migration_to_one_strategy(
    plugin_core_module,
    operation,
    installed_mode,
    has_release,
    expected_call,
):
    coordinator, git_strategy, release_strategy = make_coordinator(
        plugin_core_module
    )
    entry = registry_entry(plugin_core_module)
    current = release_descriptor(plugin_core_module)
    installed = (
        installed_release_state(current)
        if installed_mode == "release"
        else None
    )
    target = None
    if has_release:
        target = (
            release_descriptor(
                plugin_core_module,
                revision=8,
                release_id="github:owner/example-plugin:v1.5.0",
                supersedes=[current.release_id],
                version="1.5.0",
                tag="v1.5.0",
                commit=COMMIT_2,
                tree_sha256=TREE_2,
                artifact_sha256=ARTIFACT_2,
                root_prefix="example-plugin-v1.5.0",
            )
            if installed_mode == "release"
            else current
        )
    decision = decide(
        coordinator,
        entry,
        operation=operation,
        installed_mode=installed_mode,
        release=target,
        installed_release=installed,
        trigger="automatic",
        git_status="available",
        preference=("keep_git" if not has_release and installed_mode == "git" else None),
        release_was_activated=installed_mode == "release",
    )

    result = coordinator.execute(
        entry,
        decision,
        queue_on_lock=False,
    )

    assert decision.route == expected_call
    if expected_call == "git_install":
        assert git_strategy.calls == [("install", "ExamplePlugin")]
        assert release_strategy.calls == []
        assert result == (True, "git install")
    elif expected_call == "git_update":
        assert git_strategy.calls == [
            ("update", "ExamplePlugin", False)
        ]
        assert release_strategy.calls == []
        assert result == (True, "git update")
    else:
        expected_release_operation = {
            "release_install": "install",
            "release_update": "update",
            "release_migration": "migrate",
        }[expected_call]
        assert release_strategy.calls == [
            (
                expected_release_operation,
                "ExamplePlugin",
                target.release_id,
                "automatic",
            )
        ]
        assert git_strategy.calls == []
        assert result == (True, "release operation")


def test_failed_release_operation_never_falls_back_to_git(
    plugin_core_module,
):
    coordinator, git_strategy, release_strategy = make_coordinator(
        plugin_core_module,
        release_result=(False, "artifact digest mismatch"),
    )
    entry = registry_entry(plugin_core_module)
    target = release_descriptor(plugin_core_module)
    decision = decide(
        coordinator,
        entry,
        operation="install",
        installed_mode="absent",
        release=target,
        trigger="automatic",
    )

    result = coordinator.execute(entry, decision)

    assert result == (False, "artifact digest mismatch")
    assert release_strategy.calls == [
        (
            "install",
            "ExamplePlugin",
            target.release_id,
            "automatic",
        )
    ]
    assert git_strategy.calls == []


def test_execute_preserves_existing_git_status_wrapper_signature(
    plugin_core_module,
):
    coordinator, git_strategy, release_strategy = make_coordinator(
        plugin_core_module
    )
    entry = registry_entry(plugin_core_module, preferred="git")
    decision = decide(
        coordinator,
        entry,
        operation="status",
        installed_mode="git",
        git_status="available",
        trigger="automatic",
    )

    result = coordinator.execute(entry, decision)

    assert result == "git status"
    assert git_strategy.calls == [
        ("check_for_update", "ExamplePlugin")
    ]
    assert release_strategy.calls == []


def test_status_notifications_distinguish_updates_from_release_channel_switches(
    plugin_core_module,
    monkeypatch,
):
    coordinator, _, _ = make_coordinator(plugin_core_module)
    entry = registry_entry(plugin_core_module)
    target = release_descriptor(plugin_core_module)
    notifications = []
    monkeypatch.setattr(
        coordinator.plugin,
        "fnSelectedNotify",
        lambda plugin_key: notifications.append(("update", plugin_key)),
    )
    monkeypatch.setattr(
        coordinator.plugin,
        "fnReleaseChannelNotify",
        lambda plugin_key, version: notifications.append(
            ("release_channel", plugin_key, version)
        ),
    )

    decisions = iter(
        [
            plugin_core_module.ReleaseManagementDecision(
                route="release_update",
                status="available",
                release=target,
                trigger="automatic",
            ),
            plugin_core_module.ReleaseManagementDecision(
                route="release_migration",
                status="migration_available",
                release=target,
                trigger="automatic",
            ),
            plugin_core_module.ReleaseManagementDecision(
                route="blocked",
                status="migration_waiting_for_release",
                reason="installed_head_ahead",
                release=target,
                trigger="automatic",
            ),
            plugin_core_module.ReleaseManagementDecision(
                route="none",
                status="current",
                release=target,
                trigger="automatic",
            ),
        ]
    )
    monkeypatch.setattr(
        coordinator,
        "_runtime_decision",
        lambda requested_entry, operation, trigger: next(decisions),
    )

    coordinator.check_for_update(entry, trigger="automatic")
    coordinator.check_for_update(entry, trigger="automatic")
    coordinator.check_for_update(entry, trigger="automatic")
    coordinator.check_for_update(entry, trigger="automatic")

    assert notifications == [
        ("update", "ExamplePlugin"),
        ("release_channel", "ExamplePlugin", "1.4.0"),
    ]


def test_blocked_release_managed_operation_calls_neither_strategy(
    plugin_core_module,
):
    coordinator, git_strategy, release_strategy = make_coordinator(
        plugin_core_module
    )
    entry = registry_entry(plugin_core_module)
    decision = decide(
        coordinator,
        entry,
        operation="update",
        installed_mode="release",
        metadata_authorized=False,
        metadata_reason="expired",
        release_was_activated=True,
    )

    result = coordinator.execute(entry, decision)

    assert result[0] is False
    assert "expired" in result[1].lower()
    assert git_strategy.calls == []
    assert release_strategy.calls == []


def test_base_plugin_wraps_existing_git_strategy_in_release_coordinator(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()

    assert isinstance(
        plugin.install_update_strategy,
        plugin_core_module.ReleaseManagementCoordinator,
    )
    assert isinstance(
        plugin.install_update_strategy.git_strategy,
        plugin_core_module.GitInstallUpdateStrategy,
    )


def runtime_selection(plugin_core_module, release):
    release_index = plugin_core_module.ReleaseIndex(
        schema_version=1,
        sequence=42,
        generated_at="2026-07-18T08:00:00Z",
        expires_at="2026-07-25T08:00:00Z",
        registry_sha256="0" * 64,
        plugins={"ExamplePlugin": release},
        tombstones={},
    )
    return plugin_core_module.ReleaseMetadataSelection(
        sequence=42,
        registry_bytes=b"{}",
        release_index_bytes=b"{}",
        release_index=release_index,
        release_authorized=True,
    )


def configure_expiring_runtime(
    plugin_core_module,
    tmp_path,
    *,
    installed_mode,
    monkeypatch,
):
    plugins_dir, _manager_dir = configure_home(
        plugin_core_module, tmp_path
    )
    plugin = plugin_core_module.BasePlugin()
    entry = registry_entry(plugin_core_module)
    release = release_descriptor(plugin_core_module)
    plugin.registry_entries[entry.key] = entry
    plugin_dir = plugins_dir / entry.key
    plugin_dir.mkdir()
    plugin.installed_plugin_folders[entry.key] = entry.key
    if installed_mode == "git":
        plugin_dir.joinpath(".git").mkdir()
    else:
        plugin_dir.joinpath(".pypluginstore.json").write_text(
            "{}", encoding="utf-8"
        )
        monkeypatch.setattr(
            plugin.install_metadata_service,
            "read",
            lambda path: installed_release_state(release),
        )

    current_time = [datetime(2026, 7, 24, 12, tzinfo=timezone.utc)]
    metadata_root = os.path.abspath(plugin.get_release_metadata_root())
    plugin.release_metadata_store = plugin_core_module.ReleaseMetadataStore(
        metadata_root,
        clock=lambda: current_time[0],
    )
    plugin.release_metadata_store_root = metadata_root
    plugin.release_metadata_selection = runtime_selection(
        plugin_core_module, release
    )
    return plugin, entry, release, current_time


def test_runtime_decision_rechecks_expiry_and_preserves_git_fallback(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin, entry, release, current_time = configure_expiring_runtime(
        plugin_core_module,
        tmp_path,
        installed_mode="git",
        monkeypatch=monkeypatch,
    )

    fresh = plugin.getReleaseManagementContext(
        entry,
        operation="update",
        trigger="automatic",
    )
    assert fresh["metadata_authorized"] is True
    assert fresh["release"] is release

    current_time[0] = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    decision = plugin.install_update_strategy._runtime_decision(
        entry, "update", "automatic"
    )

    assert plugin.release_metadata_selection.release_authorized is False
    assert plugin.release_metadata_selection.release_index is None
    assert decision.route == "git_update"
    assert decision.status == "git_available"
    assert "expired" in decision.reason.lower()


def test_runtime_decision_does_not_fall_back_for_release_install_after_expiry(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin, entry, _release, current_time = configure_expiring_runtime(
        plugin_core_module,
        tmp_path,
        installed_mode="release",
        monkeypatch=monkeypatch,
    )
    current_time[0] = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)

    decision = plugin.install_update_strategy._runtime_decision(
        entry, "update", "automatic"
    )

    assert decision.route == "blocked"
    assert decision.status == "release_metadata_unavailable"
    assert "expired" in decision.reason.lower()


def test_release_install_uses_provider_live_candidate_after_explicit_refresh(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugin, entry, indexed_release, _current_time = (
        configure_expiring_runtime(
            plugin_core_module,
            tmp_path,
            installed_mode="release",
            monkeypatch=monkeypatch,
        )
    )
    runtime_release = release_descriptor(
        plugin_core_module,
        revision=indexed_release.revision + 1,
        release_id="github:owner/example-plugin:v2.0.0",
        supersedes=[indexed_release.release_id],
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v2.0.0",
    )
    runtime_release.authority = "provider_live"
    runtime_release.candidate_fingerprint = "e" * 64
    runtime_release.anchor_release_id = indexed_release.release_id
    runtime_release.anchor_revision = indexed_release.revision
    runtime_release.anchor_authority = "release_index"
    runtime_release.anchor_index_sequence = 42
    plugin.runtime_release_observations[entry.key] = (
        plugin_core_module.RuntimeReleaseObservation(
            state="available",
            release=runtime_release,
            message="Verified directly from the release provider.",
            checked_at="2026-07-24T12:00:00Z",
            anchor_release_id=indexed_release.release_id,
            anchor_revision=indexed_release.revision,
            anchor_authority="release_index",
            anchor_index_sequence=42,
        )
    )

    context = plugin.getReleaseManagementContext(
        entry,
        operation="update",
        trigger="manual",
    )
    decision = plugin.install_update_strategy._runtime_decision(
        entry,
        "update",
        "manual",
    )

    assert context["release"] is runtime_release
    assert decision.route == "release_update"
    assert decision.release is runtime_release
    management = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        plugin.get_host().plugins_dir(),
    )[entry.key]
    assert management["status"] == "available"
    assert management["available_version"] == "2.0.0"
    assert management["available_revision"] == runtime_release.revision
    assert management["verification_status"] == "verified_on_host"
    assert management["verification_message"] == (
        "Verified directly from the release provider."
    )
    assert management["summary"] == "Release available"
    assert [
        action["id"]
        for action in management["actions"]
        if action["enabled"]
    ] == ["update"]
    monkeypatch.setattr(
        plugin_core_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Release versions must not come from the Git branch")
        ),
    )
    versions = plugin.get_plugin_versions(
        [entry.key],
        {entry.key: "available"},
        plugin.get_host().plugins_dir(),
    )
    assert versions[entry.key] == {
        "installed": indexed_release.version,
        "available": runtime_release.version,
    }

    plugin.release_metadata_selection.release_index.plugins = {}
    missing_entry_context = plugin.getReleaseManagementContext(
        entry,
        operation="update",
        trigger="manual",
    )
    missing_entry_decision = plugin.install_update_strategy._runtime_decision(
        entry,
        "update",
        "manual",
    )

    assert missing_entry_context["runtime_observation_state"] == "available"
    assert missing_entry_context["release"] is None
    assert missing_entry_decision.route == "blocked"
    assert missing_entry_decision.status == "release_metadata_unavailable"
    assert missing_entry_decision.reason == "release_entry_missing"


def test_provider_current_observation_hides_stale_index_target(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugin, entry, indexed_release, current_time = (
        configure_expiring_runtime(
            plugin_core_module,
            tmp_path,
            installed_mode="release",
            monkeypatch=monkeypatch,
        )
    )
    indexed_release.revision = 1
    provider_release = release_descriptor(
        plugin_core_module,
        revision=2,
        release_id="github:owner/example-plugin:v2.0.0",
        supersedes=[indexed_release.release_id],
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v2.0.0",
    )
    provider_release.authority = "provider_live"
    provider_release.candidate_fingerprint = "e" * 64
    provider_release.anchor_release_id = indexed_release.release_id
    provider_release.anchor_revision = indexed_release.revision
    provider_release.anchor_authority = "release_index"
    provider_release.anchor_index_sequence = 42
    metadata_document = install_metadata_document(
        COMMIT_2,
        TREE_2,
        provider_release.revision,
        provider_release.release_id,
    )
    metadata_document.update(
        {
            "authority": "provider_live",
            "candidate_fingerprint": "e" * 64,
            "supersedes": [indexed_release.release_id],
            "lineage_complete": True,
            "anchor_release_id": indexed_release.release_id,
            "anchor_revision": indexed_release.revision,
            "anchor_authority": "release_index",
            "anchor_index_sequence": 42,
            "artifact_sha256": ARTIFACT_2,
            "index_sequence": 42,
        }
    )
    plugin_dir = plugin.get_host().plugins_dir()
    with open(
        os.path.join(plugin_dir, entry.key, ".pypluginstore.json"),
        "w",
        encoding="utf-8",
    ) as metadata_file:
        json.dump(metadata_document, metadata_file)
    plugin.install_metadata_service = plugin_core_module.InstallMetadataService(
        plugin
    )
    index_behind = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        plugin.get_host().plugins_dir(),
    )[entry.key]

    assert index_behind["status"] == "index_behind"
    assert index_behind["summary"] == "Release - index behind"
    assert index_behind["available_version"] == provider_release.version
    assert index_behind["available_revision"] == provider_release.revision
    assert index_behind["verification_status"] == "verified_on_host"
    assert index_behind["updateable"] is False
    assert not next(
        action
        for action in index_behind["actions"]
        if action["id"] == "update"
    )["enabled"]

    plugin.runtime_release_observations[entry.key] = (
        plugin_core_module.RuntimeReleaseObservation(
            state="current",
            release=None,
            message="Installed release is current at the provider.",
            checked_at="2026-07-24T12:00:00Z",
            anchor_release_id=provider_release.release_id,
            anchor_revision=provider_release.revision,
            anchor_authority="provider_live",
            anchor_index_sequence=42,
        )
    )

    management = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        plugin.get_host().plugins_dir(),
    )[entry.key]
    versions = plugin.get_plugin_versions(
        [entry.key],
        {entry.key: "current"},
        plugin.get_host().plugins_dir(),
    )

    assert management["status"] == "current"
    assert management["summary"] == "Release - current"
    assert management["available_version"] == provider_release.version
    assert management["available_revision"] == provider_release.revision
    assert management["verification_status"] == "verified_on_host"
    assert management["updateable"] is False
    assert not next(
        action
        for action in management["actions"]
        if action["id"] == "update"
    )["enabled"]
    assert versions[entry.key] == {
        "installed": provider_release.version,
        "available": provider_release.version,
    }

    monkeypatch.setattr(
        plugin.release_transaction_manager,
        "plugin_lifecycle_state",
        lambda plugin_key: {
            "rollback_available": True,
            "rollback_channel": "release",
            "rollback_version": "1.4.0",
            "rollback_revision": 1,
            "restart_pending": False,
        },
    )
    plugin.runtime_release_observations[entry.key] = (
        plugin_core_module.RuntimeReleaseObservation(
            state="tag_mutated",
            release=None,
            message="The installed release tag resolved to a changed commit.",
            checked_at="2026-07-24T12:05:00Z",
            anchor_release_id=provider_release.release_id,
            anchor_revision=provider_release.revision,
            anchor_authority="provider_live",
            anchor_index_sequence=42,
        )
    )
    mutated = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        plugin.get_host().plugins_dir(),
    )[entry.key]

    assert mutated["status"] == "verification_failed"
    assert mutated["verification_status"] == "failed"
    assert "changed commit" in mutated["verification_message"]
    assert mutated["updateable"] is False
    assert [
        action["id"]
        for action in mutated["actions"]
        if action["enabled"]
    ] == ["rollback"]

    current_time[0] = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    expired = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        plugin.get_host().plugins_dir(),
    )[entry.key]

    assert expired["status"] == "release_metadata_unavailable"
    assert expired["updateable"] is False
    assert "expired" in expired["verification_message"].lower()


def test_legacy_provider_metadata_reports_unknown_instead_of_downgrade(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugin, entry, indexed_release, _current_time = (
        configure_expiring_runtime(
            plugin_core_module,
            tmp_path,
            installed_mode="release",
            monkeypatch=monkeypatch,
        )
    )
    indexed_release.revision = 1
    provider_release_id = "github:owner/example-plugin:v2.0.0"
    document = install_metadata_document(
        COMMIT_2,
        TREE_2,
        2,
        provider_release_id,
    )
    document.update(
        {
            "schema": 3,
            "authority": "provider_live",
            "candidate_fingerprint": "e" * 64,
            "artifact_sha256": ARTIFACT_2,
            "index_sequence": 42,
        }
    )
    for field_name in (
        "supersedes",
        "lineage_complete",
        "anchor_release_id",
        "anchor_revision",
        "anchor_authority",
        "anchor_index_sequence",
    ):
        document.pop(field_name)
    metadata_path = os.path.join(
        plugin.get_host().plugins_dir(),
        entry.key,
        ".pypluginstore.json",
    )
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(document, metadata_file)
    plugin.install_metadata_service = plugin_core_module.InstallMetadataService(
        plugin
    )

    management = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        plugin.get_host().plugins_dir(),
    )[entry.key]

    assert management["status"] == "provider_status_unknown"
    assert management["summary"] == "Release - provider status unknown"
    assert management["available_version"] == "2.0.0"
    assert management["available_revision"] == 2
    assert management["updateable"] is False
    with open(metadata_path, "r", encoding="utf-8") as metadata_file:
        assert json.load(metadata_file)["schema"] == 4


def test_git_install_selects_host_verified_latest_release_for_direct_migration(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugin, entry, indexed_release, _current_time = (
        configure_expiring_runtime(
            plugin_core_module,
            tmp_path,
            installed_mode="git",
            monkeypatch=monkeypatch,
        )
    )
    runtime_release = release_descriptor(
        plugin_core_module,
        revision=indexed_release.revision + 1,
        release_id="github:owner/example-plugin:v2.0.0",
        supersedes=[
            *indexed_release.supersedes,
            indexed_release.release_id,
        ],
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        tree_sha256=TREE_2,
        artifact_sha256=ARTIFACT_2,
        root_prefix="example-plugin-v2.0.0",
    )
    runtime_release.authority = "provider_live"
    runtime_release.candidate_fingerprint = "e" * 64
    runtime_release.anchor_release_id = indexed_release.release_id
    runtime_release.anchor_revision = indexed_release.revision
    runtime_release.anchor_authority = "release_index"
    runtime_release.anchor_index_sequence = 42
    plugin.runtime_release_observations[entry.key] = (
        plugin_core_module.RuntimeReleaseObservation(
            state="available",
            release=runtime_release,
            message="Verified directly from the release provider.",
            checked_at="2026-07-24T12:00:00Z",
            anchor_release_id=indexed_release.release_id,
            anchor_revision=indexed_release.revision,
            anchor_authority="release_index",
            anchor_index_sequence=42,
        )
    )

    context = plugin.getReleaseManagementContext(
        entry,
        operation="update",
        trigger="manual",
    )
    decision = plugin.install_update_strategy._runtime_decision(
        entry,
        "update",
        "manual",
    )

    assert context["installed_mode"] == "git"
    assert context["release"] is runtime_release
    assert decision.route == "release_migration"
    assert decision.release is runtime_release
    assert decision.release.version == "2.0.0"

    plugin.release_metadata_selection.sequence = 43
    plugin.release_metadata_selection.release_index.sequence = 43
    indexed_release.index_sequence = 43
    changed_generation = plugin.getReleaseManagementContext(
        entry,
        operation="update",
        trigger="manual",
    )

    assert changed_generation["release"] is indexed_release
    assert changed_generation["runtime_observation_state"] == ""


def test_management_map_rechecks_expiry_before_status_decisions(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin, entry, _release, current_time = configure_expiring_runtime(
        plugin_core_module,
        tmp_path,
        installed_mode="git",
        monkeypatch=monkeypatch,
    )
    current_time[0] = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)

    management = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        plugin.get_host().plugins_dir(),
    )[entry.key]

    assert plugin.release_metadata_selection.release_authorized is False
    assert management["status"] == "git_current"
    assert management["channel"] == "git"
    assert management["release_available"] is False
    assert management["migration_action_state"] == "blocked"
    assert "expired" in management["verification_message"].lower()


def test_local_override_ignores_persisted_release_preference(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _manager_dir = configure_home(
        plugin_core_module, tmp_path
    )
    plugin = plugin_core_module.BasePlugin()
    public_registry = {
        "ExamplePlugin": [
            "owner",
            "example-plugin",
            "Public package",
            "main",
        ]
    }
    local_registry = {
        "ExamplePlugin": {
            "package_id": "ExamplePlugin",
            "domoticz_key": "EXAMPLE",
            "description": "Local override",
            "repository": {
                "url": "https://github.com/owner/example-plugin.git",
                "branch": "main",
            },
            "platforms": ["linux"],
        }
    }
    plugin.apply_registry_sources(public_registry, local_registry)
    entry = plugin.get_registry_entry("ExamplePlugin")
    plugin_dir = plugins_dir / entry.key
    plugin_dir.joinpath(".git").mkdir(parents=True)
    plugin.installed_plugin_folders[entry.key] = entry.key
    release = release_descriptor(plugin_core_module)
    selection = runtime_selection(plugin_core_module, release)
    monkeypatch.setattr(
        plugin,
        "getCurrentReleaseMetadataSelection",
        lambda: selection,
    )
    preference_lookups = []

    def release_preference(repository_identity):
        preference_lookups.append(repository_identity)
        return "release"

    monkeypatch.setattr(
        plugin.channel_preference_service,
        "get",
        release_preference,
    )

    context = plugin.getReleaseManagementContext(
        entry,
        operation="update",
        trigger="manual",
    )
    decision = plugin.install_update_strategy._runtime_decision(
        entry,
        "update",
        "manual",
    )
    management = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        str(plugins_dir),
    )[entry.key]

    assert entry.local is True
    assert entry.delivery.preferred == "git"
    assert preference_lookups == []
    assert context["channel_preference"] is None
    assert context["release"] is None
    assert decision.route == "git_update"
    assert management["channel"] == "git"
    assert management["status"] == "git_current"
    assert management["updateable"] is True
    assert management["release_available"] is False
    assert management["migration_action_state"] == "blocked"


def test_local_override_on_release_install_requires_git_checkout(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, _manager_dir = configure_home(
        plugin_core_module, tmp_path
    )
    plugin = plugin_core_module.BasePlugin()
    public_registry = {
        "ExamplePlugin": [
            "owner",
            "example-plugin",
            "Public package",
            "main",
        ]
    }
    local_registry = {
        "ExamplePlugin": {
            "package_id": "ExamplePlugin",
            "domoticz_key": "EXAMPLE",
            "description": "Local override",
            "repository": {
                "url": "https://github.com/owner/example-plugin.git",
                "branch": "main",
            },
            "platforms": ["linux"],
        }
    }
    plugin.apply_registry_sources(public_registry, local_registry)
    entry = plugin.get_registry_entry("ExamplePlugin")
    plugin_dir = plugins_dir / entry.key
    plugin_dir.mkdir(parents=True)
    plugin_dir.joinpath(".pypluginstore.json").write_text(
        json.dumps(
            install_metadata_document(
                COMMIT_1,
                TREE_1,
                7,
                "github:owner/example-plugin:v1.4.0",
            )
        ),
        encoding="utf-8",
    )
    installed = plugin.getInstalledPlugins(plugins_dir)
    selection = runtime_selection(
        plugin_core_module,
        release_descriptor(plugin_core_module),
    )
    monkeypatch.setattr(
        plugin,
        "getCurrentReleaseMetadataSelection",
        lambda: selection,
    )
    monkeypatch.setattr(
        plugin.release_transaction_manager,
        "plugin_lifecycle_state",
        lambda plugin_key: {
            "rollback_available": True,
            "rollback_channel": "release",
            "rollback_version": "1.3.0",
            "rollback_revision": 6,
            "restart_pending": False,
        },
    )

    management = plugin.getPluginManagementMap(
        installed,
        {entry.key: "current"},
        {},
        str(plugins_dir),
    )[entry.key]
    update_calls = []
    responses = []

    def record_update(*args, **kwargs):
        update_calls.append((args, kwargs))
        return True, "unexpected"

    monkeypatch.setattr(
        plugin,
        "UpdatePythonPlugin",
        record_update,
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand(
        {"action": "update", "plugin_key": entry.key}
    )

    assert management["channel"] == "release"
    assert management["status"] == "local_override_requires_git_checkout"
    assert management["updateable"] is False
    assert (
        "return to the previous git version"
        in management["verification_message"].lower()
    )
    assert management["rollback_available"] is True
    assert management["rollback_channel"] == "release"
    assert management["rollback_version"] == "1.3.0"
    assert next(
        action
        for action in management["actions"]
        if action["id"] == "rollback"
    )["label"] == "Restore v1.3.0"
    assert update_calls == []
    assert responses[0]["status"] == "error"
    assert responses[0]["action"] == "update"
    assert responses[0]["plugin_key"] == entry.key
    assert "local registry override" in responses[0]["message"].lower()


def test_invalid_local_registry_pauses_public_release_migration(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, manager_dir = configure_home(
        plugin_core_module, tmp_path
    )
    plugin = plugin_core_module.BasePlugin()
    manager_dir.joinpath("registry_local.json").write_text(
        "{broken",
        encoding="utf-8",
    )

    assert plugin.load_local_registry() is None
    assert plugin.local_registry_error

    public_registry = {
        "ExamplePlugin": [
            "owner",
            "example-plugin",
            "Public package",
            "main",
        ]
    }
    plugin.apply_registry_sources(public_registry, None)
    entry = plugin.get_registry_entry("ExamplePlugin")
    plugin_dir = plugins_dir / entry.key
    plugin_dir.joinpath(".git").mkdir(parents=True)
    plugin.installed_plugin_folders[entry.key] = entry.key
    release = release_descriptor(plugin_core_module)
    selection = runtime_selection(plugin_core_module, release)
    monkeypatch.setattr(
        plugin,
        "getCurrentReleaseMetadataSelection",
        lambda: selection,
    )

    context = plugin.getReleaseManagementContext(
        entry,
        operation="status",
        trigger="manual",
    )
    management = plugin.getPluginManagementMap(
        [entry.key],
        {entry.key: "current"},
        {},
        str(plugins_dir),
    )[entry.key]
    action = plugin.executeReleaseManagementAction(
        action="use_release",
        plugin_key=entry.key,
    )

    assert context["metadata_authorized"] is False
    assert "local registry" in context["metadata_reason"].lower()
    assert context["release"] is None
    assert management["channel"] == "git"
    assert management["release_available"] is False
    assert management["migration_action_state"] == "blocked"
    assert "local registry" in management["verification_message"].lower()
    assert action["status"] == "error"
    assert "no authorized release" in action["message"].lower()

    manager_dir.joinpath("registry_local.json").unlink()
    assert plugin.load_local_registry() is None
    assert plugin.local_registry_error == ""


def test_update_and_status_wrappers_carry_explicit_trigger_without_changing_git_seam(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "ExamplePlugin": [
            "owner",
            "example-plugin",
            "Example plugin",
            "main",
            "",
        ]
    }
    calls = []

    class Wrapper:
        def update(self, entry, queue_on_lock=True, trigger="manual"):
            calls.append(("update", entry.key, queue_on_lock, trigger))
            return True, ""

        def check_for_update(self, entry, trigger="manual"):
            calls.append(("status", entry.key, trigger))
            return None

    plugin.install_update_strategy = Wrapper()

    assert plugin.UpdatePythonPlugin(
        "owner",
        "example-plugin",
        "ExamplePlugin",
        queue_on_lock=False,
        trigger="automatic",
    ) == (True, "")
    plugin.CheckForUpdatePythonPlugin(
        "owner",
        "example-plugin",
        "ExamplePlugin",
        trigger="automatic",
    )
    plugin.UpdatePythonPlugin(
        "owner",
        "example-plugin",
        "ExamplePlugin",
    )

    assert calls == [
        ("update", "ExamplePlugin", False, "automatic"),
        ("status", "ExamplePlugin", "automatic"),
        ("update", "ExamplePlugin", True, "manual"),
    ]
