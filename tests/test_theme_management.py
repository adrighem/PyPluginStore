import json
import os
import shutil
import pytest
from types import SimpleNamespace
from plugin_core_helpers import configure_home


def test_theme_paths_and_validation(plugin_core_module, tmp_path):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()

    assert host.themes_dir() == os.path.abspath(os.path.join(tmp_path, "domoticz", "www", "styles"))
    assert host.theme_sources_dir() == os.path.abspath(os.path.join(host.plugin_home_folder(), ".theme_sources"))

    # Test key validations
    assert host.validate_theme_key("nightglass") == "nightglass"
    assert host.validate_theme_key("osi-dark") == "osi-dark"
    assert host.validate_theme_key("aurora") == "aurora"
    assert host.validate_theme_key("little-theme") == "little-theme"
    assert host.validate_theme_key("serenity") == "serenity"
    assert host.validate_theme_key("think-theme") == "think-theme"

    with pytest.raises(ValueError):
        host.validate_theme_key(".")
    with pytest.raises(ValueError):
        host.validate_theme_key("..")
    with pytest.raises(ValueError):
        host.validate_theme_key("sub/path")
    with pytest.raises(ValueError):
        host.validate_theme_key(".hidden")

    # Test resolve_theme_dir safety
    valid_dir = host.resolve_theme_dir("nightglass")
    assert valid_dir == os.path.abspath(os.path.join(host.themes_dir(), "nightglass"))

    with pytest.raises(ValueError):
        host.resolve_theme_dir("../traversal")


def test_theme_registry_service_loading(plugin_core_module, tmp_path):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()

    # Create dummy themes.json
    themes_data = {
        "test-theme": {
            "display_name": "Test Theme",
            "author": "tester",
            "repository": "domoticz-test-theme",
            "branch": "main",
            "description": "A nice test theme.",
            "target_dir": "test-theme",
            "source_path": ".",
            "entry_files": ["custom.css"],
            "contains_javascript": False,
            "requires_restart": "first_install"
        }
    }
    with open(os.path.join(manager_dir, "themes.json"), "w", encoding="utf-8") as f:
        json.dump(themes_data, f)

    registry = plugin.theme_registry_service.load_registry()
    assert "test-theme" in registry
    entry = registry["test-theme"]
    assert entry.display_name == "Test Theme"
    assert entry.author == "tester"
    assert entry.repository == "domoticz-test-theme"
    assert entry.branch == "main"
    assert entry.local is False

    # Create local override themes_local.json
    local_data = {
        "local-theme": {
            "display_name": "Local Theme",
            "author": "local_user",
            "repository": "local-repo",
            "branch": "master",
            "target_dir": "local-theme"
        }
    }
    with open(os.path.join(manager_dir, "themes_local.json"), "w", encoding="utf-8") as f:
        json.dump(local_data, f)

    registry = plugin.theme_registry_service.load_registry()
    assert "test-theme" in registry
    assert "local-theme" in registry
    assert registry["local-theme"].local is True
    assert registry["local-theme"].display_name == "Local Theme"


