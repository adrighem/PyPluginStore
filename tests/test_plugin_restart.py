from plugin_core_helpers import configure_home

def test_restart_command_reports_scheduled_restart(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    responses = []

    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)
    monkeypatch.setattr(plugin, "restartDomoticz", lambda: (True, "Domoticz restart requested"))

    plugin.handleApiCommand({"action": "restart_domoticz"})

    assert responses[0]["status"] == "success"
    assert responses[0]["action"] == "restart_domoticz"
    assert responses[0]["message"] == "Domoticz restart requested"


def test_restart_command_reports_scheduling_failure(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    responses = []

    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)
    monkeypatch.setattr(plugin, "restartDomoticz", lambda: (False, "restart not configured"))

    plugin.handleApiCommand({"action": "restart_domoticz"})

    assert responses[0] == {
        "status": "error",
        "action": "restart_domoticz",
        "message": "restart not configured",
    }


def test_host_runtime_factory_selects_windows(plugin_core_module, monkeypatch):
    monkeypatch.setattr(plugin_core_module.platform, "system", lambda: "Windows")

    runtime = plugin_core_module.make_host_runtime({})

    assert isinstance(runtime, plugin_core_module.WindowsHostRuntime)
    assert runtime.make_web_readable("ignored") is None


def test_host_runtime_factory_defaults_to_linux(plugin_core_module, monkeypatch):
    monkeypatch.setattr(plugin_core_module.platform, "system", lambda: "Linux")

    runtime = plugin_core_module.make_host_runtime({})

    assert isinstance(runtime, plugin_core_module.LinuxHostRuntime)


