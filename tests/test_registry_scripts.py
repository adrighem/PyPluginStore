import json
import copy
import urllib.parse
import urllib.error
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT, load_module_from_path


VALID_ENTRY = ["owner", "repo", "description", "main"]
STABLE_TAG_PATTERN = r"^v?[0-9]+(?:\.[0-9]+){1,3}$"
DOTTED_V_TAG_PATTERN = r"^v\.[0-9]+(?:\.[0-9]+){1,3}$"


def registry_package(package_id, entry):
    if isinstance(entry, list):
        owner, repository, description, branch = entry[:4]
        platforms = entry[5] if len(entry) > 5 else []
        delivery = None
        annotations = None
    else:
        owner = entry["owner"]
        repository = entry["repository"]
        description = entry["description"]
        branch = entry["branch"]
        platforms = entry.get("platforms", entry.get("platform", []))
        delivery = copy.deepcopy(entry.get("delivery"))
        annotations = {
            key: copy.deepcopy(value)
            for key, value in entry.items()
            if key
            not in {
                "owner",
                "repository",
                "description",
                "branch",
                "updated_at",
                "platforms",
                "platform",
                "delivery",
            }
        }
    first, separator, remainder = owner.partition("/")
    if first.lower() in {"github.com", "gitlab.com", "codeberg.org"} and separator:
        repository_url = "https://" + owner + "/" + repository
    elif owner.startswith("https://"):
        repository_url = owner.rstrip("/") + "/" + repository
    else:
        repository_url = "https://github.com/" + owner + "/" + repository
    host = urllib.parse.urlsplit(repository_url).hostname
    provider = {
        "github.com": "github",
        "gitlab.com": "gitlab",
        "codeberg.org": "codeberg",
    }[host]
    if delivery is None:
        delivery = {
            "preferred": "release_if_indexed",
            "git_supported": True,
            "release": {
                "provider": provider,
                "channel": "stable",
                "tag_pattern": STABLE_TAG_PATTERN,
                "artifact": "source_zip",
                "source_path": ".",
                "mutable_paths": [],
            },
        }
    else:
        delivery.pop("schema_version", None)
        if delivery.get("release", {}).get("provider") == "forgejo":
            delivery["release"]["provider"] = "codeberg"
    package = {
        "package_id": package_id,
        "domoticz_key": package_id.upper(),
        "description": description,
        "repository": {"url": repository_url, "branch": branch},
        "platforms": list(platforms or []),
        "delivery": delivery,
    }
    if annotations:
        package["annotations"] = annotations
    return package


def registry_v2(legacy_registry):
    return {
        "schema_version": 2,
        "packages": [
            registry_package(package_id, entry)
            for package_id, entry in legacy_registry.items()
            if package_id != "Idle"
        ],
    }


def update_times_v2(update_times):
    return {
        "schema_version": 2,
        "updates": [
            {"package_id": package_id, "updated_at": updated_at.split(".")[0] + "Z" if "." in updated_at else updated_at}
            for package_id, updated_at in update_times.items()
        ],
    }


def platform_metadata_v2(metadata):
    entries = metadata.get("entries", {})
    return {
        "schema_version": 2,
        "detections": [
            {"package_id": package_id, **entry}
            for package_id, entry in entries.items()
        ],
    }


def upgrade_fixture_files(registry_file, update_times_file, metadata_file):
    if registry_file.exists():
        registry = json.loads(registry_file.read_text())
        if "schema_version" not in registry:
            registry_file.write_text(json.dumps(registry_v2(registry)))
    if update_times_file.exists():
        updates = json.loads(update_times_file.read_text())
        if "schema_version" not in updates:
            update_times_file.write_text(json.dumps(update_times_v2(updates)))
    if metadata_file.exists():
        metadata = json.loads(metadata_file.read_text())
        if "schema_version" not in metadata:
            metadata_file.write_text(json.dumps(platform_metadata_v2(metadata)))


def saved_packages(path):
    return {
        package["package_id"]: package
        for package in json.loads(path.read_text())["packages"]
    }


def saved_update_times(path):
    return {
        update["package_id"]: update["updated_at"]
        for update in json.loads(path.read_text())["updates"]
    }


def saved_platform_entries(path):
    return {
        detection["package_id"]: {
            key: value
            for key, value in detection.items()
            if key != "package_id"
        }
        for detection in json.loads(path.read_text())["detections"]
    }


def mark_repo_certified(module, repo, domoticz_key="TEST"):
    repo[module.ROOT_PLUGIN_CHECKED_FIELD] = True
    repo[module.ROOT_PLUGIN_IDENTITY_FIELD] = {
        "domoticz_key": domoticz_key,
        "plugin_py_sha256": "a" * 64,
    }


def cleanup_release_index(active_repositories=(), tombstoned_repositories=()):
    commit = "b" * 40
    releases = []
    for package_id, repository_identity in active_repositories:
        releases.append({
            "package_id": package_id,
            "certified_identity": {
                "domoticz_key": package_id.upper(),
                "plugin_py_sha256": "c" * 64,
            },
            "revision": 1,
            "release_id": f"github:{repository_identity}:v1.0.0",
            "supersedes": [],
            "provider": "github",
            "repository_identity": repository_identity,
            "version": "1.0.0",
            "tag": "v1.0.0",
            "released_at": "2026-07-01T00:00:00Z",
            "commit": commit,
            "artifact": {
                "kind": "source_zip",
                "provenance": "forge_source_archive",
                "migration": {
                    "mode": "automatic",
                    "evidence": "commit_source_archive",
                },
                "url": "https://api.github.com/repos/owner/plugin/zipball/" + commit,
                "sha256": "d" * 64,
                "size": 1,
                "tree_sha256": "e" * 64,
                "root_prefix": "owner-plugin-bbbbbbb",
                "source_path": ".",
            },
        })
    tombstones = [
        {
            "package_id": package_id,
            "repository_identity": repository_identity,
            "last_revision": 1,
            "release_id": f"github:{repository_identity}:v0.9.0",
            "reason": "Previously removed.",
            "removed_at": "2026-07-01T00:00:00Z",
        }
        for package_id, repository_identity in tombstoned_repositories
    ]
    return {
        "schema_version": 2,
        "sequence": 1,
        "generated_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-07-08T00:00:00Z",
        "registry_sha256": "f" * 64,
        "releases": releases,
        "tombstones": tombstones,
    }


def patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file):
    upgrade_fixture_files(registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "REGISTRY_FILE", str(registry_file))
    monkeypatch.setattr(scan_plugins_module, "UPDATE_TIMES_FILE", str(update_times_file))
    monkeypatch.setattr(scan_plugins_module, "PLATFORM_METADATA_FILE", str(metadata_file))


def platform_decision(platforms, confidence="medium", evidence_class="test"):
    return SimpleNamespace(
        platforms=platforms,
        confidence=confidence,
        evidence_class=evidence_class,
        linux_score=0,
        windows_score=0,
        both_score=0,
        reasons=[],
    )


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


