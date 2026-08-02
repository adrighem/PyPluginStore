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

"""Provider-neutral contracts shared by CI and runtime release discovery."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import re
import unicodedata
import urllib.parse


def _capture_loaded_source_fingerprint():
    """Fingerprint the provider source loaded into the manager runtime."""
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


GIT_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)
ARTIFACT_KINDS = {"asset_zip", "generic_zip", "source_zip"}
ARTIFACT_PROVENANCE = {
    "attached_asset",
    "forge_release_asset",
    "forge_source_archive",
    "generic_manifest",
    "release_asset",
}
MIGRATION_MODES = {"automatic", "blocked", "manual"}
MIGRATION_EVIDENCE = {
    "commit_source_archive",
    "generic_manifest",
    "unverified_asset",
}
WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *("com" + str(number) for number in range(1, 10)),
    *("lpt" + str(number) for number in range(1, 10)),
}
GITHUB_API_VERSION = "2026-03-10"
GITHUB_TAG_MAX_DEREFERENCES = 8
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "PyPluginStore-Release-Scanner",
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
}
GITLAB_API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PyPluginStore-Release-Scanner",
}
FORGEJO_API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PyPluginStore-Release-Scanner",
}
CODEBERG_HOST = "codeberg.org"
CODEBERG_API_BASE = "https://codeberg.org/api/v1"
CODEBERG_WEB_BASE = "https://codeberg.org"
FORGEJO_RELEASE_PAGE_SIZE = 50
FORGEJO_RELEASE_MAX_PAGES = 20
FORGEJO_TAG_MAX_DEREFERENCES = 8
GITEA_API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PyPluginStore-Release-Scanner",
}
GITEA_RELEASE_PAGE_SIZE = 50
GITEA_RELEASE_MAX_PAGES = 20
GITEA_TAG_MAX_DEREFERENCES = 8
GENERIC_MANIFEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "PyPluginStore-Release-Scanner",
}
GENERIC_MANIFEST_REQUIRED_KEYS = {
    "schema_version",
    "release_id",
    "version",
    "released_at",
    "url",
    "sha256",
    "size",
    "source_revision",
}
GENERIC_MANIFEST_OPTIONAL_KEYS = {"commit", "source_path"}
MAX_CONFIGURED_RELEASE_PAGE_SIZE = 100


class NoReleaseError(LookupError):
    """Signal that a reachable provider has no reviewed stable release."""

    reason = "no_release"


def _require_string(value, label, allow_empty=False):
    """Return one canonical string without coercing untrusted values."""
    if not isinstance(value, str):
        raise ValueError(label + " must be a string.")
    if value != value.strip() or CONTROL_CHARACTER_PATTERN.search(value):
        raise ValueError(label + " must be a canonical string.")
    if not value and not allow_empty:
        raise ValueError(label + " must not be empty.")
    return value


def _require_repository_identity(value):
    """Validate a lowercase host/path identity without assuming one forge."""
    value = _require_string(value, "repository_identity")
    if (
        value != value.lower()
        or unicodedata.normalize("NFC", value) != value
        or any(character.isspace() for character in value)
        or "://" in value
        or "\\" in value
        or value.endswith("/")
    ):
        raise ValueError("repository_identity must be canonical.")

    try:
        parsed = urllib.parse.urlsplit("//" + value)
        parsed.port
    except ValueError as error:
        raise ValueError("repository_identity has an invalid host.") from error
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.hostname in (".", "..")
        or parsed.hostname.startswith(".")
        or parsed.hostname.endswith(".")
    ):
        raise ValueError("repository_identity is unsafe.")

    parts = parsed.path.lstrip("/").split("/")
    if (
        not parts
        or any(not part or part in (".", "..") for part in parts)
        or any("%" in part for part in parts)
        or parts[-1].endswith(".git")
    ):
        raise ValueError("repository_identity has an unsafe path.")
    canonical = parsed.netloc.lower() + "/" + "/".join(parts)
    if canonical != value:
        raise ValueError("repository_identity must be normalized.")
    return value


def _parse_utc_timestamp(value, label="released_at"):
    """Parse a provider UTC timestamp with optional fractional seconds."""
    value = _require_string(value, label)
    if not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError(label + " must be an ISO 8601 UTC timestamp.")
    timestamp_format = (
        "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        parsed = datetime.strptime(value, timestamp_format)
    except ValueError as error:
        raise ValueError(label + " must be an ISO 8601 UTC timestamp.") from error
    return parsed.replace(tzinfo=timezone.utc)


def _canonical_utc_timestamp(value, label="released_at"):
    """Normalize a valid provider timestamp to index second precision."""
    return _parse_utc_timestamp(value, label).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_git_object_id(value, allow_empty=False):
    """Validate a full lowercase SHA-1 or SHA-256 Git object ID."""
    value = _require_string(value, "commit", allow_empty=allow_empty)
    if value or not allow_empty:
        if not GIT_OBJECT_ID_PATTERN.fullmatch(value):
            raise ValueError("commit must be a full lowercase Git object ID.")
    return value


def _require_sha256(value, allow_empty=False):
    """Validate a lowercase SHA-256 digest, optionally before download."""
    value = _require_string(
        value, "provider_sha256", allow_empty=allow_empty
    )
    if value or not allow_empty:
        if not SHA256_PATTERN.fullmatch(value):
            raise ValueError(
                "provider_sha256 must be a lowercase SHA-256 digest."
            )
    return value


def _require_https_url(value, label="artifact_url"):
    """Validate one credential-free HTTPS URL."""
    value = _require_string(value, label)
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port
    except ValueError as error:
        raise ValueError(label + " is not a valid URL.") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path
    ):
        raise ValueError(
            label + " must be a credential-free HTTPS URL."
        )
    return value


def _require_source_path(value):
    """Validate a normalized cross-platform relative source path."""
    value = _require_string(value, "source_path")
    if value == ".":
        return value
    if (
        unicodedata.normalize("NFC", value) != value
        or value.startswith(("/", "\\"))
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ValueError("source_path must be a canonical relative POSIX path.")

    parts = value.split("/")
    if any(not part or part in (".", "..") for part in parts):
        raise ValueError("source_path must be a normalized relative path.")
    for part in parts:
        if part.endswith((".", " ")) or ":" in part:
            raise ValueError("source_path is not portable.")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise ValueError("source_path contains a reserved path name.")
    return value


@dataclass(frozen=True)
class ReleaseCandidate:
    """One validated provider-neutral release discovery result."""

    provider: str
    repository_identity: str
    release_id: str
    version: str
    tag: str
    released_at: str
    source_revision: str
    commit: str
    artifact_kind: str
    artifact_provenance: str
    artifact_url: str
    source_archive_url: str
    artifact_size: object
    provider_sha256: str
    source_path: str
    migration_mode: str
    migration_evidence: str

    def __post_init__(self):
        provider = _require_string(self.provider, "provider")
        if not PROVIDER_PATTERN.fullmatch(provider):
            raise ValueError("provider must be a canonical provider identifier.")
        _require_repository_identity(self.repository_identity)
        _require_string(self.release_id, "release_id")
        _require_string(self.version, "version")
        tag = _require_string(self.tag, "tag", allow_empty=True)
        if provider != "generic" and not tag:
            raise ValueError("Forge candidates require a release tag.")
        if _canonical_utc_timestamp(self.released_at) != self.released_at:
            raise ValueError("released_at must use canonical UTC second precision.")
        source_revision = _require_string(
            self.source_revision, "source_revision"
        )
        commit = _require_git_object_id(self.commit, allow_empty=True)
        if provider != "generic" and commit and source_revision != commit:
            raise ValueError("A Git source revision must equal its commit ID.")

        if self.artifact_kind not in ARTIFACT_KINDS:
            raise ValueError("artifact_kind is unsupported.")
        if self.artifact_provenance not in ARTIFACT_PROVENANCE:
            raise ValueError("artifact_provenance is unsupported.")
        _require_https_url(self.artifact_url)
        source_archive_url = _require_string(
            self.source_archive_url,
            "source_archive_url",
            allow_empty=True,
        )
        if source_archive_url:
            _require_https_url(source_archive_url, "source_archive_url")
        if self.artifact_size is not None and (
            type(self.artifact_size) is not int or self.artifact_size <= 0
        ):
            raise ValueError("artifact_size must be a positive integer or null.")
        _require_sha256(self.provider_sha256, allow_empty=True)
        _require_source_path(self.source_path)
        migration_mode = _require_string(
            self.migration_mode, "migration_mode"
        )
        migration_evidence = _require_string(
            self.migration_evidence, "migration_evidence"
        )
        if migration_mode not in MIGRATION_MODES:
            raise ValueError("migration_mode is unsupported.")
        if migration_evidence not in MIGRATION_EVIDENCE:
            raise ValueError("migration_evidence is unsupported.")
        if migration_mode == "automatic" and not commit:
            raise ValueError(
                "Automatic migration requires a full Git commit ID."
            )
        if migration_mode == "automatic" and not source_archive_url:
            raise ValueError(
                "Automatic migration requires an immutable source archive URL."
            )
        if provider != "generic" and not source_archive_url:
            raise ValueError(
                "Forge candidates require an immutable source archive URL."
            )
        if migration_mode == "automatic" and (
            migration_evidence != "commit_source_archive"
        ):
            raise ValueError(
                "Automatic provider migration requires a commit source archive."
            )
        if migration_evidence == "commit_source_archive" and (
            migration_mode != "automatic"
        ):
            raise ValueError(
                "Commit source archives must use automatic migration mode."
            )


class ReleaseProviderAdapter(ABC):
    """Common resolve contract implemented independently by each provider."""

    @abstractmethod
    def resolve(self, repository, policy, transport, *, now=None):
        """Return one validated ReleaseCandidate for repository and policy."""
        raise NotImplementedError


def tag_matches_pattern(tag, tag_pattern):
    """Return whether a tag fully matches the reviewed stable-tag policy."""
    tag = _require_string(tag, "tag")
    tag_pattern = _require_string(tag_pattern, "tag_pattern")
    try:
        pattern = re.compile(tag_pattern)
    except re.error as error:
        raise ValueError("tag_pattern is not a valid regular expression.") from error
    return pattern.fullmatch(tag) is not None


def select_latest_stable_release(
    releases,
    tag_pattern,
    *,
    released_at_key="published_at",
    excluded=None,
    now=None,
):
    """Select the newest eligible full-match tag by parsed release time."""
    if not isinstance(releases, (list, tuple)):
        raise ValueError("Provider releases must be a list.")
    if excluded is not None and not callable(excluded):
        raise ValueError("excluded must be callable.")
    if now is not None:
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("now must be a timezone-aware datetime.")
        now = now.astimezone(timezone.utc)

    candidates = []
    for position, release in enumerate(releases):
        if not isinstance(release, dict):
            raise ValueError("Each provider release must be an object.")
        skip = False
        for flag in ("draft", "prerelease", "upcoming_release"):
            if flag in release:
                if type(release[flag]) is not bool:
                    raise ValueError(flag + " must be a boolean.")
                skip = skip or release[flag]
        if skip or (excluded is not None and excluded(release)):
            continue

        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag_matches_pattern(tag, tag_pattern):
            continue
        released_at = _parse_utc_timestamp(
            release.get(released_at_key), released_at_key
        )
        if now is not None and released_at > now:
            continue
        candidates.append((released_at, -position, release))

    if not candidates:
        raise NoReleaseError(
            "No stable release matches the reviewed tag policy."
        )
    return max(candidates, key=lambda candidate: candidate[:2])[2]


def select_asset(
    assets, *, asset_name="", asset_pattern="", name_key="name"
):
    """Select exactly one reviewed asset name or full-match pattern."""
    if not isinstance(assets, (list, tuple)):
        raise ValueError("Release assets must be a list.")
    asset_name = _require_string(asset_name, "asset_name", allow_empty=True)
    asset_pattern = _require_string(
        asset_pattern, "asset_pattern", allow_empty=True
    )
    if bool(asset_name) == bool(asset_pattern):
        raise ValueError("Configure exactly one asset selector.")
    pattern = None
    if asset_pattern:
        try:
            pattern = re.compile(asset_pattern)
        except re.error as error:
            raise ValueError("asset_pattern is not a valid expression.") from error
    name_key = _require_string(name_key, "name_key")

    matches = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("Each release asset must be an object.")
        name = asset.get(name_key)
        if not isinstance(name, str):
            raise ValueError("Each release asset must have a string name.")
        if name == asset_name or (
            pattern is not None and pattern.fullmatch(name) is not None
        ):
            matches.append(asset)
    if not matches:
        raise ValueError("Configured release asset was not published.")
    if len(matches) != 1:
        raise ValueError("Configured release asset name is ambiguous.")
    return matches[0]


def select_exact_asset(assets, asset_name, *, name_key="name"):
    """Select exactly one case-sensitive asset name without fuzzy fallback."""
    return select_asset(
        assets,
        asset_name=asset_name,
        name_key=name_key,
    )


def _require_https_base_url(value, label):
    """Validate and normalize a configured provider API or web base URL."""
    value = _require_string(value, label)
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port
    except ValueError as error:
        raise ValueError(label + " is not a valid URL.") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(label + " must be a credential-free HTTPS URL.")
    return value.rstrip("/")


def _require_release_page_size(repository, provider, default=None):
    """Return the reviewed host page cap used to detect pagination end."""
    if "release_page_size" not in repository and default is None:
        raise ValueError(
            provider + " requires an explicit release_page_size capability."
        )
    value = repository.get("release_page_size", default)
    if (
        type(value) is not int
        or value <= 0
        or value > MAX_CONFIGURED_RELEASE_PAGE_SIZE
    ):
        raise ValueError(
            provider + " release_page_size must be between 1 and "
            + str(MAX_CONFIGURED_RELEASE_PAGE_SIZE)
            + "."
        )
    return value


def _require_github_repository(repository):
    """Return validated GitHub repository coordinates and configured bases."""
    if not isinstance(repository, dict):
        raise ValueError("GitHub repository configuration must be an object.")
    identity = _require_repository_identity(
        repository.get("repository_identity")
    )
    owner = _require_string(repository.get("owner"), "owner")
    name = _require_string(repository.get("repository"), "repository")
    for value, label in ((owner, "owner"), (name, "repository")):
        if (
            value != value.lower()
            or value in (".", "..")
            or "/" in value
            or "\\" in value
            or urllib.parse.quote(value, safe="") != value
        ):
            raise ValueError(label + " must be a canonical GitHub path segment.")
    if name.endswith(".git"):
        raise ValueError("repository must not include a .git suffix.")

    identity_parts = identity.split("/")
    if identity_parts[-2:] != [owner, name]:
        raise ValueError(
            "GitHub repository coordinates do not match repository_identity."
        )
    api_base = _require_https_base_url(
        repository.get("api_base"), "api_base"
    )
    web_base = _require_https_base_url(
        repository.get("web_base"), "web_base"
    )
    identity_host = identity.split("/", 1)[0].split(":", 1)[0]
    if urllib.parse.urlsplit(web_base).hostname != identity_host:
        raise ValueError(
            "GitHub web_base does not match repository_identity."
        )
    return identity, owner, name, api_base, web_base


def _transport_get_json(transport, url, headers):
    """Request JSON with diagnostics when the transport accepts headers."""
    get_json = getattr(transport, "get_json", None)
    if not callable(get_json):
        raise ValueError("Release transport must provide get_json().")

    supports_headers = True
    try:
        parameters = inspect.signature(get_json).parameters.values()
        supports_headers = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or parameter.name == "headers"
            for parameter in parameters
        )
    except (TypeError, ValueError):
        pass
    if supports_headers:
        return get_json(url, headers=dict(headers))
    return get_json(url)


def _release_document_get_json(transport, url, headers):
    """Fetch a release document, mapping an absent endpoint to no release."""
    try:
        return _transport_get_json(transport, url, headers)
    except Exception as error:
        if getattr(error, "status", None) == 404:
            raise NoReleaseError(
                "The provider has no published release collection."
            ) from error
        raise


def _version_from_tag(tag):
    """Derive display version text without using it for release ordering."""
    tag = _require_string(tag, "tag")
    if tag[:2].lower() == "v.":
        version = tag[2:]
    elif tag[:1].lower() == "v":
        version = tag[1:]
    else:
        version = tag
    return _require_string(version, "version")


def _github_provider_digest(value):
    """Parse GitHub's optional algorithm-prefixed asset digest."""
    if value in (None, ""):
        return ""
    value = _require_string(value, "GitHub asset digest")
    prefix = "sha256:"
    if not value.startswith(prefix):
        raise ValueError("GitHub asset digest must use SHA-256.")
    digest = value[len(prefix):]
    _require_sha256(digest)
    return digest


