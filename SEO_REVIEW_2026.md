# SEO Review 2026 — badifrei.ch
**Date:** March 2026  
**Reviewer:** Jarvis  
**Branch:** main  
**Scope:** Updated review against live site + codebase, after SEO_REPORT.md + SEO_TASKS.md work  
**Live site:** https://badifrei.ch

---

## Executive Summary

**SEO Health Score: 6 / 10**

Significant work has been done since the original SEO_REPORT.md — the site is no longer the near-zero baseline it was. The URL migration, sitemap, robots.txt, schema enrichment, SSR content blocks (pool descriptions, opening hours, Beste Besuchszeiten), and related-pool linking are all solid improvements. Pool pages now have substantial crawlable content where previously Google saw only nav + footer.

However, there are **critical bugs still live on production** that actively undermine this work:

- `og:title` and `twitter:title` are broken on **every single page** (showing the old generic default)
- The OG image at `/static/og-preview.jpg` returns **404** — breaking all social share previews
- `apple-touch-icon.png` also returns **404** (only `.svg` exists)
- **Multiple pool pages have garbled titles** ("Schwimmbad Rotkreuz Rotkreuz", "Bern Marzili Bern", "Strandbad Hünenberg Hunenberg") due to an incomplete `city_map` in `pool.html`
- Pool pages have **no H2 headings** and no BreadcrumbList schema
- Current occupancy is still **JS-only** — Google sees 0% for live occupancy status

These are not theoretical issues. They are live, verifiable bugs that harm real-world CTR, social sharing, and structured data quality right now. Five of the top items below can be fixed in under two hours.

---

## What's Already Done Well

Credit where it's due — the following was implemented correctly:

| ✅ Completed | Notes |
|---|---|
| URL migration `/bad/{uid}` | Clean, semantic, short. The right call. |
| Sitemap with `lastmod`, `changefreq`, `priority` | Correct values (1.0 homepage, 0.8 pools, `always` for pool pages) |
| robots.txt with API Disallow rules | `/api/`, `/predict/`, `/health`, `/dashboard/` all blocked correctly |
| Security headers (X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) | Present and correct on all HTML responses |
| Per-pool title tags: `{name} {city} – Auslastung & Beste Besuchszeit \| badifrei.ch` | Keyword-rich formula, correct — except for the city name bug |
| Per-pool `<meta description>` overriding base.html block | No more duplicate descriptions |
| Per-pool `og:description` and `twitter:description` overrides | Pool-specific social descriptions in place |
| `og:url` correctly set to `/bad/{uid}` per pool | Matches canonical, correct |
| SportsActivityLocation schema enriched | `openingHoursSpecification`, `maximumAttendeeCapacity`, `address` all present |
| FAQPage schema on pool pages | 3 questions generated server-side |
| Opening hours SSR table | Crawlable HTML table, present on all pool pages |
| Pool description text (150–200 words) — all 32 pools | Unique, factual, SSR, crawlable. Excellent. |
| "Beste Besuchszeiten" section with ML-derived weekly insights | SSR text block with quietest day/hour + peak hour. Standout pSEO content. |
| `SSR_PREDICTIONS` injected into chart JS | Chart.js initialises without an extra `/predict/range` API call on first load |
| Chart container explicit height (380px / 300px mobile) | CLS prevented. Correct. |
| Related pools section with internal links | Same-city, same-type priority. Good for crawl graph and UX. |
| `lang="de-CH"` on `<html>` | Correct language targeting |
| Homepage H1 updated: "Zürcher Schwimmbäder – Auslastung live & Prognose" | Keyword-rich, matches title tag |
| Homepage H2 city sections: "Schwimmbäder Zürich", "Schwimmbäder Luzern", etc. | Good structure |
| City expansion: Bern, Adliswil, Rotkreuz, Entfelden, Hünenberg | 32 pools across 8 cities — solid pSEO footprint |
| `canonical` per pool pointing to `/bad/{uid}` | No duplicate content risk |
| Footer backlinks to CrowdMonitor, Stadt Zürich, j2y.dev | E-E-A-T attribution |

---

## Findings

### P1 — Critical Bugs (Fix Today)

---

