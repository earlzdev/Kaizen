# =============================================================================
# deploy tool — tools/deploy/tool.py
# =============================================================================
# WHAT: Lets Кая open (and, when told to, merge) the PR that ships Kaizen's own
#       `main` branch into `deploy` — the branch a self-hosted GitHub Actions
#       runner can watch to redeploy the prod stack. Wire up your own
#       workflow file for that runner; none ships in this repo.
#
# WHY the GitHub REST API and not local git: this service's image is a `COPY`
#       build (deploy/docker/Dockerfile) with no `.git` directory at runtime —
#       there is nothing to `git push` from inside the container. Both
#       `main` and `deploy` already live on the remote, so opening/merging the
#       PR is pure GitHub API traffic, authenticated with a fine-grained PAT
#       scoped to this one repo (GH_DEPLOY_TOKEN, contents+PRs write only).
#
# WHY merge is a separate action instead of open_pr auto-merging: the owner
#       wants to review every deploy PR by default — Кая only calls
#       action="merge_pr" when told to in that conversation, never on her own.
#
# HOW: exports `TOOL`; the loader registers it. `action=open_pr` is idempotent
#      (returns the existing PR if one is already open, creates the `deploy`
#      branch from `main` on first-ever use).
# =============================================================================

import httpx

from tools.config import settings
from tools.contract import ToolDef

_API = "https://api.github.com"


class DeployError(Exception):
    """A GitHub API failure with a message already safe to show the owner."""


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    return body.get("message", resp.text) if isinstance(body, dict) else resp.text


def _raise_friendly(resp: httpx.Response, *, on_422: str | None = None, on_conflict: str | None = None) -> None:
    """Turn the common GitHub API failure codes into an owner-readable
    message instead of the raw httpx exception text, then re-raise as
    DeployError. No-op if the response is a success."""
    if resp.status_code < 400:
        return
    if resp.status_code == 403 and (
        resp.headers.get("x-ratelimit-remaining") == "0" or resp.headers.get("retry-after")
    ):
        raise DeployError("GitHub API rate limit hit — try again shortly.")
    if resp.status_code in (401, 403):
        raise DeployError("GH_DEPLOY_TOKEN is invalid, expired, or lacks contents/pull_requests write access.")
    if resp.status_code == 422 and on_422:
        raise DeployError(on_422)
    if resp.status_code in (405, 409) and on_conflict:
        raise DeployError(f"{on_conflict} — {_detail(resp)}")
    raise DeployError(f"GitHub API error {resp.status_code}: {_detail(resp)}")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.gh_deploy_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _ref_sha(client: httpx.AsyncClient, branch: str) -> str | None:
    resp = await client.get(f"/repos/{settings.gh_repo}/git/ref/heads/{branch}", headers=_headers())
    if resp.status_code == 404:
        return None
    _raise_friendly(resp)
    return resp.json()["object"]["sha"]


async def _find_open_pr(client: httpx.AsyncClient) -> dict | None:
    owner = settings.gh_repo.split("/")[0]
    resp = await client.get(
        f"/repos/{settings.gh_repo}/pulls",
        headers=_headers(),
        params={
            "state": "open",
            "head": f"{owner}:{settings.gh_main_branch}",
            "base": settings.gh_deploy_branch,
        },
    )
    _raise_friendly(resp)
    prs = resp.json()
    return prs[0] if prs else None