@pytest.fixture
def cleanup_registry_module():
    return load_module_from_path(
        "cleanup_registry_under_test",
        REPO_ROOT / ".github" / "scripts" / "cleanup_registry.py",
    )


@pytest.fixture
def platform_detector_module():
    return load_module_from_path(
        "detect_plugin_platforms_under_test",
        REPO_ROOT / ".github" / "scripts" / "detect_plugin_platforms.py",
    )


def test_validate_registry_entry_accepts_normal_entry(validate_plugins_module):
    validate_plugins_module.validate_registry_entry("NormalPlugin", VALID_ENTRY)


def test_validate_registry_entry_accepts_platform_metadata(validate_plugins_module):
    validate_plugins_module.validate_registry_entry(
        "PlatformPlugin",
        ["owner", "repo", "description", "main", "", ["linux", "windows"]],
    )


def test_validate_platform_metadata_accepts_matching_sidecar(validate_plugins_module, tmp_path, monkeypatch):
    metadata_file = tmp_path / "platform_detection.json"
    metadata_file.write_text(json.dumps(platform_metadata_v2({
        "version": 1,
        "entries": {
            "PlatformPlugin": {
                "identity": "github.com/owner/repo@main",
                "owner": "owner",
                "repository": "repo",
                "branch": "main",
                "registry_platforms": ["linux", "windows"],
                "source": "legacy_detected",
                "confidence": "unknown",
                "evidence_class": "legacy",
                "reviewed": False,
            },
        },
    })))
    monkeypatch.setattr(validate_plugins_module, "PLATFORM_METADATA_FILE_PATH", str(metadata_file))

    validate_plugins_module.validate_platform_metadata({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "PlatformPlugin": ["owner", "repo", "description", "main", "", ["linux", "windows"]],
    })


def test_validate_platform_metadata_rejects_stale_or_mismatched_sidecar(validate_plugins_module, tmp_path, monkeypatch):
    metadata_file = tmp_path / "platform_detection.json"
    metadata_file.write_text(json.dumps(platform_metadata_v2({
        "version": 1,
        "entries": {
            "PlatformPlugin": {
                "registry_platforms": ["windows"],
                "source": "legacy_detected",
                "confidence": "unknown",
                "reviewed": False,
            },
            "MissingPlugin": {
                "registry_platforms": [],
                "source": "unknown",
                "confidence": "unknown",
                "reviewed": False,
            },
        },
    })))
    monkeypatch.setattr(validate_plugins_module, "PLATFORM_METADATA_FILE_PATH", str(metadata_file))

    with pytest.raises(ValueError):
        validate_plugins_module.validate_platform_metadata({
            "PlatformPlugin": ["owner", "repo", "description", "main", "", ["linux", "windows"]],
        })


def test_validate_update_times_rejects_stale_sidecar(validate_plugins_module, tmp_path, monkeypatch):
    update_times_file = tmp_path / "update_times.json"
    update_times_file.write_text(json.dumps(update_times_v2({
        "Plugin": "2026-06-14T15:10:03Z",
        "OldPlugin": "2026-04-20T17:51:05Z",
    })))
    monkeypatch.setattr(validate_plugins_module, "UPDATE_TIMES_FILE_PATH", str(update_times_file))

    with pytest.raises(ValueError, match="OldPlugin"):
        validate_plugins_module.validate_update_times({
            "Idle": ["Idle", "Idle", "Idle", "master"],
            "Plugin": ["owner", "repo", "description", "main"],
        })


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
        ("Plugin", ["owner", "repo", "description", "main", "", []]),
        ("Plugin", ["owner", "repo", "description", "main", "", ["macos"]]),
        ("Plugin", ["owner", "repo", "description", "main", "", {"platform": "linux"}]),
    ],
)
def test_validate_registry_entry_rejects_invalid_entry(validate_plugins_module, key, entry):
    with pytest.raises(ValueError):
        validate_plugins_module.validate_registry_entry(key, entry)


def test_validate_repository_uses_argument_list_disables_prompts_and_sets_timeout(validate_plugins_module, monkeypatch):
    calls = []

    def fake_run(cmd, env, capture_output, text, timeout):
        calls.append({
            "cmd": cmd,
            "env": env,
            "capture_output": capture_output,
            "text": text,
            "timeout": timeout,
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
    assert calls[0]["timeout"] == validate_plugins_module.GIT_REMOTE_TIMEOUT_SECONDS


def test_validate_repository_uses_supported_host_urls(validate_plugins_module, monkeypatch):
    calls = []

    def fake_run(cmd, env, capture_output, text, timeout):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="abc123\trefs/heads/main\n", stderr="")

    monkeypatch.setattr(validate_plugins_module.subprocess, "run", fake_run)

    assert validate_plugins_module.validate_repository(
        "codeberg.org/Hoog",
        "Domoticz-Stromer-plugin",
        "main",
    ) is True
    assert validate_plugins_module.validate_repository(
        "gitlab.com/r.boeters",
        "DomoticzSabNZBDPlugin",
        "master",
    ) is True
    assert calls == [
        [
            "git",
            "ls-remote",
            "--heads",
            "https://codeberg.org/Hoog/Domoticz-Stromer-plugin",
            "main",
        ],
        [
            "git",
            "ls-remote",
            "--heads",
            "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin",
            "master",
        ],
    ]


def test_validate_repository_requires_matching_branch_output(validate_plugins_module, monkeypatch):
    def fake_run(cmd, env, capture_output, text, timeout):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(validate_plugins_module.subprocess, "run", fake_run)

    assert validate_plugins_module.validate_repository("owner", "empty-repo", "main") is False


def test_validate_repository_returns_false_on_timeout(validate_plugins_module, monkeypatch):
    def fake_run(cmd, env, capture_output, text, timeout):
        raise validate_plugins_module.subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(validate_plugins_module.subprocess, "run", fake_run)

    assert validate_plugins_module.validate_repository("owner", "slow-repo", "main") is False


def test_validate_root_plugin_py_accepts_present_file_on_supported_hosts(validate_plugins_module):
    fetched_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return b'"""<plugin key="STROMER" name="Stromer"></plugin>"""\n'

    def fake_urlopen(request, timeout=0):
        fetched_urls.append(request.full_url)
        return FakeResponse()

    assert validate_plugins_module.validate_root_plugin_py(
        "Domoticz-Stromer-plugin",
        "codeberg.org/Hoog",
        "Domoticz-Stromer-plugin",
        "main",
        opener=fake_urlopen,
    ) is True

    assert fetched_urls == [
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/raw/branch/main/plugin.py",
    ]


def test_validate_root_plugin_py_retries_transient_errors(validate_plugins_module):
    attempts = []
    delays = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return b'"""<plugin key="RETRY" name="Retry"></plugin>"""\n'

    def fake_urlopen(request, timeout=0):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise TimeoutError("read timed out")
        return FakeResponse()

    assert validate_plugins_module.validate_root_plugin_py(
        "RetryPlugin",
        "owner",
        "repo",
        "main",
        opener=fake_urlopen,
        sleeper=delays.append,
    ) is True
    assert len(attempts) == 2
    assert delays == [validate_plugins_module.ROOT_PLUGIN_RETRY_DELAY_SECONDS]


def test_validate_root_plugin_py_rejects_mismatched_domoticz_identity(
    validate_plugins_module,
):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return b'"""<plugin key="ACTUAL" name="Plugin"></plugin>"""\n'

    assert validate_plugins_module.validate_root_plugin_py(
        "PackageId",
        "owner",
        "repo",
        "main",
        domoticz_key="EXPECTED",
        opener=lambda *_args, **_kwargs: FakeResponse(),
    ) is False


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"", None),
        (None, urllib.error.HTTPError("https://example.invalid/plugin.py", 404, "Not Found", {}, None)),
    ],
)
def test_validate_root_plugin_py_rejects_missing_or_empty_file(validate_plugins_module, content, error):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return content

    def fake_urlopen(request, timeout=0):
        if error is not None:
            raise error
        return FakeResponse()

    assert validate_plugins_module.validate_root_plugin_py(
        "BrokenPlugin",
        "owner",
        "repo",
        "main",
        opener=fake_urlopen,
    ) is False


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


