import re
from typing import Optional

from conftest import REPO_ROOT


WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
FULL_SHA_USE = re.compile(
    r"^\s*uses:\s+[^\s@]+@[0-9a-f]{40}\s+#\s+v\d+(?:\.\d+){1,2}\s*$"
)


def _workflow_text(name: str) -> str:
    return (WORKFLOWS_DIR / name).read_text(encoding="utf-8")


def _workflow_paths():
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(
        WORKFLOWS_DIR.glob("*.yaml")
    )


def _job_block(workflow: str, job: str, next_job: Optional[str] = None) -> str:
    start = workflow.index(f"  {job}:\n")
    end = workflow.index(f"  {next_job}:\n", start) if next_job else len(workflow)
    return workflow[start:end]


def test_external_actions_are_pinned_to_versioned_full_commit_shas():
    uses_lines = []
    for workflow_path in _workflow_paths():
        uses_lines.extend(
            (workflow_path, line)
            for line in workflow_path.read_text(encoding="utf-8").splitlines()
            if line.lstrip().startswith("uses:")
            and not line.split("uses:", 1)[1].lstrip().startswith("./")
        )

    assert uses_lines
    unpinned = [
        f"{path.relative_to(REPO_ROOT)}: {line.strip()}"
        for path, line in uses_lines
        if not FULL_SHA_USE.fullmatch(line)
    ]
    assert not unpinned, "Unpinned action references:\n" + "\n".join(unpinned)


def test_plugin_generation_pr_job_is_read_only_and_checks_freshness():
    workflow = _workflow_text("generate_plugin.yml")
    verify = _job_block(workflow, "verify", "generate")

    assert "permissions:\n  contents: read\n" in workflow
    assert "if: github.event_name == 'pull_request'" in verify
    assert "permissions:\n      contents: read\n" in verify
    assert "persist-credentials: false" in verify
    assert "python .github/scripts/generate_plugin.py" in verify
    assert "git diff --exit-code -- plugin.py" in verify
    assert "contents: write" not in verify
    assert "- 'plugin.py'" in workflow
    assert "- '.github/workflows/generate_plugin.yml'" in workflow


def test_plugin_generation_write_permission_is_limited_to_trusted_push_job():
    workflow = _workflow_text("generate_plugin.yml")
    generate = _job_block(workflow, "generate")

    assert "if: github.event_name == 'push' && github.ref == 'refs/heads/master'" in generate
    assert "permissions:\n      contents: write\n" in generate
    assert workflow.count("contents: write") == 1
    assert "git push" in generate


def test_pull_request_validation_does_not_persist_checkout_credentials():
    workflow = _workflow_text("validate.yml")

    assert "pull_request:" in workflow
    assert "permissions:\n  contents: read\n" in workflow
    assert "persist-credentials: false" in workflow


def test_workflows_do_not_grant_write_permissions_by_default():
    for workflow_path in _workflow_paths():
        workflow = workflow_path.read_text(encoding="utf-8")
        if "\npermissions:\n" not in workflow:
            continue

        top_level_permissions = workflow.split("\npermissions:\n", 1)[1].split(
            "\njobs:\n", 1
        )[0]
        assert "write" not in top_level_permissions, workflow_path.relative_to(REPO_ROOT)


def test_release_publish_job_can_finalize_the_release_pull_request():
    workflow = _workflow_text("release-please.yml")
    publish = _job_block(workflow, "publish-release")

    assert "permissions:\n      contents: write\n      pull-requests: write\n" in publish


def test_weekly_cleanup_shares_temp_tombstone_requests_with_the_index_update():
    workflow = _workflow_text("scan_plugins.yml")
    request_path = '${{ runner.temp }}/release-tombstone-requests.json'

    assert workflow.count(f'"{request_path}"') == 2
    assert "--tombstone-requests-output" in workflow
    assert workflow.count("--tombstone-requests\n") == 1
    assert "--report-only" not in workflow
    cleanup_position = workflow.index("--tombstone-requests-output")
    update_position = workflow.index(
        "python .github/scripts/generate_release_index.py --update"
    )
    assert cleanup_position < update_position


def test_weekly_release_index_update_reports_transient_failures_to_validation():
    workflow = _workflow_text("scan_plugins.yml")
    report_path = '${{ runner.temp }}/release-index-report.json'

    assert f'--report-output "{report_path}"' in workflow
    update_position = workflow.index(
        "python .github/scripts/generate_release_index.py --update"
    )
    validate_position = workflow.index("validate_plugins.py")
    assert update_position < validate_position
    validate_block = _job_block(workflow, "scan-and-pr").split(
        "Validate registry and release-index binding", 1
    )[1]
    assert f"RELEASE_INDEX_REPORT_PATH: \"{report_path}\"" in validate_block
