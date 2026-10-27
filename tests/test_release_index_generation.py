import copy
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import REPO_ROOT, load_module_from_path


NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
VALIDITY_SECONDS = 7 * 24 * 60 * 60
COMMIT_1 = "1" * 40
COMMIT_2 = "2" * 40
PLUGIN_PY = b'''"""<plugin key="EXAMPLE" name="Example"></plugin>"""\n'''


@dataclass(frozen=True)
class Candidate:
    provider: str
    repository_identity: str
    release_id: str
    version: str
    tag: str
    released_at: str
    source_revision: str
    commit: str
    artifact_kind: str
    artifact_provenance: str
    artifact_url: str
    source_archive_url: str
    artifact_size: object
    provider_sha256: str
    source_path: str
    migration_mode: str
    migration_evidence: str


@dataclass(frozen=True)
class Download:
    data: bytes
    size: int
    sha256: str
    final_url: str
    redirects: int = 0
    verified: bool = False


class RecordingProvider:
    def __init__(self, outcomes):
        self.outcomes = {
            identity: list(values)
            for identity, values in outcomes.items()
        }
        self.calls = []

    def resolve(self, repository, policy, transport, now):
        identity = repository["repository_identity"]
        self.calls.append(
            {
                "repository": copy.deepcopy(repository),
                "policy": copy.deepcopy(policy),
                "transport": transport,
                "now": now,
            }
        )
        if identity not in self.outcomes or not self.outcomes[identity]:
            raise AssertionError("Unexpected provider resolution: " + identity)
        outcome = self.outcomes[identity].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ExplodingProvider:
    def __init__(self):
        self.calls = []

    def resolve(self, repository, policy, transport, now):
        self.calls.append(repository["repository_identity"])
        raise AssertionError("provider cache was not used")


class RecordingHttpClient:
    def __init__(self, artifacts):
        self.artifacts = {
            url: list(values)
            for url, values in artifacts.items()
        }
        self.calls = []

    def download(
        self,
        url,
        *,
        headers=None,
        expected_sha256=None,
        expected_size=None,
        allowed_origins=(),
    ):
        self.calls.append(
            {
                "url": url,
                "headers": copy.deepcopy(headers),
                "expected_sha256": expected_sha256,
                "expected_size": expected_size,
                "allowed_origins": list(allowed_origins),
            }
        )
        if url not in self.artifacts or not self.artifacts[url]:
            raise AssertionError("Unexpected artifact download: " + url)
        outcome = self.artifacts[url].pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        data = bytes(outcome)
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 and digest != expected_sha256:
            raise ValueError("provider digest mismatch")
        if expected_size is not None and len(data) != expected_size:
            raise ValueError("provider length mismatch")
        return Download(
            data=data,
            size=len(data),
            sha256=digest,
            final_url=url,
            verified=bool(expected_sha256 or expected_size),
        )


class ExplodingHttpClient:
    def __init__(self):
        self.calls = []

    def download(self, url, **kwargs):
        self.calls.append(url)
        raise AssertionError("artifact certification cache was not used")


@pytest.fixture
def release_index_generation_module():
    module_path = (
        REPO_ROOT / ".github" / "scripts" / "generate_release_index.py"
    )
    if not module_path.is_file():
        class MissingReleaseIndexGeneration:
            def __getattr__(self, name):
                raise AssertionError(
                    "Missing release index generation contract: " + name
                )

        return MissingReleaseIndexGeneration()
    return load_module_from_path(
        "release_index_generation_under_test",
        module_path,
    )


def registry_entry(
    owner="owner",
    repository="example-plugin",
    provider="github",
    *,
    source_path=".",
    artifact="source_zip",
    allowed_origins=None,
    preferred="release_if_indexed",
    domoticz_key=None,
):
    release = {
        "provider": provider,
        "channel": "stable",
        "tag_pattern": r"^v[0-9]+\.[0-9]+\.[0-9]+$",
        "artifact": artifact,
        "source_path": source_path,
        "mutable_paths": [],
    }
    if allowed_origins:
        release["allowed_origins"] = list(allowed_origins)
    delivery = {
        "schema_version": 1,
        "preferred": preferred,
        "git_supported": True,
    }
    if preferred != "git":
        delivery["release"] = release
    entry = {
        "owner": owner,
        "repository": repository,
        "description": "Example plugin",
        "branch": "main",
        "delivery": delivery,
    }
    if domoticz_key is not None:
        entry["domoticz_key"] = domoticz_key
    return entry


def registry_bytes(entries=None, *, indent=2):
    entries = entries or {"ExamplePlugin": registry_entry()}
    return (
        json.dumps(entries, indent=indent, sort_keys=False) + "\n"
    ).encode("utf-8")


