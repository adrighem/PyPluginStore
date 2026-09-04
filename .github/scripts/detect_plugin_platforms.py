#!/usr/bin/env python3
import argparse
import ast
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from registry_records import (
    RegistryRecord,
    load_registry_file,
    parse_registry_owner,
    save_registry_file,
)


REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../.."))
REGISTRY_FILE = os.path.join(REPO_ROOT, "registry.json")
PLATFORM_METADATA_FILE = os.path.join(REPO_ROOT, ".github", "platform_detection.json")

PLATFORM_ORDER = ["linux", "windows"]
DEFAULT_PLATFORMS = ["linux", "windows"]
MAX_ANALYSIS_FILES = 40
MAX_TEXT_FILE_BYTES = 160_000

API_USER_AGENT = "Domoticz-Plugin-Platform-Scanner"
DEFAULT_GIT_HOST = "github.com"
SUPPORTED_GIT_HOSTS = ("github.com", "gitlab.com", "codeberg.org")
PLATFORM_METADATA_VERSION = 2
CONFIDENCE_ORDER = {
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}

LINUX_ONLY_PATTERNS = [
    (r"\blinux\s+only\b", 10, "states Linux only"),
    (r"\bonly\s+(?:runs?|works?|supported)\s+(?:on|with|under)\s+linux\b", 10, "states Linux-only support"),
    (r"\brequires?\s+(?:a\s+)?(?:linux|raspbian|raspberry\s+pi)\b", 8, "states Linux/Raspberry Pi requirement"),
    (r"\bnot\s+(?:supported|working|compatible)\s+(?:on|with|under)\s+windows\b", 10, "states Windows is unsupported"),
    (r"\bdoes\s+not\s+(?:run|work)\s+(?:on|under)\s+windows\b", 10, "states Windows does not work"),
]

WINDOWS_ONLY_PATTERNS = [
    (r"\bwindows\s+only\b", 10, "states Windows only"),
    (r"\bonly\s+(?:runs?|works?|supported)\s+(?:on|with|under)\s+windows\b", 10, "states Windows-only support"),
    (r"\brequires?\s+(?:microsoft\s+)?windows\b", 8, "states Windows requirement"),
    (r"\bnot\s+(?:supported|working|compatible)\s+(?:on|with|under)\s+linux\b", 10, "states Linux is unsupported"),
    (r"\bdoes\s+not\s+(?:run|work)\s+(?:on|under)\s+linux\b", 10, "states Linux does not work"),
]

BOTH_PLATFORM_PATTERNS = [
    (r"\b(?:linux|raspbian|raspberry\s+pi)\b.{0,100}\bwindows\b", 8, "mentions Linux and Windows support"),
    (r"\bwindows\b.{0,100}\b(?:linux|raspbian|raspberry\s+pi)\b", 8, "mentions Windows and Linux support"),
    (r"\bcross[-\s]?platform\b", 8, "states cross-platform support"),
    (r"\bplatform[-\s]?independent\b", 8, "states platform-independent support"),
    (r"\b(?:works|runs|supported)\s+(?:on|under)\s+(?:both\s+)?(?:linux|windows)\s+and\s+(?:linux|windows)\b", 8, "states both-platform support"),
]

LINUX_TEXT_PATTERNS = [
    (r"\braspberry\s+pi\b|\braspbian\b|\brpi\b", 3, "mentions Raspberry Pi/Raspbian"),
    (r"\bapt(?:-get)?\s+install\b", 4, "uses apt package installation"),
    (r"\bsudo\b", 2, "uses sudo"),
    (r"\bsystemctl\b|\bsystemd\b", 4, "uses systemd"),
    (r"\bchmod\s+\+x\b", 3, "uses chmod"),
    (r"(?<!\w)/(?:dev|etc|proc|sys|var)/(?:[\w.\-/]+)?", 4, "uses Unix system paths"),
    (r"/dev/(?:tty|serial|gpio|i2c|spi)", 6, "uses Linux device paths"),
    (r"\b(?:bash|sh)\s+", 2, "uses shell commands"),
    (r"\b(?:rpi\.gpio|gpiozero|pigpio|spidev|smbus2?|wiringpi|adafruit_dht|bluepy)\b", 7, "uses Linux/Raspberry Pi Python dependency"),
]

WINDOWS_TEXT_PATTERNS = [
    (r"\bpowershell(?:\.exe)?\b", 5, "uses PowerShell"),
    (r"\bcmd\.exe\b", 5, "uses cmd.exe"),
    (r"\b(?:\.bat|\.cmd|\.ps1)\b", 4, "uses Windows script files"),
    (r"\bCOM\d+\b", 5, "uses Windows serial port names"),
    (r"\b[A-Z]:\\", 5, "uses Windows drive paths"),
    (r"\bwindows\s+service\b", 4, "mentions Windows service"),
    (r"\b(?:pywin32|pypiwin32|win32api|win32com|win32service|winreg|winsound|msvcrt|wmi)\b", 8, "uses Windows Python dependency"),
]

