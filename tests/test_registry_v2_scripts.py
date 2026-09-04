import json

import pytest

from conftest import REPO_ROOT, load_module_from_path


def repository(url="https://github.com/Owner/Example-Plugin", branch="main"):
    return {
        "url": url,
        "branch": branch,
    }


def package(package_id="ExamplePlugin", **overrides):
    document = {
        "package_id": package_id,
        "domoticz_key": "EXAMPLE",
        "description": "Example Domoticz plugin",
        "repository": repository(),
        "platforms": ["linux", "windows"],
        "delivery": {
            "preferred": "git",
            "git_supported": True,
        },
    }
    document.update(overrides)
    return document


def registry_bytes(packages, **overrides):
    document = {
        "schema_version": 2,
        "packages": packages,
    }
    document.update(overrides)
    return (json.dumps(document, indent=2) + "\n").encode("utf-8")


def release_first_package(provider, url, package_id="ExamplePlugin"):
    return package(
        package_id,
        repository=repository(url),
        delivery={
            "preferred": "release_if_indexed",
            "git_supported": True,
            "release": {
                "provider": provider,
                "channel": "stable",
                "tag_pattern": r"^v?[0-9]+(?:\.[0-9]+){1,3}$",
                "artifact": "source_zip",
                "source_path": ".",
                "mutable_paths": [],
            },
        },
    )


@pytest.fixture
def registry_records_module():
    return load_module_from_path(
        "registry_records_v2_under_test",
        REPO_ROOT / ".github" / "scripts" / "registry_records.py",
    )


def parse_registry(module, packages):
    return module.RegistryDocument.from_bytes(registry_bytes(packages))


def test_v2_registry_parses_explicit_package_identity(registry_records_module):
    document = parse_registry(
        registry_records_module,
        [package("Domoticz-SMA-Inverter", domoticz_key="SMA")],
    )

    assert document.schema_version == 2
    assert [record.package_id for record in document.packages] == [
        "Domoticz-SMA-Inverter"
    ]
    assert document.by_package_id["Domoticz-SMA-Inverter"].domoticz_key == "SMA"


