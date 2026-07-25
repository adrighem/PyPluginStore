import copy
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import REPO_ROOT, load_module_from_path


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "releases"
NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
TAG_PATTERN = r"^v[0-9]+\.[0-9]+\.[0-9]+$"
EXPECTED_CANDIDATE_FIELDS = {
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


class FixtureTransport:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.requests = []

    def get_json(self, url, **kwargs):
        self.requests.append(url)
        if url not in self.responses:
            raise AssertionError("Unexpected fixture request: " + url)
        return copy.deepcopy(self.responses[url])


@pytest.fixture
def release_providers_module():
    module_path = REPO_ROOT / "release_providers.py"
    if not module_path.is_file():
        class MissingReleaseProviders:
            def __getattr__(self, name):
                raise AssertionError(
                    "Missing scanner release-provider contract: " + name
                )

        return MissingReleaseProviders()
    return load_module_from_path(
        "release_providers_under_test",
        module_path,
    )


def load_fixture(name):
    return json.loads(
        (FIXTURE_ROOT / name).read_text(encoding="utf-8")
    )


def stable_policy(artifact="source_zip"):
    policy = {
        "channel": "stable",
        "tag_pattern": TAG_PATTERN,
        "artifact": "asset_zip" if artifact.startswith("asset:") else artifact,
        "source_path": ".",
    }
    if artifact.startswith("asset:"):
        policy["asset_name"] = artifact.split(":", 1)[1]
    return policy


def github_case(module, artifact="source_zip"):
    releases_url = (
        "https://api.github.com/repos/octo/example/releases?per_page=100"
    )
    ref_url = (
        "https://api.github.com/repos/octo/example/git/ref/tags/v1.4.0"
    )
    tag_url = (
        "https://api.github.com/repos/octo/example/git/tags/"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    transport = FixtureTransport(
        {
            releases_url: load_fixture("github_releases.json"),
            ref_url: load_fixture("github_tag_ref.json"),
            tag_url: load_fixture("github_annotated_tag.json"),
        }
    )
    repository = {
        "repository_identity": "github.com/octo/example",
        "owner": "octo",
        "repository": "example",
        "api_base": "https://api.github.com",
        "web_base": "https://github.com",
    }
    expected = {
        "provider": "github",
        "release_id": "github:octo/example:v1.4.0",
        "commit": "1111111111111111111111111111111111111111",
        "source_url": (
            "https://api.github.com/repos/octo/example/zipball/"
            "1111111111111111111111111111111111111111"
        ),
        "asset_url": (
            "https://github.com/octo/example/releases/download/v1.4.0/"
            "domoticz-plugin.zip"
        ),
        "asset_size": 45678,
        "provider_sha256": "a" * 64,
        "requests": [releases_url, ref_url, tag_url],
    }
    return (
        module.GitHubReleaseAdapter(),
        repository,
        stable_policy(artifact),
        transport,
        expected,
    )


def gitlab_case(module, artifact="source_zip"):
    project = "group%2Fsubgroup%2Fexample"
    releases_url = (
        "https://gitlab.com/api/v4/projects/"
        + project
        + "/releases?order_by=released_at&sort=desc&per_page=100"
    )
    tag_url = (
        "https://gitlab.com/api/v4/projects/"
        + project
        + "/repository/tags/v1.4.0"
    )
    transport = FixtureTransport(
        {
            releases_url: load_fixture("gitlab_releases.json"),
            tag_url: load_fixture("gitlab_tag.json"),
        }
    )
    repository = {
        "repository_identity": "gitlab.com/group/subgroup/example",
        "project_path": "group/subgroup/example",
        "api_base": "https://gitlab.com/api/v4",
        "web_base": "https://gitlab.com",
    }
    expected = {
        "provider": "gitlab",
        "release_id": "gitlab:group/subgroup/example:v1.4.0",
        "commit": "2222222222222222222222222222222222222222",
        "source_url": (
            "https://gitlab.com/api/v4/projects/"
            + project
            + "/repository/archive.zip?sha="
            + "2222222222222222222222222222222222222222"
        ),
        "asset_url": (
            "https://gitlab.com/group/subgroup/example/-/releases/v1.4.0/"
            "downloads/domoticz-plugin.zip"
        ),
        "asset_size": None,
        "provider_sha256": "",
        "requests": [releases_url, tag_url],
    }
    return (
        module.GitLabReleaseAdapter(),
        repository,
        stable_policy(artifact),
        transport,
        expected,
    )


def forgejo_case(module, artifact="source_zip"):
    releases_url = (
        "https://codeberg.org/api/v1/repos/team/example/releases?"
        "page=1&limit=50"
    )
    ref_url = (
        "https://codeberg.org/api/v1/repos/team/example/git/refs/"
        "tags%2Fv1.4.0"
    )
    transport = FixtureTransport(
        {
            releases_url: load_fixture("forgejo_releases.json"),
            ref_url: load_fixture("forgejo_tag_ref.json"),
        }
    )
    repository = {
        "repository_identity": "codeberg.org/team/example",
        "owner": "team",
        "repository": "example",
        "api_base": "https://codeberg.org/api/v1",
        "web_base": "https://codeberg.org",
    }
    expected = {
        "provider": "forgejo",
        "release_id": "forgejo:codeberg.org/team/example:v1.4.0",
        "commit": "3333333333333333333333333333333333333333",
        "source_url": (
            "https://codeberg.org/api/v1/repos/team/example/archive/"
            "3333333333333333333333333333333333333333.zip"
        ),
        "asset_url": (
            "https://codeberg.org/team/example/releases/download/v1.4.0/"
            "domoticz-plugin.zip"
        ),
        "asset_size": 34567,
        "provider_sha256": "",
        "requests": [releases_url, ref_url],
    }
    return (
        module.ForgejoReleaseAdapter(),
        repository,
        stable_policy(artifact),
        transport,
        expected,
    )


def gitea_case(module, artifact="source_zip"):
    releases_url = (
        "https://gitea.example/api/v1/repos/team/example/releases?"
        "page=1&limit=50"
    )
    ref_url = (
        "https://gitea.example/api/v1/repos/team/example/git/refs/"
        "tags%2Fv1.4.0"
    )
    transport = FixtureTransport(
        {
            releases_url: load_fixture("gitea_releases.json"),
            ref_url: load_fixture("gitea_tag_ref.json"),
        }
    )
    repository = {
        "repository_identity": "gitea.example/team/example",
        "owner": "team",
        "repository": "example",
        "api_base": "https://gitea.example/api/v1",
        "web_base": "https://gitea.example",
        "release_page_size": 50,
    }
    expected = {
        "provider": "gitea",
        "release_id": "gitea:gitea.example/team/example:v1.4.0",
        "commit": "4444444444444444444444444444444444444444",
        "source_url": (
            "https://gitea.example/api/v1/repos/team/example/archive/"
            "4444444444444444444444444444444444444444.zip"
        ),
        "asset_url": (
            "https://gitea.example/team/example/releases/download/v1.4.0/"
            "domoticz-plugin.zip"
        ),
        "asset_size": 23456,
        "provider_sha256": "",
        "requests": [releases_url, ref_url],
    }
    return (
        module.GiteaReleaseAdapter(),
        repository,
        stable_policy(artifact),
        transport,
        expected,
    )


PROVIDER_CASE_BUILDERS = {
    "github": github_case,
    "gitlab": gitlab_case,
    "forgejo": forgejo_case,
    "gitea": gitea_case,
}


def resolve_case(module, provider, artifact="source_zip"):
    adapter, repository, policy, transport, expected = (
        PROVIDER_CASE_BUILDERS[provider](module, artifact=artifact)
    )
    candidate = adapter.resolve(
        repository,
        policy,
        transport,
        now=NOW,
    )
    return candidate, transport, expected


def test_all_forge_adapters_return_the_same_provider_neutral_candidate_shape(
    release_providers_module,
):
    candidates = [
        resolve_case(release_providers_module, provider)[0]
        for provider in PROVIDER_CASE_BUILDERS
    ]

    for candidate in candidates:
        assert candidate.__class__ is release_providers_module.ReleaseCandidate
        assert is_dataclass(candidate)
        assert set(asdict(candidate)) == EXPECTED_CANDIDATE_FIELDS
        assert candidate.repository_identity
        assert candidate.release_id
        assert candidate.source_revision == candidate.commit


@pytest.mark.parametrize("provider", PROVIDER_CASE_BUILDERS)
def test_forge_adapter_filters_stable_release_and_resolves_exact_commit(
    release_providers_module, provider
):
    candidate, transport, expected = resolve_case(
        release_providers_module, provider
    )

    assert candidate.provider == expected["provider"]
    assert candidate.release_id == expected["release_id"]
    assert candidate.version == "1.4.0"
    assert candidate.tag == "v1.4.0"
    assert candidate.released_at == "2026-07-17T09:00:00Z"
    assert candidate.commit == expected["commit"]
    assert candidate.source_revision == expected["commit"]
    assert candidate.artifact_kind == "source_zip"
    assert candidate.artifact_provenance == "forge_source_archive"
    assert candidate.artifact_url == expected["source_url"]
    assert candidate.source_archive_url == expected["source_url"]
    assert "v1.4.0" not in candidate.artifact_url
    assert candidate.artifact_size is None
    assert candidate.provider_sha256 == ""
    assert candidate.source_path == "."
    assert candidate.migration_mode == "automatic"
    assert candidate.migration_evidence == "commit_source_archive"
    assert transport.requests == expected["requests"]


@pytest.mark.parametrize("provider", PROVIDER_CASE_BUILDERS)
def test_forge_adapter_accepts_sha256_git_object_ids(
    release_providers_module, provider
):
    adapter, repository, policy, transport, expected = (
        PROVIDER_CASE_BUILDERS[provider](release_providers_module)
    )
    sha256_commit = "c" * 64
    if provider == "github":
        ref_response = transport.responses[expected["requests"][1]]
        ref_response["object"] = {
            "type": "commit",
            "sha": sha256_commit,
        }
    elif provider == "gitlab":
        tag_response = transport.responses[expected["requests"][-1]]
        tag_response["target"] = sha256_commit
        tag_response["commit"]["id"] = sha256_commit
    else:
        ref_response = transport.responses[expected["requests"][-1]][0]
        ref_response["object"]["sha"] = sha256_commit

    candidate = adapter.resolve(
        repository,
        policy,
        transport,
        now=NOW,
    )

    assert candidate.commit == sha256_commit
    assert candidate.source_revision == sha256_commit
    assert sha256_commit in candidate.artifact_url


@pytest.mark.parametrize("provider", ("forgejo", "gitea"))
def test_release_listing_404_is_reported_as_no_release(
    release_providers_module, provider
):
    adapter, repository, policy, _transport, _expected = (
        PROVIDER_CASE_BUILDERS[provider](release_providers_module)
    )

    class MissingReleaseCollection(Exception):
        status = 404

    class MissingReleaseTransport:
        def get_json(self, _url, **_kwargs):
            raise MissingReleaseCollection("missing release collection")

    with pytest.raises(release_providers_module.NoReleaseError) as error:
        adapter.resolve(
            repository,
            policy,
            MissingReleaseTransport(),
            now=NOW,
        )

    assert error.value.reason == "no_release"


def test_github_release_listing_404_remains_a_provider_failure(
    release_providers_module,
):
    adapter, repository, policy, _transport, _expected = github_case(
        release_providers_module
    )

    class MissingRepository(Exception):
        status = 404

    class MissingRepositoryTransport:
        def get_json(self, _url, **_kwargs):
            raise MissingRepository("repository not found")

    with pytest.raises(MissingRepository):
        adapter.resolve(
            repository,
            policy,
            MissingRepositoryTransport(),
            now=NOW,
        )


def test_no_matching_stable_release_uses_no_release_signal(
    release_providers_module,
):
    adapter, repository, policy, transport, expected = github_case(
        release_providers_module
    )
    transport.responses[expected["requests"][0]] = []

    with pytest.raises(release_providers_module.NoReleaseError) as error:
        adapter.resolve(repository, policy, transport, now=NOW)

    assert error.value.reason == "no_release"


def test_gitlab_nested_project_path_is_encoded_in_every_api_request(
    release_providers_module,
):
    _, transport, _ = resolve_case(release_providers_module, "gitlab")

    assert len(transport.requests) == 2
    assert all(
        "/projects/group%2Fsubgroup%2Fexample/" in request
        for request in transport.requests
    )
    assert all(
        "/projects/group/subgroup/example/" not in request
        for request in transport.requests
    )


def test_gitlab_future_release_is_rejected_even_without_upcoming_flag(
    release_providers_module,
):
    adapter, repository, policy, transport, expected = gitlab_case(
        release_providers_module
    )
    releases = transport.responses[expected["requests"][0]]
    future_release = next(
        release for release in releases if release["tag_name"] == "v1.6.0"
    )
    future_release["upcoming_release"] = False

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.tag == "v1.4.0"


def test_gitlab_fractional_release_timestamp_is_canonicalized(
    release_providers_module,
):
    adapter, repository, policy, transport, expected = gitlab_case(
        release_providers_module
    )
    releases = transport.responses[expected["requests"][0]]
    stable = next(
        release for release in releases if release["tag_name"] == "v1.4.0"
    )
    stable["released_at"] = "2026-07-17T09:00:00.987Z"

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.released_at == "2026-07-17T09:00:00Z"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("name", "v1.4.1", id="tag-identity"),
        pytest.param("target", "short", id="abbreviated-target"),
        pytest.param("target", "3" * 40, id="target-commit-mismatch"),
        pytest.param("commit", None, id="missing-commit"),
    ],
)
def test_gitlab_malformed_tag_response_is_rejected(
    release_providers_module, field, value
):
    adapter, repository, policy, transport, expected = gitlab_case(
        release_providers_module
    )
    tag_document = transport.responses[expected["requests"][-1]]
    tag_document[field] = value

    with pytest.raises(ValueError):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_gitlab_project_identity_mismatch_is_rejected_before_requests(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = gitlab_case(
        release_providers_module
    )
    repository["repository_identity"] = "gitlab.com/other/example"

    with pytest.raises(ValueError, match="does not match"):
        adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.requests == []


def test_gitlab_requests_include_standard_api_headers(
    release_providers_module,
):
    adapter, repository, policy, fixture_transport, _ = gitlab_case(
        release_providers_module
    )

    class HeaderRecordingTransport(FixtureTransport):
        def __init__(self, responses):
            super().__init__(responses)
            self.options = []

        def get_json(self, url, **kwargs):
            self.options.append(copy.deepcopy(kwargs))
            return super().get_json(url, **kwargs)

    transport = HeaderRecordingTransport(fixture_transport.responses)

    adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.options
    assert all(
        options["headers"]["Accept"] == "application/json"
        and options["headers"]["User-Agent"]
        for options in transport.options
    )


def test_forgejo_ref_prefix_query_requires_one_exact_match(
    release_providers_module,
):
    adapter, repository, policy, transport, expected = forgejo_case(
        release_providers_module
    )
    refs = transport.responses[expected["requests"][-1]]
    prefix_match = copy.deepcopy(refs[0])
    prefix_match["ref"] = "refs/tags/v1.4.0-rc.1"
    refs.insert(0, prefix_match)

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.tag == "v1.4.0"

    refs.append(copy.deepcopy(refs[-1]))
    with pytest.raises(ValueError, match="ambiguous"):
        adapter.resolve(repository, policy, transport, now=NOW)


@pytest.mark.parametrize(
    "refs_document",
    [
        pytest.param({}, id="missing-ref"),
        pytest.param(
            [{"ref": "refs/tags/v1.4.0", "object": None}],
            id="missing-object",
        ),
        pytest.param(
            [
                {
                    "ref": "refs/tags/v1.4.0",
                    "object": {"type": "tree", "sha": "1" * 40},
                }
            ],
            id="non-commit-target",
        ),
        pytest.param(
            [
                {
                    "ref": "refs/tags/v1.4.0",
                    "object": {"type": "commit", "sha": "short"},
                }
            ],
            id="abbreviated-commit",
        ),
    ],
)
def test_forgejo_malformed_exact_ref_is_rejected(
    release_providers_module, refs_document
):
    adapter, repository, policy, transport, expected = forgejo_case(
        release_providers_module
    )
    transport.responses[expected["requests"][-1]] = refs_document

    with pytest.raises(ValueError):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_forgejo_peels_annotated_tag_object(release_providers_module):
    adapter, repository, policy, transport, expected = forgejo_case(
        release_providers_module
    )
    tag_sha = "a" * 40
    commit = expected["commit"]
    refs = transport.responses[expected["requests"][-1]]
    refs[0]["object"] = {"type": "tag", "sha": tag_sha}
    tag_url = (
        "https://codeberg.org/api/v1/repos/team/example/git/tags/"
        + tag_sha
    )
    transport.responses[tag_url] = {
        "sha": tag_sha,
        "object": {"type": "commit", "sha": commit},
    }

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.commit == commit
    assert transport.requests[-1] == tag_url


def test_codeberg_allows_reviewed_default_provider_bases(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = forgejo_case(
        release_providers_module
    )
    del repository["api_base"]
    del repository["web_base"]

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.repository_identity == "codeberg.org/team/example"


def test_custom_forgejo_requires_explicit_bases_before_requests(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = forgejo_case(
        release_providers_module
    )
    repository["repository_identity"] = "forge.example/team/example"
    del repository["api_base"]
    del repository["web_base"]

    with pytest.raises(ValueError, match="explicit"):
        adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.requests == []


def test_custom_forgejo_requires_reviewed_page_capability(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = forgejo_case(
        release_providers_module
    )
    repository.update(
        {
            "repository_identity": "forge.example/team/example",
            "api_base": "https://forge.example/api/v1",
            "web_base": "https://forge.example",
        }
    )

    with pytest.raises(ValueError, match="release_page_size"):
        adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.requests == []


def test_forgejo_external_asset_is_not_implicitly_trusted(
    release_providers_module,
):
    adapter, repository, policy, transport, expected = forgejo_case(
        release_providers_module,
        artifact="asset:domoticz-plugin.zip",
    )
    releases = transport.responses[expected["requests"][0]]
    stable_release = next(
        release for release in releases if release["tag_name"] == "v1.4.0"
    )
    selected = next(
        asset
        for asset in stable_release["assets"]
        if asset["name"] == "domoticz-plugin.zip"
    )
    selected["type"] = "external"

    with pytest.raises(ValueError, match="External"):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_forgejo_release_listing_paginates_until_a_short_page(
    release_providers_module,
):
    adapter, repository, policy, fixture_transport, expected = forgejo_case(
        release_providers_module
    )
    first_url = expected["requests"][0]
    second_url = first_url.replace("page=1", "page=2")
    fixture_releases = fixture_transport.responses[first_url]
    prerelease = next(
        release for release in fixture_releases if release["prerelease"]
    )
    stable = next(
        release
        for release in fixture_releases
        if not release["draft"] and not release["prerelease"]
    )
    transport = FixtureTransport(
        {
            first_url: [copy.deepcopy(prerelease) for _ in range(50)],
            second_url: [copy.deepcopy(stable)],
            expected["requests"][-1]: fixture_transport.responses[
                expected["requests"][-1]
            ],
        }
    )

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.tag == "v1.4.0"
    assert transport.requests[:2] == [first_url, second_url]


def test_forgejo_requests_include_standard_api_headers(
    release_providers_module,
):
    adapter, repository, policy, fixture_transport, _ = forgejo_case(
        release_providers_module
    )

    class HeaderRecordingTransport(FixtureTransport):
        def __init__(self, responses):
            super().__init__(responses)
            self.options = []

        def get_json(self, url, **kwargs):
            self.options.append(copy.deepcopy(kwargs))
            return super().get_json(url, **kwargs)

    transport = HeaderRecordingTransport(fixture_transport.responses)

    adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.options
    assert all(
        options["headers"]["Accept"] == "application/json"
        and options["headers"]["User-Agent"]
        for options in transport.options
    )


@pytest.mark.parametrize("single_object", [False, True])
def test_gitea_accepts_polymorphic_ref_response_but_requires_exact_match(
    release_providers_module, single_object
):
    adapter, repository, policy, transport, expected = gitea_case(
        release_providers_module
    )
    refs = transport.responses[expected["requests"][-1]]
    exact_ref = refs[0]
    if single_object:
        transport.responses[expected["requests"][-1]] = exact_ref
    else:
        prefix_match = copy.deepcopy(exact_ref)
        prefix_match["ref"] = "refs/tags/v1.4.0-rc.1"
        refs.insert(0, prefix_match)

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.commit == expected["commit"]


def test_gitea_rejects_ambiguous_exact_tag_refs(release_providers_module):
    adapter, repository, policy, transport, expected = gitea_case(
        release_providers_module
    )
    refs = transport.responses[expected["requests"][-1]]
    refs.append(copy.deepcopy(refs[0]))

    with pytest.raises(ValueError, match="ambiguous"):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_gitea_peels_annotated_tag_object(release_providers_module):
    adapter, repository, policy, transport, expected = gitea_case(
        release_providers_module
    )
    tag_sha = "a" * 40
    refs = transport.responses[expected["requests"][-1]]
    refs[0]["object"] = {"type": "tag", "sha": tag_sha}
    tag_url = (
        "https://gitea.example/api/v1/repos/team/example/git/tags/"
        + tag_sha
    )
    transport.responses[tag_url] = {
        "sha": tag_sha,
        "object": {"type": "commit", "sha": expected["commit"]},
    }

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.commit == expected["commit"]
    assert transport.requests[-1] == tag_url


def test_gitea_uses_returned_uuid_attachment_url_without_synthesis(
    release_providers_module,
):
    adapter, repository, policy, transport, expected = gitea_case(
        release_providers_module,
        artifact="asset:domoticz-plugin.zip",
    )
    releases = transport.responses[expected["requests"][0]]
    stable_release = next(
        release for release in releases if release["tag_name"] == "v1.4.0"
    )
    asset = next(
        item
        for item in stable_release["assets"]
        if item["name"] == "domoticz-plugin.zip"
    )
    uuid_url = (
        "https://gitea.example/attachments/"
        "01234567-89ab-cdef-0123-456789abcdef"
    )
    asset["browser_download_url"] = uuid_url

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.artifact_url == uuid_url


def test_gitea_requires_explicit_provider_bases_before_requests(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = gitea_case(
        release_providers_module
    )
    del repository["api_base"]

    with pytest.raises(ValueError, match="explicit"):
        adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.requests == []


def test_gitea_requires_reviewed_page_capability_before_requests(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = gitea_case(
        release_providers_module
    )
    del repository["release_page_size"]

    with pytest.raises(ValueError, match="release_page_size"):
        adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.requests == []


def test_gitea_release_listing_paginates_until_a_short_page(
    release_providers_module,
):
    adapter, repository, policy, fixture_transport, expected = gitea_case(
        release_providers_module
    )
    first_url = expected["requests"][0]
    second_url = first_url.replace("page=1", "page=2")
    fixture_releases = fixture_transport.responses[first_url]
    release_candidate = next(
        release
        for release in fixture_releases
        if release["tag_name"] == "v1.5.0-rc.1"
    )
    stable = next(
        release
        for release in fixture_releases
        if release["tag_name"] == "v1.4.0"
    )
    transport = FixtureTransport(
        {
            first_url: [copy.deepcopy(release_candidate) for _ in range(50)],
            second_url: [copy.deepcopy(stable)],
            expected["requests"][-1]: fixture_transport.responses[
                expected["requests"][-1]
            ],
        }
    )

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.tag == "v1.4.0"
    assert transport.requests[:2] == [first_url, second_url]


def test_gitea_requests_include_standard_api_headers(
    release_providers_module,
):
    adapter, repository, policy, fixture_transport, _ = gitea_case(
        release_providers_module
    )

    class HeaderRecordingTransport(FixtureTransport):
        def __init__(self, responses):
            super().__init__(responses)
            self.options = []

        def get_json(self, url, **kwargs):
            self.options.append(copy.deepcopy(kwargs))
            return super().get_json(url, **kwargs)

    transport = HeaderRecordingTransport(fixture_transport.responses)

    adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.options
    assert all(
        options["headers"]["Accept"] == "application/json"
        and options["headers"]["User-Agent"]
        for options in transport.options
    )


def test_forgejo_and_gitea_use_distinct_adapter_classes(
    release_providers_module,
):
    forgejo = release_providers_module.ForgejoReleaseAdapter()
    gitea = release_providers_module.GiteaReleaseAdapter()

    assert type(forgejo) is release_providers_module.ForgejoReleaseAdapter
    assert type(gitea) is release_providers_module.GiteaReleaseAdapter
    assert type(forgejo) is not type(gitea)


@pytest.mark.parametrize("provider", PROVIDER_CASE_BUILDERS)
def test_configured_attached_zip_is_selected_by_exact_name(
    release_providers_module, provider
):
    candidate, _, expected = resolve_case(
        release_providers_module,
        provider,
        artifact="asset:domoticz-plugin.zip",
    )

    assert candidate.artifact_url == expected["asset_url"]
    assert candidate.source_archive_url == expected["source_url"]
    assert candidate.artifact_kind == "asset_zip"
    assert candidate.artifact_provenance == "attached_asset"
    assert candidate.artifact_size == expected["asset_size"]
    assert candidate.provider_sha256 == expected["provider_sha256"]
    assert candidate.migration_mode == "manual"
    assert candidate.migration_evidence == "unverified_asset"
    assert candidate.commit == expected["commit"]


def test_missing_configured_asset_is_rejected(release_providers_module):
    adapter, repository, policy, transport, _ = github_case(
        release_providers_module,
        artifact="asset:not-published.zip",
    )

    with pytest.raises(ValueError):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_github_annotated_tag_cycle_is_rejected(release_providers_module):
    adapter, repository, policy, transport, expected = github_case(
        release_providers_module
    )
    first_sha = "a" * 40
    second_sha = "b" * 40
    first_tag_url = expected["requests"][-1]
    second_tag_url = (
        "https://api.github.com/repos/octo/example/git/tags/" + second_sha
    )
    transport.responses[first_tag_url] = {
        "sha": first_sha,
        "object": {"type": "tag", "sha": second_sha},
    }
    transport.responses[second_tag_url] = {
        "sha": second_sha,
        "object": {"type": "tag", "sha": first_sha},
    }

    with pytest.raises(ValueError, match="cycle"):
        adapter.resolve(repository, policy, transport, now=NOW)


@pytest.mark.parametrize(
    "target",
    [
        pytest.param({}, id="missing-type-and-sha"),
        pytest.param(
            {"type": "tree", "sha": "1" * 40},
            id="non-commit-target",
        ),
        pytest.param(
            {"type": "commit", "sha": "short"},
            id="abbreviated-commit",
        ),
    ],
)
def test_github_malformed_tag_target_is_rejected(
    release_providers_module, target
):
    adapter, repository, policy, transport, expected = github_case(
        release_providers_module
    )
    transport.responses[expected["requests"][1]]["object"] = target

    with pytest.raises(ValueError):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_github_rejects_malformed_provider_asset_digest(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = github_case(
        release_providers_module,
        artifact="asset:domoticz-plugin.zip",
    )
    releases = transport.responses[next(iter(transport.responses))]
    stable_release = next(
        release for release in releases if release["tag_name"] == "v1.4.0"
    )
    selected_asset = next(
        asset
        for asset in stable_release["assets"]
        if asset["name"] == "domoticz-plugin.zip"
    )
    selected_asset["digest"] = "sha512:" + "a" * 128

    with pytest.raises(ValueError, match="SHA-256"):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_github_requests_include_standard_api_headers(
    release_providers_module,
):
    adapter, repository, policy, fixture_transport, _ = github_case(
        release_providers_module
    )

    class HeaderRecordingTransport(FixtureTransport):
        def __init__(self, responses):
            super().__init__(responses)
            self.options = []

        def get_json(self, url, **kwargs):
            self.options.append(copy.deepcopy(kwargs))
            return super().get_json(url, **kwargs)

    transport = HeaderRecordingTransport(fixture_transport.responses)

    adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.options
    assert all(
        options["headers"]["Accept"] == "application/vnd.github+json"
        and options["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
        and options["headers"]["User-Agent"]
        for options in transport.options
    )


def test_github_repository_identity_must_match_configured_web_host(
    release_providers_module,
):
    adapter, repository, policy, transport, _ = github_case(
        release_providers_module
    )
    repository["repository_identity"] = "mirror.example/octo/example"

    with pytest.raises(ValueError, match="web_base"):
        adapter.resolve(repository, policy, transport, now=NOW)


def generic_case(module, manifest=None, manifest_url=None):
    manifest_url = manifest_url or (
        "https://downloads.example.test/example/release-manifest.json"
    )
    transport = FixtureTransport(
        {
            manifest_url: (
                load_fixture("generic_manifest.json")
                if manifest is None
                else manifest
            )
        }
    )
    repository = {
        "repository_identity": "downloads.example.test/example",
    }
    policy = {
        "channel": "stable",
        "manifest_url": manifest_url,
    }
    return (
        module.GenericManifestAdapter(),
        repository,
        policy,
        transport,
    )


def test_generic_https_manifest_normalizes_without_forge_api_calls(
    release_providers_module,
):
    adapter, repository, policy, transport = generic_case(
        release_providers_module
    )

    candidate = adapter.resolve(
        repository, policy, transport, now=NOW
    )

    assert candidate.__class__ is release_providers_module.ReleaseCandidate
    assert set(asdict(candidate)) == EXPECTED_CANDIDATE_FIELDS
    assert candidate.provider == "generic"
    assert candidate.repository_identity == "downloads.example.test/example"
    assert (
        candidate.release_id
        == "generic:downloads.example.test/example:v1.4.0"
    )
    assert candidate.version == "1.4.0"
    assert candidate.tag == ""
    assert candidate.released_at == "2026-07-17T09:00:00Z"
    assert candidate.source_revision == "release-2026-07-17-v1.4.0"
    assert candidate.commit == ""
    assert candidate.artifact_kind == "asset_zip"
    assert candidate.artifact_provenance == "generic_manifest"
    assert candidate.artifact_url.endswith("/domoticz-plugin.zip")
    assert candidate.source_archive_url == ""
    assert candidate.artifact_size == 56789
    assert candidate.provider_sha256 == "b" * 64
    assert candidate.source_path == "plugin"
    assert candidate.migration_mode == "manual"
    assert candidate.migration_evidence == "generic_manifest"
    assert transport.requests == [policy["manifest_url"]]


def test_generic_manifest_404_remains_a_configuration_or_provider_failure(
    release_providers_module,
):
    adapter, repository, policy, _transport = generic_case(
        release_providers_module
    )

    class MissingManifest(Exception):
        status = 404

    class MissingManifestTransport:
        def get_json(self, _url, **_kwargs):
            raise MissingManifest("missing release manifest")

    with pytest.raises(MissingManifest):
        adapter.resolve(
            repository,
            policy,
            MissingManifestTransport(),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("release_id", ""),
        ("released_at", "not-a-timestamp"),
        ("url", "http://downloads.example.test/plugin.zip"),
        ("sha256", "A" * 64),
        ("size", 0),
        ("source_revision", ""),
    ],
    ids=(
        "schema",
        "release-id",
        "timestamp",
        "artifact-https",
        "digest",
        "size",
        "source-revision",
    ),
)
def test_generic_manifest_rejects_invalid_or_mutable_artifact_metadata(
    release_providers_module, field, value
):
    manifest = load_fixture("generic_manifest.json")
    manifest[field] = value
    adapter, repository, policy, transport = generic_case(
        release_providers_module, manifest=manifest
    )

    with pytest.raises(ValueError):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_generic_manifest_location_must_use_https(release_providers_module):
    insecure_url = "http://downloads.example.test/release-manifest.json"
    adapter, repository, policy, transport = generic_case(
        release_providers_module,
        manifest_url=insecure_url,
    )

    with pytest.raises(ValueError):
        adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.requests == []


def test_generic_manifest_is_bound_to_configured_repository_identity(
    release_providers_module,
):
    manifest = load_fixture("generic_manifest.json")
    manifest["release_id"] = "generic:downloads.example.test/other:v1.4.0"
    adapter, repository, policy, transport = generic_case(
        release_providers_module, manifest=manifest
    )

    with pytest.raises(ValueError, match="repository_identity"):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_generic_manifest_host_is_validated_before_request(
    release_providers_module,
):
    adapter, repository, policy, transport = generic_case(
        release_providers_module,
        manifest_url="https://other.example.test/release-manifest.json",
    )

    with pytest.raises(ValueError, match="host"):
        adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.requests == []


def test_generic_manifest_rejects_unknown_v1_fields(
    release_providers_module,
):
    manifest = load_fixture("generic_manifest.json")
    manifest["unreviewed_extension"] = True
    adapter, repository, policy, transport = generic_case(
        release_providers_module, manifest=manifest
    )

    with pytest.raises(ValueError, match="unknown"):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_generic_manifest_source_path_must_match_reviewed_policy(
    release_providers_module,
):
    adapter, repository, policy, transport = generic_case(
        release_providers_module
    )
    policy["source_path"] = "."

    with pytest.raises(ValueError, match="source_path"):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_generic_manifest_rejects_future_release_timestamp(
    release_providers_module,
):
    manifest = load_fixture("generic_manifest.json")
    manifest["released_at"] = "2026-07-19T09:00:00Z"
    adapter, repository, policy, transport = generic_case(
        release_providers_module, manifest=manifest
    )

    with pytest.raises(ValueError, match="future"):
        adapter.resolve(repository, policy, transport, now=NOW)


def test_generic_optional_commit_is_provenance_not_migration_approval(
    release_providers_module,
):
    manifest = load_fixture("generic_manifest.json")
    manifest["commit"] = "5" * 40
    adapter, repository, policy, transport = generic_case(
        release_providers_module, manifest=manifest
    )

    candidate = adapter.resolve(repository, policy, transport, now=NOW)

    assert candidate.commit == "5" * 40
    assert candidate.source_revision == "release-2026-07-17-v1.4.0"
    assert candidate.migration_mode == "manual"
    assert candidate.migration_evidence == "generic_manifest"


def test_generic_manifest_requests_json_with_standard_headers(
    release_providers_module,
):
    adapter, repository, policy, fixture_transport = generic_case(
        release_providers_module
    )

    class HeaderRecordingTransport(FixtureTransport):
        def __init__(self, responses):
            super().__init__(responses)
            self.options = []

        def get_json(self, url, **kwargs):
            self.options.append(copy.deepcopy(kwargs))
            return super().get_json(url, **kwargs)

    transport = HeaderRecordingTransport(fixture_transport.responses)

    adapter.resolve(repository, policy, transport, now=NOW)

    assert transport.options == [
        {
            "headers": {
                "Accept": "application/json",
                "User-Agent": "PyPluginStore-Release-Scanner",
            }
        }
    ]
