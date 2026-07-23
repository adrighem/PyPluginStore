import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from plugin_core_helpers import (
    configure_home,
    write_manager_identity_bundle,
    write_plugin_py,
)


class FakeGitResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class GitScenario:
    def __init__(self):
        self.steps = []
        self.calls = []

    def expect(self, command, stdout="", stderr="", returncode=0):
        expected_command = list(command)
        self.steps.append((expected_command, FakeGitResult(stdout, stderr, returncode)))
        return self

    def run(self, command, cwd=None, **kwargs):
        actual_command = list(command)
        actual_cwd = Path(cwd) if cwd is not None else None
        self.calls.append((actual_command, actual_cwd))
        if not self.steps:
            raise AssertionError("unexpected command: " + repr(actual_command))

        expected_command, result = self.steps.pop(0)
        if actual_command != expected_command:
            raise AssertionError("expected command " + repr(expected_command) + ", got " + repr(actual_command))
        return result

    def assert_complete(self):
        assert self.steps == []

    @property
    def commands(self):
        return [command for command, _ in self.calls]


DUBIOUS_OWNERSHIP_ERROR = (
    "fatal: detected dubious ownership in repository at '/tmp/Plugin'\n"
    "To add an exception for this directory, call:\n"
    "\tgit config --global --add safe.directory /tmp/Plugin\n"
)


def recorded_messages(plugin_core_module, level):
    return [args[0] for args, _ in plugin_core_module.Domoticz.calls[level] if args]


def safe_git_command(repo_dir, command):
    return ["git", "-c", "safe.directory=" + str(repo_dir.resolve())] + list(command)[1:]


def read_json(path):
    return json.loads(path.read_text())


def add_self_update_repository_checks(scenario, manager_dir, status_stdout=""):
    scenario.expect(["git", "rev-parse", "--is-inside-work-tree"], stdout="true\n")
    scenario.expect(["git", "rev-parse", "--show-toplevel"], stdout=str(manager_dir) + "\n")
    scenario.expect(["git", "status", "--porcelain", "--untracked-files=no"], stdout=status_stdout)
    return scenario


def add_self_update_comparison_checks(scenario, upstream_ref="origin/master", comparison_stdout="0\t1\n"):
    scenario.expect(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], stdout=upstream_ref + "\n")
    scenario.expect(["git", "fetch", "--prune"])
    scenario.expect(["git", "rev-parse", "--verify", "HEAD"], stdout="abc1111\n")
    scenario.expect(["git", "rev-parse", "--verify", upstream_ref], stdout="def2222\n")
    scenario.expect(["git", "merge-base", "--is-ancestor", "HEAD", upstream_ref])
    scenario.expect(["git", "rev-list", "--left-right", "--count", "HEAD..." + upstream_ref], stdout=comparison_stdout)
    return scenario


def add_self_update_candidate_checks(
    scenario,
    upstream_ref="origin/master",
    plugin_py="print('plugin')\n",
    plugin_core_py="print('core')\n",
    package_registry_py="REGISTRY_SCHEMA_VERSION = 2\n",
    package_identity_py="def certify_plugin_py(contents): return contents\n",
):
    for candidate_path in (
        "plugin.py",
        "plugin_core.py",
        "package_registry.py",
        "package_identity.py",
        "pypluginstore.html",
        "registry.json",
    ):
        scenario.expect(["git", "cat-file", "-e", upstream_ref + ":" + candidate_path])
    scenario.expect(["git", "show", upstream_ref + ":plugin.py"], stdout=plugin_py)
    if plugin_core_py is not None:
        scenario.expect(["git", "show", upstream_ref + ":plugin_core.py"], stdout=plugin_core_py)
        scenario.expect(
            ["git", "show", upstream_ref + ":package_registry.py"],
            stdout=package_registry_py,
        )
        scenario.expect(
            ["git", "show", upstream_ref + ":package_identity.py"],
            stdout=package_identity_py,
        )
    return scenario


def test_git_ownership_failure_message_includes_current_and_expected_owner(plugin_core_module, tmp_path):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)

    message = runtime.git_ownership_failure_message(manager_dir)
    path_owner = manager_dir.stat()

    assert "Current owner:" in message
    assert str(path_owner.st_uid) + ":" + str(path_owner.st_gid) in message
    if hasattr(os, "geteuid"):
        assert "Expected owner:" in message
        assert str(os.geteuid()) + ":" + str(os.getegid()) in message
        assert "the Domoticz process user" in message
    else:
        assert "Expected owner:" not in message


def test_run_git_uses_safe_directory_for_managed_repository(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    repairs = []
    scenario = GitScenario()
    scenario.expect(safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), stdout="ok\n")

    monkeypatch.setattr(plugin_core_module.subprocess, "run", scenario.run)
    monkeypatch.setattr(
        runtime,
        "repair_git_repository_ownership",
        lambda cwd: repairs.append(Path(cwd)) or True,
    )

    result = runtime.run_git(["git", "fetch", "--quiet"], manager_dir)

    assert result.returncode == 0
    assert scenario.calls == [
        (safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), manager_dir),
    ]
    scenario.assert_complete()
    assert len(repairs) == 0  # No chown repair should have been called!
    assert not any("retrying with safe.directory bypass" in message for message in recorded_messages(plugin_core_module, "Log"))
    assert not any("ownership does not match the Domoticz user" in message for message in recorded_messages(plugin_core_module, "Error"))