@pytest.mark.parametrize(
    ("owner", "repository"),
    [
        ("domoticz", "domoticz"),
        ("galadril", "Domoticz-Python-Plugin-Template"),
        ("GaLaDrIl", "DOMOTICZ-PYTHON-PLUGIN-TEMPLATE"),
    ],
)
def test_scanner_blocks_explicit_repositories(
    scan_plugins_module,
    owner,
    repository,
):
    assert (
        scan_plugins_module.get_repo_block_reason(owner, repository)
        == "Repo blocklisted"
    )


def test_scanner_blocklist_matches_repository_identity_not_name(
    scan_plugins_module,
):
    assert (
        scan_plugins_module.get_repo_block_reason(
            "another-owner",
            "Domoticz-Python-Plugin-Template",
        )
        is None
    )


def test_scanner_normalizes_supported_host_registry_entries(scan_plugins_module):
    assert scan_plugins_module.get_registry_owner("github.com", "owner") == "owner"
    assert scan_plugins_module.get_registry_owner("codeberg.org", "Hoog") == "codeberg.org/Hoog"
    assert scan_plugins_module.get_repository_identity("gitlab.com/r.boeters", "DomoticzSabNZBDPlugin") == (
        "gitlab.com/r.boeters/domoticzsabnzbdplugin"
    )


def test_scanner_infers_dotted_v_pattern_from_latest_stable_release(
    scan_plugins_module,
):
    releases = [
        {
            "tag_name": "v.3.2.0-beta",
            "published_at": "2023-12-23T13:29:38Z",
            "draft": False,
            "prerelease": True,
        },
        {
            "tag_name": "v.3.1.0",
            "published_at": "2022-08-27T10:43:53Z",
            "draft": False,
            "prerelease": False,
        },
        {
            "tag_name": "v0.0.15",
            "published_at": "2018-11-27T21:02:11Z",
            "draft": False,
            "prerelease": False,
        },
    ]

    assert scan_plugins_module.infer_stable_tag_pattern(releases) == (
        DOTTED_V_TAG_PATTERN
    )


def test_scanner_preserves_custom_release_tag_policy(scan_plugins_module):
    custom_pattern = r"^release-[0-9]+\.[0-9]+\.[0-9]+$"
    entry = registry_package("Plugin", {
        "owner": "owner",
        "repository": "repo",
        "description": "description",
        "branch": "main",
        "platforms": ["linux"],
        "delivery": {
            "schema_version": 1,
            "preferred": "release_if_indexed",
            "git_supported": True,
            "release": {
                "provider": "github",
                "channel": "stable",
                "tag_pattern": custom_pattern,
                "artifact": "source_zip",
                "source_path": ".",
                "mutable_paths": [],
            },
        },
    })
    record = scan_plugins_module.RegistryRecord.from_entry("Plugin", entry)
    repo = {
        scan_plugins_module.RELEASE_TAG_PATTERN_CHECKED_FIELD: True,
        scan_plugins_module.RELEASE_TAG_PATTERN_FIELD: DOTTED_V_TAG_PATTERN,
    }

    assert scan_plugins_module.release_tag_pattern_update(record, repo) == ""
    assert record.delivery.release["tag_pattern"] == custom_pattern


def test_scanner_checks_root_plugin_py_on_supported_hosts(scan_plugins_module, monkeypatch):
    fetched_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return b'"""<plugin key="Test" name="Test"></plugin>"""'

    def fake_urlopen(request, timeout=0):
        fetched_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(scan_plugins_module.urllib.request, "urlopen", fake_urlopen)

    assert scan_plugins_module.has_root_plugin_py({
        "owner": {"login": "owner"},
        "name": "repo",
        "default_branch": "main",
    }) is True
    assert scan_plugins_module.has_root_plugin_py({
        "host": "codeberg.org",
        "owner": {"login": "Hoog"},
        "name": "Domoticz-Stromer-plugin",
        "default_branch": "main",
    }) is True
    assert scan_plugins_module.has_root_plugin_py({
        "host": "gitlab.com",
        "owner": {"login": "r.boeters"},
        "name": "DomoticzSabNZBDPlugin",
        "default_branch": "master",
    }) is True
    assert fetched_urls == [
        "https://raw.githubusercontent.com/owner/repo/main/plugin.py",
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/raw/branch/main/plugin.py",
        "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/raw/master/plugin.py",
    ]


