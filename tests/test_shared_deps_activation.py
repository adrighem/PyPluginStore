import json
import os
import sys

from conftest import REPO_ROOT, load_module_from_path


PROCESS = "1:100"


def make_activation(module, tmp_path, *, visible=False, with_deps=True, site_dirs=None, process=PROCESS):
    shared_deps = tmp_path / "manager" / ".shared_deps"
    shared_deps.mkdir(parents=True, exist_ok=True)
    if with_deps:
        (shared_deps / "probe_dep.py").write_text("X = 1\n")
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir(exist_ok=True)
    startup_path = ["/usr/lib/python3"] + ([str(shared_deps)] if visible else [])
    activation = module.SharedDependencyActivation(
        str(shared_deps),
        str(tmp_path / "manager" / ".pypluginstore" / "shared_deps_activation.json"),
        startup_path,
        site_dirs if site_dirs is not None else [str(site_dir)],
        process,
    )
    return activation, shared_deps, site_dir


def test_without_dependencies_writes_pth_and_stays_inactive(plugin_core_module, tmp_path):
    activation, shared_deps, site_dir = make_activation(plugin_core_module, tmp_path, with_deps=False)

    status = activation.reconcile(True, "")

    assert status["state"] == "inactive"
    assert status["message"] == ""
    assert (site_dir / activation.pth_name).read_text() == activation.pth_contents()
    assert ascii(str(shared_deps)) in activation.pth_contents()


def test_pth_name_is_unique_per_manager_folder(plugin_core_module, tmp_path):
    first, _, _ = make_activation(plugin_core_module, tmp_path / "a")
    second, _, _ = make_activation(plugin_core_module, tmp_path / "b")

    assert first.pth_name.startswith(plugin_core_module.SHARED_DEPS_PTH_PREFIX)
    assert first.pth_name != second.pth_name


def test_pth_line_prepends_only_inside_domoticz(plugin_core_module, tmp_path, monkeypatch):
    activation, shared_deps, _ = make_activation(plugin_core_module, tmp_path)
    line = activation.pth_contents()
    assert line.startswith("import ")
    monkeypatch.setattr(sys, "path", ["/usr/lib/python3/site-packages"])

    exec(line, {})
    assert str(shared_deps) not in sys.path

    monkeypatch.setattr(sys, "builtin_module_names", sys.builtin_module_names + ("Domoticz",))
    exec(line, {})
    exec(line, {})
    assert sys.path == [str(shared_deps), "/usr/lib/python3/site-packages"]


def test_visible_dependencies_are_active_and_clear_restart_request(plugin_core_module, tmp_path):
    activation, _, site_dir = make_activation(plugin_core_module, tmp_path, visible=True, process="1:50")
    activation.reconcile(False, "")
    activation.mark_restart_requested(str(site_dir / activation.pth_name))
    later, _, _ = make_activation(plugin_core_module, tmp_path, visible=True)

    status = later.reconcile(True, "")

    assert status["state"] == "active"
    assert "restart_requested_for" not in json.loads(open(later.state_path).read())


def test_pth_written_in_this_process_is_not_trusted(plugin_core_module, tmp_path):
    # A plugin-only restart gives PyPluginStore a fresh interpreter that reads
    # the new file, while plugins started earlier in this process did not.
    activation, _, _ = make_activation(plugin_core_module, tmp_path)
    activation.reconcile(False, "")
    restarted_plugin, _, _ = make_activation(plugin_core_module, tmp_path, visible=True)

    assert restarted_plugin.reconcile(True, "")["state"] == "restart_needed"


def test_invisible_dependencies_need_restart_when_restart_is_possible(plugin_core_module, tmp_path):
    activation, _, site_dir = make_activation(plugin_core_module, tmp_path)

    status = activation.reconcile(True, "")

    assert status["state"] == "restart_needed"
    assert status["pth_path"] == str(site_dir / activation.pth_name)
    assert "restart_domoticz.log" in status["message"]


