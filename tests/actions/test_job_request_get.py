"""GET of a job request, as the runner reads its previous job.

A job message can reach a runner while its worker for the previous job is
still exiting. The runner then reads the previous request
(JobDispatcher.EnsureDispatchFinished) before it starts the new job:

- 200 with `result`: it stops the old worker and runs the new job.
- 404 with typeKey TaskAgentJobNotFoundException: the same.
- Anything else: it rethrows and drops the new job, which the broker later
  fails as idle-while-holding.

The broker also hands no new job to a poll that says status=Busy: the runner
polls Busy until its previous worker has exited, then polls Online.
"""

import json

import pytest
from sqlalchemy import select, update

from app.api import actions_distributed_task as dt
from app.models.actions import WorkflowJob
from tests.actions.test_runner_broker_races import _ack, _runner, _take_job
from tests.actions.test_runner_lost_handout import _poll, _workflow


@pytest.fixture(autouse=True)
def _fast_broker(monkeypatch):
    monkeypatch.setattr(dt, "_POLL_SECONDS", 0.5)
    monkeypatch.setattr(dt, "_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(dt, "_ACK_WINDOW", dt.timedelta(seconds=0.2))
    dt._reset_broker_state()
    yield
    dt._reset_broker_state()


def _get(client, headers, job_id, prefix=""):
    return client.get(f"{prefix}/_apis/distributedtask/pools/1/jobrequests/{job_id}", headers=headers)


def _runner_runs_next_job(resp) -> bool:
    """EnsureDispatchFinished's decision, from the GET's answer."""
    if resp.status_code == 200:
        return resp.json().get("result") is not None
    if resp.status_code == 404:
        return resp.json().get("typeKey") == "TaskAgentJobNotFoundException"
    return False


def _report(client, headers, job_id, result="succeeded"):
    return client.patch(
        f"/_apis/distributedtask/pools/1/jobrequests/{job_id}",
        json={"result": result}, headers=headers,
    )


@pytest.mark.asyncio
async def test_a_second_job_delivered_while_the_first_worker_is_still_exiting_runs(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=2)
    _runner_id, headers, session_id = await _runner(client, test_token)
    first = await _take_job(client, headers, session_id)
    assert (await _report(client, headers, first)).status_code == 200

    # A poll with no status (the runner's state unknown to the broker) gets
    # the next job while the first job's worker may still be exiting.
    message = (await _poll(client, headers, session_id, first, "")).json()
    second = int(json.loads(message["body"])["requestId"])
    assert second != first

    # Before starting the second job, the runner reads the first one.
    previous = await _get(client, headers, first, "/enterprises/acme")
    assert previous.status_code == 200
    assert previous.json()["result"] == "succeeded"
    assert previous.json()["finishTime"]
    assert _runner_runs_next_job(previous)

    assert (await _ack(client, headers, session_id, second)).status_code == 200
    assert (await _report(client, headers, second)).status_code == 200
    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == second))).scalar_one()
    assert (job.status, job.conclusion) == ("completed", "success")


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "/testuser/broker-repo", "/enterprises/acme"])
async def test_every_route_form_answers(client, db_session, test_user, test_token, prefix):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    resp = await _get(client, headers, job_id, prefix)
    assert resp.status_code == 200
    assert resp.json()["requestId"] == job_id


@pytest.mark.asyncio
async def test_a_running_job_has_no_result(client, db_session, test_user, test_token):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    body = (await _get(client, headers, job_id)).json()
    assert "result" not in body and "finishTime" not in body
    assert body["jobId"] == dt._workflow_guid("job", job_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conclusion", "task_result"),
    [("success", "succeeded"), ("failure", "failed"), ("cancelled", "canceled"), ("skipped", "skipped")],
)
async def test_a_finished_job_reports_its_task_result(
    client, db_session, test_user, test_token, conclusion, task_result
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    finished = dt.datetime(2026, 11, 20, 19, 14, 43, tzinfo=dt.timezone.utc)
    await db_session.execute(update(WorkflowJob).where(WorkflowJob.id == job_id).values(
        status="completed", conclusion=conclusion, completed_at=finished,
    ))
    await db_session.commit()
    body = (await _get(client, headers, job_id)).json()
    assert body["result"] == task_result
    assert dt.datetime.fromisoformat(body["finishTime"]).replace(tzinfo=dt.timezone.utc) == finished


@pytest.mark.asyncio
async def test_another_runners_job_is_not_found(client, db_session, test_user, test_token):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token, "r1")
    job_id = await _take_job(client, headers, session_id)
    _other_id, other_headers, _other_session = await _runner(client, test_token, "r2")
    resp = await _get(client, other_headers, job_id)
    assert resp.status_code == 404
    assert resp.json()["typeKey"] == "TaskAgentJobNotFoundException"
    # The runner treats that as "gone": it stops the old worker and moves on.
    assert _runner_runs_next_job(resp)


