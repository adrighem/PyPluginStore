import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin_core_helpers import configure_home


OLD_COMMIT = "1" * 40
OLD_TREE = "2" * 64
NEW_COMMIT = "3" * 40
NEW_TREE = "4" * 64


class RecordingFilesystem:
    """Narrow filesystem seam used by dependency snapshot construction."""

    def __init__(
        self,
        events=None,
        snapshot_error=None,
        discard_error=None,
    ):
        self.events = events if events is not None else []
        self.snapshot_error = snapshot_error
        self.discard_error = discard_error
        self.snapshot_calls = []
        self.discard_calls = []

    def snapshot_tree(self, source, destination):
        source = Path(source)
        destination = Path(destination)
        self.events.append("snapshot")
        self.snapshot_calls.append((source, destination))
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            destination.mkdir()
        if self.snapshot_error is not None:
            (destination / "partial-copy.txt").write_text(
                "partial", encoding="utf-8"
            )
            raise self.snapshot_error

    def discard_tree(self, path):
        path = Path(path)
        self.events.append("discard")
        self.discard_calls.append(path)
        if self.discard_error is not None:
            raise self.discard_error
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)


class RecordingCommandRunner:
    """Installer discovery/execution seam with optional staged mutations."""

    def __init__(
        self,
        *,
        available=("uv", "pip"),
        returncode=0,
        stdout="installed",
        stderr="",
        on_run=None,
        events=None,
    ):
        self.available_commands = set(available)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.on_run = on_run
        self.events = events if events is not None else []
        self.available_calls = []
        self.calls = []

    def available(self, command, *, env=None):
        self.available_calls.append((command, dict(env or {})))
        return command in self.available_commands

    def run(self, command, *, env=None):
        command = list(command)
        self.events.append("install")
        self.calls.append((command, dict(env or {})))
        if self.on_run is not None:
            self.on_run(command)
        return SimpleNamespace(
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


class RecordingValidator:
    def __init__(
        self,
        *,
        valid=True,
        message="",
        warnings=(),
        conflicts=(),
        events=None,
    ):
        self.valid = valid
        self.message = message
        self.warnings = list(warnings)
        self.conflicts = list(conflicts)
        self.events = events if events is not None else []
        self.calls = []

    def validate(self, staged_dependencies, requirements_file):
        staged_dependencies = Path(staged_dependencies)
        requirements_file = Path(requirements_file)
        self.events.append("validate")
        self.calls.append((staged_dependencies, requirements_file))
        return {
            "valid": self.valid,
            "message": self.message,
            "warnings": list(self.warnings),
            "conflicts": list(self.conflicts),
        }


class RecordingTransactionManager:
    """Minimal journal seam expected by the snapshot service."""

    def __init__(self, transaction, events=None):
        self.transaction = transaction
        self.events = events if events is not None else []
        self.calls = []

    def load_transaction(self, operation_id):
        assert operation_id == self.transaction.operation_id
        return self.transaction

    def mark_dependencies_staged(self, operation_id, snapshot):
        assert operation_id == self.transaction.operation_id
        self.events.append("mark_dependencies_staged")
        self.calls.append(("staged", snapshot))
        self.transaction.phase = "dependencies_staged"
        self.transaction.dependency_snapshot = snapshot
        return self.transaction

    def can_retain_live_dependencies(
        self,
        transaction,
        requirements_file,
    ):
        del requirements_file
        assert transaction is self.transaction
        return bool(
            getattr(transaction, "retain_live_dependencies", False)
        )

    def mark_dependencies_retained(
        self,
        operation_id,
        snapshot,
        requirements_file,
    ):
        assert operation_id == self.transaction.operation_id
        self.events.append("mark_dependencies_retained")
        self.calls.append(("retained", snapshot, requirements_file))
        self.transaction.phase = "dependencies_staged"
        self.transaction.dependency_snapshot = snapshot
        return self.transaction

    def mark_dependency_confirmation_required(self, operation_id, snapshot):
        assert operation_id == self.transaction.operation_id
        self.events.append("mark_dependency_confirmation_required")
        self.calls.append(("confirmation", snapshot))
        self.transaction.phase = "dependency_confirmation_required"
        self.transaction.dependency_snapshot = snapshot
        return self.transaction

    def mark_dependency_blocked(self, operation_id, reason, message):
        assert operation_id == self.transaction.operation_id
        self.events.append("mark_dependency_blocked")
        self.calls.append(("blocked", reason, message))
        self.transaction.phase = "dependency_blocked"
        self.transaction.error = message
        return self.transaction


def write_files(root, files):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, contents in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, str):
            contents = contents.encode("utf-8")
        path.write_bytes(contents)
    return root


def tree_snapshot(root):
    root = Path(root)
    if not root.exists():
        return None
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def target_from_command(command):
    return Path(command[command.index("--target") + 1])


def simulate_install(package="new_dependency", version="2.0"):
    def install(command):
        target = target_from_command(command)
        write_files(
            target,
            {
                package + "/__init__.py": "__version__ = {!r}\n".format(
                    version
                ),
                package + "-" + version + ".dist-info/METADATA": (
                    "Name: " + package + "\nVersion: " + version + "\n"
                ),
            },
        )

    return install


def stub_transaction(tmp_path, *, live_dependencies=True):
    root = Path(tmp_path)
    live_code = root / "plugins" / "ExamplePlugin"
    live_deps = root / "manager" / ".shared_deps"
    staged_code = root / "manager" / ".pypluginstore" / "staging" / "op" / "code"
    staged_deps = (
        root
        / "manager"
        / ".pypluginstore"
        / "staging"
        / "op"
        / "dependencies"
    )
    write_files(
        live_code,
        {"plugin.py": "print('old')\n", "marker.txt": "old-code\n"},
    )
    write_files(
        staged_code,
        {
            "plugin.py": "print('new')\n",
            "marker.txt": "new-code\n",
            "requirements.txt": "new-dependency==2.0\n",
        },
    )
    if live_dependencies:
        write_files(
            live_deps,
            {
                "existing/__init__.py": "VALUE = 'old'\n",
                "existing-1.0.dist-info/METADATA": (
                    "Name: existing\nVersion: 1.0\n"
                ),
                "nested/data/resource.txt": "keep me\n",
            },
        )
    paths = SimpleNamespace(
        live_code=str(live_code),
        live_dependencies=str(live_deps),
        staged_code=str(staged_code),
        staged_dependencies=str(staged_deps),
    )
    return SimpleNamespace(
        operation_id="operation-001",
        plugin_key="ExamplePlugin",
        operation="release_update",
        phase="staged_verified",
        paths=paths,
        dependency_snapshot=None,
        error="",
    )


def make_service(
    plugin_core_module,
    transaction_manager,
    *,
    runner=None,
    filesystem=None,
    validator=None,
):
    return plugin_core_module.ReleaseDependencySnapshotService(
        plugin_core_module.BasePlugin(),
        transaction_manager=transaction_manager,
        command_runner=runner or RecordingCommandRunner(on_run=simulate_install()),
        filesystem=filesystem or RecordingFilesystem(),
        validator=validator or RecordingValidator(),
    )