def test_scanner_filters_github_search_results_without_root_plugin_py(scan_plugins_module, monkeypatch):
    fetched_plugin_urls = []
    good_repo = {
        "full_name": "owner/good-plugin",
        "owner": {"login": "owner"},
        "name": "good-plugin",
        "default_branch": "main",
    }
    bad_repo = {
        "full_name": "owner/wiki",
        "owner": {"login": "owner"},
        "name": "wiki",
        "default_branch": "main",
    }

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return self.content

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if url.startswith("https://api.github.com/search/repositories"):
            return FakeResponse(
                json.dumps({"items": [good_repo, bad_repo]}).encode()
            )
        fetched_plugin_urls.append(url)
        if url.endswith("/good-plugin/releases?per_page=100"):
            return FakeResponse(json.dumps([{
                "tag_name": "v.1.2.3",
                "published_at": "2026-07-01T12:00:00Z",
                "draft": False,
                "prerelease": False,
            }]).encode())
        if url.endswith("/good-plugin/main/plugin.py"):
            return FakeResponse(
                b'"""<plugin key="GOOD" name="Good"></plugin>"""\n'
            )
        if url.endswith("/wiki/main/plugin.py"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(scan_plugins_module.urllib.request, "urlopen", fake_urlopen)

    results = scan_plugins_module.search_github()
    assert len(results) == 1
    assert results[0]["full_name"] == good_repo["full_name"]
    assert fetched_plugin_urls == [
        "https://raw.githubusercontent.com/owner/good-plugin/main/plugin.py",
        "https://api.github.com/repos/owner/good-plugin/releases?per_page=100",
        "https://raw.githubusercontent.com/owner/wiki/main/plugin.py",
    ]
    assert results[0][scan_plugins_module.RELEASE_TAG_PATTERN_FIELD] == (
        DOTTED_V_TAG_PATTERN
    )
    assert results[0][scan_plugins_module.ROOT_PLUGIN_CHECKED_FIELD] is True


def test_cleanup_registry_builds_raw_plugin_urls_for_supported_hosts(cleanup_registry_module):
    assert cleanup_registry_module.raw_plugin_url("owner", "repo", "main") == (
        "https://raw.githubusercontent.com/owner/repo/main/plugin.py"
    )
    assert cleanup_registry_module.raw_plugin_url(
        "codeberg.org/Hoog",
        "Domoticz-Stromer-plugin",
        "main",
    ) == "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/raw/branch/main/plugin.py"
    assert cleanup_registry_module.raw_plugin_url(
        "gitlab.com/r.boeters",
        "DomoticzSabNZBDPlugin",
        "master",
    ) == "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/raw/master/plugin.py"


def test_cleanup_registry_dry_run_does_not_remove_entries(cleanup_registry_module, tmp_path):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry = {
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "Good": ["owner", "present", "description", "main"],
        "Missing": ["owner", "missing", "description", "main"],
        "Empty": ["owner", "empty", "description", "main"],
        "ServerError": ["owner", "server-error", "description", "main"],
    }
    update_times = {
        "Good": "2026-06-14T15:10:03Z",
        "Missing": "2026-06-14T15:10:03Z",
        "Empty": "2026-06-14T15:10:03Z",
        "ServerError": "2026-06-14T15:10:03Z",
    }
    metadata = {
        "version": 1,
        "entries": {
            key: {"registry_platforms": ["linux"]}
            for key in ("Good", "Missing", "Empty", "ServerError")
        },
    }
    registry_file.write_text(json.dumps(registry))
    update_times_file.write_text(json.dumps(update_times))
    metadata_file.write_text(json.dumps(metadata))
    upgrade_fixture_files(registry_file, update_times_file, metadata_file)
    tombstone_requests_file = tmp_path / "tombstone-requests.json"
    tombstone_requests_file.write_text("sentinel", encoding="utf-8")

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            if self.content.strip():
                return (
                    b'"""<plugin key="TEST" name="Test"></plugin>"""\n'
                )
            return self.content

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if "/present/" in url:
            return FakeResponse(b"import Domoticz\n")
        if "/missing/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/empty/" in url:
            return FakeResponse(b"")
        if "/server-error/" in url:
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)
        raise AssertionError(f"Unexpected URL: {url}")

    stats = cleanup_registry_module.cleanup_registry_files(
        str(registry_file),
        str(update_times_file),
        str(metadata_file),
        apply_changes=False,
        sleep_seconds=0,
        opener=fake_urlopen,
        release_index_file=str(tmp_path / "not-read-in-dry-run.json"),
        tombstone_requests_output=str(tombstone_requests_file),
    )

    assert stats == {
        "checked": 4,
        "present": 1,
        "would_remove": 2,
        "removed": 0,
        "errors": 1,
    }
    assert set(saved_packages(registry_file)) == {
        "Good",
        "Missing",
        "Empty",
        "ServerError",
    }
    assert saved_update_times(update_times_file) == update_times
    assert set(saved_platform_entries(metadata_file)) == set(
        metadata["entries"]
    )
    assert tombstone_requests_file.read_text(encoding="utf-8") == "sentinel"


def test_cleanup_registry_requests_tombstones_only_for_active_removed_releases(
    cleanup_registry_module,
    tmp_path,
):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    release_index_file = tmp_path / "release_index.json"
    tombstone_requests_file = tmp_path / "tombstone-requests.json"
    package_repositories = {
        "Good": "good",
        "MissingActiveZ": "missing-active-z",
        "MissingActiveA": "missing-active-a",
        "MissingUnindexed": "missing-unindexed",
        "EmptyTombstoned": "empty-tombstoned",
    }
    registry_file.write_text(json.dumps({
        package_id: ["owner", repository, "description", "main"]
        for package_id, repository in package_repositories.items()
    }))
    update_times_file.write_text(json.dumps({
        package_id: "2026-06-14T15:10:03Z"
        for package_id in package_repositories
    }))
    metadata_file.write_text(json.dumps({
        "version": 1,
        "entries": {
            package_id: {"registry_platforms": ["linux"]}
            for package_id in package_repositories
        },
    }))
    upgrade_fixture_files(registry_file, update_times_file, metadata_file)
    release_index_file.write_text(json.dumps(cleanup_release_index(
        active_repositories=(
            ("MissingActiveZ", "github.com/owner/missing-active-z"),
            ("MissingActiveA", "github.com/owner/missing-active-a"),
            ("RemovedBeforeCleanup", "github.com/owner/removed-before-cleanup"),
        ),
        tombstoned_repositories=(
            ("EmptyTombstoned", "github.com/owner/empty-tombstoned"),
        ),
    )))

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return self.content

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if "/good/" in url:
            return FakeResponse(
                b'"""<plugin key="GOOD" name="Good"></plugin>"""\n'
            )
        if "/empty-tombstoned/" in url:
            return FakeResponse(b"")
        if "/missing-" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        raise AssertionError(f"Unexpected URL: {url}")

    stats = cleanup_registry_module.cleanup_registry_files(
        str(registry_file),
        str(update_times_file),
        str(metadata_file),
        apply_changes=True,
        sleep_seconds=0,
        opener=fake_urlopen,
        release_index_file=str(release_index_file),
        tombstone_requests_output=str(tombstone_requests_file),
    )

    assert stats["removed"] == 4
    requests = json.loads(tombstone_requests_file.read_text(encoding="utf-8"))
    assert list(requests) == [
        "MissingActiveA",
        "MissingActiveZ",
        "RemovedBeforeCleanup",
    ]
    assert requests == {
        **{
            package_id: {
                "reason": (
                    "Registry cleanup removed this package because its configured "
                    "root plugin.py is missing (HTTP 404)."
                )
            }
            for package_id in ("MissingActiveA", "MissingActiveZ")
        },
        "RemovedBeforeCleanup": {
            "reason": (
                "Weekly registry maintenance removed this package before "
                "release-index generation."
            )
        },
    }
    assert tombstone_requests_file.read_bytes().endswith(b"\n")


def test_cleanup_registry_rejects_non_v2_release_index(
    cleanup_registry_module,
    tmp_path,
):
    release_index_file = tmp_path / "release_index.json"
    release_index_file.write_text(json.dumps({
        "schema_version": 1,
        "plugins": {},
    }))

    with pytest.raises(ValueError, match="v2"):
        cleanup_registry_module.load_active_release_package_ids(
            release_index_file,
            {},
        )


