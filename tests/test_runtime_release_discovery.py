import json
import hashlib
import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from conftest import REPO_ROOT, load_module_from_path
from plugin_core_helpers import configure_home


class RecordingJsonHttpClient:
    """Write one configured response through the runtime download contract."""

    def __init__(self, contents):
        self.contents = bytes(contents)
        self.calls = []

    def download_to_path(self, url, destination, **kwargs):
        self.calls.append((url, Path(destination), dict(kwargs)))
        Path(destination).write_bytes(self.contents)
        return SimpleNamespace(
            path=str(destination),
            size=len(self.contents),
            sha256="0" * 64,
            final_url=url,
            redirects=0,
            verified=False,
        )


class RecordingProviderAdapter:
    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = []

    def resolve(self, repository, policy, transport, *, now=None):
        self.calls.append((repository, policy, transport, now))
        return self.candidate


class FailingAfterFirstProviderAdapter(RecordingProviderAdapter):
    def __init__(self, candidate):
        super().__init__(candidate)
        self.failure = None

    def resolve(self, repository, policy, transport, *, now=None):
        if self.failure is not None:
            self.calls.append((repository, policy, transport, now))
            raise self.failure
        return super().resolve(repository, policy, transport, now=now)


class RecordingCertifier:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.calls = []

    def certify(self, entry, installed, candidate):
        self.calls.append((entry, installed, candidate))
        return self.descriptor


class ArchiveHttpClient:
    def __init__(self, contents):
        self.contents = bytes(contents)
        self.calls = []

    def download_to_path(self, url, destination, **kwargs):
        self.calls.append((url, Path(destination), dict(kwargs)))
        Path(destination).write_bytes(self.contents)
        return SimpleNamespace(
            path=str(destination),
            size=len(self.contents),
            sha256=hashlib.sha256(self.contents).hexdigest(),
            final_url=url,
            redirects=0,
            verified=bool(
                kwargs.get("expected_sha256")
                or kwargs.get("expected_size") is not None
            ),
        )


def provider_candidate(module, **overrides):
    values = {
        "provider": "github",
        "repository_identity": "github.com/owner/example",
        "release_id": "github:owner/example:v2.0.0",
        "version": "2.0.0",
        "tag": "v2.0.0",
        "released_at": "2026-07-25T07:00:00Z",
        "source_revision": "b" * 40,
        "commit": "b" * 40,
        "artifact_kind": "source_zip",
        "artifact_provenance": "forge_source_archive",
        "artifact_url": (
            "https://api.github.com/repos/owner/example/zipball/" + "b" * 40
        ),
        "source_archive_url": (
            "https://api.github.com/repos/owner/example/zipball/" + "b" * 40
        ),
        "artifact_size": None,
        "provider_sha256": "",
        "source_path": ".",
        "migration_mode": "automatic",
        "migration_evidence": "commit_source_archive",
    }
    values.update(overrides)
    return module.ReleaseCandidate(**values)


def release_entry(module):
    policy = module.ReleasePolicy.from_document(
        {
            "provider": "github",
            "channel": "stable",
            "tag_pattern": r"^v\.[0-9]+(?:\.[0-9]+){1,3}$",
            "artifact": "source_zip",
            "source_path": ".",
            "mutable_paths": [],
        },
        "github.com/owner/example",
    )
    delivery = module.DeliveryPolicy(
        schema_version=module.DELIVERY_POLICY_SCHEMA_VERSION,
        preferred="release_if_indexed",
        git_supported=True,
        release=policy,
    )
    return module.RegistryEntry(
        "Example",
        "https://github.com/owner/example",
        "",
        "Example plugin",
        "master",
        delivery=delivery,
        domoticz_key="EXAMPLE",
    )