LINUX_IMPORTS = {
    "adafruit_dht",
    "bluepy",
    "dbus",
    "fcntl",
    "gpiozero",
    "grp",
    "pigpio",
    "pwd",
    "rpi",
    "rpi.gpio",
    "smbus",
    "smbus2",
    "spidev",
    "syslog",
    "termios",
    "wiringpi",
}

WINDOWS_IMPORTS = {
    "msvcrt",
    "pywintypes",
    "pythoncom",
    "win32api",
    "win32com",
    "win32con",
    "win32event",
    "win32service",
    "win32serviceutil",
    "winreg",
    "winsound",
    "wmi",
}

SKIP_PATH_PARTS = {
    ".git",
    ".github",
    "__pycache__",
    "node_modules",
    "vendor",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
}

ANALYSIS_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".cmd",
    ".ini",
    ".md",
    ".ps1",
    ".py",
    ".rst",
    ".service",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

IMPORTANT_FILENAMES = {
    "dockerfile",
    "install",
    "install.sh",
    "install.ps1",
    "plugin.py",
    "pyproject.toml",
    "readme",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}


class PlatformDecision:
    def __init__(
        self,
        platforms,
        linux_score=0,
        windows_score=0,
        both_score=0,
        confidence="low",
        reasons=None,
        evidence_class="unknown",
        requires_python="",
    ):
        self.platforms = list(platforms)
        self.linux_score = linux_score
        self.windows_score = windows_score
        self.both_score = both_score
        self.confidence = confidence
        self.reasons = list(reasons or [])
        self.evidence_class = evidence_class
        self.requires_python = str(requires_python or "")

    def to_dict(self):
        result = {
            "platforms": self.platforms,
            "confidence": self.confidence,
            "evidence_class": self.evidence_class,
            "scores": {
                "linux": self.linux_score,
                "windows": self.windows_score,
                "both": self.both_score,
            },
            "reasons": self.reasons,
        }
        if self.requires_python:
            result["requires_python"] = self.requires_python
        return result


def github_headers():
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": API_USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def gitlab_headers():
    headers = {
        "Accept": "application/json",
        "User-Agent": API_USER_AGENT,
    }
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        headers["PRIVATE-TOKEN"] = token
    return headers


def generic_headers():
    return {
        "Accept": "application/json",
        "User-Agent": API_USER_AGENT,
    }


def headers_for_url(url):
    hostname = urllib.parse.urlparse(url).hostname or ""
    if hostname in {"api.github.com", "raw.githubusercontent.com"}:
        return github_headers()
    if hostname == "gitlab.com":
        return gitlab_headers()
    return generic_headers()


def fetch_json(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers=headers or headers_for_url(url))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"Error fetching {url}: HTTP {e.code}")
    except Exception as e:
        print(f"Error fetching {url}: {e}")
    return None


def fetch_text(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers=headers or headers_for_url(url))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read(MAX_TEXT_FILE_BYTES + 1)
            return content[:MAX_TEXT_FILE_BYTES].decode("utf-8", errors="replace")
    except urllib.error.HTTPError:
        return None
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None


def quote_path_part(value):
    return urllib.parse.quote(str(value), safe="")


def quote_repo_path(path):
    return urllib.parse.quote(str(path), safe="/")


def split_registry_owner(author):
    location = parse_registry_owner(author)
    return location.host, location.owner_path


def repository_path(owner, repo):
    host, owner_path = split_registry_owner(owner)
    path_parts = [part for part in (owner_path + "/" + repo).split("/") if part]
    return host, "/".join(path_parts)


def get_repo_tree(owner, repo, branch):
    host, path = repository_path(owner, repo)
    if host == "gitlab.com":
        project_path = urllib.parse.quote(path, safe="")
        url = (
            "https://gitlab.com/api/v4/projects/"
            f"{project_path}/repository/tree?recursive=true&per_page=100&ref={quote_path_part(branch)}"
        )
        data = fetch_json(url)
        return data if isinstance(data, list) else None

    if host == "codeberg.org":
        url = (
            "https://codeberg.org/api/v1/repos/"
            f"{quote_repo_path(path)}/git/trees/{quote_path_part(branch)}?recursive=1"
        )
        data = fetch_json(url)
    elif host == DEFAULT_GIT_HOST:
        url = (
            "https://api.github.com/repos/"
            f"{quote_repo_path(path)}/git/trees/"
            f"{quote_path_part(branch)}?recursive=1"
        )
        data = fetch_json(url)
    else:
        return None

    if not isinstance(data, dict):
        return None
    tree = data.get("tree")
    if not isinstance(tree, list):
        return None
    return tree


