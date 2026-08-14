import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT, load_module_from_path
from test_registry_scripts import (
    platform_metadata_v2,
    saved_packages,
    saved_platform_entries,
    update_times_v2,
    upgrade_fixture_files,
)


def release_policy(
    provider="github",
    *,
    artifact="source_zip",
    api_base=None,
    web_base=None,
    release_page_size=None,
    manifest_url=None,
):
    policy = {
        "provider": provider,
        "channel": "stable",
        "artifact": artifact,
        "source_path": ".",
        "mutable_paths": ["config.json", "data"],
        "allowed_origins": ["https://packages.example.test"],
    }
    if provider in {"github", "gitlab", "forgejo", "gitea"}:
        policy["tag_pattern"] = r"^v[0-9]+\.[0-9]+\.[0-9]+$"
    if api_base is not None:
        policy["api_base"] = api_base
    if web_base is not None:
        policy["web_base"] = web_base
    if release_page_size is not None:
        policy["release_page_size"] = release_page_size
    if manifest_url is not None:
        policy["manifest_url"] = manifest_url
    return policy


def delivery_policy(
    preferred="release_if_indexed",
    *,
    git_supported=True,
    release=None,
):
    document = {
        "schema_version": 1,
        "preferred": preferred,
        "git_supported": git_supported,
    }
    if release is not None:
        document["release"] = release
    elif preferred != "git":
        document["release"] = release_policy()
    return document


