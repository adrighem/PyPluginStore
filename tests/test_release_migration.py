import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


GIT = shutil.which("git")
PLUGIN_KEY = "ExamplePlugin"
GITHUB_IDENTITY = "github.com/owner/example-plugin"
GITHUB_REMOTE = "https://github.com/owner/example-plugin.git"


pytestmark = pytest.mark.skipif(GIT is None, reason="Git is required")


class MutableClock:
    def __init__(self, current):
        self.current = current

    def __call__(self):
        return self.current

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


def git(repository, *arguments, check=True):
    """Run Git against a temporary repository and return the completed call."""
    result = subprocess.run(
        [GIT, *arguments],
        cwd=str(repository),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            "Git command failed: "
            + " ".join([GIT, *arguments])
            + "\nstdout: "
            + result.stdout
            + "\nstderr: "
            + result.stderr
        )
    return result


def head_commit(repository):
    return git(repository, "rev-parse", "HEAD").stdout.strip()


def commit_files(repository, changes, message):
    """Apply path/content changes and create one commit."""
    repository = Path(repository)
    for relative_path, contents in changes.items():
        path = repository / relative_path
        if contents is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    git(repository, "add", "--all")
    git(repository, "commit", "--quiet", "--message", message)
    return head_commit(repository)


def initialize_repository(path, remote_url=GITHUB_REMOTE):
    """Create a small, clean repository that resembles a plugin checkout."""
    path = Path(path)
    path.mkdir(parents=True)
    git(path, "init", "--quiet")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    git(path, "config", "user.name", "PyPluginStore tests")
    git(path, "config", "user.email", "tests@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    initial = commit_files(
        path,
        {
            "plugin.py": "print('plugin')\n",
            "config/settings.json": "{}\n",
            "README.md": "Example plugin\n",
        },
        "initial plugin",
    )
    if remote_url is not None:
        git(path, "remote", "add", "origin", remote_url)
    return path, initial


def repository_snapshot(repository):
    """Capture the HEAD, worktree status, and index bytes."""
    status = git(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout
    index_path = Path(
        git(repository, "rev-parse", "--git-path", "index").stdout.strip()
    )
    if not index_path.is_absolute():
        index_path = Path(repository) / index_path
    return head_commit(repository), status, index_path.read_bytes()


def evaluate_preflight(
    plugin_core_module,
    repository,
    release_commit,
    *,
    trigger="automatic",
    repository_identity=GITHUB_IDENTITY,
    mutable_paths=(),
    plugin=None,
):
    plugin = plugin or plugin_core_module.BasePlugin()
    preflight = plugin_core_module.GitMigrationPreflight(plugin)
    return preflight.evaluate(
        plugin_key=PLUGIN_KEY,
        plugin_dir=str(repository),
        repository_identity=repository_identity,
        release_commit=release_commit,
        trigger=trigger,
        mutable_paths=list(mutable_paths),
    )


def migration_target(release_commit):
    return {
        "release_id": "github:owner/example-plugin:v1.0.0",
        "release_revision": 1,
        "commit": release_commit,
        "artifact_tree_sha256": "a" * 64,
    }


def approval_coordinator(plugin_core_module, clock):
    return plugin_core_module.ReleaseManagementCoordinator(
        plugin_core_module.BasePlugin(),
        git_strategy=object(),
        release_strategy=object(),
        confirmation_clock=clock,
        confirmation_ttl_seconds=300,
    )


@pytest.mark.parametrize("trigger", ["automatic", "manual"])
def test_clean_checkout_at_release_commit_is_migration_eligible(
    plugin_core_module, tmp_path, trigger
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger=trigger,
    )

    assert result.allowed is True
    assert result.status == "migration_available"
    assert result.reason == "release_equals_head"
    assert result.relationship == "equal"
    assert result.installed_commit == release_commit
    assert result.release_commit == release_commit
    assert result.installed_repository_identity == GITHUB_IDENTITY
    assert result.trigger == trigger
    assert result.requires_confirmation is False
    assert result.tracked_changes == []
    assert result.untracked_files == []
    assert repository_snapshot(repository) == before


@pytest.mark.parametrize("trigger", ["automatic", "manual"])
def test_clean_checkout_allows_release_commit_that_descends_from_head(
    plugin_core_module, tmp_path, trigger
):
    repository, installed_commit = initialize_repository(tmp_path / "plugin")
    release_commit = commit_files(
        repository,
        {"plugin.py": "print('new release')\n"},
        "release",
    )
    git(repository, "checkout", "--quiet", "--detach", installed_commit)
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger=trigger,
    )

    assert result.allowed is True
    assert result.status == "migration_available"
    assert result.reason == "release_descends_from_head"
    assert result.relationship == "release_descendant"
    assert result.installed_commit == installed_commit
    assert result.release_commit == release_commit
    assert result.trigger == trigger
    assert result.requires_confirmation is False
    assert repository_snapshot(repository) == before