def stage(service, transaction, **kwargs):
    requirements_file = Path(transaction.paths.staged_code) / "requirements.txt"
    return service.stage(
        transaction.operation_id,
        requirements_file=str(requirements_file),
        **kwargs,
    )


def assert_dependency_error(
    plugin_core_module,
    reason,
    call,
    *,
    manual_required=False,
):
    with pytest.raises(plugin_core_module.ReleaseDependencyError) as caught:
        call()

    assert caught.value.status == "dependency_blocked"
    assert caught.value.reason == reason
    assert caught.value.manual_required is manual_required
    return caught.value


def test_dependency_stage_builds_fresh_generation_without_stale_live_files(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    events = []
    filesystem = RecordingFilesystem(events=events)
    runner = RecordingCommandRunner(
        on_run=simulate_install(), events=events
    )
    validator = RecordingValidator(events=events)
    manager = RecordingTransactionManager(transaction, events=events)
    live_before = tree_snapshot(transaction.paths.live_dependencies)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
        filesystem=filesystem,
        validator=validator,
    )

    result = stage(service, transaction)

    staged = Path(transaction.paths.staged_dependencies)
    assert (staged / "new_dependency/__init__.py").is_file()
    assert not (staged / "existing").exists()
    assert not (staged / "nested").exists()
    assert (
        staged / ".pypluginstore-environment.json"
    ).is_file()
    assert tree_snapshot(transaction.paths.live_dependencies) == live_before
    assert filesystem.snapshot_calls == []
    assert events == [
        "discard",
        "install",
        "validate",
        "mark_dependencies_staged",
    ]
    assert result.status == "dependencies_staged"
    assert transaction.phase == "dependencies_staged"


def test_dependency_snapshot_rebuilds_instead_of_copying_runtime_caches(
    plugin_core_module,
    tmp_path,
):
    source = tmp_path / "live"
    destination = tmp_path / "staged"
    (source / "package" / "__pycache__").mkdir(parents=True)
    (source / "package" / "__init__.py").write_text(
        "VALUE = 1\n",
        encoding="utf-8",
    )
    (source / "package" / "__pycache__" / "module.pyc").write_bytes(
        b"volatile"
    )

    plugin_core_module._ReleaseDependencyFilesystem().snapshot_tree(
        source,
        destination,
    )

    assert (destination / "package" / "__init__.py").is_file()
    assert not (destination / "package" / "__pycache__").exists()


def test_missing_live_dependency_tree_stages_a_complete_empty_base(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path, live_dependencies=False)
    manager = RecordingTransactionManager(transaction)
    service = make_service(plugin_core_module, manager)

    result = stage(service, transaction)

    staged = Path(transaction.paths.staged_dependencies)
    assert staged.is_dir()
    assert (staged / "new_dependency/__init__.py").is_file()
    assert result.status == "dependencies_staged"


@pytest.mark.parametrize("installer", ["uv", "pip"])
def test_pip_and_uv_always_target_staging_never_live(
    plugin_core_module, tmp_path, installer
):
    transaction = stub_transaction(tmp_path)
    runner = RecordingCommandRunner(
        available=[installer], on_run=simulate_install()
    )
    manager = RecordingTransactionManager(transaction)
    service = make_service(plugin_core_module, manager, runner=runner)
    live_before = tree_snapshot(transaction.paths.live_dependencies)

    result = stage(service, transaction, installer=installer)

    assert len(runner.calls) == 1
    command, environment = runner.calls[0]
    assert target_from_command(command) == Path(
        transaction.paths.staged_dependencies
    )
    assert str(transaction.paths.live_dependencies) not in command
    assert str(transaction.paths.live_dependencies) not in environment.values()
    if installer == "uv":
        assert command[:3] == ["uv", "pip", "install"]
        assert command[command.index("--python") + 1] == sys.executable
        assert command[command.index("--link-mode") + 1] == "copy"
    else:
        assert command[:4] == [sys.executable, "-m", "pip", "install"]
    assert "-r" in command
    assert result.installer == installer
    assert result.command == command
    assert set(environment) == {
        "PATH",
        "LANG",
        "LC_ALL",
        "PYTHONUTF8",
        "PIP_BREAK_SYSTEM_PACKAGES",
        "PIP_CACHE_DIR",
        "UV_CACHE_DIR",
    }
    assert runner.available_calls == [(installer, environment)]
    assert tree_snapshot(transaction.paths.live_dependencies) == live_before


def test_uv_discovery_uses_the_same_sanitized_path_as_execution(
    plugin_core_module,
    monkeypatch,
):
    calls = []

    def which(command, *, path=None):
        calls.append((command, path))
        return "/usr/bin/uv"

    monkeypatch.setattr(plugin_core_module.shutil, "which", which)

    assert plugin_core_module._ReleaseDependencyCommandRunner().available(
        "uv"
    )
    assert calls == [
        ("uv", plugin_core_module.RELEASE_DEPENDENCY_INSTALLER_PATH)
    ]


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, True), (1, False)],
)
def test_pip_discovery_runs_the_domoticz_python_with_sanitized_environment(
    plugin_core_module,
    monkeypatch,
    returncode,
    expected,
):
    calls = []
    environment = {
        "PATH": plugin_core_module.RELEASE_DEPENDENCY_INSTALLER_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUTF8": "1",
        "PIP_BREAK_SYSTEM_PACKAGES": "1",
        "PIP_CACHE_DIR": "/safe/pip-cache",
        "UV_CACHE_DIR": "/safe/uv-cache",
    }

    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        return SimpleNamespace(
            returncode=returncode,
            stderr="credential-bearing probe detail",
        )

    monkeypatch.setattr(plugin_core_module.subprocess, "run", run)

    available = (
        plugin_core_module._ReleaseDependencyCommandRunner().available(
            "pip",
            env=environment,
        )
    )

    assert available is expected
    assert calls == [
        (
            [
                plugin_core_module.sys.executable,
                "-m",
                "pip",
                "--version",
            ],
            {
                "env": environment,
                "stdout": plugin_core_module.subprocess.DEVNULL,
                "stderr": plugin_core_module.subprocess.DEVNULL,
                "check": False,
                "timeout": 10,
            },
        )
    ]


def test_auto_installer_prefers_uv_then_falls_back_to_pip(
    plugin_core_module, tmp_path
):
    first = stub_transaction(tmp_path / "uv")
    uv_runner = RecordingCommandRunner(
        available=["uv", "pip"], on_run=simulate_install()
    )
    uv_manager = RecordingTransactionManager(first)
    uv_service = make_service(
        plugin_core_module, uv_manager, runner=uv_runner
    )
    second = stub_transaction(tmp_path / "pip")
    pip_runner = RecordingCommandRunner(
        available=["pip"], on_run=simulate_install()
    )
    pip_manager = RecordingTransactionManager(second)
    pip_service = make_service(
        plugin_core_module, pip_manager, runner=pip_runner
    )

    uv_result = stage(uv_service, first, installer="auto")
    pip_result = stage(pip_service, second, installer="auto")

    assert uv_result.installer == "uv"
    assert pip_result.installer == "pip"


