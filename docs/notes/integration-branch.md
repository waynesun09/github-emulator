# Integration branch: ci-self-contained

`ci-self-contained` is the one ref a consuming CI job pins. The `integration` CI job writes the live table into each run's summary.

On every push, CI runs:
- the emulator's full test suite, in 8 shards;
- the smoke test (an org, a repo, a git push, a dispatch, a 2-job run with job outputs);
- the capability probe on the Python runner and on the real `actions/runner`, over plain HTTP and over https on `ghemu.localhost`;
- the composite action in two shapes, `fast` and `fullsend`;
- the hygiene workflow: pre-commit on the change, a full-history secret scan, dependency audits and the frontend checks.

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
| 20 | fix-runner-job-logs | f9b957b | Upstream runner: logs and timeline go to the token's job, not the plan's; created logs carry a path; timeline updates are answered as `{count, value}` |
| 21 | fix-real-runner-gh-host | 8d50386 | Upstream-runner steps get `GH_HOST`, and the step's own `GH_TOKEN` is mirrored into `GH_ENTERPRISE_TOKEN`, so `gh` reaches the emulator |
| 22 | fix-index-outside-write-lock | fb63d3e | Indexing a pushed repo reads the tree before taking SQLite's write lock (a large push made other writes 503) |
| 23 | bt-f5-e2e (one commit) | 9234221 | Python runner runs bash steps with the `bash` on PATH, as actions/runner does (bt-f5) |
| 24 | fix-reusable-caller-needs | bb982e9 | A job can need a reusable-workflow caller and read `needs.<caller>.outputs` and `.result` (stacked on 5) |
| 25 | fix-emulator-hosted-actions | 1d30e6f | `uses: owner/repo@ref` resolves against a repository on the emulator first (as GHES), archive built with `git archive`; upstream otherwise |
| 26 | fix-job-token-verification | d7f5440 | Job-server routes authenticate the job token through `validate_job_token` (signature, issuer, lifetime) and answer 401 when it fails |
| 27 | chore-repo-hygiene | 58335de | pre-commit (ruff, gitleaks, shellcheck, actionlint, zizmor, frontend eslint/prettier), `.gitleaks.toml`, committed `uv.lock`, hygiene CI (hooks on the change, full-history secrets, pip-audit, npm audit, frontend checks), Dependabot, pinned Caddy, runner TLS verification by default |
| 28 | fix-runner-job-cancellation | 8c2545c | A real runner gets a `JobCancellation` message when its job is cancelled (for example superseded under `cancel-in-progress`); its later report keeps `cancelled`. On this branch, d27a3b7 also frees the runner from the plan-events report |
| 29 | fix-runner-orphaned-jobs | 5659aa4 | A runner whose job was deleted with its repository gets `JobCancellation`; a runner held busy that polls as Online is freed and its lost job fails (not within 2 minutes of the hand-out) |
| 30 | fix-runner-cancel-delivery | 96ba47e | Stacked on 29. A cancellation committed during a long poll is seen (each pass ends its read transaction; reads are fresh), and a late finish report of a runner's previous job no longer frees it while it runs the next one, which had hidden that job's cancellation |
| 31 | fix-token-repository-ids | d30ca31 | An installation token request narrows by `repository_ids` as well as by `repositories` (names) |
| 32 | fix-runner-lost-handout | 4e81aba | Stacked on 30 and on 12 (1073c32). Only a runner's newest message poll hands out a job (the real runner abandons a poll when its status changes, and the server kept running it); a job the runner never received (not acknowledged, not its lastMessageId) is requeued after a 5 s acknowledgement window, not failed. Broker events are logged under `github_emulator.runner_broker` |
| 33 | fix-runner-enhanced-annotations | 30bacf8 | `::notice::` renders on the real runner: the job message sets `DistributedTask.EnhancedAnnotations` (bt-f4) |
| 34 | fix-collaborator-permission-bot | 99dca56 | Stacked on 18. An App's bot with no collaborator row reports `none` (bt-f4) |
| 35 | fix-repo-visibility-sync | 3df0033 | Repository update keeps `private` and `visibility` in step (bt-f4) |
| 36 | fix-jobs-list-unstarted | a15caa9 | A run's jobs list leaves out jobs still waiting on `needs`, as GitHub does (probed by bt-f3) |

Nothing is pending. bt-f4-suite-timing is measurement only.

## Not included on purpose

- bt-f3-dns-probe is a throwaway probe.
- bt-f5-e2e is a consumer branch, not an emulator fix.