@pytest.mark.parametrize(
    ("remote_url", "repository_identity"),
    [
        pytest.param(
            "https://github.com/Owner/Example-Plugin.git",
            "github.com/owner/example-plugin",
            id="github-https",
        ),
        pytest.param(
            "git@gitlab.com:Group/Subgroup/Example-Plugin.git",
            "gitlab.com/group/subgroup/example-plugin",
            id="gitlab-scp",
        ),
        pytest.param(
            "ssh://git@codeberg.org/Team/Example-Plugin.git",
            "codeberg.org/team/example-plugin",
            id="codeberg-ssh",
        ),
        pytest.param(
            "https://forgejo.example.test/Team/Example-Plugin.git",
            "forgejo.example.test/team/example-plugin",
            id="forgejo-https",
        ),
        pytest.param(
            "https://gitea.example.test/Team/Example-Plugin.git",
            "gitea.example.test/team/example-plugin",
            id="gitea-https",
        ),
    ],
)
def test_repository_identity_matching_is_forge_neutral(
    plugin_core_module,
    tmp_path,
    remote_url,
    repository_identity,
):
    repository, release_commit = initialize_repository(
        tmp_path / "plugin",
        remote_url=remote_url,
    )

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        repository_identity=repository_identity,
    )

    assert result.allowed is True
    assert result.status == "migration_available"
    assert result.installed_repository_identity == repository_identity


@pytest.mark.parametrize(
    ("trigger", "reason", "requires_confirmation"),
    [
        pytest.param(
            "automatic",
            "installed_head_ahead",
            False,
            id="automatic-waits-for-release",
        ),
        pytest.param(
            "manual",
            "downgrade_confirmation_required",
            True,
            id="manual-can-confirm-downgrade",
        ),
    ],
)
def test_installed_head_ahead_of_release_never_migrates_silently(
    plugin_core_module,
    tmp_path,
    trigger,
    reason,
    requires_confirmation,
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    installed_commit = commit_files(
        repository,
        {"plugin.py": "print('local newer commit')\n"},
        "installed is ahead",
    )
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger=trigger,
    )

    assert result.allowed is False
    assert result.status == "migration_waiting_for_release"
    assert result.reason == reason
    assert result.relationship == "installed_ahead"
    assert result.installed_commit == installed_commit
    assert result.release_commit == release_commit
    assert result.trigger == trigger
    assert result.requires_confirmation is requires_confirmation
    assert result.message == (
        "The installed Git checkout contains commits newer than the available "
        "Release."
    )
    assert repository_snapshot(repository) == before