def test_generation_resolves_all_installed_requirements_in_one_command(
    plugin_core_module,
    tmp_path,
):
    transaction = stub_transaction(tmp_path)
    other_requirements = (
        Path(transaction.paths.live_code).parent
        / "OtherPlugin"
        / "requirements.txt"
    )
    write_files(
        other_requirements.parent,
        {"requirements.txt": "shared-package==1.5\n"},
    )
    runner = RecordingCommandRunner(on_run=simulate_install())
    manager = RecordingTransactionManager(transaction)
    service = make_service(plugin_core_module, manager, runner=runner)

    stage(service, transaction, installer="uv")

    command, _environment = runner.calls[0]
    requirement_paths = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "-r"
    ]
    assert requirement_paths == [
        str(Path(transaction.paths.staged_code) / "requirements.txt"),
        str(other_requirements),
    ]
    manifest = json.loads(
        Path(
            transaction.paths.staged_dependencies,
            ".pypluginstore-environment.json",
        ).read_text(encoding="utf-8")
    )
    assert [item["owner"] for item in manifest["requirements"]] == [
        "ExamplePlugin",
        "OtherPlugin",
    ]


def test_exact_migration_retains_live_dependencies_without_resolving_other_plugins(
    plugin_core_module,
    tmp_path,
):
    transaction = stub_transaction(tmp_path)
    transaction.operation = "release_migration"
    transaction.retain_live_dependencies = True
    requirements = Path(transaction.paths.staged_code) / "requirements.txt"
    write_files(
        Path(transaction.paths.live_code),
        {"requirements.txt": requirements.read_bytes()},
    )
    write_files(
        Path(transaction.paths.live_code).parent
        / "domoticz-solaredge-modbustcp-plugin",
        {
            "requirements.txt": (
                "pymodbus==3.6.9\n"
                "solaredge_modbus==0.8.0\n"
            )
        },
    )
    live_before = tree_snapshot(transaction.paths.live_dependencies)
    runner = RecordingCommandRunner(
        returncode=1,
        stderr=(
            "No matching distribution found for solaredge_modbus==0.8.0"
        ),
    )
    validator = RecordingValidator()
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
        validator=validator,
    )

    result = stage(service, transaction)

    assert result.status == "dependencies_staged"
    assert result.strategy == "retain_live"
    assert result.installer == "none"
    assert result.command == []
    assert runner.calls == []
    assert runner.available_calls == []
    assert validator.calls == []
    assert not Path(transaction.paths.staged_dependencies).exists()
    assert tree_snapshot(transaction.paths.live_dependencies) == live_before
    assert manager.calls[-1][0] == "retained"
    persisted = plugin_core_module.ReleaseDependencySnapshotResult.from_document(
        manager.calls[-1][1]
    )
    assert persisted.strategy == "retain_live"


def test_fresh_generation_ignores_legacy_hardlinks_in_live_tree(
    plugin_core_module,
    tmp_path,
):
    transaction = stub_transaction(tmp_path)
    live_file = Path(
        transaction.paths.live_dependencies,
        "existing",
        "__init__.py",
    )
    os.link(live_file, live_file.with_name("legacy-hardlink.py"))
    manager = RecordingTransactionManager(transaction)
    service = make_service(plugin_core_module, manager)

    result = stage(service, transaction, installer="uv")

    assert result.status == "dependencies_staged"
    assert not Path(
        transaction.paths.staged_dependencies,
        "existing",
    ).exists()


