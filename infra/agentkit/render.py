#!/usr/bin/env python3
# =============================================================================
# Fleet renderer — infra/agentkit/render.py
# =============================================================================
# WHAT: Turns one `project.json` spec into a complete project skeleton under
#       `projects/<name>/` — the chosen personas, the viable commands, the
#       workflow, and the whole deploy/Makefile/docs skeleton from
#       `infra/agentkit/templates/`. Then it VERIFIES what it wrote.
#
# WHY it is a script and not prose in a skill: the mechanical half of scaffolding
#       used to be 400 lines of instructions that a model re-typed for every
#       project. The first project proved what that costs — a wrong instruction
#       in the skill (`do not run gh auth login`) was inherited faithfully, and
#       fixing it means re-scaffolding by hand with no way to tell which
#       projects predate the fix. A script's bug is a diff and a re-run.
#
# WHY it does NOT author rulebooks or choose zones: those need judgement and
#       stack knowledge (MANIFEST.md §4). The split is the whole point — this
#       script does substitution, selection and verification; the model does the
#       interview, the zones and the rulebooks, and passes its answers in as
#       `slots`. Anything it forgets shows up as a `{{SLOT}}` this script
#       refuses to ship.
#
# HOW:  python3 infra/agentkit/render.py project.json
#       python3 infra/agentkit/render.py project.json --out /somewhere/else
#       Re-running is idempotent: same spec in, same tree out.
# =============================================================================

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
KAIZEN = KIT.parent.parent

# Identity pools, assigned in order and never invented (MANIFEST.md §2).
DEV_NAMES = [
    ("dev-anderson", "Thomas Anderson"), ("dev-neumann", "John von Neumann"),
    ("dev-wayne", "Bruce Wayne"), ("dev-potts", "Pepper Potts"),
    ("dev-parker", "Peter Parker"), ("dev-barton", "Clint Barton"),
    ("dev-romanoff", "Natasha Romanoff"), ("dev-kent", "Clark Kent"),
]
LEAD_NAMES = [("lead-tesla", "Nikola Tesla"), ("lead-torvalds", "Linus Torvalds")]
REVIEWER_NAMES = [("reviewer-granger", "Hermione Granger"),
                  ("reviewer-mcgonagall", "Minerva McGonagall")]

# Which model each persona defaults to.
#
# WHY these and not "opus everywhere": the SUBSCRIPTION — not the machine — is
# what limits how often the fleet can run, and a single /develop spends several
# personas. Seven of eight on opus ran the quota out constantly. Reviewers,
# security and research read and judge; architects and developers produce the
# thing being judged, and a cheap mistake there is paid for by everyone
# downstream.
MODELS = {
    "alfred": "opus",
    "architect-xavier": "opus",
    "product-owner-ohno": "opus",
    "dev-": "opus",
    "lead-": "sonnet",
    "reviewer-": "sonnet",
    "security-holmes": "sonnet",
    "researcher-curie": "sonnet",
    "analyst-lovelace": "sonnet",
    "designer-davinci": "sonnet",
    "ui-reviewer-rams": "sonnet",
}

# A command may only be installed when every persona it spawns exists
# (MANIFEST.md §2b). A command that spawns a missing agent fails mid-pipeline,
# after the owner has been told the work started.
COMMAND_REQUIRES = {
    "develop": ["architect-xavier", "security-holmes"],
    "fix": ["architect-xavier", "security-holmes"],
    "refactor": ["architect-xavier"],
    "epic": ["architect-xavier"],
    "brainstorm": ["architect-xavier"],
    "review": ["architect-xavier"],
    "next": ["architect-xavier"],
    "research": ["architect-xavier", "researcher-curie"],
    "analyze": ["architect-xavier", "analyst-lovelace"],
    "design": ["architect-xavier", "designer-davinci", "ui-reviewer-rams"],
    "product": ["product-owner-ohno", "architect-xavier"],
    "doc": [],
    "abort": [],
    # No persona needed — a single gh/git sequence, not development work.
    # Gated on spec["deploy"] in pick_commands(), same as "next" is gated on
    # spec["tracker_cli"]: an empty `needs` list here would otherwise make it
    # unconditionally on for every project, deploy pipeline or not.
    "deploy": [],
}

# Commands that are inherently two-or-more-personas-talking-to-each-other —
# not offered in solo topology no matter how capable the one persona is.
# `product` is a Product Owner handing scope to an architect; `design` is an
# architect briefing a designer who is checked by a SEPARATE design reviewer.
CREW_ONLY_COMMANDS = {"product", "design"}

# The Hub's kind vocabulary is fixed and small; nothing outside it can arrive.
# Mirrors modules/tracker/models.py's DIRECTIVE_KINDS — kept in sync by hand,
# since this file renders for OTHER repositories and cannot import Kaizen's.
HUB_KINDS = ("develop", "fix", "refactor", "research", "review", "epic",
             "brainstorm", "analyze", "ask", "converse", "deploy")

SLOT_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
# Files copied byte-for-byte: substituting inside them would corrupt them.
BINARY_SUFFIXES = {".png", ".jpg", ".gif", ".ico", ".pdf", ".zip"}


class RenderError(Exception):
    """Anything that must stop the render rather than ship a broken project."""