def installed_release(**overrides):
    values = {
        "plugin_key": "Example",
        "management_mode": "release",
        "repository_identity": "github.com/owner/example",
        "version": "1.0.0",
        "tag": "v1.0.0",
        "release_id": "github:owner/example:v1.0.0",
        "release_revision": 4,
        "released_at": "2026-07-20T07:00:00Z",
        "commit": "a" * 40,
        "source_revision": "a" * 40,
        "authority": "release_index",
        "candidate_fingerprint": "",
        "supersedes": [],
        "lineage_complete": True,
        "index_sequence": 42,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def certified_descriptor(module):
    return module.ReleaseDescriptor(
        revision=5,
        release_id="github:owner/example:v2.0.0",
        supersedes=["github:owner/example:v1.0.0"],
        provider="github",
        repository_identity="github.com/owner/example",
        version="2.0.0",
        tag="v2.0.0",
        released_at="2026-07-25T07:00:00Z",
        commit="b" * 40,
        source_revision="b" * 40,
        artifact=module.ReleaseArtifact(
            kind="source_zip",
            provenance="forge_source_archive",
            migration_mode="automatic",
            migration_evidence="commit_source_archive",
            url=(
                "https://api.github.com/repos/owner/example/zipball/"
                + "b" * 40
            ),
            sha256="c" * 64,
            size=1024,
            tree_sha256="d" * 64,
            root_prefix="owner-example-" + "b" * 7,
            source_path=".",
        ),
        package_id="Example",
        certified_identity=module.CertifiedReleaseIdentity(
            domoticz_key="EXAMPLE",
            plugin_py_sha256="e" * 64,
        ),
    )


def reviewed_descriptor(module):
    descriptor = certified_descriptor(module)
    descriptor.revision = 4
    descriptor.release_id = "github:owner/example:v1.0.0"
    descriptor.supersedes = ["github:owner/example:v0.9.0"]
    descriptor.version = "1.0.0"
    descriptor.tag = "v1.0.0"
    descriptor.released_at = "2026-07-20T07:00:00Z"
    descriptor.commit = "a" * 40
    descriptor.source_revision = "a" * 40
    return descriptor


def test_provider_adapters_are_shipped_as_a_runtime_module():
    module_path = REPO_ROOT / "release_providers.py"

    module = load_module_from_path(
        "runtime_release_providers_under_test",
        module_path,
    )

    assert module.GitHubReleaseAdapter().provider == "github"
    assert module.GitLabReleaseAdapter().provider == "gitlab"
    assert module.ForgejoReleaseAdapter().provider == "forgejo"
    assert module.GiteaReleaseAdapter().provider == "gitea"
    assert module.GenericManifestAdapter().provider == "generic"


@pytest.mark.parametrize(
    "document",
    (
        [{"tag_name": "v.3.1.0"}],
        {"name": "v.3.1.0"},
    ),
)
def test_runtime_json_transport_returns_bounded_json(
    plugin_core_module,
    document,
):
    contents = json.dumps(document).encode("utf-8")
    client = RecordingJsonHttpClient(contents)
    transport = plugin_core_module.SafeReleaseJsonTransport(
        http_client=client,
        max_bytes=4096,
    )

    result = transport.get_json(
        "https://api.example.test/releases",
        headers={"Accept": "application/json"},
    )

    assert result == document
    assert client.calls == [
        (
            "https://api.example.test/releases",
            client.calls[0][1],
            {
                "allowed_origins": (),
                "headers": {"Accept": "application/json"},
            },
        )
    ]
    assert not client.calls[0][1].exists()


@pytest.mark.parametrize(
    "contents",
    (
        b'{"tag":"v1.0.0","tag":"v2.0.0"}',
        b'{"outer":{"tag":"v1.0.0","tag":"v2.0.0"}}',
        b'"v1.0.0"',
        b"\xff",
        b"",
    ),
)
def test_runtime_json_transport_rejects_ambiguous_or_invalid_json(
    plugin_core_module,
    contents,
):
    transport = plugin_core_module.SafeReleaseJsonTransport(
        http_client=RecordingJsonHttpClient(contents),
        max_bytes=4096,
    )

    with pytest.raises(ValueError):
        transport.get_json("https://api.example.test/releases")


def test_runtime_json_transport_enforces_its_own_response_limit(
    plugin_core_module,
):
    transport = plugin_core_module.SafeReleaseJsonTransport(
        http_client=RecordingJsonHttpClient(b'{"value":"too large"}'),
        max_bytes=8,
    )

    with pytest.raises(ValueError, match="size limit"):
        transport.get_json("https://api.example.test/releases")


def test_runtime_discovery_certifies_and_caches_a_newer_release(
    plugin_core_module,
):
    candidate = provider_candidate(
        plugin_core_module._release_providers_module
    )
    adapter = RecordingProviderAdapter(candidate)
    descriptor = certified_descriptor(plugin_core_module)
    certifier = RecordingCertifier(descriptor)
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=certifier,
        clock=lambda: plugin_core_module.datetime(
            2026, 7, 25, 8, 0, tzinfo=plugin_core_module.timezone.utc
        ),
    )
    entry = release_entry(plugin_core_module)
    installed = installed_release()

    first = service.refresh_entry(entry, installed)
    second = service.refresh_entry(entry, installed)
    installed.supersedes = ["github:owner/example:v0.9.0"]
    third = service.refresh_entry(entry, installed)

    assert first.state == "available"
    assert first.release is descriptor
    assert second is first
    assert third.state == "available"
    assert third is not first
    assert len(adapter.calls) == 2
    assert certifier.calls == [
        (entry, installed, candidate),
        (entry, installed, candidate),
    ]
    assert adapter.calls[0][0] == {
        "repository_identity": "github.com/owner/example",
        "owner": "owner",
        "repository": "example",
        "api_base": "https://api.github.com",
        "web_base": "https://github.com",
    }
    assert adapter.calls[0][1]["tag_pattern"] == (
        r"^v\.[0-9]+(?:\.[0-9]+){1,3}$"
    )