def test_git_dependency_refresh_uses_same_atomic_generation_builder(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, manager_dir = configure_home(
        plugin_core_module,
        tmp_path,
    )
    plugin_dir = write_files(
        plugins_dir / "ExamplePlugin",
        {
            "plugin.py": "print('git plugin')\n",
            "requirements.txt": "new-dependency==2.0\n",
        },
    )
    write_files(
        manager_dir / ".shared_deps",
        {"stale-package/__init__.py": "STALE = True\n"},
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.installed_plugin_folders = {
        "ExamplePlugin": str(plugin_dir)
    }
    runner = RecordingCommandRunner(on_run=simulate_install())
    service = plugin_core_module.ReleaseDependencySnapshotService(
        plugin,
        transaction_manager=plugin.release_transaction_manager,
        command_runner=runner,
        filesystem=RecordingFilesystem(),
        validator=RecordingValidator(),
    )

    success, message = service.rebuild_live("ExamplePlugin")

    assert success is True
    assert "rebuilt with uv" in message
    live = manager_dir / ".shared_deps"
    assert (live / "new_dependency/__init__.py").is_file()
    assert not (live / "stale-package").exists()
    assert (live / ".pypluginstore-environment.json").is_file()
    command, environment = runner.calls[0]
    assert command[command.index("--link-mode") + 1] == "copy"
    assert "HOME" not in environment


def test_git_missing_installer_reports_pip_guidance_without_live_mutation(
    plugin_core_module,
    tmp_path,
):
    plugins_dir, manager_dir = configure_home(
        plugin_core_module,
        tmp_path,
    )
    plugin_dir = write_files(
        plugins_dir / "ExamplePlugin",
        {
            "plugin.py": "print('git plugin')\n",
            "requirements.txt": "new-dependency==2.0\n",
        },
    )
    live = write_files(
        manager_dir / ".shared_deps",
        {"working_dependency/__init__.py": "WORKING = True\n"},
    )
    live_before = tree_snapshot(live)
    plugin = plugin_core_module.BasePlugin()
    plugin.installed_plugin_folders = {
        "ExamplePlugin": str(plugin_dir)
    }
    runner = RecordingCommandRunner(available=[])
    service = plugin_core_module.ReleaseDependencySnapshotService(
        plugin,
        transaction_manager=plugin.release_transaction_manager,
        command_runner=runner,
        filesystem=RecordingFilesystem(),
        validator=RecordingValidator(),
    )

    success, message = service.rebuild_live("ExamplePlugin")

    assert success is False
    assert "uv was not found" in message
    assert "Python pip cannot be run" in message
    assert "restart Domoticz" in message
    assert "dependencies must be handled manually" in message
    assert tree_snapshot(live) == live_before
    assert runner.calls == []


@pytest.mark.parametrize(
    "crash_phase,expected_outcome",
    [
        ("prepared", "cancelled"),
        ("live_backed_up", "activated"),
        ("activated", "activated"),
    ],
)
def test_git_dependency_generation_recovers_each_durable_swap_boundary(
    plugin_core_module,
    tmp_path,
    crash_phase,
    expected_outcome,
):
    class SimulatedCrash(BaseException):
        pass

    plugins_dir, manager_dir = configure_home(
        plugin_core_module,
        tmp_path,
    )
    plugin_dir = write_files(
        plugins_dir / "ExamplePlugin",
        {
            "plugin.py": "print('git plugin')\n",
            "requirements.txt": "new-dependency==2.0\n",
        },
    )
    write_files(
        manager_dir / ".shared_deps",
        {"old_dependency/__init__.py": "OLD = True\n"},
    )
    plugin = plugin_core_module.BasePlugin()
    plugin.installed_plugin_folders = {
        "ExamplePlugin": str(plugin_dir)
    }
    service = plugin_core_module.ReleaseDependencySnapshotService(
        plugin,
        transaction_manager=plugin.release_transaction_manager,
        command_runner=RecordingCommandRunner(
            on_run=simulate_install()
        ),
        filesystem=RecordingFilesystem(),
        validator=RecordingValidator(),
    )

    def crash(phase):
        if phase == crash_phase:
            raise SimulatedCrash(phase)

    service.fault_injector = crash
    with pytest.raises(SimulatedCrash):
        service.rebuild_live("ExamplePlugin")

    recovery = plugin_core_module.ReleaseDependencySnapshotService(
        plugin,
        transaction_manager=plugin.release_transaction_manager,
        command_runner=RecordingCommandRunner(),
        filesystem=RecordingFilesystem(),
        validator=RecordingValidator(),
    )
    outcomes = recovery.recover_pending_generations()

    assert outcomes == [
        {
            "plugin_key": "ExamplePlugin",
            "outcome": expected_outcome,
        }
    ]
    live = manager_dir / ".shared_deps"
    if crash_phase == "prepared":
        assert (live / "old_dependency/__init__.py").is_file()
        assert not (live / "new_dependency").exists()
    else:
        assert (live / "new_dependency/__init__.py").is_file()
        assert not (live / "old_dependency").exists()
    generation_state = (
        manager_dir / ".pypluginstore" / "dependency-generations"
    )
    assert list(generation_state.glob("*.json")) == []
    assert list(generation_state.glob("*.staged")) == []
    assert list(generation_state.glob("*.backup")) == []


def test_no_requirements_builds_empty_manifested_generation_without_installer(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    requirements = Path(transaction.paths.staged_code) / "requirements.txt"
    requirements.unlink()
    runner = RecordingCommandRunner(available=[])
    validator = RecordingValidator()
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
        validator=validator,
    )

    result = stage(service, transaction)

    assert runner.calls == []
    assert runner.available_calls == []
    assert validator.calls == [
        (Path(transaction.paths.staged_dependencies), requirements)
    ]
    assert result.installer == "none"
    assert result.command == []
    staged = Path(transaction.paths.staged_dependencies)
    assert not (staged / "existing").exists()
    manifest = json.loads(
        (staged / ".pypluginstore-environment.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["installer"] == "none"
    assert manifest["requirements"] == []


def test_dependency_snapshot_schema_one_defaults_to_rebuild_strategy(
    plugin_core_module,
):
    previous = plugin_core_module.ReleaseDependencySnapshotResult(
        status="dependencies_staged",
        installer="none",
        command=[],
        requirements_file="",
        compatibility_warnings=[],
        compatibility_conflicts=[],
        requires_confirmation=False,
        compatibility_confirmed=False,
    ).to_document()
    previous["schema_version"] = 1
    previous.pop("strategy")

    restored = (
        plugin_core_module.ReleaseDependencySnapshotResult.from_document(
            previous
        )
    )

    assert restored.strategy == "rebuild"


def test_validation_runs_after_install_and_before_journal_stage(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    events = []

    class InspectingValidator(RecordingValidator):
        def validate(self, staged_dependencies, requirements_file):
            assert (
                Path(staged_dependencies) / "new_dependency/__init__.py"
            ).is_file()
            return super().validate(staged_dependencies, requirements_file)

    runner = RecordingCommandRunner(
        on_run=simulate_install(), events=events
    )
    validator = InspectingValidator(events=events)
    manager = RecordingTransactionManager(transaction, events=events)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
        filesystem=RecordingFilesystem(events=events),
        validator=validator,
    )

    stage(service, transaction)

    assert events.index("install") < events.index("validate")
    assert events.index("validate") < events.index("mark_dependencies_staged")


def test_generation_initialization_failure_leaves_live_untouched(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    live_before = tree_snapshot(transaction.paths.live_dependencies)
    filesystem = RecordingFilesystem(
        discard_error=OSError("discard failed")
    )
    runner = RecordingCommandRunner(on_run=simulate_install())
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
        filesystem=filesystem,
    )

    assert_dependency_error(
        plugin_core_module,
        "snapshot_failed",
        lambda: stage(service, transaction),
    )

    assert tree_snapshot(transaction.paths.live_dependencies) == live_before
    assert not Path(transaction.paths.staged_dependencies).exists()
    assert runner.calls == []
    assert transaction.phase == "dependency_blocked"
    assert filesystem.discard_calls


def test_unavailable_installer_reports_manual_dependency_state_without_live_mutation(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    live_before = tree_snapshot(transaction.paths.live_dependencies)
    runner = RecordingCommandRunner(available=[])
    manager = RecordingTransactionManager(transaction)
    service = make_service(plugin_core_module, manager, runner=runner)

    error = assert_dependency_error(
        plugin_core_module,
        "installer_unavailable",
        lambda: stage(service, transaction),
        manual_required=True,
    )

    assert tree_snapshot(transaction.paths.live_dependencies) == live_before
    assert not Path(transaction.paths.staged_dependencies).exists()
    assert transaction.phase == "dependency_blocked"
    assert "uv was not found" in error.message
    assert "Python pip cannot be run" in error.message
    assert "restart Domoticz" in error.message
    assert "dependencies must be handled manually" in error.message
    assert "manual" in transaction.error.lower()
    assert "credential" not in transaction.error.lower()


def test_missing_installer_guidance_is_platform_specific(
    plugin_core_module,
):
    linux_message = (
        plugin_core_module.dependency_installer_unavailable_message(
            "auto",
            "linux",
        )
    )
    windows_message = (
        plugin_core_module.dependency_installer_unavailable_message(
            "auto",
            "windows",
        )
    )

    assert "python3-pip" in linux_message
    assert "py3-pip" in linux_message
    assert "enable pip" not in linux_message
    assert "Python pip cannot be run" in windows_message
    assert "Python installation used by Domoticz" in windows_message
    assert "python3-pip" not in windows_message
    assert "py3-pip" not in windows_message


@pytest.mark.parametrize("installer", ["uv", "pip"])
def test_installer_failure_discards_stage_and_never_mutates_live(
    plugin_core_module, tmp_path, installer
):
    transaction = stub_transaction(tmp_path)
    live_before = tree_snapshot(transaction.paths.live_dependencies)
    runner = RecordingCommandRunner(
        available=[installer],
        returncode=1,
        stderr="resolver failed",
    )
    filesystem = RecordingFilesystem()
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
        filesystem=filesystem,
    )

    error = assert_dependency_error(
        plugin_core_module,
        "install_failed",
        lambda: stage(service, transaction, installer=installer),
    )

    assert "exit code 1" in error.message
    assert "while resolving 1 plugin requirement file" in error.message
    assert "safely recognizable cause" in error.message
    assert "Raw installer output was not logged" in error.message
    assert "resolver failed" not in error.message
    assert tree_snapshot(transaction.paths.live_dependencies) == live_before
    assert not Path(transaction.paths.staged_dependencies).exists()
    assert transaction.phase == "dependency_blocked"


def test_installer_failure_identifies_the_blocking_requirement_owner_safely(
    plugin_core_module,
    tmp_path,
):
    transaction = stub_transaction(tmp_path)
    transaction.plugin_key = "domoticz-solaredge-modbustcp-plugin"
    transaction.paths.live_code = str(
        Path(transaction.paths.live_code).parent
        / "domoticz-solaredge-modbustcp-plugin"
    )
    write_files(
        Path(transaction.paths.staged_code),
        {
            "requirements.txt": (
                "pymodbus==3.6.9\n"
                "solaredge_modbus==0.8.0\n"
            )
        },
    )
    runner = RecordingCommandRunner(
        available=["pip"],
        returncode=1,
        stderr=(
            "Looking in indexes: "
            "https://account:credential@example.invalid/simple\n"
            "ERROR: Package solaredge_modbus requires a different Python "
            "version: 3.7.3 not in '>=3.8'\n"
            "ERROR: No matching distribution found for "
            "solaredge_modbus==0.8.0"
        ),
    )
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
    )

    error = assert_dependency_error(
        plugin_core_module,
        "install_failed",
        lambda: stage(service, transaction, installer="pip"),
    )

    assert (
        "Blocking requirement: solaredge-modbus from "
        "domoticz-solaredge-modbustcp-plugin."
    ) in error.message
    assert (
        "Domoticz is using Python "
        + str(sys.version_info[0])
        + "."
        + str(sys.version_info[1])
        + "."
    ) in error.message
    assert "use a compatible Domoticz Python runtime" in error.message
    assert "account" not in error.message
    assert "credential" not in error.message
    assert "example.invalid" not in error.message
    assert runner.stderr not in error.message


def test_installer_prunes_broken_sibling_requirements_allowing_target_to_succeed(
    plugin_core_module,
    tmp_path,
):
    transaction = stub_transaction(tmp_path)
    target_req = Path(transaction.paths.staged_code) / "requirements.txt"
    write_files(
        target_req.parent,
        {"requirements.txt": "requests>=2.31.0\n"},
    )
    sibling_dir = (
        Path(transaction.paths.live_code).parent
        / "domoticz-solaredge-modbustcp-plugin"
    )
    write_files(
        sibling_dir,
        {"requirements.txt": "solaredge_modbus==0.8.0\n"},
    )

    class SiblingFaultCommandRunner:
        def __init__(self):
            self.calls = []

        def available(self, command, *, env=None):
            return command == "pip"

        def run(self, command, *, env=None):
            cmd = list(command)
            self.calls.append(cmd)
            if any("domoticz-solaredge-modbustcp-plugin" in str(arg) for arg in cmd):
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr=(
                        "Package 'solaredge-modbus' requires a different Python: 3.7.3 not in '>=3.8'\n"
                        "No matching distribution found for solaredge_modbus==0.8.0"
                    ),
                )
            return SimpleNamespace(
                returncode=0,
                stdout="Successfully installed requests\n",
                stderr="",
            )

    runner = SiblingFaultCommandRunner()
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
    )

    stage(service, transaction, installer="pip")

    assert transaction.phase == "dependencies_staged"
    manifest_path = (
        Path(transaction.paths.staged_dependencies)
        / plugin_core_module.ReleaseDependencySnapshotService.MANIFEST_FILE
    )
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert any("requests" in str(r) or transaction.plugin_key in str(r) for r in manifest.get("requirements", []))
    assert not any("domoticz-solaredge-modbustcp-plugin" in str(r) for r in manifest.get("requirements", []))


def test_installer_fails_fast_when_target_plugin_requirements_fail(
    plugin_core_module,
    tmp_path,
):
    transaction = stub_transaction(tmp_path)
    target_req = Path(transaction.paths.staged_code) / "requirements.txt"
    write_files(
        target_req.parent,
        {"requirements.txt": "broken_package==9.9.9\n"},
    )
    sibling_dir = (
        Path(transaction.paths.live_code).parent
        / "domoticz-healthy-plugin"
    )
    write_files(
        sibling_dir,
        {"requirements.txt": "requests>=2.31.0\n"},
    )

    class TargetFaultCommandRunner:
        def __init__(self):
            self.calls = []

        def available(self, command, *, env=None):
            return command == "pip"

        def run(self, command, *, env=None):
            cmd = list(command)
            self.calls.append(cmd)
            if any(str(target_req) in str(arg) for arg in cmd):
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="No matching distribution found for broken_package==9.9.9",
                )
            return SimpleNamespace(
                returncode=0,
                stdout="Successfully installed requests\n",
                stderr="",
            )

    runner = TargetFaultCommandRunner()
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
    )

    error = assert_dependency_error(
        plugin_core_module,
        "install_failed",
        lambda: stage(service, transaction, installer="pip"),
    )
    assert "broken-package" in error.message
    assert transaction.phase == "dependency_blocked"