def get_raw_file(owner, repo, branch, path):
    host, repo_path = repository_path(owner, repo)
    if host == "gitlab.com":
        url = (
            "https://gitlab.com/"
            f"{quote_repo_path(repo_path)}/-/raw/{quote_path_part(branch)}/{quote_repo_path(path)}"
        )
        return fetch_text(url)

    if host == "codeberg.org":
        url = (
            "https://codeberg.org/"
            f"{quote_repo_path(repo_path)}/raw/branch/{quote_path_part(branch)}/{quote_repo_path(path)}"
        )
        return fetch_text(url)

    if host == DEFAULT_GIT_HOST:
        url = (
            "https://raw.githubusercontent.com/"
            f"{quote_repo_path(repo_path)}/"
            f"{quote_path_part(branch)}/{quote_repo_path(path)}"
        )
        return fetch_text(url)
    return None


def normalize_platforms(platforms):
    if isinstance(platforms, str):
        platforms = [platforms]
    if not isinstance(platforms, list):
        return []

    normalized = []
    for platform in platforms:
        platform_name = str(platform or "").strip().lower()
        if platform_name in PLATFORM_ORDER and platform_name not in normalized:
            normalized.append(platform_name)
    return [platform for platform in PLATFORM_ORDER if platform in normalized]


def confidence_rank(confidence):
    return CONFIDENCE_ORDER.get(str(confidence or "unknown").lower(), 0)


def decision_platforms(decision):
    return normalize_platforms(getattr(decision, "platforms", []))


def decision_confidence(decision):
    return str(getattr(decision, "confidence", "unknown") or "unknown").lower()


def decision_evidence_class(decision):
    return str(getattr(decision, "evidence_class", "unknown") or "unknown")


def decision_to_dict(decision):
    if decision is None:
        return None
    if hasattr(decision, "to_dict"):
        return decision.to_dict()
    return {
        "platforms": decision_platforms(decision),
        "confidence": decision_confidence(decision),
        "evidence_class": decision_evidence_class(decision),
        "scores": {
            "linux": getattr(decision, "linux_score", 0),
            "windows": getattr(decision, "windows_score", 0),
            "both": getattr(decision, "both_score", 0),
        },
        "reasons": list(getattr(decision, "reasons", []) or []),
    }


def get_registry_entry_platforms(data):
    if isinstance(data, dict):
        return normalize_platforms(data.get("platforms", data.get("platform")))
    if isinstance(data, list) and len(data) > 5:
        return normalize_platforms(data[5])
    return []


def set_registry_entry_platforms(data, platforms):
    platforms = normalize_platforms(platforms)
    if not platforms:
        return data
    package_id = (
        data.get("package_id", "Plugin")
        if isinstance(data, dict)
        else "Plugin"
    )
    return RegistryRecord.from_entry(package_id, data).with_platforms(
        platforms
    ).to_document()


def registry_entry_identity(data):
    try:
        package_id = (
            data.get("package_id", "Plugin")
            if isinstance(data, dict)
            else "Plugin"
        )
        record = RegistryRecord.from_entry(package_id, data)
    except ValueError:
        return "", "", ""
    return record.owner, record.repository, record.branch


def platform_metadata_identity(owner, repo, branch):
    host, owner_path = split_registry_owner(owner)
    repo_path = "/".join(part for part in (owner_path + "/" + repo).split("/") if part)
    return f"{host}/{repo_path}@{branch}".lower()


def new_platform_metadata():
    return {
        "schema_version": PLATFORM_METADATA_VERSION,
        "entries": {},
    }


def normalize_platform_metadata(metadata):
    if not isinstance(metadata, dict):
        return new_platform_metadata()

    entries = metadata.get("entries")
    if not isinstance(entries, dict):
        entries = {}

    return {
        "schema_version": PLATFORM_METADATA_VERSION,
        "entries": entries,
    }


def parse_platform_metadata(document):
    if not isinstance(document, dict):
        raise ValueError("Platform metadata must contain a JSON object.")
    if set(document) != {"schema_version", "detections"}:
        raise ValueError("Platform metadata must use the strict v2 schema.")
    if document["schema_version"] != PLATFORM_METADATA_VERSION:
        raise ValueError("Platform metadata schema is unsupported.")
    detections = document["detections"]
    if not isinstance(detections, list):
        raise ValueError("Platform metadata detections must be an array.")
    entries = {}
    folded_ids = set()
    for detection in detections:
        if not isinstance(detection, dict):
            raise ValueError("Platform metadata detection must be an object.")
        package_id = detection.get("package_id")
        if (
            not isinstance(package_id, str)
            or not package_id
            or package_id != package_id.strip()
            or package_id.startswith(".")
            or "/" in package_id
            or "\\" in package_id
        ):
            raise ValueError("Platform metadata package_id is invalid.")
        folded_id = package_id.casefold()
        if folded_id in folded_ids:
            raise ValueError(
                "Platform metadata contains a duplicate package_id."
            )
        folded_ids.add(folded_id)
        entry = dict(detection)
        del entry["package_id"]
        entries[package_id] = entry
    return {
        "schema_version": PLATFORM_METADATA_VERSION,
        "entries": entries,
    }


