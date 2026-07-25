from abc import ABC
from dataclasses import FrozenInstanceError, asdict, is_dataclass
from datetime import datetime, timezone

import pytest

from conftest import REPO_ROOT, load_module_from_path


EXPECTED_FIELDS = {
    "provider",
    "repository_identity",
    "release_id",
    "version",
    "tag",
    "released_at",
    "source_revision",
    "commit",
    "artifact_kind",
    "artifact_provenance",
    "artifact_url",
    "source_archive_url",
    "artifact_size",
    "provider_sha256",
    "source_path",
    "migration_mode",
    "migration_evidence",
}


@pytest.fixture
def providers_module():
    return load_module_from_path(
        "release_provider_model_under_test",
        REPO_ROOT / ".github" / "scripts" / "release_providers.py",
    )


def candidate_values(**overrides):
    values = {
        "provider": "gitlab",
        "repository_identity": "gitlab.com/group/example",
        "release_id": "gitlab:group/example:v1.4.0",
        "version": "1.4.0",
        "tag": "v1.4.0",
        "released_at": "2026-07-17T09:00:00Z",
        "source_revision": "1" * 40,
        "commit": "1" * 40,
        "artifact_kind": "source_zip",
        "artifact_provenance": "forge_source_archive",
        "artifact_url": (
            "https://gitlab.com/api/v4/projects/group%2Fexample/"
            "repository/archive.zip?sha=" + "1" * 40
        ),
        "source_archive_url": (
            "https://gitlab.com/api/v4/projects/group%2Fexample/"
            "repository/archive.zip?sha=" + "1" * 40
        ),
        "artifact_size": None,
        "provider_sha256": "",
        "source_path": ".",
        "migration_mode": "automatic",
        "migration_evidence": "commit_source_archive",
    }
    values.update(overrides)
    return values


def test_release_candidate_is_immutable_and_provider_neutral(providers_module):
    candidate = providers_module.ReleaseCandidate(**candidate_values())

    assert is_dataclass(candidate)
    assert set(asdict(candidate)) == EXPECTED_FIELDS
    with pytest.raises(FrozenInstanceError):
        candidate.version = "2.0.0"


def test_release_candidate_allows_generic_non_git_revision(providers_module):
    candidate = providers_module.ReleaseCandidate(
        **candidate_values(
            provider="generic",
            repository_identity="downloads.example.test/example",
            release_id="generic:downloads.example.test/example:v1.4.0",
            tag="",
            source_revision="release-2026-07-17-v1.4.0",
            commit="",
            artifact_kind="asset_zip",
            artifact_provenance="generic_manifest",
            artifact_url="https://downloads.example.test/example/plugin.zip",
            source_archive_url="",
            artifact_size=1234,
            provider_sha256="a" * 64,
            source_path="plugin",
            migration_mode="manual",
            migration_evidence="generic_manifest",
        )
    )

    assert candidate.commit == ""
    assert candidate.tag == ""
    assert candidate.source_archive_url == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_identity", "https://gitlab.com/group/example"),
        ("released_at", "2026-07-17"),
        ("commit", "1" * 39),
        ("artifact_url", "http://downloads.example.test/plugin.zip"),
        ("source_archive_url", "http://downloads.example.test/source.zip"),
        ("source_archive_url", "https://user@example.test/source.zip"),
        ("artifact_size", 0),
        ("artifact_size", True),
        ("provider_sha256", "A" * 64),
        ("source_path", "../plugin"),
        ("migration_mode", "sometimes"),
        ("migration_mode", ["automatic"]),
        ("migration_evidence", "tag_name"),
        ("migration_evidence", ["commit_source_archive"]),
    ],
)
def test_release_candidate_rejects_invalid_fields(
    providers_module, field, value
):
    values = candidate_values(**{field: value})
    if field == "commit":
        values["source_revision"] = value

    with pytest.raises(ValueError):
        providers_module.ReleaseCandidate(**values)


def test_automatic_migration_requires_full_commit(providers_module):
    with pytest.raises(ValueError):
        providers_module.ReleaseCandidate(
            **candidate_values(
                provider="generic",
                tag="",
                source_revision="immutable-release-1",
                commit="",
            )
        )


def test_automatic_migration_requires_source_archive_url(providers_module):
    with pytest.raises(ValueError, match="source archive URL"):
        providers_module.ReleaseCandidate(
            **candidate_values(source_archive_url="")
        )


def test_manual_forge_candidate_requires_source_archive_url(providers_module):
    with pytest.raises(ValueError, match="Forge candidates"):
        providers_module.ReleaseCandidate(
            **candidate_values(
                artifact_kind="asset_zip",
                artifact_provenance="attached_asset",
                source_archive_url="",
                migration_mode="manual",
                migration_evidence="unverified_asset",
            )
        )


