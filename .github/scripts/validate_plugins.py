import os
import sys
import json
import subprocess

# Adjust path relative to the current script location
SCRIPT_DIR = os.path.dirname(__file__)
REGISTRY_FILE_PATH = os.path.join(SCRIPT_DIR, '../../registry.json')

def load_registry():
    print(f"Checking if registry file exists at: {REGISTRY_FILE_PATH}")
    if not os.path.isfile(REGISTRY_FILE_PATH):
        print(f"Registry file not found at: {REGISTRY_FILE_PATH}")
        sys.exit(1)

    with open(REGISTRY_FILE_PATH, 'r') as f:
        registry_data = json.load(f)
        
    plugin_data = {}
    for key, data in registry_data.items():
        if key == "Idle":
            continue
        validate_registry_entry(key, data)
        plugin_data[key] = {
            "key": key,
            "author": data[0],
            "repository": data[1],
            "description": data[2],
            "branch": data[3]
        }
    return plugin_data

def validate_registry_entry(key, data):
    if not isinstance(data, list) or len(data) < 4:
        raise ValueError(f"Plugin '{key}' must be a list with owner, repository, description and branch.")

    if not key or key.startswith(".") or "/" in key or "\\" in key:
        raise ValueError(f"Plugin key '{key}' is invalid. Keys must be visible folder names without path separators.")

    for index, field_name in enumerate(("author", "repository", "description", "branch")):
        if not isinstance(data[index], str) or not data[index].strip():
            raise ValueError(f"Plugin '{key}' has an invalid {field_name}.")

    repository = data[1]
    if repository.startswith(".") or "/" in repository or "\\" in repository:
        raise ValueError(f"Plugin '{key}' has an invalid repository name '{repository}'.")

def validate_repository(author, repository, branch):
    repo_url = f"https://github.com/{author}/{repository}"
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    repo_clone_cmd = ["git", "ls-remote", "--heads", repo_url, branch]
    result = subprocess.run(repo_clone_cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error executing command: {' '.join(repo_clone_cmd)}")
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
    return result.returncode == 0 and bool(result.stdout.strip())

def main():
    print("Loading registry file...")
    plugin_data = load_registry()
    print(f"Loaded {len(plugin_data)} plugins.")

    if not plugin_data:
        print("No plugin data found, exiting.")
        sys.exit(1)

    all_valid = True
    for key, data in plugin_data.items():
        print(f"Validating repository for plugin: {key}")
        is_valid = validate_repository(data["author"], data["repository"], data["branch"])
        if is_valid:
            print(f"✅ Repository {data['author']}/{data['repository']} on branch {data['branch']} is valid.")
        else:
            print(f"❌ Repository {data['author']}/{data['repository']} on branch {data['branch']} is invalid.")
            all_valid = False

    if not all_valid:
        print("One or more plugins are invalid.")
        sys.exit(1)  # Exit with a non-zero code to indicate failure

if __name__ == "__main__":
    main()
