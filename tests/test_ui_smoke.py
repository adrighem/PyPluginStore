from html.parser import HTMLParser
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from conftest import REPO_ROOT


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


def test_pypluginstore_javascript_has_valid_syntax():
    script = load_inline_script()
    mocks = """
    const noop = () => {};
    const document = {
        readyState: 'complete',
        getElementById: () => ({ addEventListener: noop, onclick: null }),
        addEventListener: noop
    };
    const window = {
        document,
        addEventListener: noop
    };
    const alert = noop;
    const location = {};
    const fetch = noop;
    const setTimeout = noop;
    """

    result = run_node_script(mocks + script, check_syntax=True)
    assert result.returncode == 0, result.stderr


def test_plugin_display_name_strips_domoticz_affixes():
    function_source = extract_js_function(load_inline_script(), "formatPluginDisplayName")
    cases = {
        "Domoticz-AWTRIX3-Plugin": "AWTRIX3-Plugin",
        "domoticz-for-HomeWizard": "HomeWizard",
        "Domoticz for Solar": "Solar",
        "domoticz_plugin_HomeWizard": "HomeWizard",
        "domoticz plugin Solar": "Solar",
        "Broadlink-Domoticz-plugin": "Broadlink",
        "Pollen-forecast-in-Norway-for-Domoticz": "Pollen-forecast-in-Norway",
        "Forecast_for_domoticz": "Forecast",
        "DomoticzTile": "DomoticzTile",
        "PluginDomoticzFreebox": "PluginDomoticzFreebox",
    }
    node_script = f"""
{function_source}
const cases = {json.dumps(cases)};
for (const [input, expected] of Object.entries(cases)) {{
    const actual = formatPluginDisplayName(input);
    if (actual !== expected) {{
        throw new Error(`${{input}}: expected "${{expected}}", got "${{actual}}"`);
    }}
}}
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_repo_url_builder_supports_codeberg_and_gitlab_hosts():
    script = load_inline_script()
    function_source = "\n".join([
        extract_js_function(script, "stripRepoUrl"),
        extract_js_function(script, "encodeRepoPath"),
        extract_js_function(script, "encodeBranchPath"),
        extract_js_function(script, "parseRepoReference"),
        extract_js_function(script, "buildRepoUrl"),
    ])
    cases = [
        ["owner", "repo", "https://github.com/owner/repo"],
        ["github.com/Hoog", "Domoticz-Stromer-plugin", "https://github.com/Hoog/Domoticz-Stromer-plugin"],
        ["codeberg.org/Hoog", "Domoticz-Stromer-plugin", "https://codeberg.org/Hoog/Domoticz-Stromer-plugin"],
        ["gitlab.com/r.boeters", "DomoticzSabNZBDPlugin", "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin"],
        ["example.org/Team", "DomoticzPlugin", "https://example.org/Team/DomoticzPlugin"],
        ["git@gitlab.com:r.boeters/DomoticzSabNZBDPlugin.git", "", "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin"],
        ["git@example.org:Team/DomoticzPlugin.git", "", "https://example.org/Team/DomoticzPlugin"],
        ["https://codeberg.org/Hoog/Domoticz-Stromer-plugin/src/branch/main", "", "https://codeberg.org/Hoog/Domoticz-Stromer-plugin"],
        ["https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/tree/master", "", "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin"],
        ["owner", "repo", "https://github.com/owner/repo/tree/feature/meters", "feature/meters"],
        ["codeberg.org/Hoog", "Domoticz-Stromer-plugin", "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/src/branch/main", "main"],
        ["gitlab.com/r.boeters", "DomoticzSabNZBDPlugin", "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/tree/release/2.0", "release/2.0"],
        ["example.org/Team", "DomoticzPlugin", "https://example.org/Team/DomoticzPlugin", "main"],
    ]
    node_script = f"""
{function_source}
const cases = {json.dumps(cases)};
for (const [author, repo, expected, branch] of cases) {{
    const actual = buildRepoUrl(author, repo, branch);
    if (actual !== expected) {{
        throw new Error(`${{author}}/${{repo}}: expected "${{expected}}", got "${{actual}}"`);
    }}
}}
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_version_status_uses_remote_label_when_installed_is_newer():
    script = load_inline_script()
    function_source = "\n".join([
        extract_js_function(script, "parseVersionParts"),
        extract_js_function(script, "compareVersions"),
        extract_js_function(script, "formatVersionStatus"),
    ])
    node_script = f"""
{function_source}
const olderRemote = formatVersionStatus({{installed: '2.0.5.5', available: '2.0.4'}}, 'available');
if (olderRemote !== 'Installed: v2.0.5.5 | Remote: v2.0.4 (installed is newer)') {{
    throw new Error(`Unexpected older remote status: ${{olderRemote}}`);
}}
const newerRemote = formatVersionStatus({{installed: '1.0.0', available: '2.0.0'}}, 'available');
if (newerRemote !== 'Installed: v1.0.0 | Available: v2.0.0') {{
    throw new Error(`Unexpected newer remote status: ${{newerRemote}}`);
}}
const sameRemote = formatVersionStatus({{installed: '2.24.4', available: '2.24.4'}}, 'available');
if (sameRemote !== 'Installed: v2.24.4 | Available: v2.24.4') {{
    throw new Error(`Unexpected equal remote status: ${{sameRemote}}`);
}}
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_author_display_includes_repository_host_for_all_hosted_entries():
    script = load_inline_script()
    function_source = "\n".join([
        extract_js_function(script, "stripRepoUrl"),
        extract_js_function(script, "encodeRepoPath"),
        extract_js_function(script, "parseRepoReference"),
        extract_js_function(script, "formatAuthorDisplay"),
    ])
    cases = [
        ["Hoog", "Domoticz-Stromer-plugin", "github.com/Hoog"],
        ["github.com/Hoog", "Domoticz-Stromer-plugin", "github.com/Hoog"],
        ["codeberg.org/Hoog", "Domoticz-Stromer-plugin", "codeberg.org/Hoog"],
        ["gitlab.com/r.boeters", "DomoticzSabNZBDPlugin", "gitlab.com/r.boeters"],
        ["https://codeberg.org/Hoog/Domoticz-Stromer-plugin/src/branch/main", "", "codeberg.org/Hoog"],
        ["git@gitlab.com:r.boeters/DomoticzSabNZBDPlugin.git", "", "gitlab.com/r.boeters"],
    ]
    node_script = f"""
{function_source}
const cases = {json.dumps(cases)};
for (const [author, repo, expected] of cases) {{
    const actual = formatAuthorDisplay(author, repo);
    if (actual !== expected) {{
        throw new Error(`${{author}}/${{repo}}: expected "${{expected}}", got "${{actual}}"`);
    }}
}}
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_plugin_cards_use_formatted_author_display():
    script = load_inline_script()

    assert "'Author: ' + formatAuthorDisplay(author, repo)" in script
    assert "'Author: ' + author" not in script


def test_update_buttons_keep_shared_and_state_specific_classes():
    html = (REPO_ROOT / "pypluginstore.html").read_text()

    assert ".btn-update {" in html
    assert ".btn-update-available {" in html
    assert ".btn-update-current {" in html
    assert "btn-update btn-update-available" in html
    assert "btn-update btn-update-current" in html


def test_plugin_cards_render_repo_mismatch_warning():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    script = load_inline_script()

    assert ".repo-mismatch-badge" in html
    assert ".repo-mismatch-detail" in html
    assert "Repo mismatch" in script
    assert "Installed checkout: " in script
    assert "updateStatus === 'mismatch'" in script
    assert "Add a matching registry_local.json override before updating this checkout" in script


def test_refresh_status_button_is_wired_to_backend_command():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    script = load_inline_script()

    assert 'id="refresh-update-status"' in html
    assert "document.getElementById('refresh-update-status').onclick = refreshUpdateStatus" in script
    assert "sendCommand('refresh_update_status', {})" in script


def test_api_bridge_lookup_is_scoped_to_own_hardware():
    script = load_inline_script()
    find_devices = extract_js_function(script, "findApiBridgeDevices")

    assert "filter=light&used=all&displayhidden=1" in find_devices
    assert "used=all&displayhidden=1&hwidx=" in find_devices
    assert "filter=all" not in find_devices
    assert "getdevices&' + query" in script