#### F-001 | `og:title` not overridden — every page shows generic default
**Priority:** P1  
**Affected files:** `api/templates/pool.html`, `api/templates/index.html`  
**Live verification:** Both `https://badifrei.ch/` and `https://badifrei.ch/bad/LETZI-1` return:
```html
<meta property="og:title" content="badifrei.ch – Ist die Badi voll?">
```
**Root cause:** `base.html` defines `{% block og_title %}` but neither `pool.html` nor `index.html` override it.

**Impact:** Every Facebook/LinkedIn/WhatsApp/Slack preview of any URL on the site shows "badifrei.ch – Ist die Badi voll?" — completely generic. No pool name, no city, no intent keywords. Dramatically lowers click-through on social shares.

**Fix — pool.html:** Add after line 1 (after `{% extends "base.html" %}`):
```jinja2
{% block og_title %}{{ pool.name }} {{ city_map.get(pool.city, pool.city | title) }} – Auslastung live | badifrei.ch{% endblock %}
```

**Fix — index.html:** Add inside the `{% block content %}` — actually at top level:
```jinja2
{% block og_title %}Zürcher Schwimmbäder – Auslastung live & Prognose | badifrei.ch{% endblock %}
{% block og_description %}Echtzeit-Auslastung und KI-Prognosen für alle Zürcher Schwimmbäder. Schau vor dem Besuch nach — jede Minute aktualisiert.{% endblock %}
```

---

#### F-002 | `twitter:title` not overridden — same issue as F-001
**Priority:** P1  
**Affected files:** `api/templates/pool.html`, `api/templates/index.html`  

Every Twitter/X card shows "badifrei.ch – Ist die Badi voll?" regardless of which page is shared. Fix is the same pattern as F-001.

**Fix — pool.html:**
```jinja2
{% block twitter_title %}{{ pool.name }} {{ city_map.get(pool.city, pool.city | title) }} – Auslastung live{% endblock %}
```

---

#### F-003 | `og:image` is a 404 — broken social previews for the entire site
**Priority:** P1  
**Affected files:** `api/static/` (missing file), `api/templates/base.html`  
**Live verification:** `https://badifrei.ch/static/og-preview.jpg` returns HTTP 404. The file does not exist in `/api/static/`. Only `favicon.svg`, `apple-touch-icon.svg`, and `style.css` exist.

**Impact:** Every social share preview (Facebook, Twitter, LinkedIn, WhatsApp, Slack) shows no image or a broken image. This is the most visible UX failure in the codebase. It affects all 33 pages.

Also affects the `SportsActivityLocation` schema `"image"` field — pointing to a 404 is invalid structured data.

**Fix:** Create `/api/static/og-preview.jpg` (1200×630px). A simple branded image is fine — pool photo with "badifrei.ch" overlay, or even a solid color with the site name and tagline.

Alternatively, update `base.html` to use the existing SVG favicon as a fallback while the real image is produced:
```html
<!-- Temporary: use a minimal PNG until og-preview.jpg is created -->
<meta property="og:image" content="https://badifrei.ch/static/og-preview.jpg">
```
But there's no escaping it — the file must be created.

---

#### F-004 | `apple-touch-icon.png` is a 404
**Priority:** P1  
**Affected files:** `api/static/` (missing file), `api/templates/base.html`  
**Live verification:** `https://badifrei.ch/static/apple-touch-icon.png` returns HTTP 404. Only `apple-touch-icon.svg` exists.

**Impact:** iOS homescreen icon is broken. Minor SEO impact, but important for mobile UX and PWA quality signals.

**Fix (Option A):** Convert `apple-touch-icon.svg` to `apple-touch-icon.png` (180×180px).

**Fix (Option B):** Update `base.html` to reference the `.svg` file:
```html
<link rel="apple-touch-icon" href="/static/apple-touch-icon.svg">
```
Note: Safari support for SVG apple-touch-icons is limited; PNG is preferred.

---

#### F-005 | City name bug — duplicate/misspelled city in title tags and meta descriptions
**Priority:** P1  
**Affected files:** `api/templates/pool.html` — `city_map` definition  
**Live verification:**
- `https://badifrei.ch/bad/HUENENBERG-1` title: **"Strandbad Hünenberg Hunenberg – Auslastung & Beste Besuchszeit"** (pool name contains "Hünenberg", city_map fallback adds "Hunenberg")
- `https://badifrei.ch/bad/RISCH-1` title: **"Schwimmbad Rotkreuz Rotkreuz"**
- `https://badifrei.ch/bad/MARZILI-1` title: **"Bern Marzili Bern"**
- `https://badifrei.ch/bad/FREIBAD-1` title: **"Entfelden Entfelden"**

