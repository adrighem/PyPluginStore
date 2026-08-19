import hashlib
import os
import unicodedata
from pathlib import Path

import pytest


ROOT_PREFIX = "example-plugin-v1"


def sha256(contents):
    return hashlib.sha256(contents).hexdigest()


def plugin_source(
    *,
    key="ExamplePlugin",
    name="Example Plugin",
    externallink="",
    body="PLUGIN_VALUE = 1\n",
):
    external_attribute = (
        ' externallink="{}"'.format(externallink) if externallink else ""
    )
    return (
        '"""\n<plugin key="{}" name="{}" author="tester"{}>\n'
        '</plugin>\n"""\n{}'.format(
            key,
            name,
            external_attribute,
            body,
        )
    ).encode("utf-8")


def write_files(root, files):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, contents in files.items():
        path = root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, str):
            contents = contents.encode("utf-8")
        path.write_bytes(contents)
    return root


def canonical_tree_sha256(files):
    records = []
    for relative_path, contents in files.items():
        relative_path = unicodedata.normalize("NFC", relative_path)
        path_bytes = relative_path.encode("utf-8")
        digest = sha256(contents).encode("ascii")
        record = (
            path_bytes
            + b"\0"
            + str(len(contents)).encode("ascii")
            + b"\0"
            + digest
            + b"\n"
        )
        records.append((path_bytes, record))
    records.sort(key=lambda item: item[0])
    return sha256(b"".join(record for _path, record in records))


def artifact_manifest(files):
    return {
        relative_path: {
            "sha256": sha256(contents),
            "size": len(contents),
        }
        for relative_path, contents in sorted(
            files.items(), key=lambda item: item[0].encode("utf-8")
        )
    }


def extracted_tree(
    tmp_path,
    *,
    root_prefix=ROOT_PREFIX,
    source_path=".",
    source_files=None,
    wrapper_files=None,
):
    source_files = dict(
        {
            "plugin.py": plugin_source(),
            "README.md": b"Example plugin\n",
        }
        if source_files is None
        else source_files
    )
    wrapper_files = dict({} if wrapper_files is None else wrapper_files)
    source_prefix = "" if source_path == "." else source_path + "/"
    all_files = dict(wrapper_files)
    all_files.update(
        {
            source_prefix + relative_path: contents
            for relative_path, contents in source_files.items()
        }
    )
    extraction_dir = Path(tmp_path) / "extracted"
    wrapper_root = write_files(extraction_dir / root_prefix, all_files)
    source_root = (
        wrapper_root
        if source_path == "."
        else wrapper_root.joinpath(*source_path.split("/"))
    )
    return extraction_dir, wrapper_root, source_root, source_files, all_files


def make_service(plugin_core_module, registry=None):
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = dict(
        registry
        or {
            "ExamplePlugin": [
                "owner",
                "example-plugin",
                "description",
                "main",
                "",
            ],
        }
    )
    return plugin_core_module.ReleaseArtifactValidationService(plugin)


def validate(
    service,
    extraction_dir,
    *,
    root_prefix=ROOT_PREFIX,
    source_path=".",
    plugin_key="ExamplePlugin",
    expected_tree_sha256,
    repository_identity=None,
    expected_domoticz_key=None,
    expected_plugin_py_sha256=None,
):
    return service.validate(
        extraction_dir=str(extraction_dir),
        root_prefix=root_prefix,
        source_path=source_path,
        plugin_key=plugin_key,
        expected_tree_sha256=expected_tree_sha256,
        repository_identity=repository_identity,
        expected_domoticz_key=expected_domoticz_key,
        expected_plugin_py_sha256=expected_plugin_py_sha256,
    )


def assert_rejected(plugin_core_module, reason, call):
    with pytest.raises(
        plugin_core_module.ReleaseArtifactValidationError
    ) as caught:
        call()
    assert caught.value.reason == reason
    return caught.value


