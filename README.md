# PyPluginStore for Domoticz

Install, update, remove, and manage Domoticz Python plugins and frontend themes from one intuitive web interface. PyPluginStore supports verified Release packages and Git repositories on Linux, including Raspberry Pi, and Windows.

<img src="pypluginstore-social-preview.jpg" alt="PyPluginStore Banner" width="100%">

> This project is based on the original [ycahome/pp-manager](https://github.com/ycahome/pp-manager). Thanks to the original maintainers and contributors for their hard work.

## Key Features

- **Unified Store UI:** A tabbed interface separating Plugins and Themes. Search, sort, filter, install, update, remove, and restore items directly from the Domoticz **Custom** menu.
- **Dependency Isolation (Plugins):** Resolves all installed plugin requirements into an isolated, recoverable `.shared_deps` generation using `uv` or the Domoticz Python interpreter's `pip`. Bypasses PEP 668 restrictions safely since installations remain strictly localized without polluting global system packages.
- **Staging-and-Mirror Architecture (Themes):** Protects your Domoticz `www/styles/` path by cloning theme repositories into an isolated staging `.theme_sources` folder, discarding `.git` metadata and only mirroring the necessary frontend CSS/JS files.
- **Javascript Scanning (Themes):** Staged themes are statically scanned for `custom.js` files; the UI automatically badges them with a warning, making you aware of dynamic execution footprints.
- **Release-first delivery:** Utilizes checksum-pinned stable Release archives when certified, with fallback support for direct Git checkouts.
- **Safe channel migration:** Migrates existing Git checkouts to Release versions automatically after verifying repository continuity and rolling-back capabilities.
- **Self-update:** PyPluginStore manages its own updates seamlessly while preserving browser, backend, and on-disk runtime cohesion.

## Install and First Run

### Requirements

| Requirement | What to check |
| --- | --- |
| Domoticz | Python plugin support is enabled in the Domoticz about box. |
| Operating system | Linux, including Raspberry Pi, or Windows. |
| Python | PyPluginStore uses the Python 3 runtime that runs Domoticz. CI tests the current Python 3 release on Ubuntu and Windows. |
| Git | `git --version` works for the Domoticz service account. |
| Write access | The Domoticz service account can write to `domoticz/plugins`, `domoticz/www/templates`, `domoticz/www/styles`, and `domoticz/www/images`. |

### Installation

1. Open a shell on the Domoticz host and navigate to the Domoticz `plugins` folder.
   ```bash
   cd /path/to/domoticz/plugins
   ```

2. Clone PyPluginStore as `00-PyPluginStore`. (The `00-` prefix is mandatory to ensure PyPluginStore's dependency isolation engine runs *before* other plugins load).
   ```bash
   git clone https://github.com/adrighem/PyPluginStore.git 00-PyPluginStore
   ```

3. Restart Domoticz.
   ```bash
   sudo systemctl restart domoticz.service
   ```

4. In Domoticz, go to **Setup -> Hardware** and add a new hardware type of **PyPluginStore**. Ensure it is enabled.

5. Go to **Setup -> Users**, edit your user, and ensure the **Custom** menu is checked.

6. Open **Custom -> pypluginstore**. The plugin store should appear and immediately connect.

## Using the Dashboard

The dashboard provides two main tabs:

### 🧩 Plugins Tab
Plugin cards display their target platform, source, currently active channel (Git or Release), installed version, and any available version updates. Dependency installations for Python plugins are handled natively in the background.

### 🎨 Themes Tab
Theme cards list responsive Domoticz skins. PyPluginStore shields the web directory by staging downloads. Themes utilizing Javascript (`custom.js`) will display a yellow **JS** badge warning. Note that `default`, `elemental`, and other built-in Domoticz themes are heavily protected from accidental deletion or overwriting by the manager.

### Settings on the Hardware Page

| Setting | Behavior |
| --- | --- |
| **Auto Update: All** | Checks registry-managed items at startup and daily after 12:00. Applies eligible updates automatically. |
| **Auto Update: All (NotifyOnly)** | The default. Runs background scheduled checks but only reports newer versions via the notification service without applying them. |
| **Auto Update: None** | Disables all background updates and notifications. |
| **Debug** | Set to **True** for detailed Python logging. |

## Advanced Operation

### Local Registry Overrides
Use `registry_local.json` to have your installation track different branches or to add private plugins not listed in the public catalog. 

Open **Local registry** in the Plugin Store header to add, edit, or delete entries. Local entries are loaded after the public registry, override public entries, and show a **Local** badge in the interface.

*See the [`registry_local.json` how-to](docs/registry_local.md) for more examples.*

## Support and Contributing

- [Issue Tracker](https://github.com/adrighem/PyPluginStore/issues)
- [Releases](https://github.com/adrighem/PyPluginStore/releases)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)
- [GNU GPL v3 or later license](LICENSE)

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

We support two ways to register your plugin, with **Release-based delivery being highly preferred and recommended**:

1. **Release-Based Delivery (Preferred):** Distributes static tagged release archives (ZIP/tarball). Offers superior stability, checksum validation, and faster downloads. Set `delivery.preferred` to `"release_if_indexed"`.
2. **Git-Based Delivery:** Clones your branch directly. Best for early development or nightly builds. Set `delivery.preferred` to `"git"`.

For complete configuration schemas, validation rules, and examples, follow the [Plugin Delivery Options guidance in CONTRIBUTING.md](CONTRIBUTING.md#plugin-delivery-options).

#### Pre-submission Checklist

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
