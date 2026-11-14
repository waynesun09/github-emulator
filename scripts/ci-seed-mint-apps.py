#!/usr/bin/env python3
"""Seed the GitHub Apps a fullsend standalone mint needs on a github-emulator.

For each role it creates one App on the emulator (the emulator generates the
App's private key), installs it on the organisation for all repositories,
and writes the key to PEM_DIR/<role>.pem, the layout fullsend's
FilesystemPEMAccessor reads. It prints the mint's ROLE_APP_IDS JSON on stdout.

No secret leaves the VM: the keys are made by the emulator for this run and
die with it. Idempotent: an existing App or installation is reused.

Usage: seed-mint-apps.py BASE_URL ADMIN_TOKEN ORG PEM_DIR [ROLE ...]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# The fullsend mint's roles; fix shares coder's App and key (PemSecretRole).
DEFAULT_ROLES = ["fullsend", "triage", "coder", "review", "retro", "prioritize", "scribe", "e2e"]
# Installation ceiling. The mint narrows each token to its role's permission
# set, so the App itself is granted the union.
APP_PERMISSIONS = {
    "actions": "write", "actions_variables": "write", "checks": "read", "contents": "write",
    "issues": "write", "metadata": "read", "organization_projects": "write", "packages": "read",
    "pull_requests": "write", "workflows": "write", "administration": "write", "secrets": "write",
    "variables": "write", "members": "read",
}
APP_ID_BASE = 910000


def call(base: str, token: str, method: str, path: str, body: dict | None = None, prefix: str = "/api/v3") -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{prefix}{path}", data=data, method=method,
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "seed-mint-apps"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw.decode(errors="replace")


def must(status: int, payload: object, ok: tuple[int, ...], what: str) -> object:
    if status not in ok:
        sys.exit(f"{what}: HTTP {status} {payload}")
    return payload


def main() -> None:
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    base, token, org, pem_dir = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3], sys.argv[4]
    roles = sys.argv[5:] or DEFAULT_ROLES
    os.makedirs(pem_dir, mode=0o700, exist_ok=True)

    status, payload = call(base, token, "GET", f"/orgs/{org}")
    if status == 404:
        must(*call(base, token, "POST", "/admin/organizations", {"login": org, "admin": "admin"}), (201,), f"create org {org}")
    else:
        must(status, payload, (200,), f"get org {org}")

    role_app_ids: dict[str, str] = {}
    for index, role in enumerate(roles, start=1):
        app_id = str(APP_ID_BASE + index)
        slug = f"{org}-{role}"
        status, app = call(base, token, "GET", f"/admin/apps/{app_id}")
        if status == 404:
            app = must(*call(base, token, "POST", "/admin/apps", {
                "app_id": app_id, "name": f"{org} {role}", "slug": slug, "permissions": APP_PERMISSIONS,
            }), (201,), f"create App for {role}")
        else:
            must(status, app, (200,), f"get App {app_id}")
        key = app.get("private_key") if isinstance(app, dict) else None
        if not key:
            key = must(*call(base, token, "GET", f"/admin/api/apps/{app_id}/private-key", prefix=""), (200,), f"key for {role}")["private_key"]
        must(*call(base, token, "POST", f"/admin/apps/{app_id}/installations", {
            "account_login": org, "account_type": "Organization", "repositories": [],
        }), (200, 201), f"install {role} App on {org}")
        path = os.path.join(pem_dir, f"{role}.pem")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(key)
        role_app_ids[role] = app_id
    print(json.dumps(role_app_ids, separators=(",", ":")))


if __name__ == "__main__":
    main()
