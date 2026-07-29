import json
import os
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from plugin_core_helpers import configure_home


PLUGIN_KEY = "ExamplePlugin"


class InlineScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_script = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self._in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag.lower() == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.scripts[-1] += data


class FakeTextDevice:
    def __init__(self, value=""):
        self.sValue = value
        self.updates = []

    def Update(self, nValue, sValue):
        self.sValue = sValue
        self.updates.append((nValue, sValue))


def load_inline_script():
    html = (REPO_ROOT / "pypluginstore.html").read_text(encoding="utf-8")
    parser = InlineScriptParser()
    parser.feed(html)
    assert parser.scripts
    return parser.scripts[0]


def extract_js_function(script, function_name):
    start = script.index("function " + function_name)
    async_prefix = "async "
    prefix_start = start - len(async_prefix)
    if prefix_start >= 0 and script[prefix_start:start] == async_prefix:
        start = prefix_start
    brace_start = script.index("{", start)
    depth = 0
    for index in range(brace_start, len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start : index + 1]
    raise AssertionError(function_name + " was not closed")


def run_node(source):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    with tempfile.NamedTemporaryFile(
        suffix=".js", mode="w", delete=False, encoding="utf-8"
    ) as script_file:
        script_file.write(source)
        script_path = script_file.name
    try:
        result = subprocess.run(
            [node, script_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        os.remove(script_path)
    assert result.returncode == 0, result.stderr


def release_management_state(**overrides):
    state = {
        "channel": "release",
        "status": "available",
        "updateable": True,
        "installed_version": "1.4.0",
        "installed_revision": 4,
        "available_version": "2.0.0",
        "available_revision": 5,
        "verification_status": "verified",
        "verification_message": "Artifact and tree digests verified.",
        "migration_status": "not_applicable",
        "migration_message": "",
        "rollback_available": True,
        "rollback_channel": "release",
        "rollback_version": "1.3.0",
        "rollback_revision": 3,
        "restart_pending": False,
        "git_supported": True,
        "release_available": True,
        "migration_action_state": "blocked",
    }
    state.update(overrides)
    return state


def legacy_plugin_data():
    return {
        PLUGIN_KEY: [
            "owner",
            "example-plugin",
            "Example plugin",
            "main",
            "2026-07-18T07:00:00Z",
        ],
        "LegacyPlugin": [
            "owner",
            "legacy-plugin",
            "Legacy plugin",
            "master",
        ],
    }


def configure_api_plugin(plugin_core_module, tmp_path):
    plugins_dir, _ = configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = legacy_plugin_data()
    plugin.plugin_platforms = {
        PLUGIN_KEY: ["linux", "windows"],
        "LegacyPlugin": ["linux"],
    }
    return plugin, plugins_dir


def test_list_plugins_adds_release_management_map_without_changing_legacy_data(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin, plugins_dir = configure_api_plugin(plugin_core_module, tmp_path)
    original_data = json.loads(json.dumps(plugin.plugin_data))
    installed = ["00-PyPluginStore", PLUGIN_KEY, "LegacyPlugin"]
    statuses = {
        "00-PyPluginStore": "unknown",
        PLUGIN_KEY: "available",
        "LegacyPlugin": "current",
    }
    versions = {
        PLUGIN_KEY: {"installed": "1.4.0", "available": "2.0.0"}
    }
    expected_management = {
        PLUGIN_KEY: release_management_state(),
        "LegacyPlugin": release_management_state(
            channel="git",
            status="git_current",
            updateable=True,
            installed_version="",
            installed_revision=None,
            available_version="",
            available_revision=None,
            verification_status="not_applicable",
            verification_message="",
            migration_status="not_available",
            rollback_available=False,
            rollback_channel="",
            rollback_version="",
            rollback_revision=None,
            release_available=False,
        ),
    }
    calls = []
    responses = []

    monkeypatch.setattr(plugin, "getInstalledPlugins", lambda path: installed)
    monkeypatch.setattr(
        plugin, "getCachedUpdateStatuses", lambda values: statuses
    )
    monkeypatch.setattr(
        plugin,
        "get_plugin_versions",
        lambda values, current_statuses, path: versions,
    )

    def management_map(values, current_statuses, current_versions, path):
        calls.append(
            (
                list(values),
                dict(current_statuses),
                dict(current_versions),
                Path(path),
            )
        )
        return expected_management

    monkeypatch.setattr(
        plugin,
        "getPluginManagementMap",
        management_map,
        raising=False,
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "list_plugins"})

    response = responses[0]
    assert response["status"] == "success"
    assert response["action"] == "list_plugins"
    assert response["management"] == expected_management
    assert response["data"] == original_data
    assert response["versions"] == versions
    assert response["update_status"] == statuses
    assert response["installation_conflicts"] == {}
    assert response["installed_scan_error"] == ""
    assert plugin.plugin_data == original_data
    assert isinstance(response["data"][PLUGIN_KEY], list)
    assert isinstance(response["data"]["LegacyPlugin"], list)
    assert calls == [(installed, statuses, versions, plugins_dir)]


def test_refresh_status_rebuilds_management_map_after_release_status_refresh(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin, plugins_dir = configure_api_plugin(plugin_core_module, tmp_path)
    installed = ["00-PyPluginStore", PLUGIN_KEY]
    refreshed_status = {
        "00-PyPluginStore": "unknown",
        PLUGIN_KEY: "available",
    }
    versions = {
        PLUGIN_KEY: {"installed": "1.4.0", "available": "2.0.0"}
    }
    state = release_management_state()
    calls = []
    responses = []

    monkeypatch.setattr(plugin, "fetch_registry", lambda: calls.append("fetch"))
    monkeypatch.setattr(plugin, "getInstalledPlugins", lambda path: installed)

    def refresh(values, path):
        calls.append("refresh")
        assert list(values) == installed
        assert Path(path) == plugins_dir
        return refreshed_status

    monkeypatch.setattr(plugin, "refreshInstalledUpdateStatuses", refresh)
    monkeypatch.setattr(
        plugin,
        "get_plugin_versions",
        lambda values, current_statuses, path: versions,
    )

    def management_map(values, current_statuses, current_versions, path):
        calls.append("management")
        assert current_statuses == refreshed_status
        assert current_versions == versions
        return {PLUGIN_KEY: state}

    monkeypatch.setattr(
        plugin,
        "getPluginManagementMap",
        management_map,
        raising=False,
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "refresh_update_status"})

    assert calls == ["fetch", "refresh", "management"]
    assert responses[0]["management"] == {PLUGIN_KEY: state}
    assert responses[0]["update_status"][PLUGIN_KEY] == "available"
    assert responses[0]["installation_conflicts"] == {}
    assert responses[0]["installed_scan_error"] == ""


def test_release_update_action_does_not_require_dot_git(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin, plugins_dir = configure_api_plugin(plugin_core_module, tmp_path)
    release_dir = plugins_dir / PLUGIN_KEY
    release_dir.mkdir()
    (release_dir / "plugin.py").write_text(
        "# release plugin\n",
        encoding="utf-8",
    )
    plugin.install_metadata_service.write(
        release_dir,
        plugin_core_module.InstallMetadata(
            schema=2,
            plugin_key=PLUGIN_KEY,
            management_mode="release",
            repository_identity="github.com/owner/example-plugin",
            version="2.0.0",
            tag="v2.0.0",
            release_id="github:owner/example-plugin:v2.0.0",
            release_revision=2,
            released_at="2026-07-18T07:00:00Z",
            commit="1" * 40,
            artifact_sha256="2" * 64,
            artifact_tree_sha256="3" * 64,
            artifact_provenance="forge_source_archive",
            artifact_files={
                "plugin.py": {
                    "sha256": "4" * 64,
                    "size": 17,
                },
            },
            preserved_files={},
            index_sequence=2,
            installed_at="2026-07-18T08:00:00Z",
        ),
    )
    assert not (release_dir / ".git").exists()
    calls = []
    responses = []

    class ReleaseCoordinator:
        def update(
            self,
            entry,
            queue_on_lock=True,
            trigger="manual",
        ):
            calls.append((entry.key, queue_on_lock, trigger))
            return True, "Release v2.0.0 staged; restart required."

    plugin.install_update_strategy = ReleaseCoordinator()
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand({"action": "update", "plugin_key": PLUGIN_KEY})

    assert calls == [(PLUGIN_KEY, True, "manual")]
    assert responses[0]["status"] == "success"
    assert responses[0]["plugin_key"] == PLUGIN_KEY
    assert "restart required" in responses[0]["message"].lower()


@pytest.mark.parametrize(
    ("action", "challenge_kind"),
    [
        ("use_release", "channel_switch"),
        ("rollback", "rollback"),
    ],
)
def test_release_management_actions_return_opaque_confirmation_challenge(
    plugin_core_module, tmp_path, monkeypatch, action, challenge_kind
):
    plugin, _ = configure_api_plugin(plugin_core_module, tmp_path)
    calls = []
    responses = []
    token = "challenge-token-" + action

    def execute(
        *,
        action,
        plugin_key,
        confirmation_token,
        trigger,
    ):
        calls.append((action, plugin_key, confirmation_token, trigger))
        return {
            "status": "confirmation_required",
            "challenge": {
                "kind": challenge_kind,
                "token": token,
                "message": "Confirm " + action.replace("_", " ") + ".",
            },
        }

    monkeypatch.setattr(
        plugin,
        "executeReleaseManagementAction",
        execute,
        raising=False,
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand(
        {
            "action": action,
            "plugin_key": PLUGIN_KEY,
            "confirmed": True,
        }
    )

    response = responses[0]
    assert calls == [(action, PLUGIN_KEY, "", "manual")]
    assert response["status"] == "confirmation_required"
    assert response["action"] == action
    assert response["plugin_key"] == PLUGIN_KEY
    assert response["challenge"]["kind"] == challenge_kind
    assert response["challenge"]["token"] == token
    assert response["challenge"]["message"]
    assert "inventory" not in response["challenge"]
    assert "paths" not in response["challenge"]
    assert "confirmed" not in response


@pytest.mark.parametrize("action", ["use_release", "rollback"])
def test_release_management_confirmation_passes_only_opaque_token(
    plugin_core_module, tmp_path, monkeypatch, action
):
    plugin, _ = configure_api_plugin(plugin_core_module, tmp_path)
    calls = []
    responses = []

    def execute(
        *,
        action,
        plugin_key,
        confirmation_token,
        trigger,
    ):
        calls.append((action, plugin_key, confirmation_token, trigger))
        return {
            "status": "success",
            "message": "Operation staged; restart required.",
            "restart_pending": True,
        }

    monkeypatch.setattr(
        plugin,
        "executeReleaseManagementAction",
        execute,
        raising=False,
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand(
        {
            "action": action,
            "plugin_key": PLUGIN_KEY,
            "confirmation_token": "opaque-token-123",
        }
    )

    assert calls == [(action, PLUGIN_KEY, "opaque-token-123", "manual")]
    assert responses[0] == {
        "status": "success",
        "message": "Operation staged; restart required.",
        "restart_pending": True,
        "action": action,
        "plugin_key": PLUGIN_KEY,
    }


def test_use_git_api_action_is_rejected_with_local_override_guidance(
    plugin_core_module, tmp_path, monkeypatch
):
    plugin, _ = configure_api_plugin(plugin_core_module, tmp_path)
    responses = []
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand(
        {"action": "use_git", "plugin_key": PLUGIN_KEY}
    )

    assert responses == [
        {
            "status": "error",
            "action": "use_git",
            "message": (
                "Switch to Git is no longer available. Add a Local "
                "registry override to use Git management."
            ),
        }
    ]


def test_confirmation_request_round_trip_remains_below_2000_byte_api_bound(
    plugin_core_module, monkeypatch
):
    payload = {
        "action": "use_release",
        "tx_id": "t" * 50,
        "plugin_key": "P" * 128,
        "confirmation_token": "c" * 512,
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    assert len(encoded) < plugin_core_module.API_PAYLOAD_MAX_LENGTH
    assert plugin_core_module.API_PAYLOAD_MAX_LENGTH == 2000
    plugin = plugin_core_module.BasePlugin()
    handled = []
    plugin_core_module.Devices = {1: FakeTextDevice(encoded)}
    monkeypatch.setattr(plugin, "handleApiCommand", handled.append)

    plugin.onCommand(2, "On", 0, 0)

    assert handled == [payload]
    assert plugin_core_module.Devices[1].sValue == ""
    debug_messages = "\n".join(
        str(arguments)
        for arguments, _keywords in plugin_core_module.Domoticz.calls["Debug"]
    )
    assert payload["confirmation_token"] not in debug_messages


def test_confirmation_request_over_2000_bytes_is_rejected_before_dispatch(
    plugin_core_module, monkeypatch
):
    payload = {
        "action": "use_release",
        "tx_id": "t" * 50,
        "plugin_key": PLUGIN_KEY,
        "confirmation_token": "c" * 2000,
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    assert len(encoded) > plugin_core_module.API_PAYLOAD_MAX_LENGTH
    plugin = plugin_core_module.BasePlugin()
    handled = []
    plugin_core_module.Devices = {1: FakeTextDevice(encoded)}
    monkeypatch.setattr(plugin, "handleApiCommand", handled.append)

    plugin.onCommand(2, "On", 0, 0)

    assert handled == []
    assert plugin_core_module.Devices[1].sValue == ""
    assert any(
        "API Payload exceeds length limit." in arguments[0]
        for arguments, _ in plugin_core_module.Domoticz.calls["Error"]
    )


def test_confirmation_challenge_response_is_not_replayed_as_a_request(
    plugin_core_module, monkeypatch
):
    challenge = {
        "status": "confirmation_required",
        "action": "use_release",
        "tx_id": "transaction-123",
        "plugin_key": PLUGIN_KEY,
        "challenge": {
            "kind": "channel_switch",
            "token": "opaque-token-123",
            "message": "Switch this plugin to the Release channel?",
        },
    }
    plugin = plugin_core_module.BasePlugin()
    handled = []
    plugin_core_module.Devices = {1: FakeTextDevice(json.dumps(challenge))}
    monkeypatch.setattr(plugin, "handleApiCommand", handled.append)

    assert plugin.isApiResponsePayload(challenge) is True
    plugin.onCommand(2, "On", 0, 0)

    assert handled == []
    assert plugin_core_module.Devices[1].sValue == ""


def test_ui_load_and_refresh_treat_management_map_as_optional_extension():
    script = load_inline_script()
    load_plugins = extract_js_function(script, "loadPlugins")
    apply_loaded = extract_js_function(script, "applyLoadedPlugins")
    refresh = extract_js_function(script, "refreshUpdateStatus")
    filter_plugins = extract_js_function(script, "filterAndRender")

    assert "applyLoadedPlugins(response)" in load_plugins
    assert "managementCache = response.management || {};" in apply_loaded
    assert (
        "managementCache = response.management || managementCache;" in refresh
    )
    assert "pluginCache = response.data" in apply_loaded
    assert "updateStatusCache = response.update_status || {}" in apply_loaded
    assert "versionsCache = response.versions || {}" in apply_loaded
    assert (
        "installationConflictCache = response.installation_conflicts || {};"
        in apply_loaded
    )
    assert "installedScanError = String(response.installed_scan_error || '')" in apply_loaded
    assert "response.installation_conflicts || installationConflictCache" in refresh
    assert "installed plugin scan warning" not in load_plugins.lower()
    assert 'setManagerActivity("Loading plugins...")' in load_plugins
    assert "clearManagerActivity()" in load_plugins
    assert "installedCache.forEach" in filter_plugins
    assert "matchDetails.management_error" in filter_plugins
    assert "not present in the current registry" in filter_plugins


def test_ui_hides_physical_aliases_and_bundled_plugin_folders():
    script = load_inline_script()
    filter_plugins = extract_js_function(script, "filterAndRender")

    run_node(
        filter_plugins
        + """
const pluginCache = {
    Somfy: ['MadPatrick', 'domoticz_somfy', 'Somfy plugin', 'master', '']
};
const installedCache = [
    'Somfy',
    'domoticz_somfy',
    'examples',
    'AwoxSMP'
];
const installedMatchDetailsCache = {
    Somfy: {
        folder: 'domoticz_somfy',
        source: 'release install metadata',
        management_mode: 'release',
        is_git: false
    },
    domoticz_somfy: {
        folder: 'domoticz_somfy',
        source: 'local folder alias',
        management_mode: 'release',
        management_error: 'Copied canonical release diagnostic.',
        is_git: false
    },
    examples: {
        folder: 'examples',
        source: 'local folder',
        is_git: false
    },
    AwoxSMP: {
        folder: 'AwoxSMP',
        source: 'local folder',
        is_git: false
    }
};
const document = {
    getElementById(id) {
        if (id === 'search-box') return {value: ''};
        if (id === 'installed-toggle') return {checked: installedOnly};
        throw new Error('unexpected element: ' + id);
    }
};
function formatPluginDisplayName(key) {
    return key;
}
let renderedKeys = [];
function renderPlugins(data) {
    renderedKeys = Object.keys(data);
}

let installedOnly = false;
filterAndRender();

if (JSON.stringify(renderedKeys) !== JSON.stringify(['Somfy'])) {
    throw new Error('unexpected rendered plugins: ' + renderedKeys.join(','));
}

installedOnly = true;
filterAndRender();

if (JSON.stringify(renderedKeys) !== JSON.stringify(['Somfy'])) {
    throw new Error(
        'unexpected installed-only plugins: ' + renderedKeys.join(',')
    );
}
"""
    )


def test_ui_keeps_actionable_orphan_release_diagnostics_visible():
    script = load_inline_script()
    filter_plugins = extract_js_function(script, "filterAndRender")

    run_node(
        filter_plugins
        + """
const pluginCache = {
    Somfy: ['MadPatrick', 'domoticz_somfy', 'Somfy plugin', 'master', '']
};
const installedCache = ['Somfy', 'orphan-release-folder'];
const installedMatchDetailsCache = {
    Somfy: {
        folder: 'domoticz_somfy',
        source: 'release install metadata',
        management_mode: 'release',
        is_git: false
    },
    'orphan-release-folder': {
        folder: 'orphan-release-folder',
        source: 'orphan release install metadata',
        management_mode: 'release',
        management_error: 'Restore the registry entry before changing this plugin.',
        is_git: false
    }
};
const document = {
    getElementById(id) {
        if (id === 'search-box') return {value: ''};
        if (id === 'installed-toggle') return {checked: false};
        throw new Error('unexpected element: ' + id);
    }
};
function formatPluginDisplayName(key) {
    return key;
}
let renderedData = {};
function renderPlugins(data) {
    renderedData = data;
}

filterAndRender();

const renderedKeys = Object.keys(renderedData);
if (
    JSON.stringify(renderedKeys)
    !== JSON.stringify(['Somfy', 'orphan-release-folder'])
) {
    throw new Error('unexpected rendered plugins: ' + renderedKeys.join(','));
}
if (
    renderedData['orphan-release-folder'][2]
    !== 'Installed plugin is not present in the current registry.'
) {
    throw new Error('orphan diagnostic description is missing');
}
"""
    )


def test_ui_has_release_and_git_channel_badges_with_safe_text_rendering():
    html = (REPO_ROOT / "pypluginstore.html").read_text(encoding="utf-8")
    script = load_inline_script()
    render_plugins = extract_js_function(script, "renderPlugins")

    assert ".channel-badge" in html
    assert ".channel-badge-release" in html
    assert ".channel-badge-git" in html
    assert ".release-status-detail" in html
    assert ".release-error-detail" in html
    assert "releaseChannelLabel" in script
    assert "channel-badge channel-badge-" in script
    assert "formatReleaseManagementStatus" in script
    assert ".textContent = managementText" in script
    assert "innerHTML = formatReleaseManagementStatus" not in script
    assert "const managementText = formatReleaseManagementStatus(management);" in render_plugins
    assert "if (managementText)" in render_plugins
    assert "installationConflict.management_error" in render_plugins
    assert "installButton.disabled = true" in render_plugins
    assert "removeButton.disabled = true" in render_plugins
    assert "!hasRegistryEntry" in render_plugins
    assert "matchDetails.management_error" in render_plugins
    assert "useReleaseButton.disabled = true" in render_plugins
    assert "rollbackButton.disabled = true" in render_plugins


def test_release_channel_labels_are_explicit():
    script = load_inline_script()
    function_source = extract_js_function(script, "releaseChannelLabel")
    run_node(
        function_source
        + """
if (releaseChannelLabel({channel: 'release'}) !== 'Release') {
    throw new Error('release label missing');
}
if (releaseChannelLabel({channel: 'git'}) !== 'Git') {
    throw new Error('git label missing');
}
if (releaseChannelLabel({}) !== '') {
    throw new Error('legacy state must not invent a channel');
}
"""
    )


def test_release_status_text_surfaces_versions_migration_and_restart():
    script = load_inline_script()
    function_source = extract_js_function(
        script, "formatReleaseManagementStatus"
    )
    cases = [
        {
            "state": release_management_state(),
            "fragments": ["v2.0.0", "available"],
        },
        {
            "state": release_management_state(
                verification_status="verified_on_host",
                verification_message=(
                    "Verified directly from the release provider."
                ),
            ),
            "fragments": [
                "v2.0.0",
                "available",
            ],
        },
        {
            "state": release_management_state(
                status="verification_failed",
                verification_status="failed",
                verification_message="Artifact SHA-256 mismatch.",
                updateable=False,
            ),
            "fragments": ["Verification failed", "SHA-256 mismatch"],
        },
        {
            "state": release_management_state(
                channel="git",
                status="migration_blocked_local_changes",
                migration_status="migration_blocked_local_changes",
                migration_message="Local changes must be reviewed.",
            ),
            "fragments": ["Cannot use Release channel", "Local changes"],
        },
        {
            "state": release_management_state(
                channel="git",
                status="migration_confirmation_required",
                migration_status="migration_confirmation_required",
                migration_message=(
                    "The installed Git checkout contains commits newer than "
                    "the available Release."
                ),
                migration_action_state="confirmation_required",
                updateable=False,
            ),
            "fragments": [
                (
                    "Use Release channel v2.0.0 instead of Git commits; "
                    "confirmation required"
                ),
                "contains commits newer than the available Release",
            ],
        },
        {
            "state": release_management_state(
                channel="git",
                status="migration_available",
                migration_status="migration_available",
                migration_action_state="available",
            ),
            "fragments": [
                "Use Release channel v2.0.0 instead of Git commits",
            ],
        },
        {
            "state": release_management_state(
                status="release_metadata_unavailable",
                verification_status="unavailable",
                verification_message="Accepted release metadata expired.",
                updateable=False,
            ),
            "fragments": ["Release metadata unavailable", "expired"],
        },
        {
            "state": release_management_state(
                status="local_override_requires_git_checkout",
                updateable=False,
            ),
            "fragments": ["Restore the previous Git version or reinstall"],
        },
        {
            "state": release_management_state(restart_pending=True),
            "fragments": ["Restart required"],
        },
        {
            "state": release_management_state(
                channel="git", status="git_available"
            ),
            "fragments": ["Git · Update available"],
        },
        {
            "state": release_management_state(
                status="rollback_available",
            ),
            "fragments": ["v1.3.0 can be restored"],
        },
        {
            "state": release_management_state(
                status="rollback_available",
                rollback_version="",
                rollback_revision=99,
            ),
            "fragments": ["Previous Release version can be restored"],
        },
    ]
    run_node(
        function_source
        + "\nconst cases = "
        + json.dumps(cases)
        + ";\n"
        + """
for (const item of cases) {
    const text = formatReleaseManagementStatus(item.state);
    if (text.toLowerCase().includes('revision')) {
        throw new Error(`internal release revision leaked into "${text}"`);
    }
    if (text.toLowerCase().includes('verified')) {
        throw new Error(`verification origin leaked into "${text}"`);
    }
    for (const fragment of item.fragments) {
        if (!text.includes(fragment)) {
            throw new Error(`missing "${fragment}" in "${text}"`);
        }
    }
}
"""
    )


def test_git_status_text_is_hidden_unless_an_update_is_available():
    script = load_inline_script()
    function_source = extract_js_function(
        script, "formatReleaseManagementStatus"
    )
    run_node(
        function_source
        + """
for (const status of ['git_current', 'git_unknown']) {
    const text = formatReleaseManagementStatus({channel: 'git', status});
    if (text !== '') {
        throw new Error(`${status} unexpectedly rendered as "${text}"`);
    }
}
const available = formatReleaseManagementStatus({
    channel: 'git',
    status: 'git_available'
});
if (available !== 'Git · Update available') {
    throw new Error(`available Git update rendered as "${available}"`);
}
const expectedFallback = formatReleaseManagementStatus({
    channel: 'git',
    status: 'git_unknown',
    summary: 'Git - update status unknown',
    verification_message: 'release_entry_missing'
});
if (expectedFallback !== 'Git - update status unknown' ||
    expectedFallback.includes('release_entry_missing')) {
    throw new Error(`Git fallback leaked internal state as "${expectedFallback}"`);
}
"""
    )


def test_current_release_status_text_is_hidden():
    script = load_inline_script()
    function_source = extract_js_function(
        script, "formatReleaseManagementStatus"
    )
    run_node(
        function_source
        + """
const text = formatReleaseManagementStatus({
    channel: 'release',
    status: 'current',
    installed_version: '1.0.0',
    available_version: '1.0.0',
    verification_status: 'verified'
});
if (text !== '') {
    throw new Error(`current Release unexpectedly rendered as "${text}"`);
}
"""
    )


def test_release_update_presentation_uses_channel_aware_status():
    script = load_inline_script()
    function_source = extract_js_function(
        script, "resolveUpdatePresentationStatus"
    )
    cases = [
        {
            "legacy": "unknown",
            "management": {"channel": "release", "status": "available"},
            "expected": "available",
        },
        {
            "legacy": "available",
            "management": {"channel": "release", "status": "current"},
            "expected": "current",
        },
        {
            "legacy": "unknown",
            "management": {"channel": "git", "status": "git_available"},
            "expected": "available",
        },
        {
            "legacy": "available",
            "management": {"channel": "git", "status": "git_current"},
            "expected": "current",
        },
        {
            "legacy": "available",
            "management": {"channel": "git", "status": "git_unknown"},
            "expected": "unknown",
        },
        {
            "legacy": "available",
            "management": None,
            "expected": "available",
        },
    ]
    run_node(
        function_source
        + "\nconst cases = "
        + json.dumps(cases)
        + ";\n"
        + """
for (const item of cases) {
    const actual = resolveUpdatePresentationStatus(
        item.legacy,
        item.management
    );
    if (actual !== item.expected) {
        throw new Error(
            `expected ${item.expected}, got ${actual}`
        );
    }
}
"""
    )

    render_plugins = extract_js_function(script, "renderPlugins")
    assert (
        "resolveUpdatePresentationStatus(\n"
        "                updateStatus,\n"
        "                management\n"
        "            )"
    ) in render_plugins
    assert (
        "presentationUpdateStatus === 'available' ? "
        "'btn-update btn-update-available'"
    ) in render_plugins
    assert (
        "presentationUpdateStatus === 'available' ? 'Update available'"
        in render_plugins
    )
    assert "&& (!management || management.channel === 'git')" in render_plugins


def test_release_action_model_keeps_release_non_git_install_updateable():
    script = load_inline_script()
    function_source = extract_js_function(script, "releaseManagementActions")
    cases = [
        {
            "state": release_management_state(),
            "context": {"installed": True, "isGit": False, "isManager": False},
            "expected": ["rollback", "update"],
        },
        {
            "state": release_management_state(
                channel="git",
                status="migration_available",
                rollback_available=False,
                migration_action_state="available",
            ),
            "context": {"installed": True, "isGit": True, "isManager": False},
            "expected": ["use_release"],
        },
        {
            "state": release_management_state(
                channel="git",
                status="migration_confirmation_required",
                updateable=False,
                rollback_available=False,
                migration_action_state="confirmation_required",
            ),
            "context": {"installed": True, "isGit": True, "isManager": False},
            "expected": ["use_release"],
        },
        {
            "state": release_management_state(
                channel="git",
                status="migration_waiting_for_release",
                updateable=False,
                rollback_available=False,
                migration_action_state="blocked",
            ),
            "context": {"installed": True, "isGit": True, "isManager": False},
            "expected": [],
        },
        {
            "state": release_management_state(
                channel="git",
                status="git_available",
                updateable=True,
                rollback_available=False,
                migration_action_state="available",
            ),
            "context": {"installed": True, "isGit": True, "isManager": False},
            "expected": ["update", "use_release"],
        },
        {
            "state": release_management_state(
                channel="git",
                status="git_available",
                updateable=True,
                rollback_available=False,
                migration_action_state="available",
            ),
            "context": {
                "installed": True,
                "isGit": True,
                "isLocal": True,
                "isManager": False,
            },
            "expected": ["update"],
        },
        {
            "state": release_management_state(restart_pending=True),
            "context": {"installed": True, "isGit": False, "isManager": False},
            "expected": ["rollback"],
        },
        {
            "state": release_management_state(
                status="local_override_requires_git_checkout",
                updateable=False,
                rollback_available=False,
            ),
            "context": {"installed": True, "isGit": False, "isManager": False},
            "expected": [],
        },
        {
            "state": None,
            "context": {"installed": True, "isGit": True, "isManager": False},
            "expected": ["update"],
        },
        {
            "state": None,
            "context": {"installed": True, "isGit": False, "isManager": False},
            "expected": [],
        },
    ]
    run_node(
        function_source
        + "\nconst cases = "
        + json.dumps(cases)
        + ";\n"
        + """
for (const item of cases) {
    const actual = releaseManagementActions(item.state, item.context).sort();
    const expected = item.expected.slice().sort();
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    }
}
"""
    )


def test_release_action_model_keeps_git_self_update_available_and_git_only():
    script = load_inline_script()
    function_source = extract_js_function(script, "releaseManagementActions")
    run_node(
        function_source
        + """
const gitActions = releaseManagementActions(null, {
    installed: true,
    isGit: true,
    isManager: true,
});
if (JSON.stringify(gitActions) !== JSON.stringify(['update'])) {
    throw new Error(`expected Git self-update action, got ${JSON.stringify(gitActions)}`);
}
const releaseActions = releaseManagementActions({
    channel: 'release',
    updateable: true,
    rollback_available: true,
    git_supported: true,
}, {
    installed: true,
    isGit: false,
    isManager: true,
});
if (JSON.stringify(releaseActions) !== JSON.stringify([])) {
    throw new Error(`expected no manager Release actions, got ${JSON.stringify(releaseActions)}`);
}
"""
    )


def test_release_action_labels_come_from_backend_descriptors():
    script = load_inline_script()
    function_source = extract_js_function(
        script,
        "releaseManagementActionLabel",
    )
    run_node(
        function_source
        + """
const state = {
    actions: [
        {
            id: 'rollback',
            label: 'Restore Git',
            enabled: true,
        },
    ],
};
if (
    releaseManagementActionLabel(
        state,
        'rollback',
        'Rollback'
    )
    !== 'Restore Git'
) {
    throw new Error('backend restore label was ignored');
}
if (releaseManagementActionLabel(state, 'update', 'Update') !== 'Update') {
    throw new Error('missing descriptor did not use the safe fallback');
}
if (
    releaseManagementActionLabel(
        {actions: [{id: 'rollback', label: '   '}]},
        'rollback',
        'Rollback'
    ) !== 'Rollback'
) {
    throw new Error('blank descriptor label did not use the safe fallback');
}
"""
    )


def test_ui_renders_explicit_channel_and_rollback_actions():
    html = (REPO_ROOT / "pypluginstore.html").read_text(encoding="utf-8")
    script = load_inline_script()

    assert ".btn-channel" in html
    assert ".btn-rollback" in html
    assert "releaseManagementActions(management" in script
    assert "isLocal: isLocal" in script
    assert "data-action" in script
    assert "use_git" not in script
    assert "Use Git" not in script
    assert "use_release" in script
    assert "Use Release channel" in script
    assert "Use the Release channel instead of Git commits" in script
    assert "rollback" in script
    assert "releaseManagementActionLabel" in script
    assert "rollbackButton.textContent = releaseManagementActionLabel" in script
    assert "handleReleaseManagementAction" in script


def test_ui_uses_a_friendly_name_for_release_channel_progress():
    script = load_inline_script()
    label_source = extract_js_function(script, "actionDisplayName")
    handle_action_source = extract_js_function(script, "handleAction")

    run_node(
        label_source
        + """
if (actionDisplayName('use_release') !== 'Release channel selection') {
    throw new Error('release channel action leaked its internal name');
}
if (actionDisplayName('refresh_update_status') !== 'refresh update status') {
    throw new Error('fallback action label was not humanized');
}
"""
    )
    assert "actionDisplayName(action)" in handle_action_source
    assert "Executing ${actionName}" in handle_action_source
    assert "Error during ${actionName}" in handle_action_source


def test_confirmation_payload_contains_only_key_and_bounded_opaque_token():
    script = load_inline_script()
    function_source = extract_js_function(
        script, "buildReleaseConfirmationPayload"
    )
    run_node(
        function_source
        + """
const payload = buildReleaseConfirmationPayload(
    'use_release',
    'ExamplePlugin',
    'opaque-token-123'
);
const expected = {
    plugin_key: 'ExamplePlugin',
    confirmation_token: 'opaque-token-123'
};
if (JSON.stringify(payload) !== JSON.stringify(expected)) {
    throw new Error(`unexpected confirmation payload: ${JSON.stringify(payload)}`);
}
const request = JSON.stringify({
    action: 'use_release',
    tx_id: 'x'.repeat(50),
    ...payload
});
if (request.length >= 2000) {
    throw new Error('normal confirmation payload exceeds API bound');
}
let rejected = false;
try {
    buildReleaseConfirmationPayload('use_release', 'ExamplePlugin', 'x'.repeat(2000));
} catch (error) {
    rejected = true;
}
if (!rejected) {
    throw new Error('oversized confirmation token was accepted');
}
rejected = false;
try {
    buildReleaseConfirmationPayload('use_git', 'ExamplePlugin', 'opaque-token-123');
} catch (error) {
    rejected = true;
}
if (!rejected) {
    throw new Error('removed use_git action was accepted');
}
"""
    )


def test_ui_replays_backend_challenge_with_token_not_boolean_confirmation():
    script = load_inline_script()
    function_source = extract_js_function(
        script, "handleReleaseManagementAction"
    )

    assert "response.status === 'confirmation_required'" in function_source
    assert "response.challenge" in function_source
    assert "response.challenge.message" in function_source
    assert "response.challenge.token" in function_source
    assert "buildReleaseConfirmationPayload" in function_source
    assert "confirmation_token" in script
    assert "confirmed: true" not in function_source


def test_ui_does_not_disable_release_updates_only_because_git_folder_is_absent():
    script = load_inline_script()
    render_plugins = extract_js_function(script, "renderPlugins")

    assert "managementCache[key]" in render_plugins
    assert "management.channel === 'release'" in render_plugins
    assert "management.updateable" in render_plugins
    assert "Cannot update non-Git plugins" in render_plugins