def _github_release_is_excluded(release):
    """Fail closed unless GitHub supplied explicit stable-release flags."""
    for flag in ("draft", "prerelease"):
        if flag not in release or type(release[flag]) is not bool:
            raise ValueError("GitHub release " + flag + " must be a boolean.")
    return release["draft"] or release["prerelease"]


class GitHubReleaseAdapter(ReleaseProviderAdapter):
    """Resolve stable GitHub releases to immutable provider-neutral candidates."""

    provider = "github"

    def _get_json(self, transport, url):
        return _transport_get_json(transport, url, GITHUB_API_HEADERS)

    def _resolve_commit(self, transport, api_repository_url, tag):
        """Peel a lightweight or nested annotated tag to a bounded commit ID."""
        encoded_tag = urllib.parse.quote(tag, safe="")
        ref_url = api_repository_url + "/git/ref/tags/" + encoded_tag
        ref_document = self._get_json(transport, ref_url)
        if not isinstance(ref_document, dict):
            raise ValueError("GitHub tag reference must be an object.")
        target = ref_document.get("object")
        visited_tags = set()
        dereferences = 0

        while True:
            if not isinstance(target, dict):
                raise ValueError("GitHub tag target must be an object.")
            target_type = target.get("type")
            target_sha = target.get("sha")
            if target_type == "commit":
                return _require_git_object_id(target_sha)
            if target_type != "tag":
                raise ValueError("GitHub tag must resolve to a commit or tag object.")
            tag_sha = _require_git_object_id(target_sha)
            if tag_sha in visited_tags:
                raise ValueError("GitHub annotated tag chain contains a cycle.")
            if dereferences >= GITHUB_TAG_MAX_DEREFERENCES:
                raise ValueError("GitHub annotated tag chain exceeds the limit.")
            visited_tags.add(tag_sha)
            dereferences += 1

            tag_url = api_repository_url + "/git/tags/" + tag_sha
            tag_document = self._get_json(transport, tag_url)
            if not isinstance(tag_document, dict):
                raise ValueError("GitHub annotated tag must be an object.")
            document_sha = _require_git_object_id(tag_document.get("sha"))
            if document_sha != tag_sha:
                raise ValueError("GitHub annotated tag response changed identity.")
            target = tag_document.get("object")

    def _select_artifact(self, release, policy, api_repository_url, commit):
        """Return candidate artifact fields for source or configured asset ZIP."""
        artifact_kind = policy.get("artifact")
        source_archive_url = api_repository_url + "/zipball/" + commit
        if artifact_kind == "source_zip":
            return {
                "artifact_kind": "source_zip",
                "artifact_provenance": "forge_source_archive",
                "artifact_url": source_archive_url,
                "source_archive_url": source_archive_url,
                "artifact_size": None,
                "provider_sha256": "",
                "migration_mode": "automatic",
                "migration_evidence": "commit_source_archive",
            }
        if artifact_kind != "asset_zip":
            raise ValueError("GitHub policy artifact must be source_zip or asset_zip.")

        asset = select_asset(
            release.get("assets"),
            asset_name=policy.get("asset_name", ""),
            asset_pattern=policy.get("asset_pattern", ""),
        )
        asset_name = asset.get("name")
        if not asset_name.lower().endswith(".zip"):
            raise ValueError("Configured GitHub release asset must be a ZIP file.")
        if asset.get("state", "uploaded") != "uploaded":
            raise ValueError("Configured GitHub release asset is not uploaded.")
        asset_size = asset.get("size")
        if type(asset_size) is not int or asset_size <= 0:
            raise ValueError("GitHub release asset size must be positive.")
        return {
            "artifact_kind": "asset_zip",
            "artifact_provenance": "attached_asset",
            "artifact_url": asset.get("browser_download_url"),
            "source_archive_url": source_archive_url,
            "artifact_size": asset_size,
            "provider_sha256": _github_provider_digest(asset.get("digest")),
            "migration_mode": "manual",
            "migration_evidence": "unverified_asset",
        }

    def resolve(self, repository, policy, transport, *, now=None):
        """Resolve the newest reviewed stable GitHub release."""
        identity, owner, name, api_base, _web_base = (
            _require_github_repository(repository)
        )
        if not isinstance(policy, dict):
            raise ValueError("GitHub release policy must be an object.")
        if policy.get("channel") != "stable":
            raise ValueError("GitHub adapter currently supports only stable releases.")

        tag_pattern = policy.get("tag_pattern")
        api_repository_url = (
            api_base
            + "/repos/"
            + urllib.parse.quote(owner, safe="")
            + "/"
            + urllib.parse.quote(name, safe="")
        )
        releases_url = api_repository_url + "/releases?per_page=100"
        releases = self._get_json(transport, releases_url)
        release = select_latest_stable_release(
            releases,
            tag_pattern,
            released_at_key="published_at",
            excluded=_github_release_is_excluded,
            now=now,
        )
        tag = release.get("tag_name")
        commit = self._resolve_commit(
            transport, api_repository_url, tag
        )
        artifact = self._select_artifact(
            release, policy, api_repository_url, commit
        )

        return ReleaseCandidate(
            provider=self.provider,
            repository_identity=identity,
            release_id="github:" + owner + "/" + name + ":" + tag,
            version=_version_from_tag(tag),
            tag=tag,
            released_at=_canonical_utc_timestamp(
                release.get("published_at"), "published_at"
            ),
            source_revision=commit,
            commit=commit,
            source_path=policy.get("source_path", "."),
            **artifact,
        )


