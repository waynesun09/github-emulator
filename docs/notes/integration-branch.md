# Integration branch: ci-self-contained

`ci-self-contained` is the one ref a consuming CI job pins. It holds fork
`main` plus every fix branch below, cherry-picked in the order listed, and the
self-contained CI stack: `scripts/ci-stack.sh`, `.github/actions/emulator`, and
the smoke and capability-probe workflow.

On every push, CI runs:
- the emulator's full test suite, in 8 shards;
- the smoke test (an org, a repo, a git push, a dispatch, a 2-job run with
  job outputs);
- the capability probe on the Python runner and on the real `actions/runner`,
  over plain HTTP and over https on `ghemu.localhost`;
- the composite action in two shapes, `fast` and `fullsend`.

The `integration` job writes the live version of the table below, matched by
patch, into the run summary.

## Fix branches included

All are cut from `main` at c1190a9 unless a note says otherwise.

| # | Branch | Tip | What it fixes |
| --- | --- | --- | --- |
| 1 | fix-artifacts-v4 | c07f79b | Actions results service for upload/download-artifact v4+ (ADR-0004) |
| 2 | fix-runner-job-outputs | b22988b | Job outputs on the real runner: plan events, plan version 8, jobOutputs |
| 3 | fix-container-job-loud | c806af5 | `container:`/`services:` jobs fail loudly instead of running on the host |
| 4 | fix-unresolved-reusable-loud | a4412bd | An unresolved reusable-workflow call fails loudly (stacked on 3) |
| 5 | fix-skipped-caller-job-run | c5bab36 | A skipped reusable-workflow caller is one skipped job |
| 6 | fix-label-already-exists-error | bd24701 | 422 already_exists for duplicate labels and refs |
| 7 | fix-issue-create-unknown-labels | 185cf0f | Issue create and update create missing labels |
| 8 | bt-f3-mint-seed | 6506413 | Seed GitHub Apps for a standalone token mint |
| 9 | bt-f3-team-memberships | d7c4012 | Team membership endpoints |
| 10 | bt-f3-oidc-claims | 8a5556e | OIDC `repository_owner_id` and `/.well-known/jwks` |
| 11 | bt-f3-localhost-dns | 6990963 | `CI_STACK_LOCALHOST_DNS`. The status script reports d079509 as missing because its pick, 75138e2, carries different context lines; the change itself is in. |
| 12 | fix-runner-job-double-claim | 1073c32 | Atomic job claim on both runner paths; a lost job request answers with a 404 that the runner stops on |
| 13 | fix-contents-sha-precondition | 00ca315 | Contents PUT: 422 with no sha, 409 on a stale sha; of two parallel writes, one wins |
| 14 | fix-workflow-lookup-resync-per-request | 35a77aa | Workflow lookups cached by the workflows tree id, not resynced on every request |
| 15 | fix-compare-merge-base | 133699f | Compare and cross-repo pulls use the real merge base |
| 16 | fix-closed-by-pull-requests-references | a78f36d | GraphQL `closedByPullRequestsReferences` |
| 17 | fix-close-issue-state-reason | 197ee90 | GraphQL `stateReason` as an enum on close and on the issue |
| 18 | fix-collaborator-permission-none | a49e162 | An org member with no role on a public org repo has read permission, not none |
| 19 | fix-review-own-pull-request | 6cc9aa3 | The PR author's APPROVE or REQUEST_CHANGES gets 422; review create returns 200; POST submit is accepted |

Nothing is pending. Every fix branch on the fork is listed above, except bt-f4-suite-timing, which is measurement only.

## Not included on purpose

- bt-f3-dns-probe is a throwaway probe.
- bt-f5-e2e is a consumer branch, not an emulator fix.