@pytest.mark.parametrize(
    ("trigger", "reason", "requires_confirmation"),
    [
        pytest.param(
            "automatic",
            "diverged_history",
            False,
            id="automatic-blocked",
        ),
        pytest.param(
            "manual",
            "downgrade_confirmation_required",
            True,
            id="manual-can-confirm-downgrade",
        ),
    ],
)
def test_diverged_history_never_migrates_silently(
    plugin_core_module,
    tmp_path,
    trigger,
    reason,
    requires_confirmation,
):
    repository, base_commit = initialize_repository(tmp_path / "plugin")
    git(repository, "checkout", "--quiet", "-b", "release")
    release_commit = commit_files(
        repository,
        {"plugin.py": "print('release line')\n"},
        "release line",
    )
    git(repository, "checkout", "--quiet", "-b", "installed", base_commit)
    installed_commit = commit_files(
        repository,
        {"plugin.py": "print('installed line')\n"},
        "installed line",
    )
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger=trigger,
    )

    assert result.allowed is False
    assert result.status == "migration_waiting_for_release"
    assert result.reason == reason
    assert result.relationship == "diverged"
    assert result.installed_commit == installed_commit
    assert result.release_commit == release_commit
    assert result.requires_confirmation is requires_confirmation
    assert result.message == (
        "The installed Git checkout contains commits that differ from the "
        "available Release."
    )
    assert repository_snapshot(repository) == before