def test_api_bridge_lookup_resolves_one_hardware_pair():
    script = load_inline_script()
    function_source = "\n".join([
        extract_js_function(script, "normalizeDomoticzId"),
        extract_js_function(script, "matchingApiBridgeDevices"),
        extract_js_function(script, "fetchWithTimeout"),
        extract_js_function(script, "fetchApiBridgeDevices"),
        extract_js_function(script, "findApiBridgeDevices"),
    ])
    node_script = """
const API_BRIDGE_REQUEST_TIMEOUT_MS = 5000;
let payloadIdx = null;
let triggerIdx = null;
const requests = [];
const responses = [
    {
        result: [
            {ID: 'OTHER_SWITCH', idx: '10', HardwareID: 2},
            {
                ID: 'PPM_API_TRIGGER',
                idx: '302',
                HardwareID: 8,
                HardwareDisabled: true,
            },
            {ID: 'PPM_API_TRIGGER', idx: '202', HardwareID: 7},
        ],
    },
    {
        result: [
            {ID: 'PPM_API_PAYLOAD', idx: '201', HardwareID: '7'},
            {ID: 'PPM_API_TRIGGER', idx: '202', HardwareID: '7'},
            {ID: 'PPM_API_PAYLOAD', idx: '301', HardwareID: '8'},
            {ID: 'PPM_API_TRIGGER', idx: '302', HardwareID: '8'},
        ],
    },
];

async function fetch(url) {
    requests.push(url);
    const response = responses.shift();
    if (!response) throw new Error('Unexpected bridge discovery request: ' + url);
    return {
        ok: true,
        status: 200,
        json: async () => response,
    };
}
""" + function_source + """

(async () => {
    await findApiBridgeDevices();

    if (payloadIdx !== '201' || triggerIdx !== '202') {
        throw new Error(
            'Unexpected bridge pair: ' + payloadIdx + '/' + triggerIdx
        );
    }
    if (requests.length !== 2) {
        throw new Error('Expected two scoped requests, got ' + requests.length);
    }
    const triggerLookup = new URL(requests[0], 'http://domoticz/');
    const pairLookup = new URL(requests[1], 'http://domoticz/');
    if (
        triggerLookup.searchParams.get('filter') !== 'light' ||
        triggerLookup.searchParams.get('used') !== 'all' ||
        triggerLookup.searchParams.get('displayhidden') !== '1' ||
        triggerLookup.searchParams.has('hwidx')
    ) {
        throw new Error('Trigger lookup was not light-scoped: ' + requests[0]);
    }
    if (
        pairLookup.searchParams.has('filter') ||
        pairLookup.searchParams.get('used') !== 'all' ||
        pairLookup.searchParams.get('displayhidden') !== '1' ||
        pairLookup.searchParams.get('hwidx') !== '7'
    ) {
        throw new Error('Pair lookup was not hardware-scoped: ' + requests[1]);
    }
    if (requests.some(url => url.includes('filter=all'))) {
        throw new Error('Discovery fell back to a broad device scan');
    }
})().catch(error => {
    console.error(error);
    process.exit(1);
});
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_api_bridge_lookup_rejects_ambiguous_or_mismatched_pairs():
    script = load_inline_script()
    function_source = "\n".join([
        extract_js_function(script, "normalizeDomoticzId"),
        extract_js_function(script, "matchingApiBridgeDevices"),
        extract_js_function(script, "fetchWithTimeout"),
        extract_js_function(script, "fetchApiBridgeDevices"),
        extract_js_function(script, "findApiBridgeDevices"),
    ])
    cases = [
        {
            "name": "missing manager trigger",
            "responses": [{"result": []}],
            "message": "API trigger not found",
            "request_count": 1,
        },
        {
            "name": "multiple manager triggers",
            "responses": [{
                "result": [
                    {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                    {"ID": "PPM_API_TRIGGER", "idx": "302", "HardwareID": 8},
                ],
            }],
            "message": "Multiple PyPluginStore API triggers found",
            "request_count": 1,
        },
        {
            "name": "invalid hardware id",
            "responses": [{
                "result": [{
                    "ID": "PPM_API_TRIGGER",
                    "idx": "202",
                    "HardwareID": "7&filter=all",
                }],
            }],
            "message": "no valid hardware ID",
            "request_count": 1,
        },
        {
            "name": "payload from another hardware",
            "responses": [
                {
                    "result": [
                        {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                    ],
                },
                {
                    "result": [
                        {"ID": "PPM_API_PAYLOAD", "idx": "301", "HardwareID": 8},
                        {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                    ],
                },
            ],
            "message": "found 0/1",
            "request_count": 2,
        },
        {
            "name": "duplicate payloads",
            "responses": [
                {
                    "result": [
                        {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                    ],
                },
                {
                    "result": [
                        {"ID": "PPM_API_PAYLOAD", "idx": "201", "HardwareID": 7},
                        {"ID": "PPM_API_PAYLOAD", "idx": "203", "HardwareID": 7},
                        {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                    ],
                },
            ],
            "message": "found 2/1",
            "request_count": 2,
        },
        {
            "name": "duplicate scoped triggers",
            "responses": [
                {
                    "result": [
                        {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                    ],
                },
                {
                    "result": [
                        {"ID": "PPM_API_PAYLOAD", "idx": "201", "HardwareID": 7},
                        {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                        {"ID": "PPM_API_TRIGGER", "idx": "204", "HardwareID": 7},
                    ],
                },
            ],
            "message": "found 1/2",
            "request_count": 2,
        },
        {
            "name": "trigger changes during discovery",
            "responses": [
                {
                    "result": [
                        {"ID": "PPM_API_TRIGGER", "idx": "202", "HardwareID": 7},
                    ],
                },
                {
                    "result": [
                        {"ID": "PPM_API_PAYLOAD", "idx": "201", "HardwareID": 7},
                        {"ID": "PPM_API_TRIGGER", "idx": "204", "HardwareID": 7},
                    ],
                },
            ],
            "message": "trigger changed during discovery",
            "request_count": 2,
        },
    ]
    node_script = """
const API_BRIDGE_REQUEST_TIMEOUT_MS = 5000;
let payloadIdx = null;
let triggerIdx = null;
let requests = [];
let responses = [];

async function fetch(url) {
    requests.push(url);
    const response = responses.shift();
    if (!response) throw new Error('Unexpected bridge discovery request: ' + url);
    return {
        ok: true,
        status: 200,
        json: async () => response,
    };
}
""" + function_source + "\nconst cases = " + json.dumps(cases) + ";\n" + """

(async () => {
    for (const testCase of cases) {
        payloadIdx = 'stale-payload';
        triggerIdx = 'stale-trigger';
        requests = [];
        responses = testCase.responses.slice();

        let failure = null;
        try {
            await findApiBridgeDevices();
        } catch (error) {
            failure = error;
        }

        if (!failure || !failure.message.includes(testCase.message)) {
            throw new Error(
                testCase.name + ': expected error containing "' +
                testCase.message + '", got "' +
                (failure ? failure.message : 'no error') + '"'
            );
        }
        if (requests.length !== testCase.request_count) {
            throw new Error(
                testCase.name + ': expected ' + testCase.request_count +
                ' request(s), got ' + requests.length
            );
        }
        if (requests.some(url => url.includes('filter=all'))) {
            throw new Error(testCase.name + ': used a broad device scan');
        }
        if (
            payloadIdx !== 'stale-payload'
            || triggerIdx !== 'stale-trigger'
        ) {
            throw new Error(testCase.name + ': replaced the valid cached pair');
        }
    }
})().catch(error => {
    console.error(error);
    process.exit(1);
});
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_custom_page_embeds_valid_manager_frontend_identity():
    identity = extract_frozen_json_constant(
        load_inline_script(),
        "MANAGER_FRONTEND_IDENTITY",
    )

    assert identity["schema_version"] == 1
    assert isinstance(identity["product_version"], str)
    assert identity["product_version"].strip()
    assert re.fullmatch(r"[0-9a-f]{64}", identity["build_id"])
    assert set(identity).issubset(
        {"schema_version", "product_version", "build_id", "git_commit"}
    )
    if "git_commit" in identity:
        assert re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", identity["git_commit"])


def test_every_frontend_command_includes_the_embedded_manager_identity():
    send_command = extract_js_function(load_inline_script(), "sendCommand")

    data_position = send_command.index("...data")
    identity_position = send_command.index(
        "frontend_identity: MANAGER_FRONTEND_IDENTITY"
    )

    # Keep the frozen page identity after caller data so a command cannot
    # accidentally replace it.
    assert data_position < identity_position


def test_api_bridge_logs_never_include_request_or_response_payloads():
    script = load_inline_script()
    send_command = extract_js_function(script, "sendCommand")
    poll_response = extract_js_function(script, "pollResponse")

    assert 'console.log("Sending command: " + action, payload)' not in (
        send_command
    )
    assert 'console.log("Received response:", data)' not in poll_response
    assert 'console.log("Sending command: " + action);' in send_command
    assert "String(data.status || \"unknown\")" in poll_response


def test_local_registry_payload_sizing_includes_the_frontend_identity():
    request_fits = extract_js_function(
        load_inline_script(),
        "localRegistryRequestFitsBridge",
    )

    assert "frontend_identity: MANAGER_FRONTEND_IDENTITY" in request_fits
    assert "JSON.stringify(request).length" in request_fits
    assert "LOCAL_REGISTRY_PAYLOAD_MAX_LENGTH" in request_fits


def test_api_bridge_ignores_stale_responses_and_clears_only_the_match():
    assert_ui_behavior(
        ("clearApiBridgePayload", "pollResponse"),
        setup="""
const payloadIdx = 1;
const API_BRIDGE_REQUEST_TIMEOUT_MS = 5000;
const responses = [
    {
        status: 'success',
        action: 'update',
        tx_id: 'stale-tx'
    },
    {
        status: 'success',
        action: 'remove',
        tx_id: 'tx-123'
    },
    {
        status: 'error',
        tx_id: 'tx-123',
        message: 'preflight failed'
    }
];
let fetchCalls = 0;
let clearCalls = 0;
let clearAtFetchCall = 0;
let verdictCalls = 0;
let warningCalls = 0;
const setTimeout = (resolve, delay) => resolve();
const fetchWithTimeout = async url => {
    if (url.includes('param=udevice')) {
        clearCalls += 1;
        clearAtFetchCall = fetchCalls;
        throw new Error('cleanup timed out');
    }
    return {
        result: [{
            Data: JSON.stringify(responses[fetchCalls++])
        }]
    };
};
function applyManagerIdentityVerdict() {
    verdictCalls += 1;
}
const console = {
    log: () => {},
    error: () => {},
    warn: () => { warningCalls += 1; }
};
""",
        exercise="""
    const response = await pollResponse('update', 'tx-123', responses.length);
    if (response.status !== 'error' || response.message !== 'preflight failed') {
        throw new Error('missing-action error response was not returned');
    }
    if (fetchCalls !== 3) {
        throw new Error(`expected both stale responses to be ignored`);
    }
    if (
        clearCalls !== 1
        || clearAtFetchCall !== 3
        || warningCalls !== 1
    ) {
        throw new Error('payload was not cleared exactly once after the match');
    }
    if (verdictCalls !== 1) {
        throw new Error('a stale response was applied');
    }

    responses.splice(
        0,
        responses.length,
        {
            status: 'success',
            action: 'update',
            tx_id: 'stale-tx'
        },
        {
            status: 'success',
            action: 'update',
            tx_id: 'tx-123',
            manager_identity: {state: 'consistent'}
        }
    );
    fetchCalls = 0;
    clearCalls = 0;
    clearAtFetchCall = 0;
    verdictCalls = 0;
    warningCalls = 0;
    const success = await pollResponse('update', 'tx-123', responses.length);
    if (success.status !== 'success' || success.action !== 'update') {
        throw new Error('matched success response was not returned');
    }
    if (
        fetchCalls !== 2
        || clearCalls !== 1
        || clearAtFetchCall !== 2
        || verdictCalls !== 1
        || warningCalls !== 1
    ) {
        throw new Error(
            'cleanup failure masked or duplicated a matched success'
        );
    }
""",
    )


