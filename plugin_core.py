import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
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
        self.last_update_date = None

    def get_current_plugin_folder(self):
        return os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/')))

    def get_git_env(self):
        env = os.environ.copy()
        env['LANG'] = 'en_US.UTF-8'
        env['LC_ALL'] = 'en_US.UTF-8'
        return env

    def add_self_to_registry(self):
        self_key = self.get_current_plugin_folder()
        if not self_key:
            return

        self.plugin_data[self_key] = [
            "adrighem",
            "PyPluginStore",
            "PyPluginStore plugin manager",
            "master",
            ""
        ]

    def fetch_registry(self):
        registry_url = "https://raw.githubusercontent.com/adrighem/PyPluginStore/refs/heads/master/registry.json"
        updates_url = "https://raw.githubusercontent.com/adrighem/PyPluginStore/refs/heads/master/update_times.json"
        
        Domoticz.Debug("Fetching plugin registry from GitHub.")
        try:
            req = urllib.request.Request(registry_url)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    self.plugin_data = json.loads(response.read().decode('utf-8'))
                    Domoticz.Log("Successfully fetched plugin registry from GitHub.")
                else:
                    Domoticz.Error("Failed to fetch registry, status code: " + str(response.status))
        except Exception as e:
            Domoticz.Error("Error fetching registry: " + str(e))
            # Fallback to local file if fetch fails
            local_reg = os.path.join(os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), "..", "..")), "plugins", os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), "registry.json")
            if os.path.isfile(local_reg):
                with open(local_reg, 'r') as f:
                    self.plugin_data = json.load(f)
                Domoticz.Log("Loaded plugin registry from local file.")
            else:
                Domoticz.Error("No local registry found. Plugins cannot be managed.")

        # Fetch update times
        update_times = {}
        Domoticz.Debug("Fetching update times from GitHub.")
        try:
            req = urllib.request.Request(updates_url)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    update_times = json.loads(response.read().decode('utf-8'))
                    Domoticz.Log("Successfully fetched update times from GitHub.")
                else:
                    Domoticz.Error("Failed to fetch update times, status code: " + str(response.status))
        except Exception as e:
            Domoticz.Error("Error fetching update times: " + str(e))
            local_upd = os.path.join(os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), "..", "..")), "plugins", os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), "update_times.json")
            if os.path.isfile(local_upd):
                with open(local_upd, 'r') as f:
                    update_times = json.load(f)
                Domoticz.Log("Loaded update times from local file.")
            else:
                Domoticz.Error("No local update times found.")
        
        # Merge update times into plugin data
        for key, data in self.plugin_data.items():
            if key == "Idle": continue
            updated_at = update_times.get(key, "")
            if len(data) == 4:
                data.append(updated_at)
            elif len(data) >= 5:
                data[4] = updated_at

        self.add_self_to_registry()

    def onStart(self):
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
            home_folder_param = Parameters.get("HomeFolder", str(os.getcwd()) + "/")
            html_src = os.path.join(home_folder_param, "pypluginstore.html")
            
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
                
                should_copy = True
                if os.path.isfile(html_dst):
                    src_mtime = os.path.getmtime(html_src)
                    dst_mtime = os.path.getmtime(html_dst)
                    if src_mtime <= dst_mtime:
                        should_copy = False
                
                if should_copy:
                    shutil.copyfile(html_src, html_dst)
                    os.chmod(html_dst, 0o644)
                    Domoticz.Log(f"Custom UI autoinstalled/updated: {html_dst}")
                else:
                    Domoticz.Debug("Custom UI is already up to date.")
        except Exception as e:
            Domoticz.Error(f"Custom UI autoinstall failed: {e}")

        if 1 not in Devices:
            Domoticz.Device(Name="API Payload", Unit=1, TypeName="Text", DeviceID="PPM_API_PAYLOAD", Used=1).Create()
        if 2 not in Devices:
            Domoticz.Device(Name="API Trigger", Unit=2, Type=244, Subtype=73, Switchtype=9, DeviceID="PPM_API_TRIGGER", Used=1).Create()
            
        self.fetch_registry()

        if Parameters.get("Mode5") == 'True':
            Domoticz.Log("Plugin Security Scan is enabled")
            secpoluserFile = os.path.join(plugins_dir, os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), "secpoluser.txt")
            if os.path.isfile(secpoluserFile):
                Domoticz.Log("secpoluser file found. Processing!!!")
                with open(secpoluserFile) as secpoluserFileHandle:
                    for line in secpoluserFileHandle:
                        line = line.strip()
                        if line.startswith("--->"):
                            secpoluserSection = line[4:]
                        elif line and not line.startswith("--->"):
                            if secpoluserSection not in self.secpoluser_list:
                                self.secpoluser_list[secpoluserSection] = []
                            self.secpoluser_list[secpoluserSection].append(line)
            else:
                self.secpoluser_list = {"Global":[]}

            for plugin_folder in os.listdir(plugins_dir):
                plugin_path = os.path.join(plugins_dir, plugin_folder)
                if os.path.isdir(plugin_path) and plugin_folder != os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                    for root, _, files in os.walk(plugin_path):
                        if '/.' in root.replace('\\', '/'):
                            continue
                        for file in files:
                            if file.endswith('.py'):
                                py_file = os.path.join(root, file)
                                self.parseFileForSecurityIssues(py_file, plugin_folder)

        exceptionFile = os.path.join(plugins_dir, os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), "exceptions.txt")
        if os.path.isfile(exceptionFile):
            Domoticz.Log("Exception file found. Processing!!!")
            with open(exceptionFile) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.exception_list.append(line)

        if Parameters["Mode4"] == 'All':
            Domoticz.Log("Updating All Plugins!!!")
            for d in os.listdir(plugins_dir):
                if os.path.isdir(os.path.join(plugins_dir, d)):
                    if d in self.plugin_data:
                        self.UpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                    elif d != os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                        Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")

        if Parameters["Mode4"] == 'AllNotify':
            Domoticz.Log("Collecting Updates for All Plugins!!!")
            for d in os.listdir(plugins_dir):
                if os.path.isdir(os.path.join(plugins_dir, d)):
                    if d in self.plugin_data:
                        self.CheckForUpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                    elif d != os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                        Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")

        Domoticz.Log("Plugin Manager Ready. Use the 'Custom' menu to manage plugins.")
        Domoticz.Heartbeat(60)

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug(f"onCommand called for Unit {Unit}: Command '{Command}', Level: {Level}")
        if Unit == 2 and Command.lower() == "on":
            if 1 in Devices:
                payload_str = Devices[1].sValue
                
                if len(payload_str) > 2000:
                    Domoticz.Error("API Payload exceeds length limit.")
                    Devices[1].Update(nValue=0, sValue="")
                    return

                Domoticz.Debug(f"API Payload received: {payload_str}")
                try:
                    Devices[1].Update(nValue=0, sValue="")
                    payload = json.loads(payload_str)
                    
                    if not isinstance(payload, dict):
                        raise ValueError("Payload must be a JSON object")
                    
                    self.tx_id = str(payload.get("tx_id", ""))[:50]
                    self.handleApiCommand(payload)
                except Exception as e:
                    Domoticz.Error(f"Failed to parse API payload: {e}")
                    self.sendApiResponse({"status": "error", "message": "Invalid JSON payload or structure"})

    def handleApiCommand(self, payload):
        import shutil
        action = str(payload.get("action", ""))
        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        
        if action == "list_plugins":
            installed_plugins = []
            for d in os.listdir(plugins_dir):
                if os.path.isdir(os.path.join(plugins_dir, d)) and not d.startswith("."):
                    installed_plugins.append(d)
                    
            self.sendApiResponse({
                "status": "success",
                "action": action,
                "data": self.plugin_data,
                "installed": installed_plugins,
                "manager_key": self.get_current_plugin_folder(),
                "update_status": self.getInstalledUpdateStatuses(installed_plugins, plugins_dir)
            })
        elif action == "install":
            plugin_key = payload.get("plugin_key")
            if plugin_key in self.plugin_data:
                plugin_author = self.plugin_data[plugin_key][0]
                plugin_repository = self.plugin_data[plugin_key][1]
                plugin_branch = self.plugin_data[plugin_key][3]
                self.InstallPythonPlugin(plugin_author, plugin_repository, plugin_key, plugin_branch)
                self.sendApiResponse({"status": "success", "action": action, "plugin_key": plugin_key})
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
            plugin_key = os.path.basename(plugin_key)
            plugin_target_dir = os.path.abspath(os.path.join(plugins_dir, plugin_key))
            
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
        update_status = {}
        for plugin_key in installed_plugins:
            plugin_dir = os.path.join(plugins_dir, plugin_key)
            update_status[plugin_key] = self.getGitUpdateStatus(plugin_dir)
        return update_status

    def getGitUpdateStatus(self, plugin_dir):
        if not os.path.isdir(os.path.join(plugin_dir, ".git")):
            return "unknown"

        try:
            subprocess.run(
                ["git", "fetch", "--quiet"],
                cwd=plugin_dir,
                env=self.get_git_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15
            )
            result = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"],
                cwd=plugin_dir,
                env=self.get_git_env(),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                return "unknown"

            ahead_behind = result.stdout.strip().split()
            if len(ahead_behind) < 2:
                return "unknown"

            behind = int(ahead_behind[1])
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

        if now.hour >= 12 and (self.last_update_date is None or self.last_update_date < now.date()):
            Domoticz.Log("Its time!!. Trigering Actions!!!")
            self.last_update_date = now.date()

            plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))

            if Parameters["Mode4"] == 'All':
                Domoticz.Log("Checking Updates for All Plugins!!!")
                for d in os.listdir(plugins_dir):
                    if os.path.isdir(os.path.join(plugins_dir, d)):
                        if d in self.plugin_data:
                            self.UpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                        elif d != os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                            Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")

            if Parameters["Mode4"] == 'AllNotify':
                Domoticz.Log("Collecting Updates for All Plugins!!!")
                for d in os.listdir(plugins_dir):
                    if os.path.isdir(os.path.join(plugins_dir, d)):
                        if d in self.plugin_data:
                            self.CheckForUpdatePythonPlugin(self.plugin_data[d][0], self.plugin_data[d][1], d)
                        elif d != os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))):
                            Domoticz.Log(f"Plugin: {d} cannot be managed with PyPluginStore!!.")

    def InstallPythonPlugin(self, ppAuthor, ppRepository, ppKey, ppBranch):
        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        Domoticz.Log("Installing Plugin:" + self.plugin_data[ppKey][2])
        ppCloneCmd = ["git", "clone", "-b", ppBranch, f"https://github.com/{ppAuthor}/{ppRepository}.git", ppKey]

        try:
            pr = subprocess.Popen(ppCloneCmd, cwd=plugins_dir, env=self.get_git_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out, error = pr.communicate()
            if out:
                Domoticz.Log("Succesfully installed: " + out.strip())
            if error and "Cloning into" in error:
                Domoticz.Log("Plugin " + ppKey + " installed Succesfully")
        except OSError as e:
            Domoticz.Error("Git Error: " + str(e.strerror))

        self.installDependencies(ppKey)
        return None

    def UpdatePythonPlugin(self, ppAuthor, ppRepository, ppKey):
        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        plugin_dir = os.path.join(plugins_dir, ppKey)

        if (ppKey in self.plugin_data and self.plugin_data[ppKey][2] in self.exception_list):
            Domoticz.Log("Plugin:" + self.plugin_data[ppKey][2] + " excluded by Exclusion file. Skipping!!!")
            return

        Domoticz.Log("Resetting and Updating Plugin:" + ppKey)
        env = self.get_git_env()

        try:
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=plugin_dir, env=env, capture_output=True, text=True)
            res = subprocess.run(["git", "pull", "--force"], cwd=plugin_dir, env=env, capture_output=True, text=True)
            out = res.stdout
            if out:
                if "Already up to date" in out or "Already up-to-date" in out:
                   Domoticz.Log("Plugin " + ppKey + " already Up-To-Date")
                elif "Updating" in out and "error" not in out.lower():
                   Domoticz.Log("Succesfully pulled gitHub update for plugin " + ppKey)
        except OSError as e:
            Domoticz.Error("Git Error: " + str(e.strerror))

        self.installDependencies(ppKey)
        return None

    def CheckForUpdatePythonPlugin(self, ppAuthor, ppRepository, ppKey):
        if ppKey in self.plugin_data and self.plugin_data[ppKey][2] in self.exception_list:
            return None

        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        plugin_dir = os.path.join(plugins_dir, ppKey)
        env = self.get_git_env()

        try:
            prFetch = subprocess.Popen(["git", "fetch"], cwd=plugin_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            prFetch.communicate()
            
            pr = subprocess.Popen(["git", "status", "-uno"], cwd=plugin_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            out, _ = pr.communicate()
            if out:
                if "up to date" in out or "up-to-date" in out:
                   Domoticz.Log("Plugin " + ppKey + " already Up-To-Date")
                elif "Your branch is behind" in out:
                   Domoticz.Log("Found that we are behind on plugin " + ppKey)
                   self.fnSelectedNotify(ppKey)
        except OSError as e:
            Domoticz.Error("Git Error: " + str(e.strerror))

        return None

    def fnSelectedNotify(self, plugin_key):
        plugin_name = self.plugin_data[plugin_key][2] if plugin_key in self.plugin_data else plugin_key
        MailSubject = platform.node() + ": Domoticz Plugin Updates Available for " + plugin_name
        MailBody = plugin_name + " has updates available!!"
        Domoticz.SendNotification(MailSubject, MailBody)
        return None

    def parseIntValue(self, s):
        try:
            return int(s)
        except:
            return None

    def is_private_ip(self, ip_str):
        try:
            octets = [int(o) for o in ip_str.split('.')]
            if len(octets) != 4: return False
            if octets[0] == 127 or octets[0] == 10: return True
            if octets[0] == 172 and 16 <= octets[1] <= 31: return True
            if octets[0] == 192 and octets[1] == 168: return True
            if octets[0] == 169 and octets[1] == 254: return True
            if octets[0] == 0: return True
            if octets[1] == 0 and octets[2] == 0 and octets[3] == 0: return True
            return False
        except:
            return False

    def parseFileForSecurityIssues(self, pyfilename, pypluginid):
        import ast
        if Parameters.get("Mode5") == 'True':
            Domoticz.Log(f"Scanning {pyfilename} for security issues...")

        if pypluginid not in self.secpoluser_list:
            self.secpoluser_list[pypluginid] = []

        ip_pattern = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')

        try:
            MAX_FILE_SIZE = 5 * 1024 * 1024
            with open(pyfilename, "r", encoding="utf-8", errors="ignore") as file:
                source_code = file.read(MAX_FILE_SIZE)
                if file.read(1):
                    Domoticz.Error(f"Plugin file {pyfilename} exceeds 5MB limit. Plugin considered UNSAFE.")
                    return

            try:
                tree = ast.parse(source_code)
            except Exception as e:
                Domoticz.Error(f"Failed to parse plugin file {pyfilename}: {e}.")
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
                    func_base_name = node.func.id if isinstance(node.func, ast.Name) else (node.func.attr if isinstance(node.func, ast.Attribute) else "")

                    exact_matches = {'os.system', 'os.popen', 'eval', 'exec', '__import__', 'compile', 'pickle.loads', 'pickle.load', 'os.remove', 'os.unlink', 'shutil.rmtree'}

                    if func_full_name in exact_matches:
                        self.findings.append((node.lineno, f"Suspicious Call: {func_full_name}"))
                    elif func_full_name.startswith('subprocess.'):
                        is_shell = False
                        for keyword in node.keywords:
                            if keyword.arg == 'shell' and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                                is_shell = True
                        if is_shell:
                            self.findings.append((node.lineno, f"Dangerous Subprocess (shell=True): {func_full_name}"))
                    elif func_base_name in {'eval', 'exec', '__import__', 'compile'}:
                        self.findings.append((node.lineno, f"Suspicious Call: {func_base_name}"))

                    self.generic_visit(node)

            scanner = SecurityScanner()
            scanner.visit(tree)

            ast_findings_map = {}
            for lineno, finding in scanner.findings:
                if lineno not in ast_findings_map:
                    ast_findings_map[lineno] = []
                ast_findings_map[lineno].append(finding)

            lines = source_code.splitlines()
            for i, text in enumerate(lines):
                lineNum = i + 1
                clean_text = text.strip()

                if not clean_text or clean_text.startswith('#') or '# security-ignore' in text or '# nosec' in text:
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
                        Domoticz.Error(f"Security Finding in {pypluginid}: --> {finding} <-- LINE: {lineNum}")

        except Exception as e:
            Domoticz.Error(f"Error processing {pyfilename}: {str(e)}")

    def restartDomoticz(self):
        Domoticz.Log("Domoticz service restart requested from PyPluginStore UI.")
        helper = r'''
import subprocess
import time
commands = [
    ["sudo", "-n", "systemctl", "restart", "domoticz.service"],
    ["systemctl", "restart", "domoticz.service"],
]
time.sleep(2)
for command in commands:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if result.returncode == 0: break
    except Exception: pass
'''
        try:
            subprocess.Popen([sys.executable, "-c", helper], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:
            Domoticz.Error(f"Failed to schedule Domoticz restart: {e}")

    def installDependencies(self, plugin_key):
        plugins_dir = os.path.abspath(os.path.join(Parameters.get("HomeFolder", str(os.getcwd()) + "/"), ".."))
        plugin_dir = os.path.join(plugins_dir, plugin_key)
        requirementsFile = os.path.join(plugin_dir, "requirements.txt")
        shared_deps_dir = os.path.join(plugins_dir, os.path.basename(os.path.normpath(Parameters.get('HomeFolder', str(os.getcwd()) + '/'))), ".shared_deps")

        def check_cmd(cmd):
            try:
                subprocess.run([cmd, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except: return False

        if os.path.isfile(requirementsFile):
            Domoticz.Log("requirements.txt found for plugin: " + plugin_key)
            os.makedirs(shared_deps_dir, exist_ok=True)

            installCmd = None
            if check_cmd("uv"):
                installCmd = ["uv", "pip", "install", "-r", requirementsFile, "--target", shared_deps_dir]
            elif check_cmd("pip3"):
                installCmd = ["pip3", "install", "-r", requirementsFile, "--target", shared_deps_dir]

            if installCmd:
                try:
                    pr = subprocess.Popen(installCmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    out, error = pr.communicate()
                    if pr.returncode == 0:
                        Domoticz.Log("Dependencies installed successfully.")
                    else:
                        Domoticz.Error("Error installing dependencies: " + error.strip())
                except Exception as e:
                    Domoticz.Error("Error running installation command: " + str(e))
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

def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug( "'" + x + "':'" + str(Parameters[x]) + "'")
    return