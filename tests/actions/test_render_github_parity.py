"""Server-side rendering of run:, with: and env: against what GitHub writes.

Compared side by side with github.com (conformance recheck):
- a false result is written "false", not "";
- ref_name, ref_type, ref_protected, workflow_sha, repository_visibility,
  repositoryUrl and retention_days have values;
- values only the runner knows (github.workspace, github.job, job.status ...)
  are left for the runner instead of rendering empty.
"""

from app.services.workflow_expressions import ref_facts, render_expressions
from app.services.workflow_service import build_steps_data, static_github_facts

CONTEXT = {
    "github": {
        "event_name": "push",
        "ref": "refs/heads/main",
        "repository": "o/r",
        "event": {"pull_request": {"merged": False}},
        **static_github_facts("refs/heads/main", "abc123", "o/r", False),
    },
    "inputs": {"flag": False, "name": ""},
}


def test_a_false_result_is_written_false():
    assert render_expressions("${{ github.event_name == 'pull_request_target' }}", CONTEXT) == "false"
    assert render_expressions("${{ github.event_name == 'push' }}", CONTEXT) == "true"
    assert render_expressions("${{ github.event.pull_request.merged }}", CONTEXT) == "false"
    assert render_expressions("${{ inputs.flag }}", CONTEXT) == "false"


def test_or_takes_the_first_truthy_alternative_else_the_last():
    assert render_expressions("${{ inputs.name || github.repository }}", CONTEXT) == "o/r"
    assert render_expressions("${{ inputs.name || inputs.flag }}", CONTEXT) == "false"


def test_ref_and_repository_facts_have_values():
    rendered = render_expressions(
        "${{ github.ref_name }} ${{ github.ref_type }} ${{ github.ref_protected }} "
        "${{ github.workflow_sha }} ${{ github.repository_visibility }} ${{ github.retention_days }}",
        CONTEXT,
    )
    assert rendered == "main branch false abc123 public 90"
    assert render_expressions("${{ github.repositoryUrl }}", CONTEXT).endswith("/o/r.git")


def test_ref_facts():
    assert ref_facts("refs/tags/v1.2") == {"ref_name": "v1.2", "ref_type": "tag"}
    assert ref_facts("refs/pull/7/merge") == {"ref_name": "7/merge", "ref_type": "branch"}


def test_values_only_the_runner_knows_are_left_for_it():
    steps = build_steps_data(
        [{
            "run": "cd ${{ github.workspace }} && echo ${{ github.job }} ${{ job.status }} ${{ github.repository }}",
            "env": {"WHERE": "${{ github.event_path }}", "BOTH": "${{ github.action == 'x' && github.ref_name }}"},
            "with": {"path": "${{ github.workspace }}/out"},
        }],
        {}, CONTEXT,
    )
    step = steps[0]
    assert step["run"] == "cd ${{ github.workspace }} && echo ${{ github.job }} ${{ job.status }} o/r"
    assert step["env"]["WHERE"] == "${{ github.event_path }}"
    # The server binds what it knows; the runner finishes the rest.
    assert step["env"]["BOTH"] == "${{ github.action == 'x' && 'main' }}"
    assert step["with"]["path"] == "${{ github.workspace }}/out"


def test_a_server_only_value_still_renders_runner_values_empty():
    # Outside a step (a concurrency group, say) nothing re-evaluates, so the
    # old rendering stands.
    assert render_expressions("${{ github.workspace }}", CONTEXT) == ""
