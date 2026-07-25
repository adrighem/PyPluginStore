import json


def configure_home(plugin_core_module, tmp_path):
    plugins_dir = tmp_path / "domoticz" / "plugins"
    manager_dir = plugins_dir / "00-PyPluginStore"
    manager_dir.mkdir(parents=True)
    write_plugin_py(
        manager_dir,
        key="PP-MANAGER",
        name="PyPluginStore",
        externallink="https://github.com/adrighem/PyPluginStore",
    )
    plugin_core_module.Parameters = {
        "HomeFolder": str(manager_dir) + "/",
        "Mode4": "None",
        "Mode6": "Normal",
    }
    return plugins_dir, manager_dir


def write_plugin_py(plugin_dir, key, name, externallink="", version=""):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    externallink_attr = f' externallink="{externallink}"' if externallink else ""
    version_attr = f' version="{version}"' if version else ""
    (plugin_dir / "plugin.py").write_text(
        f'"""\n<plugin key="{key}" name="{name}" author="tester"{version_attr}{externallink_attr}>\n</plugin>\n"""\n'
    )


def write_manager_identity_bundle(
    manager_dir,
    *,
    version="2.21.1",
    html_body="<div>Plugin Store</div>",
):
    write_plugin_py(
        manager_dir,
        key="PP-MANAGER",
        name="PyPluginStore",
        externallink="https://github.com/adrighem/PyPluginStore",
        version=version,
    )
    (manager_dir / "package_registry.py").write_text(
        "PACKAGE_REGISTRY_MARKER = 'test'\n"
    )
    (manager_dir / "package_identity.py").write_text(
        "PACKAGE_IDENTITY_MARKER = 'test'\n"
    )
    (manager_dir / "release_providers.py").write_text(
        "RELEASE_PROVIDERS_MARKER = 'test'\n"
    )
    development_identity = json.dumps(
        {
            "build_id": "0" * 64,
            "product_version": "0.0.0-dev",
            "schema_version": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    (manager_dir / "pypluginstore.html").write_text(
        html_body
        + "\n<script>\n"
        + "const MANAGER_FRONTEND_IDENTITY = Object.freeze("
        + development_identity
        + "); // x-pypluginstore-manager-identity\n"
        + "</script>\n"
    )


def debug_messages(plugin_core_module):
    return [args[0] for args, _ in plugin_core_module.Domoticz.calls["Debug"] if args]
