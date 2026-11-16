"""Binary content behind a text-like name is not indexed as text.

PostgreSQL refuses a NUL byte in a text column, so indexing such a file
aborted the post-push work (seen in the PostgreSQL behaviour run). git takes a
NUL in the first 8000 bytes as binary, and GitHub's code search skips binary
files; the index now does the same, on either database.
"""

import base64

import pytest
from sqlalchemy import select

from app.models.repository import Repository
from app.models.search_index import FileContent
from app.services import index_service
from tests.conftest import API, auth_headers


@pytest.mark.asyncio
async def test_a_file_with_a_nul_byte_is_indexed_without_content(client, db_session, test_token):
    h = auth_headers(test_token)
    resp = await client.post(f"{API}/user/repos", json={"name": "bin", "auto_init": True}, headers=h)
    assert resp.status_code == 201, resp.text
    blob = b"looks like text\x00but is not\n"
    resp = await client.put(
        f"{API}/repos/testuser/bin/contents/data.txt", headers=h,
        json={"message": "add", "content": base64.b64encode(blob).decode(), "branch": "main"},
    )
    assert resp.status_code == 201, resp.text
    repo = (await db_session.execute(select(Repository).where(Repository.full_name == "testuser/bin"))).scalar_one()

    await index_service.index_repository(db_session, repo)

    rows = {
        r.file_path: r for r in (await db_session.execute(
            select(FileContent).where(FileContent.repo_id == repo.id)
        )).scalars().all()
    }
    assert rows["data.txt"].content is None
    assert rows["data.txt"].size == len(blob)
    assert rows["README.md"].content
