import json
import os
import urllib.request
import urllib.parse
from urllib.error import HTTPError
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_FILE = os.path.join(SCRIPT_DIR, '../../registry.json')
UPDATE_TIMES_FILE = os.path.join(SCRIPT_DIR, '../../update_times.json')

# Repositories that should never be added to or kept in the registry.
REPO_BLOCKLIST = {
    "ycahome/pp-manager",
    "adrighem/pp-manager",
    "adrighem/pypluginstore",
    "domoticz/domoticz",
}

def is_valid_plugin_repo(repo_name):
    return bool(repo_name) and not repo_name.startswith('.') and '/' not in repo_name and '\\' not in repo_name

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

    size = repo.get('size')
    if size is not None:
        try:
            if int(size) <= 0:
                return "Repo empty"
        except (TypeError, ValueError):
            pass

    return None

def remove_registry_entry(registry, update_times, key, reason):
    print(f"[-] Removing {key} ({reason})")
    del registry[key]
    if key in update_times:
        del update_times[key]

def get_repo_info(owner, repo):
    url = f'https://api.github.com/repos/{owner}/{repo}'
    headers = {'User-Agent': 'Domoticz-Plugin-Scanner', 'Accept': 'application/vnd.github.v3+json'}

    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'token {token}'

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        if e.code == 404:
            return "DELETED"
        print(f"Error fetching {owner}/{repo}: {e}")
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

    headers = {'User-Agent': 'Domoticz-Plugin-Scanner', 'Accept': 'application/vnd.github.v3+json'}
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'token {token}'

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
                    if item['full_name'] not in seen_full_names:
                        all_items.append(item)
                        seen_full_names.add(item['full_name'])
        except HTTPError as e:
            print(f"Error searching GitHub for '{query}': {e}")

    return all_items

def main():
    if not os.path.exists(REGISTRY_FILE):
        print(f"Registry file not found at {REGISTRY_FILE}")
        return

    with open(REGISTRY_FILE, 'r') as f:
        registry = json.load(f)
        
    update_times = {}
    if os.path.exists(UPDATE_TIMES_FILE):
        with open(UPDATE_TIMES_FILE, 'r') as f:
            update_times = json.load(f)

    stats = {"updated": 0, "removed": 0, "added": 0}

    # 1. Sync Existing Plugins
    print("Syncing existing plugins...")
    for key in list(registry.keys()):
        if key == "Idle": continue

        data = registry[key]
        owner, repo_name = data[0], data[1]

        block_reason = get_repo_block_reason(owner, repo_name)
        if block_reason:
            remove_registry_entry(registry, update_times, key, block_reason)
            stats["removed"] += 1
            continue

        # Determine if we need to fetch info (for existing plugins, we check 1 in 4 to stay under rate limits if no token)
        # In GitHub Actions, GITHUB_TOKEN is present, so we can check all.
        info = get_repo_info(owner, repo_name)

        if info == "DELETED":
            remove_registry_entry(registry, update_times, key, "Repo deleted")
            stats["removed"] += 1
        elif info:
            skip_reason = get_repo_skip_reason(info)
            if skip_reason:
                remove_registry_entry(registry, update_times, key, skip_reason)
                stats["removed"] += 1
            else:
                # Update metadata
                updated_desc = info.get('description') or data[2]
                updated_branch = info.get('default_branch') or data[3]
                updated_at = info.get('pushed_at') or info.get('updated_at')

                # Check if changed
                if (updated_desc != data[2] or
                    updated_branch != data[3] or
                    update_times.get(key) != updated_at):

                    print(f"[*] Updating {key}")
                    registry[key] = [
                        owner,
                        repo_name,
                        updated_desc,
                        updated_branch
                    ]
                    if updated_at:
                        update_times[key] = updated_at
                    stats["updated"] += 1

        # Throttle to respect rate limits
        if not os.environ.get('GITHUB_TOKEN'):
            time.sleep(1)

    # 2. Discover New Plugins
    print("Searching for new plugins...")
    new_items = search_github()
    existing_full_names = {f"{v[0].lower()}/{v[1].lower()}" for k, v in registry.items() if k != "Idle"}

    for repo in new_items:
        full_name = repo['full_name'].lower()
        if full_name not in existing_full_names:
            owner = repo['owner']['login']
            repo_name = repo['name']

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

            description = repo['description'] or f"{repo_name} plugin for Domoticz"
            default_branch = repo['default_branch']
            pushed_at = repo.get('pushed_at') or repo.get('updated_at')

            key = repo_name
            if key in registry:
                key = f"{owner}-{repo_name}"

            print(f"[+] Adding {key}")
            registry[key] = [
                owner,
                repo_name,
                description,
                default_branch
            ]
            if pushed_at:
                update_times[key] = pushed_at
            stats["added"] += 1

    # 3. Save Results
    if stats["updated"] > 0 or stats["removed"] > 0 or stats["added"] > 0:
        with open(REGISTRY_FILE, 'w') as f:
            json.dump(registry, f, indent=4)
            f.write('\n')
        with open(UPDATE_TIMES_FILE, 'w') as f:
            json.dump(update_times, f, indent=4)
            f.write('\n')
        print(f"Registry updated: {stats['added']} added, {stats['updated']} updated, {stats['removed']} removed.")
    else:
        print("No changes needed.")

if __name__ == '__main__':
    main()
