import json
import os
import sys
import urllib.request
import urllib.parse
from urllib.error import HTTPError
import time
from datetime import datetime, timezone
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from detect_plugin_platforms import (
    choose_platforms_for_registry,
    detect_platforms_for_repo,
    decision_confidence,
    decision_platforms,
    ensure_platform_metadata_for_registry,
    get_registry_entry_platforms,
    load_platform_metadata,
    normalize_platforms,
    platform_metadata_identity,
    save_platform_metadata,
    update_platform_metadata_entry,
)
from package_identity import MAX_PLUGIN_SOURCE_BYTES, certify_plugin_py
from registry_records import (
    DEFAULT_STABLE_TAG_PATTERN,
    RegistryRecord,
    build_package_document,
    load_registry_file,
    load_update_times_file,
    normalize_repository_identity,
    normalize_update_timestamp,
    parse_registry_owner,
    save_registry_file,
    save_update_times_file,
)

REGISTRY_FILE = os.path.join(SCRIPT_DIR, '../../registry.json')
UPDATE_TIMES_FILE = os.path.join(SCRIPT_DIR, '../../update_times.json')
PLATFORM_METADATA_FILE = os.path.join(SCRIPT_DIR, '../../.github/platform_detection.json')
DEFAULT_GIT_HOST = "github.com"
SUPPORTED_GIT_HOSTS = ("github.com", "gitlab.com", "codeberg.org")
ROOT_PLUGIN_CHECKED_FIELD = "_pypluginstore_root_plugin_py_checked"
ROOT_PLUGIN_IDENTITY_FIELD = "_pypluginstore_root_plugin_identity"
RELEASE_TAG_PATTERN_CHECKED_FIELD = "_pypluginstore_release_tag_pattern_checked"
RELEASE_TAG_PATTERN_FIELD = "_pypluginstore_release_tag_pattern"
REQUEST_TIMEOUT_SECONDS = 20
DOTTED_V_STABLE_TAG_PATTERN = r"^v\.[0-9]+(?:\.[0-9]+){1,3}$"
RECOGNIZED_STABLE_TAG_PATTERNS = (
    DEFAULT_STABLE_TAG_PATTERN,
    DOTTED_V_STABLE_TAG_PATTERN,
)

# Repositories that should never be added to or kept in the registry.
REPO_BLOCKLIST = {
    "adrighem/pp-manager",
    "adrighem/pypluginstore",
    "domoticz/domoticz",
    "galadril/domoticz-python-plugin-template",
    "ycahome/pp-manager",
}

def is_valid_plugin_repo(repo_name):
    return bool(repo_name) and not repo_name.startswith('.') and '/' not in repo_name and '\\' not in repo_name

def split_registry_owner(author):
    location = parse_registry_owner(author)
    return location.host, location.owner_path


def get_registry_owner(host, owner_path):
    host = str(host or DEFAULT_GIT_HOST).strip().lower()
    owner_path = str(owner_path or "").strip().strip("/")
    if host == DEFAULT_GIT_HOST:
        return owner_path
    return host + "/" + owner_path


def get_repository_identity(owner, repo):
    return normalize_repository_identity(owner, repo)


def normalize_full_name(owner, repo):
    return f"{owner}/{repo}".lower()

def get_repo_block_reason(owner, repo):
    if normalize_full_name(owner, repo) in REPO_BLOCKLIST:
        return "Repo blocklisted"
    return None

def get_repo_skip_reason(repo):
    if repo.get('archived'):
        return "Repo archived"
    if repo.get('disabled'):
        return "Repo disabled"
    if repo.get('empty') or repo.get('empty_repo'):
        return "Repo empty"

    size = repo.get('size')
    if size is not None:
        try:
            if int(size) <= 0:
                return "Repo empty"
        except (TypeError, ValueError):
            pass

    return None

def remove_registry_entry(registry, update_times, platform_metadata, key, reason):
    print(f"[-] Removing {key} ({reason})")
    del registry[key]
    if key in update_times:
        del update_times[key]
    platform_metadata.get("entries", {}).pop(key, None)


def prune_stale_update_times(update_times, registry):
    registry_keys = {key for key in registry if key != "Idle"}
    stale_keys = sorted(key for key in update_times if key not in registry_keys)
    for key in stale_keys:
        print(f"[-] Removing stale update time for {key} (not in registry)")
        del update_times[key]
    return stale_keys