def object_entry(**overrides):
    entry = {
        "owner": "owner",
        "repository": "example-plugin",
        "description": "Example plugin",
        "branch": "main",
        "updated_at": "2026-07-17T10:00:00Z",
        "platforms": ["linux"],
        "delivery": delivery_policy(),
        "review": {
            "approved_by": "maintainer",
            "issue": 64,
        },
        "x-curator": {
            "packaging_note": "Keep source_path at the repository root.",
        },
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def registry_records_module():
    module_path = REPO_ROOT / ".github" / "scripts" / "registry_records.py"
    if not module_path.is_file():
        class MissingRegistryRecords:
            def __getattr__(self, name):
                raise AssertionError(
                    "Missing shared registry record contract: " + name
                )

        return MissingRegistryRecords()
    return load_module_from_path("registry_records_under_test", module_path)


@pytest.fixture
def scan_plugins_module():
    return load_module_from_path(
        "scan_plugins_delivery_under_test",
        REPO_ROOT / ".github" / "scripts" / "scan_github_plugins.py",
    )


@pytest.fixture
def cleanup_registry_module():
    return load_module_from_path(
        "cleanup_registry_delivery_under_test",
        REPO_ROOT / ".github" / "scripts" / "cleanup_registry.py",
    )


@pytest.fixture
def validate_plugins_module():
    return load_module_from_path(
        "validate_plugins_delivery_under_test",
        REPO_ROOT / ".github" / "scripts" / "validate_plugins.py",
    )


@pytest.fixture
def platform_detector_module():
    return load_module_from_path(
        "platform_detector_delivery_under_test",
        REPO_ROOT / ".github" / "scripts" / "detect_plugin_platforms.py",
    )


def record(module, key, entry):
    return module.RegistryRecord.from_entry(key, entry)


def test_legacy_record_round_trips_every_existing_and_trailing_slot(
    registry_records_module,
):
    entry = [
        "Owner",
        "Example-Plugin",
        "Description",
        "stable",
        "2026-07-17T10:00:00Z",
        ["linux", "windows"],
        {"reviewed": True, "note": "forward-compatible legacy field"},
    ]
    original = copy.deepcopy(entry)

    parsed = record(registry_records_module, "ExamplePlugin", entry)

    assert parsed.key == "ExamplePlugin"
    assert parsed.owner == "Owner"
    assert parsed.repository == "Example-Plugin"
    assert parsed.description == "Description"
    assert parsed.branch == "stable"
    assert parsed.updated_at == "2026-07-17T10:00:00Z"
    assert parsed.platforms == ["linux", "windows"]
    assert parsed.is_legacy is True
    assert parsed.delivery.preferred == "release_if_indexed"
    assert parsed.delivery.git_supported is True
    assert parsed.delivery.release is None
    assert parsed.to_document() == original
    assert entry == original


def test_object_record_round_trips_delivery_and_unknown_reviewed_fields(
    registry_records_module,
):
    entry = object_entry()
    original = copy.deepcopy(entry)

    parsed = record(registry_records_module, "ExamplePlugin", entry)

    assert parsed.is_legacy is False
    assert parsed.owner == "owner"
    assert parsed.repository == "example-plugin"
    assert parsed.platforms == ["linux"]
    assert parsed.delivery.preferred == "release_if_indexed"
    assert parsed.delivery.release.provider == "github"
    assert parsed.delivery.release.mutable_paths == ["config.json", "data"]
    assert parsed.to_document() == original
    assert parsed.extra_fields == {
        "review": original["review"],
        "x-curator": original["x-curator"],
    }
    assert entry == original


def test_description_and_platform_updates_are_lossless_and_functional(
    registry_records_module,
):
    entry = object_entry()
    parsed = record(registry_records_module, "ExamplePlugin", entry)

    updated = parsed.with_description("Updated from forge metadata")
    updated = updated.with_platforms(["windows", "linux", "windows"])

    expected = copy.deepcopy(entry)
    expected["description"] = "Updated from forge metadata"
    expected["platforms"] = ["linux", "windows"]
    assert updated.to_document() == expected
    assert updated.to_document()["delivery"] == entry["delivery"]
    assert updated.to_document()["review"] == entry["review"]
    assert updated.to_document()["x-curator"] == entry["x-curator"]
    assert parsed.to_document() == entry


def test_legacy_description_and_platform_updates_keep_shape_and_tail(
    registry_records_module,
):
    entry = [
        "owner",
        "repo",
        "old",
        "main",
        "",
        ["linux"],
        {"reviewed": True},
    ]
    parsed = record(registry_records_module, "Plugin", entry)

    updated = parsed.with_description("new").with_platforms(["windows"])

    assert updated.to_document() == [
        "owner",
        "repo",
        "new",
        "main",
        "",
        ["windows"],
        {"reviewed": True},
    ]
    assert parsed.to_document() == entry


@pytest.mark.parametrize(
    ("entry", "clone_url", "repository_identity"),
    [
        pytest.param(
            ["Owner", "Example-Plugin", "Description", "main"],
            "https://github.com/Owner/Example-Plugin.git",
            "github.com/owner/example-plugin",
            id="github-legacy-shorthand",
        ),
        pytest.param(
            object_entry(
                owner="gitlab.com/Group/Subgroup",
                repository="Example-Plugin",
                delivery=delivery_policy(release=release_policy("gitlab")),
            ),
            "https://gitlab.com/Group/Subgroup/Example-Plugin.git",
            "gitlab.com/group/subgroup/example-plugin",
            id="gitlab-nested-owner",
        ),
        pytest.param(
            object_entry(
                owner="codeberg.org/Team",
                repository="Example-Plugin",
                delivery=delivery_policy(release=release_policy("forgejo")),
            ),
            "https://codeberg.org/Team/Example-Plugin.git",
            "codeberg.org/team/example-plugin",
            id="codeberg",
        ),
        pytest.param(
            object_entry(
                owner="https://forge.example.test/Team/Subteam/",
                repository="Example-Plugin",
                delivery=delivery_policy("git"),
            ),
            "https://forge.example.test/Team/Subteam/Example-Plugin.git",
            "forge.example.test/team/subteam/example-plugin",
            id="custom-full-https",
        ),
    ],
)
def test_clone_url_and_repository_identity_are_canonical_and_forge_neutral(
    registry_records_module,
    entry,
    clone_url,
    repository_identity,
):
    parsed = record(registry_records_module, "ExamplePlugin", entry)

    assert parsed.clone_url == clone_url
    assert parsed.repository_identity == repository_identity


@pytest.mark.parametrize(
    "owner",
    [
        pytest.param("http://forge.example.test/team", id="custom-http"),
        pytest.param(
            "https://user:secret@forge.example.test/team",
            id="custom-credentials",
        ),
        pytest.param(
            "https://forge.example.test/team?repo=other",
            id="custom-query",
        ),
        pytest.param("forge.example.test/team", id="custom-without-https"),
    ],
)
def test_custom_hosts_require_clean_full_https_owner_url(
    registry_records_module, owner
):
    entry = object_entry(owner=owner)

    with pytest.raises(ValueError):
        record(registry_records_module, "ExamplePlugin", entry)


@pytest.mark.parametrize(
    ("release", "owner"),
    [
        pytest.param(release_policy("github"), "owner", id="github"),
        pytest.param(
            release_policy("gitlab"),
            "gitlab.com/group/subgroup",
            id="gitlab",
        ),
        pytest.param(
            release_policy("forgejo"),
            "codeberg.org/team",
            id="codeberg-forgejo",
        ),
        pytest.param(
            release_policy(
                "gitea",
                api_base="https://gitea.example.test/api/v1",
                web_base="https://gitea.example.test",
                release_page_size=50,
            ),
            "https://gitea.example.test/team",
            id="configured-gitea",
        ),
        pytest.param(
            release_policy(
                "forgejo",
                api_base="https://forgejo.example.test/api/v1",
                web_base="https://forgejo.example.test",
                release_page_size=25,
            ),
            "https://forgejo.example.test/team",
            id="configured-forgejo",
        ),
        pytest.param(
            release_policy(
                "generic",
                artifact="asset_zip",
                manifest_url=(
                    "https://downloads.example.test/release-manifest.json"
                ),
            ),
            "https://downloads.example.test/team",
            id="generic-manifest",
        ),
    ],
)
def test_delivery_validation_accepts_reviewed_provider_policies(
    registry_records_module, release, owner
):
    entry = object_entry(
        owner=owner,
        delivery=delivery_policy(release=release)
    )

    parsed = record(registry_records_module, "ExamplePlugin", entry)

    assert parsed.delivery.schema_version == 1
    assert parsed.delivery.preferred == "release_if_indexed"
    assert parsed.delivery.git_supported is True
    assert parsed.delivery.release.provider == release["provider"]
    assert parsed.to_document()["delivery"] == entry["delivery"]


def invalid_delivery_documents():
    cases = []

    def add(case_id, document):
        cases.append(pytest.param(document, id=case_id))

    add(
        "unsupported-schema",
        {**delivery_policy(), "schema_version": 2},
    )
    add(
        "unsupported-preference",
        {**delivery_policy(), "preferred": "automatic"},
    )
    add(
        "git-disabled-on-git-channel",
        delivery_policy("git", git_supported=False),
    )
    add(
        "required-release-missing-policy",
        {
            "schema_version": 1,
            "preferred": "release",
            "git_supported": True,
        },
    )

    unsupported_provider = release_policy()
    unsupported_provider["provider"] = "sourcehut"
    add(
        "unsupported-provider",
        delivery_policy(release=unsupported_provider),
    )

    gitlab_without_pattern = release_policy("gitlab")
    del gitlab_without_pattern["tag_pattern"]
    add(
        "gitlab-requires-reviewed-tag-pattern",
        delivery_policy(release=gitlab_without_pattern),
    )

    github_without_pattern = release_policy("github")
    del github_without_pattern["tag_pattern"]
    add(
        "github-requires-reviewed-tag-pattern",
        delivery_policy(release=github_without_pattern),
    )

    custom_without_api = release_policy("gitea")
    add(
        "custom-gitea-requires-api-base",
        delivery_policy(release=custom_without_api),
    )

    custom_gitea_without_web = release_policy(
        "gitea",
        api_base="https://gitea.example.test/api/v1",
        release_page_size=50,
    )
    add(
        "custom-gitea-requires-web-base",
        delivery_policy(release=custom_gitea_without_web),
    )

    custom_forgejo_without_page_size = release_policy(
        "forgejo",
        api_base="https://forgejo.example.test/api/v1",
        web_base="https://forgejo.example.test",
    )
    add(
        "custom-forgejo-requires-page-size",
        delivery_policy(release=custom_forgejo_without_page_size),
    )

    custom_gitea_bad_page_size = release_policy(
        "gitea",
        api_base="https://gitea.example.test/api/v1",
        web_base="https://gitea.example.test",
        release_page_size=101,
    )
    add(
        "custom-gitea-page-size-bounded",
        delivery_policy(release=custom_gitea_bad_page_size),
    )

    generic_without_manifest = release_policy(
        "generic", artifact="asset_zip"
    )
    add(
        "generic-requires-manifest",
        delivery_policy(release=generic_without_manifest),
    )

    insecure_api = release_policy("gitea", api_base="http://gitea.test/api")
    add("api-base-https", delivery_policy(release=insecure_api))

    unsafe_source = release_policy()
    unsafe_source["source_path"] = "../plugin"
    add("unsafe-source-path", delivery_policy(release=unsafe_source))

    reserved_mutable = release_policy()
    reserved_mutable["mutable_paths"] = ["plugin.py"]
    add("reserved-mutable-path", delivery_policy(release=reserved_mutable))

    duplicate_mutable = release_policy()
    duplicate_mutable["mutable_paths"] = ["data", "data"]
    add("duplicate-mutable-path", delivery_policy(release=duplicate_mutable))

    invalid_origin = release_policy()
    invalid_origin["allowed_origins"] = [
        "https://packages.example.test/releases"
    ]
    add("allowlist-must-contain-origins", delivery_policy(release=invalid_origin))

    source_with_asset = release_policy()
    source_with_asset["asset_name"] = "plugin.zip"
    add("source-cannot-select-asset", delivery_policy(release=source_with_asset))

    asset_without_selector = release_policy(artifact="asset_zip")
    add(
        "asset-requires-selector",
        delivery_policy(release=asset_without_selector),
    )

    generic_source = release_policy(
        "generic",
        manifest_url="https://downloads.example.test/release-manifest.json",
    )
    add(
        "generic-requires-asset-zip",
        delivery_policy(release=generic_source),
    )

    duplicate_default_port = release_policy()
    duplicate_default_port["allowed_origins"] = [
        "https://packages.example.test",
        "https://packages.example.test:443",
    ]
    add(
        "origins-normalize-default-port",
        delivery_policy(release=duplicate_default_port),
    )

    reserved_source = release_policy()
    reserved_source["source_path"] = "con/plugin"
    add(
        "source-path-is-portable",
        delivery_policy(release=reserved_source),
    )

    return cases


@pytest.mark.parametrize("delivery", invalid_delivery_documents())
def test_delivery_validation_rejects_unsafe_or_ambiguous_policy(
    registry_records_module, delivery
):
    with pytest.raises(ValueError):
        record(
            registry_records_module,
            "ExamplePlugin",
            object_entry(delivery=delivery),
        )


def test_platform_helper_preserves_object_delivery_and_review_fields(
    platform_detector_module,
):
    entry = object_entry()

    updated = platform_detector_module.set_registry_entry_platforms(
        entry,
        ["windows"],
    )

    assert updated["platforms"] == ["windows"]
    assert updated["delivery"] == entry["delivery"]
    assert updated["review"] == entry["review"]
    assert updated["x-curator"] == entry["x-curator"]
    assert entry["platforms"] == ["linux"]


def patch_scanner_paths(
    module,
    monkeypatch,
    registry_file,
    update_times_file,
    metadata_file,
):
    upgrade_fixture_files(registry_file, update_times_file, metadata_file)
    monkeypatch.setattr(module, "REGISTRY_FILE", str(registry_file))
    monkeypatch.setattr(module, "UPDATE_TIMES_FILE", str(update_times_file))
    monkeypatch.setattr(module, "PLATFORM_METADATA_FILE", str(metadata_file))


def platform_decision(platforms, confidence="high"):
    return SimpleNamespace(
        platforms=platforms,
        confidence=confidence,
        evidence_class="test",
        linux_score=0,
        windows_score=0,
        both_score=0,
        reasons=[],
    )


def test_scanner_updates_object_description_and_platforms_without_losing_delivery(
    scan_plugins_module,
    tmp_path,
    monkeypatch,
):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    original_entry = object_entry()
    registry_file.write_text(
        json.dumps({"ExamplePlugin": original_entry}),
        encoding="utf-8",
    )
    update_times_file.write_text(
        json.dumps({"ExamplePlugin": original_entry["updated_at"]}),
        encoding="utf-8",
    )
    repo_info = {
        "archived": False,
        "disabled": False,
        "size": 100,
        "full_name": "owner/example-plugin",
        "owner": {"login": "owner"},
        "name": "example-plugin",
        "description": "Updated description",
        "default_branch": "trunk",
        "pushed_at": "2026-07-18T10:00:00Z",
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
        lambda owner, repository: repo_info,
    )
    monkeypatch.setattr(scan_plugins_module, "search_github", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_gitlab", lambda: [])
    monkeypatch.setattr(scan_plugins_module, "search_codeberg", lambda: [])
    monkeypatch.setattr(
        scan_plugins_module,
        "detect_platforms_for_repo",
        lambda *args, **kwargs: platform_decision(["windows"]),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    scan_plugins_module.main()

    saved = saved_packages(registry_file)["ExamplePlugin"]
    assert saved["repository"]["url"] == (
        "https://github.com/owner/example-plugin"
    )
    assert saved["repository"]["branch"] == "main"
    assert saved["description"] == "Updated description"
    assert saved["platforms"] == ["windows"]
    expected_delivery = copy.deepcopy(original_entry["delivery"])
    expected_delivery.pop("schema_version")
    assert saved["delivery"] == expected_delivery
    assert saved["annotations"]["review"] == original_entry["review"]
    assert saved["annotations"]["x-curator"] == original_entry["x-curator"]


class FakeTextResponse:
    def __init__(
        self,
        body=b'"""<plugin key="EXAMPLE" name="Example"></plugin>"""\n',
    ):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size=-1):
        return self.body[:size]


def test_cleanup_accepts_object_entry_and_uses_canonical_raw_url(
    cleanup_registry_module,
    tmp_path,
):
    registry_file = tmp_path / "registry.json"
    update_times_file = tmp_path / "update_times.json"
    metadata_file = tmp_path / "platform_detection.json"
    entry = object_entry(
        owner="gitlab.com/Group/Subgroup",
        repository="Example-Plugin",
        delivery=delivery_policy(release=release_policy("gitlab")),
    )
    registry_file.write_text(
        json.dumps({"ExamplePlugin": entry}),
        encoding="utf-8",
    )
    update_times_file.write_text("{}", encoding="utf-8")
    upgrade_fixture_files(registry_file, update_times_file, metadata_file)
    requests = []

    def opener(request, timeout):
        requests.append((request.full_url, timeout))
        return FakeTextResponse()

    stats = cleanup_registry_module.cleanup_registry_files(
        registry_file=str(registry_file),
        update_times_file=str(update_times_file),
        platform_metadata_file=str(metadata_file),
        apply_changes=False,
        sleep_seconds=0,
        opener=opener,
    )

    assert stats == {
        "checked": 1,
        "present": 1,
        "would_remove": 0,
        "removed": 0,
        "errors": 0,
    }
    assert requests == [
        (
            "https://gitlab.com/Group/Subgroup/Example-Plugin/-/raw/"
            "main/plugin.py",
            15,
        )
    ]
    assert set(saved_packages(registry_file)) == {"ExamplePlugin"}


def test_validator_accepts_object_entry_and_matching_platform_sidecar(
    validate_plugins_module,
    tmp_path,
    monkeypatch,
):
    entry = object_entry()
    metadata_file = tmp_path / "platform_detection.json"
    metadata_file.write_text(
        json.dumps(
            platform_metadata_v2({
                "version": 1,
                "entries": {
                    "ExamplePlugin": {
                        "registry_platforms": ["linux"],
                        "source": "reviewed",
                        "confidence": "unknown",
                        "reviewed": True,
                    }
                },
            })
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        validate_plugins_module,
        "PLATFORM_METADATA_FILE_PATH",
        str(metadata_file),
    )

    validate_plugins_module.validate_registry_entry("ExamplePlugin", entry)
    validate_plugins_module.validate_platform_metadata(
        {"ExamplePlugin": entry}
    )


def test_validator_load_registry_normalizes_object_entry_for_existing_checks(
    validate_plugins_module,
    tmp_path,
    monkeypatch,
):
    entry = object_entry()
    registry_file = tmp_path / "registry.json"
    registry_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "packages": [
                    {
                        "package_id": "ExamplePlugin",
                        "domoticz_key": "EXAMPLE",
                        "description": "Example plugin",
                        "repository": {
                            "url": "https://github.com/owner/example-plugin",
                            "branch": "main",
                        },
                        "platforms": ["linux"],
                        "delivery": {
                            "preferred": "release_if_indexed",
                            "git_supported": True,
                            "release": release_policy("github"),
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        validate_plugins_module,
        "REGISTRY_FILE_PATH",
        str(registry_file),
    )
    monkeypatch.setattr(
        validate_plugins_module,
        "UPDATE_TIMES_FILE_PATH",
        str(tmp_path / "missing-update-times.json"),
    )
    monkeypatch.setattr(
        validate_plugins_module,
        "PLATFORM_METADATA_FILE_PATH",
        str(tmp_path / "missing-platforms.json"),
    )

    loaded = validate_plugins_module.load_registry()

    assert loaded == {
        "ExamplePlugin": {
            "key": "ExamplePlugin",
            "author": "owner",
            "repository": "example-plugin",
            "description": "Example plugin",
            "branch": "main",
            "domoticz_key": "EXAMPLE",
        }
    }


def test_release_index_binding_uses_exact_registry_file_bytes(
    validate_plugins_module,
    tmp_path,
):
    registry_document = {"ExamplePlugin": object_entry()}
    registry_path = tmp_path / "registry.json"
    index_path = tmp_path / "release_index.json"
    exact_bytes = (
        json.dumps(registry_document, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    registry_path.write_bytes(exact_bytes)
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sequence": 1,
                "generated_at": "2026-07-18T12:00:00Z",
                "expires_at": "2026-07-25T12:00:00Z",
                "registry_sha256": hashlib.sha256(exact_bytes).hexdigest(),
                "plugins": {},
                "tombstones": {},
            }
        ),
        encoding="utf-8",
    )

    validate_plugins_module.validate_release_index_binding(
        registry_path=str(registry_path),
        index_path=str(index_path),
    )

    registry_path.write_bytes(
        (json.dumps(registry_document, indent=4) + "\n").encode("utf-8")
    )
    with pytest.raises(ValueError, match="registry"):
        validate_plugins_module.validate_release_index_binding(
            registry_path=str(registry_path),
            index_path=str(index_path),
        )


def permission_blocks(workflow):
    return re.findall(
        r"(?m)^([ ]*)permissions:\s*\n((?:\1[ ]{2}.+\n)+)",
        workflow,
    )


def test_weekly_workflow_generates_report_and_index_after_registry_mutation():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "scan_plugins.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    scanner_position = workflow.index(
        "python .github/scripts/scan_github_plugins.py"
    )
    report_position = workflow.index(
        "python .github/scripts/generate_release_index.py --report-only"
    )
    update_position = workflow.index(
        "python .github/scripts/generate_release_index.py --update"
    )
    pull_request_position = workflow.index(
        "peter-evans/create-pull-request@"
    )

    mutation_commands = [
        "python .github/scripts/scan_github_plugins.py",
        "python .github/scripts/cleanup_registry.py --apply",
        "python .github/scripts/detect_plugin_platforms.py",
    ]
    final_mutation_position = max(
        workflow.index(command)
        for command in mutation_commands
        if command in workflow
    )
    assert scanner_position <= final_mutation_position < report_position
    assert report_position < update_position < pull_request_position
    assert "release_index.json" in workflow[pull_request_position:]
    summarizer_path = REPO_ROOT / ".github" / "scripts" / "summarize_registry_changes.py"
    summarizer = summarizer_path.read_text(encoding="utf-8")
    assert "certified Domoticz runtime key" in summarizer
    assert "provider-neutral repository URL" in summarizer
    assert "Existing positional records remain unchanged" not in workflow


def test_weekly_workflow_uses_only_required_permissions_and_no_persisted_credentials():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "scan_plugins.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    blocks = permission_blocks(workflow)
    normalized_blocks = [
        {
            line.strip()
            for line in block.splitlines()
            if line.strip()
        }
        for _, block in blocks
    ]

    assert {"contents: read"} in normalized_blocks
    assert {
        "contents: write",
        "pull-requests: write",
    } in normalized_blocks
    assert all(
        permissions
        <= {"contents: read", "contents: write", "pull-requests: write"}
        for permissions in normalized_blocks
    )
    assert "id-token: write" not in workflow
    assert "packages: write" not in workflow
    assert "actions: write" not in workflow

    checkout_step = workflow[
        workflow.index("actions/checkout@"):
        workflow.index("actions/setup-python@")
    ]
    assert "persist-credentials: false" in checkout_step

    action_references = re.findall(r"uses:\s*([^\s#]+)", workflow)
    assert action_references
    assert all(
        re.search(r"@[0-9a-f]{40}$", reference)
        for reference in action_references
    )