**Root cause:** `city_map` in `pool.html` only covers `zurich`, `luzern`, `zug`, `wengen`. Cities added since the original report (`bern`, `adliswil`, `rotkreuz`, `entfelden`, `hunenberg`) fall back to `pool.city | title`, which:
1. Doesn't handle umlauts ("hunenberg" → "Hunenberg" not "Hünenberg")
2. Gets appended even when the pool name already contains the city

**Affected pools:** Any pool in: `bern`, `adliswil`, `rotkreuz`, `entfelden`, `hunenberg`.

**Fix:** Update `city_map` in `pool.html` to include all cities:
```jinja2
{% set city_map = {
  "zurich": "Zürich", 
  "luzern": "Luzern", 
  "zug": "Zug", 
  "wengen": "Wengen",
  "bern": "Bern",
  "adliswil": "Adliswil",
  "rotkreuz": "Rotkreuz",
  "entfelden": "Entfelden",
  "hunenberg": "Hünenberg"
} %}
```

Additionally, the title formula `{{ pool.name }} {{ city_label }}` should guard against the case where the city name is already embedded in `pool.name`. The cleanest fix is to pass `city_label` from the Python route using the existing `CITY_DISPLAY` dict in `main.py`, rather than computing it in the template:

```python
# In pool_detail route:
city_label = CITY_DISPLAY.get(pool.get("city", ""), pool.get("city", "").title())
return templates.TemplateResponse("pool.html", {
    ...,
    "city_label": city_label,
})
```
Then in `pool.html`, replace all `city_map.get(pool.city, ...)` with `{{ city_label }}`. Eliminates the duplication risk entirely.

---

### P1 — High Value (Fix This Week)

---

#### F-006 | No H2 headings on pool pages — heading hierarchy broken
**Priority:** P1  
**Affected files:** `api/templates/pool.html`  
**Live verification:** `https://badifrei.ch/bad/LETZI-1` has exactly one H1 (`<h1>Freibad Letzigraben</h1>`) and one H3 (`<h3 class="related-pools-title">Weitere Bäder in Zürich</h3>`). Zero H2 tags.

**Impact:** The heading structure jumps from H1 → H3. This:
- Signals poor content structure to crawlers
- Misses keyword-bearing H2 opportunities ("Öffnungszeiten", "Beste Besuchszeiten", "Tagesverlauf")
- Lowers relevance for long-tail queries like "Freibad Letzigraben Öffnungszeiten"

**Fix:** Add H2 headings to the main content sections in `pool.html`:
```html
<!-- Before opening hours table -->
<h2>Öffnungszeiten {{ pool.name }}</h2>

<!-- Before beste-zeiten-block -->
<h2>Beste Besuchszeiten</h2>

<!-- Before chart section -->
<h2>Auslastung & Tagesprognose</h2>

<!-- Change H3 to H2 for related pools -->
<h2 class="related-pools-title">Weitere Bäder in {{ city_map.get(pool.city, pool.city) }}</h2>
```

---

#### F-007 | Current occupancy is JS-only — Google sees no live data
**Priority:** P1  
**Affected files:** `api/templates/pool.html`, `api/main.py`  
**Assessment:** The `#detail-live-count` element is populated via `refreshLiveCount()` JavaScript. The SSR HTML shows only an empty container. Google's crawler may not execute this JS reliably, especially for a real-time fetch endpoint.

**Impact:** The most compelling hook of the site — "how full is it right now?" — is invisible to Google's index. A user searching "Freibad Letzigraben voll" who clicks through sees the number, but Google can't confirm the page is relevant to that query.