def test_run_git_uses_safe_directory_for_each_managed_repository_command(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    repairs = []
    scenario = GitScenario()
    scenario.expect(safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), stdout="ok\n")
    scenario.expect(safe_git_command(manager_dir, ["git", "log", "-1", "--format=%ct", "origin/master"]), stdout="ok\n")

    monkeypatch.setattr(plugin_core_module.subprocess, "run", scenario.run)
    monkeypatch.setattr(
        runtime,
        "repair_git_repository_ownership",
        lambda cwd: repairs.append(Path(cwd)) or True,
    )

    fetch_result = runtime.run_git(["git", "fetch", "--quiet"], manager_dir)
    log_result = runtime.run_git(["git", "log", "-1", "--format=%ct", "origin/master"], manager_dir)

    assert fetch_result.returncode == 0
    assert log_result.returncode == 0
    assert scenario.calls == [
        (safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), manager_dir),
        (safe_git_command(manager_dir, ["git", "log", "-1", "--format=%ct", "origin/master"]), manager_dir),
    ]
    scenario.assert_complete()
    assert repairs == []
    assert len([message for message in recorded_messages(plugin_core_module, "Log") if "safe.directory bypass" in message]) == 0


def test_run_git_does_not_use_safe_directory_for_plugins_root_clone(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    command = ["git", "clone", "https://github.com/owner/repo.git", "Plugin"]
    scenario = GitScenario()
    scenario.expect(command, stderr="fatal: repository not found", returncode=128)

    monkeypatch.setattr(plugin_core_module.subprocess, "run", scenario.run)

    result = runtime.run_git(command, plugins_dir)

    assert result.returncode == 128
    assert scenario.calls == [(command, plugins_dir)]
    scenario.assert_complete()


def test_windows_git_commands_enable_long_paths_without_global_config(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugins_dir, manager_dir = configure_home(plugin_core_module, tmp_path)
    runtime = plugin_core_module.WindowsHostRuntime(
        plugin_core_module.Parameters
    )
    long_paths = ["git", "-c", "core.longpaths=true"]
    scenario = GitScenario()
    scenario.expect(
        long_paths
        + ["clone", "https://github.com/owner/repo.git", "Plugin"],
        stdout="ok\n",
    )
    scenario.expect(
        [
            "git",
            "-c",
            "safe.directory=" + str(manager_dir.resolve()),
            "-c",
            "core.longpaths=true",
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
        ],
        stdout="abc1111\n",
    )
    monkeypatch.setattr(plugin_core_module.subprocess, "run", scenario.run)

    clone_result = runtime.run_git(
        ["git", "clone", "https://github.com/owner/repo.git", "Plugin"],
        plugins_dir,
    )
    inspect_result = runtime.run_git_read_only(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        manager_dir,
    )

    assert clone_result.returncode == 0
    assert inspect_result.returncode == 0
    scenario.assert_complete()


def test_git_runners_decode_captured_output_as_utf8(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    run_options = []

    def fake_run(command, cwd=None, **kwargs):
        run_options.append(kwargs)
        return FakeGitResult(stdout="PyPluginStore \u00b7 build\n")

    monkeypatch.setattr(plugin_core_module.subprocess, "run", fake_run)

    regular = runtime.run_git(
        ["git", "show", "HEAD:plugin.py"],
        manager_dir,
    )
    read_only = runtime.run_git_read_only(
        ["git", "show", "HEAD:plugin.py"],
        manager_dir,
    )

    assert "\u00b7" in regular.stdout
    assert "\u00b7" in read_only.stdout
    assert len(run_options) == 2
    assert all(options["text"] is True for options in run_options)
    assert all(options["encoding"] == "utf-8" for options in run_options)


def test_run_git_skips_ownership_repair_by_default_when_safe_directory_fails(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    repairs = []
    scenario = GitScenario()
    scenario.expect(safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), stderr=DUBIOUS_OWNERSHIP_ERROR, returncode=128)

    monkeypatch.setattr(plugin_core_module.subprocess, "run", scenario.run)
    monkeypatch.setattr(
        runtime,
        "repair_git_repository_ownership",
        lambda cwd: repairs.append(Path(cwd)) or True,
    )

    result = runtime.run_git(["git", "fetch", "--quiet"], manager_dir)

    assert result.returncode == 128
    assert scenario.calls == [
        (safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), manager_dir),
    ]
    scenario.assert_complete()
    assert repairs == []
    assert any("will not change file ownership automatically" in message for message in recorded_messages(plugin_core_module, "Error"))
    assert not any("Trying to fix ownership" in message for message in recorded_messages(plugin_core_module, "Error"))


def test_run_git_falls_back_to_repair_when_enabled_and_safe_directory_fails(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin_core_module.Parameters["Mode7"] = "Enabled"
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    repairs = []
    scenario = GitScenario()
    scenario.expect(safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), stderr=DUBIOUS_OWNERSHIP_ERROR, returncode=128)
    scenario.expect(safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), stdout="ok\n")

    monkeypatch.setattr(plugin_core_module.subprocess, "run", scenario.run)
    monkeypatch.setattr(
        runtime,
        "repair_git_repository_ownership",
        lambda cwd: repairs.append(Path(cwd)) or True,
    )

    result = runtime.run_git(["git", "fetch", "--quiet"], manager_dir)

    assert result.returncode == 0
    assert scenario.calls == [
        (safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), manager_dir),
        (safe_git_command(manager_dir, ["git", "fetch", "--quiet"]), manager_dir),
    ]
    scenario.assert_complete()
    assert repairs == [manager_dir.resolve()]
    assert any("Trying to fix ownership" in message for message in recorded_messages(plugin_core_module, "Error"))
    assert any("Fixed plugin repository ownership" in message for message in recorded_messages(plugin_core_module, "Log"))


