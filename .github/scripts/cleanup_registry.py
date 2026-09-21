#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from detect_plugin_platforms import (
    load_platform_metadata,
    new_platform_metadata,
    save_platform_metadata,
)
from generate_release_index import (
    _atomic_write as atomic_write_json,
    _canonical_json_bytes as canonical_json_bytes,
    _strict_json_object as parse_release_index_json,
    _validate_v2_previous as validate_release_index_v2,
)
from package_identity import MAX_PLUGIN_SOURCE_BYTES, certify_plugin_py
from registry_records import (
    RegistryRecord,
    load_registry_file,
    load_update_times_file,
    parse_registry_owner,
    save_registry_file,
    save_update_times_file,
)


REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
REGISTRY_FILE = os.path.join(REPO_ROOT, "registry.json")
UPDATE_TIMES_FILE = os.path.join(REPO_ROOT, "update_times.json")
PLATFORM_METADATA_FILE = os.path.join(REPO_ROOT, ".github", "platform_detection.json")
RELEASE_INDEX_FILE = os.path.join(REPO_ROOT, "release_index.json")

DEFAULT_GIT_HOST = "github.com"
SUPPORTED_GIT_HOSTS = ("github.com", "gitlab.com", "codeberg.org")
API_USER_AGENT = "Domoticz-Plugin-Registry-Cleanup"

REMOVABLE_STATUSES = {"missing", "empty"}


class CheckResult:
    def __init__(
        self,
        key,
        status,
        url="",
        reason="",
        domoticz_key="",
        plugin_py_sha256="",
    ):
        self.key = key
        self.status = status
        self.url = url
        self.reason = reason
        self.domoticz_key = domoticz_key
        self.plugin_py_sha256 = plugin_py_sha256

    @property
    def removable(self):
        return self.status in REMOVABLE_STATUSES


def split_registry_owner(author):
    location = parse_registry_owner(author)
    return location.host, location.owner_path


def quote_path_part(value):
    return urllib.parse.quote(str(value), safe="")


def quote_repo_path(path):
    return urllib.parse.quote(str(path), safe="/")


def raw_plugin_url(author, repository, branch):
    return RegistryRecord.from_entry(
        "Plugin",
        [author, repository, "Plugin", branch],
    ).raw_plugin_url


def headers_for_url(url):
    host = urllib.parse.urlparse(url).hostname or ""
    headers = {
        "Accept": "text/plain,*/*",
        "User-Agent": API_USER_AGENT,
    }

    if host == "raw.githubusercontent.com":
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"
    elif host == "gitlab.com":
        token = os.environ.get("GITLAB_TOKEN")
        if token:
            headers["PRIVATE-TOKEN"] = token

    return headers