def test_v2_registry_serialization_is_deterministic_and_sorted_by_package_id(
    registry_records_module,
):
    first = registry_records_module.RegistryDocument.from_bytes(
        registry_bytes(
            [
                package(
                    "z-plugin",
                    domoticz_key="Z",
                    repository=repository(
                        "https://github.com/Owner/z-plugin"
                    ),
                ),
                package(
                    "A-plugin",
                    domoticz_key="A",
                    repository=repository(
                        "https://github.com/Owner/A-plugin"
                    ),
                ),
                package(
                    "middle-plugin",
                    domoticz_key="M",
                    repository=repository(
                        "https://github.com/Owner/middle-plugin"
                    ),
                ),
            ]
        )
    )
    second = registry_records_module.RegistryDocument.from_bytes(
        registry_bytes(
            [
                package(
                    "middle-plugin",
                    domoticz_key="M",
                    repository=repository(
                        "https://github.com/Owner/middle-plugin"
                    ),
                ),
                package(
                    "z-plugin",
                    domoticz_key="Z",
                    repository=repository(
                        "https://github.com/Owner/z-plugin"
                    ),
                ),
                package(
                    "A-plugin",
                    domoticz_key="A",
                    repository=repository(
                        "https://github.com/Owner/A-plugin"
                    ),
                ),
            ]
        )
    )

    first_bytes = first.to_bytes()
    second_bytes = second.to_bytes()

    assert first_bytes == second_bytes
    assert first_bytes.endswith(b"\n")
    assert [
        item["package_id"]
        for item in json.loads(first_bytes.decode("utf-8"))["packages"]
    ] == ["A-plugin", "middle-plugin", "z-plugin"]
    assert registry_records_module.RegistryDocument.from_bytes(
        first_bytes
    ).to_bytes() == first_bytes


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(
            {
                "ExamplePlugin": {
                    "owner": "owner",
                    "repository": "example-plugin",
                    "description": "Legacy keyed entry",
                    "branch": "main",
                }
            },
            id="legacy-top-level-package-key",
        ),
        pytest.param(
            {
                "schema_version": 2,
                "packages": {"ExamplePlugin": package()},
            },
            id="keyed-package-collection",
        ),
        pytest.param(
            {
                "schema_version": 2,
                "packages": [
                    ["owner", "example-plugin", "Description", "main"]
                ],
            },
            id="positional-package-entry",
        ),
        pytest.param(
            {
                "schema_version": 2,
                "packages": [package("Idle", domoticz_key="Idle")],
            },
            id="idle-sentinel",
        ),
    ],
)
def test_v2_registry_rejects_legacy_identity_shapes(
    registry_records_module,
    document,
):
    contents = (json.dumps(document) + "\n").encode("utf-8")

    with pytest.raises(ValueError):
        registry_records_module.RegistryDocument.from_bytes(contents)


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param(
            b'{"schema_version":2,"schema_version":2,"packages":[]}\n',
            id="duplicate-json-key",
        ),
        pytest.param(
            registry_bytes([], unexpected=True),
            id="unknown-top-level-field",
        ),
        pytest.param(
            (json.dumps({"schema_version": 2}) + "\n").encode("utf-8"),
            id="missing-packages",
        ),
        pytest.param(
            (json.dumps({"schema_version": 1, "packages": []}) + "\n").encode(
                "utf-8"
            ),
            id="unsupported-schema",
        ),
        pytest.param(
            registry_bytes([package(unexpected=True)]),
            id="unknown-package-field",
        ),
        pytest.param(
            registry_bytes(
                [package(repository={**repository(), "owner": "legacy-owner"})]
            ),
            id="unknown-repository-field",
        ),
    ],
)
def test_v2_registry_parsing_is_strict(registry_records_module, contents):
    with pytest.raises(ValueError):
        registry_records_module.RegistryDocument.from_bytes(contents)


def test_release_if_indexed_requires_an_explicit_discovery_policy(
    registry_records_module,
):
    document = package(
        delivery={
            "preferred": "release_if_indexed",
            "git_supported": True,
        }
    )

    with pytest.raises(ValueError, match="(?i)release.*requires"):
        parse_registry(registry_records_module, [document])


@pytest.mark.parametrize(
    "package_ids",
    [
        pytest.param(["ExamplePlugin", "ExamplePlugin"], id="exact-duplicate"),
        pytest.param(
            ["Domoticz-Shelly-Plugin", "Domoticz-Shelly-plugin"],
            id="case-folded-duplicate",
        ),
    ],
)
def test_v2_registry_rejects_duplicate_portable_package_ids(
    registry_records_module,
    package_ids,
):
    packages = [
        package(package_id, domoticz_key="KEY" + str(index))
        for index, package_id in enumerate(package_ids)
    ]

    with pytest.raises(ValueError, match="(?i)package.*id"):
        parse_registry(registry_records_module, packages)