def test_get_git_update_status_reports_available(plugin_core_module, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "Plugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_configured_git_remote_ref", lambda plugin_key, actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_remote_ref", lambda actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_ahead_behind", lambda actual_dir, ref: (0, 2))

    assert plugin.getGitUpdateStatus(plugin_dir) == "available"


def test_get_git_update_status_reports_current(plugin_core_module, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "Plugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_git_remote_ref", lambda actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_ahead_behind", lambda actual_dir, ref: (0, 0))

    assert plugin.getGitUpdateStatus(plugin_dir) == "current"


def test_get_git_update_status_reports_unknown_without_git(plugin_core_module, tmp_path):
    plugin = plugin_core_module.BasePlugin()

    assert plugin.getGitUpdateStatus(tmp_path / "Plugin") == "unknown"


def test_get_git_update_status_refreshes_local_update_time(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin_dir = tmp_path / "Plugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Plugin": ["owner", "repo", "description", "main", "2026-01-01T00:00:00Z"],
    }
    plugin.update_times = {"Plugin": "2026-01-01T00:00:00Z"}
    saved_update_times = []

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_configured_git_remote_ref", lambda plugin_key, actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_remote_ref", lambda actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_remote_commit_date", lambda actual_dir, ref: "2026-06-14T15:10:03Z")
    monkeypatch.setattr(plugin, "get_git_remote_url", lambda actual_dir, remote: "https://github.com/owner/repo.git")
    monkeypatch.setattr(plugin, "get_git_ahead_behind", lambda actual_dir, ref: (0, 1))
    monkeypatch.setattr(plugin, "save_update_times_cache", lambda update_times: saved_update_times.append(dict(update_times)) or True)

    assert plugin.getGitUpdateStatus(plugin_dir, "Plugin") == "available"
    assert plugin.plugin_data["Plugin"][4] == "2026-06-14T15:10:03Z"
    assert saved_update_times == [{"Plugin": "2026-06-14T15:10:03Z"}]


def test_get_git_update_status_uses_registry_branch_ref(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin_dir = tmp_path / "Plugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Plugin": ["owner", "repo", "description", "meters", ""],
    }
    checked_refs = []

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_configured_git_remote_ref", lambda plugin_key, actual_dir: "origin/meters")
    monkeypatch.setattr(plugin, "get_git_remote_commit_date", lambda actual_dir, ref: "2023-07-22T14:21:45Z")
    monkeypatch.setattr(plugin, "get_git_remote_url", lambda actual_dir, remote: "https://github.com/owner/repo.git")
    monkeypatch.setattr(plugin, "save_update_times_cache", lambda update_times: True)

    def fake_ahead_behind(actual_dir, ref):
        checked_refs.append(ref)
        return (0, 1)

    monkeypatch.setattr(plugin, "get_git_ahead_behind", fake_ahead_behind)

    assert plugin.getGitUpdateStatus(plugin_dir, "Plugin") == "available"
    assert checked_refs == ["origin/meters"]
    assert plugin.plugin_data["Plugin"][4] == "2023-07-22T14:21:45Z"


def test_get_git_update_status_reports_registry_mismatch(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin_dir = tmp_path / "Plugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Plugin": ["owner", "repo", "description", "main", "2026-01-01T00:00:00Z"],
    }
    plugin.installed_plugin_match_details = {
        "Plugin": {"registry_mismatch": True},
    }

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: (_ for _ in ()).throw(AssertionError("unexpected fetch")))

    assert plugin.getGitUpdateStatus(plugin_dir, "Plugin") == "mismatch"
    assert plugin.plugin_data["Plugin"][4] == "2026-01-01T00:00:00Z"


def test_local_override_update_time_can_be_older_than_public_time(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin_dir = tmp_path / "Plugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Plugin": ["owner", "repo", "description", "meters", ""],
    }
    plugin.local_plugin_keys = ["Plugin"]
    plugin.update_times = {"Plugin": "2025-12-23T17:17:13Z"}
    saved_update_times = []

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_configured_git_remote_ref", lambda plugin_key, actual_dir: "origin/meters")
    monkeypatch.setattr(plugin, "get_git_remote_commit_date", lambda actual_dir, ref: "2023-07-22T14:21:45Z")
    monkeypatch.setattr(plugin, "get_git_remote_url", lambda actual_dir, remote: "https://github.com/owner/repo.git")
    monkeypatch.setattr(plugin, "get_git_ahead_behind", lambda actual_dir, ref: (0, 0))
    monkeypatch.setattr(plugin, "save_update_times_cache", lambda update_times: saved_update_times.append(dict(update_times)) or True)

    assert plugin.getGitUpdateStatus(plugin_dir, "Plugin") == "current"
    assert plugin.plugin_data["Plugin"][4] == "2023-07-22T14:21:45Z"
    assert saved_update_times == [{"Plugin": "2023-07-22T14:21:45Z"}]


def test_get_installed_update_status_skips_unmanaged_plugins(plugin_core_module, tmp_path, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "KnownPlugin": ["owner", "repo", "description", "main", ""],
    }
    checked_plugins = []

    def fake_status(plugin_dir, plugin_key=None, fetch_first=True):
        checked_plugins.append(plugin_key)
        return "current"

    monkeypatch.setattr(plugin, "getGitUpdateStatus", fake_status)

    assert plugin.getInstalledUpdateStatuses(["KnownPlugin", "LooseFolder"], tmp_path) == {
        "KnownPlugin": "current",
        "LooseFolder": "unknown",
    }
    assert checked_plugins == ["KnownPlugin"]
    assert plugin.update_status == {
        "KnownPlugin": "current",
        "LooseFolder": "unknown",
    }