@pytest.mark.asyncio
async def test_a_missing_job_is_not_found(client, db_session, test_user, test_token):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, _session_id = await _runner(client, test_token)
    resp = await _get(client, headers, 999999)
    assert resp.status_code == 404
    assert resp.json()["typeKey"] == "TaskAgentJobNotFoundException"


@pytest.mark.asyncio
async def test_a_request_without_a_runner_token_is_refused(client, db_session, test_user, test_token):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    resp = await _get(client, {}, job_id)
    assert resp.status_code in (401, 403)


def _job_token(message: dict) -> tuple[str, str, str]:
    body = json.loads(message["body"])
    return body["plan"]["planId"], body["jobId"], body["variables"]["system.github.token"]["Value"]


def _completed_event(plan_id, job_guid, token, client, result, outputs):
    return client.post(
        f"/_apis/distributedtask/hubs/Actions/plans/{plan_id}/events",
        json={"name": "JobCompleted", "jobId": job_guid, "requestId": 1,
              "result": result, "outputs": outputs},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_a_completed_job_keeps_its_first_conclusion_and_outputs(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    message = (await _poll(client, headers, session_id, 0)).json()
    job_id = int(json.loads(message["body"])["requestId"])
    assert (await _ack(client, headers, session_id, job_id)).status_code == 200
    plan_id, job_guid, token = _job_token(message)

    first = await _completed_event(plan_id, job_guid, token, client, "succeeded", {"stage": {"value": "first"}})
    assert first.status_code == 200, first.text

    async def stored():
        db_session.expire_all()
        job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == job_id))).scalar_one()
        return job.status, job.conclusion, job.outputs

    recorded = await stored()
    assert recorded == ("completed", "success", {"stage": "first"})

    # Later end reports, on every route that reports one, change nothing.
    assert (await _report(client, headers, job_id, "canceled")).status_code in (200, 404)
    assert await stored() == recorded
    later = await _completed_event(plan_id, job_guid, token, client, "failed", {"stage": {"value": "second"}})
    assert later.status_code == 200
    assert await stored() == recorded
    complete = await client.post(
        f"/_apis/distributedtask/jobs/{job_id}/complete", json={"result": "Failed"}, headers=headers,
    )
    assert complete.status_code in (200, 204, 404)
    assert await stored() == recorded
    # And the runner reading the request back sees the first result.
    assert (await _get(client, headers, job_id)).json()["result"] == "succeeded"


@pytest.mark.asyncio
async def test_a_busy_poll_gets_no_job_and_the_next_online_poll_does(
    client, db_session, test_user, test_token
):
    await _workflow(client, db_session, test_user, test_token, jobs=2)
    _runner_id, headers, session_id = await _runner(client, test_token)
    first = await _take_job(client, headers, session_id)
    assert (await _report(client, headers, first)).status_code == 200

    # The first job's end is recorded, but its worker has not exited.
    busy = await _poll(client, headers, session_id, first, "Busy")
    assert busy.status_code == 204
    db_session.expire_all()
    queued = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id != first)
    )).scalar_one()
    assert (queued.status, queued.runner_id) == ("queued", None)

    online = await _poll(client, headers, session_id, first, "Online")
    assert online.status_code == 200
    second = int(json.loads(online.json()["body"])["requestId"])
    assert second == queued.id
    assert (await _ack(client, headers, session_id, second)).status_code == 200
    assert (await _report(client, headers, second)).status_code == 200
    db_session.expire_all()
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == second))).scalar_one()
    assert (job.status, job.conclusion) == ("completed", "success")
    assert (await _get(client, headers, first)).json()["result"] == "succeeded"


@pytest.mark.asyncio
async def test_a_busy_poll_still_gets_its_jobs_cancellation(client, db_session, test_user, test_token):
    await _workflow(client, db_session, test_user, test_token, jobs=1)
    _runner_id, headers, session_id = await _runner(client, test_token)
    job_id = await _take_job(client, headers, session_id)
    await db_session.execute(
        update(WorkflowJob).where(WorkflowJob.id == job_id).values(status="completed", conclusion="cancelled")
    )
    await db_session.commit()
    resp = await _poll(client, headers, session_id, job_id, "Busy")
    assert resp.status_code == 200
    assert resp.json()["messageType"] == "JobCancellation"
