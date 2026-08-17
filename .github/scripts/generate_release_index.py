#!/usr/bin/env python3
"""Generate a deterministic, report-first release index.

Provider adapters only discover candidates.  This module owns the common trust
boundary: exact registry-byte binding, artifact download verification, safe ZIP
inspection, canonical tree identity, release lineage, mutation quarantine, and
optional atomic caching/output.
"""

import argparse
import copy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from types import SimpleNamespace
import unicodedata
import urllib.parse
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from package_identity import certify_plugin_py  # noqa: E402
from package_registry import PackageRegistry  # noqa: E402


INDEX_SCHEMA_VERSION = 2
LEGACY_INDEX_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 3
DEFAULT_VALIDITY_SECONDS = 16 * 24 * 60 * 60
DEFAULT_CACHE_TTL_SECONDS = 60 * 60
DEFAULT_MAX_ARCHIVE_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_JSON_SIZE = 4 * 1024 * 1024
DEFAULT_MAX_EXPANDED_SIZE = 250 * 1024 * 1024
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_MAX_COMPRESSION_RATIO = 100.0
SCANNER_USER_AGENT = "PyPluginStore-Release-Scanner"
PUBLIC_ARTIFACT_HEADERS = {"User-Agent": SCANNER_USER_AGENT}
LOWER_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *("com" + str(number) for number in range(1, 10)),
    *("lpt" + str(number) for number in range(1, 10)),
}
WINDOWS_FORBIDDEN_CHARACTERS = '<>:"|?*'
RESERVED_METADATA_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".bzr",
    ".pypluginstore",
    ".pypluginstore.json",
}
CANDIDATE_FIELDS = (
    "provider",
    "repository_identity",
    "release_id",
    "version",
    "tag",
    "released_at",
    "source_revision",
    "commit",
    "artifact_kind",
    "artifact_provenance",
    "artifact_url",
    "source_archive_url",
    "artifact_size",
    "provider_sha256",
    "source_path",
    "migration_mode",
    "migration_evidence",
)
ARTIFACT_FIELDS = (
    "kind",
    "provenance",
    "migration",
    "url",
    "sha256",
    "size",
    "tree_sha256",
    "root_prefix",
    "source_path",
)
CERTIFIED_IDENTITY_FIELDS = ("domoticz_key", "plugin_py_sha256")
CACHED_ARTIFACT_FIELDS = ARTIFACT_FIELDS + ("certified_identity",)
MIGRATION_MODES = {"automatic", "blocked", "manual"}
MIGRATION_EVIDENCE = {
    "commit_source_archive",
    "generic_manifest",
    "source_equivalent_asset",
    "unverified_asset",
}
RELEASE_ENTRY_FIELDS = {
    "certified_identity",
    "revision",
    "release_id",
    "supersedes",
    "provider",
    "repository_identity",
    "version",
    "tag",
    "released_at",
    "commit",
    "artifact",
}
LEGACY_RELEASE_ENTRY_FIELDS = RELEASE_ENTRY_FIELDS - {"certified_identity"}


class TransientProviderError(RuntimeError):
    """A discovery failure that should retain an already accepted release."""


@dataclass(frozen=True)
class ReleaseIndexGenerationResult:
    """In-memory index/report result and whether a tracked index was written."""

    document: dict
    index_bytes: bytes
    report: dict
    report_bytes: bytes
    wrote_index: bool = False


@dataclass(frozen=True)
class _CertifiedZip:
    """Validated archive metadata used directly in an index artifact object."""

    sha256: str
    size: int
    tree_sha256: str
    root_prefix: str
    domoticz_key: str
    plugin_py_sha256: str


def _now_utc(clock):
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime.")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _format_utc(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value, label):
    value = _require_string(value, label)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(label + " must be a canonical UTC timestamp.") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError(label + " must be a canonical UTC timestamp.")
    return parsed.replace(tzinfo=timezone.utc)