def test_canonical_manifest_and_tree_use_portable_content_only_records(
    plugin_core_module, tmp_path
):
    files = {
        "z-last.txt": b"last\n",
        "plugin.py": plugin_source(),
        "café/data.txt": b"unicode\n",
        "A-first.txt": b"first\n",
    }
    first = extracted_tree(tmp_path / "first", source_files=files)
    second = extracted_tree(
        tmp_path / "second",
        root_prefix="different-wrapper-v1",
        source_files=dict(reversed(list(files.items()))),
    )
    (first[1] / "empty" / "nested").mkdir(parents=True)
    (second[1] / "another-empty-directory").mkdir()

    for path in first[1].rglob("*"):
        if path.is_file():
            os.chmod(path, 0o600)
            os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    for path in second[1].rglob("*"):
        if path.is_file():
            os.chmod(path, 0o755)
            os.utime(path, ns=(2_000_000_000, 2_000_000_000))

    expected_tree = canonical_tree_sha256(files)
    first_result = validate(
        make_service(plugin_core_module),
        first[0],
        expected_tree_sha256=expected_tree,
    )
    second_result = validate(
        make_service(plugin_core_module),
        second[0],
        root_prefix="different-wrapper-v1",
        expected_tree_sha256=expected_tree,
    )

    assert first_result.tree_sha256 == expected_tree
    assert second_result.tree_sha256 == expected_tree
    assert first_result.artifact_files == artifact_manifest(files)
    assert second_result.artifact_files == artifact_manifest(files)
    assert list(first_result.artifact_files) == sorted(
        files, key=lambda path: path.encode("utf-8")
    )
    assert not any("empty" in path for path in first_result.artifact_files)


def test_source_path_manifest_is_install_relative_but_tree_covers_full_wrapper(
    plugin_core_module, tmp_path
):
    source_files = {
        "plugin.py": plugin_source(),
        "package/module.py": b"VALUE = 42\n",
        "data/default.json": b"{}\n",
    }
    extraction_dir, _wrapper, source_root, _source, all_files = extracted_tree(
        tmp_path,
        source_path="domoticz/plugin",
        source_files=source_files,
        wrapper_files={
            "README.md": b"Repository documentation\n",
            "release-notes.txt": b"not installed\n",
        },
    )
    expected_tree = canonical_tree_sha256(all_files)

    result = validate(
        make_service(plugin_core_module),
        extraction_dir,
        source_path="domoticz/plugin",
        expected_tree_sha256=expected_tree,
    )

    assert Path(result.source_root) == source_root
    assert result.tree_sha256 == expected_tree
    assert result.tree_sha256 != canonical_tree_sha256(source_files)
    assert result.artifact_files == artifact_manifest(source_files)
    assert "README.md" not in result.artifact_files
    assert not any(path.startswith("domoticz/") for path in result.artifact_files)


def test_indexed_no_wrapper_layout_is_certified_for_release_assets(
    plugin_core_module, tmp_path
):
    files = {
        "plugin.py": plugin_source(),
        "README.md": b"Attached release asset\n",
        "package/module.py": b"VALUE = 1\n",
    }
    extraction_dir, _wrapper, source_root, _source, all_files = extracted_tree(
        tmp_path,
        root_prefix=".",
        source_files=files,
    )

    result = validate(
        make_service(plugin_core_module),
        extraction_dir,
        root_prefix=".",
        expected_tree_sha256=canonical_tree_sha256(all_files),
    )

    assert Path(result.source_root) == source_root
    assert result.artifact_files == artifact_manifest(files)


def test_indexed_tree_digest_must_match_the_observed_canonical_tree(
    plugin_core_module, tmp_path
):
    extraction_dir, *_ = extracted_tree(tmp_path)

    error = assert_rejected(
        plugin_core_module,
        "tree_mismatch",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256="0" * 64,
        ),
    )

    assert "release index" in str(error)


def test_staging_changes_during_validation_are_rejected(
    plugin_core_module, tmp_path, monkeypatch
):
    extraction_dir, _wrapper, source_root, _files, all_files = extracted_tree(
        tmp_path
    )
    service = make_service(plugin_core_module)
    certify_identity = service._certify_identity

    def mutate_after_identity(*args, **kwargs):
        result = certify_identity(*args, **kwargs)
        (source_root / "plugin.py").write_bytes(
            plugin_source(body="PLUGIN_VALUE = 2\n")
        )
        return result

    monkeypatch.setattr(service, "_certify_identity", mutate_after_identity)

    assert_rejected(
        plugin_core_module,
        "unsafe_tree",
        lambda: validate(
            service,
            extraction_dir,
            expected_tree_sha256=canonical_tree_sha256(all_files),
        ),
    )


