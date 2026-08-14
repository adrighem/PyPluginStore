import json
import os
import sys
import pytest
from pathlib import Path

from conftest import REPO_ROOT, load_module_from_path

def test_summarize_registry_changes(tmp_path, monkeypatch, capsys):
    script_path = REPO_ROOT / ".github/scripts/summarize_registry_changes.py"
    summarize_mod = load_module_from_path("summarize_registry_changes", str(script_path))

    local_reg = tmp_path / "registry.json"
    local_index = tmp_path / "release_index.json"

    monkeypatch.setattr(summarize_mod, "REGISTRY_FILE_PATH", str(local_reg))
    monkeypatch.setattr(summarize_mod, "RELEASE_INDEX_FILE_PATH", str(local_index))

    # Write mock local files
    local_reg.write_text(json.dumps({
        "schema_version": 2,
        "packages": [
            {
                "package_id": "PkgA",
                "domoticz_key": "KeyA",
                "description": "New Desc",
                "repository": {"url": "https://github.com/user/a", "branch": "main"},
                "platforms": ["linux"],
                "delivery": {"preferred": "git", "git_supported": True}
            },
            {
                "package_id": "PkgB",
                "domoticz_key": "KeyB",
                "description": "B",
                "repository": {"url": "https://github.com/user/b", "branch": "main"},
                "platforms": ["linux"],
                "delivery": {"preferred": "git", "git_supported": True}
            }
        ]
    }))

    local_index.write_text(json.dumps({
        "sequence": 12,
        "releases": [
            {
                "release_id": "github:user/a:v1.0.0",
                "package_id": "PkgA",
                "version": "1.0.0",
                "tag": "v1.0.0"
            }
        ],
        "tombstones": []
    }))

    # Mock load_git_file to return original files
    orig_registry_data = json.dumps({
        "schema_version": 2,
        "packages": [
            {
                "package_id": "PkgA",
                "domoticz_key": "KeyA",
                "description": "Old Desc",
                "repository": {"url": "https://github.com/user/a", "branch": "main"},
                "platforms": ["linux"],
                "delivery": {"preferred": "git", "git_supported": True}
            },
            {
                "package_id": "PkgC",
                "domoticz_key": "KeyC",
                "description": "C",
                "repository": {"url": "https://github.com/user/c", "branch": "main"},
                "platforms": ["linux"],
                "delivery": {"preferred": "git", "git_supported": True}
            }
        ]
    }).encode("utf-8")

    orig_index_data = json.dumps({
        "sequence": 11,
        "releases": [],
        "tombstones": []
    }).encode("utf-8")

    def mock_load_git_file(filepath):
        if filepath == "registry.json":
            return orig_registry_data
        if filepath == "release_index.json":
            return orig_index_data
        return None

    monkeypatch.setattr(summarize_mod, "load_git_file", mock_load_git_file)

    # Run the main function
    summarize_mod.main()

    captured = capsys.readouterr()
    output = captured.out

    assert "Release index sequence" in output
    assert "`11` -> `12`" in output
    assert "Active releases" in output
    assert "Packages" in output
    assert "New Packages Added" in output
    assert "PkgB" in output
    assert "Packages Removed" in output
    assert "PkgC" in output
    assert "Packages Updated" in output
    assert "PkgA" in output
    assert "description" in output