**Fix:** Pass current occupancy from the route handler (using the DB) and render SSR:
```python
# main.py — in pool_detail():
current_data = None
if db_pool:
    try:
        row = await db_pool.fetchrow(
            "SELECT current_fill, max_space, ROUND((current_fill::numeric / NULLIF(max_space, 0)) * 100) AS pct "
            "FROM pool_occupancy WHERE pool_uid = $1 ORDER BY time DESC LIMIT 1",
            pool_uid
        )
        if row:
            current_data = dict(row)
    except Exception:
        pass
```
Then in `pool.html`, add a server-rendered occupancy paragraph that the JS can later update:
```html
{% if current_data and current_data.pct is not none %}
<p class="ssr-occupancy" id="ssr-occupancy-text">
  Aktuelle Auslastung: <strong>{{ current_data.pct }}%</strong>
  {% if current_data.pct <= 50 %} — 🟢 Wenig los
  {% elif current_data.pct <= 80 %} — 🟡 Mässig voll
  {% else %} — 🔴 Sehr voll{% endif %}
</p>
{% endif %}
```
Also: this would allow the **FAQPage "wann ist es am wenigsten voll"** question to carry genuinely real-time data.

---

#### F-008 | No BreadcrumbList schema on pool pages
**Priority:** P1  
**Affected files:** `api/templates/pool.html`  
**Assessment:** The related-pools section provides some internal linking, but there's no breadcrumb navigation or BreadcrumbList schema. Google uses breadcrumbs to understand site hierarchy and display them in search results.

**Fix:** Add to the `{% block structured_data %}` in `pool.html`:
```json
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Alle Bäder",
      "item": "https://badifrei.ch/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "{{ pool.name }}",
      "item": "https://badifrei.ch/bad/{{ pool.uid }}"
    }
  ]
}
```
And add a visible breadcrumb above the H1:
```html
<nav aria-label="Breadcrumb" class="breadcrumb">
  <a href="/">Alle Bäder</a> › <span aria-current="page">{{ pool.name }}</span>
</nav>
```

---

### P2 — Medium Priority

---

#### F-009 | SportsActivityLocation `image` field points to 404
**Priority:** P2  
**Affected files:** `api/templates/pool.html` — structured data block  
**Assessment:** The JSON-LD schema has `"image": "https://badifrei.ch/static/og-preview.jpg"` — which is a 404 (see F-003). Google's Rich Results Test will flag this as an error.

**Fix:** Blocked on F-003 (create `og-preview.jpg`). Once the image exists, this is resolved automatically. 

Alternatively, use a per-pool image URL if pool photos are ever added, or remove the `image` field temporarily until the file exists.

---

#### F-010 | FAQ schema gives fallback text for off-season pools
**Priority:** P2  
**Affected files:** `api/templates/pool.html`  
**Assessment:** For Freibad Letzigraben in March (off-season, before May 1st opening):
```
Q: Wann ist Freibad Letzigraben am wenigsten voll?
A: Aktuelle Prognosedaten sind momentan nicht verfügbar. 
   Prüfe die Auslastungskurve auf dieser Seite für aktuelle Informationen.
```
This fallback fires because `quietest_hour` is `None` (all predictions are 0 for closed season). The FAQPage schema is valid but the answer is weak — Google may penalize thin FAQ answers.

**Fix:** Use `weekly_insights` as the primary source for this answer. Since `weekly_insights` is pre-computed from a typical week's data (not today's predictions), it's valid year-round:
```jinja2
{% if weekly_insights and weekly_insights.has_data %}
  "Laut historischen Daten ist {{ pool.name | replace('"', '') }} typischerweise am 
   {{ weekly_insights.quietest_day_name }} um {{ weekly_insights.quietest_hour_str }} Uhr am ruhigsten. 
   Am vollsten ist es in der Regel um {{ weekly_insights.peak_hour_str }} Uhr. 
   Aktuelle Tagesprognosen auf badifrei.ch."
{% elif quietest_hour is not none %}
  "Laut heutiger Prognose ist {{ pool.name | replace('"', '') }} um {{ '%02d' | format(quietest_hour) }}:00 Uhr am ruhigsten."
{% else %}
  "Prüfe die Auslastungsprognose auf badifrei.ch für aktuelle Informationen."
{% endif %}
```

---

#### F-011 | `geo` coordinates missing from SportsActivityLocation schema
**Priority:** P2  
**Affected files:** `api/templates/pool.html`, `ml/pool_metadata.json`  
**Assessment:** `geo` (lat/lon) is absent from all pool schemas. This is a meaningful signal for local search: pools are physical locations and local search results give weight to geo coordinates.

**Fix:** Add lat/lon to `pool_metadata.json` (a one-time data entry task) and include in schema:
```json
"geo": {
  "@type": "GeoCoordinates",
  "latitude": {{ pool.lat }},
  "longitude": {{ pool.lng }}
}
```

