"""An API commit is all or nothing when SQLite's writer lock is busy.

The contents API and createCommitOnBranch move the git ref, then record the
branch in the database. Under concurrent writers that second write met
"database is locked"; the request answered 503 with the commit already on
the branch, and the client's retry (old blob sha) got a conflict. Now the
write is replayed, and if the lock persists the ref goes back first.
"""

import base64

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import API, auth_headers
from tests.test_create_commit_on_branch import _commit, _input, _repo


@pytest.fixture
def locked_commits(monkeypatch):
    """Make the next ``state["fail"]`` commits that write a branch fail locked."""
    state = {"fail": 0, "armed": False}
    real_commit = AsyncSession.commit

    async def commit(self):
        if state["armed"] and state["fail"] > 0 and self.dirty | self.new:
            state["fail"] -= 1
            raise OperationalError("UPDATE", {}, Exception("database is locked"))
        return await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr("app.database_retry.settings.SQLITE_WRITE_RETRY_DELAY_MS", 0)
    return state


async def _head(client, name):
    return (await client.get(f"{API}/repos/testuser/{name}/branches/main")).json()["commit"]["sha"]


async def _put(client, token, name, sha=None, text=b"two\n"):
    body = {"message": "pr", "content": base64.b64encode(text).decode()}
    if sha:
        body["sha"] = sha
    return await client.put(f"{API}/repos/testuser/{name}/contents/behaviour/pr.txt",
                            json=body, headers=auth_headers(token))


@pytest.mark.asyncio
async def test_contents_put_replays_through_a_brief_lock(client, test_token, locked_commits):
    name, _ = await _repo(client, test_token, "lock-brief")
    created = await _put(client, test_token, name, text=b"one\n")
    assert created.status_code == 201
    sha = created.json()["content"]["sha"]

    locked_commits.update(fail=2, armed=True)
    resp = await _put(client, test_token, name, sha=sha)
    locked_commits["armed"] = False
    assert resp.status_code == 200, resp.text
    assert locked_commits["fail"] == 0
    assert await _head(client, name) == resp.json()["commit"]["sha"]


@pytest.mark.asyncio
async def test_contents_put_lock_that_persists_leaves_nothing_behind(client, test_token, locked_commits):
    name, _ = await _repo(client, test_token, "lock-held")
    created = await _put(client, test_token, name, text=b"one\n")
    sha = created.json()["content"]["sha"]
    before = await _head(client, name)

    locked_commits.update(fail=99, armed=True)
    resp = await _put(client, test_token, name, sha=sha)
    locked_commits["armed"] = False
    assert resp.status_code == 503
    assert await _head(client, name) == before  # the branch went back

    retry = await _put(client, test_token, name, sha=sha)  # the client's retry, same sha
    assert retry.status_code == 200, retry.text


@pytest.mark.asyncio
async def test_create_commit_on_branch_lock_that_persists_moves_the_ref_back(client, test_token, locked_commits):
    name, head = await _repo(client, test_token, "lock-graphql")
    locked_commits.update(fail=99, armed=True)
    first = await _commit(client, test_token, _input(name, head, additions=[("a.txt", b"a\n")]))
    locked_commits["armed"] = False
    assert first.get("errors")
    assert await _head(client, name) == head

    again = await _commit(client, test_token, _input(name, head, additions=[("a.txt", b"a\n")]))
    assert "errors" not in again, again
    assert await _head(client, name) == again["data"]["createCommitOnBranch"]["commit"]["oid"]
