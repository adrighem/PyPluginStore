import json

import pytest

from plugin_core_helpers import (
    configure_home,
    write_manager_identity_bundle,
    write_plugin_py,
)


def self_hosted_release_entry(provider="gitea"):
    host = provider + ".example.test"
    return {
        "owner": "https://" + host + "/team",
        "repository": "example-plugin",
        "description": provider + " plugin",
        "branch": "main",
        "delivery": {
            "schema_version": 1,
            "preferred": "release_if_indexed",
            "git_supported": True,
            "release": {
                "provider": provider,
                "channel": "stable",
                "tag_pattern": r"^v[0-9]+\.[0-9]+\.[0-9]+$",
                "artifact": "source_zip",
                "source_path": ".",
                "mutable_paths": [],
                "api_base": "https://" + host + "/api/v1",
                "web_base": "https://" + host,
                "release_page_size": 50,
            },
        },
    }


def test_add_self_to_registry_uses_installed_folder(plugin_core_module, tmp_path):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()

    plugin.add_self_to_registry()

    assert plugin.plugin_data["00-PyPluginStore"] == [
        "adrighem",
        "PyPluginStore",
        "PyPluginStore plugin manager",
        "master",
        "",
    ]


def test_on_start_installs_custom_ui_and_icon_image(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    write_manager_identity_bundle(manager_dir)
    (manager_dir / "pypluginstore-icon.png").write_bytes(b"icon")
    plugin_core_module.Devices = {}

    class FakeDevice:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def Create(self):
            return None

    monkeypatch.setattr(plugin_core_module.Domoticz, "Device", FakeDevice, raising=False)
    monkeypatch.setattr(plugin_core_module.BasePlugin, "fetch_registry", lambda self: None)

    plugin_core_module.BasePlugin().onStart()

    domoticz_dir = tmp_path / "domoticz"
    deployed_html = (
        domoticz_dir / "www" / "templates" / "pypluginstore.html"
    ).read_text()
    assert "<div>Plugin Store</div>" in deployed_html
    assert '"build_id":"0000000000000000' not in deployed_html
    assert (domoticz_dir / "www" / "images" / "pypluginstore-icon.png").read_bytes() == b"icon"
    assert not (domoticz_dir / "www" / "templates" / "pypluginstore-icon.png").exists()


def test_on_start_creates_payload_device_without_enabling_it(plugin_core_module, tmp_path, monkeypatch):
    configure_home(plugin_core_module, tmp_path)
    plugin_core_module.Devices = {}
    created_devices = []

    class FakeDevice:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def Create(self):
            created_devices.append(self.kwargs)

    monkeypatch.setattr(plugin_core_module.Domoticz, "Device", FakeDevice, raising=False)
    monkeypatch.setattr(plugin_core_module.BasePlugin, "fetch_registry", lambda self: None)

    plugin_core_module.BasePlugin().onStart()

    payload_device = next(device for device in created_devices if device["DeviceID"] == "PPM_API_PAYLOAD")
    trigger_device = next(device for device in created_devices if device["DeviceID"] == "PPM_API_TRIGGER")

    assert "Used" not in payload_device
    assert trigger_device["Used"] == 1


def test_on_start_setup_warning_skips_missing_notification_api(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir = tmp_path / "domoticz" / "plugins"
    manager_dir = plugins_dir / "PyPluginStore"
    manager_dir.mkdir(parents=True)
    plugin_core_module.Parameters = {
        "HomeFolder": str(manager_dir) + "/",
        "Mode4": "None",
        "Mode6": "Normal",
    }
    plugin_core_module.Devices = {}

    class FakeDevice:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def Create(self):
            return None

    monkeypatch.setattr(plugin_core_module.Domoticz, "Device", FakeDevice, raising=False)
    monkeypatch.delattr(plugin_core_module.Domoticz, "SendNotification", raising=False)
    monkeypatch.setattr(plugin_core_module.BasePlugin, "fetch_registry", lambda self: None)

    plugin_core_module.BasePlugin().onStart()

    assert any("strongly advised" in args[0] for args, _ in plugin_core_module.Domoticz.calls["Error"])
    assert any("Notification skipped" in args[0] for args, _ in plugin_core_module.Domoticz.calls["Log"])


@pytest.mark.parametrize(
    ("update_mode", "method_name"),
    [
        ("All", "UpdatePythonPlugin"),
        ("AllNotify", "CheckForUpdatePythonPlugin"),
    ],
)
def test_on_start_marks_configured_update_actions_as_automatic(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    update_mode,
    method_name,
):
    plugins_dir, manager_dir = configure_home(plugin_core_module, tmp_path)
    write_manager_identity_bundle(manager_dir)
    write_plugin_py(
        plugins_dir / "ExamplePlugin",
        key="ExamplePlugin",
        name="Example plugin",
    )
    plugin_core_module.Parameters["Mode4"] = update_mode
    plugin_core_module.Devices = {}

    class FakeDevice:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def Create(self):
            return None

    plugin = plugin_core_module.BasePlugin()
    calls = []

    def fetch_registry():
        plugin.plugin_data = {
            "ExamplePlugin": [
                "owner",
                "example-plugin",
                "Example plugin",
                "main",
                "",
            ]
        }
        plugin.registry_entries = {}

    monkeypatch.setattr(plugin_core_module.Domoticz, "Device", FakeDevice, raising=False)
    monkeypatch.setattr(plugin, "fetch_registry", fetch_registry)
    monkeypatch.setattr(
        plugin,
        method_name,
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    plugin.onStart()

    assert calls == [
        (
            ("owner", "example-plugin", "ExamplePlugin"),
            {"trigger": "automatic"},
        )
    ]


def test_load_update_times_falls_back_to_bundled_file(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    bundled_file = manager_dir / "update_times.json"
    bundled_file.write_text(json.dumps({"Plugin": "2026-06-14T15:10:03Z"}))
    plugin = plugin_core_module.BasePlugin()

    def fake_urlopen(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(plugin_core_module.urllib.request, "urlopen", fake_urlopen)

    assert plugin.load_update_times() == {"Plugin": "2026-06-14T15:10:03Z"}
    assert json.loads(bundled_file.read_text()) == {"Plugin": "2026-06-14T15:10:03Z"}
    assert json.loads((manager_dir / "update_times.cache.json").read_text()) == {
        "schema_version": 2,
        "updates": [
            {
                "package_id": "Plugin",
                "updated_at": "2026-06-14T15:10:03Z",
            }
        ],
    }


def test_load_update_times_accepts_strict_remote_v2(
    plugin_core_module, tmp_path, monkeypatch
):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps(
                {
                    "schema_version": 2,
                    "updates": [
                        {
                            "package_id": "Plugin",
                            "updated_at": "2026-07-21T12:00:00Z",
                        }
                    ],
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        plugin_core_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )

    assert plugin.load_update_times() == {
        "Plugin": "2026-07-21T12:00:00Z"
    }
    assert json.loads(
        (manager_dir / "update_times.cache.json").read_text()
    ) == {
        "schema_version": 2,
        "updates": [
            {
                "package_id": "Plugin",
                "updated_at": "2026-07-21T12:00:00Z",
            }
        ],
    }


def test_legacy_update_times_normalize_to_canonical_utc(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()

    assert plugin.normalize_update_times_document(
        {
            "OffsetPlugin": "2026-07-21T14:00:00+02:00",
            "FractionPlugin": "2026-07-21T12:00:00.958Z",
        }
    ) == {
        "FractionPlugin": "2026-07-21T12:00:00Z",
        "OffsetPlugin": "2026-07-21T12:00:00Z",
    }


def test_load_update_times_uses_bundled_when_cache_write_fails(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / "update_times.json").write_text(json.dumps({"Plugin": "2026-06-14T15:10:03Z"}))
    plugin = plugin_core_module.BasePlugin()

    def fake_urlopen(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(plugin_core_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(plugin, "save_update_times_cache", lambda update_times: False)

    assert plugin.load_update_times() == {"Plugin": "2026-06-14T15:10:03Z"}


def test_load_update_times_keeps_newer_cached_timestamp(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / "update_times.cache.json").write_text(json.dumps({"Plugin": "2026-06-14T15:10:03Z"}))
    plugin = plugin_core_module.BasePlugin()
    saved_update_times = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({"Plugin": "2026-01-01T00:00:00Z"}).encode("utf-8")

    monkeypatch.setattr(plugin_core_module.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(plugin, "save_update_times_cache", lambda update_times: saved_update_times.append(dict(update_times)) or True)

    assert plugin.load_update_times() == {"Plugin": "2026-06-14T15:10:03Z"}
    assert saved_update_times == [{"Plugin": "2026-06-14T15:10:03Z"}]


def test_fetch_registry_merges_remote_registry_with_local_overlay(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / "registry_local.json").write_text(json.dumps({
        "LocalPlugin": ["git@github.com:owner/private-plugin.git", "", "local description", "main", "2030-01-01T00:00:00Z"],
        "PublicPlugin": ["local-owner", "public-plugin", "local override", "main"],
    }))
    plugin = plugin_core_module.BasePlugin()

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({
                "PublicPlugin": ["remote-owner", "public-plugin", "remote description", "main"],
                "RemoteOnly": ["remote-owner", "remote-only", "remote only", "master"],
            }).encode("utf-8")

    monkeypatch.setattr(plugin_core_module.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(plugin, "load_update_times", lambda: {
        "LocalPlugin": "2026-06-16T18:42:59Z",
        "PublicPlugin": "2026-06-17T18:42:59Z",
        "RemoteOnly": "2026-06-14T15:10:03Z",
    })

    plugin.fetch_registry()

    assert plugin.plugin_data["PublicPlugin"] == [
        "https://github.com/local-owner/public-plugin.git",
        "",
        "local override",
        "main",
    ]
    assert plugin.plugin_data["LocalPlugin"] == [
        "git@github.com:owner/private-plugin.git",
        "",
        "local description",
        "main",
    ]
    assert plugin.plugin_data["RemoteOnly"] == [
        "remote-owner",
        "remote-only",
        "remote only",
        "master",
        "2026-06-14T15:10:03Z",
    ]
    assert plugin.local_plugin_keys == ["LocalPlugin", "PublicPlugin"]
    assert plugin.public_registry_data == {
        "PublicPlugin": [
            "remote-owner",
            "public-plugin",
            "remote description",
            "main",
        ],
        "RemoteOnly": [
            "remote-owner",
            "remote-only",
            "remote only",
            "master",
        ],
    }


def test_fetch_registry_falls_back_to_bundled_registry(plugin_core_module, tmp_path, monkeypatch):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    (manager_dir / "registry.json").write_text(json.dumps({
        "BundledPlugin": ["owner", "repo", "description", "main"],
    }))
    plugin = plugin_core_module.BasePlugin()

    monkeypatch.setattr(plugin_core_module.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(plugin, "load_update_times", lambda: {})

    plugin.fetch_registry()

    assert plugin.plugin_data["BundledPlugin"] == ["owner", "repo", "description", "main", ""]
    assert plugin.local_plugin_keys == []


def test_registry_normalizer_accepts_object_entries_with_platforms(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()

    registry, platforms = plugin.normalize_registry({
        "ObjectPlugin": {
            "owner": "owner",
            "repository": "repo",
            "description": "description",
            "branch": "main",
            "platforms": ["Linux", "Windows", "other"],
        },
        "ListPlugin": ["owner", "repo", "description", "main", "", ["windows"]],
    })

    assert registry["ObjectPlugin"] == ["owner", "repo", "description", "main"]
    assert platforms["ObjectPlugin"] == ["linux", "windows"]
    assert registry["ListPlugin"] == ["owner", "repo", "description", "main", ""]
    assert platforms["ListPlugin"] == ["windows"]


def test_registry_normalizer_consumes_strict_v2_package_records(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()
    document = {
        "schema_version": 2,
        "packages": [
            {
                "package_id": "SmaPackage",
                "domoticz_key": "SMA",
                "description": "SMA inverter",
                "repository": {
                    "url": "https://gitlab.com/group/sma-plugin",
                    "branch": "main",
                },
                "platforms": ["linux"],
                "delivery": {
                    "preferred": "release_if_indexed",
                    "git_supported": True,
                    "release": {
                        "provider": "gitlab",
                        "channel": "stable",
                        "tag_pattern": r"^v[0-9]+\.[0-9]+\.[0-9]+$",
                        "artifact": "source_zip",
                        "source_path": ".",
                        "mutable_paths": [],
                    },
                },
            }
        ],
    }

    registry, platforms = plugin.normalize_registry(document)

    assert registry == {
        "SmaPackage": [
            "https://gitlab.com/group/sma-plugin",
            "",
            "SMA inverter",
            "main",
        ]
    }
    entry = plugin.registry_entries["SmaPackage"]
    assert entry.package_id == "SmaPackage"
    assert entry.domoticz_key == "SMA"
    assert entry.delivery.preferred == "release_if_indexed"
    assert entry.delivery.release.provider == "gitlab"
    assert platforms == {"SmaPackage": ["linux"]}


def test_registry_entry_model_preserves_legacy_shape(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()

    registry, platforms = plugin.normalize_registry({
        "ObjectPlugin": {
            "owner": "owner",
            "repository": "repo",
            "description": "description",
            "branch": "main",
            "platforms": ["linux"],
        },
        "ListPlugin": ["owner", "repo", "description", "main", "", ["windows"]],
    })

    assert registry == {
        "ObjectPlugin": ["owner", "repo", "description", "main"],
        "ListPlugin": ["owner", "repo", "description", "main", ""],
    }
    assert platforms == {
        "ObjectPlugin": ["linux"],
        "ListPlugin": ["windows"],
    }
    assert plugin.registry_entries["ObjectPlugin"].to_legacy_list() == registry["ObjectPlugin"]
    assert plugin.registry_entries["ListPlugin"].to_legacy_list() == registry["ListPlugin"]


def test_registry_accepts_self_hosted_release_provider_capabilities(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()
    entries = {
        provider: self_hosted_release_entry(provider)
        for provider in ("forgejo", "gitea")
    }

    registry, _platforms = plugin.normalize_registry(entries)

    assert set(registry) == {"forgejo", "gitea"}
    for provider in entries:
        policy = plugin.registry_entries[provider].delivery.release
        assert policy.web_base == "https://" + provider + ".example.test"
        assert policy.release_page_size == 50
        assert policy.allowed_origins == []
    assert plugin_core_module.Domoticz.calls["Error"] == []


def test_registry_accepts_codeberg_release_policy_defaults(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()
    entry = self_hosted_release_entry("forgejo")
    entry["owner"] = "codeberg.org/team"
    release = entry["delivery"]["release"]
    for field in ("api_base", "web_base", "release_page_size"):
        del release[field]

    registry, _platforms = plugin.normalize_registry({"Codeberg": entry})

    assert "Codeberg" in registry
    policy = plugin.registry_entries["Codeberg"].delivery.release
    assert policy.web_base == ""
    assert policy.release_page_size == 0


@pytest.mark.parametrize(
    "missing_field",
    ("api_base", "web_base", "release_page_size"),
)
def test_registry_requires_complete_self_hosted_release_capabilities(
    plugin_core_module,
    missing_field,
):
    plugin = plugin_core_module.BasePlugin()
    entry = self_hosted_release_entry()
    del entry["delivery"]["release"][missing_field]

    registry, _platforms = plugin.normalize_registry({"Gitea": entry})

    assert registry == {}
    assert "require api_base, web_base, and release_page_size" in str(
        plugin_core_module.Domoticz.calls["Error"][0][0][0]
    )


@pytest.mark.parametrize("page_size", (True, 0, 101, "50"))
def test_registry_rejects_invalid_release_page_size(
    plugin_core_module,
    page_size,
):
    plugin = plugin_core_module.BasePlugin()
    entry = self_hosted_release_entry()
    entry["delivery"]["release"]["release_page_size"] = page_size

    registry, _platforms = plugin.normalize_registry({"Gitea": entry})

    assert registry == {}
    assert "must be between 1 and 100" in str(
        plugin_core_module.Domoticz.calls["Error"][0][0][0]
    )


@pytest.mark.parametrize(
    "web_base",
    (
        "http://gitea.example.test",
        "https://user:secret@gitea.example.test",
        "https://gitea.example.test?token=secret",
        "https://gitea.example.test#fragment",
        "https://gitea.example.test:70000",
    ),
)
def test_registry_rejects_unsafe_release_web_base(
    plugin_core_module,
    web_base,
):
    plugin = plugin_core_module.BasePlugin()
    entry = self_hosted_release_entry()
    entry["delivery"]["release"]["web_base"] = web_base

    registry, _platforms = plugin.normalize_registry({"Gitea": entry})

    assert registry == {}


def test_registry_rejects_capability_fields_for_github(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()
    entry = self_hosted_release_entry()
    entry["owner"] = "owner"
    entry["delivery"]["release"]["provider"] = "github"

    registry, _platforms = plugin.normalize_registry({"GitHub": entry})

    assert registry == {}
    assert "only valid for Forgejo and Gitea" in str(
        plugin_core_module.Domoticz.calls["Error"][0][0][0]
    )


def test_registry_rejects_self_hosted_release_web_host_mismatch(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()
    entry = self_hosted_release_entry()
    entry["delivery"]["release"]["web_base"] = (
        "https://other.example.test"
    )

    registry, _platforms = plugin.normalize_registry({"Gitea": entry})

    assert registry == {}
    assert "does not match registry host" in str(
        plugin_core_module.Domoticz.calls["Error"][0][0][0]
    )


def test_registry_rejects_self_hosted_release_web_port_mismatch(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()
    entry = self_hosted_release_entry()
    entry["owner"] = "https://gitea.example.test:8443/team"
    entry["delivery"]["release"]["api_base"] = (
        "https://gitea.example.test:8443/api/v1"
    )

    registry, _platforms = plugin.normalize_registry({"Gitea": entry})

    assert registry == {}
    assert "does not match registry host" in str(
        plugin_core_module.Domoticz.calls["Error"][0][0][0]
    )


def test_get_registry_entry_rebuilds_from_legacy_plugin_data(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "ManualPlugin": ["owner", "repo", "description", "develop", "2026-06-14T15:10:03Z"],
    }
    plugin.plugin_platforms = {"ManualPlugin": ["windows"]}

    entry = plugin.get_registry_entry("ManualPlugin")

    assert entry.key == "ManualPlugin"
    assert entry.author == "owner"
    assert entry.repository == "repo"
    assert entry.description == "description"
    assert entry.branch == "develop"
    assert entry.updated_at == "2026-06-14T15:10:03Z"
    assert entry.platforms == ["windows"]


def test_build_git_clone_url_accepts_owner_repo_and_full_urls(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()

    assert plugin.build_git_clone_url("owner", "repo") == "https://github.com/owner/repo.git"
    assert plugin.build_git_clone_url("github.com/owner/repo", "") == "https://github.com/owner/repo.git"
    assert plugin.build_git_clone_url("https://github.com/owner/repo/tree/main", "") == "https://github.com/owner/repo.git"
    assert plugin.build_git_clone_url("git@github.com:owner/private-repo.git", "") == "git@github.com:owner/private-repo.git"
    assert plugin.build_git_clone_url("file:///srv/git/local-plugin", "") == "file:///srv/git/local-plugin"


def test_build_git_clone_url_accepts_codeberg_and_gitlab_hosts(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()

    assert plugin.build_git_clone_url(
        "codeberg.org/Hoog",
        "Domoticz-Stromer-plugin",
    ) == "https://codeberg.org/Hoog/Domoticz-Stromer-plugin.git"
    assert plugin.build_git_clone_url(
        "gitlab.com/r.boeters",
        "DomoticzSabNZBDPlugin",
    ) == "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin.git"
    assert plugin.build_git_clone_url(
        "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/tree/master",
        "",
    ) == "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin.git"
    assert plugin.build_git_clone_url(
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/src/branch/main",
        "",
    ) == "https://codeberg.org/Hoog/Domoticz-Stromer-plugin.git"


def test_build_git_clone_url_only_normalizes_real_github_hosts(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()

    assert plugin.build_git_clone_url(
        "https://github.com.evil/owner/repo/tree/main",
        "",
    ) == "https://github.com.evil/owner/repo/tree/main"
    assert plugin.build_git_clone_url(
        "https://example.com/github.com/owner/repo/tree/main",
        "",
    ) == "https://example.com/github.com/owner/repo/tree/main"


def test_normalize_git_repo_identity_supports_codeberg_and_gitlab(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()

    assert plugin.normalize_git_repo_identity(
        "git@gitlab.com:r.boeters/DomoticzSabNZBDPlugin.git",
    ) == "gitlab.com/r.boeters/domoticzsabnzbdplugin"
    assert plugin.normalize_git_repo_identity(
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/src/branch/main",
    ) == "codeberg.org/hoog/domoticz-stromer-plugin"
    assert plugin.normalize_github_repo_identity(
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin",
    ) == "codeberg.org/hoog/domoticz-stromer-plugin"


def test_get_plugin_versions_fetches_raw_plugin_py_for_supported_hosts(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    (plugins_dir / "Stromer").mkdir()
    (plugins_dir / "Stromer" / "plugin.py").write_text(
        '"""\n<plugin key="Stromer" name="Stromer" version="1.0.0">\n</plugin>\n"""\n'
    )
    (plugins_dir / "SabNZBD").mkdir()
    (plugins_dir / "SabNZBD" / "plugin.py").write_text(
        '"""\n<plugin key="SabNZBD" name="SabNZBD" version="0.0.1">\n</plugin>\n"""\n'
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "Stromer": ["codeberg.org/Hoog", "Domoticz-Stromer-plugin", "description", "main", ""],
        "SabNZBD": ["gitlab.com/r.boeters", "DomoticzSabNZBDPlugin", "description", "master", ""],
    }
    fetched_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return b'"""\n<plugin key="Remote" name="Remote" version="2.0.0">\n</plugin>\n"""\n'

    def fake_urlopen(request, timeout=0):
        fetched_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(plugin_core_module.urllib.request, "urlopen", fake_urlopen)

    versions = plugin.get_plugin_versions(
        ["Stromer", "SabNZBD"],
        {"Stromer": "available", "SabNZBD": "available"},
        str(plugins_dir),
    )

    assert fetched_urls == [
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/raw/branch/main/plugin.py",
        "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/raw/master/plugin.py",
    ]
    assert versions == {
        "Stromer": {"installed": "1.0.0", "available": "2.0.0"},
        "SabNZBD": {"installed": "0.0.1", "available": "2.0.0"},
    }


def test_get_plugin_versions_uses_local_override_branch(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin_dir = plugins_dir / "SolarEdge"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        '"""\n<plugin key="SolarEdge_ModbusTCP" name="SolarEdge ModbusTCP" version="2.0.5.5">\n</plugin>\n"""\n'
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
    plugin.installed_plugin_folders = {
        "domoticz-solaredge-modbustcp-plugin": "SolarEdge",
    }
    fetched_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return b'"""\n<plugin key="SolarEdge_ModbusTCP" name="SolarEdge ModbusTCP" version="2.0.4">\n</plugin>\n"""\n'

    def fake_urlopen(request, timeout=0):
        fetched_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(plugin_core_module.urllib.request, "urlopen", fake_urlopen)

    versions = plugin.get_plugin_versions(
        ["domoticz-solaredge-modbustcp-plugin"],
        {"domoticz-solaredge-modbustcp-plugin": "available"},
        str(plugins_dir),
    )

    assert fetched_urls == [
        "https://raw.githubusercontent.com/addiejanssen/domoticz-solaredge-modbustcp-plugin/meters/plugin.py",
    ]
    assert versions == {
        "domoticz-solaredge-modbustcp-plugin": {
            "installed": "2.0.5.5",
            "available": "2.0.4",
        },
    }


def test_list_plugins_response_includes_manager_and_update_status(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "OtherPlugin", key="OTHER", name="OtherPlugin")
    (plugins_dir / "OtherPlugin" / ".git").mkdir()
    (plugins_dir / ".hidden").mkdir()
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "00-PyPluginStore": ["adrighem", "PyPluginStore", "PyPluginStore plugin manager", "master", ""],
        "OtherPlugin": ["owner", "repo", "description", "main", ""],
    }
    plugin.update_status = {
        "00-PyPluginStore": "current",
        "OtherPlugin": "available",
    }
    responses = []

    monkeypatch.setattr(plugin, "getGitUpdateStatus", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected git check")))
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "list_plugins"})

    response = responses[0]
    assert response["status"] == "success"
    assert response["manager_key"] == "00-PyPluginStore"
    assert set(response["installed"]) == {"00-PyPluginStore", "OtherPlugin"}
    assert response["update_status"] == {
        "00-PyPluginStore": "current",
        "OtherPlugin": "available",
    }
    assert response["local_plugins"] == []
    assert response["installed_match_details"]["00-PyPluginStore"]["source"] == "plugin.py externallink"
    assert response["installed_match_details"]["00-PyPluginStore"]["is_git"] is False
    assert response["installed_match_details"]["OtherPlugin"]["source"] == "exact folder key"
    assert response["installed_match_details"]["OtherPlugin"]["is_git"] is True
    assert response["platforms"] == {}
    assert response["self_update"]["phase"] == "idle"


def test_list_plugins_response_includes_local_plugin_keys(plugin_core_module, tmp_path, monkeypatch):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    write_plugin_py(plugins_dir / "LocalPlugin", key="PRIVATE", name="LocalPlugin")
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "LocalPlugin": ["git@github.com:owner/private-plugin.git", "", "description", "main", ""],
    }
    plugin.local_plugin_keys = ["LocalPlugin"]
    responses = []

    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "list_plugins"})

    assert responses[0]["local_plugins"] == ["LocalPlugin"]
    assert set(responses[0]["installed"]) == {"00-PyPluginStore", "LocalPlugin"}


def test_on_command_ignores_empty_api_payload(plugin_core_module, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    handled_payloads = []
    plugin_core_module.Devices = {1: FakeTextDevice("")}

    monkeypatch.setattr(plugin, "handleApiCommand", handled_payloads.append)

    plugin.onCommand(2, "On", 0, 0)

    assert handled_payloads == []
    assert plugin_core_module.Domoticz.calls["Error"] == []


def test_on_command_clears_stale_large_api_response_without_error(plugin_core_module, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    handled_payloads = []
    stale_response = json.dumps({
        "status": "success",
        "action": "list_plugins",
        "tx_id": "123",
        "data": {"Plugin": "x" * 2500},
    })
    plugin_core_module.Devices = {1: FakeTextDevice(stale_response)}

    monkeypatch.setattr(plugin, "handleApiCommand", handled_payloads.append)

    plugin.onCommand(2, "On", 0, 0)

    assert handled_payloads == []
    assert plugin_core_module.Devices[1].sValue == ""
    assert plugin_core_module.Domoticz.calls["Error"] == []


def test_on_command_clears_truncated_stale_api_response_without_error(plugin_core_module, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    handled_payloads = []
    stale_response = '{"status":"success","action":"list_plugins","tx_id":"123","data":"' + ("x" * 2500)
    plugin_core_module.Devices = {1: FakeTextDevice(stale_response)}

    monkeypatch.setattr(plugin, "handleApiCommand", handled_payloads.append)

    plugin.onCommand(2, "On", 0, 0)

    assert handled_payloads == []
    assert plugin_core_module.Devices[1].sValue == ""
    assert plugin_core_module.Domoticz.calls["Error"] == []


def test_on_command_rejects_large_api_request(plugin_core_module, monkeypatch):
    plugin = plugin_core_module.BasePlugin()
    handled_payloads = []
    large_request = json.dumps({
        "action": "install",
        "tx_id": "123",
        "plugin_key": "x" * 2500,
    })
    plugin_core_module.Devices = {1: FakeTextDevice(large_request)}

    monkeypatch.setattr(plugin, "handleApiCommand", handled_payloads.append)

    plugin.onCommand(2, "On", 0, 0)

    assert handled_payloads == []
    assert plugin_core_module.Devices[1].sValue == ""
    assert any("API Payload exceeds length limit." in args[0] for args, _ in plugin_core_module.Domoticz.calls["Error"])


def test_send_api_response_logs_error_payload_with_context(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()
    plugin_core_module.Devices = {1: FakeTextDevice("")}

    plugin.sendApiResponse({
        "status": "error",
        "action": "update",
        "plugin_key": "00-PP-MANAGER",
        "message": "preflight failed",
    })

    assert plugin_core_module.Devices[1].sValue == json.dumps({
        "status": "error",
        "action": "update",
        "plugin_key": "00-PP-MANAGER",
        "message": "preflight failed",
    })
    assert any(
        "API update for 00-PP-MANAGER failed: preflight failed" in args[0]
        for args, _ in plugin_core_module.Domoticz.calls["Error"]
    )


def test_send_api_response_logs_error_even_without_payload_device(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()
    plugin_core_module.Devices = {}

    plugin.sendApiResponse({
        "status": "error",
        "action": "restart_domoticz",
        "message": "restart not configured",
    })

    assert any(
        "API restart_domoticz failed: restart not configured" in args[0]
        for args, _ in plugin_core_module.Domoticz.calls["Error"]
    )


class FakeTextDevice:
    def __init__(self, s_value):
        self.sValue = s_value
        self.updates = []

    def Update(self, nValue, sValue):
        self.sValue = sValue
        self.updates.append((nValue, sValue))