def test_refresh_installed_update_statuses_checks_managed_plugins_in_order(plugin_core_module, tmp_path, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "FirstPlugin": ["owner", "repo", "description", "main", ""],
        "SecondPlugin": ["owner", "repo", "description", "main", ""],
    }
    checked_plugins = []

    def fake_status(plugin_dir, plugin_key=None, fetch_first=True):
        checked_plugins.append(plugin_key)
        return {
            "FirstPlugin": "current",
            "SecondPlugin": "available",
        }[plugin_key]

    monkeypatch.setattr(plugin, "getGitUpdateStatus", fake_status)

    assert plugin.refreshInstalledUpdateStatuses(
        ["FirstPlugin", "LooseFolder", "SecondPlugin"],
        tmp_path,
    ) == {
        "FirstPlugin": "current",
        "LooseFolder": "unknown",
        "SecondPlugin": "available",
    }
    assert checked_plugins == ["FirstPlugin", "SecondPlugin"]
    assert plugin.update_status == {
        "FirstPlugin": "current",
        "LooseFolder": "unknown",
        "SecondPlugin": "available",
    }


def test_refresh_installed_update_status_uses_detected_folder(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Domoticz-deCONZ"
    write_plugin_py(plugin_dir, key="DECONZ", name="deCONZ")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }
    checked_plugins = []

    def fake_status(actual_plugin_dir, plugin_key=None, fetch_first=True):
        checked_plugins.append((Path(actual_plugin_dir), plugin_key))
        return "current"

    monkeypatch.setattr(plugin, "getGitUpdateStatus", fake_status)

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert plugin.refreshInstalledUpdateStatuses(installed, plugins_dir)["deCONZ"] == "current"
    assert checked_plugins == [(plugin_dir, "deCONZ")]


def test_update_command_refreshes_cached_status_for_next_list(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "OtherPlugin"
    (plugin_dir / ".git").mkdir(parents=True)
    write_plugin_py(plugin_dir, key="OTHER", name="OtherPlugin")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repo", "description", "main", ""],
    }
    plugin.update_status = {"OtherPlugin": "available"}
    responses = []
    status_calls = []

    def fake_git_status(actual_plugin_dir, plugin_key=None, fetch_first=True):
        status_calls.append((Path(actual_plugin_dir), plugin_key, fetch_first))
        return "current"

    monkeypatch.setattr(plugin_core_module.subprocess, "run", lambda *args, **kwargs: FakeGitResult())
    monkeypatch.setattr(plugin, "refresh_single_plugin_update_time", lambda *args, **kwargs: False)
    monkeypatch.setattr(plugin, "installDependencies", lambda plugin_key: None)
    monkeypatch.setattr(plugin, "getGitUpdateStatus", fake_git_status)
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "update", "plugin_key": "OtherPlugin"})
    plugin.handleApiCommand({"action": "list_plugins"})

    assert responses[0] == {
        "status": "success",
        "action": "update",
        "plugin_key": "OtherPlugin",
    }
    assert responses[1]["update_status"]["OtherPlugin"] == "current"
    assert plugin.update_status["OtherPlugin"] == "current"
    assert status_calls == [(plugin_dir, "OtherPlugin", False)]


def test_update_command_uses_detected_repository_folder(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Domoticz-deCONZ"
    (plugin_dir / ".git").mkdir(parents=True)
    write_plugin_py(plugin_dir, key="DECONZ", name="deCONZ")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }
    responses = []
    git_calls = []
    status_calls = []

    def fake_run(command, cwd=None, **kwargs):
        git_calls.append((command, Path(cwd)))
        if command[-2:] == ["remote", "-v"]:
            class FakeRemoteResult:
                stdout = "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
                stderr = ""
                returncode = 0

            return FakeRemoteResult()
        return FakeGitResult()

    def fake_refresh_status(plugin_key, actual_plugin_dir, fetch_first=True):
        status_calls.append((plugin_key, Path(actual_plugin_dir), fetch_first))
        plugin.update_status[plugin_key] = "current"
        return "current"

    monkeypatch.setattr(plugin_core_module.subprocess, "run", fake_run)
    monkeypatch.setattr(plugin, "refresh_single_plugin_update_time", lambda *args, **kwargs: False)
    monkeypatch.setattr(plugin, "refresh_single_plugin_update_status", fake_refresh_status)
    monkeypatch.setattr(plugin, "installDependencies", lambda plugin_key: None)
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "update", "plugin_key": "deCONZ"})

    assert responses[0] == {
        "status": "success",
        "action": "update",
        "plugin_key": "deCONZ",
    }
    assert all(cwd == plugin_dir for _, cwd in git_calls)
    assert [command for command, _ in git_calls[-4:]] == [
        safe_git_command(plugin_dir, ["git", "fetch", "origin"]),
        safe_git_command(plugin_dir, ["git", "diff", "--quiet", "HEAD...origin/master"]),
        safe_git_command(plugin_dir, ["git", "checkout", "-B", "master", "origin/master"]),
        safe_git_command(plugin_dir, ["git", "reset", "--hard", "origin/master"]),
    ]
    assert status_calls == [("deCONZ", plugin_dir, False)]