def platform_metadata_document(metadata):
    metadata = normalize_platform_metadata(metadata)
    detections = []
    for package_id, entry in metadata["entries"].items():
        if not isinstance(entry, dict):
            raise ValueError("Platform metadata entry must be an object.")
        record = {"package_id": package_id}
        record.update(entry)
        detections.append(record)
    detections.sort(
        key=lambda item: (item["package_id"].casefold(), item["package_id"])
    )
    return {
        "schema_version": PLATFORM_METADATA_VERSION,
        "detections": detections,
    }


def load_platform_metadata(metadata_file=PLATFORM_METADATA_FILE):
    if not os.path.exists(metadata_file):
        return new_platform_metadata()

    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    "Platform metadata contains duplicate JSON key "
                    + str(key)
                    + "."
                )
            result[key] = value
        return result

    with open(metadata_file, "r", encoding="utf-8") as f:
        return parse_platform_metadata(
            json.load(f, object_pairs_hook=reject_duplicate_keys)
        )


def save_platform_metadata(metadata, metadata_file=PLATFORM_METADATA_FILE):
    directory = os.path.dirname(metadata_file) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix="." + os.path.basename(metadata_file) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                platform_metadata_document(metadata),
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, metadata_file)
        temporary_path = ""
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def baseline_platform_metadata_entry(key, data, reviewed=False):
    owner, repo, branch = registry_entry_identity(data)
    platforms = get_registry_entry_platforms(data)
    source = "reviewed" if reviewed else ("legacy_detected" if platforms else "unknown")
    evidence_class = "manual_registry_edit" if reviewed else "legacy"
    return {
        "identity": platform_metadata_identity(owner, repo, branch),
        "owner": owner,
        "repository": repo,
        "branch": branch,
        "registry_platforms": platforms,
        "source": source,
        "confidence": "unknown",
        "evidence_class": evidence_class,
        "reviewed": reviewed,
    }


def mark_manual_registry_edit(entry):
    entry["source"] = "reviewed"
    entry["confidence"] = "unknown"
    entry["evidence_class"] = "manual_registry_edit"
    entry["reviewed"] = True
    entry.pop("last_detection", None)
    entry.pop("policy_action", None)


def ensure_platform_metadata_for_registry(metadata, registry, manual_changes_are_reviewed=False):
    metadata = normalize_platform_metadata(metadata)
    entries = metadata["entries"]
    registry_keys = set()

    for key, data in registry.items():
        if key == "Idle":
            continue
        owner, repo, branch = registry_entry_identity(data)
        if not owner or not repo or not branch:
            continue

        registry_keys.add(key)
        platforms = get_registry_entry_platforms(data)
        identity = platform_metadata_identity(owner, repo, branch)
        entry = entries.get(key)

        if not isinstance(entry, dict):
            entries[key] = baseline_platform_metadata_entry(key, data, reviewed=manual_changes_are_reviewed)
            continue

        identity_changed = entry.get("identity") not in {None, identity}
        if identity_changed:
            entries[key] = baseline_platform_metadata_entry(key, data, reviewed=manual_changes_are_reviewed)
            entries[key]["previous_identity"] = entry.get("identity")
            continue

        platforms_changed = normalize_platforms(entry.get("registry_platforms", [])) != platforms
        entry["identity"] = identity
        entry["owner"] = owner
        entry["repository"] = repo
        entry["branch"] = branch
        entry["registry_platforms"] = platforms
        entry.setdefault("source", "legacy_detected" if platforms else "unknown")
        entry.setdefault("confidence", "unknown")
        entry.setdefault("evidence_class", "legacy")
        entry.setdefault("reviewed", False)
        if platforms_changed and manual_changes_are_reviewed:
            mark_manual_registry_edit(entry)

    for key in list(entries):
        if key not in registry_keys:
            del entries[key]

    return metadata


