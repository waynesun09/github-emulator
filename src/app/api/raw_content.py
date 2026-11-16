"""Raw file content, the way an enterprise GitHub host serves it.

github.com puts raw file bytes on a separate hostname,
``raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>``. An enterprise
install has no second hostname and serves the same bytes from the appliance
itself, at ``<host>/<owner>/<repo>/raw/<ref>/<path>``. This emulator is the
second shape, so it implements that route.

It exists because tools fetch files this way rather than through the contents
API: Fullsend resolves an agent definition to a commit and then downloads the
file from a raw URL. Without this route such a tool has nowhere local to look
and reaches the public internet instead.

The response is deliberately plain bytes with ``text/plain``, like the real
thing, rather than the contents API's JSON envelope.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.api.deps import CurrentUser, DbSession, get_repo_record_or_404
from app.config import settings
from app.git.bare_repo import get_file_content
from app.services import repository_access

router = APIRouter(tags=["raw"])
logger = logging.getLogger("github_emulator.raw")
_UPSTREAM_HOST = "raw.githubusercontent.com"


async def _from_upstream(path: str) -> Response:
    """Fetch ``path`` from the real raw.githubusercontent.com.

    The name resolves to this emulator on this machine, so connect to the
    address recorded before that, and present the real name for TLS (SNI and
    certificate check) and in Host. No credentials are forwarded.
    """
    url = f"https://{settings.RAW_CONTENT_UPSTREAM_ADDR}{path}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.get(
                url, headers={"Host": _UPSTREAM_HOST},
                extensions={"sni_hostname": _UPSTREAM_HOST},
            )
    except httpx.HTTPError as exc:
        logger.warning("raw upstream fetch of %s failed: %s", path, exc)
        raise HTTPException(status_code=502, detail="Bad Gateway") from None
    logger.info("raw upstream %s -> %d", path, upstream.status_code)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/plain; charset=utf-8"),
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/{owner}/{repo}/raw/{ref}/{path:path}")
async def raw_file(
    owner: str,
    repo: str,
    ref: str,
    path: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Serve one file's bytes at a ref."""
    try:
        repository = await get_repo_record_or_404(owner, repo, db)
    except HTTPException:
        # Not one of the emulator's repositories: on the raw host, the real
        # one may have it.
        upstream_path = request.scope.get("raw_host_path")
        if upstream_path and settings.RAW_CONTENT_UPSTREAM_ADDR:
            return await _from_upstream(upstream_path)
        raise

    # This route sits outside /repos/, so the visibility check wrapped around
    # authentication does not cover it. Ask the same question here rather than
    # leaving a second door into a private repository.
    if not await repository_access.can_read(db, repository, current_user):
        raise HTTPException(status_code=404, detail="Not Found")

    if not repository.disk_path:
        raise HTTPException(status_code=404, detail="Not Found")

    # "<ref>:<path>" resolves the ref the way rev-parse does, in the same
    # process that reads the file.
    content = await get_file_content(repository.disk_path, ref, path)
    if content is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff"},
    )