@pytest.mark.parametrize(
    ("url", "identity", "clone_url"),
    [
        pytest.param(
            "https://github.com/Owner/Example-Plugin",
            "github.com/owner/example-plugin",
            "https://github.com/Owner/Example-Plugin.git",
            id="github",
        ),
        pytest.param(
            "https://gitlab.com/Group/Subgroup/Example-Plugin",
            "gitlab.com/group/subgroup/example-plugin",
            "https://gitlab.com/Group/Subgroup/Example-Plugin.git",
            id="gitlab-nested-group",
        ),
        pytest.param(
            "https://codeberg.org/Team/Example-Plugin",
            "codeberg.org/team/example-plugin",
            "https://codeberg.org/Team/Example-Plugin.git",
            id="codeberg-forgejo",
        ),
        pytest.param(
            "https://forge.example.test/Team/Example-Plugin",
            "forge.example.test/team/example-plugin",
            "https://forge.example.test/Team/Example-Plugin.git",
            id="custom-forge",
        ),
    ],
)
def test_v2_repository_url_is_provider_neutral(
    registry_records_module,
    url,
    identity,
    clone_url,
):
    document = parse_registry(
        registry_records_module,
        [package(repository=repository(url))],
    )
    record = document.packages[0]

    assert record.repository_url == url
    assert record.repository_identity == identity
    assert record.clone_url == clone_url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/example-plugin",
        "https://user:secret@github.com/owner/example-plugin",
        "https://github.com/owner/example-plugin?ref=main",
        "https://github.com/owner/example-plugin#readme",
        "https://github.com/example-plugin",
        "https://github.com/owner/example-plugin.git",
    ],
)
def test_v2_repository_url_requires_a_canonical_public_https_web_url(
    registry_records_module,
    url,
):
    with pytest.raises(ValueError, match="(?i)repository.*url"):
        parse_registry(
            registry_records_module,
            [package(repository=repository(url))],
        )


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("github", "https://github.com/Owner/Example-Plugin"),
        ("gitlab", "https://gitlab.com/Group/Example-Plugin"),
        ("codeberg", "https://codeberg.org/Team/Example-Plugin"),
    ],
)
def test_ci_registry_loader_requires_explicit_provider_neutral_policy(
    registry_records_module,
    provider,
    url,
):
    packages = registry_records_module.registry_mapping_from_bytes(
        registry_bytes([release_first_package(provider, url)])
    )

    assert packages["ExamplePlugin"]["repository"]["url"] == url
    assert packages["ExamplePlugin"]["delivery"]["release"]["provider"] == (
        provider
    )


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("gitlab", "https://github.com/Owner/Example-Plugin"),
        ("forgejo", "https://codeberg.org/Team/Example-Plugin"),
        ("codeberg", "https://gitlab.com/Group/Example-Plugin"),
    ],
)
def test_ci_registry_loader_rejects_provider_host_mismatch(
    registry_records_module,
    provider,
    url,
):
    with pytest.raises(ValueError, match="(?i)provider.*host"):
        registry_records_module.registry_mapping_from_bytes(
            registry_bytes([release_first_package(provider, url)])
        )


def test_ci_registry_writer_is_deterministic_and_never_writes_legacy_keys(
    registry_records_module,
):
    packages = {
        "Zulu": release_first_package(
            "gitlab",
            "https://gitlab.com/Group/Zulu",
            "Zulu",
        ),
        "alpha": release_first_package(
            "codeberg",
            "https://codeberg.org/Team/Alpha",
            "alpha",
        ),
    }

    first = registry_records_module.registry_bytes_from_mapping(packages)
    second = registry_records_module.registry_bytes_from_mapping(
        dict(reversed(list(packages.items())))
    )

    assert first == second
    document = json.loads(first)
    assert list(document) == ["schema_version", "packages"]
    assert [item["package_id"] for item in document["packages"]] == [
        "Zulu",
        "alpha",
    ]
    assert all(
        not ({"owner", "repo", "plugin_key"} & set(item))
        for item in document["packages"]
    )


def test_update_times_v2_round_trips_as_sorted_explicit_records(
    registry_records_module,
):
    contents = registry_records_module.update_times_bytes_from_mapping(
        {
            "Zulu": "2026-07-21T11:00:00+02:00",
            "alpha": "2026-07-20T09:00:00.958Z",
        }
    )

    assert json.loads(contents) == {
        "schema_version": 2,
        "updates": [
            {"package_id": "alpha", "updated_at": "2026-07-20T09:00:00Z"},
            {"package_id": "Zulu", "updated_at": "2026-07-21T09:00:00Z"},
        ],
    }
    assert registry_records_module.update_times_mapping_from_bytes(contents) == {
        "alpha": "2026-07-20T09:00:00Z",
        "Zulu": "2026-07-21T09:00:00Z",
    }


