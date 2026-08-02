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

import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_FILE = os.path.join(SCRIPT_DIR, 'registry.json')
SCRIPTS_DIR = os.path.join(SCRIPT_DIR, ".github", "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from registry_records import RegistryRecord
from scan_github_plugins import get_repo_info

def main():
    with open(REGISTRY_FILE, 'r') as f:
        registry = json.load(f)

    print(f"Auditing {len(registry)} plugins...")

    for key, data in list(registry.items()):
        if key == "Idle": continue

        record = RegistryRecord.from_entry(key, data)
        owner = record.owner
        repo_name = record.repository
        desc = record.description

        print(f"Checking {owner}/{repo_name}...", end=' ', flush=True)

        info = get_repo_info(owner, repo_name)

        if info == "DELETED":
            print("❌ DELETED (Should be removed)")
        elif info:
            if info.get('archived'):
                print("⚠️ ARCHIVED (Should we remove?)")
            else:
                current_desc = info.get('description', '')
                if current_desc and current_desc != desc:
                    print("📝 Description out of date")
                else:
                    print("✅ OK")
        else:
            print("❓ Unknown Error")

        # Avoid hitting rate limits
        time.sleep(0.5)

if __name__ == '__main__':
    main()
