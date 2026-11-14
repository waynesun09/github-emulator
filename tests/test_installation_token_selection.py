"""A token narrowed to named repositories reports repository_selection "selected"."""

import time

import pytest
from jose import jwt

from tests.conftest import API, auth_headers


async def _org_wide_app(client, admin_token, owner, app_id):
    created = await client.post(
        f"{API}/admin/apps", headers=auth_headers(admin_token),
        json={"app_id": app_id, "name": f"Selection {app_id}", "slug": f"selection-{app_id}",
              "permissions": {"issues": "write", "metadata": "read"}},
    )
    assert created.status_code == 201
    installation = await client.post(
        f"{API}/admin/apps/{app_id}/installations", headers=auth_headers(admin_token),
        json={"account_login": owner, "account_type": "User", "repositories": []},
    )
    assert installation.status_code == 201
    now = int(time.time())
    assertion = jwt.encode({"iss": app_id, "iat": now - 10, "exp": now + 60},
                           created.json()["private_key"], algorithm="RS256")
    return installation.json()["id"], {"Authorization": f"Bearer {assertion}"}


@pytest.mark.asyncio
async def test_named_repositories_make_the_token_selected(client, admin_token, test_repo_with_init):
    owner, repo_name, _repo = test_repo_with_init
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1101")
    narrowed = await client.post(
        f"{API}/app/installations/{installation_id}/access_tokens", headers=headers,
        json={"repositories": [repo_name]},
    )
    assert narrowed.status_code == 201, narrowed.text
    assert narrowed.json()["repository_selection"] == "selected"
    assert [item["full_name"] for item in narrowed.json()["repositories"]] == [f"{owner}/{repo_name}"]


@pytest.mark.asyncio
async def test_an_unnarrowed_token_on_an_all_installation_stays_all(client, admin_token, test_repo_with_init):
    owner, _repo_name, _repo = test_repo_with_init
    installation_id, headers = await _org_wide_app(client, admin_token, owner, "1102")
    whole = await client.post(f"{API}/app/installations/{installation_id}/access_tokens", headers=headers, json={})
    assert whole.status_code == 201, whole.text
    assert whole.json()["repository_selection"] == "all"
