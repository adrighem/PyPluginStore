#!/usr/bin/env python3
import os
import sys
import json
import subprocess

# Adjust path relative to the current script location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

REGISTRY_FILE_PATH = os.path.join(SCRIPT_DIR, '../../registry.json')
RELEASE_INDEX_FILE_PATH = os.path.join(SCRIPT_DIR, '../../release_index.json')

try:
    from registry_records import load_registry_file, registry_mapping_from_bytes
except ImportError:
    # Fallback to simple json loading if registry_records cannot be imported
    def load_registry_file(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {pkg["package_id"]: pkg for pkg in data.get("packages", [])}

    def registry_mapping_from_bytes(contents):
        data = json.loads(contents.decode("utf-8"))
        return {pkg["package_id"]: pkg for pkg in data.get("packages", [])}

def load_git_file(filepath):
    try:
        content = subprocess.check_output(
            ["git", "show", f"HEAD:{filepath}"],
            stderr=subprocess.DEVNULL
        )
        return content
    except subprocess.CalledProcessError:
        return None

def compare_packages(orig_pkg, local_pkg):
    changes = []

    # Simple top-level fields
    for field in ["domoticz_key", "description"]:
        orig_val = orig_pkg.get(field)
        local_val = local_pkg.get(field)
        if orig_val != local_val:
            changes.append(f"**{field}** changed: `{orig_val}` -> `{local_val}`")

    # repository branch and url
    orig_repo = orig_pkg.get("repository", {})
    local_repo = local_pkg.get("repository", {})
    if orig_repo.get("url") != local_repo.get("url"):
        changes.append(f"**repository URL** changed: `{orig_repo.get('url')}` -> `{local_repo.get('url')}`")
    if orig_repo.get("branch") != local_repo.get("branch"):
        changes.append(f"**repository branch** changed: `{orig_repo.get('branch')}` -> `{local_repo.get('branch')}`")

    # platforms
    orig_plats = orig_pkg.get("platforms", [])
    local_plats = local_pkg.get("platforms", [])
    if orig_plats != local_plats:
        changes.append(f"**platforms** changed: `{orig_plats}` -> `{local_plats}`")

    # delivery
    orig_delivery = orig_pkg.get("delivery", {})
    local_delivery = local_pkg.get("delivery", {})
    if orig_delivery != local_delivery:
        changes.append("**delivery policy** updated")

    return changes

def main():
    # 1. Load Local files
    if os.path.isfile(REGISTRY_FILE_PATH):
        local_registry = load_registry_file(REGISTRY_FILE_PATH)
    else:
        local_registry = {}

    if os.path.isfile(RELEASE_INDEX_FILE_PATH):
        with open(RELEASE_INDEX_FILE_PATH, "r", encoding="utf-8") as f:
            local_index = json.load(f)
    else:
        local_index = {}

    # 2. Load Git HEAD files
    orig_bytes = load_git_file("registry.json")
    if orig_bytes:
        try:
            orig_registry = registry_mapping_from_bytes(orig_bytes)
        except Exception:
            orig_registry = {}
    else:
        orig_registry = {}

    orig_index_bytes = load_git_file("release_index.json")
    if orig_index_bytes:
        try:
            orig_index = json.loads(orig_index_bytes.decode("utf-8"))
        except Exception:
            orig_index = {}
    else:
        orig_index = {}

    # 3. Compare registry.json
    added_packages = []
    removed_packages = []
    updated_packages = []

    for pkg_id in sorted(local_registry.keys()):
        local_pkg = local_registry[pkg_id]
        if pkg_id not in orig_registry:
            repo_url = local_pkg.get("repository", {}).get("url", "")
            desc = local_pkg.get("description", "")
            added_packages.append((pkg_id, repo_url, desc))
        else:
            orig_pkg = orig_registry[pkg_id]
            pkg_changes = compare_packages(orig_pkg, local_pkg)
            if pkg_changes:
                repo_url = local_pkg.get("repository", {}).get("url", "")
                updated_packages.append((pkg_id, repo_url, pkg_changes))

    for pkg_id in sorted(orig_registry.keys()):
        if pkg_id not in local_registry:
            orig_pkg = orig_registry[pkg_id]
            repo_url = orig_pkg.get("repository", {}).get("url", "")
            removed_packages.append((pkg_id, repo_url))

    # 4. Compare release_index.json
    orig_releases_map = {r["release_id"]: r for r in orig_index.get("releases", [])} if orig_index else {}
    local_releases_map = {r["release_id"]: r for r in local_index.get("releases", [])}

    added_releases = []
    for rel_id, rel in local_releases_map.items():
        if rel_id not in orig_releases_map:
            added_releases.append(rel)

    orig_tombstones_map = {t["release_id"]: t for t in orig_index.get("tombstones", [])} if orig_index else {}
    local_tombstones_map = {t["release_id"]: t for t in local_index.get("tombstones", [])}

    added_tombstones = []
    for rel_id, tomb in local_tombstones_map.items():
        if rel_id not in orig_tombstones_map:
            added_tombstones.append(tomb)

    # 5. Generate Markdown Output
    print("This automated PR updates the Domoticz Python plugin registry with newly discovered plugins, refreshed repository metadata, and inferred platform badges from supported Git forges.")
    print()
    print("## Data Changes")
    print()

    # Summary list
    print("### Summary")
    print(f"- **Release index sequence**: `{orig_index.get('sequence', 'N/A')}` -> `{local_index.get('sequence', 'N/A')}`")
    print(f"- **Active releases**: `{len(orig_releases_map)}` -> `{len(local_releases_map)}`")
    print(f"- **Tombstones**: `{len(orig_tombstones_map)}` -> `{len(local_tombstones_map)}`")
    print(f"- **Packages**: `{len(orig_registry)}` -> `{len(local_registry)}`")
    print()

    # Added Packages
    if added_packages:
        print("### New Packages Added")
        for pkg_id, repo_url, desc in added_packages:
            print(f"- **`{pkg_id}`** ({repo_url}): {desc}")
        print()

    # Removed Packages
    if removed_packages:
        print("### Packages Removed")
        for pkg_id, repo_url in removed_packages:
            print(f"- **`{pkg_id}`** ({repo_url})")
        print()

    # Updated Packages
    if updated_packages:
        print("### Packages Updated")
        for pkg_id, repo_url, changes in updated_packages:
            print(f"- **`{pkg_id}`** ({repo_url}):")
            for change in changes:
                print(f"  - {change}")
        print()

    # Added Releases
    if added_releases:
        print("### New Active Releases")
        for rel in sorted(added_releases, key=lambda x: (x.get("package_id", ""), x.get("version", ""))):
            pkg_id = rel.get("package_id")
            version = rel.get("version")
            tag = rel.get("tag")
            print(f"- **`{pkg_id}`**: version `{version}` (tag `{tag}`)")
        print()

    # Added Tombstones
    if added_tombstones:
        print("### New Tombstones")
        for tomb in sorted(added_tombstones, key=lambda x: (x.get("package_id", ""), x.get("release_id", ""))):
            pkg_id = tomb.get("package_id")
            rel_id = tomb.get("release_id")
            reason = tomb.get("reason")
            print(f"- **`{pkg_id}`**: `{rel_id}` - Reason: {reason}")
        print()

if __name__ == "__main__":
    main()
