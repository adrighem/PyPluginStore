# PyPluginStore for Domoticz

Install, update, remove, and manage Domoticz Python plugins from one web interface. PyPluginStore supports verified Release packages and Git repositories on Linux, including Raspberry Pi, and Windows.

Individual third-party plugins may still be Linux-only or Windows-only depending on their dependencies and OS integrations.

<img src="pypluginstore.png" alt="PyPluginStore logo" width="180" height="180">

> This project is based on the original [ycahome/pp-manager](https://github.com/ycahome/pp-manager). Thanks to the original maintainers and contributors for their hard work.

## Start here

- **Installing PyPluginStore?** Follow [Install and first run](#install-and-first-run).
- **Learning the dashboard?** See [Use the dashboard](#use-the-dashboard).
- **Recovering from a problem?** Start with [Troubleshooting](#troubleshooting).
- **Using Releases, Git, or a private source?** See [Advanced operation](#advanced-operation).
- **Registering a plugin?** See [Plugin authors and contributors](#plugin-authors-and-contributors).
- **Need help or want to report a bug?** See [Support and project links](#support-and-project-links).

## Key features

- **Plugin Store UI:** Search, sort, filter, install, update, remove, and restore plugins from the Domoticz **Custom** menu. Choose the Domoticz-themed or compact PyPlugin layout.
- **Release-first delivery:** Use checksum-pinned stable Release archives when certified, with Git retained as a supported channel.
- **Safe channel migration:** Move an existing Git checkout to Release only after repository, history, local-file, dependency, and rollback checks.
- **Self-update:** Manage PyPluginStore from its own card while preserving a coherent browser, backend, and on-disk runtime.
- **Update status:** See the active channel, installed and available versions, verification failures, migration blockers, and rollback availability.
- **Scheduled operation:** Apply eligible updates automatically or use the default notification-only mode.
- **Dependency isolation:** Resolve all installed plugin requirements into an isolated, recoverable `.shared_deps` generation with `uv` or the Domoticz Python interpreter's `pip`.
- **Remote and local registries:** Use the public registry with bundled fallback, or add private and local Git sources through [`registry_local.json`](docs/registry_local.md).

## Release integrity and trust

Before activating a Release package, PyPluginStore verifies its size, SHA-256, file layout, Python syntax, package identity, Domoticz key, and complete dependency generation. These integrity checks detect changes relative to the accepted or locally certified release. They do not determine whether third-party code is trustworthy.

Installing or auto-updating a plugin means trusting its publisher and the PyPluginStore registry distribution channel. After an indexed release establishes the policy anchor, direct refresh also trusts the configured upstream provider. A local registry entry replaces the reviewed public source with one you explicitly select and trust. Review source code when that trust is not appropriate.

Dependency installation can execute third-party Python build backends. The generated dependency folder isolates package files from the global Python environment, but it is not an execution sandbox. Trust the requirement sources and package indexes used by each plugin.

## Install and first run

### Requirements

| Requirement | What to check |
| --- | --- |
| Domoticz | Python plugin support is enabled in the Domoticz about box. |
| Operating system | Linux, including Raspberry Pi, or Windows. |
| Python | PyPluginStore uses the Python 3 runtime that runs Domoticz. CI tests the current Python 3 release on Ubuntu and Windows; older Python runtimes are not guaranteed. |
| Git | `git --version` works for the Domoticz service account. |
| Write access | The Domoticz service account can write to `domoticz/plugins`, `domoticz/www/templates`, and `domoticz/www/images`. In Docker, persist the plugins folder and make all three locations writable inside the container. |
| Dependency installer | Needed only for plugins with requirements. `uv` is preferred and must be in `/usr/local/bin` or the OS default executable path used by PyPluginStore. Otherwise, the Python interpreter running Domoticz must support `python -m pip`. Standalone `pip3` and `pip` commands are not used. |
| Network | Remote registry refreshes, Git operations, and direct Release checks need outbound Git and HTTPS access. Startup can use bundled or cached registry data during a temporary outage. |

On Linux, clone as the OS account that runs Domoticz. Avoid leaving a root-owned checkout that the service cannot update. On a systemd host, identify the configured account with:

```bash
systemctl show -p User --value domoticz.service
ps -o user= -C domoticz
```

If needed, verify access without changing files. Replace `domoticz` and the paths for your installation:

```bash
DOMOTICZ_USER=domoticz
sudo -u "$DOMOTICZ_USER" test -w /path/to/domoticz/plugins
sudo -u "$DOMOTICZ_USER" test -w /path/to/domoticz/www/templates
sudo -u "$DOMOTICZ_USER" test -w /path/to/domoticz/www/images
```

No output and a zero exit status mean the location is writable. If a web
subdirectory does not exist yet, verify write access to its nearest existing
parent instead.

### Install

1.  Open a shell on the Domoticz host and go to the Domoticz `plugins` folder.

Linux:

```bash
cd /path/to/domoticz/plugins
```

Windows PowerShell:

```powershell
cd C:\path\to\domoticz\plugins
```

2.  Clone PyPluginStore as `00-PyPluginStore`.

Linux:

```bash
git clone https://github.com/adrighem/PyPluginStore.git 00-PyPluginStore
```

Windows PowerShell:

```powershell
git clone https://github.com/adrighem/PyPluginStore.git 00-PyPluginStore
```

Clone the complete repository. The runtime identity message after restart is the authoritative check that the required files are present and agree.

3.  Restart Domoticz.

Linux example:

```bash
sudo systemctl restart domoticz.service
```

Windows service example:

```powershell
Restart-Service -Name Domoticz
```

Docker Compose example, run on the container host:

```bash
docker compose restart domoticz
```

4.  In Domoticz, go to **Setup -> Hardware** and add enabled hardware of type **PyPluginStore**.

5.  Go to **Setup -> Users**, edit your user, and enable the **Custom** menu.

6.  Open **Custom -> pypluginstore**.

Success means the dashboard shows the plugin registry and its controls are available. The Domoticz log should also contain:

```text
PyPluginStore: Initialized version ...
PyPluginStore: PyPluginStore runtime identity: v... build ...
PyPluginStore: Plugin Manager Ready. Use the 'Custom' menu to manage plugins.
```

If **PyPluginStore** is not available as a hardware type, restart Domoticz and re-check Python plugin support. If the log says `Custom UI autoinstall failed`, check the web-folder permissions.

### Why `00-PyPluginStore`?

Domoticz loads Python plugins alphabetically by folder name. Prefixing with `00-` makes the manager load first, so it can add the shared dependency environment before other plugins import their libraries.

## Use the dashboard

Plugin cards show their platform, source, active Git or Release channel, installed version, and available version. Use **Refresh status** to check upstream state explicitly, then use the card's **Install**, **Update**, **Remove**, channel, or restore action. A completed code or dependency change may require a Domoticz restart before the new plugin version is loaded.

The header includes a persistent layout switch between the Domoticz theme and the compact PyPlugin view. Search, sorting, and installed-only filtering work in either layout.

### Manager version and recovery status

The status in the Plugin Store header is the authoritative manager-version
check. A healthy, matching installation keeps this status quiet. The build ID
covers `plugin.py`, `package_registry.py`, `package_identity.py`,
`release_providers.py`, `release_domain.py`, and `pypluginstore.html`, so
same-version Git updates are detected as well as normal release version
changes.

PyPluginStore compares the page running in the browser, the page deployed under
`domoticz/www/templates`, the backend loaded by Domoticz, and the files currently
installed on disk. If they differ, install, update, remove, rollback, channel,
and Local registry changes become read-only. Status checks and **Restart
Domoticz** remain available for recovery:

* **Restart required:** restart Domoticz, then reopen the Plugin Store.
* **Browser page differs:** hard-refresh the page. If the header separately says
  a restart is required, restart first and then reload it.
* **Custom page not synchronized:** check write access to
  `domoticz/www/templates`, restart Domoticz, and reload the page.
* **Identity could not be verified:** repair or reinstall the fixed runtime
  files before making changes.

Self-update progress and recovery instructions also appear in this header
status. A documentation-only Git update can keep the same build ID and does not
require a restart. The build ID detects local generation mismatches; it is not a
cryptographic signature or proof that the upstream source is trustworthy.

### Settings on the hardware page

| Setting | Behavior |
| --- | --- |
| **Auto Update: All** | Checks registry-managed installed plugins at startup and once per day after 12:00 local time. It applies eligible Git or Release updates and only performs a Git-to-Release migration automatically when source continuity is fully proven. |
| **Auto Update: All (NotifyOnly)** | The default. Runs the same scheduled checks and reports newer versions or available Release choices without applying updates. Native notifications are sent when the Domoticz runtime exposes its notification service. |
| **Auto Update: None** | Disables scheduled update and notification-only actions. Manual refresh and update controls remain available. |
| **Debug** | Set to **True** for detailed logging. |
| **Git Ownership Repair** | Leave **Disabled** unless Git still rejects a managed repository after PyPluginStore retries with a repository-scoped `safe.directory` setting. When enabled, PyPluginStore may recursively change that repository's owner to the Domoticz process user and group before retrying Git. |

## Advanced operation

### Local registry overrides

Use `registry_local.json` when you want your Domoticz installation to track a different branch of a public plugin, or when you want to add a plugin that is not in the public registry yet.

Open **Local registry** in the Plugin Store header to add, edit, or delete entries. You can start from an existing public plugin to create an override, or create a blank entry for a private, local, or unpublished repository. Local entries are loaded after the public registry, override matching public entries, and show a **Local** badge.

The manager validates entries without contacting the repository and saves changes atomically. A valid legacy local file is backed up and atomically rewritten to schema v2 on first load. If `registry_local.json` is malformed, the dialog becomes read-only and all Release management pauses until the file is repaired and reloaded. See the [`registry_local.json` how-to](docs/registry_local.md) for the UI workflow, v2 examples, and GitHub, GitLab, Codeberg, SSH, and local repository sources. If a plugin card shows **Repo mismatch**, see the [Repo Mismatch warning](docs/registry_local.md#repo-mismatch-warning).

Local registry entries remain Git-managed. They override the reviewed public source for that installation, so only add repositories you trust and prefer HTTPS or SSH for network sources.

### Release and Git channels

Release eligibility begins with an accepted public index anchor. A later
explicit **Refresh status** can extend that release lineage through its
configured provider. Provider and index revisions are separate counters;
PyPluginStore reconciles them by immutable release identity and lineage, so an
index that trails a provider-certified install is never presented as a
downgrade.

| Channel | When it is used | Update source | Return path |
| --- | --- | --- | --- |
| **Release** | A public package has a fresh certified Release target. | An immutable archive from the reviewed index, or a newer upstream release certified by an explicit **Refresh status**. | Restore the most recently retained verified Release or Git backup when one exists. |
| **Git** | No eligible Release is available, policy selects Git, or the package is PyPluginStore itself. | Commits from the configured branch. | An eligible checkout can move to Release after migration checks pass. |
| **Local override** | You choose a private repository, fork, local source, or different branch. | Git from the source in `registry_local.json`. | Remove the override to return to public policy. An existing Release folder must first restore Git or be removed and reinstalled. |

**Auto Update: All** can move a clean Git checkout to Release only when the archive proves source continuity and the installed commit is the same or older. Other permitted cases can require an exact confirmation in the UI; repository mismatches, unsafe paths, locks, submodules, and contradictory evidence remain blocked. Notify-only mode reports Release as a channel choice and does not apply it.

Once a plugin has used Release, invalid or unavailable Release metadata blocks the operation instead of silently falling back to a branch update. Rollback is one-step recovery to the most recently retained verified state; a fresh install has no previous state to restore.

See [Release and Git management](docs/release_management.md) for provider policies, migration evidence, local-data approval, downgrade confirmation, backup naming, and recovery behavior.

### Restart button

The **Restart Domoticz** button asks the host OS to restart Domoticz. This is not handled by a Domoticz JSON API endpoint.

After the request is accepted, the page disables command controls and waits for
a new backend instance whose build matches the installed files. Checks use the
lightweight manager status command with bounded HTTP timeouts and backoff. The
plugin list is loaded once after recovery is verified. If Domoticz does not
recover within two minutes, controls are restored and the page shows manual
recovery guidance inline.

The built-in Linux restart supports a host service named `domoticz.service`.
Typical containers do not run `systemctl` or `service`, so restart those from the
container host or orchestrator instead.

<details>
<summary>Linux restart authorization</summary>

On Linux it tries these non-interactive service commands, in order:

1. `systemctl restart domoticz.service`
2. `sudo -n systemctl restart domoticz.service`
3. `service domoticz restart`
4. `sudo -n service domoticz restart`

For the button to work, the user running Domoticz must have permission to restart the service. On Linux, use either a tightly scoped polkit rule or a tightly scoped sudoers rule. Do not grant broad passwordless sudo such as `NOPASSWD: ALL`, and do not allow arbitrary `systemctl` commands.

#### Polkit authorization (recommended)

On systemd hosts, polkit can authorize the direct `systemctl restart domoticz.service` attempt without using `sudo`. This matches the first Linux command PyPluginStore tries.

Create `/etc/polkit-1/rules.d/49-pypluginstore-domoticz-restart.rules` as root:

```javascript
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        subject.user == "domoticz" &&
        action.lookup("unit") == "domoticz.service" &&
        action.lookup("verb") == "restart") {
        return polkit.Result.YES;
    }
});
```

Keep the rule owned by root and not writable by the Domoticz user:

```bash
sudo chown root:root /etc/polkit-1/rules.d/49-pypluginstore-domoticz-restart.rules
sudo chmod 0644 /etc/polkit-1/rules.d/49-pypluginstore-domoticz-restart.rules
```

#### Sudoers configuration

If you prefer to use `sudo` or if the host does not support polkit, first find the OS user that runs Domoticz and the absolute command path that sudoers must match:

```bash
systemctl show -p User --value domoticz.service
ps -o user= -C domoticz
command -v systemctl
```

Then create a dedicated sudoers file with `visudo`:

```bash
sudo visudo -f /etc/sudoers.d/pypluginstore-domoticz-restart
```

Add one line, replacing `domoticz` with the Domoticz OS user and `/usr/bin/systemctl` with the `command -v systemctl` output:

```sudoers
domoticz ALL=(root) NOPASSWD: /usr/bin/systemctl restart domoticz.service
```

This matches the second Linux command PyPluginStore tries: `sudo -n systemctl restart domoticz.service`. The command must stay limited to `restart domoticz.service`; broader rules would let the Domoticz process control unrelated system services.

Validate the sudoers syntax and check the permission without prompting:

```bash
sudo visudo -c -f /etc/sudoers.d/pypluginstore-domoticz-restart
sudo chown root:root /etc/sudoers.d/pypluginstore-domoticz-restart
sudo chmod 0440 /etc/sudoers.d/pypluginstore-domoticz-restart
sudo -u domoticz sudo -n -l /usr/bin/systemctl restart domoticz.service
```

If the host does not use systemd, add the same kind of narrow rule for the exact service command path returned by `command -v service`:

```sudoers
domoticz ALL=(root) NOPASSWD: /usr/sbin/service domoticz restart
```

</details>

<details>
<summary>Windows restart authorization and diagnostics</summary>

On Windows, PyPluginStore creates a one-shot Task Scheduler task at the highest
privilege level and runs it as `SYSTEM`. The Domoticz service account must be
allowed to create and start that task. The task then tries PowerShell
`Restart-Service -Name Domoticz` and `sc stop/start Domoticz`. If task creation,
launch, or service restart is not permitted, Domoticz keeps running.

PyPluginStore writes `restart_domoticz.ps1` and `restart_domoticz.cmd` beside the
manager and schedules `\PyPluginStore-Domoticz-Restart`. If task creation or
launch fails, `restart_domoticz.log` contains the `schtasks.exe` output. If the
task starts but no helper output appears, check its Task Scheduler history. Use
any recorded `Restart-Service` or `sc.exe` output to correct the service name,
permissions, or Windows service configuration.

</details>

Restart diagnostics for both platforms are written to `restart_domoticz.log` in
the PyPluginStore folder.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **PyPluginStore hardware type is missing** | Restart Domoticz, confirm Python plugin support, and inspect the Domoticz log for plugin load errors. |
| **Custom -> pypluginstore is missing** | Enable the **Custom** menu for the current Domoticz user. Confirm `www/templates/pypluginstore.html` and `www/images/pypluginstore-icon.png` exist, check for `Custom UI autoinstall failed`, then hard-refresh the browser. In Docker, inspect these paths inside the running container. |
| **The header is read-only** | Follow its recovery message. Usually this means restarting Domoticz, hard-refreshing a stale browser page, repairing web-folder access, or restoring the complete runtime bundle. |
| **Git ownership or Repo mismatch appears** | Correct the checkout owner or enable **Git Ownership Repair** only for the documented fallback. For an intentional fork or different repository, create a matching [local override](docs/registry_local.md#repo-mismatch-warning). |
| **PyPluginStore self-update is refused** | Its own folder must be a clean Git checkout at the repository root, with an upstream and a fast-forward-only update path. Remove unintended tracked changes or local commits. Check the header and `self_update.log`. |
| **A Local registry error appears** | Correct `registry_local.json`, then select **Reload entries**. Release management remains paused while the file is invalid. |
| **Dependency resolution fails** | Follow [Dependency failures](#dependency-failures) and inspect the Domoticz log. |
| **Restart times out** | Restart Domoticz externally, then check `restart_domoticz.log`. On Windows, also inspect the `\PyPluginStore-Domoticz-Restart` Task Scheduler history. |

### Dependency failures

PyPluginStore treats `.shared_deps` as a generated environment, not a folder for
manual package installation. It resolves all installed plugins' requirements
together and swaps in a complete, recoverable generation.

If dependency installation fails:

1. Check the Domoticz log for the plugin and requirement that caused the combined resolution failure.
2. Verify that `uv` is visible in PyPluginStore's sanitized executable path, or that the Python interpreter running Domoticz supports `python -m pip`.
3. Resolve incompatible requirement pins or host-specific build prerequisites, then retry the plugin operation.

Do not run `pip --target` directly into `.shared_deps`. A later generation swap can replace those files, and manual changes are not represented in the environment manifest.

## Plugin authors and contributors

### Register a plugin

Before opening a pull request:

1. Keep `plugin.py` at the repository root and use a stable Domoticz plugin key.
2. Use a canonical, credential-free HTTPS repository URL and identify its branch.
3. Record Linux, Windows, both, or unknown platform support.
4. Add one complete registry-v2 package record to the `packages` array in `registry.json`.
5. Follow the schema, identity, and delivery-policy guidance in [Registry maintenance](CONTRIBUTING.md#registry-maintenance).

CI validates the repository, root `plugin.py`, and exact Domoticz key. The weekly
or manually triggered registry scan refreshes repository metadata and accepted
Releases, then opens a separate pull request for review. Merging another registry
edit does not itself refresh repository timestamps.

### Contribute to PyPluginStore

See [CONTRIBUTING.md](CONTRIBUTING.md) for registry and Release maintenance,
generated `plugin.py` requirements, and release commit conventions. Run
`python -m pytest -q` before submitting a code change.

## License

PyPluginStore is open-source software licensed under the GNU General Public License version 3, or (at your option) any later version. See the [LICENSE](LICENSE) file for the full text of the license.

## Support and project links

- [Domoticz forum discussion](https://forum.domoticz.com/viewtopic.php?t=44626) for usage questions and community help
- [GitHub Issues](https://github.com/adrighem/PyPluginStore/issues) for reproducible bugs and feature requests
- [Releases](https://github.com/adrighem/PyPluginStore/releases)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)
- [GNU GPL v3 or later license](LICENSE)