def test_update_command_refuses_registry_mismatch(plugin_core_module, tmp_path, monkeypatch):
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
    responses = []
    git_calls = []

    def command_endswith(command, suffix):
        return command[-len(suffix):] == suffix

    def fake_run(command, cwd=None, **kwargs):
        git_calls.append(command)
        if command_endswith(command, ["remote", "-v"]):
            return FakeGitResult(
                stdout="origin\thttps://github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin.git (fetch)\n"
            )
        if command_endswith(command, ["rev-parse", "--abbrev-ref", "HEAD"]):
            return FakeGitResult(stdout="MetersDev\n")
        if command_endswith(command, ["config", "--get", "branch.MetersDev.remote"]):
            return FakeGitResult(stdout="origin\n")
        if command_endswith(command, ["remote", "get-url", "origin"]):
            return FakeGitResult(stdout="https://github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin.git\n")
        return FakeGitResult()

    monkeypatch.setattr(plugin_core_module.subprocess, "run", fake_run)
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "update", "plugin_key": "domoticz-solaredge-modbustcp-plugin"})

    assert responses[0]["status"] == "error"
    assert "registry_local.json" in responses[0]["message"]
    assert plugin.update_status["domoticz-solaredge-modbustcp-plugin"] == "mismatch"
    assert not any(command_endswith(command, ["fetch", "origin"]) for command in git_calls)


def test_self_update_command_schedules_detached_helper(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "00-PyPluginStore": ["adrighem", "PyPluginStore", "description", "master", ""],
    }
    responses = []
    popen_calls = []

    def fail_sync_git(*args, **kwargs):
        raise AssertionError("self update should not run git synchronously")

    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))

        class FakeProcess:
            pass

        return FakeProcess()

    monkeypatch.setattr(
        plugin_core_module.HostRuntime,
        "preflight_self_update",
        lambda self, plugin_dir: (
            True,
            "Self update pre-flight checks passed.",
            {
                "already_current": False,
                "upstream_ref": "origin/master",
                "current_commit": "abc1111",
                "target_commit": "def2222",
            },
        ),
    )
    monkeypatch.setattr(plugin_core_module.subprocess, "run", fail_sync_git)
    monkeypatch.setattr(plugin_core_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "update", "plugin_key": "00-PyPluginStore"})

    assert responses[0]["status"] == "success"
    assert responses[0]["action"] == "update"
    assert responses[0]["plugin_key"] == "00-PyPluginStore"
    assert responses[0]["operation"] == "self_update"
    assert responses[0]["self_update"]["phase"] == "scheduled"
    assert responses[0]["self_update"]["upstream_ref"] == "origin/master"
    assert responses[0]["self_update"]["current_commit"] == "abc1111"
    assert responses[0]["self_update"]["target_commit"] == "def2222"
    assert responses[0]["message"].startswith("Self update started after pre-flight checks.")
    assert plugin.update_status["00-PyPluginStore"] == "unknown"
    assert len(popen_calls) == 1

    state = read_json(manager_dir / "self_update_state.json")
    assert state["operation"] == "self_update"
    assert state["phase"] == "scheduled"
    assert state["upstream_ref"] == "origin/master"
    assert state["current_commit"] == "abc1111"
    assert state["target_commit"] == "def2222"
    assert state["log_file"].endswith("self_update.log")

    command = popen_calls[0][0][0]
    helper = command[2]
    assert command[:2] == [plugin_core_module.sys.executable, "-c"]
    assert "self_update_state.json" in helper
    assert 'write_state("running"' in helper
    assert '"applied_needs_reload"' in helper
    assert 'write_state("failed"' in helper
    assert 'git_command("status", "--porcelain", "--untracked-files=no")' in helper
    assert 'git_command("fetch", "--prune")' in helper
    assert 'git_command("merge", "--ff-only", upstream_ref)' in helper
    assert "startup_delay = 5" in helper
    assert "safe.directory=" in helper
    assert 'encoding="utf-8"' in helper
    assert '["git", "reset", "--hard", "HEAD"]' not in helper
    assert '["git", "pull", "--force"]' not in helper
    assert any("Starting PyPluginStore self-update pre-flight" in message for message in recorded_messages(plugin_core_module, "Log"))
    assert any("self_update.log" in message for message in recorded_messages(plugin_core_module, "Log"))


def test_self_update_helper_reports_git_index_lock(plugin_core_module, tmp_path):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    (manager_dir / ".git" / "index.lock").write_text("")
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)

    helper = runtime.build_self_update_helper(
        str(manager_dir),
        str(manager_dir / "self_update.log"),
        "origin/master",
        str(manager_dir / "self_update_state.json"),
        "job-1",
        {"current_commit": "abc1111", "target_commit": "def2222"},
        startup_delay=0,
    )
    result = plugin_core_module.subprocess.run(
        [plugin_core_module.sys.executable, "-c", helper],
        stdout=plugin_core_module.subprocess.PIPE,
        stderr=plugin_core_module.subprocess.PIPE,
        text=True,
        timeout=15,
    )

    assert result.returncode == 1
    state = read_json(manager_dir / "self_update_state.json")
    assert state["phase"] == "failed"
    assert str(manager_dir / ".git" / "index.lock") in state["message"]
    assert "remove the lock file if it is stale" in state["message"]
    assert "git index lock exists" in (manager_dir / "self_update.log").read_text()


