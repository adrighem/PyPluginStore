import os
import sys
import json
import hashlib
import re
import subprocess
import time

# Adjust path relative to the current script location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
REGISTRY_FILE_PATH = os.path.join(SCRIPT_DIR, '../../registry.json')
THEMES_FILE_PATH = os.path.join(SCRIPT_DIR, '../../themes.json')
UPDATE_TIMES_FILE_PATH = os.path.join(SCRIPT_DIR, '../../update_times.json')
PLATFORM_METADATA_FILE_PATH = os.path.join(SCRIPT_DIR, '../../.github/platform_detection.json')
RELEASE_INDEX_FILE_PATH = os.path.join(SCRIPT_DIR, '../../release_index.json')
DEFAULT_GIT_HOST = "github.com"
SUPPORTED_GIT_HOSTS = ("github.com", "gitlab.com", "codeberg.org")
VALID_PLATFORM_METADATA_SOURCES = {"unknown", "legacy_detected", "detected", "reviewed"}
VALID_PLATFORM_METADATA_CONFIDENCE = {"unknown", "low", "medium", "high"}
GIT_REMOTE_TIMEOUT_SECONDS = 30
ROOT_PLUGIN_MAX_ATTEMPTS = 3
ROOT_PLUGIN_RETRY_DELAY_SECONDS = 1

import io
import urllib.error
import urllib.parse
import urllib.request
import zipfile

try:
    from detect_plugin_platforms import (
        get_registry_entry_platforms,
        load_platform_metadata,
    )
except ImportError:
    get_registry_entry_platforms = None
    load_platform_metadata = None

try:
    from cleanup_registry import check_root_plugin_py
except ImportError:
    check_root_plugin_py = None

from package_identity import MAX_PLUGIN_SOURCE_BYTES, certify_plugin_py
from registry_records import (
    RegistryRecord,
    load_registry_file,
    load_update_times_file,
    parse_registry_owner,
)

def load_registry():
    print(f"Checking if registry file exists at: {REGISTRY_FILE_PATH}")
    if not os.path.isfile(REGISTRY_FILE_PATH):
        print(f"Registry file not found at: {REGISTRY_FILE_PATH}")
        sys.exit(1)

    registry_data = load_registry_file(REGISTRY_FILE_PATH)

    validate_platform_metadata(registry_data)
    validate_update_times(registry_data)
        
    plugin_data = {}
    for key, data in registry_data.items():
        if key == "Idle":
            continue
        validate_registry_entry(key, data)
        record = RegistryRecord.from_entry(key, data)
        plugin_data[key] = {
            "key": key,
            "author": record.owner,
            "repository": record.repository,
            "description": record.description,
            "branch": record.branch,
            "domoticz_key": data["domoticz_key"] if isinstance(data, dict) and "domoticz_key" in data else "",
            "record": record,
        }
    return plugin_data


def normalize_platforms(platforms):
    if get_registry_entry_platforms is not None:
        return get_registry_entry_platforms(["", "", "", "", "", platforms])

    if isinstance(platforms, str):
        platforms = [platforms]
    if not isinstance(platforms, list):
        return []

    normalized = []
    for platform in platforms:
        platform_name = str(platform or "").strip().lower()
        if platform_name in {"linux", "windows"} and platform_name not in normalized:
            normalized.append(platform_name)
    return [platform for platform in ("linux", "windows") if platform in normalized]


def validate_platform_metadata(registry_data):
    if not os.path.isfile(PLATFORM_METADATA_FILE_PATH):
        return

    if load_platform_metadata is None:
        raise ValueError("Platform metadata validation is unavailable.")
    metadata = load_platform_metadata(PLATFORM_METADATA_FILE_PATH)

    entries = metadata.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("Platform metadata must contain an entries object.")

    registry_keys = {key for key in registry_data if key != "Idle"}
    for key, entry in entries.items():
        if key not in registry_keys:
            raise ValueError(f"Platform metadata contains stale entry '{key}'.")
        if not isinstance(entry, dict):
            raise ValueError(f"Platform metadata entry '{key}' must be an object.")

        registry_platforms = RegistryRecord.from_entry(
            key, registry_data[key]
        ).platforms
        metadata_platforms = normalize_platforms(entry.get("registry_platforms", []))
        if metadata_platforms != registry_platforms:
            raise ValueError(f"Platform metadata entry '{key}' does not match registry platforms.")

        if entry.get("source") not in VALID_PLATFORM_METADATA_SOURCES:
            raise ValueError(f"Platform metadata entry '{key}' has invalid source.")
        if entry.get("confidence") not in VALID_PLATFORM_METADATA_CONFIDENCE:
            raise ValueError(f"Platform metadata entry '{key}' has invalid confidence.")
        if not isinstance(entry.get("reviewed", False), bool):
            raise ValueError(f"Platform metadata entry '{key}' has invalid reviewed flag.")