@pytest.mark.parametrize(
    "dirty_kind",
    [
        pytest.param("unstaged", id="unstaged-modification"),
        pytest.param("staged", id="staged-modification"),
        pytest.param("deleted", id="tracked-deletion"),
    ],
)
def test_automatic_migration_rejects_every_kind_of_tracked_change(
    plugin_core_module, tmp_path, dirty_kind
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    settings = repository / "config" / "settings.json"
    if dirty_kind == "deleted":
        settings.unlink()
    else:
        settings.write_text('{"local": true}\n', encoding="utf-8")
    if dirty_kind == "staged":
        git(repository, "add", "config/settings.json")
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="automatic",
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_changes"
    assert result.reason == "tracked_changes"
    assert result.relationship == "equal"
    assert result.tracked_changes == ["config/settings.json"]
    assert result.preserved_paths == []
    assert repository_snapshot(repository) == before


def test_automatic_migration_reports_dirty_mutable_inventory_without_approval(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    (repository / "config" / "settings.json").write_text(
        '{"local": true}\n', encoding="utf-8"
    )

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="automatic",
        mutable_paths=["config/settings.json"],
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_changes"
    assert result.reason == "tracked_changes"
    assert result.tracked_changes == ["config/settings.json"]
    assert len(result.inventory_sha256) == 64
    assert result.preserved_paths == []


def test_manual_migration_returns_hash_bound_inventory_for_dirty_mutable_path(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    (repository / "config" / "settings.json").write_text(
        '{"local": true}\n', encoding="utf-8"
    )

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="manual",
        mutable_paths=["config/settings.json"],
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_changes"
    assert result.reason == "preservation_approval_required"
    assert result.requires_approval is True
    assert result.approval_required_paths == ["config/settings.json"]
    assert len(result.inventory_sha256) == 64
    assert result.preserved_paths == []


def test_manual_migration_inventory_digest_changes_with_dirty_content(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    settings = repository / "config" / "settings.json"
    settings.write_text(
        '{"local": true}\n', encoding="utf-8"
    )
    first_before = repository_snapshot(repository)

    first = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="manual",
        mutable_paths=["config/settings.json"],
    )
    assert repository_snapshot(repository) == first_before

    settings.write_text('{"local": "changed"}\n', encoding="utf-8")
    second_before = repository_snapshot(repository)
    second = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="manual",
        mutable_paths=["config/settings.json"],
    )

    assert first.allowed is False
    assert second.allowed is False
    assert first.reason == "preservation_approval_required"
    assert second.reason == "preservation_approval_required"
    assert first.inventory_sha256 != second.inventory_sha256
    assert repository_snapshot(repository) == second_before


def test_manual_migration_rejects_tracked_path_not_in_reviewed_policy(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    (repository / "README.md").write_text(
        "locally rewritten documentation\n", encoding="utf-8"
    )

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="manual",
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_changes"
    assert result.reason == "tracked_path_not_mutable"
    assert result.tracked_changes == ["README.md"]
    assert result.preserved_paths == []


def test_automatic_migration_blocks_unknown_untracked_files(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    state_file = repository / "runtime" / "state.json"
    state_file.parent.mkdir()
    state_file.write_text('{"counter": 1}\n', encoding="utf-8")
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="automatic",
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_files"
    assert result.reason == "untracked_files"
    assert result.tracked_changes == []
    assert result.untracked_files == ["runtime/state.json"]
    assert result.preserved_paths == []
    assert repository_snapshot(repository) == before


def test_manual_migration_requires_approval_for_unknown_untracked_noncode_file(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    state_file = repository / "runtime" / "state.json"
    state_file.parent.mkdir()
    state_file.write_text('{"counter": 1}\n', encoding="utf-8")

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="manual",
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_files"
    assert result.reason == "preservation_approval_required"
    assert result.requires_approval is True
    assert result.approval_required_paths == ["runtime/state.json"]
    assert len(result.inventory_sha256) == 64
    assert result.preserved_paths == []


def test_migration_inventory_approval_challenge_is_opaque_and_hash_bound(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    state_file = repository / "runtime" / "state.json"
    state_file.parent.mkdir()
    state_file.write_text('{"counter": 1}\n', encoding="utf-8")
    before = repository_snapshot(repository)
    preflight = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        trigger="manual",
    )
    assert preflight.allowed is False
    assert preflight.reason == "preservation_approval_required"
    assert preflight.approval_required_paths == ["runtime/state.json"]
    assert repository_snapshot(repository) == before

    clock = MutableClock(datetime(2026, 7, 18, 12, tzinfo=timezone.utc))
    coordinator = approval_coordinator(plugin_core_module, clock)
    target = migration_target(release_commit)

    challenge = coordinator.issue_confirmation_challenge(
        kind="migration_inventory",
        plugin_key=PLUGIN_KEY,
        action="update",
        target=target,
        inventory_sha256=preflight.inventory_sha256,
    )

    assert set(challenge) == {"kind", "token", "message"}
    assert challenge["kind"] == "migration_inventory"
    assert challenge["token"]
    assert challenge["token"] != preflight.inventory_sha256
    assert "runtime/state.json" not in str(challenge)

    approved_inventory_sha256 = (
        coordinator.consume_confirmation_challenge(
            token=challenge["token"],
            plugin_key=PLUGIN_KEY,
            action="update",
            target=target,
            inventory_sha256=preflight.inventory_sha256,
        )
    )

    assert approved_inventory_sha256 == preflight.inventory_sha256


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        pytest.param("plugin_key", "OtherPlugin", id="plugin"),
        pytest.param("action", "rollback", id="action"),
        pytest.param(
            "target",
            {
                "release_id": "github:owner/example-plugin:v2.0.0",
                "release_revision": 2,
                "commit": "f" * 40,
                "artifact_tree_sha256": "b" * 64,
            },
            id="target",
        ),
        pytest.param("inventory_sha256", "f" * 64, id="inventory"),
    ],
)
def test_migration_approval_challenge_rejects_changed_binding(
    plugin_core_module,
    changed_field,
    changed_value,
):
    clock = MutableClock(datetime(2026, 7, 18, 12, tzinfo=timezone.utc))
    coordinator = approval_coordinator(plugin_core_module, clock)
    target = migration_target("1" * 40)
    binding = {
        "plugin_key": PLUGIN_KEY,
        "action": "update",
        "target": target,
        "inventory_sha256": "e" * 64,
    }
    challenge = coordinator.issue_confirmation_challenge(
        kind="migration_inventory",
        **binding,
    )
    binding[changed_field] = changed_value

    with pytest.raises(plugin_core_module.ReleaseConfirmationError):
        coordinator.consume_confirmation_challenge(
            token=challenge["token"],
            **binding,
        )


def test_migration_approval_challenge_expires_and_cannot_be_replayed(
    plugin_core_module,
):
    clock = MutableClock(datetime(2026, 7, 18, 12, tzinfo=timezone.utc))
    coordinator = approval_coordinator(plugin_core_module, clock)
    binding = {
        "plugin_key": PLUGIN_KEY,
        "action": "update",
        "target": migration_target("1" * 40),
        "inventory_sha256": "e" * 64,
    }
    expired = coordinator.issue_confirmation_challenge(
        kind="migration_inventory",
        **binding,
    )
    clock.advance(301)

    with pytest.raises(plugin_core_module.ReleaseConfirmationError):
        coordinator.consume_confirmation_challenge(
            token=expired["token"],
            **binding,
        )

    current = coordinator.issue_confirmation_challenge(
        kind="migration_inventory",
        **binding,
    )
    assert coordinator.consume_confirmation_challenge(
        token=current["token"],
        **binding,
    ) == binding["inventory_sha256"]

    with pytest.raises(plugin_core_module.ReleaseConfirmationError):
        coordinator.consume_confirmation_challenge(
            token=current["token"],
            **binding,
        )


def test_clean_checkout_with_submodule_is_blocked(plugin_core_module, tmp_path):
    dependency, _ = initialize_repository(
        tmp_path / "dependency",
        remote_url=None,
    )
    repository, _ = initialize_repository(tmp_path / "plugin")
    git(
        repository,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(dependency.resolve()),
        "vendor/dependency",
    )
    release_commit = commit_files(repository, {}, "add submodule")
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_changes"
    assert result.reason == "submodules_not_supported"
    assert result.submodules == ["vendor/dependency"]
    assert repository_snapshot(repository) == before


def test_git_index_lock_blocks_preflight_without_removing_lock(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    lock_file = repository / ".git" / "index.lock"
    lock_file.touch()
    original_head = head_commit(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_changes"
    assert result.reason == "git_index_lock"
    message_prefix = "Git index lock exists at "
    assert result.message.startswith(message_prefix)
    reported_lock = result.message[len(message_prefix) : -1]
    assert os.path.normcase(os.path.normpath(reported_lock)) == os.path.normcase(
        os.path.normpath(lock_file)
    )
    assert lock_file.exists()
    assert head_commit(repository) == original_head


@pytest.mark.parametrize(
    ("marker", "operation"),
    [
        pytest.param("MERGE_HEAD", "merge", id="merge"),
        pytest.param("CHERRY_PICK_HEAD", "cherry_pick", id="cherry-pick"),
        pytest.param("REVERT_HEAD", "revert", id="revert"),
        pytest.param("rebase-merge", "rebase", id="interactive-rebase"),
        pytest.param("rebase-apply", "rebase", id="applied-rebase"),
    ],
)
def test_unresolved_git_operation_blocks_migration(
    plugin_core_module, tmp_path, marker, operation
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    operation_marker = repository / ".git" / marker
    if marker.startswith("rebase-"):
        operation_marker.mkdir()
    else:
        operation_marker.write_text(release_commit + "\n", encoding="ascii")
    original_head = head_commit(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
    )

    assert result.allowed is False
    assert result.status == "migration_blocked_local_changes"
    assert result.reason == "git_operation_in_progress"
    assert operation in result.unresolved_operations
    assert operation_marker.exists()
    assert head_commit(repository) == original_head


def test_missing_git_reports_unknown_without_touching_checkout(
    plugin_core_module, tmp_path, monkeypatch
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    before = repository_snapshot(repository)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()
    monkeypatch.setattr(host, "command_available", lambda command: False)
    monkeypatch.setattr(host, "run_git", lambda *args, **kwargs: None)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        plugin=plugin,
    )

    assert result.allowed is False
    assert result.status == "unknown"
    assert result.reason == "git_unavailable"
    assert result.relationship == "unknown"
    assert result.installed_commit == ""
    assert repository_snapshot(repository) == before


def test_non_git_directory_reports_unknown(plugin_core_module, tmp_path):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text("print('plugin')\n", encoding="utf-8")

    result = evaluate_preflight(
        plugin_core_module,
        plugin_dir,
        "1" * 40,
    )

    assert result.allowed is False
    assert result.status == "unknown"
    assert result.reason == "not_git_repository"
    assert result.relationship == "unknown"


def test_preflight_accepts_canonical_git_path_casing(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    repository, release_commit = initialize_repository(
        tmp_path / "MixedCasePlugin"
    )
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()
    real_run_git_read_only = host.run_git_read_only

    def canonicalizing_git(command, cwd, timeout=15):
        result = real_run_git_read_only(command, cwd, timeout=timeout)
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            result.stdout = result.stdout.swapcase()
        return result

    monkeypatch.setattr(
        host,
        "run_git_read_only",
        canonicalizing_git,
    )
    monkeypatch.setattr(
        plugin_core_module.os.path,
        "samefile",
        lambda left, right: (
            os.path.realpath(left).casefold()
            == os.path.realpath(right).casefold()
        ),
    )

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
        plugin=plugin,
    )

    assert result.allowed is True
    assert result.relationship == "equal"


def test_shallow_checkout_with_missing_ancestry_waits_when_fetch_fails(
    plugin_core_module, tmp_path, monkeypatch
):
    source, release_commit = initialize_repository(
        tmp_path / "source",
        remote_url=None,
    )
    commit_files(
        source,
        {"plugin.py": "print('second')\n"},
        "second commit",
    )
    installed_commit = commit_files(
        source,
        {"plugin.py": "print('third')\n"},
        "third commit",
    )
    shallow = tmp_path / "shallow"
    git(
        tmp_path,
        "clone",
        "--quiet",
        "--depth=1",
        "--branch",
        "main",
        source.resolve().as_uri(),
        str(shallow),
    )
    git(
        shallow,
        "remote",
        "set-url",
        "origin",
        "github.com/owner/example-plugin",
    )
    assert (shallow / ".git" / "shallow").is_file()
    assert (
        git(
            shallow,
            "cat-file",
            "-e",
            release_commit + "^{commit}",
            check=False,
        ).returncode
        != 0
    )
    before = repository_snapshot(shallow)
    plugin = plugin_core_module.BasePlugin()
    fetches = []

    def reject_fetch(command, cwd, timeout=15):
        fetches.append((command, cwd, timeout))
        return subprocess.CompletedProcess(command, 1, "", "fetch rejected")

    monkeypatch.setattr(plugin.get_host(), "run_git", reject_fetch)

    result = evaluate_preflight(
        plugin_core_module,
        shallow,
        release_commit,
        plugin=plugin,
    )

    assert result.allowed is False
    assert result.status == "migration_waiting_for_release"
    assert result.reason == "ancestry_unavailable"
    assert result.relationship == "unknown"
    assert result.shallow is True
    assert result.installed_commit == installed_commit
    assert repository_snapshot(shallow) == before
    assert len(fetches) == 1
    assert fetches[0][0][-2:] == ["origin", release_commit]
    assert (
        git(
            shallow,
            "cat-file",
            "-e",
            release_commit + "^{commit}",
            check=False,
        ).returncode
        != 0
    )


def test_missing_release_commit_reports_unknown_ancestry(
    plugin_core_module, tmp_path, monkeypatch
):
    repository, _ = initialize_repository(tmp_path / "plugin")
    unavailable_commit = "f" * 40
    before = repository_snapshot(repository)
    plugin = plugin_core_module.BasePlugin()
    monkeypatch.setattr(
        plugin.get_host(),
        "run_git",
        lambda command, cwd, timeout=15: subprocess.CompletedProcess(
            command,
            1,
            "",
            "fetch rejected",
        ),
    )

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        unavailable_commit,
        plugin=plugin,
    )

    assert result.allowed is False
    assert result.status == "migration_waiting_for_release"
    assert result.reason == "ancestry_unavailable"
    assert result.relationship == "unknown"
    assert result.shallow is False
    assert result.release_commit == unavailable_commit
    assert repository_snapshot(repository) == before


def test_missing_release_commit_is_fetched_from_verified_configured_remote(
    plugin_core_module,
    tmp_path,
):
    source, installed_commit = initialize_repository(
        tmp_path / "source",
        remote_url=None,
    )
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "--quiet", str(remote))
    git(source, "remote", "add", "origin", remote.resolve().as_uri())
    git(source, "push", "--quiet", "--set-upstream", "origin", "main")

    installed = tmp_path / "installed"
    git(
        tmp_path,
        "clone",
        "--quiet",
        "--branch",
        "main",
        remote.resolve().as_uri(),
        str(installed),
    )
    release_commit = commit_files(
        source,
        {"plugin.py": "print('release')\n"},
        "release",
    )
    git(source, "push", "--quiet", "origin", "main")
    assert head_commit(installed) == installed_commit
    assert (
        git(
            installed,
            "cat-file",
            "-e",
            release_commit + "^{commit}",
            check=False,
        ).returncode
        != 0
    )

    git(installed, "remote", "set-url", "origin", GITHUB_REMOTE)
    git(
        installed,
        "config",
        "url." + remote.resolve().as_uri() + ".insteadOf",
        GITHUB_REMOTE,
    )
    git(installed, "config", "protocol.file.allow", "always")
    before = repository_snapshot(installed)

    result = evaluate_preflight(
        plugin_core_module,
        installed,
        release_commit,
    )

    assert result.allowed is True
    assert result.relationship == "release_descendant"
    assert result.installed_repository_identity == GITHUB_IDENTITY
    assert repository_snapshot(installed) == before
    assert (
        git(
            installed,
            "cat-file",
            "-e",
            release_commit + "^{commit}",
            check=False,
        ).returncode
        == 0
    )


def test_repository_identity_mismatch_is_explicit_and_read_only(
    plugin_core_module, tmp_path, monkeypatch
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")
    before = repository_snapshot(repository)
    plugin = plugin_core_module.BasePlugin()

    def unexpected_fetch(*args, **kwargs):
        raise AssertionError("repository mismatch must be rejected before fetch")

    monkeypatch.setattr(plugin.get_host(), "run_git", unexpected_fetch)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        "f" * 40,
        repository_identity="gitlab.com/owner/example-plugin",
        plugin=plugin,
    )

    assert result.allowed is False
    assert result.status == "mismatch"
    assert result.reason == "repository_mismatch"
    assert result.relationship == "unknown"
    assert result.installed_repository_identity == GITHUB_IDENTITY
    assert result.expected_repository_identity == (
        "gitlab.com/owner/example-plugin"
    )
    assert repository_snapshot(repository) == before


def test_unknown_repository_identity_is_not_treated_as_a_match(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(
        tmp_path / "plugin",
        remote_url=None,
    )
    before = repository_snapshot(repository)

    result = evaluate_preflight(
        plugin_core_module,
        repository,
        release_commit,
    )

    assert result.allowed is False
    assert result.status == "unknown"
    assert result.reason == "repository_identity_unknown"
    assert result.relationship == "unknown"
    assert result.installed_repository_identity == ""
    assert result.expected_repository_identity == GITHUB_IDENTITY
    assert repository_snapshot(repository) == before


def test_migration_preflight_accepts_only_manual_or_automatic_trigger(
    plugin_core_module, tmp_path
):
    repository, release_commit = initialize_repository(tmp_path / "plugin")

    with pytest.raises(ValueError, match="trigger"):
        evaluate_preflight(
            plugin_core_module,
            repository,
            release_commit,
            trigger="scheduled",
        )