def build_registry_entry(
    package_id,
    domoticz_key,
    owner,
    repo_name,
    description,
    branch,
    platforms=None,
    release_tag_pattern="",
):
    return build_package_document(
        package_id,
        domoticz_key,
        owner,
        repo_name,
        description,
        branch,
        platforms,
        release_tag_pattern,
    )


def github_headers():
    headers = {'User-Agent': 'Domoticz-Plugin-Scanner', 'Accept': 'application/vnd.github.v3+json'}
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'token {token}'
    return headers


def gitlab_headers():
    headers = {'User-Agent': 'Domoticz-Plugin-Scanner', 'Accept': 'application/json'}
    token = os.environ.get('GITLAB_TOKEN')
    if token:
        headers['PRIVATE-TOKEN'] = token
    return headers


def generic_headers():
    return {'User-Agent': 'Domoticz-Plugin-Scanner', 'Accept': 'application/json'}


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or generic_headers())
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        if e.code == 404:
            return "DELETED"
        print(f"Error fetching {url}: {e}")
    except Exception as e:
        print(f"Error fetching {url}: {e}")
    return None


def _release_timestamp(release):
    for field in ("published_at", "released_at", "created_at"):
        value = release.get(field)
        if not isinstance(value, str) or not value:
            continue
        try:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    return None


def infer_stable_tag_pattern(releases, now=None):
    """Return one allowlisted tag convention from the newest stable release."""
    if not isinstance(releases, list):
        return ""
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime.")
    now = now.astimezone(timezone.utc)
    compiled_patterns = tuple(
        (pattern, re.compile(pattern))
        for pattern in RECOGNIZED_STABLE_TAG_PATTERNS
    )
    candidates = []
    for position, release in enumerate(releases):
        if not isinstance(release, dict):
            continue
        classification_fields = (
            "draft",
            "prerelease",
            "upcoming_release",
        )
        if any(
            flag in release and type(release[flag]) is not bool
            for flag in classification_fields
        ):
            continue
        if any(
            release.get(flag) is True
            for flag in classification_fields
        ):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str):
            continue
        matched_pattern = next(
            (
                pattern
                for pattern, compiled in compiled_patterns
                if compiled.fullmatch(tag)
            ),
            "",
        )
        if not matched_pattern:
            continue
        released_at = _release_timestamp(release)
        if released_at is None or released_at > now:
            continue
        candidates.append((released_at, -position, matched_pattern))
    if not candidates:
        return ""
    return max(candidates, key=lambda candidate: candidate[:2])[2]


def _release_collection_url(repo):
    host = repo.get("host", DEFAULT_GIT_HOST)
    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")
    if not owner or not repo_name:
        return "", {}
    if host == "gitlab.com":
        project = urllib.parse.quote(owner + "/" + repo_name, safe="")
        return (
            "https://gitlab.com/api/v4/projects/"
            + project
            + "/releases?order_by=released_at&sort=desc&per_page=100",
            gitlab_headers(),
        )
    path = "/".join(
        urllib.parse.quote(part, safe="")
        for part in (owner + "/" + repo_name).split("/")
    )
    if host == "codeberg.org":
        return (
            "https://codeberg.org/api/v1/repos/"
            + path
            + "/releases?page=1&limit=50",
            generic_headers(),
        )
    if host == DEFAULT_GIT_HOST:
        return (
            "https://api.github.com/repos/"
            + path
            + "/releases?per_page=100",
            github_headers(),
        )
    return "", {}


def annotate_release_tag_pattern(repo):
    """Attach a finite inferred stable-tag policy to repository metadata."""
    repo[RELEASE_TAG_PATTERN_CHECKED_FIELD] = True
    url, headers = _release_collection_url(repo)
    if not url:
        return repo
    releases = fetch_json(url, headers)
    pattern = infer_stable_tag_pattern(releases)
    if pattern:
        repo[RELEASE_TAG_PATTERN_FIELD] = pattern
    return repo


def release_tag_pattern_update(registry_record, repo):
    """Return an inferred replacement only for the scanner's default policy."""
    if not repo.get(RELEASE_TAG_PATTERN_CHECKED_FIELD):
        return ""
    inferred = repo.get(RELEASE_TAG_PATTERN_FIELD, "")
    release_policy = registry_record.delivery.release
    current = (
        release_policy.get("tag_pattern", "")
        if isinstance(release_policy, dict)
        else getattr(release_policy, "tag_pattern", "")
    )
    if (
        current == DEFAULT_STABLE_TAG_PATTERN
        and inferred
        and inferred != current
    ):
        return inferred
    return ""