def test_invisible_dependencies_ask_for_manual_restart_without_restart_support(plugin_core_module, tmp_path):
    activation, _, _ = make_activation(plugin_core_module, tmp_path)

    status = activation.reconcile(False, "Enable Container Restart.")

    assert status["state"] == "restart_required"
    assert status["message"].endswith("Enable Container Restart.")


def test_second_boot_without_visibility_fails_instead_of_restarting_again(plugin_core_module, tmp_path):
    activation, _, site_dir = make_activation(plugin_core_module, tmp_path)
    activation.mark_restart_requested(str(site_dir / activation.pth_name))
    next_boot, _, _ = make_activation(plugin_core_module, tmp_path, process="2:200")

    status = next_boot.reconcile(True, "")

    assert status["state"] == "failed"
    assert str(site_dir / activation.pth_name) in status["message"]


def test_restart_request_for_other_pth_path_allows_new_restart(plugin_core_module, tmp_path):
    activation, _, _ = make_activation(plugin_core_module, tmp_path)
    activation.mark_restart_requested("/opt/venv/lib/python3.12/site-packages/old.pth")

    assert activation.reconcile(True, "")["state"] == "restart_needed"


def test_unwritable_site_directories_report_unavailable(plugin_core_module, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    activation, _, _ = make_activation(
        plugin_core_module, tmp_path, site_dirs=[str(blocker / "site-packages")]
    )

    status = activation.reconcile(True, "")

    assert status["state"] == "unavailable"
    assert activation.pth_name in status["message"]


def test_current_pth_is_reused_in_any_site_directory(plugin_core_module, tmp_path):
    first_site = tmp_path / "first"
    first_site.mkdir()
    activation, _, second_site = make_activation(plugin_core_module, tmp_path)
    activation.site_dirs = [str(first_site), str(second_site)]
    current = second_site / activation.pth_name
    current.write_text(activation.pth_contents())
    os.utime(current, (0, 0))

    assert activation.ensure_pth() == (str(current), False)
    assert os.stat(current).st_mtime == 0
    assert not (first_site / activation.pth_name).exists()


def test_outdated_pth_is_replaced_in_first_writable_directory(plugin_core_module, tmp_path):
    activation, _, site_dir = make_activation(plugin_core_module, tmp_path)
    (site_dir / activation.pth_name).write_text("/old/format\n")

    assert activation.ensure_pth() == (str(site_dir / activation.pth_name), True)
    assert (site_dir / activation.pth_name).read_text() == activation.pth_contents()


class FakeHost:
    def __init__(self, manager_dir, can_restart=True, restart_result=(True, "Domoticz restart requested")):
        self.manager_dir = manager_dir
        self.can_restart = can_restart
        self.restart_result = restart_result
        self.restart_calls = 0

    def shared_deps_dir(self):
        return str(self.manager_dir / ".shared_deps")

    def plugin_home_folder(self):
        return str(self.manager_dir)

    def can_restart_domoticz(self):
        return self.can_restart

    def restart_unavailable_message(self):
        return "Restart is not configured."

    def restart_domoticz(self):
        self.restart_calls += 1
        return self.restart_result


def make_manager(tmp_path):
    manager_dir = tmp_path / "plugins" / "PyPluginStore"
    (manager_dir / ".shared_deps").mkdir(parents=True)
    (manager_dir / ".shared_deps" / "probe_dep.py").write_text("X = 1\n")
    return manager_dir


def state_file(manager_dir):
    return manager_dir / ".pypluginstore" / "shared_deps_activation.json"


def test_activation_records_restart_before_restarting(plugin_core_module, tmp_path):
    manager_dir = make_manager(tmp_path)
    host = FakeHost(manager_dir)

    status = plugin_core_module.BasePlugin().activate_shared_dependencies(host)

    assert status["state"] == "restart_needed"
    assert host.restart_calls == 1
    assert json.loads(state_file(manager_dir).read_text())["restart_requested_for"] == status["pth_path"]


def test_activation_never_restarts_when_not_allowed(plugin_core_module, tmp_path):
    host = FakeHost(make_manager(tmp_path))

    status = plugin_core_module.BasePlugin().activate_shared_dependencies(host, restart_allowed=False)

    assert status["state"] == "restart_required"
    assert host.restart_calls == 0


def test_failed_restart_clears_request_and_asks_for_manual_restart(plugin_core_module, tmp_path):
    manager_dir = make_manager(tmp_path)
    host = FakeHost(manager_dir, restart_result=(False, "systemctl failed"))

    status = plugin_core_module.BasePlugin().activate_shared_dependencies(host)

    assert status["state"] == "restart_required"
    assert status["message"].endswith("systemctl failed")
    assert "restart_requested_for" not in json.loads(state_file(manager_dir).read_text())


def test_second_attempt_in_same_process_reports_failure(plugin_core_module, tmp_path):
    host = FakeHost(make_manager(tmp_path))
    plugin = plugin_core_module.BasePlugin()
    plugin.activate_shared_dependencies(host)

    status = plugin.activate_shared_dependencies(host)

    assert status["state"] == "failed"
    assert host.restart_calls == 1
    assert any("still can't import" in args[0] for args, _ in plugin_core_module.Domoticz.calls["Error"])


def test_process_token_includes_pid(plugin_core_module):
    assert str(os.getpid()) in plugin_core_module.current_process_token().split(":")


def test_container_restart_is_disabled_by_default(plugin_core_module, monkeypatch):
    runtime = plugin_core_module.LinuxHostRuntime({})
    monkeypatch.setattr(runtime, "is_container", lambda: True)

    assert runtime.restart_command_groups() == []
    assert "Container Restart" in runtime.restart_unavailable_message()


def test_container_restart_stops_domoticz_when_enabled(plugin_core_module, monkeypatch):
    runtime = plugin_core_module.LinuxHostRuntime({"Mode3": "Enabled"})
    monkeypatch.setattr(runtime, "is_container", lambda: True)

    groups = runtime.restart_command_groups()

    assert len(groups) == 1 and len(groups[0]) == 1
    command = groups[0][0]
    assert command[1] == "-c"
    assert "os.kill({}, signal.SIGTERM)".format(os.getpid()) in command[2]


def test_kubernetes_counts_as_container(plugin_core_module, monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")

    assert plugin_core_module.LinuxHostRuntime({}).is_container() is True


def test_bare_metal_restart_keeps_service_commands(plugin_core_module, monkeypatch):
    runtime = plugin_core_module.LinuxHostRuntime({})
    monkeypatch.setattr(runtime, "is_container", lambda: False)

    assert runtime.restart_command_groups()[0] == [["systemctl", "restart", "domoticz.service"]]


def load_unpatched_module():
    # The plugin_core_module fixture replaces shared_deps_site_dirs.
    return load_module_from_path("plugin_core_site_dirs_under_test", REPO_ROOT / "plugin_core.py")


def test_site_dirs_prefer_virtual_environment(plugin_core_module, monkeypatch):
    module = load_unpatched_module()
    monkeypatch.setattr(module.sys, "prefix", "/opt/venv")
    monkeypatch.setattr(module.sys, "base_prefix", "/usr")
    monkeypatch.setattr(module.site, "ENABLE_USER_SITE", False)
    monkeypatch.setattr(module.site, "getsitepackages", lambda: ["/opt/venv/lib/python3.13/site-packages"])

    assert module.shared_deps_site_dirs() == [os.path.abspath("/opt/venv/lib/python3.13/site-packages")]


def test_site_dirs_prefer_user_site_outside_virtual_environment(plugin_core_module, monkeypatch):
    module = load_unpatched_module()
    monkeypatch.setattr(module.sys, "prefix", "/usr")
    monkeypatch.setattr(module.sys, "base_prefix", "/usr")
    monkeypatch.setattr(module.site, "ENABLE_USER_SITE", True)
    monkeypatch.setattr(module.site, "getusersitepackages", lambda: "/root/.local/lib/python3.13/site-packages")
    monkeypatch.setattr(module.site, "getsitepackages", lambda: ["/usr/local/lib/python3.13/dist-packages"])

    assert module.shared_deps_site_dirs() == [
        os.path.abspath("/root/.local/lib/python3.13/site-packages"),
        os.path.abspath("/usr/local/lib/python3.13/dist-packages"),
    ]
