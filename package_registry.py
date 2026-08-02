# Copyright (C) 2018-2026 adrighem and PyPluginStore contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Strict provider-neutral package registry contracts.

The public v2 registry uses explicit package records. Legacy package-keyed
documents are accepted only through :meth:`RegistryDocument.from_legacy_document`
so compatibility does not leak into the current schema.
"""

import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass


def _capture_loaded_source_fingerprint():
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


REGISTRY_SCHEMA_VERSION = 2
SUPPORTED_PLATFORMS = ("linux", "windows")
SUPPORTED_PREFERENCES = {"git", "release", "release_if_indexed"}
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

REGISTRY_FIELDS = {"schema_version", "packages"}
UPDATE_TIMES_FIELDS = {"schema_version", "updates"}
UPDATE_TIME_FIELDS = {"package_id", "updated_at"}
PACKAGE_FIELDS = {
    "package_id",
    "domoticz_key",
    "description",
    "repository",
    "platforms",
    "delivery",
    "annotations",
}
REPOSITORY_FIELDS = {"url", "branch"}
DELIVERY_FIELDS = {"preferred", "git_supported", "release"}
RELEASE_FIELDS = {
    "provider",
    "channel",
    "tag_pattern",
    "artifact",
    "source_path",
    "mutable_paths",
    "allowed_origins",
    "api_base",
    "web_base",
    "release_page_size",
    "manifest_url",
    "asset_name",
    "asset_pattern",
    "allow_automatic_git_migration",
    "manifest_path",
}

DEFAULT_STABLE_TAG_PATTERN = r"^v?[0-9]+(?:\.[0-9]+){1,3}$"


def _require_exact_fields(document, label, allowed, required):
    if not isinstance(document, dict):
        raise ValueError(label + " must be an object.")
    missing = sorted(set(required) - set(document))
    unknown = sorted(set(document) - set(allowed))
    if missing:
        raise ValueError(label + " is missing " + ", ".join(missing) + ".")
    if unknown:
        raise ValueError(label + " contains unknown field " + unknown[0] + ".")
    return document


def _require_string(value, label, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError(label + " must be a string.")
    if value != value.strip() or unicodedata.normalize("NFC", value) != value:
        raise ValueError(label + " must be canonical.")
    if not allow_empty and not value:
        raise ValueError(label + " must not be empty.")
    if CONTROL_CHARACTERS.search(value):
        raise ValueError(label + " contains a control character.")
    return value


def _require_package_id(value):
    value = _require_string(value, "package_id")
    if (
        value in {".", ".."}
        or value.startswith(".")
        or "/" in value
        or "\\" in value
    ):
        raise ValueError("package_id is not a safe package identifier.")
    if value.casefold() == "idle":
        raise ValueError("The legacy Idle package is not valid in registry v2.")
    return value


def _require_domoticz_key(value):
    value = _require_string(value, "domoticz_key")
    if any(character in value for character in "<>\r\n"):
        raise ValueError("domoticz_key contains an unsafe character.")
    return value


def _repository_parts(url):
    url = _require_string(url, "repository.url")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("repository.url is invalid.") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or parsed.path.endswith("/")
        or url.endswith(".git")
    ):
        raise ValueError(
            "repository.url must be a canonical credential-free HTTPS web URL."
        )
    host = parsed.hostname.lower()
    netloc = host + ((":" + str(port)) if port is not None else "")
    if parsed.netloc != netloc:
        raise ValueError("repository.url host must be canonical lowercase.")
    parts = []
    for encoded_part in parsed.path.split("/"):
        if not encoded_part:
            continue
        part = urllib.parse.unquote(encoded_part)
        if (
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or CONTROL_CHARACTERS.search(part)
            or urllib.parse.quote(part, safe="-._~") != encoded_part
        ):
            raise ValueError("repository.url contains a non-canonical path.")
        parts.append(part)
    if len(parts) < 2:
        raise ValueError("repository.url must contain an owner and repository.")
    return url, netloc, parts


def _normalize_platforms(values):
    if not isinstance(values, list):
        raise ValueError("platforms must be a list.")
    result = []
    for value in values:
        platform = _require_string(value, "platform").lower()
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError("Unsupported platform " + platform + ".")
        if platform not in result:
            result.append(platform)
    return [item for item in SUPPORTED_PLATFORMS if item in result]


@dataclass(frozen=True)
class PackageRepository:
    url: str
    branch: str
    identity: str
    clone_url: str

    @classmethod
    def from_document(cls, document):
        document = _require_exact_fields(
            document,
            "repository",
            REPOSITORY_FIELDS,
            REPOSITORY_FIELDS,
        )
        url, host, parts = _repository_parts(document["url"])
        branch = _require_string(document["branch"], "repository.branch")
        identity = (host + "/" + "/".join(parts)).lower()
        return cls(url=url, branch=branch, identity=identity, clone_url=url + ".git")

    def to_document(self):
        return {"url": self.url, "branch": self.branch}


@dataclass(frozen=True)
class PackageDelivery:
    preferred: str
    git_supported: bool
    release: object = None

    @classmethod
    def from_document(cls, document):
        document = _require_exact_fields(
            document,
            "delivery",
            DELIVERY_FIELDS,
            {"preferred", "git_supported"},
        )
        preferred = _require_string(document["preferred"], "delivery.preferred")
        if preferred not in SUPPORTED_PREFERENCES:
            raise ValueError("delivery.preferred is unsupported.")
        git_supported = document["git_supported"]
        if type(git_supported) is not bool:
            raise ValueError("delivery.git_supported must be a boolean.")
        release = document.get("release")
        if release is not None:
            release = _require_exact_fields(
                release,
                "delivery.release",
                RELEASE_FIELDS,
                {"provider"},
            )
            release = copy.deepcopy(release)
        if preferred in {"release", "release_if_indexed"} and release is None:
            raise ValueError(
                "Release-first delivery requires delivery.release."
            )
        if preferred == "git" and not git_supported:
            raise ValueError("Git delivery requires git_supported.")
        return cls(preferred, git_supported, release)

    def to_document(self):
        document = {
            "preferred": self.preferred,
            "git_supported": self.git_supported,
        }
        if self.release is not None:
            document["release"] = copy.deepcopy(self.release)
        return document


@dataclass(frozen=True)
class PackageRecord:
    package_id: str
    domoticz_key: str
    description: str
    repository: PackageRepository
    platforms: tuple
    delivery: PackageDelivery
    annotations: object = None

    @classmethod
    def from_document(cls, document):
        document = _require_exact_fields(
            document,
            "package",
            PACKAGE_FIELDS,
            {
                "package_id",
                "domoticz_key",
                "description",
                "repository",
                "platforms",
                "delivery",
            },
        )
        annotations = document.get("annotations")
        if annotations is not None and not isinstance(annotations, dict):
            raise ValueError("package.annotations must be an object.")
        return cls(
            package_id=_require_package_id(document["package_id"]),
            domoticz_key=_require_domoticz_key(document["domoticz_key"]),
            description=_require_string(document["description"], "description"),
            repository=PackageRepository.from_document(document["repository"]),
            platforms=tuple(_normalize_platforms(document["platforms"])),
            delivery=PackageDelivery.from_document(document["delivery"]),
            annotations=copy.deepcopy(annotations),
        )

    @property
    def repository_url(self):
        return self.repository.url

    @property
    def repository_identity(self):
        return self.repository.identity

    @property
    def clone_url(self):
        return self.repository.clone_url

    @property
    def branch(self):
        return self.repository.branch

    @property
    def author(self):
        parsed = urllib.parse.urlsplit(self.repository.url)
        owner_path = "/".join(parsed.path.strip("/").split("/")[:-1])
        if parsed.hostname == "github.com":
            return owner_path
        if parsed.hostname in {"gitlab.com", "codeberg.org", "gitea.com"}:
            return parsed.hostname + "/" + owner_path
        return "https://" + parsed.netloc + "/" + owner_path

    @property
    def repository_name(self):
        return urllib.parse.unquote(
            urllib.parse.urlsplit(self.repository.url).path.rstrip("/").split("/")[-1]
        )

    def to_document(self):
        document = {
            "package_id": self.package_id,
            "domoticz_key": self.domoticz_key,
            "description": self.description,
            "repository": self.repository.to_document(),
            "platforms": list(self.platforms),
            "delivery": self.delivery.to_document(),
        }
        if self.annotations is not None:
            document["annotations"] = copy.deepcopy(self.annotations)
        return document


def _strict_json_document(contents, label):
    if not isinstance(contents, (bytes, bytearray)):
        raise ValueError(label + " must be bytes.")

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(label + " contains duplicate JSON key " + key + ".")
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
        raise ValueError(label + " must contain an object.")
    return document


def _legacy_repository_url(owner, repository):
    owner = _require_string(owner, "legacy owner").rstrip("/")
    repository = _require_string(repository, "legacy repository").strip("/")
    if "/" in repository or "\\" in repository:
        raise ValueError("legacy repository must be one path segment.")
    parsed = urllib.parse.urlsplit(owner)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("legacy custom owner must use HTTPS.")
        base = owner
    else:
        first = owner.split("/", 1)[0].lower()
        if first in {"github.com", "gitlab.com", "codeberg.org", "gitea.com"}:
            base = "https://" + owner
        else:
            base = "https://github.com/" + owner
    return base + "/" + repository


def _default_delivery_document(repository):
    host = urllib.parse.urlsplit(repository.url).hostname
    provider = {
        "github.com": "github",
        "gitlab.com": "gitlab",
        "codeberg.org": "codeberg",
    }.get(host)
    if provider is None:
        return {
            "preferred": "git",
            "git_supported": True,
        }
    return {
        "preferred": "release_if_indexed",
        "git_supported": True,
        "release": {
            "provider": provider,
            "channel": "stable",
            "tag_pattern": DEFAULT_STABLE_TAG_PATTERN,
            "artifact": "source_zip",
            "source_path": ".",
            "mutable_paths": [],
        },
    }


class RegistryDocument:
    """One normalized registry-v2 document."""

    def __init__(self, packages, *, legacy=False):
        self.schema_version = REGISTRY_SCHEMA_VERSION
        self.packages = tuple(sorted(packages, key=lambda item: item.package_id))
        self.by_package_id = {item.package_id: item for item in self.packages}
        self.legacy = bool(legacy)

    @classmethod
    def from_document(cls, document):
        document = _require_exact_fields(
            document,
            "registry",
            REGISTRY_FIELDS,
            REGISTRY_FIELDS,
        )
        if type(document["schema_version"]) is not int or document["schema_version"] != 2:
            raise ValueError("registry.schema_version is not supported.")
        package_documents = document["packages"]
        if not isinstance(package_documents, list):
            raise ValueError("registry.packages must be an array.")
        packages = []
        ids = set()
        folded_ids = set()
        repositories = set()
        for package_document in package_documents:
            package = PackageRecord.from_document(package_document)
            folded = unicodedata.normalize("NFC", package.package_id).casefold()
            if package.package_id in ids or folded in folded_ids:
                raise ValueError("Registry contains a duplicate package_id.")
            if package.repository_identity in repositories:
                raise ValueError("Registry contains a duplicate repository identity.")
            ids.add(package.package_id)
            folded_ids.add(folded)
            repositories.add(package.repository_identity)
            packages.append(package)
        return cls(packages)

    @classmethod
    def from_bytes(cls, contents):
        return cls.from_document(_strict_json_document(contents, "registry"))

    @classmethod
    def from_legacy_document(cls, document):
        if not isinstance(document, dict) or "schema_version" in document:
            raise ValueError("Legacy registry must be a package-keyed object.")
        packages = []
        folded_ids = set()
        repositories = set()
        for legacy_id, value in document.items():
            if legacy_id == "Idle":
                continue
            legacy_id = _require_package_id(legacy_id)
            if isinstance(value, list):
                if len(value) < 4:
                    raise ValueError("Legacy registry entry is incomplete.")
                owner, repository, description, branch = value[:4]
                platforms = value[5] if len(value) > 5 else []
                delivery_document = None
            elif isinstance(value, dict):
                owner = value.get("owner", value.get("author"))
                repository = value.get("repository", value.get("repo"))
                description = value.get("description", "")
                branch = value.get("branch", "master")
                platforms = value.get("platforms", value.get("platform", []))
                delivery_document = copy.deepcopy(value.get("delivery"))
                if delivery_document is not None:
                    delivery_document.pop("schema_version", None)
            else:
                raise ValueError("Legacy registry entry must be an array or object.")
            repository_url = _legacy_repository_url(owner, repository)
            repository_record = PackageRepository.from_document(
                {"url": repository_url, "branch": branch}
            )
            if delivery_document is None:
                delivery_document = _default_delivery_document(
                    repository_record
                )
            package_id = legacy_id
            if (
                legacy_id == "Domoticz-Shelly-plugin"
                and repository_record.identity
                == "codeberg.org/hoog/domoticz-shelly-plugin"
            ):
                package_id = "hoog-domoticz-shelly-plugin"
            folded = package_id.casefold()
            if folded in folded_ids:
                raise ValueError("Legacy registry contains a duplicate package_id.")
            if repository_record.identity in repositories:
                raise ValueError("Legacy registry contains a duplicate repository identity.")
            folded_ids.add(folded)
            repositories.add(repository_record.identity)
            packages.append(
                PackageRecord(
                    package_id=package_id,
                    domoticz_key="",
                    description=_require_string(description, "description"),
                    repository=repository_record,
                    platforms=tuple(_normalize_platforms(platforms)),
                    delivery=PackageDelivery.from_document(delivery_document),
                )
            )
        return cls(packages, legacy=True)

    @classmethod
    def from_legacy_bytes(cls, contents):
        return cls.from_legacy_document(_strict_json_document(contents, "legacy registry"))

    def to_document(self):
        if self.legacy and any(not item.domoticz_key for item in self.packages):
            raise ValueError("Legacy registry identities must be certified before v2 output.")
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "packages": [item.to_document() for item in self.packages],
        }

    def to_bytes(self):
        return (
            json.dumps(self.to_document(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")


PackageRegistry = RegistryDocument


@dataclass(frozen=True)
class PackageUpdateTime:
    package_id: str
    updated_at: str

    @classmethod
    def from_document(cls, document):
        document = _require_exact_fields(
            document,
            "update time",
            UPDATE_TIME_FIELDS,
            UPDATE_TIME_FIELDS,
        )
        package_id = _require_package_id(document["package_id"])
        updated_at = _require_string(document["updated_at"], "updated_at")
        try:
            parsed = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as error:
            raise ValueError(
                "updated_at must be a canonical UTC timestamp."
            ) from error
        canonical = parsed.replace(tzinfo=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if canonical != updated_at:
            raise ValueError("updated_at must be a canonical UTC timestamp.")
        return cls(package_id=package_id, updated_at=updated_at)

    def to_document(self):
        return {
            "package_id": self.package_id,
            "updated_at": self.updated_at,
        }


class UpdateTimesDocument:
    """Strict package update timestamps with an explicit legacy boundary."""

    def __init__(self, updates, *, legacy=False):
        self.schema_version = REGISTRY_SCHEMA_VERSION
        self.updates = tuple(
            sorted(
                updates,
                key=lambda item: (item.package_id.casefold(), item.package_id),
            )
        )
        self.by_package_id = {
            item.package_id: item.updated_at for item in self.updates
        }
        self.legacy = bool(legacy)

    @classmethod
    def from_document(cls, document):
        document = _require_exact_fields(
            document,
            "update times",
            UPDATE_TIMES_FIELDS,
            UPDATE_TIMES_FIELDS,
        )
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != REGISTRY_SCHEMA_VERSION
        ):
            raise ValueError("update_times.schema_version is not supported.")
        records = document["updates"]
        if not isinstance(records, list):
            raise ValueError("update_times.updates must be an array.")
        updates = []
        folded_ids = set()
        for raw_record in records:
            record = PackageUpdateTime.from_document(raw_record)
            folded_id = record.package_id.casefold()
            if folded_id in folded_ids:
                raise ValueError("Update times contain a duplicate package_id.")
            folded_ids.add(folded_id)
            updates.append(record)
        return cls(updates)

    @classmethod
    def from_bytes(cls, contents):
        return cls.from_document(
            _strict_json_document(contents, "update times")
        )

    @classmethod
    def from_legacy_document(cls, document):
        if not isinstance(document, dict) or "schema_version" in document:
            raise ValueError(
                "Legacy update times must be a package-keyed object."
            )
        updates = []
        folded_ids = set()
        for package_id, updated_at in document.items():
            updated_at = _require_string(updated_at, "updated_at")
            try:
                parsed = datetime.fromisoformat(
                    updated_at.replace("Z", "+00:00")
                )
            except ValueError as error:
                raise ValueError(
                    "Legacy updated_at is not an ISO 8601 timestamp."
                ) from error
            if parsed.tzinfo is None:
                raise ValueError(
                    "Legacy updated_at must include a timezone."
                )
            updated_at = (
                parsed.astimezone(timezone.utc)
                .replace(microsecond=0)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            record = PackageUpdateTime.from_document(
                {
                    "package_id": package_id,
                    "updated_at": updated_at,
                }
            )
            folded_id = record.package_id.casefold()
            if folded_id in folded_ids:
                raise ValueError(
                    "Legacy update times contain a duplicate package_id."
                )
            folded_ids.add(folded_id)
            updates.append(record)
        return cls(updates, legacy=True)

    @classmethod
    def from_legacy_bytes(cls, contents):
        return cls.from_legacy_document(
            _strict_json_document(contents, "legacy update times")
        )

    def to_document(self):
        return {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "updates": [item.to_document() for item in self.updates],
        }

    def to_bytes(self):
        return (
            json.dumps(self.to_document(), ensure_ascii=False, indent=2)
            + "\n"
        ).encode("utf-8")