def test_cleanup_registry_apply_removes_missing_entries_from_sidecars(cleanup_registry_module, tmp_path):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "Good": ["owner", "present", "description", "main"],
        "Missing": ["owner", "missing", "description", "main"],
        "ServerError": ["owner", "server-error", "description", "main"],
    }))
    update_times_file.write_text(json.dumps({
        "Good": "2026-06-14T15:10:03Z",
        "Missing": "2026-06-14T15:10:03Z",
        "ServerError": "2026-06-14T15:10:03Z",
    }))
    metadata_file.write_text(json.dumps({
        "version": 1,
        "entries": {
            "Good": {"registry_platforms": ["linux"]},
            "Missing": {"registry_platforms": ["linux"]},
            "ServerError": {"registry_platforms": ["linux"]},
        },
    }))
    upgrade_fixture_files(registry_file, update_times_file, metadata_file)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size=-1):
            return b'"""<plugin key="TEST" name="Test"></plugin>"""\n'

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        if "/present/" in url:
            return FakeResponse()
        if "/missing/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/server-error/" in url:
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)
        raise AssertionError(f"Unexpected URL: {url}")

    stats = cleanup_registry_module.cleanup_registry_files(
        str(registry_file),
        str(update_times_file),
        str(metadata_file),
        apply_changes=True,
        sleep_seconds=0,
        opener=fake_urlopen,
    )

    registry = saved_packages(registry_file)
    update_times = saved_update_times(update_times_file)
    metadata = saved_platform_entries(metadata_file)

    assert stats["removed"] == 1
    assert "Good" in registry
    assert "Missing" not in registry
    assert "ServerError" in registry
    assert "Missing" not in update_times
    assert "Missing" not in metadata


def test_platform_detector_flags_linux_only_dependencies(platform_detector_module):
    decision = platform_detector_module.detect_platforms_from_repository_data(
        file_texts={
            "README.md": "Requires Linux on a Raspberry Pi. Install with sudo apt install i2c-tools.",
            "plugin.py": "import RPi.GPIO as GPIO\nDEVICE = '/dev/gpiochip0'\n",
        }
    )

    assert decision.platforms == ["linux"]
    assert decision.linux_score > decision.windows_score


def test_platform_detector_prefers_explicit_linux_only_over_both_mentions(platform_detector_module):
    decision = platform_detector_module.detect_platforms_from_repository_data(
        file_texts={
            "README.md": "Linux only. Windows is not supported.",
        }
    )

    assert decision.platforms == ["linux"]


def test_platform_detector_flags_windows_only_dependencies(platform_detector_module):
    decision = platform_detector_module.detect_platforms_from_repository_data(
        file_texts={
            "README.md": "Windows only plugin. Use COM3 for the serial connection.",
            "plugin.py": "import winreg\n",
        }
    )

    assert decision.platforms == ["windows"]
    assert decision.windows_score > decision.linux_score


def test_platform_detector_defaults_generic_python_plugins_to_both(platform_detector_module):
    decision = platform_detector_module.detect_platforms_from_repository_data(
        file_texts={
            "README.md": "Domoticz plugin using the HTTP API.",
            "plugin.py": "import Domoticz\nimport requests\n",
        }
    )

    assert decision.platforms == ["linux", "windows"]
    assert decision.confidence == "low"
    assert decision.evidence_class == "generic_python"


def test_platform_detector_respects_explicit_both_support(platform_detector_module):
    decision = platform_detector_module.detect_platforms_from_repository_data(
        file_texts={
            "README.md": "Supported on Linux and Windows Domoticz installations.",
        }
    )

    assert decision.platforms == ["linux", "windows"]
    assert decision.confidence == "high"
    assert decision.evidence_class == "explicit_both"


def test_platform_policy_requires_high_confidence_for_existing_changes(platform_detector_module):
    current, action = platform_detector_module.choose_platforms_for_registry(
        ["linux", "windows"],
        platform_decision(["linux"], confidence="medium", evidence_class="linux_evidence"),
    )

    assert current == ["linux", "windows"]
    assert action == "kept_existing_requires_high_confidence"

    current, action = platform_detector_module.choose_platforms_for_registry(
        ["linux", "windows"],
        platform_decision(["linux"], confidence="high", evidence_class="explicit_linux_only"),
    )

    assert current == ["linux"]
    assert action == "accepted_high_confidence_change"


def test_platform_policy_keeps_reviewed_entries(platform_detector_module):
    current, action = platform_detector_module.choose_platforms_for_registry(
        ["linux", "windows"],
        platform_decision(["linux"], confidence="high", evidence_class="explicit_linux_only"),
        metadata_entry={"reviewed": True},
    )

    assert current == ["linux", "windows"]
    assert action == "kept_reviewed"


def test_platform_policy_leaves_low_confidence_new_entries_unknown(platform_detector_module):
    current, action = platform_detector_module.choose_platforms_for_registry(
        [],
        platform_decision(["linux", "windows"], confidence="low", evidence_class="generic_python"),
        is_new=True,
    )

    assert current == []
    assert action == "kept_low_confidence_new"


def test_platform_metadata_marks_missing_entries_reviewed_when_sidecar_exists(platform_detector_module):
    registry = {
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "ManualPlugin": ["owner", "repo", "description", "main", "", ["linux"]],
    }

    metadata = platform_detector_module.ensure_platform_metadata_for_registry(
        {"version": 1, "entries": {}},
        registry,
        manual_changes_are_reviewed=True,
    )

    entry = metadata["entries"]["ManualPlugin"]
    assert entry["registry_platforms"] == ["linux"]
    assert entry["source"] == "reviewed"
    assert entry["evidence_class"] == "manual_registry_edit"
    assert entry["reviewed"] is True


def test_platform_metadata_marks_platform_edits_reviewed_when_sidecar_exists(platform_detector_module):
    registry = {
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "ManualPlugin": ["owner", "repo", "description", "main", "", ["linux"]],
    }
    metadata = {
        "version": 1,
        "entries": {
            "ManualPlugin": {
                "identity": "github.com/owner/repo@main",
                "owner": "owner",
                "repository": "repo",
                "branch": "main",
                "registry_platforms": ["linux", "windows"],
                "source": "detected",
                "confidence": "low",
                "evidence_class": "generic_python",
                "reviewed": False,
                "last_detection": {"platforms": ["linux", "windows"]},
                "policy_action": "unchanged",
            },
        },
    }

    metadata = platform_detector_module.ensure_platform_metadata_for_registry(
        metadata,
        registry,
        manual_changes_are_reviewed=True,
    )

    entry = metadata["entries"]["ManualPlugin"]
    assert entry["registry_platforms"] == ["linux"]
    assert entry["source"] == "reviewed"
    assert entry["confidence"] == "unknown"
    assert entry["evidence_class"] == "manual_registry_edit"
    assert entry["reviewed"] is True
    assert "last_detection" not in entry
    assert "policy_action" not in entry