def test_load_and_refresh_cache_manager_state_before_rendering():
    script = load_inline_script()
    load_plugins = extract_js_function(script, "loadPlugins")
    assert_ui_behavior(
        ("applyLoadedPlugins", "loadPlugins", "refreshUpdateStatus"),
        setup="""
let pluginCache = null;
let installedCache = null;
let localCache = [];
let updateStatusCache = {};
let versionsCache = {};
let managementCache = {};
let platformCache = {};
let installedMatchDetailsCache = {};
let installationConflictCache = {};
let installedScanError = '';
let managerKey = null;
let selfUpdateState = null;
let nextResponse = null;
const events = [];
const setManagerActivity = message => events.push('activity:' + message);
const clearManagerActivity = () => events.push('clear');
const applyManagerIdentityVerdict = () => events.push('identity');
const filterAndRender = () => {
    const phase = selfUpdateState ? selfUpdateState.phase : '';
    events.push('render:' + phase);
};
const sendCommand = async action => {
    events.push('command:' + action);
    return nextResponse;
};
const alert = message => {
    throw new Error('unexpected alert: ' + message);
};
""",
        exercise="""
    nextResponse = {
        status: 'success',
        manager_identity: {state: 'consistent'},
        data: {Example: {}},
        installed: [],
        self_update: {phase: 'scheduled'}
    };
    await loadPlugins();
    const expectedLoad = [
        'activity:Loading plugins...',
        'command:list_plugins',
        'identity',
        'render:scheduled',
        'clear'
    ];
    if (JSON.stringify(events) !== JSON.stringify(expectedLoad)) {
        throw new Error(
            'initial load published state out of order: '
            + JSON.stringify(events)
        );
    }

    events.splice(0);
    nextResponse = {
        status: 'success',
        data: {Example: {}},
        installed: [],
        self_update: {phase: 'running'}
    };
    await refreshUpdateStatus();
    const expectedRefresh = [
        'activity:Refreshing plugins...',
        'command:refresh_update_status',
        'render:running',
        'clear'
    ];
    if (JSON.stringify(events) !== JSON.stringify(expectedRefresh)) {
        throw new Error(
            'refresh published state out of order: '
            + JSON.stringify(events)
        );
    }
""",
    )

    assert "setStatus(`Loaded ${Object.keys(response.data).length} plugins.`)" not in (
        load_plugins
    )
    assert "function renderManagerStatus" in script

    render_status = extract_js_function(script, "renderManagerStatus")
    assert "setStatus(" in render_status
    assert "state === 'consistent'" in render_status
    assert "restart_required" in render_status
    assert "frontend_stale" in render_status
    assert "legacy_frontend" in render_status
    assert "ui_deploy_stale" in render_status
    assert "Hard-refresh this page" in render_status
    assert ".message" in render_status
    assert "product_version" not in render_status
    assert "build_id" not in render_status
    assert "git_commit" not in render_status
    assert "selfUpdateState" not in render_status


def test_manager_identity_states_gate_mutations_and_keep_recovery_read_only():
    script = load_inline_script()
    allows_mutations = extract_js_function(
        script,
        "managerIdentityAllowsMutations",
    )
    action_is_mutating = extract_js_function(script, "managerActionIsMutating")
    node_script = f"""
{allows_mutations}
{action_is_mutating}

const identityCases = [
    [null, false],
    [{{state: 'consistent', mutations_allowed: true}}, true],
    [{{state: 'consistent', mutations_allowed: false}}, false],
    [{{state: 'updating', mutations_allowed: false}}, false],
    [{{state: 'restart_required', mutations_allowed: false}}, false],
    [{{state: 'ui_deploy_stale', mutations_allowed: false}}, false],
    [{{state: 'frontend_stale', mutations_allowed: false}}, false],
    [{{state: 'legacy_frontend', mutations_allowed: false}}, false],
    [{{state: 'unverifiable', mutations_allowed: false}}, false]
];
for (const [verdict, expected] of identityCases) {{
    const actual = managerIdentityAllowsMutations(verdict);
    if (actual !== expected) {{
        throw new Error(
            `Unexpected mutation verdict for ${{JSON.stringify(verdict)}}: ${{actual}}`
        );
    }}
}}

const mutatingActions = [
    'install',
    'update',
    'remove',
    'rollback',
    'use_release',
    'upsert_local_registry_entry',
    'delete_local_registry_entry'
];
for (const action of mutatingActions) {{
    if (!managerActionIsMutating(action)) {{
        throw new Error(`Expected ${{action}} to be mutating`);
    }}
}}

const recoveryActions = [
    'list_plugins',
    'refresh_update_status',
    'get_local_registry',
    'self_update_status',
    'restart_domoticz'
];
for (const action of recoveryActions) {{
    if (managerActionIsMutating(action)) {{
        throw new Error(`Expected ${{action}} to remain available`);
    }}
}}
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr

    send_command = extract_js_function(script, "sendCommand")
    assert "managerActionIsMutating(action)" in send_command
    assert "managerIdentityAllowsMutations(managerIdentityVerdict)" in send_command
    assert "renderManagerStatus(" in send_command


def test_manager_identity_verdict_disables_mutating_controls():
    script = load_inline_script()

    assert "function updateManagerMutationControls" in script
    update_controls = extract_js_function(
        script,
        "updateManagerMutationControls",
    )
    assert "managerIdentityAllowsMutations(managerIdentityVerdict)" in update_controls
    assert "button[data-action]" in update_controls
    assert "managerActionIsMutating" in update_controls
    assert "local-registry-add" in update_controls
    assert "local-registry-save" in update_controls
    assert "local-registry-delete" in update_controls
    assert ".disabled" in update_controls

    apply_verdict = extract_js_function(script, "applyManagerIdentityVerdict")
    assert "managerIdentityVerdict =" in apply_verdict
    assert "updateManagerMutationControls()" in apply_verdict
    assert "renderManagerStatus(" in apply_verdict


def test_self_update_detail_card_rendering_is_removed_completely():
    html = (REPO_ROOT / "pypluginstore.html").read_text(encoding="utf-8")
    script = load_inline_script()

    assert ".self-update-detail" not in html
    assert "function renderSelfUpdateState" not in script
    assert "renderSelfUpdateState(" not in script


def test_installed_filter_state_is_persisted_in_local_storage():
    script = load_inline_script()

    assert "INSTALLED_FILTER_STORAGE_KEY = 'pypluginstore.installedOnly'" in script
    assert "readStoredInstalledFilter()" in script
    assert "writeStoredInstalledFilter(installedToggle.checked)" in script
    assert "installedToggle.checked = readStoredInstalledFilter()" in script
    assert ".localStorage.getItem(INSTALLED_FILTER_STORAGE_KEY)" in script
    assert ".localStorage.setItem(INSTALLED_FILTER_STORAGE_KEY" in script


def test_installed_filter_storage_helpers_are_tolerant():
    script = load_inline_script()
    read_function = extract_js_function(script, "readStoredInstalledFilter")
    write_function = extract_js_function(script, "writeStoredInstalledFilter")
    node_script = f"""
const INSTALLED_FILTER_STORAGE_KEY = 'pypluginstore.installedOnly';
{read_function}
{write_function}

const values = {{}};
global.window = {{
    localStorage: {{
        getItem: key => values[key] || null,
        setItem: (key, value) => values[key] = value,
    }}
}};

if (readStoredInstalledFilter() !== false) {{
    throw new Error('default installed filter state should be false');
}}

writeStoredInstalledFilter(true);
if (values[INSTALLED_FILTER_STORAGE_KEY] !== 'true') {{
    throw new Error('true state was not stored');
}}
if (readStoredInstalledFilter() !== true) {{
    throw new Error('true state was not restored');
}}

writeStoredInstalledFilter(false);
if (values[INSTALLED_FILTER_STORAGE_KEY] !== 'false') {{
    throw new Error('false state was not stored');
}}
if (readStoredInstalledFilter() !== false) {{
    throw new Error('false state was not restored');
}}

global.window = {{
    localStorage: {{
        getItem: () => {{ throw new Error('storage unavailable'); }},
        setItem: () => {{ throw new Error('storage unavailable'); }},
    }}
}};

if (readStoredInstalledFilter() !== false) {{
    throw new Error('unavailable storage should fall back to false');
}}
writeStoredInstalledFilter(true);
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_domoticz_theme_layout_is_default_and_original_layout_is_optional():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    script = load_inline_script()

    assert '<div id="pypluginstore-container" data-layout="theme">' in html
    assert 'id="layout-toggle" checked' in html
    assert "return stored === LAYOUT_PYPLUGIN ? LAYOUT_PYPLUGIN : LAYOUT_THEME" in script
    assert "return LAYOUT_THEME" in extract_js_function(script, "readStoredLayoutMode")
    assert "Domoticz theme" in script
    assert "PyPlugin layout" in html