def test_installer_prunes_broken_sibling_when_target_has_no_requirements(
    plugin_core_module,
    tmp_path,
):
    transaction = stub_transaction(tmp_path)
    # Remove requirements.txt from staged_code so target has none
    staged_req = Path(transaction.paths.staged_code) / "requirements.txt"
    if staged_req.exists():
        staged_req.unlink()

    sibling_dir = (
        Path(transaction.paths.live_code).parent
        / "domoticz-solaredge-modbustcp-plugin"
    )
    write_files(
        sibling_dir,
        {"requirements.txt": "solaredge_modbus==0.8.0\n"},
    )

    class SiblingFaultCommandRunner:
        def __init__(self):
            self.calls = []

        def available(self, command, *, env=None):
            return command == "pip"

        def run(self, command, *, env=None):
            cmd = list(command)
            self.calls.append(cmd)
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=(
                    "Package 'solaredge-modbus' requires a different Python: 3.7.3 not in '>=3.8'\n"
                    "No matching distribution found for solaredge_modbus==0.8.0"
                ),
            )

    runner = SiblingFaultCommandRunner()
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        runner=runner,
    )

    stage(service, transaction, installer="pip")

    assert transaction.phase == "dependencies_staged"
    manifest_path = (
        Path(transaction.paths.staged_dependencies)
        / plugin_core_module.ReleaseDependencySnapshotService.MANIFEST_FILE
    )
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("requirements") == []