def test_git_install_discovers_latest_release_from_reviewed_anchor(
    plugin_core_module,
):
    candidate = provider_candidate(
        plugin_core_module._release_providers_module
    )
    adapter = RecordingProviderAdapter(candidate)
    descriptor = certified_descriptor(plugin_core_module)
    certifier = RecordingCertifier(descriptor)
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=certifier,
    )
    entry = release_entry(plugin_core_module)
    reviewed = reviewed_descriptor(plugin_core_module)

    observation = service.refresh_entry(
        entry,
        reviewed,
        installed_mode="git",
    )

    assert observation.state == "available"
    assert observation.release is descriptor
    assert certifier.calls == [(entry, reviewed, candidate)]
    assert len(adapter.calls) == 1


def test_runtime_certifier_accepts_reviewed_anchor_for_git_migration(
    plugin_core_module,
    tmp_path,
):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    entry = release_entry(plugin_core_module)
    plugin.registry_entries[entry.key] = entry
    archive_buffer = io.BytesIO()
    root_prefix = "owner-example-" + "b" * 7
    plugin_source = (
        '"""\n'
        '<plugin key="EXAMPLE" name="Example" author="Owner" '
        'version="2.0.0"></plugin>\n'
        '"""\n'
    ).encode("utf-8")
    with zipfile.ZipFile(
        archive_buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(root_prefix + "/plugin.py", plugin_source)
    certifier = plugin_core_module.RuntimeReleaseCertificationService(
        plugin,
        http_client=ArchiveHttpClient(archive_buffer.getvalue()),
    )
    reviewed = reviewed_descriptor(plugin_core_module)

    descriptor = certifier.certify(
        entry,
        reviewed,
        provider_candidate(plugin_core_module._release_providers_module),
    )

    assert descriptor.revision == reviewed.revision + 1
    assert descriptor.supersedes == [
        "github:owner/example:v0.9.0",
        reviewed.release_id,
    ]
    assert descriptor.authority == "provider_live"
    assert len(descriptor.candidate_fingerprint) == 64
    assert descriptor.lineage_complete is True
    assert descriptor.anchor_release_id == reviewed.release_id
    assert descriptor.anchor_revision == reviewed.revision
    assert descriptor.anchor_authority == "release_index"


def test_runtime_certifier_rejects_candidate_from_unreviewed_provider(
    plugin_core_module,
):
    http_client = ArchiveHttpClient(b"")
    certifier = plugin_core_module.RuntimeReleaseCertificationService(
        plugin_core_module.BasePlugin(),
        http_client=http_client,
    )

    with pytest.raises(ValueError, match="reviewed release provider"):
        certifier.certify(
            release_entry(plugin_core_module),
            reviewed_descriptor(plugin_core_module),
            provider_candidate(
                plugin_core_module._release_providers_module,
                provider="gitlab",
            ),
        )

    assert http_client.calls == []


def test_runtime_discovery_tombstone_blocks_git_provider_refresh(
    plugin_core_module,
):
    adapter = RecordingProviderAdapter(
        provider_candidate(plugin_core_module._release_providers_module)
    )
    certifier = RecordingCertifier(certified_descriptor(plugin_core_module))
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=certifier,
    )

    observation = service.refresh_entry(
        release_entry(plugin_core_module),
        reviewed_descriptor(plugin_core_module),
        installed_mode="git",
        tombstone=object(),
    )

    assert observation.state == "blocked"
    assert observation.release is None
    assert adapter.calls == []
    assert certifier.calls == []