def test_theme_discovery_and_classification(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()

    # Write dummy registry
    themes_data = {
        "managed-theme": {
            "display_name": "Managed Theme",
            "author": "author",
            "repository": "repo",
            "branch": "main",
            "target_dir": "managed-theme"
        },
        "uninstalled-theme": {
            "display_name": "Uninstalled Theme",
            "author": "author2",
            "repository": "repo2",
            "branch": "main",
            "target_dir": "uninstalled-theme"
        }
    }
    with open(os.path.join(manager_dir, "themes.json"), "w", encoding="utf-8") as f:
        json.dump(themes_data, f)

    # Set up theme folders under www/styles
    styles_dir = host.themes_dir()
    os.makedirs(styles_dir, exist_ok=True)

    # 1. Builtin protected theme
    os.makedirs(os.path.join(styles_dir, "default"), exist_ok=True)

    # 2. Managed theme (with marker)
    managed_dir = os.path.join(styles_dir, "managed-theme")
    os.makedirs(managed_dir, exist_ok=True)
    marker_data = {
        "theme_key": "managed-theme",
        "target_dir": "managed-theme",
        "repository": "https://github.com/author/repo",
        "branch": "main",
        "source_path": ".",
        "installed_commit": "12345",
        "contains_javascript": False,
        "timestamp": "2026-07-04T12:00:00Z"
    }
    with open(os.path.join(managed_dir, ".pypluginstore-theme.json"), "w", encoding="utf-8") as f:
        json.dump(marker_data, f)

    # 3. Unmanaged local theme
    os.makedirs(os.path.join(styles_dir, "local-only"), exist_ok=True)

    # Mock git update status check to return "current" without executing real shell git
    monkeypatch.setattr(plugin.theme_discovery_service, "get_theme_update_status", lambda theme_key, entry: "current")

    discovery = plugin.theme_discovery_service.list_themes()

    assert "managed-theme" in discovery["installed"]
    assert "managed-theme" in discovery["managed"]
    assert "local-only" in discovery["installed"]
    assert "local-only" in discovery["local_themes"]
    assert "uninstalled-theme" not in discovery["installed"]
    assert "default" not in discovery["installed"]  # Protected themes are excluded

    assert discovery["protected_themes"] == sorted([
        "default",
        "dark-th3me",
        "element-dark",
        "element-light",
        "elemental",
        "simple-blue",
        "simple-gray",
    ])
    assert discovery["update_status"]["managed-theme"] == "current"
    assert discovery["update_status"]["uninstalled-theme"] == "available"


def test_install_and_remove_theme(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()

    # Registry setup
    themes_data = {
        "aurora": {
            "display_name": "Aurora Theme",
            "author": "flatsiedatsie",
            "repository": "domoticz-aurora-theme",
            "branch": "main",
            "target_dir": "aurora"
        }
    }
    with open(os.path.join(manager_dir, "themes.json"), "w", encoding="utf-8") as f:
        json.dump(themes_data, f)

    # Mock git calls
    git_calls = []
    def fake_run_git(command, cwd, timeout=15):
        git_calls.append((command, cwd))
        # Simulate cloning success by creating custom.css in source checkout folder
        source_dir = os.path.join(host.theme_sources_dir(), "aurora")
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, "custom.css"), "w") as f:
            f.write("/* aurora custom css */")
        return SimpleNamespace(returncode=0, stdout="git output", stderr="")

    def fake_run_git_command(plugin_dir, command, timeout=15):
        git_calls.append((command, plugin_dir))
        return SimpleNamespace(returncode=0, stdout="abcdef", stderr="")

    monkeypatch.setattr(host, "run_git", fake_run_git)
    monkeypatch.setattr(plugin, "run_git_command", fake_run_git_command)

    # 1. Successful Installation
    success, message = plugin.theme_discovery_service.install_theme("aurora")
    assert success is True
    assert message == "Theme installed successfully"

    dest_dir = host.resolve_theme_dir("aurora")
    assert os.path.isdir(dest_dir)
    assert os.path.isfile(os.path.join(dest_dir, "custom.css"))

    marker_path = os.path.join(dest_dir, ".pypluginstore-theme.json")
    assert os.path.isfile(marker_path)
    with open(marker_path, "r", encoding="utf-8") as f:
        marker = json.load(f)
        assert marker["theme_key"] == "aurora"
        assert marker["target_dir"] == "aurora"
        assert marker["installed_commit"] == "abcdef"

    # 2. Safety constraint checks for removal
    # Protected themes cannot be removed
    success, message = plugin.theme_discovery_service.remove_theme("default")
    assert success is False
    assert "protected" in message.lower()

    # Unmanaged local themes cannot be removed
    unmanaged_dir = host.resolve_theme_dir("unmanaged")
    os.makedirs(unmanaged_dir, exist_ok=True)
    success, message = plugin.theme_discovery_service.remove_theme("unmanaged")
    assert success is False
    assert "unmanaged" in message.lower()

    # 3. Successful Deletion of Managed Theme
    success, message = plugin.theme_discovery_service.remove_theme("aurora")
    assert success is True
    assert message == "Theme removed successfully"
    assert not os.path.exists(dest_dir)
