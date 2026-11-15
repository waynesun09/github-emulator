# Bug: an API commit answers 503 after the branch already moved

Status: fixed 2026-11-15.

## Summary

The contents API (`PUT` and `DELETE /repos/{owner}/{repo}/contents/{path}`)
and GraphQL `createCommitOnBranch` move the git ref first, then record the
branch and `pushed_at` in SQLite. With many runners and clients writing at
once (Fullsend's behaviour tests: 6 workers, 12 runners), that second write
failed with `database is locked`:

- `createCommitOnBranch` returned an error, but the commit was on the branch;
- `PUT contents` answered 503, the commit was on the branch, and the
  client's retry, still holding the old blob sha, got a conflict.

The request read the repository first, so its transaction held a read
snapshot. SQLite cannot turn that into a write transaction once another
writer has committed, and it reports this at once as `database is locked`;
the busy timeout does not apply.

## Fix

`record_moved_branch` in `src/app/services/branch_service.py`, used by all
three routes:

1. End the read transaction before writing.
2. Write in a fresh transaction, replaying it on a lock
   (`commit_with_sqlite_retry`; `SQLITE_WRITE_RETRY_ATTEMPTS` is now 5).
3. If the lock persists, move the ref back (only if it is still at the new
   commit) before answering 503, so a retry finds the repository as it was.

## Evidence

- Plain `sqlite3`, WAL: connection A reads in a transaction, B commits an
  update, A's update fails with `database is locked` after 0.00 s,
  `sqlite_errorname` `SQLITE_BUSY_SNAPSHOT`, with a 5 s busy timeout.
- `tests/test_api_commit_lock_replay.py`: a brief lock is replayed through
  (200); a held lock answers 503 with the branch unchanged, and the same
  request then succeeds; `createCommitOnBranch` the same. All three fail on
  the previous code.
- Fullsend behaviour run with 6 workers and 12 runners: 12 `database is
  locked` errors, all from `createCommitOnBranch`, and one `PUT contents` 503
  followed by a failed retry.