def choose_platforms_for_registry(current_platforms, decision, metadata_entry=None, is_new=False):
    current_platforms = normalize_platforms(current_platforms)
    detected_platforms = decision_platforms(decision)

    if not detected_platforms:
        return current_platforms, "no_decision"

    if metadata_entry and metadata_entry.get("reviewed"):
        return current_platforms, "kept_reviewed"

    if detected_platforms == current_platforms:
        return current_platforms, "unchanged"

    confidence = decision_confidence(decision)

    if not current_platforms:
        if confidence_rank(confidence) >= confidence_rank("medium"):
            return detected_platforms, "accepted_new_medium_or_high"
        return current_platforms, "kept_low_confidence_new"

    if confidence == "high":
        return detected_platforms, "accepted_high_confidence_change"

    return current_platforms, "kept_existing_requires_high_confidence"


def update_platform_metadata_entry(metadata, key, owner, repo, branch, registry_platforms, decision=None, policy_action=None):
    metadata = normalize_platform_metadata(metadata)
    entries = metadata["entries"]
    previous = entries.get(key) if isinstance(entries.get(key), dict) else {}
    identity = platform_metadata_identity(owner, repo, branch)
    identity_changed = previous.get("identity") not in {None, identity}
    reviewed = bool(previous.get("reviewed")) and not identity_changed

    registry_platforms = normalize_platforms(registry_platforms)
    detection = decision_to_dict(decision)
    confidence = previous.get("confidence", "unknown")
    evidence_class = previous.get("evidence_class", "legacy")
    source = previous.get("source", "legacy_detected" if registry_platforms else "unknown")

    if reviewed:
        source = "reviewed"
    elif detection and registry_platforms == normalize_platforms(detection.get("platforms")):
        source = "detected"
        confidence = detection.get("confidence", "unknown")
        evidence_class = detection.get("evidence_class", "unknown")
    elif identity_changed:
        source = "legacy_detected" if registry_platforms else "unknown"
        confidence = "unknown"
        evidence_class = "legacy"

    updated = {
        "identity": identity,
        "owner": owner,
        "repository": repo,
        "branch": branch,
        "registry_platforms": registry_platforms,
        "source": source,
        "confidence": confidence,
        "evidence_class": evidence_class,
        "reviewed": reviewed,
    }

    if detection:
        updated["last_detection"] = detection
    elif previous.get("last_detection") is not None:
        updated["last_detection"] = previous["last_detection"]

    if policy_action:
        updated["policy_action"] = policy_action
    elif previous.get("policy_action"):
        updated["policy_action"] = previous["policy_action"]

    if identity_changed and previous.get("identity"):
        updated["previous_identity"] = previous["identity"]

    entries[key] = updated
    return metadata


def is_skipped_path(path):
    parts = str(path).lower().split("/")
    return any(part in SKIP_PATH_PARTS for part in parts)


def file_priority(path):
    path_lower = str(path).lower()
    base = os.path.basename(path_lower)
    _, ext = os.path.splitext(base)

    if is_skipped_path(path_lower):
        return None
    if base.startswith("readme"):
        return 0
    if base in {"requirements.txt", "constraints.txt", "pyproject.toml", "setup.py", "setup.cfg"}:
        return 1
    if base == "plugin.py" or base.startswith("plugin_"):
        return 2
    if ext == ".py":
        return 3
    if base in IMPORTANT_FILENAMES or ext in ANALYSIS_EXTENSIONS:
        return 4
    return None


def select_analysis_files(tree, limit=MAX_ANALYSIS_FILES):
    candidates = []
    for item in tree or []:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        priority = file_priority(path)
        if priority is None:
            continue
        size = item.get("size")
        if size is not None:
            try:
                if int(size) > MAX_TEXT_FILE_BYTES:
                    continue
            except (TypeError, ValueError):
                pass
        candidates.append((priority, str(path).count("/"), int(size or 0), str(path)))

    candidates.sort()
    return [path for _, _, _, path in candidates[:limit]]


def add_reason(scores, target, weight, source, reason):
    scores[target] += weight
    tagged_reason = f"{source}: {reason}"
    if tagged_reason not in scores["reasons"]:
        scores["reasons"].append(tagged_reason)


def score_patterns(text, source, scores, patterns, target):
    if not text:
        return
    for pattern, weight, reason in patterns:
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            add_reason(scores, target, weight, source, reason)


def score_path(path, scores):
    path_lower = str(path).lower()
    base = os.path.basename(path_lower)
    _, ext = os.path.splitext(base)

    if ext in {".sh", ".service"} or "/systemd/" in path_lower:
        add_reason(scores, "linux", 3, path, "Linux-oriented file name")
    if ext in {".bat", ".cmd", ".ps1"} or "windows" in path_lower:
        add_reason(scores, "windows", 3, path, "Windows-oriented file name")


def collect_python_imports(tree):
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.lower())
    return imports


