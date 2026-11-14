"""Job outputs from the upstream runner reach the jobs that need them.

The runner resolves a job's ``outputs:`` itself and reports them only in the
JobCompleted plan event. The emulator neither advertised that resource nor
served it, so ``needs.<job>.outputs.<name>`` rendered empty on this path.
"""

import pytest
from sqlalchemy import select

from app.api.actions_runner_protocol import _workflow_guid
from app.models.actions import WorkflowJob
from tests.actions.test_uses_steps import _claim_job, uses_workflow  # noqa: F401

PLAN_EVENTS = "557624af-b29e-4c20-8ab0-0399d2204f3f"


def _event(job_guid: str, outputs: dict) -> dict:
    # The shape JobCompletedEvent serializes to.
    return {"name": "JobCompleted", "jobId": job_guid, "requestId": 1,
            "result": "succeeded", "outputs": outputs}


async def _raise(client, body, token, event):
    return await client.post(
        f"/_apis/distributedtask/hubs/Actions/plans/{body['plan']['planId']}/events",
        json=event, headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_the_runner_can_find_the_plan_events_resource(client):
    resp = await client.get("/_apis/connectionData")
    assert resp.status_code == 200
    definitions = resp.json()["locationServiceData"]["serviceDefinitions"]
    [events] = [d for d in definitions if d["identifier"] == PLAN_EVENTS]
    assert events["relativePath"].endswith("/plans/{planId}/events")


@pytest.mark.asyncio
async def test_job_completed_outputs_are_kept_and_secrets_are_not(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    resp = await _raise(client, body, token, _event(body["jobId"], {
        "stage": {"value": "triage", "isSecret": False},
        "payload": {"value": '{"a":1}'},
        "hidden": {"value": "s3cr3t", "isSecret": True},
    }))
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob))).scalars().first()
    assert job.outputs == {"stage": "triage", "payload": '{"a":1}'}
    # From plan version 8 the listener no longer finishes the request, so
    # this event completes the job.
    assert (job.status, job.conclusion) == ("completed", "success")


@pytest.mark.asyncio
async def test_the_plan_asks_the_worker_for_the_completed_event(
    client, uses_workflow, test_token  # noqa: F811
):
    """PlanUtil.GetFeatures: JobCompletedPlanEvent needs plan version 8."""
    body = await _claim_job(client, test_token)
    assert body["plan"]["version"] >= 8


@pytest.mark.asyncio
async def test_a_failed_job_completes_as_failed(
    client, db_session, uses_workflow, test_token  # noqa: F811
):
    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    event = {**_event(body["jobId"], {}), "result": "failed"}
    assert (await _raise(client, body, token, event)).status_code == 200
    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob))).scalars().first()
    assert (job.status, job.conclusion) == ("completed", "failure")


@pytest.mark.asyncio
async def test_an_event_for_another_job_is_refused(
    client, uses_workflow, test_token  # noqa: F811
):
    body = await _claim_job(client, test_token)
    token = body["variables"]["system.github.token"]["Value"]
    resp = await _raise(client, body, token, _event(_workflow_guid("job", 999999), {
        "stage": {"value": "forged"},
    }))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_plan_events_need_the_job_token(client, uses_workflow, test_token):  # noqa: F811
    body = await _claim_job(client, test_token)
    resp = await client.post(
        f"/_apis/distributedtask/hubs/Actions/plans/{body['plan']['planId']}/events",
        json=_event(body["jobId"], {}),
    )
    assert resp.status_code == 401