def validate_update_times(registry_data):
    if not os.path.isfile(UPDATE_TIMES_FILE_PATH):
        return

    update_times = load_update_times_file(UPDATE_TIMES_FILE_PATH)

    registry_keys = {key for key in registry_data if key != "Idle"}
    stale_keys = sorted(key for key in update_times if key not in registry_keys)
    if stale_keys:
        joined_keys = ", ".join(stale_keys)
        raise ValueError(f"Update-times file contains stale entries: {joined_keys}")


def validate_registry_entry(key, data):
    record = RegistryRecord.from_entry(key, data)
    if record.requires_python:
        for clause in record.requires_python.split(","):
            clause = clause.strip()
            if not clause or not re.match(
                r"^(?:==|!=|<=|>=|<|>|~=|===)\s*v?[0-9]+(?:\.[0-9]+)*(?:\.?(?:a|b|rc|post|dev)[0-9]*)?(?:\.\*)?$",
                clause,
            ):
                raise ValueError(
                    f"Registry package {key} has invalid requires_python specifier: {record.requires_python}"
                )


def split_registry_owner(author):
    location = parse_registry_owner(author)
    return location.host, location.owner_path


def build_repository_url(author, repository):
    location = parse_registry_owner(author)
    return (
        location.web_base
        + "/"
        + location.owner_path
        + "/"
        + repository
    )


def validate_release_index_binding(
    registry_path=REGISTRY_FILE_PATH,
    index_path=RELEASE_INDEX_FILE_PATH,
):
    """Require the generated index to bind the exact registry file bytes."""
    with open(registry_path, "rb") as registry_file:
        registry_bytes = registry_file.read()
    with open(index_path, "r", encoding="utf-8") as index_file:
        index = json.load(index_file)
    if not isinstance(index, dict):
        raise ValueError("Release index must contain a JSON object.")
    expected = hashlib.sha256(registry_bytes).hexdigest()
    if index.get("registry_sha256") != expected:
        raise ValueError("Release index registry binding does not match registry bytes.")
    return True