def score_python_ast(text, source, scores):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        return

    for module_name in collect_python_imports(tree):
        module_root = module_name.split(".", 1)[0]
        if module_name in LINUX_IMPORTS or module_root in LINUX_IMPORTS:
            add_reason(scores, "linux", 8, source, f"imports Linux-specific module {module_name}")
        if module_name in WINDOWS_IMPORTS or module_root in WINDOWS_IMPORTS:
            add_reason(scores, "windows", 8, source, f"imports Windows-specific module {module_name}")


def score_text(text, source, scores):
    score_patterns(text, source, scores, BOTH_PLATFORM_PATTERNS, "both")
    score_patterns(text, source, scores, LINUX_ONLY_PATTERNS, "linux_only")
    score_patterns(text, source, scores, WINDOWS_ONLY_PATTERNS, "windows_only")
    score_patterns(text, source, scores, LINUX_TEXT_PATTERNS, "linux")
    score_patterns(text, source, scores, WINDOWS_TEXT_PATTERNS, "windows")

    if source.lower().endswith(".py"):
        score_python_ast(text, source, scores)


def new_scores():
    return {
        "linux": 0,
        "windows": 0,
        "both": 0,
        "linux_only": 0,
        "windows_only": 0,
        "reasons": [],
    }


def decide_platforms(scores, assume_generic_python_is_cross_platform=True):
    linux_score = scores["linux"] + scores["linux_only"]
    windows_score = scores["windows"] + scores["windows_only"]
    both_score = scores["both"]

    if scores["linux_only"] >= 8 and windows_score < 6:
        return PlatformDecision(
            ["linux"],
            linux_score,
            windows_score,
            both_score,
            "high",
            scores["reasons"],
            "explicit_linux_only",
        )

    if scores["windows_only"] >= 8 and linux_score < 6:
        return PlatformDecision(
            ["windows"],
            linux_score,
            windows_score,
            both_score,
            "high",
            scores["reasons"],
            "explicit_windows_only",
        )

    if both_score >= 8:
        return PlatformDecision(
            DEFAULT_PLATFORMS,
            linux_score,
            windows_score,
            both_score,
            "high",
            scores["reasons"],
            "explicit_both",
        )

    if linux_score >= 8 and windows_score >= 8:
        return PlatformDecision(
            DEFAULT_PLATFORMS,
            linux_score,
            windows_score,
            both_score,
            "medium",
            scores["reasons"],
            "mixed_platform_evidence",
        )

    if linux_score >= 8 and windows_score < 6:
        return PlatformDecision(
            ["linux"],
            linux_score,
            windows_score,
            both_score,
            "medium",
            scores["reasons"],
            "linux_evidence",
        )

    if windows_score >= 8 and linux_score < 6:
        return PlatformDecision(
            ["windows"],
            linux_score,
            windows_score,
            both_score,
            "medium",
            scores["reasons"],
            "windows_evidence",
        )

    if assume_generic_python_is_cross_platform:
        reasons = list(scores["reasons"])
        if not reasons:
            reasons.append("generic: no Linux-only or Windows-only evidence found")
        return PlatformDecision(DEFAULT_PLATFORMS, linux_score, windows_score, both_score, "low", reasons, "generic_python")

    return PlatformDecision([], linux_score, windows_score, both_score, "unknown", scores["reasons"], "unknown")


_PYPI_CACHE = {}


def _get_pypi_requires_python(pkg_name, version=None):
    cache_key = (pkg_name.lower(), version)
    if cache_key in _PYPI_CACHE:
        return _PYPI_CACHE[cache_key]

    if version:
        url = f"https://pypi.org/pypi/{urllib.parse.quote(pkg_name)}/{urllib.parse.quote(str(version))}/json"
    else:
        url = f"https://pypi.org/pypi/{urllib.parse.quote(pkg_name)}/json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": API_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
            req_py = data.get("info", {}).get("requires_python") or ""
            _PYPI_CACHE[cache_key] = req_py.strip()
            return _PYPI_CACHE[cache_key]
    except Exception:
        _PYPI_CACHE[cache_key] = ""
        return ""


def _parse_requirement_specifiers(text):
    results = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(
            r"^([A-Za-z0-9_.\-]+)\s*(?:(==|>=|<=|~=|>|<)\s*([0-9A-Za-z_.\-]+))?",
            line,
        )
        if match:
            pkg_name = match.group(1)
            op = match.group(2)
            ver = match.group(3)
            is_pinned = (op == "==" and bool(ver))
            results.append((pkg_name, ver if is_pinned else None, op, ver))
    return results


def _extract_min_python_version(requires_python_str):
    if not requires_python_str:
        return None
    min_ver = None
    for clause in requires_python_str.split(","):
        clause = clause.strip()
        m = re.match(r"^(?:>=|>|~=|==)\s*([0-9]+(?:\.[0-9]+)*)", clause)
        if m:
            v_parts = [int(p) for p in m.group(1).split(".") if p.isdigit()]
            while len(v_parts) < 2:
                v_parts.append(0)
            v_tuple = tuple(v_parts[:2])
            if min_ver is None or v_tuple > min_ver:
                min_ver = v_tuple
    return min_ver