def test_platform_detector_fetches_codeberg_tree_and_raw_urls(platform_detector_module, monkeypatch):
    fetched_json_urls = []
    fetched_text_urls = []

    def fake_fetch_json(url, timeout=20):
        fetched_json_urls.append(url)
        return {
            "tree": [
                {"type": "blob", "path": "README.md", "size": 40},
                {"type": "blob", "path": "plugin.py", "size": 40},
            ],
        }

    def fake_fetch_text(url, timeout=20):
        fetched_text_urls.append(url)
        return "Domoticz plugin\nimport Domoticz\n"

    monkeypatch.setattr(platform_detector_module, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(platform_detector_module, "fetch_text", fake_fetch_text)

    decision = platform_detector_module.detect_platforms_for_repo(
        "codeberg.org/Hoog",
        "Domoticz-Stromer-plugin",
        "main",
        repo_info={"description": "Domoticz plugin"},
    )

    assert fetched_json_urls == [
        "https://codeberg.org/api/v1/repos/Hoog/Domoticz-Stromer-plugin/git/trees/main?recursive=1",
    ]
    assert fetched_text_urls == [
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/raw/branch/main/README.md",
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin/raw/branch/main/plugin.py",
    ]
    assert decision.platforms == ["linux", "windows"]


def test_platform_detector_fetches_gitlab_tree_and_raw_urls(platform_detector_module, monkeypatch):
    fetched_json_urls = []
    fetched_text_urls = []

    def fake_fetch_json(url, timeout=20):
        fetched_json_urls.append(url)
        return [
            {"type": "blob", "path": "README.md", "size": 40},
            {"type": "blob", "path": "plugin.py", "size": 40},
        ]

    def fake_fetch_text(url, timeout=20):
        fetched_text_urls.append(url)
        return "Domoticz plugin\nimport Domoticz\n"

    monkeypatch.setattr(platform_detector_module, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(platform_detector_module, "fetch_text", fake_fetch_text)

    decision = platform_detector_module.detect_platforms_for_repo(
        "gitlab.com/r.boeters",
        "DomoticzSabNZBDPlugin",
        "master",
        repo_info={"description": "Domoticz plugin"},
    )

    assert fetched_json_urls == [
        "https://gitlab.com/api/v4/projects/r.boeters%2FDomoticzSabNZBDPlugin/repository/tree?recursive=true&per_page=100&ref=master",
    ]
    assert fetched_text_urls == [
        "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/raw/master/README.md",
        "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin/-/raw/master/plugin.py",
    ]
    assert decision.platforms == ["linux", "windows"]


def test_platform_detector_writes_platforms_in_sixth_registry_slot(platform_detector_module):
    assert platform_detector_module.set_registry_entry_platforms(
        ["owner", "repo", "description", "main"],
        ["windows"],
    ) == ["owner", "repo", "description", "main", "", ["windows"]]

    assert platform_detector_module.set_registry_entry_platforms(
        ["owner", "repo", "description", "main", "2026-06-14T15:10:03Z"],
        ["linux"],
    ) == ["owner", "repo", "description", "main", "2026-06-14T15:10:03Z", ["linux"]]


def test_scanner_updates_existing_registry_platforms(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "Plugin": ["owner", "repo", "description", "main"],
    }))
    update_times_file.write_text(json.dumps({
        "Plugin": "2026-06-14T15:10:03Z",
    }))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "name": "repo",
        "description": "description",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
    }

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", lambda owner, repo: repo_info)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda owner, repo, branch, repo_info=None: platform_decision(["linux"], confidence="medium"),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)

    assert registry["Plugin"]["platforms"] == ["linux"]
    metadata = saved_platform_entries(metadata_file)
    assert metadata["Plugin"]["registry_platforms"] == ["linux"]
    assert metadata["Plugin"]["source"] == "detected"


def test_scanner_updates_default_policy_when_existing_plugin_starts_releasing(
    scan_plugins_module,
    tmp_path,
    monkeypatch,
):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "Plugin": ["owner", "repo", "description", "main"],
    }))
    update_times_file.write_text(json.dumps({
        "Plugin": "2026-06-14T15:10:03Z",
    }))
    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "name": "repo",
        "description": "description",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
        scan_plugins_module.RELEASE_TAG_PATTERN_CHECKED_FIELD: True,
        scan_plugins_module.RELEASE_TAG_PATTERN_FIELD: DOTTED_V_TAG_PATTERN,
    }

    patch_scanner_paths(
        scan_plugins_module,
        monkeypatch,
        registry_file,
        update_times_file,
        metadata_file,
    )
    monkeypatch.setattr(
        scan_plugins_module,
        "get_repo_info",
        lambda owner, repo: repo_info,
    )
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    assert registry["Plugin"]["delivery"]["release"]["tag_pattern"] == (
        DOTTED_V_TAG_PATTERN
    )


def test_scanner_never_updates_existing_registry_branch(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "luxtronikex": [
            "Rouzax",
            "luxtronik-domoticz-plugin-v2",
            "description",
            "dist",
            "",
            ["linux", "windows"],
        ],
    }))
    update_times_file.write_text(json.dumps({
        "luxtronikex": "2026-03-14T14:52:18Z",
    }))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "Rouzax/luxtronik-domoticz-plugin-v2",
        "owner": {"login": "Rouzax"},
        "name": "luxtronik-domoticz-plugin-v2",
        "description": "updated description",
        "default_branch": "main",
        "pushed_at": "2026-07-02T11:45:18Z",
    }

    seen_branches = []

    def detect_platforms(owner, repo, branch, repo_info=None):
        seen_branches.append(branch)
        return platform_decision(["linux", "windows"], confidence="high")

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", lambda owner, repo: repo_info)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "detect_platforms_for_repo", detect_platforms)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    metadata = saved_platform_entries(metadata_file)

    assert seen_branches == ["dist"]
    assert registry["luxtronikex"]["description"] == "updated description"
    assert registry["luxtronikex"]["repository"]["branch"] == "dist"
    assert registry["luxtronikex"]["platforms"] == ["linux", "windows"]
    assert metadata["luxtronikex"]["branch"] == "dist"
    assert metadata["luxtronikex"]["identity"] == (
        "github.com/rouzax/luxtronik-domoticz-plugin-v2@dist"
    )


def test_scanner_adds_platforms_for_new_plugins(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
    }))
    update_times_file.write_text(json.dumps({}))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "name": "repo",
        "description": "description",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
    }
    mark_repo_certified(scan_plugins_module, repo_info, "REPO")
    repo_info[scan_plugins_module.RELEASE_TAG_PATTERN_CHECKED_FIELD] = True
    repo_info[scan_plugins_module.RELEASE_TAG_PATTERN_FIELD] = (
        DOTTED_V_TAG_PATTERN
    )

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [repo_info])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "has_root_plugin_py", lambda repo: True)
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda owner, repo, branch, repo_info=None: platform_decision(["windows"], confidence="medium"),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)

    assert registry["repo"]["domoticz_key"] == "REPO"
    assert registry["repo"]["repository"] == {
        "url": "https://github.com/owner/repo",
        "branch": "main",
    }
    assert registry["repo"]["platforms"] == ["windows"]
    assert registry["repo"]["delivery"]["release"]["provider"] == "github"
    assert registry["repo"]["delivery"]["release"]["tag_pattern"] == (
        DOTTED_V_TAG_PATTERN
    )