def test_runtime_discovery_preserves_verified_git_candidate_on_refresh_failure(
    plugin_core_module,
):
    current_time = [
        plugin_core_module.datetime(
            2026, 7, 25, 8, 0, tzinfo=plugin_core_module.timezone.utc
        )
    ]
    adapter = FailingAfterFirstProviderAdapter(
        provider_candidate(plugin_core_module._release_providers_module)
    )
    descriptor = certified_descriptor(plugin_core_module)
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=RecordingCertifier(descriptor),
        clock=lambda: current_time[0],
    )
    entry = release_entry(plugin_core_module)
    reviewed = reviewed_descriptor(plugin_core_module)
    first = service.refresh_entry(entry, reviewed, installed_mode="git")

    current_time[0] += plugin_core_module.timedelta(
        seconds=service.CACHE_SECONDS + 1
    )
    adapter.failure = ValueError("provider temporarily unavailable")
    refreshed = service.refresh_entry(
        entry,
        reviewed,
        installed_mode="git",
    )

    assert first.state == "available"
    assert refreshed.state == "available"
    assert refreshed.release is descriptor
    assert refreshed.stale is True
    assert "provider temporarily unavailable" in refreshed.refresh_error


def test_runtime_discovery_quarantines_a_mutated_installed_tag(
    plugin_core_module,
):
    candidate = provider_candidate(
        plugin_core_module._release_providers_module,
        release_id="github:owner/example:v1.0.0",
        version="1.0.0",
        tag="v1.0.0",
        commit="f" * 40,
        source_revision="f" * 40,
        released_at="2026-07-20T07:00:00Z",
    )
    adapter = RecordingProviderAdapter(candidate)
    certifier = RecordingCertifier(certified_descriptor(plugin_core_module))
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=certifier,
    )

    observation = service.refresh_entry(
        release_entry(plugin_core_module),
        installed_release(),
    )

    assert observation.state == "tag_mutated"
    assert observation.release is None
    assert "changed commit" in observation.message
    assert certifier.calls == []


def test_runtime_discovery_accepts_legacy_forge_anchor_without_source_revision(
    plugin_core_module,
):
    commit = "a" * 40
    candidate = provider_candidate(
        plugin_core_module._release_providers_module,
        release_id="github:owner/example:v1.0.0",
        version="1.0.0",
        tag="v1.0.0",
        commit=commit,
        source_revision=commit,
        released_at="2026-07-20T07:00:00Z",
    )
    adapter = RecordingProviderAdapter(candidate)
    certifier = RecordingCertifier(certified_descriptor(plugin_core_module))
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=certifier,
    )

    observation = service.refresh_entry(
        release_entry(plugin_core_module),
        installed_release(source_revision=""),
    )

    assert observation.state == "current"
    assert observation.release is None
    assert certifier.calls == []