def registry_v2_bytes(
    *,
    package_id="ExamplePlugin",
    domoticz_key="EXAMPLE",
    repository_url="https://github.com/owner/example-plugin",
    previous_identities=None,
):
    entry = registry_entry(domoticz_key=domoticz_key)
    delivery = copy.deepcopy(entry["delivery"])
    delivery.pop("schema_version", None)
    repository = {"url": repository_url, "branch": entry["branch"]}
    if previous_identities is not None:
        repository["previous_identities"] = list(previous_identities)
    return (
        json.dumps(
            {
                "schema_version": 2,
                "packages": [
                    {
                        "package_id": package_id,
                        "domoticz_key": domoticz_key,
                        "description": entry["description"],
                        "repository": repository,
                        "platforms": ["linux", "windows"],
                        "delivery": delivery,
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def candidate(
    *,
    provider="github",
    repository_identity="github.com/owner/example-plugin",
    release_id="github:owner/example-plugin:v1.0.0",
    version="1.0.0",
    tag="v1.0.0",
    commit=COMMIT_1,
    artifact_kind="source_zip",
    artifact_provenance="forge_source_archive",
    artifact_url=None,
    source_archive_url=None,
    artifact_size=None,
    provider_sha256="",
    source_path=".",
    migration_eligible=True,
    migration_mode=None,
    migration_evidence=None,
    source_revision=None,
):
    if artifact_url is None:
        artifact_url = (
            "https://downloads.example.test/"
            + repository_identity.replace("/", "-")
            + "/"
            + commit
            + ".zip"
        )
    if source_revision is None:
        source_revision = commit
    if source_archive_url is None:
        source_archive_url = "" if provider == "generic" else artifact_url
    if migration_mode is None:
        migration_mode = "automatic" if migration_eligible else "manual"
    if migration_evidence is None:
        migration_evidence = (
            "commit_source_archive"
            if migration_mode == "automatic"
            else (
                "generic_manifest"
                if provider == "generic"
                else "unverified_asset"
            )
        )
    return Candidate(
        provider=provider,
        repository_identity=repository_identity,
        release_id=release_id,
        version=version,
        tag=tag,
        released_at="2026-07-18T10:00:00Z",
        source_revision=source_revision,
        commit=commit,
        artifact_kind=artifact_kind,
        artifact_provenance=artifact_provenance,
        artifact_url=artifact_url,
        source_archive_url=source_archive_url,
        artifact_size=artifact_size,
        provider_sha256=provider_sha256,
        source_path=source_path,
        migration_mode=migration_mode,
        migration_evidence=migration_evidence,
    )


def zip_archive(
    files=None,
    *,
    root_prefix="example-plugin-" + COMMIT_1,
    compression=zipfile.ZIP_DEFLATED,
    timestamp=(2026, 7, 18, 10, 0, 0),
    reverse=False,
):
    files = files or {
        "plugin.py": PLUGIN_PY,
        "README.md": b"Example plugin\n",
    }
    items = list(files.items())
    if reverse:
        items.reverse()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for relative_path, contents in items:
            info = zipfile.ZipInfo(root_prefix + "/" + relative_path)
            info.date_time = timestamp
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, contents)
    return output.getvalue()


def raw_zip_archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for entry in entries:
            name, contents = entry[:2]
            mode = entry[2] if len(entry) > 2 else 0o100644
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = mode << 16
            archive.writestr(info, contents)
    return output.getvalue()


def canonical_tree_sha256(files=None):
    files = files or {
        "plugin.py": PLUGIN_PY,
        "README.md": b"Example plugin\n",
    }
    records = []
    for path, contents in files.items():
        digest = hashlib.sha256(contents).hexdigest()
        records.append(
            path.encode("utf-8")
            + b"\0"
            + str(len(contents)).encode("ascii")
            + b"\0"
            + digest.encode("ascii")
            + b"\n"
        )
    return hashlib.sha256(b"".join(sorted(records))).hexdigest()


def artifact_document(
    archive,
    *,
    tree_sha256=None,
    root_prefix="example-plugin-" + COMMIT_1,
    kind="source_zip",
    provenance="forge_source_archive",
    migration_eligible=True,
    migration_mode=None,
    migration_evidence=None,
    url=None,
    source_path=".",
):
    if migration_mode is None:
        migration_mode = "automatic" if migration_eligible else "manual"
    if migration_evidence is None:
        migration_evidence = (
            "commit_source_archive"
            if migration_mode == "automatic"
            else (
                "generic_manifest"
                if provenance == "generic_manifest"
                else "unverified_asset"
            )
        )
    return {
        "kind": kind,
        "provenance": provenance,
        "migration": {
            "mode": migration_mode,
            "evidence": migration_evidence,
        },
        "url": url
        or (
            "https://downloads.example.test/github.com-owner-example-plugin/"
            + COMMIT_1
            + ".zip"
        ),
        "sha256": hashlib.sha256(archive).hexdigest(),
        "size": len(archive),
        "tree_sha256": tree_sha256 or canonical_tree_sha256(),
        "root_prefix": root_prefix,
        "source_path": source_path,
    }


def release_entry(
    archive,
    *,
    revision=1,
    release_id="github:owner/example-plugin:v1.0.0",
    supersedes=None,
    provider="github",
    repository_identity="github.com/owner/example-plugin",
    version="1.0.0",
    tag="v1.0.0",
    commit=COMMIT_1,
    source_revision=None,
    artifact=None,
):
    document = {
        "revision": revision,
        "release_id": release_id,
        "supersedes": list(supersedes or []),
        "provider": provider,
        "repository_identity": repository_identity,
        "version": version,
        "tag": tag,
        "released_at": "2026-07-18T10:00:00Z",
        "commit": commit,
        "certified_identity": {
            "domoticz_key": "EXAMPLE",
            "plugin_py_sha256": hashlib.sha256(PLUGIN_PY).hexdigest(),
        },
        "artifact": artifact or artifact_document(archive),
    }
    if source_revision:
        document["source_revision"] = source_revision
    return document


def previous_index(
    registry_contents,
    plugins,
    *,
    sequence=4,
    tombstones=None,
):
    return {
        "schema_version": 2,
        "sequence": sequence,
        "generated_at": "2026-07-17T12:00:00Z",
        "expires_at": "2026-07-24T12:00:00Z",
        "registry_sha256": hashlib.sha256(registry_contents).hexdigest(),
        "releases": [
            {"package_id": package_id, **copy.deepcopy(entry)}
            for package_id, entry in sorted(plugins.items())
        ],
        "tombstones": [
            {"package_id": package_id, **copy.deepcopy(entry)}
            for package_id, entry in sorted((tombstones or {}).items())
        ],
    }


def legacy_previous_index(
    registry_contents,
    plugins,
    *,
    sequence=4,
    tombstones=None,
):
    legacy_plugins = copy.deepcopy(plugins)
    for entry in legacy_plugins.values():
        entry.pop("certified_identity", None)
        migration = entry["artifact"].pop("migration")
        entry["artifact"]["migration_eligible"] = (
            migration["mode"] == "automatic"
        )
    return {
        "schema_version": 1,
        "sequence": sequence,
        "generated_at": "2026-07-17T12:00:00Z",
        "expires_at": "2026-07-24T12:00:00Z",
        "registry_sha256": hashlib.sha256(registry_contents).hexdigest(),
        "plugins": legacy_plugins,
        "tombstones": copy.deepcopy(tombstones or {}),
    }


def release_map(document):
    return {entry["package_id"]: entry for entry in document["releases"]}


def release_payload(document, package_id):
    entry = copy.deepcopy(release_map(document)[package_id])
    entry.pop("package_id")
    return entry


def tombstone_map(document):
    return {entry["package_id"]: entry for entry in document["tombstones"]}


def make_generator(
    module,
    providers,
    http_client,
    *,
    cache=None,
    now=NOW,
):
    return module.ReleaseIndexGenerator(
        providers=providers,
        http_client=http_client,
        clock=lambda: now,
        validity_seconds=VALIDITY_SECONDS,
        cache=cache,
    )


def status(result, plugin_key="ExamplePlugin"):
    return result.report["plugins"][plugin_key]["status"]


def generate_single(
    module,
    selected_candidate,
    archive,
    *,
    registry_contents=None,
    previous=None,
    cache=None,
):
    registry_contents = registry_contents or registry_bytes()
    provider = RecordingProvider(
        {
            selected_candidate.repository_identity: [selected_candidate],
        }
    )
    http_client = RecordingHttpClient(
        {selected_candidate.artifact_url: [archive]}
    )
    generator = make_generator(
        module,
        {selected_candidate.provider: provider},
        http_client,
        cache=cache,
    )
    result = generator.generate(
        registry_bytes=registry_contents,
        previous_index=previous,
        report_only=True,
    )
    return result, provider, http_client


def test_index_serialization_is_deterministic_sorted_and_bound_to_exact_registry_bytes(
    release_index_generation_module,
):
    entries = {
        "ZuluPlugin": registry_entry(
            owner="zulu",
            repository="plugin",
        ),
        "AlphaPlugin": registry_entry(
            owner="alpha",
            repository="plugin",
        ),
    }
    contents = registry_bytes(entries)
    zulu = candidate(
        repository_identity="github.com/zulu/plugin",
        release_id="github:zulu/plugin:v1.0.0",
        artifact_url="https://downloads.example.test/zulu.zip",
    )
    alpha = candidate(
        repository_identity="github.com/alpha/plugin",
        release_id="github:alpha/plugin:v1.0.0",
        artifact_url="https://downloads.example.test/alpha.zip",
    )
    archive = zip_archive(root_prefix="plugin-" + COMMIT_1)

    def one_generation():
        provider = RecordingProvider(
            {
                zulu.repository_identity: [zulu],
                alpha.repository_identity: [alpha],
            }
        )
        http_client = RecordingHttpClient(
            {
                zulu.artifact_url: [archive],
                alpha.artifact_url: [archive],
            }
        )
        return make_generator(
            release_index_generation_module,
            {"github": provider},
            http_client,
        ).generate(registry_bytes=contents, report_only=True)

    first = one_generation()
    second = one_generation()

    assert first.index_bytes == second.index_bytes
    assert first.document == second.document
    assert first.document["schema_version"] == 2
    assert set(first.document) == {
        "schema_version",
        "sequence",
        "generated_at",
        "expires_at",
        "registry_sha256",
        "releases",
        "tombstones",
    }
    assert first.document["sequence"] == 1
    assert first.document["generated_at"] == "2026-07-18T12:00:00Z"
    assert first.document["expires_at"] == "2026-07-25T12:00:00Z"
    assert first.document["registry_sha256"] == hashlib.sha256(
        contents
    ).hexdigest()
    assert [entry["package_id"] for entry in first.document["releases"]] == [
        "AlphaPlugin",
        "ZuluPlugin",
    ]
    assert first.index_bytes == (
        json.dumps(
            first.document,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    reformatted_contents = registry_bytes(entries, indent=4)
    reformatted = make_generator(
        release_index_generation_module,
        {
            "github": RecordingProvider(
                {
                    zulu.repository_identity: [zulu],
                    alpha.repository_identity: [alpha],
                }
            )
        },
        RecordingHttpClient(
            {
                zulu.artifact_url: [archive],
                alpha.artifact_url: [archive],
            }
        ),
    ).generate(registry_bytes=reformatted_contents, report_only=True)
    assert reformatted.document["registry_sha256"] == hashlib.sha256(
        reformatted_contents
    ).hexdigest()
    assert reformatted.document["registry_sha256"] != first.document[
        "registry_sha256"
    ]


def test_source_archive_certification_records_transport_and_canonical_tree_identity(
    release_index_generation_module,
):
    selected = candidate()
    archive = zip_archive()

    result, _, http_client = generate_single(
        release_index_generation_module,
        selected,
        archive,
    )
    entry = release_map(result.document)["ExamplePlugin"]
    artifact = entry["artifact"]
    assert entry["revision"] == 1
    assert entry["supersedes"] == []
    assert entry["commit"] == COMMIT_1
    assert "source_revision" not in entry
    assert artifact == {
        "kind": "source_zip",
        "provenance": "forge_source_archive",
        "migration": {
            "mode": "automatic",
            "evidence": "commit_source_archive",
        },
        "url": selected.artifact_url,
        "sha256": hashlib.sha256(archive).hexdigest(),
        "size": len(archive),
        "tree_sha256": canonical_tree_sha256(),
        "root_prefix": "example-plugin-" + COMMIT_1,
        "source_path": ".",
    }
    assert entry["certified_identity"] == {
        "domoticz_key": "EXAMPLE",
        "plugin_py_sha256": hashlib.sha256(PLUGIN_PY).hexdigest(),
    }
    assert "migration_eligible" not in json.dumps(result.document)
    assert "plugin_key" not in json.dumps(result.document)
    assert http_client.calls == [
        {
            "url": selected.artifact_url,
            "headers": {
                "User-Agent": "PyPluginStore-Release-Scanner",
            },
            "expected_sha256": None,
            "expected_size": None,
            "allowed_origins": [],
        }
    ]
    assert status(result) == "certified_new"
    assert result.report["summary"] == {"certified_new": 1}


def test_generator_consumes_strict_registry_v2_package_records(
    release_index_generation_module,
):
    contents = registry_v2_bytes()
    selected = candidate()
    result, _provider, _http = generate_single(
        release_index_generation_module,
        selected,
        zip_archive(),
        registry_contents=contents,
    )

    assert status(result) == "certified_new"
    assert result.document["registry_sha256"] == hashlib.sha256(
        contents
    ).hexdigest()
    assert [
        release["package_id"] for release in result.document["releases"]
    ] == ["ExamplePlugin"]
    release = result.document["releases"][0]
    assert release["certified_identity"] == {
        "domoticz_key": "EXAMPLE",
        "plugin_py_sha256": hashlib.sha256(PLUGIN_PY).hexdigest(),
    }


def test_certification_rejects_domoticz_identity_mismatch(
    release_index_generation_module,
):
    contents = registry_bytes(
        {"ExamplePlugin": registry_entry(domoticz_key="SMA")}
    )

    result, _, _ = generate_single(
        release_index_generation_module,
        candidate(),
        zip_archive(),
        registry_contents=contents,
    )

    assert result.document["releases"] == []
    assert status(result) == "certification_failed"
    assert "Domoticz key" in result.report["plugins"]["ExamplePlugin"][
        "detail"
    ]


def test_legacy_previous_index_is_read_only_and_preserves_lineage_in_v2(
    release_index_generation_module,
):
    contents = registry_bytes()
    old_archive = zip_archive()
    old_entry = release_entry(old_archive, revision=7)
    legacy = legacy_previous_index(
        contents,
        {"ExamplePlugin": old_entry},
    )
    original_legacy = copy.deepcopy(legacy)
    selected = candidate(
        release_id="github:owner/example-plugin:v2.0.0",
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        artifact_url="https://downloads.example.test/v2.zip",
    )
    new_archive = zip_archive(root_prefix="example-plugin-" + COMMIT_2)
    provider = RecordingProvider(
        {selected.repository_identity: [selected]}
    )
    http_client = RecordingHttpClient(
        {
            old_entry["artifact"]["url"]: [old_archive],
            selected.artifact_url: [new_archive],
        }
    )

    result = make_generator(
        release_index_generation_module,
        {"github": provider},
        http_client,
    ).generate(
        registry_bytes=contents,
        previous_index=legacy,
        report_only=True,
    )

    current = release_map(result.document)["ExamplePlugin"]
    assert legacy == original_legacy
    assert result.document["schema_version"] == 2
    assert current["revision"] == 8
    assert current["supersedes"] == [old_entry["release_id"]]
    assert current["certified_identity"]["domoticz_key"] == "EXAMPLE"
    serialized = json.dumps(result.document)
    assert "migration_eligible" not in serialized
    assert '"plugins"' not in serialized
    assert status(result) == "certified_update"
    assert result.report["summary"] == {"certified_update": 1}


def test_attached_asset_certification_supports_an_indexed_no_wrapper_layout(
    release_index_generation_module,
):
    files = {
        "plugin.py": PLUGIN_PY,
        "README.md": b"Attached release asset\n",
    }
    archive = raw_zip_archive(list(files.items()))
    selected = candidate(
        artifact_kind="asset_zip",
        artifact_provenance="release_asset",
    )

    certified = release_index_generation_module._certify_zip_bytes(
        archive,
        selected,
    )

    assert certified.root_prefix == "."
    assert certified.tree_sha256 == canonical_tree_sha256(files)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        ".pypluginstore.json",
        "nested/.Git/config",
        "bad?.txt",
        "cafe\u0301.txt",
        "bad\u0085name.txt",
    ],
    ids=("manager", "vcs", "windows", "non-nfc", "c1-control"),
)
def test_scanner_rejects_paths_the_runtime_cannot_install(
    release_index_generation_module,
    unsafe_path,
):
    root = "example-plugin-" + COMMIT_1
    archive = raw_zip_archive(
        [
            (root + "/plugin.py", PLUGIN_PY),
            (root + "/" + unsafe_path, b"unsafe\n"),
        ]
    )

    with pytest.raises(ValueError):
        release_index_generation_module._certify_zip_bytes(
            archive,
            candidate(),
        )


def test_scanner_rejects_nul_in_the_original_member_name(
    release_index_generation_module,
):
    root = "example-plugin-" + COMMIT_1
    original = (root + "/badXsuffix.txt").encode("utf-8")
    replacement = (root + "/bad\x00suffix.txt").encode("utf-8")
    archive = raw_zip_archive(
        [
            (root + "/plugin.py", PLUGIN_PY),
            (root + "/badXsuffix.txt", b"unsafe\n"),
        ]
    )
    assert archive.count(original) >= 2
    archive = archive.replace(original, replacement)

    with pytest.raises(ValueError, match="NUL"):
        release_index_generation_module._certify_zip_bytes(
            archive,
            candidate(),
        )


def test_scanner_rejects_component_only_casefold_collisions(
    release_index_generation_module,
):
    root = "example-plugin-" + COMMIT_1
    archive = raw_zip_archive(
        [
            (root + "/plugin.py", PLUGIN_PY),
            (root + "/Config/first.json", b"{}\n"),
            (root + "/config/second.json", b"{}\n"),
        ]
    )

    with pytest.raises(ValueError, match="collision"):
        release_index_generation_module._certify_zip_bytes(
            archive,
            candidate(),
        )


def test_scanner_rejects_an_extra_empty_root_outside_the_wrapper(
    release_index_generation_module,
):
    root = "example-plugin-" + COMMIT_1
    archive = raw_zip_archive(
        [
            (root + "/plugin.py", PLUGIN_PY),
            ("unexpected-root/", b"", 0o40755),
        ]
    )

    with pytest.raises(ValueError, match="wrapper"):
        release_index_generation_module._certify_zip_bytes(
            archive,
            candidate(),
        )


def test_scanner_rejects_file_and_empty_directory_prefix_collisions(
    release_index_generation_module,
):
    root = "example-plugin-" + COMMIT_1
    archive = raw_zip_archive(
        [
            (root + "/plugin.py", PLUGIN_PY),
            (root + "/data", b"regular file\n"),
            (root + "/data/empty/", b"", 0o40755),
        ]
    )

    with pytest.raises(ValueError, match="prefix collision"):
        release_index_generation_module._certify_zip_bytes(
            archive,
            candidate(),
        )


def test_scanner_rejects_directory_members_with_payload_bytes(
    release_index_generation_module,
):
    root = "example-plugin-" + COMMIT_1
    archive = raw_zip_archive(
        [
            (root + "/plugin.py", PLUGIN_PY),
            (root + "/config/", b"not empty", 0o40755),
        ]
    )

    with pytest.raises(ValueError, match="directory metadata"):
        release_index_generation_module._certify_zip_bytes(
            archive,
            candidate(),
        )


def test_reviewed_allowed_origins_are_forwarded_to_artifact_download(
    release_index_generation_module,
):
    contents = registry_bytes(
        {
            "ExamplePlugin": registry_entry(
                allowed_origins=["https://cdn.example.test"]
            )
        }
    )
    selected = candidate()
    archive = zip_archive()

    _, _, http_client = generate_single(
        release_index_generation_module,
        selected,
        archive,
        registry_contents=contents,
    )

    assert http_client.calls[0]["allowed_origins"] == [
        "https://cdn.example.test"
    ]


def test_github_api_commit_source_allows_only_the_codeload_redirect(
    release_index_generation_module,
):
    selected = candidate(
        artifact_url=(
            "https://api.github.com/repos/owner/example-plugin/zipball/"
            + COMMIT_1
        )
    )

    _, _, http_client = generate_single(
        release_index_generation_module,
        selected,
        zip_archive(),
    )

    assert http_client.calls[0]["allowed_origins"] == [
        "https://codeload.github.com"
    ]


def test_new_releases_get_monotonic_revision_and_complete_lineage(
    release_index_generation_module,
):
    contents = registry_bytes()
    archive_1 = zip_archive()
    first_candidate = candidate()
    first, _, _ = generate_single(
        release_index_generation_module,
        first_candidate,
        archive_1,
        registry_contents=contents,
    )

    second_candidate = candidate(
        release_id="github:owner/example-plugin:v2.0.0",
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        artifact_url="https://downloads.example.test/v2.zip",
    )
    archive_2 = zip_archive(root_prefix="example-plugin-" + COMMIT_2)
    second, _, _ = generate_single(
        release_index_generation_module,
        second_candidate,
        archive_2,
        registry_contents=contents,
        previous=first.document,
    )

    third_candidate = candidate(
        release_id="github:owner/example-plugin:v3.0.0",
        version="3.0.0",
        tag="v3.0.0",
        commit="3" * 40,
        artifact_url="https://downloads.example.test/v3.zip",
    )
    archive_3 = zip_archive(root_prefix="example-plugin-" + "3" * 40)
    third, _, _ = generate_single(
        release_index_generation_module,
        third_candidate,
        archive_3,
        registry_contents=contents,
        previous=second.document,
    )

    first_entry = release_map(first.document)["ExamplePlugin"]
    second_entry = release_map(second.document)["ExamplePlugin"]
    third_entry = release_map(third.document)["ExamplePlugin"]
    assert (first_entry["revision"], first_entry["supersedes"]) == (1, [])
    assert (second_entry["revision"], second_entry["supersedes"]) == (
        2,
        [first_candidate.release_id],
    )
    assert (third_entry["revision"], third_entry["supersedes"]) == (
        3,
        [first_candidate.release_id, second_candidate.release_id],
    )
    assert second.document["sequence"] == 2
    assert third.document["sequence"] == 3
    assert status(second) == "certified_update"
    assert status(third) == "certified_update"


def test_generated_source_recompression_refreshes_transport_without_new_revision(
    release_index_generation_module,
):
    contents = registry_bytes()
    original_archive = zip_archive(
        compression=zipfile.ZIP_STORED,
        timestamp=(2026, 7, 17, 10, 0, 0),
    )
    recompressed_archive = zip_archive(
        compression=zipfile.ZIP_DEFLATED,
        timestamp=(2026, 7, 18, 10, 0, 0),
        reverse=True,
    )
    assert hashlib.sha256(original_archive).digest() != hashlib.sha256(
        recompressed_archive
    ).digest()
    previous_entry = release_entry(
        original_archive,
        revision=7,
        supersedes=[
            "github:owner/example-plugin:v0.8.0",
            "github:owner/example-plugin:v0.9.0",
        ],
    )
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )

    result, _, _ = generate_single(
        release_index_generation_module,
        candidate(),
        recompressed_archive,
        registry_contents=contents,
        previous=previous,
    )

    current = release_map(result.document)["ExamplePlugin"]
    assert current["revision"] == 7
    assert current["release_id"] == previous_entry["release_id"]
    assert current["supersedes"] == previous_entry["supersedes"]
    assert current["commit"] == previous_entry["commit"]
    assert current["artifact"]["tree_sha256"] == previous_entry[
        "artifact"
    ]["tree_sha256"]
    assert current["artifact"]["sha256"] == hashlib.sha256(
        recompressed_archive
    ).hexdigest()
    assert current["artifact"]["size"] == len(recompressed_archive)
    assert status(result) == "transport_refreshed"
    assert result.report["plugins"]["ExamplePlugin"][
        "revision_changed"
    ] is False


def test_identical_candidate_and_archive_report_unchanged(
    release_index_generation_module,
):
    contents = registry_bytes()
    archive = zip_archive()
    previous_entry = release_entry(archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )

    result, _, _ = generate_single(
        release_index_generation_module,
        candidate(),
        archive,
        registry_contents=contents,
        previous=previous,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert status(result) == "unchanged"
    assert result.report["summary"] == {"unchanged": 1}


def test_changed_source_tree_at_same_commit_is_quarantined(
    release_index_generation_module,
):
    contents = registry_bytes()
    original_archive = zip_archive()
    changed_files = {
        "plugin.py": PLUGIN_PY,
        "README.md": b"unexpected changed tree\n",
    }
    changed_archive = zip_archive(files=changed_files)
    previous_entry = release_entry(original_archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )

    result, _, _ = generate_single(
        release_index_generation_module,
        candidate(),
        changed_archive,
        registry_contents=contents,
        previous=previous,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert status(result) == "quarantined_mutation"
    report = result.report["plugins"]["ExamplePlugin"]
    assert report["reason"] == "source_tree_changed"
    assert report["observed_tree_sha256"] == canonical_tree_sha256(
        changed_files
    )
    assert report["accepted_tree_sha256"] == previous_entry["artifact"][
        "tree_sha256"
    ]
    assert result.document["tombstones"] == []


@pytest.mark.parametrize(
    ("provider", "provenance", "kind", "migration_eligible"),
    [
        pytest.param(
            "github",
            "attached_asset",
            "asset_zip",
            False,
            id="attached-asset",
        ),
        pytest.param(
            "generic",
            "generic_manifest",
            "asset_zip",
            False,
            id="generic-artifact",
        ),
    ],
)
def test_attached_and_generic_recompression_is_quarantined_as_mutation(
    release_index_generation_module,
    provider,
    provenance,
    kind,
    migration_eligible,
):
    owner = (
        "owner"
        if provider == "github"
        else "downloads.example.test/team"
    )
    contents = registry_bytes(
        {
            "ExamplePlugin": registry_entry(
                owner=owner,
                provider=provider,
                artifact="asset_zip",
            )
        }
    )
    identity = (
        "github.com/owner/example-plugin"
        if provider == "github"
        else "downloads.example.test/team/example-plugin"
    )
    release_identity = (
        "github:owner/example-plugin:v1.0.0"
        if provider == "github"
        else "generic:downloads.example.test/team/example-plugin:v1.0.0"
    )
    original_archive = zip_archive(
        compression=zipfile.ZIP_STORED,
        timestamp=(2026, 7, 17, 10, 0, 0),
    )
    recompressed_archive = zip_archive(
        compression=zipfile.ZIP_DEFLATED,
        timestamp=(2026, 7, 18, 10, 0, 0),
        reverse=True,
    )
    artifact_url = "https://downloads.example.test/asset.zip"
    previous_artifact = artifact_document(
        original_archive,
        kind=kind,
        provenance=provenance,
        migration_eligible=migration_eligible,
        url=artifact_url,
    )
    previous_entry = release_entry(
        original_archive,
        revision=7,
        release_id=release_identity,
        provider=provider,
        repository_identity=identity,
        source_revision="immutable-release-1" if provider == "generic" else None,
        artifact=previous_artifact,
    )
    if provider == "generic":
        previous_entry["commit"] = ""
    selected = candidate(
        provider=provider,
        repository_identity=identity,
        release_id=release_identity,
        commit="" if provider == "generic" else COMMIT_1,
        source_revision=(
            "immutable-release-1" if provider == "generic" else COMMIT_1
        ),
        artifact_kind=kind,
        artifact_provenance=provenance,
        artifact_url=artifact_url,
        artifact_size=len(recompressed_archive),
        provider_sha256=hashlib.sha256(recompressed_archive).hexdigest(),
        migration_eligible=migration_eligible,
    )
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )

    result, _, _ = generate_single(
        release_index_generation_module,
        selected,
        recompressed_archive,
        registry_contents=contents,
        previous=previous,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert status(result) == "quarantined_mutation"
    assert result.report["plugins"]["ExamplePlugin"]["reason"] == (
        "artifact_bytes_changed"
    )


@pytest.mark.parametrize(
    ("source_files", "expected_eligible"),
    [
        pytest.param(None, True, id="equivalent-tree"),
        pytest.param(
            {
                "plugin.py": PLUGIN_PY,
                "README.md": b"different source tree\n",
            },
            False,
            id="different-tree",
        ),
    ],
)
def test_attached_asset_migration_uses_exact_commit_tree_evidence(
    release_index_generation_module,
    source_files,
    expected_eligible,
):
    contents = registry_bytes(
        {
            "ExamplePlugin": registry_entry(artifact="asset_zip"),
        }
    )
    artifact_url = "https://downloads.example.test/plugin.zip"
    source_url = "https://api.github.com/repos/owner/example-plugin/zipball/" + COMMIT_1
    asset_archive = zip_archive(root_prefix="release-asset")
    source_archive = zip_archive(
        files=source_files,
        root_prefix="example-plugin-" + COMMIT_1,
    )
    selected = candidate(
        artifact_kind="asset_zip",
        artifact_provenance="attached_asset",
        artifact_url=artifact_url,
        source_archive_url=source_url,
        artifact_size=len(asset_archive),
        provider_sha256=hashlib.sha256(asset_archive).hexdigest(),
        migration_eligible=False,
    )
    provider = RecordingProvider(
        {selected.repository_identity: [selected]}
    )
    http_client = RecordingHttpClient(
        {
            artifact_url: [asset_archive],
            source_url: [source_archive],
        }
    )

    result = make_generator(
        release_index_generation_module,
        {"github": provider},
        http_client,
    ).generate(registry_bytes=contents, report_only=True)

    migration = release_map(result.document)["ExamplePlugin"]["artifact"][
        "migration"
    ]
    assert migration["mode"] == (
        "automatic" if expected_eligible else "manual"
    )
    assert migration["evidence"] == (
        "source_equivalent_asset" if expected_eligible else "unverified_asset"
    )
    assert [call["url"] for call in http_client.calls] == [
        artifact_url,
        source_url,
    ]
    assert result.document["tombstones"] == []


def test_same_release_identity_with_changed_commit_is_quarantined(
    release_index_generation_module,
):
    contents = registry_bytes()
    original_archive = zip_archive()
    previous_entry = release_entry(original_archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    selected = candidate(
        commit=COMMIT_2,
        artifact_url="https://downloads.example.test/changed-commit.zip",
    )
    changed_commit_archive = zip_archive(
        root_prefix="example-plugin-" + COMMIT_2
    )

    result, _, _ = generate_single(
        release_index_generation_module,
        selected,
        changed_commit_archive,
        registry_contents=contents,
        previous=previous,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert status(result) == "quarantined_mutation"
    assert result.report["plugins"]["ExamplePlugin"]["reason"] == (
        "release_identity_changed_commit"
    )


def test_provider_release_regression_retains_complete_current_lineage(
    release_index_generation_module,
):
    contents = registry_bytes()
    archive = zip_archive()
    previous_entry = release_entry(
        archive,
        revision=3,
        release_id="github:owner/example-plugin:v3.0.0",
        supersedes=[
            "github:owner/example-plugin:v1.0.0",
            "github:owner/example-plugin:v2.0.0",
        ],
        version="3.0.0",
        tag="v3.0.0",
        commit="3" * 40,
    )
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    regressed = candidate(
        release_id="github:owner/example-plugin:v2.0.0",
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        artifact_url="https://downloads.example.test/v2.zip",
    )

    result, _, _ = generate_single(
        release_index_generation_module,
        regressed,
        zip_archive(root_prefix="example-plugin-" + COMMIT_2),
        registry_contents=contents,
        previous=previous,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert status(result) == "quarantined_mutation"
    assert result.report["plugins"]["ExamplePlugin"]["reason"] == (
        "release_lineage_regression"
    )


def test_candidate_and_certification_cache_avoid_repeated_provider_and_archive_calls(
    release_index_generation_module,
    tmp_path,
):
    contents = registry_bytes()
    selected = candidate()
    archive = zip_archive()
    cache_path = tmp_path / "candidate-cache.json"
    first_cache = release_index_generation_module.ReleaseCandidateCache(
        path=str(cache_path),
        ttl_seconds=3600,
        clock=lambda: NOW,
    )
    first, provider, http_client = generate_single(
        release_index_generation_module,
        selected,
        archive,
        registry_contents=contents,
        cache=first_cache,
    )
    assert len(provider.calls) == 1
    assert len(http_client.calls) == 1
    assert cache_path.is_file()

    second_cache = release_index_generation_module.ReleaseCandidateCache(
        path=str(cache_path),
        ttl_seconds=3600,
        clock=lambda: NOW + timedelta(minutes=5),
    )
    provider_from_cache = ExplodingProvider()
    http_from_cache = ExplodingHttpClient()
    second = make_generator(
        release_index_generation_module,
        {"github": provider_from_cache},
        http_from_cache,
        cache=second_cache,
        now=NOW,
    ).generate(registry_bytes=contents, report_only=True)

    assert second.index_bytes == first.index_bytes
    assert provider_from_cache.calls == []
    assert http_from_cache.calls == []
    assert status(second) == "certified_new"
    assert second.report["plugins"]["ExamplePlugin"]["cache_hit"] is True


def test_expired_candidate_cache_is_not_used(
    release_index_generation_module,
    tmp_path,
):
    contents = registry_bytes()
    selected = candidate()
    archive = zip_archive()
    cache_path = tmp_path / "candidate-cache.json"
    first_cache = release_index_generation_module.ReleaseCandidateCache(
        path=str(cache_path),
        ttl_seconds=60,
        clock=lambda: NOW,
    )
    generate_single(
        release_index_generation_module,
        selected,
        archive,
        registry_contents=contents,
        cache=first_cache,
    )

    provider = RecordingProvider(
        {selected.repository_identity: [selected]}
    )
    http_client = RecordingHttpClient(
        {selected.artifact_url: [archive]}
    )
    expired_cache = release_index_generation_module.ReleaseCandidateCache(
        path=str(cache_path),
        ttl_seconds=60,
        clock=lambda: NOW + timedelta(minutes=2),
    )
    result = make_generator(
        release_index_generation_module,
        {"github": provider},
        http_client,
        cache=expired_cache,
        now=NOW + timedelta(minutes=2),
    ).generate(registry_bytes=contents, report_only=True)

    assert len(provider.calls) == 1
    assert len(http_client.calls) == 1
    assert result.report["plugins"]["ExamplePlugin"]["cache_hit"] is False


def test_transient_provider_failure_retains_previous_entry_and_reports_it(
    release_index_generation_module,
):
    contents = registry_bytes()
    archive = zip_archive()
    previous_entry = release_entry(archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    failure = release_index_generation_module.TransientProviderError(
        "provider rate limit"
    )
    provider = RecordingProvider(
        {"github.com/owner/example-plugin": [failure]}
    )
    http_client = RecordingHttpClient({})
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        http_client,
    )

    result = generator.generate(
        registry_bytes=contents,
        previous_index=previous,
        report_only=True,
    )

    assert result.document["sequence"] == previous["sequence"] + 1
    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert result.document["tombstones"] == []
    assert status(result) == "retained_provider_failure"
    assert result.report["plugins"]["ExamplePlugin"]["transient"] is True
    assert "rate limit" in result.report["plugins"]["ExamplePlugin"][
        "detail"
    ]
    assert http_client.calls == []


def test_rate_limited_transport_error_is_translated_to_transient_report(
    release_index_generation_module,
):
    class RateLimitedError(Exception):
        reason = "rate_limited"

    provider = RecordingProvider(
        {
            "github.com/owner/example-plugin": [
                RateLimitedError("provider quota exhausted")
            ]
        }
    )
    result = make_generator(
        release_index_generation_module,
        {"github": provider},
        RecordingHttpClient({}),
    ).generate(registry_bytes=registry_bytes(), report_only=True)

    report = result.report["plugins"]["ExamplePlugin"]
    assert report["status"] == "provider_failed"
    assert report["transient"] is True
    assert report["provider"] == "github"
    assert "quota exhausted" in report["detail"]


def test_provider_no_release_signal_reaches_no_release_report(
    release_index_generation_module,
):
    class NoReleaseError(Exception):
        reason = "no_release"

    provider = RecordingProvider(
        {
            "github.com/owner/example-plugin": [
                NoReleaseError("no reviewed stable release")
            ]
        }
    )
    result = make_generator(
        release_index_generation_module,
        {"github": provider},
        RecordingHttpClient({}),
    ).generate(registry_bytes=registry_bytes(), report_only=True)

    assert status(result) == "no_release"
    assert result.report["plugins"]["ExamplePlugin"]["provider"] == (
        "github"
    )


def test_object_entry_without_delivery_uses_implicit_release_first_policy(
    release_index_generation_module,
):
    entry = registry_entry()
    del entry["delivery"]
    provider = RecordingProvider(
        {"github.com/owner/example-plugin": [None]}
    )

    result = make_generator(
        release_index_generation_module,
        {"github": provider},
        RecordingHttpClient({}),
    ).generate(
        registry_bytes=registry_bytes({"ExamplePlugin": entry}),
        report_only=True,
    )

    assert status(result) == "no_release"
    assert len(provider.calls) == 1
    assert provider.calls[0]["policy"] == {
        "provider": "github",
        "channel": "stable",
        "tag_pattern": r"^v?[0-9]+(?:\.[0-9]+){1,3}$",
        "artifact": "source_zip",
        "source_path": ".",
        "mutable_paths": [],
    }


def test_github_not_found_error_remains_a_non_transient_provider_failure(
    release_index_generation_module,
):
    class NotFoundError(Exception):
        reason = "http_error"
        status = 404

    provider = RecordingProvider(
        {
            "github.com/owner/example-plugin": [
                NotFoundError("repository not found")
            ]
        }
    )
    result = make_generator(
        release_index_generation_module,
        {"github": provider},
        RecordingHttpClient({}),
    ).generate(registry_bytes=registry_bytes(), report_only=True)

    report = result.report["plugins"]["ExamplePlugin"]
    assert report == {
        "status": "provider_failed",
        "transient": False,
        "detail": "repository not found",
        "provider": "github",
    }


def test_missing_candidate_retains_previous_entry_without_silent_tombstone(
    release_index_generation_module,
):
    contents = registry_bytes()
    archive = zip_archive()
    previous_entry = release_entry(archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    provider = RecordingProvider(
        {"github.com/owner/example-plugin": [None]}
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        RecordingHttpClient({}),
    )

    result = generator.generate(
        registry_bytes=contents,
        previous_index=previous,
        report_only=True,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert result.document["tombstones"] == []
    assert status(result) == "retained_no_candidate"


def test_missing_candidate_without_prior_certification_reports_no_release(
    release_index_generation_module,
):
    contents = registry_bytes()
    provider = RecordingProvider(
        {"github.com/owner/example-plugin": [None]}
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        RecordingHttpClient({}),
    )

    result = generator.generate(
        registry_bytes=contents,
        report_only=True,
    )

    assert result.document["releases"] == []
    assert result.document["tombstones"] == []
    assert status(result) == "no_release"
    assert result.report["summary"] == {"no_release": 1}


def test_release_if_indexed_discovers_a_release_after_git_only_operation(
    release_index_generation_module,
):
    contents = registry_bytes()
    repository_identity = "github.com/owner/example-plugin"
    first = make_generator(
        release_index_generation_module,
        {
            "github": RecordingProvider(
                {repository_identity: [None]}
            )
        },
        RecordingHttpClient({}),
    ).generate(registry_bytes=contents, report_only=True)
    selected = candidate()

    second, provider, http_client = generate_single(
        release_index_generation_module,
        selected,
        zip_archive(),
        registry_contents=contents,
        previous=first.document,
    )

    assert first.document["releases"] == []
    assert status(first) == "no_release"
    assert release_map(second.document)["ExamplePlugin"]["revision"] == 1
    assert status(second) == "certified_new"
    assert len(provider.calls) == 1
    assert len(http_client.calls) == 1


def test_policy_disable_does_not_silently_remove_previously_accepted_release(
    release_index_generation_module,
):
    contents = registry_bytes(
        {
            "ExamplePlugin": registry_entry(preferred="git"),
        }
    )
    archive = zip_archive()
    previous_entry = release_entry(archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": ExplodingProvider()},
        ExplodingHttpClient(),
    )

    result = generator.generate(
        registry_bytes=contents,
        previous_index=previous,
        report_only=True,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert result.document["tombstones"] == []
    assert status(result) == "retained_policy_disabled"


def test_explicit_tombstone_request_decertifies_prior_release_with_reason(
    release_index_generation_module,
):
    contents = registry_bytes()
    archive = zip_archive()
    previous_entry = release_entry(archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    provider = ExplodingProvider()
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        ExplodingHttpClient(),
    )

    result = generator.generate(
        registry_bytes=contents,
        previous_index=previous,
        tombstone_requests={
            "ExamplePlugin": {
                "reason": "Release packaging is no longer maintained."
            }
        },
        report_only=True,
    )

    assert result.document["releases"] == []
    assert tombstone_map(result.document) == {
        "ExamplePlugin": {
            "package_id": "ExamplePlugin",
            "repository_identity": previous_entry["repository_identity"],
            "last_revision": previous_entry["revision"],
            "release_id": previous_entry["release_id"],
            "reason": "Release packaging is no longer maintained.",
            "removed_at": "2026-07-18T12:00:00Z",
        }
    }
    assert status(result) == "tombstoned"
    assert result.report["summary"] == {"tombstoned": 1}
    assert provider.calls == []


def test_legacy_release_can_be_tombstoned_after_registry_package_removal(
    release_index_generation_module,
):
    old_registry = registry_bytes()
    previous_entry = release_entry(zip_archive(), revision=7)
    previous = legacy_previous_index(
        old_registry,
        {"ExamplePlugin": previous_entry},
    )
    current_registry = (
        json.dumps({"schema_version": 2, "packages": []}, indent=2)
        + "\n"
    ).encode("utf-8")
    generator = make_generator(
        release_index_generation_module,
        {"github": ExplodingProvider()},
        ExplodingHttpClient(),
    )

    result = generator.generate(
        registry_bytes=current_registry,
        previous_index=previous,
        tombstone_requests={
            "ExamplePlugin": {
                "reason": "The registry record was not a Domoticz plugin."
            }
        },
        report_only=True,
    )

    assert result.document["releases"] == []
    assert tombstone_map(result.document)["ExamplePlugin"] == {
        "package_id": "ExamplePlugin",
        "repository_identity": previous_entry["repository_identity"],
        "last_revision": 7,
        "release_id": previous_entry["release_id"],
        "reason": "The registry record was not a Domoticz plugin.",
        "removed_at": "2026-07-18T12:00:00Z",
    }
    assert status(result) == "tombstoned"


def test_v2_tombstone_survives_package_removal_from_registry(
    release_index_generation_module,
):
    old_registry = registry_bytes()
    tombstone = {
        "repository_identity": "github.com/owner/example-plugin",
        "last_revision": 7,
        "release_id": "github:owner/example-plugin:v1.0.0",
        "reason": "The registry record was not a Domoticz plugin.",
        "removed_at": "2026-07-17T12:00:00Z",
    }
    previous = previous_index(
        old_registry,
        {},
        tombstones={"ExamplePlugin": tombstone},
    )
    current_registry = (
        json.dumps({"schema_version": 2, "packages": []}, indent=2)
        + "\n"
    ).encode("utf-8")
    generator = make_generator(
        release_index_generation_module,
        {"github": ExplodingProvider()},
        ExplodingHttpClient(),
    )

    result = generator.generate(
        registry_bytes=current_registry,
        previous_index=previous,
        report_only=True,
    )

    assert result.document["releases"] == []
    assert tombstone_map(result.document) == {
        "ExamplePlugin": {"package_id": "ExamplePlugin", **tombstone}
    }


def test_tombstoned_release_is_not_reaccepted_as_a_new_candidate(
    release_index_generation_module,
):
    contents = registry_bytes()
    selected = candidate()
    tombstone = {
        "repository_identity": selected.repository_identity,
        "last_revision": 7,
        "release_id": selected.release_id,
        "reason": "The release failed package identity certification.",
        "removed_at": "2026-07-17T12:00:00Z",
    }
    previous = previous_index(
        contents,
        {},
        tombstones={"ExamplePlugin": tombstone},
    )
    provider = RecordingProvider(
        {selected.repository_identity: [selected]}
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        ExplodingHttpClient(),
    )

    result = generator.generate(
        registry_bytes=contents,
        previous_index=previous,
        report_only=True,
    )

    assert result.document["releases"] == []
    assert tombstone_map(result.document) == {
        "ExamplePlugin": {"package_id": "ExamplePlugin", **tombstone}
    }
    assert status(result) == "retained_tombstone"
    assert result.report["plugins"]["ExamplePlugin"]["cache_hit"] is False
    assert len(provider.calls) == 1


def test_new_certified_release_reactivates_a_tombstoned_package(
    release_index_generation_module,
):
    contents = registry_bytes()
    previous_release_id = "github:owner/example-plugin:v1.0.0"
    tombstone = {
        "repository_identity": "github.com/owner/example-plugin",
        "last_revision": 7,
        "release_id": previous_release_id,
        "reason": "The release failed package identity certification.",
        "removed_at": "2026-07-17T12:00:00Z",
    }
    previous = previous_index(
        contents,
        {},
        tombstones={"ExamplePlugin": tombstone},
    )
    selected = candidate(
        release_id="github:owner/example-plugin:v2.0.0",
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        artifact_url="https://downloads.example.test/v2.zip",
    )
    archive = zip_archive(root_prefix="example-plugin-" + COMMIT_2)

    result, _provider, _http = generate_single(
        release_index_generation_module,
        selected,
        archive,
        registry_contents=contents,
        previous=previous,
    )

    current = release_map(result.document)["ExamplePlugin"]
    assert result.document["tombstones"] == []
    assert current["revision"] == 8
    assert current["release_id"] == selected.release_id
    assert current["supersedes"] == [previous_release_id]
    assert status(result) == "reactivated"
    assert result.report["summary"] == {"reactivated": 1}


@pytest.mark.parametrize(
    "tombstone_request",
    [
        pytest.param(
            {"UnknownPlugin": {"reason": "Unknown"}},
            id="unknown-plugin",
        ),
        pytest.param(
            {"ExamplePlugin": {"reason": ""}},
            id="empty-reason",
        ),
        pytest.param(
            {"ExamplePlugin": {}},
            id="missing-reason",
        ),
    ],
)
def test_tombstones_require_explicit_valid_request_for_prior_release(
    release_index_generation_module, tombstone_request
):
    contents = registry_bytes()
    archive = zip_archive()
    previous = previous_index(
        contents,
        {"ExamplePlugin": release_entry(archive, revision=7)},
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": ExplodingProvider()},
        ExplodingHttpClient(),
    )

    with pytest.raises(ValueError):
        generator.generate(
            registry_bytes=contents,
            previous_index=previous,
            tombstone_requests=tombstone_request,
            report_only=True,
        )


def test_certification_failure_retains_previous_entry_and_reports_failure(
    release_index_generation_module,
):
    contents = registry_bytes()
    original_archive = zip_archive()
    previous_entry = release_entry(original_archive, revision=7)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    selected = candidate(
        release_id="github:owner/example-plugin:v2.0.0",
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        artifact_url="https://downloads.example.test/v2.zip",
    )
    provider = RecordingProvider(
        {selected.repository_identity: [selected]}
    )
    http_client = RecordingHttpClient(
        {selected.artifact_url: [ValueError("archive rejected")]}
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        http_client,
    )

    result = generator.generate(
        registry_bytes=contents,
        previous_index=previous,
        report_only=True,
    )

    assert release_payload(result.document, "ExamplePlugin") == previous_entry
    assert status(result) == "certification_failed"
    assert "archive rejected" in result.report["plugins"][
        "ExamplePlugin"
    ]["detail"]
    assert result.document["tombstones"] == []


def test_report_only_file_run_never_changes_registry_or_tracked_index(
    release_index_generation_module,
    tmp_path,
):
    contents = registry_bytes()
    original_archive = zip_archive()
    previous_entry = release_entry(original_archive, revision=1)
    previous = previous_index(
        contents,
        {"ExamplePlugin": previous_entry},
    )
    registry_path = tmp_path / "registry.json"
    index_path = tmp_path / "release_index.json"
    registry_path.write_bytes(contents)
    previous_bytes = (
        json.dumps(previous, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    index_path.write_bytes(previous_bytes)
    selected = candidate(
        release_id="github:owner/example-plugin:v2.0.0",
        version="2.0.0",
        tag="v2.0.0",
        commit=COMMIT_2,
        artifact_url="https://downloads.example.test/v2.zip",
    )
    archive = zip_archive(root_prefix="example-plugin-" + COMMIT_2)
    provider = RecordingProvider(
        {selected.repository_identity: [selected]}
    )
    http_client = RecordingHttpClient(
        {selected.artifact_url: [archive]}
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        http_client,
    )
    before_paths = sorted(path.name for path in tmp_path.iterdir())

    result = generator.run(
        registry_path=str(registry_path),
        index_path=str(index_path),
        report_only=True,
    )

    assert registry_path.read_bytes() == contents
    assert index_path.read_bytes() == previous_bytes
    assert sorted(path.name for path in tmp_path.iterdir()) == before_paths
    assert result.index_bytes != previous_bytes
    assert result.document["sequence"] == previous["sequence"] + 1
    assert result.wrote_index is False
    assert status(result) == "certified_update"


def test_report_is_sorted_and_summarizes_each_scanner_outcome(
    release_index_generation_module,
):
    entries = {
        "ZuluNoRelease": registry_entry(
            owner="zulu",
            repository="none",
        ),
        "AlphaCertified": registry_entry(
            owner="alpha",
            repository="plugin",
        ),
    }
    contents = registry_bytes(entries)
    selected = candidate(
        repository_identity="github.com/alpha/plugin",
        release_id="github:alpha/plugin:v1.0.0",
        artifact_url="https://downloads.example.test/alpha.zip",
    )
    provider = RecordingProvider(
        {
            "github.com/alpha/plugin": [selected],
            "github.com/zulu/none": [None],
        }
    )
    generator = make_generator(
        release_index_generation_module,
        {"github": provider},
        RecordingHttpClient(
            {selected.artifact_url: [zip_archive(root_prefix="plugin-" + COMMIT_1)]}
        ),
    )

    result = generator.generate(
        registry_bytes=contents,
        report_only=True,
    )

    assert list(result.report["plugins"]) == [
        "AlphaCertified",
        "ZuluNoRelease",
    ]
    assert result.report["plugins"]["AlphaCertified"]["status"] == (
        "certified_new"
    )
    assert result.report["plugins"]["ZuluNoRelease"]["status"] == (
        "no_release"
    )
    assert result.report["plugins"]["AlphaCertified"]["provider"] == (
        "github"
    )
    assert result.report["plugins"]["ZuluNoRelease"]["provider"] == (
        "github"
    )
    assert result.report["providers"] == {
        "github": {
            "certified_new": 1,
            "no_release": 1,
        }
    }
    assert result.report["summary"] == {
        "certified_new": 1,
        "no_release": 1,
    }
    assert result.report["sequence"] == result.document["sequence"]
    assert result.report["report_only"] is True


def test_default_construction_uses_a_separate_bounded_strict_json_transport(
    release_index_generation_module,
    monkeypatch,
):
    module = release_index_generation_module

    class ArtifactClient:
        def download(self, url, **kwargs):
            raise AssertionError("artifact client should not be used for provider JSON")

    class JsonClient:
        def __init__(self):
            self.calls = []

        def download(self, url, **kwargs):
            self.calls.append((url, copy.deepcopy(kwargs)))
            data = b'[{"tag":"v1.0.0"}]'
            return Download(
                data=data,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                final_url=url,
            )

    artifact_client = ArtifactClient()
    json_client = JsonClient()
    json_transport = module.SecureJsonTransport(json_client, max_bytes=128)
    providers = {"github": object()}
    monkeypatch.setattr(module, "default_provider_adapters", lambda: providers)
    monkeypatch.setattr(
        module,
        "default_secure_http_client",
        lambda: artifact_client,
    )
    monkeypatch.setattr(
        module,
        "default_secure_json_transport",
        lambda: json_transport,
    )

    generator = module.ReleaseIndexGenerator()

    assert generator.providers is providers
    assert generator.http_client is artifact_client
    assert generator.provider_transport is json_transport
    assert generator.provider_transport.get_json(
        "https://api.example.test/releases",
        headers={"Accept": "application/json"},
    ) == [{"tag": "v1.0.0"}]
    assert json_client.calls == [
        (
            "https://api.example.test/releases",
            {
                "headers": {"Accept": "application/json"},
                "expected_sha256": None,
                "expected_size": None,
                "allowed_origins": [],
            },
        )
    ]


def test_secure_json_transport_scopes_authentication_to_exact_origin(
    release_index_generation_module,
):
    module = release_index_generation_module

    class JsonClient:
        def __init__(self):
            self.calls = []

        def download(self, url, **kwargs):
            self.calls.append((url, copy.deepcopy(kwargs)))
            data = b"{}"
            return Download(
                data=data,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                final_url=url,
            )

    client = JsonClient()
    transport = module.SecureJsonTransport(
        client,
        authentication_headers={
            "https://api.github.com": {
                "Authorization": "Bearer scanner-secret"
            }
        },
    )

    transport.get_json(
        "https://api.github.com/repos/example/plugin/releases",
        headers={"Accept": "application/json"},
    )
    transport.get_json(
        "https://github.example.test/api/v3/repos/example/plugin/releases",
        headers={"Accept": "application/json"},
    )

    assert client.calls[0][1]["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer scanner-secret",
    }
    assert client.calls[1][1]["headers"] == {
        "Accept": "application/json"
    }


OLD_IDENTITY = "github.com/owner/example-plugin"
RENAMED_IDENTITY = "github.com/owner/renamed-plugin"
RENAMED_URL = "https://github.com/owner/renamed-plugin"


class RenamedRepositoryProvider(RecordingProvider):
    def __init__(self, outcomes, repository_ids):
        super().__init__(outcomes)
        self.repository_ids = dict(repository_ids)
        self.id_calls = []

    def repository_id(self, repository, transport):
        identity = repository["repository_identity"]
        self.id_calls.append(identity)
        outcome = self.repository_ids[identity]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def renamed_previous(contents, archive):
    prior = release_entry(
        archive,
        revision=2,
        supersedes=["github:owner/example-plugin:v0.9.0"],
    )
    return prior, previous_index(contents, {"ExamplePlugin": prior})


def renamed_candidate():
    return candidate(
        repository_identity=RENAMED_IDENTITY,
        release_id="github:owner/renamed-plugin:v1.0.0",
    )


def generate_renamed(module, provider, contents, previous):
    selected = renamed_candidate()
    generator = make_generator(
        module,
        {"github": provider},
        RecordingHttpClient({selected.artifact_url: [zip_archive()]}),
    )
    return generator.generate(
        registry_bytes=contents,
        previous_index=previous,
        report_only=True,
    )


def test_listed_repository_rename_carries_lineage_to_the_new_identity(
    release_index_generation_module,
):
    contents = registry_v2_bytes(
        repository_url=RENAMED_URL,
        previous_identities=[OLD_IDENTITY],
    )
    _prior, previous = renamed_previous(contents, zip_archive())
    provider = RenamedRepositoryProvider(
        {RENAMED_IDENTITY: [renamed_candidate()]},
        {OLD_IDENTITY: 1109940110, RENAMED_IDENTITY: 1109940110},
    )

    result = generate_renamed(
        release_index_generation_module, provider, contents, previous
    )

    entry = release_payload(result.document, "ExamplePlugin")
    assert status(result) == "certified_update"
    assert provider.id_calls == [OLD_IDENTITY, RENAMED_IDENTITY]
    assert entry["repository_identity"] == RENAMED_IDENTITY
    assert entry["release_id"] == "github:owner/renamed-plugin:v1.0.0"
    assert entry["revision"] == 3
    assert entry["supersedes"] == [
        "github:owner/example-plugin:v0.9.0",
        "github:owner/example-plugin:v1.0.0",
    ]


def test_unlisted_repository_identity_change_still_fails_generation(
    release_index_generation_module,
):
    contents = registry_v2_bytes(repository_url=RENAMED_URL)
    _prior, previous = renamed_previous(contents, zip_archive())
    provider = RenamedRepositoryProvider({}, {})

    with pytest.raises(ValueError, match="no longer matches registry"):
        generate_renamed(
            release_index_generation_module, provider, contents, previous
        )
    assert provider.calls == []
    assert provider.id_calls == []


def test_listed_rename_with_different_repository_id_retains_prior_release(
    release_index_generation_module,
):
    contents = registry_v2_bytes(
        repository_url=RENAMED_URL,
        previous_identities=[OLD_IDENTITY],
    )
    prior, previous = renamed_previous(contents, zip_archive())
    provider = RenamedRepositoryProvider(
        {},
        {OLD_IDENTITY: 1, RENAMED_IDENTITY: 2},
    )

    result = generate_renamed(
        release_index_generation_module, provider, contents, previous
    )

    assert status(result) == "certification_failed"
    assert "different repository" in result.report["plugins"][
        "ExamplePlugin"
    ]["detail"]
    assert release_payload(result.document, "ExamplePlugin") == prior
    assert provider.calls == []


def test_rename_verification_rate_limit_is_a_transient_retention(
    release_index_generation_module,
):
    class RateLimitedError(Exception):
        reason = "rate_limited"

    contents = registry_v2_bytes(
        repository_url=RENAMED_URL,
        previous_identities=[OLD_IDENTITY],
    )
    prior, previous = renamed_previous(contents, zip_archive())
    provider = RenamedRepositoryProvider(
        {},
        {OLD_IDENTITY: RateLimitedError("provider quota exhausted")},
    )

    result = generate_renamed(
        release_index_generation_module, provider, contents, previous
    )

    report = result.report["plugins"]["ExamplePlugin"]
    assert report["status"] == "retained_provider_failure"
    assert report["transient"] is True
    assert release_payload(result.document, "ExamplePlugin") == prior


def test_previous_tombstone_under_a_listed_identity_remains_valid(
    release_index_generation_module,
):
    module = release_index_generation_module
    contents = registry_v2_bytes(
        repository_url=RENAMED_URL,
        previous_identities=[OLD_IDENTITY],
    )
    tombstone = {
        "repository_identity": OLD_IDENTITY,
        "last_revision": 2,
        "release_id": "github:owner/example-plugin:v1.0.0",
        "reason": "withdrawn",
        "removed_at": "2026-07-17T12:00:00Z",
    }
    previous = previous_index(
        contents, {}, tombstones={"ExamplePlugin": tombstone}
    )
    registry = module._registry_records(
        module._strict_json_object(contents, "registry")
    )

    validated = module._validate_previous(previous, registry)

    assert validated["tombstones"]["ExamplePlugin"] == tombstone
