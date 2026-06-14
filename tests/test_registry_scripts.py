import json
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT, load_module_from_path


VALID_ENTRY = ["owner", "repo", "description", "main"]


@pytest.fixture
def validate_plugins_module():
    return load_module_from_path(
        "validate_plugins_under_test",
        REPO_ROOT / ".github" / "scripts" / "validate_plugins.py",
    )


@pytest.fixture
def scan_plugins_module():
    return load_module_from_path(
        "scan_github_plugins_under_test",
        REPO_ROOT / ".github" / "scripts" / "scan_github_plugins.py",
    )


def test_validate_registry_entry_accepts_normal_entry(validate_plugins_module):
    validate_plugins_module.validate_registry_entry("NormalPlugin", VALID_ENTRY)


@pytest.mark.parametrize(
    ("key", "entry"),
    [
        ("", VALID_ENTRY),
        (".github", VALID_ENTRY),
        ("nested/plugin", VALID_ENTRY),
        ("nested\\plugin", VALID_ENTRY),
        ("Plugin", ["owner", ".github", "description", "main"]),
        ("Plugin", ["owner", "nested/repo", "description", "main"]),
        ("Plugin", ["owner", "repo", "", "main"]),
        ("Plugin", ["owner", "repo", "description"]),
        ("Plugin", {"owner": "owner", "repo": "repo"}),
    ],
)
def test_validate_registry_entry_rejects_invalid_entry(validate_plugins_module, key, entry):
    with pytest.raises(ValueError):
        validate_plugins_module.validate_registry_entry(key, entry)


def test_validate_repository_uses_argument_list_and_disables_prompts(validate_plugins_module, monkeypatch):
    calls = []

    def fake_run(cmd, env, capture_output, text):
        calls.append({
            "cmd": cmd,
            "env": env,
            "capture_output": capture_output,
            "text": text,
        })
        return SimpleNamespace(returncode=0, stdout="abc123\trefs/heads/main\n", stderr="")

    monkeypatch.setattr(validate_plugins_module.subprocess, "run", fake_run)

    assert validate_plugins_module.validate_repository("owner", "repo", "main") is True
    assert calls[0]["cmd"] == [
        "git",
        "ls-remote",
        "--heads",
        "https://github.com/owner/repo",
        "main",
    ]
    assert calls[0]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert calls[0]["capture_output"] is True
    assert calls[0]["text"] is True


def test_validate_repository_requires_matching_branch_output(validate_plugins_module, monkeypatch):
    def fake_run(cmd, env, capture_output, text):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(validate_plugins_module.subprocess, "run", fake_run)

    assert validate_plugins_module.validate_repository("owner", "empty-repo", "main") is False


@pytest.mark.parametrize(
    ("repo_name", "expected"),
    [
        ("domoticz-plugin", True),
        ("", False),
        (None, False),
        (".github", False),
        ("nested/repo", False),
        ("nested\\repo", False),
    ],
)
def test_scanner_validates_plugin_repository_names(scan_plugins_module, repo_name, expected):
    assert scan_plugins_module.is_valid_plugin_repo(repo_name) is expected


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ({"archived": True, "size": 10}, "Repo archived"),
        ({"disabled": True, "size": 10}, "Repo disabled"),
        ({"size": 0}, "Repo empty"),
        ({"size": "0"}, "Repo empty"),
        ({"size": 1}, None),
        ({}, None),
    ],
)
def test_scanner_explains_unscannable_repositories(scan_plugins_module, repo, expected):
    assert scan_plugins_module.get_repo_skip_reason(repo) == expected


def test_scanner_blocks_explicit_repositories(scan_plugins_module):
    assert scan_plugins_module.get_repo_block_reason("domoticz", "domoticz") == "Repo blocklisted"
    assert scan_plugins_module.get_repo_block_reason("owner", "repo") is None


def test_scanner_removes_empty_existing_repo_and_does_not_readd(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "Domoticz_integration": [
            "cipesokram",
            "Domoticz_integration",
            "Arduino to domoticz integration",
            "master",
        ],
    }))
    update_times_file.write_text(json.dumps({
        "Domoticz_integration": "2018-03-02T13:38:59Z",
    }))

    empty_repo = {
        "archived": False,
        "disabled": False,
        "size": 0,
        "full_name": "cipesokram/Domoticz_integration",
        "owner": {"login": "cipesokram"},
        "name": "Domoticz_integration",
        "description": "Arduino to domoticz integration",
        "default_branch": "master",
        "pushed_at": "2018-03-02T13:38:59Z",
    }

    monkeypatch.setattr(scan_plugins_module, "REGISTRY_FILE", str(registry_file))
    monkeypatch.setattr(scan_plugins_module, "UPDATE_TIMES_FILE", str(update_times_file))
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", lambda owner, repo: empty_repo)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [empty_repo])
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = json.loads(registry_file.read_text())
    update_times = json.loads(update_times_file.read_text())

    assert "Domoticz_integration" not in registry
    assert "Domoticz_integration" not in update_times


def test_scanner_removes_blocklisted_existing_repo_and_does_not_readd(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "domoticz": [
            "domoticz",
            "domoticz",
            "Free open source home automation system",
            "development",
        ],
    }))
    update_times_file.write_text(json.dumps({
        "domoticz": "2026-06-13T09:52:48Z",
    }))

    domoticz_repo = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "domoticz/domoticz",
        "owner": {"login": "domoticz"},
        "name": "domoticz",
        "description": "Free open source home automation system",
        "default_branch": "development",
        "pushed_at": "2026-06-13T09:52:48Z",
    }

    def fail_get_repo_info(owner, repo):
        raise AssertionError("blocklisted repositories should not be fetched")

    monkeypatch.setattr(scan_plugins_module, "REGISTRY_FILE", str(registry_file))
    monkeypatch.setattr(scan_plugins_module, "UPDATE_TIMES_FILE", str(update_times_file))
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", fail_get_repo_info)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [domoticz_repo])
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = json.loads(registry_file.read_text())
    update_times = json.loads(update_times_file.read_text())

    assert "domoticz" not in registry
    assert "domoticz" not in update_times
