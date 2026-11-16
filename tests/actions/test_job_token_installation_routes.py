"""A job's GITHUB_TOKEN on the /installation routes, as on GitHub.

GitHub's GITHUB_TOKEN is an installation token for the run's repository:
GET /installation/repositories lists that repository, and DELETE
/installation/token revokes it (later API calls are 401). Clients probe the
first route to tell an installation token from a personal access token.
"""

import pytest
from sqlalchemy import select

from app.models.actions import Workflow, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.services.job_token_service import issue_job_token, validate_job_token
from tests.conftest import auth_headers

API = "/api/v3"


@pytest.fixture(autouse=True)
def _forget_revocations():
    # Each test database restarts job ids, so two tests can mint the same
    # token; a revocation must not carry over.
    from app.services import job_token_service

    job_token_service._revoked_api_tokens.clear()
    yield
    job_token_service._revoked_api_tokens.clear()


@pytest.fixture
async def job_token(client, db_session, test_user, test_token):
    resp = await client.post(f"{API}/user/repos", json={"name": "jobtok"}, headers=auth_headers(test_token))
    assert resp.status_code == 201, resp.text
    repo = (await db_session.execute(select(Repository).where(Repository.name == "jobtok"))).scalar_one()
    workflow = Workflow(repo_id=repo.id, name="W", path=".github/workflows/w.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo.id, head_sha="abc", head_branch="main",
        event="workflow_dispatch", run_number=1, actor_id=test_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    job = WorkflowJob(run_id=run.id, name="j", permissions={"contents": "read"})
    db_session.add(job)
    await db_session.commit()
    return issue_job_token(job)


@pytest.mark.asyncio
async def test_a_job_token_lists_its_own_repository(client, job_token):
    resp = await client.get(f"{API}/installation/repositories", headers={"Authorization": f"Bearer {job_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["repository_selection"] == "selected"
    assert [r["full_name"] for r in body["repositories"]] == ["testuser/jobtok"]
    assert body["total_count"] == 1


@pytest.mark.asyncio
async def test_a_job_token_revokes_itself_for_the_api_only(client, db_session, job_token):
    headers = {"Authorization": f"Bearer {job_token}"}
    resp = await client.delete(f"{API}/installation/token", headers=headers)
    assert resp.status_code == 204, resp.text

    after = await client.get(f"{API}/installation/repositories", headers=headers)
    assert after.status_code == 401, after.text
    # The runner authenticates with the same token and keeps working.
    assert await validate_job_token(db_session, job_token) is not None


@pytest.mark.asyncio
async def test_a_personal_access_token_is_still_refused(client, test_token):
    for method, path in (("get", "repositories"), ("delete", "token")):
        resp = await getattr(client, method)(f"{API}/installation/{path}", headers=auth_headers(test_token))
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_a_revoked_job_token_is_refused_even_on_a_public_repository(client, job_token):
    headers = {"Authorization": f"Bearer {job_token}"}
    assert (await client.get(f"{API}/repos/testuser/jobtok", headers=headers)).status_code == 200
    assert (await client.delete(f"{API}/installation/token", headers=headers)).status_code == 204
    after = await client.get(f"{API}/repos/testuser/jobtok", headers=headers)
    assert after.status_code == 401, after.text
    assert after.json()["message"] == "Bad credentials"