async def _open_pr() -> str:
    async with httpx.AsyncClient(base_url=_API, timeout=20) as client:
        main_sha = await _ref_sha(client, settings.gh_main_branch)
        if main_sha is None:
            return (
                f"Error: branch '{settings.gh_main_branch}' not found on {settings.gh_repo} "
                "— check GH_REPO is correct and GH_DEPLOY_TOKEN is scoped to it (GitHub "
                "returns the same 404 for a wrong branch name and for a repo/branch the "
                "token can't see)."
            )

        deploy_sha = await _ref_sha(client, settings.gh_deploy_branch)
        if deploy_sha is None:
            resp = await client.post(
                f"/repos/{settings.gh_repo}/git/refs",
                headers=_headers(),
                json={"ref": f"refs/heads/{settings.gh_deploy_branch}", "sha": main_sha},
            )
            _raise_friendly(resp)
            return (
                f"Created '{settings.gh_deploy_branch}' from '{settings.gh_main_branch}' "
                f"({main_sha[:7]}) — they're identical, nothing to deploy yet."
            )

        # A naive `deploy_sha == main_sha` check only catches the very first
        # deploy: merging via merge commit (see _merge_pr) gives `deploy` a
        # NEW sha even once its CONTENT matches `main` again, so every deploy
        # after the first would fall through here and hit a 422 "no commits
        # between" from GitHub on PR creation. Ask the compare endpoint
        # instead — it answers by content, not by sha.
        compare = await client.get(
            f"/repos/{settings.gh_repo}/compare/{settings.gh_deploy_branch}...{settings.gh_main_branch}",
            headers=_headers(),
        )
        _raise_friendly(compare)
        commits = compare.json().get("commits", [])
        if not commits:
            return f"'{settings.gh_deploy_branch}' is already up to date with '{settings.gh_main_branch}' — nothing to deploy."

        existing = await _find_open_pr(client)
        if existing:
            return f"Deploy PR is already open: {existing['html_url']}"

        body = "\n".join(f"- {c['commit']['message'].splitlines()[0]}" for c in commits)

        resp = await client.post(
            f"/repos/{settings.gh_repo}/pulls",
            headers=_headers(),
            json={
                "title": f"Deploy: {settings.gh_main_branch} -> {settings.gh_deploy_branch}",
                "head": settings.gh_main_branch,
                "base": settings.gh_deploy_branch,
                "body": body,
            },
        )
        _raise_friendly(
            resp,
            on_422=(
                f"GitHub rejected the PR — '{settings.gh_main_branch}' and "
                f"'{settings.gh_deploy_branch}' may have diverged (e.g. a commit "
                f"landed directly on '{settings.gh_deploy_branch}'). Check both "
                "branches manually before retrying."
            ),
        )
        return f"Opened deploy PR: {resp.json()['html_url']}\n\n{body}"


async def _merge_pr() -> str:
    async with httpx.AsyncClient(base_url=_API, timeout=20) as client:
        pr = await _find_open_pr(client)
        if pr is None:
            return "Error: no open deploy PR found — call action=open_pr first."
        resp = await client.put(
            f"/repos/{settings.gh_repo}/pulls/{pr['number']}/merge",
            headers=_headers(),
            json={"merge_method": "merge"},
        )
        _raise_friendly(resp, on_conflict=f"PR #{pr['number']} isn't mergeable right now")
        return f"Merged deploy PR #{pr['number']} ({pr['html_url']}). The prod runner will pick it up from here."


async def deploy(action: str) -> str:
    if not settings.gh_deploy_token or not settings.gh_repo:
        return "Error: GH_DEPLOY_TOKEN/GH_REPO aren't configured — deploys are disabled."
    if action not in ("open_pr", "merge_pr"):
        return "Error: action must be one of open_pr|merge_pr."
    try:
        return await (_open_pr() if action == "open_pr" else _merge_pr())
    except DeployError as e:
        return f"Error: {e}"


TOOL = ToolDef(
    name="deploy",
    description=(
        "Ship Kaizen's own current `main` branch to prod. `action=open_pr` opens (or "
        "returns the existing) PR from `main` into `deploy` — call this when the owner "
        "says something like 'deploy your changes'. `action=merge_pr` merges that PR, "
        "which is what actually triggers the prod redeploy — ONLY call this when the "
        "owner explicitly says to merge/ship it, never on your own after opening the PR."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["open_pr", "merge_pr"]},
        },
        "required": ["action"],
    },
    handler=deploy,
)