def test_scanner_keeps_low_confidence_new_plugin_platforms_unknown(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
    }))
    update_times_file.write_text(json.dumps({}))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "name": "repo",
        "description": "description",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
    }
    mark_repo_certified(scan_plugins_module, repo_info, "REPO")

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [repo_info])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "has_root_plugin_py", lambda repo: True)
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda owner, repo, branch, repo_info=None: platform_decision(
            ["linux", "windows"],
            confidence="low",
            evidence_class="generic_python",
        ),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    metadata = saved_platform_entries(metadata_file)

    assert registry["repo"]["platforms"] == []
    assert metadata["repo"]["registry_platforms"] == []
    assert metadata["repo"]["last_detection"]["platforms"] == ["linux", "windows"]
    assert metadata["repo"]["policy_action"] == "kept_low_confidence_new"


def test_scanner_skips_new_candidates_without_root_plugin_py(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry = {
        "Idle": ["Idle", "Idle", "Idle", "master"],
    }
    registry_file.write_text(json.dumps(registry))
    update_times_file.write_text(json.dumps({}))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/wiki",
        "owner": {"login": "owner"},
        "name": "wiki",
        "description": "Wiki part of a Domoticz plugin",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
    }

    def fail_detect_platforms(*args, **kwargs):
        pytest.fail("repositories without a root plugin.py should not reach platform detection")

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [repo_info])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "has_root_plugin_py", lambda repo: False)
    monkeypatch.setattr(scan_plugins_module, "detect_platforms_for_repo", fail_detect_platforms)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    assert saved_packages(registry_file) == {}
    assert saved_update_times(update_times_file) == {}
    assert not metadata_file.exists()


def test_scanner_blocks_medium_confidence_existing_platform_downgrade(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "Plugin": ["owner", "repo", "description", "main", "", ["linux", "windows"]],
    }))
    update_times_file.write_text(json.dumps({
        "Plugin": "2026-06-14T15:10:03Z",
    }))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "name": "repo",
        "description": "description",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
    }

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", lambda owner, repo: repo_info)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda owner, repo, branch, repo_info=None: platform_decision(
            ["linux"],
            confidence="medium",
            evidence_class="linux_evidence",
        ),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    metadata = saved_platform_entries(metadata_file)

    assert registry["Plugin"]["platforms"] == ["linux", "windows"]
    assert metadata["Plugin"]["registry_platforms"] == ["linux", "windows"]
    assert metadata["Plugin"]["last_detection"]["platforms"] == ["linux"]
    assert metadata["Plugin"]["policy_action"] == "kept_existing_requires_high_confidence"


def test_scanner_treats_existing_registry_entries_missing_from_sidecar_as_reviewed(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "ManualPlugin": ["owner", "repo", "description", "main"],
    }))
    update_times_file.write_text(json.dumps({
        "ManualPlugin": "2026-06-14T15:10:03Z",
    }))
    metadata_file.write_text(json.dumps({
        "version": 1,
        "entries": {},
    }))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "name": "repo",
        "description": "description",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
    }

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", lambda owner, repo: repo_info)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda owner, repo, branch, repo_info=None: platform_decision(
            ["linux"],
            confidence="medium",
            evidence_class="linux_evidence",
        ),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    metadata = saved_platform_entries(metadata_file)

    assert registry["ManualPlugin"]["description"] == "description"
    assert metadata["ManualPlugin"]["source"] == "reviewed"
    assert metadata["ManualPlugin"]["reviewed"] is True
    assert metadata["ManualPlugin"]["policy_action"] == "kept_reviewed"
    assert metadata["ManualPlugin"]["last_detection"]["platforms"] == ["linux"]


def test_scanner_prunes_stale_update_times(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "Plugin": ["owner", "repo", "description", "main"],
    }))
    update_times_file.write_text(json.dumps({
        "Plugin": "2026-06-14T15:10:03Z",
        "OldPlugin": "2026-04-20T17:51:05Z",
    }))

    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "name": "repo",
        "description": "description",
        "default_branch": "main",
        "pushed_at": "2026-06-14T15:10:03Z",
    }

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", lambda owner, repo: repo_info)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "detect_platforms_for_repo", lambda *args, **kwargs: None)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    update_times = saved_update_times(update_times_file)

    assert update_times == {"Plugin": "2026-06-14T15:10:03Z"}


def test_scanner_adds_codeberg_and_gitlab_plugins(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
    }))
    update_times_file.write_text(json.dumps({}))

    codeberg_repo = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "host": "codeberg.org",
        "full_name": "Hoog/Domoticz-Stromer-plugin",
        "owner": {"login": "Hoog"},
        "name": "Domoticz-Stromer-plugin",
        "description": "Domoticz plugin for integrating Stromer portal data.",
        "default_branch": "main",
        "pushed_at": "2026-06-30T19:36:29Z",
    }
    gitlab_repo = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "host": "gitlab.com",
        "full_name": "r.boeters/DomoticzSabNZBDPlugin",
        "owner": {"login": "r.boeters"},
        "name": "DomoticzSabNZBDPlugin",
        "description": "SabNZBD Python plugin for Domoticz Home Automation",
        "default_branch": "master",
        "pushed_at": "2019-08-16T18:29:37.958Z",
    }
    mark_repo_certified(scan_plugins_module, codeberg_repo, "STROMER")
    mark_repo_certified(scan_plugins_module, gitlab_repo, "SABNZBD")

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [codeberg_repo])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [gitlab_repo])
    monkeypatch.setattr(scan_plugins_module, "has_root_plugin_py", lambda repo: True)
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda owner, repo, branch, repo_info=None: platform_decision(["linux", "windows"], confidence="medium"),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    update_times = saved_update_times(update_times_file)

    assert registry["Domoticz-Stromer-plugin"]["repository"]["url"] == (
        "https://codeberg.org/Hoog/Domoticz-Stromer-plugin"
    )
    assert registry["Domoticz-Stromer-plugin"]["domoticz_key"] == "STROMER"
    assert registry["Domoticz-Stromer-plugin"]["delivery"]["release"]["provider"] == "codeberg"
    assert registry["DomoticzSabNZBDPlugin"]["repository"]["url"] == (
        "https://gitlab.com/r.boeters/DomoticzSabNZBDPlugin"
    )
    assert registry["DomoticzSabNZBDPlugin"]["domoticz_key"] == "SABNZBD"
    assert registry["DomoticzSabNZBDPlugin"]["delivery"]["release"]["provider"] == "gitlab"
    assert update_times["Domoticz-Stromer-plugin"] == "2026-06-30T19:36:29Z"
    assert update_times["DomoticzSabNZBDPlugin"] == "2019-08-16T18:29:37Z"