def test_tree_inventory_does_not_retain_python_source_bytes(
    plugin_core_module, tmp_path
):
    _extraction, wrapper, _source, _files, _all_files = extracted_tree(
        tmp_path,
        source_files={
            "plugin.py": plugin_source(),
            "package/large.py": b"VALUE = 1\n" * 10_000,
        },
    )
    service = make_service(plugin_core_module)

    files, _directories = service._scan_tree(str(wrapper))

    assert files
    assert all(
        not any(
            isinstance(value, (bytes, bytearray, memoryview))
            for value in vars(file_record).values()
        )
        for file_record in files
    )


@pytest.mark.parametrize(
    (
        "actual_root",
        "indexed_root",
        "source_path",
        "indexed_source",
        "reason",
    ),
    [
        (
            "Example-Plugin-v1",
            "example-plugin-v1",
            ".",
            ".",
            "root_layout",
        ),
        (ROOT_PREFIX, ROOT_PREFIX, "Plugin", "plugin", "source_path"),
        (ROOT_PREFIX, ROOT_PREFIX, "plugin", "missing", "source_path"),
    ],
    ids=["wrapper-case", "source-case", "missing-source"],
)
def test_wrapper_and_source_path_resolution_is_exact(
    plugin_core_module,
    tmp_path,
    actual_root,
    indexed_root,
    source_path,
    indexed_source,
    reason,
):
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        root_prefix=actual_root,
        source_path=source_path,
    )

    assert_rejected(
        plugin_core_module,
        reason,
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            root_prefix=indexed_root,
            source_path=indexed_source,
            expected_tree_sha256=canonical_tree_sha256(all_files),
        ),
    )


def test_multiple_wrapper_roots_are_rejected_as_ambiguous(
    plugin_core_module, tmp_path
):
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path
    )
    write_files(
        extraction_dir / "unexpected-second-root",
        {"plugin.py": plugin_source()},
    )

    assert_rejected(
        plugin_core_module,
        "root_layout",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256=canonical_tree_sha256(all_files),
        ),
    )


def test_source_path_is_never_resolved_through_a_link(
    plugin_core_module, tmp_path
):
    extraction_dir, wrapper, _source, _files, _all_files = extracted_tree(
        tmp_path,
        source_path="real-plugin",
    )
    linked_source = wrapper / "selected-plugin"
    try:
        linked_source.symlink_to("real-plugin", target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable on this host")

    assert_rejected(
        plugin_core_module,
        "unsafe_tree",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            source_path="selected-plugin",
            expected_tree_sha256="0" * 64,
        ),
    )


@pytest.mark.parametrize(
    ("source_files", "reason"),
    [
        ({"README.md": b"missing plugin\n"}, "plugin_missing"),
        (
            {"plugin.py": b"", "README.md": b"empty plugin\n"},
            "plugin_missing",
        ),
    ],
    ids=["missing", "empty"],
)
def test_selected_source_requires_a_nonempty_root_plugin_py(
    plugin_core_module, tmp_path, source_files, reason
):
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_files=source_files,
    )

    assert_rejected(
        plugin_core_module,
        reason,
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256=canonical_tree_sha256(all_files),
        ),
    )


def test_canonical_tree_rejects_symlinks_even_when_they_stay_inside_root(
    plugin_core_module, tmp_path
):
    extraction_dir, wrapper, _source, _files, _all_files = extracted_tree(
        tmp_path
    )
    try:
        (wrapper / "readme-link.txt").symlink_to("README.md")
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are unavailable on this host")

    assert_rejected(
        plugin_core_module,
        "unsafe_tree",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256="0" * 64,
        ),
    )