def _require_gitlab_repository(repository):
    """Return validated GitLab project identity, path, and provider bases."""
    if not isinstance(repository, dict):
        raise ValueError("GitLab repository configuration must be an object.")
    identity = _require_repository_identity(
        repository.get("repository_identity")
    )
    project_path = _require_string(
        repository.get("project_path"), "project_path"
    )
    if (
        project_path != project_path.lower()
        or unicodedata.normalize("NFC", project_path) != project_path
        or project_path.startswith("/")
        or project_path.endswith("/")
        or "\\" in project_path
        or "%" in project_path
    ):
        raise ValueError("project_path must be canonical and unencoded.")
    path_parts = project_path.split("/")
    if (
        len(path_parts) < 2
        or any(part in ("", ".", "..") for part in path_parts)
        or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", part)
            for part in path_parts
        )
        or path_parts[-1].endswith(".git")
    ):
        raise ValueError("project_path contains an unsafe GitLab path segment.")
    if "/".join(identity.split("/")[1:]) != project_path:
        raise ValueError(
            "GitLab project_path does not match repository_identity."
        )

    api_base = _require_https_base_url(
        repository.get("api_base"), "api_base"
    )
    web_base = _require_https_base_url(
        repository.get("web_base"), "web_base"
    )
    identity_host = identity.split("/", 1)[0].split(":", 1)[0]
    if urllib.parse.urlsplit(web_base).hostname != identity_host:
        raise ValueError(
            "GitLab web_base does not match repository_identity."
        )
    return identity, project_path, api_base, web_base