def test_domoticz_theme_probe_matches_dashboard_tile_contexts():
    script = load_inline_script()

    assert "const holder = document.getElementById('holder')" in script
    assert "const dashContent = document.getElementById('dashcontent')" in script
    assert "'<div id=\"dashcontent\">'" in script
    assert "'<div class=\"row\">'" in script
    assert "'<div class=\"span3 span4\">'" in script
    assert "'<div id=\"pypluginstore-theme-card-sample\" class=\"item itemBlock\">'" in script
    assert "'<div id=\"search\"><input type=\"text\" id=\"searchInput\"" in script
    assert "'<select id=\"pypluginstore-theme-select-sample\" class=\"ui-corner-all\"><option>Sort</option></select>'" in script
    assert "probe.querySelector('#searchInput') || probe.querySelector('input')" in script
    assert "probe.querySelector('#pypluginstore-theme-select-sample') || probe.querySelector('#pypluginstore-theme-content-input')" in script
    assert "'.btnstyle3, .btnsmall, .btn.btn-default, .btn'" in script
    assert "'--main-item-bg-color'" in script
    assert "'--ColorDashboard_Block_or_Span3and4'" in script
    assert "'--ColorFontName'" in script
    assert "'--dz-accent'" in script
    assert "'--main-blue-color'" in script


def test_domoticz_theme_uses_panel_and_button_contract_variables():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    script = load_inline_script()

    assert "--pps-panel-bg: var(--dz-pps-panel-bg, var(--dz-panel-bg, transparent))" in html
    assert "--pps-panel-text: var(--dz-pps-panel-text, var(--dz-panel-text, var(--pps-text)))" in html
    assert "--pps-panel-shadow: var(--dz-pps-panel-shadow, none)" in html
    assert "--pps-divider-border: var(--dz-pps-divider-border, 1px solid var(--dz-border, var(--dz-border-color, var(--dz-input-border, transparent))))" in html
    assert "--pps-card-border-hover: var(--dz-pps-card-border-hover, var(--pps-border-hover))" in html
    assert "--pps-card-button-bg: var(--dz-pps-card-button-bg, var(--pps-button-bg))" in html
    assert "--pps-card-button-border: var(--dz-pps-card-button-border, var(--pps-button-border))" in html
    assert "color: var(--pps-panel-text)" in extract_css_rule(html, "\n    #pypluginstore-status")
    assert "border-color: var(--pps-card-border-hover)" in extract_css_rule(html, "#pypluginstore-container .pps-card:hover")
    assert "border-block-end: var(--pps-divider-border)" in html
    assert "border-bottom: 1px solid var(--pps-border)" not in html
    assert "const rawPanelBg = firstUsefulThemeValue(" in script
    assert "const panelIsTransparent = !isUsefulThemeValue(rawPanelBg)" in script
    assert "const domoticzLegacyItemHoverBg = readCssVariable('--ColorDashboard_Block_or_Span3and4_HOVER')" in script
    assert "const cardHoverBg = firstResolvedThemeValue(hoverTileBg, readCssVariable('--dz-widget-hover-bg'), domoticzLegacyItemHoverBg, cardBg)" in script
    assert "function firstResolvedThemeValue()" in script
    assert "function isUnresolvedCssVariableReference(value)" in script
    assert "isUsefulThemeValue(readCssVariable(match[1]))" in script
    assert "const panelText = ensureReadableColor(panelBg" in script
    assert "setRequiredThemeVar('--dz-pps-panel-bg', panelBg)" in script
    assert "setRequiredThemeVar('--dz-pps-panel-text', panelText)" in script
    assert "setRequiredThemeVar('--dz-pps-panel-border', panelBorder)" in script
    assert "setRequiredThemeVar('--dz-pps-panel-shadow', panelShadow)" in script
    assert "container.style.setProperty(name, value)" in script
    assert "panelIsTransparent ? '0 solid transparent'" in script
    assert "panelIsTransparent ? 'none'" in script
    assert "setThemeVar('--dz-pps-panel-bg', cardBg)" not in script
    assert "const panelBg = panelIsTransparent ? 'transparent' : rawPanelBg" in script
    assert "const cardHasTransparentBorder = isNoneThemeValue(cardBorder) ||" in script
    assert "const cardThemeExposesShadowHover = isUsefulThemeValue(hoverTileShadow) ||" in script
    assert "const cardCanInferAccentShadowHover = isUsefulThemeValue(cardShadow) &&" in script
    assert "const cardUsesShadowHoverBorder = cardHasTransparentBorder && (" in script
    assert "isUsefulThemeValue(widgetHoverShadow)" in script
    assert "cardUsesShadowHoverBorder && isUsefulThemeValue(domoticzAccent) ? '0 0 0 2px ' + domoticzAccent : ''" in script
    assert "const cardTitleRadius = cardUsesShadowHoverBorder ? firstUsefulThemeValue(cardRadius, readRadius(nameStyle)) : readRadius(nameStyle)" in script
    assert "isTransparentBorder(cardBorder)" in script
    assert "cardUsesShadowHoverBorder ? 'transparent' : ''" in script
    assert "setThemeVar('--dz-pps-shadow-hover', cardShadowHover, { allowNone: true })" in script
    assert "setThemeVar('--dz-pps-card-title-radius', cardTitleRadius)" in script
    assert "setThemeVar('--dz-pps-card-border-hover', cardBorderHover, { allowTransparent: true })" in script
    assert "setThemeVar('--dz-pps-border-hover', cardBorderHover" not in script
    assert "const visibleTitleBorderColor = readVisibleBorderColor(nameStyle)" in script
    assert "const themeBorderHoverColor = firstUsefulThemeValue(" in script
    assert "readCssVariable('--dz-panel-text')" in script
    assert "readCssVariable('--dz-modal-text')" in script
    assert "function readColorBackground(style)" in script
    assert "const buttonBackgroundDeclaration = readFirstMatchingCssDeclarationInfo(" in script
    assert "const buttonHoverDeclaration = readFirstMatchingCssDeclarationInfo(" in script
    assert "const buttonHoverBg = normalizeBackgroundUrls(buttonHoverDeclaration.value, buttonHoverDeclaration.baseUrl)" in script
    assert "function createDomoticzThemeProbe()" in script
    assert "function readDomoticzThemeProbe(probe)" in script
    assert "function readDomoticzThemeHoverStyles()" in script
    assert "function createDomoticzThemeVarWriters(container)" in script
    assert "function applyDomoticzButtonThemeVars(options)" in script
    assert "function applyDomoticzPrimaryButtonThemeVars(options)" in script
    assert "const themeButtonBg = readCssVariable('--dz-btn-bg')" in script
    assert "const buttonComputedBg = readColorBackground(buttonStyle)" in script
    assert "const buttonBg = firstUsefulThemeValue(themeButtonBg, buttonComputedBg)" in script
    assert "const buttonBackground = normalizeBackgroundUrls(" in script
    assert "options.buttonBackgroundDeclaration && options.buttonBackgroundDeclaration.baseUrl" in script
    assert "const buttonHoverColorBg = readColorBackgroundValue(options.buttonHoverBg)" in script
    assert "const cardButtonBg = firstUsefulThemeValue(buttonBackground, buttonComputedBg, buttonBg)" in script
    assert "const cardButtonHoverBg = firstUsefulThemeValue(buttonHoverColorBg, cardButtonBg)" in script
    assert "const cardButtonUsesPaintedBackground = isUsefulThemeValue(buttonBackground) && !readColorBackgroundValue(buttonBackground)" in script
    assert "cardButtonUsesPaintedBackground && isUsefulThemeValue(buttonStyle.color)" in script
    assert "setThemeVar('--dz-pps-card-button-bg', cardButtonBg)" in script
    assert "setThemeVar('--dz-pps-card-button-hover-bg', cardButtonHoverBg)" in script
    assert "setThemeVar('--dz-pps-card-button-text', cardButtonText)" in script
    assert "setThemeVar('--dz-pps-card-button-border', cardButtonBorder, { allowNone: true })" in script
    assert "function readColorBackgroundValue(value)" in script
    assert "return resolveCssColor(value) ? String(value).trim() : ''" in script
    assert "function normalizeBackgroundUrls(value, baseUrl)" in script
    assert "function resolveCssAssetUrl(urlValue, baseUrl)" in script
    assert "function getDomoticzCssBaseUrl()" in script
    assert "return new URL(clean, baseUrl || getDomoticzCssBaseUrl()).href" in script
    assert "pathname.match(/^(.*\\/domoticz)(?:\\/|$)/)" in script
    assert "basePath + '/css/'" in script
    assert "baseUrl: ownerHref || document.baseURI" in script
    assert "readCssVariable('--dz-btn-bg')" in script
    assert "readCssVariable('--dz-btn-hover-bg')" in script
    assert "readCssVariable('--dz-btn-text')" in script
    assert "readCssVariable('--dz-btn-border')" in script
    assert "ensureVisibleBorder(buttonEffectiveBg" in script
    assert "toCssBorder(readCssVariable('--dz-btn-border'))" in script
    assert "const buttonBg = firstUsefulThemeValue(readBackground(buttonStyle), readCssVariable('--dz-btn-bg'))" not in script


def test_domoticz_theme_keeps_container_transparent_for_image_only_page_backgrounds():
    script = load_inline_script()

    assert "const pageSurfaceBg = firstUsefulThemeValue(" in script
    assert "const pageBg = firstUsefulThemeValue(pageSurfaceBg, '#ffffff')" in script
    assert "setThemeVar('--dz-pps-bg', pageSurfaceBg)" in script
    assert "setThemeVar('--dz-pps-bg', pageBg)" not in script


def test_domoticz_theme_preserves_borderless_theme_cards():
    script = load_inline_script()

    assert "const cardBorder = firstUsefulThemeValueOrNone(" in script
    assert "function firstUsefulThemeValueOrNone()" in script
    assert "isNoneThemeValue(arguments[index])" in script
    assert "setThemeVar('--dz-pps-card-border', cardBorder, { allowNone: true })" in script


