import pytest

from plugin_core_helpers import configure_home, debug_messages, write_plugin_py


def test_list_plugins_detects_repository_named_existing_folder(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "Domoticz-deCONZ",
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/Smanar/Domoticz-deCONZ",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }
    responses = []

    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "list_plugins"})

    response = responses[0]
    assert "deCONZ" in response["installed"]
    assert "Domoticz-deCONZ" in response["installed"]
    assert plugin.installed_plugin_folders["deCONZ"] == "Domoticz-deCONZ"
    assert response["installed_match_details"]["deCONZ"]["source"] == "plugin.py externallink"
    assert response["installed_match_details"]["Domoticz-deCONZ"]["source"] == "local folder alias"


def test_get_installed_plugins_detects_matching_git_remote(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    renamed_dir = plugins_dir / "MyZigbeePlugin"
    (renamed_dir / ".git").mkdir(parents=True)
    write_plugin_py(renamed_dir, key="DECONZ", name="deCONZ")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "git remote"


def test_exact_registry_folder_without_metadata_is_installed(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "deCONZ").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "deCONZ"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "exact folder key"


def test_exact_registry_folder_with_conflicting_metadata_is_installed(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "deCONZ",
        key="OTHER",
        name="OtherPlugin",
        externallink="https://github.com/example/OtherPlugin",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "deCONZ"


def test_externallink_overrides_exact_registry_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "deCONZ",
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/MadPatrick/Domoticz-BMW-plugin",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Bmw" in installed
    assert "deCONZ" not in installed
    assert plugin.installed_plugin_folders["Bmw"] == "deCONZ"
    assert plugin.installed_plugin_match_details["Bmw"]["source"] == "plugin.py externallink"


def test_repository_named_folder_without_metadata_is_inferred(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Domoticz-deCONZ").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "Domoticz-deCONZ"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "repository/archive folder name"


def test_multiple_physical_folders_for_one_plugin_are_blocked(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    canonical = plugins_dir / "deCONZ"
    repository_named = plugins_dir / "Domoticz-deCONZ"
    canonical.mkdir()
    repository_named.mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": [
            "Smanar",
            "Domoticz-deCONZ",
            "description",
            "master",
            "",
        ],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert "deCONZ" not in plugin.installed_plugin_folders
    assert plugin.ambiguous_installed_plugin_folders["deCONZ"] == [
        "deCONZ",
        "Domoticz-deCONZ",
    ]
    detail = plugin.installed_plugin_match_details["deCONZ"]
    assert detail["source"] == "ambiguous physical folders"
    assert detail["folders"] == ["deCONZ", "Domoticz-deCONZ"]
    with pytest.raises(ValueError, match="Multiple installed folders"):
        plugin.resolve_installed_plugin_dir("deCONZ")

    repository_named.rmdir()

    assert plugin.resolve_installed_plugin_dir(
        "deCONZ",
        refresh=True,
    ) == str(canonical)
    assert plugin.ambiguous_installed_plugin_folders == {}


def test_repository_named_folder_matches_flexible_punctuation(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Domoticz-HP-iLo").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "HP_iLo": ["MadPatrick", "Domoticz_HP_ilo", "description", "main", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "HP_iLo" in installed
    assert plugin.installed_plugin_folders["HP_iLo"] == "Domoticz-HP-iLo"
    assert plugin.installed_plugin_match_details["HP_iLo"]["source"] == "normalized folder name"


def test_local_alias_detects_repository_named_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "domoticz-apc-ups-plugin").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "APC_UPS": ["MadPatrick", "domoticz-apc-ups-plugin", "description", "main", ""],
    }
    plugin.local_plugin_keys = ["APC_UPS"]

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "APC_UPS" in installed
    assert plugin.installed_plugin_folders["APC_UPS"] == "domoticz-apc-ups-plugin"


def test_local_alias_preferred_when_repository_name_collides(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Domoticz-BMW-plugin").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
        "Domoticz-BMW-plugin": ["FilipDem", "Domoticz-BMW-plugin", "description", "main", ""],
    }
    plugin.local_plugin_keys = ["Bmw"]

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Bmw" in installed
    assert "Domoticz-BMW-plugin" not in installed
    assert plugin.installed_plugin_folders["Bmw"] == "Domoticz-BMW-plugin"
    assert plugin.installed_plugin_match_details["Bmw"]["source"] == "repository/archive folder name"


def test_domoticz_affixed_repo_matches_short_branch_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "APC UPS-main").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "APC_UPS": ["MadPatrick", "Domoticz_apc_ups_plugin", "description", "main", ""],
    }
    plugin.local_plugin_keys = ["APC_UPS"]

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "APC_UPS" in installed
    assert plugin.installed_plugin_folders["APC_UPS"] == "APC UPS-main"
    assert plugin.installed_plugin_match_details["APC_UPS"]["source"] == "normalized folder name"