def _gitlab_release_is_excluded(release):
    """Reject an explicitly upcoming GitLab release."""
    upcoming = release.get("upcoming_release", False)
    if type(upcoming) is not bool:
        raise ValueError("GitLab upcoming_release must be a boolean.")
    return upcoming


class GitLabReleaseAdapter(ReleaseProviderAdapter):
    """Resolve stable GitLab releases without coupling to other forges."""

    provider = "gitlab"

    def _get_json(self, transport, url):
        return _transport_get_json(transport, url, GITLAB_API_HEADERS)

    def _resolve_commit(self, transport, api_project_url, tag):
        """Resolve the selected GitLab tag to one full immutable commit ID."""
        tag_url = (
            api_project_url
            + "/repository/tags/"
            + urllib.parse.quote(tag, safe="")
        )
        tag_document = self._get_json(transport, tag_url)
        if not isinstance(tag_document, dict):
            raise ValueError("GitLab tag response must be an object.")
        if tag_document.get("name") != tag:
            raise ValueError("GitLab tag response changed identity.")
        target = _require_git_object_id(tag_document.get("target"))
        commit_document = tag_document.get("commit")
        if not isinstance(commit_document, dict):
            raise ValueError("GitLab tag commit must be an object.")
        commit = _require_git_object_id(commit_document.get("id"))
        if target != commit:
            raise ValueError("GitLab tag target does not match its commit.")
        return commit

    def _select_artifact(self, release, policy, api_project_url, commit):
        """Return source or reviewed direct-asset candidate fields."""
        artifact_kind = policy.get("artifact")
        source_archive_url = (
            api_project_url + "/repository/archive.zip?sha=" + commit
        )
        if artifact_kind == "source_zip":
            return {
                "artifact_kind": "source_zip",
                "artifact_provenance": "forge_source_archive",
                "artifact_url": source_archive_url,
                "source_archive_url": source_archive_url,
                "artifact_size": None,
                "provider_sha256": "",
                "migration_mode": "automatic",
                "migration_evidence": "commit_source_archive",
            }
        if artifact_kind != "asset_zip":
            raise ValueError("GitLab policy artifact must be source_zip or asset_zip.")

        assets_document = release.get("assets")
        if not isinstance(assets_document, dict):
            raise ValueError("GitLab release assets must be an object.")
        asset = select_asset(
            assets_document.get("links"),
            asset_name=policy.get("asset_name", ""),
            asset_pattern=policy.get("asset_pattern", ""),
        )
        asset_name = asset.get("name")
        if not asset_name.lower().endswith(".zip"):
            raise ValueError("Configured GitLab release asset must be a ZIP file.")
        asset_size = asset.get("size")
        if asset_size is not None and (
            type(asset_size) is not int or asset_size <= 0
        ):
            raise ValueError("GitLab release asset size must be positive or null.")
        return {
            "artifact_kind": "asset_zip",
            "artifact_provenance": "attached_asset",
            "artifact_url": (
                asset.get("direct_asset_url") or asset.get("url")
            ),
            "source_archive_url": source_archive_url,
            "artifact_size": asset_size,
            "provider_sha256": "",
            "migration_mode": "manual",
            "migration_evidence": "unverified_asset",
        }

    def resolve(self, repository, policy, transport, *, now=None):
        """Resolve the newest released GitLab candidate matching policy."""
        identity, project_path, api_base, _web_base = (
            _require_gitlab_repository(repository)
        )
        if not isinstance(policy, dict):
            raise ValueError("GitLab release policy must be an object.")
        if policy.get("channel") != "stable":
            raise ValueError("GitLab adapter currently supports only stable releases.")
        tag_pattern = _require_string(
            policy.get("tag_pattern"), "tag_pattern"
        )
        try:
            re.compile(tag_pattern)
        except re.error as error:
            raise ValueError(
                "tag_pattern is not a valid regular expression."
            ) from error
        if now is None:
            now = datetime.now(timezone.utc)

        encoded_project = urllib.parse.quote(project_path, safe="")
        api_project_url = api_base + "/projects/" + encoded_project
        releases_url = (
            api_project_url
            + "/releases?order_by=released_at&sort=desc&per_page=100"
        )
        releases = self._get_json(transport, releases_url)
        release = select_latest_stable_release(
            releases,
            tag_pattern,
            released_at_key="released_at",
            excluded=_gitlab_release_is_excluded,
            now=now,
        )
        tag = release.get("tag_name")
        commit = self._resolve_commit(
            transport, api_project_url, tag
        )
        artifact = self._select_artifact(
            release, policy, api_project_url, commit
        )

        return ReleaseCandidate(
            provider=self.provider,
            repository_identity=identity,
            release_id="gitlab:" + project_path + ":" + tag,
            version=_version_from_tag(tag),
            tag=tag,
            released_at=_canonical_utc_timestamp(
                release.get("released_at"), "released_at"
            ),
            source_revision=commit,
            commit=commit,
            source_path=policy.get("source_path", "."),
            **artifact,
        )


