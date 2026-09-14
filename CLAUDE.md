# Working on botpit

Practical notes for Claude Code (or anyone else) picking up this repo,
written from how it's actually been built so far, not a generic template.
For what the project *is*, start with `llms.txt` and `docs/stack.md`; for
local setup, `docs/development/setup.md`; for branch/PR mechanics,
`CONTRIBUTING.md`. This file is about how the work itself gets done.

## Phases, not sprints

Work is tracked in `PROGRESS.md` (what's done, phase by phase, with real
detail — not just checkboxes) and `DECISIONS.md` (permanent decisions and
why, referenced by number). Before starting anything, read both — most
"why is it built this way" questions are already answered there, and
re-deciding something already settled wastes a round-trip.

Each phase gets a spec (usually from the project owner, sometimes
self-planned when the phase is a natural continuation) with an explicit
scope boundary — what's in, what's out, what "done" means. Infra-only
phases (Phase 9.7) are the clearest example: a hard rule to not touch
application logic or test content, because it's tempting to "fix" adjacent
things while in there. Respect the stated boundary even when a nearby fix
looks easy and safe — flag it in the docs as found-but-deferred instead.

Planning a phase doesn't always mean invoking `ecc`'s `planner` agent
(below). If the phase is a direct continuation of work already in context
— the previous nine phases were built this session, say — a fresh planner
subagent would just re-derive the same context from `PROGRESS.md` at real
token cost, with no added judgment. Say so explicitly when skipping it;
don't silently improvise. Reach for a real planner call when starting
genuinely fresh (new session, unfamiliar phase) or when the phase is big
enough that a second pass at the plan is worth the cost.

## Git workflow

Full mechanics (branch naming, commit convention, PR requirements) are in
`CONTRIBUTING.md` — read that first. The part worth adding here:

- **Never push to `master` directly**, even for a one-line fix. Every
  change lands via a phase-branch PR, reviewed and merged with
  `gh pr merge --merge` (a real merge commit, not squash/rebase, so
  `PROGRESS.md`'s phase history matches the actual git history).
- `gh pr merge`/`gh pr view` can 504 without the operation having actually
  failed. After merging, always verify with
  `gh pr view <n> --json state,mergedAt` rather than trusting the merge
  command's own exit code or lack of output.
- Waiting on CI is normal and can take several minutes (the Playwright
  matrix especially). Poll `gh pr checks <n>`, don't assume a check that
  hasn't reported yet has failed — and don't skip a check because it's
  "probably fine."
- **Worktrees + parallel subagents** are for genuinely independent pieces
  of a phase — e.g. Phase 9.5 split a dependency audit, a docs
  reorganization, and a rebrand pass across three worktrees because none
  of the three touched files the others needed. Don't reach for this by
  default: for a phase (like 9.7) whose pieces mostly touch separate files
  but where one person already has the full picture in context, working
  through it directly is often the better call, and cheaper. State which
  way you're going and why.

## Review, always

Every PR into `master` gets a two-reviewer pass before merge —
`ecc`'s `code-reviewer` and `python-reviewer` agents (below), dispatched
in parallel against the open PR's diff, each given the actual context of
what changed and why (not just "review this PR"). This has caught real
bugs, not nitpicks — e.g. on Phase 9.7's PR, `python-reviewer` caught a
`chown -R` in `backend/Dockerfile` that was silently giving back ~430MB
of a measured image-size win via overlay2 layer copy-up, and
`code-reviewer` caught a Docker BuildKit cache backend that would have
been silently inert in CI because no workflow provisioned the buildx
driver it needs. Both were real, both were fixed and re-verified before
merge, neither would have been obvious from a superficial read of the
diff. Treat every finding as worth checking, even ones that sound minor —
triage explicitly (fixed / deliberately not fixed with a stated reason),
don't just wave them off.

## Testing discipline

- Backend: `cd backend && uv run pytest tests` (753 tests as of Phase 9.7,
  ~53s). The full gate before any PR is
  `uv run prek run --all-files` (ruff check + format, mypy strict, ty) —
  see `CONTRIBUTING.md` for the exact commands and optional markers.
- The suite intentionally does **not** use `pytest-xdist`: `tests/conftest.py`'s
  `db` fixture is session-scoped against one real, shared Postgres
  database with no per-worker isolation, so parallelizing would risk real
  cross-worker collisions, not just a hypothetical one. This was checked
  directly (not assumed) during Phase 9.7 and rejected — don't re-add
  parallelization without first reworking that fixture's isolation
  strategy.
- Frontend (once Phase 10 exists): `bun run build`, `bun run lint` — clean
  build+lint has been the bar for template scaffolding so far, since
  there's no real UI yet to browser-test.
- After any infra/CI change, don't just assume it works — verify locally
  where possible (`docker compose build`, `docker compose up -d --wait`,
  actually exec into the container) and say plainly when something can
  only be confirmed on GitHub's own runners (a GHA cache backend, for
  instance) rather than claiming a result you didn't actually observe.
