# ADR-004: Mobile-first CSS breakpoints

**Date**: 2026-08-09
**Status**: Accepted
**Deciders**: Eng (pool-card grid alignment)

## Context

The dashboard is Jinja2 + vanilla CSS (`api/static/style.css`, page-local
`<style>` in templates). Layout rules used desktop-first `max-width` media
queries. After adding a second `max-width` step for the home pool grid, cascade
order mattered: a later, wider `max-width` query still matched phones and
overrode the narrower single-column rule.

We also needed shared vocabulary for where “tablet” vs “phone” layouts start,
without adopting a CSS framework.

## Options Considered

### Option 1: Keep `max-width` (desktop-first), enforce source order

Base styles target wide viewports; narrower overrides must appear **after**
wider ones in the stylesheet.

- Pros: Minimal change to existing rules
- Cons: Easy to regress when adding a breakpoint in the wrong place; “earlier /
  later” is about source order, not viewport size — unintuitive for reviewers

### Option 2: Mobile-first `min-width`, ad-hoc pixel values

Base styles target the smallest layout; `min-width` queries only widen.

- Pros: Later queries cannot accidentally re-narrow; progressive enhancement
- Cons: Still invents project-specific cutoffs (601, 769, …) that nobody
  remembers; `px` media queries ignore root font-size

### Option 3: Mobile-first `min-width` using Tailwind’s default rem widths (chosen)

Same as Option 2, but cutoffs match Tailwind v4’s generated breakpoints —
without installing Tailwind:

| Token | `min-width` | ≈ at 16px root | Typical use here                          |
| ----- | ----------- | -------------- | ----------------------------------------- |
| sm    | `40rem`     | 640px          | Pool grid multi-column, filter bar, chart |
| md    | `48rem`     | 768px          | Page chrome, detail header single row     |

One-off widths below sm (e.g. detail-header stack at `22.5rem` / 360px) stay
allowed when Tailwind has no equivalent token. Use `rem` for those too.

- Pros: Matches Tailwind v4 output; scales with root font-size; familiar tokens
- Cons: Slight layout shift vs the old 600/768 `max-width` edges; rem breakpoints
  move if the root font-size changes

## Decision

**Chosen**: Option 3 — mobile-first `min-width`, Tailwind sm/md in `rem`.

Do not add Tailwind (or another utility CSS framework) for breakpoints alone.
Prefer CSS Grid for multi-card and multi-region layouts; use subgrid where
sibling cards must share row tracks (e.g. aligned occupancy bars).

## Consequences

### Positive

- New breakpoints append safely: they only enhance larger viewports
- sm/md are recognizable to anyone who has used Tailwind
- Pool cards can share header/body row heights via subgrid without fixed title
  heights

### Negative / Trade-offs

- Existing `max-width` habits in reviews must be rejected
- Very-narrow exceptions (below sm) are unlabeled one-offs — document them in
  the rule comment when added
- Page-local CSS in templates must follow the same convention (not only
  `style.css`)

## Implementation Notes

- Default (no query) = narrowest intended layout
- Shared steps: `@media (min-width: 40rem)` (sm) and `@media (min-width: 48rem)` (md)
- Prefer `rem` over `px`/`em` in width media queries (Tailwind v4 does the same)
- Do not use `@media (min-width: var(--…))` — custom properties in media
  queries are not reliably supported; keep literal `rem` values
- Non-width queries (`pointer: coarse`, `prefers-*`) are out of scope for this
  ADR and may remain as-is
