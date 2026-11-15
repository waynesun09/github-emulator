# Bug: issues created at once get the same number

Status: fixed 2026-11-15.

## Summary

Creating an issue or pull request read `next_issue_number` from the
repository row the request had loaded, then wrote `number + 1` back. Two
requests that loaded the row before either committed took the same number;
the second failed the `(repo_id, number)` unique constraint with 500. Both
also wrote `open_issues_count + 1` from the same stale value, so the count
lost one.

GraphQL `createIssue` and `createPullRequest` did not count the new issue
as open at all.

## Fix

`app.services.issue_number.allocate_issue_number` takes the number and
counts the issue as open in one `UPDATE ... RETURNING`, which the
database applies to the current row. REST issues and pulls, the issue and
pull-request services and both GraphQL mutations use it.

## Evidence

- `scripts/db-write-bench.py`-style load (6 clients, 20 s, 4 repositories,
  each client opening issues in a loop): one
  `UNIQUE constraint failed: issues.repo_id, issues.number` 500 in 288
  creates on f016e9e.
- `tests/test_issue_number_race.py`.
