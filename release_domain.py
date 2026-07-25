"""Pure release-lifecycle contracts shared by backend orchestration and UI.

The module deliberately contains no Domoticz, filesystem, or network access.
It gives contributors one small place to understand the states that may cross
the backend/frontend boundary.
"""

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import re
from typing import Optional, Tuple


def _capture_loaded_source_fingerprint():
    """Fingerprint the source loaded into the manager runtime."""
    try:
        with open(__file__, "rb") as source_file:
            contents = source_file.read(4 * 1024 * 1024 + 1)
        if not contents or len(contents) > 4 * 1024 * 1024:
            return None
        return (len(contents), hashlib.sha256(contents).hexdigest())
    except OSError:
        return None


PYPLUGINSTORE_LOADED_SOURCE_FINGERPRINT = (
    _capture_loaded_source_fingerprint()
)


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEX_REVISION = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _require_text(value, label, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(label + " must be non-empty canonical text.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(label + " contains a control character.")
    return value


def _require_identifier(value, label):
    value = _require_text(value, label)
    if _SAFE_IDENTIFIER.fullmatch(value) is None or value in {".", ".."}:
        raise ValueError(label + " is not a safe identifier.")
    return value


def _enum_value(value, enum_type, label):
    if not isinstance(value, enum_type):
        raise ValueError(label + " is unsupported.")
    return value


class InstallationChannel(str, Enum):
    GIT = "git"
    RELEASE = "release"


class LifecyclePhase(str, Enum):
    IDLE = "idle"
    PREPARED = "prepared"
    ACTIVATED = "activated"
    RESTART_REQUIRED = "restart_required"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    CONFLICTED = "conflicted"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class NoticeSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class ObservedInstallationState:
    """One immutable observation of logical and physical installation state."""

    package_id: str
    installation_folder: str
    channel: InstallationChannel
    installed_version: str
    installed_revision: str
    release_id: Optional[str]
    working_tree_clean: bool

    def __post_init__(self):
        _require_identifier(self.package_id, "package_id")
        _require_identifier(self.installation_folder, "installation_folder")
        _enum_value(self.channel, InstallationChannel, "channel")
        _require_text(self.installed_version, "installed_version")
        _require_text(self.installed_revision, "installed_revision")
        if not isinstance(self.working_tree_clean, bool):
            raise ValueError("working_tree_clean must be a boolean.")
        if self.channel is InstallationChannel.GIT:
            if self.release_id is not None:
                raise ValueError(
                    "A Git installation cannot carry a release identity."
                )
        elif self.release_id is None:
            raise ValueError(
                "A Release installation requires a release identity."
            )
        else:
            _require_text(self.release_id, "release_id")

    def to_dict(self):
        return {
            "package_id": self.package_id,
            "installation_folder": self.installation_folder,
            "channel": self.channel.value,
            "installed_version": self.installed_version,
            "installed_revision": self.installed_revision,
            "release_id": self.release_id,
            "working_tree_clean": self.working_tree_clean,
        }


@dataclass(frozen=True)
class ReleaseCandidateState:
    """One immutable release candidate and its verification provenance."""

    release_id: str
    version: str
    tag: str
    commit: str
    source: str
    certified: bool
    stale: bool = False

    def __post_init__(self):
        _require_text(self.release_id, "release_id")
        _require_text(self.version, "version")
        _require_text(self.tag, "tag")
        commit = _require_text(self.commit, "commit").lower()
        if _HEX_REVISION.fullmatch(commit) is None:
            raise ValueError("commit must be an immutable Git object ID.")
        _require_identifier(self.source, "source")
        if not isinstance(self.certified, bool) or not isinstance(
            self.stale, bool
        ):
            raise ValueError("Candidate flags must be booleans.")
        if self.stale and not self.certified:
            raise ValueError("Only a certified candidate may be retained stale.")

    def to_dict(self):
        return {
            "release_id": self.release_id,
            "version": self.version,
            "tag": self.tag,
            "commit": self.commit,
            "source": self.source,
            "certified": self.certified,
            "stale": self.stale,
        }


@dataclass(frozen=True)
class TransitionState:
    """Durable lifecycle state presented independently from release freshness."""

    phase: LifecyclePhase
    operation: Optional[str]
    operation_id: Optional[str]
    restart_required: bool
    failure: Optional[str] = None

    def __post_init__(self):
        _enum_value(self.phase, LifecyclePhase, "phase")
        if not isinstance(self.restart_required, bool):
            raise ValueError("restart_required must be a boolean.")
        if self.restart_required != (
            self.phase is LifecyclePhase.RESTART_REQUIRED
        ):
            raise ValueError(
                "restart_required must match the restart_required phase."
            )
        if self.phase is LifecyclePhase.IDLE:
            if (
                self.operation is not None
                or self.operation_id is not None
                or self.failure is not None
            ):
                raise ValueError("An idle transition cannot carry operation state.")
            return
        _require_identifier(self.operation, "operation")
        _require_identifier(self.operation_id, "operation_id")
        if self.phase in {LifecyclePhase.FAILED, LifecyclePhase.CONFLICTED}:
            _require_text(self.failure, "failure")
        elif self.failure is not None:
            _require_text(self.failure, "failure")

    def to_dict(self):
        return {
            "phase": self.phase.value,
            "operation": self.operation,
            "operation_id": self.operation_id,
            "restart_required": self.restart_required,
            "failure": self.failure,
        }


@dataclass(frozen=True)
class LifecycleNotice:
    code: str
    severity: NoticeSeverity
    message: str

    def __post_init__(self):
        _require_identifier(self.code, "notice code")
        _enum_value(self.severity, NoticeSeverity, "notice severity")
        _require_text(self.message, "notice message")

    def to_dict(self):
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
        }


@dataclass(frozen=True)
class ActionDescriptor:
    """A backend-owned action decision; the frontend only renders it."""

    action: str
    label: str
    enabled: bool
    disabled_reason: Optional[str]
    confirmation_required: bool

    def __post_init__(self):
        action = _require_text(self.action, "action")
        if _SAFE_ACTION.fullmatch(action) is None:
            raise ValueError("action is unsupported.")
        _require_text(self.label, "label")
        if not isinstance(self.enabled, bool) or not isinstance(
            self.confirmation_required, bool
        ):
            raise ValueError("Action flags must be booleans.")
        if self.enabled and self.disabled_reason is not None:
            raise ValueError("An enabled action cannot have a disabled reason.")
        if not self.enabled and self.disabled_reason is None:
            raise ValueError("A disabled action requires a reason.")
        if self.disabled_reason is not None:
            _require_text(self.disabled_reason, "disabled_reason")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class PluginManagementView:
    """The complete backend-owned release-management view for one plugin."""

    installation: ObservedInstallationState
    certified_migration_target: Optional[ReleaseCandidateState]
    upstream_latest: Optional[ReleaseCandidateState]
    transition: TransitionState
    notice: Optional[LifecycleNotice]
    actions: Tuple[ActionDescriptor, ...]

    def __post_init__(self):
        if not isinstance(self.installation, ObservedInstallationState):
            raise ValueError("installation is invalid.")
        if self.certified_migration_target is not None and not isinstance(
            self.certified_migration_target, ReleaseCandidateState
        ):
            raise ValueError("certified_migration_target is invalid.")
        if self.upstream_latest is not None and not isinstance(
            self.upstream_latest, ReleaseCandidateState
        ):
            raise ValueError("upstream_latest is invalid.")
        if not isinstance(self.transition, TransitionState):
            raise ValueError("transition is invalid.")
        if self.notice is not None and not isinstance(
            self.notice, LifecycleNotice
        ):
            raise ValueError("notice is invalid.")
        if not isinstance(self.actions, tuple) or any(
            not isinstance(action, ActionDescriptor)
            for action in self.actions
        ):
            raise ValueError("actions must be action descriptors.")
        action_names = [action.action for action in self.actions]
        if len(action_names) != len(set(action_names)):
            raise ValueError("actions contain a duplicate.")
        if self.transition.restart_required:
            if self.notice is None or self.notice.code != "restart_required":
                raise ValueError(
                    "A restart transition requires a prominent restart notice."
                )
            update = next(
                (
                    action
                    for action in self.actions
                    if action.action == "update"
                ),
                None,
            )
            if update is not None and update.enabled:
                raise ValueError(
                    "Update cannot be enabled while restart is required."
                )

    def to_dict(self):
        return {
            "installation": self.installation.to_dict(),
            "certified_migration_target": (
                self.certified_migration_target.to_dict()
                if self.certified_migration_target is not None
                else None
            ),
            "upstream_latest": (
                self.upstream_latest.to_dict()
                if self.upstream_latest is not None
                else None
            ),
            "transition": self.transition.to_dict(),
            "notice": (
                self.notice.to_dict() if self.notice is not None else None
            ),
            "actions": [action.to_dict() for action in self.actions],
        }