def normalize_gitlab_project(project):
    full_name = project.get('path_with_namespace') or project.get('full_name') or ""
    if "/" not in full_name:
        return None
    owner_path, repo_name = full_name.rsplit("/", 1)
    return {
        "host": "gitlab.com",
        "archived": bool(project.get('archived')),
        "disabled": False,
        "empty_repo": bool(project.get('empty_repo', False)),
        "size": project.get('repository_size', project.get('size', 1)),
        "full_name": full_name,
        "owner": {"login": owner_path},
        "name": repo_name,
        "description": project.get('description') or "",
        "default_branch": project.get('default_branch') or "master",
        "pushed_at": project.get('last_activity_at') or project.get('updated_at'),
    }


def normalize_codeberg_repo(repo):
    full_name = repo.get('full_name') or ""
    if "/" not in full_name:
        return None
    owner_path, repo_name = full_name.rsplit("/", 1)
    return {
        "host": "codeberg.org",
        "archived": bool(repo.get('archived')),
        "disabled": False,
        "empty": bool(repo.get('empty')),
        "size": repo.get('size', 1),
        "full_name": full_name,
        "owner": {"login": owner_path},
        "name": repo_name,
        "description": repo.get('description') or "",
        "default_branch": repo.get('default_branch') or "master",
        "pushed_at": repo.get('updated_at') or repo.get('pushed_at'),
    }


def raw_plugin_url_for_repo(repo):
    host = repo.get('host', DEFAULT_GIT_HOST)
    owner = repo.get('owner', {}).get('login', '')
    repo_name = repo.get('name', '')
    branch = repo.get('default_branch') or 'master'
    path = "/".join(urllib.parse.quote(part, safe="") for part in (owner + "/" + repo_name).split("/") if part)
    branch = urllib.parse.quote(branch, safe="")
    if host == "gitlab.com":
        return f"https://gitlab.com/{path}/-/raw/{branch}/plugin.py"
    if host == "codeberg.org":
        return f"https://codeberg.org/{path}/raw/branch/{branch}/plugin.py"
    return f"https://raw.githubusercontent.com/{path}/{branch}/plugin.py"


def raw_plugin_headers_for_repo(repo):
    headers = {'User-Agent': 'Domoticz-Plugin-Scanner', 'Accept': 'text/plain,*/*'}
    host = repo.get('host', DEFAULT_GIT_HOST)
    if host == "gitlab.com":
        token = os.environ.get('GITLAB_TOKEN')
        if token:
            headers['PRIVATE-TOKEN'] = token
    elif host == DEFAULT_GIT_HOST:
        token = os.environ.get('GITHUB_TOKEN')
        if token:
            headers['Authorization'] = f'token {token}'
    return headers


def has_root_plugin_py(repo):
    url = raw_plugin_url_for_repo(repo)
    headers = raw_plugin_headers_for_repo(repo)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            contents = response.read(MAX_PLUGIN_SOURCE_BYTES + 1)
        if len(contents) > MAX_PLUGIN_SOURCE_BYTES:
            return False
        identity = certify_plugin_py(contents)
        repo[ROOT_PLUGIN_IDENTITY_FIELD] = {
            "domoticz_key": identity.domoticz_key,
            "plugin_py_sha256": identity.plugin_py_sha256,
        }
        return True
    except Exception:
        return False


def add_discovered_plugin_repo(all_items, seen_full_names, repo):
    full_name = repo.get('full_name')
    if not full_name or full_name in seen_full_names:
        return False

    seen_full_names.add(full_name)
    if not has_root_plugin_py(repo):
        print(f"[-] Skipping {full_name} (missing root plugin.py)")
        return False

    repo[ROOT_PLUGIN_CHECKED_FIELD] = True
    annotate_release_tag_pattern(repo)
    all_items.append(repo)
    return True


def discovered_repo_has_root_plugin_py(repo):
    return bool(repo.get(ROOT_PLUGIN_CHECKED_FIELD)) or has_root_plugin_py(repo)


def discovered_repo_identity(repo):
    identity = repo.get(ROOT_PLUGIN_IDENTITY_FIELD)
    if not isinstance(identity, dict):
        return None
    domoticz_key = identity.get("domoticz_key")
    plugin_py_sha256 = identity.get("plugin_py_sha256")
    if not isinstance(domoticz_key, str) or not domoticz_key:
        return None
    if not isinstance(plugin_py_sha256, str) or len(plugin_py_sha256) != 64:
        return None
    return identity


