import json
from pathlib import Path

from plugin_core_helpers import configure_home

def test_safe_plugin_dir_rejects_traversal(plugin_core_module, tmp_path):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()

    assert Path(host.resolve_plugin_dir("NormalPlugin")).name == "NormalPlugin"

    for bad_key in ("../outside", "..\\outside", ".hidden", ""):
        try:
            host.resolve_plugin_dir(bad_key)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad_key} should be rejected")


def test_remove_command_rejects_traversal(plugin_core_module, tmp_path):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()

    success, message = plugin.removePlugin("../outside")

    assert success is False
    assert "Invalid plugin key" in message


def test_windows_locked_remove_is_queued(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin_dir = tmp_path / "domoticz" / "plugins" / "LockedPlugin"
    plugin_dir.mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.host = plugin_core_module.WindowsHostRuntime(plugin_core_module.Parameters)

    monkeypatch.setattr(plugin_core_module.shutil, "rmtree", lambda path: (_ for _ in ()).throw(PermissionError("in use")))

    success, message = plugin.removePlugin("LockedPlugin")

    assert success is False
    assert "queued" in message
    assert json.loads((manager_dir / "pending_operations.json").read_text()) == [
        {"action": "remove", "plugin_key": "LockedPlugin"}
    ]


def test_startup_folder_split_paths(plugin_core_module, tmp_path):
    import os
    configure_home(plugin_core_module, tmp_path)

    # Configure custom StartupFolder in Parameters
    startup_dir = tmp_path / "startup_domoticz"
    startup_dir.mkdir()
    plugin_core_module.Parameters["StartupFolder"] = str(startup_dir) + "/"

    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()

    # Assert paths resolve to StartupFolder
    assert host.startup_dir() == os.path.abspath(startup_dir)
    assert host.templates_dir() == os.path.join(host.startup_dir(), "www", "templates")
    assert host.images_dir() == os.path.join(host.startup_dir(), "www", "images")
    assert host.themes_dir() == os.path.join(host.startup_dir(), "www", "styles")

    # Assert fallback if StartupFolder is empty or missing
    plugin_core_module.Parameters["StartupFolder"] = ""
    plugin_fallback = plugin_core_module.BasePlugin()
    host_fallback = plugin_fallback.get_host()

    assert host_fallback.startup_dir() == host_fallback.domoticz_dir()
    assert host_fallback.templates_dir() == os.path.join(host_fallback.domoticz_dir(), "www", "templates")
    assert host_fallback.images_dir() == os.path.join(host_fallback.domoticz_dir(), "www", "images")
    assert host_fallback.themes_dir() == os.path.join(host_fallback.domoticz_dir(), "www", "styles")