def _require_forgejo_repository(repository):
    """Validate Codeberg defaults or explicit custom Forgejo coordinates."""
    if not isinstance(repository, dict):
        raise ValueError("Forgejo repository configuration must be an object.")
    identity = _require_repository_identity(
        repository.get("repository_identity")
    )
    identity_parts = identity.split("/")
    if len(identity_parts) != 3:
        raise ValueError("Forgejo identity must contain one owner and repository.")
    identity_host, identity_owner, identity_name = identity_parts

    owner = _require_string(repository.get("owner"), "owner")
    name = _require_string(repository.get("repository"), "repository")
    for value, label in ((owner, "owner"), (name, "repository")):
        if (
            value != value.lower()
            or value in (".", "..")
            or "/" in value
            or "\\" in value
            or urllib.parse.quote(value, safe="") != value
        ):
            raise ValueError(label + " must be a canonical Forgejo path segment.")
    if name.endswith(".git"):
        raise ValueError("repository must not include a .git suffix.")
    if [identity_owner, identity_name] != [owner, name]:
        raise ValueError(
            "Forgejo coordinates do not match repository_identity."
        )

    if identity_host == CODEBERG_HOST:
        api_value = repository.get("api_base", CODEBERG_API_BASE)
        web_value = repository.get("web_base", CODEBERG_WEB_BASE)
        release_page_size = _require_release_page_size(
            repository, "Codeberg", FORGEJO_RELEASE_PAGE_SIZE
        )
    else:
        if (
            "api_base" not in repository
            or "web_base" not in repository
            or "release_page_size" not in repository
        ):
            raise ValueError(
                "Custom Forgejo hosts require explicit api_base, web_base, "
                "and release_page_size capabilities."
            )
        api_value = repository.get("api_base")
        web_value = repository.get("web_base")
        release_page_size = _require_release_page_size(
            repository, "Forgejo"
        )
    api_base = _require_https_base_url(api_value, "api_base")
    web_base = _require_https_base_url(web_value, "web_base")
    if urllib.parse.urlsplit(web_base).hostname != identity_host.split(":", 1)[0]:
        raise ValueError(
            "Forgejo web_base does not match repository_identity."
        )
    return (
        identity,
        identity_host,
        owner,
        name,
        api_base,
        web_base,
        release_page_size,
    )


def _forgejo_release_is_excluded(release):
    """Require explicit Forgejo draft and prerelease classifications."""
    for flag in ("draft", "prerelease"):
        if flag not in release or type(release[flag]) is not bool:
            raise ValueError("Forgejo release " + flag + " must be a boolean.")
    return release["draft"] or release["prerelease"]