@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "No solution found when resolving dependencies",
            "resolution_conflict",
        ),
        (
            "No matching distribution found for example-package",
            "package_not_found",
        ),
        (
            "example-package Requires-Python >=3.14",
            "python_incompatible",
        ),
        (
            "example-package requires a different Python version",
            "python_incompatible",
        ),
        (
            "Package 'example-package' requires a different Python: 3.7.3 not in '>=3.8'",
            "python_incompatible",
        ),
        (
            "Package example-package requires a different Python: 3.7.3 not in '>=3.8'",
            "python_incompatible",
        ),
        (
            "Ignored the following versions that require a different python version",
            "python_incompatible",
        ),
        (
            "could not find a version that satisfies the requirement example-package==1.2.3",
            "package_not_found",
        ),
        (
            "no matching distribution found for example-package==1.2.3",
            "package_not_found",
        ),
        (
            "certificate verify failed while downloading",
            "network_tls",
        ),
        (
            "temporary failure in name resolution",
            "network_unavailable",
        ),
        (
            "permission denied while creating target",
            "permission_denied",
        ),
        (
            "no space left on device",
            "disk_full",
        ),
        (
            "failed to build example-package",
            "build_failed",
        ),
        (
            "unexpected argument '--link-mode'",
            "installer_incompatible",
        ),
        (
            "SENSITIVE_INSTALLER_DETAIL",
            "unknown",
        ),
    ],
)
def test_installer_failure_output_is_reduced_to_an_allowlisted_category(
    plugin_core_module,
    output,
    expected,
):
    assert plugin_core_module.classify_release_dependency_failure(
        "",
        output,
    ) == expected


def test_release_dependency_failure_packages_and_owners_support_pinned_and_quoted_formats(
    plugin_core_module,
    tmp_path,
):
    requirements_file = tmp_path / "requirements.txt"
    requirements_file.write_text("solaredge_modbus==0.8.0\nrequests>=2.31.0\n")
    requirements = [("domoticz-solaredge-modbustcp-plugin", str(requirements_file))]

    outputs = [
        "Could not find a version that satisfies the requirement solaredge_modbus==0.8.0",
        "No matching distribution found for solaredge_modbus==0.8.0",
        "Package 'solaredge-modbus' requires a different Python: 3.7.3 not in '>=3.8'",
        "Package solaredge_modbus requires a different Python: 3.7.3 not in '>=3.8'",
    ]
    for output in outputs:
        packages = plugin_core_module.release_dependency_failure_packages(
            "", output
        )
        assert "solaredge-modbus" in packages, f"Failed to extract solaredge-modbus from: {output}"
        owners = plugin_core_module.release_dependency_failure_owners(
            requirements, "", output
        )
        assert owners == [("domoticz-solaredge-modbustcp-plugin", "solaredge-modbus")], f"Failed to map owner from: {output}"



def test_validation_failure_discards_installed_stage_and_never_mutates_live(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    live_before = tree_snapshot(transaction.paths.live_dependencies)
    validator = RecordingValidator(
        valid=False, message="broken distribution metadata"
    )
    filesystem = RecordingFilesystem()
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module,
        manager,
        filesystem=filesystem,
        validator=validator,
    )

    error = assert_dependency_error(
        plugin_core_module,
        "validation_failed",
        lambda: stage(service, transaction),
    )

    assert "broken distribution metadata" in error.message
    assert tree_snapshot(transaction.paths.live_dependencies) == live_before
    assert not Path(transaction.paths.staged_dependencies).exists()
    assert transaction.phase == "dependency_blocked"


def test_compatibility_warnings_are_persisted_and_surfaced(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    validator = RecordingValidator(
        warnings=[
            "OtherPlugin has not declared a compatible urllib3 constraint"
        ]
    )
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module, manager, validator=validator
    )

    result = stage(service, transaction)

    assert result.status == "dependencies_staged"
    assert result.compatibility_warnings == [
        "OtherPlugin has not declared a compatible urllib3 constraint"
    ]
    assert result.compatibility_conflicts == []
    assert transaction.dependency_snapshot["compatibility_warnings"] == (
        result.compatibility_warnings
    )


def test_compatibility_conflicts_require_confirmation_before_staging_completes(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    live_before = tree_snapshot(transaction.paths.live_dependencies)
    conflict = (
        "ExamplePlugin requires urllib3>=2 but OtherPlugin requires urllib3<2"
    )
    validator = RecordingValidator(conflicts=[conflict])
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module, manager, validator=validator
    )

    result = stage(service, transaction)

    assert result.status == "dependency_confirmation_required"
    assert result.requires_confirmation is True
    assert result.compatibility_conflicts == [conflict]
    assert transaction.phase == "dependency_confirmation_required"
    assert manager.calls[-1][0] == "confirmation"
    assert tree_snapshot(transaction.paths.live_dependencies) == live_before


def test_confirmed_compatibility_conflict_is_recorded_not_hidden(
    plugin_core_module, tmp_path
):
    transaction = stub_transaction(tmp_path)
    conflict = "shared dependency version may affect OtherPlugin"
    validator = RecordingValidator(conflicts=[conflict])
    manager = RecordingTransactionManager(transaction)
    service = make_service(
        plugin_core_module, manager, validator=validator
    )

    result = stage(service, transaction, compatibility_confirmed=True)

    assert result.status == "dependencies_staged"
    assert result.requires_confirmation is False
    assert result.compatibility_conflicts == [conflict]
    assert result.compatibility_confirmed is True
    assert transaction.phase == "dependencies_staged"
    assert transaction.dependency_snapshot["compatibility_conflicts"] == [
        conflict
    ]


def install_metadata_document():
    staged_files = {
        "plugin.py": b"print('new')\n",
        "marker.txt": b"new-code\n",
        "new-only.py": b"new\n",
        "requirements.txt": b"new-dependency==2.0\n",
    }
    return {
        "schema": 1,
        "plugin_key": "ExamplePlugin",
        "management_mode": "release",
        "repository_identity": "github.com/owner/example-plugin",
        "version": "2.0.0",
        "tag": "v2.0.0",
        "release_id": "github:owner/example-plugin:v2.0.0",
        "release_revision": 2,
        "released_at": "2026-07-18T07:00:00Z",
        "commit": NEW_COMMIT,
        "artifact_sha256": "5" * 64,
        "artifact_tree_sha256": NEW_TREE,
        "artifact_provenance": "forge_source_archive",
        "artifact_files": {
            path: {
                "sha256": hashlib.sha256(contents).hexdigest(),
                "size": len(contents),
            }
            for path, contents in staged_files.items()
        },
        "preserved_files": {},
        "index_sequence": 2,
        "installed_at": "2026-07-18T08:00:00Z",
    }


