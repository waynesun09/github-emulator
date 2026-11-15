"""Record branch refs after application-side Git writes.

Callers commit these updates together with their repository metadata.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.git.bare_repo import get_branches
from app.models.branch import Branch
from app.models.repository import Repository


async def record_branch(
    db: AsyncSession, repository: Repository, name: str, sha: str,
) -> None:
    """Insert or advance a branch without replacing its protection settings."""
    branch = (await db.execute(
        select(Branch).where(Branch.repo_id == repository.id, Branch.name == name)
    )).scalar_one_or_none()
    if branch is None:
        db.add(Branch(repo_id=repository.id, name=name, sha=sha))
    else:
        branch.sha = sha


async def record_disk_branches(db: AsyncSession, repository: Repository) -> None:
    """Register all actual heads after cloning a repository, including packed refs."""
    for branch in await get_branches(repository.disk_path):
        await record_branch(db, repository, branch["name"], branch["sha"])


async def record_moved_branch(
    db: AsyncSession,
    repository: Repository,
    branch: str,
    before: str | None,
    after: str,
    *,
    refresh: tuple = (),
) -> Repository:
    """Record a branch an API write has just moved on disk, as one request.

    The git ref moves first and the database second. Under concurrent
    writers SQLite refuses that second write ("database is locked"): the
    request's read transaction started before another writer committed, so
    it cannot become a writer. Answering 503 then left the commit on the
    branch, and the client's retry, holding the old blob sha, got a
    conflict instead of a success. So: end the read transaction, write in a
    fresh one, replay it on a lock, and if the lock persists put the branch
    back where it was (only if it is still at ``after``) before the 503, so
    a retry finds the repository as it was before the request.

    ``refresh`` lists other loaded objects the caller reads afterwards; a
    rollback expires them, so they are reloaded on replay. Returns the
    repository, reloaded if a replay happened.
    """
    from datetime import datetime, timezone

    from app.database_retry import commit_with_sqlite_retry
    from app.middleware.error_handler import RetryableDatabaseError

    repository_id, disk_path = repository.id, repository.disk_path
    current = repository
    await db.commit()  # nothing pending: ends the read snapshot

    async def write(reload: bool) -> None:
        nonlocal current
        if reload:
            current = await db.get(Repository, repository_id, populate_existing=True)
            for obj in refresh:
                await db.refresh(obj)
        await record_branch(db, current, branch, after)
        current.pushed_at = datetime.now(timezone.utc)

    await write(False)
    try:
        await commit_with_sqlite_retry(db, label=f"record {branch}", before_retry=lambda: write(True))
    except RetryableDatabaseError:
        await _move_ref_back(disk_path, branch, before, after)
        raise
    return current


async def _move_ref_back(disk_path: str, branch: str, before: str | None, after: str) -> None:
    """Undo an API commit's ref move, only while the ref is still at ``after``."""
    import asyncio

    args = ["update-ref", f"refs/heads/{branch}", before, after] if before else [
        "update-ref", "-d", f"refs/heads/{branch}", after]
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=disk_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
