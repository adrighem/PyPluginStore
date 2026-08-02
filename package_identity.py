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

"""Shared, non-executing Domoticz package identity certification."""

import hashlib
import html
import io
import re
import tokenize
import unicodedata
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


MAX_PLUGIN_SOURCE_BYTES = 4 * 1024 * 1024
PLUGIN_TAG = re.compile(r"<plugin\b([^>]*)>", re.IGNORECASE | re.DOTALL)
PLUGIN_ATTRIBUTE = re.compile(
    r"([A-Za-z_:][A-Za-z0-9_.:-]*)\s*=\s*(['\"])(.*?)\2",
    re.DOTALL,
)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class CertifiedPackageIdentity:
    domoticz_key: str
    plugin_py_sha256: str


def _decode_python_source(contents):
    if not isinstance(contents, (bytes, bytearray)):
        raise ValueError("plugin.py contents must be bytes.")
    contents = bytes(contents)
    if not contents or len(contents) > MAX_PLUGIN_SOURCE_BYTES:
        raise ValueError("plugin.py has an invalid size.")
    if b"\x00" in contents:
        raise ValueError("plugin.py contains a null byte.")
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(contents).readline)
        source = contents.decode(encoding)
    except (LookupError, SyntaxError, UnicodeDecodeError) as error:
        raise ValueError("plugin.py encoding is invalid.") from error
    return contents, source


def _canonical_domoticz_key(value):
    value = html.unescape(value)
    if (
        not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or CONTROL_CHARACTERS.search(value)
        or any(character in value for character in "<>\r\n")
    ):
        raise ValueError("Domoticz plugin key is invalid.")
    return value


def certify_plugin_py(contents):
    """Return the exact Domoticz key and digest without importing plugin code."""
    contents, source = _decode_python_source(contents)
    candidates = []
    for tag in PLUGIN_TAG.findall(source):
        attributes = {}
        for name, _quote, value in PLUGIN_ATTRIBUTE.findall(tag):
            name = name.lower()
            if name in attributes:
                raise ValueError(
                    "Domoticz plugin tag contains a duplicate attribute."
                )
            attributes[name] = value
        if "key" in attributes:
            candidates.append(attributes)
    if len(candidates) != 1:
        raise ValueError(
            "plugin.py must contain exactly one keyed Domoticz plugin tag."
        )
    attributes = candidates[0]
    if "key" not in attributes:
        raise ValueError("Domoticz plugin tag does not declare a key.")
    return CertifiedPackageIdentity(
        domoticz_key=_canonical_domoticz_key(attributes["key"]),
        plugin_py_sha256=hashlib.sha256(contents).hexdigest(),
    )