def _require_string(value, label, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError(label + " must be a string.")
    if value != value.strip() or CONTROL_CHARACTER_PATTERN.search(value):
        raise ValueError(label + " must be a canonical string.")
    if not value and not allow_empty:
        raise ValueError(label + " must not be empty.")
    return value


def _require_positive_integer(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(label + " must be a positive integer.")
    return value


def _require_sha256(value, label, allow_empty=False):
    value = _require_string(value, label, allow_empty=allow_empty)
    if value or not allow_empty:
        if not LOWER_SHA256_PATTERN.fullmatch(value):
            raise ValueError(label + " must be a lowercase SHA-256 digest.")
    return value


def _require_git_object_id(value, label, allow_empty=False):
    value = _require_string(value, label, allow_empty=allow_empty)
    if value or not allow_empty:
        if not GIT_OBJECT_ID_PATTERN.fullmatch(value):
            raise ValueError(label + " must be a full lowercase Git object ID.")
    return value


def _require_https_url(value, label, *, require_path=True):
    value = _require_string(value, label)
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(label + " is not a valid URL.") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (require_path and not parsed.path)
    ):
        raise ValueError(label + " must be a credential-free HTTPS URL.")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(label + " has an invalid port.")
    return value


def _require_https_origin(value, label):
    value = _require_string(value, label)
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(label + " is not a valid origin.") from error
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or "*" in hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(label + " must be an exact credential-free HTTPS origin.")
    hostname = hostname.lower()
    host = "[" + hostname + "]" if ":" in hostname else hostname
    if port not in (None, 443):
        host += ":" + str(port)
    return "https://" + host


def _normalize_relative_path(value, label, allow_root=True):
    value = _require_string(value, label)
    if value == "." and allow_root:
        return value
    if (
        value.startswith(("/", "\\"))
        or "\\" in value
        or WINDOWS_DRIVE_PATTERN.match(value)
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ValueError(label + " must be a canonical relative POSIX path.")
    parts = value.split("/")
    if any(not part or part in (".", "..") for part in parts):
        raise ValueError(label + " must be a normalized relative path.")
    for part in parts:
        if part.endswith((".", " ")) or ":" in part:
            raise ValueError(label + " is not portable.")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise ValueError(label + " contains a reserved path name.")
    return "/".join(parts)


def _strict_json_value(contents, label):
    if not isinstance(contents, (bytes, bytearray)):
        raise ValueError(label + " must be bytes.")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(label + " contains duplicate key " + str(key) + ".")
            result[key] = value
        return result

    try:
        document = json.loads(
            bytes(contents).decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(label + " contains non-finite number " + value + ".")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(label + " is not valid UTF-8 JSON.") from error
    return document


def _strict_json_object(contents, label):
    document = _strict_json_value(contents, label)
    if not isinstance(document, dict):
        raise ValueError(label + " must contain a JSON object.")
    return document


def _canonical_json_bytes(document):
    return (
        json.dumps(
            document,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _compact_json_bytes(document):
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _cache_key(namespace, document):
    digest = hashlib.sha256(_compact_json_bytes(document)).hexdigest()
    return namespace + ":" + digest


def _atomic_write(path, contents):
    """Replace one file atomically and durably without exposing partial JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = None
    temporary_path = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix="." + target.name + ".",
            suffix=".tmp",
            dir=str(target.parent),
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(bytes(contents))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        try:
            directory_descriptor = os.open(str(target.parent), os.O_RDONLY)
        except OSError:
            directory_descriptor = None
        if directory_descriptor is not None:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


class ReleaseCandidateCache:
    """TTL-bound, strict JSON cache for discovery and ZIP certification."""

    def __init__(self, path, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS, clock=None):
        self.path = Path(_require_string(str(path), "cache path"))
        self.ttl_seconds = _require_positive_integer(
            ttl_seconds, "cache ttl_seconds"
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._loaded = False
        self._entries = {}

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        if not self.path.is_file():
            return
        try:
            document = _strict_json_object(self.path.read_bytes(), "release cache")
            if set(document) != {"schema_version", "entries"}:
                return
            if document["schema_version"] != CACHE_SCHEMA_VERSION:
                return
            entries = document["entries"]
            if not isinstance(entries, dict):
                return
            clean = {}
            for key, entry in entries.items():
                if not isinstance(key, str) or not isinstance(entry, dict):
                    continue
                if set(entry) != {"stored_at", "payload"}:
                    continue
                stored_at = entry.get("stored_at")
                if type(stored_at) not in (int, float) or isinstance(stored_at, bool):
                    continue
                if not math.isfinite(stored_at):
                    continue
                clean[key] = copy.deepcopy(entry)
            self._entries = clean
        except (OSError, ValueError):
            # A cache is an optimization, never an authority or availability
            # dependency.  A corrupt/partial cache is ignored and replaced only
            # after fresh provider work succeeds.
            self._entries = {}

    def _timestamp(self):
        return _now_utc(self.clock).timestamp()

    def get(self, key):
        self._load()
        entry = self._entries.get(key)
        if entry is None:
            return None
        age = self._timestamp() - entry["stored_at"]
        if age < 0 or age > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        return copy.deepcopy(entry["payload"])

    def put(self, key, payload):
        self._load()
        # Round-trip now so cache persistence cannot fail after provider work
        # because a caller supplied an unserializable object.
        try:
            payload_copy = json.loads(_compact_json_bytes(payload).decode("utf-8"))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("cache payload is not canonical JSON.") from error
        self._entries[key] = {
            "stored_at": self._timestamp(),
            "payload": payload_copy,
        }
        document = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "entries": dict(sorted(self._entries.items())),
        }
        _atomic_write(self.path, _canonical_json_bytes(document))


def _module_from_sibling(name):
    """Load a secure component without relying on the working directory."""
    module_name = "_pypluginstore_" + name
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    if name == "release_providers":
        path = Path(__file__).resolve().parents[2] / "release_providers.py"
    else:
        path = Path(__file__).resolve().with_name(name + ".py")
    if not path.is_file():
        raise RuntimeError("Required secure component is unavailable: " + path.name)
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError("Cannot load required component: " + path.name)
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    try:
        specification.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def default_provider_adapters():
    """Return fresh adapters for every v1 discovery backend."""
    providers = _module_from_sibling("release_providers")
    forgejo = providers.ForgejoReleaseAdapter()
    return {
        "github": providers.GitHubReleaseAdapter(),
        "gitlab": providers.GitLabReleaseAdapter(),
        "forgejo": forgejo,
        # `codeberg` is accepted as a registry spelling but shares only this
        # explicit adapter instance; the adapter still reports forgejo provenance.
        "codeberg": forgejo,
        "gitea": providers.GiteaReleaseAdapter(),
        "generic": providers.GenericManifestAdapter(),
    }


def _default_secure_client(max_bytes):
    """Build a bounded client without falling back to a generic URL opener."""
    release_http = _module_from_sibling("release_http")
    for name in (
        "default_secure_client",
        "make_default_client",
        "create_default_client",
    ):
        factory = getattr(release_http, name, None)
        if callable(factory):
            return factory(max_bytes=max_bytes)
    client_type = getattr(release_http, "SafeReleaseHttpClient", None)
    if client_type is not None:
        for name in ("from_system", "default"):
            factory = getattr(client_type, name, None)
            if callable(factory):
                return factory(max_bytes=max_bytes)
    raise RuntimeError(
        "Secure release HTTP defaults are unavailable; refusing an unsafe fallback."
    )


def default_secure_http_client():
    """Return the bounded artifact client used by the command-line workflow."""
    return _default_secure_client(DEFAULT_MAX_ARCHIVE_SIZE)


class SecureJsonTransport:
    """Decode one bounded HTTP download as strict provider JSON."""

    def __init__(
        self,
        http_client,
        max_bytes=DEFAULT_MAX_JSON_SIZE,
        authentication_headers=None,
    ):
        if not callable(getattr(http_client, "download", None)):
            raise ValueError("JSON http_client must provide download().")
        self.http_client = http_client
        self.max_bytes = _require_positive_integer(max_bytes, "JSON max_bytes")
        if authentication_headers is None:
            authentication_headers = {}
        if not isinstance(authentication_headers, dict):
            raise ValueError("JSON authentication headers must be an origin mapping.")
        self.authentication_headers = {}
        for origin, headers in authentication_headers.items():
            normalized_origin = _require_https_origin(origin, "authentication origin")
            if normalized_origin != origin:
                raise ValueError("Authentication origins must be canonical.")
            if not isinstance(headers, dict) or not headers:
                raise ValueError("Authentication headers must be a non-empty mapping.")
            clean_headers = {}
            for name, value in headers.items():
                name = _require_string(name, "authentication header name")
                value = _require_string(value, "authentication header value")
                if name.casefold() in {
                    existing.casefold() for existing in clean_headers
                }:
                    raise ValueError("Authentication header names must be unique.")
                clean_headers[name] = value
            self.authentication_headers[origin] = clean_headers

    @staticmethod
    def _request_origin(url):
        parsed = urllib.parse.urlsplit(_require_https_url(url, "provider URL"))
        return _require_https_origin(
            "https://" + parsed.netloc,
            "provider URL origin",
        )

    def get_json(self, url, headers=None):
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            raise ValueError("JSON request headers must be a mapping.")
        request_headers = dict(headers)
        authentication = self.authentication_headers.get(
            self._request_origin(url), {}
        )
        existing_names = {name.casefold() for name in request_headers}
        for name, value in authentication.items():
            if name.casefold() in existing_names:
                raise ValueError(
                    "Provider headers conflict with configured authentication."
                )
            request_headers[name] = value
        downloaded = self.http_client.download(
            url,
            headers=request_headers,
            expected_sha256=None,
            expected_size=None,
            allowed_origins=[],
        )
        data = getattr(downloaded, "data", None)
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("HTTP client did not return provider JSON bytes.")
        data = bytes(data)
        if not data or len(data) > self.max_bytes:
            raise ValueError("Provider JSON response exceeds the size limit.")
        if getattr(downloaded, "size", len(data)) != len(data):
            raise ValueError("HTTP client returned an inconsistent JSON length.")
        digest = hashlib.sha256(data).hexdigest()
        if getattr(downloaded, "sha256", digest) != digest:
            raise ValueError("HTTP client returned an inconsistent JSON digest.")
        return _strict_json_value(data, "provider response")


def default_secure_json_transport():
    """Return a separately bounded provider-API JSON transport."""
    authentication_headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        authentication_headers["https://api.github.com"] = {
            "Authorization": "Bearer "
            + _require_string(github_token, "GITHUB_TOKEN")
        }
    gitlab_token = os.environ.get("GITLAB_TOKEN")
    if gitlab_token:
        authentication_headers["https://gitlab.com"] = {
            "PRIVATE-TOKEN": _require_string(gitlab_token, "GITLAB_TOKEN")
        }
    return SecureJsonTransport(
        _default_secure_client(DEFAULT_MAX_JSON_SIZE),
        max_bytes=DEFAULT_MAX_JSON_SIZE,
        authentication_headers=authentication_headers,
    )


def _candidate_snapshot(candidate):
    if candidate is None:
        return None
    if is_dataclass(candidate):
        document = asdict(candidate)
    elif isinstance(candidate, dict):
        document = copy.deepcopy(candidate)
    else:
        try:
            document = {
                field: getattr(candidate, field) for field in CANDIDATE_FIELDS
            }
        except AttributeError as error:
            raise ValueError("Provider candidate has an incomplete shape.") from error
    if set(document) != set(CANDIDATE_FIELDS):
        raise ValueError("Provider candidate has an unexpected shape.")
    return {field: document[field] for field in CANDIDATE_FIELDS}


def _validate_candidate(document, provider, repository_identity, now):
    if not isinstance(document, dict) or set(document) != set(CANDIDATE_FIELDS):
        raise ValueError("Provider candidate has an unexpected shape.")
    selected_provider = _require_string(document["provider"], "candidate.provider")
    accepted_providers = {provider}
    if provider == "codeberg":
        accepted_providers.add("forgejo")
    if selected_provider not in accepted_providers:
        raise ValueError("Provider candidate changed provider identity.")
    identity = _require_string(
        document["repository_identity"], "candidate.repository_identity"
    )
    if identity != repository_identity:
        raise ValueError("Provider candidate changed repository identity.")
    _require_string(document["release_id"], "candidate.release_id")
    _require_string(document["version"], "candidate.version")
    tag = _require_string(document["tag"], "candidate.tag", allow_empty=True)
    if selected_provider != "generic" and not tag:
        raise ValueError("Forge candidate requires a release tag.")
    released_at = _parse_utc(document["released_at"], "candidate.released_at")
    if released_at > now:
        raise ValueError("Provider candidate is dated in the future.")
    source_revision = _require_string(
        document["source_revision"], "candidate.source_revision"
    )
    commit = _require_git_object_id(
        document["commit"], "candidate.commit", allow_empty=True
    )
    if selected_provider != "generic" and not commit:
        raise ValueError("Forge candidate requires a resolved commit.")
    if selected_provider != "generic" and commit and source_revision != commit:
        raise ValueError("Git candidate source_revision must equal its commit.")
    if document["artifact_kind"] not in {"asset_zip", "generic_zip", "source_zip"}:
        raise ValueError("Candidate artifact kind is unsupported.")
    if document["artifact_provenance"] not in {
        "attached_asset",
        "forge_release_asset",
        "forge_source_archive",
        "generic_manifest",
        "release_asset",
    }:
        raise ValueError("Candidate artifact provenance is unsupported.")
    _require_https_url(document["artifact_url"], "candidate.artifact_url")
    source_archive_url = _require_string(
        document["source_archive_url"],
        "candidate.source_archive_url",
        allow_empty=True,
    )
    if source_archive_url:
        _require_https_url(
            source_archive_url,
            "candidate.source_archive_url",
        )
    artifact_size = document["artifact_size"]
    if artifact_size is not None:
        _require_positive_integer(artifact_size, "candidate.artifact_size")
    _require_sha256(
        document["provider_sha256"],
        "candidate.provider_sha256",
        allow_empty=True,
    )
    _normalize_relative_path(document["source_path"], "candidate.source_path")
    migration_mode = _require_string(
        document["migration_mode"], "candidate.migration_mode"
    )
    migration_evidence = _require_string(
        document["migration_evidence"], "candidate.migration_evidence"
    )
    if migration_mode not in {"automatic", "blocked", "manual"}:
        raise ValueError("candidate.migration_mode is unsupported.")
    if migration_evidence not in {
        "commit_source_archive",
        "generic_manifest",
        "unverified_asset",
    }:
        raise ValueError("candidate.migration_evidence is unsupported.")
    if migration_mode == "automatic" and not commit:
        raise ValueError("Automatic migration requires a resolved commit.")
    if selected_provider != "generic" and not source_archive_url:
        raise ValueError("Forge candidates require a source archive URL.")
    if migration_mode == "automatic" and not source_archive_url:
        raise ValueError("Automatic migration requires a source archive URL.")
    if migration_mode == "automatic" and (
        migration_evidence != "commit_source_archive"
    ):
        raise ValueError(
            "Automatic provider migration requires commit source evidence."
        )
    return SimpleNamespace(**copy.deepcopy(document))


def _validate_migration(document, label="migration"):
    if not isinstance(document, dict) or set(document) != {"mode", "evidence"}:
        raise ValueError(label + " has an unexpected shape.")
    mode = _require_string(document["mode"], label + ".mode")
    evidence = _require_string(document["evidence"], label + ".evidence")
    if mode not in MIGRATION_MODES:
        raise ValueError(label + ".mode is unsupported.")
    if evidence not in MIGRATION_EVIDENCE:
        raise ValueError(label + ".evidence is unsupported.")
    if mode == "automatic" and evidence not in {
        "commit_source_archive",
        "source_equivalent_asset",
    }:
        raise ValueError(label + " lacks automatic migration evidence.")
    if mode == "manual" and evidence in {
        "commit_source_archive",
        "source_equivalent_asset",
    }:
        raise ValueError(label + " has contradictory migration evidence.")
    return {"mode": mode, "evidence": evidence}


def _validate_certified_identity(document, label="certified_identity"):
    if not isinstance(document, dict) or set(document) != set(
        CERTIFIED_IDENTITY_FIELDS
    ):
        raise ValueError(label + " has an unexpected shape.")
    domoticz_key = _require_string(
        document["domoticz_key"], label + ".domoticz_key"
    )
    if any(character in domoticz_key for character in "<>\r\n"):
        raise ValueError(label + ".domoticz_key is unsafe.")
    return {
        "domoticz_key": domoticz_key,
        "plugin_py_sha256": _require_sha256(
            document["plugin_py_sha256"], label + ".plugin_py_sha256"
        ),
    }


def _validate_public_artifact(document, label="artifact"):
    if not isinstance(document, dict) or set(document) != set(ARTIFACT_FIELDS):
        raise ValueError(label + " has an unexpected shape.")
    kind = _require_string(document["kind"], label + ".kind")
    if kind not in {"asset_zip", "generic_zip", "source_zip"}:
        raise ValueError(label + ".kind is unsupported.")
    provenance = _require_string(
        document["provenance"], label + ".provenance"
    )
    if provenance not in {
        "attached_asset",
        "forge_release_asset",
        "forge_source_archive",
        "generic_manifest",
        "release_asset",
    }:
        raise ValueError(label + ".provenance is unsupported.")
    root_prefix = _normalize_relative_path(
        document["root_prefix"], label + ".root_prefix"
    )
    if root_prefix != "." and "/" in root_prefix:
        raise ValueError(label + ".root_prefix must be one wrapper segment.")
    if kind == "source_zip" and root_prefix == ".":
        raise ValueError(label + " source ZIP requires one wrapper segment.")
    return {
        "kind": kind,
        "provenance": provenance,
        "migration": _validate_migration(
            document["migration"], label + ".migration"
        ),
        "url": _require_https_url(document["url"], label + ".url"),
        "sha256": _require_sha256(document["sha256"], label + ".sha256"),
        "size": _require_positive_integer(document["size"], label + ".size"),
        "tree_sha256": _require_sha256(
            document["tree_sha256"], label + ".tree_sha256"
        ),
        "root_prefix": root_prefix,
        "source_path": _normalize_relative_path(
            document["source_path"], label + ".source_path"
        ),
    }


def _validate_cached_artifact(document, candidate):
    if not isinstance(document, dict) or set(document) != set(
        CACHED_ARTIFACT_FIELDS
    ):
        raise ValueError("Cached artifact has an unexpected shape.")
    if document["kind"] != candidate.artifact_kind:
        raise ValueError("Cached artifact kind changed.")
    if document["provenance"] != candidate.artifact_provenance:
        raise ValueError("Cached artifact provenance changed.")
    migration = _validate_migration(
        document["migration"], "cached artifact migration"
    )
    if migration != {
        "mode": candidate.migration_mode,
        "evidence": candidate.migration_evidence,
    }:
        raise ValueError("Cached artifact migration policy changed.")
    if document["url"] != candidate.artifact_url:
        raise ValueError("Cached artifact URL changed.")
    _require_sha256(document["sha256"], "artifact.sha256")
    _require_positive_integer(document["size"], "artifact.size")
    _require_sha256(document["tree_sha256"], "artifact.tree_sha256")
    root = _normalize_relative_path(
        document["root_prefix"], "artifact.root_prefix"
    )
    if root != "." and "/" in root:
        raise ValueError("artifact.root_prefix must be one wrapper segment.")
    if document["source_path"] != candidate.source_path:
        raise ValueError("Cached artifact source path changed.")
    _validate_certified_identity(
        document["certified_identity"], "cached certified identity"
    )
    return copy.deepcopy(document)


def _safe_zip_path(filename):
    if not isinstance(filename, str) or not filename:
        raise ValueError("ZIP member name must be non-empty text.")
    if (
        CONTROL_CHARACTER_PATTERN.search(filename)
        or any(unicodedata.category(character) == "Cc" for character in filename)
        or "\\" in filename
        or filename.startswith("/")
        or WINDOWS_DRIVE_PATTERN.match(filename)
    ):
        raise ValueError("ZIP member has an unsafe path.")
    directory = filename.endswith("/")
    stripped = filename[:-1] if directory else filename
    if not stripped:
        raise ValueError("ZIP member has an empty path.")
    raw_parts = stripped.split("/")
    if any(not part or part in (".", "..") for part in raw_parts):
        raise ValueError("ZIP member path is not normalized.")
    normalized_parts = []
    for part in raw_parts:
        normalized = unicodedata.normalize("NFC", part)
        if (
            not normalized
            or normalized != part
            or CONTROL_CHARACTER_PATTERN.search(normalized)
        ):
            raise ValueError("ZIP member path is not canonical Unicode.")
        if normalized.endswith((".", " ")) or any(
            character in WINDOWS_FORBIDDEN_CHARACTERS
            for character in normalized
        ):
            raise ValueError("ZIP member path is not portable.")
        if normalized.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise ValueError("ZIP member uses a reserved path name.")
        if normalized.casefold() in RESERVED_METADATA_PARTS:
            raise ValueError("ZIP member contains manager or VCS metadata.")
        normalized_parts.append(normalized)
    return "/".join(normalized_parts), directory


def _zip_member_kind(info):
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode) if info.create_system == 3 else 0
    if kind == 0:
        return "unspecified"
    if kind == stat.S_IFREG:
        return "file"
    if kind == stat.S_IFDIR:
        return "directory"
    raise ValueError("ZIP contains a link or special file.")


def _certify_zip_bytes(data, candidate):
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Downloaded artifact data must be bytes.")
    data = bytes(data)
    if not data or len(data) > DEFAULT_MAX_ARCHIVE_SIZE:
        raise ValueError("Downloaded ZIP size is outside the certification limit.")

    files = []
    members = []
    seen_normalized = set()
    seen_casefold = {}
    component_spellings = {}
    node_kinds = {}
    expanded_size = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > DEFAULT_MAX_ENTRIES:
                raise ValueError("ZIP entry count is outside the certification limit.")
            for info in infos:
                original_name = getattr(info, "orig_filename", info.filename)
                if original_name != info.filename or "\0" in original_name:
                    raise ValueError("ZIP member contains a NUL byte.")
                if info.flag_bits & (0x1 | 0x40):
                    raise ValueError("Encrypted ZIP members are unsupported.")
                normalized_path, named_directory = _safe_zip_path(original_name)
                member_kind = _zip_member_kind(info)
                is_directory = named_directory
                if (is_directory and member_kind == "file") or (
                    not is_directory and member_kind == "directory"
                ):
                    raise ValueError("ZIP directory metadata is inconsistent.")
                if is_directory and info.file_size != 0:
                    raise ValueError("ZIP directory metadata is inconsistent.")
                members.append((normalized_path, is_directory))

                path_parts = tuple(normalized_path.split("/"))
                canonical_parts = tuple(part.casefold() for part in path_parts)
                for index, (canonical_part, original_part) in enumerate(
                    zip(canonical_parts, path_parts)
                ):
                    spelling_key = (canonical_parts[:index], canonical_part)
                    previous_component = component_spellings.get(spelling_key)
                    if (
                        previous_component is not None
                        and previous_component != original_part
                    ):
                        raise ValueError(
                            "ZIP contains a Unicode or case-fold path collision."
                        )
                    component_spellings[spelling_key] = original_part

                for prefix_length in range(1, len(canonical_parts)):
                    prefix = canonical_parts[:prefix_length]
                    if node_kinds.get(prefix) == "file":
                        raise ValueError(
                            "ZIP contains a file/directory prefix collision."
                        )
                    node_kinds.setdefault(prefix, "directory")
                existing_kind = node_kinds.get(canonical_parts)
                if is_directory:
                    if existing_kind == "file":
                        raise ValueError(
                            "ZIP contains a file/directory prefix collision."
                        )
                    node_kinds[canonical_parts] = "directory"
                else:
                    if existing_kind is not None:
                        raise ValueError(
                            "ZIP contains a file/directory prefix collision."
                        )
                    node_kinds[canonical_parts] = "file"

                collision_key = normalized_path.casefold()
                previous_spelling = seen_casefold.get(collision_key)
                if previous_spelling is not None and previous_spelling != normalized_path:
                    raise ValueError("ZIP contains a Unicode or case-fold path collision.")
                seen_casefold[collision_key] = normalized_path
                if normalized_path in seen_normalized:
                    raise ValueError("ZIP contains a duplicate member path.")
                seen_normalized.add(normalized_path)
                if is_directory:
                    continue

                if info.file_size < 0 or info.file_size > DEFAULT_MAX_FILE_SIZE:
                    raise ValueError("ZIP member exceeds the per-file limit.")
                expanded_size += info.file_size
                if expanded_size > DEFAULT_MAX_EXPANDED_SIZE:
                    raise ValueError("ZIP exceeds the expanded-size limit.")
                if info.file_size:
                    if info.compress_size <= 0:
                        raise ValueError("ZIP member has an invalid compressed size.")
                    if info.file_size / info.compress_size > DEFAULT_MAX_COMPRESSION_RATIO:
                        raise ValueError("ZIP member exceeds the compression-ratio limit.")
                contents = archive.read(info)
                if len(contents) != info.file_size:
                    raise ValueError("ZIP member length changed while reading.")
                files.append((normalized_path, contents))
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        raise ValueError("Artifact is not a valid supported ZIP archive.") from error

    if not files:
        raise ValueError("ZIP contains no regular files.")

    first_parts = [path.split("/", 1) for path, _contents in files]
    if all(len(parts) == 2 for parts in first_parts):
        possible_root = first_parts[0][0]
        if all(parts[0] == possible_root for parts in first_parts):
            root_prefix = possible_root
            relative_files = [
                (parts[1], contents)
                for parts, (_path, contents) in zip(first_parts, files)
            ]
        else:
            root_prefix = "."
            relative_files = files
    else:
        root_prefix = "."
        relative_files = files
    root_prefix = _normalize_relative_path(root_prefix, "artifact.root_prefix")
    if root_prefix != "." and "/" in root_prefix:
        raise ValueError("ZIP root wrapper must be one path segment.")
    if candidate.artifact_kind == "source_zip" and root_prefix == ".":
        raise ValueError("Forge source ZIP must have one wrapper directory.")
    if root_prefix != "." and any(
        path != root_prefix and not path.startswith(root_prefix + "/")
        for path, _is_directory in members
    ):
        raise ValueError("ZIP contains paths outside its single wrapper.")

    relative_paths = sorted(path for path, _contents in relative_files)
    for index, path in enumerate(relative_paths[:-1]):
        if relative_paths[index + 1].startswith(path + "/"):
            raise ValueError("ZIP contains a file/directory prefix collision.")
    expected_plugin = (
        "plugin.py"
        if candidate.source_path == "."
        else candidate.source_path + "/plugin.py"
    )
    if expected_plugin not in set(relative_paths):
        raise ValueError("Configured source_path does not contain plugin.py.")
    plugin_contents = dict(relative_files)[expected_plugin]
    package_identity = certify_plugin_py(plugin_contents)

    records = []
    for path, contents in relative_files:
        digest = hashlib.sha256(contents).hexdigest()
        records.append(
            path.encode("utf-8")
            + b"\0"
            + str(len(contents)).encode("ascii")
            + b"\0"
            + digest.encode("ascii")
            + b"\n"
        )
    tree_sha256 = hashlib.sha256(b"".join(sorted(records))).hexdigest()
    return _CertifiedZip(
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        tree_sha256=tree_sha256,
        root_prefix=root_prefix,
        domoticz_key=package_identity.domoticz_key,
        plugin_py_sha256=package_identity.plugin_py_sha256,
    )


def _registry_coordinates(entry):
    if isinstance(entry, list):
        if len(entry) < 2:
            raise ValueError("Legacy registry entry is incomplete.")
        owner, repository = entry[:2]
    elif isinstance(entry, dict):
        repository_document = entry.get("repository")
        if isinstance(repository_document, dict):
            repository_url = repository_document.get("url")
            try:
                parsed = urllib.parse.urlsplit(repository_url)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "Registry repository URL is invalid."
                ) from error
            path_parts = parsed.path.strip("/").split("/")
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or len(path_parts) < 2
            ):
                raise ValueError(
                    "Registry repository URL is incomplete."
                )
            owner = (
                "https://"
                + parsed.netloc
                + "/"
                + "/".join(path_parts[:-1])
            )
            repository = path_parts[-1]
        else:
            owner = entry.get("owner", entry.get("author"))
            repository = entry.get("repository", entry.get("repo"))
    else:
        raise ValueError("Registry entry must be an object or legacy list.")
    owner = _require_string(owner, "registry owner")
    repository = _require_string(repository, "registry repository")
    if "/" in repository or "\\" in repository or repository in (".", ".."):
        raise ValueError("Registry repository must be one path segment.")
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not repository:
        raise ValueError("Registry repository is empty.")
    return owner, repository


def _registry_records(document):
    """Normalize strict v2 while keeping v1 at an explicit read boundary."""
    if not isinstance(document, dict):
        raise ValueError("Registry must be an object.")
    if "schema_version" not in document:
        return copy.deepcopy(document)
    registry = PackageRegistry.from_document(document)
    return {
        package.package_id: package.to_document()
        for package in registry.packages
    }


def _repository_location(owner, repository):
    source = owner.strip().rstrip("/")
    if "://" in source:
        try:
            parsed = urllib.parse.urlsplit(source)
            parsed.port
        except ValueError as error:
            raise ValueError("Registry owner URL is invalid.") from error
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Registry owner URL must be credential-free HTTPS.")
        host = parsed.netloc.lower()
        owner_path = parsed.path.strip("/")
    else:
        first = source.split("/", 1)[0]
        if "." in first or ":" in first:
            host = first.lower()
            owner_path = source.split("/", 1)[1] if "/" in source else ""
        else:
            host = "github.com"
            owner_path = source
    parts = [part for part in owner_path.split("/") if part]
    parts.append(repository)
    canonical_parts = []
    for part in parts:
        decoded = urllib.parse.unquote(part)
        if (
            not decoded
            or decoded in (".", "..")
            or "/" in decoded
            or "\\" in decoded
            or CONTROL_CHARACTER_PATTERN.search(decoded)
        ):
            raise ValueError("Registry repository path is unsafe.")
        canonical_parts.append(decoded.lower())
    if len(canonical_parts) < 2:
        raise ValueError("Registry repository identity is incomplete.")
    return host + "/" + "/".join(canonical_parts), host, canonical_parts


def _implicit_provider(host):
    if host == "gitlab.com":
        return "gitlab"
    if host == "codeberg.org":
        return "forgejo"
    return "github"


def _effective_delivery(entry, host):
    delivery = entry.get("delivery") if isinstance(entry, dict) else None
    if delivery is None:
        provider = _implicit_provider(host)
        return {
            "schema_version": 1,
            "preferred": "release_if_indexed",
            "git_supported": True,
            "release": {
                "provider": provider,
                "channel": "stable",
                "tag_pattern": r"^v?[0-9]+(?:\.[0-9]+){1,3}$",
                "artifact": "source_zip",
                "source_path": ".",
                "mutable_paths": [],
            },
        }
    if not isinstance(delivery, dict):
        raise ValueError("Registry delivery policy must be an object.")
    preferred = _require_string(delivery.get("preferred"), "delivery.preferred")
    if preferred not in {"git", "release", "release_if_indexed"}:
        raise ValueError("delivery.preferred is unsupported.")
    release = delivery.get("release")
    if release is not None and not isinstance(release, dict):
        raise ValueError("delivery.release must be an object.")
    return copy.deepcopy(delivery)


def _repository_for_provider(identity, host, parts, policy):
    provider = _require_string(policy.get("provider"), "delivery.release.provider").lower()
    origin = "https://" + host
    repository = {
        "repository_identity": identity,
    }
    if provider == "github":
        if len(parts) != 2:
            raise ValueError("GitHub repository must have one owner segment.")
        repository.update(
            owner=parts[0],
            repository=parts[1],
            api_base=policy.get(
                "api_base",
                "https://api.github.com" if host == "github.com" else origin + "/api/v3",
            ),
            web_base=origin,
        )
    elif provider == "gitlab":
        repository.update(
            project_path="/".join(parts),
            api_base=policy.get("api_base", origin + "/api/v4"),
            web_base=origin,
        )
    elif provider in ("forgejo", "codeberg"):
        if len(parts) != 2:
            raise ValueError("Forgejo repository must have one owner segment.")
        repository.update(owner=parts[0], repository=parts[1])
        if host != "codeberg.org":
            repository.update(
                api_base=policy.get("api_base"),
                web_base=origin,
                release_page_size=policy.get("release_page_size", 50),
            )
        elif "api_base" in policy:
            repository["api_base"] = policy["api_base"]
    elif provider == "gitea":
        if len(parts) != 2:
            raise ValueError("Gitea repository must have one owner segment.")
        repository.update(
            owner=parts[0],
            repository=parts[1],
            api_base=policy.get("api_base"),
            web_base=origin,
            release_page_size=policy.get("release_page_size", 50),
        )
    elif provider == "generic":
        pass
    else:
        raise ValueError("Unsupported release provider: " + provider)
    return provider, repository


def _allowed_origins(policy):
    values = policy.get("allowed_origins", [])
    if not isinstance(values, list):
        raise ValueError("delivery.release.allowed_origins must be a list.")
    result = []
    for value in values:
        origin = _require_https_origin(value, "allowed origin")
        if origin in result:
            raise ValueError("Allowed origins contain a duplicate.")
        result.append(origin)
    return result


def _artifact_allowed_origins(candidate, reviewed_origins):
    """Add only provider-contract redirects that are immutable and predictable."""
    result = list(reviewed_origins)
    parsed = urllib.parse.urlsplit(candidate.artifact_url)
    path_parts = parsed.path.split("/")
    github_source_archive = (
        candidate.provider == "github"
        and candidate.artifact_kind == "source_zip"
        and candidate.artifact_provenance == "forge_source_archive"
        and parsed.scheme == "https"
        and parsed.hostname == "api.github.com"
        and parsed.port in (None, 443)
        and not parsed.query
        and len(path_parts) == 6
        and path_parts[1] == "repos"
        and path_parts[2]
        and path_parts[3]
        and path_parts[4] == "zipball"
        and path_parts[5] == candidate.commit
    )
    if github_source_archive:
        codeload_origin = "https://codeload.github.com"
        if codeload_origin not in result:
            result.append(codeload_origin)
    return result


def _entry_repository_identity(entry):
    owner, repository = _registry_coordinates(entry)
    identity, _host, _parts = _repository_location(owner, repository)
    return identity


def _validate_previous_common(previous):
    _require_positive_integer(previous["sequence"], "previous sequence")
    _parse_utc(previous["generated_at"], "previous generated_at")
    _parse_utc(previous["expires_at"], "previous expires_at")
    _require_sha256(previous["registry_sha256"], "previous registry_sha256")


def _legacy_migration(artifact):
    eligible = artifact.pop("migration_eligible")
    if type(eligible) is not bool:
        raise ValueError("Legacy migration_eligible must be a boolean.")
    if eligible:
        return {"mode": "automatic", "evidence": "commit_source_archive"}
    evidence = (
        "generic_manifest"
        if artifact.get("provenance") == "generic_manifest"
        else "unverified_asset"
    )
    return {"mode": "manual", "evidence": evidence}


def _validate_legacy_previous(previous, registry, retiring_package_ids=None):
    """Normalize one read-only schema-v1 previous index for v2 generation."""
    retiring_package_ids = set(retiring_package_ids or ())
    required = {
        "schema_version",
        "sequence",
        "generated_at",
        "expires_at",
        "registry_sha256",
        "plugins",
    }
    optional = {"tombstones"}
    if not isinstance(previous, dict) or not required.issubset(previous) or (
        not set(previous).issubset(required | optional)
    ):
        raise ValueError("legacy previous_index has an unsupported shape.")
    if previous["schema_version"] != LEGACY_INDEX_SCHEMA_VERSION:
        raise ValueError("legacy previous_index schema is unsupported.")
    _validate_previous_common(previous)
    plugins = copy.deepcopy(previous["plugins"])
    tombstones = copy.deepcopy(previous.get("tombstones", {}))
    if not isinstance(plugins, dict) or not isinstance(tombstones, dict):
        raise ValueError("previous_index plugin collections must be objects.")
    if set(plugins) & set(tombstones):
        raise ValueError("previous_index overlaps active and tombstoned plugins.")
    unknown_active = set(plugins) - set(registry) - retiring_package_ids
    if unknown_active:
        raise ValueError("Previously accepted plugin disappeared from registry.")
    for key, entry in plugins.items():
        if not isinstance(entry, dict):
            raise ValueError("Previous release entry must be an object.")
        if set(entry) not in (
            LEGACY_RELEASE_ENTRY_FIELDS,
            LEGACY_RELEASE_ENTRY_FIELDS | {"source_revision"},
        ):
            raise ValueError("Legacy release entry has an unsupported shape.")
        if key in registry and entry.get(
            "repository_identity"
        ) != _entry_repository_identity(registry[key]):
            raise ValueError("Previous release repository no longer matches registry.")
        _require_positive_integer(entry.get("revision"), "previous revision")
        _require_string(entry.get("release_id"), "previous release_id")
        if not isinstance(entry.get("supersedes"), list):
            raise ValueError("Previous release lineage must be a list.")
        if not isinstance(entry.get("artifact"), dict):
            raise ValueError("Previous release artifact must be an object.")
        legacy_artifact = copy.deepcopy(entry["artifact"])
        if set(legacy_artifact) != set(ARTIFACT_FIELDS) - {"migration"} | {
            "migration_eligible"
        }:
            raise ValueError("Legacy release artifact has an unsupported shape.")
        legacy_artifact["migration"] = _legacy_migration(legacy_artifact)
        entry = copy.deepcopy(entry)
        entry["artifact"] = _validate_public_artifact(
            legacy_artifact, "legacy release artifact"
        )
        plugins[key] = entry
    for tombstone in tombstones.values():
        if not isinstance(tombstone, dict):
            raise ValueError("Previous tombstone must be an object.")
    return {
        "schema_version": LEGACY_INDEX_SCHEMA_VERSION,
        "sequence": previous["sequence"],
        "generated_at": previous["generated_at"],
        "expires_at": previous["expires_at"],
        "registry_sha256": previous["registry_sha256"],
        "plugins": copy.deepcopy(plugins),
        "tombstones": copy.deepcopy(tombstones),
    }


def _validate_v2_previous(previous, registry, retiring_package_ids=None):
    retiring_package_ids = set(retiring_package_ids or ())
    required = {
        "schema_version",
        "sequence",
        "generated_at",
        "expires_at",
        "registry_sha256",
        "releases",
        "tombstones",
    }
    if not isinstance(previous, dict) or set(previous) != required:
        raise ValueError("previous_index v2 has an unsupported shape.")
    if previous["schema_version"] != INDEX_SCHEMA_VERSION:
        raise ValueError("previous_index v2 schema is unsupported.")
    _validate_previous_common(previous)
    if not isinstance(previous["releases"], list) or not isinstance(
        previous["tombstones"], list
    ):
        raise ValueError("previous_index v2 collections must be arrays.")

    plugins = {}
    for release in previous["releases"]:
        if not isinstance(release, dict):
            raise ValueError("Previous release record must be an object.")
        package_id = _require_string(
            release.get("package_id"), "previous package_id"
        )
        if package_id in plugins:
            raise ValueError("Previous releases contain a duplicate package_id.")
        entry = copy.deepcopy(release)
        entry.pop("package_id")
        if set(entry) not in (
            RELEASE_ENTRY_FIELDS,
            RELEASE_ENTRY_FIELDS | {"source_revision"},
        ):
            raise ValueError("Previous release record has an unsupported shape.")
        if (
            package_id not in registry
            and package_id not in retiring_package_ids
        ):
            raise ValueError("Previously accepted package disappeared from registry.")
        if (
            package_id in registry
            and entry["repository_identity"]
            != _entry_repository_identity(registry[package_id])
        ):
            raise ValueError("Previous release repository no longer matches registry.")
        _require_positive_integer(entry["revision"], "previous revision")
        release_id = _require_string(
            entry["release_id"], "previous release_id"
        )
        if not isinstance(entry["supersedes"], list):
            raise ValueError("Previous release lineage must be a list.")
        lineage = []
        for predecessor in entry["supersedes"]:
            predecessor = _require_string(
                predecessor, "previous superseded release_id"
            )
            if predecessor == release_id or predecessor in lineage:
                raise ValueError("Previous release lineage is invalid.")
            lineage.append(predecessor)
        entry["supersedes"] = lineage
        provider = _require_string(entry["provider"], "previous provider")
        _require_string(
            entry["repository_identity"], "previous repository_identity"
        )
        _require_string(entry["version"], "previous version")
        _require_string(
            entry["tag"], "previous tag", allow_empty=provider == "generic"
        )
        _parse_utc(entry["released_at"], "previous released_at")
        commit = _require_git_object_id(
            entry["commit"], "previous commit", allow_empty=provider == "generic"
        )
        source_revision = entry.get("source_revision", commit)
        _require_string(source_revision, "previous source_revision")
        if provider != "generic" and source_revision != commit:
            raise ValueError("Previous source revision does not match commit.")
        _validate_certified_identity(entry["certified_identity"])
        entry["artifact"] = _validate_public_artifact(
            entry["artifact"], "previous release artifact"
        )
        if (
            entry["artifact"]["migration"]["mode"] == "automatic"
            and not commit
        ):
            raise ValueError("Automatic previous migration requires a commit.")
        plugins[package_id] = entry

    tombstones = {}
    tombstone_fields = {
        "package_id",
        "repository_identity",
        "last_revision",
        "release_id",
        "reason",
        "removed_at",
    }
    for tombstone in previous["tombstones"]:
        if not isinstance(tombstone, dict) or set(tombstone) != tombstone_fields:
            raise ValueError("Previous tombstone record has an unsupported shape.")
        package_id = _require_string(
            tombstone["package_id"], "previous tombstone package_id"
        )
        if package_id in tombstones or package_id in plugins:
            raise ValueError("Previous package state is duplicated.")
        normalized = copy.deepcopy(tombstone)
        normalized.pop("package_id")
        if (
            package_id in registry
            and normalized["repository_identity"]
            != _entry_repository_identity(registry[package_id])
        ):
            raise ValueError("Previous tombstone repository no longer matches registry.")
        _require_positive_integer(
            normalized["last_revision"], "previous tombstone revision"
        )
        _require_string(
            normalized["release_id"], "previous tombstone release_id"
        )
        _require_string(normalized["reason"], "previous tombstone reason")
        _parse_utc(normalized["removed_at"], "previous tombstone removed_at")
        tombstones[package_id] = normalized
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "sequence": previous["sequence"],
        "generated_at": previous["generated_at"],
        "expires_at": previous["expires_at"],
        "registry_sha256": previous["registry_sha256"],
        "plugins": plugins,
        "tombstones": tombstones,
    }


def _validate_previous(previous, registry, retiring_package_ids=None):
    if previous is None:
        return None
    if not isinstance(previous, dict):
        raise ValueError("previous_index must be an object.")
    if previous.get("schema_version") == LEGACY_INDEX_SCHEMA_VERSION:
        return _validate_legacy_previous(
            previous,
            registry,
            retiring_package_ids,
        )
    if previous.get("schema_version") == INDEX_SCHEMA_VERSION:
        return _validate_v2_previous(
            previous,
            registry,
            retiring_package_ids,
        )
    raise ValueError("previous_index schema is unsupported.")


def _validate_tombstone_requests(requests, previous, registry):
    if requests is None:
        return {}
    if not isinstance(requests, dict):
        raise ValueError("tombstone_requests must be an object.")
    previous_plugins = previous["plugins"] if previous else {}
    result = {}
    for plugin_key, request in requests.items():
        if plugin_key not in previous_plugins:
            raise ValueError("Tombstone requires a known prior accepted release.")
        if not isinstance(request, dict) or set(request) != {"reason"}:
            raise ValueError("Tombstone request must contain only a reason.")
        reason = _require_string(request["reason"], "tombstone reason")
        result[plugin_key] = {"reason": reason}
    return result


def _artifact_document(candidate, certified):
    return {
        "kind": candidate.artifact_kind,
        "provenance": candidate.artifact_provenance,
        "migration": {
            "mode": candidate.migration_mode,
            "evidence": candidate.migration_evidence,
        },
        "url": candidate.artifact_url,
        "sha256": certified.sha256,
        "size": certified.size,
        "tree_sha256": certified.tree_sha256,
        "root_prefix": certified.root_prefix,
        "source_path": candidate.source_path,
        "certified_identity": {
            "domoticz_key": certified.domoticz_key,
            "plugin_py_sha256": certified.plugin_py_sha256,
        },
    }


def _candidate_entry(candidate, artifact, revision, supersedes):
    artifact = copy.deepcopy(artifact)
    certified_identity = artifact.pop("certified_identity")
    entry = {
        "revision": revision,
        "release_id": candidate.release_id,
        "supersedes": list(supersedes),
        "provider": candidate.provider,
        "repository_identity": candidate.repository_identity,
        "version": candidate.version,
        "tag": candidate.tag,
        "released_at": candidate.released_at,
        "commit": candidate.commit,
        "certified_identity": certified_identity,
        "artifact": artifact,
    }
    if candidate.provider == "generic" or candidate.source_revision != candidate.commit:
        entry["source_revision"] = candidate.source_revision
    return entry


def _public_artifact(artifact):
    if not isinstance(artifact, dict) or not set(ARTIFACT_FIELDS).issubset(
        artifact
    ):
        raise ValueError("Certified artifact has an incomplete shape.")
    return {field: copy.deepcopy(artifact[field]) for field in ARTIFACT_FIELDS}


def _mutation_report(reason, **fields):
    report = {"status": "quarantined_mutation", "reason": reason}
    report.update(fields)
    return report


def _release_records(plugins):
    records = []
    for package_id in sorted(plugins):
        entry = copy.deepcopy(plugins[package_id])
        if set(entry) not in (
            RELEASE_ENTRY_FIELDS,
            RELEASE_ENTRY_FIELDS | {"source_revision"},
        ):
            raise ValueError("Release record has an unsupported shape.")
        identity = _validate_certified_identity(
            entry.get("certified_identity")
        )
        artifact = _validate_public_artifact(
            entry.get("artifact"), "release artifact"
        )
        entry["certified_identity"] = identity
        entry["artifact"] = artifact
        records.append({"package_id": package_id, **entry})
    return records


def _tombstone_records(tombstones):
    return [
        {"package_id": package_id, **copy.deepcopy(tombstones[package_id])}
        for package_id in sorted(tombstones)
    ]


def _compare_with_previous(candidate, artifact, previous_entry):
    if candidate.release_id in previous_entry.get("supersedes", []):
        return previous_entry, _mutation_report("release_lineage_regression")

    if candidate.release_id != previous_entry["release_id"]:
        if (
            candidate.provider == "generic"
            and candidate.source_revision == previous_entry.get("source_revision")
        ):
            return previous_entry, _mutation_report("source_revision_reused")
        lineage = list(previous_entry.get("supersedes", []))
        if previous_entry["release_id"] not in lineage:
            lineage.append(previous_entry["release_id"])
        entry = _candidate_entry(
            candidate,
            artifact,
            previous_entry["revision"] + 1,
            lineage,
        )
        return entry, {
            "status": "certified_update",
            "revision_changed": True,
        }

    previous_commit = previous_entry.get("commit", "")
    previous_source_revision = previous_entry.get(
        "source_revision", previous_commit
    )
    if (
        candidate.commit != previous_commit
        or candidate.source_revision != previous_source_revision
    ):
        return previous_entry, _mutation_report(
            "release_identity_changed_commit"
        )

    stable_fields = {
        "provider": candidate.provider,
        "repository_identity": candidate.repository_identity,
        "version": candidate.version,
        "tag": candidate.tag,
        "released_at": candidate.released_at,
    }
    if any(previous_entry.get(name) != value for name, value in stable_fields.items()):
        return previous_entry, _mutation_report(
            "release_identity_metadata_changed"
        )
    current_artifact = _public_artifact(artifact)
    previous_artifact = previous_entry.get("artifact", {})
    for field, value in (
        ("kind", candidate.artifact_kind),
        ("provenance", candidate.artifact_provenance),
        ("migration", current_artifact["migration"]),
        ("source_path", candidate.source_path),
    ):
        if previous_artifact.get(field) != value:
            return previous_entry, _mutation_report(
                "release_artifact_policy_changed"
            )

    if current_artifact["tree_sha256"] != previous_artifact.get("tree_sha256"):
        reason = (
            "source_tree_changed"
            if candidate.artifact_provenance == "forge_source_archive"
            else "artifact_bytes_changed"
        )
        return previous_entry, _mutation_report(
            reason,
            observed_tree_sha256=current_artifact["tree_sha256"],
            accepted_tree_sha256=previous_artifact.get("tree_sha256"),
        )

    transport_fields = ("url", "sha256", "size", "root_prefix")
    transport_changed = any(
        current_artifact.get(field) != previous_artifact.get(field)
        for field in transport_fields
    )
    if candidate.artifact_provenance != "forge_source_archive" and transport_changed:
        return previous_entry, _mutation_report("artifact_bytes_changed")
    if not transport_changed:
        refreshed = copy.deepcopy(previous_entry)
        refreshed["certified_identity"] = copy.deepcopy(
            artifact["certified_identity"]
        )
        refreshed["artifact"] = current_artifact
        return refreshed, {
            "status": "unchanged",
            "revision_changed": False,
        }

    refreshed = copy.deepcopy(previous_entry)
    refreshed["certified_identity"] = copy.deepcopy(
        artifact["certified_identity"]
    )
    refreshed["artifact"] = current_artifact
    return refreshed, {
        "status": "transport_refreshed",
        "revision_changed": False,
    }


class ReleaseIndexGenerator:
    """Resolve, certify, and report normalized release candidates."""

    def __init__(
        self,
        providers=None,
        http_client=None,
        *,
        provider_transport=None,
        clock=None,
        validity_seconds=DEFAULT_VALIDITY_SECONDS,
        cache=None,
    ):
        self.providers = providers if providers is not None else default_provider_adapters()
        if not isinstance(self.providers, dict) or not self.providers:
            raise ValueError("providers must be a non-empty mapping.")
        uses_default_http = http_client is None
        self.http_client = (
            default_secure_http_client() if uses_default_http else http_client
        )
        if not callable(getattr(self.http_client, "download", None)):
            raise ValueError("http_client must provide download().")
        if provider_transport is None:
            if uses_default_http:
                provider_transport = default_secure_json_transport()
            elif callable(getattr(self.http_client, "get_json", None)):
                provider_transport = self.http_client
            else:
                provider_transport = SecureJsonTransport(self.http_client)
        if not callable(getattr(provider_transport, "get_json", None)):
            raise ValueError("provider_transport must provide get_json().")
        self.provider_transport = provider_transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.validity_seconds = _require_positive_integer(
            validity_seconds, "validity_seconds"
        )
        if cache is not None and not isinstance(cache, ReleaseCandidateCache):
            raise ValueError("cache must be a ReleaseCandidateCache.")
        self.cache = cache

    def _resolve_candidate(
        self,
        plugin_key,
        registry_digest,
        provider_name,
        provider,
        repository,
        policy,
        now,
    ):
        key_document = {
            "plugin_key": plugin_key,
            "registry_sha256": registry_digest,
            "provider": provider_name,
            "repository": repository,
            "policy": policy,
        }
        key = _cache_key("candidate", key_document)
        if self.cache is not None:
            payload = self.cache.get(key)
            if payload is not None:
                candidate = _validate_candidate(
                    payload, provider_name, repository["repository_identity"], now
                )
                return candidate, True

        try:
            outcome = provider.resolve(
                copy.deepcopy(repository),
                copy.deepcopy(policy),
                self.provider_transport,
                now=now,
            )
        except Exception as error:
            reason = getattr(error, "reason", None)
            if reason == "no_release":
                return None, False
            if reason == "rate_limited":
                raise TransientProviderError(str(error)) from error
            raise
        if outcome is None:
            return None, False
        snapshot = _candidate_snapshot(outcome)
        candidate = _validate_candidate(
            snapshot, provider_name, repository["repository_identity"], now
        )
        if self.cache is not None:
            self.cache.put(key, snapshot)
        return candidate, False

    def _certify_artifact(
        self,
        plugin_key,
        registry_digest,
        candidate,
        allowed_origins,
    ):
        allowed_origins = _artifact_allowed_origins(candidate, allowed_origins)
        candidate_document = {
            field: getattr(candidate, field) for field in CANDIDATE_FIELDS
        }
        key = _cache_key(
            "artifact",
            {
                "plugin_key": plugin_key,
                "registry_sha256": registry_digest,
                "candidate": candidate_document,
                "allowed_origins": list(allowed_origins),
            },
        )
        if self.cache is not None:
            payload = self.cache.get(key)
            if payload is not None:
                return _validate_cached_artifact(payload, candidate), True

        expected_sha256 = candidate.provider_sha256 or None
        expected_size = candidate.artifact_size
        downloaded = self.http_client.download(
            candidate.artifact_url,
            headers=dict(PUBLIC_ARTIFACT_HEADERS),
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            allowed_origins=list(allowed_origins),
        )
        data = getattr(downloaded, "data", None)
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("HTTP client did not return artifact bytes.")
        data = bytes(data)
        actual_sha256 = hashlib.sha256(data).hexdigest()
        actual_size = len(data)
        if getattr(downloaded, "size", actual_size) != actual_size:
            raise ValueError("HTTP client returned an inconsistent artifact length.")
        if getattr(downloaded, "sha256", actual_sha256) != actual_sha256:
            raise ValueError("HTTP client returned an inconsistent artifact digest.")
        if expected_size is not None and actual_size != expected_size:
            raise ValueError("Downloaded artifact length does not match provider metadata.")
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ValueError("Downloaded artifact digest does not match provider metadata.")

        certified = _certify_zip_bytes(data, candidate)
        artifact = _artifact_document(candidate, certified)
        if self.cache is not None:
            self.cache.put(key, artifact)
        return artifact, False

    def _certify_migration_evidence(
        self,
        plugin_key,
        registry_digest,
        candidate,
        artifact,
        allowed_origins,
    ):
        """Strengthen an attached asset only when its commit tree is equal."""
        if not (
            candidate.artifact_kind == "asset_zip"
            and candidate.migration_mode == "manual"
            and candidate.migration_evidence == "unverified_asset"
            and candidate.commit
            and candidate.source_archive_url
        ):
            return artifact, True

        source_document = {
            field: getattr(candidate, field) for field in CANDIDATE_FIELDS
        }
        source_document.update(
            artifact_kind="source_zip",
            artifact_provenance="forge_source_archive",
            artifact_url=candidate.source_archive_url,
            artifact_size=None,
            provider_sha256="",
            migration_mode="automatic",
            migration_evidence="commit_source_archive",
        )
        source_candidate = _validate_candidate(
            source_document,
            candidate.provider,
            candidate.repository_identity,
            _parse_utc(candidate.released_at, "candidate.released_at"),
        )
        try:
            source_artifact, cache_hit = self._certify_artifact(
                plugin_key,
                registry_digest,
                source_candidate,
                allowed_origins,
            )
        except Exception:
            # Source comparison strengthens migration authorization only. A
            # verified release asset remains installable in manual mode when
            # its provider source archive is temporarily unavailable.
            return artifact, False
        strengthened = copy.deepcopy(artifact)
        if source_artifact["tree_sha256"] == artifact["tree_sha256"]:
            strengthened["migration"] = {
                "mode": "automatic",
                "evidence": "source_equivalent_asset",
            }
        return strengthened, cache_hit

    def _certify_legacy_previous_entry(
        self,
        package_id,
        registry_digest,
        previous_entry,
    ):
        """Re-certify one v1 accepted artifact before emitting it as v2."""
        if "certified_identity" in previous_entry:
            return copy.deepcopy(previous_entry)
        artifact = previous_entry["artifact"]
        migration = _validate_migration(
            artifact["migration"], "legacy normalized migration"
        )
        candidate = SimpleNamespace(
            provider=previous_entry["provider"],
            repository_identity=previous_entry["repository_identity"],
            release_id=previous_entry["release_id"],
            version=previous_entry["version"],
            tag=previous_entry["tag"],
            released_at=previous_entry["released_at"],
            source_revision=previous_entry.get(
                "source_revision", previous_entry.get("commit", "")
            ),
            commit=previous_entry.get("commit", ""),
            artifact_kind=artifact["kind"],
            artifact_provenance=artifact["provenance"],
            artifact_url=artifact["url"],
            source_archive_url=(
                artifact["url"]
                if artifact["provenance"] == "forge_source_archive"
                else ""
            ),
            artifact_size=artifact["size"],
            provider_sha256=artifact["sha256"],
            source_path=artifact["source_path"],
            migration_mode=migration["mode"],
            migration_evidence=migration["evidence"],
        )
        certified, _cache_hit = self._certify_artifact(
            package_id,
            registry_digest,
            candidate,
            [],
        )
        public_artifact = _public_artifact(certified)
        for field in ARTIFACT_FIELDS:
            if public_artifact[field] != artifact[field]:
                raise ValueError(
                    "Legacy accepted artifact no longer matches its certification."
                )
        upgraded = copy.deepcopy(previous_entry)
        upgraded["certified_identity"] = copy.deepcopy(
            certified["certified_identity"]
        )
        upgraded["artifact"] = public_artifact
        return upgraded

    def generate(
        self,
        *,
        registry_bytes,
        previous_index=None,
        tombstone_requests=None,
        report_only=True,
    ):
        if type(report_only) is not bool:
            raise ValueError("report_only must be a boolean.")
        registry_contents = bytes(registry_bytes)
        registry_document = _strict_json_object(
            registry_contents, "registry"
        )
        registry = _registry_records(registry_document)
        retiring_package_ids = (
            set(tombstone_requests)
            if isinstance(tombstone_requests, dict)
            else set()
        )
        previous = _validate_previous(
            previous_index,
            registry,
            retiring_package_ids,
        )
        tombstone_requests = _validate_tombstone_requests(
            tombstone_requests, previous, registry
        )
        now = _now_utc(self.clock)
        if previous is not None and now < _parse_utc(
            previous["generated_at"], "previous generated_at"
        ):
            raise ValueError("Generation clock regressed behind previous index.")
        sequence = previous["sequence"] + 1 if previous is not None else 1
        registry_digest = hashlib.sha256(registry_contents).hexdigest()
        previous_plugins = previous["plugins"] if previous else {}
        previous_tombstones = previous["tombstones"] if previous else {}
        plugins = {}
        tombstones = copy.deepcopy(previous_tombstones)
        reports = {}
        report_providers = {}

        for plugin_key in sorted(set(registry) | set(tombstone_requests)):
            plugin_key = _require_string(plugin_key, "registry plugin key")
            prior = previous_plugins.get(plugin_key)
            prior_tombstone = previous_tombstones.get(plugin_key)
            if plugin_key in tombstone_requests:
                report_providers[plugin_key] = prior["provider"]
                tombstone = {
                    "repository_identity": prior["repository_identity"],
                    "last_revision": prior["revision"],
                    "release_id": prior["release_id"],
                    "reason": tombstone_requests[plugin_key]["reason"],
                    "removed_at": _format_utc(now),
                }
                tombstones[plugin_key] = tombstone
                reports[plugin_key] = {"status": "tombstoned"}
                continue

            if prior is not None and "certified_identity" not in prior:
                try:
                    prior = self._certify_legacy_previous_entry(
                        plugin_key,
                        registry_digest,
                        prior,
                    )
                    previous_plugins[plugin_key] = copy.deepcopy(prior)
                except Exception as error:
                    reports[plugin_key] = {
                        "status": "certification_failed",
                        "detail": (
                            "Legacy release cutover failed: " + str(error)
                        ),
                        "cache_hit": False,
                    }
                    continue

            try:
                owner, repository_name = _registry_coordinates(registry[plugin_key])
                identity, host, parts = _repository_location(owner, repository_name)
                delivery = _effective_delivery(registry[plugin_key], host)
                preferred = delivery.get("preferred")
                policy = delivery.get("release")
                if preferred == "git" or policy is None:
                    if prior is not None:
                        report_providers[plugin_key] = prior["provider"]
                        plugins[plugin_key] = copy.deepcopy(prior)
                        reports[plugin_key] = {"status": "retained_policy_disabled"}
                    elif prior_tombstone is not None:
                        reports[plugin_key] = {
                            "status": "retained_tombstone_policy_disabled"
                        }
                    else:
                        reports[plugin_key] = {"status": "policy_disabled"}
                    continue
                provider_name, repository = _repository_for_provider(
                    identity, host, parts, policy
                )
                report_providers[plugin_key] = provider_name
                provider = self.providers.get(provider_name)
                if provider is None:
                    raise ValueError(
                        "No adapter is configured for provider " + provider_name + "."
                    )
                origins = _allowed_origins(policy)
            except Exception as error:
                if prior is not None:
                    plugins[plugin_key] = copy.deepcopy(prior)
                reports[plugin_key] = {
                    "status": (
                        "retained_tombstone_configuration_failure"
                        if prior_tombstone is not None
                        else "configuration_failed"
                    ),
                    "detail": str(error),
                }
                continue

            try:
                selected, candidate_cache_hit = self._resolve_candidate(
                    plugin_key,
                    registry_digest,
                    provider_name,
                    provider,
                    repository,
                    policy,
                    now,
                )
            except TransientProviderError as error:
                if prior is not None:
                    plugins[plugin_key] = copy.deepcopy(prior)
                reports[plugin_key] = {
                    "status": (
                        "retained_provider_failure"
                        if prior is not None or prior_tombstone is not None
                        else "provider_failed"
                    ),
                    "transient": True,
                    "detail": str(error),
                }
                continue
            except Exception as error:
                if prior is not None:
                    plugins[plugin_key] = copy.deepcopy(prior)
                reports[plugin_key] = {
                    "status": (
                        "retained_provider_failure"
                        if prior is not None or prior_tombstone is not None
                        else "provider_failed"
                    ),
                    "transient": False,
                    "detail": str(error),
                }
                continue

            if selected is None:
                if prior is not None:
                    plugins[plugin_key] = copy.deepcopy(prior)
                    reports[plugin_key] = {"status": "retained_no_candidate"}
                elif prior_tombstone is not None:
                    reports[plugin_key] = {
                        "status": "retained_tombstone_no_candidate"
                    }
                else:
                    reports[plugin_key] = {"status": "no_release"}
                continue

            if prior_tombstone is not None:
                if selected.release_id == prior_tombstone["release_id"]:
                    reports[plugin_key] = {
                        "status": "retained_tombstone",
                        "cache_hit": candidate_cache_hit,
                    }
                    continue
                if (
                    selected.repository_identity
                    != prior_tombstone["repository_identity"]
                ):
                    reports[plugin_key] = {
                        "status": "certification_failed",
                        "detail": (
                            "Release repository does not match the tombstoned "
                            "package."
                        ),
                        "cache_hit": candidate_cache_hit,
                    }
                    continue

            if prior is not None and (
                selected.release_id in prior.get("supersedes", [])
            ):
                plugins[plugin_key] = copy.deepcopy(prior)
                report = _mutation_report("release_lineage_regression")
                report["cache_hit"] = candidate_cache_hit
                reports[plugin_key] = report
                continue

            try:
                artifact, artifact_cache_hit = self._certify_artifact(
                    plugin_key,
                    registry_digest,
                    selected,
                    origins,
                )
                artifact, evidence_cache_hit = (
                    self._certify_migration_evidence(
                        plugin_key,
                        registry_digest,
                        selected,
                        artifact,
                        origins,
                    )
                )
                artifact_cache_hit = (
                    artifact_cache_hit and evidence_cache_hit
                )
                expected_domoticz_key = (
                    registry[plugin_key].get("domoticz_key")
                    if isinstance(registry[plugin_key], dict)
                    else None
                )
                if expected_domoticz_key is not None and (
                    artifact["certified_identity"]["domoticz_key"]
                    != expected_domoticz_key
                ):
                    raise ValueError(
                        "Certified Domoticz key does not match the package registry."
                    )
            except Exception as error:
                if prior is not None:
                    plugins[plugin_key] = copy.deepcopy(prior)
                reports[plugin_key] = {
                    "status": "certification_failed",
                    "detail": str(error),
                    "cache_hit": False,
                }
                continue

            cache_hit = candidate_cache_hit and artifact_cache_hit
            if prior_tombstone is not None:
                entry = _candidate_entry(
                    selected,
                    artifact,
                    prior_tombstone["last_revision"] + 1,
                    [prior_tombstone["release_id"]],
                )
                tombstones.pop(plugin_key, None)
                report = {
                    "status": "reactivated",
                    "revision_changed": True,
                }
            elif prior is None:
                entry = _candidate_entry(selected, artifact, 1, [])
                report = {
                    "status": "certified_new",
                    "revision_changed": True,
                }
            else:
                entry, report = _compare_with_previous(
                    selected, artifact, prior
                )
            plugins[plugin_key] = copy.deepcopy(entry)
            report["cache_hit"] = cache_hit
            reports[plugin_key] = report

        document = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "sequence": sequence,
            "generated_at": _format_utc(now),
            "expires_at": _format_utc(
                now + timedelta(seconds=self.validity_seconds)
            ),
            "registry_sha256": registry_digest,
            "releases": _release_records(plugins),
            "tombstones": _tombstone_records(tombstones),
        }
        for plugin_key, provider_name in report_providers.items():
            if plugin_key in reports:
                reports[plugin_key]["provider"] = provider_name
        summary = {}
        provider_summary = {}
        for plugin_report in reports.values():
            state = plugin_report["status"]
            summary[state] = summary.get(state, 0) + 1
            provider_name = plugin_report.get("provider")
            if provider_name is not None:
                states = provider_summary.setdefault(provider_name, {})
                states[state] = states.get(state, 0) + 1
        report = {
            "schema_version": 1,
            "sequence": sequence,
            "generated_at": _format_utc(now),
            "registry_sha256": registry_digest,
            "report_only": report_only,
            "plugins": dict(sorted(reports.items())),
            "providers": {
                provider_name: dict(sorted(states.items()))
                for provider_name, states in sorted(provider_summary.items())
            },
            "summary": dict(sorted(summary.items())),
        }
        return ReleaseIndexGenerationResult(
            document=document,
            index_bytes=_canonical_json_bytes(document),
            report=report,
            report_bytes=_canonical_json_bytes(report),
            wrote_index=False,
        )

    def run(
        self,
        *,
        registry_path,
        index_path,
        report_only=True,
        report_path=None,
        tombstone_requests=None,
    ):
        registry_file = Path(registry_path)
        index_file = Path(index_path)
        registry_contents = registry_file.read_bytes()
        previous = None
        if index_file.is_file():
            previous = _strict_json_object(index_file.read_bytes(), "previous index")
        result = self.generate(
            registry_bytes=registry_contents,
            previous_index=previous,
            tombstone_requests=tombstone_requests,
            report_only=report_only,
        )
        wrote_index = False
        if not report_only:
            _atomic_write(index_file, result.index_bytes)
            wrote_index = True
        if report_path is not None:
            _atomic_write(report_path, result.report_bytes)
        return ReleaseIndexGenerationResult(
            document=result.document,
            index_bytes=result.index_bytes,
            report=result.report,
            report_bytes=result.report_bytes,
            wrote_index=wrote_index,
        )


def _parse_tombstones(path):
    if path is None:
        return None
    return _strict_json_object(Path(path).read_bytes(), "tombstone requests")


def _argument_parser():
    parser = argparse.ArgumentParser(
        description="Resolve and certify plugin releases into a normalized index."
    )
    parser.add_argument("--registry", default="registry.json")
    parser.add_argument("--index", default="release_index.json")
    parser.add_argument("--report-output")
    parser.add_argument("--cache")
    parser.add_argument(
        "--cache-ttl-seconds",
        type=int,
        default=DEFAULT_CACHE_TTL_SECONDS,
    )
    parser.add_argument(
        "--validity-seconds",
        type=int,
        default=DEFAULT_VALIDITY_SECONDS,
    )
    parser.add_argument("--tombstone-requests")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--report-only",
        action="store_true",
        dest="report_only",
        help="Do not replace the tracked release index (default).",
    )
    mode.add_argument(
        "--update",
        "--write-index",
        action="store_false",
        dest="report_only",
        help="Atomically replace the index after successful generation (--write-index is an alias).",
    )
    parser.set_defaults(report_only=True)
    return parser


def main(argv=None):
    arguments = _argument_parser().parse_args(argv)
    try:
        cache = (
            ReleaseCandidateCache(
                arguments.cache,
                ttl_seconds=arguments.cache_ttl_seconds,
            )
            if arguments.cache
            else None
        )
        generator = ReleaseIndexGenerator(
            validity_seconds=arguments.validity_seconds,
            cache=cache,
        )
        result = generator.run(
            registry_path=arguments.registry,
            index_path=arguments.index,
            report_only=arguments.report_only,
            report_path=arguments.report_output,
            tombstone_requests=_parse_tombstones(arguments.tombstone_requests),
        )
    except Exception as error:
        print("release index generation failed: " + str(error), file=sys.stderr)
        return 2
    sys.stdout.buffer.write(result.report_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
