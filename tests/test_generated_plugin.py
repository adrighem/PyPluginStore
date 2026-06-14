import subprocess
import sys

from conftest import REPO_ROOT


def test_generated_plugin_py_is_current():
    plugin_file = REPO_ROOT / "plugin.py"
    original = plugin_file.read_bytes()

    try:
        result = subprocess.run(
            [sys.executable, ".github/scripts/generate_plugin.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        generated = plugin_file.read_bytes()
        assert result.returncode == 0, result.stderr
        assert generated == original, (
            "plugin.py is stale; run python .github/scripts/generate_plugin.py "
            "and commit the generated file. If this still fails, check that "
            "plugin.py uses LF line endings."
        )
    finally:
        if plugin_file.read_bytes() != original:
            plugin_file.write_bytes(original)