def check_root_plugin_py(key, data, opener=None):
    try:
        record = RegistryRecord.from_entry(key, data)
    except ValueError as error:
        return CheckResult(key, "invalid-entry", reason=str(error))

    if not record.delivery.git_supported or record.delivery.preferred == "release":
        return CheckResult(key, "present", reason="release-based delivery")

    url = record.raw_plugin_url
    request = urllib.request.Request(url, headers=headers_for_url(url))
    opener = opener or urllib.request.urlopen

    try:
        with opener(request, timeout=15) as response:
            content = response.read(MAX_PLUGIN_SOURCE_BYTES + 1)
            if content.strip():
                if len(content) > MAX_PLUGIN_SOURCE_BYTES:
                    return CheckResult(
                        key,
                        "invalid-plugin",
                        url=url,
                        reason="root plugin.py exceeds its size limit",
                    )
                try:
                    identity = certify_plugin_py(content)
                except ValueError as error:
                    return CheckResult(
                        key,
                        "invalid-plugin",
                        url=url,
                        reason=str(error),
                    )
                return CheckResult(
                    key,
                    "present",
                    url=url,
                    domoticz_key=identity.domoticz_key,
                    plugin_py_sha256=identity.plugin_py_sha256,
                )
            return CheckResult(key, "empty", url=url, reason="root plugin.py is empty")
    except urllib.error.HTTPError as e:
        if e.code in {404, 410}:
            return CheckResult(key, "missing", url=url, reason=f"HTTP {e.code}")
        return CheckResult(key, "error", url=url, reason=f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        return CheckResult(key, "error", url=url, reason=str(e.reason))
    except Exception as e:
        return CheckResult(key, "error", url=url, reason=str(e))


def remove_registry_entries(registry, update_times, platform_metadata, keys):
    for key in keys:
        registry.pop(key, None)
        update_times.pop(key, None)
        platform_metadata.get("entries", {}).pop(key, None)


def load_active_release_package_ids(release_index_file, registry):
    """Return active package IDs from the canonical strict v2 index parser."""
    document = parse_release_index_json(
        Path(release_index_file).read_bytes(),
        "release_index.json",
    )
    releases = document.get("releases")
    retiring_package_ids = set()
    if isinstance(releases, list):
        retiring_package_ids = {
            release.get("package_id")
            for release in releases
            if isinstance(release, dict)
            and isinstance(release.get("package_id"), str)
            and release["package_id"] not in registry
        }
    validated = validate_release_index_v2(
        document,
        registry,
        retiring_package_ids=retiring_package_ids,
    )
    return set(validated["plugins"])


def tombstone_reason(result=None):
    if result is None:
        return (
            "Weekly registry maintenance removed this package before "
            "release-index generation."
        )
    detail = f" ({result.reason})" if result.reason else ""
    return (
        "Registry cleanup removed this package because its configured root "
        f"plugin.py is {result.status}{detail}."
    )


def write_tombstone_requests(path, requests):
    """Atomically write canonical, deterministic tombstone request JSON."""
    document = {
        package_id: {"reason": requests[package_id]["reason"]}
        for package_id in sorted(
            requests,
            key=lambda value: (value.casefold(), value),
        )
    }
    atomic_write_json(Path(path), canonical_json_bytes(document))


def print_result(result, apply_changes):
    if result.status == "present":
        print(f"[OK] {result.key}")
    elif result.removable:
        action = "Removing" if apply_changes else "Would remove"
        print(f"[-] {action} {result.key}: {result.status} plugin.py ({result.reason})")
    else:
        detail = f": {result.reason}" if result.reason else ""
        print(f"[!] Keeping {result.key}: {result.status}{detail}")


def cleanup_registry_files(
    registry_file=REGISTRY_FILE,
    update_times_file=UPDATE_TIMES_FILE,
    platform_metadata_file=PLATFORM_METADATA_FILE,
    apply_changes=False,
    sleep_seconds=0.2,
    keys=None,
    opener=None,
    release_index_file=RELEASE_INDEX_FILE,
    tombstone_requests_output=None,
):
    registry = load_registry_file(registry_file)

    update_times_exists = os.path.exists(update_times_file)
    update_times = load_update_times_file(
        update_times_file,
        missing_ok=True,
    )

    platform_metadata_exists = os.path.exists(platform_metadata_file)
    platform_metadata = (
        load_platform_metadata(platform_metadata_file)
        if platform_metadata_exists
        else new_platform_metadata()
    )

    selected_keys = set(keys or [])
    results = []
    for key in registry:
        if key == "Idle":
            continue
        if selected_keys and key not in selected_keys:
            continue

        result = check_root_plugin_py(key, registry[key], opener=opener)
        results.append(result)
        print_result(result, apply_changes)

        if sleep_seconds:
            time.sleep(sleep_seconds)

    removable_keys = [result.key for result in results if result.removable]
    if apply_changes and tombstone_requests_output is not None:
        active_releases = load_active_release_package_ids(
            release_index_file,
            registry,
        )
        removable_results = {
            result.key: result
            for result in results
            if result.removable
        }
        retained_package_ids = set(registry) - set(removable_keys)
        requests = {
            package_id: {
                "reason": tombstone_reason(removable_results.get(package_id))
            }
            for package_id in active_releases - retained_package_ids
        }
        write_tombstone_requests(tombstone_requests_output, requests)

    if apply_changes and removable_keys:
        remove_registry_entries(registry, update_times, platform_metadata, removable_keys)
        save_registry_file(registry_file, registry)
        if update_times_exists:
            save_update_times_file(update_times_file, update_times)
        if platform_metadata_exists:
            save_platform_metadata(platform_metadata, platform_metadata_file)

    stats = {
        "checked": len(results),
        "present": sum(1 for result in results if result.status == "present"),
        "would_remove": 0 if apply_changes else len(removable_keys),
        "removed": len(removable_keys) if apply_changes else 0,
        "errors": sum(
            1
            for result in results
            if result.status in {"error", "invalid-entry", "invalid-plugin"}
        ),
    }
    return stats


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Remove public registry entries whose configured branch does not contain a root plugin.py.",
    )
    parser.add_argument("--registry", default=REGISTRY_FILE, help="Path to registry.json.")
    parser.add_argument("--update-times", default=UPDATE_TIMES_FILE, help="Path to update_times.json.")
    parser.add_argument(
        "--platform-metadata",
        default=PLATFORM_METADATA_FILE,
        help="Path to .github/platform_detection.json.",
    )
    parser.add_argument("--apply", action="store_true", help="Write removals to registry files.")
    parser.add_argument(
        "--release-index",
        default=RELEASE_INDEX_FILE,
        help="Path to the strict v2 release_index.json used for tombstone requests.",
    )
    parser.add_argument(
        "--tombstone-requests-output",
        help="Atomically write active removed releases as generator tombstone requests.",
    )
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay between remote checks, in seconds.")
    parser.add_argument("--only", action="append", default=[], help="Only check this registry key. Can be repeated.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        stats = cleanup_registry_files(
            registry_file=args.registry,
            update_times_file=args.update_times,
            platform_metadata_file=args.platform_metadata,
            apply_changes=args.apply,
            sleep_seconds=args.sleep,
            keys=args.only,
            release_index_file=args.release_index,
            tombstone_requests_output=args.tombstone_requests_output,
        )
    except Exception as e:
        print(f"Registry cleanup failed: {e}", file=sys.stderr)
        return 1

    if args.apply:
        print(
            "Registry cleanup complete: "
            f"{stats['checked']} checked, {stats['removed']} removed, {stats['errors']} kept with errors."
        )
    else:
        print(
            "Dry run complete: "
            f"{stats['checked']} checked, {stats['would_remove']} removable, "
            f"{stats['errors']} kept with errors. Re-run with --apply to update files."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