@pytest.mark.parametrize(
    "document",
    [
        {"ExamplePlugin": "2026-07-21T09:00:00Z"},
        {"schema_version": 2, "updates": {"ExamplePlugin": "timestamp"}},
        {
            "schema_version": 2,
            "updates": [
                {
                    "plugin_key": "ExamplePlugin",
                    "updated_at": "2026-07-21T09:00:00Z",
                }
            ],
        },
    ],
)
def test_update_times_v2_rejects_legacy_identity_shapes(
    registry_records_module,
    document,
):
    with pytest.raises(ValueError):
        registry_records_module.update_times_mapping_from_bytes(
            (json.dumps(document) + "\n").encode("utf-8")
        )


@pytest.mark.parametrize(
    ("owner", "repository_name", "provider"),
    [
        ("Owner", "GitHub-Plugin", "github"),
        ("gitlab.com/Group/Subgroup", "GitLab-Plugin", "gitlab"),
        ("codeberg.org/Team", "Codeberg-Plugin", "codeberg"),
    ],
)
def test_scanner_package_builder_emits_release_first_v2_records(
    registry_records_module,
    owner,
    repository_name,
    provider,
):
    document = registry_records_module.build_package_document(
        repository_name,
        "RUNTIME-KEY",
        owner,
        repository_name,
        "Example plugin",
        "main",
        ["linux"],
    )

    assert document["package_id"] == repository_name
    assert document["domoticz_key"] == "RUNTIME-KEY"
    assert document["delivery"]["preferred"] == "release_if_indexed"
    assert document["delivery"]["git_supported"] is True
    assert document["delivery"]["release"]["provider"] == provider


def test_scanner_package_builder_includes_requires_python(registry_records_module):
    document = registry_records_module.build_package_document(
        "Solaredge",
        "SOLAREDGE",
        "Owner",
        "Solaredge",
        "Solaredge plugin",
        "main",
        ["linux"],
        requires_python=">=3.8",
    )
    assert document["requires_python"] == ">=3.8"
    record = registry_records_module.RegistryRecord.from_entry("Solaredge", document)
    assert record.requires_python == ">=3.8"

    updated = record.with_requires_python(">=3.10")
    assert updated.requires_python == ">=3.10"
    assert updated.to_document()["requires_python"] == ">=3.10"

    cleared = updated.with_requires_python("")
    assert cleared.requires_python == ""
    assert "requires_python" not in cleared.to_document()


def test_detect_requires_python_from_texts():
    platform_module = load_module_from_path(
        "detect_plugin_platforms_under_test",
        REPO_ROOT / ".github" / "scripts" / "detect_plugin_platforms.py",
    )
    detect = platform_module.detect_requires_python_from_texts

    # pyproject.toml
    assert detect({"pyproject.toml": '[project]\nrequires-python = ">=3.14.2"\n'}) == ">=3.14.2"
    # setup.cfg
    assert detect({"setup.cfg": "[options]\npython_requires = >=3.9\n"}) == ">=3.9"
    # setup.py
    assert detect({"setup.py": 'setup(name="test", python_requires=">=3.8")'}) == ">=3.8"
    # plugin.py sys.version_info
    assert detect({"plugin.py": "if sys.version_info < (3, 11):\n    Domoticz.Error('requires 3.11')\n"}) == ">=3.11"
    # plugin.py XML header
    assert detect({"plugin.py": '<plugin key="test" name="Test" requires_python=">=3.10">\n</plugin>'}) == ">=3.10"


def test_validate_registry_entry_pep440():
    validate_module = load_module_from_path(
        "validate_plugins_under_test",
        REPO_ROOT / ".github" / "scripts" / "validate_plugins.py",
    )
    valid_doc = package("ValidPlugin", requires_python=">=3.8,<4.0")
    validate_module.validate_registry_entry("ValidPlugin", valid_doc)

    invalid_doc = package("InvalidPlugin", requires_python="invalid-specifier!!!")
    with pytest.raises(ValueError, match="invalid requires_python specifier"):
        validate_module.validate_registry_entry("InvalidPlugin", invalid_doc)