def test_self_update_command_reports_preflight_failure(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "00-PyPluginStore": ["adrighem", "PyPluginStore", "description", "master", ""],
    }
    responses = []

    monkeypatch.setattr(
        plugin_core_module.HostRuntime,
        "preflight_self_update",
        lambda self, plugin_dir: (False, "PyPluginStore has local tracked file changes; self-update refused.", {}),
    )
    monkeypatch.setattr(
        plugin_core_module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected helper launch")),
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "update", "plugin_key": "00-PyPluginStore"})

    assert responses[0] == {
        "status": "error",
        "action": "update",
        "plugin_key": "00-PyPluginStore",
        "message": "PyPluginStore has local tracked file changes; self-update refused.",
    }
    state = read_json(manager_dir / "self_update_state.json")
    assert state["operation"] == "self_update"
    assert state["phase"] == "preflight_failed"
    assert state["message"] == "PyPluginStore has local tracked file changes; self-update refused."
    assert any("PyPluginStore self-update pre-flight failed" in message for message in recorded_messages(plugin_core_module, "Error"))


def test_self_update_preflight_rejects_dirty_tracked_files(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    scenario = GitScenario()
    add_self_update_repository_checks(scenario, manager_dir, status_stdout=" M plugin.py\n")

    monkeypatch.setattr(runtime, "command_available", lambda command: command == "git")
    monkeypatch.setattr(runtime, "run_git", scenario.run)

    success, message, plan = runtime.preflight_self_update(manager_dir)

    assert success is False
    assert message == "PyPluginStore has local tracked file changes; self-update refused."
    assert plan == {}
    scenario.assert_complete()


def test_self_update_preflight_rejects_git_index_lock(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    (manager_dir / ".git" / "index.lock").write_text("")
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)

    monkeypatch.setattr(runtime, "command_available", lambda command: command == "git")
    monkeypatch.setattr(
        runtime,
        "run_git",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected git command")),
    )

    success, message, plan = runtime.preflight_self_update(manager_dir)

    assert success is False
    assert str(manager_dir / ".git" / "index.lock") in message
    assert "remove the lock file if it is stale" in message
    assert plan == {}


def test_self_update_preflight_reports_dubious_ownership(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    scenario = GitScenario()
    scenario.expect(
        safe_git_command(manager_dir, ["git", "rev-parse", "--is-inside-work-tree"]),
        stderr=DUBIOUS_OWNERSHIP_ERROR,
        returncode=128,
    )
    repairs = []

    monkeypatch.setattr(runtime, "command_available", lambda command: command == "git")
    monkeypatch.setattr(runtime, "_run_git_once", scenario.run)
    monkeypatch.setattr(runtime, "repair_git_repository_ownership", lambda cwd: repairs.append(Path(cwd)) or False)

    success, message, plan = runtime.preflight_self_update(manager_dir)

    assert success is False
    assert "ownership does not match the Domoticz user" in message
    assert "fix the plugin folder ownership manually" in message
    assert "safe.directory" not in message
    assert plan == {}
    assert repairs == []
    scenario.assert_complete()


def test_self_update_preflight_rejects_invalid_target_python(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    scenario = GitScenario()
    add_self_update_repository_checks(scenario, manager_dir)
    add_self_update_comparison_checks(scenario)
    add_self_update_candidate_checks(scenario, plugin_py="def broken(:\n", plugin_core_py=None)

    monkeypatch.setattr(runtime, "command_available", lambda command: command == "git")
    monkeypatch.setattr(runtime, "run_git", scenario.run)

    success, message, plan = runtime.preflight_self_update(manager_dir)

    assert success is False
    assert "invalid Python syntax in plugin.py" in message
    assert plan == {}
    scenario.assert_complete()


def test_self_update_preflight_allows_fast_forward_candidate(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    scenario = GitScenario()
    add_self_update_repository_checks(scenario, manager_dir)
    add_self_update_comparison_checks(scenario)
    add_self_update_candidate_checks(scenario)

    monkeypatch.setattr(runtime, "command_available", lambda command: command == "git")
    monkeypatch.setattr(runtime, "run_git", scenario.run)

    success, message, plan = runtime.preflight_self_update(manager_dir)

    assert success is True
    assert message == "Self update pre-flight checks passed."
    assert plan == {
        "already_current": False,
        "upstream_ref": "origin/master",
        "current_commit": "abc1111",
        "target_commit": "def2222",
    }
    scenario.assert_complete()


def test_self_update_preflight_reports_already_current(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / ".git").mkdir()
    runtime = plugin_core_module.LinuxHostRuntime(plugin_core_module.Parameters)
    scenario = GitScenario()
    add_self_update_repository_checks(scenario, manager_dir)
    add_self_update_comparison_checks(scenario, comparison_stdout="0\t0\n")

    monkeypatch.setattr(runtime, "command_available", lambda command: command == "git")
    monkeypatch.setattr(runtime, "run_git", scenario.run)

    success, message, plan = runtime.preflight_self_update(manager_dir)

    assert success is True
    assert message == "PyPluginStore is already up-to-date."
    assert plan == {
        "already_current": True,
        "upstream_ref": "origin/master",
        "current_commit": "abc1111",
        "target_commit": "def2222",
    }
    scenario.assert_complete()


def test_refresh_update_status_command_runs_serial_refresh(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "OtherPlugin", key="OTHER", name="OtherPlugin")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repo", "description", "main", ""],
    }
    responses = []
    calls = []

    def fake_fetch_registry():
        calls.append("fetch_registry")
        plugin.plugin_data["LocalPlugin"] = [
            "git@github.com:owner/private-plugin.git",
            "",
            "local description",
            "main",
            "",
        ]
        plugin.local_plugin_keys = ["LocalPlugin"]

    def fake_refresh(installed, actual_plugins_dir):
        calls.append("refresh_status")
        assert Path(actual_plugins_dir) == plugins_dir
        assert set(installed) == {"00-PyPluginStore", "OtherPlugin"}
        return {"00-PyPluginStore": "unknown", "OtherPlugin": "available"}

    monkeypatch.setattr(plugin, "fetch_registry", fake_fetch_registry)
    monkeypatch.setattr(plugin, "refreshInstalledUpdateStatuses", fake_refresh)
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "refresh_update_status"})

    response = responses[0]
    assert response["status"] == "success"
    assert response["action"] == "refresh_update_status"
    assert response["update_status"] == {
        "00-PyPluginStore": "unknown",
        "OtherPlugin": "available",
    }
    assert response["data"] == plugin.plugin_data
    assert response["local_plugins"] == ["LocalPlugin"]
    assert response["installed_match_details"]["OtherPlugin"]["source"] == "exact folder key"
    assert response["self_update"]["phase"] == "idle"
    assert calls == ["fetch_registry", "refresh_status"]


def test_self_update_status_command_returns_persisted_state(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.get_host().write_self_update_state(
        "running",
        "Self update helper is running.",
        job_id="job-1",
        upstream_ref="origin/master",
        target_commit="def2222",
    )
    responses = []

    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "self_update_status"})

    assert responses[0]["status"] == "success"
    assert responses[0]["action"] == "self_update_status"
    assert responses[0]["self_update"]["phase"] == "running"
    assert responses[0]["self_update"]["message"] == "Self update helper is running."
    assert responses[0]["self_update"]["job_id"] == "job-1"
    assert responses[0]["self_update"]["target_commit"] == "def2222"
    assert responses[0]["self_update"]["log_file"] == str(manager_dir / "self_update.log")