def get_github_repo_info(owner, repo):
    url = f'https://api.github.com/repos/{owner}/{repo}'
    data = fetch_json(url, github_headers())
    if isinstance(data, dict):
        annotate_release_tag_pattern(data)
    return data


def get_gitlab_repo_info(owner_path, repo):
    project_path = urllib.parse.quote(owner_path + "/" + repo, safe="")
    data = fetch_json(f'https://gitlab.com/api/v4/projects/{project_path}', gitlab_headers())
    if data == "DELETED" or data is None:
        return data
    normalized = normalize_gitlab_project(data)
    annotate_release_tag_pattern(normalized)
    return normalized


def get_codeberg_repo_info(owner_path, repo):
    path = "/".join(urllib.parse.quote(part, safe="") for part in (owner_path + "/" + repo).split("/"))
    data = fetch_json(f'https://codeberg.org/api/v1/repos/{path}', generic_headers())
    if data == "DELETED" or data is None:
        return data
    normalized = normalize_codeberg_repo(data)
    annotate_release_tag_pattern(normalized)
    return normalized


def get_repo_info(owner, repo):
    host, owner_path = split_registry_owner(owner)
    if host == "gitlab.com":
        return get_gitlab_repo_info(owner_path, repo)
    if host == "codeberg.org":
        return get_codeberg_repo_info(owner_path, repo)
    if host == DEFAULT_GIT_HOST:
        return get_github_repo_info(owner_path, repo)
    print(f"Skipping metadata refresh for unsupported scanner host {host}")
    return None

def search_github():
    # Multiple queries to be more comprehensive
    queries = [
        'domoticz plugin',
        'domoticz integration',
        'domoticz python',
        'topic:domoticz-plugin'
    ]

    all_items = []
    seen_full_names = set()

    headers = github_headers()

    for query in queries:
        print(f"Searching for: {query}")
        encoded_query = urllib.parse.quote(query)
        url = f'https://api.github.com/search/repositories?q={encoded_query}&sort=updated&order=desc&per_page=100'

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                items = data.get('items', [])
                for item in items:
                    add_discovered_plugin_repo(all_items, seen_full_names, item)
        except HTTPError as e:
            print(f"Error searching GitHub for '{query}': {e}")

    return all_items


def search_gitlab():
    queries = [
        'domoticz plugin',
        'domoticz python plugin',
    ]
    all_items = []
    seen_full_names = set()

    for query in queries:
        print(f"Searching GitLab for: {query}")
        encoded_query = urllib.parse.quote(query)
        url = (
            'https://gitlab.com/api/v4/projects?'
            f'search={encoded_query}&simple=true&per_page=100&order_by=last_activity_at&sort=desc'
        )
        data = fetch_json(url, gitlab_headers())
        if not isinstance(data, list):
            continue
        for item in data:
            repo = normalize_gitlab_project(item)
            if repo:
                add_discovered_plugin_repo(all_items, seen_full_names, repo)

    return all_items


def search_codeberg():
    queries = [
        'domoticz',
        'domoticz plugin',
    ]
    all_items = []
    seen_full_names = set()

    for query in queries:
        print(f"Searching Codeberg for: {query}")
        encoded_query = urllib.parse.quote(query)
        url = f'https://codeberg.org/api/v1/repos/search?q={encoded_query}&limit=50'
        data = fetch_json(url, generic_headers())
        items = data.get('data', []) if isinstance(data, dict) else []
        for item in items:
            repo = normalize_codeberg_repo(item)
            if repo:
                add_discovered_plugin_repo(all_items, seen_full_names, repo)

    return all_items


def search_repositories():
    return search_github() + search_gitlab() + search_codeberg()

