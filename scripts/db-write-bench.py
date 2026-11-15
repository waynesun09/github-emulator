"""Concurrent write load against a running emulator, for comparing database settings.

Seeds REPOS repositories, each with a workflow that runs on issues (a shim
calling a reusable workflow of 12 jobs, the shape of Fullsend's), then runs
WORKERS clients for SECONDS. Each client loops: open an issue (which
creates a run of 13 jobs), comment on it, label it, list issues, read the
repository. Prints throughput, latency percentiles and status counts as one
JSON line.

    python scripts/db-write-bench.py http://127.0.0.1:8123 [workers] [seconds] [repos]
"""

import asyncio
import base64
import json
import statistics
import sys
import time
import uuid

import httpx

TOKEN = "ghp_admin_default_token"
SHIM = """name: shim
on:
  issues:
    types: [opened, labeled]
  issue_comment:
    types: [created]
jobs:
  dispatch:
    uses: ./.github/workflows/dispatch.yml
    with:
      event_action: ${{ github.event.action }}
"""


def dispatch_yaml(n=12):
    jobs = ["""  route:
    runs-on: ubuntu-latest
    steps:
      - run: echo route
"""]
    for i in range(n):
        jobs.append(f"""  stage{i}:
    needs: [route]
    runs-on: ubuntu-latest
    concurrency:
      group: s{i}-${{{{ github.event.issue.number }}}}
      cancel-in-progress: true
    steps:
      - run: echo {i}
""")
    return "name: dispatch\non:\n  workflow_call:\n    inputs:\n      event_action: {type: string}\njobs:\n" + "".join(jobs)


async def seed(client, repos):
    names = []
    tag = uuid.uuid4().hex[:6]
    for r in range(repos):
        name = f"bench-{tag}-{r}"
        resp = await client.post("/api/v3/user/repos", json={"name": name, "auto_init": True})
        resp.raise_for_status()
        owner = resp.json()["owner"]["login"]
        for path, text in ((".github/workflows/shim.yml", SHIM), (".github/workflows/dispatch.yml", dispatch_yaml())):
            put = await client.put(f"/api/v3/repos/{owner}/{name}/contents/{path}",
                                   json={"message": "wf", "content": base64.b64encode(text.encode()).decode()})
            put.raise_for_status()
        names.append(f"{owner}/{name}")
    return names


async def worker(client, repo, deadline, samples, statuses):
    async def call(kind, method, url, **kw):
        start = time.monotonic()
        try:
            resp = await client.request(method, url, **kw)
            code = resp.status_code
        except httpx.HTTPError as exc:
            resp, code = None, type(exc).__name__
        samples.setdefault(kind, []).append(time.monotonic() - start)
        statuses[str(code)] = statuses.get(str(code), 0) + 1
        return resp

    while time.monotonic() < deadline:
        resp = await call("issue", "POST", f"/api/v3/repos/{repo}/issues", json={"title": "bench"})
        if resp is None or resp.status_code != 201:
            continue
        number = resp.json()["number"]
        await call("comment", "POST", f"/api/v3/repos/{repo}/issues/{number}/comments", json={"body": "hi"})
        await call("label", "POST", f"/api/v3/repos/{repo}/issues/{number}/labels", json={"labels": ["bench"]})
        await call("list", "GET", f"/api/v3/repos/{repo}/issues?per_page=30")
        await call("repo", "GET", f"/api/v3/repos/{repo}")


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


async def main():
    base = sys.argv[1]
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 60
    repos = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    headers = {"Authorization": f"token {TOKEN}"}
    limits = httpx.Limits(max_connections=workers + 4)
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=120, limits=limits) as client:
        names = await seed(client, repos)
        samples, statuses = {}, {}
        start = time.monotonic()
        deadline = start + seconds
        await asyncio.gather(*[worker(client, names[i % len(names)], deadline, samples, statuses) for i in range(workers)])
        elapsed = time.monotonic() - start
    total = sum(len(v) for v in samples.values())
    print(json.dumps({
        "workers": workers, "seconds": round(elapsed, 1), "requests": total,
        "req_per_s": round(total / elapsed, 1),
        "issues_per_s": round(len(samples.get("issue", [])) / elapsed, 2),
        "statuses": statuses,
        "ms": {k: {"p50": round(statistics.median(v) * 1000), "p95": round(pct(v, 0.95) * 1000),
                   "max": round(max(v) * 1000)} for k, v in samples.items()},
    }))


asyncio.run(main())