def test_scanner_removes_empty_existing_repo_and_does_not_readd(scan_plugins_module, tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
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

    patch_scanner_paths(scan_plugins_module, monkeypatch, registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", lambda owner, repo: empty_repo)
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [empty_repo])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    update_times = saved_update_times(update_times_file)

    assert "Domoticz_integration" not in registry
    assert "Domoticz_integration" not in update_times


def test_scanner_removes_blocklisted_existing_repo_and_does_not_readd(
    scan_plugins_module,
    tmp_path,
    monkeypatch,
):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    registry_file.write_text(json.dumps({
        "Idle": ["Idle", "Idle", "Idle", "master"],
        "TemplateAlias": [
            "galadril",
            "Domoticz-Python-Plugin-Template",
            "Template repository for making Domoticz Python Plugins",
            "main",
            "",
            ["linux", "windows"],
        ],
    }))
    update_times_file.write_text(json.dumps({
        "TemplateAlias": "2025-01-08T10:05:44Z",
    }))
    metadata_file.write_text(json.dumps({
        "version": 1,
        "entries": {
            "TemplateAlias": {
                "identity": (
                    "github.com/galadril/"
                    "domoticz-python-plugin-template@main"
                ),
                "owner": "galadril",
                "repository": "Domoticz-Python-Plugin-Template",
                "branch": "main",
                "registry_platforms": ["linux", "windows"],
                "source": "legacy_detected",
                "confidence": "unknown",
                "evidence_class": "legacy",
                "reviewed": False,
            },
        },
    }))

    template_repo = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "galadril/Domoticz-Python-Plugin-Template",
        "owner": {"login": "galadril"},
        "name": "Domoticz-Python-Plugin-Template",
        "description": "Template repository for making Domoticz Python Plugins",
        "default_branch": "main",
        "pushed_at": "2025-01-08T10:05:44Z",
    }

    def fail_get_repo_info(owner, repo):
        raise AssertionError("blocklisted repositories should not be fetched")

    def fail_candidate_inspection(*args, **kwargs):
        raise AssertionError(
            "blocklisted repositories should not be inspected"
        )

    patch_scanner_paths(
        scan_plugins_module,
        monkeypatch,
        registry_file,
        update_times_file,
        metadata_file,
    )
    monkeypatch.setattr(scan_plugins_module, "get_repo_info", fail_get_repo_info)
    monkeypatch.setattr(
        scan_plugins_module,
        "search_github",
        lambda: [template_repo],
    )
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(
        scan_plugins_module,
        "discovered_repo_has_root_plugin_py",
        fail_candidate_inspection,
    )
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        fail_candidate_inspection,
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    registry = saved_packages(registry_file)
    update_times = saved_update_times(update_times_file)
    metadata = saved_platform_entries(metadata_file)

    assert "TemplateAlias" not in registry
    assert "TemplateAlias" not in update_times
    assert "TemplateAlias" not in metadata


def test_checked_in_active_artifacts_exclude_blocklisted_repositories(
    scan_plugins_module,
):
    registry = scan_plugins_module.load_registry_file(
        REPO_ROOT / "registry.json"
    )
    registry_repositories = set()
    for package_id, entry in registry.items():
        if package_id == "Idle":
            continue
        record = scan_plugins_module.RegistryRecord.from_entry(
            package_id,
            entry,
        )
        registry_repositories.add(
            scan_plugins_module.normalize_full_name(
                record.owner,
                record.repository,
            )
        )
    release_index = json.loads(
        (REPO_ROOT / "release_index.json").read_text(encoding="utf-8")
    )
    blocked_repository_identities = {
        f"github.com/{repository}"
        for repository in scan_plugins_module.REPO_BLOCKLIST
    }
    active_release_repositories = {
        release["repository_identity"].casefold()
        for release in release_index["releases"]
    }
    migration = json.loads(
        (
            REPO_ROOT / ".github" / "package_identity_migration.json"
        ).read_text(encoding="utf-8")
    )
    migration_repositories = {
        mapping["repository_identity"].casefold()
        for mapping in migration["package_mappings"]
    }

    assert registry_repositories.isdisjoint(
        scan_plugins_module.REPO_BLOCKLIST
    )
    assert active_release_repositories.isdisjoint(
        blocked_repository_identities
    )
    assert migration["package_count"] == len(migration["package_mappings"])
    assert migration_repositories.isdisjoint(blocked_repository_identities)


def test_validate_theme_entry_accepts_valid_entry(validate_plugins_module):
    valid_entry = {
        "display_name": "Test Theme",
        "author": "test-author",
        "repository": "test-repo",
        "branch": "main",
        "description": "A very beautiful theme.",
        "target_dir": "test-theme",
        "source_path": "dist",
        "entry_files": ["custom.css"],
        "contains_javascript": False,
        "requires_restart": "first_install",
    }
    # Should not raise exception
    validate_plugins_module.validate_theme_entry("test-theme", valid_entry)


def test_validate_theme_entry_rejects_missing_keys(validate_plugins_module):
    invalid_entry = {
        "display_name": "Test Theme",
        "author": "test-author",
        "repository": "test-repo",
        "branch": "main",
        # "description" is missing
        "target_dir": "test-theme",
    }
    with pytest.raises(ValueError, match="missing required key 'description'"):
        validate_plugins_module.validate_theme_entry("test-theme", invalid_entry)


def test_validate_theme_entry_rejects_invalid_types(validate_plugins_module):
    invalid_entry = {
        "display_name": "Test Theme",
        "author": "test-author",
        "repository": "test-repo",
        "branch": 1234,  # branch must be a string
        "description": "A beautiful theme.",
        "target_dir": "test-theme",
    }
    with pytest.raises(ValueError, match="key 'branch' must be a str"):
        validate_plugins_module.validate_theme_entry("test-theme", invalid_entry)


def test_validate_theme_entry_rejects_unsafe_target_dir(validate_plugins_module):
    invalid_entry = {
        "display_name": "Test Theme",
        "author": "test-author",
        "repository": "test-repo",
        "branch": "main",
        "description": "A beautiful theme.",
        "target_dir": "../unsafe-path",
    }
    with pytest.raises(ValueError, match="not a safe directory name"):
        validate_plugins_module.validate_theme_entry("test-theme", invalid_entry)


def test_load_themes_validates_json_file(validate_plugins_module, tmp_path, monkeypatch):
    themes_file = tmp_path / "themes.json"
    themes_data = {
        "valid-theme": {
            "display_name": "Valid Theme",
            "author": "valid-author",
            "repository": "valid-repo",
            "branch": "main",
            "description": "A valid theme",
            "target_dir": "valid-theme",
        }
    }
    themes_file.write_text(json.dumps(themes_data), encoding="utf-8")
    monkeypatch.setattr(validate_plugins_module, "THEMES_FILE_PATH", str(themes_file))

    loaded = validate_plugins_module.load_themes()
    assert "valid-theme" in loaded
    assert loaded["valid-theme"]["display_name"] == "Valid Theme"