def test_normal_action_buttons_use_normal_button_style():
    html = (REPO_ROOT / "pypluginstore.html").read_text()

    refresh_rule = extract_css_rule(html, "#pypluginstore-container .btn-refresh")
    refresh_hover_rule = extract_css_rule(html, "#pypluginstore-container .btn-refresh:hover")

    assert "background: var(--pps-button-bg)" in refresh_rule
    assert "color: var(--pps-button-text)" in refresh_rule
    assert "border: var(--pps-button-border)" in refresh_rule
    assert "background: var(--pps-button-hover-bg)" in refresh_hover_rule
    assert "border-color: var(--pps-border-hover)" in refresh_hover_rule
    assert "var(--pps-button-primary" not in refresh_rule
    assert "var(--pps-primary" not in refresh_hover_rule

    for selector in [
        "#pypluginstore-container .btn-install",
        "#pypluginstore-container .btn-update-current",
        "#pypluginstore-container .btn-info",
    ]:
        rule = extract_css_rule(html, selector)
        hover_rule = extract_css_rule(html, selector + ":hover")

        assert "background: var(--pps-card-button-bg)" in rule
        assert "color: var(--pps-card-button-text)" in rule
        assert "border: var(--pps-card-button-border)" in rule
        assert "background: var(--pps-card-button-hover-bg)" in hover_rule
        assert "var(--pps-button-primary" not in rule
        assert "var(--pps-primary" not in hover_rule

    assert "border-color: var(--pps-border-hover)" in extract_css_rule(html, "#pypluginstore-container .btn-install:hover")


def test_domoticz_theme_search_input_preserves_theme_specific_styles():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    script = load_inline_script()

    assert "--pps-input-border-block-end" in html
    assert "--pps-input-placeholder-opacity" in html
    assert "window.getComputedStyle(input, '::placeholder')" in script
    assert "setRequiredThemeVar('--dz-pps-input-bg', 'transparent')" in script
    assert "const contentControlBorderBlockEnd = readBorderSide(contentControlStyle, 'Bottom')" in script
    assert "const inputBorderBlockEnd = inputStyle ? chooseInputBorderBlockEnd(" in script
    assert "function chooseInputBorderBlockEnd(inputBorder, contentBorder, accentColor)" in script
    assert "function borderColorMatches(borderValue, colorValue)" in script
    assert "function readVisibleBorderColor(style)" in script
    assert "function applyDomoticzInputThemeVars(options)" in script
    assert "readBorderSide(inputStyle, 'Bottom')" in script
    assert "options.setThemeVar('--dz-pps-input-border-block-end', options.inputBorderBlockEnd, { allowNone: true })" in script
    assert "setThemeVar('--dz-pps-input-radius', readRadius(inputStyle))" in script


def test_filter_controls_share_panel_text_and_markup_pattern():
    html = (REPO_ROOT / "pypluginstore.html").read_text()

    assert '<label class="filter-control sort-controls" for="sort-select">' in html
    assert '<label class="filter-control layout-choice"' in html
    assert '<label class="filter-control installed-choice" for="installed-toggle">' in html
    assert html.count('class="filter-control-label"') == 3
    assert "color: var(--pps-panel-text)" in extract_css_rule(html, "#pypluginstore-container .filter-control")
    assert "color: var(--pps-panel-text)" in extract_css_rule(html, "#pypluginstore-container .sort-controls select")
    assert "#pypluginstore-container .filters label" not in html


def test_platform_badges_are_wired_to_backend_response():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    script = load_inline_script()

    assert ".platform-badge-linux" in html
    assert ".platform-badge-windows" in html
    assert "platformCache = response.platforms || {}" in script
    assert "platform-badge platform-badge-" in script


def test_card_header_badges_use_multiline_rows():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    script = load_inline_script()

    header_rule = extract_css_rule(html, "#pypluginstore-container .pps-card-header")
    assert "flex-direction: column" in header_rule
    assert "align-items: stretch" in header_rule
    assert "justify-content: space-between" not in header_rule

    main_rule = extract_css_rule(html, "#pypluginstore-container .pps-card-header-main")
    assert "display: grid" in main_rule
    assert "grid-template-columns: minmax(0, 1fr) auto" in main_rule

    row_rule = extract_css_rule(html, "#pypluginstore-container .pps-card-header-platforms,")
    assert "justify-content: flex-start" in row_rule
    assert "flex-wrap: wrap" in row_rule

    assert ".pps-card-header-left" not in html
    assert "headerMain.className = 'pps-card-header-main'" in script
    assert "statusBadges.className = 'pps-card-header-status'" in script
    assert "headerMain.appendChild(badge)" in script
    assert "statusBadges.appendChild(nonGitBadge)" in script
    assert "statusBadges.appendChild(mismatchBadge)" in script
    assert "platformBadges.className = 'pps-card-header-platforms platform-badges'" in script
    assert "if (knownPlatforms.length > 0 || isLocal)" in script
    assert "platformBadges.appendChild(localBadge)" in script
    assert "statusBadges.appendChild(localBadge)" not in script
    assert "if (statusBadges.childNodes.length > 0)" in script


def test_plugin_card_actions_wrap_when_buttons_do_not_fit():
    html = (REPO_ROOT / "pypluginstore.html").read_text()

    actions_rule = extract_css_rule(
        html,
        "#pypluginstore-container .pps-actions",
    )
    assert "display: flex" in actions_rule
    assert "flex-wrap: wrap" in actions_rule
    assert "gap: 8px" in actions_rule


def test_plugin_card_title_and_details_are_selectable():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    selector_group = """#pypluginstore-container .pps-plugin-title,
    #pypluginstore-container .pps-card-desc,
    #pypluginstore-container .pps-card-meta"""

    selectable_rule = extract_css_rule(
        html,
        "#pypluginstore-container .pps-plugin-title,",
    )
    assert selector_group in html
    assert "-webkit-user-select: text" in selectable_rule
    assert "user-select: text" in selectable_rule
    assert "cursor: text" in selectable_rule


def test_local_registry_uses_one_accessible_native_dialog():
    html = (REPO_ROOT / "pypluginstore.html").read_text()

    assert 'id="manage-local-registry"' in html
    assert '>Local registry</button>' in html
    assert html.count("<dialog ") == 1
    assert 'id="local-registry-dialog"' in html
    assert 'aria-labelledby="local-registry-title"' in html
    assert 'id="local-registry-title"' in html
    assert 'id="local-registry-close"' in html
    assert 'aria-label="Close local registry manager"' in html
    assert 'id="local-registry-alert"' in html
    assert 'role="alert"' in html
    assert 'aria-live="assertive"' in html


def test_local_registry_form_has_only_approved_editable_fields():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    dialog = html[
        html.index('<dialog id="local-registry-dialog"'):
        html.index("</dialog>")
    ]

    for field_id in [
        "local-registry-key",
        "local-registry-source",
        "local-registry-description",
        "local-registry-branch",
    ]:
        assert f'for="{field_id}"' in dialog
        assert f'id="{field_id}"' in dialog

    assert 'id="local-registry-public-seed"' in dialog
    assert 'maxlength="128"' in dialog
    assert 'maxlength="1000"' in dialog
    assert 'maxlength="500"' in dialog
    assert 'maxlength="255"' in dialog
    assert "platform" not in dialog.lower()


def test_local_registry_ui_wires_revisioned_crud_actions():
    script = load_inline_script()

    assert "sendCommand('get_local_registry', {})" in script
    assert "sendCommand('upsert_local_registry_entry'," in script
    assert "sendCommand('delete_local_registry_entry'," in script
    assert "expected_revision: localRegistryRevision" in script
    assert "original_key: localRegistryOriginalKey" in script
    assert "field_errors" in script
    assert "reload_required" in script
    assert "localRegistryKey.readOnly = Boolean(localRegistryOriginalKey)" in script
    assert "await loadPlugins()" in extract_js_function(
        script, "saveLocalRegistryEntry"
    )


def test_local_registry_delete_confirmation_is_inline_and_explains_installed_state():
    script = load_inline_script()

    assert "local-registry-delete-confirm" in script
    assert "The installed plugin will remain on disk." in script
    assert "may become Repo mismatch" in script
    assert "confirm(`Delete local registry" not in script


def test_local_registry_dialog_uses_theme_tokens_and_modern_layout():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    dialog_rule = extract_css_rule(
        html, "#pypluginstore-container .local-registry-dialog"
    )
    form_rule = extract_css_rule(
        html, "#pypluginstore-container .local-registry-form"
    )

    assert "background: var(--pps-panel-bg)" in dialog_rule
    assert "color: var(--pps-panel-text)" in dialog_rule
    assert "max-block-size:" in dialog_rule
    assert "inline-size:" in dialog_rule
    assert "display: grid" in form_rule
    assert "gap:" in form_rule
    assert ".local-registry-dialog::backdrop" in html
    assert ".local-registry-field :is(input, textarea, select):focus-visible" in html


def test_custom_ui_references_existing_icon_asset():
    html = (REPO_ROOT / "pypluginstore.html").read_text()

    assert 'src="images/pypluginstore-icon.png"' in html
    assert "this.src = '/images/pypluginstore-icon.png'" in html
    assert (REPO_ROOT / "pypluginstore-icon.png").is_file()


