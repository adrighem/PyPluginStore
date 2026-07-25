import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT, load_module_from_path


class RecordingJsonHttpClient:
    """Write one configured response through the runtime download contract."""

    def __init__(self, contents):
        self.contents = bytes(contents)
        self.calls = []

    def download_to_path(self, url, destination, **kwargs):
        self.calls.append((url, Path(destination), dict(kwargs)))
        Path(destination).write_bytes(self.contents)
        return SimpleNamespace(
            path=str(destination),
            size=len(self.contents),
            sha256="0" * 64,
            final_url=url,
            redirects=0,
            verified=False,
        )


def test_provider_adapters_are_shipped_as_a_runtime_module():
    module_path = REPO_ROOT / "release_providers.py"

    module = load_module_from_path(
        "runtime_release_providers_under_test",
        module_path,
    )

    assert module.GitHubReleaseAdapter().provider == "github"
    assert module.GitLabReleaseAdapter().provider == "gitlab"
    assert module.ForgejoReleaseAdapter().provider == "forgejo"
    assert module.GiteaReleaseAdapter().provider == "gitea"
    assert module.GenericManifestAdapter().provider == "generic"


@pytest.mark.parametrize(
    "document",
    (
        [{"tag_name": "v.3.1.0"}],
        {"name": "v.3.1.0"},
    ),
)
def test_runtime_json_transport_returns_bounded_json(
    plugin_core_module,
    document,
):
    contents = json.dumps(document).encode("utf-8")
    client = RecordingJsonHttpClient(contents)
    transport = plugin_core_module.SafeReleaseJsonTransport(
        http_client=client,
        max_bytes=4096,
    )

    result = transport.get_json(
        "https://api.example.test/releases",
        headers={"Accept": "application/json"},
    )

    assert result == document
    assert client.calls == [
        (
            "https://api.example.test/releases",
            client.calls[0][1],
            {
                "allowed_origins": (),
                "headers": {"Accept": "application/json"},
            },
        )
    ]
    assert not client.calls[0][1].exists()


@pytest.mark.parametrize(
    "contents",
    (
        b'{"tag":"v1.0.0","tag":"v2.0.0"}',
        b'{"outer":{"tag":"v1.0.0","tag":"v2.0.0"}}',
        b'"v1.0.0"',
        b"\xff",
        b"",
    ),
)
def test_runtime_json_transport_rejects_ambiguous_or_invalid_json(
    plugin_core_module,
    contents,
):
    transport = plugin_core_module.SafeReleaseJsonTransport(
        http_client=RecordingJsonHttpClient(contents),
        max_bytes=4096,
    )

    with pytest.raises(ValueError):
        transport.get_json("https://api.example.test/releases")


def test_runtime_json_transport_enforces_its_own_response_limit(
    plugin_core_module,
):
    transport = plugin_core_module.SafeReleaseJsonTransport(
        http_client=RecordingJsonHttpClient(b'{"value":"too large"}'),
        max_bytes=8,
    )

    with pytest.raises(ValueError, match="size limit"):
        transport.get_json("https://api.example.test/releases")