class ForgejoReleaseAdapter(ReleaseProviderAdapter):
    """Resolve Codeberg and explicitly configured Forgejo releases."""

    provider = "forgejo"

    def _get_json(self, transport, url):
        return _transport_get_json(transport, url, FORGEJO_API_HEADERS)

    def _get_releases_json(self, transport, url):
        return _release_document_get_json(
            transport, url, FORGEJO_API_HEADERS
        )

    def _list_releases(self, transport, api_repository_url, page_size):
        """Read bounded release-list pages instead of created-at /latest."""
        releases = []
        for page in range(1, FORGEJO_RELEASE_MAX_PAGES + 1):
            releases_url = (
                api_repository_url
                + "/releases?page="
                + str(page)
                + "&limit="
                + str(page_size)
            )
            page_document = (
                self._get_releases_json(transport, releases_url)
                if page == 1
                else self._get_json(transport, releases_url)
            )
            if not isinstance(page_document, list):
                raise ValueError("Forgejo releases response must be a list.")
            releases.extend(page_document)
            if len(page_document) < page_size:
                return releases
        raise ValueError("Forgejo release pagination exceeds the safety limit.")

    def _resolve_commit(self, transport, api_repository_url, tag):
        """Resolve one exact prefix-query ref and peel annotated tags safely."""
        expected_ref = "refs/tags/" + tag
        encoded_ref = urllib.parse.quote("tags/" + tag, safe="")
        refs_url = api_repository_url + "/git/refs/" + encoded_ref
        refs_document = self._get_json(transport, refs_url)
        if isinstance(refs_document, dict):
            refs = [refs_document]
        elif isinstance(refs_document, list):
            refs = refs_document
        else:
            raise ValueError("Forgejo tag references must be an object or list.")
        if any(not isinstance(reference, dict) for reference in refs):
            raise ValueError("Each Forgejo tag reference must be an object.")
        exact_refs = [
            reference
            for reference in refs
            if reference.get("ref") == expected_ref
        ]
        if len(exact_refs) != 1:
            raise ValueError(
                "Forgejo tag reference is missing or ambiguous."
            )

        target = exact_refs[0].get("object")
        visited_tags = set()
        dereferences = 0
        while True:
            if not isinstance(target, dict):
                raise ValueError("Forgejo tag target must be an object.")
            target_type = target.get("type")
            target_sha = target.get("sha")
            if target_type == "commit":
                return _require_git_object_id(target_sha)
            if target_type != "tag":
                raise ValueError(
                    "Forgejo tag must resolve to a commit or tag object."
                )
            tag_sha = _require_git_object_id(target_sha)
            if tag_sha in visited_tags:
                raise ValueError("Forgejo annotated tag chain contains a cycle.")
            if dereferences >= FORGEJO_TAG_MAX_DEREFERENCES:
                raise ValueError("Forgejo annotated tag chain exceeds the limit.")
            visited_tags.add(tag_sha)
            dereferences += 1

            tag_url = api_repository_url + "/git/tags/" + tag_sha
            tag_document = self._get_json(transport, tag_url)
            if not isinstance(tag_document, dict):
                raise ValueError("Forgejo annotated tag must be an object.")
            document_sha = _require_git_object_id(tag_document.get("sha"))
            if document_sha != tag_sha:
                raise ValueError(
                    "Forgejo annotated tag response changed identity."
                )
            target = tag_document.get("object")

    def _select_artifact(self, release, policy, api_repository_url, commit):
        """Return commit source archive or reviewed attached ZIP fields."""
        artifact_kind = policy.get("artifact")
        source_archive_url = (
            api_repository_url + "/archive/" + commit + ".zip"
        )
        if artifact_kind == "source_zip":
            return {
                "artifact_kind": "source_zip",
                "artifact_provenance": "forge_source_archive",
                "artifact_url": source_archive_url,
                "source_archive_url": source_archive_url,
                "artifact_size": None,
                "provider_sha256": "",
                "migration_mode": "automatic",
                "migration_evidence": "commit_source_archive",
            }
        if artifact_kind != "asset_zip":
            raise ValueError("Forgejo policy artifact must be source_zip or asset_zip.")

        asset = select_asset(
            release.get("assets"),
            asset_name=policy.get("asset_name", ""),
            asset_pattern=policy.get("asset_pattern", ""),
        )
        if asset.get("type", "attachment") != "attachment":
            raise ValueError(
                "External Forgejo release assets require separate review."
            )
        asset_name = asset.get("name")
        if not asset_name.lower().endswith(".zip"):
            raise ValueError("Configured Forgejo release asset must be a ZIP file.")
        asset_size = asset.get("size")
        if type(asset_size) is not int or asset_size <= 0:
            raise ValueError("Forgejo release asset size must be positive.")
        return {
            "artifact_kind": "asset_zip",
            "artifact_provenance": "attached_asset",
            "artifact_url": asset.get("browser_download_url"),
            "source_archive_url": source_archive_url,
            "artifact_size": asset_size,
            "provider_sha256": "",
            "migration_mode": "manual",
            "migration_evidence": "unverified_asset",
        }

    def resolve(self, repository, policy, transport, *, now=None):
        """Resolve the newest stable release from a Forgejo release list."""
        identity, _host, owner, name, api_base, _web_base, page_size = (
            _require_forgejo_repository(repository)
        )
        if not isinstance(policy, dict):
            raise ValueError("Forgejo release policy must be an object.")
        if policy.get("channel") != "stable":
            raise ValueError("Forgejo adapter currently supports only stable releases.")
        tag_pattern = _require_string(
            policy.get("tag_pattern"), "tag_pattern"
        )
        try:
            re.compile(tag_pattern)
        except re.error as error:
            raise ValueError(
                "tag_pattern is not a valid regular expression."
            ) from error
        if now is None:
            now = datetime.now(timezone.utc)

        api_repository_url = (
            api_base
            + "/repos/"
            + urllib.parse.quote(owner, safe="")
            + "/"
            + urllib.parse.quote(name, safe="")
        )
        releases = self._list_releases(
            transport, api_repository_url, page_size
        )
        release = select_latest_stable_release(
            releases,
            tag_pattern,
            released_at_key="published_at",
            excluded=_forgejo_release_is_excluded,
            now=now,
        )
        tag = release.get("tag_name")
        commit = self._resolve_commit(
            transport, api_repository_url, tag
        )
        artifact = self._select_artifact(
            release, policy, api_repository_url, commit
        )

        return ReleaseCandidate(
            provider=self.provider,
            repository_identity=identity,
            release_id="forgejo:" + identity + ":" + tag,
            version=_version_from_tag(tag),
            tag=tag,
            released_at=_canonical_utc_timestamp(
                release.get("published_at"), "published_at"
            ),
            source_revision=commit,
            commit=commit,
            source_path=policy.get("source_path", "."),
            **artifact,
        )