def test_finalize_self_update_state_confirms_applied_target(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    write_manager_identity_bundle(manager_dir)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()
    host.write_self_update_state(
        "applied_needs_reload",
        "Self update completed.",
        job_id="job-1",
        target_commit="def2222",
    )

    monkeypatch.setattr(
        host,
        "run_git_read_only",
        lambda command, cwd, timeout=15: FakeGitResult(stdout="def2222\n"),
    )

    plugin.finalizeSelfUpdateState()

    state = read_json(manager_dir / "self_update_state.json")
    assert state["phase"] == "confirmed"
    assert state["confirmed_commit"] == "def2222"
    assert "confirmed" in state["message"].lower()
    assert any("PyPluginStore self-update confirmed" in message for message in recorded_messages(plugin_core_module, "Log"))
    assert any("self_update.log" in message for message in recorded_messages(plugin_core_module, "Log"))


def test_finalize_self_update_state_reports_unknown_when_head_cannot_be_verified(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()
    host.write_self_update_state(
        "applied_needs_reload",
        "Self update completed.",
        job_id="job-1",
        target_commit="def2222",
    )

    monkeypatch.setattr(
        host,
        "run_git_read_only",
        lambda command, cwd, timeout=15: FakeGitResult(stderr="fatal: no head\n", returncode=128),
    )

    plugin.finalizeSelfUpdateState()

    state = read_json(manager_dir / "self_update_state.json")
    assert state["phase"] == "stale_unknown"
    assert "could not verify" in state["message"]
    assert any("self_update.log" in message for message in recorded_messages(plugin_core_module, "Error"))


def test_check_for_update_reports_unknown_without_error(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "OtherPlugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repo", "description", "main", ""],
    }

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: False)

    plugin.CheckForUpdatePythonPlugin("owner", "repo", "OtherPlugin")

    assert plugin_core_module.Domoticz.calls["Error"] == []
    assert plugin_core_module.Domoticz.calls["SendNotification"] == []
    assert plugin.update_status["OtherPlugin"] == "unknown"


def test_check_for_update_notifies_when_update_available(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "OtherPlugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repo", "description", "main", ""],
    }
    monkeypatch.setattr(
        plugin_core_module.platform,
        "node",
        lambda: "domoticz-host",
    )

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_git_remote_ref", lambda actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_remote_commit_date", lambda actual_dir, ref: "")
    monkeypatch.setattr(plugin, "get_git_ahead_behind", lambda actual_dir, ref: (0, 1))

    plugin.CheckForUpdatePythonPlugin("owner", "repo", "OtherPlugin")

    assert plugin_core_module.Domoticz.calls["Error"] == []
    assert len(plugin_core_module.Domoticz.calls["SendNotification"]) == 1
    arguments, _ = plugin_core_module.Domoticz.calls["SendNotification"][0]
    assert arguments == (
        "domoticz-host: Domoticz Plugin Updates Available for description",
        "description has updates available!!",
    )
    assert plugin.update_status["OtherPlugin"] == "available"


@pytest.mark.parametrize(
    ("release_version", "expected_body"),
    [
        (
            "v1.4.0",
            (
                "Use the Release channel v1.4.0 for Example plugin instead of "
                "Git commits."
            ),
        ),
        (
            "",
            (
                "Use the Release channel for Example plugin instead of Git "
                "commits."
            ),
        ),
    ],
)
def test_release_channel_notification_explains_that_it_replaces_git_commits(
    plugin_core_module,
    monkeypatch,
    release_version,
    expected_body,
):
    plugin = plugin_core_module.BasePlugin()
    entry = plugin_core_module.RegistryEntry(
        "OtherPlugin",
        "owner",
        "repo",
        "Example plugin",
        "main",
    )
    plugin.registry_entries[entry.key] = entry
    monkeypatch.setattr(
        plugin_core_module.platform,
        "node",
        lambda: "domoticz-host",
    )

    plugin.fnReleaseChannelNotify(entry.key, release_version)

    assert len(plugin_core_module.Domoticz.calls["SendNotification"]) == 1
    arguments, _ = plugin_core_module.Domoticz.calls["SendNotification"][0]
    subject, body = arguments
    assert subject == (
        "domoticz-host: Domoticz Plugin Release Channel Available for "
        "Example plugin"
    )
    assert "Updates Available" not in subject
    assert body == expected_body


