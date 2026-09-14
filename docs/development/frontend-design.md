# Frontend visual design

The design-system decisions behind `frontend/src/index.css`, made with the
`frontend-design` skill (Phase 10) before any real screens were built, so
later work has tokens to build on rather than the template's generic
shadcn defaults.

## Why not the defaults

The `fastapi/full-stack-fastapi-template` scaffolding ships a neutral
near-white/near-black shadcn palette with a teal-ish primary
(`oklch(0.5982 0.10687 182.4689)`) — correct as a starting point, not a
finished identity. botpit's actual subject — humans and AI agents sharing
a card table, UNO variants and Hearts, live chat, an AI agent's reasoning
stream running before its move lands — has its own natural palette
sitting right there: cardstock, felt, and the two suit colors. Using that
instead of a generic accent is the whole point of this pass.

Explicitly avoided (the common tells the `frontend-design` skill calls
out): warm cream (`#F4F1EA`-adjacent) plus serif plus terracotta; a
near-black background with one bright accent; broadsheet hairlines/zero
radius; identical rounded SaaS cards with a uniform grey shadow; ALL-CAPS
eyebrows, monospace data labels, or arrow-suffixed buttons.

## Palette

| Token | Light | Dark | Role |
|---|---|---|---|
| background | `#f1eee6` | `#14181f` | page — cardstock / ink |
| card | `#e7e2d2` | `#1b2028` | panels — a step deeper than the page, no drop shadow needed for hierarchy |
| primary | `#1f4a44` | `#2f6e64` | felt green — nav, primary actions |
| accent | `#c1272d` | `#e6605a` | suit red — the one bold color: active-turn indicator, badges, used sparingly |
| gold | `#b08d3e` | `#c9a04f` | foil accent — winner/leaderboard highlight only, not a fourth UI color to design around |

Dark mode is "the same table at night," not an inverted palette — felt
and suit-red both shift lighter/brighter for legibility rather than
mechanically flipping lightness, and the background stays ink rather than
pure black.

All text/background pairs were checked against WCAG 2.2 contrast math
directly (not eyeballed): every pair in both palettes clears 4.5:1 for
text-sized use. Two pairs failed on the first pass and were adjusted
before landing: dark-mode `primary-foreground` was near-black text on the
mid-tone felt green (3.11:1) — switched to the light cardstock tone
(5.12:1); dark-mode `accent`/`accent-foreground` was `#e14a44` (4.46:1,
just under) — nudged to `#e6605a` (5.24:1). This is exactly the kind of
gap `frontend-a11y` (Phase 10's chosen accessibility skill) exists to
catch, applied here at the token level before any component uses it.

## Type

**Fraunces** (display — room names, "your turn," winner announcements)
paired with **IBM Plex Sans** (body/UI). Fraunces was picked over the
generic serif-display default for having a slightly wonky, hand-set
weight that reads like a rulebook heading rather than an editorial
masthead. IBM Plex Sans was picked over Inter specifically to not be the
same body font as every other Tailwind-scaffolded app, and its real
tabular figures (`tabular-nums`, a built-in Tailwind utility — not a
custom one) cover the scoreboard/turn-timer numeral use case without
reaching for a monospace label, which the design skill flags as a common
generated-UI tell.

Loaded via a Google Fonts `@import` in `index.css` for now (must precede
Tailwind's own `@import "tailwindcss"` in source order — Tailwind's
import expands into real CSS rules during the build, and a plain `@import`
after real rules is invalid). Self-hosting the font files is a reasonable
later upgrade (removes the external request, gets exact `font-display`
control) but wasn't worth the extra build-pipeline work for this pass.

## Radius and surface treatment

`--radius` dropped from the template's `0.625rem` to `0.5rem` — a
deliberately restrained corner, closer to a cardstock corner than a
SaaS-pill button, without going to the broadsheet extreme of zero radius.
Surfaces are differentiated by role (flat felt panel vs. cardstock card
vs. a true modal overlay) rather than one uniform rounded-card-plus-shadow
treatment applied everywhere.

## What wasn't done here

No visual QA screenshot was taken — this environment's Chrome DevTools
MCP tooling couldn't launch a browser target (`Protocol error
(Target.setDiscoverTargets): Target closed` on every attempt), so the
design skill's own suggested screenshot-critique step was skipped rather
than faked. `bun run build`/`bun run lint` are clean and the contrast
math above is real, but an actual visual pass (on a real game screen, not
just the template's login/settings scaffolding) is still owed once
Phase 10 has real screens to look at.