- Measured, not assumed: when a change is supposed to make something
  faster or smaller, get a real before/after number (image size via
  `docker images`, CI duration via `gh run view`, cache hit/miss via
  `gh run view --log`) rather than asserting an improvement. A rough,
  honestly-labeled number is worth more than a confident guess — and
  worth re-measuring if a later fix (like the `chown -R` one above)
  changes the thing you measured.

## Tools this workflow actually depends on

### `ecc` plugin (`ecc@ecc`, user-scoped — not something this repo ships)

This project's whole review/planning workflow assumes the `ecc` Claude
Code plugin is installed. It isn't bundled with the repo (it's installed
at the user level, so a fresh clone on another machine needs it added
separately — `/plugin marketplace add` the `ecc` marketplace, then
`/plugin install ecc@ecc`), but assume it's present when working here;
most of this file describes a workflow built around it.

Agents actually used so far, by name:
- `ecc:code-reviewer` / `ecc:python-reviewer` — the standard two-reviewer
  pass on every PR (see "Review, always" above). Dispatch both in
  parallel against the same PR with real context (what changed, why, what
  to specifically check), not a bare "review this."
- `ecc:planner` — used when a phase needs a fresh planning pass rather
  than a direct continuation of context already in hand (see "Phases, not
  sprints" above).
- Language/build-specific reviewers and resolvers (`ecc:fastapi-reviewer`,
  `ecc:react-reviewer`, `ecc:react-build-resolver`, etc.) exist in the
  plugin and are fair game once the frontend (Phase 10) is real code to
  review — none of it exists yet, so none of these have been used yet.
- `ecc:doc-updater`, `ecc:refactor-cleaner`, and others are available but
  haven't come up — this project's phases so far have mostly called for
  direct edits plus the reviewer pass rather than a dedicated cleanup
  agent. Don't reach for an agent just because it exists; reach for it
  when the task genuinely benefits from a fresh, separately-scoped pass
  (see the worktree/subagent judgment-call note above — the same
  reasoning applies to every agent dispatch, not just planning).

### RTK (https://github.com/rtk-ai/rtk)

RTK is installed and active **at the user/session level** — a
`PreToolUse` hook rewrites common Bash commands (`git status` →
`rtk git status`, `pytest` → `rtk pytest`, etc.) to compress their output
before it reaches context. It is not part of this repo's own
configuration (no project-level RTK config exists here); it comes from
whoever's running Claude Code in this environment, per their own
`~/.config/rtk/config.toml`. If you're picking this repo up in an
environment without RTK installed, the commands in this file and in
`CONTRIBUTING.md` all still work identically un-rewritten — RTK only
changes how much of their output survives into context, never their
behavior.

One thing worth knowing if you *do* have RTK active here: its
`[hooks].exclude_commands` list is currently empty in this environment,
meaning nothing is carved out for long-running/interactive commands. RTK
buffers output, so anything genuinely long-lived (`docker compose up`
without `-d`, a `tail -f`, a dev server) should be run with
`run_in_background` or otherwise kept out of RTK's rewrite rather than
run through it directly — this project hasn't hit a case where that
mattered yet (dev workflows here use `docker compose up -d`, not
foreground), but it's the reason if a long-running command ever seems to
hang under RTK.

## Session personas (informational, not project convention)

You may see responses in this project shaped by session-level personas
(a terse "caveman" reply style, a "ponytail" lazy-but-correct build
philosophy) — these are also user-level Claude Code plugins, not
something the repo requires or encodes. They affect *how* answers are
phrased/scoped, not the actual engineering standards above. Don't
mistake terse output for skipped rigor, and don't assume a differently-configured
session will read the same way — the substance in this file is what's
durable.