def test_canonical_tree_rejects_special_files(plugin_core_module, tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO fixtures are unavailable on this host")
    extraction_dir, wrapper, _source, _files, _all_files = extracted_tree(
        tmp_path
    )
    os.mkfifo(wrapper / "runtime.pipe")

    assert_rejected(
        plugin_core_module,
        "unsafe_tree",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256="0" * 64,
        ),
    )


def test_canonical_tree_rejects_casefold_collisions(
    plugin_core_module, tmp_path
):
    extraction_dir, wrapper, _source, _files, _all_files = extracted_tree(
        tmp_path,
        source_files={
            "plugin.py": plugin_source(),
            "README.md": b"first\n",
            "readme.md": b"second\n",
        },
    )
    spellings = {path.name for path in wrapper.iterdir()}
    if not {"README.md", "readme.md"}.issubset(spellings):
        pytest.skip("the test filesystem is case-insensitive")

    assert_rejected(
        plugin_core_module,
        "unsafe_tree",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256="0" * 64,
        ),
    )


def test_canonical_tree_rejects_nfc_collisions(
    plugin_core_module, tmp_path
):
    nfc_name = unicodedata.normalize("NFC", "café.txt")
    nfd_name = unicodedata.normalize("NFD", nfc_name)
    assert nfc_name != nfd_name
    extraction_dir, wrapper, _source, _files, _all_files = extracted_tree(
        tmp_path,
        source_files={
            "plugin.py": plugin_source(),
            nfc_name: b"first\n",
            nfd_name: b"second\n",
        },
    )
    spellings = {path.name for path in wrapper.iterdir()}
    if not {nfc_name, nfd_name}.issubset(spellings):
        pytest.skip("the test filesystem normalizes Unicode names")

    assert_rejected(
        plugin_core_module,
        "unsafe_tree",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256="0" * 64,
        ),
    )


@pytest.mark.parametrize(
    "reserved_path",
    [
        ".pypluginstore.json",
        "nested/.PyPluginStore/state.json",
    ],
)
def test_canonical_tree_rejects_archive_provided_manager_metadata(
    plugin_core_module, tmp_path, reserved_path
):
    extraction_dir, _wrapper, _source, _files, _all_files = extracted_tree(
        tmp_path,
        source_files={
            "plugin.py": plugin_source(),
            reserved_path: b"untrusted manager state\n",
        },
    )

    assert_rejected(
        plugin_core_module,
        "unsafe_tree",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256="0" * 64,
        ),
    )


def test_identity_certification_accepts_flexible_existing_registry_evidence(
    plugin_core_module, tmp_path
):
    source_files = {
        "plugin.py": plugin_source(key="HPILO", name="HP iLO"),
        "README.md": b"HP iLO plugin\n",
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_path="Domoticz-HP-iLo",
        source_files=source_files,
    )
    service = make_service(
        plugin_core_module,
        {
            "HP_iLo": [
                "MadPatrick",
                "Domoticz_HP_ilo",
                "description",
                "main",
                "",
            ],
        },
    )

    result = validate(
        service,
        extraction_dir,
        source_path="Domoticz-HP-iLo",
        plugin_key="HP_iLo",
        expected_tree_sha256=canonical_tree_sha256(all_files),
    )

    assert result.plugin_key == "HP_iLo"
    assert result.identity_source == "normalized folder name"