def test_self_update_success_skips_only_the_generic_success_alert():
    assert_ui_behavior(
        ("handleAction",),
        setup="""
let installedScanError = '';
let managerIdentityVerdict = {
    state: 'consistent',
    mutations_allowed: true
};
let managerKey = 'PyPluginStore';
let selfUpdateState = null;
let nextResponse = null;
const alerts = [];
const activities = [];
let selfUpdatePolls = 0;
let loadCalls = 0;
const managerActionIsMutating = () => true;
const managerIdentityAllowsMutations = () => true;
const renderManagerStatus = () => {};
const confirm = () => true;
const actionDisplayName = action => action;
const setManagerActivity = message => activities.push(message);
const clearManagerActivity = () => activities.push('cleared');
const sendCommand = async () => nextResponse;
const handleReleaseManagementAction = async () => nextResponse;
const filterAndRender = () => {};
const pollSelfUpdateStatus = () => { selfUpdatePolls += 1; };
const loadPlugins = async () => { loadCalls += 1; };
const alert = message => alerts.push(message);
""",
        exercise="""
    nextResponse = {
        status: 'success',
        message: 'Plugin installed.'
    };
    await handleAction('install', 'ExamplePlugin');
    if (alerts.length !== 1 || alerts[0] !== 'Plugin installed.') {
        throw new Error('ordinary plugin success did not show its alert');
    }
    if (loadCalls !== 1) {
        throw new Error('ordinary plugin success did not reload the list');
    }

    nextResponse = {
        status: 'success',
        operation: 'self_update',
        message: 'PyPluginStore update scheduled.',
        self_update: {phase: 'scheduled'}
    };
    await handleAction('update', managerKey);
    if (alerts.length !== 1) {
        throw new Error('self-update leaked the generic success alert');
    }
    if (selfUpdatePolls !== 1) {
        throw new Error('self-update status polling was not started');
    }
    if (!selfUpdateState || selfUpdateState.phase !== 'scheduled') {
        throw new Error('self-update state was not cached before polling');
    }
    if (loadCalls !== 1) {
        throw new Error('self-update reloaded the list before restart recovery');
    }
    if (!activities.includes('Updating PyPluginStore...')) {
        throw new Error('self-update activity was not kept inline');
    }
""",
    )


def test_send_command_concurrency_guard_is_silent_and_does_not_dispatch():
    assert_ui_behavior(
        ("sendCommand",),
        setup="""
let isLoading = true;
let managerIdentityVerdict = {
    state: 'consistent',
    mutations_allowed: true
};
let fetchCalls = 0;
let alertCalls = 0;
const managerActionIsMutating = () => false;
const managerIdentityAllowsMutations = () => true;
const renderManagerStatus = () => {};
const fetch = async () => { fetchCalls += 1; };
const alert = () => { alertCalls += 1; };
""",
        exercise="""
    const response = await sendCommand('list_plugins', {});
    if (fetchCalls !== 0) {
        throw new Error('concurrent command reached the API bridge');
    }
    if (alertCalls !== 0) {
        throw new Error('concurrent command used a modal alert');
    }
    if (response !== null && response !== undefined) {
        throw new Error('silent guard returned an actionable response');
    }
""",
    )


def test_send_command_avoids_preclear_and_restores_busy_state_on_failure():
    assert_ui_behavior(
        ("sendCommand",),
        setup="""
const API_BRIDGE_REQUEST_TIMEOUT_MS = 5000;
const LOCAL_REGISTRY_PAYLOAD_MAX_LENGTH = 2000;
const MANAGER_FRONTEND_IDENTITY = {build_id: 'frontend'};
let managerIdentityVerdict = {
    state: 'consistent',
    mutations_allowed: true
};
let payloadIdx = '10';
let triggerIdx = '11';
let isLoading = false;
let fetchMode = 'success';
const events = [];
const managerActionIsMutating = () => false;
const managerIdentityAllowsMutations = () => true;
const renderManagerStatus = () => {};
const createTransactionId = () => 'tx-123';
const setCommandBusy = busy => {
    isLoading = Boolean(busy);
    events.push('busy:' + String(busy));
};
const clearApiBridgePayload = async () => {
    events.push('clear');
};
const fetchWithTimeout = async () => {
    events.push('fetch');
    if (fetchMode === 'timeout') {
        throw new Error('HTTP request timed out.');
    }
    return {};
};
const pollResponse = async () => {
    events.push('poll');
    return {status: 'success'};
};
""",
        exercise="""
    const response = await sendCommand('list_plugins', {});
    if (!response || response.status !== 'success') {
        throw new Error('successful command did not return its response');
    }
    const expectedSuccess = [
        'busy:true',
        'fetch',
        'fetch',
        'poll',
        'busy:false'
    ];
    if (JSON.stringify(events) !== JSON.stringify(expectedSuccess)) {
        throw new Error(
            'unexpected successful dispatch lifecycle: '
            + JSON.stringify(events)
        );
    }

    events.splice(0);
    fetchMode = 'timeout';
    let failure = null;
    try {
        await sendCommand('list_plugins', {});
    } catch (error) {
        failure = error;
    }
    if (!failure || !failure.message.includes('timed out')) {
        throw new Error('transport failure was not propagated');
    }
    const expectedFailure = ['busy:true', 'fetch', 'busy:false'];
    if (JSON.stringify(events) !== JSON.stringify(expectedFailure)) {
        throw new Error(
            'failed dispatch did not restore busy state: '
            + JSON.stringify(events)
        );
    }
""",
    )


def test_restart_recovery_state_machine_is_bounded_and_build_aware():
    script = load_inline_script()
    matches = extract_js_function(
        script,
        "restartRecoveryResponseMatches",
    )
    recovery = extract_js_function(script, "waitForRestartRecovery")
    node_script = f"""
const RESTART_RECOVERY_DEADLINE_MS = 120000;
const RESTART_RECOVERY_MAX_PROBES = 24;
const RESTART_RECOVERY_RETRY_MS = 5000;
const RESTART_RECOVERY_MAX_RETRY_MS = 15000;
const probeRestartRecoveryBackend = async () => null;
const console = {{warn: () => {{}}}};
{matches}
{recovery}

(async () => {{
    const expectedBuild = 'b'.repeat(64);
    const previousRuntime = 'runtime-before-restart';
    const transitions = [];
    let probes = 0;
    let clock = 0;
    const responses = [
        new Error('offline'),
        {{
            status: 'success',
            manager_identity: {{
                state: 'restart_required',
                runtime_instance_id: previousRuntime,
                runtime: {{build_id: 'a'.repeat(64)}},
                matches: {{backend_installed: false}}
            }}
        }},
        {{
            status: 'success',
            manager_identity: {{
                state: 'consistent',
                runtime_instance_id: previousRuntime,
                runtime: {{build_id: expectedBuild}},
                matches: {{backend_installed: true}}
            }}
        }},
        {{
            status: 'success',
            manager_identity: {{
                state: 'frontend_stale',
                runtime_instance_id: 'runtime-after-restart',
                runtime: {{build_id: expectedBuild}},
                matches: {{backend_installed: true}}
            }}
        }}
    ];
    const recovered = await waitForRestartRecovery(expectedBuild, {{
        deadlineMs: 60000,
        maxProbes: 6,
        pollIntervalMs: 5000,
        previousRuntimeInstanceId: previousRuntime,
        now: () => clock,
        sleep: async delay => {{ clock += delay; }},
        probe: async () => {{
            probes += 1;
            const response = responses.shift();
            if (response instanceof Error) throw response;
            return response;
        }},
        onTransition: state => transitions.push(state)
    }});
    for (const state of ['waiting', 'verifying', 'recovered']) {{
        if (!transitions.includes(state)) {{
            throw new Error(`missing restart transition ${{state}}`);
        }}
    }}
    if (recovered.phase !== 'recovered' || probes !== 4) {{
        throw new Error('new runtime and installed build were not both verified');
    }}

    const timeoutTransitions = [];
    probes = 0;
    clock = 0;
    const timedOut = await waitForRestartRecovery(expectedBuild, {{
        deadlineMs: 12000,
        maxProbes: 2,
        pollIntervalMs: 5000,
        previousRuntimeInstanceId: previousRuntime,
        now: () => clock,
        sleep: async delay => {{ clock += delay; }},
        probe: async () => {{
            probes += 1;
            return {{
                status: 'success',
                manager_identity: {{
                    state: 'restart_required',
                    runtime_instance_id: previousRuntime,
                    runtime: {{build_id: 'c'.repeat(64)}},
                    matches: {{backend_installed: false}}
                }}
            }};
        }},
        onTransition: state => timeoutTransitions.push(state)
    }});
    if (timedOut.phase !== 'timed_out') {{
        throw new Error('restart recovery did not time out explicitly');
    }}
    if (!timeoutTransitions.includes('timed_out') || probes > 2) {{
        throw new Error('restart probes were not bounded');
    }}
}})().catch(error => {{
    console.error(error);
    process.exit(1);
}});
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_fetch_with_timeout_bounds_fetch_and_json_without_abort_controller():
    assert_ui_behavior(
        ("fetchWithTimeout",),
        setup="""
const AbortController = undefined;
let fetchMode = 'fetch';
let jsonCalls = 0;
const fetch = async () => {
    if (fetchMode === 'fetch') return await new Promise(() => {});
    return {
        ok: true,
        json: async () => {
            jsonCalls += 1;
            return await new Promise(() => {});
        }
    };
};
const setTimeout = callback => {
    queueMicrotask(callback);
    return 1;
};
const clearTimeout = () => {};

async function expectTimeout(label, operation) {
    try {
        await operation();
    } catch (error) {
        if (!String(error.message).includes('timed out')) {
            throw new Error(label + ' rejected for the wrong reason');
        }
        return;
    }
    throw new Error(label + ' was not bounded by the timeout');
}
""",
        exercise="""
    await expectTimeout(
        'never-resolving fetch',
        () => fetchWithTimeout('/probe', 5)
    );
    fetchMode = 'json';
    await expectTimeout(
        'never-resolving response.json',
        () => fetchWithTimeout('/probe', 5, {parseJson: true})
    );
    if (jsonCalls !== 1) {
        throw new Error('response.json was not included in the request bound');
    }
""",
    )


def test_restart_recovery_rejects_invalid_or_late_matching_responses():
    script = load_inline_script()
    matches = extract_js_function(
        script,
        "restartRecoveryResponseMatches",
    )
    recovery = extract_js_function(script, "waitForRestartRecovery")
    node_script = f"""