def test_check_for_update_caches_current_status(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "OtherPlugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repo", "description", "main", ""],
    }

    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_git_remote_ref", lambda actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_remote_commit_date", lambda actual_dir, ref: "")
    monkeypatch.setattr(plugin, "get_git_ahead_behind", lambda actual_dir, ref: (0, 0))

    plugin.CheckForUpdatePythonPlugin("owner", "repo", "OtherPlugin")

    assert plugin_core_module.Domoticz.calls["Error"] == []
    assert plugin_core_module.Domoticz.calls["SendNotification"] == []
    assert plugin.update_status["OtherPlugin"] == "current"


def test_check_for_update_skips_missing_notification_api(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "OtherPlugin"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repo", "description", "main", ""],
    }

    monkeypatch.delattr(plugin_core_module.Domoticz, "SendNotification", raising=False)
    monkeypatch.setattr(plugin, "fetch_git_repo", lambda actual_dir: True)
    monkeypatch.setattr(plugin, "get_git_remote_ref", lambda actual_dir: "origin/main")
    monkeypatch.setattr(plugin, "get_git_remote_commit_date", lambda actual_dir, ref: "")
    monkeypatch.setattr(plugin, "get_git_ahead_behind", lambda actual_dir, ref: (0, 1))

    plugin.CheckForUpdatePythonPlugin("owner", "repo", "OtherPlugin")

    assert plugin_core_module.Domoticz.calls["Error"] == []
    assert plugin_core_module.Domoticz.calls["SendNotification"] == []
    assert any("Notification skipped" in args[0] for args, _ in plugin_core_module.Domoticz.calls["Log"])
    assert plugin.update_status["OtherPlugin"] == "available"


def test_install_command_reports_clone_failure(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "PrivatePlugin": ["git@github.com:owner/private-plugin.git", "", "description", "main", ""],
    }
    responses = []

    class FakeGitResult:
        stdout = ""
        stderr = "fatal: repository not found"
        returncode = 128

    monkeypatch.setattr(plugin_core_module.subprocess, "run", lambda *args, **kwargs: FakeGitResult())
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "install", "plugin_key": "PrivatePlugin"})

    assert responses[0]["status"] == "error"
    assert "repository not found" in responses[0]["message"]


def test_install_wrapper_passes_registry_entry_to_strategy(plugin_core_module, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Plugin": ["owner", "repo", "description", "main", ""],
    }
    calls = []

    def fake_install(entry):
        calls.append(entry)
        return True, ""

    monkeypatch.setattr(plugin.install_update_strategy, "install", fake_install)

    assert plugin.InstallPythonPlugin("override-owner", "override-repo", "Plugin", "develop") == (True, "")
    assert calls[0].key == "Plugin"
    assert calls[0].author == "override-owner"
    assert calls[0].repository == "override-repo"
    assert calls[0].description == "description"
    assert calls[0].branch == "develop"


def test_update_wrapper_uses_explicit_strategy(plugin_core_module, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Plugin": ["owner", "repo", "description", "develop", ""],
    }
    calls = []

    def fake_update(entry, queue_on_lock=True):
        calls.append((entry, queue_on_lock))
        return True, ""

    monkeypatch.setattr(plugin.install_update_strategy, "update", fake_update)

    assert plugin.UpdatePythonPlugin("owner", "repo", "Plugin", queue_on_lock=False) == (True, "")
    assert calls[0][0].key == "Plugin"
    assert calls[0][0].branch == "develop"
    assert calls[0][1] is False


def test_daily_heartbeat_refreshes_update_status_once(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    refresh_calls = []

    class FakeDateTime:
        @classmethod
        def now(cls):
            return datetime(2026, 6, 15, 8, 0, 0)

    monkeypatch.setattr(plugin_core_module, "datetime", FakeDateTime)
    monkeypatch.setattr(plugin, "refreshInstalledUpdateStatuses", lambda **kwargs: refresh_calls.append(kwargs) or {})

    plugin.onHeartbeat()
    plugin.onHeartbeat()

    assert len(refresh_calls) == 1
    assert Path(refresh_calls[0]["plugins_dir"]).name == "plugins"


def test_dependency_install_command_prefers_uv_with_active_python(plugin_core_module, tmp_path):
    runtime = plugin_core_module.LinuxHostRuntime({})
    runtime.command_available = lambda command: command == "uv"

    command = runtime.dependency_install_command(str(tmp_path / "requirements.txt"), str(tmp_path / "deps"))

    assert command[:5] == ["uv", "pip", "install", "--python", plugin_core_module.sys.executable]


def test_dependency_install_command_prefers_current_python_before_pip3(plugin_core_module, tmp_path):
    runtime = plugin_core_module.LinuxHostRuntime({})
    runtime.command_available = lambda command: command == "pip3"
    runtime.command_can_run = lambda command, timeout=10: command == [plugin_core_module.sys.executable, "-m", "pip", "--version"]

    command = runtime.dependency_install_command(str(tmp_path / "requirements.txt"), str(tmp_path / "deps"))

    assert command[:3] == [plugin_core_module.sys.executable, "-m", "pip"]