def _require_gitea_repository(repository):
    """Validate explicitly configured Gitea repository coordinates."""
    if not isinstance(repository, dict):
        raise ValueError("Gitea repository configuration must be an object.")
    identity = _require_repository_identity(
        repository.get("repository_identity")
    )
    identity_parts = identity.split("/")
    if len(identity_parts) != 3:
        raise ValueError("Gitea identity must contain one owner and repository.")
    identity_host, identity_owner, identity_name = identity_parts

    owner = _require_string(repository.get("owner"), "owner")
    name = _require_string(repository.get("repository"), "repository")
    for value, label in ((owner, "owner"), (name, "repository")):
        if (
            value != value.lower()
            or value in (".", "..")
            or "/" in value
            or "\\" in value
            or urllib.parse.quote(value, safe="") != value
        ):
            raise ValueError(label + " must be a canonical Gitea path segment.")
    if name.endswith(".git"):
        raise ValueError("repository must not include a .git suffix.")
    if [identity_owner, identity_name] != [owner, name]:
        raise ValueError(
            "Gitea coordinates do not match repository_identity."
        )
    if (
        "api_base" not in repository
        or "web_base" not in repository
        or "release_page_size" not in repository
    ):
        raise ValueError(
            "Gitea requires explicit api_base, web_base, and "
            "release_page_size capabilities."
        )
    api_base = _require_https_base_url(
        repository.get("api_base"), "api_base"
    )
    web_base = _require_https_base_url(
        repository.get("web_base"), "web_base"
    )
    if urllib.parse.urlsplit(web_base).hostname != identity_host.split(":", 1)[0]:
        raise ValueError("Gitea web_base does not match repository_identity.")
    release_page_size = _require_release_page_size(repository, "Gitea")
    return identity, owner, name, api_base, web_base, release_page_size


def _gitea_release_is_excluded(release):
    """Require explicit Gitea draft and prerelease classifications."""
    for flag in ("draft", "prerelease"):
        if flag not in release or type(release[flag]) is not bool:
            raise ValueError("Gitea release " + flag + " must be a boolean.")
    return release["draft"] or release["prerelease"]


class GiteaReleaseAdapter(ReleaseProviderAdapter):
    """Resolve stable Gitea releases through an independent API contract."""

    provider = "gitea"

    def _get_json(self, transport, url):
        return _transport_get_json(transport, url, GITEA_API_HEADERS)

    def _get_releases_json(self, transport, url):
        return _release_document_get_json(
            transport, url, GITEA_API_HEADERS
        )

    def _list_releases(self, transport, api_repository_url, page_size):
        """Read a bounded sequence of Gitea release-list pages."""
        releases = []
        for page in range(1, GITEA_RELEASE_MAX_PAGES + 1):
            releases_url = (
                api_repository_url
                + "/releases?page="
                + str(page)
                + "&limit="
                + str(page_size)
            )
            page_document = (
                self._get_releases_json(transport, releases_url)
                if page == 1
                else self._get_json(transport, releases_url)
            )
            if not isinstance(page_document, list):
                raise ValueError("Gitea releases response must be a list.")
            releases.extend(page_document)
            if len(page_document) < page_size:
                return releases
        raise ValueError("Gitea release pagination exceeds the safety limit.")

    def _resolve_commit(self, transport, api_repository_url, tag):
        """Resolve one exact Gitea tag ref and peel annotated tag objects."""
        expected_ref = "refs/tags/" + tag
        encoded_ref = urllib.parse.quote("tags/" + tag, safe="")
        refs_url = api_repository_url + "/git/refs/" + encoded_ref
        refs_document = self._get_json(transport, refs_url)
        if isinstance(refs_document, dict):
            refs = [refs_document]
        elif isinstance(refs_document, list):
            refs = refs_document
        else:
            raise ValueError("Gitea tag references must be an object or list.")
        if any(not isinstance(reference, dict) for reference in refs):
            raise ValueError("Each Gitea tag reference must be an object.")
        exact_refs = [
            reference
            for reference in refs
            if reference.get("ref") == expected_ref
        ]
        if len(exact_refs) != 1:
            raise ValueError("Gitea tag reference is missing or ambiguous.")

        target = exact_refs[0].get("object")
        visited_tags = set()
        dereferences = 0
        while True:
            if not isinstance(target, dict):
                raise ValueError("Gitea tag target must be an object.")
            target_type = target.get("type")
            target_sha = target.get("sha")
            if target_type == "commit":
                return _require_git_object_id(target_sha)
            if target_type != "tag":
                raise ValueError(
                    "Gitea tag must resolve to a commit or tag object."
                )
            tag_sha = _require_git_object_id(target_sha)
            if tag_sha in visited_tags:
                raise ValueError("Gitea annotated tag chain contains a cycle.")
            if dereferences >= GITEA_TAG_MAX_DEREFERENCES:
                raise ValueError("Gitea annotated tag chain exceeds the limit.")
            visited_tags.add(tag_sha)
            dereferences += 1

            tag_url = api_repository_url + "/git/tags/" + tag_sha
            tag_document = self._get_json(transport, tag_url)
            if not isinstance(tag_document, dict):
                raise ValueError("Gitea annotated tag must be an object.")
            document_sha = _require_git_object_id(tag_document.get("sha"))
            if document_sha != tag_sha:
                raise ValueError(
                    "Gitea annotated tag response changed identity."
                )
            target = tag_document.get("object")

    def _select_artifact(self, release, policy, api_repository_url, commit):
        """Return Gitea commit archive or reviewed attached ZIP fields."""
        artifact_kind = policy.get("artifact")
        source_archive_url = (
            api_repository_url + "/archive/" + commit + ".zip"
        )
        if artifact_kind == "source_zip":
            return {
                "artifact_kind": "source_zip",
                "artifact_provenance": "forge_source_archive",
                "artifact_url": source_archive_url,
                "source_archive_url": source_archive_url,
                "artifact_size": None,
                "provider_sha256": "",
                "migration_mode": "automatic",
                "migration_evidence": "commit_source_archive",
            }
        if artifact_kind != "asset_zip":
            raise ValueError("Gitea policy artifact must be source_zip or asset_zip.")

        asset = select_asset(
            release.get("assets"),
            asset_name=policy.get("asset_name", ""),
            asset_pattern=policy.get("asset_pattern", ""),
        )
        if "type" in asset:
            raise ValueError("Gitea release asset type is not supported.")
        asset_name = asset.get("name")
        if not asset_name.lower().endswith(".zip"):
            raise ValueError("Configured Gitea release asset must be a ZIP file.")
        asset_size = asset.get("size")
        if type(asset_size) is not int or asset_size <= 0:
            raise ValueError("Gitea release asset size must be positive.")
        return {
            "artifact_kind": "asset_zip",
            "artifact_provenance": "attached_asset",
            "artifact_url": asset.get("browser_download_url"),
            "source_archive_url": source_archive_url,
            "artifact_size": asset_size,
            "provider_sha256": "",
            "migration_mode": "manual",
            "migration_evidence": "unverified_asset",
        }

    def resolve(self, repository, policy, transport, *, now=None):
        """Resolve the newest stable Gitea release matching reviewed policy."""
        identity, owner, name, api_base, _web_base, page_size = (
            _require_gitea_repository(repository)
        )
        if not isinstance(policy, dict):
            raise ValueError("Gitea release policy must be an object.")
        if policy.get("channel") != "stable":
            raise ValueError("Gitea adapter currently supports only stable releases.")
        tag_pattern = _require_string(
            policy.get("tag_pattern"), "tag_pattern"
        )
        try:
            re.compile(tag_pattern)
        except re.error as error:
            raise ValueError(
                "tag_pattern is not a valid regular expression."
            ) from error
        if now is None:
            now = datetime.now(timezone.utc)

        api_repository_url = (
            api_base
            + "/repos/"
            + urllib.parse.quote(owner, safe="")
            + "/"
            + urllib.parse.quote(name, safe="")
        )
        releases = self._list_releases(
            transport, api_repository_url, page_size
        )
        release = select_latest_stable_release(
            releases,
            tag_pattern,
            released_at_key="published_at",
            excluded=_gitea_release_is_excluded,
            now=now,
        )
        tag = release.get("tag_name")
        commit = self._resolve_commit(
            transport, api_repository_url, tag
        )
        artifact = self._select_artifact(
            release, policy, api_repository_url, commit
        )

        return ReleaseCandidate(
            provider=self.provider,
            repository_identity=identity,
            release_id="gitea:" + identity + ":" + tag,
            version=_version_from_tag(tag),
            tag=tag,
            released_at=_canonical_utc_timestamp(
                release.get("published_at"), "published_at"
            ),
            source_revision=commit,
            commit=commit,
            source_path=policy.get("source_path", "."),
            **artifact,
        )


