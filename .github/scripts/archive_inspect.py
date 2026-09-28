"""Hardened zip archive inspection utilities for scanner and validator scripts."""

from __future__ import annotations

import io
import os
import sys
import zipfile
from typing import Optional, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from package_identity import MAX_PLUGIN_SOURCE_BYTES  # noqa: E402


def _sanitize_entry_parts(filename: str) -> list[str]:
    cleaned = filename.replace("\\", "/").strip("/")
    if "\0" in cleaned:
        return []
    return [part for part in cleaned.split("/") if part]


def _is_safe_candidate(parts: list[str], info: zipfile.ZipInfo) -> bool:
    if not parts:
        return False
    if info.is_dir() or info.filename.endswith("/"):
        return False
    if info.flag_bits & 0x1:
        return False
    if any(part.startswith(".") or part == "__MACOSX" for part in parts):
        return False
    if parts[-1].lower() != "plugin.py":
        return False
    return 0 < info.file_size <= MAX_PLUGIN_SOURCE_BYTES


def extract_plugin_from_zip(
    archive_bytes: bytes | bytearray,
    *,
    is_source_zip: bool = False,
    expected_source_path: Optional[str] = None,
    root_prefix: Optional[str] = None,
) -> Tuple[Optional[bytes], Optional[str]]:
    """Extract and validate plugin.py from zip archive bytes safely."""
    if not isinstance(archive_bytes, (bytes, bytearray)) or not archive_bytes:
        return None, None

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
            candidates: list[tuple[list[str], str, zipfile.ZipInfo]] = []

            for info in archive.infolist():
                parts = _sanitize_entry_parts(info.filename)
                if not _is_safe_candidate(parts, info):
                    continue

                rel_parts = parts
                if is_source_zip and len(rel_parts) > 1:
                    rel_parts = rel_parts[1:]
                elif root_prefix and rel_parts and rel_parts[0] == root_prefix:
                    rel_parts = rel_parts[1:]

                candidate_source_dir = "/".join(rel_parts[:-1]) if len(rel_parts) > 1 else "."
                candidates.append((rel_parts, candidate_source_dir, info))

            if not candidates:
                return None, None

            if expected_source_path is not None:
                normalized_expected = "." if expected_source_path in ("", ".") else expected_source_path.strip("/")
                for _rel_parts, candidate_source_dir, info in candidates:
                    if candidate_source_dir == normalized_expected:
                        content = archive.read(info)
                        if len(content) != info.file_size:
                            return None, None
                        return content, candidate_source_dir
                return None, None

            candidates.sort(key=lambda item: len(item[0]))
            _rel_parts, chosen_source_dir, chosen_info = candidates[0]

            content = archive.read(chosen_info)
            if len(content) != chosen_info.file_size:
                return None, None

            return content, chosen_source_dir

    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, KeyError):
        return None, None
