To add your plugin, please make a pull request with your lines added.

## Generated plugin.py

`plugin.py` is generated from `plugin_core.py` plus the Domoticz XML header. If you edit `plugin_core.py`, run:

```bash
python .github/scripts/generate_plugin.py
```

Commit both `plugin_core.py` and the regenerated `plugin.py`. The generated file should use LF line endings; the repository `.gitattributes` file is configured to help keep this consistent.