def old_install_metadata_document():
    installed_files = {
        "plugin.py": b"print('old')\n",
        "marker.txt": b"old-code\n",
        "old-only.py": b"old\n",
    }
    document = install_metadata_document()
    document.update(
        {
            "version": "1.0.0",
            "tag": "v1.0.0",
            "release_id": "github:owner/example-plugin:v1.0.0",
            "release_revision": 1,
            "released_at": "2026-07-17T07:00:00Z",
            "commit": OLD_COMMIT,
            "artifact_tree_sha256": OLD_TREE,
            "artifact_files": {
                path: {
                    "sha256": hashlib.sha256(contents).hexdigest(),
                    "size": len(contents),
                }
                for path, contents in installed_files.items()
            },
            "index_sequence": 1,
            "installed_at": "2026-07-17T08:00:00Z",
        }
    )
    return document


def create_real_transaction(
    plugin_core_module,
    tmp_path,
    *,
    operation="release_update",
    live_code=True,
    live_dependencies=True,
):
    plugins_dir, manager_dir = configure_home(plugin_core_module, tmp_path)
    manager = plugin_core_module.ReleaseTransactionManager(
        plugin_core_module.BasePlugin()
    )
    live_code_path = plugins_dir / "ExamplePlugin"
    live_dependencies_path = manager_dir / ".shared_deps"
    if live_code:
        write_files(
            live_code_path,
            {
                "plugin.py": "print('old')\n",
                "marker.txt": "old-code\n",
                "old-only.py": "old\n",
                ".pypluginstore.json": json.dumps(
                    old_install_metadata_document()
                ),
            },
        )
    if live_dependencies:
        write_files(
            live_dependencies_path,
            {
                "marker.txt": "old-dependencies\n",
                "old_dependency/__init__.py": "old = True\n",
            },
        )
    expected_current = (
        {
            "management_mode": "release",
            "commit": OLD_COMMIT,
            "artifact_tree_sha256": OLD_TREE,
        }
        if live_code
        else {"management_mode": "absent"}
    )
    transaction = manager.create_transaction(
        plugin_key="ExamplePlugin",
        operation_id="operation-001",
        operation=operation,
        expected_current=expected_current,
        target={
            "management_mode": "release",
            "release_id": "github:owner/example-plugin:v2.0.0",
            "release_revision": 2,
            "commit": NEW_COMMIT,
            "artifact_tree_sha256": NEW_TREE,
        },
    )
    write_files(
        transaction.paths.staged_code,
        {
            "plugin.py": "print('new')\n",
            "marker.txt": "new-code\n",
            "new-only.py": "new\n",
            "requirements.txt": "new-dependency==2.0\n",
            ".pypluginstore.json": json.dumps(install_metadata_document()),
        },
    )
    transaction = manager.mark_staged_verified(transaction.operation_id)
    return manager, transaction


def real_service(plugin_core_module, manager, *, events=None):
    events = events if events is not None else []
    return plugin_core_module.ReleaseDependencySnapshotService(
        plugin_core_module.BasePlugin(),
        transaction_manager=manager,
        command_runner=RecordingCommandRunner(
            on_run=simulate_install(), events=events
        ),
        filesystem=RecordingFilesystem(events=events),
        validator=RecordingValidator(events=events),
    )


def test_validated_dependency_snapshot_integrates_with_atomic_activation(
    plugin_core_module, tmp_path
):
    manager, transaction = create_real_transaction(
        plugin_core_module, tmp_path
    )
    service = real_service(plugin_core_module, manager)

    staged = stage(service, transaction)
    before_activation = manager.load_transaction(transaction.operation_id)

    assert staged.status == "dependencies_staged"
    assert before_activation.phase == "dependencies_staged"
    assert Path(before_activation.paths.live_code, "marker.txt").read_text(
        encoding="utf-8"
    ) == "old-code\n"
    assert Path(
        before_activation.paths.live_dependencies, "marker.txt"
    ).read_text(encoding="utf-8") == "old-dependencies\n"

    activated = manager.activate(transaction.operation_id)

    assert activated.phase == "restart_pending"
    assert Path(activated.paths.live_code, "marker.txt").read_text(
        encoding="utf-8"
    ) == "new-code\n"
    assert Path(
        activated.paths.live_dependencies, "new_dependency", "__init__.py"
    ).is_file()
    assert not Path(
        activated.paths.live_dependencies, "old_dependency"
    ).exists()
    assert Path(
        activated.paths.live_dependencies,
        ".pypluginstore-environment.json",
    ).is_file()


def test_git_generation_is_blocked_while_release_restart_is_pending(
    plugin_core_module,
    tmp_path,
):
    manager, transaction = create_real_transaction(
        plugin_core_module,
        tmp_path,
    )
    release_service = real_service(plugin_core_module, manager)
    stage(release_service, transaction)
    activated = manager.activate(transaction.operation_id)
    live_before = tree_snapshot(activated.paths.live_dependencies)
    git_service = plugin_core_module.ReleaseDependencySnapshotService(
        manager.plugin,
        transaction_manager=manager,
        command_runner=RecordingCommandRunner(
            on_run=simulate_install()
        ),
        filesystem=RecordingFilesystem(),
        validator=RecordingValidator(),
    )

    with pytest.raises(RuntimeError, match="unfinished Release"):
        git_service.rebuild_live("ExamplePlugin")

    assert tree_snapshot(activated.paths.live_dependencies) == live_before