# -- roster -------------------------------------------------------------------
def build_roster(spec: dict) -> list[dict]:
    """Apply MANIFEST.md §2 mechanically. Returns entries in chart order."""
    zones = spec["zones"]
    if not zones:
        raise RenderError("spec has no zones — at least one is required")

    if spec.get("topology") == "solo":
        # One persona, no fan-out: zones still describe the codebase's parts
        # (paths, rulebooks, verify commands) for CLAUDE.md's zone table, but
        # nobody owns just one of them — this persona owns all of it. No PO
        # either: a PO handing off to an architect is inherently two agents
        # talking, which is exactly the coordination cost solo exists to
        # skip. The SLUG and FILE stay "alfred" regardless of the chosen
        # display name (spec["solo_name"]) — it's the kit's technical join
        # key (status pushes, subagent_type, .claude/agents/alfred.md), and
        # changing it per project would mean synthesizing a differently
        # named file per render. Only the display name — what the persona
        # calls itself, what shows in reports — is customizable.
        return [
            {"slug": "alfred", "name": spec.get("solo_name") or "Alfred",
             "role": "Overseer", "tier": "architect",
             "model": model_for("alfred")},
            # The one deliberate exception to "solo = one agent". Without a
            # second, tool-restricted persona, review-loop's solo path can
            # only spawn a fresh instance of Alfred told "don't edit this
            # time" — a prompt instruction, not an enforced boundary, one
            # bad turn away from silently patching what it was supposed to
            # only report. reviewer-strict has no Write/Edit in its tool
            # list at all (MANIFEST.md "DinD"'s sibling section, "Review
            # loop"): independence enforced by the harness, not requested of
            # the model. Always installed regardless of `reviewers`/other
            # spec keys — this is infrastructure for the skill, not a
            # roster choice.
            {"slug": "reviewer-strict", "name": "Strict Reviewer",
             "role": "Independent Reviewer", "tier": "reviewer",
             "model": model_for("reviewer-strict")},
        ]

    roster: list[dict] = []
    po = bool(spec.get("product_owner"))
    if po:
        # `product`, never `owner`: `owner` is the HUMAN the work is for. The Hub
        # seeds that row, and claiming it here both labels an agent human and
        # stops the real owner from ever appearing on the chart.
        roster.append({"slug": "product-owner-ohno", "name": "Taiichi Ohno",
                       "role": "Product Owner", "tier": "product"})
    roster.append({"slug": "architect-xavier", "name": "Charles Xavier",
                   "role": "Solution Architect", "tier": "architect",
                   "reports_to": "product-owner-ohno" if po else ""})

    # Leads: only where they are not pure latency. A lead with one report adds a
    # hop and no judgement.
    multi_dev = any(int(z.get("devs", 1)) >= 2 for z in zones)
    want_leads = multi_dev or len(zones) >= 3
    leads: dict[str, str] = {}
    if want_leads:
        for zone, (slug, name) in zip(zones, LEAD_NAMES):
            leads[zone["key"]] = slug
            roster.append({"slug": slug, "name": name,
                           "role": f"{zone['label']} Team Lead", "tier": "lead",
                           "area": zone["key"], "reports_to": "architect-xavier"})

    devs = iter(DEV_NAMES)
    for zone in zones:
        for _ in range(max(1, int(zone.get("devs", 1)))):
            try:
                slug, name = next(devs)
            except StopIteration:
                raise RenderError(
                    "more developers requested than the kit has identities (8). "
                    "Merge zones — two agents that must coordinate constantly "
                    "belonged in one zone."
                )
            roster.append({"slug": slug, "name": name,
                           "role": f"{zone['label']} Developer", "tier": "developer",
                           "area": zone["key"],
                           "reports_to": leads.get(zone["key"], "architect-xavier")})

    for i in range(max(1, int(spec.get("reviewers", 1)))):
        slug, name = REVIEWER_NAMES[min(i, len(REVIEWER_NAMES) - 1)]
        roster.append({"slug": slug, "name": name, "role": "Code Reviewer",
                       "tier": "reviewer", "reports_to": "architect-xavier"})
    roster.append({"slug": "security-holmes", "name": "Sherlock Holmes",
                   "role": "Security Reviewer", "tier": "reviewer",
                   "reports_to": "architect-xavier"})

    if spec.get("researcher", True):
        roster.append({"slug": "researcher-curie", "name": "Marie Curie",
                       "role": "Researcher", "tier": "developer", "area": "research",
                       "reports_to": "architect-xavier"})
    if spec.get("analyst"):
        roster.append({"slug": "analyst-lovelace", "name": "Ada Lovelace",
                       "role": "Systems Analyst", "tier": "developer", "area": "specs",
                       "reports_to": "architect-xavier"})
    if spec.get("design"):
        roster.append({"slug": "designer-davinci", "name": "Leonardo da Vinci",
                       "role": "Product Designer", "tier": "developer", "area": "design",
                       "reports_to": "architect-xavier"})
        roster.append({"slug": "ui-reviewer-rams", "name": "Dieter Rams",
                       "role": "Design Reviewer", "tier": "reviewer",
                       "reports_to": "architect-xavier"})

    for entry in roster:
        entry["model"] = model_for(entry["slug"])
    return roster


def model_for(slug: str) -> str:
    if slug in MODELS:
        return MODELS[slug]
    for prefix, model in MODELS.items():
        if prefix.endswith("-") and slug.startswith(prefix):
            return model
    return "sonnet"


def pick_commands(spec: dict, slugs: set[str]) -> list[str]:
    solo = spec.get("topology") == "solo"
    chosen = []
    for name, needs in COMMAND_REQUIRES.items():
        if name == "next" and not spec.get("tracker_cli"):
            continue  # /next pops a queue; without a tracker service there is none
        if name == "deploy" and not spec.get("deploy"):
            continue  # opt-in — most projects have no prod host to ship to
        if name in CREW_ONLY_COMMANDS and solo:
            # These require two DISTINCT personas talking to each other (a PO
            # handing off to an architect, an architect briefing a designer +
            # a separate design reviewer) — not satisfiable by one agent
            # regardless of what it's capable of.
            continue
        if solo:
            # Alfred alone stands in for whatever a command would otherwise
            # spawn — see alfred.md and each command's {{SOLO_NOTE}}.
            chosen.append(name)
            continue
        if all(s in slugs for s in needs):
            chosen.append(name)
    return sorted(chosen)


# -- slots --------------------------------------------------------------------
def verify_all_cmd(spec: dict, zones: list[dict]) -> str:
    """The project-wide verify command.

    Defaults to the zones' own commands chained — NEVER to `make test`: that
    string is rendered into the BODY of the Makefile's own `test` target, so
    `make test` would call itself forever. verify() refuses it even when the
    spec asks for it explicitly.
    """
    return spec.get("verify_all") or " && ".join(z["verify"] for z in zones)