const RESTART_RECOVERY_DEADLINE_MS = 120000;
const RESTART_RECOVERY_MAX_PROBES = 24;
const RESTART_RECOVERY_RETRY_MS = 5000;
const RESTART_RECOVERY_MAX_RETRY_MS = 15000;
const probeRestartRecoveryBackend = async () => null;
const console = {{warn: () => {{}}}};
{matches}
{recovery}

const expectedBuild = 'b'.repeat(64);
const oldRuntime = 'runtime-before';
const matchingResponse = {{
    status: 'success',
    manager_identity: {{
        state: 'frontend_stale',
        runtime_instance_id: 'runtime-after',
        runtime: {{build_id: expectedBuild}},
        matches: {{backend_installed: true}}
    }}
}};
if (!restartRecoveryResponseMatches(
    matchingResponse,
    expectedBuild,
    oldRuntime
)) {{
    throw new Error('new runtime with the installed build was rejected');
}}
const rejectedCases = [
    [
        'same runtime nonce',
        {{
            ...matchingResponse,
            manager_identity: {{
                ...matchingResponse.manager_identity,
                runtime_instance_id: oldRuntime
            }}
        }},
        expectedBuild,
        oldRuntime
    ],
    ['empty target build', matchingResponse, '', oldRuntime],
    ['empty previous nonce', matchingResponse, expectedBuild, ''],
    [
        'unexpected build',
        {{
            ...matchingResponse,
            manager_identity: {{
                ...matchingResponse.manager_identity,
                runtime: {{build_id: 'c'.repeat(64)}}
            }}
        }},
        expectedBuild,
        oldRuntime
    ],
    [
        'backend does not match installed files',
        {{
            ...matchingResponse,
            manager_identity: {{
                ...matchingResponse.manager_identity,
                matches: {{backend_installed: false}}
            }}
        }},
        expectedBuild,
        oldRuntime
    ],
    [
        'backend is still updating',
        {{
            ...matchingResponse,
            manager_identity: {{
                ...matchingResponse.manager_identity,
                state: 'updating'
            }}
        }},
        expectedBuild,
        oldRuntime
    ]
];
for (const [label, response, target, previous] of rejectedCases) {{
    if (restartRecoveryResponseMatches(response, target, previous)) {{
        throw new Error(label + ' was accepted');
    }}
}}

(async () => {{
    let clock = 0;
    const result = await waitForRestartRecovery(expectedBuild, {{
        deadlineMs: 100,
        maxProbes: 1,
        pollIntervalMs: 0,
        maxPollIntervalMs: 0,
        previousRuntimeInstanceId: oldRuntime,
        now: () => clock,
        sleep: async () => {{}},
        probe: async () => {{
            clock = 101;
            return matchingResponse;
        }}
    }});
    if (result.phase !== 'timed_out') {{
        throw new Error('a response completing after the deadline was accepted');
    }}
}})().catch(error => {{
    process.stderr.write(error.stack + '\\n');
    process.exit(1);
}});
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_restart_probe_is_lightweight_and_reuses_cached_bridge_ids():
    assert_ui_behavior(
        ("probeRestartRecoveryBackend",),
        setup="""
const RESTART_RECOVERY_COMMAND_RETRIES = 2;
const RESTART_RECOVERY_REQUEST_TIMEOUT_MS = 2000;
let payloadIdx = '10';
let triggerIdx = '11';
let discoveryCalls = 0;
const commands = [];
const findApiBridgeDevices = async () => { discoveryCalls += 1; };
const sendCommand = async (action, data, options) => {
    commands.push({action, data, options});
    return {status: 'success'};
};
const console = {warn: () => {}};
""",
        exercise="""
    await probeRestartRecoveryBackend();
    if (discoveryCalls !== 0) {
        throw new Error('cached bridge IDs triggered rediscovery');
    }
    if (
        commands.length !== 1
        || commands[0].action !== 'self_update_status'
    ) {
        throw new Error('restart probe used a heavyweight command');
    }
    if (
        commands[0].options.retries !== RESTART_RECOVERY_COMMAND_RETRIES
        || commands[0].options.requestTimeoutMs
            !== RESTART_RECOVERY_REQUEST_TIMEOUT_MS
    ) {
        throw new Error('restart probe did not use its bounded options');
    }
""",
    )


def test_restart_orchestration_verifies_identity_and_handles_failures_inline():
    restart = extract_js_function(load_inline_script(), "restartDomoticz")
    node_script = f"""
let managerIdentityVerdict = {{
    installed: {{build_id: 'b'.repeat(64)}},
    runtime_instance_id: 'runtime-before'
}};
const actions = [];
const notices = [];
const lifecycle = [];
const activities = [];
let actionsSeenByRecovery = null;
let recoveryArguments = null;
let recoveryPhase = 'recovered';
let listMode = 'success';
let applied = 0;
let cleared = 0;
const confirm = () => true;
const setManagerActivity = message => {{ activities.push(message); }};
const setManagerNotice = message => {{ notices.push(message); }};
const alert = message => {{
    throw new Error('unexpected alert: ' + message);
}};
const sendCommand = async action => {{
    actions.push(action);
    if (action === 'restart_domoticz') return {{status: 'success'}};
    if (listMode === 'error') return {{status: 'error'}};
    return {{status: 'success', data: {{}}}};
}};
const waitForRestartRecovery = async (expectedBuildId, options) => {{
    actionsSeenByRecovery = actions.slice();
    recoveryArguments = {{expectedBuildId, options}};
    return {{
        phase: recoveryPhase,
        response: {{status: 'success'}}
    }};
}};
const applyLoadedPlugins = () => {{
    applied += 1;
    lifecycle.push('apply');
}};
const clearManagerActivity = () => {{
    cleared += 1;
    lifecycle.push('clear');
}};
const console = {{warn: () => {{}}}};

{restart}

function resetScenario() {{
    actions.splice(0);
    notices.splice(0);
    lifecycle.splice(0);
    activities.splice(0);
    actionsSeenByRecovery = null;
    recoveryArguments = null;
    recoveryPhase = 'recovered';
    listMode = 'success';
    applied = 0;
    cleared = 0;
}}

(async () => {{
    await restartDomoticz();
    if (JSON.stringify(actionsSeenByRecovery) !== '["restart_domoticz"]') {{
        throw new Error('plugin list was fetched before verified recovery');
    }}
    if (
        recoveryArguments.expectedBuildId !== 'b'.repeat(64)
        || recoveryArguments.options.previousRuntimeInstanceId
            !== 'runtime-before'
    ) {{
        throw new Error('restart recovery did not receive both identity guards');
    }}
    if (
        actions.filter(action => action === 'list_plugins').length !== 1
        || actions[actions.length - 1] !== 'list_plugins'
    ) {{
        throw new Error('plugin list was not fetched exactly once after recovery');
    }}
    if (applied !== 1 || cleared !== 1) {{
        throw new Error('verified plugin state was not applied exactly once');
    }}
    if (lifecycle.join(',') !== 'apply,clear') {{
        throw new Error('activity cleared before verified state was applied');
    }}
    if (
        activities.join(',') !== 'Restarting Domoticz...,Loading plugins...'
    ) {{
        throw new Error('successful restart exposed the wrong activity');
    }}
    if (notices.length !== 0) {{
        throw new Error('successful recovery emitted a failure notice');
    }}

    resetScenario();
    recoveryPhase = 'timed_out';
    await restartDomoticz();
    if (
        actions.join(',') !== 'restart_domoticz'
        || applied !== 0
        || cleared !== 0
        || activities.join(',') !== 'Restarting Domoticz...'
    ) {{
        throw new Error('timed-out recovery fetched or applied plugin state');
    }}
    if (
        notices.length !== 1
        || !notices[0].includes('did not finish restarting')
    ) {{
        throw new Error('timed-out recovery did not remain actionable inline');
    }}

    resetScenario();
    listMode = 'error';
    await restartDomoticz();
    if (
        actions.filter(action => action === 'list_plugins').length !== 1
        || applied !== 0
        || cleared !== 0
        || activities.join(',')
            !== 'Restarting Domoticz...,Loading plugins...'
    ) {{
        throw new Error('failed post-restart reload applied incomplete state');
    }}
    if (
        notices.length !== 1
        || !notices[0].includes('could not reload its plugin list')
    ) {{
        throw new Error('post-restart reload failure was not retained inline');
    }}
}})().catch(error => {{
    process.stderr.write(error.stack + '\\n');
    process.exit(1);
}});
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_busy_and_identity_layers_compose_in_both_directions():
    script = load_inline_script()
    for selector in (
        "button[data-action]",
        "manage-local-registry",
        "refresh-update-status",
        "restart-domoticz",
        "local-registry-reload",
        "local-registry-add",
        "local-registry-save",
        "local-registry-edit",
        "local-registry-delete",
    ):
        assert selector in script

    assert_ui_behavior(
        (
            "managerIdentityAllowsMutations",
            "managerActionIsMutating",
            "managerControlsAreBusy",
            "setManagerBusyDisabled",
            "updateManagerBusyControls",
            "setCommandBusy",
            "setManagerIdentityDisabled",
            "updateManagerMutationControls",
        ),
        setup="""
let isLoading = false;
let managerActivityActive = false;
let managerStatusDetail = '';
let localRegistryReadOnly = false;
let managerIdentityVerdict = {
    state: 'consistent',
    mutations_allowed: true,
    message: ''
};
const MANAGER_COMMAND_CONTROL_SELECTOR = 'button';
const controls = [];
const attributes = {};
const root = {
    setAttribute: (name, value) => { attributes[name] = value; },
    removeAttribute: name => { delete attributes[name]; }
};
const document = {
    querySelectorAll: () => controls,
    getElementById: id => id === 'pypluginstore-container' ? root : null
};
const makeControl = (
    disabled = false,
    title = 'original'
) => ({
    disabled,
    title,
    dataset: {action: 'update'}
});
""",
        exercise="""