def validate_repository(author, repository, branch):
    repo_url = build_repository_url(author, repository)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    repo_clone_cmd = ["git", "ls-remote", "--heads", repo_url, branch]
    try:
        result = subprocess.run(
            repo_clone_cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_REMOTE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"Timed out executing command after {GIT_REMOTE_TIMEOUT_SECONDS}s: {' '.join(repo_clone_cmd)}")
        return False
    if result.returncode != 0:
        print(f"Error executing command: {' '.join(repo_clone_cmd)}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
    return result.returncode == 0 and bool(result.stdout.strip())


def validate_root_plugin_py(
    key,
    author,
    repository,
    branch,
    domoticz_key="",
    opener=None,
    sleeper=time.sleep,
):
    if check_root_plugin_py is None:
        print("Root plugin.py validation is unavailable because cleanup_registry.py could not be imported.")
        return False

    for attempt in range(1, ROOT_PLUGIN_MAX_ATTEMPTS + 1):
        result = check_root_plugin_py(
            key,
            [author, repository, "Plugin", branch],
            opener=opener,
        )
        if result.status == "present":
            if domoticz_key and result.domoticz_key != domoticz_key:
                print(
                    "Root plugin.py key for "
                    + key
                    + " is "
                    + result.domoticz_key
                    + ", expected "
                    + domoticz_key
                    + "."
                )
                return False
            return True
        if result.status != "error" or attempt == ROOT_PLUGIN_MAX_ATTEMPTS:
            break

        print(
            f"Root plugin.py check for {key} returned an error; "
            f"retrying ({attempt}/{ROOT_PLUGIN_MAX_ATTEMPTS - 1})."
        )
        sleeper(ROOT_PLUGIN_RETRY_DELAY_SECONDS)

    detail = f" ({result.reason})" if result.reason else ""
    print(f"Root plugin.py check failed for {key}: {result.status}{detail}")
    if result.url:
        print(f"Checked URL: {result.url}")
    return False


def load_release_index(index_path=RELEASE_INDEX_FILE_PATH):
    if not os.path.isfile(index_path):
        return {}
    with open(index_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    releases_by_pkg = {}
    for release in data.get("releases", []):
        if isinstance(release, dict) and "package_id" in release:
            releases_by_pkg[release["package_id"]] = release
    return releases_by_pkg


def validate_release_archive(
    key,
    record,
    release_entry,
    opener=None,
    timeout=30,
):
    artifact = release_entry.get("artifact", {})
    url = artifact.get("url")
    expected_sha256 = artifact.get("sha256")
    if not url or not expected_sha256:
        print(f"Release artifact for {key} missing url or sha256.")
        return False

    headers = {"User-Agent": "PyPluginStore-Release-Scanner"}
    host = urllib.parse.urlparse(url).hostname or ""
    if host in {"api.github.com", "raw.githubusercontent.com", "github.com"}:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"
    elif host == "gitlab.com":
        token = os.environ.get("GITLAB_TOKEN")
        if token:
            headers["PRIVATE-TOKEN"] = token

    request = urllib.request.Request(url, headers=headers)
    opener = opener or urllib.request.urlopen

    try:
        with opener(request, timeout=timeout) as response:
            content = response.read()
    except Exception as e:
        print(f"Failed downloading release artifact for {key}: {e}")
        return False

    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        print(f"SHA-256 mismatch for release artifact {key}: {actual_sha256} != {expected_sha256}")
        return False

    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
            namelist = zf.namelist()
            source_path = artifact.get("source_path", ".")
            root_prefix = artifact.get("root_prefix", "")
            plugin_py_entry = None
            for name in namelist:
                parts = name.strip("/").split("/")
                if root_prefix and parts and parts[0] == root_prefix:
                    parts = parts[1:]
                subpath = "/".join(parts)
                if source_path == "." and subpath == "plugin.py":
                    plugin_py_entry = name
                    break
                elif source_path != "." and subpath == f"{source_path}/plugin.py":
                    plugin_py_entry = name
                    break
                elif subpath.endswith("plugin.py"):
                    if not plugin_py_entry:
                        plugin_py_entry = name

            if not plugin_py_entry:
                print(f"plugin.py not found in release archive for {key}")
                return False

            plugin_py_bytes = zf.read(plugin_py_entry)
            identity = certify_plugin_py(plugin_py_bytes)
            expected_identity = release_entry.get("certified_identity", {})
            if expected_identity.get("domoticz_key") and identity.domoticz_key != expected_identity["domoticz_key"]:
                print(
                    f"Certified domoticz_key mismatch for {key}: {identity.domoticz_key} != {expected_identity['domoticz_key']}"
                )
                return False
            if expected_identity.get("plugin_py_sha256") and identity.plugin_py_sha256 != expected_identity["plugin_py_sha256"]:
                print(
                    f"Certified plugin_py_sha256 mismatch for {key}: {identity.plugin_py_sha256} != {expected_identity['plugin_py_sha256']}"
                )
                return False
    except Exception as e:
        print(f"Failed verifying release archive zip for {key}: {e}")
        return False

    return True


def validate_theme_entry(key, data):
    if not isinstance(data, dict):
        raise ValueError(f"Theme '{key}' must be an object.")

    required_keys = {"display_name": str, "author": str, "repository": str, "branch": str, "description": str, "target_dir": str}
    for req_key, req_type in required_keys.items():
        if req_key not in data:
            raise ValueError(f"Theme '{key}' is missing required key '{req_key}'.")
        if not isinstance(data[req_key], req_type):
            raise ValueError(f"Theme '{key}' key '{req_key}' must be a {req_type.__name__}.")
        if req_key != "description" and not data[req_key].strip():
            raise ValueError(f"Theme '{key}' key '{req_key}' must not be empty.")

    target_dir = data["target_dir"]
    if target_dir in (".", "..") or target_dir.startswith(".") or "/" in target_dir or "\\" in target_dir:
        raise ValueError(f"Theme '{key}' target_dir '{target_dir}' is not a safe directory name.")

    if "source_path" in data:
        if not isinstance(data["source_path"], str):
            raise ValueError(f"Theme '{key}' source_path must be a string.")
        if not data["source_path"].strip():
            raise ValueError(f"Theme '{key}' source_path must not be empty.")

    if "entry_files" in data:
        if not isinstance(data["entry_files"], list):
            raise ValueError(f"Theme '{key}' entry_files must be a list of strings.")
        for item in data["entry_files"]:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"Theme '{key}' entry_files must contain non-empty strings.")

    if "contains_javascript" in data and not isinstance(data["contains_javascript"], bool):
        raise ValueError(f"Theme '{key}' contains_javascript must be a boolean.")

    if "requires_restart" in data:
        if not isinstance(data["requires_restart"], str):
            raise ValueError(f"Theme '{key}' requires_restart must be a string.")


def load_themes():
    print(f"Checking if themes file exists at: {THEMES_FILE_PATH}")
    if not os.path.isfile(THEMES_FILE_PATH):
        print(f"Themes file not found at: {THEMES_FILE_PATH}")
        return {}

    with open(THEMES_FILE_PATH, "r", encoding="utf-8") as f:
        themes_data = json.load(f)

    if not isinstance(themes_data, dict):
        raise ValueError("themes.json must be a JSON object.")

    for key, data in themes_data.items():
        if key in (".", "..") or key.startswith(".") or "/" in key or "\\" in key:
            raise ValueError(f"Theme key '{key}' is not a safe identifier.")
        validate_theme_entry(key, data)

    return themes_data


def main():
    print("Loading registry file...")
    plugin_data = load_registry()
    if os.path.isfile(RELEASE_INDEX_FILE_PATH):
        validate_release_index_binding()
    print(f"Loaded {len(plugin_data)} plugins.")

    print("Loading themes file...")
    theme_data = load_themes()
    print(f"Loaded {len(theme_data)} themes.")

    if not plugin_data and not theme_data:
        print("No plugin or theme data found, exiting.")
        sys.exit(1)

    indexed_releases = load_release_index()

    all_valid = True
    for key, data in plugin_data.items():
        record = data.get("record")
        is_release_based = record is not None and (
            record.delivery.preferred == "release" or not record.delivery.git_supported
        )

        if is_release_based:
            print(f"Validating release archive for plugin: {key}")
            release_entry = indexed_releases.get(key)
            if not release_entry:
                print(f"❌ Release-based plugin {key} has no indexed releases in release_index.json.")
                all_valid = False
                continue
            archive_is_valid = validate_release_archive(key, record, release_entry)
            if archive_is_valid:
                print(f"✅ Release archive for {key} is valid.")
            else:
                print(f"❌ Release archive for {key} is invalid.")
                all_valid = False
        else:
            print(f"Validating repository for plugin: {key}")
            repository_is_valid = validate_repository(data["author"], data["repository"], data["branch"])
            plugin_file_is_valid = False
            if repository_is_valid:
                plugin_file_is_valid = validate_root_plugin_py(
                    key,
                    data["author"],
                    data["repository"],
                    data["branch"],
                    data["domoticz_key"],
                )

            if repository_is_valid and plugin_file_is_valid:
                print(f"✅ Repository {data['author']}/{data['repository']} on branch {data['branch']} is valid.")
            else:
                print(f"❌ Repository {data['author']}/{data['repository']} on branch {data['branch']} is invalid.")
                all_valid = False

    for key, data in theme_data.items():
        print(f"Validating repository for theme: {key}")
        repository_is_valid = validate_repository(data["author"], data["repository"], data["branch"])
        if repository_is_valid:
            print(f"✅ Theme repository {data['author']}/{data['repository']} on branch {data['branch']} is valid.")
        else:
            print(f"❌ Theme repository {data['author']}/{data['repository']} on branch {data['branch']} is invalid.")
            all_valid = False

    if not all_valid:
        print("One or more registry items are invalid.")
        sys.exit(1)  # Exit with a non-zero code to indicate failure

if __name__ == "__main__":
    main()
