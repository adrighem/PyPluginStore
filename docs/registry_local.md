# `registry_local.json` How-To

Use `registry_local.json` for private repositories, local forks, test branches,
LAN repositories, or plugins that are not ready for the public registry. Local
packages are Git-managed and do not participate in the public release index.

## Manage entries in the UI

Open **Local registry** in the Plugin Store header. You can add a blank package,
copy a public package into a local override, edit its repository or branch, and
delete an override without deleting the installed plugin.

`package_id` is the stable PyPluginStore identity and cannot be renamed while
editing. Create a new package and remove the old entry if you need another ID.
The `domoticz_key` is separate: it is the exact `<plugin key="...">` value that
Domoticz uses to bind hardware configuration.

Saving validates the document locally and writes it atomically; it does not
contact or install the repository. Concurrent edits are protected by a content
revision. Repository URLs containing HTTP credentials are rejected, so use the
Git credentials or SSH keys available to the Domoticz OS user.

If JSON is malformed, the dialog stays read-only and shows the parse error. All
Release management is also paused so invalid local policy cannot be bypassed.
Correct the file manually, then select **Reload entries**.

## Automatic upgrade from the old format

On the first successful read of a valid package-keyed v1 file, PyPluginStore:

1. preserves its exact bytes as `registry_local.v1.backup.json`;
2. derives `domoticz_key` only from the exact installed package's `plugin.py`
   when available;
3. atomically rewrites `registry_local.json` as schema v2.

An uninstalled package may therefore migrate with an empty `domoticz_key`.
Malformed input, colliding package IDs, unsafe paths, or an incompatible backup
leave the original file untouched and read-only. The active v2 file never keeps
legacy owner/author/repo aliases, positional entries, or keyed package objects.

## Manual file format

The file is stored beside the installed PyPluginStore plugin:

- Linux: `/path/to/domoticz/plugins/00-PyPluginStore/registry_local.json`
- Windows: `C:\path\to\domoticz\plugins\00-PyPluginStore\registry_local.json`

Use schema v2 with an explicit `packages` array:

```json
{
  "schema_version": 2,
  "packages": [
    {
      "package_id": "HeatingLab",
      "domoticz_key": "HEATING-LAB",
      "description": "Private heating automation plugin.",
      "repository": {
        "url": "git@github.com:my-org/domoticz-heating-lab.git",
        "branch": "main"
      },
      "platforms": ["linux"]
    }
  ]
}
```

The JSON must use double quotes and contain no comments or trailing commas.
Package IDs must be unique even when case is ignored.

| Field | Use |
| --- | --- |
| `package_id` | Stable PyPluginStore package identity; normally the new-install folder name. |
| `domoticz_key` | Exact Domoticz `<plugin key="...">`; it may differ from `package_id`. Use `""` only when it cannot yet be certified. |
| `description` | Text shown on the package card. |
| `repository.url` | Complete Git clone source: HTTPS, SSH, `file://`, or an HTTP/LAN source you explicitly trust. |
| `repository.branch` | Branch to clone or update. |
| `platforms` | `[]`, `["linux"]`, `["windows"]`, or both. Empty means unknown. |

Unlike the public registry, a local record has no `delivery` policy because it
always stays on Git. A local entry replaces the reviewed public source for that
installation, so only add repositories you trust. Prefer HTTPS or SSH for
network sources. Plain HTTP is accepted but has no transport protection;
`file://` and LAN sources belong to the host's local trust boundary.

## Common use cases

### Private repository over SSH

```json
{
  "schema_version": 2,
  "packages": [
    {
      "package_id": "HeatingLab",
      "domoticz_key": "HEATING-LAB",
      "description": "Private heating automation plugin.",
      "repository": {
        "url": "git@github.com:my-org/domoticz-heating-lab.git",
        "branch": "main"
      },
      "platforms": ["linux"]
    }
  ]
}
```

### GitLab and Codeberg repositories

```json
{
  "schema_version": 2,
  "packages": [
    {
      "package_id": "SabnzbdLab",
      "domoticz_key": "SABNZBD-LAB",
      "description": "Local GitLab plugin entry.",
      "repository": {
        "url": "https://gitlab.com/my-group/domoticz-sabnzbd-lab.git",
        "branch": "main"
      },
      "platforms": ["linux"]
    },
    {
      "package_id": "StromerLab",
      "domoticz_key": "STROMER-LAB",
      "description": "Local Codeberg plugin entry.",
      "repository": {
        "url": "https://codeberg.org/my-user/domoticz-stromer-lab.git",
        "branch": "main"
      },
      "platforms": ["linux", "windows"]
    }
  ]
}
```

### Local or LAN repository

```json
{
  "schema_version": 2,
  "packages": [
    {
      "package_id": "GarageController",
      "domoticz_key": "GARAGE-CONTROLLER",
      "description": "Garage controller from a local bare repository.",
      "repository": {
        "url": "file:///srv/git/domoticz-garage-controller.git",
        "branch": "main"
      },
      "platforms": ["linux"]
    }
  ]
}
```

## Override a public package

Use the same `package_id` as the public record. The local package replaces that
one definition on this installation and stays Git-managed. Prefer to keep its
`domoticz_key` unchanged so Domoticz continues to recognize the same hardware.

## Repo mismatch warning

**Repo mismatch** means an installed checkout does not match the repository in
the active public or local record for that `package_id`. PyPluginStore will not
update it automatically because it may be an intentional fork.

- If the checkout is intentional, add or update a matching local override.
- If the public repository is intended, remove the mismatched folder, install
  the registered package, and restart Domoticz.

Keeping the same `domoticz_key` preserves Domoticz's hardware identity even
when the package ID or physical folder differs. Deleting a local override never
deletes the installed folder.

## Troubleshooting

Validate a manually edited file before reloading it:

```bash
python -m json.tool /path/to/domoticz/plugins/00-PyPluginStore/registry_local.json
```

Then select **Reload entries** or **Refresh status**. If a private repository
cannot be installed, verify that the Domoticz OS user can run `git ls-remote`
against the exact configured URL and branch.