def test_windows_restart_schedules_system_task(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    monkeypatch.setattr(plugin_core_module.platform, "system", lambda: "Windows")
    plugin = plugin_core_module.BasePlugin()
    schtasks_calls = []

    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_file_execution", lambda self: True)
    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_encoded_command", lambda self: True)
    monkeypatch.setattr(
        plugin_core_module.WindowsHostRuntime,
        "run_schtasks",
        lambda self, args, timeout=20: schtasks_calls.append(args) or True,
    )

    success, message = plugin.restartDomoticz()

    assert success is True
    assert message == "Domoticz restart requested"
    helper = (manager_dir / "restart_domoticz.ps1").read_text()
    command_helper = (manager_dir / "restart_domoticz.cmd").read_text()
    assert "Restart-Service -Name " in helper
    assert "@(\"sc.exe\", \"stop\", $serviceName)" in helper
    assert "Invoke-Expression $script" in command_helper
    assert schtasks_calls[0][:2] == ["/Create", "/TN"]
    assert "/RU" in schtasks_calls[0]
    assert "SYSTEM" in schtasks_calls[0]
    assert schtasks_calls[1] == ["/Run", "/TN", r"\PyPluginStore-Domoticz-Restart"]
    assert (manager_dir / "restart_domoticz.log").exists()
    assert "launching Windows scheduled restart task" in (
        manager_dir / "restart_domoticz.log"
    ).read_text()


def test_windows_restart_schedules_system_task_when_script_files_are_blocked(
    plugin_core_module, tmp_path, monkeypatch
):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    monkeypatch.setattr(plugin_core_module.platform, "system", lambda: "Windows")
    plugin = plugin_core_module.BasePlugin()
    schtasks_calls = []

    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_file_execution", lambda self: False)
    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_encoded_command", lambda self: True)
    monkeypatch.setattr(
        plugin_core_module.WindowsHostRuntime,
        "run_schtasks",
        lambda self, args, timeout=20: schtasks_calls.append(args) or True,
    )

    success, message = plugin.restartDomoticz()

    assert success is True
    assert message == "Domoticz restart requested"
    assert (manager_dir / "restart_domoticz.ps1").exists()
    assert (manager_dir / "restart_domoticz.cmd").exists()
    assert schtasks_calls[1] == ["/Run", "/TN", r"\PyPluginStore-Domoticz-Restart"]
    assert "launching Windows scheduled restart task" in (manager_dir / "restart_domoticz.log").read_text()


def test_windows_restart_reports_encoded_command_probe_failure(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    monkeypatch.setattr(plugin_core_module.platform, "system", lambda: "Windows")
    plugin = plugin_core_module.BasePlugin()
    schtasks_calls = []

    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_file_execution", lambda self: False)
    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_encoded_command", lambda self: False)
    monkeypatch.setattr(
        plugin_core_module.WindowsHostRuntime,
        "run_schtasks",
        lambda self, args, timeout=20: schtasks_calls.append(args) or True,
    )

    success, message = plugin.restartDomoticz()

    assert success is False
    assert message == "PowerShell EncodedCommand probe failed. See restart_domoticz.log."
    assert schtasks_calls == []


def test_windows_restart_reports_schtasks_failure(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    monkeypatch.setattr(plugin_core_module.platform, "system", lambda: "Windows")
    plugin = plugin_core_module.BasePlugin()

    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_file_execution", lambda self: True)
    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "probe_powershell_encoded_command", lambda self: True)
    monkeypatch.setattr(plugin_core_module.WindowsHostRuntime, "run_schtasks", lambda self, args, timeout=20: False)

    success, message = plugin.restartDomoticz()

    assert success is False
    assert message == "Failed to schedule Windows restart task. See restart_domoticz.log."


def test_windows_restart_probe_detects_script_execution_policy(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    runtime = plugin_core_module.WindowsHostRuntime(plugin_core_module.Parameters)

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "cannot be loaded because running scripts is disabled on this system"

    monkeypatch.setattr(plugin_core_module.subprocess, "run", lambda *args, **kwargs: FakeResult())

    assert runtime.probe_powershell_file_execution() is False
    log_text = (manager_dir / "restart_domoticz.log").read_text()
    assert "PowerShell .ps1 probe return code: 1" in log_text
    assert "PowerShell .ps1 execution probe failed" in log_text
    assert "PowerShell execution policy blocks .ps1 files" in log_text


def test_windows_restart_probe_checks_encoded_command(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    runtime = plugin_core_module.WindowsHostRuntime(plugin_core_module.Parameters)

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "This program is blocked by group policy"

    monkeypatch.setattr(plugin_core_module.subprocess, "run", lambda *args, **kwargs: FakeResult())

    assert runtime.probe_powershell_encoded_command() is False
    log_text = (manager_dir / "restart_domoticz.log").read_text()
    assert "PowerShell EncodedCommand probe return code: 1" in log_text
    assert "This program is blocked by group policy" in log_text


def test_restart_helper_logs_command_output(plugin_core_module, tmp_path):
    runtime = plugin_core_module.HostRuntime({})
    log_file = tmp_path / "restart_domoticz.log"
    command_groups = [
        [[
            plugin_core_module.sys.executable,
            "-c",
            "import sys; sys.stderr.write('restart failed'); sys.exit(7)",
        ]],
        [[plugin_core_module.sys.executable, "-c", "print('restart ok')"]],
    ]

    helper = runtime.build_restart_helper(command_groups, str(log_file), startup_delay=0, command_delay=0)
    result = plugin_core_module.subprocess.run(
        [plugin_core_module.sys.executable, "-c", helper],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    log_text = log_file.read_text()
    assert "return code: 7" in log_text
    assert "stderr: restart failed" in log_text
    assert "stdout: restart ok" in log_text
    assert "restart command group completed" in log_text


def test_restart_helper_summarizes_permission_failures(plugin_core_module, tmp_path):
    runtime = plugin_core_module.HostRuntime({})
    log_file = tmp_path / "restart_domoticz.log"
    command_groups = [
        [[
            plugin_core_module.sys.executable,
            "-c",
            "import sys; sys.stderr.write('sudo: een wachtwoord is verplicht'); sys.exit(1)",
        ]],
        [[
            plugin_core_module.sys.executable,
            "-c",
            "import sys; sys.stderr.write('Failed to restart domoticz.service: Access denied'); sys.exit(4)",
        ]],
    ]

    helper = runtime.build_restart_helper(command_groups, str(log_file), startup_delay=0, command_delay=0)
    result = plugin_core_module.subprocess.run(
        [plugin_core_module.sys.executable, "-c", helper],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    log_text = log_file.read_text()
    assert "all restart command groups failed" in log_text
    assert "failure summary: Domoticz restart failed: sudo requires an interactive password." in log_text
    assert "NOPASSWD sudoers rule" in log_text


INVALID_UTF8_WRITER = "import sys; sys.stdout.buffer.write(b'caf\\xe9 \\xff ok\\n')"


def test_restart_helper_tolerates_undecodable_output(plugin_core_module, tmp_path):
    runtime = plugin_core_module.HostRuntime({})
    log_file = tmp_path / "restart_domoticz.log"
    command_groups = [
        [[plugin_core_module.sys.executable, "-c", INVALID_UTF8_WRITER]],
        [[plugin_core_module.sys.executable, "-c", "print('second group')"]],
    ]

    helper = runtime.build_restart_helper(command_groups, str(log_file), startup_delay=0, command_delay=0)
    result = plugin_core_module.subprocess.run(
        [plugin_core_module.sys.executable, "-c", helper],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    log_text = log_file.read_text(encoding="utf-8")
    assert "return code: 0" in log_text
    assert "ok" in log_text
    assert "exception:" not in log_text
    assert "second group" not in log_text
    assert "restart command group completed" in log_text


def test_git_runner_replaces_undecodable_output(plugin_core_module, tmp_path):
    runtime = plugin_core_module.HostRuntime({})

    result = runtime._run_git_once(
        [plugin_core_module.sys.executable, "-c", INVALID_UTF8_WRITER],
        str(tmp_path),
    )

    assert result is not None
    assert result.returncode == 0
    assert result.stdout == "caf� � ok\n"


def test_git_read_only_runner_fails_closed_on_undecodable_output(plugin_core_module, tmp_path, monkeypatch):
    runtime = plugin_core_module.HostRuntime({})
    monkeypatch.setattr(
        runtime,
        "safe_git_command",
        lambda command, cwd: [plugin_core_module.sys.executable, "-c", INVALID_UTF8_WRITER],
    )

    assert runtime.run_git_read_only(["git", "ls-files", "-z"], str(tmp_path)) is None


def test_windows_restart_probes_decode_with_replacement(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    runtime = plugin_core_module.WindowsHostRuntime(plugin_core_module.Parameters)
    calls = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return FakeResult()

    monkeypatch.setattr(plugin_core_module.subprocess, "run", fake_run)

    runtime.probe_powershell_file_execution()
    runtime.probe_powershell_encoded_command()
    runtime.run_schtasks(["/Query"])

    assert len(calls) == 3
    assert all(call.get("errors") == "replace" for call in calls)
