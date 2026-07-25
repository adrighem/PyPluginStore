import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from plugin_core_helpers import configure_home


PRODUCT_VERSION = "2.21.1"
RUNTIME_FILES = (
    "plugin.py",
    "package_registry.py",
    "package_identity.py",
    "release_providers.py",
    "pypluginstore.html",
)
DEVELOPMENT_FRONTEND_IDENTITY = {
    "schema_version": 1,
    "product_version": "0.0.0-dev",
    "build_id": "0" * 64,
}


class FakeGitResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def bundle_documents(
    *,
    version=PRODUCT_VERSION,
    plugin_marker="backend-a",
    registry_marker="registry-a",
    identity_marker="identity-a",
    providers_marker="providers-a",
    frontend_marker="frontend-a",
):
    development_identity = json.dumps(
        DEVELOPMENT_FRONTEND_IDENTITY,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "plugin.py": (
            '"""\n'
            '<plugin key="PP-MANAGER" name="PyPluginStore" '
            f'version="{version}"></plugin>\n'
            '"""\n'
            f'PLUGIN_MARKER = "{plugin_marker}"\n'
        ).encode(),
        "package_registry.py": (
            f'PACKAGE_REGISTRY_MARKER = "{registry_marker}"\n'
        ).encode(),
        "package_identity.py": (
            f'PACKAGE_IDENTITY_MARKER = "{identity_marker}"\n'
        ).encode(),
        "release_providers.py": (
            f'RELEASE_PROVIDERS_MARKER = "{providers_marker}"\n'
        ).encode(),
        "pypluginstore.html": (
            "<div id=\"pypluginstore-container\"></div>\n"
            "<script>\n"
            "const MANAGER_FRONTEND_IDENTITY = Object.freeze("
            + development_identity
            + "); // x-pypluginstore-manager-identity\n"
            f"const FRONTEND_MARKER = {json.dumps(frontend_marker)};\n"
            "</script>\n"
        ).encode(),
    }


def write_bundle(manager_dir, documents):
    manager_dir.mkdir(parents=True, exist_ok=True)
    for relative_path in RUNTIME_FILES:
        (manager_dir / relative_path).write_bytes(documents[relative_path])


def require_identity_api(plugin_core_module):
    compute = getattr(plugin_core_module, "compute_manager_build_id", None)
    service_class = getattr(plugin_core_module, "ManagerIdentityService", None)
    assert callable(compute), (
        "compute_manager_build_id() must provide deterministic framed "
        "runtime-bundle hashes"
    )
    assert service_class is not None, (
        "ManagerIdentityService must capture the loaded runtime separately "
        "from installed files"
    )
    return compute, service_class


def make_identity_service(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    *,
    documents=None,
    git_commit="a" * 40,
):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    write_bundle(manager_dir, documents or bundle_documents())
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()
    current_commit = {"value": git_commit}

    def fake_git(command, cwd, timeout=15):
        assert command == ["git", "rev-parse", "--verify", "HEAD"]
        assert Path(cwd) == manager_dir
        return FakeGitResult(stdout=current_commit["value"] + "\n")

    monkeypatch.setattr(host, "run_git_read_only", fake_git)
    _, service_class = require_identity_api(plugin_core_module)
    service = service_class(plugin)
    runtime_identity = service.capture_runtime()
    return plugin, service, manager_dir, current_commit, runtime_identity


def deploy_runtime_frontend(service, plugin):
    rendered = service.render_runtime_frontend()
    destination = Path(plugin.get_host().ui_html_destination())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return destination