def detect_requires_python_from_texts(file_texts):
    """Detect Python version requirement from pyproject.toml, setup.cfg, setup.py, requirements, or plugin.py."""
    if not file_texts:
        return ""

    # 1. pyproject.toml
    for path, text in file_texts.items():
        if os.path.basename(path).lower() == "pyproject.toml":
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib
                except ImportError:
                    tomllib = None
            if tomllib is not None:
                try:
                    data = tomllib.loads(text)
                    req = (
                        data.get("project", {}).get("requires-python")
                        or data.get("tool", {})
                        .get("poetry", {})
                        .get("dependencies", {})
                        .get("python")
                    )
                    if isinstance(req, str) and req.strip():
                        return req.strip()
                except Exception:
                    pass
            m = re.search(
                r"requires-python\s*=\s*['\"]([^'\"]+)['\"]",
                text,
                re.IGNORECASE,
            )
            if m:
                return m.group(1).strip()

    # 2. setup.cfg
    for path, text in file_texts.items():
        if os.path.basename(path).lower() == "setup.cfg":
            import configparser

            try:
                cfg = configparser.ConfigParser()
                cfg.read_string(text)
                req = cfg.get("options", "python_requires", fallback=None)
                if req and req.strip():
                    return req.strip()
            except Exception:
                pass
            m = re.search(
                r"python_requires\s*=\s*([^#\r\n]+)", text, re.IGNORECASE
            )
            if m:
                return m.group(1).split("#")[0].strip().strip("'\"")

    # 3. setup.py
    for path, text in file_texts.items():
        if os.path.basename(path).lower() == "setup.py":
            m = re.search(
                r"python_requires\s*=\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE
            )
            if m:
                return m.group(1).strip()

    # 4. plugin.py XML header or sys.version_info check
    for path, text in file_texts.items():
        if os.path.basename(path).lower() == "plugin.py":
            m = re.search(
                r"<plugin\b[^>]*\b(?:requires_python|python_requires|python)\s*=\s*['\"]([^'\"]+)['\"]",
                text,
                re.IGNORECASE,
            )
            if m:
                return m.group(1).strip()
            m = re.search(
                r"sys\.version_info\s*(?:<|<=\s*\(|\s*<\s*\()\s*([0-9]+)\s*,\s*([0-9]+)",
                text,
            )
            if m:
                return f">={m.group(1)}.{m.group(2)}"

    # 5. requirements.txt / constraints.txt pinned dependencies
    highest_min = None
    for path, text in file_texts.items():
        base = os.path.basename(path).lower()
        if base in {"requirements.txt", "constraints.txt"}:
            specs = _parse_requirement_specifiers(text)
            for pkg_name, pinned_ver, op, ver in specs:
                if pinned_ver:
                    py_req = _get_pypi_requires_python(pkg_name, pinned_ver)
                    if py_req:
                        min_v = _extract_min_python_version(py_req)
                        if min_v and (highest_min is None or min_v > highest_min):
                            highest_min = min_v

    if highest_min and highest_min >= (3, 8):
        return f">={highest_min[0]}.{highest_min[1]}"

    return ""


def detect_platforms_from_repository_data(repo_info=None, file_texts=None, assume_generic_python_is_cross_platform=True):
    scores = new_scores()
    repo_info = repo_info or {}
    file_texts = file_texts or {}

    metadata_parts = [
        repo_info.get("name", ""),
        repo_info.get("description", ""),
        " ".join(repo_info.get("topics") or []),
    ]
    score_text("\n".join(str(part) for part in metadata_parts if part), "github metadata", scores)

    for path, text in file_texts.items():
        score_path(path, scores)
        score_text(text, path, scores)

    decision = decide_platforms(scores, assume_generic_python_is_cross_platform)
    decision.requires_python = detect_requires_python_from_texts(file_texts)
    return decision


def detect_platforms_for_repo(owner, repo, branch, repo_info=None):
    tree = get_repo_tree(owner, repo, branch)
    if tree is None:
        decision = detect_platforms_from_repository_data(
            repo_info=repo_info,
            file_texts={},
            assume_generic_python_is_cross_platform=False,
        )
        return decision if decision.platforms else None

    file_texts = {}
    for path in select_analysis_files(tree):
        text = get_raw_file(owner, repo, branch, path)
        if text is not None:
            file_texts[path] = text

    decision = detect_platforms_from_repository_data(
        repo_info=repo_info,
        file_texts=file_texts,
        assume_generic_python_is_cross_platform=bool(file_texts),
    )
    return decision if decision.platforms else None