def test_runtime_release_anchor_does_not_backfill_generic_source_revision(
    plugin_core_module,
):
    anchor = plugin_core_module._runtime_release_anchor(
        installed_release(source_revision=""),
        provider="generic",
    )

    assert anchor["source_revision"] == ""


def test_runtime_discovery_blocks_changed_generic_source_revision(
    plugin_core_module,
):
    commit = "a" * 40
    release_id = "generic:downloads.example.test/example:v1.0.0"
    candidate = provider_candidate(
        plugin_core_module._release_providers_module,
        provider="generic",
        release_id=release_id,
        version="1.0.0",
        tag="",
        commit=commit,
        source_revision="new-source-revision",
        released_at="2026-07-20T07:00:00Z",
        artifact_kind="generic_zip",
        artifact_provenance="generic_manifest",
    )
    adapter = RecordingProviderAdapter(candidate)
    certifier = RecordingCertifier(certified_descriptor(plugin_core_module))
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"generic": adapter},
        transport=object(),
        certifier=certifier,
    )
    entry = release_entry(plugin_core_module)
    entry.delivery.release.provider = "generic"
    entry.delivery.release.manifest_url = (
        "https://downloads.example.test/example/release.json"
    )

    observation = service.refresh_entry(
        entry,
        installed_release(
            release_id=release_id,
            commit=commit,
            source_revision="installed-source-revision",
        ),
    )

    assert observation.state == "tag_mutated"
    assert observation.release is None
    assert certifier.calls == []


@pytest.mark.parametrize(
    "candidate_overrides",
    (
        {"provider": "gitlab"},
        {"repository_identity": "github.com/other/example"},
    ),
)
def test_runtime_discovery_blocks_mismatched_candidate_identity(
    plugin_core_module,
    candidate_overrides,
):
    commit = "a" * 40
    candidate = provider_candidate(
        plugin_core_module._release_providers_module,
        release_id="github:owner/example:v1.0.0",
        version="1.0.0",
        tag="v1.0.0",
        commit=commit,
        source_revision=commit,
        released_at="2026-07-20T07:00:00Z",
        **candidate_overrides,
    )
    adapter = RecordingProviderAdapter(candidate)
    certifier = RecordingCertifier(certified_descriptor(plugin_core_module))
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=certifier,
    )

    observation = service.refresh_entry(
        release_entry(plugin_core_module),
        installed_release(source_revision=""),
    )

    assert observation.state == "blocked"
    assert observation.release is None
    assert certifier.calls == []


def test_runtime_discovery_blocks_a_republished_superseded_ancestor(
    plugin_core_module,
):
    ancestor_release_id = "github:owner/example:v0.9.0"
    candidate = provider_candidate(
        plugin_core_module._release_providers_module,
        release_id=ancestor_release_id,
        version="0.9.0",
        tag="v0.9.0",
        released_at="2026-07-30T07:00:00Z",
    )
    adapter = RecordingProviderAdapter(candidate)
    certifier = RecordingCertifier(certified_descriptor(plugin_core_module))
    service = plugin_core_module.RuntimeReleaseDiscoveryService(
        adapters={"github": adapter},
        transport=object(),
        certifier=certifier,
    )

    observation = service.refresh_entry(
        release_entry(plugin_core_module),
        installed_release(supersedes=[ancestor_release_id]),
    )

    assert observation.state == "blocked"
    assert observation.release is None
    assert "superseded" in observation.message
    assert len(adapter.calls) == 1
    assert certifier.calls == []


