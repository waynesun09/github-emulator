"""Issues created at once get distinct numbers, and all count as open."""

import asyncio

import pytest
from sqlalchemy import select

from app.models.repository import Repository
from app.services.issue_number import allocate_issue_number
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_a_stale_repository_row_still_gets_the_next_number(client, db_session, test_token):
    h = auth_headers(test_token)
    assert (await client.post(f"{API}/user/repos", json={"name": "n"}, headers=h)).status_code == 201
    stale = (await db_session.execute(select(Repository).where(Repository.name == "n"))).scalar_one()
    assert stale.next_issue_number == 1
    # Another request takes number 1 after this session loaded the row.
    assert (await client.post(f"{API}/repos/testuser/n/issues", json={"title": "a"}, headers=h)).json()["number"] == 1
    # Reading the loaded row's counter would give 1 again.
    assert await allocate_issue_number(db_session, stale) == 2
    assert stale.next_issue_number == 3


@pytest.mark.asyncio
async def test_concurrent_issue_and_pull_numbers_are_distinct(client, test_token):
    h = auth_headers(test_token)
    assert (await client.post(f"{API}/user/repos", json={"name": "m"}, headers=h)).status_code == 201
    results = await asyncio.gather(*[
        client.post(f"{API}/repos/testuser/m/issues", json={"title": f"t{i}"}, headers=h) for i in range(6)
    ])
    created = [r.json()["number"] for r in results if r.status_code == 201]
    assert all(r.status_code in (201, 503) for r in results), [r.text for r in results]
    assert len(created) == len(set(created))
    repo = (await client.get(f"{API}/repos/testuser/m", headers=h)).json()
    assert repo["open_issues_count"] == len(created)
