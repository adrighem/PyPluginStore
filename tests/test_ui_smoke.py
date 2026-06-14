from html.parser import HTMLParser
import json
import shutil
import subprocess

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
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    script = load_inline_script()

    result = subprocess.run(
        [node, "--check", "-"],
        input=script,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_plugin_display_name_strips_domoticz_affixes():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

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

    result = subprocess.run(
        [node, "-"],
        input=node_script,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_update_buttons_keep_shared_and_state_specific_classes():
    html = (REPO_ROOT / "pypluginstore.html").read_text()

    assert ".btn-update {" in html
    assert ".btn-update-available {" in html
    assert ".btn-update-current {" in html
    assert "btn-update btn-update-available" in html
    assert "btn-update btn-update-current" in html


def load_inline_script():
    html = (REPO_ROOT / "pypluginstore.html").read_text()
    parser = InlineScriptParser()
    parser.feed(html)
    assert parser.scripts, "pypluginstore.html does not contain an inline script"
    return parser.scripts[0]


def extract_js_function(script, function_name):
    start = script.index(f"function {function_name}")
    brace_start = script.index("{", start)
    depth = 0
    for index in range(brace_start, len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]

    raise AssertionError(f"Function {function_name} was not closed")