def test_explicit_package_identity_accepts_sma_without_package_id_guessing(
    plugin_core_module, tmp_path
):
    plugin_contents = plugin_source(key="SMA", name="SMA Inverter")
    source_files = {
        "plugin.py": plugin_contents,
        "README.md": b"SMA inverter plugin\n",
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_path="upstream-payload",
        source_files=source_files,
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.normalize_registry(
        {
            "schema_version": 2,
            "packages": [
                {
                    "package_id": "Domoticz-SMA-Inverter",
                    "domoticz_key": "SMA",
                    "description": "SMA inverter",
                    "repository": {
                        "url": (
                            "https://github.com/SBFspot/"
                            "Domoticz-SMA-Inverter"
                        ),
                        "branch": "master",
                    },
                    "platforms": ["linux"],
                    "delivery": {
                        "preferred": "release_if_indexed",
                        "git_supported": True,
                        "release": {
                            "provider": "github",
                            "channel": "stable",
                            "tag_pattern": r"^v[0-9]+\.[0-9]+\.[0-9]+$",
                            "artifact": "source_zip",
                            "source_path": "upstream-payload",
                            "mutable_paths": [],
                        },
                    },
                }
            ],
        }
    )
    service = plugin_core_module.ReleaseArtifactValidationService(plugin)

    result = validate(
        service,
        extraction_dir,
        source_path="upstream-payload",
        plugin_key="Domoticz-SMA-Inverter",
        expected_tree_sha256=canonical_tree_sha256(all_files),
        repository_identity=(
            "github.com/sbfspot/domoticz-sma-inverter"
        ),
        expected_domoticz_key="SMA",
        expected_plugin_py_sha256=sha256(plugin_contents),
    )

    assert result.plugin_key == "Domoticz-SMA-Inverter"
    assert result.identity_source == "certified plugin.py identity"


@pytest.mark.parametrize(
    ("expected_domoticz_key", "expected_digest"),
    [
        ("OTHER", "actual"),
        ("SMA", "0" * 64),
    ],
)
def test_explicit_package_identity_rejects_wrong_release_certification(
    plugin_core_module,
    tmp_path,
    expected_domoticz_key,
    expected_digest,
):
    plugin_contents = plugin_source(key="SMA", name="SMA Inverter")
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_files={"plugin.py": plugin_contents},
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "SmaPackage": [
            "owner",
            "sma-plugin",
            "SMA inverter",
            "main",
        ]
    }
    plugin.registry_entries = {
        "SmaPackage": plugin_core_module.RegistryEntry(
            "SmaPackage",
            "owner",
            "sma-plugin",
            "SMA inverter",
            "main",
            domoticz_key="SMA",
        )
    }
    service = plugin_core_module.ReleaseArtifactValidationService(plugin)
    if expected_digest == "actual":
        expected_digest = sha256(plugin_contents)

    assert_rejected(
        plugin_core_module,
        "identity_mismatch",
        lambda: validate(
            service,
            extraction_dir,
            plugin_key="SmaPackage",
            expected_tree_sha256=canonical_tree_sha256(all_files),
            expected_domoticz_key=expected_domoticz_key,
            expected_plugin_py_sha256=expected_digest,
        ),
    )


def test_identity_certification_accepts_externallink_for_arbitrary_layout(
    plugin_core_module, tmp_path
):
    source_files = {
        "plugin.py": plugin_source(
            key="UNRELATED",
            name="Unrelated folder name",
            externallink="https://github.com/Smanar/Domoticz-deCONZ",
        ),
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_path="payload",
        source_files=source_files,
    )
    service = make_service(
        plugin_core_module,
        {
            "deCONZ": [
                "Smanar",
                "Domoticz-deCONZ",
                "description",
                "master",
                "",
            ],
        },
    )

    result = validate(
        service,
        extraction_dir,
        source_path="payload",
        plugin_key="deCONZ",
        expected_tree_sha256=canonical_tree_sha256(all_files),
    )

    assert result.plugin_key == "deCONZ"
    assert result.identity_source == "plugin.py externallink"


@pytest.mark.parametrize(
    "repository_base",
    [
        "https://gitlab.com/group/subgroup",
        "https://codeberg.org/acme",
        "https://forgejo.example.test/acme",
        "https://gitea.example.test/acme",
        "https://releases.example.test/catalog",
    ],
    ids=("gitlab", "codeberg", "forgejo", "gitea", "generic"),
)
def test_externallink_identity_is_forge_neutral(
    plugin_core_module, tmp_path, repository_base
):
    repository_url = repository_base + "/portable-plugin"
    source_files = {
        "plugin.py": plugin_source(
            key="UNRELATED",
            name="Unrelated layout",
            externallink=repository_url,
        ),
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_path="payload",
        source_files=source_files,
    )
    service = make_service(
        plugin_core_module,
        {
            "PortablePlugin": [
                repository_base,
                "portable-plugin",
                "description",
                "main",
                "",
            ],
        },
    )

    result = validate(
        service,
        extraction_dir,
        source_path="payload",
        plugin_key="PortablePlugin",
        expected_tree_sha256=canonical_tree_sha256(all_files),
        repository_identity=repository_url[len("https://") :],
    )

    assert result.identity_source == "plugin.py externallink"