def test_domoticz_affixed_archive_accepts_stripped_metadata_key(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "Domoticz_Marstek_Modbus-main",
        key="Marstek_modbus",
        name="Marstek Venus Modbus",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Domoticz_Marstek_Modbus": [
            "hopSilentSimon",
            "Domoticz_Marstek_Modbus",
            "description",
            "main",
            "",
        ],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Domoticz_Marstek_Modbus" in installed
    assert plugin.installed_plugin_folders["Domoticz_Marstek_Modbus"] == "Domoticz_Marstek_Modbus-main"
    assert plugin.installed_plugin_match_details["Domoticz_Marstek_Modbus"]["source"] == "repository/archive folder name"


def test_git_remote_match_does_not_require_plugin_metadata(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    renamed_dir = plugins_dir / "MyZigbeePlugin"
    (renamed_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"


def test_git_remote_match_overrides_conflicting_externallink(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    renamed_dir = plugins_dir / "MyZigbeePlugin"
    (renamed_dir / ".git").mkdir(parents=True)
    write_plugin_py(
        renamed_dir,
        key="BMW",
        name="BMW",
        externallink="https://github.com/MadPatrick/Domoticz-BMW-plugin",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:Smanar/Domoticz-deCONZ.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert "Bmw" not in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "git remote"


def test_unmatched_git_remote_falls_back_to_externallink(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    fork_dir = plugins_dir / "MyPrivateFork"
    (fork_dir / ".git").mkdir(parents=True)
    write_plugin_py(
        fork_dir,
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/Smanar/Domoticz-deCONZ",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:private/Domoticz-deCONZ-fork.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyPrivateFork"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "plugin.py externallink"


def test_unmatched_git_remote_allows_exact_folder_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "deCONZ"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:private/Domoticz-deCONZ-fork.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "deCONZ"


def test_installed_fork_branch_reports_registry_mismatch(plugin_core_module, tmp_path):
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

    class FakeGitResult:
        stderr = ""
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_git(plugin_dir_arg, command, timeout=15):
        if command == ["git", "remote", "-v"]:
            return FakeGitResult(
                "origin\thttps://github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin.git (fetch)\n"
            )
        if command == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return FakeGitResult("MetersDev\n")
        if command == ["git", "config", "--get", "branch.MetersDev.remote"]:
            return FakeGitResult("origin\n")
        if command == ["git", "remote", "get-url", "origin"]:
            return FakeGitResult("https://github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin.git\n")
        raise AssertionError("unexpected git command: " + repr(command))

    plugin.run_git_command = fake_git

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "domoticz-solaredge-modbustcp-plugin" in installed
    details = plugin.installed_plugin_match_details["domoticz-solaredge-modbustcp-plugin"]
    assert details["registry_mismatch"] is True
    assert details["repo_mismatch"] is True
    assert details["branch_mismatch"] is True
    assert details["configured_repo"] == "github.com/addiejanssen/domoticz-solaredge-modbustcp-plugin"
    assert details["configured_branch"] == "meters"
    assert details["installed_repo"] == "github.com/jvanderzande/domoticz-solaredge-modbustcp-plugin"
    assert details["installed_branch"] == "MetersDev"
    assert plugin.getCachedUpdateStatuses(installed)["domoticz-solaredge-modbustcp-plugin"] == "mismatch"


def test_unmatched_git_remote_allows_repository_folder_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Domoticz-deCONZ"
    (plugin_dir / ".git").mkdir(parents=True)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    class FakeGitResult:
        stdout = "origin\tgit@github.com:private/Domoticz-deCONZ-fork.git (fetch)\n"
        stderr = ""
        returncode = 0

    plugin.run_git_command = lambda *args, **kwargs: FakeGitResult()

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "Domoticz-deCONZ"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "repository/archive folder name"


def test_unknown_externallink_allows_metadata_name_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "MyZigbeePlugin",
        key="DECONZ",
        name="deCONZ",
        externallink="https://github.com/private/Domoticz-deCONZ-fork",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"
    assert plugin.installed_plugin_match_details["deCONZ"]["source"] == "plugin.py key/name"
    assert any("externallink" in message and "does not match the registry" in message for message in debug_messages(plugin_core_module))


def test_externallink_match_detects_arbitrary_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(
        plugins_dir / "MyZigbeePlugin",
        key="OTHER",
        name="OtherPlugin",
        externallink="https://github.com/Smanar/Domoticz-deCONZ",
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"


def test_plugin_metadata_name_detects_arbitrary_folder(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "MyZigbeePlugin", key="DECONZ", name="deCONZ")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" in installed
    assert plugin.installed_plugin_folders["deCONZ"] == "MyZigbeePlugin"


def test_invalid_folder_inference_falls_back_to_metadata_name(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "Domoticz-deCONZ", key="BMW", name="BMW")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
        "Bmw": ["MadPatrick", "Domoticz-BMW-plugin", "description", "PdB", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "Bmw" in installed
    assert "deCONZ" not in installed
    assert plugin.installed_plugin_folders["Bmw"] == "Domoticz-deCONZ"
    assert plugin.installed_plugin_match_details["Bmw"]["source"] == "plugin.py key/name"
    assert any("continuing with lower priority evidence" in message for message in debug_messages(plugin_core_module))


def test_flexible_folder_match_rejects_ambiguous_names(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Shared Plugin").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "FirstPlugin": ["owner-a", "Shared-Plugin", "description", "master", ""],
        "SecondPlugin": ["owner-b", "Shared_Plugin", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "FirstPlugin" not in installed
    assert "SecondPlugin" not in installed
    assert plugin.installed_plugin_match_details["Shared Plugin"]["source"] == "local folder"
    assert any("normalized folder name" in message and "multiple registry entries" in message for message in debug_messages(plugin_core_module))


def test_archive_folder_rejects_ambiguous_repository_name(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "SharedRepo-master", key="SHARED", name="SharedRepo")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "FirstPlugin": ["owner-a", "SharedRepo", "description", "master", ""],
        "SecondPlugin": ["owner-b", "SharedRepo", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "FirstPlugin" not in installed
    assert "SecondPlugin" not in installed
    assert plugin.installed_plugin_match_details["SharedRepo-master"]["source"] == "local folder"
    assert any("folder name" in message and "multiple registry entries" in message for message in debug_messages(plugin_core_module))


def test_archive_folder_does_not_accept_author_only_match(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "Domoticz-deCONZ-master"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        '"""\n<plugin key="OTHER" name="OtherPlugin" author="Smanar">\n</plugin>\n"""\n'
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "deCONZ": ["Smanar", "Domoticz-deCONZ", "description", "master", ""],
    }

    installed = plugin.getInstalledPlugins(plugins_dir)

    assert "deCONZ" not in installed