---

#### F-012 | Homepage `og:title` and `og:description` not overridden in index.html
**Priority:** P2  
**Affected files:** `api/templates/index.html`  
**Live verification:** Homepage returns:
```
og:title: "badifrei.ch – Ist die Badi voll?"
title:    "Zürcher Schwimmbäder – Auslastung live & Prognose | badifrei.ch"
```
The title tag and og:title are out of sync. Social shares of the homepage show the old tagline, not the improved keyword-rich title.

**Fix:** Add to `index.html`:
```jinja2
{% block og_title %}Zürcher Schwimmbäder – Auslastung live & Prognose | badifrei.ch{% endblock %}
{% block og_description %}Echtzeit-Auslastung und KI-Prognosen für alle Zürcher Schwimmbäder. Schau vor dem Besuch nach — jede Minute aktualisiert.{% endblock %}
{% block twitter_title %}Zürcher Schwimmbäder – Auslastung live & Prognose{% endblock %}
```

---

#### F-013 | CSP is enforced mode — should be Report-Only initially
**Priority:** P2  
**Affected files:** `api/main.py` — `SecurityHeadersMiddleware`  
**Assessment:** The CSP is currently:
```
Content-Security-Policy: default-src 'self'; script-src 'self' cdn.jsdelivr.net 'unsafe-inline'; ...
```
This is enforced (not `Content-Security-Policy-Report-Only`). The policy uses `'unsafe-inline'` for scripts which is fairly permissive, and Chart.js loads from `cdn.jsdelivr.net` which is allowed. However, any future inline script or external resource not in the allowlist will silently break functionality.

**Note:** SEO_TASKS.md (TASK-SEO-005) specified Report-Only mode for initial deployment.

**Minor inconsistency:** `connect-src 'self'` — the JS makes API calls to `/api/current`, `/predict/range`, `/api/history` which are same-origin, so this is fine.

---

#### F-014 | Title tag renders with inline newline
**Priority:** P2 (cosmetic)  
**Affected files:** `api/templates/pool.html`  
**Assessment:** Live title for Letzigraben:
```
"Freibad Letzigraben Zürich – Auslastung & Beste Besuchszeit |\nbadifrei.ch"
```
The Jinja2 template has a line break inside `{% block title %}`. This usually renders fine in SERPs (browsers strip newlines from titles) but is technically malformed HTML.

**Fix:** Collapse the block title onto one line:
```jinja2
{% block title %}{{ pool.name }} {{ city_label }} – Auslastung & Beste Besuchszeit | badifrei.ch{% endblock %}
```

---

#### F-015 | `{% set city_map %}` defined AFTER its first use in pool.html
**Priority:** P2 (potential rendering risk)  
**Affected files:** `api/templates/pool.html`  
**Assessment:** In `pool.html`:
```
Line 2:  {% block title %}{{ city_map.get(pool.city, ...) }}  ← uses city_map
Line 4:  {% set city_map = {...} %}                           ← defines city_map
```
In Jinja2, template-level `{% set %}` in child templates is technically available to block rendering due to how the inheritance scope works, but this is an anti-pattern that varies by Jinja2 version. The fact it works now doesn't mean it will continue to work — and it's confusing to maintain.

**Fix:** Move `{% set city_map %}` to line 1 (before any block usage), or better yet, pass `city_label` from Python (see F-005 fix).

---

### P3 — Nice to Have

---

#### F-016 | WebSite schema lacks `@id` — prevents @graph entity linking
**Priority:** P3  
**Affected files:** `api/templates/base.html`  
**Fix:** Add `"@id": "https://badifrei.ch/#website"` to enable @graph cross-referencing with pool schemas.

---

#### F-017 | No breadcrumb navigation (visual) on pool pages
**Priority:** P3  
**Affected files:** `api/templates/pool.html`  
A visible breadcrumb nav improves UX and reinforces the BreadcrumbList schema. See F-008 fix for implementation.

---

#### F-018 | No seasonal content / blog
**Priority:** P3  
One article per season ("Freibad Zürich 2026 – alle Öffnungsdaten", "Hitzewelle: Welche Badi hat noch Platz?") would build topical authority. Low priority now but high ROI in summer.

---