def test_release_repository_identity_must_match_the_registry(
    plugin_core_module, tmp_path
):
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path
    )

    assert_rejected(
        plugin_core_module,
        "identity_mismatch",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256=canonical_tree_sha256(all_files),
            repository_identity="gitlab.com/other/project",
        ),
    )


def test_stronger_identity_tier_cannot_certify_a_different_registry_plugin(
    plugin_core_module, tmp_path
):
    source_files = {
        "plugin.py": plugin_source(
            key="FirstPlugin",
            name="First Plugin",
            externallink="https://github.com/owner-b/second-plugin",
        ),
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_path="FirstPlugin",
        source_files=source_files,
    )
    service = make_service(
        plugin_core_module,
        {
            "FirstPlugin": [
                "owner-a",
                "first-plugin",
                "description",
                "main",
                "",
            ],
            "SecondPlugin": [
                "owner-b",
                "second-plugin",
                "description",
                "main",
                "",
            ],
        },
    )

    assert_rejected(
        plugin_core_module,
        "identity_mismatch",
        lambda: validate(
            service,
            extraction_dir,
            source_path="FirstPlugin",
            plugin_key="FirstPlugin",
            expected_tree_sha256=canonical_tree_sha256(all_files),
        ),
    )


def test_identity_certification_reports_ambiguous_existing_matches(
    plugin_core_module, tmp_path
):
    source_files = {
        "plugin.py": plugin_source(key="SHARED", name="Shared Plugin"),
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_path="Shared Plugin",
        source_files=source_files,
    )
    service = make_service(
        plugin_core_module,
        {
            "FirstPlugin": [
                "owner-a",
                "Shared-Plugin",
                "description",
                "master",
                "",
            ],
            "SecondPlugin": [
                "owner-b",
                "Shared_Plugin",
                "description",
                "master",
                "",
            ],
        },
    )

    assert_rejected(
        plugin_core_module,
        "identity_ambiguous",
        lambda: validate(
            service,
            extraction_dir,
            source_path="Shared Plugin",
            plugin_key="FirstPlugin",
            expected_tree_sha256=canonical_tree_sha256(all_files),
        ),
    )


def test_python_sources_are_compiled_without_being_executed(
    plugin_core_module, tmp_path
):
    source_files = {
        "plugin.py": plugin_source(),
        "package/compile_only.py": b"raise RuntimeError('must not execute')\n",
        "package/__init__.py": b"VALUE = 1\n",
        "data/template.txt": b"not Python\n",
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_files=source_files,
    )

    result = validate(
        make_service(plugin_core_module),
        extraction_dir,
        expected_tree_sha256=canonical_tree_sha256(all_files),
    )

    assert result.artifact_files == artifact_manifest(source_files)


def test_invalid_nested_python_source_blocks_certification(
    plugin_core_module, tmp_path
):
    source_files = {
        "plugin.py": plugin_source(),
        "package/broken.py": b"def broken(:\n    pass\n",
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_files=source_files,
    )

    error = assert_rejected(
        plugin_core_module,
        "compile_failed",
        lambda: validate(
            make_service(plugin_core_module),
            extraction_dir,
            expected_tree_sha256=canonical_tree_sha256(all_files),
        ),
    )

    assert "package/broken.py" in str(error)


def test_compile_skips_non_domoticz_folders(plugin_core_module, tmp_path):
    source_files = {
        "plugin.py": plugin_source(),
        "custom_components/broken.py": b"def broken(:\n    pass\n",
        "tests/invalid_syntax.py": b"class {):\n",
    }
    extraction_dir, _wrapper, _source, _files, all_files = extracted_tree(
        tmp_path,
        source_files=source_files,
    )

    # This should validate successfully because the compilation check skips those paths
    result = validate(
        make_service(plugin_core_module),
        extraction_dir,
        expected_tree_sha256=canonical_tree_sha256(all_files),
    )

    assert result.artifact_files == artifact_manifest(source_files)