def main():
    if not os.path.exists(REGISTRY_FILE):
        print(f"Registry file not found at {REGISTRY_FILE}")
        return

    registry = load_registry_file(REGISTRY_FILE)
    update_times = load_update_times_file(
        UPDATE_TIMES_FILE,
        missing_ok=True,
    )

    platform_metadata_exists = os.path.exists(PLATFORM_METADATA_FILE)
    platform_metadata = ensure_platform_metadata_for_registry(
        load_platform_metadata(PLATFORM_METADATA_FILE),
        registry,
        manual_changes_are_reviewed=platform_metadata_exists,
    )

    stats = {"updated": 0, "removed": 0, "added": 0, "metadata_updated": 0, "update_times_pruned": 0}

    # 1. Sync Existing Plugins
    print("Syncing existing plugins...")
    for key in list(registry.keys()):
        if key == "Idle": continue

        data = registry[key]
        registry_record = RegistryRecord.from_entry(key, data)
        owner = registry_record.owner
        repo_name = registry_record.repository

        block_reason = get_repo_block_reason(owner, repo_name)
        if block_reason:
            remove_registry_entry(registry, update_times, platform_metadata, key, block_reason)
            stats["removed"] += 1
            continue

        # Determine if we need to fetch info (for existing plugins, we check 1 in 4 to stay under rate limits if no token)
        # In GitHub Actions, GITHUB_TOKEN is present, so we can check all.
        info = get_repo_info(owner, repo_name)

        if info == "DELETED":
            remove_registry_entry(registry, update_times, platform_metadata, key, "Repo deleted")
            stats["removed"] += 1
        elif info:
            skip_reason = get_repo_skip_reason(info)
            if skip_reason:
                remove_registry_entry(registry, update_times, platform_metadata, key, skip_reason)
                stats["removed"] += 1
            else:
                # Update metadata. Registry branches are curated and must not
                # follow repository default-branch changes automatically.
                updated_desc = info.get('description') or registry_record.description
                registry_branch = registry_record.branch
                updated_at = info.get('pushed_at') or info.get('updated_at')
                if updated_at:
                    updated_at = normalize_update_timestamp(updated_at)
                current_platforms = get_registry_entry_platforms(data)
                platform_decision = detect_platforms_for_repo(owner, repo_name, registry_branch, info)
                detected_platforms = decision_platforms(platform_decision)
                metadata_entry = platform_metadata["entries"].get(key, {})
                if metadata_entry.get("identity") != platform_metadata_identity(owner, repo_name, registry_branch):
                    metadata_entry = {}
                next_platforms, platform_policy = choose_platforms_for_registry(
                    current_platforms,
                    platform_decision,
                    metadata_entry=metadata_entry,
                    is_new=False,
                )
                release_policy = registry_record.delivery.release
                current_tag_pattern = (
                    release_policy.get("tag_pattern", "")
                    if isinstance(release_policy, dict)
                    else getattr(release_policy, "tag_pattern", "")
                )
                inferred_tag_pattern = release_tag_pattern_update(
                    registry_record,
                    info,
                )
                release_pattern_changed = bool(inferred_tag_pattern)

                # Check if changed
                if (updated_desc != registry_record.description or
                    update_times.get(key) != updated_at or
                    next_platforms != current_platforms or
                    release_pattern_changed):

                    print(f"[*] Updating {key}")
                    if release_pattern_changed:
                        print(
                            "    stable release tag policy "
                            + current_tag_pattern
                            + " -> "
                            + inferred_tag_pattern
                        )
                    if detected_platforms and next_platforms == current_platforms:
                        print(
                            f"    keeping platforms {current_platforms or ['unknown']}; "
                            f"detected {detected_platforms} "
                            f"({decision_confidence(platform_decision)}, {platform_policy})"
                        )
                    elif next_platforms != current_platforms:
                        print(
                            f"    platforms {current_platforms or ['unknown']} -> {next_platforms} "
                            f"({decision_confidence(platform_decision)}, {platform_policy})"
                        )
                    updated_record = registry_record.with_description(updated_desc)
                    if next_platforms:
                        updated_record = updated_record.with_platforms(
                            next_platforms
                        )
                    if release_pattern_changed:
                        updated_record = (
                            updated_record.with_release_tag_pattern(
                                inferred_tag_pattern
                            )
                        )
                    registry[key] = updated_record.to_document()
                    if updated_at:
                        update_times[key] = updated_at
                    stats["updated"] += 1

                if platform_decision is not None and platform_policy != "unchanged":
                    before = json.dumps(platform_metadata["entries"].get(key, {}), sort_keys=True)
                    platform_metadata = update_platform_metadata_entry(
                        platform_metadata,
                        key,
                        owner,
                        repo_name,
                        registry_branch,
                        next_platforms,
                        decision=platform_decision,
                        policy_action=platform_policy,
                    )
                    after = json.dumps(platform_metadata["entries"].get(key, {}), sort_keys=True)
                    if after != before:
                        stats["metadata_updated"] += 1

        # Throttle to respect rate limits
        if not os.environ.get('GITHUB_TOKEN'):
            time.sleep(1)

    # 2. Discover New Plugins
    print("Searching for new plugins...")
    new_items = search_repositories()
    existing_full_names = {
        RegistryRecord.from_entry(key, value).repository_identity
        for key, value in registry.items()
        if key != "Idle"
    }

    for repo in new_items:
        repo_host = repo.get('host', DEFAULT_GIT_HOST)
        owner = repo['owner']['login']
        repo_name = repo['name']
        registry_owner = get_registry_owner(repo_host, owner)
        full_name = get_repository_identity(registry_owner, repo_name)
        if full_name not in existing_full_names:
            block_reason = get_repo_block_reason(owner, repo_name)
            if block_reason:
                print(f"[-] Skipping {repo['full_name']} ({block_reason})")
                continue

            skip_reason = get_repo_skip_reason(repo)
            if skip_reason:
                print(f"[-] Skipping {repo['full_name']} ({skip_reason})")
                continue

            if not is_valid_plugin_repo(repo_name):
                print(f"[-] Skipping {repo['full_name']} (Invalid plugin repository name)")
                continue

            if not discovered_repo_has_root_plugin_py(repo):
                print(f"[-] Skipping {repo['full_name']} (missing root plugin.py)")
                continue
            certified_identity = discovered_repo_identity(repo)
            if certified_identity is None:
                print(
                    f"[-] Skipping {repo['full_name']} "
                    "(uncertified Domoticz plugin identity)"
                )
                continue

            description = repo['description'] or f"{repo_name} plugin for Domoticz"
            default_branch = repo['default_branch']
            pushed_at = repo.get('pushed_at') or repo.get('updated_at')
            platform_decision = detect_platforms_for_repo(registry_owner, repo_name, default_branch, repo)
            platforms, platform_policy = choose_platforms_for_registry(
                [],
                platform_decision,
                metadata_entry=None,
                is_new=True,
            )

            existing_package_ids = {
                package_id.casefold() for package_id in registry
            }
            key = repo_name
            if key.casefold() in existing_package_ids:
                key = f"{owner}-{repo_name}"
            if key.casefold() in existing_package_ids:
                print(
                    f"[-] Skipping {repo['full_name']} "
                    "(package_id collision)"
                )
                continue

            print(f"[+] Adding {key}")
            detected_platforms = decision_platforms(platform_decision)
            if detected_platforms and not platforms:
                print(
                    f"    leaving platforms unknown; detected {detected_platforms} "
                    f"({decision_confidence(platform_decision)}, {platform_policy})"
                )
            elif platforms:
                print(f"    platforms {platforms} ({decision_confidence(platform_decision)}, {platform_policy})")
            registry[key] = build_registry_entry(
                key,
                certified_identity["domoticz_key"],
                registry_owner,
                repo_name,
                description,
                default_branch,
                platforms,
                (
                    repo.get(RELEASE_TAG_PATTERN_FIELD, "")
                    if repo.get(RELEASE_TAG_PATTERN_CHECKED_FIELD)
                    else ""
                ),
            )
            if pushed_at:
                update_times[key] = normalize_update_timestamp(pushed_at)
            before = json.dumps(platform_metadata["entries"].get(key, {}), sort_keys=True)
            platform_metadata = update_platform_metadata_entry(
                platform_metadata,
                key,
                registry_owner,
                repo_name,
                default_branch,
                platforms,
                decision=platform_decision,
                policy_action=platform_policy,
            )
            after = json.dumps(platform_metadata["entries"].get(key, {}), sort_keys=True)
            if after != before:
                stats["metadata_updated"] += 1
            stats["added"] += 1

    stale_update_time_keys = prune_stale_update_times(update_times, registry)
    stats["update_times_pruned"] = len(stale_update_time_keys)

    # 3. Save Results
    if any(stats.values()):
        platform_metadata = ensure_platform_metadata_for_registry(platform_metadata, registry)
        save_registry_file(REGISTRY_FILE, registry)
        save_update_times_file(UPDATE_TIMES_FILE, update_times)
        save_platform_metadata(platform_metadata, PLATFORM_METADATA_FILE)
        print(
            "Registry updated: "
            f"{stats['added']} added, {stats['updated']} updated, "
            f"{stats['removed']} removed, {stats['metadata_updated']} metadata updated, "
            f"{stats['update_times_pruned']} stale update times pruned."
        )
    else:
        print("No changes needed.")

if __name__ == '__main__':
    main()