const enabled = makeControl(false, 'Update available');
const preDisabled = makeControl(true, 'Restart required');
controls.push(enabled, preDisabled);
setCommandBusy(true);
if (
    !enabled.disabled
    || !preDisabled.disabled
    || attributes['aria-busy'] !== 'true'
) {
    throw new Error('busy state did not disable every command entry');
}
managerActivityActive = true;
setCommandBusy(false);
if (!enabled.disabled || attributes['aria-busy'] !== 'true') {
    throw new Error('command completion cleared caller-owned activity');
}
managerActivityActive = false;
updateManagerBusyControls();
if (enabled.disabled) {
    throw new Error('previously enabled control was not restored');
}
if (!preDisabled.disabled || preDisabled.title !== 'Restart required') {
    throw new Error('pre-disabled control state was overwritten');
}
if (Object.prototype.hasOwnProperty.call(attributes, 'aria-busy')) {
    throw new Error('aria-busy was not cleared');
}

controls.splice(0);
const identityFirst = makeControl();
controls.push(identityFirst);
managerIdentityVerdict = {
    state: 'frontend_stale',
    mutations_allowed: false,
    message: 'Hard-refresh.'
};
updateManagerMutationControls();
setCommandBusy(true);
managerIdentityVerdict = {
    state: 'consistent',
    mutations_allowed: true,
    message: ''
};
updateManagerMutationControls();
setCommandBusy(false);
if (identityFirst.disabled) {
    throw new Error('busy release resurrected an old identity block');
}

const busyFirst = makeControl();
controls.splice(0, controls.length, busyFirst);
setCommandBusy(true);
managerIdentityVerdict = {
    state: 'frontend_stale',
    mutations_allowed: false,
    message: 'Hard-refresh.'
};
updateManagerMutationControls();
setCommandBusy(false);
if (!busyFirst.disabled || busyFirst.title !== 'Hard-refresh.') {
    throw new Error('identity block was lost when busy state cleared');
}
managerIdentityVerdict = {
    state: 'consistent',
    mutations_allowed: true,
    message: ''
};
updateManagerMutationControls();
if (busyFirst.disabled || busyFirst.title !== 'original') {
    throw new Error('identity release did not restore the base control state');
}

setCommandBusy(true);
const dynamic = makeControl();
controls.push(dynamic);
updateManagerBusyControls();
if (!dynamic.disabled) {
    throw new Error('dynamically added command control stayed enabled');
}
setCommandBusy(false);
if (dynamic.disabled || dynamic.title !== 'original') {
    throw new Error('dynamic control was not safely restored');
}
""",
    )


@pytest.mark.parametrize(
    (
        "terminal_phase",
        "expected_detail",
        "expected_status",
        "expected_disabled",
        "max_attempts",
    ),
    [
        (
            "failed",
            "Update needs attention.",
            "Update needs attention.",
            False,
            1,
        ),
        (
            "stale_unknown",
            "Update needs attention.",
            "Update needs attention.",
            False,
            1,
        ),
        (
            "applied_needs_reload",
            "",
            "Restart Domoticz to finish the PyPluginStore update.",
            True,
            1,
        ),
        (
            "running",
            "PyPluginStore update status is still unknown. Refresh status to check again.",
            "PyPluginStore update status is still unknown. Refresh status to check again.",
            False,
            3,
        ),
    ],
)
def test_self_update_polling_terminates_with_actionable_status(
    terminal_phase,
    expected_detail,
    expected_status,
    expected_disabled,
    max_attempts,
):
    script = load_inline_script()
    functions = "\n".join(
        extract_js_function(script, function_name)
        for function_name in (
            "managerIdentityAllowsMutations",
            "managerActionIsMutating",
            "managerControlsAreBusy",
            "setManagerBusyDisabled",
            "updateManagerBusyControls",
            "setManagerIdentityDisabled",
            "updateManagerMutationControls",
            "renderManagerStatus",
            "setManagerActivity",
            "setManagerNotice",
            "clearManagerActivity",
            "selfUpdatePhase",
            "selfUpdateIsActive",
            "pollSelfUpdateStatus",
        )
    )
    node_script = f"""
const SELF_UPDATE_STATUS_POLL_INTERVAL_MS = 5000;
const SELF_UPDATE_STATUS_MAX_ATTEMPTS = 12;
const SELF_UPDATE_STATUS_DEADLINE_MS = 180000;
const RESTART_RECOVERY_REQUEST_TIMEOUT_MS = 2000;
const MANAGER_COMMAND_CONTROL_SELECTOR = 'button';
let isLoading = false;
let managerActivityActive = false;
let managerStatusDetail = '';
let localRegistryReadOnly = false;
let selfUpdateState = null;
const terminalPhase = {json.dumps(terminal_phase)};
let managerIdentityVerdict = terminalPhase === 'applied_needs_reload'
    ? {{
        state: 'restart_required',
        mutations_allowed: false,
        message: 'Restart required.'
    }}
    : {{
        state: 'consistent',
        mutations_allowed: true,
        message: ''
    }};
const control = {{
    disabled: false,
    title: 'Update',
    dataset: {{action: 'update'}}
}};
const root = {{
    setAttribute: () => {{}},
    removeAttribute: () => {{}}
}};
const statusMessages = [];
const delays = [];
let calls = 0;
const document = {{
    querySelectorAll: () => [control],
    getElementById: id => id === 'pypluginstore-container' ? root : null
}};
const setStatus = message => statusMessages.push(message);
const setTimeout = (resolve, delay) => {{
    delays.push(delay);
    resolve();
}};
const filterAndRender = () => {{}};
const sendCommand = async () => {{
    calls += 1;
    return {{
        status: 'success',
        self_update: {{
            phase: terminalPhase,
            message: 'Update needs attention.'
        }}
    }};
}};
const console = {{warn: () => {{}}}};

{functions}

(async () => {{
    setManagerActivity('Updating PyPluginStore...');
    if (!control.disabled || !managerActivityActive) {{
        throw new Error('test did not begin in an active state');
    }}
    await pollSelfUpdateStatus({max_attempts});
    if (
        calls !== {max_attempts}
        || delays.length !== {max_attempts}
        || delays.some(delay => delay !== 5000)
    ) {{
        throw new Error('self-update polling was not attempt bounded');
    }}
    if (managerActivityActive) {{
        throw new Error('terminal self-update left activity running');
    }}
    if (control.disabled !== {str(expected_disabled).lower()}) {{
        throw new Error('terminal self-update exposed the wrong control state');
    }}
    if (
        managerStatusDetail !== {json.dumps(expected_detail)}
        || statusMessages[statusMessages.length - 1]
            !== {json.dumps(expected_status)}
    ) {{
        throw new Error('terminal self-update exposed the wrong inline status');
    }}
}})().catch(error => {{
    process.stderr.write(error.stack + '\\n');
    process.exit(1);
}});
"""

    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def test_manager_status_is_an_accessible_polite_live_region():
    html = (REPO_ROOT / "pypluginstore.html").read_text(encoding="utf-8")
    status_start = html.index('<div id="pypluginstore-status"')
    status_end = html.index(">", status_start)
    status_tag = html[status_start:status_end]

    assert 'role="status"' in status_tag
    assert 'aria-live="polite"' in status_tag
    assert 'aria-atomic="true"' in status_tag


def assert_ui_behavior(function_names, *, setup, exercise):
    """Run selected inline UI functions in a bounded Node behavior harness."""
    script = load_inline_script()
    functions = "\n".join(
        extract_js_function(script, function_name)
        for function_name in function_names
    )
    node_script = "\n".join(
        (
            setup,
            functions,
            "(async () => {",
            exercise,
            "})().catch(error => {",
            "    process.stderr.write(error.stack + '\\n');",
            "    process.exit(1);",
            "});",
        )
    )
    result = run_node_script(node_script)
    assert result.returncode == 0, result.stderr


def run_node_script(source, *, check_syntax=False, timeout=15):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    with tempfile.NamedTemporaryFile(
        suffix=".js",
        mode="w",
        delete=False,
        encoding="utf-8",
    ) as script_file:
        script_file.write(source)
        script_path = script_file.name
    try:
        command = [node]
        if check_syntax:
            command.append("--check")
        command.append(script_path)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    finally:
        os.remove(script_path)


def extract_frozen_json_constant(script, constant_name):
    prefix = "const " + constant_name + " = Object.freeze("
    start = script.index(prefix) + len(prefix)
    serialized = script[start:].lstrip()
    value, end = json.JSONDecoder().raw_decode(serialized)
    assert serialized[end:].lstrip().startswith(");")
    assert isinstance(value, dict)
    return value


def load_inline_script():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    parser = InlineScriptParser()
    parser.feed(html)
    assert parser.scripts, "pypluginstore.html does not contain an inline script"
    return parser.scripts[0]


def extract_css_rule(html, selector):
    start = html.index(selector)
    brace_start = html.index("{", start)
    brace_end = html.index("}", brace_start)
    return html[brace_start + 1:brace_end]


def extract_js_function(script, function_name):
    start = script.index(f"function {function_name}")
    async_prefix = "async "
    prefix_start = start - len(async_prefix)
    if prefix_start >= 0 and script[prefix_start:start] == async_prefix:
        start = prefix_start
    arguments_start = script.index("(", start)
    argument_depth = 0
    arguments_end = None
    for index in range(arguments_start, len(script)):
        if script[index] == "(":
            argument_depth += 1
        elif script[index] == ")":
            argument_depth -= 1
            if argument_depth == 0:
                arguments_end = index
                break
    if arguments_end is None:
        raise AssertionError(function_name + " arguments were not closed")
    brace_start = script.index("{", arguments_end)
    depth = 0
    for index in range(brace_start, len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]

    raise AssertionError(f"Function {function_name} was not closed")
