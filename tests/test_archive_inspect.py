import io
import zipfile

from conftest import REPO_ROOT, load_module_from_path

archive_inspect = load_module_from_path(
    "archive_inspect_under_test",
    REPO_ROOT / ".github" / "scripts" / "archive_inspect.py",
)
extract_plugin_from_zip = archive_inspect.extract_plugin_from_zip


def _build_zip(files: dict[str, bytes | tuple[bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, entry in files.items():
            if isinstance(entry, tuple):
                content, flag_bits = entry
                info = zipfile.ZipInfo(name)
                info.flag_bits = flag_bits
                zf.writestr(info, content)
            else:
                zf.writestr(name, entry)
    return buffer.getvalue()


def test_extract_root_plugin_py():
    archive = _build_zip({"plugin.py": b'"""<plugin key="test"></plugin>"""\n'})
    content, source_path = extract_plugin_from_zip(archive)
    assert content == b'"""<plugin key="test"></plugin>"""\n'
    assert source_path == "."


def test_extract_nested_plugin_py():
    archive = _build_zip({"custom/domoticz/plugin.py": b'"""<plugin key="test"></plugin>"""\n'})
    content, source_path = extract_plugin_from_zip(archive)
    assert content == b'"""<plugin key="test"></plugin>"""\n'
    assert source_path == "custom/domoticz"


def test_extract_shallowest_plugin_py():
    archive = _build_zip(
        {
            "deep/nested/path/plugin.py": b"deep",
            "plugin.py": b"shallow",
        }
    )
    content, source_path = extract_plugin_from_zip(archive)
    assert content == b"shallow"
    assert source_path == "."


def test_ignore_macosx_and_dotfiles():
    archive = _build_zip(
        {
            "__MACOSX/._plugin.py": b"apple metadata",
            ".hidden/plugin.py": b"hidden plugin",
            "src/plugin.py": b"valid plugin",
        }
    )
    content, source_path = extract_plugin_from_zip(archive)
    assert content == b"valid plugin"
    assert source_path == "src"


def test_source_zip_root_prefix_stripping():
    archive = _build_zip(
        {
            "repo-v1.0.0/src/plugin.py": b"src plugin",
            "repo-v1.0.0/README.md": b"readme",
        }
    )
    content, source_path = extract_plugin_from_zip(archive, is_source_zip=True)
    assert content == b"src plugin"
    assert source_path == "src"

    root_archive = _build_zip({"repo-v1.0.0/plugin.py": b"root plugin"})
    content, source_path = extract_plugin_from_zip(root_archive, is_source_zip=True)
    assert content == b"root plugin"
    assert source_path == "."


def test_expected_source_path_verification():
    archive = _build_zip(
        {
            "plugin.py": b"root",
            "sub/plugin.py": b"sub",
        }
    )
    content, source_path = extract_plugin_from_zip(archive, expected_source_path="sub")
    assert content == b"sub"
    assert source_path == "sub"

    content, source_path = extract_plugin_from_zip(archive, expected_source_path="nonexistent")
    assert content is None
    assert source_path is None


def test_expected_source_path_with_root_prefix():
    archive = _build_zip(
        {
            "my-repo-main/sub/plugin.py": b"sub plugin",
        }
    )
    content, source_path = extract_plugin_from_zip(
        archive,
        expected_source_path="sub",
        root_prefix="my-repo-main",
    )
    assert content == b"sub plugin"
    assert source_path == "sub"


def test_reject_oversized_plugin():
    huge_content = b"a" * (archive_inspect.MAX_PLUGIN_SOURCE_BYTES + 1)
    archive = _build_zip({"plugin.py": huge_content})
    content, source_path = extract_plugin_from_zip(archive)
    assert content is None
    assert source_path is None


def test_reject_empty_plugin():
    archive = _build_zip({"plugin.py": b""})
    content, source_path = extract_plugin_from_zip(archive)
    assert content is None
    assert source_path is None


def test_reject_encrypted_entry():
    raw = bytearray(_build_zip({"plugin.py": b"secret"}))
    raw[6] |= 0x1
    loc = raw.find(b"PK\x01\x02")
    if loc != -1:
        raw[loc + 8] |= 0x1
    content, source_path = extract_plugin_from_zip(bytes(raw))
    assert content is None
    assert source_path is None


def test_reject_directory_entry():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("plugin.py/", b"")
    content, source_path = extract_plugin_from_zip(buffer.getvalue())
    assert content is None
    assert source_path is None


def test_reject_nul_bytes_in_name():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        info = zipfile.ZipInfo("dir\x00/plugin.py")
        zf.writestr(info, b"content")
    content, source_path = extract_plugin_from_zip(buffer.getvalue())
    assert content is None
    assert source_path is None


def test_reject_invalid_archive_bytes():
    assert extract_plugin_from_zip(b"not a zip") == (None, None)
    assert extract_plugin_from_zip(b"") == (None, None)
    assert extract_plugin_from_zip(None) == (None, None)