#### F-019 | No link building outreach done
**Priority:** P3  
CrowdMonitor (ase.ch) is already credited in footer — reaching out for a reciprocal link is a 30-min task with potentially high domain authority payoff.

---

## Quick Wins (Under 1 Hour Total)

These five items can be fixed in a single focused session and unblock the most critical issues:

| # | Task | Time | Impact |
|---|---|---|---|
| **QW-1** | Add `{% block og_title %}` and `{% block twitter_title %}` to `pool.html` and `index.html` | 10 min | 🔴 Fixes all social share titles |
| **QW-2** | Fix `city_map` in `pool.html` — add missing cities including `"hunenberg": "Hünenberg"` | 5 min | 🔴 Fixes garbled titles on 10+ pools |
| **QW-3** | Create `og-preview.jpg` (1200×630) and `apple-touch-icon.png` (180×180) in `/api/static/` | 20 min | 🔴 Fixes broken OG image across entire site |
| **QW-4** | Add H2 headings to pool.html content sections | 10 min | 🟠 Improves heading structure on all 32 pool pages |
| **QW-5** | Add `BreadcrumbList` JSON-LD to pool page `{% block structured_data %}` | 15 min | 🟠 Enables breadcrumbs in SERPs |

---

## Strategic Opportunities

### SO-1: Dynamic OG Image with Live Occupancy
The most viral SEO play available: when someone shares a pool page during a heatwave, the link preview could show **"Letzigraben: aktuell 32% belegt 🟢"**. This drives click-through from social at exactly the moment of highest intent.

Implementation: Generate OG images server-side (Pillow or Playwright screenshot) with occupancy embedded in the image. Even a simple text overlay on a solid background would outperform a generic static image.

Alternatively, use meta `og:description` with live occupancy (already recommended in SEO_REPORT.md, still not done):
```python
og_description = f"{pool.name} – aktuell {current_pct}% belegt. Prognose und beste Besuchszeiten auf badifrei.ch."
```

### SO-2: "Jetzt geöffnet" filtered page
A URL like `/jetzt-geoeffnet` or a static page showing only currently open pools would:
- Rank for "Bäder Zürich jetzt geöffnet" / "offene Schwimmbäder Zürich"
- Provide a useful deep-link that people bookmark
- Generate social shares during peak summer days

### SO-3: District/Kreis hub pages  
"Schwimmbad Zürich Kreis 9" or "Badi Wiedikon" are longer-tail but zero-competition keywords. Adding `kreis` metadata to `pool_metadata.json` and building static district hub pages would capture this traffic with minimal effort.

### SO-4: Seasonal article (publish in May)
One article for summer 2026: *"Freibad Saison 2026 – alle Öffnungstermine Zürich"*. Targets searches like "wann öffnen die Freibäder Zürich 2026". Publish in April, includes a table of all Freibad pools with opening dates pulled from `pool_metadata.json`. Update annually.

### SO-5: PR during the first summer heatwave
The feature is genuinely newsworthy. A pitch to Tsüri.ch or 20min Zürich when temperatures hit 30°C: *"Welche Badi hat noch Platz? Diese KI weiss es."* One press article = 20–50 inbound links from credible Swiss sources.

---

## Priority Fix List — Top 5 Most Impactful

| Rank | Fix | Files | Effort | Why #N |
|---|---|---|---|---|
| **1** | Create `og-preview.jpg` + fix `og:title`/`twitter:title` blocks (F-001/F-002/F-003/F-004) | `pool.html`, `index.html`, `/static/` | 30 min | Fixes broken social sharing across entire site. Every share is currently serving wrong title + broken image. |
| **2** | Fix `city_map` to include all cities (F-005) | `pool.html` | 5 min | "Schwimmbad Rotkreuz Rotkreuz" and "Strandbad Hünenberg Hunenberg" look broken and unprofessional to both users and Google. |
| **3** | Add H2 headings to pool pages (F-006) | `pool.html` | 10 min | Heading structure is a primary on-page relevance signal. H1 → H3 jump is a crawlability red flag. |
| **4** | SSR current occupancy text (F-007) | `pool.html`, `main.py` | 1–2h | The core value proposition of the site ("is it full right now?") is invisible to Google. This single fix most improves the relevance of pool pages to their target queries. |
| **5** | Add BreadcrumbList schema + fix homepage og tags (F-008/F-012) | `pool.html`, `index.html` | 20 min | BreadcrumbList enables SERP breadcrumbs (CTR boost). Fixing homepage og tags aligns all sharing previews with the improved title. |