def update_registry_platforms(
    registry_file=REGISTRY_FILE,
    metadata_file=PLATFORM_METADATA_FILE,
    missing_only=False,
    dry_run=False,
    limit=None,
    sleep_seconds=None,
):
    registry = load_registry_file(registry_file)

    metadata_exists = os.path.exists(metadata_file)
    platform_metadata = ensure_platform_metadata_for_registry(
        load_platform_metadata(metadata_file),
        registry,
        manual_changes_are_reviewed=metadata_exists,
    )

    stats = {
        "scanned": 0,
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
        "skipped": 0,
        "metadata_updated": 0,
    }

    if sleep_seconds is None:
        sleep_seconds = 0 if os.environ.get("GITHUB_TOKEN") else 1

    for key, data in list(registry.items()):
        if key == "Idle":
            continue
        if limit is not None and stats["scanned"] >= limit:
            break

        current_platforms = get_registry_entry_platforms(data)
        if missing_only and current_platforms:
            stats["skipped"] += 1
            continue

        owner, repo, branch = registry_entry_identity(data)
        if not owner or not repo or not branch:
            print(f"[-] Skipping {key} (invalid registry entry)")
            stats["skipped"] += 1
            continue

        stats["scanned"] += 1
        print(f"Checking {key} ({owner}/{repo})...", end=" ", flush=True)
        decision = detect_platforms_for_repo(owner, repo, branch)
        if decision is None:
            print("no decision")
            stats["failed"] += 1
        else:
            current_record = RegistryRecord.from_entry(key, data)
            current_requires_python = current_record.requires_python
            detected_requires_python = getattr(decision, "requires_python", "")
            requires_python_changed = bool(
                detected_requires_python
                and detected_requires_python != current_requires_python
            )

            metadata_entry = platform_metadata["entries"].get(key, {})
            next_platforms, policy_action = choose_platforms_for_registry(
                current_platforms,
                decision,
                metadata_entry=metadata_entry,
                is_new=False,
            )
            platforms_changed = (next_platforms != current_platforms)

            if platforms_changed or requires_python_changed:
                updated_record = current_record
                if platforms_changed:
                    updated_record = updated_record.with_platforms(next_platforms)
                if requires_python_changed:
                    updated_record = updated_record.with_requires_python(
                        detected_requires_python
                    )
                registry[key] = updated_record.to_document()
                log_parts = []
                if platforms_changed:
                    log_parts.append(
                        f"platforms: {current_platforms or ['unknown']} -> {next_platforms} ({decision.confidence}, {policy_action})"
                    )
                if requires_python_changed:
                    log_parts.append(
                        f"requires_python: '{current_requires_python}' -> '{detected_requires_python}'"
                    )
                print("; ".join(log_parts))
                stats["updated"] += 1
            else:
                print(f"{current_platforms} unchanged")
                stats["unchanged"] += 1

            before = json.dumps(platform_metadata["entries"].get(key, {}), sort_keys=True)
            platform_metadata = update_platform_metadata_entry(
                platform_metadata,
                key,
                owner,
                repo,
                branch,
                next_platforms,
                decision=decision,
                policy_action=policy_action,
            )
            after = json.dumps(platform_metadata["entries"].get(key, {}), sort_keys=True)
            if after != before:
                stats["metadata_updated"] += 1

        if sleep_seconds:
            time.sleep(sleep_seconds)

    if stats["updated"] and not dry_run:
        save_registry_file(registry_file, registry)

    if stats["metadata_updated"] and not dry_run:
        platform_metadata = ensure_platform_metadata_for_registry(platform_metadata, registry)
        save_platform_metadata(platform_metadata, metadata_file)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Detect likely Domoticz plugin platform support and update registry metadata.")
    parser.add_argument("--registry", default=REGISTRY_FILE, help="Path to registry.json")
    parser.add_argument("--metadata", default=PLATFORM_METADATA_FILE, help="Path to platform detection sidecar JSON")
    parser.add_argument("--missing-only", action="store_true", help="Only classify entries without platform metadata")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing registry.json")
    parser.add_argument("--limit", type=int, help="Limit the number of registry entries scanned")
    parser.add_argument("--sleep", type=float, help="Seconds to sleep between repositories")
    args = parser.parse_args()

    stats = update_registry_platforms(
        registry_file=args.registry,
        metadata_file=args.metadata,
        missing_only=args.missing_only,
        dry_run=args.dry_run,
        limit=args.limit,
        sleep_seconds=args.sleep,
    )
    print(
        "Platform scan complete: "
        f"{stats['scanned']} scanned, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged, {stats['failed']} failed, "
        f"{stats['skipped']} skipped, {stats['metadata_updated']} metadata updated."
    )


if __name__ == "__main__":
    main()
