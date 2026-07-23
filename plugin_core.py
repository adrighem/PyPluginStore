

import base64
import copy
from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import html
import http.client
import importlib.util
import ipaddress
import io
import json
import math
import os
import platform
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import Domoticz


def _capture_loaded_source_fingerprint(path):
    """Fingerprint module source at import time before disk can advance."""
    try:
        path_stat = os.lstat(path)
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            return None
        if path_stat.st_size <= 0 or path_stat.st_size > 8 * 1024 * 1024:
            return None
        with open(path, "rb") as source_file:
            contents = source_file.read(8 * 1024 * 1024 + 1)
        if not contents or len(contents) > 8 * 1024 * 1024:
            return None
        return (len(contents), hashlib.sha256(contents).hexdigest())
    except OSError:
        return None


_MANAGER_LOADED_PLUGIN_SOURCE_FINGERPRINT = (
    _capture_loaded_source_fingerprint(__file__)
)


def _load_package_registry_module():
    """Import sibling package contracts from normal or path-based loads."""
    try:
        import package_registry as registry_module

        return registry_module
    except ModuleNotFoundError as error:
        if error.name != "package_registry":
            raise

    module_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "package_registry.py"
    )
    specification = importlib.util.spec_from_file_location(
        "package_registry", module_path
    )
    if specification is None or specification.loader is None:
        raise ImportError("Could not load the package registry contract.")
    module = importlib.util.module_from_spec(specification)
    sys.modules["package_registry"] = module
    try:
        specification.loader.exec_module(module)
    except Exception:
        if sys.modules.get("package_registry") is module:
            del sys.modules["package_registry"]
        raise
    return module


_package_registry_module = _load_package_registry_module()
PackageRegistry = _package_registry_module.PackageRegistry
UpdateTimesDocument = _package_registry_module.UpdateTimesDocument
_MANAGER_LOADED_PACKAGE_REGISTRY_FINGERPRINT = getattr(
    _package_registry_module,
    "PYPLUGINSTORE_LOADED_SOURCE_FINGERPRINT",
    None,
)


def _load_package_identity_module():
    """Import the sibling non-executing identity certifier."""
    try:
        import package_identity as identity_module

        return identity_module
    except ModuleNotFoundError as error:
        if error.name != "package_identity":
            raise

    module_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "package_identity.py"
    )
    specification = importlib.util.spec_from_file_location(
        "package_identity", module_path
    )
    if specification is None or specification.loader is None:
        raise ImportError("Could not load the package identity certifier.")
    module = importlib.util.module_from_spec(specification)
    sys.modules["package_identity"] = module
    try:
        specification.loader.exec_module(module)
    except Exception:
        if sys.modules.get("package_identity") is module:
            del sys.modules["package_identity"]
        raise
    return module


_package_identity_module = _load_package_identity_module()
certify_plugin_py = _package_identity_module.certify_plugin_py
_MANAGER_LOADED_PACKAGE_IDENTITY_FINGERPRINT = getattr(
    _package_identity_module,
    "PYPLUGINSTORE_LOADED_SOURCE_FINGERPRINT",
    None,
)


API_PAYLOAD_MAX_LENGTH = 2000
SELF_UPDATE_STARTUP_DELAY_SECONDS = 5
SELF_UPDATE_ACTIVE_STALE_SECONDS = 15 * 60
DEFAULT_GIT_HOST = "github.com"
SUPPORTED_GIT_HOSTS = ("github.com", "gitlab.com", "codeberg.org")
REPOSITORY_PATH_STOP_PARTS = {
    "-",
    "blob",
    "commits",
    "issues",
    "pulls",
    "raw",
    "releases",
    "src",
    "tree",
}


def is_git_checkout(plugin_dir):
    """Recognize normal clones and Git worktrees without following links."""
    git_control_path = os.path.join(plugin_dir, ".git")
    return bool(
        not os.path.islink(git_control_path)
        and (
            os.path.isdir(git_control_path)
            or os.path.isfile(git_control_path)
        )
    )


def parameter_get(parameters, key, default):
    try:
        return parameters.get(key, default)
    except AttributeError:
        try:
            return parameters[key]
        except Exception:
            return default


class HostRuntime:
    platform_name = "generic"

    def __init__(self, parameters):
        self.parameters = parameters
        self._git_ownership_reported = set()
        self._git_ownership_safe_directory_reported = set()
        self._git_ownership_repair_attempted = set()

    def parameter(self, key, default):
        return parameter_get(self.parameters, key, default)

    def plugin_home_folder(self):
        return os.path.abspath(self.parameter("HomeFolder", str(os.getcwd()) + os.sep))

    def current_plugin_folder(self):
        return os.path.basename(os.path.normpath(self.plugin_home_folder()))

    def plugins_dir(self):
        return os.path.abspath(os.path.join(self.plugin_home_folder(), ".."))

    def domoticz_dir(self):
        return os.path.abspath(os.path.join(self.plugin_home_folder(), "..", ".."))

    def shared_deps_dir(self):
        return os.path.join(self.plugin_home_folder(), ".shared_deps")

    def templates_dir(self):
        return os.path.join(self.domoticz_dir(), "www", "templates")

    def images_dir(self):
        return os.path.join(self.domoticz_dir(), "www", "images")

    def ui_html_source(self):
        return os.path.join(self.plugin_home_folder(), "pypluginstore.html")

    def ui_html_destination(self):
        return os.path.join(self.templates_dir(), "pypluginstore.html")

    def ui_asset_source(self, asset_name):
        return os.path.join(self.plugin_home_folder(), asset_name)

    def ui_asset_destination(self, asset_name):
        return os.path.join(self.images_dir(), asset_name)

    def requirements_file(self, plugin_key):
        return os.path.join(self.resolve_plugin_dir(plugin_key), "requirements.txt")

    def pending_operations_file(self):
        return os.path.join(self.plugin_home_folder(), "pending_operations.json")

    def restart_log_file(self):
        return os.path.join(self.plugin_home_folder(), "restart_domoticz.log")

    def self_update_log_file(self):
        return os.path.join(self.plugin_home_folder(), "self_update.log")

    def self_update_state_file(self):
        return os.path.join(self.plugin_home_folder(), "self_update_state.json")

    def git_index_lock_file(self, plugin_dir):
        return os.path.join(plugin_dir, ".git", "index.lock")

    def has_git_index_lock(self, plugin_dir):
        return os.path.exists(self.git_index_lock_file(plugin_dir))

    def git_index_lock_message(self, plugin_dir):
        lock_file = self.git_index_lock_file(plugin_dir)
        return (
            "PyPluginStore git index lock exists at " + lock_file
            + "; stop any running git command, then remove the lock file if it is stale and retry self-update."
        )

    def utc_timestamp(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def read_self_update_state(self):
        default_state = {
            "operation": "self_update",
            "phase": "idle",
            "message": "",
            "log_file": self.self_update_log_file(),
        }
        state_file = self.self_update_state_file()
        if not os.path.isfile(state_file):
            return default_state

        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                raise ValueError("state file does not contain a JSON object")
            default_state.update(state)
            default_state["operation"] = "self_update"
            default_state["log_file"] = default_state.get("log_file") or self.self_update_log_file()
            return default_state
        except Exception as e:
            default_state.update({
                "phase": "stale_unknown",
                "message": "Could not read self-update state: " + str(e),
            })
            return default_state

    def write_json_atomic(self, path, data, sort_keys=True):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=sort_keys)
                f.write("\n")
            os.replace(tmp_path, path)
        except Exception:
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise

    def prepare_web_file(self, path):
        """Prepare a temporary web asset before it atomically becomes visible."""
        return None

    def write_web_bytes_atomic(self, path, contents):
        """Replace a web asset without exposing a partially written file."""
        if not isinstance(contents, (bytes, bytearray)):
            raise ValueError("Web asset contents must be bytes.")
        contents = bytes(contents)
        destination_dir = os.path.dirname(path)
        os.makedirs(destination_dir, exist_ok=True)
        file_descriptor, tmp_path = tempfile.mkstemp(
            prefix=".pypluginstore-",
            suffix=".tmp",
            dir=destination_dir,
        )
        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(contents)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            self.prepare_web_file(tmp_path)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def build_self_update_state(self, phase, message="", previous_state=None, **fields):
        now = self.utc_timestamp()
        state = dict(previous_state or {})
        state.setdefault("created_at", now)
        state.update({
            "operation": "self_update",
            "phase": phase,
            "message": message,
            "updated_at": now,
            "log_file": self.self_update_log_file(),
        })
        for key, value in fields.items():
            if value is not None:
                state[key] = value
        return state

    def write_self_update_state(self, phase, message="", previous_state=None, **fields):
        state = self.build_self_update_state(
            phase,
            message,
            previous_state=previous_state,
            **fields,
        )
        self.write_json_atomic(self.self_update_state_file(), state)
        return state

    def append_restart_log(self, message):
        timestamp = datetime.now().isoformat(timespec="seconds")
        try:
            with open(self.restart_log_file(), "a", encoding="utf-8") as restart_log:
                restart_log.write("[{}] {}\n".format(timestamp, message))
            return True
        except Exception as e:
            Domoticz.Error(f"Failed to write Domoticz restart log: {e}")
            return False

    def get_git_env(self):
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def git_result_output(self, result):
        if result is None:
            return ""
        return "\n".join(
            part.strip()
            for part in (getattr(result, "stderr", ""), getattr(result, "stdout", ""))
            if part and part.strip()
        )

    def is_git_dubious_ownership(self, result):
        output = self.git_result_output(result).lower()
        return "detected dubious ownership in repository" in output

    def username_for_uid(self, uid):
        try:
            import pwd
            return pwd.getpwuid(uid).pw_name
        except Exception:
            return ""

    def groupname_for_gid(self, gid):
        try:
            import grp
            return grp.getgrgid(gid).gr_name
        except Exception:
            return ""

    def format_owner(self, uid, gid):
        user = self.username_for_uid(uid)
        group = self.groupname_for_gid(gid)
        numeric = str(uid) + ":" + str(gid)
        if user and group:
            return user + ":" + group + " (" + numeric + ")"
        return numeric

    def path_owner_description(self, path):
        if not path:
            return ""
        try:
            stat_result = os.stat(path)
            return self.format_owner(stat_result.st_uid, stat_result.st_gid)
        except Exception:
            return ""

    def process_owner_description(self):
        if not hasattr(os, "geteuid"):
            return ""
        uid = os.geteuid()
        gid = os.getegid() if hasattr(os, "getegid") else -1
        return self.format_owner(uid, gid)

    def git_ownership_guidance(self, cwd):
        details = []
        expected_owner = self.process_owner_description()
        current_owner = self.path_owner_description(cwd)
        if current_owner:
            details.append("Current owner: " + current_owner + ".")
        if expected_owner:
            details.append("Expected owner: " + expected_owner + " (the Domoticz process user).")
        return " ".join(details)

    def git_ownership_repair_message(self, cwd):
        return (
            "Git refused the plugin repository because file ownership does not match the Domoticz user. "
            "Trying to fix ownership for " + str(cwd) + "."
        )

    def git_ownership_repair_enabled(self):
        value = str(self.parameter("Mode7", "Disabled") or "").strip().lower()
        return value in ("enabled", "true", "yes", "allow")

    def git_ownership_failure_message(self, cwd):
        location = " for " + str(cwd) if cwd else ""
        guidance = self.git_ownership_guidance(cwd)
        guidance = " " + guidance if guidance else ""
        if not self.git_ownership_repair_enabled():
            return (
                "Git refused the plugin repository because file ownership does not match the Domoticz user. "
                "PyPluginStore will not change file ownership automatically" + location + "; "
                "fix the plugin folder ownership manually if Git still fails."
                + guidance
            )
        return (
            "Git refused the plugin repository because file ownership does not match the Domoticz user. "
            "PyPluginStore could not fix ownership" + location + "; fix the plugin folder ownership manually."
            + guidance
        )

    def has_safe_directory_option(self, command):
        return any(str(part).startswith("safe.directory=") for part in command)

    def platform_git_command(self, command):
        return list(command)

    def safe_git_command(self, command, cwd):
        safe_command = self.platform_git_command(command)
        repo_dir = os.path.realpath(os.path.abspath(cwd))
        if len(safe_command) > 0 and safe_command[0] == "git" and not self.has_safe_directory_option(safe_command):
            safe_command.insert(1, "-c")
            safe_command.insert(2, "safe.directory=" + repo_dir)
        return safe_command

    def should_use_safe_git_directory(self, command, cwd):
        return (
            len(command) > 0
            and command[0] == "git"
            and self.is_managed_plugin_repository(cwd)
        )

    def format_command(self, command):
        return " ".join(str(part) for part in command)

    def command_available(self, command):
        return shutil.which(command) is not None

    def command_can_run(self, command, timeout=10):
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_git_once(self, command, cwd, timeout=15):
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                env=self.get_git_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout
            )
        except subprocess.TimeoutExpired:
            Domoticz.Error("Git command timed out in " + str(cwd) + ": " + self.format_command(command))
        except OSError as e:
            Domoticz.Error("Git ErrorNo:" + str(e.errno))
            Domoticz.Error("Git StrError:" + str(e.strerror))
        except Exception as e:
            Domoticz.Error("Git command failed in " + str(cwd) + ": " + str(e))
        return None

    def is_managed_plugin_repository(self, path):
        repo_dir = os.path.realpath(os.path.abspath(path))
        plugins_dir = os.path.realpath(self.plugins_dir())
        try:
            inside_plugins_dir = os.path.commonpath([repo_dir, plugins_dir]) == plugins_dir
        except ValueError:
            inside_plugins_dir = False
        return (
            repo_dir != plugins_dir
            and inside_plugins_dir
            and is_git_checkout(repo_dir)
        )

    def chown_path(self, path, uid, gid):
        stat_result = os.lstat(path)
        target_gid = stat_result.st_gid if gid == -1 else gid
        if stat_result.st_uid == uid and stat_result.st_gid == target_gid:
            return
        try:
            os.chown(path, uid, gid, follow_symlinks=False)
        except TypeError:
            if not os.path.islink(path):
                os.chown(path, uid, gid)

    def repair_git_repository_ownership(self, cwd):
        if not hasattr(os, "chown") or not hasattr(os, "geteuid"):
            return False

        repo_dir = os.path.realpath(os.path.abspath(cwd))
        if not self.is_managed_plugin_repository(repo_dir):
            return False

        uid = os.geteuid()
        gid = os.getegid() if hasattr(os, "getegid") else -1
        try:
            for root, dirs, files in os.walk(repo_dir, topdown=True, followlinks=False):
                self.chown_path(root, uid, gid)
                for name in dirs:
                    self.chown_path(os.path.join(root, name), uid, gid)
                for name in files:
                    self.chown_path(os.path.join(root, name), uid, gid)
            return True
        except Exception as e:
            Domoticz.Debug("Could not fix Git repository ownership for " + repo_dir + ": " + str(e))
            return False

    def handle_git_ownership_failure(self, result, command, cwd, timeout):
        repo_dir = os.path.realpath(os.path.abspath(cwd))

        safe_command = self.safe_git_command(command, cwd)
        if safe_command != list(command):
            if repo_dir not in self._git_ownership_safe_directory_reported:
                Domoticz.Log("Git refused the plugin repository due to file ownership; retrying with safe.directory bypass.")
                self._git_ownership_safe_directory_reported.add(repo_dir)

            retry_result = self._run_git_once(safe_command, cwd, timeout=timeout)
            if retry_result is not None and not self.is_git_dubious_ownership(retry_result):
                return retry_result

        if repo_dir in self._git_ownership_repair_attempted:
            return result

        self._git_ownership_repair_attempted.add(repo_dir)
        if not self.git_ownership_repair_enabled():
            if repo_dir not in self._git_ownership_reported:
                Domoticz.Error(self.git_ownership_failure_message(repo_dir))
                self._git_ownership_reported.add(repo_dir)
            return result

        # Fallback to original chown repair if safe.directory fails
        if repo_dir not in self._git_ownership_reported:
            Domoticz.Error(self.git_ownership_repair_message(repo_dir))
            self._git_ownership_reported.add(repo_dir)

        if not self.repair_git_repository_ownership(repo_dir):
            Domoticz.Error(self.git_ownership_failure_message(repo_dir))
            return result

        Domoticz.Log("Fixed plugin repository ownership; retrying Git command.")
        retry_result = self._run_git_once(command, cwd, timeout=timeout)
        if retry_result is not None and self.is_git_dubious_ownership(retry_result):
            Domoticz.Error(self.git_ownership_failure_message(repo_dir))
        return retry_result

    def run_git(self, command, cwd, timeout=15):
        if self.should_use_safe_git_directory(command, cwd):
            actual_command = self.safe_git_command(command, cwd)
        else:
            actual_command = self.platform_git_command(command)
        result = self._run_git_once(actual_command, cwd, timeout=timeout)
        if result is not None and result.returncode != 0 and self.is_git_dubious_ownership(result):
            return self.handle_git_ownership_failure(result, actual_command, cwd, timeout)
        return result

    def run_git_read_only(self, command, cwd, timeout=15):
        """Run an inspection-only Git command without taking optional locks."""
        actual_command = self.safe_git_command(command, cwd)
        environment = self.get_git_env()
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        try:
            return subprocess.run(
                actual_command,
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            Domoticz.Error(
                "Read-only Git command timed out in "
                + str(cwd)
                + ": "
                + self.format_command(actual_command)
            )
        except OSError as error:
            Domoticz.Error("Git ErrorNo:" + str(error.errno))
            Domoticz.Error("Git StrError:" + str(error.strerror))
        except Exception as error:
            Domoticz.Error(
                "Read-only Git command failed in "
                + str(cwd)
                + ": "
                + str(error)
            )
        return None

    def make_web_readable(self, path):
        return None

    def validate_plugin_key(self, plugin_key):
        plugin_key = str(plugin_key or "").strip()
        if not plugin_key or plugin_key in (".", ".."):
            raise ValueError("Invalid plugin key")
        if plugin_key.startswith(".") or "/" in plugin_key or "\\" in plugin_key:
            raise ValueError("Invalid plugin key")
        if os.path.basename(plugin_key) != plugin_key:
            raise ValueError("Invalid plugin key")
        return plugin_key

    def is_path_inside(self, target_path, base_path):
        target_path = os.path.normcase(os.path.abspath(target_path))
        base_path = os.path.normcase(os.path.abspath(base_path))
        try:
            return os.path.commonpath([target_path, base_path]) == base_path
        except ValueError:
            return False

    def resolve_plugin_dir(self, plugin_key):
        plugin_key = self.validate_plugin_key(plugin_key)
        plugin_dir = os.path.abspath(os.path.join(self.plugins_dir(), plugin_key))
        if not self.is_path_inside(plugin_dir, self.plugins_dir()):
            raise ValueError("Invalid plugin path")
        return plugin_dir

    def restart_command_groups(self):
        return []

    def detached_popen_kwargs(self):
        return {}

    def build_restart_helper(self, command_groups, log_file, startup_delay=2, command_delay=3):
        helper = """
import datetime
import subprocess
import time
import traceback

command_groups = __COMMAND_GROUPS__
log_file = __LOG_FILE__
startup_delay = __STARTUP_DELAY__
command_delay = __COMMAND_DELAY__
failures = []

def write_log(message):
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with open(log_file, "a", encoding="utf-8") as restart_log:
            restart_log.write("[{}] {}\\n".format(timestamp, message))
    except Exception:
        pass

def classify_restart_failure(failed_attempts):
    combined_output = "\\n".join(
        [
            "\\n".join(
                [
                    str(failure.get("stdout", "")),
                    str(failure.get("stderr", "")),
                    str(failure.get("exception", "")),
                ]
            )
            for failure in failed_attempts
        ]
    ).lower()

    sudo_password_markers = (
        "a password is required",
        "password is required",
        "a terminal is required to read the password",
        "wachtwoord is verplicht",
    )
    permission_markers = (
        "access denied",
        "permission denied",
        "not authorized",
        "interactive authentication required",
        "authentication is required",
    )
    service_missing_markers = (
        "unit domoticz.service not found",
        "domoticz.service not found",
        "unrecognized service",
    )
    command_missing_markers = (
        "no such file or directory",
        "command not found",
        "not found",
    )

    if any(marker in combined_output for marker in sudo_password_markers):
        return (
            "Domoticz restart failed: sudo requires an interactive password. "
            "Configure a narrowly scoped NOPASSWD sudoers rule for the exact Domoticz restart command, or restart Domoticz manually."
        )
    if any(marker in combined_output for marker in permission_markers):
        return (
            "Domoticz restart failed: the Domoticz OS user is not allowed to restart domoticz.service. "
            "Grant only the required service-restart permission, or restart Domoticz manually."
        )
    if any(marker in combined_output for marker in service_missing_markers):
        return (
            "Domoticz restart failed: domoticz.service was not found. "
            "Check the Domoticz service name and restart it manually."
        )
    if any(marker in combined_output for marker in command_missing_markers):
        return (
            "Domoticz restart failed: one or more restart commands were not available on this host. "
            "Check the Domoticz service manager and restart it manually."
        )
    return "Domoticz restart failed: all configured restart commands failed. Review the command output above."

write_log("restart helper started")
if startup_delay:
    time.sleep(startup_delay)
for group_index, command_group in enumerate(command_groups, start=1):
    write_log("trying command group {}".format(group_index))
    success = True
    for index, command in enumerate(command_group):
        write_log("running: {}".format(subprocess.list2cmdline(command)))
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20
            )
            write_log("return code: {}".format(result.returncode))
            if result.stdout:
                write_log("stdout: {}".format(result.stdout.strip()))
            if result.stderr:
                write_log("stderr: {}".format(result.stderr.strip()))
            if result.returncode != 0:
                failures.append({
                    "command": subprocess.list2cmdline(command),
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                })
                success = False
                break
            if index < len(command_group) - 1 and command_delay:
                time.sleep(command_delay)
        except Exception as e:
            write_log("exception: {}".format(e))
            write_log(traceback.format_exc().strip())
            failures.append({
                "command": subprocess.list2cmdline(command),
                "exception": str(e),
            })
            success = False
            break
    if success:
        write_log("restart command group completed")
        break
else:
    write_log("all restart command groups failed")
    write_log("failure summary: {}".format(classify_restart_failure(failures)))
"""
        return (
            helper
            .replace("__COMMAND_GROUPS__", repr(command_groups))
            .replace("__LOG_FILE__", repr(log_file))
            .replace("__STARTUP_DELAY__", repr(startup_delay))
            .replace("__COMMAND_DELAY__", repr(command_delay))
        )

    def restart_domoticz(self):
        command_groups = self.restart_command_groups()
        if not command_groups:
            return False, "Domoticz restart is not configured for this platform."

        helper = self.build_restart_helper(command_groups, self.restart_log_file())
        self.append_restart_log("restart requested")
        self.append_restart_log("launching Python restart helper: " + str(sys.executable))
        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        popen_kwargs.update(self.detached_popen_kwargs())

        try:
            subprocess.Popen([sys.executable, "-c", helper], **popen_kwargs)
            return True, "Domoticz restart requested"
        except Exception as e:
            Domoticz.Error(f"Failed to schedule Domoticz restart: {e}")
            return False, str(e)

    def git_failure_message(self, result, fallback, cwd=""):
        if result is None:
            return fallback
        if self.is_git_dubious_ownership(result):
            return self.git_ownership_failure_message(cwd)
        output = (result.stderr or result.stdout or "").strip()
        return output or fallback

    def log_git_result(self, label, result):
        if not label or result is None:
            return
        if result.stdout:
            Domoticz.Debug("Git " + label + " Response:" + result.stdout.strip())
        if result.stderr and not self.is_git_dubious_ownership(result):
            Domoticz.Debug("Git " + label + " Error:" + result.stderr.strip())

    def require_git_success(self, plugin_dir, command, timeout=15, fallback=None, log_label=""):
        result = self.run_git(command, plugin_dir, timeout=timeout)
        self.log_git_result(log_label, result)
        fallback = fallback or "Git command failed: " + self.format_command(command)
        if result is None:
            return result, fallback
        if result.returncode != 0:
            return result, self.git_failure_message(
                result,
                fallback,
                plugin_dir,
            )
        return result, ""

    def validate_self_update_candidate(self, plugin_dir, target_ref):
        required_paths = (
            "plugin.py",
            "plugin_core.py",
            "package_registry.py",
            "package_identity.py",
            "pypluginstore.html",
            "registry.json",
        )
        for candidate_path in required_paths:
            _, message = self.require_git_success(
                plugin_dir,
                ["git", "cat-file", "-e", f"{target_ref}:{candidate_path}"],
                fallback="Self update target is missing " + candidate_path + ".",
            )
            if message:
                return False, message

        for python_path in (
            "plugin.py",
            "plugin_core.py",
            "package_registry.py",
            "package_identity.py",
        ):
            result, message = self.require_git_success(
                plugin_dir,
                ["git", "show", f"{target_ref}:{python_path}"],
                fallback="Could not read " + python_path + " from self update target.",
            )
            if message:
                return False, message
            try:
                compile(result.stdout, python_path, "exec")
            except SyntaxError as e:
                return False, "Self update target has invalid Python syntax in " + python_path + ": " + str(e)

        return True, ""

    def preflight_self_update(self, plugin_dir):
        if not self.command_available("git"):
            return False, "Git is not available, so PyPluginStore cannot self-update.", {}
        if not is_git_checkout(plugin_dir):
            return False, "PyPluginStore is not installed as a git repository.", {}
        if self.has_git_index_lock(plugin_dir):
            return False, self.git_index_lock_message(plugin_dir), {}

        result, message = self.require_git_success(
            plugin_dir,
            ["git", "rev-parse", "--is-inside-work-tree"],
            fallback="Could not verify the PyPluginStore git repository.",
        )
        if message:
            return False, message, {}
        if result.stdout.strip().lower() != "true":
            return False, "PyPluginStore folder is not a git work tree.", {}

        result, message = self.require_git_success(
            plugin_dir,
            ["git", "rev-parse", "--show-toplevel"],
            fallback="Could not verify the PyPluginStore git work tree root.",
        )
        if message:
            return False, message, {}
        repo_root = os.path.normcase(os.path.abspath(result.stdout.strip()))
        expected_root = os.path.normcase(os.path.abspath(plugin_dir))
        if repo_root != expected_root:
            return False, "PyPluginStore self-update must run from the repository root.", {}

        result, message = self.require_git_success(
            plugin_dir,
            ["git", "status", "--porcelain", "--untracked-files=no"],
            fallback="Could not check PyPluginStore working tree status.",
        )
        if message:
            return False, message, {}
        if result.stdout.strip():
            return False, "PyPluginStore has local tracked file changes; self-update refused.", {}

        result, message = self.require_git_success(
            plugin_dir,
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            fallback="PyPluginStore branch has no upstream; self-update refused.",
        )
        if message:
            return False, message, {}
        upstream_ref = result.stdout.strip()
        if not upstream_ref:
            return False, "PyPluginStore branch has no upstream; self-update refused.", {}

        _, message = self.require_git_success(
            plugin_dir,
            ["git", "fetch", "--prune"],
            timeout=60,
            fallback="Could not fetch PyPluginStore updates from the upstream remote.",
        )
        if message:
            return False, message, {}

        current_result, message = self.require_git_success(
            plugin_dir,
            ["git", "rev-parse", "--verify", "HEAD"],
            fallback="Could not verify the current PyPluginStore revision.",
        )
        if message:
            return False, message, {}
        current_commit = current_result.stdout.strip().splitlines()[0] if current_result.stdout.strip() else ""

        target_result, message = self.require_git_success(
            plugin_dir,
            ["git", "rev-parse", "--verify", upstream_ref],
            fallback="Could not verify the PyPluginStore upstream revision.",
        )
        if message:
            return False, message, {}
        target_commit = target_result.stdout.strip().splitlines()[0] if target_result.stdout.strip() else ""

        _, message = self.require_git_success(
            plugin_dir,
            ["git", "merge-base", "--is-ancestor", "HEAD", upstream_ref],
            fallback="PyPluginStore local branch has diverged from upstream; self-update refused.",
        )
        if message:
            return False, message, {}

        result, message = self.require_git_success(
            plugin_dir,
            ["git", "rev-list", "--left-right", "--count", "HEAD..." + upstream_ref],
            fallback="Could not compare PyPluginStore with upstream.",
        )
        if message:
            return False, message, {}
        try:
            ahead, behind = [int(value) for value in result.stdout.split()[:2]]
        except Exception:
            return False, "Could not parse PyPluginStore upstream comparison.", {}
        if ahead:
            return False, "PyPluginStore has local commits; self-update refused.", {}
        if behind == 0:
            return True, "PyPluginStore is already up-to-date.", {
                "already_current": True,
                "upstream_ref": upstream_ref,
                "current_commit": current_commit,
                "target_commit": target_commit,
            }

        valid_candidate, message = self.validate_self_update_candidate(plugin_dir, upstream_ref)
        if not valid_candidate:
            return False, message, {}
        if self.has_git_index_lock(plugin_dir):
            return False, self.git_index_lock_message(plugin_dir), {}

        return True, "Self update pre-flight checks passed.", {
            "already_current": False,
            "upstream_ref": upstream_ref,
            "current_commit": current_commit,
            "target_commit": target_commit,
        }

    def build_self_update_helper(
        self,
        plugin_dir,
        log_file,
        upstream_ref,
        state_file=None,
        job_id="",
        state_template=None,
        startup_delay=SELF_UPDATE_STARTUP_DELAY_SECONDS,
    ):
        state_file = state_file or self.self_update_state_file()
        state_template = dict(state_template or {})
        helper = """
import datetime
import json
import os
import subprocess
import time
import traceback

plugin_dir = __PLUGIN_DIR__
log_file = __LOG_FILE__
upstream_ref = __UPSTREAM_REF__
state_file = __STATE_FILE__
job_id = __JOB_ID__
state_template = __STATE_TEMPLATE__
startup_delay = __STARTUP_DELAY__
safe_directory = os.path.realpath(os.path.abspath(plugin_dir))
index_lock_file = os.path.join(plugin_dir, ".git", "index.lock")

def write_log(message):
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with open(log_file, "a", encoding="utf-8") as update_log:
            update_log.write("[{}] {}\\n".format(timestamp, message))
    except Exception:
        pass

def write_state(phase, message="", **extra):
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = dict(state_template)
    state.setdefault("created_at", timestamp)
    state.update({
        "operation": "self_update",
        "phase": phase,
        "message": message,
        "updated_at": timestamp,
        "log_file": log_file,
        "job_id": job_id,
        "upstream_ref": upstream_ref,
    })
    state.update(extra)
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        tmp_state_file = state_file + ".tmp"
        with open(tmp_state_file, "w", encoding="utf-8") as state_handle:
            json.dump(state, state_handle, indent=2, sort_keys=True)
            state_handle.write("\\n")
        os.replace(tmp_state_file, state_file)
    except Exception as e:
        write_log("failed to write state: {}".format(e))

write_log("self update helper started")
write_state("running", "Self update helper is running.")
if startup_delay:
    time.sleep(startup_delay)

git_control_path = os.path.join(plugin_dir, ".git")
if (
    os.path.islink(git_control_path)
    or not (
        os.path.isdir(git_control_path)
        or os.path.isfile(git_control_path)
    )
):
    write_log("not a git repository: {}".format(plugin_dir))
    write_state("failed", "PyPluginStore is not installed as a git repository.")
    raise SystemExit(1)

env = os.environ.copy()
env["GIT_TERMINAL_PROMPT"] = "0"

def git_command(*args):
    return ["git", "-c", "safe.directory=" + safe_directory] + list(args)

def git_index_lock_message():
    return (
        "PyPluginStore git index lock exists at {}; "
        "stop any running git command, then remove the lock file if it is stale and retry self-update."
    ).format(index_lock_file)

def refuse_git_index_lock():
    if os.path.exists(index_lock_file):
        message = git_index_lock_message()
        write_log(message)
        write_state("failed", message)
        raise SystemExit(1)

def run_command(command, timeout):
    write_log("running: {}".format(subprocess.list2cmdline(command)))
    try:
        result = subprocess.run(
            command,
            cwd=plugin_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=timeout
        )
        write_log("return code: {}".format(result.returncode))
        if result.stdout:
            write_log("stdout: {}".format(result.stdout.strip()))
        if result.stderr:
            write_log("stderr: {}".format(result.stderr.strip()))
        if result.returncode != 0:
            write_log("self update failed")
            write_state(
                "failed",
                "Self update command failed: {}".format(subprocess.list2cmdline(command)),
                return_code=result.returncode
            )
            raise SystemExit(result.returncode)
        return result
    except Exception as e:
        write_log("exception: {}".format(e))
        write_log(traceback.format_exc().strip())
        write_state("failed", "Self update helper exception: {}".format(e))
        raise

refuse_git_index_lock()
status = run_command(git_command("status", "--porcelain", "--untracked-files=no"), 15)
if status.stdout.strip():
    write_log("tracked files changed after pre-flight; self update refused")
    write_state("failed", "Tracked files changed after pre-flight; self update refused.")
    raise SystemExit(1)

run_command(git_command("fetch", "--prune"), 60)
refuse_git_index_lock()
run_command(git_command("merge", "--ff-only", upstream_ref), 120)
head = run_command(git_command("rev-parse", "--short", "HEAD"), 15).stdout.strip()
write_log("self update completed")
write_state(
    "applied_needs_reload",
    "Self update completed. Reload the Plugin Store after Domoticz finishes reloading the plugin.",
    applied_commit=head
)
"""
        return (
            helper
            .replace("__PLUGIN_DIR__", repr(plugin_dir))
            .replace("__LOG_FILE__", repr(log_file))
            .replace("__UPSTREAM_REF__", repr(upstream_ref))
            .replace("__STATE_FILE__", repr(state_file))
            .replace("__JOB_ID__", repr(job_id))
            .replace("__STATE_TEMPLATE__", repr(state_template))
            .replace("__STARTUP_DELAY__", repr(startup_delay))
        )

    def schedule_self_update(self, plugin_dir):
        Domoticz.Log("Starting PyPluginStore self-update pre-flight.")
        preflight_success, preflight_message, preflight_plan = self.preflight_self_update(plugin_dir)
        if not preflight_success or preflight_plan.get("already_current"):
            phase = "confirmed" if preflight_plan.get("already_current") else "preflight_failed"
            state = self.write_self_update_state(
                phase,
                preflight_message,
                job_id=self.utc_timestamp().replace("-", "").replace(":", ""),
                upstream_ref=preflight_plan.get("upstream_ref", ""),
                current_commit=preflight_plan.get("current_commit", ""),
                target_commit=preflight_plan.get("target_commit", ""),
            )
            if preflight_success:
                Domoticz.Log("PyPluginStore self-update pre-flight completed: " + preflight_message)
            else:
                Domoticz.Error("PyPluginStore self-update pre-flight failed: " + preflight_message)
            return preflight_success, preflight_message

        job_id = self.utc_timestamp().replace("-", "").replace(":", "")
        state = self.write_self_update_state(
            "scheduled",
            "Self update helper scheduled.",
            job_id=job_id,
            upstream_ref=preflight_plan["upstream_ref"],
            current_commit=preflight_plan.get("current_commit", ""),
            target_commit=preflight_plan.get("target_commit", ""),
        )
        log_file = self.self_update_log_file()
        Domoticz.Log("PyPluginStore self-update helper scheduled; detailed output: " + log_file)

        helper = self.build_self_update_helper(
            plugin_dir,
            log_file,
            preflight_plan["upstream_ref"],
            self.self_update_state_file(),
            job_id,
            state,
        )
        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        popen_kwargs.update(self.detached_popen_kwargs())

        try:
            subprocess.Popen([sys.executable, "-c", helper], **popen_kwargs)
            return True, "Self update started after pre-flight checks. Reload the Plugin Store after Domoticz finishes reloading the plugin."
        except Exception as e:
            Domoticz.Error(f"Failed to schedule PyPluginStore self update: {e}")
            self.write_self_update_state(
                "failed",
                str(e),
                previous_state=state,
                job_id=job_id,
                upstream_ref=preflight_plan["upstream_ref"],
            )
            return False, str(e)

    def dependency_install_command(self, requirements_file, target_dir):
        if self.command_available("uv") and sys.executable:
            return ["uv", "pip", "install", "--python", sys.executable, "-r", requirements_file, "--target", target_dir]

        if sys.executable and self.command_can_run([sys.executable, "-m", "pip", "--version"]):
            return [sys.executable, "-m", "pip", "install", "-r", requirements_file, "--target", target_dir]

        for command in ("pip3", "pip"):
            if self.command_available(command):
                return [command, "install", "-r", requirements_file, "--target", target_dir]
        return None

    def install_requirements(self, requirements_file, target_dir, plugin_key):
        if not os.path.isfile(requirements_file):
            Domoticz.Log("No requirements.txt found for plugin: " + plugin_key)
            return True, "No requirements.txt found"

        Domoticz.Log("requirements.txt found for plugin: " + plugin_key)
        os.makedirs(target_dir, exist_ok=True)

        install_command = self.dependency_install_command(requirements_file, target_dir)
        if not install_command:
            Domoticz.Log("Neither 'uv' nor a working pip command found. Skipping automatic dependency installation.")
            Domoticz.Log(f"Please install dependencies manually from {requirements_file} into {target_dir}")
            return False, "No Python dependency installer found"

        Domoticz.Log("Installing dependencies using: " + self.format_command(install_command))
        try:
            pr = subprocess.Popen(install_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out, error = pr.communicate()
            if pr.returncode == 0:
                Domoticz.Log("Dependencies installed successfully: " + out.strip())
                return True, ""
            Domoticz.Error("Error installing dependencies: " + error.strip())
            return False, error.strip()
        except Exception as e:
            Domoticz.Error("Error running installation command: " + str(e))
            return False, str(e)

    def is_locked_file_error(self, error):
        return False

    def is_locked_file_message(self, message):
        return False


class LinuxHostRuntime(HostRuntime):
    platform_name = "linux"

    def get_git_env(self):
        env = super().get_git_env()
        env["LANG"] = "en_US.UTF-8"
        env["LC_ALL"] = "en_US.UTF-8"
        return env

    def make_web_readable(self, path):
        try:
            os.chmod(path, 0o644)
        except Exception as e:
            Domoticz.Debug(f"Could not update file permissions for {path}: {e}")

    def prepare_web_file(self, path):
        os.chmod(path, 0o644)

    def restart_command_groups(self):
        return [
            [["systemctl", "restart", "domoticz.service"]],
            [["sudo", "-n", "systemctl", "restart", "domoticz.service"]],
            [["service", "domoticz", "restart"]],
            [["sudo", "-n", "service", "domoticz", "restart"]],
        ]

    def detached_popen_kwargs(self):
        return {"start_new_session": True}


class WindowsHostRuntime(HostRuntime):
    platform_name = "windows"

    def platform_git_command(self, command):
        platform_command = list(command)
        if not platform_command or platform_command[0] != "git":
            return platform_command

        option_index = 1
        long_paths_configured = False
        while (
            option_index + 1 < len(platform_command)
            and platform_command[option_index] == "-c"
        ):
            config_index = option_index + 1
            name, separator, _value = str(
                platform_command[config_index]
            ).partition("=")
            if separator and name.casefold() == "core.longpaths":
                platform_command[config_index] = "core.longpaths=true"
                long_paths_configured = True
            option_index += 2
        if not long_paths_configured:
            platform_command[option_index:option_index] = [
                "-c",
                "core.longpaths=true",
            ]
        return platform_command

    def windows_restart_script_file(self):
        return os.path.join(self.plugin_home_folder(), "restart_domoticz.ps1")

    def windows_restart_command_file(self):
        return os.path.join(self.plugin_home_folder(), "restart_domoticz.cmd")

    def windows_restart_probe_file(self):
        return os.path.join(self.plugin_home_folder(), "restart_domoticz_probe.ps1")

    def windows_restart_task_name(self):
        return r"\PyPluginStore-Domoticz-Restart"

    def schtasks_executable(self):
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidate = os.path.join(system_root, "System32", "schtasks.exe")
        if os.path.isfile(candidate):
            return candidate
        return "schtasks.exe"

    def powershell_executable(self):
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidate = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.isfile(candidate):
            return candidate
        return "powershell.exe"

    def powershell_quote(self, value):
        return "'" + str(value).replace("'", "''") + "'"

    def powershell_encoded_command(self, script):
        return base64.b64encode(script.encode("utf-16le")).decode("ascii")

    def probe_powershell_file_execution(self):
        probe_file = self.windows_restart_probe_file()
        try:
            with open(probe_file, "w", encoding="utf-8", newline="\r\n") as probe_script:
                probe_script.write("Write-Output 'PyPluginStore PowerShell file execution probe'\n")

            result = subprocess.run(
                [
                    self.powershell_executable(),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    probe_file,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            self.append_restart_log("PowerShell .ps1 probe return code: " + str(result.returncode))
            if result.stdout:
                self.append_restart_log("PowerShell .ps1 probe stdout: " + result.stdout.strip())
            if result.stderr:
                self.append_restart_log("PowerShell .ps1 probe stderr: " + result.stderr.strip())
            if result.returncode != 0:
                self.append_restart_log("PowerShell .ps1 execution probe failed")
                if "running scripts is disabled" in str(result.stderr).lower():
                    self.append_restart_log("PowerShell execution policy blocks .ps1 files")
                return False
            return True
        except Exception as e:
            self.append_restart_log("PowerShell .ps1 probe exception: " + str(e))
            return False

    def probe_powershell_encoded_command(self):
        script = (
            "$LogFile = {log_file}\n"
            "$timestamp = (Get-Date).ToString(\"s\")\n"
            "Add-Content -LiteralPath $LogFile -Value \"[$timestamp] PowerShell EncodedCommand probe succeeded\" -Encoding UTF8\n"
            "Write-Output 'PyPluginStore PowerShell EncodedCommand probe'\n"
        ).format(log_file=self.powershell_quote(self.restart_log_file()))

        try:
            result = subprocess.run(
                [
                    self.powershell_executable(),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    self.powershell_encoded_command(script),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            self.append_restart_log("PowerShell EncodedCommand probe return code: " + str(result.returncode))
            if result.stdout:
                self.append_restart_log("PowerShell EncodedCommand probe stdout: " + result.stdout.strip())
            if result.stderr:
                self.append_restart_log("PowerShell EncodedCommand probe stderr: " + result.stderr.strip())
            return result.returncode == 0
        except Exception as e:
            self.append_restart_log("PowerShell EncodedCommand probe exception: " + str(e))
            return False

    def build_windows_restart_script(self):
        script = r"""
$ErrorActionPreference = "Continue"
$LogFile = __LOG_FILE__
$ServiceNames = @(__SERVICE_NAMES__)

function Write-RestartLog {
    param([string]$Message)
    try {
        $timestamp = (Get-Date).ToString("s")
        Add-Content -LiteralPath $LogFile -Value "[$timestamp] $Message" -Encoding UTF8
    } catch {
    }
}

function Write-CommandOutput {
    param($Output)
    if ($null -eq $Output) {
        return
    }
    foreach ($line in $Output) {
        Write-RestartLog ("output: " + [string]$line)
    }
}

function Invoke-ExternalCommand {
    param([string[]]$Command)
    Write-RestartLog ("running: " + ($Command -join " "))
    try {
        $commandArgs = @()
        if ($Command.Count -gt 1) {
            $commandArgs = $Command[1..($Command.Count - 1)]
        }
        $output = & $Command[0] @commandArgs 2>&1
        $exitCode = $LASTEXITCODE
        Write-CommandOutput $output
        Write-RestartLog ("return code: " + $exitCode)
        return ($exitCode -eq 0)
    } catch {
        Write-RestartLog ("exception: " + $_.Exception.Message)
        return $false
    }
}

Write-RestartLog "restart helper started"
Start-Sleep -Seconds 2

foreach ($serviceName in $ServiceNames) {
    Write-RestartLog ("running: Restart-Service -Name " + $serviceName + " -Force")
    try {
        $output = Restart-Service -Name $serviceName -Force -ErrorAction Stop 2>&1
        Write-CommandOutput $output
        Write-RestartLog ("Restart-Service completed for " + $serviceName)
        exit 0
    } catch {
        Write-RestartLog ("exception: " + $_.Exception.Message)
    }
}

foreach ($serviceName in $ServiceNames) {
    $stopOk = Invoke-ExternalCommand @("sc.exe", "stop", $serviceName)
    Start-Sleep -Seconds 3
    $startOk = Invoke-ExternalCommand @("sc.exe", "start", $serviceName)
    if ($stopOk -and $startOk) {
        Write-RestartLog ("sc stop/start completed for " + $serviceName)
        exit 0
    }
}

Write-RestartLog "all restart command groups failed"
exit 1
"""
        service_names = [self.powershell_quote("Domoticz"), self.powershell_quote("domoticz")]
        return (
            script
            .replace("__LOG_FILE__", self.powershell_quote(self.restart_log_file()))
            .replace("__SERVICE_NAMES__", ", ".join(service_names))
        )

    def build_windows_restart_command(self, script_file):
        powershell_command = (
            "$ErrorActionPreference = 'Stop'; "
            "$LogFile = {log_file}; "
            "function Write-RestartLog {{ "
            "param([string]$Message) "
            "try {{ "
            "$timestamp = (Get-Date).ToString('s'); "
            "Add-Content -LiteralPath $LogFile -Value ('[{{0}}] {{1}}' -f $timestamp, $Message) -Encoding UTF8 "
            "}} catch {{}} "
            "}}; "
            "try {{ "
            "Write-RestartLog 'scheduled task helper started'; "
            "$script = [System.IO.File]::ReadAllText({script_file}); "
            "Invoke-Expression $script; "
            "exit $LASTEXITCODE "
            "}} catch {{ "
            "Write-RestartLog ('scheduled task helper exception: ' + $_.Exception.Message); "
            "exit 1 "
            "}}"
        ).format(
            log_file=self.powershell_quote(self.restart_log_file()),
            script_file=self.powershell_quote(script_file),
        )
        return '@echo off\r\n"{}" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "{}"\r\n'.format(
            self.powershell_executable(),
            powershell_command,
        )

    def run_schtasks(self, args, timeout=20):
        command = [self.schtasks_executable()] + args
        self.append_restart_log("running: " + subprocess.list2cmdline(command))
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            self.append_restart_log("return code: " + str(result.returncode))
            if result.stdout:
                self.append_restart_log("stdout: " + result.stdout.strip())
            if result.stderr:
                self.append_restart_log("stderr: " + result.stderr.strip())
            return result.returncode == 0
        except Exception as e:
            self.append_restart_log("exception: " + str(e))
            return False

    def schedule_windows_restart_task(self, command_file):
        task_time = (datetime.now() + timedelta(minutes=1)).strftime("%H:%M")
        task_action = 'cmd.exe /d /c ""{}""'.format(command_file)
        task_name = self.windows_restart_task_name()
        if not self.run_schtasks([
            "/Create",
            "/TN", task_name,
            "/SC", "ONCE",
            "/ST", task_time,
            "/TR", task_action,
            "/RU", "SYSTEM",
            "/RL", "HIGHEST",
            "/F",
        ]):
            return False
        return self.run_schtasks(["/Run", "/TN", task_name])

    def restart_domoticz(self):
        script_file = self.windows_restart_script_file()
        command_file = self.windows_restart_command_file()
        try:
            self.append_restart_log("restart requested")
            script = self.build_windows_restart_script()
            with open(script_file, "w", encoding="utf-8", newline="\r\n") as restart_script:
                restart_script.write(script)
            with open(command_file, "w", encoding="utf-8", newline="\r\n") as restart_command:
                restart_command.write(self.build_windows_restart_command(script_file))

            self.probe_powershell_file_execution()
            if not self.probe_powershell_encoded_command():
                return False, "PowerShell EncodedCommand probe failed. See restart_domoticz.log."

            self.append_restart_log("launching Windows scheduled restart task: " + command_file)
            if not self.schedule_windows_restart_task(command_file):
                return False, "Failed to schedule Windows restart task. See restart_domoticz.log."
            return True, "Domoticz restart requested"
        except Exception as e:
            self.append_restart_log("failed to schedule restart: " + str(e))
            Domoticz.Error(f"Failed to schedule Domoticz restart: {e}")
            return False, str(e)

    def restart_command_groups(self):
        return [
            [[
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "Restart-Service -Name 'Domoticz' -Force -ErrorAction Stop",
            ]],
            [["sc", "stop", "Domoticz"], ["sc", "start", "Domoticz"]],
            [["sc", "stop", "domoticz"], ["sc", "start", "domoticz"]],
        ]

    def detached_popen_kwargs(self):
        creationflags = 0
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        if creationflags:
            return {"creationflags": creationflags}
        return {}

    def is_locked_file_error(self, error):
        winerror = getattr(error, "winerror", None)
        if winerror in (5, 32, 33):
            return True
        return isinstance(error, PermissionError)

    def is_locked_file_message(self, message):
        message = str(message or "").lower()
        locked_markers = (
            "access is denied",
            "being used by another process",
            "could not unlink",
            "unable to unlink",
            "permission denied",
        )
        return any(marker in message for marker in locked_markers)


def make_host_runtime(parameters):
    if platform.system() == "Windows":
        return WindowsHostRuntime(parameters)
    return LinuxHostRuntime(parameters)


RELEASE_INDEX_SCHEMA_VERSION = 2
LEGACY_RELEASE_INDEX_SCHEMA_VERSION = 1
RELEASE_METADATA_STATE_SCHEMA_VERSION = 1
DELIVERY_POLICY_SCHEMA_VERSION = 1
RELEASE_PROVIDERS = {
    "codeberg",
    "forgejo",
    "generic",
    "gitea",
    "github",
    "gitlab",
}
RELEASE_ARTIFACT_KINDS = {
    "asset_zip",
    "generic_zip",
    "source_zip",
}
RELEASE_POLICY_ARTIFACTS = {"asset_zip", "source_zip"}
RELEASE_ARTIFACT_PROVENANCE = {
    "attached_asset",
    "forge_release_asset",
    "forge_source_archive",
    "generic_manifest",
    "release_asset",
}
RELEASE_MIGRATION_MODES = {"automatic", "blocked", "manual"}
RELEASE_MIGRATION_EVIDENCE = {
    "commit_source_archive",
    "generic_manifest",
    "source_equivalent_asset",
    "unverified_asset",
}
AUTOMATIC_RELEASE_MIGRATION_EVIDENCE = {
    "commit_source_archive",
    "source_equivalent_asset",
}
RELEASE_PREFERRED_MODES = {"git", "release", "release_if_indexed"}
LOWER_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
RELEASE_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RELEASE_SECRET_REQUEST_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "cookie2",
    "private-token",
}
RELEASE_HEADER_NAME_PATTERN = re.compile(
    r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$"
)
RELEASE_CONTENT_LENGTH_PATTERN = re.compile(r"^[0-9]+$")
RELEASE_INVALID_PERCENT_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
RELEASE_HOST_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
RELEASE_HTTP_CHUNK_SIZE = 64 * 1024
RELEASE_MAX_DNS_ANSWERS = 64


def _require_document(value, label, required_keys, optional_keys=()):
    """Validate a versioned metadata object and return it unchanged."""
    if not isinstance(value, dict):
        raise ValueError(label + " must be an object.")

    required = set(required_keys)
    allowed = required | set(optional_keys)
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ValueError(label + " is missing fields: " + ", ".join(missing))
    if unknown:
        raise ValueError(label + " has unknown fields: " + ", ".join(unknown))
    return value


def _require_nonempty_string(value, label):
    """Return a non-empty metadata string without silently coercing types."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(label + " must be a non-empty string.")
    if CONTROL_CHARACTER_PATTERN.search(value):
        raise ValueError(label + " contains control characters.")
    return value


def _require_positive_integer(value, label):
    """Return a positive integer, rejecting booleans and numeric strings."""
    if type(value) is not int or value <= 0:
        raise ValueError(label + " must be a positive integer.")
    return value


def _require_boolean(value, label):
    """Return a real JSON boolean."""
    if type(value) is not bool:
        raise ValueError(label + " must be a boolean.")
    return value


def _require_sha256(value, label):
    """Return a canonical lowercase SHA-256 digest."""
    value = _require_nonempty_string(value, label)
    if not LOWER_SHA256_PATTERN.fullmatch(value):
        raise ValueError(label + " must be a lowercase SHA-256 digest.")
    return value


def _require_git_commit(value, label, required=True):
    """Return a full lowercase Git object identifier."""
    if value in (None, "") and not required:
        return ""
    value = _require_nonempty_string(value, label)
    if not GIT_COMMIT_PATTERN.fullmatch(value):
        raise ValueError(label + " must be a full lowercase Git commit ID.")
    return value


def _parse_utc_timestamp(value, label):
    """Parse the canonical UTC timestamp format used by release metadata."""
    value = _require_nonempty_string(value, label)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(label + " must be an ISO 8601 UTC timestamp.") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError(label + " must be a canonical UTC timestamp.")
    return parsed.replace(tzinfo=timezone.utc)


def _normalize_relative_metadata_path(value, label, allow_root=True):
    """Normalize a safe POSIX path used in reviewed release metadata."""
    value = _require_nonempty_string(value, label)
    if value == "." and allow_root:
        return value
    if (
        value.startswith(("/", "\\"))
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ValueError(label + " must be a relative POSIX path.")

    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(label + " must be a normalized relative path.")
    return "/".join(parts)


def _require_https_url(value, label):
    """Validate a public HTTPS metadata or artifact URL."""
    value = _require_nonempty_string(value, label)
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(label + " is not a valid URL.") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(label + " must be a public HTTPS URL.")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(label + " has an invalid port.")
    return value


def _repository_host(parsed_url):
    """Return a lowercase host and optional explicit port from a URL."""
    hostname = str(parsed_url.hostname or "").lower()
    if not hostname:
        return ""
    try:
        port = parsed_url.port
    except ValueError:
        return ""
    return hostname + ((":" + str(port)) if port is not None else "")


def normalize_repository_identity(source, repository=""):
    """Return the forge-neutral host/path identity for a repository."""
    source = str(source or "").strip().rstrip("/")
    repository = str(repository or "").strip().strip("/")
    if not source:
        return ""

    host = ""
    path = ""
    if re.match(r"^[^/@\s:]+@[^/\s:]+:.+$", source):
        user_host, path = source.split(":", 1)
        host = user_host.split("@", 1)[-1].lower()
    else:
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme:
            if parsed.scheme not in ("http", "https", "ssh"):
                return ""
            host = _repository_host(parsed)
            path = parsed.path
        else:
            shorthand = urllib.parse.urlparse("//" + source)
            first_part = source.split("/", 1)[0]
            if shorthand.hostname and ("." in first_part or ":" in first_part):
                host = _repository_host(shorthand)
                path = shorthand.path
            else:
                host = DEFAULT_GIT_HOST
                path = source

    if not host:
        return ""
    path_parts = []
    for part in path.split("/"):
        part = urllib.parse.unquote(part.strip())
        if not part:
            continue
        if (
            CONTROL_CHARACTER_PATTERN.search(part)
            or "/" in part
            or "\\" in part
            or part in (".", "..")
        ):
            return ""
        if part in REPOSITORY_PATH_STOP_PARTS:
            break
        path_parts.append(part)
    if repository:
        if "/" in repository or "\\" in repository:
            return ""
        path_parts.append(repository)
    if path_parts and path_parts[-1].endswith(".git"):
        path_parts[-1] = path_parts[-1][:-4]
    if len(path_parts) < 2 or any(not part for part in path_parts):
        return ""
    return (host + "/" + "/".join(path_parts)).lower()


def _registry_entry_identity(data):
    """Extract a normalized repository identity from a registry entry."""
    if isinstance(data, list) and len(data) >= 2:
        return normalize_repository_identity(data[0], data[1])
    if isinstance(data, dict):
        return normalize_repository_identity(
            data.get("author", data.get("owner", "")),
            data.get("repository", data.get("repo", "")),
        )
    return ""


def _load_json_object(contents, label):
    """Load UTF-8 JSON while rejecting duplicate object keys."""
    if not isinstance(contents, (bytes, bytearray)):
        raise ValueError(label + " must be bytes.")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(label + " contains duplicate key " + str(key) + ".")
            result[key] = value
        return result

    try:
        document = json.loads(
            bytes(contents).decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(label + " is not valid UTF-8 JSON.") from error
    if not isinstance(document, dict):
        raise ValueError(label + " must contain a JSON object.")
    return document


@dataclass
class ReleasePolicy:
    """Reviewed provider discovery policy from a registry entry."""

    provider: str
    channel: str
    tag_pattern: str
    artifact: str
    source_path: str
    mutable_paths: list
    api_base: str = ""
    web_base: str = ""
    release_page_size: int = 0
    manifest_url: str = ""
    asset_name: str = ""
    asset_pattern: str = ""
    allowed_origins: list = None

    @classmethod
    def from_document(cls, document, repository_identity=""):
        """Parse and validate the release portion of a delivery policy."""
        document = _require_document(
            document,
            "delivery.release",
            ("provider",),
            (
                "api_base",
                "allowed_origins",
                "artifact",
                "asset_name",
                "asset_pattern",
                "channel",
                "manifest_url",
                "mutable_paths",
                "release_page_size",
                "source_path",
                "tag_pattern",
                "web_base",
            ),
        )
        provider = _require_nonempty_string(
            document["provider"], "delivery.release.provider"
        ).lower()
        if provider not in RELEASE_PROVIDERS:
            raise ValueError("delivery.release.provider is not supported.")

        identity_host = str(repository_identity or "").split("/", 1)[0]
        expected_providers = {
            "github.com": {"github"},
            "gitlab.com": {"gitlab"},
            "codeberg.org": {"codeberg", "forgejo"},
        }.get(identity_host)
        if (
            expected_providers
            and provider not in expected_providers | {"generic"}
        ):
            raise ValueError("Release provider does not match registry host.")
        if (
            identity_host
            and identity_host not in ("github.com", "gitlab.com", "codeberg.org")
            and provider in ("github", "gitlab")
        ):
            raise ValueError(
                "Custom GitHub and GitLab release hosts are not supported."
            )

        channel = _require_nonempty_string(
            document.get("channel", "stable"), "delivery.release.channel"
        )
        tag_pattern = document.get("tag_pattern", "")
        if tag_pattern:
            tag_pattern = _require_nonempty_string(
                tag_pattern, "delivery.release.tag_pattern"
            )
            try:
                re.compile(tag_pattern)
            except re.error as error:
                raise ValueError(
                    "delivery.release.tag_pattern is not a valid regular expression."
                ) from error

        artifact = _require_nonempty_string(
            document.get("artifact", "source_zip"),
            "delivery.release.artifact",
        )
        if artifact not in RELEASE_POLICY_ARTIFACTS:
            raise ValueError("delivery.release.artifact is not supported.")
        source_path = _normalize_relative_metadata_path(
            document.get("source_path", "."),
            "delivery.release.source_path",
        )

        mutable_paths_document = document.get("mutable_paths", [])
        if not isinstance(mutable_paths_document, list):
            raise ValueError("delivery.release.mutable_paths must be a list.")
        mutable_paths = []
        for value in mutable_paths_document:
            path = _normalize_relative_metadata_path(
                value, "delivery.release.mutable_paths entry", allow_root=False
            )
            first_part = path.split("/", 1)[0].lower()
            if first_part in (".git", ".pypluginstore") or path.lower() in (
                ".pypluginstore.json",
                "plugin.py",
            ):
                raise ValueError("delivery.release.mutable_paths contains a reserved path.")
            if path in mutable_paths:
                raise ValueError("delivery.release.mutable_paths contains a duplicate.")
            mutable_paths.append(path)

        api_base = document.get("api_base", "")
        if api_base:
            api_base = _require_https_url(api_base, "delivery.release.api_base")
            if urllib.parse.urlparse(api_base).query:
                raise ValueError(
                    "delivery.release.api_base must not contain a query."
                )
            api_base = api_base.rstrip("/")
        web_base = document.get("web_base", "")
        if web_base:
            web_base = _require_https_url(web_base, "delivery.release.web_base")
            if urllib.parse.urlparse(web_base).query:
                raise ValueError(
                    "delivery.release.web_base must not contain a query."
                )
            web_base = web_base.rstrip("/")
        page_size_present = "release_page_size" in document
        release_page_size = document.get("release_page_size", 0)
        if page_size_present and (
            type(release_page_size) is not int
            or not 1 <= release_page_size <= 100
        ):
            raise ValueError(
                "delivery.release.release_page_size must be between 1 and 100."
            )

        if provider in ("codeberg", "forgejo", "gitea"):
            if not identity_host:
                raise ValueError(
                    "Forgejo and Gitea policies require a valid repository identity."
                )
            codeberg_defaults = (
                provider in ("codeberg", "forgejo")
                and identity_host == "codeberg.org"
            )
            if not codeberg_defaults and (
                not api_base or not web_base or not page_size_present
            ):
                raise ValueError(
                    "Custom Forgejo and Gitea policies require api_base, "
                    "web_base, and release_page_size."
                )
            if web_base and identity_host:
                web_host = _repository_host(urllib.parse.urlparse(web_base))
                if web_host != identity_host:
                    raise ValueError(
                        "delivery.release.web_base does not match registry host."
                    )
        elif api_base or web_base or page_size_present:
            raise ValueError(
                "Host capability fields are only valid for Forgejo and Gitea."
            )
        manifest_url = document.get("manifest_url", "")
        if manifest_url:
            manifest_url = _require_https_url(
                manifest_url, "delivery.release.manifest_url"
            )
        if provider == "generic" and not manifest_url:
            raise ValueError("Generic release policy requires manifest_url.")
        if provider != "generic" and manifest_url:
            raise ValueError("manifest_url is only valid for the generic provider.")

        allowed_origins_document = document.get("allowed_origins", [])
        if not isinstance(allowed_origins_document, list):
            raise ValueError("delivery.release.allowed_origins must be a list.")
        allowed_origins = []
        for origin in allowed_origins_document:
            origin = _require_https_url(
                origin, "delivery.release.allowed_origins entry"
            )
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.path not in ("", "/") or parsed_origin.query:
                raise ValueError(
                    "delivery.release.allowed_origins entries must be origins."
                )
            normalized_origin = (
                "https://" + _repository_host(parsed_origin)
            )
            if normalized_origin in allowed_origins:
                raise ValueError(
                    "delivery.release.allowed_origins contains a duplicate."
                )
            allowed_origins.append(normalized_origin)

        asset_name = document.get("asset_name", "")
        if asset_name:
            asset_name = _require_nonempty_string(
                asset_name, "delivery.release.asset_name"
            )
        asset_pattern = document.get("asset_pattern", "")
        if asset_pattern:
            asset_pattern = _require_nonempty_string(
                asset_pattern, "delivery.release.asset_pattern"
            )
            try:
                re.compile(asset_pattern)
            except re.error as error:
                raise ValueError(
                    "delivery.release.asset_pattern is not a valid regular expression."
                ) from error
        if asset_name and asset_pattern:
            raise ValueError("Choose either asset_name or asset_pattern, not both.")
        if artifact == "source_zip" and (asset_name or asset_pattern):
            raise ValueError("Source ZIP policies cannot select a release asset.")

        return cls(
            provider=provider,
            channel=channel,
            tag_pattern=tag_pattern,
            artifact=artifact,
            source_path=source_path,
            mutable_paths=mutable_paths,
            api_base=api_base,
            web_base=web_base,
            release_page_size=release_page_size,
            manifest_url=manifest_url,
            asset_name=asset_name,
            asset_pattern=asset_pattern,
            allowed_origins=allowed_origins,
        )


@dataclass
class DeliveryPolicy:
    """Backward-compatible release/Git selection policy."""

    schema_version: int
    preferred: str
    git_supported: bool
    release: object = None

    @classmethod
    def implicit(cls):
        """Return the policy assigned to a legacy registry list entry."""
        return cls(
            schema_version=DELIVERY_POLICY_SCHEMA_VERSION,
            preferred="release_if_indexed",
            git_supported=True,
            release=None,
        )

    @classmethod
    def git_only(cls):
        """Return the policy for local-registry entries in unsigned v1."""
        return cls(
            schema_version=DELIVERY_POLICY_SCHEMA_VERSION,
            preferred="git",
            git_supported=True,
            release=None,
        )

    @classmethod
    def from_document(cls, document, repository_identity=""):
        """Parse a versioned registry delivery policy."""
        document = _require_document(
            document,
            "delivery",
            ("schema_version", "preferred", "git_supported"),
            ("release",),
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != DELIVERY_POLICY_SCHEMA_VERSION
        ):
            raise ValueError("delivery.schema_version is not supported.")
        preferred = _require_nonempty_string(
            document["preferred"], "delivery.preferred"
        )
        if preferred not in RELEASE_PREFERRED_MODES:
            raise ValueError("delivery.preferred is not supported.")
        git_supported = _require_boolean(
            document["git_supported"], "delivery.git_supported"
        )
        release_document = document.get("release")
        release = (
            ReleasePolicy.from_document(release_document, repository_identity)
            if release_document is not None
            else None
        )
        if preferred == "release" and release is None:
            raise ValueError("Release delivery requires delivery.release.")
        if preferred == "git" and not git_supported:
            raise ValueError("Git delivery requires git_supported.")
        return cls(
            schema_version=DELIVERY_POLICY_SCHEMA_VERSION,
            preferred=preferred,
            git_supported=git_supported,
            release=release,
        )


@dataclass
class ReleaseArtifact:
    """Provider-neutral, checksum-pinned ZIP artifact metadata."""

    kind: str
    provenance: str
    migration_mode: str
    migration_evidence: str
    url: str
    sha256: str
    size: int
    tree_sha256: str
    root_prefix: str
    source_path: str

    @classmethod
    def from_document(cls, document):
        """Parse a strict schema-v2 release artifact descriptor."""
        document = _require_document(
            document,
            "release artifact",
            (
                "kind",
                "provenance",
                "migration",
                "url",
                "sha256",
                "size",
                "tree_sha256",
                "root_prefix",
                "source_path",
            ),
        )
        migration = _require_document(
            document["migration"],
            "artifact.migration",
            ("mode", "evidence"),
        )
        return cls._from_document(document, migration)

    @classmethod
    def from_legacy_document(cls, document):
        """Normalize one explicit schema-v1 migration eligibility flag."""
        document = _require_document(
            document,
            "legacy release artifact",
            (
                "kind",
                "provenance",
                "migration_eligible",
                "url",
                "sha256",
                "size",
                "tree_sha256",
                "root_prefix",
                "source_path",
            ),
        )
        eligible = _require_boolean(
            document["migration_eligible"],
            "artifact.migration_eligible",
        )
        evidence = (
            "commit_source_archive"
            if eligible
            else (
                "generic_manifest"
                if document.get("provenance") == "generic_manifest"
                else "unverified_asset"
            )
        )
        return cls._from_document(
            document,
            {
                "mode": "automatic" if eligible else "manual",
                "evidence": evidence,
            },
        )

    @classmethod
    def _from_document(cls, document, migration):
        kind = _require_nonempty_string(document["kind"], "artifact.kind")
        if kind not in RELEASE_ARTIFACT_KINDS:
            raise ValueError("artifact.kind is not supported.")
        provenance = _require_nonempty_string(
            document["provenance"], "artifact.provenance"
        )
        if provenance not in RELEASE_ARTIFACT_PROVENANCE:
            raise ValueError("artifact.provenance is not supported.")

        migration_mode = _require_nonempty_string(
            migration["mode"], "artifact.migration.mode"
        )
        migration_evidence = _require_nonempty_string(
            migration["evidence"], "artifact.migration.evidence"
        )
        if migration_mode not in RELEASE_MIGRATION_MODES:
            raise ValueError("artifact.migration.mode is not supported.")
        if migration_evidence not in RELEASE_MIGRATION_EVIDENCE:
            raise ValueError("artifact.migration.evidence is not supported.")
        automatic_evidence = (
            migration_evidence in AUTOMATIC_RELEASE_MIGRATION_EVIDENCE
        )
        if migration_mode == "automatic" and not automatic_evidence:
            raise ValueError("Automatic migration evidence is insufficient.")
        if migration_mode == "manual" and automatic_evidence:
            raise ValueError("Manual migration evidence is contradictory.")

        root_prefix = _require_nonempty_string(
            document["root_prefix"], "artifact.root_prefix"
        )
        if root_prefix != ".":
            root_prefix = _normalize_relative_metadata_path(
                root_prefix, "artifact.root_prefix", allow_root=False
            )
            if "/" in root_prefix:
                raise ValueError("artifact.root_prefix must identify one wrapper.")
        elif kind == "source_zip":
            raise ValueError("Forge source ZIP artifacts require one wrapper.")

        return cls(
            kind=kind,
            provenance=provenance,
            migration_mode=migration_mode,
            migration_evidence=migration_evidence,
            url=_require_https_url(document["url"], "artifact.url"),
            sha256=_require_sha256(document["sha256"], "artifact.sha256"),
            size=_require_positive_integer(document["size"], "artifact.size"),
            tree_sha256=_require_sha256(
                document["tree_sha256"], "artifact.tree_sha256"
            ),
            root_prefix=root_prefix,
            source_path=_normalize_relative_metadata_path(
                document["source_path"], "artifact.source_path"
            ),
        )

    @property
    def migration_eligible(self):
        """Compatibility view for callers that mean automatic migration."""
        return self.migration_mode == "automatic"

    @property
    def migration(self):
        return {
            "mode": self.migration_mode,
            "evidence": self.migration_evidence,
        }


@dataclass(frozen=True)
class CertifiedReleaseIdentity:
    """Identity observed from the exact plugin.py certified by CI."""

    domoticz_key: str
    plugin_py_sha256: str

    @classmethod
    def from_document(cls, document):
        document = _require_document(
            document,
            "certified identity",
            ("domoticz_key", "plugin_py_sha256"),
        )
        return cls(
            domoticz_key=_require_nonempty_string(
                document["domoticz_key"], "certified_identity.domoticz_key"
            ),
            plugin_py_sha256=_require_sha256(
                document["plugin_py_sha256"],
                "certified_identity.plugin_py_sha256",
            ),
        )


@dataclass
class ReleaseDescriptor:
    """One accepted release target independent of its source forge."""

    revision: int
    release_id: str
    supersedes: list
    provider: str
    repository_identity: str
    version: str
    tag: str
    released_at: str
    commit: str
    artifact: ReleaseArtifact
    source_revision: str = ""
    package_id: str = ""
    certified_identity: object = None

    @classmethod
    def from_document(cls, document):
        """Parse a strict schema-v2 release descriptor."""
        return cls._from_document(document, ReleaseArtifact.from_document)

    @classmethod
    def from_legacy_document(cls, document):
        """Normalize one explicit schema-v1 release descriptor."""
        return cls._from_document(
            document,
            ReleaseArtifact.from_legacy_document,
        )

    @classmethod
    def _from_document(cls, document, artifact_parser):
        document = _require_document(
            document,
            "release descriptor",
            (
                "revision",
                "release_id",
                "supersedes",
                "provider",
                "repository_identity",
                "version",
                "tag",
                "released_at",
                "artifact",
            ),
            ("commit", "source_revision"),
        )
        provider = _require_nonempty_string(
            document["provider"], "release.provider"
        ).lower()
        if provider not in RELEASE_PROVIDERS:
            raise ValueError("release.provider is not supported.")

        repository_identity = normalize_repository_identity(
            document["repository_identity"]
        )
        if not repository_identity:
            raise ValueError("release.repository_identity is invalid.")

        release_id = _require_nonempty_string(
            document["release_id"], "release.release_id"
        )
        supersedes_document = document["supersedes"]
        if not isinstance(supersedes_document, list):
            raise ValueError("release.supersedes must be a list.")
        supersedes = []
        for value in supersedes_document:
            predecessor = _require_nonempty_string(
                value, "release.supersedes entry"
            )
            if predecessor == release_id or predecessor in supersedes:
                raise ValueError("release.supersedes contains invalid lineage.")
            supersedes.append(predecessor)

        source_revision = document.get("source_revision", "")
        if source_revision:
            source_revision = _require_nonempty_string(
                source_revision, "release.source_revision"
            )
        if provider == "generic" and not source_revision:
            raise ValueError("Generic releases require source_revision.")
        commit = _require_git_commit(
            document.get("commit", ""),
            "release.commit",
            required=provider != "generic",
        )
        artifact = artifact_parser(document["artifact"])
        if not commit and artifact.migration_mode == "automatic":
            raise ValueError("A release without a commit cannot be migration eligible.")

        released_at = _require_nonempty_string(
            document["released_at"], "release.released_at"
        )
        _parse_utc_timestamp(released_at, "release.released_at")
        return cls(
            revision=_require_positive_integer(
                document["revision"], "release.revision"
            ),
            release_id=release_id,
            supersedes=supersedes,
            provider=provider,
            repository_identity=repository_identity,
            version=_require_nonempty_string(
                document["version"], "release.version"
            ),
            tag=_require_nonempty_string(document["tag"], "release.tag"),
            released_at=released_at,
            commit=commit,
            artifact=artifact,
            source_revision=source_revision,
        )

    @classmethod
    def from_release_record(cls, document):
        """Parse one strict schema-v2 release array record."""
        document = _require_document(
            document,
            "release record",
            (
                "package_id",
                "certified_identity",
                "revision",
                "release_id",
                "supersedes",
                "provider",
                "repository_identity",
                "version",
                "tag",
                "released_at",
                "artifact",
            ),
            ("commit", "source_revision"),
        )
        descriptor_document = dict(document)
        package_id = _require_nonempty_string(
            descriptor_document.pop("package_id"), "release.package_id"
        )
        certified_identity = CertifiedReleaseIdentity.from_document(
            descriptor_document.pop("certified_identity")
        )
        descriptor = cls.from_document(descriptor_document)
        descriptor.package_id = package_id
        descriptor.certified_identity = certified_identity
        return descriptor

    @property
    def domoticz_key(self):
        identity = self.certified_identity
        return identity.domoticz_key if identity is not None else ""

    @property
    def plugin_py_sha256(self):
        identity = self.certified_identity
        return identity.plugin_py_sha256 if identity is not None else ""

    def same_accepted_target(self, other):
        """Return whether an equal revision still names the accepted source."""
        if not isinstance(other, ReleaseDescriptor):
            return False
        common_fields_match = (
            self.revision == other.revision
            and self.release_id == other.release_id
            and self.supersedes == other.supersedes
            and self.provider == other.provider
            and self.repository_identity == other.repository_identity
            and self.version == other.version
            and self.tag == other.tag
            and self.released_at == other.released_at
            and self.commit == other.commit
            and self.source_revision == other.source_revision
            and self.artifact.kind == other.artifact.kind
            and self.artifact.provenance == other.artifact.provenance
            and self.artifact.migration_mode
            == other.artifact.migration_mode
            and self.artifact.migration_evidence
            == other.artifact.migration_evidence
            and self.artifact.tree_sha256 == other.artifact.tree_sha256
            and self.artifact.source_path == other.artifact.source_path
            and (not other.package_id or self.package_id == other.package_id)
            and (
                other.certified_identity is None
                or self.certified_identity == other.certified_identity
            )
        )
        if not common_fields_match:
            return False
        if self.artifact.provenance == "forge_source_archive":
            return True
        return (
            self.artifact.url == other.artifact.url
            and self.artifact.sha256 == other.artifact.sha256
            and self.artifact.size == other.artifact.size
            and self.artifact.root_prefix == other.artifact.root_prefix
        )


@dataclass
class ReleaseTombstone:
    """Reviewed release de-certification metadata."""

    repository_identity: str
    last_revision: int
    release_id: str
    reason: str
    removed_at: str
    package_id: str = ""

    @classmethod
    def from_document(cls, document):
        """Parse a de-certification tombstone."""
        document = _require_document(
            document,
            "release tombstone",
            (
                "repository_identity",
                "last_revision",
                "release_id",
                "reason",
                "removed_at",
            ),
        )
        repository_identity = normalize_repository_identity(
            document["repository_identity"]
        )
        if not repository_identity:
            raise ValueError("tombstone.repository_identity is invalid.")
        removed_at = _require_nonempty_string(
            document["removed_at"], "tombstone.removed_at"
        )
        _parse_utc_timestamp(removed_at, "tombstone.removed_at")
        return cls(
            repository_identity=repository_identity,
            last_revision=_require_positive_integer(
                document["last_revision"], "tombstone.last_revision"
            ),
            release_id=_require_nonempty_string(
                document["release_id"], "tombstone.release_id"
            ),
            reason=_require_nonempty_string(document["reason"], "tombstone.reason"),
            removed_at=removed_at,
        )

    @classmethod
    def from_tombstone_record(cls, document):
        """Parse one strict schema-v2 tombstone array record."""
        document = _require_document(
            document,
            "release tombstone record",
            (
                "package_id",
                "repository_identity",
                "last_revision",
                "release_id",
                "reason",
                "removed_at",
            ),
        )
        tombstone_document = dict(document)
        package_id = _require_nonempty_string(
            tombstone_document.pop("package_id"), "tombstone.package_id"
        )
        tombstone = cls.from_document(tombstone_document)
        tombstone.package_id = package_id
        return tombstone


@dataclass
class ReleaseIndex:
    """Validated release targets bound to exact public registry bytes."""

    schema_version: int
    sequence: int
    generated_at: str
    expires_at: str
    registry_sha256: str
    plugins: dict
    tombstones: dict

    @property
    def releases(self):
        """Return the normalized release map used by runtime consumers."""
        return self.plugins

    @staticmethod
    def _package_map(registry):
        packages = getattr(registry, "packages", None)
        if isinstance(packages, dict):
            return packages
        by_package_id = getattr(registry, "by_package_id", None)
        if isinstance(by_package_id, dict):
            return by_package_id
        if isinstance(packages, (list, tuple)):
            return {package.package_id: package for package in packages}
        raise ValueError("registry packages are invalid.")

    @classmethod
    def _validated_common(
        cls,
        document,
        registry_bytes,
        now,
        previous,
    ):
        sequence = _require_positive_integer(
            document["sequence"], "release_index.sequence"
        )
        if previous is not None:
            if not isinstance(previous, cls):
                raise ValueError("previous release index is invalid.")
            if sequence <= previous.sequence:
                raise ValueError("release_index.sequence must increase.")

        generated_at = _require_nonempty_string(
            document["generated_at"], "release_index.generated_at"
        )
        expires_at = _require_nonempty_string(
            document["expires_at"], "release_index.expires_at"
        )
        generated_time = _parse_utc_timestamp(
            generated_at, "release_index.generated_at"
        )
        expiry_time = _parse_utc_timestamp(
            expires_at, "release_index.expires_at"
        )
        if expiry_time <= generated_time:
            raise ValueError("release index validity window is invalid.")
        if now is None:
            now = datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError(
                "release index validation time must be timezone-aware."
            )
        if expiry_time <= now.astimezone(timezone.utc):
            raise ValueError("release index is expired.")

        expected_registry_sha256 = _require_sha256(
            document["registry_sha256"], "release_index.registry_sha256"
        )
        actual_registry_sha256 = hashlib.sha256(bytes(registry_bytes)).hexdigest()
        if expected_registry_sha256 != actual_registry_sha256:
            raise ValueError("release index does not match registry bytes.")
        return (
            sequence,
            generated_at,
            expires_at,
            expected_registry_sha256,
        )

    @classmethod
    def _result(
        cls,
        *,
        common,
        releases,
        tombstones,
        previous,
    ):
        sequence, generated_at, expires_at, registry_sha256 = common
        result = cls(
            schema_version=RELEASE_INDEX_SCHEMA_VERSION,
            sequence=sequence,
            generated_at=generated_at,
            expires_at=expires_at,
            registry_sha256=registry_sha256,
            plugins=releases,
            tombstones=tombstones,
        )
        if previous is not None:
            result._validate_lineage(previous)
        return result

    @classmethod
    def from_document(cls, document, registry_bytes, now=None, previous=None):
        """Validate and normalize one strict schema-v2 release index."""
        document = _require_document(
            document,
            "release index",
            (
                "schema_version",
                "sequence",
                "generated_at",
                "expires_at",
                "registry_sha256",
                "releases",
                "tombstones",
            ),
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != RELEASE_INDEX_SCHEMA_VERSION
        ):
            raise ValueError("release_index.schema_version is not supported.")
        common = cls._validated_common(
            document, registry_bytes, now, previous
        )
        registry_document = _load_json_object(registry_bytes, "registry")
        registry = PackageRegistry.from_document(registry_document)
        packages = cls._package_map(registry)
        releases_document = document["releases"]
        tombstones_document = document["tombstones"]
        if not isinstance(releases_document, list):
            raise ValueError("release_index.releases must be an array.")
        if not isinstance(tombstones_document, list):
            raise ValueError("release_index.tombstones must be an array.")

        releases = {}
        release_ids = set()
        active_release_ids = set()
        for entry_document in releases_document:
            descriptor = ReleaseDescriptor.from_release_record(entry_document)
            package_id = descriptor.package_id
            collision_id = package_id.casefold()
            if collision_id in release_ids:
                raise ValueError("Release index contains a duplicate package ID.")
            release_ids.add(collision_id)
            package = packages.get(package_id)
            if package is None:
                raise ValueError("Release index contains an unknown package.")
            if descriptor.repository_identity != package.repository_identity:
                raise ValueError("Release repository does not match the registry.")
            if descriptor.domoticz_key != package.domoticz_key:
                raise ValueError("Release Domoticz identity does not match the registry.")
            if descriptor.release_id in active_release_ids:
                raise ValueError("Release index contains a duplicate release identity.")
            active_release_ids.add(descriptor.release_id)
            releases[package_id] = descriptor

        tombstones = {}
        tombstone_ids = set()
        for tombstone_document in tombstones_document:
            tombstone = ReleaseTombstone.from_tombstone_record(
                tombstone_document
            )
            package_id = tombstone.package_id
            collision_id = package_id.casefold()
            if collision_id in tombstone_ids:
                raise ValueError("Release index contains a duplicate package ID.")
            if collision_id in release_ids:
                raise ValueError(
                    "A package cannot be active and de-certified together."
                )
            tombstone_ids.add(collision_id)
            package = packages.get(package_id)
            if (
                package is not None
                and tombstone.repository_identity
                != package.repository_identity
            ):
                raise ValueError("Tombstone repository does not match the registry.")
            tombstones[package_id] = tombstone

        return cls._result(
            common=common,
            releases=releases,
            tombstones=tombstones,
            previous=previous,
        )

    @classmethod
    def from_legacy_document(
        cls, document, registry_bytes, now=None, previous=None
    ):
        """Normalize schema-v1 keyed metadata at an explicit read boundary."""
        document = _require_document(
            document,
            "legacy release index",
            (
                "schema_version",
                "sequence",
                "generated_at",
                "expires_at",
                "registry_sha256",
                "plugins",
            ),
            ("tombstones",),
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != LEGACY_RELEASE_INDEX_SCHEMA_VERSION
        ):
            raise ValueError("legacy release_index.schema_version is not supported.")
        common = cls._validated_common(
            document, registry_bytes, now, previous
        )
        registry_document = _load_json_object(registry_bytes, "legacy registry")
        registry = PackageRegistry.from_legacy_document(registry_document)
        packages = cls._package_map(registry)
        plugins_document = document["plugins"]
        tombstones_document = document.get("tombstones", {})
        if not isinstance(plugins_document, dict):
            raise ValueError("legacy release_index.plugins must be an object.")
        if not isinstance(tombstones_document, dict):
            raise ValueError("legacy release_index.tombstones must be an object.")
        if set(plugins_document) & set(tombstones_document):
            raise ValueError("A package cannot be active and de-certified together.")

        releases = {}
        active_release_ids = set()
        for package_id, entry_document in plugins_document.items():
            package = packages.get(package_id)
            if package is None:
                raise ValueError("Release index contains an unknown package.")
            descriptor = ReleaseDescriptor.from_legacy_document(entry_document)
            descriptor.package_id = package_id
            if descriptor.repository_identity != package.repository_identity:
                raise ValueError("Release repository does not match the registry.")
            if descriptor.release_id in active_release_ids:
                raise ValueError("Release index contains a duplicate release identity.")
            active_release_ids.add(descriptor.release_id)
            releases[package_id] = descriptor

        tombstones = {}
        for package_id, tombstone_document in tombstones_document.items():
            package = packages.get(package_id)
            if package is None:
                raise ValueError("Release index contains an unknown package tombstone.")
            tombstone = ReleaseTombstone.from_document(tombstone_document)
            tombstone.package_id = package_id
            if tombstone.repository_identity != package.repository_identity:
                raise ValueError("Tombstone repository does not match the registry.")
            tombstones[package_id] = tombstone

        return cls._result(
            common=common,
            releases=releases,
            tombstones=tombstones,
            previous=previous,
        )

    def _validate_lineage(self, previous):
        """Reject revision regressions, gaps, mutations, and silent removals."""
        for plugin_key, previous_release in previous.plugins.items():
            current_release = self.plugins.get(plugin_key)
            tombstone = self.tombstones.get(plugin_key)
            if current_release is None:
                if tombstone is None:
                    raise ValueError("Accepted release disappeared without a tombstone.")
                if (
                    tombstone.repository_identity
                    != previous_release.repository_identity
                    or tombstone.last_revision != previous_release.revision
                    or tombstone.release_id != previous_release.release_id
                ):
                    raise ValueError("Release tombstone does not match prior state.")
                continue

            if current_release.revision < previous_release.revision:
                raise ValueError("Release revision regressed.")
            if current_release.revision == previous_release.revision:
                if not current_release.same_accepted_target(previous_release):
                    raise ValueError("An accepted release revision was mutated.")
                continue

            if current_release.release_id == previous_release.release_id:
                raise ValueError(
                    "A higher release revision must name a new release."
                )

            required_predecessors = set(previous_release.supersedes)
            required_predecessors.add(previous_release.release_id)
            if not required_predecessors.issubset(set(current_release.supersedes)):
                raise ValueError("Release lineage has a predecessor gap.")

        for plugin_key, previous_tombstone in previous.tombstones.items():
            current_tombstone = self.tombstones.get(plugin_key)
            if current_tombstone is None:
                current_release = self.plugins.get(plugin_key)
                if (
                    current_release is None
                    or current_release.repository_identity
                    != previous_tombstone.repository_identity
                    or current_release.revision
                    <= previous_tombstone.last_revision
                    or current_release.release_id
                    == previous_tombstone.release_id
                    or previous_tombstone.release_id
                    not in current_release.supersedes
                ):
                    raise ValueError(
                        "A de-certified release was reactivated without review."
                    )
                continue
            if (
                current_tombstone.repository_identity
                != previous_tombstone.repository_identity
                or current_tombstone.last_revision < previous_tombstone.last_revision
                or current_tombstone.release_id != previous_tombstone.release_id
            ):
                raise ValueError("Release tombstone regressed or changed identity.")


@dataclass
class ReleaseMetadataSelection:
    """The registry/index pair selected for runtime use."""

    sequence: int
    registry_bytes: bytes
    release_index_bytes: bytes
    release_index: object
    release_authorized: bool
    reason: str = ""


@dataclass
class _ReleaseMetadataGeneration:
    """One complete and hash-verified on-disk metadata generation."""

    sequence: int
    registry_bytes: bytes
    release_index_bytes: bytes
    release_index: ReleaseIndex


class ReleaseMetadataStore:
    """Durably cache and recover exact registry/release-index generations."""

    def __init__(
        self,
        metadata_root,
        bundled_registry_path=None,
        bundled_index_path=None,
        clock=None,
        fault_injector=None,
    ):
        self.metadata_root = os.path.abspath(str(metadata_root))
        self.generations_dir = os.path.join(self.metadata_root, "generations")
        self.watermark_path = os.path.join(
            self.metadata_root, "trust-state.json"
        )
        self.pointer_path = os.path.join(self.metadata_root, "current.json")
        self.bundled_registry_path = bundled_registry_path
        self.bundled_index_path = bundled_index_path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fault_injector = fault_injector

    def _event(self, event):
        """Expose a completed durability boundary to crash-injection tests."""
        if self.fault_injector is not None:
            self.fault_injector(event)

    def _ensure_directories(self):
        """Create the manager-owned metadata and generation directories."""
        os.makedirs(self.generations_dir, exist_ok=True)

    def _fsync_directory(self, path):
        """Persist directory entry changes where the platform supports it."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            if os.name == "nt":
                return
            raise
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _json_bytes(self, document):
        """Serialize small state documents deterministically."""
        return (
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _write_generation_file(
        self, path, contents, written_event, fsynced_event
    ):
        """Write and fsync one file inside an unpublished generation."""
        with open(path, "xb") as metadata_file:
            metadata_file.write(contents)
            self._event(written_event)
            metadata_file.flush()
            os.fsync(metadata_file.fileno())
            self._event(fsynced_event)

    def _write_atomic_state(
        self,
        path,
        document,
        written_event,
        fsynced_event,
        replaced_event,
        directory_fsynced_event,
    ):
        """Atomically replace a durable metadata state document."""
        self._ensure_directories()
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".tmp-",
            dir=self.metadata_root,
        )
        with os.fdopen(descriptor, "wb") as state_file:
            state_file.write(self._json_bytes(document))
            self._event(written_event)
            state_file.flush()
            os.fsync(state_file.fileno())
            self._event(fsynced_event)
        os.replace(temporary_path, path)
        self._event(replaced_event)
        self._fsync_directory(self.metadata_root)
        self._event(directory_fsynced_event)

    def _write_watermark(self, sequence):
        """Raise the durable highest-sequence watermark."""
        self._write_atomic_state(
            self.watermark_path,
            {
                "schema_version": RELEASE_METADATA_STATE_SCHEMA_VERSION,
                "highest_sequence": sequence,
            },
            "watermark_written",
            "watermark_fsynced",
            "watermark_replaced",
            "metadata_fsynced_after_watermark",
        )

    def _write_pointer(self, sequence):
        """Point runtime readers at one complete metadata generation."""
        self._write_atomic_state(
            self.pointer_path,
            {
                "schema_version": RELEASE_METADATA_STATE_SCHEMA_VERSION,
                "sequence": sequence,
            },
            "pointer_written",
            "pointer_fsynced",
            "pointer_replaced",
            "metadata_fsynced_after_pointer",
        )

    def _read_state_sequence(self, path, field):
        """Read one small state document without repairing invalid data."""
        if not os.path.isfile(path):
            return "missing", 0
        try:
            with open(path, "rb") as state_file:
                document = _load_json_object(state_file.read(), path)
            allowed = {field, "schema_version"}
            if set(document) - allowed or field not in document:
                raise ValueError("State document has unexpected fields.")
            if "schema_version" in document and (
                type(document["schema_version"]) is not int
                or document["schema_version"]
                != RELEASE_METADATA_STATE_SCHEMA_VERSION
            ):
                raise ValueError("State document schema is unsupported.")
            sequence = _require_positive_integer(document[field], field)
            return "valid", sequence
        except (OSError, ValueError, TypeError):
            return "invalid", 0

    def _historical_index(self, registry_bytes, index_bytes):
        """Validate a stored pair without applying current-time expiry."""
        document = _load_json_object(index_bytes, "release_index.json")
        generated_at = _parse_utc_timestamp(
            document.get("generated_at"), "release_index.generated_at"
        )
        return self._parse_index(
            document,
            registry_bytes,
            now=generated_at,
        )

    def _parse_index(self, document, registry_bytes, *, now, previous=None):
        """Normalize legacy v1 only at the metadata-store read boundary."""
        parser = (
            ReleaseIndex.from_legacy_document
            if document.get("schema_version")
            == LEGACY_RELEASE_INDEX_SCHEMA_VERSION
            else ReleaseIndex.from_document
        )
        return parser(
            document,
            registry_bytes=registry_bytes,
            now=now,
            previous=previous,
        )

    def _generation_from_directory(self, generation_path):
        """Load one complete generation, returning None if it is corrupt."""
        generation_name = os.path.basename(generation_path)
        if (
            not generation_name.isdigit()
            or os.path.islink(generation_path)
            or not os.path.isdir(generation_path)
        ):
            return None
        try:
            sequence = _require_positive_integer(
                int(generation_name), "generation sequence"
            )
            with open(
                os.path.join(generation_path, "registry.json"), "rb"
            ) as registry_file:
                registry_bytes = registry_file.read()
            with open(
                os.path.join(generation_path, "release_index.json"), "rb"
            ) as index_file:
                index_bytes = index_file.read()
            with open(
                os.path.join(generation_path, "hashes.json"), "rb"
            ) as hashes_file:
                hashes = _load_json_object(
                    hashes_file.read(), "generation hashes"
                )
            if set(hashes) != {
                "registry_sha256",
                "release_index_sha256",
            }:
                raise ValueError("Generation hashes have unexpected fields.")
            registry_sha256 = _require_sha256(
                hashes["registry_sha256"], "generation registry hash"
            )
            index_sha256 = _require_sha256(
                hashes["release_index_sha256"], "generation index hash"
            )
            if hashlib.sha256(registry_bytes).hexdigest() != registry_sha256:
                raise ValueError("Cached registry hash mismatch.")
            if hashlib.sha256(index_bytes).hexdigest() != index_sha256:
                raise ValueError("Cached release index hash mismatch.")
            release_index = self._historical_index(
                registry_bytes, index_bytes
            )
            if release_index.sequence != sequence:
                raise ValueError(
                    "Generation directory does not match embedded sequence."
                )
            return _ReleaseMetadataGeneration(
                sequence=sequence,
                registry_bytes=registry_bytes,
                release_index_bytes=index_bytes,
                release_index=release_index,
            )
        except (OSError, ValueError, TypeError):
            return None

    def _scan_generations(self):
        """Return all complete generations ordered by increasing sequence."""
        if not os.path.isdir(self.generations_dir):
            return []
        generations = []
        try:
            names = os.listdir(self.generations_dir)
        except OSError:
            return generations
        for name in names:
            if not name.isdigit():
                continue
            generation = self._generation_from_directory(
                os.path.join(self.generations_dir, name)
            )
            if generation is not None:
                generations.append(generation)
        return sorted(generations, key=lambda generation: generation.sequence)

    def _read_bundle_bytes(self):
        """Read bundled bytes for bootstrap or registry-only fallback."""
        registry_bytes = b""
        index_bytes = b""
        try:
            if self.bundled_registry_path:
                with open(self.bundled_registry_path, "rb") as registry_file:
                    registry_bytes = registry_file.read()
                _load_json_object(registry_bytes, "bundled registry")
        except (OSError, ValueError):
            registry_bytes = b""
        try:
            if self.bundled_index_path:
                with open(self.bundled_index_path, "rb") as index_file:
                    index_bytes = index_file.read()
        except OSError:
            index_bytes = b""
        return registry_bytes, index_bytes

    def _is_fresh(self, release_index):
        """Return whether an otherwise valid index may authorize mutations."""
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Release metadata clock must be timezone-aware.")
        expires_at = _parse_utc_timestamp(
            release_index.expires_at, "release_index.expires_at"
        )
        return expires_at > now.astimezone(timezone.utc)

    def _selection(self, generation, reason=""):
        """Build a runtime selection and apply current freshness rules."""
        release_authorized = self._is_fresh(generation.release_index)
        if not release_authorized and not reason:
            reason = "Release metadata is expired; release changes are paused."
        return ReleaseMetadataSelection(
            sequence=generation.sequence,
            registry_bytes=generation.registry_bytes,
            release_index_bytes=generation.release_index_bytes,
            release_index=(
                generation.release_index if release_authorized else None
            ),
            release_authorized=release_authorized,
            reason=reason,
        )

    def _unauthorized_selection(
        self, reason, registry_bytes=b"", sequence=0, index_bytes=b""
    ):
        """Return registry data while refusing all release mutations."""
        return ReleaseMetadataSelection(
            sequence=sequence,
            registry_bytes=registry_bytes,
            release_index_bytes=index_bytes,
            release_index=None,
            release_authorized=False,
            reason=reason,
        )

    def revalidate_selection(self, selection):
        """Revoke an in-memory selection when its validity window ends."""
        if not isinstance(selection, ReleaseMetadataSelection):
            raise ValueError("Release metadata selection is invalid.")
        if not selection.release_authorized:
            return selection

        release_index = selection.release_index
        reason = ""
        if (
            not isinstance(release_index, ReleaseIndex)
            or release_index.sequence != selection.sequence
        ):
            reason = (
                "Release metadata freshness could not be verified; "
                "release changes are paused."
            )
        else:
            try:
                if self._is_fresh(release_index):
                    return selection
                reason = (
                    "Release metadata is expired; release changes are paused."
                )
            except (TypeError, ValueError):
                reason = (
                    "Release metadata freshness could not be verified; "
                    "release changes are paused."
                )

        return self._unauthorized_selection(
            reason,
            registry_bytes=selection.registry_bytes,
            sequence=selection.sequence,
            index_bytes=selection.release_index_bytes,
        )

    def _trusted_generation(self, repair=True):
        """Select the highest complete generation at or above the watermark."""
        watermark_status, watermark = self._read_state_sequence(
            self.watermark_path, "highest_sequence"
        )
        if watermark_status == "invalid":
            raise ValueError(
                "The durable release metadata watermark is malformed."
            )

        generations = self._scan_generations()
        eligible = [
            generation
            for generation in generations
            if generation.sequence >= watermark
        ]
        generation = eligible[-1] if eligible else None
        if generation is None:
            if watermark > 0:
                raise ValueError(
                    "The generation named by the durable watermark is unavailable."
                )
            return None, 0, generations

        pointer_status, pointer = self._read_state_sequence(
            self.pointer_path, "sequence"
        )
        pointer_matches = (
            pointer_status == "valid" and pointer == generation.sequence
        )
        watermark_matches = watermark == generation.sequence
        if repair and (not watermark_matches or not pointer_matches):
            self._event("recovery_selected")
            if not watermark_matches:
                self._write_watermark(generation.sequence)
            if not pointer_matches:
                self._write_pointer(generation.sequence)
        return generation, watermark, generations

    def _persist_generation(
        self, registry_bytes, index_bytes, release_index
    ):
        """Publish an exact pair, then durably raise trust and current state."""
        self._ensure_directories()
        sequence = release_index.sequence
        final_path = os.path.join(self.generations_dir, str(sequence))
        if os.path.exists(final_path):
            raise ValueError("Release metadata generation already exists.")
        temporary_path = tempfile.mkdtemp(
            prefix=str(sequence) + ".tmp-", dir=self.generations_dir
        )

        self._write_generation_file(
            os.path.join(temporary_path, "registry.json"),
            registry_bytes,
            "registry_written",
            "registry_fsynced",
        )
        self._write_generation_file(
            os.path.join(temporary_path, "release_index.json"),
            index_bytes,
            "index_written",
            "index_fsynced",
        )
        hashes = self._json_bytes(
            {
                "registry_sha256": hashlib.sha256(
                    registry_bytes
                ).hexdigest(),
                "release_index_sha256": hashlib.sha256(
                    index_bytes
                ).hexdigest(),
            }
        )
        self._write_generation_file(
            os.path.join(temporary_path, "hashes.json"),
            hashes,
            "hashes_written",
            "hashes_fsynced",
        )
        self._fsync_directory(temporary_path)
        self._event("generation_fsynced")
        os.replace(temporary_path, final_path)
        self._event("generation_renamed")
        self._fsync_directory(self.generations_dir)
        self._event("generations_fsynced")
        self._write_watermark(sequence)
        self._write_pointer(sequence)
        return _ReleaseMetadataGeneration(
            sequence=sequence,
            registry_bytes=registry_bytes,
            release_index_bytes=index_bytes,
            release_index=release_index,
        )

    def accept_remote(self, registry_bytes, index_bytes):
        """Validate and durably accept a fresh, increasing remote pair."""
        if not isinstance(registry_bytes, (bytes, bytearray)) or not isinstance(
            index_bytes, (bytes, bytearray)
        ):
            raise ValueError("Remote release metadata must be bytes.")
        registry_bytes = bytes(registry_bytes)
        index_bytes = bytes(index_bytes)

        prior_generation, watermark, _ = self._trusted_generation(repair=True)
        document = _load_json_object(index_bytes, "release_index.json")
        release_index = self._parse_index(
            document,
            registry_bytes,
            now=self.clock(),
            previous=(
                prior_generation.release_index
                if prior_generation is not None
                else None
            ),
        )
        if release_index.sequence <= watermark:
            raise ValueError("Release metadata sequence does not raise trust.")

        generation = self._persist_generation(
            registry_bytes, index_bytes, release_index
        )
        return self._selection(generation)

    def load(self):
        """Recover the highest trusted pair or return a registry-only fallback."""
        bundled_registry, bundled_index = self._read_bundle_bytes()
        generations = self._scan_generations()
        fallback_generation = generations[-1] if generations else None
        fallback_registry = (
            fallback_generation.registry_bytes
            if fallback_generation is not None
            else bundled_registry
        )
        fallback_sequence = (
            fallback_generation.sequence if fallback_generation is not None else 0
        )

        try:
            generation, watermark, _ = self._trusted_generation(repair=True)
        except ValueError as error:
            return self._unauthorized_selection(
                str(error),
                registry_bytes=fallback_registry,
                sequence=fallback_sequence,
            )
        if generation is not None:
            return self._selection(generation)

        if not bundled_registry:
            return self._unauthorized_selection(
                "No valid bundled or cached plugin registry is available."
            )
        if not bundled_index:
            return self._unauthorized_selection(
                "The bundled release index is unavailable.",
                registry_bytes=bundled_registry,
            )

        try:
            bundled_release_index = self._historical_index(
                bundled_registry, bundled_index
            )
        except ValueError as error:
            return self._unauthorized_selection(
                "The bundled release index is invalid: " + str(error),
                registry_bytes=bundled_registry,
            )
        if bundled_release_index.sequence < watermark:
            return self._unauthorized_selection(
                "The bundled release index is below the durable watermark.",
                registry_bytes=bundled_registry,
                sequence=bundled_release_index.sequence,
                index_bytes=bundled_index,
            )
        if not self._is_fresh(bundled_release_index):
            return self._unauthorized_selection(
                "The bundled release index is expired.",
                registry_bytes=bundled_registry,
                sequence=bundled_release_index.sequence,
                index_bytes=bundled_index,
            )

        generation = self._persist_generation(
            bundled_registry, bundled_index, bundled_release_index
        )
        return self._selection(generation)


INSTALL_METADATA_SCHEMA_VERSION = 2
LEGACY_INSTALL_METADATA_SCHEMA_VERSION = 1
WINDOWS_RESERVED_PATH_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *("com" + str(number) for number in range(1, 10)),
    *("lpt" + str(number) for number in range(1, 10)),
}


def _require_plugin_key(value, label="plugin_key"):
    """Validate one cross-platform plugin directory key."""
    value = _require_nonempty_string(value, label)
    invalid_windows_characters = '<>:"/\\|?*'
    if (
        value in (".", "..")
        or value.startswith(".")
        or value.endswith((".", " "))
        or any(character in value for character in invalid_windows_characters)
        or os.path.basename(value) != value
    ):
        raise ValueError(label + " is not a safe plugin key.")
    windows_stem = value.split(".", 1)[0].casefold()
    if windows_stem in WINDOWS_RESERVED_PATH_NAMES:
        raise ValueError(label + " is reserved on Windows.")
    return value


def _require_audit_path(value, label):
    """Validate one canonical, cross-platform relative audit path."""
    path = _normalize_relative_metadata_path(value, label, allow_root=False)
    if unicodedata.normalize("NFC", path) != path:
        raise ValueError(label + " must use NFC Unicode normalization.")
    parts = path.split("/")
    for part in parts:
        if part.endswith((".", " ")) or ":" in part:
            raise ValueError(label + " is not portable across supported hosts.")
        windows_stem = part.split(".", 1)[0].casefold()
        if windows_stem in WINDOWS_RESERVED_PATH_NAMES:
            raise ValueError(label + " contains a Windows-reserved name.")
    first_part = parts[0].casefold()
    if first_part in (".git", ".pypluginstore") or path.casefold() in (
        ".pypluginstore.json",
        "plugin.py",
    ):
        raise ValueError(label + " is manager-reserved or executable code.")
    return path


def _require_artifact_file_path(value, label):
    """Validate one portable regular-file path from a pristine artifact."""
    path = _normalize_relative_metadata_path(value, label, allow_root=False)
    if unicodedata.normalize("NFC", path) != path:
        raise ValueError(label + " must use NFC Unicode normalization.")
    parts = path.split("/")
    for part in parts:
        if part.endswith((".", " ")) or ":" in part:
            raise ValueError(label + " is not portable across supported hosts.")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_PATH_NAMES:
            raise ValueError(label + " contains a Windows-reserved name.")
    if parts[0].casefold() in (".git", ".pypluginstore") or path.casefold() == (
        ".pypluginstore.json"
    ):
        raise ValueError(label + " is manager-reserved.")
    return path


class ReleaseHttpError(Exception):
    """Categorized runtime release-download failure."""

    def __init__(self, reason, message="", *, status=None, url=None):
        self.reason = str(reason)
        self.status = status
        self.url = url
        super().__init__(message or self.reason)


class SystemResolver:
    """Resolve stream addresses using the operating-system resolver."""

    def resolve(self, hostname, port):
        answers = socket.getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        addresses = []
        seen = set()
        for family, _kind, _protocol, _canonical, socket_address in answers:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            address = socket_address[0]
            if address not in seen:
                seen.add(address)
                addresses.append(address)
        return addresses


class _ReleaseResponseHeaders(Mapping):
    """Expose wire header pairs without hiding case-fold duplicates."""

    def __init__(self, pairs):
        self._pairs = tuple(pairs)

    def __getitem__(self, key):
        for name, value in self._pairs:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (name for name, _value in self._pairs)

    def __len__(self):
        return len(self._pairs)

    def items(self):
        return iter(self._pairs)


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    """Connect to one validated IP while authenticating the URL hostname."""

    def __init__(
        self,
        connect_ip,
        server_hostname,
        port,
        timeout,
        context,
    ):
        super().__init__(
            server_hostname,
            port=port,
            timeout=timeout,
            context=context,
        )
        self._connect_ip = connect_ip
        self._server_hostname = server_hostname

    def connect(self):
        if self._tunnel_host is not None:
            raise RuntimeError("Pinned HTTPS connections do not support proxies.")

        address = ipaddress.ip_address(self._connect_ip)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        endpoint = (
            (str(address), self.port, 0, 0)
            if family == socket.AF_INET6
            else (str(address), self.port)
        )
        raw_socket = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw_socket.settimeout(self.timeout)
            raw_socket.connect(endpoint)
            self.sock = self._context.wrap_socket(
                raw_socket,
                server_hostname=self._server_hostname,
            )
        except Exception:
            raw_socket.close()
            self.sock = None
            raise


class _PinnedHttpsResponse:
    """Adapt ``http.client`` responses to the bounded stream contract."""

    def __init__(self, response, connection):
        self._response = response
        self._connection = connection
        self.status = response.status
        self.headers = _ReleaseResponseHeaders(response.getheaders())
        self._closed = False

    def iter_bytes(self, chunk_size):
        while True:
            chunk = self._response.read(chunk_size)
            if not chunk:
                return
            yield chunk

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        finally:
            self._connection.close()


class PinnedHttpsTransport:
    """Direct stdlib HTTPS transport that never consults proxy settings."""

    def __init__(self, context=None):
        self.context = ssl.create_default_context() if context is None else context
        if not callable(getattr(self.context, "wrap_socket", None)):
            raise ValueError("context must provide wrap_socket().")

    def request(
        self,
        method,
        url,
        *,
        connect_ip,
        server_hostname,
        host_header,
        headers,
        connect_timeout,
        read_timeout,
    ):
        target = _validate_release_url(url)
        if method != "GET":
            raise ValueError("PinnedHttpsTransport only supports GET.")
        if (
            server_hostname != target.hostname
            or host_header != target.host_header
        ):
            raise ValueError("TLS and Host identities must match the URL.")
        try:
            pinned_address = str(ipaddress.ip_address(connect_ip))
        except ValueError as error:
            raise ValueError("connect_ip must be an IP address literal.") from error
        request_headers = _validate_release_request_headers(headers)
        parsed = urllib.parse.urlsplit(target.url)
        request_target = parsed.path or "/"
        if parsed.query:
            request_target += "?" + parsed.query

        connection = _PinnedHttpsConnection(
            pinned_address,
            target.hostname,
            target.port,
            connect_timeout,
            self.context,
        )
        try:
            connection.connect()
            connection.putrequest(
                method,
                request_target,
                skip_host=True,
                skip_accept_encoding=True,
            )
            connection.putheader("Host", target.host_header)
            for name, value in request_headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            connection.sock.settimeout(read_timeout)
            response = connection.getresponse()
            return _PinnedHttpsResponse(response, connection)
        except Exception:
            connection.close()
            raise


@dataclass(frozen=True)
class ReleaseDownload:
    """Verified metadata for one durable runtime artifact download."""

    path: str
    size: int
    sha256: str
    final_url: str
    redirects: int
    verified: bool


@dataclass(frozen=True)
class _ValidatedReleaseUrl:
    url: str
    hostname: str
    port: int
    host_header: str
    origin: tuple


def _release_http_error(reason, message, *, status=None, url=None):
    return ReleaseHttpError(reason, message, status=status, url=url)


def _validate_release_url(url):
    """Validate an absolute credential-free HTTPS request target."""
    if not isinstance(url, str) or not url:
        raise _release_http_error(
            "https_required",
            "An absolute HTTPS URL is required.",
        )
    if (
        CONTROL_CHARACTER_PATTERN.search(url)
        or any(character.isspace() for character in url)
        or "\\" in url
        or "#" in url
        or RELEASE_INVALID_PERCENT_PATTERN.search(url)
    ):
        raise _release_http_error("invalid_url", "URL is not canonical.", url=url)
    try:
        parsed = urllib.parse.urlsplit(url)
        explicit_port = parsed.port
    except ValueError as error:
        raise _release_http_error(
            "invalid_url",
            "URL authority is invalid.",
            url=url,
        ) from error
    if parsed.scheme.lower() != "https":
        raise _release_http_error(
            "https_required",
            "An absolute HTTPS URL is required.",
            url=url,
        )
    hostname = parsed.hostname
    if (
        not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or "*" in hostname
        or hostname.startswith(".")
        or hostname.endswith(".")
        or parsed.netloc.endswith(":")
    ):
        raise _release_http_error(
            "invalid_url",
            "URL must have a canonical credential-free authority.",
            url=url,
        )
    port = 443 if explicit_port is None else explicit_port
    if not 1 <= port <= 65535:
        raise _release_http_error("invalid_url", "URL port is invalid.", url=url)
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise _release_http_error(
            "invalid_url",
            "URL hostname is invalid.",
            url=url,
        ) from error
    try:
        ipaddress.ip_address(ascii_hostname)
        hostname_is_ip = True
    except ValueError:
        hostname_is_ip = False
    if (
        not ascii_hostname
        or CONTROL_CHARACTER_PATTERN.search(ascii_hostname)
        or "%" in ascii_hostname
        or len(ascii_hostname) > 253
        or (
            not hostname_is_ip
            and any(
                not RELEASE_HOST_LABEL_PATTERN.fullmatch(label)
                for label in ascii_hostname.split(".")
            )
        )
    ):
        raise _release_http_error(
            "invalid_url",
            "URL hostname is invalid.",
            url=url,
        )

    host_name = (
        "[" + ascii_hostname + "]" if ":" in ascii_hostname else ascii_hostname
    )
    host_header = host_name
    if explicit_port is not None:
        host_header += ":" + str(explicit_port)
    return _ValidatedReleaseUrl(
        url=url,
        hostname=ascii_hostname,
        port=port,
        host_header=host_header,
        origin=("https", ascii_hostname, port),
    )


def _validate_release_origin_allowlist(allowed_origins):
    """Return exact HTTPS origins from reviewed configuration."""
    if allowed_origins is None:
        return set()
    if isinstance(allowed_origins, (str, bytes)) or not isinstance(
        allowed_origins,
        (list, tuple, set, frozenset),
    ):
        raise _release_http_error(
            "invalid_origin_allowlist",
            "Origin allowlist must be a collection of exact HTTPS origins.",
        )

    validated = set()
    for origin in allowed_origins:
        if (
            not isinstance(origin, str)
            or not origin
            or CONTROL_CHARACTER_PATTERN.search(origin)
            or any(character.isspace() for character in origin)
            or "\\" in origin
            or "?" in origin
            or "#" in origin
        ):
            raise _release_http_error(
                "invalid_origin_allowlist",
                "Origin allowlist contains an invalid value.",
            )
        try:
            parsed = urllib.parse.urlsplit(origin)
            explicit_port = parsed.port
        except ValueError as error:
            raise _release_http_error(
                "invalid_origin_allowlist",
                "Origin allowlist contains an invalid authority.",
            ) from error
        hostname = parsed.hostname
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in hostname
            or hostname.startswith(".")
            or hostname.endswith(".")
            or parsed.netloc.endswith(":")
        ):
            raise _release_http_error(
                "invalid_origin_allowlist",
                "Origin allowlist values must be exact HTTPS origins.",
            )
        port = 443 if explicit_port is None else explicit_port
        if not 1 <= port <= 65535:
            raise _release_http_error(
                "invalid_origin_allowlist",
                "Origin allowlist contains an invalid port.",
            )
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise _release_http_error(
                "invalid_origin_allowlist",
                "Origin allowlist contains an invalid hostname.",
            ) from error
        try:
            ipaddress.ip_address(ascii_hostname)
            hostname_is_ip = True
        except ValueError:
            hostname_is_ip = False
        if (
            not ascii_hostname
            or "%" in ascii_hostname
            or len(ascii_hostname) > 253
            or (
                not hostname_is_ip
                and any(
                    not RELEASE_HOST_LABEL_PATTERN.fullmatch(label)
                    for label in ascii_hostname.split(".")
                )
            )
        ):
            raise _release_http_error(
                "invalid_origin_allowlist",
                "Origin allowlist contains an invalid hostname.",
            )
        validated.add(("https", ascii_hostname, port))
    return validated


def _validate_release_request_headers(headers):
    """Validate caller headers and force an undecoded response body."""
    if headers is None:
        headers = {}
    if not isinstance(headers, Mapping):
        raise ValueError("headers must be a mapping.")

    result = {}
    seen = set()
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or not RELEASE_HEADER_NAME_PATTERN.fullmatch(name)
        ):
            raise ValueError("HTTP header name is invalid.")
        lower_name = name.lower()
        if lower_name in seen:
            raise ValueError("HTTP header names must be unique ignoring case.")
        seen.add(lower_name)
        if not isinstance(value, str) or CONTROL_CHARACTER_PATTERN.search(value):
            raise ValueError("HTTP header value is invalid.")
        if lower_name == "host":
            raise ValueError("Host is controlled by the pinned transport.")
        if lower_name == "accept-encoding":
            continue
        result[name] = value
    result["Accept-Encoding"] = "identity"
    return result


def _strip_release_cross_origin_secrets(headers):
    """Remove credentials and ambient cookies on an approved origin change."""
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in RELEASE_SECRET_REQUEST_HEADERS
    }


def _release_response_headers(response, url):
    """Normalize response headers while rejecting case-fold duplicates."""
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        raise _release_http_error(
            "invalid_response_headers",
            "HTTP response headers are not a mapping.",
            url=url,
        )
    normalized = {}
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or not RELEASE_HEADER_NAME_PATTERN.fullmatch(name)
        ):
            raise _release_http_error(
                "invalid_response_headers",
                "HTTP response contains an invalid header name.",
                url=url,
            )
        lower_name = name.lower()
        if lower_name in normalized:
            raise _release_http_error(
                "invalid_response_headers",
                "HTTP response contains duplicate headers.",
                url=url,
            )
        if not isinstance(value, str) or CONTROL_CHARACTER_PATTERN.search(value):
            raise _release_http_error(
                "invalid_response_headers",
                "HTTP response contains an invalid header value.",
                url=url,
            )
        normalized[lower_name] = value
    return normalized


def _safe_close_release_response(response):
    """Best-effort close without masking a categorized fetch failure."""
    try:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


@dataclass(frozen=True)
class ReleaseArchiveLimits:
    """Conservative resource limits for one untrusted release ZIP."""

    max_archive_size: int = 50 * 1024 * 1024
    max_expanded_size: int = 250 * 1024 * 1024
    max_entries: int = 5000
    max_file_size: int = 50 * 1024 * 1024
    max_compression_ratio: float = 100.0

    def __post_init__(self):
        for field_name in (
            "max_archive_size",
            "max_expanded_size",
            "max_entries",
            "max_file_size",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                raise ValueError(field_name + " must be a positive integer.")
        ratio = self.max_compression_ratio
        if (
            type(ratio) not in (int, float)
            or not math.isfinite(ratio)
            or ratio <= 0
        ):
            raise ValueError(
                "max_compression_ratio must be a finite positive number."
            )


class SafeReleaseHttpClient:
    """Stream one bounded artifact through independently pinned HTTPS hops."""

    def __init__(
        self,
        *,
        resolver=None,
        transport=None,
        max_redirects=3,
        connect_timeout=5.0,
        read_timeout=30.0,
        max_bytes=50 * 1024 * 1024,
        stream_chunk_size=RELEASE_HTTP_CHUNK_SIZE,
    ):
        if type(max_redirects) is not int or max_redirects < 0:
            raise ValueError("max_redirects must be a non-negative integer.")
        for value, label in (
            (connect_timeout, "connect_timeout"),
            (read_timeout, "read_timeout"),
        ):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(label + " must be a finite positive number.")
        if (
            type(max_bytes) is not int
            or max_bytes <= 0
            or max_bytes > sys.maxsize
        ):
            raise ValueError("max_bytes must be a positive integer.")
        if type(stream_chunk_size) is not int or stream_chunk_size <= 0:
            raise ValueError("stream_chunk_size must be a positive integer.")
        if resolver is None:
            resolver = SystemResolver()
        if transport is None:
            transport = PinnedHttpsTransport()
        if not callable(getattr(resolver, "resolve", None)):
            raise ValueError("resolver must provide resolve().")
        if not callable(getattr(transport, "request", None)):
            raise ValueError("transport must provide request().")

        self.resolver = resolver
        self.transport = transport
        self.max_redirects = max_redirects
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.max_bytes = max_bytes
        self.stream_chunk_size = min(stream_chunk_size, max_bytes)

    def _resolve_public_address(self, hostname, port, url):
        """Validate the entire DNS answer, then pin its first public address."""
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            answers = [hostname]
        else:
            try:
                answers = self.resolver.resolve(hostname, port)
            except Exception as error:
                raise _release_http_error(
                    "dns_resolution_failed",
                    "DNS resolution failed.",
                    url=url,
                ) from error
        if isinstance(answers, (str, bytes)):
            answers = None
        try:
            answer_iterator = iter(answers) if answers is not None else iter(())
        except Exception as error:
            raise _release_http_error(
                "dns_resolution_failed",
                "DNS answer is invalid.",
                url=url,
            ) from error

        parsed_addresses = []
        for answer_index in range(RELEASE_MAX_DNS_ANSWERS + 1):
            try:
                answer = next(answer_iterator)
            except StopIteration:
                break
            except Exception as error:
                raise _release_http_error(
                    "dns_resolution_failed",
                    "DNS answer could not be read.",
                    url=url,
                ) from error
            if answer_index == RELEASE_MAX_DNS_ANSWERS:
                raise _release_http_error(
                    "dns_resolution_failed",
                    "DNS answer contains too many addresses.",
                    url=url,
                )
            if not isinstance(answer, str):
                raise _release_http_error(
                    "dns_resolution_failed",
                    "DNS answer contains a malformed address.",
                    url=url,
                )
            try:
                address = ipaddress.ip_address(answer)
            except ValueError as error:
                raise _release_http_error(
                    "dns_resolution_failed",
                    "DNS answer contains a malformed address.",
                    url=url,
                ) from error
            public_address = getattr(address, "ipv4_mapped", None) or address
            if not public_address.is_global or public_address.is_multicast:
                raise _release_http_error(
                    "non_public_address",
                    "DNS answer contains a non-public address.",
                    url=url,
                )
            parsed_addresses.append(str(address))
        if not parsed_addresses:
            raise _release_http_error(
                "dns_resolution_failed",
                "DNS answer is empty.",
                url=url,
            )
        return parsed_addresses[0]

    def _request(self, target, headers):
        """Open one pinned transport request without retrying unknown bytes."""
        connect_ip = self._resolve_public_address(
            target.hostname,
            target.port,
            target.url,
        )
        try:
            return self.transport.request(
                "GET",
                target.url,
                connect_ip=connect_ip,
                server_hostname=target.hostname,
                host_header=target.host_header,
                headers=dict(headers),
                connect_timeout=self.connect_timeout,
                read_timeout=self.read_timeout,
            )
        except TimeoutError as error:
            raise _release_http_error(
                "timeout",
                "HTTP request timed out.",
                url=target.url,
            ) from error
        except ReleaseHttpError:
            raise
        except Exception as error:
            raise _release_http_error(
                "transport_error",
                "HTTP transport failed.",
                url=target.url,
            ) from error

    def _response_length(self, response_headers, target, expected_size):
        content_encoding = response_headers.get("content-encoding")
        if (
            content_encoding is not None
            and content_encoding.strip().lower() != "identity"
        ):
            raise _release_http_error(
                "unsupported_content_encoding",
                "Compressed response bodies are not accepted.",
                url=target.url,
            )

        content_length = response_headers.get("content-length")
        if content_length is None:
            return None
        if not RELEASE_CONTENT_LENGTH_PATTERN.fullmatch(content_length):
            raise _release_http_error(
                "invalid_content_length",
                "Content-Length is malformed.",
                url=target.url,
            )
        normalized_length = content_length.lstrip("0") or "0"
        maximum_length = str(self.max_bytes)
        if (
            len(normalized_length) > len(maximum_length)
            or (
                len(normalized_length) == len(maximum_length)
                and normalized_length > maximum_length
            )
        ):
            raise _release_http_error(
                "content_too_large",
                "Declared response size exceeds the limit.",
                url=target.url,
            )
        declared_size = int(normalized_length)
        if declared_size > self.max_bytes:
            raise _release_http_error(
                "content_too_large",
                "Declared response size exceeds the limit.",
                url=target.url,
            )
        if expected_size is not None and declared_size != expected_size:
            raise _release_http_error(
                "length_mismatch",
                "Declared response size differs from expected size.",
                url=target.url,
            )
        return declared_size

    def _remove_partial_download(self, destination):
        try:
            os.remove(destination)
        except FileNotFoundError:
            pass

    def _write_chunk(self, output, chunk, target):
        view = memoryview(chunk)
        while view:
            try:
                written = output.write(view)
            except Exception as error:
                raise _release_http_error(
                    "write_failed",
                    "Release artifact could not be written.",
                    url=target.url,
                ) from error
            if type(written) is not int or written <= 0:
                raise _release_http_error(
                    "write_failed",
                    "Release artifact write did not make progress.",
                    url=target.url,
                )
            view = view[written:]

    def _read_download_to_path(
        self,
        response,
        target,
        response_headers,
        destination,
        expected_sha256,
        expected_size,
        redirects,
    ):
        """Stream, verify, and durably persist one successful response."""
        declared_size = self._response_length(
            response_headers,
            target,
            expected_size,
        )
        iterator = getattr(response, "iter_bytes", None)
        if not callable(iterator):
            raise _release_http_error(
                "transport_error",
                "HTTP response does not expose a byte iterator.",
                url=target.url,
            )

        try:
            output = open(destination, "xb", buffering=0)
        except FileExistsError as error:
            raise _release_http_error(
                "destination_exists",
                "Release download destination already exists.",
                url=target.url,
            ) from error
        except Exception as error:
            raise _release_http_error(
                "write_failed",
                "Release download destination could not be created.",
                url=target.url,
            ) from error

        digest = hashlib.sha256()
        actual_size = 0
        try:
            try:
                for chunk in iterator(self.stream_chunk_size):
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise _release_http_error(
                            "transport_error",
                            "HTTP response yielded non-byte data.",
                            url=target.url,
                        )
                    chunk = bytes(chunk)
                    if actual_size + len(chunk) > self.max_bytes:
                        raise _release_http_error(
                            "content_too_large",
                            "Streamed response exceeds the size limit.",
                            url=target.url,
                        )
                    self._write_chunk(output, chunk, target)
                    actual_size += len(chunk)
                    digest.update(chunk)
            except ReleaseHttpError:
                raise
            except TimeoutError as error:
                raise _release_http_error(
                    "timeout",
                    "HTTP response stream timed out.",
                    url=target.url,
                ) from error
            except Exception as error:
                raise _release_http_error(
                    "transport_error",
                    "HTTP response stream failed.",
                    url=target.url,
                ) from error

            if declared_size is not None and actual_size != declared_size:
                raise _release_http_error(
                    "length_mismatch",
                    "Received bytes differ from Content-Length.",
                    url=target.url,
                )
            if expected_size is not None and actual_size != expected_size:
                raise _release_http_error(
                    "length_mismatch",
                    "Received bytes differ from expected size.",
                    url=target.url,
                )
            actual_digest = digest.hexdigest()
            if expected_sha256 and actual_digest != expected_sha256:
                raise _release_http_error(
                    "digest_mismatch",
                    "Received bytes differ from expected SHA-256.",
                    url=target.url,
                )
            try:
                output.flush()
                os.fsync(output.fileno())
            except Exception as error:
                raise _release_http_error(
                    "write_failed",
                    "Release artifact could not be made durable.",
                    url=target.url,
                ) from error
            try:
                output.close()
            except Exception as error:
                raise _release_http_error(
                    "write_failed",
                    "Release artifact could not be closed.",
                    url=target.url,
                ) from error
            return ReleaseDownload(
                path=destination,
                size=actual_size,
                sha256=actual_digest,
                final_url=target.url,
                redirects=redirects,
                verified=bool(expected_sha256 or expected_size is not None),
            )
        except BaseException:
            try:
                output.close()
            finally:
                self._remove_partial_download(destination)
            raise

    def download_to_path(
        self,
        url,
        destination,
        *,
        expected_sha256=None,
        expected_size=None,
        allowed_origins=(),
        headers=None,
    ):
        """Download and verify one artifact into a brand-new durable file."""
        if expected_sha256 is None:
            expected_sha256 = ""
        if not isinstance(expected_sha256, str) or (
            expected_sha256
            and not LOWER_SHA256_PATTERN.fullmatch(expected_sha256)
        ):
            raise ValueError("expected_sha256 must be a lowercase SHA-256 digest.")
        if expected_size is not None and (
            type(expected_size) is not int
            or expected_size <= 0
            or expected_size > self.max_bytes
        ):
            raise ValueError(
                "expected_size must be a positive integer within max_bytes."
            )
        request_headers = _validate_release_request_headers(headers)
        reviewed_origins = _validate_release_origin_allowlist(allowed_origins)
        target = _validate_release_url(url)
        try:
            destination_path = os.fspath(destination)
        except TypeError as error:
            raise ValueError("destination must be a filesystem path.") from error
        if not isinstance(destination_path, str) or not destination_path:
            raise ValueError("destination must be a non-empty text path.")
        if os.path.lexists(destination_path):
            raise _release_http_error(
                "destination_exists",
                "Release download destination already exists.",
                url=target.url,
            )

        approved_origins = set(reviewed_origins)
        approved_origins.add(target.origin)
        redirects = 0
        while True:
            response = self._request(target, request_headers)
            try:
                status = getattr(response, "status", None)
                if type(status) is not int:
                    raise _release_http_error(
                        "transport_error",
                        "HTTP response status is invalid.",
                        url=target.url,
                    )
                response_headers = _release_response_headers(
                    response,
                    target.url,
                )
                if status in RELEASE_REDIRECT_STATUSES:
                    if redirects >= self.max_redirects:
                        raise _release_http_error(
                            "too_many_redirects",
                            "HTTP redirect limit exceeded.",
                            status=status,
                            url=target.url,
                        )
                    location = response_headers.get("location")
                    if not location:
                        raise _release_http_error(
                            "redirect_location_missing",
                            "HTTP redirect did not include Location.",
                            status=status,
                            url=target.url,
                        )
                    next_url = urllib.parse.urljoin(target.url, location)
                    next_target = _validate_release_url(next_url)
                    if next_target.origin != target.origin:
                        if next_target.origin not in approved_origins:
                            raise _release_http_error(
                                "origin_not_allowed",
                                "Redirect target origin was not reviewed.",
                                status=status,
                                url=next_target.url,
                            )
                        request_headers = _strip_release_cross_origin_secrets(
                            request_headers
                        )
                    redirects += 1
                    target = next_target
                    continue
                if status != 200:
                    raise _release_http_error(
                        "http_error",
                        "HTTP response status is not a successful download.",
                        status=status,
                        url=target.url,
                    )
                return self._read_download_to_path(
                    response,
                    target,
                    response_headers,
                    destination_path,
                    expected_sha256,
                    expected_size,
                    redirects,
                )
            finally:
                _safe_close_release_response(response)


@dataclass(frozen=True)
class ReleaseArchiveExtraction:
    """Inventory returned after a complete safe extraction."""

    root_prefix: str
    root_path: str
    file_count: int
    expanded_size: int


@dataclass(frozen=True)
class _SafeZipMember:
    info: object
    parts: tuple
    canonical_parts: tuple
    is_directory: bool


class SafeZipExtractor:
    """Inspect and stream an untrusted ZIP without using ``extractall``."""

    CHUNK_SIZE = 64 * 1024
    RESERVED_METADATA_PARTS = {
        ".git",
        ".hg",
        ".svn",
        ".bzr",
        ".pypluginstore",
        ".pypluginstore.json",
    }
    WINDOWS_FORBIDDEN_CHARACTERS = '<>:"|?*'

    def __init__(self, limits):
        if not isinstance(limits, ReleaseArchiveLimits):
            raise ValueError("limits must be ReleaseArchiveLimits.")
        self.limits = limits

    def _portable_parts(self, path, label):
        if not isinstance(path, str) or not path:
            raise ValueError(label + " is empty.")
        if (
            path.startswith(("/", "\\"))
            or "\\" in path
            or re.match(r"^[A-Za-z]:", path)
        ):
            raise ValueError(label + " is not a relative POSIX path.")
        parts = path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError(label + " is not a normalized relative path.")

        for part in parts:
            if unicodedata.normalize("NFC", part) != part:
                raise ValueError(label + " must use NFC Unicode normalization.")
            if any(
                unicodedata.category(character) == "Cc"
                for character in part
            ):
                raise ValueError(label + " contains a control character.")
            if part.endswith((".", " ")):
                raise ValueError(label + " has a trailing dot or space.")
            if any(
                character in self.WINDOWS_FORBIDDEN_CHARACTERS
                for character in part
            ):
                raise ValueError(label + " is not portable on Windows.")
            windows_stem = part.split(".", 1)[0].casefold()
            if windows_stem in WINDOWS_RESERVED_PATH_NAMES:
                raise ValueError(label + " contains a Windows-reserved name.")
            if part.casefold() in self.RESERVED_METADATA_PARTS:
                raise ValueError(label + " contains manager or VCS metadata.")
        return tuple(parts)

    def _expected_root(self, expected_root_prefix):
        if expected_root_prefix == ".":
            return "."
        parts = self._portable_parts(
            expected_root_prefix,
            "expected_root_prefix",
        )
        if len(parts) != 1:
            raise ValueError("expected_root_prefix must be one exact wrapper.")
        return parts[0]

    def _member(self, info, expected_root_prefix):
        original_name = getattr(info, "orig_filename", info.filename)
        if original_name != info.filename or "\x00" in original_name:
            raise ValueError("ZIP member contains a NUL byte.")
        if info.flag_bits & (0x1 | 0x40):
            raise ValueError("Encrypted ZIP members are not supported.")

        is_directory = original_name.endswith("/")
        path = original_name[:-1] if is_directory else original_name
        parts = self._portable_parts(path, "ZIP member name")
        if expected_root_prefix != ".":
            if parts[0] != expected_root_prefix:
                raise ValueError("ZIP wrapper does not match the indexed root.")
            if len(parts) == 1 and not is_directory:
                raise ValueError("The indexed ZIP root must be a directory.")

        unix_mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(unix_mode) if info.create_system == 3 else 0
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError("ZIP member has an unsupported file type.")
        if is_directory:
            if file_type == stat.S_IFREG or info.file_size != 0:
                raise ValueError("ZIP directory metadata is inconsistent.")
        elif file_type == stat.S_IFDIR:
            raise ValueError("ZIP file metadata is inconsistent.")

        return _SafeZipMember(
            info=info,
            parts=parts,
            canonical_parts=tuple(part.casefold() for part in parts),
            is_directory=is_directory,
        )

    def _inspect(self, archive, expected_root_prefix):
        try:
            infos = archive.infolist()
        except Exception as error:
            raise ValueError("ZIP central directory is invalid.") from error
        if not infos:
            raise ValueError("ZIP archive is empty.")
        if len(infos) > self.limits.max_entries:
            raise ValueError("ZIP archive contains too many members.")

        members = []
        member_paths = set()
        node_kinds = {}
        component_spellings = {}
        expanded_size = 0
        for info in infos:
            member = self._member(info, expected_root_prefix)
            canonical_parts = member.canonical_parts

            for index, (canonical_part, original_part) in enumerate(
                zip(canonical_parts, member.parts)
            ):
                spelling_key = (canonical_parts[:index], canonical_part)
                previous_spelling = component_spellings.get(spelling_key)
                if (
                    previous_spelling is not None
                    and previous_spelling != original_part
                ):
                    raise ValueError("ZIP member paths collide across hosts.")
                component_spellings[spelling_key] = original_part

            if canonical_parts in member_paths:
                raise ValueError("ZIP archive contains duplicate member paths.")
            for prefix_length in range(1, len(canonical_parts)):
                prefix = canonical_parts[:prefix_length]
                if node_kinds.get(prefix) == "file":
                    raise ValueError("ZIP file and directory paths collide.")
                node_kinds.setdefault(prefix, "directory")

            existing_kind = node_kinds.get(canonical_parts)
            if member.is_directory:
                if existing_kind == "file":
                    raise ValueError("ZIP file and directory paths collide.")
                node_kinds[canonical_parts] = "directory"
            else:
                if existing_kind is not None:
                    raise ValueError("ZIP file and directory paths collide.")
                node_kinds[canonical_parts] = "file"
            member_paths.add(canonical_parts)

            if type(info.file_size) is not int or info.file_size < 0:
                raise ValueError("ZIP member has an invalid expanded size.")
            if type(info.compress_size) is not int or info.compress_size < 0:
                raise ValueError("ZIP member has an invalid compressed size.")
            if not member.is_directory:
                if info.file_size > self.limits.max_file_size:
                    raise ValueError("ZIP member exceeds the per-file limit.")
                expanded_size += info.file_size
                if expanded_size > self.limits.max_expanded_size:
                    raise ValueError("ZIP archive exceeds the expansion limit.")
                if info.file_size:
                    if info.compress_size == 0 or (
                        info.file_size
                        > self.limits.max_compression_ratio
                        * info.compress_size
                    ):
                        raise ValueError(
                            "ZIP member exceeds the compression-ratio limit."
                        )
            members.append(member)
        return members

    def _target_path(self, destination, parts):
        target = os.path.abspath(os.path.join(destination, *parts))
        try:
            inside = os.path.commonpath((destination, target)) == destination
        except ValueError:
            inside = False
        if not inside:
            raise ValueError("ZIP member escapes the extraction directory.")
        return target

    def _extract_members(self, archive, members, destination):
        file_count = 0
        expanded_size = 0
        for member in members:
            target = self._target_path(destination, member.parts)
            if member.is_directory:
                os.makedirs(target, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(target), exist_ok=True)
            member_size = 0
            with archive.open(member.info, "r") as source, open(
                target, "xb"
            ) as output:
                while True:
                    chunk = source.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    member_size += len(chunk)
                    expanded_size += len(chunk)
                    if member_size > self.limits.max_file_size:
                        raise ValueError("ZIP member exceeds the per-file limit.")
                    if expanded_size > self.limits.max_expanded_size:
                        raise ValueError("ZIP archive exceeds the expansion limit.")
                    output.write(chunk)
            if member_size != member.info.file_size:
                raise ValueError("ZIP member size differs from its metadata.")
            file_count += 1
        return file_count, expanded_size

    def _remove_destination(self, destination):
        try:
            if os.path.isdir(destination) and not os.path.islink(destination):
                shutil.rmtree(destination)
            elif os.path.lexists(destination):
                os.remove(destination)
        except FileNotFoundError:
            pass

    def extract(
        self,
        archive_path,
        destination,
        *,
        expected_root_prefix,
    ):
        """Validate then extract one ZIP into a brand-new directory."""
        created_destination = False
        destination_path = None
        try:
            root_prefix = self._expected_root(expected_root_prefix)
            archive_path = os.path.abspath(os.fspath(archive_path))
            destination_path = os.path.abspath(os.fspath(destination))
            if os.path.lexists(destination_path):
                raise ValueError("Extraction destination already exists.")

            path_stat = os.lstat(archive_path)
            if not stat.S_ISREG(path_stat.st_mode):
                raise ValueError("Release archive must be a regular file.")
            open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            open_flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(archive_path, open_flags)
            try:
                archive_stat = os.fstat(descriptor)
                if not stat.S_ISREG(archive_stat.st_mode):
                    raise ValueError("Release archive must be a regular file.")
                if (
                    archive_stat.st_dev != path_stat.st_dev
                    or archive_stat.st_ino != path_stat.st_ino
                ):
                    raise ValueError("Release archive changed while being opened.")
                if archive_stat.st_size > self.limits.max_archive_size:
                    raise ValueError(
                        "Release archive exceeds the compressed limit."
                    )

                with os.fdopen(descriptor, "rb") as archive_file:
                    descriptor = None
                    with zipfile.ZipFile(archive_file, "r") as archive:
                        members = self._inspect(archive, root_prefix)
                        os.mkdir(destination_path)
                        created_destination = True
                        file_count, expanded_size = self._extract_members(
                            archive,
                            members,
                            destination_path,
                        )
            finally:
                if descriptor is not None:
                    os.close(descriptor)

            return ReleaseArchiveExtraction(
                root_prefix=root_prefix,
                root_path=(
                    destination_path
                    if root_prefix == "."
                    else os.path.join(destination_path, root_prefix)
                ),
                file_count=file_count,
                expanded_size=expanded_size,
            )
        except BaseException as error:
            if created_destination and destination_path is not None:
                self._remove_destination(destination_path)
            if isinstance(error, ValueError):
                raise
            if isinstance(error, Exception):
                raise ValueError("Release ZIP extraction failed.") from error
            raise


class ReleaseArtifactValidationError(ValueError):
    """Categorized failure while certifying an extracted release tree."""

    def __init__(self, reason, message=""):
        self.reason = str(reason)
        super().__init__(message or self.reason)


@dataclass(frozen=True)
class ReleaseArtifactValidation:
    """Canonical identity and install inventory for a staged artifact."""

    source_root: str
    plugin_key: str
    identity_source: str
    tree_sha256: str
    artifact_files: dict


@dataclass(frozen=True)
class _ReleaseTreeFile:
    relative_path: str
    physical_path: str
    size: int
    sha256: str
    device: int
    inode: int


class ReleaseArtifactValidationService:
    """Certify a safe extracted tree before transaction staging."""

    CHUNK_SIZE = 64 * 1024

    def __init__(self, plugin, limits=None):
        if plugin is None:
            raise ValueError("plugin is required.")
        if limits is None:
            limits = ReleaseArchiveLimits()
        if not isinstance(limits, ReleaseArchiveLimits):
            raise ValueError("limits must be ReleaseArchiveLimits.")
        self.plugin = plugin
        self.limits = limits
        self.path_validator = SafeZipExtractor(limits)

    def _error(self, reason, message):
        return ReleaseArtifactValidationError(reason, message)

    def _directory_stat(self, path, reason, message):
        try:
            path_stat = os.lstat(path)
        except OSError as error:
            raise self._error(reason, message) from error
        if not stat.S_ISDIR(path_stat.st_mode):
            raise self._error(reason, message)
        return path_stat

    def _root_layout(self, extraction_dir, root_prefix):
        try:
            root_prefix = self.path_validator._expected_root(root_prefix)
        except ValueError as error:
            raise self._error(
                "root_layout",
                "The indexed archive root is invalid.",
            ) from error
        try:
            extraction_path = os.path.abspath(os.fspath(extraction_dir))
        except TypeError as error:
            raise self._error(
                "root_layout",
                "The extraction directory is invalid.",
            ) from error
        self._directory_stat(
            extraction_path,
            "root_layout",
            "The extraction directory is missing or unsafe.",
        )
        if root_prefix == ".":
            return extraction_path
        try:
            entries = list(os.scandir(extraction_path))
        except OSError as error:
            raise self._error(
                "root_layout",
                "The extraction directory could not be inspected.",
            ) from error
        if len(entries) != 1 or entries[0].name != root_prefix:
            raise self._error(
                "root_layout",
                "The extracted archive does not have the indexed wrapper.",
            )
        root_path = os.path.abspath(entries[0].path)
        try:
            inside = os.path.commonpath((extraction_path, root_path)) == (
                extraction_path
            )
        except ValueError:
            inside = False
        if not inside:
            raise self._error(
                "root_layout",
                "The extracted archive root escapes its staging directory.",
            )
        self._directory_stat(
            root_path,
            "root_layout",
            "The indexed archive root is missing or unsafe.",
        )
        return root_path

    def _portable_relative_path(self, relative_path):
        try:
            parts = self.path_validator._portable_parts(
                relative_path,
                "artifact path",
            )
        except ValueError as error:
            raise self._error(
                "unsafe_tree",
                "The extracted artifact contains an unsafe path.",
            ) from error
        try:
            relative_path.encode("utf-8")
        except UnicodeError as error:
            raise self._error(
                "unsafe_tree",
                "The extracted artifact path is not valid UTF-8.",
            ) from error
        return parts

    def _register_components(self, spellings, canonical_parts, parts):
        for index, (canonical_part, original_part) in enumerate(
            zip(canonical_parts, parts)
        ):
            spelling_key = (canonical_parts[:index], canonical_part)
            previous = spellings.get(spelling_key)
            if previous is not None and previous != original_part:
                raise self._error(
                    "unsafe_tree",
                    "The extracted artifact contains a cross-host path collision.",
                )
            spellings[spelling_key] = original_part

    def _opened_regular_file(self, path, path_stat):
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise self._error(
                "unsafe_tree",
                "An artifact file could not be opened safely.",
            ) from error
        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != path_stat.st_dev
                or opened_stat.st_ino != path_stat.st_ino
                or opened_stat.st_size != path_stat.st_size
            ):
                raise self._error(
                    "unsafe_tree",
                    "An artifact file changed while being opened.",
                )
            return descriptor, opened_stat
        except BaseException:
            os.close(descriptor)
            raise

    def _hash_regular_file(self, path, path_stat):
        descriptor, opened_stat = self._opened_regular_file(path, path_stat)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "rb") as source:
                descriptor = None
                while True:
                    chunk = source.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.limits.max_file_size:
                        raise self._error(
                            "unsafe_tree",
                            "An artifact file exceeds the per-file limit.",
                        )
                    digest.update(chunk)
        except ReleaseArtifactValidationError:
            raise
        except OSError as error:
            raise self._error(
                "unsafe_tree",
                "An artifact file could not be read safely.",
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if size != opened_stat.st_size:
            raise self._error(
                "unsafe_tree",
                "An artifact file changed while being read.",
            )
        return size, digest.hexdigest(), opened_stat

    def _scan_tree(self, root_path):
        files = []
        directories = {".": root_path}
        node_spellings = {}
        node_paths = {}
        stack = [(root_path, "")]
        entry_count = 0
        expanded_size = 0

        while stack:
            directory, relative_directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as error:
                raise self._error(
                    "unsafe_tree",
                    "The extracted artifact tree could not be inspected.",
                ) from error
            for entry in entries:
                entry_count += 1
                if entry_count > self.limits.max_entries:
                    raise self._error(
                        "unsafe_tree",
                        "The extracted artifact contains too many entries.",
                    )
                relative_path = (
                    entry.name
                    if not relative_directory
                    else relative_directory + "/" + entry.name
                )
                parts = self._portable_relative_path(relative_path)
                canonical_parts = tuple(part.casefold() for part in parts)
                self._register_components(
                    node_spellings,
                    canonical_parts,
                    parts,
                )
                previous_path = node_paths.get(canonical_parts)
                if previous_path is not None and previous_path != relative_path:
                    raise self._error(
                        "unsafe_tree",
                        "The extracted artifact contains colliding paths.",
                    )
                node_paths[canonical_parts] = relative_path

                try:
                    path_stat = os.stat(
                        entry.path,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise self._error(
                        "unsafe_tree",
                        "An artifact path could not be inspected safely.",
                    ) from error
                if stat.S_ISDIR(path_stat.st_mode):
                    directories[relative_path] = entry.path
                    stack.append((entry.path, relative_path))
                    continue
                if not stat.S_ISREG(path_stat.st_mode):
                    raise self._error(
                        "unsafe_tree",
                        "The extracted artifact contains a link or special file.",
                    )
                if getattr(path_stat, "st_nlink", 1) > 1:
                    raise self._error(
                        "unsafe_tree",
                        "The extracted artifact contains a hard-linked file.",
                    )
                if path_stat.st_size > self.limits.max_file_size:
                    raise self._error(
                        "unsafe_tree",
                        "An artifact file exceeds the per-file limit.",
                    )
                size, digest, opened_stat = self._hash_regular_file(
                    entry.path,
                    path_stat,
                )
                expanded_size += size
                if expanded_size > self.limits.max_expanded_size:
                    raise self._error(
                        "unsafe_tree",
                        "The extracted artifact exceeds the expansion limit.",
                    )
                files.append(
                    _ReleaseTreeFile(
                        relative_path=relative_path,
                        physical_path=entry.path,
                        size=size,
                        sha256=digest,
                        device=opened_stat.st_dev,
                        inode=opened_stat.st_ino,
                    )
                )
        if not files:
            raise self._error(
                "unsafe_tree",
                "The extracted artifact does not contain regular files.",
            )
        return files, directories

    def _tree_digest(self, files):
        records = []
        for file_record in files:
            path_bytes = file_record.relative_path.encode("utf-8")
            record = (
                path_bytes
                + b"\0"
                + str(file_record.size).encode("ascii")
                + b"\0"
                + file_record.sha256.encode("ascii")
                + b"\n"
            )
            records.append((path_bytes, record))
        records.sort(key=lambda item: item[0])
        return hashlib.sha256(
            b"".join(record for _path, record in records)
        ).hexdigest()

    def _tree_fingerprint(self, files):
        return sorted(
            (
                file_record.relative_path,
                file_record.size,
                file_record.sha256,
                file_record.device,
                file_record.inode,
            )
            for file_record in files
        )

    def _source_tree(self, root_path, files, directories, source_path):
        try:
            source_path = _normalize_relative_metadata_path(
                source_path,
                "artifact.source_path",
            )
            if source_path != ".":
                self.path_validator._portable_parts(
                    source_path,
                    "artifact.source_path",
                )
        except ValueError as error:
            raise self._error(
                "source_path",
                "The indexed source path is invalid.",
            ) from error
        if source_path not in directories:
            raise self._error(
                "source_path",
                "The indexed source path is not a staged directory.",
            )
        source_root = root_path if source_path == "." else directories[source_path]
        prefix = "" if source_path == "." else source_path + "/"
        selected = []
        for file_record in files:
            if prefix and not file_record.relative_path.startswith(prefix):
                continue
            installed_path = (
                file_record.relative_path
                if not prefix
                else file_record.relative_path[len(prefix) :]
            )
            if not installed_path:
                continue
            selected.append((installed_path, file_record))
        selected.sort(key=lambda item: item[0].encode("utf-8"))
        return source_root, selected

    def _read_verified_contents(self, file_record):
        try:
            path_stat = os.lstat(file_record.physical_path)
        except OSError as error:
            raise self._error(
                "unsafe_tree",
                "An artifact file disappeared during validation.",
            ) from error
        descriptor, opened_stat = self._opened_regular_file(
            file_record.physical_path,
            path_stat,
        )
        try:
            with os.fdopen(descriptor, "rb") as source:
                descriptor = None
                contents = source.read(self.limits.max_file_size + 1)
        except OSError as error:
            raise self._error(
                "unsafe_tree",
                "An artifact file could not be re-read safely.",
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if (
            len(contents) != file_record.size
            or len(contents) != opened_stat.st_size
            or opened_stat.st_dev != file_record.device
            or opened_stat.st_ino != file_record.inode
            or hashlib.sha256(contents).hexdigest() != file_record.sha256
        ):
            raise self._error(
                "unsafe_tree",
                "An artifact file changed during validation.",
            )
        return contents

    def _compile_python(self, selected_files):
        plugin_metadata = None
        certified_identity = None
        for installed_path, file_record in selected_files:
            if not installed_path.casefold().endswith(".py"):
                continue
            contents = self._read_verified_contents(file_record)
            try:
                compile(
                    contents,
                    file_record.physical_path,
                    "exec",
                    dont_inherit=True,
                )
            except (
                SyntaxError,
                UnicodeError,
                ValueError,
                OverflowError,
                RecursionError,
            ) as error:
                raise self._error(
                    "compile_failed",
                    "Python source failed compilation: " + installed_path,
                ) from error
            if installed_path == "plugin.py":
                try:
                    certified_identity = certify_plugin_py(contents)
                except ValueError as error:
                    raise self._error(
                        "identity_mismatch",
                        "The staged plugin identity could not be certified.",
                    ) from error
                plugin_metadata = self._parse_plugin_metadata(contents)
        return plugin_metadata, certified_identity

    def _parse_plugin_metadata(self, contents):
        try:
            encoding, _lines = tokenize.detect_encoding(
                io.BytesIO(contents).readline
            )
            source_code = contents.decode(encoding)
        except (SyntaxError, UnicodeError, LookupError) as error:
            raise self._error(
                "identity_mismatch",
                "The staged plugin metadata encoding is invalid.",
            ) from error
        plugin_tags = re.findall(
            r"<plugin\b([^>]*)>",
            source_code,
            re.IGNORECASE | re.DOTALL,
        )
        keyed_metadata = []
        for plugin_tag in plugin_tags:
            metadata = {}
            for match in re.finditer(
                r"([A-Za-z_][\w:.-]*)\s*=\s*(\"([^\"]*)\"|'([^']*)')",
                plugin_tag,
            ):
                key = match.group(1).lower()
                if key in metadata:
                    raise self._error(
                        "identity_ambiguous",
                        "The staged plugin metadata contains duplicate attributes.",
                    )
                value = (
                    match.group(3)
                    if match.group(3) is not None
                    else match.group(4)
                )
                metadata[key] = html.unescape(value or "")
            if "key" in metadata:
                keyed_metadata.append(metadata)
        if len(keyed_metadata) != 1:
            raise self._error(
                "identity_mismatch",
                "The staged plugin must contain one keyed Domoticz plugin tag.",
            )
        return keyed_metadata[0]

    def _repository_lookup(self):
        lookup = {}
        keys = list(getattr(self.plugin, "plugin_data", {}))
        for key in getattr(self.plugin, "registry_entries", {}):
            if key not in keys:
                keys.append(key)
        for key in keys:
            entry = self.plugin.get_registry_entry(key)
            if entry is None:
                continue
            identity = normalize_repository_identity(
                entry.author,
                entry.repository,
            )
            if not identity:
                continue
            lookup.setdefault(identity, [])
            if key not in lookup[identity]:
                lookup[identity].append(key)
        return lookup

    def _identity_tier(self, candidates, plugin_key, source):
        unique = []
        for candidate in candidates:
            if candidate and candidate not in unique:
                unique.append(candidate)
        if len(unique) > 1:
            raise self._error(
                "identity_ambiguous",
                "The staged plugin identity matches multiple entries.",
            )
        if not unique:
            return ""
        if unique[0] != plugin_key:
            raise self._error(
                "identity_mismatch",
                "The staged plugin identity resolves to another plugin.",
            )
        return source

    def _certify_identity(
        self,
        source_root,
        plugin_key,
        metadata,
        certified_identity,
        repository_identity,
        expected_domoticz_key,
        expected_plugin_py_sha256,
    ):
        entry = self.plugin.get_registry_entry(plugin_key)
        if entry is None:
            raise self._error(
                "identity_mismatch",
                "The requested plugin is not present in the registry.",
            )
        configured_identity = normalize_repository_identity(
            entry.author,
            entry.repository,
        )
        if not configured_identity:
            raise self._error(
                "identity_mismatch",
                "The registry repository identity is invalid.",
            )
        if repository_identity is not None:
            normalized_identity = normalize_repository_identity(
                repository_identity
            )
            if (
                not normalized_identity
                or normalized_identity != configured_identity
            ):
                raise self._error(
                    "identity_mismatch",
                    "The release repository identity differs from the registry.",
                )

        registered_domoticz_key = str(
            getattr(entry, "domoticz_key", "") or ""
        )
        if expected_domoticz_key:
            if (
                registered_domoticz_key
                and expected_domoticz_key != registered_domoticz_key
            ):
                raise self._error(
                    "identity_mismatch",
                    "The release Domoticz identity differs from the registry.",
                )
            registered_domoticz_key = expected_domoticz_key
        if registered_domoticz_key:
            if (
                certified_identity is None
                or certified_identity.domoticz_key
                != registered_domoticz_key
            ):
                raise self._error(
                    "identity_mismatch",
                    "The staged Domoticz identity differs from the requested package.",
                )
            if (
                expected_plugin_py_sha256
                and certified_identity.plugin_py_sha256
                != expected_plugin_py_sha256
            ):
                raise self._error(
                    "identity_mismatch",
                    "The staged plugin.py differs from the certified release.",
                )
            return "certified plugin.py identity"

        try:
            lookups = self.plugin.build_installed_plugin_lookup()
        except Exception as error:
            raise self._error(
                "identity_mismatch",
                "The staged plugin identity could not be certified.",
            ) from error
        source_folder = os.path.basename(source_root)

        external_link = metadata.get("externallink", "")
        if external_link:
            external_identity = normalize_repository_identity(external_link)
            if not external_identity:
                raise self._error(
                    "identity_mismatch",
                    "The staged plugin externallink is invalid.",
                )
            source = self._identity_tier(
                self._repository_lookup().get(external_identity, []),
                plugin_key,
                "plugin.py externallink",
            )
            if source:
                return source

        exact_key = lookups[0].get(
            self.plugin.normalize_plugin_folder_name(source_folder),
            "",
        )
        source = self._identity_tier(
            [exact_key] if exact_key else [],
            plugin_key,
            "exact folder key",
        )
        if source:
            return source

        folder_tiers = (
            (
                lookups[1].get(
                    self.plugin.normalize_plugin_folder_name(source_folder),
                    [],
                ),
                "repository/archive folder name",
            ),
            (
                lookups[2].get(
                    self.plugin.normalize_plugin_metadata_value(source_folder),
                    [],
                ),
                "normalized folder name",
            ),
        )
        for candidates, source_label in folder_tiers:
            if len(set(candidates)) > 1:
                raise self._error(
                    "identity_ambiguous",
                    "The staged plugin identity matches multiple entries.",
                )
            if candidates and self.plugin.plugin_metadata_matches_registry(
                candidates[0],
                source_root,
                metadata,
            ):
                source = self._identity_tier(
                    candidates,
                    plugin_key,
                    source_label,
                )
                if source:
                    return source

        metadata_candidates = []
        for field in ("key", "name"):
            normalized = self.plugin.normalize_plugin_metadata_value(
                metadata.get(field, "")
            )
            for candidate in lookups[3].get(normalized, []):
                if candidate not in metadata_candidates:
                    metadata_candidates.append(candidate)
        source = self._identity_tier(
            metadata_candidates,
            plugin_key,
            "plugin.py key/name",
        )
        if source:
            return source
        raise self._error(
            "identity_mismatch",
            "The staged plugin identity does not match the requested plugin.",
        )

    def validate(
        self,
        *,
        extraction_dir,
        root_prefix,
        source_path,
        plugin_key,
        expected_tree_sha256=None,
        repository_identity=None,
        expected_domoticz_key=None,
        expected_plugin_py_sha256=None,
    ):
        """Return canonical identity only after every runtime check succeeds."""
        try:
            plugin_key = _require_plugin_key(plugin_key)
        except ValueError as error:
            raise self._error(
                "identity_mismatch",
                "The requested plugin key is invalid.",
            ) from error
        if expected_tree_sha256 is not None:
            try:
                expected_tree_sha256 = _require_sha256(
                    expected_tree_sha256,
                    "expected_tree_sha256",
                )
            except ValueError as error:
                raise self._error(
                    "tree_mismatch",
                    "The expected artifact tree digest is invalid.",
                ) from error
        if bool(expected_domoticz_key) != bool(expected_plugin_py_sha256):
            raise self._error(
                "identity_mismatch",
                "The certified release identity is incomplete.",
            )
        if expected_domoticz_key:
            try:
                expected_domoticz_key = _require_nonempty_string(
                    expected_domoticz_key,
                    "expected_domoticz_key",
                )
                expected_plugin_py_sha256 = _require_sha256(
                    expected_plugin_py_sha256,
                    "expected_plugin_py_sha256",
                )
            except ValueError as error:
                raise self._error(
                    "identity_mismatch",
                    "The certified release identity is invalid.",
                ) from error

        root_path = self._root_layout(extraction_dir, root_prefix)
        files, directories = self._scan_tree(root_path)
        tree_sha256 = self._tree_digest(files)
        if (
            expected_tree_sha256 is not None
            and tree_sha256 != expected_tree_sha256
        ):
            raise self._error(
                "tree_mismatch",
                "The staged artifact tree differs from the release index.",
            )
        source_root, selected_files = self._source_tree(
            root_path,
            files,
            directories,
            source_path,
        )
        selected_by_path = dict(selected_files)
        plugin_file = selected_by_path.get("plugin.py")
        if plugin_file is None or plugin_file.size <= 0:
            raise self._error(
                "plugin_missing",
                "The selected source tree requires a non-empty root plugin.py.",
            )
        metadata, certified_identity = self._compile_python(selected_files)
        if metadata is None or certified_identity is None:
            raise self._error(
                "identity_mismatch",
                "The staged plugin metadata could not be read.",
            )
        identity_source = self._certify_identity(
            source_root,
            plugin_key,
            metadata,
            certified_identity,
            repository_identity,
            expected_domoticz_key,
            expected_plugin_py_sha256,
        )
        revalidated_files, _revalidated_directories = self._scan_tree(root_path)
        if self._tree_fingerprint(revalidated_files) != self._tree_fingerprint(
            files
        ):
            raise self._error(
                "unsafe_tree",
                "The staged artifact changed during validation.",
            )
        artifact_files = {
            installed_path: {
                "sha256": file_record.sha256,
                "size": file_record.size,
            }
            for installed_path, file_record in selected_files
        }
        return ReleaseArtifactValidation(
            source_root=source_root,
            plugin_key=plugin_key,
            identity_source=identity_source,
            tree_sha256=tree_sha256,
            artifact_files=artifact_files,
        )


@dataclass
class InstallMetadata:
    """Validated audit record stored in a release-managed plugin folder."""

    schema: int
    plugin_key: str
    management_mode: str
    repository_identity: str
    version: str
    tag: str
    release_id: str
    release_revision: int
    released_at: str
    commit: str
    artifact_sha256: str
    artifact_tree_sha256: str
    artifact_provenance: str
    artifact_files: dict
    preserved_files: dict
    index_sequence: int
    installed_at: str
    source_revision: str = ""
    migration_source_commit: str = ""
    migration_inventory_sha256: str = ""

    @classmethod
    def from_document(cls, document):
        """Parse strict schema-v2 release-managed install metadata."""
        return cls._from_document(
            document,
            schema_version=INSTALL_METADATA_SCHEMA_VERSION,
            identity_field="package_id",
        )

    @classmethod
    def from_legacy_document(cls, document):
        """Normalize one explicit schema-v1 install metadata document."""
        return cls._from_document(
            document,
            schema_version=LEGACY_INSTALL_METADATA_SCHEMA_VERSION,
            identity_field="plugin_key",
        )

    @classmethod
    def _from_document(cls, document, *, schema_version, identity_field):
        document = _require_document(
            document,
            "install metadata",
            (
                "schema",
                identity_field,
                "management_mode",
                "repository_identity",
                "version",
                "tag",
                "release_id",
                "release_revision",
                "released_at",
                "commit",
                "artifact_sha256",
                "artifact_tree_sha256",
                "artifact_provenance",
                "artifact_files",
                "preserved_files",
                "index_sequence",
                "installed_at",
            ),
            (
                "source_revision",
                "migration_source_commit",
                "migration_inventory_sha256",
            ),
        )
        if (
            type(document["schema"]) is not int
            or document["schema"] != schema_version
        ):
            raise ValueError("Install metadata schema is unsupported.")

        management_mode = _require_nonempty_string(
            document["management_mode"], "management_mode"
        )
        if management_mode != "release":
            raise ValueError("Install metadata must be release-managed.")

        raw_repository_identity = _require_nonempty_string(
            document["repository_identity"], "repository_identity"
        )
        repository_identity = normalize_repository_identity(
            raw_repository_identity
        )
        if (
            not repository_identity
            or repository_identity != raw_repository_identity
        ):
            raise ValueError("repository_identity must be normalized.")

        released_at = _require_nonempty_string(
            document["released_at"], "released_at"
        )
        installed_at = _require_nonempty_string(
            document["installed_at"], "installed_at"
        )
        released_time = _parse_utc_timestamp(released_at, "released_at")
        installed_time = _parse_utc_timestamp(installed_at, "installed_at")
        if installed_time < released_time:
            raise ValueError("installed_at cannot precede released_at.")

        source_revision = document.get("source_revision", "")
        if source_revision:
            source_revision = _require_nonempty_string(
                source_revision, "source_revision"
            )
        commit = _require_git_commit(
            document["commit"], "commit", required=not source_revision
        )
        if not commit and not source_revision:
            raise ValueError("Install metadata requires an immutable source revision.")

        migration_source_present = "migration_source_commit" in document
        migration_inventory_present = (
            "migration_inventory_sha256" in document
        )
        if migration_source_present != migration_inventory_present:
            raise ValueError(
                "Git migration metadata requires both audit fields."
            )
        migration_source_commit = ""
        migration_inventory_sha256 = ""
        if migration_source_present:
            migration_source_commit = _require_git_commit(
                document["migration_source_commit"],
                "migration_source_commit",
            )
            migration_inventory_sha256 = _require_sha256(
                document["migration_inventory_sha256"],
                "migration_inventory_sha256",
            )

        tag = document["tag"]
        if (
            not isinstance(tag, str)
            or tag != tag.strip()
            or CONTROL_CHARACTER_PATTERN.search(tag)
        ):
            raise ValueError("tag must be a canonical string.")

        preserved_document = document["preserved_files"]
        if not isinstance(preserved_document, dict):
            raise ValueError("preserved_files must be an object.")
        preserved_files = {}
        normalized_paths = set()
        for raw_path, raw_digest in preserved_document.items():
            path = _require_audit_path(raw_path, "preserved_files path")
            collision_key = unicodedata.normalize("NFC", path).casefold()
            if collision_key in normalized_paths:
                raise ValueError("preserved_files contains colliding paths.")
            normalized_paths.add(collision_key)
            preserved_files[path] = _require_sha256(
                raw_digest, "preserved_files digest"
            )

        artifact_provenance = _require_nonempty_string(
            document["artifact_provenance"], "artifact_provenance"
        )
        if artifact_provenance not in RELEASE_ARTIFACT_PROVENANCE:
            raise ValueError("artifact_provenance is unsupported.")
        artifact_files_document = document["artifact_files"]
        if not isinstance(artifact_files_document, dict) or not artifact_files_document:
            raise ValueError("artifact_files must be a non-empty object.")
        artifact_files = {}
        artifact_collision_keys = set()
        for raw_path, raw_record in artifact_files_document.items():
            path = _require_artifact_file_path(raw_path, "artifact_files path")
            collision_key = path.casefold()
            if collision_key in artifact_collision_keys:
                raise ValueError("artifact_files contains colliding paths.")
            artifact_collision_keys.add(collision_key)
            raw_record = _require_document(
                raw_record,
                "artifact_files record",
                ("sha256", "size"),
            )
            artifact_files[path] = {
                "sha256": _require_sha256(
                    raw_record["sha256"], "artifact_files digest"
                ),
                "size": _require_positive_integer(
                    raw_record["size"], "artifact_files size"
                ),
            }
        if "plugin.py" not in {path.casefold() for path in artifact_files}:
            raise ValueError("artifact_files must include root plugin.py.")

        return cls(
            schema=INSTALL_METADATA_SCHEMA_VERSION,
            plugin_key=_require_plugin_key(
                document[identity_field], identity_field
            ),
            management_mode=management_mode,
            repository_identity=repository_identity,
            version=_require_nonempty_string(document["version"], "version"),
            tag=tag,
            release_id=_require_nonempty_string(
                document["release_id"], "release_id"
            ),
            release_revision=_require_positive_integer(
                document["release_revision"], "release_revision"
            ),
            released_at=released_at,
            commit=commit,
            artifact_sha256=_require_sha256(
                document["artifact_sha256"], "artifact_sha256"
            ),
            artifact_tree_sha256=_require_sha256(
                document["artifact_tree_sha256"], "artifact_tree_sha256"
            ),
            artifact_provenance=artifact_provenance,
            artifact_files=artifact_files,
            preserved_files=preserved_files,
            index_sequence=_require_positive_integer(
                document["index_sequence"], "index_sequence"
            ),
            installed_at=installed_at,
            source_revision=source_revision,
            migration_source_commit=migration_source_commit,
            migration_inventory_sha256=migration_inventory_sha256,
        )

    def to_document(self):
        """Return the canonical JSON-compatible install audit document."""
        document = {
            "schema": INSTALL_METADATA_SCHEMA_VERSION,
            "package_id": self.plugin_key,
            "management_mode": self.management_mode,
            "repository_identity": self.repository_identity,
            "version": self.version,
            "tag": self.tag,
            "release_id": self.release_id,
            "release_revision": self.release_revision,
            "released_at": self.released_at,
            "commit": self.commit,
            "artifact_sha256": self.artifact_sha256,
            "artifact_tree_sha256": self.artifact_tree_sha256,
            "artifact_provenance": self.artifact_provenance,
            "artifact_files": {
                path: dict(record)
                for path, record in self.artifact_files.items()
            },
            "preserved_files": dict(self.preserved_files),
            "index_sequence": self.index_sequence,
            "installed_at": self.installed_at,
        }
        if self.source_revision:
            document["source_revision"] = self.source_revision
        if self.migration_source_commit:
            document["migration_source_commit"] = (
                self.migration_source_commit
            )
            document["migration_inventory_sha256"] = (
                self.migration_inventory_sha256
            )
        return document

    @property
    def package_id(self):
        """Expose the durable package identity without breaking callers."""
        return self.plugin_key


class InstallMetadataService:
    """Read and durably replace manager-owned plugin install metadata."""

    FILE_NAME = ".pypluginstore.json"
    TEMP_FILE_NAME = ".pypluginstore.json.tmp"

    def __init__(self, plugin):
        self.plugin = plugin

    def _paths(self, plugin_dir):
        """Return committed and temporary metadata paths for a plugin."""
        plugin_dir = os.path.abspath(str(plugin_dir))
        if not os.path.isdir(plugin_dir) or os.path.islink(plugin_dir):
            raise ValueError("Plugin metadata directory must be a real directory.")
        return (
            plugin_dir,
            os.path.join(plugin_dir, self.FILE_NAME),
            os.path.join(plugin_dir, self.TEMP_FILE_NAME),
        )

    def _fsync_directory(self, path):
        """Persist directory changes where supported by the host platform."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            if os.name == "nt":
                return
            raise
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _discard_temp(self, plugin_dir, temporary_path):
        """Discard an uncommitted write without ever promoting its contents."""
        if not os.path.lexists(temporary_path):
            return
        if os.path.isdir(temporary_path) and not os.path.islink(temporary_path):
            raise ValueError("Install metadata temporary path is not a file.")
        os.unlink(temporary_path)
        self._fsync_directory(plugin_dir)

    def _read_committed(self, metadata_path):
        """Parse one committed metadata file without changing the filesystem."""
        if os.path.islink(metadata_path) or not os.path.isfile(metadata_path):
            raise ValueError("Install metadata path is not a regular file.")
        try:
            with open(metadata_path, "rb") as metadata_file:
                document = _load_json_object(
                    metadata_file.read(), self.FILE_NAME
                )
        except OSError as error:
            raise ValueError(
                "Could not read install metadata: " + str(error)
            ) from error
        is_legacy = (
            type(document.get("schema")) is int
            and document.get("schema")
            == LEGACY_INSTALL_METADATA_SCHEMA_VERSION
        )
        parser = (
            InstallMetadata.from_legacy_document
            if is_legacy
            else InstallMetadata.from_document
        )
        return parser(document), is_legacy

    def inspect(self, plugin_dir):
        """Read committed metadata without cleanup or schema upgrades."""
        _plugin_dir, metadata_path, _temporary_path = self._paths(plugin_dir)
        if not os.path.lexists(metadata_path):
            return None
        metadata, _is_legacy = self._read_committed(metadata_path)
        return metadata

    def read(self, plugin_dir):
        """Read committed metadata after discarding any orphan temporary file."""
        plugin_dir, metadata_path, temporary_path = self._paths(plugin_dir)
        self._discard_temp(plugin_dir, temporary_path)
        if not os.path.lexists(metadata_path):
            return None
        metadata, is_legacy = self._read_committed(metadata_path)
        if is_legacy:
            self.write(plugin_dir, metadata)
        return metadata

    def write(self, plugin_dir, metadata):
        """Fsync and atomically replace committed install metadata."""
        if not isinstance(metadata, InstallMetadata):
            raise ValueError("metadata must be validated InstallMetadata.")
        document = metadata.to_document()
        InstallMetadata.from_document(document)
        plugin_dir, metadata_path, temporary_path = self._paths(plugin_dir)
        self._discard_temp(plugin_dir, temporary_path)
        contents = (
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

        try:
            with open(temporary_path, "xb") as metadata_file:
                metadata_file.write(contents)
                metadata_file.flush()
                os.fsync(metadata_file.fileno())
            os.replace(temporary_path, metadata_path)
            self._fsync_directory(plugin_dir)
        finally:
            if os.path.lexists(temporary_path):
                if os.path.isdir(temporary_path) and not os.path.islink(
                    temporary_path
                ):
                    raise ValueError(
                        "Install metadata temporary path is not a file."
                    )
                os.unlink(temporary_path)
                self._fsync_directory(plugin_dir)


class ReleasePreservationError(RuntimeError):
    """A local-data inventory that cannot safely be overlaid."""

    status = "preservation_blocked"

    def __init__(self, reason, message="", paths=()):
        self.reason = str(reason)
        self.message = str(message or reason)
        self.paths = sorted(set(str(path) for path in paths))
        super().__init__(self.message)


@dataclass(frozen=True)
class ReleasePreservationInventoryEntry:
    """One content-bound candidate from an installed plugin tree."""

    relative_path: str
    sha256: str
    size: int
    classification: str


@dataclass(frozen=True)
class ReleasePreservationInventory:
    """Immutable inputs and audit digest for a preservation decision."""

    installed_dir: str
    operation: str
    entries: dict
    unknown_paths: list
    approval_required_paths: list
    ignored_paths: list
    tracked_changes: list
    untracked_files: list
    mutable_paths: list
    artifact_files: dict
    preserved_files: dict
    sha256: str


@dataclass(frozen=True)
class ReleasePreservationOverlay:
    """Audit data for files copied over a pristine staged artifact."""

    operation: str
    inventory_sha256: str
    preserved_paths: list
    preserved_files: dict


@dataclass(frozen=True)
class _ReleasePreservationNode:
    relative_path: str
    physical_path: str
    path_stat: object
    sha256: str = ""


RELEASE_VOLATILE_CACHE_DIRECTORIES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


class ReleasePreservationService:
    """Inventory and overlay only reviewed, non-executable local data."""

    OPERATIONS = {
        "release_update",
        "rollback",
        "channel_switch",
        "git_migration",
    }
    TRIGGERS = {"automatic", "manual"}
    CACHE_DIRECTORIES = RELEASE_VOLATILE_CACHE_DIRECTORIES
    EXECUTABLE_SUFFIXES = {
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".dylib",
        ".exe",
        ".pyd",
        ".py",
        ".pyc",
        ".pyo",
        ".ps1",
        ".sh",
        ".so",
    }
    WINDOWS_FORBIDDEN_CHARACTERS = '<>"|?*'
    CHUNK_SIZE = 64 * 1024

    def __init__(
        self,
        plugin,
        *,
        max_file_size=50 * 1024 * 1024,
        max_total_size=250 * 1024 * 1024,
        max_files=5000,
    ):
        if plugin is None:
            raise ValueError("plugin is required.")
        for name, value in (
            ("max_file_size", max_file_size),
            ("max_total_size", max_total_size),
            ("max_files", max_files),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(name + " must be a positive integer.")
        self.plugin = plugin
        self.max_file_size = max_file_size
        self.max_total_size = max_total_size
        self.max_files = max_files

    def _error(self, reason, message, paths=()):
        return ReleasePreservationError(
            reason,
            message,
            paths=paths,
        )

    def _collision_key(self, path):
        return unicodedata.normalize("NFC", path).casefold()

    def _path_parts_key(self, path):
        return tuple(
            unicodedata.normalize("NFC", part).casefold()
            for part in path.split("/")
        )

    def _is_executable_path(self, path):
        suffix = os.path.splitext(path.rsplit("/", 1)[-1])[1].casefold()
        return suffix in self.EXECUTABLE_SUFFIXES

    def _portable_path(self, value, *, policy=False):
        raw_value = str(value)
        try:
            path = _require_audit_path(raw_value, "preserved path")
            if any(
                character in self.WINDOWS_FORBIDDEN_CHARACTERS
                for character in path
            ):
                raise ValueError("preserved path is not portable.")
            if policy and self._is_executable_path(path):
                raise ValueError("preserved path names executable code.")
            return path
        except ValueError as error:
            raise self._error(
                "unsafe_preserved_path",
                "The preservation policy contains an unsafe path.",
                [raw_value],
            ) from error

    def _normalized_paths(self, values, *, policy=False):
        if not isinstance(values, (list, tuple)):
            raise self._error(
                "unsafe_preserved_path",
                "Preservation paths must be a list.",
            )
        paths = []
        spellings = {}
        for value in values:
            path = self._portable_path(value, policy=policy)
            key = self._collision_key(path)
            previous = spellings.get(key)
            if previous is not None:
                raise self._error(
                    "path_collision",
                    "Preservation paths collide on a supported host.",
                    [previous, path],
                )
            spellings[key] = path
            paths.append(path)
        return sorted(paths)

    def _normalize_artifact_files(self, artifact_files):
        if not isinstance(artifact_files, Mapping):
            raise ValueError("artifact_files must be a mapping.")
        normalized = {}
        spellings = {}
        for raw_path, raw_record in artifact_files.items():
            path = _require_artifact_file_path(
                raw_path,
                "artifact_files path",
            )
            if isinstance(raw_record, str):
                digest = _require_sha256(
                    raw_record,
                    "artifact_files digest",
                )
            elif isinstance(raw_record, Mapping):
                digest = _require_sha256(
                    raw_record.get("sha256"),
                    "artifact_files digest",
                )
            else:
                raise ValueError("artifact_files record is invalid.")
            key = self._collision_key(path)
            if key in spellings:
                raise ValueError("artifact_files contains colliding paths.")
            spellings[key] = path
            normalized[path] = digest
        return dict(sorted(normalized.items()))

    def _normalize_preserved_files(self, preserved_files):
        if not isinstance(preserved_files, Mapping):
            raise ValueError("preserved_files must be a mapping.")
        normalized = {}
        spellings = {}
        for raw_path, raw_digest in preserved_files.items():
            path = self._portable_path(raw_path)
            digest = _require_sha256(
                raw_digest,
                "preserved_files digest",
            )
            key = self._collision_key(path)
            if key in spellings:
                raise self._error(
                    "path_collision",
                    "Preserved audit paths collide on a supported host.",
                    [spellings[key], path],
                )
            spellings[key] = path
            normalized[path] = digest
        return dict(sorted(normalized.items()))

    def _ignored_path(self, relative_path):
        parts = relative_path.split("/")
        folded = [part.casefold() for part in parts]
        if folded[0] in {".git", ".pypluginstore"}:
            return True
        if folded[0] in {
            ".pypluginstore.json",
            ".pypluginstore.json.tmp",
        }:
            return True
        return any(part in self.CACHE_DIRECTORIES for part in folded)

    def _ignored_leaves(self, path, relative_path):
        try:
            path_stat = os.lstat(path)
        except OSError:
            return [relative_path]
        if not stat.S_ISDIR(path_stat.st_mode) or stat.S_ISLNK(
            path_stat.st_mode
        ):
            return [relative_path]
        leaves = []
        try:
            entries = sorted(os.scandir(path), key=lambda entry: entry.name)
        except OSError:
            return [relative_path]
        for entry in entries:
            child_path = relative_path + "/" + entry.name
            leaves.extend(self._ignored_leaves(entry.path, child_path))
        return leaves

    def _collect_source_nodes(self, installed_dir):
        installed_dir = os.path.abspath(os.fspath(installed_dir))
        try:
            root_stat = os.lstat(installed_dir)
        except OSError as error:
            raise self._error(
                "unsafe_preserved_path",
                "The installed plugin directory is unavailable.",
            ) from error
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(
            root_stat.st_mode
        ):
            raise self._error(
                "unsafe_preserved_path",
                "The installed plugin directory is unsafe.",
            )

        nodes = []
        ignored = []
        stack = [(installed_dir, "")]
        while stack:
            directory, relative_directory = stack.pop()
            try:
                entries = sorted(
                    os.scandir(directory),
                    key=lambda entry: entry.name,
                    reverse=True,
                )
            except OSError as error:
                raise self._error(
                    "unsafe_preserved_path",
                    "The installed plugin tree could not be inspected.",
                ) from error
            for entry in entries:
                relative_path = (
                    entry.name
                    if not relative_directory
                    else relative_directory + "/" + entry.name
                )
                if self._ignored_path(relative_path):
                    ignored.extend(
                        self._ignored_leaves(entry.path, relative_path)
                    )
                    continue
                try:
                    path_stat = os.stat(
                        entry.path,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise self._error(
                        "unsafe_preserved_path",
                        "A local plugin path could not be inspected.",
                        [relative_path],
                    ) from error
                if stat.S_ISDIR(path_stat.st_mode):
                    stack.append((entry.path, relative_path))
                    continue
                nodes.append(
                    _ReleasePreservationNode(
                        relative_path=relative_path,
                        physical_path=entry.path,
                        path_stat=path_stat,
                    )
                )
        return installed_dir, sorted(
            nodes,
            key=lambda node: node.relative_path,
        ), sorted(set(ignored))

    def _hash_regular_file(self, node, *, include_contents=False):
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(node.physical_path, flags)
        except OSError as error:
            raise self._error(
                "unsafe_preserved_path",
                "A preserved file could not be opened safely.",
                [node.relative_path],
            ) from error
        digest = hashlib.sha256()
        contents = []
        size = 0
        try:
            opened_stat = os.fstat(descriptor)
            expected_stat = node.path_stat
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != expected_stat.st_dev
                or opened_stat.st_ino != expected_stat.st_ino
                or opened_stat.st_size != expected_stat.st_size
            ):
                raise self._error(
                    "unsafe_preserved_path",
                    "A preserved file changed while being opened.",
                    [node.relative_path],
                )
            while True:
                chunk = os.read(descriptor, self.CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                if include_contents:
                    contents.append(chunk)
            final_stat = os.fstat(descriptor)
            if (
                size != opened_stat.st_size
                or final_stat.st_size != opened_stat.st_size
                or getattr(final_stat, "st_mtime_ns", None)
                != getattr(opened_stat, "st_mtime_ns", None)
                or getattr(final_stat, "st_ctime_ns", None)
                != getattr(opened_stat, "st_ctime_ns", None)
            ):
                raise self._error(
                    "unsafe_preserved_path",
                    "A preserved file changed while being read.",
                    [node.relative_path],
                )
        finally:
            os.close(descriptor)
        return digest.hexdigest(), size, b"".join(contents)

    def _matches_mutable_path(self, relative_path, mutable_paths):
        return any(
            relative_path == mutable_path
            or relative_path.startswith(mutable_path + "/")
            for mutable_path in mutable_paths
        )

    def _candidate_nodes(
        self,
        nodes,
        artifact_files,
        preserved_files,
        tracked_changes,
        untracked_files,
    ):
        forced_paths = set(tracked_changes) | set(untracked_files)
        candidates = []
        for node in nodes:
            path_stat = node.path_stat
            if not stat.S_ISREG(path_stat.st_mode):
                candidates.append(node)
                continue
            digest, _size, _contents = self._hash_regular_file(node)
            baseline_digest = artifact_files.get(node.relative_path)
            if (
                baseline_digest is None
                or digest != baseline_digest
                or node.relative_path in preserved_files
                or node.relative_path in forced_paths
            ):
                candidates.append(
                    _ReleasePreservationNode(
                        relative_path=node.relative_path,
                        physical_path=node.physical_path,
                        path_stat=node.path_stat,
                        sha256=digest,
                    )
                )
        return candidates

    def _validate_candidate_nodes(self, candidates):
        spellings = {}
        for node in candidates:
            key = self._collision_key(node.relative_path)
            previous = spellings.get(key)
            if previous is not None and previous != node.relative_path:
                raise self._error(
                    "path_collision",
                    "Preservation candidates collide on a supported host.",
                    [previous, node.relative_path],
                )
            spellings[key] = node.relative_path

        for node in candidates:
            self._portable_path(node.relative_path)
            path_stat = node.path_stat
            if stat.S_ISLNK(path_stat.st_mode):
                raise self._error(
                    "unsafe_preserved_path",
                    "Filesystem links cannot be preservation overlays.",
                    [node.relative_path],
                )
            if not stat.S_ISREG(path_stat.st_mode):
                raise self._error(
                    "unsupported_file_type",
                    "Only regular files can be preservation overlays.",
                    [node.relative_path],
                )
            if (
                getattr(path_stat, "st_nlink", 1) > 1
                or stat.S_IMODE(path_stat.st_mode)
                & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                or self._is_executable_path(node.relative_path)
            ):
                raise self._error(
                    "unsafe_preserved_path",
                    "Executable code cannot be a preservation overlay.",
                    [node.relative_path],
                )

        oversized = [
            node.relative_path
            for node in candidates
            if node.path_stat.st_size > self.max_file_size
        ]
        if oversized:
            raise self._error(
                "preservation_limit_exceeded",
                "A preserved file exceeds the per-file size limit.",
                oversized,
            )
        candidate_paths = [node.relative_path for node in candidates]
        if len(candidates) > self.max_files:
            raise self._error(
                "preservation_limit_exceeded",
                "The preservation inventory exceeds the file-count limit.",
                candidate_paths,
            )
        if sum(node.path_stat.st_size for node in candidates) > (
            self.max_total_size
        ):
            raise self._error(
                "preservation_limit_exceeded",
                "The preservation inventory exceeds the total-size limit.",
                candidate_paths,
            )

    def _inventory_digest(
        self,
        operation,
        entries,
        tracked_changes,
        untracked_files,
        mutable_paths,
        artifact_files,
        preserved_files,
    ):
        document = {
            "schema_version": 1,
            "operation": operation,
            "entries": {
                path: {
                    "sha256": entry.sha256,
                    "size": entry.size,
                    "classification": entry.classification,
                }
                for path, entry in sorted(entries.items())
            },
            "tracked_changes": list(tracked_changes),
            "untracked_files": list(untracked_files),
            "mutable_paths": list(mutable_paths),
            "artifact_files": dict(artifact_files),
            "preserved_files": dict(preserved_files),
        }
        contents = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(contents).hexdigest()

    def inventory(
        self,
        *,
        installed_dir,
        artifact_files,
        preserved_files,
        mutable_paths,
        operation,
        tracked_changes,
        untracked_files,
    ):
        """Return a content-bound inventory without mutating either tree."""
        if operation not in self.OPERATIONS:
            raise self._error(
                "invalid_operation",
                "The requested preservation operation is unsupported.",
            )
        mutable_paths = self._normalized_paths(
            mutable_paths,
            policy=True,
        )
        tracked_changes = self._normalized_paths(tracked_changes)
        untracked_files = self._normalized_paths(untracked_files)
        artifact_files = self._normalize_artifact_files(artifact_files)
        preserved_files = self._normalize_preserved_files(preserved_files)
        installed_dir, nodes, ignored_paths = self._collect_source_nodes(
            installed_dir
        )
        candidates = self._candidate_nodes(
            nodes,
            artifact_files,
            preserved_files,
            tracked_changes,
            untracked_files,
        )
        self._validate_candidate_nodes(candidates)

        entries = {}
        unknown_paths = []
        approval_required_paths = []
        for node in candidates:
            mutable = self._matches_mutable_path(
                node.relative_path,
                mutable_paths,
            )
            previous_digest = preserved_files.get(node.relative_path)
            if previous_digest is not None and node.sha256 == previous_digest:
                classification = "preserved" if mutable else "unknown"
            elif previous_digest is not None:
                classification = "changed_preserved"
                approval_required_paths.append(node.relative_path)
            elif mutable:
                classification = "mutable"
            else:
                classification = "unknown"
            if classification in {"unknown", "changed_preserved"}:
                unknown_paths.append(node.relative_path)
            entries[node.relative_path] = ReleasePreservationInventoryEntry(
                relative_path=node.relative_path,
                sha256=node.sha256,
                size=node.path_stat.st_size,
                classification=classification,
            )
        entries = dict(sorted(entries.items()))
        unknown_paths = sorted(unknown_paths)
        approval_required_paths = sorted(approval_required_paths)
        inventory_sha256 = self._inventory_digest(
            operation,
            entries,
            tracked_changes,
            untracked_files,
            mutable_paths,
            artifact_files,
            preserved_files,
        )
        return ReleasePreservationInventory(
            installed_dir=installed_dir,
            operation=operation,
            entries=entries,
            unknown_paths=unknown_paths,
            approval_required_paths=approval_required_paths,
            ignored_paths=ignored_paths,
            tracked_changes=tracked_changes,
            untracked_files=untracked_files,
            mutable_paths=mutable_paths,
            artifact_files=artifact_files,
            preserved_files=preserved_files,
            sha256=inventory_sha256,
        )

    def _revalidate_inventory(self, inventory_result):
        try:
            current = self.inventory(
                installed_dir=inventory_result.installed_dir,
                artifact_files=inventory_result.artifact_files,
                preserved_files=inventory_result.preserved_files,
                mutable_paths=inventory_result.mutable_paths,
                operation=inventory_result.operation,
                tracked_changes=inventory_result.tracked_changes,
                untracked_files=inventory_result.untracked_files,
            )
        except (ReleasePreservationError, OSError, ValueError) as error:
            raise self._error(
                "inventory_changed",
                "The installed plugin changed after it was inventoried.",
            ) from error
        if current.sha256 != inventory_result.sha256:
            raise self._error(
                "inventory_changed",
                "The installed plugin changed after it was inventoried.",
            )
        return current

    def _approval_paths(self, inventory_result):
        return sorted(
            set(inventory_result.unknown_paths)
            | set(inventory_result.tracked_changes)
        )

    def _authorize_overlay(
        self,
        inventory_result,
        trigger,
        approved_inventory_sha256,
    ):
        if trigger not in self.TRIGGERS:
            raise self._error(
                "invalid_trigger",
                "The preservation trigger is unsupported.",
            )
        if (
            inventory_result.operation == "git_migration"
            and inventory_result.tracked_changes
        ):
            if trigger == "automatic":
                raise self._error(
                    "tracked_changes",
                    "Automatic Release channel selection cannot preserve "
                    "tracked changes.",
                    inventory_result.tracked_changes,
                )
            outside_policy = [
                path
                for path in inventory_result.tracked_changes
                if not self._matches_mutable_path(
                    path,
                    inventory_result.mutable_paths,
                )
            ]
            if outside_policy:
                raise self._error(
                    "tracked_path_not_mutable",
                    "A tracked change is outside reviewed mutable policy.",
                    outside_policy,
                )

        approval_paths = self._approval_paths(inventory_result)
        if inventory_result.approval_required_paths and (
            approved_inventory_sha256 != inventory_result.sha256
        ):
            raise self._error(
                "inventory_approval_required",
                "Changed preserved data requires exact inventory approval.",
                approval_paths,
            )
        if inventory_result.unknown_paths and trigger == "automatic":
            raise self._error(
                "unknown_local_paths",
                "Automatic replacement cannot preserve unknown local paths.",
                inventory_result.unknown_paths,
            )
        if (
            trigger == "manual"
            and approval_paths
            and approved_inventory_sha256 != inventory_result.sha256
        ):
            raise self._error(
                "inventory_approval_required",
                "Manual replacement requires exact inventory approval.",
                approval_paths,
            )

    def _collect_staged_nodes(self, staged_dir):
        staged_dir = os.path.abspath(os.fspath(staged_dir))
        try:
            root_stat = os.lstat(staged_dir)
        except OSError as error:
            raise self._error(
                "unsafe_staged_path",
                "The staged plugin directory is unavailable.",
            ) from error
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(
            root_stat.st_mode
        ):
            raise self._error(
                "unsafe_staged_path",
                "The staged plugin directory is unsafe.",
            )
        nodes = []
        stack = [(staged_dir, "")]
        while stack:
            directory, relative_directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as error:
                raise self._error(
                    "unsafe_staged_path",
                    "The staged plugin tree could not be inspected.",
                ) from error
            for entry in entries:
                relative_path = (
                    entry.name
                    if not relative_directory
                    else relative_directory + "/" + entry.name
                )
                try:
                    path_stat = os.stat(
                        entry.path,
                        follow_symlinks=False,
                    )
                except OSError as error:
                    raise self._error(
                        "unsafe_staged_path",
                        "A staged path could not be inspected.",
                        [relative_path],
                    ) from error
                nodes.append(
                    _ReleasePreservationNode(
                        relative_path=relative_path,
                        physical_path=entry.path,
                        path_stat=path_stat,
                    )
                )
                if stat.S_ISDIR(path_stat.st_mode):
                    stack.append((entry.path, relative_path))
        return staged_dir, nodes

    def _validate_staged_collisions(
        self,
        inventory_result,
        staged_nodes,
    ):
        for path, candidate in inventory_result.entries.items():
            candidate_key = self._path_parts_key(path)
            for staged_node in staged_nodes:
                staged_key = self._path_parts_key(
                    staged_node.relative_path
                )
                related = (
                    candidate_key == staged_key
                    or candidate_key[: len(staged_key)] == staged_key
                    or staged_key[: len(candidate_key)] == candidate_key
                )
                if not related:
                    continue
                if stat.S_ISLNK(staged_node.path_stat.st_mode):
                    raise self._error(
                        "unsafe_staged_path",
                        "A staged link could redirect a preservation overlay.",
                        [path],
                    )
                exact_spelling = path == staged_node.relative_path
                exact_mutable_file = (
                    exact_spelling
                    and candidate.classification in {
                        "mutable",
                        "preserved",
                        "changed_preserved",
                    }
                    and stat.S_ISREG(staged_node.path_stat.st_mode)
                )
                if exact_mutable_file:
                    if getattr(staged_node.path_stat, "st_nlink", 1) > 1:
                        raise self._error(
                            "unsafe_staged_path",
                            "A staged hard link cannot be replaced safely.",
                            [path],
                        )
                    continue
                if candidate_key == staged_key or (
                    stat.S_ISREG(staged_node.path_stat.st_mode)
                    and (
                        candidate_key[: len(staged_key)] == staged_key
                        or staged_key[: len(candidate_key)] == candidate_key
                    )
                ):
                    raise self._error(
                        "packaged_path_collision",
                        "A preservation overlay collides with packaged data.",
                        [path, staged_node.relative_path],
                    )

    def _read_overlay_contents(self, inventory_result):
        contents = {}
        for path, entry in inventory_result.entries.items():
            physical_path = os.path.join(
                inventory_result.installed_dir,
                *path.split("/"),
            )
            try:
                path_stat = os.lstat(physical_path)
            except OSError as error:
                raise self._error(
                    "inventory_changed",
                    "A preserved file disappeared before copying.",
                ) from error
            node = _ReleasePreservationNode(
                relative_path=path,
                physical_path=physical_path,
                path_stat=path_stat,
            )
            try:
                digest, size, data = self._hash_regular_file(
                    node,
                    include_contents=True,
                )
            except ReleasePreservationError as error:
                raise self._error(
                    "inventory_changed",
                    "A preserved file changed before copying.",
                ) from error
            if digest != entry.sha256 or size != entry.size:
                raise self._error(
                    "inventory_changed",
                    "A preserved file changed before copying.",
                )
            contents[path] = (
                data,
                stat.S_IMODE(path_stat.st_mode)
                & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH),
            )
        return contents

    def _safe_parent(self, staged_dir, relative_path, created_directories):
        parent = staged_dir
        for part in relative_path.split("/")[:-1]:
            parent = os.path.join(parent, part)
            if not os.path.lexists(parent):
                os.mkdir(parent, 0o700)
                created_directories.append(parent)
                continue
            parent_stat = os.lstat(parent)
            if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_ISLNK(
                parent_stat.st_mode
            ):
                raise self._error(
                    "unsafe_staged_path",
                    "A staged parent cannot contain an overlay safely.",
                    [relative_path],
                )
        return parent

    def _temporary_file(self, parent, data, mode):
        descriptor, path = tempfile.mkstemp(
            prefix=".pypluginstore-preserve-",
            dir=parent,
        )
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            try:
                os.fchmod(descriptor, mode)
            except (AttributeError, OSError):
                pass
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            if os.path.lexists(path):
                os.unlink(path)
            raise
        os.close(descriptor)
        return path

    def _apply_contents(self, staged_dir, contents):
        prepared = []
        created_directories = []
        backups = []
        activated = []
        try:
            for relative_path, (data, mode) in sorted(contents.items()):
                parent = self._safe_parent(
                    staged_dir,
                    relative_path,
                    created_directories,
                )
                destination = os.path.join(
                    staged_dir,
                    *relative_path.split("/"),
                )
                temporary = self._temporary_file(parent, data, mode)
                prepared.append((relative_path, destination, temporary))

            for relative_path, destination, temporary in prepared:
                backup = ""
                if os.path.lexists(destination):
                    destination_stat = os.lstat(destination)
                    if not stat.S_ISREG(destination_stat.st_mode) or (
                        stat.S_ISLNK(destination_stat.st_mode)
                    ):
                        raise self._error(
                            "unsafe_staged_path",
                            "A staged destination changed before overlay.",
                            [relative_path],
                        )
                    backup = destination + ".pypluginstore-preserve-backup"
                    if os.path.lexists(backup):
                        raise self._error(
                            "unsafe_staged_path",
                            "A preservation backup path already exists.",
                            [relative_path],
                        )
                    os.replace(destination, backup)
                    backups.append((destination, backup))
                os.replace(temporary, destination)
                activated.append(destination)

            for destination, backup in backups:
                if os.path.lexists(backup):
                    os.unlink(backup)
        except BaseException:
            for destination in reversed(activated):
                try:
                    if os.path.lexists(destination):
                        os.unlink(destination)
                except OSError:
                    pass
            for destination, backup in reversed(backups):
                try:
                    if os.path.lexists(backup):
                        os.replace(backup, destination)
                except OSError:
                    pass
            for _relative_path, _destination, temporary in prepared:
                try:
                    if os.path.lexists(temporary):
                        os.unlink(temporary)
                except OSError:
                    pass
            for directory in reversed(created_directories):
                try:
                    os.rmdir(directory)
                except OSError:
                    pass
            raise

    def apply_overlay(
        self,
        inventory_result,
        *,
        staged_dir,
        trigger,
        approved_inventory_sha256=None,
    ):
        """Revalidate and copy an authorized inventory into staging."""
        if not isinstance(
            inventory_result,
            ReleasePreservationInventory,
        ):
            raise ValueError(
                "inventory_result must be ReleasePreservationInventory."
            )
        current = self._revalidate_inventory(inventory_result)
        self._authorize_overlay(
            current,
            trigger,
            approved_inventory_sha256,
        )
        staged_dir, staged_nodes = self._collect_staged_nodes(staged_dir)
        self._validate_staged_collisions(current, staged_nodes)
        contents = self._read_overlay_contents(current)
        self._apply_contents(staged_dir, contents)
        preserved_files = {
            path: current.entries[path].sha256
            for path in sorted(current.entries)
        }
        return ReleasePreservationOverlay(
            operation=current.operation,
            inventory_sha256=current.sha256,
            preserved_paths=sorted(preserved_files),
            preserved_files=preserved_files,
        )


CHANNEL_PREFERENCE_SCHEMA_VERSION = 1
CHANNEL_PREFERENCE_CHOICES = {"keep_git", "release"}


def _normalize_channel_repository_identity(value):
    """Normalize one safe forge-neutral repository identity."""
    value = _require_nonempty_string(value, "repository identity")
    if any(character.isspace() for character in value):
        raise ValueError("Repository identity cannot contain whitespace.")

    scp_style = re.match(r"^[^/@\s:]+@[^/\s:]+:.+$", value)
    if scp_style:
        repository_host = value.split("@", 1)[1].split(":", 1)[0]
        if "?" in value or "#" in value:
            raise ValueError("Repository identity cannot contain URL metadata.")
    else:
        has_scheme = re.match(
            r"^[A-Za-z][A-Za-z0-9+.-]*://", value
        )
        try:
            parsed = urllib.parse.urlparse(value if has_scheme else "//" + value)
            parsed.port
        except ValueError as error:
            raise ValueError("Repository identity is not a valid URL.") from error

        scheme = parsed.scheme.lower()
        if has_scheme and scheme not in ("https", "ssh"):
            raise ValueError(
                "Repository identity must use HTTPS or SSH."
            )
        if parsed.password is not None:
            raise ValueError("Repository identity cannot contain credentials.")
        if scheme == "https" and parsed.username is not None:
            raise ValueError("Repository identity cannot contain credentials.")
        if not has_scheme and (
            parsed.username is not None or parsed.password is not None
        ):
            raise ValueError("Repository identity cannot contain credentials.")
        if parsed.params or parsed.query or parsed.fragment:
            raise ValueError("Repository identity cannot contain URL metadata.")
        repository_host = parsed.hostname or ""

    if (
        not repository_host
        or repository_host in (".", "..")
        or repository_host.startswith(".")
        or repository_host.endswith(".")
        or "\\" in repository_host
    ):
        raise ValueError("Repository identity has an unsafe host.")

    normalized = normalize_repository_identity(value)
    if not normalized:
        raise ValueError("Repository identity is unsafe or incomplete.")
    return normalized


def _require_channel_preference(value):
    """Accept only an explicit, supported local channel choice."""
    if not isinstance(value, str) or value not in CHANNEL_PREFERENCE_CHOICES:
        raise ValueError("Channel preference must be keep_git or release.")
    return value


@dataclass
class ChannelPreferenceDocument:
    """Validated manager-owned repository channel preferences."""

    schema_version: int
    channels: dict

    @classmethod
    def from_document(cls, document):
        """Parse a strict channel-preference state document."""
        document = _require_document(
            document,
            "channel preferences",
            ("schema_version", "channels"),
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"]
            != CHANNEL_PREFERENCE_SCHEMA_VERSION
        ):
            raise ValueError("Channel preference schema is unsupported.")

        raw_channels = document["channels"]
        if not isinstance(raw_channels, dict):
            raise ValueError("channels must be an object.")

        channels = {}
        normalized_identities = set()
        for raw_identity, raw_preference in raw_channels.items():
            normalized_identity = _normalize_channel_repository_identity(
                raw_identity
            )
            if normalized_identity in normalized_identities:
                raise ValueError(
                    "Channel preferences contain colliding identities."
                )
            normalized_identities.add(normalized_identity)
            if normalized_identity != raw_identity:
                raise ValueError(
                    "Channel preference identities must be normalized."
                )
            channels[normalized_identity] = _require_channel_preference(
                raw_preference
            )

        return cls(
            schema_version=CHANNEL_PREFERENCE_SCHEMA_VERSION,
            channels=channels,
        )

    def to_document(self):
        """Return the canonical JSON-compatible preference document."""
        return {
            "schema_version": self.schema_version,
            "channels": dict(self.channels),
        }


class ChannelPreferenceService:
    """Durably store explicit channel choices outside plugin checkouts."""

    STATE_DIRECTORY_NAME = ".pypluginstore"
    FILE_NAME = "channels.json"
    TEMP_FILE_NAME = "channels.json.tmp"

    def __init__(self, plugin):
        self.plugin = plugin

    def _fsync_directory(self, path):
        """Persist directory changes where supported by the host platform."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            if os.name == "nt":
                return
            raise
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _paths(self, create_state_directory=False):
        """Resolve manager-owned state paths without entering a checkout."""
        manager_dir = os.path.abspath(self.plugin.get_plugin_home_folder())
        if not os.path.isdir(manager_dir) or os.path.islink(manager_dir):
            raise ValueError("Plugin manager home must be a real directory.")

        state_dir = os.path.join(manager_dir, self.STATE_DIRECTORY_NAME)
        if os.path.lexists(state_dir):
            if os.path.islink(state_dir) or not os.path.isdir(state_dir):
                raise ValueError(
                    "Plugin manager state path must be a real directory."
                )
        elif create_state_directory:
            os.mkdir(state_dir)
            self._fsync_directory(manager_dir)

        return (
            state_dir,
            os.path.join(state_dir, self.FILE_NAME),
            os.path.join(state_dir, self.TEMP_FILE_NAME),
        )

    def _discard_temp(self, state_dir, temporary_path):
        """Discard an interrupted write without promoting its contents."""
        if not os.path.lexists(temporary_path):
            return
        if os.path.isdir(temporary_path) and not os.path.islink(temporary_path):
            raise ValueError("Channel preference temporary path is not a file.")
        os.unlink(temporary_path)
        self._fsync_directory(state_dir)

    def read(self):
        """Load committed preferences after removing any orphan temp file."""
        state_dir, preferences_path, temporary_path = self._paths()
        if not os.path.isdir(state_dir):
            return ChannelPreferenceDocument(
                schema_version=CHANNEL_PREFERENCE_SCHEMA_VERSION,
                channels={},
            )

        self._discard_temp(state_dir, temporary_path)
        if not os.path.lexists(preferences_path):
            return ChannelPreferenceDocument(
                schema_version=CHANNEL_PREFERENCE_SCHEMA_VERSION,
                channels={},
            )
        if os.path.islink(preferences_path) or not os.path.isfile(
            preferences_path
        ):
            raise ValueError("Channel preference path is not a regular file.")
        try:
            with open(preferences_path, "rb") as preferences_file:
                document = _load_json_object(
                    preferences_file.read(), self.FILE_NAME
                )
        except OSError as error:
            raise ValueError(
                "Could not read channel preferences: " + str(error)
            ) from error
        return ChannelPreferenceDocument.from_document(document)

    def _write(self, preferences):
        """Fsync and atomically replace the preference state document."""
        if not isinstance(preferences, ChannelPreferenceDocument):
            raise ValueError(
                "preferences must be a validated ChannelPreferenceDocument."
            )
        document = preferences.to_document()
        ChannelPreferenceDocument.from_document(document)
        state_dir, preferences_path, temporary_path = self._paths(
            create_state_directory=True
        )
        self._discard_temp(state_dir, temporary_path)
        contents = (
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

        try:
            with open(temporary_path, "xb") as preferences_file:
                preferences_file.write(contents)
                preferences_file.flush()
                os.fsync(preferences_file.fileno())
            os.replace(temporary_path, preferences_path)
            self._fsync_directory(state_dir)
        finally:
            if os.path.lexists(temporary_path):
                if os.path.isdir(temporary_path) and not os.path.islink(
                    temporary_path
                ):
                    raise ValueError(
                        "Channel preference temporary path is not a file."
                    )
                os.unlink(temporary_path)
                self._fsync_directory(state_dir)

    def get(self, repository_identity):
        """Return an explicit preference for one normalized identity."""
        identity = _normalize_channel_repository_identity(repository_identity)
        return self.read().channels.get(identity)

    def set(self, repository_identity, preference):
        """Set one explicit channel choice while preserving all others."""
        identity = _normalize_channel_repository_identity(repository_identity)
        preference = _require_channel_preference(preference)
        channels = dict(self.read().channels)
        channels[identity] = preference
        self._write(
            ChannelPreferenceDocument(
                schema_version=CHANNEL_PREFERENCE_SCHEMA_VERSION,
                channels=channels,
            )
        )

    def clear(self, repository_identity):
        """Clear one explicit choice and leave unrelated choices intact."""
        identity = _normalize_channel_repository_identity(repository_identity)
        channels = dict(self.read().channels)
        if identity not in channels:
            return
        del channels[identity]
        self._write(
            ChannelPreferenceDocument(
                schema_version=CHANNEL_PREFERENCE_SCHEMA_VERSION,
                channels=channels,
            )
        )


RELEASE_TRANSACTION_SCHEMA_VERSION = 3
PREVIOUS_RELEASE_TRANSACTION_SCHEMA_VERSION = 2
LEGACY_RELEASE_TRANSACTION_SCHEMA_VERSION = 1
RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION = 3
PREVIOUS_RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION = 2
LEGACY_RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION = 1
RELEASE_TRANSACTION_OPERATIONS = {
    "release_install",
    "release_update",
    "release_migration",
}
RELEASE_TRANSACTION_FINAL_PHASES = {
    "dependency_blocked",
    "release_managed",
    "restart_pending",
    "rolled_back",
    "stale_target",
}
RELEASE_TRANSACTION_PREACTIVATION_PHASES = {
    "created",
    "staged_verified",
    "dependency_confirmation_required",
    "dependencies_staged",
    "dependency_blocked",
}
RELEASE_MIGRATION_RESTORE_PHASES = {
    "migration_restore_pending",
    "migration_restore_completed",
}
RELEASE_GLOBAL_DEPENDENCY_ROLLBACK_PHASES = {
    "dependencies_backup_pending",
    "dependencies_backed_up",
    "dependencies_activation_pending",
    "dependencies_activated",
    "code_activation_pending",
    "release_activated",
    "restart_pending",
    "release_managed",
}
RELEASE_TRANSACTION_ACTIVE_PHASES = {
    "created",
    "staged_verified",
    "dependency_confirmation_required",
    "dependencies_staged",
    "code_backup_pending",
    "code_backed_up",
    *RELEASE_MIGRATION_RESTORE_PHASES,
    "dependencies_backup_pending",
    "dependencies_backed_up",
    "dependencies_activation_pending",
    "dependencies_activated",
    "code_activation_pending",
    "release_activated",
    "rollback_pending",
    "queued_locked",
    "restart_pending",
}
RELEASE_TRANSACTION_PHASES = RELEASE_TRANSACTION_FINAL_PHASES | {
    "created",
    "staged_verified",
    "dependency_confirmation_required",
    "dependencies_staged",
    "code_backup_pending",
    "code_backed_up",
    *RELEASE_MIGRATION_RESTORE_PHASES,
    "dependencies_backup_pending",
    "dependencies_backed_up",
    "dependencies_activation_pending",
    "dependencies_activated",
    "code_activation_pending",
    "release_activated",
    "rollback_pending",
    "queued_locked",
}
_RELEASE_TRANSACTION_LOCKS = {}
_RELEASE_TRANSACTION_LOCKS_GUARD = threading.Lock()


def _transaction_json_copy(value, label):
    """Return a JSON-only deep copy for a durable transaction descriptor."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        )
        return json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(label + " must contain only finite JSON values.") from error


def _validated_dependency_tree(value, label, allow_none=False):
    if value is None and allow_none:
        return None
    value = _require_document(value, label, ("present", "tree"))
    present = _require_boolean(value["present"], label + ".present")
    tree = value["tree"]
    if not present:
        if tree is not None:
            raise ValueError(label + ".tree must be null when absent.")
        return {"present": False, "tree": None}
    tree = _require_document(
        tree,
        label + ".tree",
        ("sha256", "files", "directories", "bytes"),
    )
    normalized = {"sha256": _require_sha256(tree["sha256"], label + ".sha256")}
    for field in ("files", "directories", "bytes"):
        field_value = tree[field]
        if type(field_value) is not int or field_value < 0:
            raise ValueError(label + "." + field + " must be a non-negative integer.")
        normalized[field] = field_value
    return {"present": True, "tree": normalized}


def _validated_migration_snapshot(value):
    """Validate the content-bound Git state required by a migration."""
    value = _require_document(
        value,
        "expected_current.migration_snapshot",
        (
            "repository_identity",
            "release_commit",
            "relationship",
            "inventory_sha256",
            "tracked_changes",
            "untracked_files",
            "mutable_paths",
            "shallow",
        ),
    )
    raw_identity = _require_nonempty_string(
        value["repository_identity"],
        "migration_snapshot.repository_identity",
    )
    repository_identity = normalize_repository_identity(raw_identity)
    if not repository_identity or repository_identity != raw_identity:
        raise ValueError(
            "migration_snapshot.repository_identity must be normalized."
        )
    relationship = _require_nonempty_string(
        value["relationship"],
        "migration_snapshot.relationship",
    )
    if relationship not in {
        "equal",
        "release_descendant",
        "installed_ahead",
        "diverged",
    }:
        raise ValueError(
            "migration_snapshot.relationship is unsupported."
        )

    def canonical_paths(field_name):
        paths = value[field_name]
        if not isinstance(paths, list):
            raise ValueError(
                "migration_snapshot."
                + field_name
                + " must be a list."
            )
        normalized = [
            _require_audit_path(
                path,
                "migration_snapshot." + field_name + " entry",
            )
            for path in paths
        ]
        if normalized != sorted(set(normalized)):
            raise ValueError(
                "migration_snapshot."
                + field_name
                + " must be sorted and unique."
            )
        return normalized

    return {
        "repository_identity": repository_identity,
        "release_commit": _require_git_commit(
            value["release_commit"],
            "migration_snapshot.release_commit",
        ),
        "relationship": relationship,
        "inventory_sha256": _require_sha256(
            value["inventory_sha256"],
            "migration_snapshot.inventory_sha256",
        ),
        "tracked_changes": canonical_paths("tracked_changes"),
        "untracked_files": canonical_paths("untracked_files"),
        "mutable_paths": canonical_paths("mutable_paths"),
        "shallow": _require_boolean(
            value["shallow"],
            "migration_snapshot.shallow",
        ),
    }


def _validated_dependency_state(value):
    value = _require_document(
        value,
        "dependency_state",
        ("expected", "target"),
    )
    return {
        "expected": _validated_dependency_tree(
            value["expected"],
            "dependency_state.expected",
        ),
        "target": _validated_dependency_tree(
            value["target"],
            "dependency_state.target",
            allow_none=True,
        ),
    }


def _validated_staged_snapshot(value, allow_none=False):
    if value is None and allow_none:
        return None
    value = _require_document(
        value,
        "staged_snapshot",
        ("install_metadata_sha256", "artifact_files", "preserved_files"),
    )
    metadata_sha256 = _require_sha256(
        value["install_metadata_sha256"],
        "staged_snapshot.install_metadata_sha256",
    )
    artifact_files = value["artifact_files"]
    if not isinstance(artifact_files, dict) or not artifact_files:
        raise ValueError("staged_snapshot.artifact_files must be non-empty.")
    normalized_artifacts = {}
    artifact_spellings = {}
    for raw_path, raw_record in artifact_files.items():
        path = _require_artifact_file_path(
            raw_path,
            "staged_snapshot.artifact_files path",
        )
        collision_key = path.casefold()
        if collision_key in artifact_spellings:
            raise ValueError(
                "staged_snapshot.artifact_files contains colliding paths."
            )
        artifact_spellings[collision_key] = path
        raw_record = _require_document(
            raw_record,
            "staged_snapshot artifact record",
            ("sha256", "size"),
        )
        normalized_artifacts[path] = {
            "sha256": _require_sha256(
                raw_record["sha256"],
                "staged_snapshot artifact digest",
            ),
            "size": _require_positive_integer(
                raw_record["size"],
                "staged_snapshot artifact size",
            ),
        }
    preserved_files = value["preserved_files"]
    if not isinstance(preserved_files, dict):
        raise ValueError("staged_snapshot.preserved_files must be an object.")
    normalized_preserved = {}
    preserved_spellings = {}
    for raw_path, digest in preserved_files.items():
        path = _require_audit_path(
            raw_path,
            "staged_snapshot preserved path",
        )
        collision_key = path.casefold()
        if collision_key in preserved_spellings:
            raise ValueError(
                "staged_snapshot.preserved_files contains colliding paths."
            )
        artifact_spelling = artifact_spellings.get(collision_key)
        if artifact_spelling is not None and artifact_spelling != path:
            raise ValueError(
                "staged_snapshot inventories contain colliding paths."
            )
        preserved_spellings[collision_key] = path
        normalized_preserved[path] = _require_sha256(
            digest,
            "staged_snapshot preserved digest",
        )
    return {
        "install_metadata_sha256": metadata_sha256,
        "artifact_files": normalized_artifacts,
        "preserved_files": normalized_preserved,
    }


@dataclass(frozen=True)
class ReleaseTransactionPaths:
    journal: str
    staged_code: str
    staged_dependencies: str
    backup_code: str
    backup_dependencies: str
    live_code: str
    live_dependencies: str

    @property
    def staging_root(self):
        return os.path.dirname(self.staged_code)

    @property
    def backup_root(self):
        return os.path.dirname(self.backup_code)

    def to_document(self):
        return {
            "journal": self.journal,
            "staged_code": self.staged_code,
            "staged_dependencies": self.staged_dependencies,
            "backup_code": self.backup_code,
            "backup_dependencies": self.backup_dependencies,
            "live_code": self.live_code,
            "live_dependencies": self.live_dependencies,
        }


@dataclass
class ReleaseTransaction:
    operation_id: str
    plugin_key: str
    live_folder: str
    operation: str
    phase: str
    expected_current: dict
    target: dict
    dependency_snapshot: object
    dependency_state: dict
    staged_snapshot: object
    paths: ReleaseTransactionPaths
    created_at: str
    updated_at: str
    error: str = ""
    rollback_from: str = ""

    def to_document(self):
        return {
            "schema_version": RELEASE_TRANSACTION_SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "package_id": self.plugin_key,
            "live_folder": self.live_folder,
            "operation": self.operation,
            "phase": self.phase,
            "expected_current": _transaction_json_copy(
                self.expected_current,
                "expected_current",
            ),
            "target": _transaction_json_copy(self.target, "target"),
            "dependency_snapshot": _transaction_json_copy(
                self.dependency_snapshot,
                "dependency_snapshot",
            ),
            "dependency_state": _transaction_json_copy(
                self.dependency_state,
                "dependency_state",
            ),
            "staged_snapshot": _transaction_json_copy(
                self.staged_snapshot,
                "staged_snapshot",
            ),
            "paths": self.paths.to_document(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "rollback_from": self.rollback_from,
        }

    @classmethod
    def from_document(cls, document, expected_paths):
        """Parse one strict current transaction journal."""
        return cls._from_document(
            document,
            expected_paths,
            schema_version=RELEASE_TRANSACTION_SCHEMA_VERSION,
            identity_field="package_id",
            has_live_folder=True,
        )

    @classmethod
    def from_previous_document(cls, document, expected_paths):
        """Normalize one schema-v2 transaction journal."""
        return cls._from_document(
            document,
            expected_paths,
            schema_version=PREVIOUS_RELEASE_TRANSACTION_SCHEMA_VERSION,
            identity_field="package_id",
            has_live_folder=False,
        )

    @classmethod
    def from_legacy_document(cls, document, expected_paths):
        """Normalize one explicit schema-v1 transaction journal."""
        return cls._from_document(
            document,
            expected_paths,
            schema_version=LEGACY_RELEASE_TRANSACTION_SCHEMA_VERSION,
            identity_field="plugin_key",
            has_live_folder=False,
        )

    @classmethod
    def _from_document(
        cls,
        document,
        expected_paths,
        *,
        schema_version,
        identity_field,
        has_live_folder,
    ):
        required_fields = [
            "schema_version",
            "operation_id",
            identity_field,
            "operation",
            "phase",
            "expected_current",
            "target",
            "dependency_snapshot",
            "dependency_state",
            "staged_snapshot",
            "paths",
            "created_at",
            "updated_at",
            "error",
            "rollback_from",
        ]
        if has_live_folder:
            required_fields.insert(3, "live_folder")
        document = _require_document(
            document,
            "release transaction",
            tuple(required_fields),
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != schema_version
        ):
            raise ValueError("Release transaction schema is unsupported.")
        operation_id = _require_plugin_key(
            document["operation_id"],
            "operation_id",
        )
        plugin_key = _require_plugin_key(
            document[identity_field], identity_field
        )
        live_folder = (
            _require_plugin_key(document["live_folder"], "live_folder")
            if has_live_folder
            else plugin_key
        )
        if os.path.basename(expected_paths.live_code) != live_folder:
            raise ValueError(
                "Release transaction live folder is not manager-owned."
            )
        operation = _require_nonempty_string(
            document["operation"],
            "operation",
        )
        if operation not in RELEASE_TRANSACTION_OPERATIONS:
            raise ValueError("Release transaction operation is unsupported.")
        phase = _require_nonempty_string(document["phase"], "phase")
        if phase not in RELEASE_TRANSACTION_PHASES:
            raise ValueError("Release transaction phase is unsupported.")
        if not isinstance(document["expected_current"], dict):
            raise ValueError("expected_current must be an object.")
        if not isinstance(document["target"], dict):
            raise ValueError("target must be an object.")
        dependency_snapshot = document["dependency_snapshot"]
        if dependency_snapshot is not None and not isinstance(
            dependency_snapshot,
            dict,
        ):
            raise ValueError("dependency_snapshot must be an object or null.")
        dependency_state = _validated_dependency_state(
            document["dependency_state"]
        )
        staged_snapshot = _validated_staged_snapshot(
            document["staged_snapshot"],
            allow_none=True,
        )
        if document["paths"] != expected_paths.to_document():
            raise ValueError("Release transaction paths are not manager-owned.")
        created_at = _require_nonempty_string(
            document["created_at"],
            "created_at",
        )
        updated_at = _require_nonempty_string(
            document["updated_at"],
            "updated_at",
        )
        _parse_utc_timestamp(created_at, "created_at")
        _parse_utc_timestamp(updated_at, "updated_at")
        error = document["error"]
        if not isinstance(error, str):
            raise ValueError("error must be text.")
        rollback_from = document["rollback_from"]
        if not isinstance(rollback_from, str):
            raise ValueError("rollback_from must be text.")
        if rollback_from and (
            rollback_from not in RELEASE_TRANSACTION_PHASES
            or rollback_from in ("rollback_pending", "rolled_back")
        ):
            raise ValueError("rollback_from is unsupported.")
        if phase == "rollback_pending" and not rollback_from:
            raise ValueError("rollback_pending requires rollback_from.")
        if phase in RELEASE_MIGRATION_RESTORE_PHASES:
            if schema_version != RELEASE_TRANSACTION_SCHEMA_VERSION:
                raise ValueError(
                    "Migration restore phases require the current schema."
                )
            if (
                operation != "release_migration"
                or rollback_from != "code_backed_up"
                or not error
            ):
                raise ValueError(
                    "Migration restore phase descriptor is inconsistent."
                )
        return cls(
            operation_id=operation_id,
            plugin_key=plugin_key,
            live_folder=live_folder,
            operation=operation,
            phase=phase,
            expected_current=_transaction_json_copy(
                document["expected_current"],
                "expected_current",
            ),
            target=_transaction_json_copy(document["target"], "target"),
            dependency_snapshot=_transaction_json_copy(
                dependency_snapshot,
                "dependency_snapshot",
            ),
            dependency_state=dependency_state,
            staged_snapshot=staged_snapshot,
            paths=expected_paths,
            created_at=created_at,
            updated_at=updated_at,
            error=error,
            rollback_from=rollback_from,
        )

    @property
    def package_id(self):
        """Expose the durable package identity without breaking callers."""
        return self.plugin_key


class ReleaseTransactionManager:
    """Durably activate and recover manager-owned release transactions."""

    def __init__(self, plugin):
        if plugin is None:
            raise ValueError("plugin is required.")
        self.plugin = plugin
        self.fault_injector = None

    def _fsync_directory(self, path):
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            if os.name == "nt":
                return
            raise
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _manager_paths(self, create=False):
        manager_dir = os.path.abspath(self.plugin.get_plugin_home_folder())
        if not os.path.isdir(manager_dir) or os.path.islink(manager_dir):
            raise ValueError("Plugin manager home must be a real directory.")
        plugins_dir = os.path.abspath(os.path.dirname(manager_dir))
        if not os.path.isdir(plugins_dir) or os.path.islink(plugins_dir):
            raise ValueError("Domoticz plugins path must be a real directory.")
        state_root = os.path.join(manager_dir, ".pypluginstore")
        directories = (
            state_root,
            os.path.join(state_root, "transactions"),
            os.path.join(state_root, "staging"),
            os.path.join(state_root, "backups"),
        )
        parent = manager_dir
        for directory in directories:
            if os.path.lexists(directory):
                if os.path.islink(directory) or not os.path.isdir(directory):
                    raise ValueError(
                        "Release transaction state path must be a real directory."
                    )
            elif create:
                os.mkdir(directory)
                self._fsync_directory(parent)
            else:
                raise ValueError(
                    "Release transaction state directory does not exist."
                )
            parent = state_root
        manager_device = os.stat(plugins_dir).st_dev
        if any(
            os.stat(directory).st_dev != manager_device
            for directory in directories
        ):
            raise ValueError(
                "Release transactions require manager state and plugins on one filesystem."
            )
        return manager_dir, plugins_dir, state_root

    def _canonical_paths(
        self,
        plugin_key,
        operation_id,
        *,
        live_folder=None,
        create_state=False,
    ):
        """Derive manager-owned paths without requiring payload containers."""
        plugin_key = _require_plugin_key(plugin_key)
        operation_id = _require_plugin_key(operation_id, "operation_id")
        if live_folder is None:
            live_folder = plugin_key
        live_folder = _require_plugin_key(live_folder, "live_folder")
        manager_dir, plugins_dir, state_root = self._manager_paths(
            create=create_state
        )
        staging_root = os.path.join(
            state_root,
            "staging",
            plugin_key,
            operation_id,
        )
        backup_root = os.path.join(
            state_root,
            "backups",
            plugin_key,
            operation_id,
        )
        return ReleaseTransactionPaths(
            journal=os.path.join(
                state_root,
                "transactions",
                operation_id + ".json",
            ),
            staged_code=os.path.join(staging_root, "code"),
            staged_dependencies=os.path.join(staging_root, "dependencies"),
            backup_code=os.path.join(backup_root, "code"),
            backup_dependencies=os.path.join(backup_root, "dependencies"),
            live_code=os.path.join(plugins_dir, live_folder),
            live_dependencies=os.path.join(manager_dir, ".shared_deps"),
        )

    def _require_transaction_directory(self, directory, manager_device):
        if os.path.islink(directory) or not os.path.isdir(directory):
            raise ValueError(
                "Release transaction path must be a real directory."
            )
        if os.stat(directory).st_dev != manager_device:
            raise ValueError(
                "Release transaction paths must share one filesystem."
            )

    def _require_operation_containers(self, paths):
        """Validate package and operation directories before payload access."""
        manager_device = os.stat(os.path.dirname(paths.journal)).st_dev
        for directory in (
            os.path.dirname(paths.staging_root),
            paths.staging_root,
            os.path.dirname(paths.backup_root),
            paths.backup_root,
        ):
            self._require_transaction_directory(directory, manager_device)

    def _create_operation_containers(self, paths):
        """Create fresh operation roots without ever reusing old payloads."""
        manager_device = os.stat(os.path.dirname(paths.journal)).st_dev
        for directory in (
            os.path.dirname(paths.staging_root),
            os.path.dirname(paths.backup_root),
        ):
            if os.path.lexists(directory):
                self._require_transaction_directory(
                    directory,
                    manager_device,
                )
                continue
            os.mkdir(directory)
            self._fsync_directory(os.path.dirname(directory))
            self._require_transaction_directory(directory, manager_device)
        for directory in (paths.staging_root, paths.backup_root):
            if os.path.lexists(directory):
                raise ValueError(
                    "Release transaction operation path already exists."
                )
            os.mkdir(directory)
            self._fsync_directory(os.path.dirname(directory))
        self._require_operation_containers(paths)

    def _paths(
        self,
        plugin_key,
        operation_id,
        *,
        live_folder=None,
        create_parents=False,
    ):
        paths = self._canonical_paths(
            plugin_key,
            operation_id,
            live_folder=live_folder,
            create_state=create_parents,
        )
        if create_parents:
            self._create_operation_containers(paths)
        else:
            self._require_operation_containers(paths)
        return paths

    def _process_lock(self, lock_path):
        with _RELEASE_TRANSACTION_LOCKS_GUARD:
            process_lock = _RELEASE_TRANSACTION_LOCKS.get(lock_path)
            if process_lock is None:
                process_lock = threading.Lock()
                _RELEASE_TRANSACTION_LOCKS[lock_path] = process_lock
        return process_lock

    def _lock_file(self, descriptor, blocking):
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(descriptor, mode, 1)
            except OSError as error:
                raise RuntimeError(
                    "Another release transaction is already running."
                ) from error
            return
        import fcntl

        operation = fcntl.LOCK_EX
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, operation)
        except OSError as error:
            raise RuntimeError(
                "Another release transaction is already running."
            ) from error

    def _unlock_file(self, descriptor):
        if os.name == "nt":
            import msvcrt

            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)

    @contextmanager
    def _manager_lock(self, lock_name, blocking):
        if type(blocking) is not bool:
            raise ValueError("blocking must be a boolean.")
        _manager, _plugins, state_root = self._manager_paths(create=True)
        lock_path = os.path.join(state_root, lock_name)
        process_lock = self._process_lock(lock_path)
        if not process_lock.acquire(blocking=blocking):
            raise RuntimeError("Another release transaction is already running.")
        descriptor = None
        locked = False
        try:
            previous_stat = None
            if os.path.lexists(lock_path):
                previous_stat = os.lstat(lock_path)
                if not stat.S_ISREG(previous_stat.st_mode):
                    raise ValueError(
                        "Release transaction lock must be a regular file."
                    )
            open_flags = os.O_RDWR | os.O_CREAT
            open_flags |= getattr(os, "O_BINARY", 0)
            open_flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(lock_path, open_flags, 0o600)
            except OSError as error:
                raise ValueError(
                    "Release transaction lock could not be opened safely."
                ) from error
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise ValueError(
                    "Release transaction lock must be a regular file."
                )
            if previous_stat is not None and (
                opened_stat.st_dev != previous_stat.st_dev
                or opened_stat.st_ino != previous_stat.st_ino
            ):
                raise ValueError(
                    "Release transaction lock changed while being opened."
                )
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            self._lock_file(descriptor, blocking)
            locked = True
            yield
        finally:
            if descriptor is not None:
                if locked:
                    self._unlock_file(descriptor)
                os.close(descriptor)
            process_lock.release()

    @contextmanager
    def operation_lock(self, blocking=True):
        with self._manager_lock("transactions.lock", blocking):
            yield

    @contextmanager
    def workflow_lock(self, blocking=True):
        """Serialize full release preparation around the global dependency tree."""
        with self._manager_lock("release-workflow.lock", blocking):
            yield

    def _write_transaction(self, transaction, update_timestamp=True):
        if update_timestamp:
            transaction.updated_at = self.plugin.get_host().utc_timestamp()
        document = transaction.to_document()
        expected_paths = self._canonical_paths(
            transaction.plugin_key,
            transaction.operation_id,
            live_folder=transaction.live_folder,
        )
        ReleaseTransaction.from_document(document, expected_paths)
        journal = transaction.paths.journal
        journal_dir = os.path.dirname(journal)
        temporary = journal + ".tmp"
        contents = (
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if os.path.lexists(temporary):
            if os.path.islink(temporary) or not os.path.isfile(temporary):
                raise ValueError("Release transaction temporary path is unsafe.")
            os.unlink(temporary)
            self._fsync_directory(journal_dir)
        try:
            with open(temporary, "xb") as journal_file:
                journal_file.write(contents)
                journal_file.flush()
                os.fsync(journal_file.fileno())
            os.replace(temporary, journal)
            self._fsync_directory(journal_dir)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
                self._fsync_directory(journal_dir)

    def _pending_transactions_path(self):
        _manager, _plugins, state_root = self._manager_paths(create=False)
        return os.path.join(state_root, "pending_transactions.json")

    def _pending_transaction_identity(self, transaction):
        return {
            "operation_id": transaction.operation_id,
            "package_id": transaction.plugin_key,
            "live_folder": transaction.live_folder,
            "operation": transaction.operation,
            "expected_current": transaction.expected_current,
            "target": transaction.target,
            "dependency_snapshot": transaction.dependency_snapshot,
            "dependency_state": transaction.dependency_state,
            "staged_snapshot": transaction.staged_snapshot,
            "paths": transaction.paths.to_document(),
            "created_at": transaction.created_at,
        }

    def _load_pending_transactions_locked(self):
        pending_path = self._pending_transactions_path()
        if not os.path.lexists(pending_path):
            return []
        if os.path.islink(pending_path) or not os.path.isfile(pending_path):
            raise ValueError(
                "Pending release transactions path is not a regular file."
            )
        with open(pending_path, "rb") as pending_file:
            document = _load_json_object(
                pending_file.read(),
                "pending release transactions",
            )
        document = _require_document(
            document,
            "pending release transactions",
            ("schema_version", "operations"),
        )
        schema_version = document["schema_version"]
        if type(schema_version) is not int or schema_version not in {
            RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION,
            PREVIOUS_RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION,
            LEGACY_RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION,
        }:
            raise ValueError(
                "Pending release transactions schema is unsupported."
            )
        identity_field = (
            "plugin_key"
            if schema_version
            == LEGACY_RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION
            else "package_id"
        )
        transaction_parser = self._transaction_parser(schema_version)
        raw_operations = document["operations"]
        if not isinstance(raw_operations, list):
            raise ValueError(
                "Pending release transaction operations must be a list."
            )
        operations = []
        operation_ids = set()
        for raw_operation in raw_operations:
            if not isinstance(raw_operation, dict):
                raise ValueError(
                    "Pending release transaction descriptor must be an object."
                )
            operation_id = _require_plugin_key(
                raw_operation.get("operation_id"),
                "operation_id",
            )
            if operation_id in operation_ids:
                raise ValueError(
                    "Pending release transactions contain a duplicate operation."
                )
            plugin_key = raw_operation.get(identity_field, "")
            live_folder = self._transaction_live_folder(
                raw_operation,
                plugin_key,
                schema_version,
            )
            expected_paths = self._paths(
                plugin_key,
                operation_id,
                live_folder=live_folder,
            )
            transaction = transaction_parser(
                raw_operation,
                expected_paths,
            )
            self._validate_descriptors(
                transaction.operation,
                transaction.expected_current,
                transaction.target,
            )
            if transaction.phase != "queued_locked":
                raise ValueError(
                    "Pending release transaction is not queued_locked."
                )
            journal_transaction = self._load_transaction(operation_id)
            if (
                self._pending_transaction_identity(journal_transaction)
                != self._pending_transaction_identity(transaction)
            ):
                raise ValueError(
                    "Pending release transaction differs from its journal."
                )
            operation_ids.add(operation_id)
            operations.append(transaction)
        if schema_version != RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION:
            self._write_pending_transactions_locked(operations)
        return operations

    def _write_pending_transactions_locked(self, transactions):
        pending_path = self._pending_transactions_path()
        pending_dir = os.path.dirname(pending_path)
        temporary = pending_path + ".tmp"
        if os.path.lexists(pending_path) and (
            os.path.islink(pending_path) or not os.path.isfile(pending_path)
        ):
            raise ValueError(
                "Pending release transactions path is not a regular file."
            )
        if os.path.lexists(temporary):
            if os.path.islink(temporary) or not os.path.isfile(temporary):
                raise ValueError(
                    "Pending release transactions temporary path is unsafe."
                )
            os.unlink(temporary)
            self._fsync_directory(pending_dir)
        document = {
            "schema_version": RELEASE_PENDING_TRANSACTIONS_SCHEMA_VERSION,
            "operations": [
                transaction.to_document()
                for transaction in sorted(
                    transactions,
                    key=lambda item: item.operation_id,
                )
            ],
        }
        contents = (
            json.dumps(document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        try:
            with open(temporary, "xb") as pending_file:
                pending_file.write(contents)
                pending_file.flush()
                os.fsync(pending_file.fileno())
            os.replace(temporary, pending_path)
            self._fsync_directory(pending_dir)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
                self._fsync_directory(pending_dir)

    def _sync_pending_transactions_locked(self):
        _manager, _plugins, state_root = self._manager_paths(create=True)
        transaction_dir = os.path.join(state_root, "transactions")
        queued = []
        for name in sorted(os.listdir(transaction_dir)):
            if not name.endswith(".json"):
                continue
            transaction = self._load_transaction(name[:-5])
            if transaction.phase == "queued_locked":
                queued.append(transaction)
        self._write_pending_transactions_locked(queued)
        return queued

    def _read_transaction_document(self, operation_id):
        operation_id = _require_plugin_key(operation_id, "operation_id")
        _manager, _plugins, state_root = self._manager_paths(create=False)
        journal = os.path.join(
            state_root,
            "transactions",
            operation_id + ".json",
        )
        if os.path.islink(journal) or not os.path.isfile(journal):
            raise ValueError("Release transaction journal does not exist.")
        with open(journal, "rb") as journal_file:
            return _load_json_object(
                journal_file.read(),
                "release transaction journal",
            )

    def _transaction_document_identity(self, document):
        schema_version = document.get("schema_version")
        if type(schema_version) is not int or schema_version not in {
            RELEASE_TRANSACTION_SCHEMA_VERSION,
            PREVIOUS_RELEASE_TRANSACTION_SCHEMA_VERSION,
            LEGACY_RELEASE_TRANSACTION_SCHEMA_VERSION,
        }:
            raise ValueError("Release transaction schema is unsupported.")
        identity_field = (
            "plugin_key"
            if schema_version == LEGACY_RELEASE_TRANSACTION_SCHEMA_VERSION
            else "package_id"
        )
        plugin_key = _require_plugin_key(
            document.get(identity_field, ""),
            identity_field,
        )
        return plugin_key, schema_version

    def _transaction_parser(self, schema_version):
        return {
            RELEASE_TRANSACTION_SCHEMA_VERSION: (
                ReleaseTransaction.from_document
            ),
            PREVIOUS_RELEASE_TRANSACTION_SCHEMA_VERSION: (
                ReleaseTransaction.from_previous_document
            ),
            LEGACY_RELEASE_TRANSACTION_SCHEMA_VERSION: (
                ReleaseTransaction.from_legacy_document
            ),
        }[schema_version]

    def _transaction_live_folder(
        self,
        document,
        plugin_key,
        schema_version,
    ):
        if schema_version != RELEASE_TRANSACTION_SCHEMA_VERSION:
            return plugin_key
        return _require_plugin_key(
            document.get("live_folder", ""),
            "live_folder",
        )

    def _installed_state_matches_expected(self, transaction):
        return bool(
            self._metadata_matches(transaction)
            and self._dependency_tree_matches(
                transaction.paths.live_dependencies,
                transaction.dependency_state["expected"],
                "Installed dependency snapshot",
            )
        )

    def _complete_pre_activation_rollback_locked(
        self,
        transaction,
        error="",
        *,
        recreate_staging_root=False,
    ):
        """Converge an untouched-live transaction on one terminal state."""
        resuming = bool(
            transaction.phase == "rollback_pending"
            and transaction.rollback_from
            in RELEASE_TRANSACTION_PREACTIVATION_PHASES
        )
        if resuming:
            rollback_from = transaction.rollback_from
        elif transaction.phase in RELEASE_TRANSACTION_PREACTIVATION_PHASES:
            rollback_from = transaction.phase
        else:
            raise RuntimeError(
                "Transaction is not a pre-activation rollback."
            )

        installed_state_matches = self._installed_state_matches_expected(
            transaction
        )
        fallback_error = (
            "Recovered an interrupted pre-activation release operation."
            if installed_state_matches
            else (
                "Installed state changed after an interrupted "
                "pre-activation release operation."
            )
        )
        requested_error = str(error or "")
        if resuming:
            primary_error = (
                transaction.error or requested_error or fallback_error
            )
        else:
            primary_error = (
                requested_error or transaction.error or fallback_error
            )
            transaction.rollback_from = rollback_from
            transaction.phase = "rollback_pending"
            transaction.error = primary_error
            self._write_transaction(transaction)
        if resuming and not transaction.error:
            transaction.error = primary_error
            self._write_transaction(transaction)

        if recreate_staging_root:
            os.mkdir(transaction.paths.staging_root)
            self._fsync_directory(
                os.path.dirname(transaction.paths.staging_root)
            )
            self._require_operation_containers(transaction.paths)
        self._discard_transaction_payloads(transaction)
        return self._set_phase(
            transaction,
            "rolled_back" if installed_state_matches else "stale_target",
            error=primary_error,
        )

    def _repair_missing_pre_activation_container(
        self,
        transaction,
        *,
        is_legacy,
    ):
        """Repair only the container shape created by the v2.19 abort bug."""
        legacy_pre_activation = bool(
            is_legacy
            and transaction.phase
            in RELEASE_TRANSACTION_PREACTIVATION_PHASES
        )
        interrupted_repair = bool(
            transaction.phase == "rollback_pending"
            and transaction.rollback_from
            in RELEASE_TRANSACTION_PREACTIVATION_PHASES
        )
        if not (legacy_pre_activation or interrupted_repair):
            return False

        paths = transaction.paths
        manager_device = os.stat(os.path.dirname(paths.journal)).st_dev
        staging_package_root = os.path.dirname(paths.staging_root)
        backup_package_root = os.path.dirname(paths.backup_root)
        for directory in (
            staging_package_root,
            backup_package_root,
            paths.backup_root,
        ):
            self._require_transaction_directory(directory, manager_device)
        if os.path.lexists(paths.staging_root):
            return False
        if os.listdir(paths.backup_root):
            raise RuntimeError(
                "Legacy pre-activation backup is not empty; recovery stopped."
            )
        self._complete_pre_activation_rollback_locked(
            transaction,
            recreate_staging_root=True,
        )
        return True

    def _load_transaction(self, operation_id, document=None):
        operation_id = _require_plugin_key(operation_id, "operation_id")
        if document is None:
            document = self._read_transaction_document(operation_id)
        plugin_key, schema_version = self._transaction_document_identity(
            document
        )
        live_folder = self._transaction_live_folder(
            document,
            plugin_key,
            schema_version,
        )
        expected_paths = self._canonical_paths(
            plugin_key,
            operation_id,
            live_folder=live_folder,
        )
        parser = self._transaction_parser(schema_version)
        transaction = parser(
            document,
            expected_paths,
        )
        expected_current, target = self._validate_descriptors(
            transaction.operation,
            transaction.expected_current,
            transaction.target,
        )
        transaction.expected_current = expected_current
        transaction.target = target
        repaired = False
        try:
            self._require_operation_containers(transaction.paths)
        except ValueError as path_error:
            repaired = self._repair_missing_pre_activation_container(
                transaction,
                is_legacy=(
                    schema_version
                    == LEGACY_RELEASE_TRANSACTION_SCHEMA_VERSION
                ),
            )
            if not repaired:
                raise path_error
        if (
            schema_version != RELEASE_TRANSACTION_SCHEMA_VERSION
            and not repaired
        ):
            self._write_transaction(transaction, update_timestamp=False)
        return transaction

    def load_transaction(self, operation_id):
        with self.operation_lock():
            return self._load_transaction(operation_id)

    def _transactions_locked(self, plugin_key=None):
        """Load validated journals in durable creation order."""
        if plugin_key is not None:
            plugin_key = _require_plugin_key(plugin_key)
        _manager, _plugins, state_root = self._manager_paths(create=True)
        transaction_dir = os.path.join(state_root, "transactions")
        transactions = []
        for name in sorted(os.listdir(transaction_dir)):
            if not name.endswith(".json"):
                continue
            operation_id = name[:-5]
            try:
                document = self._read_transaction_document(operation_id)
                document_plugin_key, _schema_version = (
                    self._transaction_document_identity(document)
                )
            except (OSError, ValueError):
                if plugin_key is not None:
                    continue
                raise
            if plugin_key is not None and document_plugin_key != plugin_key:
                continue
            transactions.append(
                self._load_transaction(operation_id, document=document)
            )
        return sorted(
            transactions,
            key=lambda item: (item.created_at, item.operation_id),
        )

    def _rollback_snapshots_match_locked(self, transaction):
        """Verify both sides of a retained rollback set before exposing it."""
        if (
            transaction.operation == "release_install"
            or transaction.phase not in {"restart_pending", "release_managed"}
        ):
            return False
        return bool(
            self._release_tree_matches_target(
                transaction.paths.live_code,
                transaction,
            )
            and self._active_dependencies_match_target(transaction)
            and self._code_path_matches(
                transaction.paths.backup_code,
                transaction.plugin_key,
                transaction.expected_current,
            )
            and self._dependency_tree_matches(
                transaction.paths.backup_dependencies,
                transaction.dependency_state["expected"],
                "Retained dependency backup",
            )
        )

    def _latest_verified_rollback_locked(self, plugin_key, transactions=None):
        if transactions is None:
            transactions = self._transactions_locked(plugin_key)
        for transaction in reversed(transactions):
            if self._rollback_snapshots_match_locked(transaction):
                return transaction
        return None

    def plugin_lifecycle_state(self, plugin_key):
        """Return verified rollback and restart state for one plugin."""
        plugin_key = _require_plugin_key(plugin_key)
        with self.operation_lock():
            transactions = self._transactions_locked(plugin_key)
            rollback = self._latest_verified_rollback_locked(
                plugin_key,
                transactions,
            )
            restart_pending = False
            for transaction in reversed(transactions):
                if transaction.phase == "restart_pending":
                    restart_pending = bool(
                        self._release_tree_matches_target(
                            transaction.paths.live_code,
                            transaction,
                        )
                        and self._active_dependencies_match_target(transaction)
                    )
                    if restart_pending:
                        break
                if (
                    transaction.phase == "rolled_back"
                    and transaction.rollback_from
                    in {"restart_pending", "release_managed"}
                ):
                    restart_pending = bool(
                        self._metadata_matches(transaction)
                        and self._dependency_tree_matches(
                            transaction.paths.live_dependencies,
                            transaction.dependency_state["expected"],
                            "Restored dependency snapshot",
                        )
                    )
                    if restart_pending:
                        break

            rollback_version = ""
            rollback_revision = None
            if (
                rollback is not None
                and rollback.expected_current.get("management_mode")
                == "release"
            ):
                try:
                    metadata = InstallMetadataService(self.plugin).read(
                        rollback.paths.backup_code
                    )
                except ValueError:
                    metadata = None
                if metadata is not None:
                    rollback_version = metadata.version
                    rollback_revision = metadata.release_revision

            return {
                "rollback": rollback,
                "rollback_available": rollback is not None,
                "rollback_version": rollback_version,
                "rollback_revision": rollback_revision,
                "restart_pending": restart_pending,
            }

    def _validate_descriptors(self, operation, expected_current, target):
        if operation not in RELEASE_TRANSACTION_OPERATIONS:
            raise ValueError("Release transaction operation is unsupported.")
        expected_current = _transaction_json_copy(
            expected_current,
            "expected_current",
        )
        target = _transaction_json_copy(target, "target")
        if not isinstance(expected_current, dict):
            raise ValueError("expected_current must be an object.")
        if not isinstance(target, dict):
            raise ValueError("target must be an object.")
        expected_mode = expected_current.get("management_mode")
        required_mode = {
            "release_install": "absent",
            "release_update": "release",
            "release_migration": "git",
        }[operation]
        if expected_mode != required_mode:
            raise ValueError("expected_current does not match the operation.")
        if target.get("management_mode") != "release":
            raise ValueError("Release transaction target must be release-managed.")
        _require_nonempty_string(target.get("release_id"), "target.release_id")
        _require_positive_integer(
            target.get("release_revision"),
            "target.release_revision",
        )
        target_source_revision = str(target.get("source_revision") or "")
        if target_source_revision:
            _require_nonempty_string(
                target_source_revision,
                "target.source_revision",
            )
        _require_git_commit(
            target.get("commit"),
            "target.commit",
            required=not target_source_revision,
        )
        _require_sha256(
            target.get("artifact_tree_sha256"),
            "target.artifact_tree_sha256",
        )
        if expected_mode == "git":
            _require_git_commit(
                expected_current.get("commit"),
                "expected_current.commit",
            )
            expected_current["migration_snapshot"] = (
                _validated_migration_snapshot(
                    expected_current.get("migration_snapshot")
                )
            )
        if expected_mode == "release":
            expected_source_revision = str(
                expected_current.get("source_revision") or ""
            )
            if expected_source_revision:
                _require_nonempty_string(
                    expected_source_revision,
                    "expected_current.source_revision",
                )
            _require_git_commit(
                expected_current.get("commit"),
                "expected_current.commit",
                required=not expected_source_revision,
            )
            _require_sha256(
                expected_current.get("artifact_tree_sha256"),
                "expected_current.artifact_tree_sha256",
            )
        return expected_current, target

    def create_transaction(
        self,
        *,
        plugin_key,
        operation_id,
        operation,
        expected_current,
        target,
        live_folder=None,
    ):
        plugin_key = _require_plugin_key(plugin_key)
        operation_id = _require_plugin_key(operation_id, "operation_id")
        operation = _require_nonempty_string(operation, "operation")
        if live_folder is None:
            live_folder = plugin_key
        live_folder = _require_plugin_key(live_folder, "live_folder")
        if operation == "release_install" and live_folder != plugin_key:
            raise ValueError(
                "A new release install must use its package folder."
            )
        expected_current, target = self._validate_descriptors(
            operation,
            expected_current,
            target,
        )
        with self.operation_lock():
            active = [
                transaction
                for transaction in self._transactions_locked()
                if transaction.phase in RELEASE_TRANSACTION_ACTIVE_PHASES
            ]
            if active:
                raise RuntimeError(
                    "Another release operation is still active; restart or "
                    "finish it before starting a new one."
                )
            if operation == "release_install":
                self.plugin.assert_release_install_absent_locked(plugin_key)
            paths = self._paths(
                plugin_key,
                operation_id,
                live_folder=live_folder,
                create_parents=True,
            )
            if os.path.lexists(paths.journal):
                raise ValueError("Release transaction operation already exists.")
            dependency_state = {
                "expected": self._dependency_tree_state(
                    paths.live_dependencies,
                    "Installed dependency snapshot",
                ),
                "target": None,
            }
            timestamp = self.plugin.get_host().utc_timestamp()
            transaction = ReleaseTransaction(
                operation_id=operation_id,
                plugin_key=plugin_key,
                live_folder=live_folder,
                operation=operation,
                phase="created",
                expected_current=expected_current,
                target=target,
                dependency_snapshot=None,
                dependency_state=dependency_state,
                staged_snapshot=None,
                paths=paths,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._write_transaction(transaction)
            return transaction

    def _require_real_directory(self, path, label):
        if os.path.islink(path) or not os.path.isdir(path):
            raise ValueError(label + " must be a real directory.")

    def _sync_staged_tree(
        self,
        root,
        label,
        *,
        expected_files=None,
        allow_install_metadata=False,
        capture_tree=False,
        ignored_directories=frozenset(),
    ):
        """Validate and fsync one manager-owned tree without following links."""
        self._require_real_directory(root, label)
        root_stat = os.lstat(root)
        expected = None
        if expected_files is not None:
            expected = {
                path: dict(record)
                for path, record in expected_files.items()
            }
        validator = SafeZipExtractor(ReleaseArchiveLimits())
        stack = [(root, "")]
        directories = []
        seen_nodes = set()
        seen_files = set()
        tree_records = []
        total_bytes = 0
        while stack:
            directory, relative_directory = stack.pop()
            directory_stat = os.lstat(directory)
            if (
                not stat.S_ISDIR(directory_stat.st_mode)
                or directory_stat.st_dev != root_stat.st_dev
            ):
                raise ValueError(label + " contains an unsafe directory.")
            directories.append(directory)
            try:
                entries = list(os.scandir(directory))
            except OSError as error:
                raise ValueError(label + " could not be inspected.") from error
            for entry in entries:
                relative_path = (
                    entry.name
                    if not relative_directory
                    else relative_directory + "/" + entry.name
                )
                if (
                    allow_install_metadata
                    and relative_path == InstallMetadataService.FILE_NAME
                ):
                    parts = (relative_path,)
                else:
                    try:
                        parts = validator._portable_parts(
                            relative_path,
                            label + " path",
                        )
                    except ValueError as error:
                        raise ValueError(
                            label + " contains an unsafe path."
                        ) from error
                collision_key = tuple(part.casefold() for part in parts)
                if collision_key in seen_nodes:
                    raise ValueError(
                        label + " contains a cross-platform path collision."
                    )
                seen_nodes.add(collision_key)
                try:
                    path_stat = os.stat(
                        entry.path,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    if (
                        entry.name.casefold()
                        in ignored_directories
                    ):
                        continue
                    raise ValueError(
                        label + " contains an unreadable path."
                    ) from None
                except OSError as error:
                    raise ValueError(
                        label + " contains an unreadable path."
                    ) from error
                if path_stat.st_dev != root_stat.st_dev:
                    raise ValueError(
                        label + " must remain on the transaction filesystem."
                    )
                if stat.S_ISDIR(path_stat.st_mode):
                    if (
                        entry.name.casefold()
                        in ignored_directories
                    ):
                        continue
                    if capture_tree:
                        tree_records.append(("directory", relative_path))
                    stack.append((entry.path, relative_path))
                    continue
                if not stat.S_ISREG(path_stat.st_mode):
                    raise ValueError(label + " contains a link or special file.")
                if getattr(path_stat, "st_nlink", 1) > 1:
                    raise ValueError(label + " contains a hard-linked file.")
                is_metadata = (
                    allow_install_metadata
                    and relative_path == InstallMetadataService.FILE_NAME
                )
                if expected is not None and not is_metadata:
                    if relative_path not in expected:
                        raise ValueError(
                            label + " contains a file outside its audit inventory."
                        )
                    expected_size = expected[relative_path].get("size")
                    if (
                        expected_size is not None
                        and path_stat.st_size != expected_size
                    ):
                        raise ValueError(
                            label + " file size differs from its audit inventory."
                        )
                # Windows requires GENERIC_WRITE access for FlushFileBuffers,
                # which backs os.fsync(). No write is performed here.
                access = os.O_RDWR if os.name == "nt" else os.O_RDONLY
                flags = access | getattr(os, "O_BINARY", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(entry.path, flags)
                except OSError as error:
                    raise ValueError(
                        label + " file could not be opened safely."
                    ) from error
                should_hash = not is_metadata and (
                    expected is not None or capture_tree
                )
                digest = hashlib.sha256() if should_hash else None
                try:
                    opened_stat = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(opened_stat.st_mode)
                        or opened_stat.st_dev != path_stat.st_dev
                        or opened_stat.st_ino != path_stat.st_ino
                        or opened_stat.st_size != path_stat.st_size
                    ):
                        raise ValueError(
                            label + " file changed while being opened."
                        )
                    if digest is not None:
                        while True:
                            chunk = os.read(descriptor, RELEASE_HTTP_CHUNK_SIZE)
                            if not chunk:
                                break
                            digest.update(chunk)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                if expected is not None and not is_metadata:
                    if digest.hexdigest() != expected[relative_path]["sha256"]:
                        raise ValueError(
                            label + " digest differs from its audit inventory."
                        )
                    seen_files.add(relative_path)
                if capture_tree and not is_metadata:
                    tree_records.append(
                        (
                            "file",
                            relative_path,
                            path_stat.st_size,
                            digest.hexdigest(),
                        )
                    )
                    total_bytes += path_stat.st_size
        if expected is not None and seen_files != set(expected):
            raise ValueError(label + " is missing an audited file.")
        for directory in reversed(directories):
            self._fsync_directory(directory)
        if not capture_tree:
            return None
        tree_digest = hashlib.sha256()
        for record in sorted(
            tree_records,
            key=lambda item: item[1].encode("utf-8"),
        ):
            tree_digest.update(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            tree_digest.update(b"\n")
        return {
            "sha256": tree_digest.hexdigest(),
            "files": sum(1 for record in tree_records if record[0] == "file"),
            "directories": sum(
                1 for record in tree_records if record[0] == "directory"
            ),
            "bytes": total_bytes,
        }

    def _install_metadata_digest(
        self,
        metadata,
        schema_version=INSTALL_METADATA_SCHEMA_VERSION,
    ):
        document = metadata.to_document()
        if schema_version == LEGACY_INSTALL_METADATA_SCHEMA_VERSION:
            document["schema"] = LEGACY_INSTALL_METADATA_SCHEMA_VERSION
            document["plugin_key"] = document.pop("package_id")
        elif schema_version != INSTALL_METADATA_SCHEMA_VERSION:
            raise ValueError("Install metadata digest schema is unsupported.")
        contents = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(contents).hexdigest()

    def _snapshot_for_metadata(self, metadata):
        return _validated_staged_snapshot(
            {
                "install_metadata_sha256": self._install_metadata_digest(
                    metadata
                ),
                "artifact_files": {
                    path: dict(record)
                    for path, record in metadata.artifact_files.items()
                },
                "preserved_files": dict(metadata.preserved_files),
            }
        )

    def _release_tree_matches_descriptor(
        self,
        root,
        plugin_key,
        descriptor,
        staged_snapshot=None,
    ):
        try:
            metadata = InstallMetadataService(self.plugin).read(root)
            if not self._release_metadata_matches(
                metadata,
                plugin_key,
                descriptor,
            ):
                return False
            if staged_snapshot is not None:
                staged_snapshot = _validated_staged_snapshot(staged_snapshot)
                accepted_metadata_digests = {
                    self._install_metadata_digest(metadata),
                    self._install_metadata_digest(
                        metadata,
                        schema_version=LEGACY_INSTALL_METADATA_SCHEMA_VERSION,
                    ),
                }
                if (
                    staged_snapshot["install_metadata_sha256"]
                    not in accepted_metadata_digests
                    or metadata.artifact_files
                    != staged_snapshot["artifact_files"]
                    or metadata.preserved_files
                    != staged_snapshot["preserved_files"]
                ):
                    return False
                artifact_files = staged_snapshot["artifact_files"]
                preserved_files = staged_snapshot["preserved_files"]
            else:
                artifact_files = metadata.artifact_files
                preserved_files = metadata.preserved_files
            expected_files = {
                path: dict(record)
                for path, record in artifact_files.items()
            }
            for path, digest in preserved_files.items():
                expected_files[path] = {"sha256": digest}
            self._sync_staged_tree(
                root,
                "Release tree",
                expected_files=expected_files,
                allow_install_metadata=True,
            )
            return True
        except (OSError, ValueError):
            return False

    def _release_tree_matches_target(self, root, transaction):
        if transaction.staged_snapshot is None:
            return False
        return self._release_tree_matches_descriptor(
            root,
            transaction.plugin_key,
            transaction.target,
            transaction.staged_snapshot,
        )

    def _capture_staged_snapshot(self, root, transaction):
        metadata = InstallMetadataService(self.plugin).read(root)
        if not self._release_metadata_matches(
            metadata,
            transaction.plugin_key,
            transaction.target,
        ):
            raise ValueError("Staged metadata does not match the target.")
        snapshot = self._snapshot_for_metadata(metadata)
        if not self._release_tree_matches_descriptor(
            root,
            transaction.plugin_key,
            transaction.target,
            snapshot,
        ):
            raise ValueError("Staged release inventory is invalid.")
        return snapshot

    def _dependency_tree_state(self, path, label):
        if not os.path.lexists(path):
            return {"present": False, "tree": None}
        self._require_real_directory(path, label)
        return _validated_dependency_tree(
            {
                "present": True,
                "tree": self._sync_staged_tree(
                    path,
                    label,
                    capture_tree=True,
                    ignored_directories=(
                        RELEASE_VOLATILE_CACHE_DIRECTORIES
                    ),
                ),
            },
            label,
        )

    def _dependency_tree_matches(self, path, expected, label):
        try:
            current = self._dependency_tree_state(path, label)
        except (OSError, ValueError):
            return False
        return current == _validated_dependency_tree(expected, label)

    def mark_staged_verified(self, operation_id):
        with self.operation_lock():
            transaction = self._load_transaction(operation_id)
            if transaction.phase != "created":
                raise RuntimeError(
                    "Transaction must be created before staged verification."
                )
            self._require_real_directory(
                transaction.paths.staged_code,
                "Staged release code",
            )
            try:
                staged_snapshot = self._capture_staged_snapshot(
                    transaction.paths.staged_code,
                    transaction,
                )
            except (OSError, ValueError):
                raise ValueError(
                    "Staged release does not match the pinned target."
                ) from None
            transaction.staged_snapshot = staged_snapshot
            transaction.phase = "staged_verified"
            transaction.error = ""
            self._write_transaction(transaction)
            return transaction

    def mark_dependencies_staged(self, operation_id, dependency_snapshot):
        dependency_snapshot = _transaction_json_copy(
            dependency_snapshot,
            "dependency_snapshot",
        )
        if not isinstance(dependency_snapshot, dict):
            raise ValueError("dependency_snapshot must be an object.")
        with self.operation_lock():
            transaction = self._load_transaction(operation_id)
            if transaction.phase not in {
                "staged_verified",
                "dependency_confirmation_required",
            }:
                raise RuntimeError(
                    "Transaction must be staged_verified before dependencies."
                )
            self._require_real_directory(
                transaction.paths.staged_dependencies,
                "Staged dependency snapshot",
            )
            if transaction.phase == "dependency_confirmation_required":
                target = transaction.dependency_state.get("target")
                if target is None or not self._dependency_tree_matches(
                    transaction.paths.staged_dependencies,
                    target,
                    "Confirmed dependency snapshot",
                ):
                    raise ValueError(
                        "Confirmed dependencies do not match their pinned snapshot."
                    )
            else:
                transaction.dependency_state["target"] = (
                    self._dependency_tree_state(
                        transaction.paths.staged_dependencies,
                        "Staged dependency snapshot",
                    )
                )
            transaction.dependency_snapshot = dependency_snapshot
            transaction.phase = "dependencies_staged"
            transaction.error = ""
            self._write_transaction(transaction)
            return transaction

    def mark_dependency_confirmation_required(
        self,
        operation_id,
        dependency_snapshot,
    ):
        dependency_snapshot = _transaction_json_copy(
            dependency_snapshot,
            "dependency_snapshot",
        )
        if not isinstance(dependency_snapshot, dict):
            raise ValueError("dependency_snapshot must be an object.")
        with self.operation_lock():
            transaction = self._load_transaction(operation_id)
            if transaction.phase != "staged_verified":
                raise RuntimeError(
                    "Transaction must be staged_verified before confirmation."
                )
            self._require_real_directory(
                transaction.paths.staged_dependencies,
                "Staged dependency snapshot",
            )
            transaction.dependency_state["target"] = (
                self._dependency_tree_state(
                    transaction.paths.staged_dependencies,
                    "Staged dependency snapshot",
                )
            )
            transaction.dependency_snapshot = dependency_snapshot
            transaction.phase = "dependency_confirmation_required"
            transaction.error = ""
            self._write_transaction(transaction)
            return transaction

    def mark_dependency_blocked(self, operation_id, reason, message):
        reason = _require_nonempty_string(reason, "reason")
        message = _require_nonempty_string(message, "message")
        with self.operation_lock():
            transaction = self._load_transaction(operation_id)
            if transaction.phase not in {
                "staged_verified",
                "dependency_confirmation_required",
            }:
                raise RuntimeError(
                    "Transaction must be staged_verified before blocking dependencies."
                )
            transaction.dependency_state["target"] = None
            transaction.dependency_snapshot = {
                "status": "dependency_blocked",
                "reason": reason,
                "message": message,
            }
            transaction.phase = "dependency_blocked"
            transaction.error = message
            self._write_transaction(transaction)
            return transaction

    def _event(self, phase, transaction):
        if callable(self.fault_injector):
            self.fault_injector(phase, transaction)

    def _set_phase(self, transaction, phase, error=None, inject=False):
        transaction.phase = phase
        if error is not None:
            transaction.error = str(error)
        self._write_transaction(transaction)
        if inject:
            self._event(phase, transaction)
        return transaction

    def _replace_directory(
        self,
        transaction,
        source,
        destination,
        pending_phase,
        completed_phase,
    ):
        if (
            transaction.operation == "release_install"
            and pending_phase == "code_backup_pending"
        ):
            if os.path.lexists(source):
                raise ValueError(
                    "A plugin folder appeared after Release install "
                    "preflight; it was left untouched."
                )
            # A fresh install has no approved live tree to back up. Never move
            # a late writer aside as though it were expected_current.
            self._set_phase(transaction, pending_phase)
            self._set_phase(transaction, completed_phase, inject=True)
            return
        self._set_phase(transaction, pending_phase)
        if os.path.lexists(source):
            if os.path.lexists(destination):
                raise ValueError(
                    "Release transaction rename destination already exists."
                )
            os.replace(source, destination)
            self._fsync_directory(os.path.dirname(source))
            self._fsync_directory(os.path.dirname(destination))
        self._set_phase(transaction, completed_phase, inject=True)

    def _git_migration_snapshot_check(
        self,
        path,
        plugin_key,
        descriptor,
    ):
        try:
            snapshot = _validated_migration_snapshot(
                descriptor.get("migration_snapshot")
            )
            preflight = GitMigrationPreflight(self.plugin).evaluate(
                plugin_key=plugin_key,
                plugin_dir=path,
                repository_identity=snapshot["repository_identity"],
                release_commit=snapshot["release_commit"],
                trigger="manual",
                mutable_paths=snapshot["mutable_paths"],
            )
        except (OSError, RuntimeError, ValueError):
            return False, "inspection failed"
        comparisons = (
            (
                (
                    "preservation inventory is unavailable ("
                    + str(preflight.reason or "unknown")
                    + ")"
                ),
                preflight.preservation_inventory is not None,
            ),
            (
                "installed commit changed",
                preflight.installed_commit == descriptor.get("commit"),
            ),
            (
                "repository identity changed",
                preflight.installed_repository_identity
                == snapshot["repository_identity"],
            ),
            (
                "release commit changed",
                preflight.release_commit == snapshot["release_commit"],
            ),
            (
                "Git history relationship changed",
                preflight.relationship == snapshot["relationship"],
            ),
            (
                "local file inventory changed",
                preflight.inventory_sha256
                == snapshot["inventory_sha256"],
            ),
            (
                "tracked changes changed",
                preflight.tracked_changes
                == snapshot["tracked_changes"],
            ),
            (
                "untracked files changed",
                preflight.untracked_files
                == snapshot["untracked_files"],
            ),
            (
                "shallow checkout state changed",
                preflight.shallow == snapshot["shallow"],
            ),
        )
        for reason, matches in comparisons:
            if not matches:
                return False, reason
        return True, ""

    def _git_migration_snapshot_matches(
        self,
        path,
        plugin_key,
        descriptor,
    ):
        matches, _reason = self._git_migration_snapshot_check(
            path,
            plugin_key,
            descriptor,
        )
        return matches

    def _code_path_matches(self, path, plugin_key, descriptor):
        mode = descriptor.get("management_mode")
        if mode == "absent":
            return not os.path.lexists(path)
        if mode == "release":
            if not os.path.isdir(path) or os.path.islink(path):
                return False
            return self._release_tree_matches_descriptor(
                path,
                plugin_key,
                descriptor,
            )
        if mode == "git":
            if (
                not os.path.isdir(path)
                or os.path.islink(path)
                or not is_git_checkout(path)
            ):
                return False
            return self._git_migration_snapshot_matches(
                path,
                plugin_key,
                descriptor,
            )
        return False

    def _metadata_matches(self, transaction):
        return self._code_path_matches(
            transaction.paths.live_code,
            transaction.plugin_key,
            transaction.expected_current,
        )

    def _release_metadata_matches(
        self,
        metadata,
        plugin_key,
        descriptor,
    ):
        if (
            metadata is None
            or metadata.management_mode != "release"
            or metadata.plugin_key != plugin_key
        ):
            return False
        fields = (
            ("commit", metadata.commit),
            ("source_revision", metadata.source_revision),
            ("artifact_tree_sha256", metadata.artifact_tree_sha256),
            ("release_id", metadata.release_id),
            ("release_revision", metadata.release_revision),
        )
        return all(
            key not in descriptor or descriptor[key] == value
            for key, value in fields
        )

    def _staged_metadata_matches_target(self, transaction):
        return self._release_tree_matches_target(
            transaction.paths.staged_code,
            transaction,
        )

    def _validate_activation_paths(self, transaction):
        transaction_device = os.stat(
            os.path.dirname(transaction.paths.journal)
        ).st_dev
        for path, label in (
            (transaction.paths.staged_code, "Staged release code"),
            (
                transaction.paths.staged_dependencies,
                "Staged dependency snapshot",
            ),
        ):
            self._require_real_directory(path, label)
            if os.stat(path).st_dev != transaction_device:
                raise ValueError(
                    label + " must share the transaction filesystem."
                )
        for path, label in (
            (transaction.paths.live_code, "Installed plugin"),
            (transaction.paths.live_dependencies, "Installed dependencies"),
        ):
            if not os.path.lexists(path):
                continue
            self._require_real_directory(path, label)
            if os.stat(path).st_dev != transaction_device:
                raise ValueError(
                    label + " must share the transaction filesystem."
                )
        if os.path.lexists(transaction.paths.backup_code) or os.path.lexists(
            transaction.paths.backup_dependencies
        ):
            raise ValueError(
                "Release transaction backup destination already exists."
            )

    def _active_dependencies_match_target(self, transaction):
        target = transaction.dependency_state.get("target")
        if target is None:
            return False
        return self._dependency_tree_matches(
            transaction.paths.live_dependencies,
            target,
            "Activated dependency snapshot",
        )

    def _remove_transaction_path(self, path):
        if not os.path.lexists(path):
            return
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        self._fsync_directory(os.path.dirname(path))

    def _discard_transaction_payloads(self, transaction):
        """Empty validated operation roots while retaining journal paths."""
        self._require_operation_containers(transaction.paths)
        for operation_root in (
            transaction.paths.staging_root,
            transaction.paths.backup_root,
        ):
            with os.scandir(operation_root) as entries:
                payloads = sorted(
                    (entry.path for entry in entries),
                    key=lambda path: os.path.basename(path).casefold(),
                )
            for payload in payloads:
                self._remove_transaction_path(payload)

    def _operation_root_is_empty(self, path):
        with os.scandir(path) as entries:
            return next(entries, None) is None

    def _restore_component(
        self,
        live,
        backup,
        staged,
        backup_authoritative=False,
        activated_without_backup=False,
    ):
        if backup_authoritative and os.path.lexists(backup):
            if os.path.lexists(live):
                if os.path.lexists(staged):
                    self._remove_transaction_path(staged)
                os.replace(live, staged)
                self._fsync_directory(os.path.dirname(live))
                self._fsync_directory(os.path.dirname(staged))
            os.replace(backup, live)
            self._fsync_directory(os.path.dirname(backup))
            self._fsync_directory(os.path.dirname(live))
            return
        if (
            activated_without_backup
            and os.path.lexists(live)
            and not os.path.lexists(staged)
        ):
            os.replace(live, staged)
            self._fsync_directory(os.path.dirname(live))
            self._fsync_directory(os.path.dirname(staged))

    def _rollback_locked(self, transaction, error=""):
        if transaction.phase == "rolled_back":
            return transaction
        if (
            transaction.phase in RELEASE_TRANSACTION_PREACTIVATION_PHASES
            or (
                transaction.phase == "rollback_pending"
                and transaction.rollback_from
                in RELEASE_TRANSACTION_PREACTIVATION_PHASES
            )
        ):
            return self._complete_pre_activation_rollback_locked(
                transaction,
                error,
            )
        original_phase = (
            transaction.rollback_from
            if transaction.phase in ("rollback_pending", "queued_locked")
            and transaction.rollback_from
            else transaction.phase
        )
        if transaction.phase != "rollback_pending":
            transaction.rollback_from = original_phase
        code_backup_authoritative = original_phase in {
            "code_backed_up",
            "dependencies_backup_pending",
            "dependencies_backed_up",
            "dependencies_activation_pending",
            "dependencies_activated",
            "code_activation_pending",
            "release_activated",
            "restart_pending",
            "release_managed",
            "rollback_pending",
        }
        if original_phase == "code_backup_pending":
            code_backup_authoritative = bool(
                os.path.lexists(transaction.paths.backup_code)
                and not os.path.lexists(transaction.paths.live_code)
            )
        dependencies_backup_authoritative = original_phase in {
            "dependencies_backed_up",
            "dependencies_activation_pending",
            "dependencies_activated",
            "code_activation_pending",
            "release_activated",
            "restart_pending",
            "release_managed",
            "rollback_pending",
        }
        if original_phase == "dependencies_backup_pending":
            dependencies_backup_authoritative = bool(
                os.path.lexists(transaction.paths.backup_dependencies)
                and not os.path.lexists(transaction.paths.live_dependencies)
            )
        code_may_be_activated = original_phase in {
            "code_activation_pending",
            "release_activated",
            "restart_pending",
            "release_managed",
        }
        dependencies_may_be_activated = original_phase in {
            "dependencies_activation_pending",
            "dependencies_activated",
            "code_activation_pending",
            "release_activated",
            "restart_pending",
            "release_managed",
        }
        self._set_phase(
            transaction,
            "rollback_pending",
            error=error or transaction.error,
        )
        code_backup_matches = True
        if code_backup_authoritative and not self._code_path_matches(
            transaction.paths.backup_code,
            transaction.plugin_key,
            transaction.expected_current,
        ):
            code_backup_matches = self._code_path_matches(
                transaction.paths.live_code,
                transaction.plugin_key,
                transaction.expected_current,
            )
            if code_backup_matches:
                code_backup_authoritative = False
        dependency_backup_matches = True
        if (
            dependencies_backup_authoritative
            and not self._dependency_tree_matches(
                transaction.paths.backup_dependencies,
                transaction.dependency_state["expected"],
                "Retained dependency backup",
            )
        ):
            dependency_backup_matches = self._dependency_tree_matches(
                transaction.paths.live_dependencies,
                transaction.dependency_state["expected"],
                "Restored dependency snapshot",
            )
            if dependency_backup_matches:
                dependencies_backup_authoritative = False
        if not code_backup_matches or not dependency_backup_matches:
            rollback_error = (
                "Rollback could not restore from the retained backup."
            )
            self._set_phase(
                transaction,
                "rollback_pending",
                error=(error or transaction.error) + " " + rollback_error,
            )
            raise RuntimeError(rollback_error)
        self._restore_component(
            transaction.paths.live_code,
            transaction.paths.backup_code,
            transaction.paths.staged_code,
            backup_authoritative=code_backup_authoritative,
            activated_without_backup=code_may_be_activated,
        )
        self._restore_component(
            transaction.paths.live_dependencies,
            transaction.paths.backup_dependencies,
            transaction.paths.staged_dependencies,
            backup_authoritative=dependencies_backup_authoritative,
            activated_without_backup=dependencies_may_be_activated,
        )
        code_restored = self._metadata_matches(transaction)
        dependencies_restored = self._dependency_tree_matches(
            transaction.paths.live_dependencies,
            transaction.dependency_state["expected"],
            "Restored dependency snapshot",
        )
        if not code_restored or not dependencies_restored:
            rollback_error = (
                "Rollback could not restore the expected installed state."
            )
            self._set_phase(
                transaction,
                "rollback_pending",
                error=(error or transaction.error) + " " + rollback_error,
            )
            raise RuntimeError(rollback_error)
        if (
            transaction.operation == "release_install"
            or original_phase in RELEASE_TRANSACTION_PREACTIVATION_PHASES
        ):
            self._discard_transaction_payloads(transaction)
        return self._set_phase(
            transaction,
            "rolled_back",
            error=error or transaction.error,
        )

    def _finish_migration_restore_locked(self, transaction, error):
        """Converge a journaled backup-to-live restore on safe cleanup."""
        backup_exists = os.path.lexists(transaction.paths.backup_code)
        live_exists = os.path.lexists(transaction.paths.live_code)
        if backup_exists == live_exists:
            transaction.rollback_from = "code_backed_up"
            self._set_phase(
                transaction,
                "rollback_pending",
                error=(
                    error
                    + " Both the live folder and retained checkout require "
                    "recovery."
                    if backup_exists
                    else error + " The retained checkout is unavailable."
                ),
            )
            raise RuntimeError(error)
        if backup_exists:
            os.replace(
                transaction.paths.backup_code,
                transaction.paths.live_code,
            )
            self._fsync_directory(
                os.path.dirname(transaction.paths.backup_code)
            )
            self._fsync_directory(
                os.path.dirname(transaction.paths.live_code)
            )
        self._set_phase(
            transaction,
            "migration_restore_completed",
            error=error,
            inject=True,
        )
        transaction.rollback_from = "dependencies_staged"
        self._set_phase(transaction, "rollback_pending", error=error)
        return self._complete_pre_activation_rollback_locked(
            transaction,
            error,
        )

    def _recover_migration_restore_locked(self, transaction):
        if (
            transaction.operation != "release_migration"
            or transaction.phase not in RELEASE_MIGRATION_RESTORE_PHASES
        ):
            raise RuntimeError(
                "Transaction is not a migration checkout restore."
            )
        error = transaction.error or (
            "Recovered an interrupted Git checkout restore."
        )
        return self._finish_migration_restore_locked(transaction, error)

    def _revalidate_migration_backup_locked(self, transaction):
        """Freeze migration approval to the checkout actually moved aside."""
        if transaction.operation != "release_migration":
            return
        matches, mismatch_reason = self._git_migration_snapshot_check(
            transaction.paths.backup_code,
            transaction.plugin_key,
            transaction.expected_current,
        )
        if matches:
            return
        error = (
            "Git checkout changed after migration approval; activation "
            "was cancelled because "
            + mismatch_reason
            + "."
        )
        backup_exists = os.path.lexists(transaction.paths.backup_code)
        live_exists = os.path.lexists(transaction.paths.live_code)
        if backup_exists and not live_exists:
            transaction.rollback_from = "code_backed_up"
            self._set_phase(
                transaction,
                "migration_restore_pending",
                error=error,
            )
            self._finish_migration_restore_locked(transaction, error)
        else:
            # If another actor recreated the live folder, never discard either
            # side: backup_code may be the only copy of the approved checkout.
            transaction.rollback_from = "code_backed_up"
            self._set_phase(
                transaction,
                "rollback_pending",
                error=(
                    error
                    + " Both the live folder and retained checkout require "
                    "recovery."
                ),
            )
        raise RuntimeError(error)

    def _activate_locked(self, transaction, validate_current=True):
        if transaction.phase != "dependencies_staged":
            raise RuntimeError(
                "Transaction must reach dependencies_staged before activation."
            )
        self._validate_activation_paths(transaction)
        if not self._staged_metadata_matches_target(transaction):
            error = "Staged release metadata does not match the pinned target."
            self._rollback_locked(transaction, error)
            raise ValueError(error)
        target_dependencies = transaction.dependency_state.get("target")
        if target_dependencies is None or not self._dependency_tree_matches(
            transaction.paths.staged_dependencies,
            target_dependencies,
            "Staged dependency snapshot",
        ):
            error = "Staged dependencies do not match their pinned snapshot."
            self._rollback_locked(transaction, error)
            raise ValueError(error)
        if validate_current:
            if transaction.operation == "release_install":
                try:
                    self.plugin.assert_release_install_absent_locked(
                        transaction.plugin_key
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    message = (
                        "Release activation stopped because another installed "
                        "folder now claims this plugin: "
                        + str(error)
                    )
                    self._complete_pre_activation_rollback_locked(
                        transaction,
                        message,
                    )
                    raise RuntimeError(message) from None
            current_matches = self._metadata_matches(transaction)
            dependencies_match = self._dependency_tree_matches(
                transaction.paths.live_dependencies,
                transaction.dependency_state["expected"],
                "Installed dependency snapshot",
            )
            if not (current_matches and dependencies_match):
                changed = []
                if not current_matches:
                    changed.append("the installed plugin folder or its contents")
                if not dependencies_match:
                    changed.append("the shared dependencies")
                error = (
                    "Release activation stopped because "
                    + " and ".join(changed)
                    + " changed while the release was being prepared; the "
                    "installed state no longer matches expected_current. "
                    "Refresh and try again."
                )
                self._complete_pre_activation_rollback_locked(
                    transaction,
                    error,
                )
                raise RuntimeError(error)
        try:
            self._replace_directory(
                transaction,
                transaction.paths.live_code,
                transaction.paths.backup_code,
                "code_backup_pending",
                "code_backed_up",
            )
            self._revalidate_migration_backup_locked(transaction)
            self._replace_directory(
                transaction,
                transaction.paths.live_dependencies,
                transaction.paths.backup_dependencies,
                "dependencies_backup_pending",
                "dependencies_backed_up",
            )
            self._replace_directory(
                transaction,
                transaction.paths.staged_dependencies,
                transaction.paths.live_dependencies,
                "dependencies_activation_pending",
                "dependencies_activated",
            )
            self._replace_directory(
                transaction,
                transaction.paths.staged_code,
                transaction.paths.live_code,
                "code_activation_pending",
                "release_activated",
            )
            return self._set_phase(transaction, "restart_pending")
        except Exception as error:
            if (
                transaction.phase
                in RELEASE_MIGRATION_RESTORE_PHASES
                or transaction.phase
                in {"rollback_pending", "stale_target"}
            ):
                raise
            host = self.plugin.get_host()
            locked_file = bool(
                transaction.phase
                in {
                    "code_backup_pending",
                    "dependencies_backup_pending",
                    "dependencies_activation_pending",
                    "code_activation_pending",
                }
                and host.is_locked_file_error(error)
            )
            if locked_file:
                transaction.rollback_from = transaction.phase
                self._set_phase(
                    transaction,
                    "queued_locked",
                    error="Plugin files are locked; activation is queued.",
                )
                self._sync_pending_transactions_locked()
                self._rollback_locked(transaction, str(error))
                transaction.rollback_from = ""
                self._set_phase(
                    transaction,
                    "queued_locked",
                    error="Plugin files are locked; activation is queued.",
                )
                self._sync_pending_transactions_locked()
                return transaction
            self._rollback_locked(transaction, str(error))
            raise

    def activate(self, operation_id):
        with self.operation_lock():
            transaction = self._load_transaction(operation_id)
            return self._activate_locked(transaction)

    def abort(self, operation_id, error):
        """Discard a pre-activation transaction without touching live state."""
        error = str(error or "Release operation was aborted.")
        with self.operation_lock():
            transaction = self._load_transaction(operation_id)
            if transaction.phase in {"rolled_back", "stale_target"} and (
                transaction.rollback_from
                in RELEASE_TRANSACTION_PREACTIVATION_PHASES
            ):
                self._discard_transaction_payloads(transaction)
                return transaction
            if (
                transaction.phase == "stale_target"
                and not transaction.rollback_from
                and self._operation_root_is_empty(
                    transaction.paths.backup_root
                )
            ):
                # Schema-v1/v2 builds could record a pre-rename mismatch as a
                # terminal stale target without its safe rollback origin.
                self._discard_transaction_payloads(transaction)
                return transaction
            if not (
                transaction.phase
                in RELEASE_TRANSACTION_PREACTIVATION_PHASES
                or (
                    transaction.phase == "rollback_pending"
                    and transaction.rollback_from
                    in RELEASE_TRANSACTION_PREACTIVATION_PHASES
                )
            ):
                raise RuntimeError(
                    "An activation that may have changed live state must be rolled back."
                )
            return self._complete_pre_activation_rollback_locked(
                transaction,
                error,
            )

    def rollback(self, operation_id):
        with self.workflow_lock():
            with self.operation_lock():
                transaction = self._load_transaction(operation_id)
                if transaction.phase not in (
                    "restart_pending",
                    "release_managed",
                ):
                    raise RuntimeError(
                        "Only an activated release can be explicitly rolled back."
                    )
                live_target_matches = self._release_tree_matches_target(
                    transaction.paths.live_code,
                    transaction,
                ) and self._active_dependencies_match_target(transaction)
                backup_matches = self._code_path_matches(
                    transaction.paths.backup_code,
                    transaction.plugin_key,
                    transaction.expected_current,
                ) and self._dependency_tree_matches(
                    transaction.paths.backup_dependencies,
                    transaction.dependency_state["expected"],
                    "Retained dependency backup",
                )
                if not live_target_matches or not backup_matches:
                    raise RuntimeError(
                        "Rollback snapshots no longer match the transaction."
                    )
                return self._rollback_locked(transaction)

    def _prune_older_backups_locked(self, transaction):
        """Retain only the backup belonging to the verified live release."""
        if not self._rollback_snapshots_match_locked(transaction):
            return
        for previous in self._transactions_locked(transaction.plugin_key):
            if previous.operation_id == transaction.operation_id:
                continue
            for path in (
                previous.paths.backup_code,
                previous.paths.backup_dependencies,
            ):
                self._remove_transaction_path(path)

    def _mark_release_managed_locked(self, transaction):
        if transaction.phase not in {"restart_pending", "release_managed"}:
            raise RuntimeError(
                "Transaction must be restart_pending before completion."
            )
        if not (
            self._release_tree_matches_target(
                transaction.paths.live_code,
                transaction,
            )
            and self._active_dependencies_match_target(transaction)
        ):
            raise RuntimeError(
                "Activated release no longer matches the pinned target."
            )
        if transaction.phase == "restart_pending":
            self._set_phase(transaction, "release_managed")
        self._prune_older_backups_locked(transaction)
        return transaction

    def mark_release_managed(self, operation_id):
        with self.workflow_lock():
            with self.operation_lock():
                transaction = self._load_transaction(operation_id)
                return self._mark_release_managed_locked(transaction)

    def finalize_startup(self, blocking=True):
        """Recover durable work, then finish restart-bound transactions."""
        with self.workflow_lock(blocking=blocking):
            self._recover_pending_locked(blocking=blocking)
            return self._finalize_startup_locked(blocking=blocking)

    def _finalize_startup_locked(self, blocking=True):
        finalized = []
        with self.operation_lock(blocking=blocking):
            transactions = self._transactions_locked()
            restart_transactions = [
                transaction
                for transaction in transactions
                if transaction.phase == "restart_pending"
            ]
            dependency_owner = next(
                (
                    transaction
                    for transaction in reversed(restart_transactions)
                    if self._release_tree_matches_target(
                        transaction.paths.live_code,
                        transaction,
                    )
                    and self._active_dependencies_match_target(transaction)
                ),
                None,
            )
            finalized_plugins = set()
            for transaction in reversed(restart_transactions):
                if transaction.plugin_key in finalized_plugins:
                    finalized.append(
                        self._set_phase(
                            transaction,
                            "stale_target",
                            error=(
                                "A newer release transaction superseded this "
                                "restart-pending operation."
                            ),
                        )
                    )
                    continue
                if (
                    self._release_tree_matches_target(
                        transaction.paths.live_code,
                        transaction,
                    )
                    and self._active_dependencies_match_target(transaction)
                ):
                    finalized.append(
                        self._mark_release_managed_locked(transaction)
                    )
                elif dependency_owner is not None:
                    code_matches = self._release_tree_matches_target(
                        transaction.paths.live_code,
                        transaction,
                    )
                    finalized.append(
                        self._set_phase(
                            transaction,
                            (
                                "release_managed"
                                if code_matches
                                else "stale_target"
                            ),
                            error=(
                                "Shared dependencies were superseded by a "
                                "newer release transaction; this older "
                                "transaction cannot be rolled back."
                            ),
                        )
                    )
                else:
                    finalized.append(
                        self._rollback_locked(
                            transaction,
                            "Activated release did not survive restart verification.",
                        )
                    )
                finalized_plugins.add(transaction.plugin_key)

            for transaction in self._transactions_locked():
                if not (
                    transaction.phase == "rolled_back"
                    and transaction.rollback_from
                    in {"restart_pending", "release_managed"}
                ):
                    continue
                if not (
                    self._metadata_matches(transaction)
                    and self._dependency_tree_matches(
                        transaction.paths.live_dependencies,
                        transaction.dependency_state["expected"],
                        "Restored dependency snapshot",
                    )
                ):
                    raise RuntimeError(
                        "Rolled-back release did not survive restart verification."
                    )
                transaction.rollback_from = ""
                self._write_transaction(transaction)
                finalized.append(transaction)
        return finalized

    def _recover_transaction_locked(self, transaction):
        if transaction.phase == "queued_locked":
            return transaction
        if transaction.phase in RELEASE_MIGRATION_RESTORE_PHASES:
            return self._recover_migration_restore_locked(transaction)
        if (
            transaction.phase == "rollback_pending"
            and transaction.rollback_from
            in RELEASE_TRANSACTION_PREACTIVATION_PHASES
        ):
            return self._complete_pre_activation_rollback_locked(
                transaction
            )
        if transaction.phase == "dependency_confirmation_required":
            return self._complete_pre_activation_rollback_locked(
                transaction,
                "Dependency compatibility confirmation expired during restart.",
            )
        if (
            transaction.phase == "stale_target"
            and not transaction.rollback_from
            and self._operation_root_is_empty(
                transaction.paths.backup_root
            )
        ):
            # Older builds leaked staged payloads after a safe pre-rename
            # comparison failure. They are manager-owned and no backup was
            # ever created, so startup can finish that cleanup safely.
            self._discard_transaction_payloads(transaction)
            return transaction
        if transaction.phase in RELEASE_TRANSACTION_FINAL_PHASES:
            return transaction
        if transaction.phase == "release_activated":
            if (
                self._active_dependencies_match_target(transaction)
                and self._release_tree_matches_target(
                    transaction.paths.live_code,
                    transaction,
                )
            ):
                return self._set_phase(transaction, "restart_pending")
            return self._rollback_locked(
                transaction,
                "Activated release no longer matches the pinned target.",
            )
        if transaction.phase == "code_activation_pending":
            live_code = transaction.paths.live_code
            if (
                os.path.isdir(live_code)
                and not os.path.islink(live_code)
                and self._active_dependencies_match_target(transaction)
                and self._release_tree_matches_target(
                    live_code,
                    transaction,
                )
            ):
                self._set_phase(transaction, "release_activated")
                return self._set_phase(transaction, "restart_pending")
        return self._rollback_locked(
            transaction,
            transaction.error
            or "Recovered an interrupted release transaction.",
        )

    def _recover_pending_locked(self, blocking=True):
        with self.operation_lock(blocking=blocking):
            pending_transactions = self._load_pending_transactions_locked()
            pending_operation_ids = {
                transaction.operation_id
                for transaction in pending_transactions
            }
            pending_by_operation = {
                transaction.operation_id: transaction
                for transaction in pending_transactions
            }
            _manager, _plugins, state_root = self._manager_paths(create=True)
            transaction_dir = os.path.join(state_root, "transactions")
            transactions = self._transactions_locked()
            dependency_owner = next(
                (
                    transaction
                    for transaction in reversed(transactions)
                    if transaction.phase
                    in RELEASE_GLOBAL_DEPENDENCY_ROLLBACK_PHASES
                    and self._release_tree_matches_target(
                        transaction.paths.live_code,
                        transaction,
                    )
                    and self._active_dependencies_match_target(transaction)
                ),
                None,
            )
            global_recovery_candidates = []
            for transaction in transactions:
                origin_phase = (
                    transaction.rollback_from
                    if transaction.phase in {"rollback_pending", "queued_locked"}
                    and transaction.rollback_from
                    else transaction.phase
                )
                if (
                    transaction.phase
                    not in RELEASE_TRANSACTION_FINAL_PHASES
                    and origin_phase
                    in RELEASE_GLOBAL_DEPENDENCY_ROLLBACK_PHASES
                ):
                    global_recovery_candidates.append(transaction)
            if (
                dependency_owner is None
                and len(global_recovery_candidates) > 1
            ):
                raise RuntimeError(
                    "Recovery found multiple unfinished release activations "
                    "but could not identify the live shared dependency "
                    "generation. No files were changed."
                )
            queued = []
            for name in sorted(os.listdir(transaction_dir)):
                if not name.endswith(".json"):
                    continue
                operation_id = name[:-5]
                transaction = self._load_transaction(operation_id)
                origin_phase = (
                    transaction.rollback_from
                    if transaction.phase in {"rollback_pending", "queued_locked"}
                    and transaction.rollback_from
                    else transaction.phase
                )
                superseded_dependencies = bool(
                    dependency_owner is not None
                    and dependency_owner.operation_id
                    != transaction.operation_id
                    and transaction.phase
                    not in RELEASE_TRANSACTION_FINAL_PHASES
                    and origin_phase
                    in RELEASE_GLOBAL_DEPENDENCY_ROLLBACK_PHASES
                )
                if superseded_dependencies:
                    error = (
                        "Recovery stopped an older activation from replacing "
                        "newer shared dependencies."
                    )
                    if self._release_tree_matches_target(
                        transaction.paths.live_code,
                        transaction,
                    ):
                        transaction.rollback_from = ""
                        recovered = self._set_phase(
                            transaction,
                            "restart_pending",
                            error=error,
                        )
                    else:
                        if transaction.phase != "rollback_pending":
                            transaction.rollback_from = origin_phase
                        self._set_phase(
                            transaction,
                            "rollback_pending",
                            error=error,
                        )
                        raise RuntimeError(error)
                elif transaction.phase == "queued_locked":
                    if operation_id not in pending_by_operation:
                        pending_by_operation[operation_id] = transaction
                        pending_operation_ids.add(operation_id)
                        self._write_pending_transactions_locked(
                            list(pending_by_operation.values())
                        )
                    if transaction.rollback_from:
                        self._rollback_locked(
                            transaction,
                            "Recovered a locked activation before retry.",
                        )
                        transaction.rollback_from = ""
                        self._set_phase(
                            transaction,
                            "queued_locked",
                            error=(
                                "Plugin files are locked; activation is "
                                "queued."
                            ),
                        )
                    queued.append(transaction)
                    continue
                else:
                    recovered = self._recover_transaction_locked(transaction)
                if (
                    operation_id in pending_operation_ids
                    and recovered.phase == "rolled_back"
                ):
                    recovered.rollback_from = ""
                    self._set_phase(
                        recovered,
                        "queued_locked",
                        error="Plugin files are locked; activation is queued.",
                    )
                    queued.append(recovered)
            for transaction in queued:
                transaction.phase = "dependencies_staged"
                transaction.rollback_from = ""
                transaction.error = ""
                try:
                    self._activate_locked(transaction)
                except RuntimeError:
                    if transaction.phase != "stale_target":
                        raise
            self._sync_pending_transactions_locked()

    def recover_pending(self, blocking=True):
        with self.workflow_lock(blocking=blocking):
            return self._recover_pending_locked(blocking=blocking)


class ReleaseDependencyError(RuntimeError):
    """A dependency snapshot that cannot safely proceed to activation."""

    status = "dependency_blocked"

    def __init__(self, reason, message, manual_required=False):
        super().__init__(message)
        self.reason = str(reason)
        self.message = str(message)
        self.manual_required = bool(manual_required)


@dataclass(frozen=True)
class ReleaseDependencySnapshotResult:
    """Auditable outcome of constructing a staged dependency snapshot."""

    status: str
    installer: str
    command: list
    requirements_file: str
    compatibility_warnings: list
    compatibility_conflicts: list
    requires_confirmation: bool
    compatibility_confirmed: bool

    def to_document(self):
        return {
            "schema_version": 1,
            "status": self.status,
            "installer": self.installer,
            "command": list(self.command),
            "requirements_file": self.requirements_file,
            "compatibility_warnings": list(self.compatibility_warnings),
            "compatibility_conflicts": list(self.compatibility_conflicts),
            "requires_confirmation": self.requires_confirmation,
            "compatibility_confirmed": self.compatibility_confirmed,
        }

    @classmethod
    def from_document(cls, document):
        document = _require_document(
            document,
            "dependency snapshot",
            (
                "schema_version",
                "status",
                "installer",
                "command",
                "requirements_file",
                "compatibility_warnings",
                "compatibility_conflicts",
                "requires_confirmation",
                "compatibility_confirmed",
            ),
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
        ):
            raise ValueError("Dependency snapshot schema is unsupported.")
        status = _require_nonempty_string(document["status"], "status")
        if status not in {
            "dependencies_staged",
            "dependency_confirmation_required",
        }:
            raise ValueError("Dependency snapshot status is unsupported.")
        installer = _require_nonempty_string(
            document["installer"],
            "installer",
        )
        if installer not in {"none", "pip", "uv"}:
            raise ValueError("Dependency snapshot installer is unsupported.")
        command = document["command"]
        if not isinstance(command, list) or any(
            not isinstance(part, str) or not part for part in command
        ):
            raise ValueError("Dependency snapshot command is invalid.")
        requirements_file = document["requirements_file"]
        if not isinstance(requirements_file, str):
            raise ValueError("Dependency snapshot requirements path is invalid.")

        def messages(name):
            value = document[name]
            if not isinstance(value, list) or any(
                not isinstance(message, str) or not message
                for message in value
            ):
                raise ValueError(name + " must contain non-empty messages.")
            return list(value)

        requires_confirmation = _require_boolean(
            document["requires_confirmation"],
            "requires_confirmation",
        )
        compatibility_confirmed = _require_boolean(
            document["compatibility_confirmed"],
            "compatibility_confirmed",
        )
        conflicts = messages("compatibility_conflicts")
        if status == "dependency_confirmation_required" and (
            not requires_confirmation
            or compatibility_confirmed
            or not conflicts
        ):
            raise ValueError("Dependency confirmation state is inconsistent.")
        if status == "dependencies_staged" and requires_confirmation:
            raise ValueError("Staged dependency state is inconsistent.")
        if (installer == "none") != (command == []):
            raise ValueError("Dependency installer command is inconsistent.")
        return cls(
            status=status,
            installer=installer,
            command=list(command),
            requirements_file=requirements_file,
            compatibility_warnings=messages("compatibility_warnings"),
            compatibility_conflicts=conflicts,
            requires_confirmation=requires_confirmation,
            compatibility_confirmed=compatibility_confirmed,
        )


class _ReleaseDependencyFilesystem:
    """Construct dependency trees without following filesystem links."""

    def discard_tree(self, path):
        path = os.path.abspath(str(path))
        if not os.path.lexists(path):
            return
        path_stat = os.lstat(path)
        if stat.S_ISDIR(path_stat.st_mode) and not stat.S_ISLNK(
            path_stat.st_mode
        ):
            shutil.rmtree(path)
        else:
            os.unlink(path)

    def _copy_file(self, source, destination, source_stat):
        source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        source_flags |= getattr(os, "O_NOFOLLOW", 0)
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        destination_flags |= getattr(os, "O_BINARY", 0)
        source_descriptor = os.open(source, source_flags)
        destination_descriptor = None
        try:
            opened_stat = os.fstat(source_descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != source_stat.st_dev
                or opened_stat.st_ino != source_stat.st_ino
                or opened_stat.st_size != source_stat.st_size
                or getattr(opened_stat, "st_nlink", 1) > 1
            ):
                raise ValueError("Dependency file changed while being copied.")
            destination_descriptor = os.open(
                destination,
                destination_flags,
                stat.S_IMODE(source_stat.st_mode),
            )
            while True:
                chunk = os.read(source_descriptor, RELEASE_HTTP_CHUNK_SIZE)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_descriptor, view)
                    view = view[written:]
            try:
                os.fchmod(
                    destination_descriptor,
                    stat.S_IMODE(source_stat.st_mode),
                )
            except (AttributeError, OSError):
                pass
        finally:
            os.close(source_descriptor)
            if destination_descriptor is not None:
                os.close(destination_descriptor)

    def _copy_directory(self, source, destination, root_device):
        with os.scandir(source) as entries:
            entries = sorted(entries, key=lambda entry: entry.name)
        for entry in entries:
            try:
                entry_stat = os.stat(
                    entry.path,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                if (
                    entry.name.casefold()
                    in RELEASE_VOLATILE_CACHE_DIRECTORIES
                ):
                    continue
                raise
            if entry_stat.st_dev != root_device:
                raise ValueError(
                    "Dependency snapshot may not cross filesystem boundaries."
                )
            target = os.path.join(destination, entry.name)
            if stat.S_ISDIR(entry_stat.st_mode):
                if (
                    entry.name.casefold()
                    in RELEASE_VOLATILE_CACHE_DIRECTORIES
                ):
                    continue
                os.mkdir(target, stat.S_IMODE(entry_stat.st_mode))
                self._copy_directory(entry.path, target, root_device)
                continue
            if (
                not stat.S_ISREG(entry_stat.st_mode)
                or stat.S_ISLNK(entry_stat.st_mode)
                or getattr(entry_stat, "st_nlink", 1) > 1
            ):
                raise ValueError(
                    "Dependency snapshot contains a link or special file."
                )
            self._copy_file(entry.path, target, entry_stat)

    def snapshot_tree(self, source, destination):
        source = os.path.abspath(str(source))
        destination = os.path.abspath(str(destination))
        if source == destination:
            raise ValueError("Dependency snapshot paths must be distinct.")
        self.discard_tree(destination)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        if not os.path.lexists(source):
            os.mkdir(destination)
            return
        source_stat = os.lstat(source)
        if (
            not stat.S_ISDIR(source_stat.st_mode)
            or stat.S_ISLNK(source_stat.st_mode)
        ):
            raise ValueError("Live dependencies must be a real directory.")
        os.mkdir(destination, stat.S_IMODE(source_stat.st_mode))
        try:
            self._copy_directory(source, destination, source_stat.st_dev)
        except Exception:
            self.discard_tree(destination)
            raise


class _ReleaseDependencyCommandRunner:
    """Discover and execute supported dependency installers."""

    def available(self, command):
        if command == "uv":
            return shutil.which("uv") is not None
        if command == "pip":
            try:
                __import__("pip")
                return True
            except ImportError:
                return False
        return False

    def run(self, command, *, env=None):
        return subprocess.run(
            list(command),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=900,
        )


class _ReleaseDependencyValidator:
    """Perform the baseline structural validation for a staged snapshot."""

    def validate(self, staged_dependencies, requirements_file):
        del requirements_file
        root = os.path.abspath(str(staged_dependencies))
        try:
            root_stat = os.lstat(root)
            if (
                not stat.S_ISDIR(root_stat.st_mode)
                or stat.S_ISLNK(root_stat.st_mode)
            ):
                raise ValueError("Staged dependencies are not a real directory.")
            stack = [root]
            while stack:
                directory = stack.pop()
                with os.scandir(directory) as entries:
                    entries = list(entries)
                for entry in entries:
                    entry_stat = os.stat(
                        entry.path,
                        follow_symlinks=False,
                    )
                    if entry_stat.st_dev != root_stat.st_dev:
                        raise ValueError(
                            "Staged dependencies cross a filesystem boundary."
                        )
                    if stat.S_ISDIR(entry_stat.st_mode):
                        stack.append(entry.path)
                    elif (
                        not stat.S_ISREG(entry_stat.st_mode)
                        or stat.S_ISLNK(entry_stat.st_mode)
                        or getattr(entry_stat, "st_nlink", 1) > 1
                    ):
                        raise ValueError(
                            "Staged dependencies contain a link or special file."
                        )
        except (OSError, ValueError) as error:
            return {
                "valid": False,
                "message": str(error),
                "warnings": [],
                "conflicts": [],
            }
        return {
            "valid": True,
            "message": "",
            "warnings": [],
            "conflicts": [],
        }


class ReleaseDependencySnapshotService:
    """Build and validate shared dependencies before atomic activation."""

    def __init__(
        self,
        plugin,
        *,
        transaction_manager=None,
        command_runner=None,
        filesystem=None,
        validator=None,
    ):
        if plugin is None:
            raise ValueError("plugin is required.")
        self.plugin = plugin
        self.transaction_manager = (
            transaction_manager or ReleaseTransactionManager(plugin)
        )
        self.command_runner = command_runner or _ReleaseDependencyCommandRunner()
        self.filesystem = filesystem or _ReleaseDependencyFilesystem()
        self.validator = validator or _ReleaseDependencyValidator()

    def _requirements_path(self, transaction, requirements_file):
        staged_code = os.path.abspath(str(transaction.paths.staged_code))
        if requirements_file is None:
            requirements_file = os.path.join(staged_code, "requirements.txt")
        requirements_file = os.path.abspath(str(requirements_file))
        try:
            contained = os.path.commonpath(
                (staged_code, requirements_file)
            ) == staged_code
        except ValueError:
            contained = False
        if not contained:
            raise ValueError(
                "Dependency requirements must belong to staged release code."
            )
        return requirements_file

    def _messages(self, validation, name):
        messages = validation.get(name, [])
        if not isinstance(messages, (list, tuple)) or any(
            not isinstance(message, str) or not message
            for message in messages
        ):
            raise ValueError(name + " must contain non-empty messages.")
        return list(messages)

    def _block(
        self,
        operation_id,
        staged_dependencies,
        reason,
        message,
        *,
        manual_required=False,
    ):
        cleanup_error = None
        try:
            self.filesystem.discard_tree(staged_dependencies)
        except Exception as error:
            cleanup_error = error
        if cleanup_error is not None:
            message += " Staged cleanup also failed: " + str(cleanup_error)
        self.transaction_manager.mark_dependency_blocked(
            operation_id,
            reason,
            message,
        )
        raise ReleaseDependencyError(
            reason,
            message,
            manual_required=manual_required,
        )

    def _confirm_existing(
        self,
        transaction,
        requirements_file,
        compatibility_confirmed,
    ):
        result = ReleaseDependencySnapshotResult.from_document(
            transaction.dependency_snapshot
        )
        if result.status != "dependency_confirmation_required":
            raise ValueError("Dependency confirmation snapshot is invalid.")
        if result.requirements_file != requirements_file:
            raise ValueError(
                "Dependency confirmation does not match the requirements file."
            )
        if not compatibility_confirmed:
            return result
        confirmed = ReleaseDependencySnapshotResult(
            status="dependencies_staged",
            installer=result.installer,
            command=list(result.command),
            requirements_file=result.requirements_file,
            compatibility_warnings=list(result.compatibility_warnings),
            compatibility_conflicts=list(result.compatibility_conflicts),
            requires_confirmation=False,
            compatibility_confirmed=True,
        )
        self.transaction_manager.mark_dependencies_staged(
            transaction.operation_id,
            confirmed.to_document(),
        )
        return confirmed

    def stage(
        self,
        operation_id,
        *,
        requirements_file=None,
        installer="auto",
        compatibility_confirmed=False,
    ):
        transaction = self.transaction_manager.load_transaction(operation_id)
        requirements_file = self._requirements_path(
            transaction,
            requirements_file,
        )
        if transaction.phase == "dependency_confirmation_required":
            return self._confirm_existing(
                transaction,
                requirements_file,
                bool(compatibility_confirmed),
            )
        if transaction.phase != "staged_verified":
            raise RuntimeError(
                "Transaction must be staged_verified before dependencies."
            )
        if installer not in {"auto", "pip", "uv"}:
            raise ValueError("installer must be auto, pip, or uv.")

        live_dependencies = os.path.abspath(
            str(transaction.paths.live_dependencies)
        )
        staged_dependencies = os.path.abspath(
            str(transaction.paths.staged_dependencies)
        )
        try:
            self.filesystem.snapshot_tree(
                live_dependencies,
                staged_dependencies,
            )
        except Exception as error:
            self._block(
                transaction.operation_id,
                staged_dependencies,
                "snapshot_failed",
                "Dependency snapshot failed: " + str(error),
            )

        selected_installer = "none"
        command = []
        requirements_present = os.path.lexists(requirements_file)
        if requirements_present:
            try:
                requirements_stat = os.lstat(requirements_file)
            except OSError as error:
                self._block(
                    transaction.operation_id,
                    staged_dependencies,
                    "validation_failed",
                    "Dependency requirements could not be read: " + str(error),
                )
            if (
                not stat.S_ISREG(requirements_stat.st_mode)
                or stat.S_ISLNK(requirements_stat.st_mode)
            ):
                self._block(
                    transaction.operation_id,
                    staged_dependencies,
                    "validation_failed",
                    "Dependency requirements must be a regular file.",
                )
            candidates = ("uv", "pip") if installer == "auto" else (installer,)
            selected_installer = next(
                (
                    candidate
                    for candidate in candidates
                    if self.command_runner.available(candidate)
                ),
                "",
            )
            if not selected_installer:
                self._block(
                    transaction.operation_id,
                    staged_dependencies,
                    "installer_unavailable",
                    "No supported dependency installer is available; manual "
                    "dependency handling is required.",
                    manual_required=True,
                )
            if selected_installer == "uv":
                command = [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    sys.executable,
                    "--target",
                    staged_dependencies,
                    "-r",
                    requirements_file,
                ]
            else:
                command = [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--target",
                    staged_dependencies,
                    "-r",
                    requirements_file,
                ]
            try:
                completed = self.command_runner.run(command, env=None)
            except Exception as error:
                self._block(
                    transaction.operation_id,
                    staged_dependencies,
                    "install_failed",
                    "Dependency installation failed: " + str(error),
                )
            if completed.returncode != 0:
                detail = str(completed.stderr or completed.stdout or "").strip()
                message = "Dependency installation failed."
                if detail:
                    message += " " + detail
                self._block(
                    transaction.operation_id,
                    staged_dependencies,
                    "install_failed",
                    message,
                )

        try:
            validation = self.validator.validate(
                staged_dependencies,
                requirements_file,
            )
            if not isinstance(validation, Mapping):
                raise ValueError("Dependency validator returned an invalid result.")
            warnings = self._messages(validation, "warnings")
            conflicts = self._messages(validation, "conflicts")
            valid = validation.get("valid") is True
            validation_message = validation.get("message", "")
            if not isinstance(validation_message, str):
                raise ValueError("Dependency validation message must be text.")
        except Exception as error:
            self._block(
                transaction.operation_id,
                staged_dependencies,
                "validation_failed",
                "Dependency validation failed: " + str(error),
            )
        if not valid:
            self._block(
                transaction.operation_id,
                staged_dependencies,
                "validation_failed",
                validation_message or "Dependency validation failed.",
            )

        requires_confirmation = bool(
            conflicts and not compatibility_confirmed
        )
        result = ReleaseDependencySnapshotResult(
            status=(
                "dependency_confirmation_required"
                if requires_confirmation
                else "dependencies_staged"
            ),
            installer=selected_installer,
            command=list(command),
            requirements_file=requirements_file,
            compatibility_warnings=warnings,
            compatibility_conflicts=conflicts,
            requires_confirmation=requires_confirmation,
            compatibility_confirmed=bool(compatibility_confirmed),
        )
        if requires_confirmation:
            self.transaction_manager.mark_dependency_confirmation_required(
                transaction.operation_id,
                result.to_document(),
            )
        else:
            self.transaction_manager.mark_dependencies_staged(
                transaction.operation_id,
                result.to_document(),
            )
        return result


class RegistryEntry:
    def __init__(
        self,
        key,
        author,
        repository,
        description,
        branch,
        updated_at="",
        platforms=None,
        updated_at_slot_present=False,
        local=False,
        delivery=None,
        domoticz_key="",
    ):
        self.package_id = str(key or "")
        self.key = self.package_id
        self.author = str(author or "")
        self.repository = str(repository or "")
        self.description = str(description or "")
        self.branch = str(branch or "master")
        self.updated_at = str(updated_at or "")
        self.platforms = list(platforms or ["unknown"])
        self.updated_at_slot_present = bool(updated_at_slot_present or self.updated_at)
        self.local = bool(local)
        self.delivery = delivery or DeliveryPolicy.implicit()
        self.domoticz_key = str(domoticz_key or "")

    def to_legacy_list(self):
        entry = [self.author, self.repository, self.description, self.branch]
        if self.updated_at_slot_present or self.updated_at:
            entry.append(self.updated_at)
        return entry

    def copy_with(self, author=None, repository=None, branch=None):
        return RegistryEntry(
            self.key,
            self.author if author is None else author,
            self.repository if repository is None else repository,
            self.description,
            self.branch if branch is None else branch,
            self.updated_at,
            self.platforms,
            self.updated_at_slot_present,
            self.local,
            self.delivery,
            self.domoticz_key,
        )


class RegistryService:
    def __init__(self, plugin):
        self.plugin = plugin

    def normalize_entry(self, key, data, local=False, default_platforms=None):
        platforms = ["unknown"]
        if isinstance(data, dict):
            explicit_repository = data.get("repository")
            if "package_id" in data and isinstance(
                explicit_repository, dict
            ):
                if data.get("package_id") != key:
                    Domoticz.Error(
                        "Plugin '" + str(key) + "' package_id does not match its record."
                    )
                    return None, ["unknown"]
                author = explicit_repository.get("url", "")
                repository = ""
                branch = explicit_repository.get("branch", "master")
                domoticz_key = data.get("domoticz_key", "")
            else:
                author = data.get("author", data.get("owner", ""))
                repository = data.get("repository", data.get("repo", ""))
                branch = data.get("branch", "master")
                domoticz_key = data.get("domoticz_key", "")
            description = data.get("description", "")
            updated_at = "" if local else data.get("updated_at", "")
            platforms = self.plugin.normalize_platforms(data.get("platforms", data.get("platform", None)))
            try:
                if local:
                    delivery = DeliveryPolicy.git_only()
                else:
                    delivery_document = data.get("delivery")
                    if (
                        isinstance(delivery_document, dict)
                        and "schema_version" not in delivery_document
                    ):
                        delivery_document = {
                            "schema_version": DELIVERY_POLICY_SCHEMA_VERSION,
                            **delivery_document,
                        }
                    delivery = (
                        DeliveryPolicy.from_document(
                            delivery_document,
                            normalize_repository_identity(author, repository),
                        )
                        if "delivery" in data
                        else DeliveryPolicy.implicit()
                    )
            except ValueError as error:
                Domoticz.Error(
                    "Plugin '" + str(key) + "' has invalid delivery policy: "
                    + str(error)
                )
                return None, ["unknown"]
            entry = RegistryEntry(
                key,
                author,
                repository,
                description,
                branch,
                updated_at,
                platforms,
                bool(updated_at),
                local,
                delivery,
                domoticz_key,
            )
        elif isinstance(data, list):
            raw_entry = list(data[:5])
            if len(data) > 5:
                platforms = self.plugin.normalize_platforms(data[5])
            elif default_platforms is not None:
                platforms = self.plugin.normalize_platforms(default_platforms)
            if len(raw_entry) < 4:
                Domoticz.Error("Plugin '" + str(key) + "' registry entry must contain owner, repository, description and branch.")
                return None, ["unknown"]
            updated_at = "" if local else (raw_entry[4] if len(raw_entry) >= 5 else "")
            entry = RegistryEntry(
                key,
                raw_entry[0],
                raw_entry[1],
                raw_entry[2],
                raw_entry[3],
                updated_at,
                platforms,
                False if local else len(raw_entry) >= 5,
                local,
                DeliveryPolicy.git_only() if local else None,
            )
        else:
            Domoticz.Error("Plugin '" + str(key) + "' has an invalid registry entry.")
            return None, ["unknown"]

        return entry, platforms

    def records_from_document(self, registry):
        if not isinstance(registry, dict):
            raise ValueError("Registry must be an object.")
        if "schema_version" in registry:
            parsed = PackageRegistry.from_document(registry)
            return {
                package.package_id: package.to_document()
                for package in parsed.packages
            }
        return dict(registry)

    def normalize_registry(self, registry, local_keys=None):
        registry = self.records_from_document(registry)
        local_keys = set(local_keys or [])
        normalized_registry = {}
        plugin_platforms = {}
        registry_entries = {}
        for key, data in registry.items():
            if key == "Idle":
                normalized_registry[key] = data
                plugin_platforms[key] = ["unknown"]
                continue

            try:
                self.plugin.get_host().validate_plugin_key(key)
            except ValueError as e:
                Domoticz.Error("Skipping invalid plugin key '" + str(key) + "': " + str(e))
                continue

            entry, platforms = self.normalize_entry(key, data, local=key in local_keys)
            if entry is None:
                continue
            normalized_registry[key] = entry.to_legacy_list()
            plugin_platforms[key] = platforms
            registry_entries[key] = entry
        return normalized_registry, plugin_platforms, registry_entries


@dataclass
class LocalRegistryDocument:
    """A byte-revisioned view of registry_local.json."""

    entries: dict
    revision: str
    path: str
    exists: bool
    writable: bool
    error_code: str = ""
    message: str = ""
    schema_version: int = 2
    backup_path: str = ""
    migrated: bool = False


class LocalRegistryError(Exception):
    """A structured local-registry operation failure."""

    def __init__(
        self,
        code,
        message,
        field_errors=None,
        reload_required=False,
        read_only=False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_errors = dict(field_errors or {})
        self.reload_required = reload_required
        self.read_only = read_only


class LocalRegistryService:
    """Read, validate, and atomically mutate registry_local.json."""

    SCHEMA_VERSION = 2
    BACKUP_FILE_NAME = "registry_local.v1.backup.json"
    MISSING_REVISION_BYTES = b"PyPluginStore:missing-local-registry:v2"
    DOCUMENT_FIELDS = {"schema_version", "packages"}
    PACKAGE_FIELDS = {
        "package_id",
        "domoticz_key",
        "description",
        "repository",
        "platforms",
    }
    REPOSITORY_FIELDS = {"url", "branch"}
    FIELD_LIMITS = {
        "key": 128,
        "repository_source": 1000,
        "description": 500,
        "branch": 255,
    }
    CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

    def __init__(self, plugin):
        self.plugin = plugin

    def revision_for_bytes(self, contents):
        """Return a stable SHA-256 revision for exact file bytes."""
        return "sha256:" + hashlib.sha256(contents).hexdigest()

    def backup_path(self):
        return os.path.join(
            os.path.dirname(self.plugin.get_local_registry_file()),
            self.BACKUP_FILE_NAME,
        )

    def parse_json_object(self, contents, label):
        """Decode strict JSON while rejecting duplicate object fields."""
        def reject_duplicate_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(
                        label + " contains duplicate JSON key " + str(key) + "."
                    )
                result[key] = value
            return result

        try:
            document = json.loads(
                contents.decode("utf-8"),
                object_pairs_hook=reject_duplicate_keys,
            )
        except json.JSONDecodeError as error:
            raise ValueError(
                "{} at line {} column {}.".format(
                    error.msg,
                    error.lineno,
                    error.colno,
                )
            ) from error
        except UnicodeDecodeError as error:
            raise ValueError(str(error)) from error
        if not isinstance(document, dict):
            raise ValueError(label + " must contain a JSON object.")
        return document

    def require_exact_fields(self, document, allowed, label):
        if not isinstance(document, dict):
            raise ValueError(label + " must be an object.")
        missing = sorted(set(allowed) - set(document))
        unknown = sorted(set(document) - set(allowed))
        if missing:
            raise ValueError(label + " is missing " + missing[0] + ".")
        if unknown:
            raise ValueError(
                label + " contains unknown field " + unknown[0] + "."
            )

    def canonical_identity(self, value, label, allow_empty=False):
        if not isinstance(value, str):
            raise ValueError(label + " must be text.")
        if value != value.strip() or unicodedata.normalize("NFC", value) != value:
            raise ValueError(label + " must be canonical text.")
        if not value:
            if allow_empty:
                return ""
            raise ValueError(label + " must not be empty.")
        if self.has_control_characters(value):
            raise ValueError(label + " contains control characters.")
        return value

    def validate_package_id(self, value):
        value = self.canonical_identity(value, "Local package_id")
        if len(value) > self.FIELD_LIMITS["key"]:
            raise ValueError("Local package_id is too long.")
        value = self.plugin.get_host().validate_plugin_key(value)
        if value.casefold() == "idle":
            raise ValueError("Local package_id cannot be Idle.")
        return value

    def validate_domoticz_key(self, value, allow_empty=False):
        value = self.canonical_identity(
            value,
            "Local domoticz_key",
            allow_empty=allow_empty,
        )
        if value and any(character in value for character in "<>"):
            raise ValueError("Local domoticz_key contains unsafe characters.")
        return value

    def folded_package_id(self, package_id):
        return unicodedata.normalize("NFC", package_id).casefold()

    def normalize_persisted_platforms(self, platforms):
        if not isinstance(platforms, list):
            raise ValueError("Local registry platforms must be a list.")
        normalized = []
        for platform_name in platforms:
            if not isinstance(platform_name, str):
                raise ValueError("Local registry platform must be text.")
            platform_name = platform_name.strip().lower()
            if platform_name not in ("linux", "windows"):
                raise ValueError("Local registry platform is unsupported.")
            if platform_name not in normalized:
                normalized.append(platform_name)
        return [
            platform_name
            for platform_name in ("linux", "windows")
            if platform_name in normalized
        ]

    def validate_persisted_record(self, record):
        self.require_exact_fields(record, self.PACKAGE_FIELDS, "Local package")
        package_id = self.validate_package_id(record["package_id"])
        domoticz_key = self.validate_domoticz_key(
            record["domoticz_key"],
            allow_empty=True,
        )

        description = record["description"]
        if not isinstance(description, str):
            raise ValueError("Local package description must be text.")
        if self.has_control_characters(description):
            raise ValueError("Local package description contains control characters.")
        if len(description) > self.FIELD_LIMITS["description"]:
            raise ValueError("Local package description is too long.")

        repository = record["repository"]
        self.require_exact_fields(
            repository,
            self.REPOSITORY_FIELDS,
            "Local package repository",
        )
        repository_url = repository["url"]
        branch = repository["branch"]
        if (
            not isinstance(repository_url, str)
            or repository_url != repository_url.strip()
            or not self.repository_source_is_valid(repository_url)
        ):
            raise ValueError("Local package repository URL is invalid.")
        if len(repository_url) > self.FIELD_LIMITS["repository_source"]:
            raise ValueError("Local package repository URL is too long.")
        if (
            not isinstance(branch, str)
            or not branch
            or branch != branch.strip()
            or self.has_control_characters(branch)
        ):
            raise ValueError("Local package repository branch is invalid.")
        if len(branch) > self.FIELD_LIMITS["branch"]:
            raise ValueError("Local package repository branch is too long.")

        return {
            "package_id": package_id,
            "domoticz_key": domoticz_key,
            "description": description,
            "repository": {
                "url": repository_url,
                "branch": branch,
            },
            "platforms": self.normalize_persisted_platforms(
                record["platforms"]
            ),
        }

    def parse_v2_document(self, document):
        self.require_exact_fields(
            document,
            self.DOCUMENT_FIELDS,
            "Local registry",
        )
        if document["schema_version"] != self.SCHEMA_VERSION:
            raise ValueError("Local registry schema is unsupported.")
        packages = document["packages"]
        if not isinstance(packages, list):
            raise ValueError("Local registry packages must be an array.")

        entries = {}
        folded_ids = set()
        for raw_record in packages:
            record = self.validate_persisted_record(raw_record)
            folded_id = self.folded_package_id(record["package_id"])
            if folded_id in folded_ids:
                raise ValueError(
                    "Local registry contains colliding package IDs."
                )
            folded_ids.add(folded_id)
            entries[record["package_id"]] = record
        return entries

    def serialize_entries(self, entries):
        records = []
        folded_ids = set()
        for package_id, raw_record in entries.items():
            if not isinstance(raw_record, dict):
                raise ValueError("Local registry entry must be an object.")
            candidate = copy.deepcopy(raw_record)
            if candidate.get("package_id") != package_id:
                raise ValueError("Local registry package_id does not match its entry.")
            record = self.validate_persisted_record(candidate)
            folded_id = self.folded_package_id(package_id)
            if folded_id in folded_ids:
                raise ValueError(
                    "Local registry contains colliding package IDs."
                )
            folded_ids.add(folded_id)
            records.append(record)
        records.sort(
            key=lambda record: (
                record["package_id"].casefold(),
                record["package_id"],
            )
        )
        return (
            json.dumps(
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "packages": records,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")

    def atomic_write_bytes(self, path, contents):
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix="." + os.path.basename(path) + ".",
            suffix=".tmp",
            dir=directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(contents)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
            temporary_path = ""
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def preserve_legacy_backup(self, contents):
        path = self.backup_path()
        if os.path.lexists(path):
            if os.path.islink(path) or not os.path.isfile(path):
                raise ValueError("Local registry backup path is unsafe.")
            with open(path, "rb") as backup_file:
                if backup_file.read() != contents:
                    raise ValueError(
                        "Local registry backup already contains different bytes."
                    )
            return path
        self.atomic_write_bytes(path, contents)
        return path

    def derive_installed_domoticz_key(self, package_id):
        """Read identity only from the exact, real package folder."""
        try:
            package_id = self.plugin.get_host().validate_plugin_key(package_id)
            plugin_dir = os.path.join(
                self.plugin.get_host().plugins_dir(), package_id
            )
            if os.path.islink(plugin_dir) or not os.path.isdir(plugin_dir):
                return ""
            plugin_file = os.path.join(plugin_dir, "plugin.py")
            if os.path.islink(plugin_file) or not os.path.isfile(plugin_file):
                return ""
            metadata = self.plugin.parse_domoticz_plugin_metadata(plugin_dir)
            domoticz_key = (metadata or {}).get("key") or ""
            if not domoticz_key:
                return ""
            return self.validate_domoticz_key(domoticz_key)
        except (OSError, ValueError):
            return ""

    def normalize_legacy_platforms(self, platforms):
        normalized = self.plugin.normalize_platforms(platforms)
        return [] if normalized == ["unknown"] else normalized

    def legacy_entry_to_record(self, package_id, entry):
        package_id = self.validate_package_id(package_id)
        platforms = []
        if isinstance(entry, list):
            if len(entry) < 4:
                raise ValueError("Legacy local registry entry is incomplete.")
            owner, repository, description, branch = entry[:4]
            if len(entry) > 5:
                platforms = entry[5]
        elif isinstance(entry, dict):
            if "package_id" in entry or isinstance(entry.get("repository"), dict):
                raise ValueError("Local registry mixes legacy and v2 entries.")
            owner = entry.get("owner", entry.get("author", ""))
            repository = entry.get("repository", entry.get("repo", ""))
            description = entry.get("description", "")
            branch = entry.get("branch", "master")
            platforms = entry.get("platforms", entry.get("platform", []))
        else:
            raise ValueError("Legacy local registry entry is invalid.")

        if not isinstance(description, str) or not isinstance(branch, str):
            raise ValueError("Legacy local registry text fields are invalid.")
        source = self.plugin.build_git_clone_url(owner, repository)
        return self.validate_persisted_record({
            "package_id": package_id,
            "domoticz_key": self.derive_installed_domoticz_key(package_id),
            "description": description,
            "repository": {
                "url": source,
                "branch": branch or "master",
            },
            "platforms": self.normalize_legacy_platforms(platforms),
        })

    def migrate_legacy_document(self, document):
        entries = {}
        folded_ids = set()
        for package_id, legacy_entry in document.items():
            if not isinstance(package_id, str):
                raise ValueError("Legacy local package ID must be text.")
            folded_id = self.folded_package_id(package_id)
            if folded_id in folded_ids:
                raise ValueError(
                    "Legacy local registry contains colliding package IDs."
                )
            folded_ids.add(folded_id)
            record = self.legacy_entry_to_record(package_id, legacy_entry)
            entries[record["package_id"]] = record
        return entries

    def read_document(self):
        """Read v2, or durably migrate one valid legacy document to v2."""
        path = self.plugin.get_local_registry_file()
        backup_path = self.backup_path()
        try:
            with open(path, "rb") as registry_file:
                contents = registry_file.read()
        except FileNotFoundError:
            return LocalRegistryDocument(
                entries={},
                revision=self.revision_for_bytes(self.MISSING_REVISION_BYTES),
                path=path,
                exists=False,
                writable=True,
                backup_path=backup_path,
            )
        except Exception as error:
            return LocalRegistryDocument(
                entries={},
                revision="",
                path=path,
                exists=True,
                writable=False,
                error_code="registry_read_failed",
                message=str(error),
                backup_path=backup_path,
            )

        revision = self.revision_for_bytes(contents)
        try:
            parsed = self.parse_json_object(contents, "Local registry")
            migrated = False
            if type(parsed.get("schema_version")) is int:
                entries = self.parse_v2_document(parsed)
            else:
                entries = self.migrate_legacy_document(parsed)
                migrated_contents = self.serialize_entries(entries)
                self.preserve_legacy_backup(contents)
                self.atomic_write_bytes(path, migrated_contents)
                contents = migrated_contents
                revision = self.revision_for_bytes(contents)
                migrated = True
        except (OSError, ValueError) as error:
            return LocalRegistryDocument(
                entries={},
                revision=revision,
                path=path,
                exists=True,
                writable=False,
                error_code="invalid_local_registry",
                message=str(error),
                backup_path=backup_path,
            )

        return LocalRegistryDocument(
            entries=entries,
            revision=revision,
            path=path,
            exists=True,
            writable=True,
            backup_path=backup_path,
            migrated=migrated,
        )

    def require_current_document(self, expected_revision):
        """Return the current writable document or raise a structured error."""
        document = self.read_document()
        if not document.writable:
            raise LocalRegistryError(
                document.error_code,
                document.message,
                read_only=True,
            )
        if str(expected_revision or "") != document.revision:
            raise LocalRegistryError(
                "registry_conflict",
                "registry_local.json changed after it was loaded.",
                reload_required=True,
            )
        return document

    def has_control_characters(self, value):
        return self.CONTROL_CHARACTERS.search(value) is not None

    def repository_source_is_valid(self, source):
        if re.match(r"^[^/@\s:]+@[^/\s:]+:.+$", source):
            return True

        parsed = urllib.parse.urlparse(source)
        if parsed.scheme in ("http", "https"):
            if parsed.username is not None or parsed.password is not None:
                return False
            return bool(parsed.hostname and parsed.path.strip("/"))
        if parsed.scheme == "ssh":
            return bool(parsed.hostname and parsed.path.strip("/"))
        if parsed.scheme == "file":
            return bool(parsed.path.strip("/"))

        host, path_parts = self.plugin.split_supported_repo_reference(source)
        return bool(host and len(path_parts) >= 2)

    def validate_entry(self, entry):
        """Validate and return the canonical persisted entry fields."""
        if not isinstance(entry, dict):
            raise LocalRegistryError(
                "invalid_local_registry_entry",
                "Local registry entry must be an object.",
            )

        values = {
            "key": str(entry.get("key") or "").strip(),
            "repository_source": str(
                entry.get("repository_source") or ""
            ).strip(),
            "description": str(entry.get("description") or "").strip(),
            "branch": str(entry.get("branch") or "master").strip() or "master",
        }
        field_errors = {}

        try:
            self.validate_package_id(values["key"])
        except ValueError:
            field_errors["key"] = "Enter a valid plugin key."

        for field, limit in self.FIELD_LIMITS.items():
            value = values[field]
            if len(value) > limit:
                field_errors[field] = "Use at most {} characters.".format(limit)
            elif self.has_control_characters(value):
                field_errors[field] = "Control characters are not allowed."

        if not values["repository_source"]:
            field_errors["repository_source"] = "Repository source is required."
        elif not self.repository_source_is_valid(values["repository_source"]):
            field_errors["repository_source"] = "Enter a valid repository source."

        if field_errors:
            raise LocalRegistryError(
                "invalid_local_registry_entry",
                "Check the highlighted local registry fields.",
                field_errors=field_errors,
            )

        return values

    def write_entries(self, entries):
        try:
            contents = self.serialize_entries(entries)
            self.atomic_write_bytes(
                self.plugin.get_local_registry_file(),
                contents,
            )
        except Exception as error:
            raise LocalRegistryError(
                "registry_write_failed",
                "Could not write registry_local.json: " + str(error),
            ) from error
        return self.read_document()

    def upsert(self, expected_revision, original_key, entry):
        values = self.validate_entry(entry)
        original_key = str(original_key or "").strip()
        if original_key and values["key"] != original_key:
            raise LocalRegistryError(
                "invalid_local_registry_entry",
                "Plugin key cannot be renamed.",
                field_errors={"key": "Plugin key cannot be renamed."},
            )

        document = self.require_current_document(expected_revision)
        if original_key:
            if original_key not in document.entries:
                raise LocalRegistryError(
                    "local_registry_entry_not_found",
                    "Local registry entry was not found.",
                    reload_required=True,
                )
        else:
            requested_id = self.folded_package_id(values["key"])
            if any(
                self.folded_package_id(package_id) == requested_id
                for package_id in document.entries
            ):
                raise LocalRegistryError(
                    "local_registry_entry_exists",
                    "A local registry entry with this key already exists.",
                    field_errors={
                        "key": "This plugin key already exists locally."
                    },
                )

        next_entries = dict(document.entries)
        previous = next_entries.get(original_key, {}) if original_key else {}
        next_entries[values["key"]] = {
            "package_id": values["key"],
            "domoticz_key": (
                previous.get("domoticz_key", "")
                or self.derive_installed_domoticz_key(values["key"])
            ),
            "description": values["description"],
            "repository": {
                "url": values["repository_source"],
                "branch": values["branch"],
            },
            "platforms": list(previous.get("platforms", [])),
        }
        return self.write_entries(next_entries)

    def delete(self, expected_revision, plugin_key):
        try:
            plugin_key = self.plugin.get_host().validate_plugin_key(plugin_key)
        except ValueError as error:
            raise LocalRegistryError(
                "invalid_local_registry_entry",
                "Enter a valid plugin key.",
                field_errors={"key": "Enter a valid plugin key."},
            ) from error

        document = self.require_current_document(expected_revision)
        if plugin_key not in document.entries:
            raise LocalRegistryError(
                "local_registry_entry_not_found",
                "Local registry entry was not found.",
                reload_required=True,
            )

        next_entries = dict(document.entries)
        del next_entries[plugin_key]
        return self.write_entries(next_entries)

    def entry_for_api(self, key, data, public_keys, installed_keys):
        errors = {}
        if (
            isinstance(data, dict)
            and data.get("package_id") == key
            and isinstance(data.get("repository"), dict)
        ):
            source = data["repository"].get("url", "")
            description = data.get("description", "")
            branch = data["repository"].get("branch", "master")
        else:
            source = ""
            description = ""
            branch = "master"
            errors["entry"] = "Entry must use the local registry v2 format."

        entry = {
            "key": str(key),
            "repository_source": source,
            "description": str(description or ""),
            "branch": str(branch or "master"),
            "overrides_public": key in public_keys,
            "installed": key in installed_keys,
            "valid": not errors,
            "errors": errors,
        }
        if not errors:
            try:
                self.validate_entry(entry)
            except LocalRegistryError as error:
                entry["valid"] = False
                entry["errors"] = error.field_errors or {
                    "entry": error.message,
                }
        return entry

    def entries_for_api(self, document):
        public_keys = set(self.plugin.public_registry_data or {})
        installed_keys = set(
            self.plugin.getInstalledPlugins(self.plugin.get_host().plugins_dir())
        )
        return [
            self.entry_for_api(key, data, public_keys, installed_keys)
            for key, data in sorted(
                document.entries.items(),
                key=lambda item: str(item[0]).casefold(),
            )
        ]


class UpdateStatusService:
    def __init__(self, plugin):
        self.plugin = plugin

    def refresh_single_plugin_update_status(self, plugin_key, plugin_dir, fetch_first=True):
        if plugin_key not in self.plugin.plugin_data:
            self.plugin.update_status[plugin_key] = "unknown"
            return "unknown"

        update_status = self.plugin.getGitUpdateStatus(plugin_dir, plugin_key, fetch_first=fetch_first)
        self.plugin.update_status[plugin_key] = update_status
        return update_status

    def refresh_installed_update_statuses(self, installed_plugins=None, plugins_dir=None):
        if plugins_dir is None:
            plugins_dir = self.plugin.get_host().plugins_dir()
        if installed_plugins is None:
            installed_plugins = self.plugin.getInstalledPlugins(plugins_dir)

        update_status = {}
        for plugin_key in installed_plugins:
            if plugin_key not in self.plugin.plugin_data:
                update_status[plugin_key] = "unknown"
                continue

            try:
                plugin_dir = self.plugin.resolve_installed_plugin_dir(plugin_key, plugins_dir)
            except ValueError as e:
                Domoticz.Error(str(e))
                update_status[plugin_key] = "unknown"
                continue
            update_status[plugin_key] = self.plugin.refresh_single_plugin_update_status(plugin_key, plugin_dir)

        self.plugin.update_status.update(update_status)
        return update_status

    def get_git_update_status(self, plugin_dir, plugin_key=None, fetch_first=True):
        if not is_git_checkout(plugin_dir):
            return "unknown"

        try:
            if plugin_key and self.plugin.installed_registry_mismatch(plugin_key, plugin_dir):
                return "mismatch"

            if fetch_first and not self.plugin.fetch_git_repo(plugin_dir):
                return "unknown"

            remote_ref = self.plugin.get_configured_git_remote_ref(plugin_key, plugin_dir) or self.plugin.get_git_remote_ref(plugin_dir) or "@{u}"
            if plugin_key:
                update_times = dict(self.plugin.update_times) if self.plugin.update_times else self.plugin.load_cached_update_times()
                if self.plugin.refresh_git_update_time(plugin_key, plugin_dir, update_times, remote_ref):
                    self.plugin.save_update_times_cache(update_times)
                    self.plugin.apply_update_times(update_times, include_local=True)

            ahead_behind = self.plugin.get_git_ahead_behind(plugin_dir, remote_ref)
            if ahead_behind is None:
                return "unknown"

            behind = ahead_behind[1]
            if behind > 0:
                return "available"
            return "current"
        except Exception as e:
            Domoticz.Debug(f"Could not determine update status for {plugin_dir}: {e}")
            return "unknown"


class GitInstallUpdateStrategy:
    def __init__(self, plugin):
        self.plugin = plugin

    def install(self, entry):
        Domoticz.Debug("InstallPythonPlugin called")

        host = self.plugin.get_host()
        plugins_dir = host.plugins_dir()
        try:
            plugin_dir = host.resolve_plugin_dir(entry.key)
            plugin_key = host.validate_plugin_key(entry.key)
        except ValueError as e:
            Domoticz.Error(str(e))
            return False, str(e)

        try:
            existing_plugin_dir = self.plugin.resolve_installed_plugin_dir(
                plugin_key,
                plugins_dir,
                refresh=True,
            )
        except ValueError as e:
            Domoticz.Error(str(e))
            return False, str(e)

        if os.path.isdir(existing_plugin_dir):
            existing_folder = os.path.basename(existing_plugin_dir)
            Domoticz.Log("Plugin " + plugin_key + " is already installed in folder " + existing_folder + ".")
            self.plugin.refresh_single_plugin_update_time(plugin_key, existing_plugin_dir, fetch_first=False)
            self.plugin.refresh_single_plugin_update_status(plugin_key, existing_plugin_dir, fetch_first=False)
            self.plugin.installDependencies(plugin_key)
            return True, "Plugin already installed."

        Domoticz.Log("Installing Plugin:" + entry.description)
        clone_command = [
            "git",
            "clone",
            "-b",
            entry.branch or "master",
            self.plugin.build_git_clone_url(entry.author, entry.repository),
            plugin_key,
        ]
        Domoticz.Log("Calling: " + " ".join(clone_command))

        try:
            with self.plugin.release_transaction_manager.operation_lock():
                self.plugin.assert_release_install_absent_locked(plugin_key)
                result, clone_message = host.require_git_success(
                    plugins_dir,
                    clone_command,
                    timeout=120,
                    fallback="Git clone failed",
                    log_label="Clone",
                )
                if clone_message:
                    Domoticz.Error(
                        "Git clone failed for plugin "
                        + plugin_key
                        + "."
                    )
                    return False, clone_message
                if not os.path.isdir(plugin_dir):
                    Domoticz.Error(
                        "Plugin folder was not created: " + plugin_dir
                    )
                    return False, "Plugin folder was not created."
        except (OSError, RuntimeError, ValueError) as error:
            Domoticz.Error(str(error))
            return False, str(error)
        Domoticz.Log("Plugin " + plugin_key + " installed successfully.")

        self.plugin.refresh_single_plugin_update_time(plugin_key, plugin_dir, fetch_first=False)
        self.plugin.refresh_single_plugin_update_status(plugin_key, plugin_dir, fetch_first=False)
        self.plugin.installDependencies(plugin_key)
        Domoticz.Log("---Restarting Domoticz MAY BE REQUIRED to activate new plugins---")
        return True, ""

    def update(self, entry, queue_on_lock=True):
        Domoticz.Debug("UpdatePythonPlugin called")

        host = self.plugin.get_host()
        try:
            plugin_key = host.validate_plugin_key(entry.key)
            plugin_dir = self.plugin.resolve_installed_plugin_dir(
                plugin_key,
                refresh=True,
            )
        except ValueError as e:
            Domoticz.Error(str(e))
            return False, str(e)

        is_self_update = plugin_key == self.plugin.get_current_plugin_folder()

        if entry.description in self.plugin.exception_list:
            Domoticz.Log("Plugin:" + entry.description + " excluded by Exclusion file (exclusion.txt). Skipping!!!")
            return True, "Excluded by exception list"

        if self.plugin.installed_registry_mismatch(plugin_key, plugin_dir):
            self.plugin.update_status[plugin_key] = "mismatch"
            return False, "Installed Git checkout does not match the configured registry entry. Add a matching registry_local.json override before updating."

        if is_self_update:
            Domoticz.Log("Self update requested for PyPluginStore.")
            update_success, update_message = host.schedule_self_update(plugin_dir)
            if update_success:
                self.plugin.update_status[plugin_key] = "unknown"
            return update_success, update_message

        Domoticz.Log("Resetting and Updating Plugin:" + plugin_key)

        branch = entry.branch or "master"
        fetch_command = ["git", "fetch", "origin"]
        Domoticz.Debug("Calling: " + " ".join(fetch_command) + " on folder " + plugin_dir)
        fetch_result, fetch_message = host.require_git_success(
            plugin_dir,
            fetch_command,
            timeout=60,
            fallback="Git fetch failed",
            log_label="Fetch",
        )
        if fetch_message:
            return False, fetch_message

        diff_command = ["git", "diff", "--quiet", "HEAD...origin/" + branch]
        Domoticz.Debug("Calling: " + " ".join(diff_command) + " on folder " + plugin_dir)
        diff_result = host.run_git(diff_command, plugin_dir, timeout=15)
        host.log_git_result("Diff", diff_result)
        if diff_result is None:
            return False, "Git diff failed"
        if diff_result.returncode not in (0, 1):
            return False, host.git_failure_message(diff_result, "Git diff failed", plugin_dir)
        is_already_current = (diff_result is not None and diff_result.returncode == 0)

        checkout_command = ["git", "checkout", "-B", branch, "origin/" + branch]
        Domoticz.Debug("Calling: " + " ".join(checkout_command) + " on folder " + plugin_dir)
        checkout_result, checkout_message = host.require_git_success(
            plugin_dir,
            checkout_command,
            timeout=30,
            fallback="Git checkout failed",
            log_label="Checkout",
        )
        if checkout_message:
            if checkout_result is not None and host.is_locked_file_message(host.git_result_output(checkout_result)):
                message = "Plugin files are in use; update queued for the next startup."
                if queue_on_lock:
                    self.plugin.queuePendingOperation("update", plugin_key)
                Domoticz.Error(message)
                return False, message
            return False, checkout_message

        reset_command = ["git", "reset", "--hard", "origin/" + branch]
        Domoticz.Debug("Calling: " + " ".join(reset_command) + " on folder " + plugin_dir)
        reset_result, reset_message = host.require_git_success(
            plugin_dir,
            reset_command,
            timeout=30,
            fallback="Git reset failed",
            log_label="Reset",
        )
        if reset_message:
            if reset_result is not None and host.is_locked_file_message(host.git_result_output(reset_result)):
                message = "Plugin files are in use; update queued for the next startup."
                if queue_on_lock:
                    self.plugin.queuePendingOperation("update", plugin_key)
                Domoticz.Error(message)
                return False, message
            return False, reset_message

        if is_already_current:
            Domoticz.Log("Plugin " + plugin_key + " already Up-To-Date")
        else:
            Domoticz.Log("Succesfully pulled gitHub update for plugin " + plugin_key)
            Domoticz.Log("---Restarting Domoticz MAY BE REQUIRED to activate new plugins---")

        self.plugin.refresh_single_plugin_update_time(plugin_key, plugin_dir, fetch_first=False)
        self.plugin.refresh_single_plugin_update_status(plugin_key, plugin_dir, fetch_first=False)
        self.plugin.installDependencies(plugin_key)
        return True, ""

    def check_for_update(self, entry):
        Domoticz.Debug("CheckForUpdatePythonPlugin called")

        if entry.description in self.plugin.exception_list:
            self.plugin.update_status[entry.key] = "unknown"
            Domoticz.Log("Plugin:" + entry.description + " excluded by Exclusion file (exclusion.txt). Skipping!!!")
            return None

        Domoticz.Debug("Checking Plugin:" + entry.key + " for updates")

        try:
            plugin_dir = self.plugin.resolve_installed_plugin_dir(entry.key)
        except ValueError as e:
            self.plugin.update_status[entry.key] = "unknown"
            Domoticz.Error(str(e))
            return None

        if not is_git_checkout(plugin_dir):
            self.plugin.update_status[entry.key] = "unknown"
            Domoticz.Log("Plugin:" + entry.key + " is not installed from gitHub. Ignoring!!.")
            return None

        update_status = self.plugin.getGitUpdateStatus(plugin_dir, entry.key)
        self.plugin.update_status[entry.key] = update_status
        if update_status == "available":
            Domoticz.Log("Found that we are behind on plugin " + entry.key)
            self.plugin.fnSelectedNotify(entry.key)
        elif update_status == "current":
            Domoticz.Log("Plugin " + entry.key + " already Up-To-Date")
        else:
            Domoticz.Debug("Could not determine update status for " + entry.key + ".")

        return None


@dataclass(frozen=True)
class GitMigrationPreflightResult:
    """Auditable outcome of a working-tree-preserving Git migration check."""

    allowed: bool = False
    status: str = "unknown"
    reason: str = ""
    message: str = ""
    relationship: str = "unknown"
    installed_commit: str = ""
    release_commit: str = ""
    installed_repository_identity: str = ""
    expected_repository_identity: str = ""
    trigger: str = "manual"
    requires_confirmation: bool = False
    requires_approval: bool = False
    tracked_changes: list = field(default_factory=list)
    untracked_files: list = field(default_factory=list)
    preserved_paths: list = field(default_factory=list)
    approval_required_paths: list = field(default_factory=list)
    inventory_sha256: str = ""
    submodules: list = field(default_factory=list)
    unresolved_operations: list = field(default_factory=list)
    shallow: bool = False
    preservation_inventory: object = None

    @property
    def manual_action_state(self):
        """Describe whether the reviewed manual switch can be offered."""
        if not self.inventory_sha256:
            return "blocked"
        if self.requires_confirmation or self.requires_approval:
            return "confirmation_required"
        return "available" if self.allowed else "blocked"


@dataclass(frozen=True)
class ReleaseSwitchPlan:
    """One shared read-only plan for rendering and executing a channel switch."""

    action_state: str
    message: str = ""
    decision: object = None
    preflight: object = None
    release_strategy: object = None


class GitMigrationPreflight:
    """Inspect a checkout, fetching only a verified release commit if needed."""

    TRIGGERS = {"automatic", "manual"}
    OPERATION_MARKERS = (
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry_pick"),
        ("REVERT_HEAD", "revert"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
    )

    def __init__(self, plugin, *, preservation_service=None):
        if plugin is None:
            raise ValueError("plugin is required.")
        self.plugin = plugin
        self.preservation_service = (
            preservation_service or ReleasePreservationService(plugin)
        )

    def _result(self, **values):
        return GitMigrationPreflightResult(**values)

    def _run(self, plugin_dir, *arguments):
        return self.plugin.get_host().run_git_read_only(
            ["git", *arguments],
            plugin_dir,
            timeout=15,
        )

    def _output(self, result):
        if result is None or result.returncode != 0:
            return ""
        return str(result.stdout or "").strip()

    def _nul_paths(self, result):
        if result is None or result.returncode != 0:
            return None
        return sorted(
            set(path for path in str(result.stdout or "").split("\0") if path)
        )

    def _authoritative_remote(self, plugin_dir):
        remote_names = []
        branch_result = self._run(
            plugin_dir,
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
        )
        branch = self._output(branch_result)
        if branch and re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
            configured = self._run(
                plugin_dir,
                "config",
                "--get",
                "branch." + branch + ".remote",
            )
            remote_name = self._output(configured)
            if (
                remote_name
                and remote_name != "."
                and not remote_name.startswith("-")
                and re.fullmatch(r"[A-Za-z0-9._/-]+", remote_name)
            ):
                remote_names.append(remote_name)
        if "origin" not in remote_names:
            remote_names.append("origin")

        for remote_name in remote_names:
            result = self._run(
                plugin_dir,
                "config",
                "--get-all",
                "remote." + remote_name + ".url",
            )
            if result is None or result.returncode != 0:
                continue
            urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if urls:
                return remote_name, urls[0]
        return "", ""

    def _fetch_release_commit(self, plugin_dir, remote_name, release_commit):
        """Fetch one SHA from a previously verified configured remote."""
        if (
            not remote_name
            or remote_name == "."
            or remote_name.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", remote_name)
        ):
            return False
        result = self.plugin.get_host().run_git(
            [
                "git",
                "-c",
                "gc.auto=0",
                "-c",
                "protocol.ext.allow=never",
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-recurse-submodules",
                remote_name,
                release_commit,
            ],
            plugin_dir,
            timeout=60,
        )
        if result is None or result.returncode != 0:
            return False
        available = self._run(
            plugin_dir,
            "cat-file",
            "-e",
            release_commit + "^{commit}",
        )
        return available is not None and available.returncode == 0

    def _index(self, plugin_dir):
        result = self._run(plugin_dir, "ls-files", "--stage", "-z", "--")
        if result is None or result.returncode != 0:
            return None, None
        tracked_paths = []
        submodules = []
        for record in str(result.stdout or "").split("\0"):
            if not record:
                continue
            metadata, separator, path = record.partition("\t")
            parts = metadata.split()
            if not separator or len(parts) != 3 or not path:
                return None, None
            mode, _object_id, stage = parts
            if stage == "0":
                tracked_paths.append(path)
                if mode == "160000":
                    submodules.append(path)
        return sorted(set(tracked_paths)), sorted(set(submodules))

    def _baseline_files(self, plugin_dir, tracked_paths):
        baseline = {}
        for relative_path in tracked_paths:
            try:
                relative_path = _require_artifact_file_path(
                    relative_path,
                    "tracked path",
                )
            except ValueError as error:
                raise ReleasePreservationError(
                    "unsafe_preserved_path",
                    "A tracked Git path is unsafe to preserve when using the "
                    "Release channel.",
                    [relative_path],
                ) from error
            physical_path = os.path.join(
                plugin_dir,
                *relative_path.split("/"),
            )
            try:
                path_stat = os.lstat(physical_path)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ReleasePreservationError(
                    "unsafe_preserved_path",
                    "A tracked Git path could not be inspected safely.",
                    [relative_path],
                ) from error
            if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(
                path_stat.st_mode
            ):
                continue
            node = _ReleasePreservationNode(
                relative_path=relative_path,
                physical_path=physical_path,
                path_stat=path_stat,
            )
            digest, _size, _contents = self.preservation_service._hash_regular_file(
                node
            )
            baseline[relative_path] = digest
        return baseline

    def _inventory(
        self,
        plugin_dir,
        tracked_paths,
        tracked_changes,
        untracked_files,
        mutable_paths,
    ):
        return self.preservation_service.inventory(
            installed_dir=plugin_dir,
            artifact_files=self._baseline_files(plugin_dir, tracked_paths),
            preserved_files={},
            mutable_paths=list(mutable_paths),
            operation="git_migration",
            tracked_changes=tracked_changes,
            untracked_files=untracked_files,
        )

    def evaluate(
        self,
        *,
        plugin_key,
        plugin_dir,
        repository_identity,
        release_commit,
        trigger,
        mutable_paths,
    ):
        """Return a migration decision without changing HEAD, index, or files."""
        if trigger not in self.TRIGGERS:
            raise ValueError("Migration trigger must be manual or automatic.")
        plugin_key = _require_plugin_key(plugin_key)
        del plugin_key
        release_commit = _require_git_commit(
            release_commit,
            "release_commit",
        )
        expected_identity = normalize_repository_identity(repository_identity)
        plugin_dir = os.path.abspath(os.fspath(plugin_dir))
        common = {
            "release_commit": release_commit,
            "expected_repository_identity": expected_identity,
            "trigger": trigger,
        }
        host = self.plugin.get_host()
        if not host.command_available("git"):
            return self._result(
                reason="git_unavailable",
                message=(
                    "Git is unavailable, so the Release channel cannot be "
                    "compared with this checkout."
                ),
                **common,
            )
        if not os.path.isdir(plugin_dir) or os.path.islink(plugin_dir):
            return self._result(
                reason="not_git_repository",
                message="The installed plugin is not a Git repository.",
                **common,
            )

        work_tree = self._run(plugin_dir, "rev-parse", "--is-inside-work-tree")
        if self._output(work_tree).lower() != "true":
            return self._result(
                reason="not_git_repository",
                message="The installed plugin is not a Git repository.",
                **common,
            )
        top_level = self._output(
            self._run(plugin_dir, "rev-parse", "--show-toplevel")
        )
        same_top_level = False
        if top_level:
            try:
                same_top_level = os.path.samefile(
                    top_level,
                    plugin_dir,
                )
            except OSError:
                same_top_level = os.path.normcase(
                    os.path.realpath(top_level)
                ) == os.path.normcase(os.path.realpath(plugin_dir))
        if not same_top_level:
            return self._result(
                reason="not_git_repository",
                message="The plugin folder is not the Git repository root.",
                **common,
            )
        git_dir = self._output(
            self._run(plugin_dir, "rev-parse", "--absolute-git-dir")
        )
        if not git_dir:
            return self._result(
                reason="git_inspection_failed",
                message="Git metadata could not be inspected safely.",
                **common,
            )
        git_dir = os.path.normpath(git_dir)

        index_lock = os.path.join(git_dir, "index.lock")
        if os.path.lexists(index_lock):
            return self._result(
                status="migration_blocked_local_changes",
                reason="git_index_lock",
                message="Git index lock exists at " + index_lock + ".",
                **common,
            )
        unresolved_operations = sorted(
            set(
                operation
                for marker, operation in self.OPERATION_MARKERS
                if os.path.lexists(os.path.join(git_dir, marker))
            )
        )
        if unresolved_operations:
            return self._result(
                status="migration_blocked_local_changes",
                reason="git_operation_in_progress",
                message=(
                    "Finish the current Git operation before using the "
                    "Release channel."
                ),
                unresolved_operations=unresolved_operations,
                **common,
            )

        installed_commit = self._output(
            self._run(plugin_dir, "rev-parse", "--verify", "HEAD^{commit}")
        ).lower()
        if not GIT_COMMIT_PATTERN.fullmatch(installed_commit):
            return self._result(
                reason="git_head_unknown",
                message="The installed Git commit could not be determined.",
                **common,
            )
        shallow = (
            self._output(
                self._run(
                    plugin_dir,
                    "rev-parse",
                    "--is-shallow-repository",
                )
            ).lower()
            == "true"
        )
        remote_name, installed_remote = self._authoritative_remote(plugin_dir)
        installed_identity = normalize_repository_identity(installed_remote)
        identified = {
            "installed_commit": installed_commit,
            "installed_repository_identity": installed_identity,
            "shallow": shallow,
            **common,
        }
        if not installed_identity:
            return self._result(
                reason="repository_identity_unknown",
                message="The installed repository identity is unknown.",
                **identified,
            )
        if not expected_identity or installed_identity != expected_identity:
            return self._result(
                status="mismatch",
                reason="repository_mismatch",
                message="The installed repository does not match the release.",
                **identified,
            )

        tracked_paths, submodules = self._index(plugin_dir)
        tracked_changes = self._nul_paths(
            self._run(
                plugin_dir,
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                "HEAD",
                "--",
            )
        )
        untracked_files = self._nul_paths(
            self._run(
                plugin_dir,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
            )
        )
        if (
            tracked_paths is None
            or tracked_changes is None
            or untracked_files is None
        ):
            return self._result(
                reason="git_inspection_failed",
                message="The Git working tree could not be inspected safely.",
                **identified,
            )
        if submodules:
            return self._result(
                status="migration_blocked_local_changes",
                reason="submodules_not_supported",
                message=(
                    "Using the Release channel does not support Git "
                    "submodules."
                ),
                tracked_changes=tracked_changes,
                untracked_files=untracked_files,
                submodules=submodules,
                **identified,
            )

        if installed_commit == release_commit:
            relationship = "equal"
            relationship_reason = "release_equals_head"
        else:
            available = self._run(
                plugin_dir,
                "cat-file",
                "-e",
                release_commit + "^{commit}",
            )
            if (
                available is None
                or available.returncode != 0
            ) and not self._fetch_release_commit(
                plugin_dir,
                remote_name,
                release_commit,
            ):
                return self._result(
                    status="migration_waiting_for_release",
                    reason="ancestry_unavailable",
                    message=(
                        "Release ancestry could not be fetched from the "
                        "verified repository."
                    ),
                    tracked_changes=tracked_changes,
                    untracked_files=untracked_files,
                    **identified,
                )
            release_descends = self._run(
                plugin_dir,
                "merge-base",
                "--is-ancestor",
                installed_commit,
                release_commit,
            )
            if release_descends is not None and release_descends.returncode == 0:
                relationship = "release_descendant"
                relationship_reason = "release_descends_from_head"
            elif release_descends is None or release_descends.returncode != 1:
                return self._result(
                    status="migration_waiting_for_release",
                    reason="ancestry_unavailable",
                    message="Release ancestry could not be verified.",
                    tracked_changes=tracked_changes,
                    untracked_files=untracked_files,
                    **identified,
                )
            else:
                installed_descends = self._run(
                    plugin_dir,
                    "merge-base",
                    "--is-ancestor",
                    release_commit,
                    installed_commit,
                )
                if (
                    installed_descends is not None
                    and installed_descends.returncode == 0
                ):
                    relationship = "installed_ahead"
                    relationship_reason = "installed_head_ahead"
                elif (
                    installed_descends is not None
                    and installed_descends.returncode == 1
                ):
                    relationship = "diverged"
                    relationship_reason = "diverged_history"
                else:
                    return self._result(
                        status="migration_waiting_for_release",
                        reason="ancestry_unavailable",
                        message="Release ancestry could not be verified.",
                        tracked_changes=tracked_changes,
                        untracked_files=untracked_files,
                        **identified,
                    )

        related = {"relationship": relationship, **identified}
        try:
            inventory = self._inventory(
                plugin_dir,
                tracked_paths,
                tracked_changes,
                untracked_files,
                mutable_paths,
            )
        except ReleasePreservationError as error:
            return self._result(
                status="migration_blocked_local_changes",
                reason=error.reason,
                message=error.message,
                tracked_changes=tracked_changes,
                untracked_files=untracked_files,
                approval_required_paths=error.paths,
                **related,
            )
        inventory_values = {
            "inventory_sha256": inventory.sha256,
            "preservation_inventory": inventory,
            "tracked_changes": tracked_changes,
            "untracked_files": untracked_files,
        }
        if relationship in {"installed_ahead", "diverged"}:
            requires_confirmation = trigger == "manual"
            relationship_message = (
                "The installed Git checkout contains commits newer than the "
                "available Release."
                if relationship == "installed_ahead"
                else (
                    "The installed Git checkout contains commits that differ "
                    "from the available Release."
                )
            )
            return self._result(
                status="migration_waiting_for_release",
                reason=(
                    "downgrade_confirmation_required"
                    if requires_confirmation
                    else relationship_reason
                ),
                message=relationship_message,
                requires_confirmation=requires_confirmation,
                **inventory_values,
                **related,
            )
        if tracked_changes:
            if trigger == "automatic":
                return self._result(
                    status="migration_blocked_local_changes",
                    reason="tracked_changes",
                    message=(
                        "Automatic use of the Release channel cannot preserve "
                        "tracked changes."
                    ),
                    **inventory_values,
                    **related,
                )
            outside_policy = [
                path
                for path in tracked_changes
                if not self.preservation_service._matches_mutable_path(
                    path,
                    inventory.mutable_paths,
                )
            ]
            if outside_policy:
                return self._result(
                    status="migration_blocked_local_changes",
                    reason="tracked_path_not_mutable",
                    message="A tracked change is outside reviewed mutable policy.",
                    approval_required_paths=outside_policy,
                    **inventory_values,
                    **related,
                )
            return self._result(
                status="migration_blocked_local_changes",
                reason="preservation_approval_required",
                message="Tracked mutable data requires exact inventory approval.",
                requires_approval=True,
                approval_required_paths=tracked_changes,
                **inventory_values,
                **related,
            )

        unknown_paths = sorted(
            set(untracked_files) | set(inventory.unknown_paths)
        )
        if unknown_paths:
            if trigger == "automatic":
                return self._result(
                    status="migration_blocked_local_files",
                    reason="untracked_files",
                    message=(
                        "Automatic use of the Release channel cannot preserve "
                        "unknown local files."
                    ),
                    **inventory_values,
                    **related,
                )
            return self._result(
                status="migration_blocked_local_files",
                reason="preservation_approval_required",
                message="Unknown local data requires exact inventory approval.",
                requires_approval=True,
                approval_required_paths=unknown_paths,
                **inventory_values,
                **related,
            )

        return self._result(
            allowed=True,
            status="migration_available",
            reason=relationship_reason,
            message=(
                "This Git checkout can use the Release channel instead of Git "
                "commits."
            ),
            **inventory_values,
            **related,
        )


class ReleaseInstallUpdateStrategy:
    """Install one index-pinned release through the hardened transaction path."""

    RUNTIME_ARTIFACT_HEADERS = {
        "User-Agent": "PyPluginStore-Release-Runtime",
    }

    def __init__(
        self,
        plugin,
        *,
        transaction_manager=None,
        dependency_service=None,
        http_client=None,
        extractor=None,
        validator=None,
        metadata_service=None,
        preservation_service=None,
        migration_preflight=None,
        limits=None,
    ):
        if plugin is None:
            raise ValueError("plugin is required.")
        self.plugin = plugin
        self.limits = limits or ReleaseArchiveLimits()
        self.transaction_manager = (
            transaction_manager or ReleaseTransactionManager(plugin)
        )
        self.dependency_service = dependency_service or (
            ReleaseDependencySnapshotService(
                plugin,
                transaction_manager=self.transaction_manager,
            )
        )
        self.http_client = http_client or SafeReleaseHttpClient(
            max_bytes=self.limits.max_archive_size
        )
        self.extractor = extractor or SafeZipExtractor(self.limits)
        self.validator = validator or ReleaseArtifactValidationService(
            plugin, self.limits
        )
        self.metadata_service = metadata_service or InstallMetadataService(
            plugin
        )
        self.preservation_service = preservation_service
        self.migration_preflight = migration_preflight
        self.last_operation_id = ""

    def _preservation(self):
        if self.preservation_service is None:
            service_type = globals().get("ReleasePreservationService")
            if service_type is None:
                raise RuntimeError("Release preservation is not configured.")
            self.preservation_service = service_type(self.plugin)
        return self.preservation_service

    def _preflight(self):
        if self.migration_preflight is None:
            self.migration_preflight = GitMigrationPreflight(
                self.plugin,
                preservation_service=self._preservation(),
            )
        return self.migration_preflight

    def _operation_id(self):
        return "release-" + hashlib.sha256(os.urandom(32)).hexdigest()[:24]

    def _index_sequence(self):
        selection = getattr(
            self.plugin, "release_metadata_selection", None
        )
        sequence = getattr(selection, "sequence", 0)
        if type(sequence) is not int or sequence <= 0:
            raise ValueError(
                "A trusted release index generation is required."
            )
        return sequence

    def _target(self, release):
        target = {
            "management_mode": "release",
            "release_id": release.release_id,
            "release_revision": release.revision,
            "commit": release.commit,
            "artifact_tree_sha256": release.artifact.tree_sha256,
        }
        if release.source_revision:
            target["source_revision"] = release.source_revision
        return target

    def _installed_metadata(self, entry):
        plugin_dir = self.plugin.resolve_installed_plugin_dir(
            entry.key,
            refresh=True,
        )
        metadata = self.metadata_service.read(plugin_dir)
        if metadata is None:
            raise ValueError(
                "Release install metadata is unavailable or invalid."
            )
        expected_identity = normalize_repository_identity(
            entry.author, entry.repository
        )
        if (
            metadata.plugin_key != entry.key
            or not expected_identity
            or metadata.repository_identity != expected_identity
        ):
            raise ValueError(
                "Installed release metadata does not match the registry entry."
            )
        return plugin_dir, metadata

    def _migration_snapshot(self, preflight):
        inventory = preflight.preservation_inventory
        if not isinstance(inventory, ReleasePreservationInventory):
            raise ValueError(
                "Release channel preservation inventory is unavailable."
            )
        return _validated_migration_snapshot(
            {
                "repository_identity": (
                    preflight.installed_repository_identity
                ),
                "release_commit": preflight.release_commit,
                "relationship": preflight.relationship,
                "inventory_sha256": preflight.inventory_sha256,
                "tracked_changes": list(preflight.tracked_changes),
                "untracked_files": list(preflight.untracked_files),
                "mutable_paths": list(inventory.mutable_paths),
                "shallow": preflight.shallow,
            }
        )

    def _expected_current(
        self,
        entry,
        operation,
        migration_preflight=None,
    ):
        if operation == "release_install":
            discovered_dir = self.plugin.resolve_installed_plugin_dir(
                entry.key,
                refresh=True,
            )
            if os.path.lexists(discovered_dir):
                raise ValueError("Plugin folder already exists.")
            return {"management_mode": "absent"}, None, None
        if operation == "release_migration":
            if not isinstance(
                migration_preflight,
                GitMigrationPreflightResult,
            ):
                raise ValueError(
                    "A validated Release channel preflight is required."
                )
            plugin_dir = self.plugin.resolve_installed_plugin_dir(
                entry.key,
                refresh=True,
            )
            inventory = migration_preflight.preservation_inventory
            if (
                not isinstance(inventory, ReleasePreservationInventory)
                or os.path.normcase(os.path.abspath(inventory.installed_dir))
                != os.path.normcase(os.path.abspath(plugin_dir))
            ):
                raise ValueError(
                    "The installed plugin folder changed after Release "
                    "channel preflight. Refresh and try again."
                )
            return (
                {
                    "management_mode": "git",
                    "commit": migration_preflight.installed_commit,
                    "migration_snapshot": self._migration_snapshot(
                        migration_preflight
                    ),
                },
                plugin_dir,
                None,
            )
        plugin_dir, metadata = self._installed_metadata(entry)
        expected = {
            "management_mode": "release",
            "release_id": metadata.release_id,
            "release_revision": metadata.release_revision,
            "commit": metadata.commit,
            "artifact_tree_sha256": metadata.artifact_tree_sha256,
        }
        if metadata.source_revision:
            expected["source_revision"] = metadata.source_revision
        return expected, plugin_dir, metadata

    def _allowed_origins(self, entry, release):
        """Return reviewed and exact provider-contract redirect origins."""
        release_policy = getattr(entry.delivery, "release", None)
        origins = list(
            getattr(release_policy, "allowed_origins", []) or []
        )
        parsed = urllib.parse.urlsplit(release.artifact.url)
        path_parts = parsed.path.split("/")
        github_source_archive = (
            release.provider == "github"
            and release.artifact.kind == "source_zip"
            and release.artifact.provenance == "forge_source_archive"
            and parsed.scheme == "https"
            and parsed.hostname == "api.github.com"
            and parsed.port in (None, 443)
            and not parsed.query
            and len(path_parts) == 6
            and path_parts[1] == "repos"
            and path_parts[2]
            and path_parts[3]
            and path_parts[4] == "zipball"
            and path_parts[5] == release.commit
        )
        if (
            github_source_archive
            and "https://codeload.github.com" not in origins
        ):
            origins.append("https://codeload.github.com")
        return origins

    def _move_validated_source(self, source_root, staged_code):
        source_root = os.path.abspath(source_root)
        staged_code = os.path.abspath(staged_code)
        if os.path.lexists(staged_code):
            raise ValueError("Staged release destination already exists.")
        os.replace(source_root, staged_code)

    def _installed_at(self, release):
        now = self.plugin.get_host().utc_timestamp()
        try:
            if _parse_utc_timestamp(now, "installed_at") < _parse_utc_timestamp(
                release.released_at, "release.released_at"
            ):
                return release.released_at
        except ValueError:
            pass
        return now

    def _metadata(
        self,
        entry,
        release,
        validation,
        index_sequence,
        preserved_files,
        migration_preflight=None,
    ):
        document = {
            "schema": INSTALL_METADATA_SCHEMA_VERSION,
            "package_id": entry.key,
            "management_mode": "release",
            "repository_identity": release.repository_identity,
            "version": release.version,
            "tag": release.tag,
            "release_id": release.release_id,
            "release_revision": release.revision,
            "released_at": release.released_at,
            "commit": release.commit,
            "source_revision": release.source_revision,
            "artifact_sha256": release.artifact.sha256,
            "artifact_tree_sha256": release.artifact.tree_sha256,
            "artifact_provenance": release.artifact.provenance,
            "artifact_files": validation.artifact_files,
            "preserved_files": preserved_files,
            "index_sequence": index_sequence,
            "installed_at": self._installed_at(release),
        }
        if migration_preflight is not None:
            document["migration_source_commit"] = (
                migration_preflight.installed_commit
            )
            document["migration_inventory_sha256"] = (
                migration_preflight.inventory_sha256
            )
        return InstallMetadata.from_document(document)

    def _apply_update_preservation(
        self,
        entry,
        installed_dir,
        installed_metadata,
        staged_code,
        trigger,
        approved_inventory_sha256=None,
    ):
        if installed_metadata is None:
            return {}
        release_policy = getattr(entry.delivery, "release", None)
        mutable_paths = list(
            getattr(release_policy, "mutable_paths", []) or []
        )
        scanned = self._preservation().inventory(
            installed_dir=installed_dir,
            artifact_files=installed_metadata.artifact_files,
            preserved_files=installed_metadata.preserved_files,
            mutable_paths=mutable_paths,
            operation="release_update",
            tracked_changes=[],
            untracked_files=[],
        )
        result = self._preservation().apply_overlay(
            scanned,
            staged_dir=staged_code,
            trigger=trigger,
            approved_inventory_sha256=approved_inventory_sha256,
        )
        return dict(result.preserved_files)

    def _apply_migration_preservation(
        self,
        migration_preflight,
        staged_code,
        trigger,
        approved_inventory_sha256=None,
    ):
        inventory = migration_preflight.preservation_inventory
        if not isinstance(inventory, ReleasePreservationInventory):
            raise ValueError(
                "Release channel preservation inventory is unavailable."
            )
        result = self._preservation().apply_overlay(
            inventory,
            staged_dir=staged_code,
            trigger=trigger,
            approved_inventory_sha256=approved_inventory_sha256,
        )
        return dict(result.preserved_files)

    def _abort(self, operation_id, error):
        if not operation_id:
            return
        try:
            self.transaction_manager.abort(operation_id, str(error))
        except Exception as abort_error:
            Domoticz.Error(
                "Release transaction cleanup failed: " + str(abort_error)
            )

    def _execute(
        self,
        entry,
        release,
        trigger,
        operation,
        *,
        index_sequence,
        approved_inventory_sha256=None,
        compatibility_confirmed=False,
        migration_preflight=None,
    ):
        self.last_operation_id = ""
        arguments = {
            "index_sequence": index_sequence,
            "approved_inventory_sha256": approved_inventory_sha256,
            "compatibility_confirmed": compatibility_confirmed,
            "migration_preflight": migration_preflight,
        }
        workflow_lock = getattr(
            self.transaction_manager,
            "workflow_lock",
            None,
        )
        if not callable(workflow_lock):
            return self._execute_locked(
                entry,
                release,
                trigger,
                operation,
                **arguments,
            )
        try:
            with workflow_lock(blocking=False):
                return self._execute_locked(
                    entry,
                    release,
                    trigger,
                    operation,
                    **arguments,
                )
        except (OSError, RuntimeError, ValueError) as error:
            Domoticz.Error(
                "Release operation failed for "
                + entry.key
                + ": "
                + str(error)
            )
            return False, str(error)

    def _execute_locked(
        self,
        entry,
        release,
        trigger,
        operation,
        *,
        index_sequence,
        approved_inventory_sha256=None,
        compatibility_confirmed=False,
        migration_preflight=None,
    ):
        if trigger not in ReleaseManagementCoordinator.TRIGGERS:
            raise ValueError(
                "Release management trigger must be manual or automatic."
            )
        if not isinstance(release, ReleaseDescriptor):
            return False, "A validated release target is required."
        if type(index_sequence) is not int or index_sequence <= 0:
            return False, "A trusted release index generation is required."
        expected_identity = normalize_repository_identity(
            entry.author, entry.repository
        )
        if (
            not expected_identity
            or release.repository_identity != expected_identity
        ):
            return False, "Release repository identity does not match the registry."

        operation_id = ""
        try:
            expected_current, installed_dir, installed_metadata = (
                self._expected_current(
                    entry,
                    operation,
                    migration_preflight,
                )
            )
            operation_id = self._operation_id()
            self.last_operation_id = operation_id
            live_folder = (
                entry.key
                if installed_dir is None
                else os.path.basename(os.path.normpath(installed_dir))
            )
            transaction = self.transaction_manager.create_transaction(
                plugin_key=entry.key,
                live_folder=live_folder,
                operation_id=operation_id,
                operation=operation,
                expected_current=expected_current,
                target=self._target(release),
            )
            staging_parent = os.path.dirname(transaction.paths.staged_code)
            archive_path = os.path.join(staging_parent, "artifact.zip")
            extraction_dir = os.path.join(staging_parent, "extracted")
            self.http_client.download_to_path(
                release.artifact.url,
                archive_path,
                headers=dict(self.RUNTIME_ARTIFACT_HEADERS),
                expected_sha256=release.artifact.sha256,
                expected_size=release.artifact.size,
                allowed_origins=self._allowed_origins(entry, release),
            )
            self.extractor.extract(
                archive_path,
                extraction_dir,
                expected_root_prefix=release.artifact.root_prefix,
            )
            validation = self.validator.validate(
                extraction_dir=extraction_dir,
                root_prefix=release.artifact.root_prefix,
                source_path=release.artifact.source_path,
                plugin_key=entry.key,
                expected_tree_sha256=release.artifact.tree_sha256,
                repository_identity=release.repository_identity,
                expected_domoticz_key=(release.domoticz_key or None),
                expected_plugin_py_sha256=(
                    release.plugin_py_sha256 or None
                ),
            )
            self._move_validated_source(
                validation.source_root,
                transaction.paths.staged_code,
            )
            if os.path.lexists(extraction_dir):
                shutil.rmtree(extraction_dir)
            if os.path.lexists(archive_path):
                os.unlink(archive_path)

            preserved_files = {}
            if operation == "release_update":
                preserved_files = self._apply_update_preservation(
                    entry,
                    installed_dir,
                    installed_metadata,
                    transaction.paths.staged_code,
                    trigger,
                    approved_inventory_sha256,
                )
            elif operation == "release_migration":
                preserved_files = self._apply_migration_preservation(
                    migration_preflight,
                    transaction.paths.staged_code,
                    trigger,
                    approved_inventory_sha256,
                )
            metadata = self._metadata(
                entry,
                release,
                validation,
                index_sequence,
                preserved_files,
                migration_preflight,
            )
            self.metadata_service.write(
                transaction.paths.staged_code, metadata
            )
            self.transaction_manager.mark_staged_verified(operation_id)
            dependency_result = self.dependency_service.stage(
                operation_id,
                requirements_file=os.path.join(
                    transaction.paths.staged_code, "requirements.txt"
                ),
                compatibility_confirmed=compatibility_confirmed,
            )
            if dependency_result.requires_confirmation:
                message = (
                    "Shared dependency compatibility requires a separate "
                    "review; no plugin files were changed."
                )
                self._abort(operation_id, message)
                return (
                    False,
                    message,
                )
            activated = self.transaction_manager.activate(operation_id)
            if activated.phase == "queued_locked":
                return (
                    True,
                    "Plugin files are locked; Release activation is queued "
                    "for startup. Restart required.",
                )
            return (
                True,
                "Release "
                + release.version
                + " staged successfully; restart required.",
            )
        except Exception as error:
            self._abort(operation_id, error)
            Domoticz.Error(
                "Release operation failed for " + entry.key + ": " + str(error)
            )
            return False, str(error)

    def execute_decision(self, entry, decision):
        operation = {
            "release_install": "release_install",
            "release_update": "release_update",
            "release_migration": "release_migration",
        }.get(decision.route)
        if operation is None:
            raise ValueError("Decision does not select a release operation.")
        if operation == "release_migration":
            return self.migrate(
                entry,
                decision.release,
                decision.trigger,
                index_sequence=decision.index_sequence,
            )
        return self._execute(
            entry,
            decision.release,
            decision.trigger,
            operation,
            index_sequence=decision.index_sequence,
        )

    def install(self, entry, release, trigger):
        return self._execute(
            entry,
            release,
            trigger,
            "release_install",
            index_sequence=self._index_sequence(),
        )

    def update(self, entry, release, trigger):
        return self._execute(
            entry,
            release,
            trigger,
            "release_update",
            index_sequence=self._index_sequence(),
        )

    def preflight_migration(self, entry, release, trigger):
        """Evaluate one Git checkout under the release migration policy."""
        if trigger not in ReleaseManagementCoordinator.TRIGGERS:
            raise ValueError(
                "Migration trigger must be manual or automatic."
            )
        if not isinstance(release, ReleaseDescriptor):
            raise ValueError("A validated release target is required.")
        migration_mode = release.artifact.migration_mode
        if migration_mode == "blocked":
            raise ValueError(
                "The selected release cannot safely replace this Git checkout."
            )
        if trigger == "automatic" and migration_mode != "automatic":
            raise ValueError(
                "The selected release requires manual confirmation before "
                "using the Release channel."
            )
        if not release.commit:
            raise ValueError(
                "The selected release has no commit to compare with this Git "
                "checkout."
            )
        plugin_dir = self.plugin.resolve_installed_plugin_dir(
            entry.key,
            refresh=True,
        )
        release_policy = getattr(entry.delivery, "release", None)
        mutable_paths = list(
            getattr(release_policy, "mutable_paths", []) or []
        )
        return self._preflight().evaluate(
            plugin_key=entry.key,
            plugin_dir=plugin_dir,
            repository_identity=release.repository_identity,
            release_commit=release.commit,
            trigger=trigger,
            mutable_paths=mutable_paths,
        )

    def migrate(
        self,
        entry,
        release,
        trigger,
        *,
        index_sequence=None,
        approved_inventory_sha256=None,
        downgrade_confirmed=False,
    ):
        if index_sequence is None:
            try:
                index_sequence = self._index_sequence()
            except ValueError as error:
                return False, str(error)

        try:
            preflight = self.preflight_migration(
                entry,
                release,
                trigger,
            )
        except (OSError, ReleasePreservationError, ValueError) as error:
            return False, str(error)

        inventory = preflight.preservation_inventory
        approval_paths = []
        if isinstance(inventory, ReleasePreservationInventory):
            approval_paths = sorted(
                set(inventory.unknown_paths)
                | set(inventory.tracked_changes)
                | set(inventory.approval_required_paths)
            )
        if preflight.requires_confirmation and not downgrade_confirmed:
            return False, preflight.message or (
                "Using the Release channel requires explicit downgrade "
                "confirmation."
            )
        if approval_paths and (
            approved_inventory_sha256 != preflight.inventory_sha256
        ):
            return False, (
                "Using the Release channel requires exact approval of the "
                "current local-data inventory."
            )
        manually_authorized = bool(
            trigger == "manual"
            and (
                preflight.requires_confirmation
                or preflight.requires_approval
            )
        )
        if not preflight.allowed and not manually_authorized:
            return False, preflight.message or preflight.reason or (
                "The Release channel is not available for this Git checkout."
            )
        if not preflight.installed_commit or inventory is None:
            return False, "Release channel preflight is incomplete."

        return self._execute(
            entry,
            release,
            trigger,
            "release_migration",
            index_sequence=index_sequence,
            approved_inventory_sha256=approved_inventory_sha256,
            migration_preflight=preflight,
        )


class ReleaseConfirmationError(RuntimeError):
    """An opaque release-management confirmation is invalid or stale."""


@dataclass(frozen=True)
class ReleaseManagementDecision:
    """One explicit, auditable route through release or Git management."""

    route: str
    status: str
    reason: str = ""
    release: object = None
    trigger: str = "manual"
    requires_confirmation: bool = False
    index_sequence: int = 0


class ReleaseManagementCoordinator:
    """Select release-first operations while retaining the Git strategy."""

    OPERATIONS = {"install", "update", "status"}
    INSTALLED_MODES = {"absent", "git", "release"}
    TRIGGERS = {"manual", "automatic"}

    def __init__(
        self,
        plugin,
        git_strategy=None,
        release_strategy=None,
        *,
        confirmation_clock=None,
        confirmation_ttl_seconds=300,
    ):
        if plugin is None:
            raise ValueError("plugin is required.")
        if (
            type(confirmation_ttl_seconds) is not int
            or confirmation_ttl_seconds <= 0
        ):
            raise ValueError(
                "confirmation_ttl_seconds must be a positive integer."
            )
        self.plugin = plugin
        self.git_strategy = git_strategy or GitInstallUpdateStrategy(plugin)
        self.release_strategy = release_strategy or ReleaseInstallUpdateStrategy(
            plugin
        )
        self.confirmation_clock = confirmation_clock or (
            lambda: datetime.now(timezone.utc)
        )
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self._confirmation_challenges = {}
        self._confirmation_lock = threading.Lock()

    def _confirmation_now(self):
        current = self.confirmation_clock()
        if not isinstance(current, datetime):
            raise ReleaseConfirmationError(
                "Confirmation clock did not return a datetime."
            )
        if current.tzinfo is None or current.utcoffset() is None:
            raise ReleaseConfirmationError(
                "Confirmation clock must return an aware datetime."
            )
        return current.astimezone(timezone.utc)

    def _confirmation_binding(
        self,
        *,
        kind,
        plugin_key,
        action,
        target,
        inventory_sha256,
    ):
        kind = _require_nonempty_string(kind, "confirmation kind")
        plugin_key = _require_plugin_key(plugin_key)
        action = _require_nonempty_string(action, "confirmation action")
        target = _transaction_json_copy(target, "confirmation target")
        if not isinstance(target, dict):
            raise ValueError("confirmation target must be an object.")
        inventory_sha256 = _require_sha256(
            inventory_sha256,
            "inventory_sha256",
        )
        document = {
            "kind": kind,
            "plugin_key": plugin_key,
            "action": action,
            "target": target,
            "inventory_sha256": inventory_sha256,
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest(), inventory_sha256

    def issue_confirmation_challenge(
        self,
        *,
        kind,
        plugin_key,
        action,
        target,
        inventory_sha256,
    ):
        """Issue a bounded one-use token without exposing confirmation inputs."""
        binding, approved_digest = self._confirmation_binding(
            kind=kind,
            plugin_key=plugin_key,
            action=action,
            target=target,
            inventory_sha256=inventory_sha256,
        )
        now = self._confirmation_now()
        expires_at = now + timedelta(seconds=self.confirmation_ttl_seconds)
        with self._confirmation_lock:
            self._confirmation_challenges = {
                token: record
                for token, record in self._confirmation_challenges.items()
                if record[3] >= now
            }
            while True:
                token = base64.urlsafe_b64encode(os.urandom(32)).decode(
                    "ascii"
                ).rstrip("=")
                if token not in self._confirmation_challenges:
                    break
            self._confirmation_challenges[token] = (
                kind,
                binding,
                approved_digest,
                expires_at,
            )
        return {
            "kind": kind,
            "token": token,
            "message": "Confirm the selected release-management operation.",
        }

    def consume_confirmation_challenge(
        self,
        *,
        token,
        plugin_key,
        action,
        target,
        inventory_sha256,
    ):
        """Consume an exact-bound challenge once and return its approval digest."""
        if not isinstance(token, str) or not token:
            raise ReleaseConfirmationError("Confirmation token is invalid.")
        now = self._confirmation_now()
        with self._confirmation_lock:
            record = self._confirmation_challenges.pop(token, None)
        if record is None:
            raise ReleaseConfirmationError(
                "Confirmation token is unknown or already used."
            )
        kind, expected_binding, approved_digest, expires_at = record
        if now > expires_at:
            raise ReleaseConfirmationError("Confirmation token has expired.")
        try:
            actual_binding, actual_digest = self._confirmation_binding(
                kind=kind,
                plugin_key=plugin_key,
                action=action,
                target=target,
                inventory_sha256=inventory_sha256,
            )
        except (TypeError, ValueError) as error:
            raise ReleaseConfirmationError(
                "Confirmation binding is invalid."
            ) from error
        if (
            actual_binding != expected_binding
            or actual_digest != approved_digest
        ):
            raise ReleaseConfirmationError(
                "Confirmation does not match the requested operation."
            )
        return approved_digest

    def _decision(
        self,
        route,
        status,
        *,
        reason="",
        release=None,
        trigger="manual",
        requires_confirmation=False,
    ):
        return ReleaseManagementDecision(
            route=route,
            status=status,
            reason=reason,
            release=release,
            trigger=trigger,
            requires_confirmation=requires_confirmation,
        )

    def _git_decision(
        self, operation, installed_mode, git_status, *, reason="", trigger="manual"
    ):
        if operation == "status":
            status = {
                "current": "git_current",
                "available": "git_available",
            }.get(git_status, "git_unknown")
            return self._decision(
                "git_status", status, reason=reason, trigger=trigger
            )
        if operation == "install" or installed_mode == "absent":
            route = "git_install"
        else:
            route = "git_update"
        return self._decision(
            route, "git_available", reason=reason, trigger=trigger
        )

    def _metadata_unavailable_decision(
        self,
        entry,
        operation,
        installed_mode,
        channel_preference,
        release_was_activated,
        git_status,
        reason,
        trigger,
    ):
        policy = entry.delivery
        release_required = (
            channel_preference == "release"
            or policy.preferred == "release"
            or installed_mode == "release"
            or (
                release_was_activated
                and channel_preference != "keep_git"
            )
        )
        git_selected = (
            channel_preference == "keep_git"
            or policy.preferred == "git"
            or policy.preferred == "release_if_indexed"
        )
        if release_required or not policy.git_supported or not git_selected:
            return self._decision(
                "blocked",
                "release_metadata_unavailable",
                reason=reason,
                trigger=trigger,
            )
        return self._git_decision(
            operation,
            installed_mode,
            git_status,
            reason=reason,
            trigger=trigger,
        )

    def _release_update_decision(
        self,
        release,
        installed_release,
        trigger,
        downgrade_confirmed,
    ):
        if installed_release is None:
            return self._decision(
                "blocked",
                "verification_failed",
                reason="release_install_metadata_missing",
                trigger=trigger,
            )

        installed_revision = getattr(
            installed_release, "release_revision", None
        )
        if type(installed_revision) is not int:
            return self._decision(
                "blocked",
                "verification_failed",
                reason="release_install_metadata_invalid",
                trigger=trigger,
            )

        if release.revision > installed_revision:
            installed_release_id = getattr(
                installed_release, "release_id", ""
            )
            if installed_release_id not in release.supersedes:
                return self._decision(
                    "blocked",
                    "verification_failed",
                    reason="predecessor_gap",
                    trigger=trigger,
                )
            return self._decision(
                "release_update",
                "available",
                release=release,
                trigger=trigger,
            )

        if release.revision < installed_revision:
            if trigger != "manual":
                return self._decision(
                    "blocked",
                    "verification_failed",
                    reason="release_downgrade",
                    trigger=trigger,
                )
            if not downgrade_confirmed:
                return self._decision(
                    "confirmation_required",
                    "confirmation_required",
                    reason="release_downgrade",
                    release=release,
                    trigger=trigger,
                    requires_confirmation=True,
                )
            return self._decision(
                "release_update",
                "available",
                reason="release_downgrade",
                release=release,
                trigger=trigger,
            )

        immutable_fields_match = (
            release.release_id
            == getattr(installed_release, "release_id", "")
            and release.commit
            == getattr(installed_release, "commit", "")
            and release.source_revision
            == getattr(installed_release, "source_revision", "")
            and release.artifact.tree_sha256
            == getattr(installed_release, "artifact_tree_sha256", "")
            and release.artifact.provenance
            == getattr(installed_release, "artifact_provenance", "")
        )
        if not immutable_fields_match:
            return self._decision(
                "blocked",
                "verification_failed",
                reason="release_mutation",
                trigger=trigger,
            )

        installed_artifact_sha256 = getattr(
            installed_release, "artifact_sha256", ""
        )
        if (
            release.artifact.provenance != "forge_source_archive"
            and release.artifact.sha256 != installed_artifact_sha256
        ):
            return self._decision(
                "blocked",
                "verification_failed",
                reason="release_mutation",
                trigger=trigger,
            )
        reason = ""
        if release.artifact.sha256 != installed_artifact_sha256:
            reason = "equivalent_recompressed_source"
        return self._decision(
            "none", "current", reason=reason, release=release, trigger=trigger
        )

    def decide(
        self,
        entry,
        *,
        operation,
        installed_mode,
        release=None,
        tombstone=None,
        metadata_authorized=True,
        metadata_reason="",
        installed_release=None,
        channel_preference=None,
        trigger="manual",
        downgrade_confirmed=False,
        release_was_activated=False,
        git_status="unknown",
    ):
        """Return the sole permitted route for the supplied trusted state."""
        if operation not in self.OPERATIONS:
            raise ValueError("Release management operation is unsupported.")
        if installed_mode not in self.INSTALLED_MODES:
            raise ValueError("Installed management mode is unsupported.")
        if trigger not in self.TRIGGERS:
            raise ValueError("Release management trigger must be manual or automatic.")
        if channel_preference is not None:
            channel_preference = _require_channel_preference(
                channel_preference
            )
        if operation == "install" and installed_mode != "absent":
            return self._decision(
                "none",
                "current",
                reason="plugin_already_installed",
                trigger=trigger,
            )

        policy = entry.delivery
        if channel_preference == "keep_git":
            if not policy.git_supported:
                return self._decision(
                    "blocked",
                    "release_metadata_unavailable",
                    reason="git_channel_unsupported",
                    trigger=trigger,
                )
            return self._git_decision(
                operation,
                installed_mode,
                git_status,
                trigger=trigger,
            )

        unavailable_reason = ""
        if tombstone is not None:
            unavailable_reason = "release_decertified"
            release = None
        elif not metadata_authorized:
            unavailable_reason = metadata_reason or "release_metadata_unavailable"
            release = None
        elif release is None:
            unavailable_reason = "release_entry_missing"

        if policy.preferred == "git" and channel_preference != "release":
            return self._git_decision(
                operation,
                installed_mode,
                git_status,
                reason=("release_decertified" if tombstone is not None else ""),
                trigger=trigger,
            )

        if release is None:
            return self._metadata_unavailable_decision(
                entry,
                operation,
                installed_mode,
                channel_preference,
                release_was_activated,
                git_status,
                unavailable_reason,
                trigger,
            )

        if operation == "install" or installed_mode == "absent":
            return self._decision(
                "release_install",
                "available",
                release=release,
                trigger=trigger,
            )

        if installed_mode == "git":
            migration_mode = release.artifact.migration_mode
            if not release.commit or migration_mode == "blocked":
                return self._decision(
                    "blocked",
                    "migration_waiting_for_release",
                    reason="release_not_migration_eligible",
                    trigger=trigger,
                )
            if trigger == "automatic" and migration_mode != "automatic":
                return self._decision(
                    "blocked",
                    "migration_waiting_for_release",
                    reason="release_requires_manual_migration",
                    trigger=trigger,
                )
            return self._decision(
                "release_migration",
                "migration_available",
                release=release,
                trigger=trigger,
            )

        return self._release_update_decision(
            release,
            installed_release,
            trigger,
            downgrade_confirmed,
        )

    def execute(self, entry, decision, queue_on_lock=True):
        """Execute exactly the selected route without cross-channel fallback."""
        if not isinstance(decision, ReleaseManagementDecision):
            raise ValueError("decision must be a ReleaseManagementDecision.")
        if decision.route == "git_install":
            return self.git_strategy.install(entry)
        if decision.route == "git_update":
            return self.git_strategy.update(
                entry, queue_on_lock=queue_on_lock
            )
        if decision.route == "git_status":
            return self.git_strategy.check_for_update(entry)
        if decision.route in {
            "release_install",
            "release_update",
            "release_migration",
        }:
            if self.release_strategy is None:
                return False, "Release management is not configured."
            execute_decision = getattr(
                self.release_strategy, "execute_decision", None
            )
            if callable(execute_decision):
                return execute_decision(entry, decision)
            operation = {
                "release_install": "install",
                "release_update": "update",
                "release_migration": "migrate",
            }[decision.route]
            return getattr(self.release_strategy, operation)(
                entry, decision.release, decision.trigger
            )
        if decision.route == "none":
            return True, decision.reason or "Plugin is current."
        if decision.route in {"blocked", "confirmation_required"}:
            return False, decision.reason or decision.status
        raise ValueError("Release management decision route is unsupported.")

    def _runtime_decision(self, entry, operation, trigger):
        context = self.plugin.getReleaseManagementContext(
            entry,
            operation=operation,
            trigger=trigger,
        )
        context = dict(context)
        index_sequence = context.pop("index_sequence", 0)
        context_error = context.pop("error", "")
        if context_error:
            return self._decision(
                "blocked",
                "verification_failed",
                reason=context_error,
                trigger=trigger,
            )
        decision = self.decide(
            entry,
            operation=operation,
            trigger=trigger,
            **context,
        )
        return replace(decision, index_sequence=index_sequence)

    def install(self, entry, trigger="manual"):
        """Install through the release-first policy selected at runtime."""
        if trigger not in self.TRIGGERS:
            raise ValueError("Release management trigger must be manual or automatic.")
        decision = self._runtime_decision(entry, "install", trigger)
        return self.execute(entry, decision)

    def update(self, entry, queue_on_lock=True, trigger="manual"):
        """Update or migrate through one selected management channel."""
        if trigger not in self.TRIGGERS:
            raise ValueError("Release management trigger must be manual or automatic.")
        decision = self._runtime_decision(entry, "update", trigger)
        return self.execute(
            entry,
            decision,
            queue_on_lock=queue_on_lock,
        )

    def check_for_update(self, entry, trigger="manual"):
        """Refresh channel-aware status without mutating a release install."""
        if trigger not in self.TRIGGERS:
            raise ValueError("Release management trigger must be manual or automatic.")
        decision = self._runtime_decision(entry, "status", trigger)
        if decision.route == "git_status":
            return self.execute(entry, decision)
        self.plugin.update_status[entry.key] = decision.status
        if decision.status == "available":
            self.plugin.fnSelectedNotify(entry.key)
        elif decision.status == "migration_available":
            release_version = (
                getattr(decision.release, "version", "")
                if decision.release is not None
                else ""
            )
            self.plugin.fnReleaseChannelNotify(
                entry.key,
                release_version,
            )
        return None


MANAGER_IDENTITY_SCHEMA_VERSION = 1
MANAGER_IDENTITY_RUNTIME_FILES = (
    "plugin.py",
    "package_registry.py",
    "package_identity.py",
    "pypluginstore.html",
)
MANAGER_IDENTITY_MAX_FILE_BYTES = 4 * 1024 * 1024
MANAGER_IDENTITY_MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MANAGER_IDENTITY_HASH_DOMAIN = b"PyPluginStore manager runtime identity\x00v1"
MANAGER_FRONTEND_IDENTITY_PATTERN = re.compile(
    rb"const MANAGER_FRONTEND_IDENTITY = Object\.freeze\((\{[^\r\n]*\})\);"
    rb" // x-pypluginstore-manager-identity"
)
MANAGER_IDENTITY_RECOVERY_ACTIONS = frozenset(
    {
        "get_local_registry",
        "list_plugins",
        "refresh_update_status",
        "restart_domoticz",
        "self_update_status",
    }
)

try:
    PYPLUGINSTORE_VERSION
except NameError:
    PYPLUGINSTORE_VERSION = "development"


def _manager_hash_frame(digest, value):
    value = bytes(value)
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def compute_manager_build_id(documents):
    """Return the deterministic build ID for the fixed manager runtime bundle."""
    if not isinstance(documents, Mapping):
        raise ValueError("Manager runtime documents must be a mapping.")

    total_size = 0
    digest = hashlib.sha256()
    _manager_hash_frame(digest, MANAGER_IDENTITY_HASH_DOMAIN)
    for relative_path in MANAGER_IDENTITY_RUNTIME_FILES:
        contents = documents.get(relative_path)
        if not isinstance(contents, (bytes, bytearray)):
            raise ValueError(relative_path + " is missing from the manager runtime bundle.")
        contents = bytes(contents)
        if not contents or len(contents) > MANAGER_IDENTITY_MAX_FILE_BYTES:
            raise ValueError(relative_path + " has an invalid manager runtime size.")
        total_size += len(contents)
        if total_size > MANAGER_IDENTITY_MAX_BUNDLE_BYTES:
            raise ValueError("Manager runtime bundle exceeds the size limit.")
        _manager_hash_frame(digest, relative_path.encode("utf-8"))
        _manager_hash_frame(digest, contents)
    return digest.hexdigest()


def _read_manager_runtime_file(root, relative_path):
    path = os.path.join(root, relative_path)
    try:
        path_stat = os.lstat(path)
    except OSError as error:
        raise ValueError(relative_path + " could not be read.") from error
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ValueError(relative_path + " must be a regular non-symlink file.")
    if (
        path_stat.st_size <= 0
        or path_stat.st_size > MANAGER_IDENTITY_MAX_FILE_BYTES
    ):
        raise ValueError(relative_path + " has an invalid manager runtime size.")

    open_flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        open_flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(path, open_flags)
        with os.fdopen(file_descriptor, "rb") as runtime_file:
            opened_stat = os.fstat(runtime_file.fileno())
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != path_stat.st_dev
                or opened_stat.st_ino != path_stat.st_ino
            ):
                raise ValueError(relative_path + " changed while it was being read.")
            contents = runtime_file.read(MANAGER_IDENTITY_MAX_FILE_BYTES + 1)
    except (OSError, ValueError) as error:
        if isinstance(error, ValueError):
            raise
        raise ValueError(relative_path + " could not be read.") from error

    if not contents or len(contents) > MANAGER_IDENTITY_MAX_FILE_BYTES:
        raise ValueError(relative_path + " has an invalid manager runtime size.")
    return contents


def _read_manager_runtime_bundle(root):
    root = os.path.abspath(root)
    documents = {}
    total_size = 0
    for relative_path in MANAGER_IDENTITY_RUNTIME_FILES:
        contents = _read_manager_runtime_file(root, relative_path)
        total_size += len(contents)
        if total_size > MANAGER_IDENTITY_MAX_BUNDLE_BYTES:
            raise ValueError("Manager runtime bundle exceeds the size limit.")
        documents[relative_path] = contents
    return documents


def _manager_product_version(plugin_contents):
    try:
        source = plugin_contents.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("plugin.py encoding is invalid.") from error
    plugin_tags = re.findall(
        r"<plugin\b([^>]*)>",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    versions = []
    for attributes in plugin_tags:
        version_match = re.search(
            r"\bversion\s*=\s*([\"'])(.*?)\1",
            attributes,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if version_match:
            versions.append(html.unescape(version_match.group(2)).strip())
    if len(versions) != 1 or not versions[0]:
        raise ValueError("plugin.py must declare exactly one manager version.")
    if re.search(r"[\x00-\x1f\x7f]", versions[0]):
        raise ValueError("plugin.py manager version is invalid.")
    return versions[0]


def _manager_identity_is_valid(identity):
    if not isinstance(identity, dict):
        return False
    return (
        identity.get("schema_version") == MANAGER_IDENTITY_SCHEMA_VERSION
        and isinstance(identity.get("product_version"), str)
        and bool(identity["product_version"].strip())
        and isinstance(identity.get("build_id"), str)
        and re.fullmatch(r"[0-9a-f]{64}", identity["build_id"]) is not None
        and (
            "git_commit" not in identity
            or (
                isinstance(identity["git_commit"], str)
                and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", identity["git_commit"])
                is not None
            )
        )
    )


def _public_manager_identity(identity):
    if not isinstance(identity, dict):
        return {}
    public_identity = {
        "schema_version": identity.get(
            "schema_version",
            MANAGER_IDENTITY_SCHEMA_VERSION,
        ),
        "product_version": str(identity.get("product_version") or ""),
        "build_id": str(identity.get("build_id") or ""),
    }
    git_commit = str(identity.get("git_commit") or "").lower()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", git_commit):
        public_identity["git_commit"] = git_commit
    return public_identity


def _manager_identities_match(first, second):
    if not _manager_identity_is_valid(first) or not _manager_identity_is_valid(second):
        return False
    return (
        first["schema_version"] == second["schema_version"]
        and first["product_version"] == second["product_version"]
        and first["build_id"] == second["build_id"]
    )


class ManagerIdentityService:
    """Compare loaded, installed, deployed, and browser manager generations."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.runtime_documents = None
        self.runtime_identity = None
        self.runtime_error = ""
        self.rendered_runtime_frontend = None

    def _runtime_root(self):
        loaded_module_path = os.path.abspath(__file__)
        if os.path.basename(loaded_module_path).lower() == "plugin.py":
            return os.path.dirname(loaded_module_path)
        return self.plugin.get_host().plugin_home_folder()

    def _git_commit(self, root):
        try:
            result = self.plugin.get_host().run_git_read_only(
                ["git", "rev-parse", "--verify", "HEAD"],
                root,
                timeout=15,
            )
        except Exception:
            return ""
        if result is None or result.returncode != 0:
            return ""
        output = str(result.stdout or "").strip().splitlines()
        commit = output[0].strip().lower() if output else ""
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
            return commit
        return ""

    def _identity_from_documents(self, documents, root):
        identity = {
            "schema_version": MANAGER_IDENTITY_SCHEMA_VERSION,
            "product_version": _manager_product_version(documents["plugin.py"]),
            "build_id": compute_manager_build_id(documents),
        }
        git_commit = self._git_commit(root)
        if git_commit:
            identity["git_commit"] = git_commit
        return identity

    def _validate_loaded_source_fingerprints(self, documents):
        if os.path.basename(os.path.abspath(__file__)).lower() != "plugin.py":
            return
        expected_fingerprints = {
            "plugin.py": _MANAGER_LOADED_PLUGIN_SOURCE_FINGERPRINT,
            "package_registry.py": (
                _MANAGER_LOADED_PACKAGE_REGISTRY_FINGERPRINT
            ),
            "package_identity.py": (
                _MANAGER_LOADED_PACKAGE_IDENTITY_FINGERPRINT
            ),
        }
        for relative_path, expected in expected_fingerprints.items():
            contents = documents[relative_path]
            actual = (len(contents), hashlib.sha256(contents).hexdigest())
            if expected is None or tuple(expected) != actual:
                raise ValueError(
                    relative_path
                    + " does not match the source loaded by this runtime."
                )

    def _unverifiable_identity(self):
        product_version = str(PYPLUGINSTORE_VERSION or "unknown")
        return {
            "schema_version": MANAGER_IDENTITY_SCHEMA_VERSION,
            "product_version": product_version,
            "build_id": "",
        }

    def capture_runtime(self):
        """Freeze the loaded generation before self-update can change disk."""
        if self.runtime_identity is not None:
            return dict(self.runtime_identity)

        runtime_root = self._runtime_root()
        try:
            documents = _read_manager_runtime_bundle(runtime_root)
            self._validate_loaded_source_fingerprints(documents)
            identity = self._identity_from_documents(documents, runtime_root)
            expected_version = str(PYPLUGINSTORE_VERSION or "")
            if (
                os.path.basename(os.path.abspath(__file__)).lower() == "plugin.py"
                and expected_version
                and expected_version != "development"
                and identity["product_version"] != expected_version
            ):
                raise ValueError("Loaded and declared manager versions do not match.")
            self.runtime_documents = dict(documents)
            self.runtime_identity = identity
        except (OSError, ValueError) as error:
            self.runtime_error = str(error)
            self.runtime_identity = self._unverifiable_identity()
            try:
                self.runtime_documents = {
                    "pypluginstore.html": _read_manager_runtime_file(
                        runtime_root,
                        "pypluginstore.html",
                    )
                }
            except ValueError:
                self.runtime_documents = None
        return dict(self.runtime_identity)

    def _replace_frontend_identity(self, source, identity):
        matches = list(MANAGER_FRONTEND_IDENTITY_PATTERN.finditer(source))
        if len(matches) != 1:
            raise ValueError(
                "pypluginstore.html must contain exactly one manager identity placeholder."
            )
        serialized = json.dumps(
            _public_manager_identity(identity),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        replacement = (
            b"const MANAGER_FRONTEND_IDENTITY = Object.freeze("
            + serialized
            + b"); // x-pypluginstore-manager-identity"
        )
        match = matches[0]
        return source[: match.start()] + replacement + source[match.end() :]

    def render_runtime_frontend(self):
        """Return the immutable custom page rendered for the loaded generation."""
        self.capture_runtime()
        if self.rendered_runtime_frontend is not None:
            return self.rendered_runtime_frontend
        if not self.runtime_documents:
            raise ValueError("Loaded manager frontend could not be captured.")
        source = self.runtime_documents.get("pypluginstore.html")
        self.rendered_runtime_frontend = self._replace_frontend_identity(
            source,
            self.runtime_identity,
        )
        return self.rendered_runtime_frontend

    def deploy_runtime_frontend(self):
        """Atomically synchronize the Domoticz template with the loaded page."""
        rendered = self.render_runtime_frontend()
        destination = self.plugin.get_host().ui_html_destination()
        current = None
        try:
            if os.path.islink(destination):
                current = None
            else:
                with open(destination, "rb") as deployed_file:
                    current = deployed_file.read(MANAGER_IDENTITY_MAX_FILE_BYTES + 1)
        except OSError:
            current = None
        if current == rendered:
            return False
        self.plugin.get_host().write_web_bytes_atomic(destination, rendered)
        return True

    def _installed_identity(self):
        installed_root = self.plugin.get_host().plugin_home_folder()
        try:
            documents = _read_manager_runtime_bundle(installed_root)
            return self._identity_from_documents(documents, installed_root), ""
        except (OSError, ValueError) as error:
            return self._unverifiable_identity(), str(error)

    def runtime_installation_status(self):
        """Return whether the frozen runtime and current installed bundle match."""
        runtime = self.capture_runtime()
        installed, installed_error = self._installed_identity()
        verifiable = (
            _manager_identity_is_valid(runtime)
            and not self.runtime_error
            and _manager_identity_is_valid(installed)
            and not installed_error
        )
        return {
            "verifiable": verifiable,
            "matches": verifiable
            and _manager_identities_match(runtime, installed),
            "runtime": _public_manager_identity(runtime),
            "installed": _public_manager_identity(installed),
        }

    def _extract_frontend_identity(self, contents):
        matches = list(MANAGER_FRONTEND_IDENTITY_PATTERN.finditer(contents))
        if len(matches) != 1:
            return {}
        try:
            identity = json.loads(matches[0].group(1).decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return _public_manager_identity(identity)

    def _deployed_frontend(self):
        deployed = {"identity": {}, "exact_match": False}
        try:
            rendered = self.render_runtime_frontend()
            destination = self.plugin.get_host().ui_html_destination()
            destination_stat = os.lstat(destination)
            if stat.S_ISLNK(destination_stat.st_mode) or not stat.S_ISREG(
                destination_stat.st_mode
            ):
                return deployed
            if destination_stat.st_size > MANAGER_IDENTITY_MAX_FILE_BYTES:
                return deployed
            with open(destination, "rb") as deployed_file:
                contents = deployed_file.read(MANAGER_IDENTITY_MAX_FILE_BYTES + 1)
            if len(contents) > MANAGER_IDENTITY_MAX_FILE_BYTES:
                return deployed
            deployed["identity"] = self._extract_frontend_identity(contents)
            deployed["exact_match"] = contents == rendered
        except (OSError, ValueError):
            pass
        return deployed

    def _identity_label(self, identity):
        if not _manager_identity_is_valid(identity):
            return "PyPluginStore"
        label = (
            "PyPluginStore v"
            + identity["product_version"]
            + " \u00b7 build "
            + identity["build_id"][:12]
        )
        if identity.get("git_commit"):
            label += " \u00b7 git " + identity["git_commit"][:8]
        return label

    def get_verdict(self, frontend_identity=None, self_update_state=None):
        """Return a safe coherence verdict for one browser request."""
        runtime = self.capture_runtime()
        installed, installed_error = self._installed_identity()
        deployed = self._deployed_frontend()
        frontend = _public_manager_identity(frontend_identity)
        runtime_valid = _manager_identity_is_valid(runtime) and not self.runtime_error
        installed_valid = (
            _manager_identity_is_valid(installed) and not installed_error
        )
        runtime_installed_match = _manager_identities_match(runtime, installed)
        frontend_match = _manager_identities_match(runtime, frontend)
        deployed_match = (
            deployed["exact_match"]
            and _manager_identities_match(runtime, deployed["identity"])
        )
        active_update = str((self_update_state or {}).get("phase") or "") in {
            "scheduled",
            "running",
        }
        label = self._identity_label(runtime)

        if not runtime_valid or not installed_valid:
            state = "unverifiable"
            coherent = False
            message = (
                label
                + " \u00b7 runtime identity could not be verified. Repair or reinstall "
                "PyPluginStore before making changes."
            )
        elif active_update:
            state = "updating"
            coherent = runtime_installed_match and deployed_match and frontend_match
            message = (
                label
                + " \u00b7 self-update is in progress. Changes are temporarily read-only."
            )
        elif not runtime_installed_match:
            state = "restart_required"
            coherent = False
            message = (
                label
                + " is running, but a different build is installed. Restart "
                "Domoticz to finish the PyPluginStore update."
            )
        elif not deployed_match:
            state = "ui_deploy_stale"
            coherent = False
            message = (
                label
                + " \u00b7 the Domoticz custom page is not synchronized. Check template "
                "permissions, restart Domoticz, then reload this page."
            )
        elif frontend_identity is None:
            state = "legacy_frontend"
            coherent = False
            message = (
                label
                + " \u00b7 this browser page has no runtime identity. Hard-refresh the "
                "page to enable changes."
            )
        elif not frontend_match:
            state = "frontend_stale"
            coherent = False
            message = (
                label
                + " \u00b7 this browser page is from a different build. Hard-refresh "
                "the page before making changes."
            )
        else:
            state = "consistent"
            coherent = True
            message = (
                label
                + " \u00b7 frontend, backend, and installed files match."
            )

        verdict = {
            "schema_version": MANAGER_IDENTITY_SCHEMA_VERSION,
            "state": state,
            "coherent": coherent,
            "mutations_allowed": state == "consistent",
            "message": message,
            "runtime": _public_manager_identity(runtime),
            "installed": _public_manager_identity(installed),
            "deployed": deployed,
        }
        if frontend:
            verdict["frontend"] = frontend
        return verdict


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
        self.registry_entries = {}
        self.public_registry_data = None
        self.local_plugin_keys = []
        self.local_registry_error = ""
        self.update_times = {}
        self.update_status = {}
        self.plugin_platforms = {}
        self.last_update_date = None
        self.last_update_status_refresh_date = None
        self.host = None
        self.installed_plugin_folders = {}
        self.ambiguous_installed_plugin_folders = {}
        self.installed_plugin_match_details = {}
        self.installed_plugin_install_conflicts = {}
        self.installed_plugins_snapshot = []
        self.installed_plugins_snapshot_available = False
        self.installed_plugin_scan_error = ""
        self.release_metadata_selection = ReleaseMetadataSelection(
            sequence=0,
            registry_bytes=b"",
            release_index_bytes=b"",
            release_index=None,
            release_authorized=False,
            reason="Release metadata has not been loaded.",
        )
        self.release_metadata_store = None
        self.release_metadata_store_root = ""
        self.registry_service = RegistryService(self)
        self.local_registry_service = LocalRegistryService(self)
        self.update_status_service = UpdateStatusService(self)
        self.channel_preference_service = ChannelPreferenceService(self)
        self.install_metadata_service = InstallMetadataService(self)
        self.release_transaction_manager = ReleaseTransactionManager(self)
        self.release_dependency_service = ReleaseDependencySnapshotService(
            self,
            transaction_manager=self.release_transaction_manager,
        )
        self.release_install_update_strategy = ReleaseInstallUpdateStrategy(
            self,
            transaction_manager=self.release_transaction_manager,
            dependency_service=self.release_dependency_service,
            metadata_service=self.install_metadata_service,
        )
        self.install_update_strategy = ReleaseManagementCoordinator(
            self,
            release_strategy=self.release_install_update_strategy,
        )
        self.manager_identity_service = None
        self._api_frontend_identity = None
        self._api_manager_identity = None

    def get_host(self):
        parameters = globals().get("Parameters")
        if parameters is None:
            parameters = self.host.parameters if self.host is not None else {}
        if self.host is None or self.host.parameters is not parameters:
            self.host = make_host_runtime(parameters)
        return self.host

    def getManagerIdentityService(self):
        if self.manager_identity_service is None:
            self.manager_identity_service = ManagerIdentityService(self)
        return self.manager_identity_service

    def captureManagerRuntimeIdentity(self):
        return self.getManagerIdentityService().capture_runtime()

    def getManagerIdentityVerdict(
        self,
        frontend_identity=None,
        self_update_state=None,
    ):
        return self.getManagerIdentityService().get_verdict(
            frontend_identity=frontend_identity,
            self_update_state=self_update_state,
        )

    def get_current_plugin_folder(self):
        return self.get_host().current_plugin_folder()

    def get_git_env(self):
        return self.get_host().get_git_env()

    def get_plugin_home_folder(self):
        return self.get_host().plugin_home_folder()

    def get_bundled_update_times_file(self):
        return os.path.join(self.get_plugin_home_folder(), "update_times.json")

    def get_update_times_cache_file(self):
        return os.path.join(self.get_plugin_home_folder(), "update_times.cache.json")

    def get_update_times_url(self):
        return "https://raw.githubusercontent.com/adrighem/PyPluginStore/refs/heads/master/update_times.json"

    def get_registry_url(self):
        return "https://raw.githubusercontent.com/adrighem/PyPluginStore/refs/heads/master/registry.json"

    def get_release_index_url(self):
        return "https://raw.githubusercontent.com/adrighem/PyPluginStore/refs/heads/master/release_index.json"

    def get_bundled_registry_file(self):
        return os.path.join(self.get_plugin_home_folder(), "registry.json")

    def get_bundled_release_index_file(self):
        return os.path.join(
            self.get_plugin_home_folder(), "release_index.json"
        )

    def get_release_metadata_root(self):
        return os.path.join(
            self.get_plugin_home_folder(),
            ".pypluginstore",
            "metadata",
        )

    def getReleaseMetadataStore(self):
        metadata_root = os.path.abspath(self.get_release_metadata_root())
        if (
            self.release_metadata_store is None
            or self.release_metadata_store_root != metadata_root
        ):
            self.release_metadata_store = ReleaseMetadataStore(
                metadata_root,
                bundled_registry_path=self.get_bundled_registry_file(),
                bundled_index_path=self.get_bundled_release_index_file(),
            )
            self.release_metadata_store_root = metadata_root
        return self.release_metadata_store

    def get_local_registry_file(self):
        return os.path.join(self.get_plugin_home_folder(), "registry_local.json")

    def loadReleaseMetadataSelection(self):
        try:
            selection = self.getReleaseMetadataStore().load()
        except Exception as error:
            selection = ReleaseMetadataSelection(
                sequence=0,
                registry_bytes=b"",
                release_index_bytes=b"",
                release_index=None,
                release_authorized=False,
                reason="Release metadata could not be loaded: " + str(error),
            )
        self.release_metadata_selection = selection
        return selection

    def getCurrentReleaseMetadataSelection(self):
        """Return the selected pair after rechecking runtime freshness."""
        selection = self.release_metadata_selection
        if not isinstance(selection, ReleaseMetadataSelection):
            selection = ReleaseMetadataSelection(
                sequence=0,
                registry_bytes=b"",
                release_index_bytes=b"",
                release_index=None,
                release_authorized=False,
                reason=(
                    "Release metadata freshness could not be verified; "
                    "release changes are paused."
                ),
            )
        elif selection.release_authorized:
            try:
                selection = self.getReleaseMetadataStore().revalidate_selection(
                    selection
                )
            except Exception:
                selection = ReleaseMetadataSelection(
                    sequence=selection.sequence,
                    registry_bytes=selection.registry_bytes,
                    release_index_bytes=selection.release_index_bytes,
                    release_index=None,
                    release_authorized=False,
                    reason=(
                        "Release metadata freshness could not be verified; "
                        "release changes are paused."
                    ),
                )
        self.release_metadata_selection = selection
        return selection

    def getReleaseManagementContext(
        self,
        entry,
        *,
        operation,
        trigger,
    ):
        if operation not in ReleaseManagementCoordinator.OPERATIONS:
            raise ValueError("Release management operation is unsupported.")
        if trigger not in ReleaseManagementCoordinator.TRIGGERS:
            raise ValueError(
                "Release management trigger must be manual or automatic."
            )
        selection = self.getCurrentReleaseMetadataSelection()
        metadata_authorized, metadata_reason = (
            self.getReleaseMetadataAuthorization(selection)
        )
        release_index = (
            selection.release_index
            if metadata_authorized
            else None
        )
        release = None
        tombstone = None
        if release_index is not None and not entry.local:
            release = release_index.plugins.get(entry.key)
            tombstone = release_index.tombstones.get(entry.key)
            expected_identity = normalize_repository_identity(
                entry.author, entry.repository
            )
            if release is not None and (
                not expected_identity
                or release.repository_identity != expected_identity
            ):
                return {
                    "error": "Release metadata does not match the registry repository.",
                    "installed_mode": "absent",
                    "index_sequence": selection.sequence,
                }

        try:
            plugin_dir = self.resolve_installed_plugin_dir(entry.key)
        except ValueError as error:
            return {
                "error": str(error),
                "installed_mode": "absent",
                "index_sequence": selection.sequence,
            }

        installed_mode = "absent"
        installed_release = None
        release_was_activated = False
        metadata_path = os.path.join(
            plugin_dir, InstallMetadataService.FILE_NAME
        )
        if os.path.lexists(plugin_dir):
            if os.path.islink(plugin_dir) or not os.path.isdir(plugin_dir):
                return {
                    "error": "Installed plugin path is not a real directory.",
                    "installed_mode": "absent",
                    "index_sequence": selection.sequence,
                }
            if os.path.lexists(metadata_path):
                installed_mode = "release"
                release_was_activated = True
                if os.path.lexists(os.path.join(plugin_dir, ".git")):
                    return {
                        "error": (
                            "The plugin contains both Release metadata and Git "
                            "control data. Resolve the mixed management state "
                            "before changing it."
                        ),
                        "installed_mode": "release",
                        "index_sequence": selection.sequence,
                    }
                try:
                    installed_release = self.install_metadata_service.read(
                        plugin_dir
                    )
                except OSError:
                    return {
                        "error": (
                            "Installed release metadata could not be upgraded "
                            "or cleaned up safely."
                        ),
                        "installed_mode": "release",
                        "index_sequence": selection.sequence,
                    }
                except ValueError as error:
                    return {
                        "error": "Installed release metadata is invalid: "
                        + str(error),
                        "installed_mode": "release",
                        "index_sequence": selection.sequence,
                    }
                expected_identity = normalize_repository_identity(
                    entry.author,
                    entry.repository,
                )
                installed_package_id = getattr(
                    installed_release,
                    "package_id",
                    getattr(installed_release, "plugin_key", None),
                )
                installed_repository_identity = getattr(
                    installed_release,
                    "repository_identity",
                    None,
                )
                if (
                    installed_release is None
                    or not expected_identity
                    or (
                        installed_package_id is not None
                        and installed_package_id != entry.key
                    )
                    or (
                        installed_repository_identity is not None
                        and installed_repository_identity
                        != expected_identity
                    )
                    or entry.local
                ):
                    return {
                        "error": (
                            "Installed release metadata does not match the "
                            "current registry entry."
                        ),
                        "installed_mode": "release",
                        "index_sequence": selection.sequence,
                    }
            elif is_git_checkout(plugin_dir):
                installed_mode = "git"
            elif entry.key == self.get_current_plugin_folder():
                installed_mode = "git"
            else:
                return {
                    "error": "Installed plugin is not managed by Release or Git.",
                    "installed_mode": "absent",
                    "index_sequence": selection.sequence,
                }

        preference = None
        repository_identity = normalize_repository_identity(
            entry.author, entry.repository
        )
        if repository_identity and not entry.local:
            try:
                preference = self.channel_preference_service.get(
                    repository_identity
                )
            except ValueError as error:
                return {
                    "error": "Channel preference is invalid: " + str(error),
                    "installed_mode": installed_mode,
                    "index_sequence": selection.sequence,
                }

        return {
            "installed_mode": installed_mode,
            "release": release,
            "tombstone": tombstone,
            "metadata_authorized": metadata_authorized,
            "metadata_reason": metadata_reason,
            "installed_release": installed_release,
            "channel_preference": preference,
            "downgrade_confirmed": False,
            "release_was_activated": release_was_activated,
            "git_status": self.update_status.get(entry.key, "unknown"),
            "index_sequence": selection.sequence,
        }

    def get_registry_entry(self, plugin_key):
        existing_entry = self.registry_entries.get(plugin_key)
        if existing_entry is not None:
            return existing_entry

        data = self.plugin_data.get(plugin_key)
        if data is not None:
            has_explicit_platforms = (
                plugin_key in self.plugin_platforms
                or (isinstance(data, list) and len(data) > 5)
                or (
                    isinstance(data, dict)
                    and ("platforms" in data or "platform" in data)
                )
            )
            entry, platforms = self.registry_service.normalize_entry(
                plugin_key,
                data,
                local=plugin_key in self.local_plugin_keys,
                default_platforms=self.plugin_platforms.get(plugin_key),
            )
            if entry is None:
                return None
            self.registry_entries[plugin_key] = entry
            if has_explicit_platforms:
                self.plugin_platforms[plugin_key] = platforms
            return entry

        return self.registry_entries.get(plugin_key)

    def get_registry_entry_for_operation(self, plugin_key, author="", repository="", branch=""):
        entry = self.get_registry_entry(plugin_key)
        if entry is None:
            entry = RegistryEntry(plugin_key, author, repository, "", branch or "master")
        else:
            entry = entry.copy_with(
                author=author if author else None,
                repository=repository if repository else None,
                branch=branch if branch else None,
            )
        return entry

    def supported_git_host(self, hostname):
        hostname = str(hostname or "").strip().lower()
        return hostname if hostname in SUPPORTED_GIT_HOSTS else ""

    def clean_repository_path_parts(self, path_parts):
        cleaned_parts = []
        for part in path_parts:
            part = urllib.parse.unquote(str(part or "").strip())
            if not part:
                continue
            if part in REPOSITORY_PATH_STOP_PARTS:
                break
            cleaned_parts.append(part)

        if cleaned_parts and cleaned_parts[-1].endswith(".git"):
            cleaned_parts[-1] = cleaned_parts[-1][:-4]
        return cleaned_parts

    def split_supported_repo_reference(self, repo_reference):
        repo_reference = str(repo_reference or "").strip().rstrip("/")
        if not repo_reference:
            return "", []

        if re.match(r"^[^/@:]+@[^:]+:.+", repo_reference):
            user_host, path = repo_reference.split(":", 1)
            host = self.supported_git_host(user_host.split("@")[-1])
            if not host:
                return "", []
            return host, self.clean_repository_path_parts(path.split("/"))

        parsed_url = urllib.parse.urlparse(repo_reference)
        if parsed_url.hostname:
            host = self.supported_git_host(parsed_url.hostname)
            if not host:
                return "", []
            return host, self.clean_repository_path_parts(parsed_url.path.split("/"))

        shorthand_url = urllib.parse.urlparse("//" + repo_reference)
        host = self.supported_git_host(shorthand_url.hostname)
        if host:
            return host, self.clean_repository_path_parts(shorthand_url.path.split("/"))

        return "", []

    def repository_path_from_entry(self, author, repository):
        repository = str(repository or "").strip().strip("/")
        host, path_parts = self.split_supported_repo_reference(author)
        if not host:
            host = DEFAULT_GIT_HOST
            path_parts = self.clean_repository_path_parts(str(author or "").strip().split("/"))

        if repository:
            path_parts = list(path_parts) + [repository]

        return host, self.clean_repository_path_parts(path_parts)

    def quote_url_path_parts(self, path_parts):
        return "/".join(urllib.parse.quote(str(part), safe="") for part in path_parts)

    def build_git_clone_url(self, author, repository):
        author = str(author or "").strip()
        repository = str(repository or "").strip()

        if author.startswith("git@") or author.startswith("ssh://") or author.startswith("file://"):
            return author.rstrip("/")

        if author.startswith("http://") or author.startswith("https://"):
            clone_url = author.rstrip("/")
            parsed_url = urllib.parse.urlparse(clone_url)
            host, path_parts = self.split_supported_repo_reference(clone_url)
            if parsed_url.scheme in ("http", "https") and host and len(path_parts) >= 2:
                clone_url = parsed_url.scheme + "://" + host + "/" + self.quote_url_path_parts(path_parts)
                if not clone_url.endswith(".git"):
                    clone_url += ".git"
            return clone_url

        host, path_parts = self.repository_path_from_entry(author, repository)
        if host and len(path_parts) >= 2:
            return "https://" + host + "/" + self.quote_url_path_parts(path_parts) + ".git"

        return f"https://github.com/{author}/{repository}.git"

    def build_raw_plugin_url(self, author, repository, branch):
        host, path_parts = self.repository_path_from_entry(author, repository)
        if not host or len(path_parts) < 2:
            return ""

        repo_path = self.quote_url_path_parts(path_parts)
        branch = urllib.parse.quote(str(branch or ""), safe="")
        if host == "github.com":
            return "https://raw.githubusercontent.com/" + repo_path + "/" + branch + "/plugin.py"
        if host == "gitlab.com":
            return "https://gitlab.com/" + repo_path + "/-/raw/" + branch + "/plugin.py"
        if host == "codeberg.org":
            return "https://codeberg.org/" + repo_path + "/raw/branch/" + branch + "/plugin.py"
        return ""

    def normalize_plugin_folder_name(self, folder_name):
        folder_name = str(folder_name or "").strip().strip("/\\")
        if folder_name.endswith(".git"):
            folder_name = folder_name[:-4]
        return folder_name.lower()

    def plugin_folder_name_from_clone_url(self, clone_url):
        clone_url = str(clone_url or "").strip().rstrip("/")
        if not clone_url:
            return ""
        if clone_url.endswith(".git"):
            clone_url = clone_url[:-4]

        if re.match(r"^[^/@:]+@[^:]+:.+", clone_url):
            path = clone_url.split(":", 1)[1]
        else:
            parsed_url = urllib.parse.urlparse(clone_url)
            path = parsed_url.path if parsed_url.scheme else clone_url

        return os.path.basename(path.rstrip("/"))

    def normalize_git_remote_url(self, remote_url):
        remote_url = str(remote_url or "").strip()
        if not remote_url:
            return ""

        remote_url = remote_url.replace(" (fetch)", "").replace(" (push)", "").strip()
        if re.match(r"^[^/@:]+@[^:]+:.+", remote_url):
            user_host, path = remote_url.split(":", 1)
            host = user_host.split("@")[-1]
            path = path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
            return (host + "/" + path).lower()

        parsed_url = urllib.parse.urlparse(remote_url)
        if parsed_url.scheme == "file":
            file_path = urllib.request.url2pathname(parsed_url.path)
            return "file://" + os.path.normcase(os.path.abspath(file_path)).rstrip(os.sep).lower()

        if parsed_url.hostname:
            path = parsed_url.path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
            return (parsed_url.hostname + "/" + path).lower()

        remote_url = remote_url.rstrip("/")
        if remote_url.endswith(".git"):
            remote_url = remote_url[:-4]
        return remote_url.lower()

    def normalize_git_repo_identity(self, repo_url):
        host, path_parts = self.split_supported_repo_reference(repo_url)
        if not host or len(path_parts) < 2:
            return ""
        return (host + "/" + "/".join(path_parts)).lower()

    def normalize_github_repo_identity(self, repo_url):
        return self.normalize_git_repo_identity(repo_url)

    def normalize_plugin_metadata_value(self, value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def strip_domoticz_plugin_affixes(self, value):
        original_value = str(value or "").strip()
        if original_value.endswith(".git"):
            original_value = original_value[:-4]

        cleaned_value = original_value
        for pattern in (
            r"^domoticz[-_ ]+plugin[-_ ]+",
            r"^domoticz[-_ ]+for[-_ ]+",
            r"^domoticz[-_ ]+",
            r"[-_ ]+domoticz[-_ ]+plugin$",
            r"[-_ ]+for[-_ ]+domoticz$",
            r"[-_ ]+domoticz$",
        ):
            cleaned_value = re.sub(pattern, "", cleaned_value, flags=re.IGNORECASE)

        cleaned_value = re.sub(r"^[-_ ]+|[-_ ]+$", "", cleaned_value)
        if cleaned_value != original_value:
            cleaned_value = re.sub(r"^plugin[-_ ]+", "", cleaned_value, flags=re.IGNORECASE)
            cleaned_value = re.sub(r"[-_ ]+plugin$", "", cleaned_value, flags=re.IGNORECASE)
            cleaned_value = re.sub(r"^[-_ ]+|[-_ ]+$", "", cleaned_value)

        return cleaned_value or original_value

    def parse_domoticz_plugin_metadata(self, plugin_dir):
        plugin_file = os.path.join(plugin_dir, "plugin.py")
        if not os.path.isfile(plugin_file):
            return None

        try:
            with open(plugin_file, "r", encoding="utf-8", errors="ignore") as f:
                source_code = f.read(256 * 1024)
        except Exception as e:
            Domoticz.Debug("Could not read plugin metadata from " + plugin_file + ": " + str(e))
            return None

        plugin_tag = re.search(r"<plugin\b([^>]*)>", source_code, re.IGNORECASE | re.DOTALL)
        if not plugin_tag:
            return None

        metadata = {}
        for match in re.finditer(r"([A-Za-z_][\w:.-]*)\s*=\s*(\"([^\"]*)\"|'([^']*)')", plugin_tag.group(1)):
            value = match.group(3) if match.group(3) is not None else match.group(4)
            metadata[match.group(1).lower()] = html.unescape(value or "")
        return metadata

    def plugin_metadata_matches_registry(self, plugin_key, plugin_dir, metadata=None):
        entry = self.get_registry_entry(plugin_key)
        if entry is None:
            return False

        if metadata is None:
            metadata = self.parse_domoticz_plugin_metadata(plugin_dir)
            if metadata is None:
                Domoticz.Debug("Skipped possible plugin match for folder " + os.path.basename(plugin_dir) + " because plugin.py metadata could not be verified.")
                return False

        clone_url = self.build_git_clone_url(entry.author, entry.repository)
        expected_repo_identity = self.normalize_github_repo_identity(clone_url)
        externallink = metadata.get("externallink", "")
        externallink_identity = self.normalize_github_repo_identity(externallink)
        if externallink_identity:
            if externallink_identity == expected_repo_identity:
                return True
            Domoticz.Debug("Skipped possible plugin match for folder " + os.path.basename(plugin_dir) + " because externallink points to another repository.")
            return False

        repo_folder = self.plugin_folder_name_from_clone_url(clone_url)
        expected_names = set()
        for expected_name in (
            plugin_key,
            entry.repository,
            repo_folder,
            entry.domoticz_key,
        ):
            expected_names.add(self.normalize_plugin_metadata_value(expected_name))
            expected_names.add(
                self.normalize_plugin_metadata_value(
                    self.strip_domoticz_plugin_affixes(expected_name)
                )
            )
        expected_names.discard("")

        metadata_names = [
            self.normalize_plugin_metadata_value(metadata.get("key", "")),
            self.normalize_plugin_metadata_value(metadata.get("name", "")),
        ]
        if any(metadata_name in expected_names for metadata_name in metadata_names if metadata_name):
            return True

        plugin_key_metadata = self.normalize_plugin_metadata_value(metadata.get("key", ""))
        if plugin_key_metadata and plugin_key_metadata not in expected_names:
            Domoticz.Debug("Skipped possible plugin match for folder " + os.path.basename(plugin_dir) + " because plugin metadata key does not match.")
            return False

        return True

    def add_lookup_candidate(self, lookup, lookup_key, plugin_key):
        if not lookup_key:
            return
        if lookup_key not in lookup:
            lookup[lookup_key] = []
        if plugin_key not in lookup[lookup_key]:
            lookup[lookup_key].append(plugin_key)

    def prefer_local_lookup_candidates(self, lookup):
        local_plugin_keys = set(self.local_plugin_keys)
        if not local_plugin_keys:
            return

        for lookup_key, matched_keys in list(lookup.items()):
            local_matches = [plugin_key for plugin_key in matched_keys if plugin_key in local_plugin_keys]
            if local_matches and len(local_matches) != len(matched_keys):
                lookup[lookup_key] = local_matches

    def add_install_name_candidate(self, name_lookup, candidate, plugin_key):
        self.add_lookup_candidate(name_lookup, self.normalize_plugin_folder_name(candidate), plugin_key)

    def add_flexible_name_candidate(self, name_lookup, candidate, plugin_key):
        self.add_lookup_candidate(name_lookup, self.normalize_plugin_metadata_value(candidate), plugin_key)

    def add_domoticz_affix_name_candidate(self, name_lookup, candidate, plugin_key):
        self.add_flexible_name_candidate(name_lookup, self.strip_domoticz_plugin_affixes(candidate), plugin_key)

    def build_registry_repository_identity_lookup(self):
        """Return every registry owner for each repository identity."""
        lookup = {}
        for plugin_key in self.plugin_data:
            if plugin_key == "Idle":
                continue
            entry = self.get_registry_entry(plugin_key)
            if entry is None:
                continue
            clone_url = self.build_git_clone_url(
                entry.author,
                entry.repository,
            )
            self.add_lookup_candidate(
                lookup,
                self.normalize_github_repo_identity(clone_url),
                plugin_key,
            )
        return lookup

    def build_installed_plugin_lookup(self):
        exact_name_lookup = {}
        archive_name_lookup = {}
        flexible_name_lookup = {}
        metadata_name_lookup = {}
        remote_lookup = {}
        repo_identity_lookup = {}

        for plugin_key in self.plugin_data:
            entry = self.get_registry_entry(plugin_key)
            if plugin_key == "Idle" or entry is None:
                continue

            exact_name_lookup[self.normalize_plugin_folder_name(plugin_key)] = plugin_key
            self.add_flexible_name_candidate(flexible_name_lookup, plugin_key, plugin_key)
            self.add_flexible_name_candidate(metadata_name_lookup, plugin_key, plugin_key)

        for plugin_key in self.plugin_data:
            entry = self.get_registry_entry(plugin_key)
            if plugin_key == "Idle" or entry is None:
                continue

            repository = entry.repository
            branch = entry.branch
            clone_url = self.build_git_clone_url(entry.author, repository)
            repo_folder = self.plugin_folder_name_from_clone_url(clone_url)
            name_candidates = [repository, repo_folder]

            for candidate in name_candidates:
                if not candidate:
                    continue
                self.add_install_name_candidate(archive_name_lookup, candidate, plugin_key)
                self.add_flexible_name_candidate(flexible_name_lookup, candidate, plugin_key)
                self.add_domoticz_affix_name_candidate(flexible_name_lookup, candidate, plugin_key)
                self.add_flexible_name_candidate(metadata_name_lookup, candidate, plugin_key)
                if branch:
                    self.add_install_name_candidate(archive_name_lookup, candidate + "-" + branch, plugin_key)
                    self.add_install_name_candidate(archive_name_lookup, candidate + "_" + branch, plugin_key)
                    self.add_flexible_name_candidate(flexible_name_lookup, candidate + "-" + branch, plugin_key)
                    self.add_flexible_name_candidate(flexible_name_lookup, candidate + "_" + branch, plugin_key)
                    stripped_candidate = self.strip_domoticz_plugin_affixes(candidate)
                    self.add_flexible_name_candidate(flexible_name_lookup, stripped_candidate + "-" + branch, plugin_key)
                    self.add_flexible_name_candidate(flexible_name_lookup, stripped_candidate + "_" + branch, plugin_key)

            remote_key = self.normalize_git_remote_url(clone_url)
            self.add_lookup_candidate(remote_lookup, remote_key, plugin_key)
            repo_identity = self.normalize_github_repo_identity(clone_url)
            self.add_lookup_candidate(repo_identity_lookup, repo_identity, plugin_key)

        for lookup in (
            archive_name_lookup,
            flexible_name_lookup,
            metadata_name_lookup,
            remote_lookup,
            repo_identity_lookup,
        ):
            self.prefer_local_lookup_candidates(lookup)

        return (
            exact_name_lookup,
            archive_name_lookup,
            flexible_name_lookup,
            metadata_name_lookup,
            remote_lookup,
            repo_identity_lookup,
        )

    def add_release_install_conflict(
        self,
        conflicts,
        plugin_key,
        plugin_folder,
        match,
        reason,
    ):
        """Aggregate authoritative metadata claims that block an install."""
        conflict = conflicts.get(plugin_key, {})
        folders = set(conflict.get("folders", []))
        folders.add(plugin_folder)
        folders = sorted(folders, key=lambda value: value.casefold())
        package_ids = set(conflict.get("package_ids", []))
        if match.get("package_id"):
            package_ids.add(match["package_id"])
        package_ids = sorted(package_ids, key=lambda value: value.casefold())
        repository_identities = set(
            conflict.get("repository_identities", [])
        )
        if match.get("repository_identity"):
            repository_identities.add(match["repository_identity"])
        repository_identities = sorted(repository_identities)
        reasons = set(conflict.get("reasons", []))
        reasons.add(reason)
        reasons = sorted(reasons)
        sources = set(conflict.get("sources", []))
        sources.add(
            match.get("source", "release install metadata conflict")
        )
        sources = sorted(sources)
        folder_label = ", ".join(folders)
        package_label = ", ".join(package_ids) or "an unknown package"
        conflicts[plugin_key] = {
            "folder": folders[0] if len(folders) == 1 else "",
            "folders": folders,
            "source": (
                sources[0]
                if len(sources) == 1
                else "release install metadata conflict"
            ),
            "sources": sources,
            "detail": (
                "Committed Release metadata in "
                + folder_label
                + " conflicts with this registry plugin by "
                + " and ".join(reasons)
                + "."
            ),
            "management_error": (
                "Release-managed folder "
                + folder_label
                + " is recorded for "
                + package_label
                + " and already uses this repository or package folder. "
                "Restore the matching registry entry or reinstall that "
                "folder before changing "
                + plugin_key
                + "."
            ),
            "package_id": package_ids[0] if len(package_ids) == 1 else "",
            "package_ids": package_ids,
            "repository_identity": (
                repository_identities[0]
                if len(repository_identities) == 1
                else ""
            ),
            "repository_identities": repository_identities,
            "reasons": reasons,
        }

    def get_git_remote_urls(self, plugin_dir):
        result = self.run_git_command(plugin_dir, ["git", "remote", "-v"], timeout=10)
        remote_urls = []
        if result is not None and result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[2] in ("(fetch)", "(push)") and parts[1] not in remote_urls:
                    remote_urls.append(parts[1])

        if not remote_urls:
            remote_url = self.get_git_remote_url(plugin_dir, "origin")
            if remote_url and not any(char.isspace() for char in remote_url):
                remote_urls.append(remote_url)
        return remote_urls

    def make_installed_plugin_match(self, plugin_key, source, priority, detail):
        return {
            "key": plugin_key,
            "source": source,
            "priority": priority,
            "detail": detail,
        }

    def match_detail_for_response(self, plugin_folder, match):
        detail = {
            "folder": plugin_folder,
            "source": match.get("source", ""),
            "detail": match.get("detail", ""),
        }
        for field in (
            "management_mode",
            "management_error",
            "release_metadata_state",
            "package_id",
            "repository_identity",
        ):
            if field in match:
                detail[field] = match[field]
        return detail

    def inspect_release_install_match(self, plugin_path):
        """Return discovery evidence from committed Release install metadata."""
        plugin_folder = os.path.basename(os.path.normpath(plugin_path))
        metadata_path = os.path.join(
            plugin_path,
            InstallMetadataService.FILE_NAME,
        )
        if not os.path.lexists(metadata_path):
            return None

        try:
            metadata = self.install_metadata_service.inspect(plugin_path)
        except ValueError:
            match = self.make_installed_plugin_match(
                "",
                "invalid release install metadata",
                0,
                (
                    "A committed Release metadata marker exists but could not "
                    "be validated."
                ),
            )
            match.update(
                {
                    "management_mode": "release",
                    "management_error": (
                        "Installed release metadata in folder "
                        + plugin_folder
                        + " is invalid or unreadable. Restore the verified "
                        "plugin backup or reinstall it before making changes."
                    ),
                    "release_metadata_state": "invalid",
                    "authoritative": False,
                }
            )
            return match

        if metadata is None:
            match = self.make_installed_plugin_match(
                "",
                "invalid release install metadata",
                0,
                (
                    "A committed Release metadata marker changed while the "
                    "plugin folder was being inspected."
                ),
            )
            match.update(
                {
                    "management_mode": "release",
                    "management_error": (
                        "Installed release metadata in folder "
                        + plugin_folder
                        + " changed during discovery. Refresh before making "
                        "changes."
                    ),
                    "release_metadata_state": "invalid",
                    "authoritative": False,
                }
            )
            return match

        package_id = metadata.package_id
        match = self.make_installed_plugin_match(
            "",
            "release install metadata",
            0,
            "Committed Release metadata identifies the installed package.",
        )
        match.update(
            {
                "management_mode": "release",
                "release_metadata_state": "valid",
                "package_id": package_id,
                "repository_identity": metadata.repository_identity,
                "authoritative": True,
            }
        )

        if package_id not in self.plugin_data:
            match.update(
                {
                    "source": "orphan release install metadata",
                    "detail": (
                        "Committed Release metadata references a package that "
                        "is not in the current registry."
                    ),
                    "management_error": (
                        "Installed release metadata in folder "
                        + plugin_folder
                        + " references package "
                        + package_id
                        + ", which is not present in the current registry. "
                        "Restore the registry entry before changing this plugin."
                    ),
                    "release_metadata_state": "orphan",
                }
            )
            return match

        entry = self.get_registry_entry(package_id)
        if entry is None:
            match.update(
                {
                    "source": "orphan release install metadata",
                    "detail": (
                        "Committed Release metadata references a registry "
                        "package whose entry is invalid."
                    ),
                    "management_error": (
                        "The registry entry referenced by installed release "
                        "metadata is invalid. Repair the registry entry before "
                        "changing this plugin."
                    ),
                    "release_metadata_state": "orphan",
                }
            )
            return match

        match["key"] = package_id
        errors = []
        expected_identity = normalize_repository_identity(
            entry.author,
            entry.repository,
        )
        if (
            not expected_identity
            or metadata.repository_identity != expected_identity
        ):
            errors.append(
                "Installed release metadata in folder "
                + plugin_folder
                + " does not match the current registry repository. Restore "
                "the matching registry entry or reinstall the plugin before "
                "changing it."
            )
            match["release_metadata_state"] = "repository_mismatch"
        if entry.local:
            errors.append(
                "Installed release metadata in folder "
                + plugin_folder
                + " belongs to a Git-only local registry entry. Restore the "
                "intended Git checkout or matching Release registry entry "
                "before changing it."
            )
            match["release_metadata_state"] = "local_registry_conflict"
        if os.path.lexists(os.path.join(plugin_path, ".git")):
            errors.append(
                "Folder "
                + plugin_folder
                + " contains both Release metadata and Git control data. "
                "Restore either the verified Release installation or the "
                "intended Git checkout before changing it."
            )
            match["release_metadata_state"] = "mixed"
        if errors:
            match["management_error"] = " ".join(errors)
            match["detail"] += " The folder has a conflicting management state."
        return match

    def merge_release_install_diagnostic(self, match, diagnostic):
        """Attach an invalid-marker diagnostic to a legacy discovery match."""
        if not diagnostic:
            return match
        if not match:
            return diagnostic
        for field in (
            "management_mode",
            "management_error",
            "release_metadata_state",
            "package_id",
            "repository_identity",
        ):
            if field in diagnostic:
                match[field] = diagnostic[field]
        diagnostic_detail = diagnostic.get("detail", "")
        if diagnostic_detail:
            match["detail"] = (
                match.get("detail", "").rstrip() + " " + diagnostic_detail
            ).strip()
        return match

    def registry_target_details(self, plugin_key):
        entry = self.get_registry_entry(plugin_key)
        if entry is None:
            return None

        clone_url = self.build_git_clone_url(entry.author, entry.repository)
        return {
            "repo": self.normalize_git_repo_identity(clone_url),
            "branch": entry.branch or "",
        }

    def installed_git_target_details(self, plugin_dir):
        current_branch = self.get_git_current_branch(plugin_dir)
        remote_name = self.get_git_remote_name(plugin_dir, current_branch)
        remote_url = self.get_git_remote_url(plugin_dir, remote_name)
        if not remote_url:
            remote_urls = self.get_git_remote_urls(plugin_dir)
            remote_url = remote_urls[0] if remote_urls else ""

        return {
            "repo": self.normalize_git_repo_identity(remote_url),
            "branch": current_branch,
        }

    def detect_installed_registry_mismatch(self, plugin_key, plugin_dir):
        if not plugin_key or not is_git_checkout(plugin_dir):
            return {}

        configured = self.registry_target_details(plugin_key)
        if configured is None:
            return {}

        installed = self.installed_git_target_details(plugin_dir)
        configured_repo = configured.get("repo", "")
        installed_repo = installed.get("repo", "")
        configured_branch = configured.get("branch", "")
        installed_branch = installed.get("branch", "")

        repo_mismatch = bool(configured_repo and installed_repo and configured_repo != installed_repo)
        branch_mismatch = bool(configured_branch and installed_branch and configured_branch != installed_branch)
        if not repo_mismatch and not branch_mismatch:
            return {}

        return {
            "registry_mismatch": True,
            "repo_mismatch": repo_mismatch,
            "branch_mismatch": branch_mismatch,
            "configured_repo": configured_repo,
            "configured_branch": configured_branch,
            "installed_repo": installed_repo,
            "installed_branch": installed_branch,
        }

    def installed_registry_mismatch(self, plugin_key, plugin_dir=None):
        details = self.installed_plugin_match_details.get(plugin_key, {})
        if details.get("registry_mismatch"):
            return True
        if details and "registry_mismatch" in details:
            return False
        if plugin_dir is None:
            return False
        return bool(self.detect_installed_registry_mismatch(plugin_key, plugin_dir).get("registry_mismatch"))

    def log_installed_plugin_match(self, plugin_folder, match):
        Domoticz.Debug(
            "Detected installed plugin "
            + match.get("key", "")
            + " in folder "
            + plugin_folder
            + " using "
            + match.get("source", "unknown source")
            + ". "
            + match.get("detail", "")
        )

    def match_lookup_candidate(self, plugin_folder, lookup_key, lookup, source, priority, detail, reason):
        if not lookup_key:
            return None

        matched_keys = lookup.get(lookup_key, [])
        if len(matched_keys) == 1:
            return self.make_installed_plugin_match(matched_keys[0], source, priority, detail)
        if len(matched_keys) > 1:
            Domoticz.Debug(
                "Could not use "
                + reason
                + " for folder "
                + plugin_folder
                + " because it matches multiple registry entries: "
                + ", ".join(matched_keys)
            )
        return None

    def match_unique_lookup_candidate(self, plugin_folder, lookup_key, lookup, reason):
        match = self.match_lookup_candidate(
            plugin_folder,
            lookup_key,
            lookup,
            reason,
            0,
            "",
            reason,
        )
        if match:
            return match["key"]
        return ""

    def match_unique_archive_match_candidate(self, plugin_folder, archive_name_lookup):
        return self.match_lookup_candidate(
            plugin_folder,
            self.normalize_plugin_folder_name(plugin_folder),
            archive_name_lookup,
            "repository/archive folder name",
            40,
            "Folder name matches a unique repository or GitHub archive folder name.",
            "folder name",
        )

    def match_unique_archive_candidate(self, plugin_folder, archive_name_lookup):
        match = self.match_unique_archive_match_candidate(plugin_folder, archive_name_lookup)
        if match:
            return match["key"]
        return ""

    def match_unique_flexible_folder_match_candidate(self, plugin_folder, flexible_name_lookup):
        return self.match_lookup_candidate(
            plugin_folder,
            self.normalize_plugin_metadata_value(plugin_folder),
            flexible_name_lookup,
            "normalized folder name",
            50,
            "Folder name matches after punctuation, spacing, case, and Domoticz affix normalization.",
            "normalized folder name",
        )

    def match_unique_flexible_folder_candidate(self, plugin_folder, flexible_name_lookup):
        match = self.match_unique_flexible_folder_match_candidate(plugin_folder, flexible_name_lookup)
        if match:
            return match["key"]
        return ""

    def match_metadata_externallink_candidate(self, plugin_folder, metadata, repo_identity_lookup):
        externallink_identity = self.normalize_github_repo_identity(metadata.get("externallink", ""))
        if not externallink_identity:
            return None

        match = self.match_lookup_candidate(
            plugin_folder,
            externallink_identity,
            repo_identity_lookup,
            "plugin.py externallink",
            20,
            "plugin.py externallink points to a unique registry repository.",
            "plugin externallink",
        )
        if match:
            return match

        if not repo_identity_lookup.get(externallink_identity, []):
            Domoticz.Debug("Could not use plugin.py externallink for folder " + plugin_folder + " because it does not match the registry.")
        return None

    def match_metadata_externallink(self, plugin_folder, metadata, repo_identity_lookup):
        match = self.match_metadata_externallink_candidate(plugin_folder, metadata, repo_identity_lookup)
        if match:
            return match["key"]
        return ""

    def match_metadata_names_candidate(self, plugin_folder, metadata, metadata_name_lookup):
        matched_keys = []
        matched_fields = []
        for metadata_field in ("key", "name"):
            metadata_key = self.normalize_plugin_metadata_value(metadata.get(metadata_field, ""))
            for matched_key in metadata_name_lookup.get(metadata_key, []):
                if matched_key not in matched_keys:
                    matched_keys.append(matched_key)
                    matched_fields.append(metadata_field)

        if len(matched_keys) == 1:
            return self.make_installed_plugin_match(
                matched_keys[0],
                "plugin.py key/name",
                60,
                "plugin.py " + "/".join(matched_fields) + " metadata matches a unique registry entry.",
            )
        if len(matched_keys) > 1:
            Domoticz.Debug(
                "Could not use plugin.py key/name metadata for folder "
                + plugin_folder
                + " because it matches multiple registry entries: "
                + ", ".join(matched_keys)
            )
        return None

    def match_metadata_names(self, plugin_folder, metadata, metadata_name_lookup):
        match = self.match_metadata_names_candidate(plugin_folder, metadata, metadata_name_lookup)
        if match:
            return match["key"]
        return ""

    def inferred_folder_match_is_valid(self, matched_key, plugin_path, metadata):
        if metadata is None:
            return True
        return self.plugin_metadata_matches_registry(matched_key, plugin_path, metadata)

    def add_valid_inferred_folder_candidate(self, candidates, rejected_keys, candidate, plugin_folder, plugin_path, metadata):
        if not candidate:
            return
        matched_key = candidate["key"]
        if matched_key in rejected_keys:
            return
        if self.inferred_folder_match_is_valid(matched_key, plugin_path, metadata):
            candidates.append(candidate)
            return

        rejected_keys.add(matched_key)
        Domoticz.Debug(
            "Skipped "
            + candidate.get("source", "folder")
            + " candidate "
            + matched_key
            + " for folder "
            + plugin_folder
            + " because plugin.py metadata does not confirm it; continuing with lower priority evidence."
        )

    def choose_installed_plugin_match(self, candidates):
        valid_candidates = [candidate for candidate in candidates if candidate]
        if not valid_candidates:
            return None
        authoritative_candidates = [
            candidate
            for candidate in valid_candidates
            if candidate.get("authoritative")
        ]
        if authoritative_candidates:
            authoritative_candidates.sort(
                key=lambda candidate: candidate.get("priority", 1000)
            )
            return authoritative_candidates[0]
        local_plugin_keys = set(self.local_plugin_keys)
        local_candidates = [candidate for candidate in valid_candidates if candidate.get("key") in local_plugin_keys]
        if local_candidates:
            valid_candidates = local_candidates
        valid_candidates.sort(key=lambda candidate: candidate.get("priority", 1000))
        return valid_candidates[0]

    def match_installed_plugin(
        self,
        plugin_folder,
        plugin_path,
        exact_name_lookup,
        archive_name_lookup,
        flexible_name_lookup,
        metadata_name_lookup,
        remote_lookup,
        repo_identity_lookup,
    ):
        candidates = []
        plugin_file = os.path.join(plugin_path, "plugin.py")
        is_git_repo = is_git_checkout(plugin_path)
        release_install_match = self.inspect_release_install_match(
            plugin_path
        )
        if release_install_match and release_install_match.get(
            "authoritative"
        ):
            if release_install_match.get("key"):
                self.log_installed_plugin_match(
                    plugin_folder,
                    release_install_match,
                )
            else:
                Domoticz.Debug(
                    "Release metadata for folder "
                    + plugin_folder
                    + " references a package outside the current registry."
                )
            return release_install_match

        remote_urls = self.get_git_remote_urls(plugin_path) if is_git_repo else []
        for remote_url in remote_urls:
            match = self.match_lookup_candidate(
                plugin_folder,
                self.normalize_git_remote_url(remote_url),
                remote_lookup,
                "git remote",
                10,
                "Git remote URL matches a unique registry repository.",
                "git remote",
            )
            if match:
                candidates.append(match)

        metadata = self.parse_domoticz_plugin_metadata(plugin_path) if os.path.isfile(plugin_file) else None
        if metadata is not None:
            candidates.append(self.match_metadata_externallink_candidate(plugin_folder, metadata, repo_identity_lookup))

        matched_key = exact_name_lookup.get(self.normalize_plugin_folder_name(plugin_folder), "")
        if matched_key:
            candidates.append(
                self.make_installed_plugin_match(
                    matched_key,
                    "exact folder key",
                    30,
                    "Folder name exactly matches a registry plugin key.",
                )
            )

        rejected_inferred_keys = set()
        self.add_valid_inferred_folder_candidate(
            candidates,
            rejected_inferred_keys,
            self.match_unique_archive_match_candidate(plugin_folder, archive_name_lookup),
            plugin_folder,
            plugin_path,
            metadata,
        )

        self.add_valid_inferred_folder_candidate(
            candidates,
            rejected_inferred_keys,
            self.match_unique_flexible_folder_match_candidate(plugin_folder, flexible_name_lookup),
            plugin_folder,
            plugin_path,
            metadata,
        )

        if metadata is not None:
            candidates.append(self.match_metadata_names_candidate(plugin_folder, metadata, metadata_name_lookup))
        elif not os.path.isfile(plugin_file) and not candidates:
            Domoticz.Debug("No registry match found for folder " + plugin_folder + " from git, folder, or metadata because plugin.py was not found.")

        match = self.choose_installed_plugin_match(candidates)
        match = self.merge_release_install_diagnostic(
            match,
            release_install_match,
        )
        if match:
            if match.get("key"):
                self.log_installed_plugin_match(plugin_folder, match)
            else:
                Domoticz.Debug(
                    "No registry match found for folder "
                    + plugin_folder
                    + "; its Release metadata is invalid."
                )
        else:
            Domoticz.Debug("No registry match found for folder " + plugin_folder + ".")
        return match

    def match_installed_plugin_key(
        self,
        plugin_folder,
        plugin_path,
        exact_name_lookup,
        archive_name_lookup,
        flexible_name_lookup,
        metadata_name_lookup,
        remote_lookup,
        repo_identity_lookup,
    ):
        match = self.match_installed_plugin(
            plugin_folder,
            plugin_path,
            exact_name_lookup,
            archive_name_lookup,
            flexible_name_lookup,
            metadata_name_lookup,
            remote_lookup,
            repo_identity_lookup,
        )
        if match:
            return match["key"]
        return ""

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

    def _fetch_release_metadata_bytes(self, url, label, max_bytes):
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=5) as response:
            if getattr(response, "status", None) != 200:
                raise ValueError(
                    label
                    + " request failed with status "
                    + str(getattr(response, "status", "unknown"))
                    + "."
                )
            contents = response.read()
        if not isinstance(contents, bytes) or not contents:
            raise ValueError(label + " response is empty.")
        if len(contents) > max_bytes:
            raise ValueError(label + " response exceeds its size limit.")
        return contents

    def refreshReleaseMetadata(self):
        """Accept a complete remote pair or retain the last trusted pair."""
        store = self.getReleaseMetadataStore()
        try:
            registry_bytes = self._fetch_release_metadata_bytes(
                self.get_registry_url(),
                "registry.json",
                10 * 1024 * 1024,
            )
            index_bytes = self._fetch_release_metadata_bytes(
                self.get_release_index_url(),
                "release_index.json",
                10 * 1024 * 1024,
            )
            try:
                selection = store.accept_remote(
                    registry_bytes, index_bytes
                )
            except ValueError:
                selection = store.load()
        except Exception as error:
            Domoticz.Debug(
                "Could not refresh release metadata pair: " + str(error)
            )
            selection = store.load()
        self.release_metadata_selection = selection
        return selection

    def load_bundled_registry(self):
        return self.load_registry_file(self.get_bundled_registry_file(), "bundled", True)

    def load_local_registry(self):
        document = self.local_registry_service.read_document()
        self.local_registry_error = ""
        if not document.exists:
            Domoticz.Debug("No local registry file found.")
            return None
        if not document.writable:
            self.local_registry_error = (
                document.message or "Local registry could not be loaded."
            )
            Domoticz.Error(
                "Error reading local registry file: " + document.message
            )
            return None
        Domoticz.Log("Loaded local plugin registry schema v2.")
        return document.entries

    def getReleaseMetadataAuthorization(self, selection):
        """Pause Release changes while an existing local registry is invalid."""
        if self.local_registry_error:
            return (
                False,
                "Local registry could not be loaded; Release management is "
                "paused until registry_local.json is repaired.",
            )
        return selection.release_authorized, selection.reason

    def getLocalOverrideGitCheckoutError(self, entry, plugin_dir):
        """Explain why a Release install cannot follow a Local override."""
        if not entry.local:
            return ""
        metadata_path = os.path.join(
            plugin_dir, InstallMetadataService.FILE_NAME
        )
        if not os.path.lexists(metadata_path):
            return ""
        if is_git_checkout(plugin_dir):
            return ""
        return (
            "This Local registry override requires a Git checkout. "
            "Restore a verified Git backup with Rollback, or remove and "
            "reinstall the plugin."
        )

    def load_update_times_file(self, update_times_file, label):
        if not os.path.isfile(update_times_file):
            Domoticz.Debug("No " + label + " update_times file found.")
            return {}

        try:
            with open(update_times_file, "rb") as update_times_input:
                contents = update_times_input.read()
            document = _load_json_object(
                contents,
                label + " update_times file",
            )
            update_times = self.normalize_update_times_document(document)
            Domoticz.Debug("Loaded update times from " + label + " file.")
            return update_times
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
            document = UpdateTimesDocument.from_legacy_document(
                dict(update_times)
            ).to_document()
            self.get_host().write_json_atomic(
                update_times_file,
                document,
                sort_keys=False,
            )
            Domoticz.Log("Updated update_times.cache.json.")
            return True
        except Exception as e:
            Domoticz.Error("Error writing update_times.cache.json: " + str(e))
            return False

    def normalize_update_times_document(self, document):
        if not isinstance(document, dict):
            raise ValueError("update_times must contain a JSON object.")
        if "schema_version" in document:
            parsed = UpdateTimesDocument.from_document(document)
        else:
            parsed = UpdateTimesDocument.from_legacy_document(document)
        return dict(parsed.by_package_id)

    def load_update_times(self):
        update_times = {}
        Domoticz.Debug("Fetching remote update times.")
        try:
            req = urllib.request.Request(self.get_update_times_url())
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    document = _load_json_object(
                        response.read(),
                        "remote update_times.json",
                    )
                    update_times = self.normalize_update_times_document(
                        document
                    )
                    Domoticz.Log("Successfully fetched remote update times.")
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

    def apply_update_times(self, update_times, include_local=False):
        self.update_times = update_times
        for key, data in self.plugin_data.items():
            if key == "Idle":
                continue
            if key in self.local_plugin_keys and not include_local:
                continue

            updated_at = update_times.get(key, "")
            entry = self.get_registry_entry(key)
            if entry is not None:
                entry.updated_at = updated_at
                entry.updated_at_slot_present = True

            if not isinstance(data, list):
                continue

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
        is_local = plugin_key in self.local_plugin_keys
        if not is_local and not self.is_better_update_time(updated_at, current_updated_at):
            return False
        if is_local and current_updated_at == updated_at:
            entry = self.get_registry_entry(plugin_key)
            data = self.plugin_data.get(plugin_key)
            data_updated_at = data[4] if isinstance(data, list) and len(data) >= 5 else ""
            entry_updated_at = entry.updated_at if entry is not None else ""
            if data_updated_at == updated_at and entry_updated_at == updated_at:
                return False

        update_times[plugin_key] = updated_at

        data = self.plugin_data.get(plugin_key)
        if isinstance(data, list):
            if len(data) == 4:
                data.append(updated_at)
            elif len(data) >= 5:
                data[4] = updated_at
        entry = self.get_registry_entry(plugin_key)
        if entry is not None:
            entry.updated_at = updated_at
            entry.updated_at_slot_present = True

        Domoticz.Log("Git update date changed for " + plugin_key + ": " + updated_at)
        if remote_url:
            Domoticz.Debug("Update date source for " + plugin_key + ": " + remote_url + " " + remote_ref)
        return True

    def run_git_command(self, plugin_dir, command, timeout=15):
        return self.get_host().run_git(command, plugin_dir, timeout=timeout)

    def fetch_git_repo(self, plugin_dir):
        result = self.run_git_command(plugin_dir, ["git", "fetch", "--quiet"], timeout=15)
        if result is None:
            return False
        self.get_host().log_git_result("Fetch", result)
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

    def get_configured_git_remote_ref(self, plugin_key, plugin_dir):
        entry = self.get_registry_entry(plugin_key)
        if entry is None or not entry.branch:
            return ""

        remote_name = self.get_git_remote_name(plugin_dir, entry.branch)
        branch_ref = remote_name + "/" + entry.branch
        if self.git_ref_exists(plugin_dir, branch_ref):
            return branch_ref
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
        if not plugin_key or not is_git_checkout(plugin_dir):
            return False
        if self.installed_registry_mismatch(plugin_key, plugin_dir):
            return False

        if not remote_ref:
            remote_ref = self.get_configured_git_remote_ref(plugin_key, plugin_dir) or self.get_git_remote_ref(plugin_dir)

        updated_at = self.get_git_remote_commit_date(plugin_dir, remote_ref)
        if not updated_at:
            return False

        remote_name = remote_ref.split("/", 1)[0] if "/" in remote_ref else "origin"
        remote_url = self.get_git_remote_url(plugin_dir, remote_name)
        return self.overlay_git_update_time(plugin_key, updated_at, update_times, remote_url, remote_ref)

    def refresh_single_plugin_update_time(self, plugin_key, plugin_dir, fetch_first=True):
        if not is_git_checkout(plugin_dir):
            return False

        if fetch_first and not self.fetch_git_repo(plugin_dir):
            return False

        update_times = dict(self.update_times) if self.update_times else self.load_cached_update_times()
        changed = self.refresh_git_update_time(plugin_key, plugin_dir, update_times)
        if changed:
            self.save_update_times_cache(update_times)
            self.apply_update_times(update_times, include_local=True)
        return changed

    def add_installed_plugin(self, installed_plugins, plugin_key):
        if plugin_key and plugin_key not in installed_plugins:
            installed_plugins.append(plugin_key)

    def getInstalledPlugins(self, plugins_dir):
        """Scan only while Release activation paths are filesystem-stable."""
        configured_plugins_dir = self.get_host().plugins_dir()
        if (
            os.path.normcase(os.path.realpath(str(plugins_dir)))
            != os.path.normcase(os.path.realpath(configured_plugins_dir))
        ):
            return self._get_installed_plugins_unlocked(
                plugins_dir,
                publish=False,
            )

        lock_entered = False
        try:
            with self.release_transaction_manager.operation_lock(
                blocking=False
            ):
                lock_entered = True
                return self._get_installed_plugins_unlocked(plugins_dir)
        except (OSError, RuntimeError, ValueError) as error:
            error_detail = str(error).strip() or type(error).__name__
            if not lock_entered:
                if self.installed_plugins_snapshot_available:
                    installed_plugins = list(
                        self.installed_plugins_snapshot
                    )
                else:
                    try:
                        installed_plugins = (
                            self._get_installed_plugins_unlocked(
                                plugins_dir,
                                publish=False,
                            )
                        )
                    except (OSError, RuntimeError, ValueError):
                        installed_plugins = []
                self.installed_plugin_scan_error = (
                    "Installed plugins could not be scanned with transaction "
                    "coordination: "
                    + error_detail
                    + ". Wait for the current operation or restore access to "
                    "the .pypluginstore manager state, then Refresh. The last "
                    "trusted snapshot is retained and plugin changes are "
                    "paused."
                )
                Domoticz.Error(self.installed_plugin_scan_error)
                return installed_plugins

            self.installed_plugin_scan_error = (
                "Installed plugin discovery failed: "
                + error_detail
                + ". Check the plugins folder and .pypluginstore manager "
                "state, then Refresh. The last successful installed-plugin "
                "snapshot is retained and plugin changes are paused."
            )
            Domoticz.Error(
                self.installed_plugin_scan_error
            )
            return list(self.installed_plugins_snapshot)

    def assert_release_install_absent_locked(self, plugin_key):
        """Revalidate install absence while the transaction lock is held."""
        plugin_key = self.get_host().validate_plugin_key(plugin_key)
        plugins_dir = self.get_host().plugins_dir()
        installed_plugins = self._get_installed_plugins_unlocked(
            plugins_dir
        )
        if self.installed_plugin_scan_error:
            raise RuntimeError(self.installed_plugin_scan_error)
        conflict = self.installed_plugin_install_conflicts.get(
            plugin_key,
            {},
        ).get("management_error", "")
        if conflict:
            raise ValueError(conflict)
        ambiguous = self.ambiguous_installed_plugin_folders.get(
            plugin_key,
            [],
        )
        if ambiguous:
            raise ValueError(
                "Multiple installed folders match "
                + plugin_key
                + ": "
                + ", ".join(ambiguous)
                + "."
            )
        if (
            plugin_key in installed_plugins
            or plugin_key in self.installed_plugin_folders
            or os.path.lexists(
                self.get_host().resolve_plugin_dir(plugin_key)
            )
        ):
            raise ValueError(
                "Plugin "
                + plugin_key
                + " is already installed or claimed by an existing folder."
            )

    def _get_installed_plugins_unlocked(self, plugins_dir, *, publish=True):
        installed_plugins = []
        installed_plugin_folders = {}
        ambiguous_installed_plugin_folders = {}
        installed_plugin_match_details = {}
        installed_plugin_install_conflicts = {}
        (
            exact_name_lookup,
            archive_name_lookup,
            flexible_name_lookup,
            metadata_name_lookup,
            remote_lookup,
            repo_identity_lookup,
        ) = self.build_installed_plugin_lookup()
        all_repo_identity_lookup = (
            self.build_registry_repository_identity_lookup()
        )

        try:
            plugin_folders = sorted(
                os.listdir(plugins_dir),
                key=lambda value: str(value).casefold(),
            )
        except Exception as e:
            Domoticz.Error("Could not scan Domoticz plugins folder: " + str(e))
            if publish:
                self.installed_plugin_scan_error = (
                    "Could not scan the Domoticz plugins folder: "
                    + str(e)
                    + ". Restore folder access, then Refresh. Plugin changes "
                    "are paused."
                )
                return list(self.installed_plugins_snapshot)
            return []

        if publish:
            self.installed_plugin_scan_error = ""
        for plugin_folder in plugin_folders:
            plugin_path = os.path.join(plugins_dir, plugin_folder)
            if not os.path.isdir(plugin_path) or plugin_folder.startswith("."):
                continue

            is_git_repo = is_git_checkout(plugin_path)
            match = self.match_installed_plugin(
                plugin_folder,
                plugin_path,
                exact_name_lookup,
                archive_name_lookup,
                flexible_name_lookup,
                metadata_name_lookup,
                remote_lookup,
                repo_identity_lookup,
            )
            if (
                match
                and match.get("authoritative")
                and match.get("management_mode") == "release"
            ):
                conflict_reasons = {}
                repository_identity = match.get("repository_identity", "")
                for conflict_key in all_repo_identity_lookup.get(
                    repository_identity,
                    [],
                ):
                    conflict_reasons.setdefault(conflict_key, set()).add(
                        "repository identity"
                    )
                folder_owner = exact_name_lookup.get(
                    self.normalize_plugin_folder_name(plugin_folder),
                    "",
                )
                if folder_owner:
                    conflict_reasons.setdefault(folder_owner, set()).add(
                        "package folder"
                    )
                matched_key = match.get("key", "")
                for conflict_key, reasons in conflict_reasons.items():
                    if conflict_key == matched_key:
                        continue
                    for reason in sorted(reasons):
                        self.add_release_install_conflict(
                            installed_plugin_install_conflicts,
                            conflict_key,
                            plugin_folder,
                            match,
                            reason,
                        )
            matched_key = match["key"] if match else ""
            if matched_key:
                self.add_installed_plugin(installed_plugins, matched_key)
                detail = self.match_detail_for_response(plugin_folder, match)
                detail["is_git"] = is_git_repo
                if is_git_repo:
                    mismatch = self.detect_installed_registry_mismatch(matched_key, plugin_path)
                    if mismatch:
                        detail.update(mismatch)
                matching_folders = set(
                    ambiguous_installed_plugin_folders.get(matched_key, [])
                )
                existing_folder = installed_plugin_folders.get(
                    matched_key, ""
                )
                if existing_folder:
                    matching_folders.add(existing_folder)
                matching_folders.add(plugin_folder)
                if len(matching_folders) > 1:
                    previous_detail = installed_plugin_match_details.get(
                        matched_key,
                        {},
                    )
                    folders = sorted(
                        matching_folders,
                        key=lambda value: value.casefold(),
                    )
                    ambiguous_installed_plugin_folders[matched_key] = folders
                    installed_plugin_folders.pop(matched_key, None)
                    installed_plugin_match_details[matched_key] = {
                        "folder": "",
                        "folders": folders,
                        "source": "ambiguous physical folders",
                        "detail": (
                            "Multiple physical folders match this registry "
                            "plugin: "
                            + ", ".join(folders)
                            + ". Remove or rename duplicates before changing "
                            "it."
                        ),
                        "is_git": bool(
                            is_git_repo
                            or previous_detail.get("is_git")
                        ),
                        "management_mode": (
                            "release"
                            if (
                                detail.get("management_mode") == "release"
                                or previous_detail.get("management_mode")
                                == "release"
                            )
                            else "git"
                        ),
                    }
                else:
                    installed_plugin_folders[matched_key] = plugin_folder
                    installed_plugin_match_details[matched_key] = detail
                if plugin_folder not in self.plugin_data:
                    self.add_installed_plugin(installed_plugins, plugin_folder)
                    installed_plugin_folders[plugin_folder] = plugin_folder
                    alias_detail = {
                        "folder": plugin_folder,
                        "source": "local folder alias",
                        "detail": "Physical folder is also listed because it is not a registry plugin key.",
                        "is_git": is_git_repo,
                    }
                    for field in (
                        "management_mode",
                        "management_error",
                        "release_metadata_state",
                        "package_id",
                        "repository_identity",
                    ):
                        if field in detail:
                            alias_detail[field] = detail[field]
                    installed_plugin_match_details[plugin_folder] = alias_detail
            elif plugin_folder not in self.plugin_data:
                self.add_installed_plugin(installed_plugins, plugin_folder)
                installed_plugin_folders[plugin_folder] = plugin_folder
                if match:
                    detail = self.match_detail_for_response(
                        plugin_folder,
                        match,
                    )
                    detail["is_git"] = is_git_repo
                    installed_plugin_match_details[plugin_folder] = detail
                else:
                    installed_plugin_match_details[plugin_folder] = {
                        "folder": plugin_folder,
                        "source": "local folder",
                        "detail": "No registry match was found; folder is listed as a local plugin.",
                        "is_git": is_git_repo,
                    }

        if publish:
            self.installed_plugin_folders = installed_plugin_folders
            self.ambiguous_installed_plugin_folders = (
                ambiguous_installed_plugin_folders
            )
            self.installed_plugin_match_details = (
                installed_plugin_match_details
            )
            self.installed_plugin_install_conflicts = (
                installed_plugin_install_conflicts
            )
            self.installed_plugins_snapshot = list(installed_plugins)
            self.installed_plugins_snapshot_available = True
        return installed_plugins

    def get_installed_plugin_folder(
        self,
        plugin_key,
        plugins_dir=None,
        *,
        refresh=False,
    ):
        plugin_key = self.get_host().validate_plugin_key(plugin_key)
        if plugins_dir is None:
            plugins_dir = self.get_host().plugins_dir()

        if refresh:
            self.getInstalledPlugins(plugins_dir)
            if self.installed_plugin_scan_error:
                raise ValueError(
                    "Installed plugin folders could not be scanned safely."
                )
        ambiguous_folders = self.ambiguous_installed_plugin_folders.get(
            plugin_key, []
        )
        if ambiguous_folders:
            raise ValueError(
                "Multiple installed folders match "
                + plugin_key
                + ": "
                + ", ".join(ambiguous_folders)
                + ". Remove or rename duplicates before continuing."
            )
        install_conflict = self.installed_plugin_install_conflicts.get(
            plugin_key,
            {},
        ).get("management_error", "")
        if install_conflict:
            raise ValueError(install_conflict)
        management_error = self.installed_plugin_match_details.get(
            plugin_key,
            {},
        ).get("management_error", "")
        if management_error:
            raise ValueError(management_error)
        plugin_folder = self.installed_plugin_folders.get(plugin_key, "")
        if plugin_folder and os.path.isdir(os.path.join(plugins_dir, plugin_folder)):
            return plugin_folder

        self.getInstalledPlugins(plugins_dir)
        if self.installed_plugin_scan_error:
            raise ValueError(
                "Installed plugin folders could not be scanned safely."
            )
        ambiguous_folders = self.ambiguous_installed_plugin_folders.get(
            plugin_key, []
        )
        if ambiguous_folders:
            raise ValueError(
                "Multiple installed folders match "
                + plugin_key
                + ": "
                + ", ".join(ambiguous_folders)
                + ". Remove or rename duplicates before continuing."
            )
        install_conflict = self.installed_plugin_install_conflicts.get(
            plugin_key,
            {},
        ).get("management_error", "")
        if install_conflict:
            raise ValueError(install_conflict)
        management_error = self.installed_plugin_match_details.get(
            plugin_key,
            {},
        ).get("management_error", "")
        if management_error:
            raise ValueError(management_error)
        return self.installed_plugin_folders.get(plugin_key, plugin_key)

    def resolve_installed_plugin_dir(
        self,
        plugin_key,
        plugins_dir=None,
        *,
        refresh=False,
    ):
        host = self.get_host()
        plugin_key = host.validate_plugin_key(plugin_key)
        if plugins_dir is None:
            plugins_dir = host.plugins_dir()

        plugins_dir = os.path.abspath(plugins_dir)
        plugin_folder = host.validate_plugin_key(
            self.get_installed_plugin_folder(
                plugin_key,
                plugins_dir,
                refresh=refresh,
            )
        )
        plugin_dir = os.path.abspath(os.path.join(plugins_dir, plugin_folder))
        if not host.is_path_inside(plugin_dir, plugins_dir):
            raise ValueError("Invalid plugin path")
        return plugin_dir

    def installed_plugin_diagnostic_dir(self, plugin_key, plugins_dir=None):
        """Return a verified physical folder for read-only diagnostics."""
        host = self.get_host()
        try:
            plugin_key = host.validate_plugin_key(plugin_key)
            plugin_folder = host.validate_plugin_key(
                self.installed_plugin_match_details.get(
                    plugin_key,
                    {},
                ).get("folder", "")
            )
        except ValueError:
            return ""
        if plugins_dir is None:
            plugins_dir = host.plugins_dir()
        plugins_root = os.path.abspath(plugins_dir)
        candidate_dir = os.path.abspath(
            os.path.join(plugins_root, plugin_folder)
        )
        if (
            not host.is_path_inside(candidate_dir, plugins_root)
            or not os.path.isdir(candidate_dir)
            or os.path.islink(candidate_dir)
        ):
            return ""
        return candidate_dir

    def getCachedUpdateStatuses(self, installed_plugins):
        update_status = {}
        for plugin_key in installed_plugins:
            if plugin_key not in self.plugin_data:
                update_status[plugin_key] = "unknown"
                continue
            if self.installed_registry_mismatch(plugin_key):
                update_status[plugin_key] = "mismatch"
                continue
            update_status[plugin_key] = self.update_status.get(plugin_key, "unknown")
        return update_status

    def get_plugin_versions(self, installed_plugins, update_status, plugins_dir):
        if self.installed_plugin_scan_error:
            return {}
        versions = {}
        for plugin_key in installed_plugins:
            entry = self.get_registry_entry(plugin_key)
            if entry is None:
                continue

            try:
                plugin_dir = self.resolve_installed_plugin_dir(plugin_key, plugins_dir)
            except ValueError:
                continue

            local_meta = self.parse_domoticz_plugin_metadata(plugin_dir)
            installed_version = local_meta.get("version") if local_meta else None
            if (
                plugin_key == self.get_current_plugin_folder()
                and self.manager_identity_service is not None
            ):
                runtime_identity = (
                    self.manager_identity_service.capture_runtime()
                )
                if _manager_identity_is_valid(runtime_identity):
                    installed_version = runtime_identity["product_version"]
            available_version = None

            if update_status.get(plugin_key) == "available":
                url = self.build_raw_plugin_url(entry.author, entry.repository, entry.branch)
                if not url:
                    Domoticz.Debug("Could not build remote version URL for " + str(entry.repository))
                    continue
                try:
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=5) as response:
                        source_code = response.read().decode('utf-8', errors='ignore')
                        plugin_tag = re.search(r"<plugin\b([^>]*)>", source_code, re.IGNORECASE | re.DOTALL)
                        if plugin_tag:
                            for match in re.finditer(r"([A-Za-z_][\w:.-]*)\s*=\s*(\"([^\"]*)\"|'([^']*)')", plugin_tag.group(1)):
                                if match.group(1).lower() == 'version':
                                    val = match.group(3) if match.group(3) is not None else match.group(4)
                                    available_version = html.unescape(val or "")
                                    break
                except Exception as e:
                    Domoticz.Debug(f"Could not fetch remote version for {entry.repository}: {e}")

            if installed_version or available_version:
                versions[plugin_key] = {}
                if installed_version:
                    versions[plugin_key]["installed"] = installed_version
                if available_version:
                    versions[plugin_key]["available"] = available_version
        return versions

    def blocked_plugin_management_state(
        self,
        entry,
        reason,
        release,
        version_info,
        channel,
    ):
        """Return one compatible management record with all changes blocked."""
        return {
            "channel": channel,
            "status": "verification_failed",
            "updateable": False,
            "installed_version": str(
                version_info.get("installed") or ""
            ),
            "installed_revision": None,
            "available_version": (
                release.version
                if release is not None
                else str(version_info.get("available") or "")
            ),
            "available_revision": (
                release.revision if release is not None else None
            ),
            "verification_status": "failed",
            "verification_message": str(reason),
            "migration_status": "not_available",
            "migration_message": "",
            "rollback_available": False,
            "rollback_version": "",
            "rollback_revision": None,
            "restart_pending": False,
            "git_supported": entry.delivery.git_supported,
            "release_available": release is not None,
            "migration_action_state": "blocked",
        }

    def getPluginManagementMap(
        self,
        installed_plugins,
        update_status,
        versions,
        plugins_dir,
    ):
        """Return the forge-neutral management extension for API clients."""
        management = {}
        manager_key = self.get_current_plugin_folder()
        for plugin_key in installed_plugins:
            if plugin_key == manager_key:
                continue
            entry = self.get_registry_entry(plugin_key)
            if entry is None:
                continue
            selection = self.getCurrentReleaseMetadataSelection()
            metadata_authorized, metadata_reason = (
                self.getReleaseMetadataAuthorization(selection)
            )
            release_index = (
                selection.release_index
                if metadata_authorized
                else None
            )
            release = (
                release_index.plugins.get(plugin_key)
                if release_index is not None and not entry.local
                else None
            )
            tombstone = (
                release_index.tombstones.get(plugin_key)
                if release_index is not None and not entry.local
                else None
            )
            match_detail = self.installed_plugin_match_details.get(
                plugin_key,
                {},
            )
            channel = (
                "release"
                if match_detail.get("management_mode") == "release"
                else "git"
            )
            version_info = versions.get(plugin_key, {})
            if self.installed_plugin_scan_error:
                management[plugin_key] = (
                    self.blocked_plugin_management_state(
                        entry,
                        self.installed_plugin_scan_error,
                        release,
                        version_info,
                        channel,
                    )
                )
                continue
            try:
                plugin_dir = self.resolve_installed_plugin_dir(
                    plugin_key, plugins_dir
                )
            except ValueError as error:
                plugin_dir = ""
                if (
                    match_detail.get("release_metadata_state")
                    == "local_registry_conflict"
                ):
                    plugin_dir = self.installed_plugin_diagnostic_dir(
                        plugin_key,
                        plugins_dir,
                    )
                if not plugin_dir:
                    management[plugin_key] = (
                        self.blocked_plugin_management_state(
                            entry,
                            error,
                            release,
                            version_info,
                            channel,
                        )
                    )
                    continue
            metadata = None
            metadata_invalid = ""
            metadata_path = os.path.join(
                plugin_dir, InstallMetadataService.FILE_NAME
            )
            if os.path.lexists(metadata_path):
                try:
                    metadata = self.install_metadata_service.read(plugin_dir)
                except OSError:
                    metadata_invalid = (
                        "metadata could not be upgraded or cleaned up safely"
                    )
                except ValueError as error:
                    metadata_invalid = str(error)
            is_release = metadata is not None or bool(metadata_invalid)
            is_git = is_git_checkout(plugin_dir)
            channel = "release" if is_release else "git"
            local_override_git_error = (
                self.getLocalOverrideGitCheckoutError(entry, plugin_dir)
            )
            preference = None
            identity = normalize_repository_identity(
                entry.author, entry.repository
            )
            if identity and not entry.local:
                try:
                    preference = self.channel_preference_service.get(identity)
                except ValueError:
                    preference = None
            try:
                lifecycle = self.release_transaction_manager.plugin_lifecycle_state(
                    plugin_key
                )
            except (OSError, RuntimeError, ValueError) as error:
                Domoticz.Error(
                    "Could not verify release lifecycle state for "
                    + plugin_key
                    + ": "
                    + str(error)
                )
                lifecycle = {
                    "rollback_available": False,
                    "rollback_version": "",
                    "rollback_revision": None,
                    "restart_pending": False,
                }

            migration_action_state = "blocked"
            decision_context = {
                "installed_mode": ("release" if is_release else "git"),
                "release": release,
                "tombstone": tombstone,
                "metadata_authorized": metadata_authorized,
                "metadata_reason": metadata_reason,
                "installed_release": metadata,
                "channel_preference": preference,
                "downgrade_confirmed": False,
                "release_was_activated": is_release,
                "git_status": update_status.get(plugin_key, "unknown"),
                "index_sequence": selection.sequence,
            }
            if metadata_invalid:
                status = "verification_failed"
                reason = "Installed release metadata is invalid: " + metadata_invalid
            elif local_override_git_error:
                status = "local_override_requires_git_checkout"
                reason = local_override_git_error
            else:
                status_context = dict(decision_context)
                status_context.pop("index_sequence")
                decision = self.install_update_strategy.decide(
                    entry,
                    operation="status",
                    **status_context,
                )
                status = decision.status
                reason = decision.reason
                if is_git and release is not None:
                    try:
                        switch_plan = self._plan_release_switch(
                            entry,
                            decision_context,
                            "manual",
                        )
                    except (
                        OSError,
                        ReleasePreservationError,
                        RuntimeError,
                        ValueError,
                    ) as error:
                        switch_plan = ReleaseSwitchPlan(
                            "blocked",
                            str(error),
                        )
                    migration_action_state = switch_plan.action_state
                    if decision.route == "release_migration":
                        if switch_plan.action_state == "confirmation_required":
                            status = "migration_confirmation_required"
                            reason = switch_plan.message
                        elif switch_plan.preflight is not None:
                            status = switch_plan.preflight.status
                            if status not in {
                                "migration_available",
                                "migration_blocked_local_changes",
                                "migration_blocked_local_files",
                                "migration_waiting_for_release",
                            }:
                                status = "migration_waiting_for_release"
                            reason = switch_plan.message
                        else:
                            status = "migration_waiting_for_release"
                            reason = switch_plan.message
                    elif (
                        decision.status == "migration_waiting_for_release"
                        and switch_plan.message
                    ):
                        reason = switch_plan.message

            installed_version = (
                metadata.version
                if metadata is not None
                else str(version_info.get("installed") or "")
            )
            installed_revision = (
                metadata.release_revision
                if metadata is not None
                else None
            )
            available_version = (
                release.version
                if release is not None
                else str(version_info.get("available") or "")
            )
            available_revision = (
                release.revision if release is not None else None
            )
            migration_status = (
                status
                if status.startswith("migration_")
                else ("not_applicable" if is_release else "not_available")
            )
            release_blocked = status in {
                "release_metadata_unavailable",
                "verification_failed",
                "local_override_requires_git_checkout",
                "migration_blocked_local_changes",
                "migration_blocked_local_files",
                "migration_waiting_for_release",
                "migration_confirmation_required",
            }
            management[plugin_key] = {
                "channel": channel,
                "status": status,
                "updateable": bool(
                    (is_release and not release_blocked)
                    or (is_git and not release_blocked)
                ),
                "installed_version": installed_version,
                "installed_revision": installed_revision,
                "available_version": available_version,
                "available_revision": available_revision,
                "verification_status": (
                    "failed"
                    if status == "verification_failed"
                    else (
                        "unavailable"
                        if status == "release_metadata_unavailable"
                        else (
                            "verified"
                            if release is not None
                            else "not_applicable"
                        )
                    )
                ),
                "verification_message": reason,
                "migration_status": migration_status,
                "migration_message": reason if migration_status.startswith("migration_") else "",
                "rollback_available": lifecycle["rollback_available"],
                "rollback_version": lifecycle["rollback_version"],
                "rollback_revision": lifecycle["rollback_revision"],
                "restart_pending": lifecycle["restart_pending"],
                "git_supported": entry.delivery.git_supported,
                "release_available": release is not None,
                "migration_action_state": migration_action_state,
            }
        return management

    def refresh_single_plugin_update_status(self, plugin_key, plugin_dir, fetch_first=True):
        return self.update_status_service.refresh_single_plugin_update_status(plugin_key, plugin_dir, fetch_first)

    def refreshInstalledUpdateStatuses(self, installed_plugins=None, plugins_dir=None):
        return self.update_status_service.refresh_installed_update_statuses(installed_plugins, plugins_dir)

    def get_managed_installed_plugin_keys(self, plugins_dir=None):
        if plugins_dir is None:
            plugins_dir = self.get_host().plugins_dir()

        managed_plugins = []
        for plugin_key in self.getInstalledPlugins(plugins_dir):
            if plugin_key in self.plugin_data and plugin_key not in managed_plugins:
                managed_plugins.append(plugin_key)
        return managed_plugins

    def add_self_to_registry(self):
        self_key = self.get_current_plugin_folder()
        if not self_key:
            return

        entry = RegistryEntry(
            self_key,
            "adrighem",
            "PyPluginStore",
            "PyPluginStore plugin manager",
            "master",
            self.update_times.get(self_key, ""),
            ["linux", "windows"],
            True,
        )
        self.registry_entries[self_key] = entry
        self.plugin_data[self_key] = entry.to_legacy_list()
        self.plugin_platforms[self_key] = entry.platforms

    def normalize_platforms(self, platforms):
        if not platforms:
            return ["unknown"]
        if isinstance(platforms, str):
            platforms = [platforms]
        if not isinstance(platforms, list):
            return ["unknown"]

        normalized = []
        for platform_name in platforms:
            platform_name = str(platform_name or "").strip().lower()
            if platform_name in ("linux", "windows") and platform_name not in normalized:
                normalized.append(platform_name)

        return normalized or ["unknown"]

    def normalize_registry_entry(self, key, data):
        entry, platforms = self.registry_service.normalize_entry(key, data)
        if entry is None:
            return None, platforms
        return entry.to_legacy_list(), platforms

    def normalize_registry(self, registry):
        normalized_registry, plugin_platforms, registry_entries = self.registry_service.normalize_registry(registry)
        self.registry_entries = registry_entries
        return normalized_registry, plugin_platforms

    def apply_registry_sources(self, public_registry, local_registry):
        public_registry = self.registry_service.records_from_document(
            public_registry or {}
        )
        local_registry = dict(local_registry or {})
        merged_registry = dict(public_registry)
        merged_registry.update(local_registry)
        local_plugin_keys = sorted(local_registry.keys())

        (
            self.plugin_data,
            self.plugin_platforms,
            self.registry_entries,
        ) = self.registry_service.normalize_registry(
            merged_registry,
            local_plugin_keys,
        )
        self.local_plugin_keys = local_plugin_keys
        if local_plugin_keys:
            Domoticz.Log(
                "Merged "
                + str(len(local_plugin_keys))
                + " local plugin registry entries."
            )

    def reapply_local_registry(self, local_registry):
        if self.public_registry_data is None:
            self.fetch_registry()
            return

        self.apply_registry_sources(
            self.public_registry_data,
            local_registry,
        )
        self.apply_update_times(self.update_times, include_local=False)
        self.add_self_to_registry()

    def fetch_registry(self):
        selection = self.refreshReleaseMetadata()
        registry = None
        if selection.registry_bytes and selection.sequence > 0:
            try:
                registry = _load_json_object(
                    selection.registry_bytes,
                    "selected registry.json",
                )
            except ValueError as error:
                Domoticz.Error(
                    "Selected release registry is invalid: " + str(error)
                )
        if registry is None:
            registry = self.fetch_remote_registry()
        if registry is None:
            registry = self.load_bundled_registry()

        local_registry = self.load_local_registry()
        registry_loaded = registry is not None or local_registry is not None

        if registry_loaded:
            if registry is not None:
                try:
                    self.public_registry_data = (
                        self.registry_service.records_from_document(registry)
                    )
                except ValueError as error:
                    Domoticz.Error(
                        "Plugin registry is invalid: " + str(error)
                    )
                    registry = None
            selected_public_registry = self.public_registry_data
            self.apply_registry_sources(
                selected_public_registry,
                local_registry,
            )
        elif self.plugin_data:
            Domoticz.Error("No plugin registry found. Keeping existing plugin registry.")
        else:
            self.plugin_data = {}
            self.registry_entries = {}
            self.plugin_platforms = {}
            self.local_plugin_keys = []
            Domoticz.Error("No plugin registry found. Plugins cannot be managed.")

        update_times = self.load_update_times()
        self.apply_update_times(update_times, include_local=False)
        self.add_self_to_registry()

    def sendDomoticzNotification(self, subject, message):
        send_notification = getattr(Domoticz, "SendNotification", None)
        if not callable(send_notification):
            Domoticz.Log("Notification skipped because this Domoticz version does not expose SendNotification: " + subject)
            return False

        try:
            send_notification(subject, message)
            Domoticz.Debug("Notification sent natively.")
            return True
        except Exception as e:
            Domoticz.Error("Failed to send notification: " + str(e))
            return False

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

        host = self.get_host()
        Domoticz.Log(f"PyPluginStore host runtime is: {host.platform_name}")
        manager_identity = self.captureManagerRuntimeIdentity()
        if _manager_identity_is_valid(manager_identity):
            Domoticz.Log(
                "PyPluginStore runtime identity: v"
                + manager_identity["product_version"]
                + " build "
                + manager_identity["build_id"][:12]
            )
        else:
            Domoticz.Error(
                "PyPluginStore runtime identity could not be verified."
            )
        self.finalizeSelfUpdateState()

        plugins_dir = host.plugins_dir()

        current_folder = self.get_current_plugin_folder()
        if not current_folder.startswith("00-"):
            warn_msg = f"PyPluginStore is in '{current_folder}'. It is strongly advised to rename the folder to start with '00-' (e.g., '00-PyPluginStore') so it loads first."
            Domoticz.Error(warn_msg)
            self.sendDomoticzNotification("PyPluginStore Setup Warning", warn_msg)

        try:
            finalized_transactions = (
                self.release_transaction_manager.finalize_startup()
            )
            if finalized_transactions:
                Domoticz.Log(
                    "Recovered "
                    + str(len(finalized_transactions))
                    + " release transaction(s) during startup."
                )
        except Exception as error:
            Domoticz.Error(
                "Release transaction startup recovery failed: " + str(error)
            )

        # Inject shared dependencies into sys.path
        shared_deps_dir = host.shared_deps_dir()
        if os.path.isdir(shared_deps_dir) and shared_deps_dir not in sys.path:
            sys.path.insert(0, shared_deps_dir)
            Domoticz.Log(f"Injected PyPluginStore shared dependencies into sys.path: {shared_deps_dir}")

        # Autoinstall/Update Custom UI
        templates_dir = host.templates_dir()
        try:
            html_src = host.ui_html_source()
            ui_asset_names = ["pypluginstore-icon.png"]
            images_dir = host.images_dir()
            html_dst = host.ui_html_destination()
            
            if os.path.isfile(html_src):
                if not os.path.exists(templates_dir):
                    Domoticz.Debug(f"Creating templates directory: {templates_dir}")
                    os.makedirs(templates_dir, exist_ok=True)
                if not os.path.exists(images_dir):
                    Domoticz.Debug(f"Creating images directory: {images_dir}")
                    os.makedirs(images_dir, exist_ok=True)
                
                # Remove legacy UI if it exists
                old_html_dst = os.path.join(templates_dir, "pp-manager.html")
                if os.path.isfile(old_html_dst):
                    try:
                        os.remove(old_html_dst)
                        Domoticz.Log(f"Removed legacy UI file: {old_html_dst}")
                    except Exception as e:
                        Domoticz.Error(f"Failed to remove legacy UI file: {e}")
                
                if self.getManagerIdentityService().deploy_runtime_frontend():
                    Domoticz.Log(f"Custom UI autoinstalled/updated: {html_dst}")
                else:
                    Domoticz.Debug("Custom UI is already up to date.")

                for asset_name in ui_asset_names:
                    asset_src = host.ui_asset_source(asset_name)
                    asset_dst = host.ui_asset_destination(asset_name)
                    if os.path.isfile(asset_src):
                        shutil.copyfile(asset_src, asset_dst)
                        host.make_web_readable(asset_dst)
                        Domoticz.Debug(f"Custom UI asset installed/updated: {asset_dst}")
        except Exception as e:
            Domoticz.Error(f"Custom UI autoinstall failed: {e}")
            Domoticz.Debug(f"Check permissions for: {templates_dir}")

        if 1 not in Devices:
            Domoticz.Device(Name="API Payload", Unit=1, TypeName="Text", DeviceID="PPM_API_PAYLOAD").Create()
        if 2 not in Devices:
            Domoticz.Device(Name="API Trigger", Unit=2, Type=244, Subtype=73, Switchtype=9, DeviceID="PPM_API_TRIGGER", Used=1).Create()
            
        self.fetch_registry()
        self.processPendingOperations()

        if Parameters.get("Mode5") == 'True':
            Domoticz.Log("Plugin Security Scan is enabled")
            secpoluserFile = os.path.join(self.get_plugin_home_folder(), "secpoluser.txt")
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
                if os.path.isdir(plugin_path) and plugin_folder != self.get_current_plugin_folder():

                    # Recursively walk through the plugin's folder to find all .py files
                    for root, _, files in os.walk(plugin_path):
                        # Optional: skip hidden folders like .git or .shared_deps
                        if '/.' in root.replace('\\', '/'):
                            continue

                        for file in files:
                            if file.endswith('.py'):
                                py_file = os.path.join(root, file)
                                self.parseFileForSecurityIssues(py_file, plugin_folder)

        exceptionFile = os.path.join(self.get_plugin_home_folder(), "exceptions.txt")
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
            for plugin_key in self.get_managed_installed_plugin_keys(plugins_dir):
                entry = self.get_registry_entry(plugin_key)
                if entry is not None:
                    self.UpdatePythonPlugin(entry.author, entry.repository, plugin_key)

        if Parameters["Mode4"] == 'AllNotify':
            Domoticz.Log("Collecting Updates for All Plugins!!!")
            for plugin_key in self.get_managed_installed_plugin_keys(plugins_dir):
                entry = self.get_registry_entry(plugin_key)
                if entry is not None:
                    self.CheckForUpdatePythonPlugin(entry.author, entry.repository, plugin_key)

        Domoticz.Log("Plugin Manager Ready. Use the 'Custom' menu to manage plugins.")
        Domoticz.Heartbeat(60)

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug(f"onCommand called for Unit {Unit}: Command '{Command}', Level: {Level}")
        if Unit == 2 and Command.lower() == "on":
            if 1 in Devices:
                payload_str = str(Devices[1].sValue or "")

                if not payload_str:
                    Domoticz.Debug("API payload device is empty; ignoring trigger.")
                    return
                
                # 1. DoS Protection: commands are small, but the shared bridge can still contain a large prior response.
                if len(payload_str) > API_PAYLOAD_MAX_LENGTH:
                    if self.isApiResponsePayloadString(payload_str):
                        Domoticz.Debug("Ignoring stale API response left in payload device.")
                        Devices[1].Update(nValue=0, sValue="")
                        return

                    Domoticz.Error("API Payload exceeds length limit.")
                    Devices[1].Update(nValue=0, sValue="")
                    return

                Domoticz.Debug("API command payload received.")
                try:
                    # Clear payload device immediately to prevent replay/abuse
                    Devices[1].Update(nValue=0, sValue="")
                    
                    payload = json.loads(payload_str)
                    
                    # 2. Type Validation: Ensure we got a dictionary
                    if not isinstance(payload, dict):
                        raise ValueError("Payload must be a JSON object")

                    if self.isApiResponsePayload(payload):
                        Domoticz.Debug("Ignoring stale API response left in payload device.")
                        return
                    
                    # 3. Content Sanitization
                    self.tx_id = str(payload.get("tx_id", ""))[:50] # Limit tx_id length
                    self.handleApiCommand(payload)
                except Exception as e:
                    Domoticz.Error(f"Failed to parse API payload: {e}")
                    self.sendApiResponse({"status": "error", "message": "Invalid JSON payload or structure"})

    def isApiResponsePayload(self, payload):
        return (
            isinstance(payload, dict)
            and payload.get("status") in (
                "success",
                "error",
                "confirmation_required",
            )
            and "tx_id" in payload
        )

    def isApiResponsePayloadString(self, payload_str):
        try:
            return self.isApiResponsePayload(json.loads(payload_str))
        except Exception:
            return re.match(r'^\s*\{\s*"status"\s*:\s*"(success|error)"', payload_str) is not None

    def loadPendingOperations(self):
        pending_file = self.get_host().pending_operations_file()
        if not os.path.isfile(pending_file):
            return []
        try:
            with open(pending_file, "r", encoding="utf-8") as f:
                operations = json.load(f)
            if isinstance(operations, list):
                return operations
            Domoticz.Error("pending_operations.json does not contain a JSON list.")
        except Exception as e:
            Domoticz.Error("Error reading pending_operations.json: " + str(e))
        return []

    def savePendingOperations(self, operations):
        pending_file = self.get_host().pending_operations_file()
        try:
            if operations:
                with open(pending_file, "w", encoding="utf-8") as f:
                    json.dump(operations, f, indent=4)
                    f.write("\n")
            elif os.path.isfile(pending_file):
                os.remove(pending_file)
            return True
        except Exception as e:
            Domoticz.Error("Error writing pending_operations.json: " + str(e))
            return False

    def queuePendingOperation(self, action, plugin_key):
        try:
            plugin_key = self.get_host().validate_plugin_key(plugin_key)
        except ValueError as e:
            Domoticz.Error(str(e))
            return False

        operations = self.loadPendingOperations()
        operation = {"action": action, "plugin_key": plugin_key}
        for existing_operation in operations:
            if existing_operation.get("action") == action and existing_operation.get("plugin_key") == plugin_key:
                Domoticz.Debug("Pending operation already queued: " + action + " " + plugin_key)
                return True

        operations.append(operation)
        if self.savePendingOperations(operations):
            Domoticz.Log("Queued pending operation: " + action + " " + plugin_key)
            return True
        return False

    def processPendingOperations(self):
        operations = self.loadPendingOperations()
        if not operations:
            return

        Domoticz.Log("Processing pending plugin operations.")
        remaining_operations = []
        for operation in operations:
            action = operation.get("action")
            plugin_key = operation.get("plugin_key")
            if action == "remove":
                success, message = self.removePlugin(plugin_key, queue_on_lock=False)
            elif action == "update":
                entry = self.get_registry_entry(plugin_key)
                if entry is not None:
                    success, message = self.UpdatePythonPlugin(
                        entry.author,
                        entry.repository,
                        plugin_key,
                        queue_on_lock=False
                    )
                else:
                    success = False
                    message = "Plugin not found in registry"
            else:
                success = True
                message = "Unknown pending operation ignored"

            if success:
                Domoticz.Log("Completed pending operation: " + str(action) + " " + str(plugin_key))
            else:
                Domoticz.Error("Pending operation failed for " + str(plugin_key) + ": " + str(message))
                remaining_operations.append(operation)

        self.savePendingOperations(remaining_operations)

    def removePlugin(self, plugin_key, queue_on_lock=True):
        host = self.get_host()
        try:
            plugin_key = host.validate_plugin_key(plugin_key)
            plugin_target_dir = self.resolve_installed_plugin_dir(
                plugin_key,
                refresh=True,
            )
        except ValueError as e:
            return False, str(e)

        if os.path.basename(plugin_target_dir) == self.get_current_plugin_folder():
            return False, "Plugin directory not found or cannot remove self"

        if not os.path.isdir(plugin_target_dir):
            return False, "Plugin directory not found or cannot remove self"

        try:
            shutil.rmtree(plugin_target_dir)
            self.getInstalledPlugins(host.plugins_dir())
            return True, ""
        except Exception as e:
            if queue_on_lock and host.is_locked_file_error(e):
                self.queuePendingOperation("remove", plugin_key)
                return False, "Plugin files are in use; removal queued for the next startup."
            return False, str(e)

    def _release_action_confirmation(
        self,
        *,
        kind,
        action,
        plugin_key,
        target,
        confirmation_token,
        inventory_sha256="",
        message="",
    ):
        """Issue or consume an opaque action token bound to current state."""
        if not inventory_sha256:
            binding = {
                "action": action,
                "plugin_key": plugin_key,
                "target": target,
            }
            inventory_sha256 = hashlib.sha256(
                json.dumps(
                    binding,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        coordinator = self.install_update_strategy
        if not confirmation_token:
            challenge = coordinator.issue_confirmation_challenge(
                kind=kind,
                plugin_key=plugin_key,
                action=action,
                target=target,
                inventory_sha256=inventory_sha256,
            )
            if message:
                challenge["message"] = message
            return {
                "status": "confirmation_required",
                "challenge": challenge,
            }, ""
        approved_digest = coordinator.consume_confirmation_challenge(
            token=confirmation_token,
            plugin_key=plugin_key,
            action=action,
            target=target,
            inventory_sha256=inventory_sha256,
        )
        return None, approved_digest

    def _release_action_context(self, entry, trigger):
        context = self.getReleaseManagementContext(
            entry,
            operation="update",
            trigger=trigger,
        )
        error = str(context.get("error") or "")
        if error:
            raise RuntimeError(error)
        return context

    def _plan_release_switch(self, entry, context, trigger="manual"):
        """Plan one Git-to-Release switch for both the API and the UI."""
        if trigger not in ReleaseManagementCoordinator.TRIGGERS:
            raise ValueError(
                "Release management trigger must be manual or automatic."
            )
        error = str(context.get("error") or "")
        if error:
            return ReleaseSwitchPlan("blocked", error)
        release = context.get("release")
        if (
            not context.get("metadata_authorized")
            or release is None
            or context.get("tombstone") is not None
        ):
            return ReleaseSwitchPlan(
                "blocked",
                "No authorized release is available for this plugin.",
            )
        if context.get("installed_mode") != "git":
            return ReleaseSwitchPlan(
                "blocked",
                "This plugin is not using Git, so this Release channel action "
                "is not applicable.",
            )

        decision_context = dict(context)
        index_sequence = decision_context.pop("index_sequence", 0)
        decision_context.pop("error", None)
        decision_context["channel_preference"] = "release"
        decision = self.install_update_strategy.decide(
            entry,
            operation="update",
            trigger=trigger,
            **decision_context,
        )
        decision = replace(decision, index_sequence=index_sequence)
        if decision.route != "release_migration":
            decision_message = {
                "release_not_migration_eligible": (
                    "The selected release cannot safely replace this Git "
                    "checkout."
                ),
                "release_requires_manual_migration": (
                    "The selected release requires manual confirmation before "
                    "using the Release channel."
                ),
            }.get(decision.reason, decision.reason)
            return ReleaseSwitchPlan(
                "blocked",
                decision_message
                or "The Release channel is not available for this Git checkout.",
                decision=decision,
            )

        release_strategy = getattr(
            self.install_update_strategy,
            "release_strategy",
            None,
        )
        preflight_migration = getattr(
            release_strategy,
            "preflight_migration",
            None,
        )
        if not callable(preflight_migration):
            return ReleaseSwitchPlan(
                "blocked",
                "Using the Release channel is not configured.",
                decision=decision,
            )
        try:
            preflight = preflight_migration(entry, release, trigger)
        except (
            OSError,
            ReleasePreservationError,
            RuntimeError,
            ValueError,
        ) as error:
            return ReleaseSwitchPlan(
                "blocked",
                str(error),
                decision=decision,
                release_strategy=release_strategy,
            )
        action_state = preflight.manual_action_state
        return ReleaseSwitchPlan(
            action_state,
            preflight.message
            or preflight.reason
            or (
                "The Release channel is not available for this Git checkout."
                if action_state == "blocked"
                else ""
            ),
            decision=decision,
            preflight=preflight,
            release_strategy=release_strategy,
        )

    def _channel_action_target(self, entry, context, channel):
        release = context.get("release")
        target = {
            "repository_identity": normalize_repository_identity(
                entry.author,
                entry.repository,
            ),
            "channel": channel,
            "installed_mode": context.get("installed_mode", "absent"),
            "current_preference": context.get("channel_preference") or "",
        }
        if target["installed_mode"] != "absent":
            target["live_folder"] = os.path.basename(
                os.path.normpath(
                    self.resolve_installed_plugin_dir(entry.key)
                )
            )
        installed_release = context.get("installed_release")
        if installed_release is not None:
            target["installed_release_id"] = installed_release.release_id
            target["installed_revision"] = installed_release.release_revision
        if release is not None:
            target.update(
                {
                    "release_id": release.release_id,
                    "release_revision": release.revision,
                    "commit": release.commit,
                    "source_revision": release.source_revision,
                    "artifact_tree_sha256": release.artifact.tree_sha256,
                }
            )
        return target

    def _persist_channel_after_activation(self, identity, preference):
        """Keep filesystem truth successful if preference persistence fails."""
        try:
            self.channel_preference_service.set(identity, preference)
        except (OSError, RuntimeError, ValueError) as error:
            Domoticz.Error(
                "Release channel preference could not be saved after "
                "activation: "
                + str(error)
            )
            return (
                " The plugin files were changed successfully, but the channel "
                "preference could not be saved. Select the channel again after "
                "restart."
            )
        return ""

    def _release_operation_transaction(
        self,
        release_strategy,
        plugin_key,
        release,
    ):
        """Re-read the durable result of the release operation, when present."""
        operation_id = str(
            getattr(release_strategy, "last_operation_id", "") or ""
        )
        if not operation_id:
            return None, ""
        transaction_manager = getattr(
            release_strategy,
            "transaction_manager",
            None,
        )
        load_transaction = getattr(
            transaction_manager,
            "load_transaction",
            None,
        )
        if not callable(load_transaction):
            return (
                None,
                " Release activation was accepted, but its durable state "
                "could not be re-read. Restart to reconcile the installed "
                "state.",
            )
        try:
            transaction = load_transaction(operation_id)
        except (OSError, RuntimeError, ValueError):
            return (
                None,
                " Release activation was accepted, but its durable state "
                "could not be re-read. Restart to reconcile the installed "
                "state.",
            )
        target = getattr(transaction, "target", {})
        if (
            getattr(transaction, "plugin_key", "") != plugin_key
            or not isinstance(target, dict)
            or target.get("release_id") != release.release_id
            or target.get("release_revision") != release.revision
            or target.get("artifact_tree_sha256")
            != release.artifact.tree_sha256
        ):
            return (
                None,
                " Release activation was accepted, but its durable state "
                "does not match the selected release. Restart to reconcile "
                "the installed state.",
            )
        return transaction, ""

    def _installed_release_matches(self, installed_release, release):
        return bool(
            installed_release is not None
            and getattr(installed_release, "release_id", None)
            == release.release_id
            and getattr(installed_release, "release_revision", None)
            == release.revision
            and getattr(installed_release, "artifact_tree_sha256", None)
            == release.artifact.tree_sha256
        )

    def executeReleaseManagementAction(
        self,
        *,
        action,
        plugin_key,
        confirmation_token="",
        trigger="manual",
    ):
        """Execute a confirmed migration, rollback, or channel preference."""
        try:
            if action not in {
                "rollback",
                "update",
                "use_release",
            }:
                raise ValueError("Release management action is unsupported.")
            if trigger != "manual":
                raise ValueError(
                    "Release management actions require a manual trigger."
                )
            plugin_key = self.get_host().validate_plugin_key(plugin_key)
            entry = self.get_registry_entry(plugin_key)
            if entry is None:
                raise ValueError("Plugin was not found in the registry.")
            self.getInstalledPlugins(self.get_host().plugins_dir())
            if self.installed_plugin_scan_error:
                raise RuntimeError(self.installed_plugin_scan_error)
            if plugin_key == self.get_current_plugin_folder():
                raise ValueError(
                    "Release-based manager self-update is not supported."
                )

            identity = normalize_repository_identity(
                entry.author,
                entry.repository,
            )
            if not identity:
                raise ValueError("Plugin repository identity is invalid.")

            if action == "rollback":
                lifecycle = (
                    self.release_transaction_manager.plugin_lifecycle_state(
                        plugin_key
                    )
                )
                transaction = lifecycle.get("rollback")
                if transaction is None:
                    raise RuntimeError(
                        "No verified retained backup is available."
                    )
                target = {
                    "operation_id": transaction.operation_id,
                    "current": transaction.target,
                    "rollback": transaction.expected_current,
                    "dependency_state": transaction.dependency_state,
                    "updated_at": transaction.updated_at,
                }
                confirmation, _approved_digest = (
                    self._release_action_confirmation(
                    kind="rollback",
                    action=action,
                    plugin_key=plugin_key,
                    target=target,
                    confirmation_token=confirmation_token,
                    )
                )
                if confirmation is not None:
                    return confirmation
                rolled_back = self.release_transaction_manager.rollback(
                    transaction.operation_id
                )
                if (
                    rolled_back.expected_current.get("management_mode")
                    == "git"
                ):
                    warning = self._persist_channel_after_activation(
                        identity,
                        "keep_git",
                    )
                else:
                    warning = ""
                return {
                    "status": "success",
                    "message": (
                        "Retained backup restored; restart required."
                        + warning
                    ),
                    "restart_pending": True,
                }

            context = self._release_action_context(entry, trigger)
            installed_mode = context.get("installed_mode")
            if installed_mode == "absent":
                raise RuntimeError("Plugin is not installed.")

            release = context.get("release")
            if (
                not context.get("metadata_authorized")
                or release is None
                or context.get("tombstone") is not None
            ):
                raise RuntimeError(
                    "No authorized release is available for this plugin."
                )
            if installed_mode == "release" and action == "use_release":
                target = self._channel_action_target(
                    entry,
                    context,
                    "release",
                )
                confirmation, _approved_digest = (
                    self._release_action_confirmation(
                        kind="channel_switch",
                        action=action,
                        plugin_key=plugin_key,
                        target=target,
                        confirmation_token=confirmation_token,
                    )
                )
                if confirmation is not None:
                    return confirmation
                self.channel_preference_service.set(identity, "release")
                return {
                    "status": "success",
                    "message": "Release management selected.",
                    "restart_pending": False,
                }
            if installed_mode != "git":
                raise RuntimeError(
                    "This plugin is not using Git, so this Release channel "
                    "action is not applicable."
                )

            switch_plan = self._plan_release_switch(
                entry,
                context,
                trigger,
            )
            if switch_plan.action_state == "blocked":
                raise RuntimeError(
                    switch_plan.message
                    or (
                        "The Release channel is not available for this Git "
                        "checkout."
                    )
                )
            decision = switch_plan.decision
            preflight = switch_plan.preflight
            release_strategy = switch_plan.release_strategy
            target = self._channel_action_target(
                entry,
                context,
                "release",
            )
            target["migration_source_commit"] = (
                preflight.installed_commit
            )
            target["migration_relationship"] = preflight.relationship
            target["migration_reason"] = preflight.reason
            migration_inventory = preflight.preservation_inventory
            requires_inventory_approval = bool(
                isinstance(
                    migration_inventory,
                    ReleasePreservationInventory,
                )
                and (
                    migration_inventory.unknown_paths
                    or migration_inventory.tracked_changes
                    or migration_inventory.approval_required_paths
                )
            )
            target["requires_inventory_approval"] = (
                requires_inventory_approval
            )
            target["requires_downgrade_confirmation"] = bool(
                preflight.requires_confirmation
            )
            relationship_message = (
                preflight.message
                or (
                    "The selected Release channel does not contain the "
                    "installed Git commit."
                )
            )
            confirmation_message = (
                "Use the Release channel instead of Git commits for this "
                "plugin?"
            )
            if (
                preflight.requires_confirmation
                and requires_inventory_approval
            ):
                confirmation_message = (
                    relationship_message
                    + " Confirm preserving the reviewed local-data "
                    "inventory and using the Release channel instead of Git "
                    "commits."
                )
            elif preflight.requires_confirmation:
                confirmation_message = (
                    relationship_message
                    + " Confirm using the Release channel instead of the "
                    "installed Git commits."
                )
            elif requires_inventory_approval:
                confirmation_message = (
                    "Confirm preserving the reviewed local-data inventory and "
                    "using the Release channel instead of Git commits."
                )
            confirmation, approved_digest = (
                self._release_action_confirmation(
                    kind="git_migration",
                    action=action,
                    plugin_key=plugin_key,
                    target=target,
                    confirmation_token=confirmation_token,
                    inventory_sha256=preflight.inventory_sha256,
                    message=confirmation_message,
                )
            )
            if confirmation is not None:
                return confirmation
            migrate = getattr(release_strategy, "migrate", None)
            if not callable(migrate):
                raise RuntimeError(
                    "Using the Release channel is not configured."
                )
            success, message = migrate(
                entry,
                release,
                trigger,
                index_sequence=decision.index_sequence,
                approved_inventory_sha256=approved_digest,
                downgrade_confirmed=bool(
                    preflight.requires_confirmation
                ),
            )
            if not success:
                raise RuntimeError(
                    message or "Using the Release channel failed."
                )
            operation_transaction, journal_warning = (
                self._release_operation_transaction(
                    release_strategy,
                    plugin_key,
                    release,
                )
            )
            activation_state = "activated"
            verification_warning = ""
            if operation_transaction is not None:
                if operation_transaction.phase == "queued_locked":
                    activation_state = "queued"
                elif operation_transaction.phase in {
                    "restart_pending",
                    "release_managed",
                }:
                    try:
                        installed_release = (
                            self.install_metadata_service.read(
                                operation_transaction.paths.live_code
                            )
                        )
                    except (OSError, RuntimeError, ValueError):
                        installed_release = None
                    if not self._installed_release_matches(
                        installed_release,
                        release,
                    ):
                        verification_warning = (
                            " Release activation is committed, but "
                            "post-activation verification could not complete. "
                            "Restart to complete verification."
                        )
                else:
                    raise RuntimeError(
                        operation_transaction.error
                        or (
                            "Release activation did not complete; durable "
                            "state is "
                            + operation_transaction.phase
                            + "."
                        )
                    )
            elif journal_warning:
                activation_state = "pending_verification"
            else:
                plugin_dir = self.resolve_installed_plugin_dir(plugin_key)
                installed_release = self.install_metadata_service.read(
                    plugin_dir
                )
                if not self._installed_release_matches(
                    installed_release,
                    release,
                ):
                    raise RuntimeError(
                        "Migrated release could not be verified."
                    )
            warning = self._persist_channel_after_activation(
                identity,
                "release",
            )
            return {
                "status": "success",
                "message": (
                    message
                    or "Release channel selected; restart required."
                )
                + journal_warning
                + verification_warning
                + warning,
                "restart_pending": True,
                "activation_state": activation_state,
            }
        except (OSError, ReleaseConfirmationError, RuntimeError, ValueError) as error:
            return {
                "status": "error",
                "message": str(error),
                "restart_pending": False,
            }

    def _manual_update_uses_release_migration(self, entry):
        """Return whether the current manual Update route changes channel."""
        try:
            plugin_dir = self.resolve_installed_plugin_dir(entry.key)
            if not is_git_checkout(plugin_dir):
                return False
            context = dict(self._release_action_context(entry, "manual"))
            context.pop("index_sequence", None)
            context.pop("error", None)
            decision = self.install_update_strategy.decide(
                entry,
                operation="update",
                trigger="manual",
                **context,
            )
            return decision.route == "release_migration"
        except (OSError, RuntimeError, ValueError):
            return False

    def handleApiCommand(self, payload):
        # Ensure action is a safe string
        action = str(payload.get("action", ""))
        plugins_dir = self.get_host().plugins_dir()
        self._api_frontend_identity = None
        self._api_manager_identity = None
        if self.manager_identity_service is not None:
            frontend_identity = (
                payload.get("frontend_identity")
                if "frontend_identity" in payload
                else None
            )
            if "frontend_identity" in payload and not isinstance(
                frontend_identity,
                dict,
            ):
                frontend_identity = {}
            self._api_frontend_identity = frontend_identity
            self._api_manager_identity = self.getManagerIdentityVerdict(
                frontend_identity=frontend_identity,
                self_update_state=self.getSelfUpdateState(),
            )
            if (
                action not in MANAGER_IDENTITY_RECOVERY_ACTIONS
                and not self._api_manager_identity.get(
                    "mutations_allowed",
                    False,
                )
            ):
                self.sendApiResponse(
                    {
                        "status": "error",
                        "action": action,
                        "code": "manager_identity_mismatch",
                        "message": self._api_manager_identity.get(
                            "message",
                            "PyPluginStore generations do not match.",
                        ),
                        "manager_identity": self._api_manager_identity,
                    }
                )
                return
        
        if action == "get_local_registry":
            document = self.local_registry_service.read_document()
            if document.writable:
                self.sendApiResponse({
                    "status": "success",
                    "action": action,
                    "entries": self.local_registry_service.entries_for_api(
                        document
                    ),
                    "revision": document.revision,
                    "path": document.path,
                    "read_only": False,
                })
            else:
                self.sendApiResponse({
                    "status": "error",
                    "action": action,
                    "code": document.error_code,
                    "message": document.message,
                    "revision": document.revision,
                    "path": document.path,
                    "read_only": True,
                })
        elif action in (
            "upsert_local_registry_entry",
            "delete_local_registry_entry",
        ):
            try:
                if action == "upsert_local_registry_entry":
                    entry = payload.get("entry")
                    document = self.local_registry_service.upsert(
                        payload.get("expected_revision"),
                        payload.get("original_key"),
                        entry,
                    )
                    plugin_key = str(
                        entry.get("key") if isinstance(entry, dict) else ""
                    ).strip()
                else:
                    plugin_key = str(payload.get("plugin_key") or "").strip()
                    document = self.local_registry_service.delete(
                        payload.get("expected_revision"),
                        plugin_key,
                    )

                try:
                    self.reapply_local_registry(document.entries)
                except Exception as error:
                    self.fetch_registry()
                    self.sendApiResponse({
                        "status": "error",
                        "action": action,
                        "code": "registry_reapply_failed",
                        "message": (
                            "registry_local.json was saved, but the registry "
                            "could not be reapplied: " + str(error)
                        ),
                        "reload_required": True,
                        "read_only": False,
                    })
                    return

                self.sendApiResponse({
                    "status": "success",
                    "action": action,
                    "plugin_key": plugin_key,
                    "revision": document.revision,
                })
            except LocalRegistryError as error:
                self.sendApiResponse({
                    "status": "error",
                    "action": action,
                    "code": error.code,
                    "message": error.message,
                    "field_errors": error.field_errors,
                    "reload_required": error.reload_required,
                    "read_only": error.read_only,
                })
        elif action == "list_plugins":
            installed_plugins = self.getInstalledPlugins(plugins_dir)
            cached_update_status = self.getCachedUpdateStatuses(installed_plugins)
            versions = self.get_plugin_versions(installed_plugins, cached_update_status, plugins_dir)
            management = self.getPluginManagementMap(
                installed_plugins,
                cached_update_status,
                versions,
                plugins_dir,
            )
                    
            self.sendApiResponse({
                "status": "success",
                "action": action,
                "data": self.plugin_data,
                "installed": installed_plugins,
                "manager_key": self.get_current_plugin_folder(),
                "local_plugins": self.local_plugin_keys,
                "installed_match_details": self.installed_plugin_match_details,
                "installation_conflicts": self.installed_plugin_install_conflicts,
                "installed_scan_error": self.installed_plugin_scan_error,
                "update_status": cached_update_status,
                "versions": versions,
                "management": management,
                "platforms": self.plugin_platforms,
                "self_update": self.getSelfUpdateState()
            })
        elif action == "refresh_update_status":
            self.fetch_registry()
            installed_plugins = self.getInstalledPlugins(plugins_dir)
            if self.installed_plugin_scan_error:
                update_status = self.getCachedUpdateStatuses(
                    installed_plugins
                )
            else:
                update_status = self.refreshInstalledUpdateStatuses(
                    installed_plugins,
                    plugins_dir,
                )
            versions = self.get_plugin_versions(installed_plugins, update_status, plugins_dir)
            management = self.getPluginManagementMap(
                installed_plugins,
                update_status,
                versions,
                plugins_dir,
            )
            self.sendApiResponse({
                "status": "success",
                "action": action,
                "data": self.plugin_data,
                "installed": installed_plugins,
                "manager_key": self.get_current_plugin_folder(),
                "local_plugins": self.local_plugin_keys,
                "installed_match_details": self.installed_plugin_match_details,
                "installation_conflicts": self.installed_plugin_install_conflicts,
                "installed_scan_error": self.installed_plugin_scan_error,
                "update_status": update_status,
                "versions": versions,
                "management": management,
                "platforms": self.plugin_platforms,
                "self_update": self.getSelfUpdateState()
            })
        elif action == "self_update_status":
            response = {
                "status": "success",
                "action": action,
                "self_update": self.getSelfUpdateState(),
            }
            if self._api_manager_identity is not None:
                response["manager_identity"] = self._api_manager_identity
            self.sendApiResponse(response)
        elif action == "use_git":
            self.sendApiResponse({
                "status": "error",
                "action": action,
                "message": (
                    "Switch to Git is no longer available. Add a Local "
                    "registry override to use Git management."
                ),
            })
        elif action in ("use_release", "rollback"):
            plugin_key = str(payload.get("plugin_key") or "")
            confirmation_token = payload.get("confirmation_token", "")
            if not isinstance(confirmation_token, str):
                confirmation_token = ""
            result = self.executeReleaseManagementAction(
                action=action,
                plugin_key=plugin_key,
                confirmation_token=confirmation_token,
                trigger="manual",
            )
            response = dict(result)
            response["action"] = action
            response["plugin_key"] = plugin_key
            self.sendApiResponse(response)
        elif action == "install":
            plugin_key = payload.get("plugin_key")
            entry = self.get_registry_entry(plugin_key)
            if entry is not None:
                install_success, install_message = self.InstallPythonPlugin(entry.author, entry.repository, plugin_key, entry.branch)
                if install_success:
                    self.sendApiResponse({"status": "success", "action": action, "plugin_key": plugin_key})
                else:
                    self.sendApiResponse({"status": "error", "action": action, "plugin_key": plugin_key, "message": install_message or "Plugin install failed"})
            else:
                self.sendApiResponse({"status": "error", "action": action, "plugin_key": plugin_key, "message": "Plugin not found"})
        elif action == "update":
            plugin_key = payload.get("plugin_key")
            entry = self.get_registry_entry(plugin_key)
            if entry is not None:
                try:
                    plugin_dir = self.resolve_installed_plugin_dir(plugin_key)
                    local_override_git_error = (
                        self.getLocalOverrideGitCheckoutError(
                            entry, plugin_dir
                        )
                    )
                except ValueError as error:
                    diagnostic_dir = self.installed_plugin_diagnostic_dir(
                        plugin_key,
                        plugins_dir,
                    )
                    local_override_git_error = (
                        self.getLocalOverrideGitCheckoutError(
                            entry,
                            diagnostic_dir,
                        )
                        if diagnostic_dir
                        else ""
                    )
                    self.sendApiResponse({
                        "status": "error",
                        "action": action,
                        "plugin_key": plugin_key,
                        "message": local_override_git_error or str(error),
                    })
                    return
                if local_override_git_error:
                    self.sendApiResponse({
                        "status": "error",
                        "action": action,
                        "plugin_key": plugin_key,
                        "message": local_override_git_error,
                    })
                    return
                if self._manual_update_uses_release_migration(entry):
                    confirmation_token = payload.get(
                        "confirmation_token",
                        "",
                    )
                    if not isinstance(confirmation_token, str):
                        confirmation_token = ""
                    result = self.executeReleaseManagementAction(
                        action="update",
                        plugin_key=plugin_key,
                        confirmation_token=confirmation_token,
                        trigger="manual",
                    )
                    response = dict(result)
                    response["action"] = action
                    response["plugin_key"] = plugin_key
                    self.sendApiResponse(response)
                    return
                update_success, update_message = self.UpdatePythonPlugin(entry.author, entry.repository, plugin_key)
                if update_success:
                    response = {"status": "success", "action": action, "plugin_key": plugin_key}
                    if plugin_key == self.get_current_plugin_folder():
                        response["operation"] = "self_update"
                        response["self_update"] = self.getSelfUpdateState()
                    if update_message:
                        response["message"] = update_message
                    self.sendApiResponse(response)
                else:
                    self.sendApiResponse({"status": "error", "action": action, "plugin_key": plugin_key, "message": update_message or "Plugin update failed"})
            else:
                self.sendApiResponse({"status": "error", "action": action, "plugin_key": plugin_key, "message": "Plugin not found"})
        elif action == "restart_domoticz":
            restart_success, restart_message = self.restartDomoticz()
            if restart_success:
                response = {
                    "status": "success",
                    "action": action,
                    "message": restart_message,
                }
            else:
                response = {
                    "status": "error",
                    "action": action,
                    "message": restart_message,
                }
            if self._api_manager_identity is not None:
                response["manager_identity"] = self._api_manager_identity
            self.sendApiResponse(response)
        elif action == "remove":
            plugin_key = payload.get("plugin_key", "")
            remove_success, remove_message = self.removePlugin(plugin_key)
            if remove_success:
                self.sendApiResponse({"status": "success", "action": action, "plugin_key": plugin_key})
            else:
                self.sendApiResponse({"status": "error", "action": action, "plugin_key": plugin_key, "message": remove_message})
        else:
            self.sendApiResponse({"status": "error", "action": action, "message": f"Unknown action: {action}"})

    def getInstalledUpdateStatuses(self, installed_plugins, plugins_dir):
        return self.refreshInstalledUpdateStatuses(installed_plugins, plugins_dir)

    def getGitUpdateStatus(self, plugin_dir, plugin_key=None, fetch_first=True):
        return self.update_status_service.get_git_update_status(plugin_dir, plugin_key, fetch_first)

    def getSelfUpdateState(self):
        host = self.get_host()
        state = host.read_self_update_state()
        if self.selfUpdateStateIsStale(state):
            message = (
                "PyPluginStore self-update stopped reporting progress. Check "
                "self_update.log before retrying."
            )
            try:
                return host.write_self_update_state(
                    "stale_unknown",
                    message,
                    previous_state=state,
                )
            except Exception as error:
                Domoticz.Error(
                    "Could not persist stale self-update state: " + str(error)
                )
                return host.build_self_update_state(
                    "stale_unknown",
                    message,
                    previous_state=state,
                )
        if (
            state.get("phase") == "applied_needs_reload"
            and self.manager_identity_service is not None
        ):
            return self.finalizeSelfUpdateState()
        return state

    def selfUpdateStateIsStale(self, state):
        if str(state.get("phase") or "") not in {"scheduled", "running"}:
            return False
        updated_at = str(state.get("updated_at") or "").strip()
        if not updated_at:
            return False
        try:
            updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - updated.astimezone(timezone.utc)
            return age.total_seconds() > SELF_UPDATE_ACTIVE_STALE_SECONDS
        except (TypeError, ValueError):
            return True

    def finalizeSelfUpdateState(self):
        host = self.get_host()
        state = host.read_self_update_state()
        if state.get("phase") != "applied_needs_reload":
            return state

        plugin_dir = host.plugin_home_folder()
        result = host.run_git_read_only(
            ["git", "rev-parse", "--verify", "HEAD"],
            plugin_dir,
            timeout=15,
        )
        current_commit = result.stdout.strip().splitlines()[0] if result is not None and result.returncode == 0 and result.stdout.strip() else ""
        target_commit = str(state.get("target_commit") or "").strip()

        if not current_commit:
            message = "PyPluginStore self-update finalization could not verify the current revision."
            updated_state = host.write_self_update_state(
                "stale_unknown",
                message,
                previous_state=state,
            )
            Domoticz.Error(message + " Detailed output: " + host.self_update_log_file())
            return updated_state

        if target_commit and current_commit and not (current_commit.startswith(target_commit) or target_commit.startswith(current_commit)):
            message = "PyPluginStore self-update finalization found " + current_commit + " instead of target " + target_commit + "."
            updated_state = host.write_self_update_state(
                "failed",
                message,
                previous_state=state,
                confirmed_commit=current_commit,
            )
            Domoticz.Error(message + " Detailed output: " + host.self_update_log_file())
            return updated_state

        identity_status = (
            self.getManagerIdentityService().runtime_installation_status()
        )
        if not identity_status["verifiable"]:
            message = (
                "PyPluginStore self-update was applied, but the installed "
                "runtime bundle could not be verified."
            )
            updated_state = host.write_self_update_state(
                "stale_unknown",
                message,
                previous_state=state,
                confirmed_commit=current_commit,
            )
            Domoticz.Error(
                message + " Detailed output: " + host.self_update_log_file()
            )
            return updated_state

        if not identity_status["matches"]:
            message = (
                "PyPluginStore runtime files changed. Restart Domoticz to "
                "finish the self-update."
            )
            if state.get("message") == message:
                return state
            updated_state = host.write_self_update_state(
                "applied_needs_reload",
                message,
                previous_state=state,
                confirmed_commit=current_commit,
            )
            Domoticz.Log(
                message + " Detailed output: " + host.self_update_log_file()
            )
            return updated_state

        message = (
            "PyPluginStore self-update confirmed; the loaded runtime bundle "
            "matches the installed files."
        )
        updated_state = host.write_self_update_state(
            "confirmed",
            message,
            previous_state=state,
            confirmed_commit=current_commit,
        )
        Domoticz.Log(message + " Detailed output: " + host.self_update_log_file())
        return updated_state

    def logApiErrorResponse(self, response_dict):
        if not isinstance(response_dict, dict) or response_dict.get("status") != "error":
            return

        action = str(response_dict.get("action") or "").strip()
        plugin_key = str(response_dict.get("plugin_key") or "").strip()
        message = str(response_dict.get("message") or "Unknown error")

        context = "API " + action if action else "API request"
        if plugin_key:
            context += " for " + plugin_key
        Domoticz.Error(context + " failed: " + message)

    def sendApiResponse(self, response_dict):
        response_dict = dict(response_dict)
        if self.manager_identity_service is not None:
            self._api_manager_identity = self.getManagerIdentityVerdict(
                frontend_identity=self._api_frontend_identity,
                self_update_state=self.getSelfUpdateState(),
            )
            response_dict["manager_identity"] = self._api_manager_identity
        self.logApiErrorResponse(response_dict)
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

        plugins_dir = self.get_host().plugins_dir()

        if self.last_update_status_refresh_date is None or self.last_update_status_refresh_date < now.date():
            Domoticz.Log("Refreshing plugin update status cache.")
            self.last_update_status_refresh_date = now.date()
            self.refreshInstalledUpdateStatuses(plugins_dir=plugins_dir)

        if now.hour >= 12 and (self.last_update_date is None or self.last_update_date < now.date()):
            Domoticz.Log("Its time!!. Trigering Actions!!!")
            self.last_update_date = now.date()

            if Parameters["Mode4"] == 'All':
                Domoticz.Log("Checking Updates for All Plugins!!!")
                for plugin_key in self.get_managed_installed_plugin_keys(plugins_dir):
                    entry = self.get_registry_entry(plugin_key)
                    if entry is not None:
                        self.UpdatePythonPlugin(
                            entry.author,
                            entry.repository,
                            plugin_key,
                            trigger="automatic",
                        )

            if Parameters["Mode4"] == 'AllNotify':
                Domoticz.Log("Collecting Updates for All Plugins!!!")
                for plugin_key in self.get_managed_installed_plugin_keys(plugins_dir):
                    entry = self.get_registry_entry(plugin_key)
                    if entry is not None:
                        self.CheckForUpdatePythonPlugin(
                            entry.author,
                            entry.repository,
                            plugin_key,
                            trigger="automatic",
                        )

    def InstallPythonPlugin(
        self, ppAuthor, ppRepository, ppKey, ppBranch, trigger="manual"
    ):
        entry = self.get_registry_entry_for_operation(ppKey, ppAuthor, ppRepository, ppBranch)
        if trigger == "manual":
            return self.install_update_strategy.install(entry)
        return self.install_update_strategy.install(entry, trigger=trigger)

    def UpdatePythonPlugin(
        self,
        ppAuthor,
        ppRepository,
        ppKey,
        queue_on_lock=True,
        trigger="manual",
    ):
        entry = self.get_registry_entry_for_operation(ppKey, ppAuthor, ppRepository)
        if trigger == "manual":
            return self.install_update_strategy.update(
                entry, queue_on_lock=queue_on_lock
            )
        return self.install_update_strategy.update(
            entry, queue_on_lock=queue_on_lock, trigger=trigger
        )

    def CheckForUpdatePythonPlugin(
        self, ppAuthor, ppRepository, ppKey, trigger="manual"
    ):
        entry = self.get_registry_entry_for_operation(ppKey, ppAuthor, ppRepository)
        if trigger == "manual":
            return self.install_update_strategy.check_for_update(entry)
        return self.install_update_strategy.check_for_update(
            entry, trigger=trigger
        )

    def fnSelectedNotify(self, plugin_key):
        Domoticz.Debug("fnSelectedNotify called")
        Domoticz.Log("Preparing Notification")
        entry = self.get_registry_entry(plugin_key)
        plugin_name = entry.description if entry is not None and entry.description else plugin_key
        MailSubject = platform.node() + ": Domoticz Plugin Updates Available for " + plugin_name
        MailBody = plugin_name + " has updates available!!"
        self.sendDomoticzNotification(MailSubject, MailBody)
        return None

    def fnReleaseChannelNotify(self, plugin_key, release_version=""):
        Domoticz.Debug("fnReleaseChannelNotify called")
        Domoticz.Log("Preparing Release channel notification")
        entry = self.get_registry_entry(plugin_key)
        plugin_name = (
            entry.description
            if entry is not None and entry.description
            else plugin_key
        )
        normalized_version = str(release_version or "").strip()
        if normalized_version[:1].lower() == "v":
            normalized_version = normalized_version[1:]
        release_target = (
            " v" + normalized_version
            if normalized_version
            else ""
        )
        MailSubject = (
            platform.node()
            + ": Domoticz Plugin Release Channel Available for "
            + plugin_name
        )
        MailBody = (
            "Use the Release channel"
            + release_target
            + " for "
            + plugin_name
            + " instead of Git commits."
        )
        self.sendDomoticzNotification(MailSubject, MailBody)
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
        return self.get_host().restart_domoticz()

    def installDependencies(self, plugin_key):
        Domoticz.Debug("installDependencies called")
        host = self.get_host()
        try:
            requirements_file = os.path.join(self.resolve_installed_plugin_dir(plugin_key), "requirements.txt")
        except ValueError as e:
            Domoticz.Error(str(e))
            return False, str(e)
        return host.install_requirements(requirements_file, host.shared_deps_dir(), plugin_key)

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