def test_automatic_migration_requires_commit_source_archive_evidence(
    providers_module,
):
    with pytest.raises(ValueError, match="commit source archive"):
        providers_module.ReleaseCandidate(
            **candidate_values(migration_evidence="unverified_asset")
        )


@pytest.mark.parametrize("migration_mode", ["manual", "blocked"])
def test_commit_source_archive_must_be_automatic(
    providers_module, migration_mode
):
    with pytest.raises(ValueError, match="must use automatic"):
        providers_module.ReleaseCandidate(
            **candidate_values(migration_mode=migration_mode)
        )


def test_unverified_asset_can_be_explicitly_blocked(providers_module):
    candidate = providers_module.ReleaseCandidate(
        **candidate_values(
            artifact_kind="asset_zip",
            artifact_provenance="attached_asset",
            migration_mode="blocked",
            migration_evidence="unverified_asset",
        )
    )

    assert candidate.migration_mode == "blocked"


def test_release_provider_adapter_defines_abstract_resolve_contract(
    providers_module,
):
    assert issubclass(providers_module.ReleaseProviderAdapter, ABC)

    class MissingResolve(providers_module.ReleaseProviderAdapter):
        pass

    with pytest.raises(TypeError):
        MissingResolve()


def test_stable_release_selection_full_matches_and_sorts_timestamps(
    providers_module,
):
    releases = [
        {
            "tag_name": "v1.3.0",
            "published_at": "2026-07-16T09:00:00Z",
            "draft": False,
            "prerelease": False,
        },
        {
            "tag_name": "v1.5.0-rc.1",
            "published_at": "2026-07-18T09:00:00Z",
            "draft": False,
            "prerelease": False,
        },
        {
            "tag_name": "v2.0.0",
            "published_at": "2026-07-18T10:00:00Z",
            "draft": True,
            "prerelease": False,
        },
        {
            "tag_name": "v1.4.0",
            "published_at": "2026-07-17T09:00:00Z",
            "draft": False,
            "prerelease": False,
        },
    ]

    selected = providers_module.select_latest_stable_release(
        releases,
        r"^v[0-9]+\.[0-9]+\.[0-9]+$",
        now=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    )

    assert selected["tag_name"] == "v1.4.0"
    assert not providers_module.tag_matches_pattern(
        "prefix-v1.4.0", r"v[0-9]+\.[0-9]+\.[0-9]+"
    )


def test_default_stable_pattern_accepts_v_dot_tags_and_normalizes_version(
    providers_module,
):
    pattern = r"^v\.[0-9]+(?:\.[0-9]+){1,3}$"
    releases = [
        {
            "tag_name": "v.3.1.0",
            "published_at": "2022-08-27T10:43:53Z",
            "draft": False,
            "prerelease": False,
        },
    ]

    selected = providers_module.select_latest_stable_release(
        releases,
        pattern,
    )

    assert selected["tag_name"] == "v.3.1.0"
    assert providers_module._version_from_tag(selected["tag_name"]) == "3.1.0"
    assert not providers_module.tag_matches_pattern("v.3.2.0-beta", pattern)


def test_exact_asset_selection_rejects_missing_or_duplicate_names(
    providers_module,
):
    assets = [
        {"name": "checksums.txt"},
        {"name": "domoticz-plugin.zip", "size": 1234},
    ]

    selected = providers_module.select_exact_asset(
        assets, "domoticz-plugin.zip"
    )
    assert selected["size"] == 1234

    with pytest.raises(ValueError):
        providers_module.select_exact_asset(assets, "plugin.zip")
    with pytest.raises(ValueError):
        providers_module.select_exact_asset(
            assets + [{"name": "domoticz-plugin.zip"}],
            "domoticz-plugin.zip",
        )


def test_asset_pattern_is_full_match_and_must_select_exactly_one(
    providers_module,
):
    assets = [
        {"name": "plugin-v1.4.0.zip"},
        {"name": "plugin-v1.4.0.zip.sha256"},
    ]

    selected = providers_module.select_asset(
        assets,
        asset_pattern=r"plugin-v[0-9]+\.[0-9]+\.[0-9]+\.zip",
    )

    assert selected["name"] == "plugin-v1.4.0.zip"
    with pytest.raises(ValueError):
        providers_module.select_asset(assets, asset_pattern=r"plugin-.*")
    with pytest.raises(ValueError):
        providers_module.select_asset(
            assets,
            asset_name="plugin-v1.4.0.zip",
            asset_pattern=r".*\.zip",
        )
