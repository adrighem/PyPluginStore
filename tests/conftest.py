import importlib.util
import sys
import types
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin_core_module(monkeypatch, tmp_path_factory):
    domoticz = types.ModuleType("Domoticz")
    calls = {name: [] for name in ("Debug", "Log", "Error", "SendNotification", "Debugging", "Heartbeat")}

    def recorder(name):
        def record(*args, **kwargs):
            calls[name].append((args, kwargs))
        return record

    for name in calls:
        setattr(domoticz, name, recorder(name))
    domoticz.calls = calls

    monkeypatch.setitem(sys.modules, "Domoticz", domoticz)
    module = load_module_from_path("plugin_core_under_test", REPO_ROOT / "plugin_core.py")
    # onStart writes a .pth file and may restart Domoticz: keep the file out of
    # the real site directories and never run real restart commands.
    site_dir = str(tmp_path_factory.mktemp("site-packages"))
    monkeypatch.setattr(module, "shared_deps_site_dirs", lambda: [site_dir])
    monkeypatch.setattr(module.HostRuntime, "can_restart_domoticz", lambda self: False)
    return module