def global_slots(spec: dict, roster: list[dict], zones: list[dict]) -> dict:
    solo = spec.get("topology") == "solo"
    po = any(r["slug"] == "product-owner-ohno" for r in roster)
    zone_rows = "\n".join(
        f"| `{z['key']}` | {z['label']} | {', '.join(f'`{p}`' for p in z['paths'])} "
        f"| `{z.get('rulebook', '')}` | `{z['verify']}` | {owner_of(roster, z['key'])} |"
        for z in zones
    )
    registry = "\n".join(
        f"| `{r['slug']}` | {r['name']} | {r['role']} | {r.get('area') or '—'} | `{r['model']}` |"
        for r in roster
    )
    return {
        "PROJECT": spec["project"],
        "OWNER_LANGUAGE": spec.get("owner_language", "Russian"),
        "MAIN_BRANCH": spec.get("main_branch", "main"),
        "INTEGRATION_BRANCH": spec.get("integration_branch", "develop"),
        # Fixed for a Warden project: infra/wardenkit/trackerfiles.py hardcodes
        # this layout, so another value makes the fleet write handoffs the
        # Warden never sees.
        "TRACKER_ROOT": "docs/tracker",
        "PRODUCT_ROOT": spec.get("product_root", "docs/product"),
        "SPEC_ROOT": spec.get("spec_root", "docs/specs"),
        "RESEARCH_ROOT": spec.get("research_root", "docs/research"),
        "SURFACE_REGISTRY": spec.get("surface_registry", "docs/specs/screen-registry.md"),
        "RULEBOOK_CORE": ".claude/projects/00-core-invariants.md",
        "RULEBOOK_SECURITY": ".claude/projects/03-security-checklist.md",
        "VERIFY_ALL_CMD": verify_all_cmd(spec, zones),
        "NOTIFY_CMD": spec.get("notify_cmd", "scripts/notify.sh"),
        "ASK_OWNER_CMD": spec.get("ask_owner_cmd", "scripts/ask_owner.sh"),
        "TRACKER_CMD": spec.get("tracker_cmd", ""),
        "ZONE_KEYS": ", ".join(f"`{z['key']}`" for z in zones),
        "ZONE_KEY_EXAMPLE": zones[0]["key"],
        "ZONE_TABLE": zone_rows,
        "AGENT_REGISTRY": registry,
        # Solo topology has no separate architect — Alfred fills every role
        # slot referenced by the templates, including this one, so a command
        # written for the crew case ("spawn {{ARCHITECT_NAME}}") still points
        # at a real persona instead of a name nobody rendered.
        # Solo's display name comes from the roster (build_roster() already
        # resolved spec["solo_name"] there) rather than re-reading spec here
        # — one source of truth for what the persona is actually called.
        "ARCHITECT_NAME": (
            next(r["name"] for r in roster if r["slug"] == "alfred") if solo
            else "Charles Xavier"
        ),
        "ARCHITECT_HANDLE": "alfred" if solo else "xavier",
        # The join key for status pushes — the roster slug, verbatim. The
        # HANDLE above is prose; pushing status as "xavier" matches nothing.
        "ARCHITECT_SLUG": "alfred" if solo else "architect-xavier",
        # Inserted near the top of every multi-phase command template.
        # Overrides every later "spawn <agent>" instruction EXCEPT the one
        # that runs the review-loop skill — solo topology has nobody to
        # spawn for the fan-out phases (see alfred.md), including OTHER
        # phases that happen to have "Review" in their title but do not
        # invoke review-loop (e.g. /analyze's and /research's own
        # "Phase 3 — Quality Review (Xavier)", which spawns architect-xavier
        # — absent from the solo roster, so it collapses into "do it
        # yourself" like every other spawn instruction, same as before).
        # The one exception is genuinely narrow: solo's roster DOES have
        # `reviewer-strict` (build_roster()'s always-installed second solo
        # entry), and the review-loop skill's "Who reviews" section spawns
        # it precisely so solo never ships on a self-review alone.
        "SOLO_NOTE": (
            "> **Solo project.** There is only one persona here — `alfred`. "
            "Ignore every \"spawn `<agent>`\" instruction below **except the "
            "one phase that says \"Run the review-loop skill\"** (do not "
            "confuse this with a phase that merely has \"Review\" in its "
            "title, such as a Quality Review that spawns Xavier — that one "
            "is not the exception; collapse it into your own work like "
            "every other spawn instruction): do every other phase's work "
            "yourself, in this same context, one phase after another, and "
            "skip any step that exists only to hand work between separate "
            "agents (Handoff files, cross-agent status pushes). The "
            "review-loop phase still happens — the review-loop skill "
            "(`.claude/skills/review-loop/`) spawns `reviewer-strict`, a "
            "second, independent, edit-incapable agent, for every "
            "development task, and in solo YOU (the orchestrator) apply "
            "its findings yourself, same as the skill's default rule. This "
            "is not optional and self-review is never a substitute for it "
            "— see `alfred.md`.\n"
            if solo else ""
        ),
        # CLAUDE.md's zone table sentence — a flat ownership statement, not a
        # pipeline, so it gets its own wording rather than {{SOLO_NOTE}}'s
        # "ignore every spawn instruction below" framing (there is no
        # instruction to ignore here, just a claim that would otherwise
        # directly contradict alfred.md's "you own all of it").
        "ZONE_OWNERSHIP_NOTE": (
            "There is one agent — it owns every zone in the table above. "
            "The split is for organisation (rulebooks, verify commands), "
            "not an ownership boundary between agents."
            if solo else
            "An agent works **only** inside its own zone's paths. Another "
            "zone's file is someone else's — hand off, do not edit."
        ),
        "SECURITY_NAME": "Sherlock Holmes", "SECURITY_HANDLE": "holmes",
        "RESEARCHER_NAME": "Marie Curie", "RESEARCHER_HANDLE": "curie",
        "PO_NAME": "Taiichi Ohno" if po else "—", "PO_HANDLE": "ohno" if po else "—",
        "DESIGNER_NAME": "Leonardo da Vinci", "UI_REVIEWER_NAME": "Dieter Rams",
        "LEADS_AND_DIRECT_REPORTS": bullets(
            f"{r['name']} — {r['role']} (`{r['slug']}`)"
            for r in roster if r.get("reports_to") == "architect-xavier"),
        "CROSS_CUTTING_ROLES": bullets(
            f"{r['name']} — {r['role']} (`{r['slug']}`)"
            for r in roster if r.get("tier") == "reviewer" or r.get("area") in ("research", "specs", "design")),
        "AUTONOMY_LEVEL": autonomy_text(spec.get("autonomy", "L0")),
        # Read by every persona, so it is a PHRASE here and the real command in
        # a persona (agent_slots overrides it). Rendering the project-wide
        # command into a developer makes them verify the whole repo on every
        # change, which is slow enough that they start skipping it.
        "VERIFY_CMD": "the zone's own verify command — see the zone table in CLAUDE.md",
        "CONTEXT_DOCS": spec.get(
            "context_docs", "`README.md`, `CLAUDE.md`, `docs/decisions.md`"),
        "CONTEXT_DOC_POLICY": spec.get(
            "context_doc_policy",
            "update the `AGENTS.md` of the area you changed; keep each ≤300 lines; "
            "no code in them"),
        "TEST_POLICY": spec.get(
            "test_policy",
            "one e2e scenario per acceptance criterion; unit tests only for pure, "
            "branchy logic"),
        "DOC_CONVENTION": spec.get("doc_convention", "docstrings"),
        "EXTRA_SELF_CHECK": spec.get("extra_self_check", "- (nothing beyond the standard checks)"),
        # Derived, not authored: this is exactly what the rendered entrypoint
        # does, so the fleet learns how it is authenticated without ever reading
        # the value.
        "GIT_CREDENTIALS": (
            "`GH_TOKEN` comes from the owner's `deploy/warden/.env` through "
            "compose. The container's entrypoint writes it into `~/.git-credentials` and into "
            "`gh`'s own config, then **drops it from the environment** — so "
            "`printenv GH_TOKEN` returns nothing and you never need the value. "
            "`git push` and `gh` both just work. If they do not, the fleet is "
            "local-only: report a file list, not a PR."),
        "PO_PHASE_NOTE": (
            "Ohno is reached through the Kaizen tracker: a Directive of kind "
            "`brainstorm` runs `/product`, and kind `ask` consults Ohno directly "
            "for a text answer with no pipeline."
            if po else
            "This project has no Product Owner — the owner drives every task."),
        "MAX_CONCURRENT": str(spec.get("max_concurrent", 1)),
        "GRPC_ADDR": spec.get("grpc_addr", f"{spec['project']}-warden:9200"),
        "REPO_URL": spec.get("repo_url", ""),
        "PURPOSE": spec.get("purpose", ""),
        # MANIFEST.md §5 rule 10: these are the review-loop skill's caps —
        # fixed here, not spec-driven, because they are an invariant of the
        # kit's pipelines, not a per-project choice.
        "DEVELOP_ROUND_CAP": "3",
        "FIX_ROUND_CAP": "2",
    }