---

## SEO Score Comparison

| Category | SEO_REPORT.md (March 2026) | This Review (March 2026) | Delta |
|---|---|---|---|
| Technical SEO (tags, canonical, headers) | 55/100 | 72/100 | +17 |
| Structured data | 35/100 | 68/100 | +33 |
| Content (pool pages) | 10/100 | 65/100 | +55 |
| Content (homepage) | 45/100 | 62/100 | +17 |
| Internal linking | 20/100 | 65/100 | +45 |
| Performance / CWV | 40/100 | 62/100 | +22 |
| Social / OG | 30/100 | 15/100 | −15 (regression — og:image now 404) |
| **Overall** | **25/100** | **60/100** | **+35** |

The social/OG regression is real: the original report mentioned `og:image` as "assumed to exist — confirm", but now it's confirmed broken. The technical debt around static assets (og:image, apple-touch-icon) needs resolving before the site is share-ready.

**After top-5 fixes above are applied: estimated 73/100.**

---

## Appendix: Live Verification Snapshot

Taken: 2026-03-13

| Check | URL | Result |
|---|---|---|
| Homepage title | badifrei.ch | ✅ "Zürcher Schwimmbäder – Auslastung live & Prognose \| badifrei.ch" |
| Homepage og:title | badifrei.ch | ❌ "badifrei.ch – Ist die Badi voll?" (default, not overridden) |
| Pool title | /bad/LETZI-1 | ✅ "Freibad Letzigraben Zürich – Auslastung & Beste Besuchszeit \| badifrei.ch" (but with inline newline) |
| Pool og:title | /bad/LETZI-1 | ❌ "badifrei.ch – Ist die Badi voll?" (default, not overridden) |
| Pool canonical | /bad/LETZI-1 | ✅ `https://badifrei.ch/bad/LETZI-1` |
| Pool og:url | /bad/LETZI-1 | ✅ `https://badifrei.ch/bad/LETZI-1` |
| Pool meta description | /bad/LETZI-1 | ✅ Pool-specific, contains pool name + city |
| Pool H1 | /bad/LETZI-1 | ✅ "Freibad Letzigraben" |
| Pool H2 | /bad/LETZI-1 | ❌ None (0 H2 tags) |
| Pool H3 | /bad/LETZI-1 | ⚠️ "Weitere Bäder in Zürich" (should be H2) |
| Pool description text (SSR) | /bad/LETZI-1 | ✅ Present and crawlable |
| Pool opening hours (SSR) | /bad/LETZI-1 | ✅ HTML table present |
| Pool Beste Besuchszeiten (SSR) | /bad/LETZI-1 | ✅ Present (from weekly_insights) |
| Pool current occupancy (SSR) | /bad/LETZI-1 | ❌ JS-only, not in initial HTML |
| SportsActivityLocation schema | /bad/LETZI-1 | ✅ Valid + openingHours + capacity |
| SportsActivityLocation image | /bad/LETZI-1 | ❌ Points to 404 |
| FAQPage schema | /bad/LETZI-1 | ⚠️ Present but Q1 answer is fallback text (off-season) |
| BreadcrumbList schema | /bad/LETZI-1 | ❌ Missing |
| WebSite schema | all pages | ✅ Present |
| og:image | all pages | ❌ 404 |
| apple-touch-icon.png | all pages | ❌ 404 |
| Sitemap coverage | /sitemap.xml | ✅ 33 URLs, all with lastmod/changefreq/priority |
| robots.txt | /robots.txt | ✅ Correct Disallow rules |
| Security headers | /bad/LETZI-1 | ✅ X-Frame, X-Content-Type, Referrer, Permissions |
| City title bug | /bad/HUENENBERG-1 | ❌ "Strandbad Hünenberg Hunenberg" |
| City title bug | /bad/RISCH-1 | ❌ "Schwimmbad Rotkreuz Rotkreuz" |
| City title bug | /bad/MARZILI-1 | ❌ "Bern Marzili Bern" |

---

*Review completed 2026-03-13. The site has made substantial progress since the original audit — 
the content and structured data layers are now much stronger. The remaining issues are bugs, 
not architecture gaps, and most are quick fixes.*