def test_dependency_snapshot_failure_never_starts_code_activation(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, transaction = create_real_transaction(
        plugin_core_module, tmp_path
    )
    runner = RecordingCommandRunner(
        available=["pip"], returncode=1, stderr="resolution failed"
    )
    service = plugin_core_module.ReleaseDependencySnapshotService(
        plugin_core_module.BasePlugin(),
        transaction_manager=manager,
        command_runner=runner,
        filesystem=RecordingFilesystem(),
        validator=RecordingValidator(),
    )
    replace_calls = []
    real_replace = plugin_core_module.os.replace

    def record_replace(source, destination):
        if Path(source).is_dir():
            replace_calls.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(plugin_core_module.os, "replace", record_replace)

    assert_dependency_error(
        plugin_core_module,
        "install_failed",
        lambda: stage(service, transaction, installer="pip"),
    )

    blocked = manager.load_transaction(transaction.operation_id)
    assert blocked.phase == "dependency_blocked"
    assert replace_calls == []
    assert Path(blocked.paths.live_code, "marker.txt").read_text(
        encoding="utf-8"
    ) == "old-code\n"
    assert Path(blocked.paths.live_dependencies, "marker.txt").read_text(
        encoding="utf-8"
    ) == "old-dependencies\n"


def test_code_activation_failure_rolls_back_code_and_dependency_snapshot_together(
    plugin_core_module, tmp_path, monkeypatch
):
    manager, transaction = create_real_transaction(
        plugin_core_module, tmp_path
    )
    service = real_service(plugin_core_module, manager)
    stage(service, transaction)
    real_replace = plugin_core_module.os.replace
    directory_renames = 0

    def fail_code_activation(source, destination):
        nonlocal directory_renames
        if Path(source).is_dir():
            directory_renames += 1
            if directory_renames == 4:
                raise OSError("injected code activation failure")
        return real_replace(source, destination)

    monkeypatch.setattr(
        plugin_core_module.os, "replace", fail_code_activation
    )

    with pytest.raises(OSError, match="injected code activation failure"):
        manager.activate(transaction.operation_id)

    rolled_back = manager.load_transaction(transaction.operation_id)
    assert rolled_back.phase == "rolled_back"
    assert Path(rolled_back.paths.live_code, "marker.txt").read_text(
        encoding="utf-8"
    ) == "old-code\n"
    assert Path(
        rolled_back.paths.live_dependencies, "marker.txt"
    ).read_text(encoding="utf-8") == "old-dependencies\n"
    assert not Path(
        rolled_back.paths.live_dependencies,
        "new_dependency",
    ).exists()


def test_successful_dependency_and_code_activation_requires_restart(
    plugin_core_module, tmp_path
):
    manager, transaction = create_real_transaction(
        plugin_core_module, tmp_path
    )
    service = real_service(plugin_core_module, manager)
    stage(service, transaction)

    activated = manager.activate(transaction.operation_id)

    assert activated.phase == "restart_pending"


@pytest.mark.parametrize("live_dependencies", [True, False])
def test_failed_new_install_removes_new_code_and_restores_or_removes_dependencies(
    plugin_core_module, tmp_path, monkeypatch, live_dependencies
):
    manager, transaction = create_real_transaction(
        plugin_core_module,
        tmp_path,
        operation="release_install",
        live_code=False,
        live_dependencies=live_dependencies,
    )
    old_dependencies = tree_snapshot(transaction.paths.live_dependencies)
    service = real_service(plugin_core_module, manager)
    stage(service, transaction)
    real_replace = plugin_core_module.os.replace

    def fail_new_code_activation(source, destination):
        if Path(destination) == Path(transaction.paths.live_code):
            raise OSError("new code activation failed")
        return real_replace(source, destination)

    monkeypatch.setattr(
        plugin_core_module.os, "replace", fail_new_code_activation
    )

    with pytest.raises(OSError, match="new code activation failed"):
        manager.activate(transaction.operation_id)

    rolled_back = manager.load_transaction(transaction.operation_id)
    assert rolled_back.phase == "rolled_back"
    assert not Path(rolled_back.paths.live_code).exists()
    assert tree_snapshot(rolled_back.paths.live_dependencies) == old_dependencies
    assert not Path(rolled_back.paths.staged_code).exists()
    assert not Path(rolled_back.paths.staged_dependencies).exists()
    assert not Path(rolled_back.paths.backup_code).exists()
    assert not Path(rolled_back.paths.backup_dependencies).exists()


def test_successful_new_install_reaches_restart_pending_without_fake_backups(
    plugin_core_module, tmp_path
):
    manager, transaction = create_real_transaction(
        plugin_core_module,
        tmp_path,
        operation="release_install",
        live_code=False,
        live_dependencies=False,
    )
    service = real_service(plugin_core_module, manager)
    stage(service, transaction)

    activated = manager.activate(transaction.operation_id)

    assert activated.phase == "restart_pending"
    assert Path(activated.paths.live_code, "marker.txt").read_text(
        encoding="utf-8"
    ) == "new-code\n"
    assert Path(
        activated.paths.live_dependencies, "new_dependency", "__init__.py"
    ).is_file()
    assert not Path(activated.paths.backup_code).exists()
    assert not Path(activated.paths.backup_dependencies).exists()


def test_base_plugin_python_compatibility_hybrid_resolution(plugin_core_module):
    plugin = plugin_core_module.BasePlugin()

    # Mock registry entries
    entry_compat = plugin_core_module.RegistryEntry(
        key="CompatPlugin",
        author="Author",
        repository="repo",
        description="desc",
        branch="master",
        requires_python=">=3.7",
    )
    entry_incompat = plugin_core_module.RegistryEntry(
        key="IncompatPlugin",
        author="Author",
        repository="repo",
        description="desc",
        branch="master",
        requires_python=">=3.12",
    )
    plugin.registry_entries = {
        "CompatPlugin": entry_compat,
        "IncompatPlugin": entry_incompat,
    }

    # Record dynamic incompatibility for an unannotated plugin
    plugin.record_python_incompatibility("DynamicIncompatPlugin", ">=3.11")

    compat_map = plugin.get_plugin_python_compatibility()

    assert "CompatPlugin" in compat_map
    assert compat_map["CompatPlugin"]["requires_python"] == ">=3.7"
    assert compat_map["CompatPlugin"]["compatible"] is True

    assert "IncompatPlugin" in compat_map
    assert compat_map["IncompatPlugin"]["requires_python"] == ">=3.12"
    expected_incompat = plugin_core_module.is_python_version_compatible(">=3.12")
    assert compat_map["IncompatPlugin"]["compatible"] == expected_incompat

    assert "DynamicIncompatPlugin" in compat_map
    assert compat_map["DynamicIncompatPlugin"]["requires_python"] == ">=3.11"
    expected_dyn = plugin_core_module.is_python_version_compatible(">=3.11")
    assert compat_map["DynamicIncompatPlugin"]["compatible"] == expected_dyn


def test_snapshot_service_records_dynamic_python_incompatibility(plugin_core_module, tmp_path):
    manager, transaction = create_real_transaction(
        plugin_core_module,
        tmp_path,
        operation="release_update",
    )
    service = real_service(plugin_core_module, manager)

    # Add a mock plugin instance on service with recording support
    service.plugin = plugin_core_module.BasePlugin()

    # Create sibling plugin with incompatible python requirement
    sibling_dir = Path(transaction.paths.live_code).parent / "IncompatibleSibling"
    sibling_dir.mkdir(parents=True, exist_ok=True)
    (sibling_dir / "plugin.py").write_text("print('sib')\n", encoding="utf-8")
    (sibling_dir / "requirements.txt").write_text("solaredge_modbus==0.8.0\n", encoding="utf-8")

    # Mock installer runner to simulate failure on sibling
    def fake_run_installer(installer, staged_dependencies, requirements_files, environment):
        files_str = str(requirements_files)
        if "IncompatibleSibling" in files_str:
            return "fake-pip", SimpleNamespace(
                returncode=1,
                stdout="ERROR: Package 'solaredge_modbus' requires a different Python: 3.7.3 not in '>=3.8'",
                stderr="",
            )
        return "fake-pip", SimpleNamespace(
            returncode=0,
            stdout="Successfully installed new-dependency",
            stderr="",
        )

    service._run_installer = fake_run_installer
    service._locate_installer = lambda: "fake-pip"

    stage(service, transaction)

    assert "IncompatibleSibling" in service.plugin.dynamic_python_requirements
    assert service.plugin.dynamic_python_requirements["IncompatibleSibling"] == ">=3.8"