def dind_slots(spec: dict) -> dict:
    """DinD support for deploy/warden/{Dockerfile,docker-compose.yml}.

    WHY isolated nested docker and not the host's own /var/run/docker.sock:
    the socket is host-root-equivalent — any container that can reach it can
    start, stop or inspect ANY container on that host. A Warden almost never
    runs on a box dedicated to one project (Kaizen itself is usually on the
    same host), so that mount hands host-root to whatever the fleet's agent
    turns out to do — including a fleet whose own product is a public,
    untrusted-input endpoint (first proven wrong in an earlier project,
    2026-08-05: approved as "the host is dedicated", reverted the same day
    once that premise turned out to be false). `dind` is a sibling service running its
    OWN docker engine, on a network (`dind-net`) nothing outside this one
    compose file joins — a privileged boundary around an isolated engine,
    not a bridge into the host's real one.

    Off by default (`spec.get("dind")` falsy): most projects don't need live
    Postgres/Redis/HTTP for their own tests, and every one of these blocks
    is dead weight — an unused privileged service, an extra image pull — for
    a project that never asked for it.
    """
    if not spec.get("dind"):
        return {
            "DIND_ENV_BLOCK": "", "DIND_DEPENDS_ON_BLOCK": "", "DIND_NETWORK_ITEM": "",
            "DIND_SERVICE_BLOCK": "", "DIND_NETWORK_DEF_BLOCK": "", "DIND_VOLUME_DEF_BLOCK": "",
            "DIND_DOCKERFILE_BLOCK": "",
        }
    project = spec["project"]
    return {
        "DIND_ENV_BLOCK": f'      DOCKER_HOST: "tcp://{project}-dind:2375"',
        "DIND_DEPENDS_ON_BLOCK": (
            "    depends_on:\n"
            "      dind:\n"
            "        condition: service_healthy"),
        "DIND_NETWORK_ITEM": "      - dind-net",
        "DIND_SERVICE_BLOCK": (
            "  # Isolated docker engine — see dind_slots() in infra/agentkit/render.py\n"
            "  # for why this exists instead of mounting the host's docker.sock.\n"
            "  # No port published to the host; reachable only from `warden`, over\n"
            "  # `dind-net`, which nothing outside this compose file joins.\n"
            "  dind:\n"
            f"    container_name: {project}-dind\n"
            "    image: docker:27-dind\n"
            "    privileged: true\n"
            "    restart: unless-stopped\n"
            "    environment:\n"
            "      DOCKER_TLS_CERTDIR: \"\"\n"
            "    healthcheck:\n"
            '      test: ["CMD", "docker", "info"]\n'
            "      interval: 3s\n"
            "      timeout: 5s\n"
            "      retries: 20\n"
            "    volumes:\n"
            f"      - {project}-dind-data:/var/lib/docker\n"
            "    networks:\n"
            "      - dind-net"),
        "DIND_NETWORK_DEF_BLOCK": (
            "  dind-net:\n"
            "    # Not external, not shared with any other compose project —\n"
            "    # created and owned entirely by this file.\n"
            f"    name: {project}-dind-net"),
        "DIND_VOLUME_DEF_BLOCK": (
            f"  {project}-dind-data:\n"
            f"    name: {project}-dind-data"),
        "DIND_DOCKERFILE_BLOCK": (
            "\n"
            "# Isolated docker engine support — see dind_slots() in\n"
            "# infra/agentkit/render.py. Only the CLI + compose plugin: this image\n"
            "# never runs its own dockerd, it talks to the `dind` sibling service\n"
            "# over DOCKER_HOST (set in docker-compose.yml), never a host socket.\n"
            "RUN install -m 0755 -d /etc/apt/keyrings \\\n"
            "    && curl -fsSL https://download.docker.com/linux/debian/gpg \\\n"
            "        -o /etc/apt/keyrings/docker.asc \\\n"
            "    && chmod a+r /etc/apt/keyrings/docker.asc \\\n"
            '    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \\\n'
            "        https://download.docker.com/linux/debian bookworm stable\" \\\n"
            "        > /etc/apt/sources.list.d/docker.list \\\n"
            "    && apt-get update && apt-get install -y --no-install-recommends \\\n"
            "        docker-ce-cli docker-compose-plugin \\\n"
            "    && rm -rf /var/lib/apt/lists/*"),
    }


def deploy_slots(spec: dict) -> dict:
    """Slots for the opt-in deploy pipeline: `/deploy` (commands/deploy.md)
    and `.github/workflows/deploy.yml` (write_deploy_files() below).

    Mirrors Kaizen's own deploy pipeline (docs/deploy-pipeline.md): a PR from
    `{{MAIN_BRANCH}}` into `{{DEPLOY_BRANCH}}`, redeployed by a self-hosted
    GitHub Actions runner on the prod host once that PR merges.

    Off by default (`spec.get("deploy")` falsy): most scaffolded projects
    have no prod host yet, and DEPLOY_CMD/RUNNER_LABEL/BRANCH substituting to
    "" is harmless because pick_commands() already drops `/deploy` entirely
    and write_deploy_files() is never called — same belt-and-braces pattern
    as dind_slots().
    """
    if not spec.get("deploy"):
        return {"DEPLOY_BRANCH": "", "DEPLOY_RUNNER_LABEL": "", "DEPLOY_CMD": ""}
    deploy_cmd = spec.get("deploy_cmd", "make up prod")
    if "\n" in deploy_cmd:
        # deploy-workflow.yml substitutes this into a `run: |` block scalar
        # AND into several one-line comments — a newline breaks the comments'
        # own YAML regardless of the block scalar being newline-safe. Chain
        # multiple steps with && instead of raising this here at render time,
        # where it's a clear error, not a broken workflow YAML nobody notices
        # until the first push to deploy.
        raise RenderError('spec["deploy_cmd"] must be a single line — chain steps with &&')
    return {
        "DEPLOY_BRANCH": spec.get("deploy_branch", "deploy"),
        "DEPLOY_RUNNER_LABEL": spec.get("deploy_runner_label", f"{spec['project']}-prod"),
        # The project's own root Makefile always gets this target
        # (MANIFEST.md §1, "two Makefiles") — no new deploy command invented.
        "DEPLOY_CMD": deploy_cmd,
    }