def test_identity_label_renders_unicode_separators_from_ascii_source(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    _, service, _, _, runtime_identity = make_identity_service(
        plugin_core_module,
        tmp_path,
        monkeypatch,
    )

    assert service._identity_label(runtime_identity) == (
        "PyPluginStore v"
        + runtime_identity["product_version"]
        + " \u00b7 build "
        + runtime_identity["build_id"][:12]
        + " \u00b7 git "
        + runtime_identity["git_commit"][:8]
    )


def test_build_id_is_deterministic_order_independent_and_framed(
    plugin_core_module,
):
    compute, _ = require_identity_api(plugin_core_module)
    first = bundle_documents()
    reordered = dict(reversed(list(first.items())))

    first_build = compute(first)
    reordered_build = compute(reordered)

    assert first_build == reordered_build
    assert re.fullmatch(r"[0-9a-f]{64}", first_build)

    left = dict(first)
    left["plugin.py"] = b"a"
    left["package_registry.py"] = b"bc"
    right = dict(first)
    right["plugin.py"] = b"ab"
    right["package_registry.py"] = b"c"

    assert b"".join(left[name] for name in RUNTIME_FILES) == b"".join(
        right[name] for name in RUNTIME_FILES
    )
    assert compute(left) != compute(right)


def test_build_id_tracks_semantic_version_and_same_version_runtime_changes(
    plugin_core_module,
):
    compute, _ = require_identity_api(plugin_core_module)
    release = bundle_documents(version="2.21.1", plugin_marker="first")
    same_version_git_update = bundle_documents(
        version="2.21.1",
        plugin_marker="second",
    )
    next_release = bundle_documents(version="2.22.0", plugin_marker="second")

    release_build = compute(release)

    assert compute(same_version_git_update) != release_build
    assert compute(next_release) != compute(same_version_git_update)
    assert compute({**release, "README.md": b"documentation changed\n"}) == (
        release_build
    )


def test_docs_only_git_revision_is_diagnostic_and_does_not_require_reload(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    (
        plugin,
        service,
        _manager_dir,
        current_commit,
        runtime_identity,
    ) = make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    deploy_runtime_frontend(service, plugin)
    current_commit["value"] = "b" * 40

    verdict = service.get_verdict(
        frontend_identity=runtime_identity,
        self_update_state={"phase": "applied_needs_reload"},
    )

    assert verdict["state"] == "consistent"
    assert verdict["coherent"] is True
    assert verdict["mutations_allowed"] is True
    assert verdict["runtime"]["build_id"] == verdict["installed"]["build_id"]
    assert verdict["runtime"]["git_commit"] == "a" * 40
    assert verdict["installed"]["git_commit"] == "b" * 40


def test_runtime_snapshot_detects_same_version_installed_disk_change(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    (
        plugin,
        service,
        manager_dir,
        current_commit,
        runtime_identity,
    ) = make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    deploy_runtime_frontend(service, plugin)
    updated_documents = bundle_documents(
        version=PRODUCT_VERSION,
        plugin_marker="backend-b",
    )
    write_bundle(manager_dir, updated_documents)
    current_commit["value"] = "b" * 40

    verdict = service.get_verdict(
        frontend_identity=runtime_identity,
        self_update_state={"phase": "applied_needs_reload"},
    )

    assert verdict["state"] == "restart_required"
    assert verdict["coherent"] is False
    assert verdict["mutations_allowed"] is False
    assert verdict["runtime"]["product_version"] == PRODUCT_VERSION
    assert verdict["installed"]["product_version"] == PRODUCT_VERSION
    assert verdict["runtime"]["build_id"] != verdict["installed"]["build_id"]
    assert "restart" in verdict["message"].lower()


def test_cached_support_module_cannot_masquerade_as_loaded_disk_source(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    _, manager_dir = configure_home(plugin_core_module, tmp_path)
    documents = bundle_documents()
    write_bundle(manager_dir, documents)
    monkeypatch.setattr(
        plugin_core_module,
        "__file__",
        str(manager_dir / "plugin.py"),
    )

    def fingerprint(contents):
        return (len(contents), hashlib.sha256(contents).hexdigest())

    monkeypatch.setattr(
        plugin_core_module,
        "_MANAGER_LOADED_PLUGIN_SOURCE_FINGERPRINT",
        fingerprint(documents["plugin.py"]),
    )
    monkeypatch.setattr(
        plugin_core_module,
        "_MANAGER_LOADED_PACKAGE_REGISTRY_FINGERPRINT",
        (1, "f" * 64),
    )
    monkeypatch.setattr(
        plugin_core_module,
        "_MANAGER_LOADED_PACKAGE_IDENTITY_FINGERPRINT",
        fingerprint(documents["package_identity.py"]),
    )
    monkeypatch.setattr(
        plugin_core_module,
        "_MANAGER_LOADED_RELEASE_PROVIDERS_FINGERPRINT",
        fingerprint(documents["release_providers.py"]),
    )

    plugin = plugin_core_module.BasePlugin()
    service = plugin_core_module.ManagerIdentityService(plugin)

    runtime_identity = service.capture_runtime()

    assert runtime_identity["build_id"] == ""
    assert "loaded" in service.runtime_error.lower()


def test_deployed_template_byte_mismatch_is_reported_even_with_matching_identity(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugin, service, _manager_dir, _commit, runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    destination = deploy_runtime_frontend(service, plugin)
    destination.write_bytes(
        destination.read_bytes() + b"<!-- locally stale template bytes -->\n"
    )

    verdict = service.get_verdict(frontend_identity=runtime_identity)

    assert verdict["state"] == "ui_deploy_stale"
    assert verdict["coherent"] is False
    assert verdict["mutations_allowed"] is False
    assert verdict["deployed"]["identity"] == runtime_identity
    assert verdict["deployed"]["exact_match"] is False


def test_deployment_replaces_stale_bytes_even_when_destination_mtime_is_newer(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugin, service, manager_dir, _commit, _runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    destination = Path(plugin.get_host().ui_html_destination())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"stale custom page\n")
    future_mtime = (
        (manager_dir / "pypluginstore.html").stat().st_mtime + 3600
    )
    os.utime(destination, (future_mtime, future_mtime))

    changed = service.deploy_runtime_frontend()

    assert changed is True
    assert destination.read_bytes() == service.render_runtime_frontend()
    assert service.deploy_runtime_frontend() is False


def test_atomic_deployment_failure_preserves_destination_and_cleans_temp_file(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    plugin, service, _manager_dir, _commit, _runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    destination = Path(plugin.get_host().ui_html_destination())
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous_contents = b"previous complete custom page\n"
    destination.write_bytes(previous_contents)
    real_replace = plugin_core_module.os.replace

    def fail_destination_replace(source, target):
        if Path(target) == destination:
            raise OSError("simulated atomic replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(
        plugin_core_module.os,
        "replace",
        fail_destination_replace,
    )

    with pytest.raises(OSError, match="simulated atomic replace failure"):
        service.deploy_runtime_frontend()

    assert destination.read_bytes() == previous_contents
    assert list(destination.parent.glob(".pypluginstore-*.tmp")) == []


@pytest.mark.parametrize(
    "frontend_identity",
    [
        {
            "schema_version": 1,
            "product_version": PRODUCT_VERSION,
            "build_id": "f" * 64,
        },
        {"schema_version": 1, "product_version": PRODUCT_VERSION},
    ],
)
def test_present_stale_or_malformed_browser_identity_requires_refresh(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    frontend_identity,
):
    plugin, service, _manager_dir, _commit, runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    deploy_runtime_frontend(service, plugin)

    verdict = service.get_verdict(frontend_identity=frontend_identity)

    assert verdict["state"] == "frontend_stale"
    assert verdict["coherent"] is False
    assert verdict["mutations_allowed"] is False
    assert verdict["runtime"] == runtime_identity
    assert "refresh" in verdict["message"].lower()


@pytest.mark.parametrize("phase", ["scheduled", "running"])
def test_active_self_update_is_reported_and_temporarily_read_only(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    phase,
):
    plugin, service, _manager_dir, _commit, runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    deploy_runtime_frontend(service, plugin)

    verdict = service.get_verdict(
        frontend_identity=runtime_identity,
        self_update_state={"phase": phase},
    )

    assert verdict["state"] == "updating"
    assert verdict["coherent"] is True
    assert verdict["mutations_allowed"] is False
    assert "progress" in verdict["message"].lower()


def test_stale_active_self_update_state_becomes_recoverable_unknown(
    plugin_core_module,
    tmp_path,
):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.get_host().write_self_update_state(
        "running",
        "Self update helper is running.",
        updated_at="2000-01-01T00:00:00Z",
    )

    state = plugin.getSelfUpdateState()

    assert state["phase"] == "stale_unknown"
    assert "stopped reporting progress" in state["message"]


def test_stale_self_update_state_remains_recoverable_when_persistence_fails(
    plugin_core_module,
    tmp_path,
    monkeypatch,
):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    host = plugin.get_host()
    host.write_self_update_state(
        "running",
        "Self update helper is running.",
        updated_at="2000-01-01T00:00:00Z",
    )

    def fail_write(*args, **kwargs):
        raise PermissionError("manager directory is read-only")

    monkeypatch.setattr(host, "write_self_update_state", fail_write)

    state = plugin.getSelfUpdateState()

    assert state["operation"] == "self_update"
    assert state["phase"] == "stale_unknown"
    assert "stopped reporting progress" in state["message"]
    assert state["log_file"] == host.self_update_log_file()


@pytest.mark.parametrize("failure_kind", ["missing", "symlink"])
def test_missing_or_symlinked_installed_identity_input_is_unverifiable(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    failure_kind,
):
    plugin, service, manager_dir, _commit, runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    deploy_runtime_frontend(service, plugin)
    identity_module = manager_dir / "package_identity.py"
    identity_module.unlink()

    if failure_kind == "symlink":
        target = manager_dir / "outside-package-identity.py"
        target.write_text("PACKAGE_IDENTITY_MARKER = 'outside'\n")
        try:
            identity_module.symlink_to(target)
        except OSError as error:
            pytest.skip("Creating file symlinks is unavailable: " + str(error))

    verdict = service.get_verdict(frontend_identity=runtime_identity)

    assert verdict["state"] == "unverifiable"
    assert verdict["coherent"] is False
    assert verdict["mutations_allowed"] is False
    assert "verif" in verdict["message"].lower()


@pytest.mark.parametrize("failure_kind", ["oversized", "unreadable"])
def test_oversized_or_unreadable_installed_input_is_unverifiable(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    failure_kind,
):
    plugin, service, manager_dir, _commit, runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    deploy_runtime_frontend(service, plugin)
    identity_module = manager_dir / "package_identity.py"

    if failure_kind == "oversized":
        with identity_module.open("wb") as identity_file:
            identity_file.truncate(
                plugin_core_module.MANAGER_IDENTITY_MAX_FILE_BYTES + 1
            )
    else:
        real_open = plugin_core_module.os.open
        identity_path = os.path.abspath(identity_module)

        def deny_identity_open(path, flags, *args, **kwargs):
            if os.path.abspath(os.fspath(path)) == identity_path:
                raise PermissionError("simulated unreadable identity input")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(
            plugin_core_module.os,
            "open",
            deny_identity_open,
        )

    verdict = service.get_verdict(frontend_identity=runtime_identity)

    assert verdict["state"] == "unverifiable"
    assert verdict["coherent"] is False
    assert verdict["mutations_allowed"] is False
    assert "verif" in verdict["message"].lower()


@pytest.mark.parametrize("git_failure", ["nonzero", "exception"])
def test_git_head_failure_is_optional_and_does_not_break_bundle_coherence(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    git_failure,
):
    plugin, service, _manager_dir, _commit, runtime_identity = (
        make_identity_service(plugin_core_module, tmp_path, monkeypatch)
    )
    deploy_runtime_frontend(service, plugin)
    host = plugin.get_host()

    def unavailable_git(*args, **kwargs):
        if git_failure == "exception":
            raise OSError("git unavailable")
        return FakeGitResult(stderr="fatal: no HEAD\n", returncode=128)

    monkeypatch.setattr(host, "run_git_read_only", unavailable_git)

    verdict = service.get_verdict(
        frontend_identity=runtime_identity,
        self_update_state={"phase": "applied_needs_reload"},
    )

    assert verdict["state"] == "consistent"
    assert verdict["coherent"] is True
    assert verdict["mutations_allowed"] is True
    assert verdict["runtime"]["build_id"] == verdict["installed"]["build_id"]
    assert "git_commit" in verdict["runtime"]
    assert "git_commit" not in verdict["installed"]


def identity_verdict(
    *,
    state="frontend_stale",
    coherent=False,
    mutations_allowed=False,
):
    runtime = {
        "schema_version": 1,
        "product_version": PRODUCT_VERSION,
        "build_id": "a" * 64,
    }
    installed = dict(runtime)
    frontend = {
        "schema_version": 1,
        "product_version": PRODUCT_VERSION,
        "build_id": "b" * 64,
    }
    return {
        "schema_version": 1,
        "state": state,
        "coherent": coherent,
        "mutations_allowed": mutations_allowed,
        "message": "Manager identities do not match; hard refresh required.",
        "frontend": frontend,
        "runtime": runtime,
        "installed": installed,
        "deployed": {
            "identity": runtime,
            "exact_match": True,
        },
    }


class FakeManagerIdentityService:
    def __init__(self, mismatch):
        self.mismatch = mismatch

    def get_verdict(self, frontend_identity=None, self_update_state=None):
        if frontend_identity is None:
            legacy = dict(self.mismatch)
            legacy.update(
                {
                    "state": "legacy_frontend",
                    "message": (
                        "Legacy frontend is read-only until the page is "
                        "refreshed."
                    ),
                }
            )
            return legacy
        return self.mismatch


def install_fake_identity_verdict(plugin, monkeypatch, mismatch):
    service = FakeManagerIdentityService(mismatch)
    plugin.manager_identity_service = service
    monkeypatch.setattr(
        plugin,
        "getManagerIdentityVerdict",
        service.get_verdict,
        raising=False,
    )


def mutation_payload(action, frontend_identity):
    payload = {"action": action}
    if frontend_identity is not None:
        payload["frontend_identity"] = frontend_identity
    if action in {"install", "update", "remove", "use_release", "rollback"}:
        payload["plugin_key"] = "OtherPlugin"
    elif action == "upsert_local_registry_entry":
        payload.update(
            {
                "expected_revision": "revision-a",
                "entry": {
                    "key": "OtherPlugin",
                    "repository_source": "https://github.com/owner/repository",
                    "description": "Other",
                    "branch": "main",
                },
            }
        )
    elif action == "delete_local_registry_entry":
        payload.update(
            {
                "expected_revision": "revision-a",
                "plugin_key": "OtherPlugin",
            }
        )
    return payload


@pytest.mark.parametrize(
    "action",
    [
        "install",
        "update",
        "remove",
        "use_release",
        "rollback",
        "upsert_local_registry_entry",
        "delete_local_registry_entry",
    ],
)
def test_identity_mismatch_rejects_mutations_before_dispatch(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    action,
):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repository", "Other", "main", ""],
    }
    mismatch = identity_verdict()
    install_fake_identity_verdict(plugin, monkeypatch, mismatch)
    responses = []

    def forbidden_mutation(*args, **kwargs):
        raise AssertionError("identity guard did not run before mutation")

    monkeypatch.setattr(plugin, "InstallPythonPlugin", forbidden_mutation)
    monkeypatch.setattr(plugin, "UpdatePythonPlugin", forbidden_mutation)
    monkeypatch.setattr(plugin, "removePlugin", forbidden_mutation)
    monkeypatch.setattr(
        plugin,
        "executeReleaseManagementAction",
        forbidden_mutation,
    )
    monkeypatch.setattr(
        plugin.local_registry_service,
        "upsert",
        forbidden_mutation,
    )
    monkeypatch.setattr(
        plugin.local_registry_service,
        "delete",
        forbidden_mutation,
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand(
        mutation_payload(action, mismatch["frontend"])
    )

    assert len(responses) == 1
    assert responses[0]["status"] == "error"
    assert responses[0]["action"] == action
    assert responses[0]["code"] == "manager_identity_mismatch"
    assert responses[0]["manager_identity"] == mismatch


@pytest.mark.parametrize(
    "frontend_identity",
    [
        None,
        {"schema_version": 1, "product_version": PRODUCT_VERSION},
    ],
)
def test_missing_or_malformed_frontend_identity_is_read_only(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    frontend_identity,
):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    plugin.plugin_data = {
        "OtherPlugin": ["owner", "repository", "Other", "main", ""],
    }
    mismatch = identity_verdict()
    install_fake_identity_verdict(plugin, monkeypatch, mismatch)
    responses = []

    monkeypatch.setattr(
        plugin,
        "InstallPythonPlugin",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(AssertionError("legacy or malformed frontend mutated state")),
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)

    plugin.handleApiCommand(
        mutation_payload("install", frontend_identity)
    )

    assert len(responses) == 1
    assert responses[0]["status"] == "error"
    assert responses[0]["code"] == "manager_identity_mismatch"
    assert responses[0]["manager_identity"]["mutations_allowed"] is False
    if frontend_identity is None:
        assert (
            responses[0]["manager_identity"]["state"]
            == "legacy_frontend"
        )
    else:
        assert responses[0]["manager_identity"]["state"] == "frontend_stale"


@pytest.mark.parametrize("action", ["self_update_status", "restart_domoticz"])
@pytest.mark.parametrize(
    "frontend_identity",
    [
        None,
        {
            "schema_version": 1,
            "product_version": PRODUCT_VERSION,
            "build_id": "b" * 64,
        },
    ],
)
def test_identity_mismatch_allows_status_and_restart_recovery(
    plugin_core_module,
    tmp_path,
    monkeypatch,
    action,
    frontend_identity,
):
    configure_home(plugin_core_module, tmp_path)
    plugin = plugin_core_module.BasePlugin()
    mismatch = identity_verdict()
    install_fake_identity_verdict(plugin, monkeypatch, mismatch)
    responses = []

    monkeypatch.setattr(
        plugin,
        "getSelfUpdateState",
        lambda: {"phase": "applied_needs_reload"},
    )
    monkeypatch.setattr(
        plugin,
        "restartDomoticz",
        lambda: (True, "Domoticz restart requested"),
    )
    monkeypatch.setattr(plugin, "sendApiResponse", responses.append)
    payload = {"action": action}
    if frontend_identity is not None:
        payload["frontend_identity"] = frontend_identity

    plugin.handleApiCommand(payload)

    assert len(responses) == 1
    assert responses[0]["status"] == "success"
    assert responses[0]["action"] == action
    assert responses[0]["manager_identity"]["mutations_allowed"] is False
