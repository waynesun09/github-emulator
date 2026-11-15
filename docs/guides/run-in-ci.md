# Run in CI

Run GitHub Actions workflows against this emulator inside your CI job with no containers or extra infrastructure. Choose a setup below to start.

## Choose a setup

Pick the setup that fits your workflow. A runner is the program that executes the jobs. Both setups run in this repository's CI on every push.

| Setup | Runner | Use it for | Measured on ubuntu-latest |
|---|---|---|---|
| fast | the built-in Python runner, plain HTTP | `run:` steps, checkout, local composite actions, OIDC | action 10 s, 2-job smoke 4 s, whole job 18 s |
| GitHub-faithful | the real actions/runner 2.338.2, https on `ghemu.localhost` | anything else: actions from github.com, artifacts v4+, `gh` inside a job, job outputs between jobs | action 36 s, smoke 8 s, whole job 49 s |

## Add the step

The composite action [.github/actions/emulator](../../.github/actions/emulator/action.yml) starts the emulator and its runner(s) as plain processes inside the current job (SQLite, no containers) and waits until the API answers. Jobs the emulator schedules run on the same machine, as the job's user. Outputs: `url`, `api-url`, `token` (the seeded site-admin token), `ca-file`, `ca-bundle`, and `state-dir` (logs).

For the GitHub-faithful setup, pin the action by full commit SHA (shown here as `<sha>`):

```yaml
- id: emu
  uses: jctanner/github-emulator/.github/actions/emulator@<sha>
  with:
    runner-kind: upstream
    port: "443"
    tls: "true"
    system-trust: "true"
    host: ghemu.localhost
    localhost-dns: "true"
    runners: "2"
- name: Use it
  env:
    EMU: ${{ steps.emu.outputs.url }}
    EMU_CA: ${{ steps.emu.outputs.ca-file }}
  run: scripts/ci-workflow-smoke.sh
```

The fast setup is the same step with no `with:` inputs other than `runners`. Replace the second step with your own; `ci-workflow-smoke.sh` is this repository's example.

## Check it worked

Run [scripts/ci-workflow-smoke.sh](../../scripts/ci-workflow-smoke.sh), which creates an org and a repo, pushes a workflow over git, dispatches it, and waits for the 2-job run:

```
== create org smoke-org and repo smoke-org/smoke-repo
201
201
== push the workflow over git
== dispatch
204
== wait for the run
run 1: success
  job produce: success
  job consume: success
PASS: dispatch input crossed a job output into the consume job's log
```

## Run it locally

Run the Python runner on any free port with [scripts/ci-stack.sh](../../scripts/ci-stack.sh) (`CI_STACK_PYTHON` is a virtualenv with the locked dependencies installed, e.g. `uv sync --frozen`'s `.venv`):

```
$ CI_STACK_PORT=18304 CI_STACK_PYTHON=.venv/bin/python scripts/ci-stack.sh start
emulator http://127.0.0.1:18304 (pid 12345), 1 python runner(s)

$ EMU=http://127.0.0.1:18304 scripts/ci-workflow-smoke.sh
(the same output as above)

$ scripts/ci-stack.sh stop
```

`stop` prints nothing. The GitHub-faithful setup needs Linux (it installs a CA and changes DNS with sudo), so use it in CI.

## Troubleshooting

| Error text | Cause | Fix |
|---|---|---|
| `Connection refused (127.0.0.1:80)` (runner registration) | actions/runner drops the port from some URLs and dials the default one | Serve on `port: "80"`, or `"443"` with `tls: "true"` |
| `GHESNotSupportedError: @actions/artifact v2.0.0+, upload-artifact@v4+ and download-artifact@v4+ are not currently supported on GHES` | The artifact actions accept only a server host of github.com, `*.ghe.com` or `*.localhost` | Use `host: ghemu.localhost` |
| `'using: node24' is not supported` | The runner predates node24 actions | Keep `runner-version` at its default, 2.338.2, or newer |
| `::error::reusable workflow <ref> was not found on this emulator; it resolves only against repositories stored here, so push the repository that holds it at that ref first` | A `uses:` of a reusable workflow in a repository the emulator does not have | Push that repository to the emulator first |
| `::error::container: not supported by this emulator's runners; the job's steps were NOT run` | The job declares `container:` or `services:` | Run those steps without a job container |
| A run stays `queued` while the runner works on other jobs | A repository you pushed to the emulator has its own `on: push` workflows, and they run first | Turn Actions off for that repository before pushing: `PUT /api/v3/repos/{owner}/{repo}/actions/permissions` with `{"enabled": false}` |