def e2e_secrets_slots(spec: dict) -> dict:
    """Makefile/.gitignore/CLAUDE.md slots for the e2e secrets split
    (docs/e2e/README.md §8): throwaway tokens regenerated on demand
    (`.env.e2e.generated`) vs. real, spend-capped credentials for the manual
    `live` tier only (`.env.e2e.live.secrets`, hand-provisioned, never read
    directly by an agent). The generator script itself
    (docs/e2e/host/e2e_gen_secrets.py) is copied by write_e2e_files(), not
    templated here — these three slots just wire the project's own
    Makefile/.gitignore/CLAUDE.md to know it exists.

    Off by default, same reasoning as dind_slots(): a project without e2e
    should not carry make targets for a script it was never given.
    """
    if not spec.get("e2e"):
        return {"SECRETS_PHONY": "", "SECRETS_MAKE_TARGETS": "", "SECRETS_GITIGNORE_BLOCK": "", "SECRETS_RULE": ""}
    return {
        "SECRETS_PHONY": " e2e-secrets e2e-secrets-live",
        "SECRETS_MAKE_TARGETS": (
            "\n# Regenerate throwaway e2e tokens into .env.e2e.generated (flow/contract\n"
            "# tiers — no real credentials involved, see docs/e2e/README.md §8). Edit\n"
            "# .env.e2e.generated.example, not the script, to change which keys this\n"
            "# project generates.\n"
            "e2e-secrets:\n"
            "\t@python3 .e2e/e2e_gen_secrets.py\n"
            "\n"
            "# Same, but also checks .env.e2e.live.secrets exists and has the expected\n"
            "# keys — required for the manual `live` tier only, never an automatic gate.\n"
            "e2e-secrets-live:\n"
            "\t@python3 .e2e/e2e_gen_secrets.py --live\n"
        ),
        "SECRETS_GITIGNORE_BLOCK": ".env.e2e.generated\n.env.e2e.live.secrets\n",
        "SECRETS_RULE": (
            "- Same rule for **`.env.e2e.live.secrets`** (real, spend-capped e2e "
            "`live`-tier credentials — see `docs/e2e/README.md` §8): edit via its "
            "`.example` template, never read the real file. `.env.e2e.generated` "
            "(flow/contract tiers) is throwaway and fine to read.\n"
        ),
    }


def owner_of(roster: list[dict], zone_key: str) -> str:
    owners = [r["name"] for r in roster
              if r.get("area") == zone_key and r["tier"] in ("developer", "lead")]
    if owners:
        return ", ".join(owners)
    # An unowned zone falls back to whoever's tier is `architect` — Xavier in
    # crew, Alfred in solo (its one roster entry, no `area` of its own, is
    # tier `architect` for exactly this reason). Never a hardcoded name a
    # solo render never actually produced.
    architect = next((r["name"] for r in roster if r["tier"] == "architect"), None)
    return architect or "Charles Xavier"


def bullets(items) -> str:
    lines = [f"- {i}" for i in items]
    return "\n".join(lines) or "- (none in this project)"


def autonomy_text(level: str) -> str:
    # L2 is NEVER inferred. "They said they trust the fleet" is not a grant, and
    # a fleet that merges when it should not have is discovered days later.
    return {
        "L0": "L0 — review each PR: a PR per task, the owner merges",
        "L1": "L1 — batch: agents work the queue, open PRs, merge nothing, and "
              "report once per milestone with every link",
        "L2": "L2 — autonomous merge into the integration branch when every gate "
              "is green, reporting once per milestone",
    }.get(level, "L0 — review each PR: a PR per task, the owner merges")


def agent_slots(entry: dict, roster: list[dict], zones: list[dict], spec: dict) -> dict:
    """The slots whose value depends on WHICH persona is being rendered."""
    zone = next((z for z in zones if z["key"] == entry.get("area")), None)
    lead = next((r for r in roster if r["slug"] == entry.get("reports_to")), None)
    reviewers = [r for r in roster if r["tier"] == "reviewer"]
    team = [r for r in roster if r.get("reports_to") == entry["slug"]]
    lead_zones = sorted({r.get("area", "") for r in team if r.get("area")})
    books = (
        ["`.claude/projects/00-core-invariants.md`"]
        + ([f"`.claude/projects/{zone['rulebook']}`"] if zone and zone.get("rulebook") else [])
        + ["`.claude/projects/03-security-checklist.md`"]
    )
    return {
        "MODEL": entry["model"],
        "ZONE_KEY": zone["key"] if zone else "—",
        "ZONE_LABEL": zone["label"] if zone else "the whole project",
        # ZONE-SCOPED, deliberately: render VERIFY_ALL_CMD here and a developer
        # verifies the whole repo on every change, which is slow enough that
        # they start skipping it.
        "VERIFY_CMD": zone["verify"] if zone else verify_all_cmd(spec, zones),
        "OWNED_PATHS": bullets(f"`{p}`" for p in (zone["paths"] if zone else ["(all)"])),
        "OTHER_ZONES": bullets(
            f"`{', '.join(z['paths'])}` — {owner_of(roster, z['key'])} ({z['label']})"
            for z in zones if not zone or z["key"] != zone["key"]),
        "LEAD_NAME": lead["name"] if lead else "Charles Xavier",
        "LEAD_HANDLE": (lead["slug"].split("-")[-1] if lead else "xavier"),
        "TEAMMATES": bullets(
            f"{r['name']} — {r.get('area') or 'cross-cutting'} — `{r['slug']}.md`"
            for r in roster if r["slug"] != entry["slug"]),
        "REVIEWERS": bullets(f"{r['name']} (`{r['slug']}`)" for r in reviewers),
        "TEAM": bullets(f"{r['name']} — {r.get('area', '')} (`{r['slug']}`)" for r in team),
        "LEAD_ZONES": ", ".join(f"`{k}`" for k in lead_zones) or "—",
        "REVIEW_ZONES": ", ".join(f"`{z['key']}`" for z in zones),
        "RULEBOOKS": bullets(books),
        # The ordered read pass. A sensible default rather than a required
        # authored value: every persona needs the same spine (invariants → its
        # zone's rulebook → security → what was already decided), and a project
        # that wants more can override it in the spec.
        "PRE_READ": bullets(
            books
            + ["`docs/decisions.md` — what was already decided, and why"]
            + [spec.get("context_docs", "`README.md`, `CLAUDE.md`")]),
    }


# -- rendering ----------------------------------------------------------------
def substitute(text: str, slots: dict) -> str:
    return SLOT_RE.sub(lambda m: str(slots.get(m.group(1), m.group(0))), text)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_tree(src: Path, dst: Path, slots: dict) -> list[Path]:
    """Copy a template tree, substituting slots in every text file."""
    written = []
    for item in sorted(src.rglob("*")):
        if item.is_dir() or "__pycache__" in item.parts:
            continue
        target = dst / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix in BINARY_SUFFIXES:
            shutil.copy2(item, target)
        else:
            write(target, substitute(item.read_text(encoding="utf-8"), slots))
            if item.suffix == ".sh":
                target.chmod(0o755)
        written.append(target)
    return written