class GenericManifestAdapter(ReleaseProviderAdapter):
    """Normalize one immutable release described by a reviewed HTTPS manifest."""

    provider = "generic"

    def _get_json(self, transport, url):
        return _transport_get_json(
            transport, url, GENERIC_MANIFEST_HEADERS
        )

    def resolve(self, repository, policy, transport, *, now=None):
        """Return a provider-neutral candidate from a strict v1 manifest."""
        if not isinstance(repository, dict):
            raise ValueError("Generic repository configuration must be an object.")
        identity = _require_repository_identity(
            repository.get("repository_identity")
        )
        if not isinstance(policy, dict):
            raise ValueError("Generic release policy must be an object.")
        if policy.get("channel") != "stable":
            raise ValueError(
                "Generic manifest adapter currently supports only stable releases."
            )

        manifest_url = _require_https_url(policy.get("manifest_url"))
        manifest_host = urllib.parse.urlsplit(manifest_url).netloc.lower()
        identity_host = identity.split("/", 1)[0]
        if manifest_host != identity_host:
            raise ValueError(
                "Generic manifest host does not match repository_identity."
            )

        manifest = self._get_json(transport, manifest_url)
        if not isinstance(manifest, dict):
            raise ValueError("Generic release manifest must be an object.")
        manifest_keys = set(manifest)
        if not GENERIC_MANIFEST_REQUIRED_KEYS.issubset(manifest_keys):
            raise ValueError("Generic release manifest is missing required fields.")
        if not manifest_keys.issubset(
            GENERIC_MANIFEST_REQUIRED_KEYS | GENERIC_MANIFEST_OPTIONAL_KEYS
        ):
            raise ValueError("Generic release manifest contains unknown fields.")
        if type(manifest.get("schema_version")) is not int:
            raise ValueError("Generic manifest schema_version must be an integer.")
        if manifest["schema_version"] != 1:
            raise ValueError("Generic manifest schema_version is unsupported.")

        release_id = _require_string(manifest.get("release_id"), "release_id")
        release_prefix = "generic:" + identity + ":"
        if (
            not release_id.startswith(release_prefix)
            or not release_id[len(release_prefix):]
        ):
            raise ValueError(
                "Generic manifest release_id does not match repository_identity."
            )
        version = _require_string(manifest.get("version"), "version")
        released_at = _require_string(
            manifest.get("released_at"), "released_at"
        )
        released_at_value = _parse_utc_timestamp(released_at)
        if now is not None:
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise ValueError("now must be a timezone-aware datetime.")
            if released_at_value > now.astimezone(timezone.utc):
                raise ValueError("Generic manifest release is dated in the future.")

        artifact_url = _require_https_url(manifest.get("url"))
        artifact_size = manifest.get("size")
        if type(artifact_size) is not int or artifact_size <= 0:
            raise ValueError("Generic manifest size must be a positive integer.")
        provider_sha256 = _require_sha256(manifest.get("sha256"))
        source_revision = _require_string(
            manifest.get("source_revision"), "source_revision"
        )
        commit = _require_git_object_id(
            manifest.get("commit", ""), allow_empty=True
        )

        manifest_source_path = manifest.get(
            "source_path", policy.get("source_path", ".")
        )
        source_path = _require_source_path(manifest_source_path)
        if "source_path" in policy:
            policy_source_path = _require_source_path(policy["source_path"])
            if source_path != policy_source_path:
                raise ValueError(
                    "Generic manifest source_path differs from reviewed policy."
                )

        return ReleaseCandidate(
            provider=self.provider,
            repository_identity=identity,
            release_id=release_id,
            version=version,
            tag="",
            released_at=released_at,
            source_revision=source_revision,
            commit=commit,
            artifact_kind="asset_zip",
            artifact_provenance="generic_manifest",
            artifact_url=artifact_url,
            source_archive_url="",
            artifact_size=artifact_size,
            provider_sha256=provider_sha256,
            source_path=source_path,
            migration_mode="manual",
            migration_evidence="generic_manifest",
        )
