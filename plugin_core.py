

import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from datetime import datetime

import Domoticz



class BasePlugin:
    enabled = False
    pluginState = "Not Ready"
    sessionCookie = ""
    privateKey = b""
    socketOn = "FALSE"

    def __init__(self):
        self.debug = False
        self.error = False
        self.nextpoll = None
        self.pollinterval = 60
        self.exception_list = []
        self.secpoluser_list = {}
        self.plugin_data = {}
        self.local_plugin_keys = []
        self.update_times = {}
        self.update_status = {}
        self.last_update_date = None
        self.last_update_status_refresh_date = None

    def get_current_plugin_folder(self):
        return os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/')))

    def get_git_env(self):
        env = os.environ.copy()
        env['LANG'] = 'en_US.UTF-8'
        env['LC_ALL'] = 'en_US.UTF-8'
        return env

    def get_plugin_home_folder(self):
        return os.path.abspath(Parameters.get("HomeFolder", str(os.getcwd()) + "/"))

    def get_bundled_update_times_file(self):
        return os.path.join(self.get_plugin_home_folder(), "update_times.json")

    def get_update_times_cache_file(self):
        return os.path.join(self.get_plugin_home_folder(), "update_times.cache.json")

    def get_update_times_url(self):
        return "https://raw.githubusercontent.com/adrighem/PyPluginStore/refs/heads/master/update_times.json"

    def get_registry_url(self):
        return "https://raw.githubusercontent.com/adrighem/PyPluginStore/refs/heads/master/registry.json"

    def get_bundled_registry_file(self):
        return os.path.join(self.get_plugin_home_folder(), "registry.json")

    def get_local_registry_file(self):
        return os.path.join(self.get_plugin_home_folder(), "registry_local.json")

    def build_git_clone_url(self, author, repository):
        author = str(author or "").strip()
        repository = str(repository or "").strip()

        if author.startswith("git@") or author.startswith("ssh://") or author.startswith("file://"):
            return author.rstrip("/")

        if author.startswith("http://") or author.startswith("https://"):
            clone_url = author.rstrip("/")
            parsed_url = urllib.parse.urlparse(clone_url)
            if parsed_url.scheme in ("http", "https") and parsed_url.hostname == "github.com":
                path_parts = [part for part in parsed_url.path.split("/") if part]
                if len(path_parts) >= 2:
                    clone_url = parsed_url.scheme + "://github.com/" + path_parts[0] + "/" + path_parts[1]
                    if not clone_url.endswith(".git"):
                        clone_url += ".git"
            return clone_url

        shorthand_url = urllib.parse.urlparse("//" + author)
        if shorthand_url.hostname == "github.com":
            path_parts = [part for part in shorthand_url.path.split("/") if part]
            if len(path_parts) >= 2:
                clone_url = "https://github.com/" + path_parts[0] + "/" + path_parts[1]
                if not clone_url.endswith(".git"):
                    clone_url += ".git"
                return clone_url

        return f"https://github.com/{author}/{repository}.git"

    def load_registry_file(self, registry_file, label, missing_is_error=False):
        if not os.path.isfile(registry_file):
            if missing_is_error:
                Domoticz.Error("No " + label + " registry found.")
            else:
                Domoticz.Debug("No " + label + " registry file found.")
            return None

        try:
            with open(registry_file, "r", encoding="utf-8") as f:
                registry = json.load(f)
            if isinstance(registry, dict):
                Domoticz.Log("Loaded " + label + " plugin registry.")
                return registry
            Domoticz.Error(label + " registry file does not contain a JSON object.")
        except Exception as e:
            Domoticz.Error("Error reading " + label + " registry file: " + str(e))
        return None

    def fetch_remote_registry(self):
        Domoticz.Debug("Fetching plugin registry from GitHub.")
        try:
            req = urllib.request.Request(self.get_registry_url())
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    registry = json.loads(response.read().decode("utf-8"))
                    if isinstance(registry, dict):
                        Domoticz.Log("Successfully fetched plugin registry from GitHub.")
                        return registry
                    Domoticz.Error("Remote registry.json does not contain a JSON object.")
                else:
                    Domoticz.Error("Failed to fetch registry, status code: " + str(response.status))
        except Exception as e:
            Domoticz.Error("Error fetching registry: " + str(e))
        return None

    def load_bundled_registry(self):
        return self.load_registry_file(self.get_bundled_registry_file(), "bundled", True)

    def load_local_registry(self):
        return self.load_registry_file(self.get_local_registry_file(), "local")

    def load_update_times_file(self, update_times_file, label):
        if not os.path.isfile(update_times_file):
            Domoticz.Debug("No " + label + " update_times file found.")
            return {}

        try:
            with open(update_times_file, "r", encoding="utf-8") as f:
                update_times = json.load(f)
            if isinstance(update_times, dict):
                Domoticz.Debug("Loaded update times from " + label + " file.")
                return update_times
            Domoticz.Error(label + " update_times file does not contain a JSON object.")
        except Exception as e:
            Domoticz.Error("Error reading " + label + " update_times file: " + str(e))
        return {}

    def load_bundled_update_times(self):
        return self.load_update_times_file(self.get_bundled_update_times_file(), "bundled")

    def load_cached_update_times(self):
        return self.load_update_times_file(self.get_update_times_cache_file(), "cached")

    def save_update_times_cache(self, update_times):
        update_times_file = self.get_update_times_cache_file()
        try:
            with open(update_times_file, "w", encoding="utf-8") as f:
                json.dump(update_times, f, indent=4)
                f.write("\n")
            Domoticz.Log("Updated update_times.cache.json.")
            return True
        except Exception as e:
            Domoticz.Error("Error writing update_times.cache.json: " + str(e))
            return False

    def load_update_times(self):
        update_times = {}
        Domoticz.Debug("Fetching update times from GitHub.")
        try:
            req = urllib.request.Request(self.get_update_times_url())
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    update_times = json.loads(response.read().decode("utf-8"))
                    if isinstance(update_times, dict):
                        Domoticz.Log("Successfully fetched update times from GitHub.")
                    else:
                        Domoticz.Error("Remote update_times.json does not contain a JSON object.")
                        update_times = {}
                else:
                    Domoticz.Error("Failed to fetch update times, status code: " + str(response.status))
        except Exception as e:
            Domoticz.Error("Error fetching update times: " + str(e))

        if not update_times:
            update_times = self.load_bundled_update_times()

        cached_update_times = self.load_cached_update_times()
        for key, cached_updated_at in cached_update_times.items():
            if self.is_better_update_time(cached_updated_at, update_times.get(key, "")):
                update_times[key] = cached_updated_at

        if update_times:
            self.save_update_times_cache(update_times)
        return update_times

    def apply_update_times(self, update_times):
        self.update_times = update_times
        for key, data in self.plugin_data.items():
            if key == "Idle":
                continue
            if not isinstance(data, list):
                continue

            updated_at = update_times.get(key, "")
            if len(data) == 4:
                data.append(updated_at)
            elif len(data) >= 5:
                data[4] = updated_at

    def parse_update_time(self, updated_at):
        if not updated_at:
            return None
        try:
            return datetime.strptime(str(updated_at), "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None

    def is_better_update_time(self, candidate, current):
        if not candidate:
            return False
        if not current:
            return True

        candidate_time = self.parse_update_time(candidate)
        current_time = self.parse_update_time(current)
        if candidate_time and current_time:
            return candidate_time > current_time
        if candidate_time and not current_time:
            return True
        return False

    def overlay_git_update_time(self, plugin_key, updated_at, update_times=None, remote_url="", remote_ref=""):
        if plugin_key not in self.plugin_data:
            return False

        if update_times is None:
            update_times = self.update_times

        current_updated_at = update_times.get(plugin_key, "")
        if not self.is_better_update_time(updated_at, current_updated_at):
            return False

        update_times[plugin_key] = updated_at

        data = self.plugin_data.get(plugin_key)
        if isinstance(data, list):
            if len(data) == 4:
                data.append(updated_at)
            elif len(data) >= 5:
                data[4] = updated_at

        Domoticz.Log("Git update date changed for " + plugin_key + ": " + updated_at)
        if remote_url:
            Domoticz.Debug("Update date source for " + plugin_key + ": " + remote_url + " " + remote_ref)
        return True

    def run_git_command(self, plugin_dir, command, timeout=15):
        try:
            return subprocess.run(
                command,
                cwd=plugin_dir,
                env=self.get_git_env(),
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except subprocess.TimeoutExpired:
            Domoticz.Error("Git command timed out in " + plugin_dir + ": " + " ".join(command))
        except OSError as e:
            Domoticz.Error("Git ErrorNo:" + str(e.errno))
            Domoticz.Error("Git StrError:" + str(e.strerror))
        except Exception as e:
            Domoticz.Error("Git command failed in " + plugin_dir + ": " + str(e))
        return None

    def fetch_git_repo(self, plugin_dir):
        result = self.run_git_command(plugin_dir, ["git", "fetch", "--quiet"], timeout=15)
        if result is None:
            return False
        if result.stdout:
            Domoticz.Debug("Git Fetch Response:" + result.stdout.strip())
        if result.stderr:
            Domoticz.Debug("Git Fetch Error:" + result.stderr.strip())
        return result.returncode == 0

    def get_git_current_branch(self, plugin_dir):
        result = self.run_git_command(plugin_dir, ["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
        if result is not None and result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
        return ""

    def get_git_remote_name(self, plugin_dir, branch=""):
        if branch:
            result = self.run_git_command(plugin_dir, ["git", "config", "--get", "branch." + branch + ".remote"], timeout=10)
            if result is not None and result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()

        result = self.run_git_command(plugin_dir, ["git", "remote"], timeout=10)
        if result is not None and result.returncode == 0:
            remotes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if "origin" in remotes:
                return "origin"
            if remotes:
                return remotes[0]
        return "origin"

    def get_git_remote_url(self, plugin_dir, remote_name):
        result = self.run_git_command(plugin_dir, ["git", "remote", "get-url", remote_name], timeout=10)
        if result is not None and result.returncode == 0:
            return result.stdout.strip()
        return ""

    def git_ref_exists(self, plugin_dir, ref_name):
        result = self.run_git_command(plugin_dir, ["git", "rev-parse", "--verify", ref_name], timeout=10)
        return result is not None and result.returncode == 0

    def get_git_remote_ref(self, plugin_dir):
        result = self.run_git_command(
            plugin_dir,
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            timeout=10
        )
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        branch = self.get_git_current_branch(plugin_dir)
        remote_name = self.get_git_remote_name(plugin_dir, branch)
        if branch:
            branch_ref = remote_name + "/" + branch
            if self.git_ref_exists(plugin_dir, branch_ref):
                return branch_ref

        result = self.run_git_command(plugin_dir, ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/" + remote_name + "/HEAD"], timeout=10)
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        return ""

    def get_git_remote_commit_date(self, plugin_dir, remote_ref):
        if not remote_ref:
            return ""

        result = self.run_git_command(plugin_dir, ["git", "log", "-1", "--format=%ct", remote_ref], timeout=10)
        if result is None or result.returncode != 0:
            if result is not None and result.stderr:
                Domoticz.Debug("Git Log Error:" + result.stderr.strip())
            return ""

        timestamp = result.stdout.strip()
        if not timestamp:
            return ""

        try:
            return datetime.utcfromtimestamp(int(timestamp)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            Domoticz.Debug("Could not parse git commit timestamp '" + timestamp + "': " + str(e))
            return ""

    def get_git_ahead_behind(self, plugin_dir, remote_ref):
        if not remote_ref:
            return None

        result = self.run_git_command(
            plugin_dir,
            ["git", "rev-list", "--left-right", "--count", "HEAD..." + remote_ref],
            timeout=10
        )
        if result is None or result.returncode != 0:
            if result is not None and result.stderr:
                Domoticz.Debug("Git Rev-List Error:" + result.stderr.strip())
            return None

        ahead_behind = result.stdout.strip().split()
        if len(ahead_behind) < 2:
            return None

        try:
            return int(ahead_behind[0]), int(ahead_behind[1])
        except ValueError:
            return None

    def refresh_git_update_time(self, plugin_key, plugin_dir, update_times=None, remote_ref=""):
        if not plugin_key or not os.path.isdir(os.path.join(plugin_dir, ".git")):
            return False

        if not remote_ref:
            remote_ref = self.get_git_remote_ref(plugin_dir)

        updated_at = self.get_git_remote_commit_date(plugin_dir, remote_ref)
        if not updated_at:
            return False

        remote_name = remote_ref.split("/", 1)[0] if "/" in remote_ref else "origin"
        remote_url = self.get_git_remote_url(plugin_dir, remote_name)
        return self.overlay_git_update_time(plugin_key, updated_at, update_times, remote_url, remote_ref)

    def refresh_single_plugin_update_time(self, plugin_key, plugin_dir, fetch_first=True):
        if not os.path.isdir(os.path.join(plugin_dir, ".git")):
            return False

        if fetch_first and not self.fetch_git_repo(plugin_dir):
            return False

        update_times = dict(self.update_times) if self.update_times else self.load_cached_update_times()
        changed = self.refresh_git_update_time(plugin_key, plugin_dir, update_times)
        if changed:
            self.save_update_times_cache(update_times)
            self.apply_update_times(update_times)
        return changed

    def getInstalledPlugins(self, plugins_dir):
        installed_plugins = []
        for d in os.listdir(plugins_dir):
            if os.path.isdir(os.path.join(plugins_dir, d)) and not d.startswith("."):
                installed_plugins.append(d)
        return installed_plugins

    def getCachedUpdateStatuses(self, installed_plugins):
        update_status = {}
        for plugin_key in installed_plugins:
            if plugin_key not in self.plugin_data:
                update_status[plugin_key] = "unknown"
                continue
            update_status[plugin_key] = self.update_status.get(plugin_key, "unknown")
        return update_status

    def refresh_single_plugin_update_status(self, plugin_key, plugin_dir, fetch_first=True):
        if plugin_key not in self.plugin_data:
            self.update_status[plugin_key] = "unknown"
            return "unknown"

        update_status = self.getGitUpdateStatus(plugin_dir, plugin_key, fetch_first=fetch_first)
        self.update_status[plugin_key] = update_status
        return update_status

    def refreshInstalledUpdateStatuses(self, installed_plugins=None, plugins_dir=None):
        if plugins_dir is None:
            plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        if installed_plugins is None:
            installed_plugins = self.getInstalledPlugins(plugins_dir)

        update_status = {}
        for plugin_key in installed_plugins:
            if plugin_key not in self.plugin_data:
                update_status[plugin_key] = "unknown"
                continue

            plugin_dir = os.path.join(plugins_dir, plugin_key)
            update_status[plugin_key] = self.refresh_single_plugin_update_status(plugin_key, plugin_dir)

        self.update_status.update(update_status)
        return update_status

    def add_self_to_registry(self):
        self_key = self.get_current_plugin_folder()
        if not self_key:
            return

        self.plugin_data[self_key] = [
            "adrighem",
            "PyPluginStore",
            "PyPluginStore plugin manager",
            "master",
            self.update_times.get(self_key, "")
        ]

    def fetch_registry(self):
        registry = self.fetch_remote_registry()
        if registry is None:
            registry = self.load_bundled_registry()

        local_registry = self.load_local_registry()
        registry_loaded = registry is not None or local_registry is not None
        merged_registry = dict(registry) if registry is not None else {}
        local_plugin_keys = []

        if local_registry:
            merged_registry.update(local_registry)
            local_plugin_keys = sorted(local_registry.keys())
            Domoticz.Log("Merged " + str(len(local_registry)) + " local plugin registry entries.")

        if registry_loaded:
            self.plugin_data = merged_registry
            self.local_plugin_keys = local_plugin_keys
        elif self.plugin_data:
            Domoticz.Error("No plugin registry found. Keeping existing plugin registry.")
        else:
            self.plugin_data = {}
            self.local_plugin_keys = []
            Domoticz.Error("No plugin registry found. Plugins cannot be managed.")

        update_times = self.load_update_times()
        self.apply_update_times(update_times)
        self.add_self_to_registry()

    def onStart(self):
        import json
        Domoticz.Debug("onStart called")

        if Parameters["Mode6"] == 'Debug':
            self.debug = True
            Domoticz.Debugging(1)
            DumpConfigToLog()
        else:
            Domoticz.Debugging(0)

        Domoticz.Log(f"Domoticz Node Name is: {platform.node()}")
        Domoticz.Log(f"Domoticz Platform System is: {platform.system()}")
        Domoticz.Debug(f"Domoticz Platform Release is: {platform.release()}")
        Domoticz.Debug(f"Domoticz Platform Version is: {platform.version()}")
        Domoticz.Log(f"Default Python Version is: {sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")

        if platform.system() == "Windows":
            Domoticz.Error("Windows Platform NOT YET SUPPORTED!!")
            return

        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))

        current_folder = os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/')))
        if not current_folder.startswith("00-"):
            warn_msg = f"PyPluginStore is in '{current_folder}'. It is strongly advised to rename the folder to start with '00-' (e.g., '00-PyPluginStore') so it loads first."
            Domoticz.Error(warn_msg)
            Domoticz.SendNotification("PyPluginStore Setup Warning", warn_msg)

        # Inject shared dependencies into sys.path
        shared_deps_dir = os.path.join(plugins_dir, os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), ".shared_deps")
        if os.path.isdir(shared_deps_dir) and shared_deps_dir not in sys.path:
            sys.path.insert(0, shared_deps_dir)
            Domoticz.Log(f"Injected PyPluginStore shared dependencies into sys.path: {shared_deps_dir}")

        # Autoinstall/Update Custom UI
        try:
            import shutil
            # Determine paths
            home_folder_param = Parameters.get("HomeFolder", str(os.getcwd()) + "/")
            html_src = os.path.join(home_folder_param, "pypluginstore.html")
            
            # Find templates directory (relative to plugins folder)
            domoticz_dir = os.path.abspath(os.path.join(home_folder_param, "..", ".."))
            templates_dir = os.path.join(domoticz_dir, "www", "templates")
            html_dst = os.path.join(templates_dir, "pypluginstore.html")
            
            if os.path.isfile(html_src):
                if not os.path.exists(templates_dir):
                    Domoticz.Debug(f"Creating templates directory: {templates_dir}")
                    os.makedirs(templates_dir, exist_ok=True)
                
                # Remove legacy UI if it exists
                old_html_dst = os.path.join(templates_dir, "pp-manager.html")
                if os.path.isfile(old_html_dst):
                    try:
                        os.remove(old_html_dst)
                        Domoticz.Log(f"Removed legacy UI file: {old_html_dst}")
                    except Exception as e:
                        Domoticz.Error(f"Failed to remove legacy UI file: {e}")
                
                # Check if we need to copy (exists and different, or doesn't exist)
                should_copy = True
                if os.path.isfile(html_dst):
                    src_mtime = os.path.getmtime(html_src)
                    dst_mtime = os.path.getmtime(html_dst)
                    if src_mtime <= dst_mtime:
                        should_copy = False
                
                if should_copy:
                    shutil.copyfile(html_src, html_dst)
                    # Try to ensure it is readable by the web server
                    os.chmod(html_dst, 0o644)
                    Domoticz.Log(f"Custom UI autoinstalled/updated: {html_dst}")
                else:
                    Domoticz.Debug("Custom UI is already up to date.")
        except Exception as e:
            Domoticz.Error(f"Custom UI autoinstall failed: {e}")
            Domoticz.Debug(f"Check permissions for: {templates_dir}")

        if 1 not in Devices:
            Domoticz.Device(Name="API Payload", Unit=1, TypeName="Text", DeviceID="PPM_API_PAYLOAD", Used=1).Create()
        if 2 not in Devices:
            Domoticz.Device(Name="API Trigger", Unit=2, Type=244, Subtype=73, Switchtype=9, DeviceID="PPM_API_TRIGGER", Used=1).Create()
            
        self.fetch_registry()

        if Parameters.get("Mode5") == 'True':
            Domoticz.Log("Plugin Security Scan is enabled")
            secpoluserFile = os.path.join(plugins_dir, os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), "secpoluser.txt")
            Domoticz.Debug("Checking for SecPolUser file on:" + secpoluserFile)
            if os.path.isfile(secpoluserFile):
                Domoticz.Log("secpoluser file found. Processing!!!")
                with open(secpoluserFile) as secpoluserFileHandle:
                    for line in secpoluserFileHandle:
                        line = line.strip()
                        if line.startswith("--->"):
                            secpoluserSection = line[4:]
                            Domoticz.Log("secpoluser settings found for plugin:" + secpoluserSection)
                        elif line and not line.startswith("--->"):
                            Domoticz.Debug("SecPolUserList exception (" + secpoluserSection + "): '" + line + "'")
                            if secpoluserSection not in self.secpoluser_list:
                                self.secpoluser_list[secpoluserSection] = []
                            self.secpoluser_list[secpoluserSection].append(line)
                Domoticz.Log("SecPolUserList exception:" + str(self.secpoluser_list))
            else:
                self.secpoluser_list = {"Global":[]}

            # Scan all plugins in the plugins directory
            for plugin_folder in os.listdir(plugins_dir):
                plugin_path = os.path.join(plugins_dir, plugin_folder)

                # Make sure it's a directory and not the manager itself
                if os.path.isdir(plugin_path) and plugin_folder != os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):

                    # Recursively walk through the plugin's folder to find all .py files
                    for root, _, files in os.walk(plugin_path):
                        # Optional: skip hidden folders like .git or .shared_deps
                        if '/.' in root.replace('\\', '/'):
                            continue

                        for file in files:
                            if file.endswith('.py'):
                                py_file = os.path.join(root, file)
                                self.parseFileForSecurityIssues(py_file, plugin_folder)

        exceptionFile = os.path.join(plugins_dir, os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), "exceptions.txt")
        Domoticz.Debug("Checking for Exception file on:" + exceptionFile)
        if os.path.isfile(exceptionFile):
            Domoticz.Log("Exception file found. Processing!!!")
            with open(exceptionFile) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        Domoticz.Log("File ReadLine result:'" + line + "'")
                        self.exception_list.append(line)
        Domoticz.Debug("self.exception_list:" + str(self.exception_list))

        if Parameters["Mode4"] == 'All':
            Domoticz.Log("Updating All Plugins!!!")
            for root, dirs, files in os.walk(plugins_dir):
                for d in dirs:
                    if d:
                        if d in self.plugin_data:
                            self.UpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                        elif d == os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                            Domoticz.Debug("PyPluginStore Folder found. Skipping!!")
                        else:
                            Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")
                break

        if Parameters["Mode4"] == 'AllNotify':
            Domoticz.Log("Collecting Updates for All Plugins!!!")
            for root, dirs, files in os.walk(plugins_dir):
                for d in dirs:
                    if d:
                        if d in self.plugin_data:
                            self.CheckForUpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                        elif d == os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                            Domoticz.Debug("PyPluginStore Folder found. Skipping!!")
                        else:
                            Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")
                break

        Domoticz.Log("Plugin Manager Ready. Use the 'Custom' menu to manage plugins.")
        Domoticz.Heartbeat(60)

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug(f"onCommand called for Unit {Unit}: Command '{Command}', Level: {Level}")
        if Unit == 2 and Command.lower() == "on":
            if 1 in Devices:
                payload_str = Devices[1].sValue
                
                # 1. DoS Protection: Limit payload length (Domoticz text limit is usually enough, but let's be safe)
                if len(payload_str) > 2000:
                    Domoticz.Error("API Payload exceeds length limit.")
                    Devices[1].Update(nValue=0, sValue="")
                    return

                Domoticz.Debug(f"API Payload received: {payload_str}")
                try:
                    # Clear payload device immediately to prevent replay/abuse
                    Devices[1].Update(nValue=0, sValue="")
                    
                    payload = json.loads(payload_str)
                    
                    # 2. Type Validation: Ensure we got a dictionary
                    if not isinstance(payload, dict):
                        raise ValueError("Payload must be a JSON object")
                    
                    # 3. Content Sanitization
                    self.tx_id = str(payload.get("tx_id", ""))[:50] # Limit tx_id length
                    self.handleApiCommand(payload)
                except Exception as e:
                    Domoticz.Error(f"Failed to parse API payload: {e}")
                    self.sendApiResponse({"status": "error", "message": "Invalid JSON payload or structure"})

    def handleApiCommand(self, payload):
        import shutil
        # Ensure action is a safe string
        action = str(payload.get("action", ""))
        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        
        if action == "list_plugins":
            installed_plugins = self.getInstalledPlugins(plugins_dir)
                    
            self.sendApiResponse({
                "status": "success",
                "action": action,
                "data": self.plugin_data,
                "installed": installed_plugins,
                "manager_key": self.get_current_plugin_folder(),
                "local_plugins": self.local_plugin_keys,
                "update_status": self.getCachedUpdateStatuses(installed_plugins)
            })
        elif action == "refresh_update_status":
            installed_plugins = self.getInstalledPlugins(plugins_dir)
            update_status = self.refreshInstalledUpdateStatuses(installed_plugins, plugins_dir)
            self.sendApiResponse({
                "status": "success",
                "action": action,
                "data": self.plugin_data,
                "installed": installed_plugins,
                "manager_key": self.get_current_plugin_folder(),
                "local_plugins": self.local_plugin_keys,
                "update_status": update_status
            })
        elif action == "install":
            plugin_key = payload.get("plugin_key")
            if plugin_key in self.plugin_data:
                plugin_author = self.plugin_data[plugin_key][0]
                plugin_repository = self.plugin_data[plugin_key][1]
                plugin_branch = self.plugin_data[plugin_key][3]
                install_success, install_message = self.InstallPythonPlugin(plugin_author, plugin_repository, plugin_key, plugin_branch)
                if install_success:
                    self.sendApiResponse({"status": "success", "action": action, "plugin_key": plugin_key})
                else:
                    self.sendApiResponse({"status": "error", "message": install_message or "Plugin install failed"})
            else:
                self.sendApiResponse({"status": "error", "message": "Plugin not found"})
        elif action == "update":
            plugin_key = payload.get("plugin_key")
            if plugin_key in self.plugin_data:
                plugin_author = self.plugin_data[plugin_key][0]
                plugin_repository = self.plugin_data[plugin_key][1]
                self.UpdatePythonPlugin(plugin_author, plugin_repository, plugin_key)
                self.sendApiResponse({"status": "success", "action": action, "plugin_key": plugin_key})
            else:
                self.sendApiResponse({"status": "error", "message": "Plugin not found"})
        elif action == "restart_domoticz":
            self.sendApiResponse({
                "status": "success",
                "action": action,
                "message": "Domoticz restart requested"
            })
            self.restartDomoticz()
        elif action == "remove":
            plugin_key = payload.get("plugin_key", "")
            # Security: Prevent path traversal and accidental deletion of core folders
            plugin_key = os.path.basename(plugin_key)
            plugin_target_dir = os.path.abspath(os.path.join(plugins_dir, plugin_key))
            
            # Ensure the resolved path is still inside the plugins directory
            if not plugin_target_dir.startswith(plugins_dir):
                 self.sendApiResponse({"status": "error", "message": "Invalid plugin path"})
                 return

            if os.path.isdir(plugin_target_dir) and plugin_key != os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                try:
                    shutil.rmtree(plugin_target_dir)
                    self.sendApiResponse({"status": "success", "action": action, "plugin_key": plugin_key})
                except Exception as e:
                    self.sendApiResponse({"status": "error", "message": str(e)})
            else:
                self.sendApiResponse({"status": "error", "message": "Plugin directory not found or cannot remove self"})
        else:
            self.sendApiResponse({"status": "error", "message": f"Unknown action: {action}"})

    def getInstalledUpdateStatuses(self, installed_plugins, plugins_dir):
        return self.refreshInstalledUpdateStatuses(installed_plugins, plugins_dir)

    def getGitUpdateStatus(self, plugin_dir, plugin_key=None, fetch_first=True):
        if not os.path.isdir(os.path.join(plugin_dir, ".git")):
            return "unknown"

        try:
            if fetch_first and not self.fetch_git_repo(plugin_dir):
                return "unknown"

            remote_ref = self.get_git_remote_ref(plugin_dir) or "@{u}"
            if plugin_key:
                update_times = dict(self.update_times) if self.update_times else self.load_cached_update_times()
                if self.refresh_git_update_time(plugin_key, plugin_dir, update_times, remote_ref):
                    self.save_update_times_cache(update_times)
                    self.apply_update_times(update_times)

            ahead_behind = self.get_git_ahead_behind(plugin_dir, remote_ref)
            if ahead_behind is None:
                return "unknown"

            behind = ahead_behind[1]
            if behind > 0:
                return "available"
            return "current"
        except Exception as e:
            Domoticz.Debug(f"Could not determine update status for {plugin_dir}: {e}")
            return "unknown"

    def sendApiResponse(self, response_dict):
        if 1 in Devices:
            try:
                if hasattr(self, 'tx_id') and self.tx_id:
                    response_dict['tx_id'] = self.tx_id
                response_str = json.dumps(response_dict)
                Devices[1].Update(nValue=0, sValue=response_str)
            except Exception as e:
                Domoticz.Error(f"Failed to send API response: {e}")

    def onStop(self):
        Domoticz.Debug("onStop called")
        Domoticz.Log("Plugin is stopping.")
        Domoticz.Debugging(0)

    def onHeartbeat(self):
        Domoticz.Debug("onHeartbeat called")

        now = datetime.now()
        Domoticz.Debug(f"Current time: {now.strftime('%H:%M')}")

        home_folder = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), "..", ".."))
        plugins_dir = os.path.join(home_folder, "plugins")

        if self.last_update_status_refresh_date is None or self.last_update_status_refresh_date < now.date():
            Domoticz.Log("Refreshing plugin update status cache.")
            self.last_update_status_refresh_date = now.date()
            self.refreshInstalledUpdateStatuses(plugins_dir=plugins_dir)

        if now.hour >= 12 and (self.last_update_date is None or self.last_update_date < now.date()):
            Domoticz.Log("Its time!!. Trigering Actions!!!")
            self.last_update_date = now.date()

            if Parameters["Mode4"] == 'All':
                Domoticz.Log("Checking Updates for All Plugins!!!")
                for root, dirs, files in os.walk(plugins_dir):
                    for d in dirs:
                        if d:
                            if d in self.plugin_data:
                                self.UpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                            elif d == os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                                Domoticz.Debug("PyPluginStore Folder found. Skipping!!")
                            else:
                                Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")
                    break

            if Parameters["Mode4"] == 'AllNotify':
                Domoticz.Log("Collecting Updates for All Plugins!!!")
                for root, dirs, files in os.walk(plugins_dir):
                    for d in dirs:
                        if d:
                            if d in self.plugin_data:
                                self.CheckForUpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                            elif d == os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                                Domoticz.Debug("PyPluginStore Folder found. Skipping!!")
                            else:
                                Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")
                    break

    def InstallPythonPlugin(self, ppAuthor, ppRepository, ppKey, ppBranch):
        Domoticz.Debug("InstallPythonPlugin called")

        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        plugin_dir = os.path.join(plugins_dir, ppKey)

        Domoticz.Log("Installing Plugin:" + self.plugin_data[ppKey][2])
        ppCloneCmd = ["git", "clone", "-b", ppBranch, self.build_git_clone_url(ppAuthor, ppRepository), ppKey]
        Domoticz.Log("Calling: " + " ".join(ppCloneCmd))

        env = self.get_git_env()

        try:
            pr = subprocess.Popen(ppCloneCmd, cwd=plugins_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out, error = pr.communicate()
            if out:
                Domoticz.Debug("Git Response: " + out.strip())
            if error:
                Domoticz.Debug("Git Error: " + error.strip())
            if pr.returncode != 0:
                Domoticz.Error("Git clone failed for plugin " + ppKey + ".")
                return False, (error or out or "Git clone failed").strip()
            Domoticz.Log("Plugin " + ppKey + " installed successfully.")
        except OSError as e:
            Domoticz.Error("Git ErrorNo:" + str(e.errno))
            Domoticz.Error("Git StrError:" + str(e.strerror))
            return False, str(e)

        if not os.path.isdir(plugin_dir):
            Domoticz.Error("Plugin folder was not created: " + plugin_dir)
            return False, "Plugin folder was not created."

        self.refresh_single_plugin_update_time(ppKey, plugin_dir, fetch_first=False)
        self.refresh_single_plugin_update_status(ppKey, plugin_dir, fetch_first=False)
        self.installDependencies(ppKey)
        Domoticz.Log("---Restarting Domoticz MAY BE REQUIRED to activate new plugins---")
        return True, ""

    def UpdatePythonPlugin(self, ppAuthor, ppRepository, ppKey):
        Domoticz.Debug("UpdatePythonPlugin called")

        home_folder = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), "..", ".."))
        plugin_dir = os.path.join(home_folder, "plugins", ppKey)
        is_self_update = ppKey == self.get_current_plugin_folder()

        if (ppKey in self.plugin_data and self.plugin_data[ppKey][2] in self.exception_list):
            Domoticz.Log("Plugin:" + self.plugin_data[ppKey][2] + " excluded by Exclusion file (exclusion.txt). Skipping!!!")
            return

        if is_self_update:
            Domoticz.Log("Self update requested for PyPluginStore.")

        Domoticz.Log("Resetting and Updating Plugin:" + ppKey)
        env = self.get_git_env()

        ppGitReset = ["git", "reset", "--hard", "HEAD"]
        try:
            res_reset = subprocess.run(ppGitReset, cwd=plugin_dir, env=env, capture_output=True, text=True)
            if res_reset.stdout:
                Domoticz.Debug("Git Reset Response:" + res_reset.stdout)
            if res_reset.stderr:
                Domoticz.Debug("Git Reset Error:" + res_reset.stderr.strip())
        except OSError as eReset:
            Domoticz.Error("Git ErrorNo:" + str(eReset.errno))
            Domoticz.Error("Git StrError:" + str(eReset.strerror))

        ppUrl = ["git", "pull", "--force"]
        Domoticz.Debug("Calling: " + " ".join(ppUrl) + " on folder " + plugin_dir)

        try:
            res = subprocess.run(ppUrl, cwd=plugin_dir, env=env, capture_output=True, text=True)
            out = res.stdout
            error = res.stderr
            if out:
                Domoticz.Debug("Git Response:" + out)
                if "Already up to date" in out or "Already up-to-date" in out:
                   Domoticz.Log("Plugin " + ppKey + " already Up-To-Date")
                elif "Updating" in out and "error" not in out.lower():
                   Domoticz.Log("Succesfully pulled gitHub update for plugin " + ppKey)
                   Domoticz.Log("---Restarting Domoticz MAY BE REQUIRED to activate new plugins---")
                else:
                   Domoticz.Error("Something went wrong with update of " + str(ppKey))
            if error:
                Domoticz.Debug("Git Error:" + error.strip())
                if "Not a git repository" in error:
                   Domoticz.Log("Plugin:" + ppKey + " is not installed from gitHub. Cannot be updated with PyPluginStore!!.")
        except OSError as e:
            Domoticz.Error("Git ErrorNo:" + str(e.errno))
            Domoticz.Error("Git StrError:" + str(e.strerror))

        self.refresh_single_plugin_update_time(ppKey, plugin_dir, fetch_first=False)
        self.refresh_single_plugin_update_status(ppKey, plugin_dir, fetch_first=False)
        self.installDependencies(ppKey)
        return None

    def CheckForUpdatePythonPlugin(self, ppAuthor, ppRepository, ppKey):
        Domoticz.Debug("CheckForUpdatePythonPlugin called")

        if ppKey in self.plugin_data and self.plugin_data[ppKey][2] in self.exception_list:
            Domoticz.Log("Plugin:" + self.plugin_data[ppKey][2] + " excluded by Exclusion file (exclusion.txt). Skipping!!!")
            return

        Domoticz.Debug("Checking Plugin:" + ppKey + " for updates")

        home_folder = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), "..", ".."))
        plugin_dir = os.path.join(home_folder, "plugins", ppKey)

        if not os.path.isdir(os.path.join(plugin_dir, ".git")):
            Domoticz.Log("Plugin:" + ppKey + " is not installed from gitHub. Ignoring!!.")
            return None

        if not self.fetch_git_repo(plugin_dir):
            Domoticz.Error("Something went wrong with update check of " + str(ppKey))
            return None

        remote_ref = self.get_git_remote_ref(plugin_dir) or "@{u}"
        update_times = dict(self.update_times) if self.update_times else self.load_cached_update_times()
        if self.refresh_git_update_time(ppKey, plugin_dir, update_times, remote_ref):
            self.save_update_times_cache(update_times)
            self.apply_update_times(update_times)

        ahead_behind = self.get_git_ahead_behind(plugin_dir, remote_ref)
        if ahead_behind is None:
            Domoticz.Error("Something went wrong with update check of " + str(ppKey))
            return None

        ahead, behind = ahead_behind
        if behind > 0:
            Domoticz.Log("Found that we are behind on plugin " + ppKey)
            self.fnSelectedNotify(ppKey)
        elif ahead > 0:
            Domoticz.Debug("Found that we are ahead on plugin " + ppKey + ". No need for update")
        else:
            Domoticz.Log("Plugin " + ppKey + " already Up-To-Date")

        return None

    def fnSelectedNotify(self, plugin_key):
        Domoticz.Debug("fnSelectedNotify called")
        Domoticz.Log("Preparing Notification")
        plugin_name = self.plugin_data[plugin_key][2] if plugin_key in self.plugin_data else plugin_key
        MailSubject = platform.node() + ": Domoticz Plugin Updates Available for " + plugin_name
        MailBody = plugin_name + " has updates available!!"
        Domoticz.SendNotification(MailSubject, MailBody)
        Domoticz.Debug("Notification sent natively.")
        return None

    def parseIntValue(self, s):
        Domoticz.Debug("parseIntValue called")
        try:
            return int(s)
        except:
            return None

    def is_private_ip(self, ip_str):
        try:
            octets = [int(o) for octet in ip_str.split('.') for o in octet.split()] # Handle potential spaces
            octets = [int(o) for o in ip_str.split('.')]
            if len(octets) != 4: return False
            # Loopback
            if octets[0] == 127: return True
            # Class A private
            if octets[0] == 10: return True
            # Class B private
            if octets[0] == 172 and 16 <= octets[1] <= 31: return True
            # Class C private
            if octets[0] == 192 and octets[1] == 168: return True
            # Link-local
            if octets[0] == 169 and octets[1] == 254: return True
            # Broadcast / Software versions (e.g. 0.0.0.0)
            if octets[0] == 0: return True
            # Ignore Chrome version numbers (e.g. 124.0.0.0)
            if octets[1] == 0 and octets[2] == 0 and octets[3] == 0: return True
            return False
        except:
            return False

    def parseFileForSecurityIssues(self, pyfilename, pypluginid):
        import ast
        Domoticz.Debug("parseFileForSecurityIssues called")
        if Parameters.get("Mode5") == 'True':
            Domoticz.Log(f"Scanning {pyfilename} for security issues...")

        if pypluginid not in self.secpoluser_list:
            self.secpoluser_list[pypluginid] = []

        ip_pattern = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')

        try:
            # 1. Prevent Large File DoS: Limit file read to 5MB
            MAX_FILE_SIZE = 5 * 1024 * 1024
            with open(pyfilename, "r", encoding="utf-8", errors="ignore") as file:
                source_code = file.read(MAX_FILE_SIZE)
                if file.read(1):
                    Domoticz.Error(f"Plugin file {pyfilename} exceeds 5MB limit. Plugin considered UNSAFE.")
                    return

            try:
                tree = ast.parse(source_code)
            except (SyntaxError, RecursionError, MemoryError, Exception) as e:
                Domoticz.Error(f"Failed to parse plugin file {pyfilename} (Possible AST Bomb or invalid syntax): {e}. Plugin considered UNSAFE.")
                return

            class SecurityScanner(ast.NodeVisitor):
                def __init__(self):
                    self.findings = []

                def get_full_name(self, node):
                    if isinstance(node, ast.Name):
                        return node.id
                    elif isinstance(node, ast.Attribute):
                        val = self.get_full_name(node.value)
                        return f"{val}.{node.attr}" if val else node.attr
                    return ""

                def visit_Call(self, node):
                    func_full_name = self.get_full_name(node.func)

                    func_base_name = ""
                    if isinstance(node.func, ast.Name):
                        func_base_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_base_name = node.func.attr

                    exact_matches = {'os.system', 'os.popen', 'eval', 'exec', '__import__', 'compile', 'pickle.loads', 'pickle.load', 'os.remove', 'os.unlink', 'shutil.rmtree'}

                    if func_full_name in exact_matches:
                        self.findings.append((node.lineno, f"Suspicious Call: {func_full_name}"))
                    elif func_full_name.startswith('subprocess.'):
                        # Specifically look for shell=True which is the biggest risk
                        is_shell = False
                        for keyword in node.keywords:
                            if keyword.arg == 'shell' and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                                is_shell = True
                        if is_shell:
                            self.findings.append((node.lineno, f"Dangerous Subprocess (shell=True): {func_full_name}"))
                    elif func_base_name in {'eval', 'exec', '__import__', 'compile'}:
                        self.findings.append((node.lineno, f"Suspicious Call: {func_base_name}"))
                    elif func_base_name in {'system', 'popen', 'rmtree', 'unlink'}:
                        self.findings.append((node.lineno, f"Potentially Suspicious Call (Alias?): {func_base_name}"))

                    self.generic_visit(node)

                def visit_Name(self, node):
                    if node.id in {'eval', 'exec', '__import__', 'compile'} and isinstance(getattr(node, 'ctx', None), ast.Load):
                        self.findings.append((node.lineno, f"Dangerous Builtin Referenced: {node.id}"))
                    self.generic_visit(node)

            scanner = SecurityScanner()
            scanner.visit(tree)

            ast_findings_map = {}
            for lineno, finding in scanner.findings:
                if lineno not in ast_findings_map:
                    ast_findings_map[lineno] = []
                if finding not in ast_findings_map[lineno]:
                    ast_findings_map[lineno].append(finding)

            lines = source_code.splitlines()
            for i, text in enumerate(lines):
                lineNum = i + 1
                clean_text = text.strip()

                # Ignore comments, empty lines, and explicit overrides
                if not clean_text or clean_text.startswith('#') or '<param field=' in clean_text or '# security-ignore' in text or '# nosec' in text:
                    continue

                findings = []

                for ip in ip_pattern.findall(clean_text):
                    if all(0 <= int(octet) <= 255 for octet in ip.split('.')):
                        if not self.is_private_ip(ip):
                            findings.append(f"Public IP Address: {ip}")

                if lineNum in ast_findings_map:
                    findings.extend(ast_findings_map[lineNum])

                for finding in findings:
                    is_excluded = False
                    combined_exclusions = self.secpoluser_list.get("Global", []) + self.secpoluser_list[pypluginid]
                    for exclusion in combined_exclusions:
                        if exclusion in clean_text or exclusion in finding:
                            is_excluded = True
                            break

                    if not is_excluded:
                        Domoticz.Error(f"Security Finding in {pypluginid}: --> {finding} <-- LINE: {lineNum} FILE: {pyfilename}")
                        Domoticz.Error(f"Code context: {clean_text}")

        except Exception as e:
            Domoticz.Error(f"Error reading or processing {pyfilename}: {str(e)}")

    def restartDomoticz(self):
        Domoticz.Log("Domoticz service restart requested from PyPluginStore UI.")
        helper = r'''
import subprocess
import time

commands = [
    ["sudo", "-n", "systemctl", "restart", "domoticz.service"],
    ["systemctl", "restart", "domoticz.service"],
    ["sudo", "-n", "service", "domoticz", "restart"],
    ["service", "domoticz", "restart"],
]

time.sleep(2)
for command in commands:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if result.returncode == 0:
            break
    except Exception:
        pass
'''
        try:
            subprocess.Popen(
                [sys.executable, "-c", helper],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception as e:
            Domoticz.Error(f"Failed to schedule Domoticz restart: {e}")

    def installDependencies(self, plugin_key):
        Domoticz.Debug("installDependencies called")
        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        plugin_dir = os.path.join(plugins_dir, plugin_key)
        requirementsFile = os.path.join(plugin_dir, "requirements.txt")
        home_folder = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), "..", ".."))
        shared_deps_dir = os.path.join(home_folder, "plugins", os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), ".shared_deps")

        def check_cmd(cmd):
            try:
                subprocess.run([cmd, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except:
                return False

        if os.path.isfile(requirementsFile):
            Domoticz.Log("requirements.txt found for plugin: " + plugin_key)
            os.makedirs(shared_deps_dir, exist_ok=True)

            # Check for 'uv' then 'pip' as fallbacks
            installCmd = None
            if check_cmd("uv"):
                installCmd = ["uv", "pip", "install", "-r", requirementsFile, "--target", shared_deps_dir]
            elif check_cmd("pip3"):
                installCmd = ["pip3", "install", "-r", requirementsFile, "--target", shared_deps_dir]
            elif check_cmd("pip"):
                installCmd = ["pip", "install", "-r", requirementsFile, "--target", shared_deps_dir]

            if installCmd:
                Domoticz.Log("Installing dependencies using: " + " ".join(installCmd))
                try:
                    pr = subprocess.Popen(installCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    out, error = pr.communicate()
                    if pr.returncode == 0:
                        Domoticz.Log("Dependencies installed successfully: " + out.strip())
                    else:
                        Domoticz.Error("Error installing dependencies: " + error.strip())
                except Exception as e:
                    Domoticz.Error("Error running installation command: " + str(e))
            else:
                Domoticz.Log("Neither 'uv' nor 'pip' found. Skipping automatic dependency installation.")
                Domoticz.Log(f"Please install dependencies manually from {requirementsFile} into {shared_deps_dir}")
        else:
            Domoticz.Log("No requirements.txt found for plugin: " + plugin_key)
        return None

global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug( "'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
    return