def vendor_kit(dst: Path) -> None:
    """Copy the parts of Kaizen a project imports into deploy/warden/vendor/.

    The copy is COMMITTED, so a deploy host needs nothing — re-running this is a
    development step, never a deployment one.
    """
    vendor = dst / "deploy" / "warden" / "vendor"
    # `infra/quota.py` is the ONE module wardenkit imports beyond the generated
    # stubs — the CLI's spent-subscription wording, shared with Кая so the two
    # cannot drift. Forget it here and every Warden dies on import.
    for rel in ("infra/__init__.py", "infra/proto/__init__.py", "infra/quota.py"):
        write(vendor / rel, (KAIZEN / rel).read_text(encoding="utf-8"))
    for rel in ("infra/wardenkit", "infra/proto/gen"):
        target = vendor / rel
        # rm -rf before copying, so a removed file in the kit is removed here
        # too. Guarded by the fact that `target` is always inside the project we
        # just rendered — never a path the caller supplied.
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(KAIZEN / rel, target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


# -- verification -------------------------------------------------------------
def verify(dst: Path, roster: list[dict], commands: list[str], zones: list[dict],
           verify_all: str = "") -> list[str]:
    """Everything that must hold before this counts as a render. Returns problems."""
    problems: list[str] = []

    # `make test` as the project-wide verify is rendered into the BODY of the
    # Makefile's own `test` target — verified to recurse forever. Refused even
    # when the spec asks for it explicitly, because following the docs
    # literally used to produce exactly this.
    if re.fullmatch(r"make\s+test", (verify_all or "").strip()):
        problems.append(
            "verify_all is 'make test', which becomes the body of the Makefile's "
            "own `test` target and recurses forever — give the real command(s), "
            "e.g. the zones' verify commands chained with '&&'"
        )

    # A hole in a persona is not cosmetic: the agent reads `{{SLOT}}` as literal
    # text and behaves unpredictably.
    for path in sorted(dst.rglob("*")):
        if path.is_file() and path.suffix in (".md", ".yml", ".yaml", ".py", ".sh", ""):
            try:
                found = set(SLOT_RE.findall(path.read_text(encoding="utf-8")))
            except (UnicodeDecodeError, OSError):
                continue
            if found:
                problems.append(f"{path.relative_to(dst)}: unresolved {sorted(found)}")

    # Two zones owning the same path means two agents editing the same file with
    # no idea the other exists. Checked PAIRWISE, not just on adjacent pairs.
    for i, a in enumerate(zones):
        for b in zones[i + 1:]:
            shared = set(a["paths"]) & set(b["paths"])
            if shared:
                problems.append(f"zones '{a['key']}' and '{b['key']}' both own {sorted(shared)}")

    slugs = {r["slug"] for r in roster}
    # Solo topology's persona (build_roster's "alfred" entry, plus the
    # always-installed `reviewer-strict` — see build_roster()) stands in for
    # whatever a command would otherwise spawn — see pick_commands()'s
    # matching bypass and each command's {{SOLO_NOTE}}. CREW_ONLY_COMMANDS
    # already keeps two-persona commands (product, design) out of `commands`
    # for solo, so nothing here needs to re-check that. Checked by presence
    # of "alfred", NOT by exact slug-set equality — reviewer-strict makes
    # solo's roster two entries, not one, and an equality check here would
    # wrongly re-enable this loop and report every solo command as missing
    # personas nobody was ever going to spawn.
    if "alfred" not in slugs:
        for name in commands:
            missing = [s for s in COMMAND_REQUIRES[name] if s not in slugs]
            if missing:
                problems.append(f"/{name} spawns missing persona(s): {missing}")

    for path in sorted((dst / ".claude" / "agents").glob("*.md")):
        head = path.read_text(encoding="utf-8")[:400]
        match = re.search(r"^name:\s*(\S+)", head, re.M)
        if match and match.group(1) != path.stem:
            problems.append(f"{path.name}: frontmatter name '{match.group(1)}' != filename")

    return problems


# -- e2e ------------------------------------------------------------------
def prefill_e2e_profile(template_text: str) -> str:
    """Fill the parts of `.e2e/profile.yml` a dind-wired project already knows.

    `env.mode` and `boot.up`/`down`/`timeout` are mechanical facts of the DinD
    wiring (MANIFEST.md "DinD") — the coding agent already runs inside
    `warden`, `DOCKER_HOST` already points at the sibling `dind` engine, and
    the project's own stack is, by convention, `deploy/docker-compose.yml`
    (MANIFEST.md §3: "authored per-project, not part of this kit"). `boot.ready`/
    `reset`, `run.*`, `needs` and `boundary` stay placeholders — they depend on
    this project's actual health check, test runner and secrets, none of which
    this script can know. The `/e2e` interview (docs/e2e/command/e2e.md §1)
    fills those in on first use.
    """
    fills = {
        'mode: <local | dind | remote | ci>': 'mode: dind',
        '  up:      "<how to bring the environment up — e.g. docker compose -f deploy/docker-compose.yml up -d>"':
            '  up:      "docker compose -f deploy/docker-compose.yml up -d"',
        '  timeout: <seconds, e.g. 120>': '  timeout: 120',
        '  down:    "<how to tear the environment down>"':
            '  down:    "docker compose -f deploy/docker-compose.yml down"',
    }
    text = template_text
    for old, new in fills.items():
        if old not in text:
            raise RenderError(
                f"docs/e2e/profile.template.yml no longer contains the expected "
                f"line {old!r} — prefill_e2e_profile() is out of sync with it"
            )
        text = text.replace(old, new)
    return text


def write_e2e_files(spec: dict, out: Path) -> None:
    """Copy the e2e method + secrets generator, and pre-fill the profile /
    write the secrets example files on first render only.

    All three re-copied files (`e2e.md`, `e2e_gen_secrets.py`) live in
    `docs/e2e/` (KAIZEN, not the kit) — project-agnostic exports
    (docs/e2e/README.md §2, §8), copied byte-for-byte so every adopting
    project stays in sync with the same rules; re-copying them on every
    render is correct, since they are kit-owned and never hand-edited
    (docs/e2e/command/e2e.md's own header).

    `.e2e/profile.yml` and the two `.env.e2e.*.example` files are the
    OPPOSITE: co-located PROJECT data (docs/e2e/README.md §1, §8), not
    rendered kit artifacts. The first render pre-fills/creates them
    (`env.mode`/`boot.up`/`down`/`timeout` are known facts of the DinD wiring;
    the example files start as empty skeletons), but every render after that
    must leave them alone — the owner's `/e2e` interview fills in
    `boot.ready`/`reset`, `run.*`, `needs`, `boundary`, and their own e2e
    secret keys, over time, and a script that overwrites these files on the
    next spec change would silently erase that answered interview.
    """
    e2e_src = KAIZEN / "docs" / "e2e" / "command" / "e2e.md"
    if not e2e_src.exists():
        raise RenderError("spec wants e2e but docs/e2e/command/e2e.md is missing")
    write(out / ".claude" / "commands" / "e2e.md", e2e_src.read_text(encoding="utf-8"))

    # Secrets split (docs/e2e/README.md §8): the generator is project-agnostic
    # and kit-owned, so it is re-copied every render like e2e.md above. The two
    # example files are PROJECT data (this project's own key list) — like
    # profile.yml below, written once and never overwritten, so a later render
    # cannot erase keys the owner already filled in.
    secrets_script_src = KAIZEN / "docs" / "e2e" / "host" / "e2e_gen_secrets.py"
    if not secrets_script_src.exists():
        raise RenderError("spec wants e2e but docs/e2e/host/e2e_gen_secrets.py is missing")
    write(out / ".e2e" / "e2e_gen_secrets.py", secrets_script_src.read_text(encoding="utf-8"))

    generated_example_dst = out / ".env.e2e.generated.example"
    if not generated_example_dst.exists():
        write(generated_example_dst, (
            "# e2e flow/contract tier secrets — see docs/e2e/README.md §8 and\n"
            "# .claude/commands/e2e.md. List every auth-shaped env key this project's\n"
            "# e2e stand needs; a value of GENERATE gets a fresh random token from\n"
            "# `make e2e-secrets`. Any other value is copied through as-is — use that\n"
            "# for fixed, non-secret test config, not real credentials.\n"
            "#\n"
            "# WATCH OUT: if your e2e compose overlay keeps a database volume warm\n"
            "# across runs (recommended — a full up/down per test costs minutes), do\n"
            "# NOT mark that DB's password GENERATE. The password gets baked into the\n"
            "# data directory once, at first boot; regenerating it on every run while\n"
            "# the volume survives breaks every service's connection the moment the\n"
            "# container restarts without a matching `down -v`. Use a fixed value for\n"
            "# anything baked into a warm volume — GENERATE only for auth tokens\n"
            "# services re-read from the environment on every boot.\n"
            "#\n"
            "# Fill this in as part of the /e2e interview, alongside .e2e/profile.yml.\n"
            "#\n"
            "# YOUR_SERVICE_TOKEN=GENERATE\n"
        ))

    live_secrets_example_dst = out / ".env.e2e.live.secrets.example"
    if not live_secrets_example_dst.exists():
        write(live_secrets_example_dst, (
            "# e2e `live` tier secrets — see docs/e2e/README.md §8. Only needed if\n"
            "# this project's e2e has a manual `live` tier hitting a real external\n"
            "# service; the default flow/contract tiers use fakes and need none of\n"
            "# this.\n"
            "#\n"
            "# List any real, capped/throwaway credential the live tier needs. Never\n"
            "# generated, never read directly by an agent (see this project's\n"
            "# CLAUDE.md).\n"
            "#\n"
            "# YOUR_REAL_API_KEY=\n"
        ))

    profile_dst = out / ".e2e" / "profile.yml"
    if profile_dst.exists():
        print("e2e: .e2e/profile.yml already present — left untouched")
        return
    profile_src = KAIZEN / "docs" / "e2e" / "profile.template.yml"
    if not profile_src.exists():
        raise RenderError("spec wants e2e but docs/e2e/profile.template.yml is missing")
    write(profile_dst, prefill_e2e_profile(profile_src.read_text(encoding="utf-8")))


def write_deploy_files(slots: dict, out: Path) -> None:
    """Render the opt-in deploy workflow — `.github/workflows/deploy.yml`.

    Source lives at `infra/agentkit/deploy-workflow.yml` (a top-level KIT
    file, same as `workflow.md`/`git-workflow.md`, not under `templates/`
    since that tree renders unconditionally and this must not). Mirrors
    Kaizen's own `.github/workflows/deploy.yml`: a self-hosted runner
    already living on the prod host, triggered by a push to
    `{{DEPLOY_BRANCH}}`, running `{{DEPLOY_CMD}}`.

    Takes the full, already-assembled `slots` dict (not just deploy_slots())
    because the workflow's own comments reference `{{MAIN_BRANCH}}`, which
    comes from global_slots(), not deploy_slots().
    """
    src = KIT / "deploy-workflow.yml"
    if not src.exists():
        raise RenderError("spec wants deploy but infra/agentkit/deploy-workflow.yml is missing")
    write(out / ".github" / "workflows" / "deploy.yml",
          substitute(src.read_text(encoding="utf-8"), slots))


# -- main ---------------------------------------------------------------------
def render(spec: dict, out: Path) -> None:
    if spec.get("e2e") and not spec.get("dind"):
        # e2e's boot/ready/reset commands run `docker compose` against this
        # project's own dind sidecar — the coding agent already runs inside
        # `warden`, the one container DinD wires up with DOCKER_HOST, so this
        # is the one flag e2e needs turned on (MANIFEST.md "DinD").
        spec = {**spec, "dind": True}
    zones = spec["zones"]
    roster = build_roster(spec)
    slugs = {r["slug"] for r in roster}
    commands = pick_commands(spec, slugs)

    kinds = [k for k in spec.get("kinds", HUB_KINDS) if k in HUB_KINDS]
    # brainstorm goes to /product when there is a PO: "what is worth doing and
    # what do we start with" is the Product Owner's question, and routing it to
    # /brainstorm puts the architect in business-analyst mode and silently
    # bypasses whoever owns the backlog.
    # "ask" and "converse" both consult a PERSONA rather than run a slash
    # command — see ASK_PERSONA below, which conversemode.run_conversation
    # reuses for the same reason run_ask does: one persona for both, since a
    # tunnel turn and an ask are the same shape, just looped.
    kind_commands = {
        k: ("/product" if k == "brainstorm" and "product-owner-ohno" in slugs else f"/{k}")
        for k in kinds if k not in ("ask", "converse")
    }
    kind_commands = {k: v for k, v in kind_commands.items() if v.lstrip("/") in commands}

    slots = global_slots(spec, roster, zones)
    slots.update(dind_slots(spec))
    slots.update(e2e_secrets_slots(spec))
    slots.update(deploy_slots(spec))
    slots.update({
        "ROSTER": json.dumps(roster, ensure_ascii=False, indent=4),
        "KINDS": json.dumps(
            sorted(
                set(kind_commands)
                | ({"ask"} if "ask" in kinds else set())
                | ({"converse"} if "converse" in kinds else set())
            ),
            ensure_ascii=False,
        ),
        "KIND_COMMANDS": json.dumps(kind_commands, ensure_ascii=False, indent=4),
        "ARTIFACT_GLOBS": json.dumps({
            "*": ["docs/tracker/{task_id}/tasks/*.md"],
            "research": ["docs/research/{task_id}/*.md"],
            "brainstorm": ["docs/product/*.md"],
            "analyze": ["docs/specs/*.md"],
        }, ensure_ascii=False, indent=4),
        # The consulted persona for `ask`. Alfred when solo (there is no one
        # else); otherwise the PO when there is one — questions about state
        # and priorities are theirs — otherwise the architect.
        "ASK_PERSONA": (
            "alfred" if "alfred" in slugs
            else "product-owner-ohno" if "product-owner-ohno" in slugs
            else "architect-xavier"
        ),
    })
    # Authored values (MANIFEST.md §4) come from the spec and win over defaults.
    slots.update(spec.get("slots", {}))

    out.mkdir(parents=True, exist_ok=True)
    render_tree(KIT / "templates", out, slots)

    # Re-rendering is how a project grows (or shrinks) its topology — solo
    # today, crew next month, just by re-running with an updated
    # project.json (SKILL.md §1, MANIFEST.md §2). That claim is false if a
    # persona or command from the PREVIOUS render survives: a leftover
    # `alfred.md` in a project that just grew into a crew is spawnable, and
    # a leftover `product.md` in a project that just shrank to solo tells
    # the one persona to run a command spawning nobody. Prune to exactly
    # what THIS render chose, before writing it — same reasoning as
    # `vendor_kit()`'s rm-before-copy, and safe for the identical reason:
    # both targets are always inside the project directory just rendered.
    # Only prune names the KIT itself can produce — `.claude/commands/` in
    # particular is the standard place a Claude Code user drops their own
    # project-specific slash commands, and a blind `*.md` glob deleted those
    # (and any hand-added `.claude/agents/*.md`) with no warning. A file this
    # renderer never wrote is never a candidate, regardless of topology.
    pruned: list[str] = []
    agents_dir = out / ".claude" / "agents"
    if agents_dir.exists():
        kit_agents = {p.name for p in (KIT / "agents").glob("*.md")}
        wanted = {f"{r['slug']}.md" for r in roster}
        for stale in agents_dir.glob("*.md"):
            if stale.name in kit_agents and stale.name not in wanted:
                stale.unlink()
                pruned.append(f".claude/agents/{stale.name}")
    commands_dir = out / ".claude" / "commands"
    if commands_dir.exists():
        kit_commands = {p.name for p in (KIT / "commands").glob("*.md")}
        wanted = {f"{name}.md" for name in commands}
        for stale in commands_dir.glob("*.md"):
            if stale.name in kit_commands and stale.name not in wanted:
                stale.unlink()
                pruned.append(f".claude/commands/{stale.name}")
    # Unlike commands/agents, the deploy workflow isn't written through
    # render_tree()/the commands loop — it's a guarded write below
    # (write_deploy_files()). Nothing else would ever remove a stale one, and
    # unlike a leftover doc file it's ACTIVE: a project that just turned
    # "deploy" off would otherwise keep auto-deploying on every push to
    # deploy, which is the exact failure this whole prune block exists to
    # prevent (see its header comment above).
    deploy_workflow = out / ".github" / "workflows" / "deploy.yml"
    if not spec.get("deploy") and deploy_workflow.exists():
        deploy_workflow.unlink()
        pruned.append(".github/workflows/deploy.yml")
    if pruned:
        print(f"Pruned (previous render, wrong topology): {', '.join(sorted(pruned))}")

    for entry in roster:
        src = KIT / "agents" / f"{entry['slug']}.md"
        if not src.exists():
            raise RenderError(f"kit has no persona '{entry['slug']}.md'")
        per = dict(slots)
        per.update(agent_slots(entry, roster, zones, spec))
        # Keep the KIT FILENAME — the commands reference personas by
        # `subagent_type`, which is this stem.
        write(out / ".claude" / "agents" / src.name,
              substitute(src.read_text(encoding="utf-8"), per))

    for name in commands:
        src = KIT / "commands" / f"{name}.md"
        write(out / ".claude" / "commands" / src.name,
              substitute(src.read_text(encoding="utf-8"), slots))

    # Skills: one shared implementation per skill (e.g. review-loop), copied
    # into every project regardless of topology — every command with a
    # review phase invokes it instead of re-describing round caps and
    # VERDICT parsing in its own body (MANIFEST.md §5 rule 10).
    skills_root = KIT / "skills"
    if skills_root.exists():
        for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
            src = skill_dir / "SKILL.md"
            if not src.exists():
                continue
            write(out / ".claude" / "skills" / skill_dir.name / "SKILL.md",
                  substitute(src.read_text(encoding="utf-8"), slots))

    for name in ("workflow.md", "git-workflow.md"):
        write(out / ".claude" / name,
              substitute((KIT / name).read_text(encoding="utf-8"), slots))

    write(out / ".claude" / "KIT_VERSION", (KIT / "VERSION").read_text(encoding="utf-8"))
    vendor_kit(out)

    if spec.get("e2e"):
        write_e2e_files(spec, out)

    if spec.get("deploy"):
        write_deploy_files(slots, out)

    problems = verify(out, roster, commands, zones, slots["VERIFY_ALL_CMD"])
    report(spec, out, roster, commands, kind_commands, problems)
    if problems:
        raise RenderError(f"{len(problems)} problem(s) — see above. Nothing here is usable yet.")


def report(spec, out, roster, commands, kind_commands, problems) -> None:
    print(f"\nRendered '{spec['project']}' → {out}")
    print(f"\nFleet ({len(roster)}):")
    for r in roster:
        print(f"  {r['slug']:<22} {r['model']:<7} {r['tier']:<10} {r.get('area') or '—'}")
    print(f"\nCommands: {', '.join('/' + c for c in commands)}")
    skipped = sorted(set(COMMAND_REQUIRES) - set(commands))
    if skipped:
        print(f"Not installed: {', '.join('/' + c for c in skipped)} "
              "(a required persona is absent — installing them would fail mid-pipeline)")
    print(f"Kinds → commands: {kind_commands}, plus ask → persona")
    print(f"e2e: {'wired — .claude/commands/e2e.md + .e2e/profile.yml (dind pre-filled) + .e2e/e2e_gen_secrets.py + .env.e2e.*.example' if spec.get('e2e') else 'not requested'}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  ✗ {p}")
        return
    main_branch = spec.get("main_branch", "main")
    integration_branch = spec.get("integration_branch", "develop")
    print(f"""
Next:
  1. Author the rulebooks in {out}/.claude/projects/ — this is the part no
     script can do: stack, layout, idioms, traps. The personas already point at
     them by name.
  2. cd {out} && git init -b {main_branch} && git add -A && \\
     git commit -m "chore: scaffold {spec['project']}" && \\
     git checkout -b {integration_branch}
     ({main_branch} is yours alone from here — the fleet only ever opens PRs
      into {integration_branch}. Leave {integration_branch} checked out: it's
      where every task starts.)
  3. cd deploy/warden && make up
     (`make up`, run from deploy/warden/, chowns the state volume before
      starting — it is created root-owned and the container runs as your uid.
      If you start the stack any other way, run the chown yourself:
        docker run --rm -v {spec['project']}-warden-state:/state alpine \\
          chown -R $(id -u):$(id -g) /state )
  4. One-time: make login   (still from deploy/warden/) — the claude CLI needs
     one /login; the credential lands in the state volume and survives
     restarts. Until then every Directive fails with "Not logged in".
  5. Once the fleet has written this project's own deploy/docker-compose.yml,
     `make up` from the repo root starts the app itself — separate stack,
     separate `.env`, see {out}/README.md.
""")
    if KAIZEN in out.parents:
        print(f"""WARNING — this project is rendered INSIDE the Kaizen repo. That is fine for
the containerised fleet (it only ever sees /repo), but an interactive session
opened here on the HOST reads Kaizen's CLAUDE.md and rulebooks as its own, and
the fleet would follow Kaizen's conventions instead of this project's.
Move it out before working in it by hand:
  mv {out} ~/{spec['project']}
""")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a project skeleton from the agent kit")
    ap.add_argument("spec", help="path to project.json")
    ap.add_argument("--out", default="", help="target dir (default: <kaizen>/projects/<name>)")
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else KAIZEN / "projects" / spec["project"]
    try:
        render(spec, out.resolve())
    except RenderError as e:
        print(f"\nrender failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