def test_index_tombstone_overrides_cached_provider_live_candidate(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()
    entry = release_entry(plugin_core_module)
    descriptor = certified_descriptor(plugin_core_module)
    descriptor.authority = "provider_live"
    descriptor.candidate_fingerprint = "f" * 64
    plugin.runtime_release_observations[entry.key] = (
        plugin_core_module.RuntimeReleaseObservation(
            state="available",
            release=descriptor,
            message="Verified directly from the release provider.",
            checked_at="2026-07-25T08:00:00Z",
        )
    )

    assert (
        plugin.getRuntimeReleaseCandidate(
            entry,
            installed_release(),
            tombstone=object(),
        )
        is None
    )


def test_runtime_candidate_is_lineage_bound_not_revision_ordered(
    plugin_core_module,
):
    plugin = plugin_core_module.BasePlugin()
    entry = release_entry(plugin_core_module)
    anchor = installed_release(release_revision=50)
    descriptor = certified_descriptor(plugin_core_module)
    descriptor.revision = 2
    descriptor.authority = "provider_live"
    descriptor.candidate_fingerprint = "f" * 64
    descriptor.anchor_release_id = anchor.release_id
    descriptor.anchor_revision = anchor.release_revision
    descriptor.anchor_authority = "release_index"
    descriptor.anchor_index_sequence = 42
    observation = plugin_core_module.RuntimeReleaseObservation(
        state="available",
        release=descriptor,
        message="Verified directly from the release provider.",
        checked_at="2026-07-25T08:00:00Z",
        anchor_release_id=anchor.release_id,
        anchor_revision=anchor.release_revision,
        anchor_authority="release_index",
        anchor_index_sequence=42,
    )
    plugin.runtime_release_observations[entry.key] = observation

    assert plugin.getRuntimeReleaseCandidate(entry, anchor) is descriptor

    plugin.runtime_release_observations[entry.key] = replace(
        observation,
        anchor_revision=anchor.release_revision - 1,
    )
    assert plugin.getRuntimeReleaseCandidate(entry, anchor) is None

    plugin.runtime_release_observations[entry.key] = observation
    descriptor.anchor_revision = anchor.release_revision - 1
    assert plugin.getRuntimeReleaseCandidate(entry, anchor) is None


def test_runtime_certifier_derives_checksums_tree_and_plugin_identity(
    plugin_core_module,
    tmp_path,
):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    entry = release_entry(plugin_core_module)
    plugin.registry_entries[entry.key] = entry
    archive_buffer = io.BytesIO()
    root_prefix = "owner-example-" + "b" * 7
    plugin_source = (
        '"""\n'
        '<plugin key="EXAMPLE" name="Example" author="Owner" '
        'version="2.0.0"></plugin>\n'
        '"""\n'
    ).encode("utf-8")
    with zipfile.ZipFile(
        archive_buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(root_prefix + "/plugin.py", plugin_source)
        archive.writestr(root_prefix + "/README.md", b"Example\n")
    contents = archive_buffer.getvalue()
    http_client = ArchiveHttpClient(contents)
    certifier = plugin_core_module.RuntimeReleaseCertificationService(
        plugin,
        http_client=http_client,
    )

    descriptor = certifier.certify(
        entry,
        installed_release(),
        provider_candidate(plugin_core_module._release_providers_module),
    )

    assert descriptor.revision == 5
    assert descriptor.supersedes == ["github:owner/example:v1.0.0"]
    assert descriptor.artifact.sha256 == hashlib.sha256(contents).hexdigest()
    assert descriptor.artifact.size == len(contents)
    assert descriptor.artifact.root_prefix == root_prefix
    assert descriptor.artifact.tree_sha256
    assert descriptor.authority == "provider_live"
    assert len(descriptor.candidate_fingerprint) == 64
    assert descriptor.lineage_complete is True
    assert descriptor.anchor_release_id == "github:owner/example:v1.0.0"
    assert descriptor.anchor_revision == 4
    assert descriptor.anchor_authority == "release_index"
    assert descriptor.anchor_index_sequence == 42
    assert descriptor.certified_identity.domoticz_key == "EXAMPLE"
    assert descriptor.certified_identity.plugin_py_sha256 == hashlib.sha256(
        plugin_source
    ).hexdigest()
    assert not http_client.calls[0][1].exists()
