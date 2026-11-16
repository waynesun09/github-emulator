"""Runners that register without a name each get their own row and token.

actions/runner's config.sh posts only ``url`` and ``runner_event`` to
/actions/runner-registration; the runner's name comes later, with the agent
add. Registering several runners before any of them had added its agent
gave them all the placeholder name, each registration re-keyed the same row,
and every runner but the last got 401 on its next call.
"""

import pytest

from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_unnamed_registrations_keep_their_own_tokens(client, admin_token):
    tokens = []
    for _ in range(4):
        reg = await client.post(
            f"{API}/enterprises/breadboard/actions/runners/registration-token",
            headers=auth_headers(admin_token),
        )
        assert reg.status_code == 200, reg.text
        resp = await client.post(
            f"{API}/actions/runner-registration",
            json={"url": "http://test/enterprises/breadboard", "runner_event": "register"},
            headers={"Authorization": f"RemoteAuth {reg.json()['token']}"},
        )
        assert resp.status_code == 200, resp.text
        tokens.append(resp.json()["token"])

    for token in tokens:
        pools = await client.get(
            "/enterprises/breadboard/_apis/distributedtask/pools",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert pools.status_code == 200, pools.text


@pytest.mark.asyncio
async def test_a_named_registration_still_reuses_its_row(client, admin_token):
    ids = []
    for _ in range(2):
        reg = await client.post(
            f"{API}/enterprises/breadboard/actions/runners/registration-token",
            headers=auth_headers(admin_token),
        )
        resp = await client.post(
            f"{API}/actions/runner-registration",
            json={"registrationToken": reg.json()["token"], "agentName": "pod-runner"},
        )
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["runner"]["id"])
    assert ids[0] == ids[1]


async def _registration_token(client, path, token):
    resp = await client.post(f"{API}{path}/actions/runners/registration-token", headers=auth_headers(token))
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["token"]


async def _config_sh_register(client, reg_token, name):
    """What config.sh does: runner-registration, then the agent add."""
    resp = await client.post(
        f"{API}/actions/runner-registration",
        json={"url": "http://test/x", "runner_event": "register"},
        headers={"Authorization": f"RemoteAuth {reg_token}"},
    )
    assert resp.status_code == 200, resp.text
    credential = resp.json()["token"]
    added = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"name": name, "labels": [{"name": "self-hosted"}]},
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert added.status_code == 200, added.text
    return credential, added.json()["id"]


@pytest.mark.asyncio
async def test_one_registration_token_registers_several_runners(client, admin_token):
    reg_token = await _registration_token(client, "/enterprises/breadboard", admin_token)
    credentials = [
        (await _config_sh_register(client, reg_token, f"shared-{i}"))[0] for i in range(3)
    ]
    assert len(set(credentials)) == 3
    for credential in credentials:
        pools = await client.get(
            "/enterprises/breadboard/_apis/distributedtask/pools",
            headers={"Authorization": f"Bearer {credential}"},
        )
        assert pools.status_code == 200, pools.text


@pytest.mark.asyncio
async def test_a_runner_sees_and_changes_only_runners_in_its_own_scope(client, test_token):
    h = auth_headers(test_token)
    for name in ("scope-a", "scope-b"):
        resp = await client.post(f"{API}/user/repos", json={"name": name}, headers=h)
        assert resp.status_code == 201, resp.text
    a_cred, a_id = await _config_sh_register(
        client, await _registration_token(client, "/repos/testuser/scope-a", test_token), "same-name")
    b_cred, b_id = await _config_sh_register(
        client, await _registration_token(client, "/repos/testuser/scope-b", test_token), "same-name")
    a_headers = {"Authorization": f"Bearer {a_cred}"}

    found = await client.get(
        "/_apis/distributedtask/pools/1/agents", params={"agentName": "same-name"}, headers=a_headers)
    assert found.status_code == 200
    assert [agent["id"] for agent in found.json()["value"]] == [a_id]

    replaced = await client.put(
        f"/_apis/distributedtask/pools/1/agents/{b_id}", json={"name": "taken-over"}, headers=a_headers)
    assert replaced.status_code == 404
    removed = await client.delete(f"/_apis/distributedtask/pools/1/agents/{b_id}", headers=a_headers)
    assert removed.status_code == 404

    b_view = await client.get(
        "/_apis/distributedtask/pools/1/agents", params={"agentName": "same-name"},
        headers={"Authorization": f"Bearer {b_cred}"})
    assert [agent["id"] for agent in b_view.json()["value"]] == [b_id]
