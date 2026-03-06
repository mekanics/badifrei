# SEO Audit Report — badifrei.ch
**Date:** March 2026  
**Auditor:** Jarvis (AI, orchestrated by j2y.dev)  
**Scope:** Full technical + content + pSEO audit  
**Site:** https://badifrei.ch  
**Stack:** FastAPI + Jinja2 + Chart.js + TimescaleDB  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Technical SEO Audit](#2-technical-seo-audit)
3. [Content SEO Audit](#3-content-seo-audit)
4. [pSEO Opportunities](#4-pseo-opportunities)
5. [Link Building & Authority](#5-link-building--authority)
6. [Prioritized Action Plan](#6-prioritized-action-plan)
7. [Implementation Code Snippets](#7-implementation-code-snippets)

---

## 1. Executive Summary

**badifrei.ch** has a genuinely unique value proposition: real-time + ML-predicted pool occupancy for Zürich's Bäder — a data product nobody else offers in this market. That's an enormous SEO asset hiding in plain sight.

### Current State: Promising Foundation, Under-Optimized Execution

The site has done the basics right: it has `lang="de-CH"`, canonical URLs, OG tags, a sitemap, robots.txt, and per-page Schema.org structured data. For a side project, that's already ahead of most.

The problems are structural and content-level:

- **Pool detail pages are almost invisible to Google.** The occupancy chart is entirely JS-rendered (Chart.js). Google sees the page shell but zero data — no numbers, no context, no text about the pool. These pages will not rank for pool-specific searches.
- **Title/description tags lack keyword depth.** `Freibad Letzigraben – badifrei.ch` is fine branding, but terrible for capturing "Freibad Letzigraben Zürich auslastung" searches.
- **OG tags are partially hardcoded.** Pool pages inherit the homepage's generic `og:description` instead of per-pool descriptions.
- **No server-side content on pool pages.** The only crawlable text on a pool page is the nav and footer. Everything meaningful (chart, capacity, occupancy) is loaded via JavaScript API calls.
- **URL structure includes `/dashboard/` prefix** — semantically wrong for a user-facing page, adds a path segment that dilutes keyword relevance.
- **Zero long-form content.** No FAQ, no "best time to visit" guides, no seasonal content. Google has nothing to assess topical authority.

### Key Wins

| ✅ Already working |
|---|
| `lang="de-CH"` on `<html>` |
| Per-pool canonical URLs |
| sitemap.xml + robots.txt present |
| Per-pool `SportsActivityLocation` structured data |
| Mobile viewport meta tag |
| OG image dimensions specified |
| Favicon + apple-touch-icon linked |
| Footer backlinks to data sources (good for E-E-A-T) |
| Descriptive pool UIDs in URLs (e.g. `freibad-letzigraben`) |

### Key Gaps

| ❌ Needs fixing |
|---|
| Chart data is JS-rendered → invisible to Google |
| Pool pages have no unique meta description |
| OG tags not fully overridden per pool |
| URL path `/dashboard/pools/` is semantically awkward |
| No FAQPage schema or rich result opportunities |
| No SSR text content (capacity, hours, typical busy times) |
| `og:url` hardcoded to homepage in base.html |
| No security headers (X-Frame-Options, CSP) |
| Missing hreflang (low priority but worth noting) |
| No internal linking between pool pages |
| No blog / seasonal content |

### Effort vs. Impact Summary

```
P0 — Fix pool page meta tags + add SSR text blocks  →  HIGH IMPACT, LOW EFFORT
P1 — Add SSR "Beste Besuchszeit" content block       →  HIGH IMPACT, MEDIUM EFFORT
P1 — Fix og:url + og:description per pool            →  HIGH IMPACT, LOW EFFORT
P2 — Add FAQPage schema per pool                     →  MEDIUM IMPACT, LOW EFFORT
P2 — Restructure URL from /dashboard/pools/ to /bad/ →  HIGH IMPACT, HIGH EFFORT
P3 — City expansion (Basel, Bern)                    →  HIGH IMPACT, HIGH EFFORT
```

---

## 2. Technical SEO Audit

### 2.1 Title Tags

#### Current Implementation

```html
<!-- base.html (default) -->
<title>{% block title %}badifrei.ch – Ist die Badi voll?{% endblock %}</title>

<!-- index.html -->
{% block title %}badifrei.ch – Zürcher Bäder live{% endblock %}

<!-- pool.html -->
{% block title %}{{ pool.name }} – badifrei.ch{% endblock %}
```

#### Assessment

| Page | Current Title | Length | SEO Quality |
|---|---|---|---|
| Homepage | `badifrei.ch – Zürcher Bäder live` | 34 chars | ⚠️ OK — weak on keywords |
| Pool (e.g. Letzigraben) | `Freibad Letzigraben – badifrei.ch` | 34 chars | ❌ Missing city + intent keywords |

**Issues:**
- Homepage title doesn't include "Auslastung", "Schwimmbad", "Zürich" in a natural way
- Pool titles are `[name] – [brand]` — the most common lazy pattern. Misses searchers' actual queries.
- Target keyword "Badi Letzigraben voll" or "Freibad Letzigraben Auslastung" is completely absent

**Recommended titles:**

```
Homepage: "Bäder Zürich – Auslastung live & Vorhersage | badifrei.ch"
          (53 chars — fits in ~600px)

Pool:     "Freibad Letzigraben Zürich – Auslastung & Beste Besuchszeit"
          (59 chars — descriptive, keyword-rich, not spammy)
```

The pool page title formula:
```
{pool.name} {city} – Auslastung & Beste Besuchszeit
```
or for indoor pools:
```
{pool.name} {city} – Auslastung, Öffnungszeiten & Prognose
```

---

### 2.2 Meta Descriptions

#### Current Implementation

```html
<!-- base.html (default, used by pool pages too) -->
<meta name="description" content="{% block description %}Echtzeit-Belegung und Vorhersagen für Zürichs Schwimmbäder. Schau bevor du gehst.{% endblock %}">
```

**Critical issue:** `pool.html` does **not override** the `{% block description %}`. Every pool page has the identical generic description. Google will either ignore it or auto-generate from page content (which is also thin).

**Pool pages need unique descriptions:**

```
Freibad Letzigraben Zürich – Auslastung jetzt live. Wann ist es am wenigsten voll? Prognose für heute mit KI-Modell. Öffnungszeiten & Kapazität.
(143 chars — stays under 160)
```

Formula:
```
{pool.name} {city} – Auslastung live. Wann ist es am ruhigsten? KI-Prognose, Öffnungszeiten & Kapazität auf badifrei.ch.
```

---

### 2.3 Open Graph & Twitter Card Tags

#### Current Issues

**1. `og:url` is hardcoded to homepage in `base.html`:**
```html
<meta property="og:url" content="https://badifrei.ch">  <!-- WRONG for pool pages -->
```
This tells Facebook/LinkedIn that every pool page is actually the homepage. Canonical confusion for social crawlers.

**2. `og:description` is not in a template block:**
```html
<meta property="og:description" content="Echtzeit-Belegung und Vorhersagen für Zürichs Schwimmbäder. Schau bevor du gehst.">
```
This cannot be overridden by child templates. Every page shares this generic description.

**3. Single `og:image` for all pages.** Pool pages should ideally have pool-specific OG images, but at minimum the current generic one is acceptable as a fallback.

**4. Twitter card `og:type` should remain `website` for pool pages** — `article` would be wrong, but `website` is fine.

**Required fixes:**

```html
<!-- base.html -->
<meta property="og:url" content="https://badifrei.ch{% block og_url_path %}/{% endblock %}">
<meta property="og:description" content="{% block og_description %}Echtzeit-Belegung und Vorhersagen für Zürichs Schwimmbäder. Schau bevor du gehst.{% endblock %}">

<!-- pool.html -->
{% block og_url_path %}/dashboard/pools/{{ pool.uid }}{% endblock %}
{% block og_description %}{{ pool.name }} – Auslastung live und Prognose auf badifrei.ch. Finde den besten Zeitpunkt für deinen Besuch.{% endblock %}
```

---

### 2.4 Canonical URLs

#### Current Implementation

```html
<!-- base.html -->
<link rel="canonical" href="https://badifrei.ch{% block canonical_path %}/{% endblock %}">

<!-- pool.html -->
{% block canonical_path %}/dashboard/pools/{{ pool.uid }}{% endblock %}
```

**Assessment: ✅ Correctly implemented.** Each pool page self-canonicalizes. Homepage defaults to `/`. This is good.

**Minor concern:** Ensure the sitemap.xml URLs exactly match canonical URLs (same path, no trailing slash differences). Any mismatch between sitemap and canonical causes confusion.

---

### 2.5 Structured Data

#### Current Implementation

**base.html** — site-wide `WebSite` schema:
```json
{
  "@context": "https://schema.org",
  "@type": "WebSite",
  "name": "badifrei.ch",
  "url": "https://badifrei.ch",
  "description": "Echtzeit-Belegung und Vorhersagen für Zürichs Schwimmbäder",
  "inLanguage": "de-CH"
}
```

**pool.html** — per-pool `SportsActivityLocation`:
```json
{
  "@context": "https://schema.org",
  "@type": "SportsActivityLocation",
  "name": "{{ pool.name }}",
  "description": "Schwimmbad in Zürich — aktuelle Auslastung und Tagesprognose",
  "url": "https://badifrei.ch/dashboard/pools/{{ pool.uid }}",
  "address": {
    "@type": "PostalAddress",
    "addressLocality": "Zürich",
    "addressCountry": "CH"
  }
}
```

#### Issues & Improvements

**Issue 1: `description` is hardcoded** — "Schwimmbad in Zürich" is wrong for Luzern or Zug pools. Should use `{{ pool.city }}` or be conditional.

**Issue 2: Missing fields** in `SportsActivityLocation` that Google can use:
- `openingHoursSpecification` — you have the opening hours data, use it!
- `telephone` (if available)
- `geo` coordinates (if available)
- `maximumAttendeeCapacity` — you have this!
- `image` (pool photo if available)

**Issue 3: No `FAQPage` schema.** This is a quick win for rich results. You can generate FAQ from known patterns:

```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Wann ist {{ pool.name }} am wenigsten voll?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Laut unserer KI-Prognose ist {{ pool.name }} typischerweise {{ busiest_day_text }} am vollsten und {{ quietest_time_text }} am ruhigsten."
      }
    },
    {
      "@type": "Question",
      "name": "Wie viele Personen passen in {{ pool.name }}?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "{{ pool.name }} hat eine Kapazität von {{ pool.capacity }} Personen."
      }
    }
  ]
}
```

**Issue 4: No `WebSite` `SearchAction` (Sitelinks Searchbox).** Low priority but possible if you add a search route.

**Issue 5: Both `WebSite` and `SportsActivityLocation` schemas could be combined in an array** using `@graph` for cleaner markup.

**Recommended improved `SportsActivityLocation`:**

```json
{
  "@context": "https://schema.org",
  "@type": "SportsActivityLocation",
  "name": "{{ pool.name }}",
  "description": "{{ pool.name }} in {{ pool.city }} — aktuelle Auslastung, Tagesprognose und Öffnungszeiten.",
  "url": "https://badifrei.ch/dashboard/pools/{{ pool.uid }}",
  "maximumAttendeeCapacity": {{ pool.capacity }},
  "address": {
    "@type": "PostalAddress",
    "addressLocality": "{{ pool.city }}",
    "addressCountry": "CH"
  },
  "openingHoursSpecification": [
    {% for day in pool.opening_hours %}
    {
      "@type": "OpeningHoursSpecification",
      "dayOfWeek": "https://schema.org/{{ day.schema_day }}",
      "opens": "{{ day.open }}",
      "closes": "{{ day.close }}"
    }{% if not loop.last %},{% endif %}
    {% endfor %}
  ]
}
```

---

### 2.6 Sitemap & robots.txt

#### robots.txt (current — allows all, points to sitemap)

```
User-agent: *
Allow: /
Sitemap: https://badifrei.ch/sitemap.xml
```

**Assessment: ✅ Correct.** No pages need to be blocked (the JSON API endpoints at `/api/*` and `/predict/*` are crawlable but Google won't rank them, which is fine — they don't waste crawl budget significantly).

**Improvement:** You could explicitly disallow API paths to save crawl budget:
```
User-agent: *
Allow: /
Disallow: /api/
Disallow: /predict/
Disallow: /health
Disallow: /pools
Sitemap: https://badifrei.ch/sitemap.xml
```

#### sitemap.xml

**Assessment: ✅ Exists and covers homepage + all pool pages.** 

**Missing from sitemap:**
- `<lastmod>` timestamps — Google uses these to prioritize crawls
- `<changefreq>` hints — pool pages update frequently (live data), homepage very frequently
- `<priority>` values — homepage should be 1.0, pool pages 0.8

**Improved sitemap structure:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://badifrei.ch/</loc>
    <changefreq>always</changefreq>
    <priority>1.0</priority>
    <lastmod>2026-03-01</lastmod>
  </url>
  {% for pool in pools %}
  <url>
    <loc>https://badifrei.ch/dashboard/pools/{{ pool.uid }}</loc>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
    <lastmod>{{ today_iso }}</lastmod>
  </url>
  {% endfor %}
</urlset>
```

---

### 2.7 URL Structure

#### Current: `/dashboard/pools/{pool_uid}`

**Issues:**
1. **`/dashboard/`** is a UI concept, not a content concept. It signals "app" not "content" to crawlers. It's also two path segments before the keyword-rich slug.
2. The path is long: `/dashboard/pools/freibad-letzigraben` = 33 characters of path.
3. `/pools/` in the path is slightly redundant — you could go flatter.

**Recommended alternatives (best first):**

| Option | URL | Notes |
|---|---|---|
| A (Best) | `/bad/freibad-letzigraben` | Short, clean, Swiss word "Bad" |
| B (Good) | `/schwimmbad/freibad-letzigraben` | More descriptive keyword |
| C (OK) | `/pools/freibad-letzigraben` | Removes `/dashboard/` segment |
| D (Current) | `/dashboard/pools/freibad-letzigraben` | ❌ Keep only if migration cost too high |

> **Recommendation:** Migrate to `/bad/{uid}` or `/schwimmbad/{uid}`. This is the highest SEO ROI URL change possible. However, it requires 301 redirects from old URLs and sitemap update. If pools haven't been indexed yet (new site), do this now before Google caches the old structure.

**Check if pages are indexed yet:**
```
site:badifrei.ch/dashboard/pools/
```
If few/none are indexed, the migration cost is near-zero.

---

### 2.8 Page Speed & Core Web Vitals

#### Chart.js Rendering

**Critical for SEO:** Chart.js renders entirely client-side. The typical sequence:
1. Browser downloads HTML (fast, SSR)
2. Browser downloads `Chart.js` (external JS, can be large: ~200KB minified)
3. Browser executes JS, calls `/api/history` and `/predict/range` APIs
4. Chart renders

**CWV Impact:**
- **LCP (Largest Contentful Paint):** If the chart is the largest element, LCP will be 3-5+ seconds. Google's threshold is <2.5s.
- **TBT (Total Blocking Time):** Chart.js + XGBoost API calls block the main thread.
- **CLS (Cumulative Layout Shift):** Chart container probably needs explicit dimensions to avoid layout shift.

**Fixes:**
1. **Pin chart container dimensions in CSS** to prevent CLS:
   ```css
   .chart-container {
     width: 100%;
     height: 400px; /* explicit, not dynamic */
     contain: layout;
   }
   ```

2. **Load Chart.js from CDN with `defer`** or use the npm bundle with tree-shaking to reduce JS size.

3. **Preload critical API calls:**
   ```html
   <link rel="preload" href="/api/history?pool={{ pool.uid }}&date={{ today }}" as="fetch" crossorigin>
   ```

4. **Consider SSR for the initial data state** — serve the current occupancy and today's predictions embedded in the HTML. Chart.js can then initialize with that data immediately without an API call. (See Section 7 for implementation.)

5. **Lazy-load Chart.js** — show a static "loading" placeholder, then load the chart script after LCP. Since the chart isn't LCP-critical if you show static summary text first:
   ```html
   <script defer src="/static/chart.min.js"></script>
   ```

---

### 2.9 Missing Static Assets

| Asset | Path | Status | Impact |
|---|---|---|---|
| OG Preview Image | `/static/og-preview.jpg` | ⚠️ Assumed exists — confirm | HIGH: social shares look broken without it |
| Apple Touch Icon | `/static/apple-touch-icon.png` | ⚠️ Assumed exists — confirm | MEDIUM: affects PWA homescreen quality |
| Favicon | `/static/favicon.ico` | ✅ Linked correctly | — |

**Action:** Verify both files exist and return 200. A broken `og:image` means blank/ugly social previews, crushing click-through from social.

**For pool-specific OG images:** You could generate them server-side using a headless screenshot tool or a static design template per pool type (Freibad vs. Hallenbad). Even a text overlay on a pool photo would significantly improve click-through rates from social shares.

---

### 2.10 Security Headers

There's no mention of security headers in the FastAPI config. This affects:
1. **Google's experience** — not a direct ranking factor, but affects trust signals
2. **Chrome warnings** — CSP violations appear in browser console
3. **Security scanners** — sites without headers get flagged in audits

**Missing headers (add via FastAPI middleware):**

```python
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        # Note: CSP needs to allow Chart.js CDN and your API endpoints
        # Start with report-only mode
        response.headers["Content-Security-Policy-Report-Only"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self';"
        )
        return response
```

Not a ranking factor directly, but important for E-E-A-T signals (trustworthy site).

---

### 2.11 hreflang Considerations

**Current state:** Single language (`de-CH`), no hreflang tags.

**Assessment:** For a single-language site targeting de-CH users, no hreflang is needed. Google handles this via `lang="de-CH"` on the `<html>` tag. ✅

**If you expand to French-speaking Switzerland** (fr-CH), you'd need:
```html
<link rel="alternate" hreflang="de-CH" href="https://badifrei.ch/bad/freibad-letzigraben">
<link rel="alternate" hreflang="fr-CH" href="https://badifrei.ch/fr/piscine/freibad-letzigraben">
<link rel="alternate" hreflang="x-default" href="https://badifrei.ch/">
```

For now: not needed. Defer until multilingual expansion.

---

## 3. Content SEO Audit

### 3.1 Keyword Opportunities

**Swiss German pool search intent analysis:**

The typical user query falls into these categories:

| Intent | Example Queries | Current Coverage |
|---|---|---|
| **Is it busy now?** | "Freibad Letzigraben voll", "Badi Zürich auslastung jetzt" | ❌ No indexed text content |
| **When to go?** | "Freibad Letzigraben wann am wenigsten voll", "beste Zeit Badi Zürich" | ❌ Not addressed at all |
| **Is it open?** | "Freibad Letzigraben geöffnet", "Hallenbad City Öffnungszeiten" | ⚠️ Opening hours in app but not crawlable |
| **Discover** | "Schwimmbäder Zürich", "Bäder Zürich Übersicht" | ⚠️ Homepage vaguely covers this |
| **Specific pool info** | "Freibad Letzigraben Kapazität", "Hallenbad Uto Adresse" | ❌ Not crawlable (JS-rendered) |

**High-value keywords to target:**

```
Tier 1 (Homepage):
- Schwimmbäder Zürich (720/mo est.)
- Bäder Zürich Auslastung
- Badi Zürich voll
- Freibad Zürich aktuell

Tier 2 (Pool pages):
- [Pool Name] Auslastung
- [Pool Name] Öffnungszeiten
- [Pool Name] voll
- [Pool Name] Zürich
- Freibad Letzigraben aktuell
- Hallenbad City Zürich Auslastung

Tier 3 (Long-tail, content):
- Wann ist [Pool Name] am wenigsten voll
- [Pool Name] Tipp beste Besuchszeit
- Schwimmbad Zürich Prognose
- Badi Zürich Vorhersage KI
```

---

### 3.2 H1/H2 Structure

#### Homepage

```html
<h1 class="page-title">Zürcher Bäder 🏊</h1>
<p class="subtitle">Ist deine Lieblingsbadi gerade voll? ...</p>
```

**Assessment:** ✅ H1 is present, above the fold, relevant. ⚠️ But "Zürcher Bäder" alone is weak — "Zürcher Schwimmbäder – Auslastung live" would be stronger.

**Missing:** H2 subheadings per city section (Zürich, Luzern, etc.) — these help structure for crawlers and users.

**Improved structure:**
```html
<h1>Zürcher Schwimmbäder – Auslastung live & Prognose</h1>
<p>Finde heraus, ob deine Lieblingsbadi gerade voll ist — jede Minute aktualisiert.</p>

<h2>Freibäder Zürich</h2>
<!-- Freibad cards -->

<h2>Hallenbäder Zürich</h2>
<!-- Hallenbad cards -->

<h2>Weitere Städte</h2>
<!-- Luzern, Zug, Wengen cards -->
```

#### Pool Detail Page

Currently has no meaningful server-rendered headings — everything is the chart. At minimum:

```html
<h1>{{ pool.name }}</h1>
<h2>Aktuelle Auslastung</h2>
<!-- SSR occupancy block -->
<h2>Tagesprognose</h2>
<!-- Chart -->
<h2>Beste Besuchszeiten</h2>
<!-- SSR summary of typical busy/quiet hours -->
<h2>Öffnungszeiten</h2>
<!-- Opening hours table -->
```

---

### 3.3 Missing Content

**What's absent that would dramatically help SEO:**

#### 1. Static Pool Descriptions (Highest ROI)
Each pool page needs 100-200 words of factual, unique text about the pool:
- Type (Freibad / Hallenbad / Seebad)
- Facilities (lanes, outdoor area, kids zone, sauna, restaurant)
- Location / how to get there (public transport)
- Typical visitors (families, fitness swimmers, etc.)

This content is static, SSR, and gives Google something to index.

#### 2. "Beste Besuchszeiten" Section
Generated server-side from historical ML model data. For each pool:
- "Erfahrungsgemäß ist es **samstags um 14-16 Uhr** am vollsten."
- "Am ruhigsten: **Werktags vor 10 Uhr** und nach 19 Uhr."
- "Heute wird es voraussichtlich **um 15 Uhr** am stärksten frequentiert."

This is your killer pSEO feature. It turns raw data into crawlable, valuable content.

#### 3. FAQ Section (Per Pool)
Short FAQs answering the most common questions. Enables FAQPage rich results in Google SERPs.

#### 4. Seasonal / Event Content
- "Freibad Saison 2026" — when do outdoor pools open/close?
- "Hitzewelle: Welche Badi ist heute am wenigsten voll?"
- "Ferienzeiten: Wann sind die Bäder am vollsten?"

A simple blog or news section (even 1-2 articles per season) would dramatically improve topical authority.

---

### 3.4 Thin Content Risk

**Current pool pages are critically thin** for search engines:

What Google currently sees on a pool page:
```
Nav: 🏊 badifrei.ch
[Pool name heading — probably]
[Nothing else — chart is JS-rendered]
Footer: [text about data sources, XGBoost, j2y.dev]
```

Google's quality guidelines consider pages with minimal unique, helpful content as "thin." Pool pages in their current state are at real risk of:
- Not being indexed (Google drops thin pages from index)
- Being indexed but never ranking (no content relevance signal)
- Potentially dragging down overall site quality score

**The fix is SSR content** — see Section 4.4 and Section 7 for implementation.

---

### 3.5 Multilingual Opportunity

**Short-term (0-6 months): Not recommended.** Focus on making German content excellent first.

**Medium-term (6-18 months):**

| Market | Language | Priority | Justification |
|---|---|---|---|
| Zürich area | de-CH | ✅ Current | Primary market |
| German tourist | de-DE | 🔵 Low | Tourists visiting Zürich in summer |
| Romandie pools | fr-CH | 🟡 Medium | If adding Geneva/Lausanne pools |
| Ticino pools | it-CH | 🟠 Low | Smaller market |

**Recommendation:** When/if you add Basel or Bern, stick to de-CH. Only add fr-CH if you add Romandie pools (Geneva, Lausanne, Bern's French area). The hreflang implementation would be required at that point.

---

## 4. pSEO Opportunities

### 4.1 Pool Pages as Programmatic Landing Pages — Current State

**Assessment: The architecture is right; the execution is wrong.**

You have 20 pools × unique URLs = 20 potential landing pages. Each pool already has:
- ✅ Unique URL with descriptive slug
- ✅ Canonical tag
- ✅ Pool-specific structured data
- ✅ Real data backing (live + predicted occupancy)
- ❌ No crawlable content
- ❌ No unique meta description
- ❌ Identical OG description for all

The programmatic infrastructure is there. The content layer is missing.

**What makes a great pSEO pool page:**
```
[Crawlable]   Pool name, city, type
[Crawlable]   100-200 words pool description
[Crawlable]   Opening hours (structured table)
[Crawlable]   Current occupancy status (SSR at render time)
[Crawlable]   Typical busy/quiet hours summary
[Crawlable]   Capacity number
[Dynamic]     Chart.js visualization (enhancement, not core content)
[Crawlable]   FAQ section
[Crawlable]   How to get there
```

---

### 4.2 Keyword Patterns by Page Type

#### Homepage keyword targets:
```
Primary: "Schwimmbäder Zürich Auslastung"
Secondary: "Bäder Zürich live", "Badi Zürich voll heute"
Long-tail: "welche Badi in Zürich ist gerade nicht voll"
```

#### Pool page keyword targets (per pool, example: Freibad Letzigraben):
```
Primary: "Freibad Letzigraben Auslastung"
Secondary: "Freibad Letzigraben Zürich", "Letzigraben Freibad voll"
Long-tail: "Freibad Letzigraben wann am wenigsten voll"
           "Freibad Letzigraben Öffnungszeiten 2026"
           "Freibad Letzigraben Kapazität"
```

#### "Best time" content pages (new, high opportunity):
```
"Wann sind Zürcher Freibäder am wenigsten voll?"
"Beste Zeit für Badi Zürich – Prognose"
"Hallenbad Zürich Stoßzeiten"
```

---

### 4.3 Missing pSEO Angles

#### Angle 1: "Wann ist [Pool] am ruhigsten?" — Best Time to Visit
**Search volume:** Medium (but high intent)  
**Implementation:** Generate a summary from your ML model's historical predictions. "Erfahrungsgemäß Dienstag und Mittwoch morgens am ruhigsten." — render SSR.

#### Angle 2: Day-of-Week Pattern Pages
**Example:** "Freibad Letzigraben am Wochenende" — how busy is it on weekends?  
**Implementation:** Pre-compute weekly patterns and render them in a "Typischer Wochenverlauf" section.

#### Angle 3: Seasonal Comparisons
**Example:** "Schwimmbäder Zürich im Sommer" — comprehensive guide  
**Implementation:** Blog/content page with internal links to all Freibad pool pages.

#### Angle 4: Pool Type Comparison Pages
**Example:** "Freibäder vs. Hallenbäder Zürich — welches ist gerade weniger voll?"  
**Implementation:** A comparison/hub page with live data for both types.

#### Angle 5: Neighbourhood / District Targeting
**Example:** "Schwimmbad Wiedikon", "Badi Zürich West"  
**Implementation:** Add district/Kreis metadata to pools. Create district hub pages.

#### Angle 6: "Jetzt geöffnet" Filter
**Example:** "Welche Bäder in Zürich sind jetzt geöffnet?"  
**Implementation:** A filtered view of the homepage. Could rank for "offene Bäder Zürich jetzt".

---

### 4.4 Server-Side Rendering of Chart Data — The Critical Fix

**This is the single most impactful technical SEO fix for pool pages.**

Currently, Google Googlebot:
1. Fetches the pool page HTML
2. Sees: nav + footer (no meaningful content)
3. May or may not execute JavaScript (Google does render JS, but with delay)
4. Even if JS runs, the `/api/history` call happens after render — may time out for Google

**Solution: Inject initial data into the page HTML, render a text summary server-side.**

**What to SSR:**
```python
# In your FastAPI pool route:
current_occupancy = await get_current_occupancy(pool_uid)
today_predictions = await get_predictions_today(pool_uid)
typical_patterns = await get_typical_patterns(pool_uid)  # pre-computed

# Pass to template
return templates.TemplateResponse("pool.html", {
    "pool": pool,
    "current_occupancy": current_occupancy,
    "today_predictions_json": json.dumps(today_predictions),  # for Chart.js init
    "typical_quiet_time": typical_patterns.quietest_hour,
    "typical_busy_time": typical_patterns.busiest_hour,
    "typical_quiet_day": typical_patterns.quietest_day,
})
```

Then in `pool.html`, render a visible text summary:
```html
<div class="occupancy-summary" aria-live="polite">
  <p class="occupancy-current">
    Aktuelle Auslastung: <strong>{{ current_occupancy }}%</strong>
    — {% if current_occupancy < 40 %}🟢 Wenig los{% elif current_occupancy < 70 %}🟡 Mässig voll{% else %}🔴 Sehr voll{% endif %}
  </p>
</div>

<section class="visit-tips">
  <h2>Beste Besuchszeiten für {{ pool.name }}</h2>
  <p>
    Erfahrungsgemäß ist <strong>{{ pool.name }}</strong> 
    am <strong>{{ typical_busy_day }}</strong> gegen <strong>{{ typical_busy_hour }} Uhr</strong> am stärksten besucht.
    Am ruhigsten ist es <strong>{{ typical_quiet_day }}</strong> 
    {% if typical_quiet_hour %}vor <strong>{{ typical_quiet_hour }} Uhr</strong>{% endif %}.
  </p>
  <p>Unsere KI-Prognose für heute deutet auf eine Spitzenbelegung 
    um <strong>{{ today_peak_hour }} Uhr</strong> hin.</p>
</section>
```

This gives Google ~150 words of unique, data-driven content per pool page. **Immediately indexes. Immediately differentiated from every other pool website.**

---

### 4.5 City Expansion Opportunity

**Potential expansion markets:**

| City | Pools Available | pSEO Opportunity | Effort |
|---|---|---|---|
| Basel | ~8 public pools | High — no competitor with live data | Medium |
| Bern | ~6 public pools | High | Medium |
| Geneva | ~10 public pools | High — fr-CH market | High (multilingual) |
| Winterthur | ~4 pools | Medium | Low (same canton, same data sources?) |
| Luzern | Already present | ✅ | — |
| Zug | Already present | ✅ | — |

**pSEO math:** Each city × 6-10 pools = 12-20 new landing pages per city, each targeting local search terms. Basel alone could add "Schwimmbäder Basel Auslastung" as a primary keyword for a new hub page.

**Prioritize:** Winterthur (likely uses same Stadt Zürich data format) and Basel (large German-speaking city with no live data competitor).

---

### 4.6 Content Templates for Pool Pages

**Template for 150-word pool description block (generate once per pool, store in `pool_metadata.json`):**

```
{pool.name} ist ein {pool.type} in {pool.district}, Zürich. 
Das Bad hat eine Kapazität von {pool.capacity} Personen und bietet 
{pool.facilities}. Es ist {pool.transport} erreichbar.

In der Regel öffnet {pool.name} {pool.season_info}. 
Die Badi ist besonders bei Familien aus dem Kreis {pool.kreis} 
und Fitness-Schwimmerinnen und -Schwimmern beliebt.

Auf badifrei.ch siehst du jederzeit, wie voll {pool.name} gerade ist — 
und unsere KI-Prognose zeigt dir, wann sich ein Besuch am meisten lohnt.
```

This is a one-time content exercise: write 20 descriptions, store in JSON, render SSR. Total time: ~2 hours.

---

## 5. Link Building & Authority

### 5.1 Internal Linking

**Current state:** There is essentially no internal linking between pool pages. The homepage links to pool pages (via pool cards), but pool pages don't link to each other or back to a hub structure.

**Missing internal links:**

1. **"Ähnliche Bäder" section on pool pages:**
   ```html
   <section class="similar-pools">
     <h3>Weitere Bäder in Zürich</h3>
     <ul>
       {% for nearby_pool in pool.nearby %}
       <li><a href="/dashboard/pools/{{ nearby_pool.uid }}">{{ nearby_pool.name }}</a> 
           — {{ nearby_pool.current_occupancy }}% belegt</li>
       {% endfor %}
     </ul>
   </section>
   ```
   This creates a link graph between pool pages, distributes PageRank, and gives users a useful "where else could I go?" option.

2. **Breadcrumb navigation:**
   ```html
   <nav aria-label="Breadcrumb">
     <ol class="breadcrumb">
       <li><a href="/">Bäder Zürich</a></li>
       <li><a href="/?type=freibad">Freibäder</a></li>
       <li aria-current="page">{{ pool.name }}</li>
     </ol>
   </nav>
   ```
   Add `BreadcrumbList` schema too:
   ```json
   {
     "@type": "BreadcrumbList",
     "itemListElement": [
       {"@type": "ListItem", "position": 1, "name": "Startseite", "item": "https://badifrei.ch/"},
       {"@type": "ListItem", "position": 2, "name": "{{ pool.name }}", "item": "https://badifrei.ch/dashboard/pools/{{ pool.uid }}"}
     ]
   }
   ```

3. **City/type hub pages** (new pages):
   - `/freibaeder-zuerich` — links to all Freibad pool pages
   - `/hallenbaeeder-zuerich` — links to all Hallenbad pool pages
   These act as internal link hubs and rank for type-based queries.

---

### 5.2 Backlink Opportunities

**Tier 1 — Highest authority, realistic to get:**

| Source | Type | How to Get It |
|---|---|---|
| **badi-info.ch** | Data source — you should have a reciprocal link | You already credit them in footer. Email them: "Wir nutzen eure Daten und haben euch verlinkt — wäre ein Link zu badifrei.ch von eurer Seite möglich?" |
| **Stadt Zürich website** | .ch government domain — very high authority | Submit as a useful third-party resource. Contact via their contact form. Unlikely but possible. |
| **Zürich Tourism / MySwitzerland** | Tourism authority | Submit via their press/resource contacts |

**Tier 2 — Medium effort, medium authority:**

| Source | Type | Notes |
|---|---|---|
| Swiss Reddit (r/zurich) | Community | Post "built a tool to see if the Badi is voll" — authentic, useful |
| Zürcher Lokalmedien (20min, NZZ Lokal, Tsüri) | Press | "Zürich Startup baut KI-Tool für Badi-Auslastung" — genuinely newsworthy story |
| Swiss Dev community (dev.to, local Slack groups) | Tech community | The tech angle (FastAPI + XGBoost + TimescaleDB) is worth a writeup |
| Zürich Facebook groups (Zürich Life, Expats in Zürich) | Community | Share during summer heatwave — natural viral moment |

**Tier 3 — Long-term, low-effort signals:**

- Google Business Profile: Create one for "badifrei.ch" as a website/tool
- Schema.org attribution: already partially done via footer credits
- GitHub repo: If open-source, add to README — GitHub links are nofollow but drive traffic

**The PR angle that actually works:**  
Send a pitch to Tsüri.ch or 20min Zürich in June when the first heatwave hits. Headline writes itself: *"Welche Badi hat noch Platz? Diese Website weiss es."* One local article = dozens of organic backlinks.

---

### 5.3 Social Sharing Optimization

**Current state:** OG tags exist, but as noted they're not pool-specific. This limits viral sharing.

**The viral sharing moment:** During a heatwave, someone shares a specific pool page: "Letzigraben ist gerade nur 30% voll!" — but the link preview shows the generic homepage description. Click-through rate will be low.

**Fixes:**
1. **Pool-specific OG descriptions** (see Section 2.3)
2. **Dynamic OG description with real-time occupancy:**
   ```python
   # In route handler, before rendering:
   og_description = f"{pool.name} – aktuell {current_occupancy}% belegt. " \
                   f"Prognose und beste Besuchszeiten auf badifrei.ch."
   ```
   This makes every shared link show live data in the preview. **Extremely shareable.**

3. **Twitter/X card:** Already using `summary_large_image`, which is correct. Ensure the OG image actually renders the pool or a branded graphic (not just a generic photo).

4. **WhatsApp sharing:** WhatsApp uses OG tags too. A pool page that shows "Letzigraben: jetzt 25% voll 🟢" in the preview will get clicked.

---

## 6. Prioritized Action Plan

### P0 — Critical (Do This Week)

These issues directly prevent pool pages from ranking. Zero SEO benefit until fixed.

| # | Issue | Fix | Effort | Impact |
|---|---|---|---|---|
| P0.1 | Pool pages have no crawlable content | Add SSR text blocks: current occupancy, opening hours, typical patterns, pool description | 4-6h | 🔴 Critical |
| P0.2 | Pool pages have no unique meta description | Override `{% block description %}` in `pool.html` with pool-specific text | 1h | 🔴 Critical |
| P0.3 | `og:url` hardcoded to homepage | Move to template block in `base.html` | 30min | 🔴 Critical |
| P0.4 | `og:description` not overridable | Convert to template block | 30min | 🟠 High |
| P0.5 | Verify og-preview.jpg + apple-touch-icon exist | Check static file serving | 15min | 🟠 High |

---

### P1 — High Value (This Month)

| # | Issue | Fix | Effort | Impact |
|---|---|---|---|---|
| P1.1 | Improve pool page title tags | Use keyword-rich formula with city + "Auslastung" | 1h | 🟠 High |
| P1.2 | Add H2 structure to pool pages | Structure: Auslastung / Prognose / Beste Zeiten / Öffnungszeiten | 2h | 🟠 High |
| P1.3 | Improve homepage H1 and title | Add "Auslastung" and "Prognose" to title/H1 | 30min | 🟡 Medium |
| P1.4 | Improve `SportsActivityLocation` schema | Add opening hours, capacity, address corrections | 2h | 🟡 Medium |
| P1.5 | Add "Ähnliche Bäder" section to pool pages | Internal linking between pool pages | 2h | 🟡 Medium |
| P1.6 | Add breadcrumbs with `BreadcrumbList` schema | Breadcrumb nav + schema | 2h | 🟡 Medium |
| P1.7 | Add security headers middleware | FastAPI middleware | 1h | 🟡 Medium |
| P1.8 | Improve sitemap with lastmod + changefreq | Update sitemap generation | 1h | 🟡 Medium |
| P1.9 | Add pool descriptions to `pool_metadata.json` | Write 20 × 150-word descriptions | 3h | 🟠 High |

---

### P2 — Medium Term (Next 1-3 Months)

| # | Task | Effort | Impact |
|---|---|---|---|
| P2.1 | Write "Beste Besuchszeiten" algorithm to SSR typical patterns | 4h | 🟠 High |
| P2.2 | Add FAQPage schema to pool pages | 3h | 🟡 Medium |
| P2.3 | Create Freibäder and Hallenbäder hub pages for internal linking | 4h | 🟡 Medium |
| P2.4 | Evaluate URL restructure `/dashboard/pools/` → `/bad/` | 2h planning + migration | 🟠 High |
| P2.5 | Contact badi-info.ch for reciprocal link | 30min | 🟠 High (authority) |
| P2.6 | Fix Chart.js CLS with explicit container dimensions | 1h | 🟡 Medium |
| P2.7 | Implement dynamic OG description with live occupancy | 2h | 🟡 Medium |
| P2.8 | Disallow API/health paths in robots.txt | 15min | 🟢 Low |

---

### P3 — Long Term (3-12 Months)

| # | Task | Effort | Impact |
|---|---|---|---|
| P3.1 | City expansion: Winterthur or Basel pools | High | 🔴 Critical (doubles content) |
| P3.2 | Write 2-3 seasonal blog/guide articles | Medium | 🟡 Medium |
| P3.3 | PR outreach to Tsüri.ch / 20min Zürich during summer | Medium | 🟠 High (backlinks) |
| P3.4 | Pool-specific OG images (per pool type) | High | 🟡 Medium |
| P3.5 | "Jetzt geöffnet" filtered view as a separate page | Medium | 🟡 Medium |
| P3.6 | District/Kreis hub pages (Schwimmbad Zürich Kreis 9) | High | 🟡 Medium |

---

## 7. Implementation Code Snippets

### 7.1 Fix: Pool Page Meta Tags (pool.html)

```html
{% extends "base.html" %}

{% block title %}{{ pool.name }} {{ pool.city }} – Auslastung & Beste Besuchszeit | badifrei.ch{% endblock %}

{% block description %}{{ pool.name }} in {{ pool.city }} – aktuelle Auslastung live, Öffnungszeiten und KI-Prognose für heute. Finde den besten Zeitpunkt für deinen Besuch.{% endblock %}

{% block og_title %}{{ pool.name }} {{ pool.city }} – Auslastung live{% endblock %}

{% block og_description %}{{ pool.name }} – aktuell {{ current_occupancy_pct }}% belegt. KI-Prognose und beste Besuchszeiten auf badifrei.ch.{% endblock %}

{% block og_url_path %}/dashboard/pools/{{ pool.uid }}{% endblock %}

{% block twitter_title %}{{ pool.name }} {{ pool.city }} – Auslastung live{% endblock %}

{% block canonical_path %}/dashboard/pools/{{ pool.uid }}{% endblock %}
```

---

### 7.2 Fix: base.html OG Tag Blocks

```html
<!-- In base.html <head>, replace hardcoded OG tags: -->
<meta property="og:title" content="{% block og_title %}badifrei.ch – Ist die Badi voll?{% endblock %}">
<meta property="og:description" content="{% block og_description %}Echtzeit-Belegung und Vorhersagen für Zürichs Schwimmbäder. Schau bevor du gehst.{% endblock %}">
<meta property="og:type" content="website">
<meta property="og:url" content="https://badifrei.ch{% block og_url_path %}/{% endblock %}">
<meta property="og:image" content="https://badifrei.ch/static/og-preview.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{% block twitter_title %}badifrei.ch – Ist die Badi voll?{% endblock %}">
<meta name="twitter:description" content="{% block twitter_description %}Echtzeit-Belegung und Vorhersagen für Zürichs Schwimmbäder.{% endblock %}">
<meta name="twitter:image" content="https://badifrei.ch/static/og-preview.jpg">
```

---

### 7.3 Fix: SSR Content Block in pool.html

```html
{% block content %}
<!-- Breadcrumb -->
<nav aria-label="Breadcrumb" class="breadcrumb-nav">
  <ol class="breadcrumb">
    <li><a href="/">Alle Bäder</a></li>
    <li>›</li>
    <li aria-current="page">{{ pool.name }}</li>
  </ol>
</nav>

<!-- Pool Header -->
<header class="pool-header">
  <h1>{{ pool.name }}</h1>
  <span class="pool-type-badge">{{ pool.type }}</span>
</header>

<!-- Static description (from pool_metadata.json) -->
{% if pool.description %}
<section class="pool-description">
  <p>{{ pool.description }}</p>
</section>
{% endif %}

<!-- SSR: Current Occupancy (rendered at request time) -->
<section class="occupancy-now" aria-live="polite">
  <h2>Aktuelle Auslastung</h2>
  <div class="occupancy-display">
    <div class="occupancy-bar-container">
      <div class="occupancy-bar" style="width: {{ current_occupancy_pct }}%"></div>
    </div>
    <p class="occupancy-label">
      {% if current_occupancy_pct is not none %}
        <strong>{{ current_occupancy_pct }}%</strong> belegt
        {% if current_occupancy_pct < 40 %} — 🟢 Wenig los{% elif current_occupancy_pct < 70 %} — 🟡 Mässig voll{% else %} — 🔴 Sehr voll{% endif %}
        <small>(aktualisiert: {{ last_updated }})</small>
      {% else %}
        Keine aktuellen Daten verfügbar (Bad möglicherweise geschlossen).
      {% endif %}
    </p>
  </div>
</section>

<!-- SSR: Opening Hours Table -->
<section class="opening-hours">
  <h2>Öffnungszeiten {{ pool.name }}</h2>
  <table class="hours-table">
    <caption>Öffnungszeiten ({{ pool.season_label }})</caption>
    <thead>
      <tr><th>Tag</th><th>Öffnungszeit</th></tr>
    </thead>
    <tbody>
      {% for day in pool.opening_hours %}
      <tr>
        <td>{{ day.label }}</td>
        <td>{% if day.closed %}Geschlossen{% else %}{{ day.open }} – {{ day.close }} Uhr{% endif %}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>

<!-- SSR: Best Visit Times (pre-computed from ML model) -->
<section class="visit-tips">
  <h2>Wann ist {{ pool.name }} am wenigsten voll?</h2>
  <p>
    Laut unserer Analyse der letzten Saison ist <strong>{{ pool.name }}</strong> 
    typischerweise am <strong>{{ typical_busy_day }}</strong> 
    gegen <strong>{{ typical_busy_hour }}:00 Uhr</strong> am stärksten besucht.
  </p>
  <p>
    Die ruhigsten Zeiten sind erfahrungsgemäss <strong>{{ typical_quiet_desc }}</strong>.
    {% if today_peak_hour %}
    Die heutige KI-Prognose deutet auf eine Spitzenbelegung um <strong>{{ today_peak_hour }}:00 Uhr</strong> hin.
    {% endif %}
  </p>
  <p class="capacity-note">Kapazität: <strong>{{ pool.capacity }} Personen</strong></p>
</section>

<!-- Chart.js chart (dynamic, progressive enhancement) -->
<section class="occupancy-chart-section">
  <h2>Tagesverlauf & Prognose</h2>
  <div class="chart-container" style="position:relative; height:400px; width:100%">
    <canvas id="occupancyChart"></canvas>
  </div>
</section>

<!-- SSR: Similar Pools -->
<section class="similar-pools">
  <h2>Weitere Bäder in {{ pool.city }}</h2>
  <ul class="pool-links">
    {% for other_pool in nearby_pools %}
    <li>
      <a href="/dashboard/pools/{{ other_pool.uid }}">{{ other_pool.name }}</a>
      {% if other_pool.current_occupancy_pct is not none %}
        — {{ other_pool.current_occupancy_pct }}% belegt
      {% endif %}
    </li>
    {% endfor %}
  </ul>
  <p><a href="/">← Alle Bäder anzeigen</a></p>
</section>

<!-- Chart.js init with SSR data (no initial API call needed) -->
<script>
  window.__INITIAL_POOL_DATA__ = {
    uid: "{{ pool.uid }}",
    predictions: {{ today_predictions_json | safe }},
    history: {{ today_history_json | safe }}
  };
</script>
<script defer src="/static/chart-init.js"></script>
{% endblock %}
```

---

### 7.4 Fix: Improved Structured Data for Pool Page

```html
{% block structured_data %}
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "SportsActivityLocation",
      "@id": "https://badifrei.ch/dashboard/pools/{{ pool.uid }}#pool",
      "name": "{{ pool.name }}",
      "description": "{{ pool.name }} in {{ pool.city }} — aktuelle Auslastung, KI-Prognose und Öffnungszeiten auf badifrei.ch.",
      "url": "https://badifrei.ch/dashboard/pools/{{ pool.uid }}",
      "maximumAttendeeCapacity": {{ pool.capacity }},
      "address": {
        "@type": "PostalAddress",
        "addressLocality": "{{ pool.city }}",
        "addressRegion": "ZH",
        "addressCountry": "CH"
      },
      "openingHoursSpecification": [
        {% for day in pool.opening_hours if not day.closed %}
        {
          "@type": "OpeningHoursSpecification",
          "dayOfWeek": "https://schema.org/{{ day.schema_day }}",
          "opens": "{{ day.open }}",
          "closes": "{{ day.close }}"
        }{% if not loop.last %},{% endif %}
        {% endfor %}
      ]
    },
    {
      "@type": "BreadcrumbList",
      "itemListElement": [
        {
          "@type": "ListItem",
          "position": 1,
          "name": "Alle Bäder Zürich",
          "item": "https://badifrei.ch/"
        },
        {
          "@type": "ListItem",
          "position": 2,
          "name": "{{ pool.name }}",
          "item": "https://badifrei.ch/dashboard/pools/{{ pool.uid }}"
        }
      ]
    },
    {
      "@type": "FAQPage",
      "mainEntity": [
        {
          "@type": "Question",
          "name": "Wann ist {{ pool.name }} am wenigsten voll?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "{{ pool.name }} ist erfahrungsgemäss {{ typical_quiet_desc }} am ruhigsten. Am vollsten ist es typischerweise {{ typical_busy_desc }}. Unsere live KI-Prognose auf badifrei.ch zeigt dir den genauen Tagesverlauf."
          }
        },
        {
          "@type": "Question",
          "name": "Wie viele Personen fasst {{ pool.name }}?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "{{ pool.name }} hat eine Kapazität von {{ pool.capacity }} Personen."
          }
        },
        {
          "@type": "Question",
          "name": "Wann hat {{ pool.name }} geöffnet?",
          "acceptedAnswer": {
            "@type": "Answer",
            "text": "{{ pool.name }} ist {{ opening_hours_summary }} geöffnet. Die genauen Öffnungszeiten findest du auf dieser Seite."
          }
        }
      ]
    }
  ]
}
</script>
{% endblock %}
```

---

### 7.5 Fix: FastAPI Route Enhancement

```python
# In main.py — enhanced pool detail route

@app.get("/dashboard/pools/{pool_uid}", response_class=HTMLResponse)
async def pool_detail(request: Request, pool_uid: str, db: AsyncSession = Depends(get_db)):
    pool = await get_pool_metadata(pool_uid)
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")
    
    # Fetch all data server-side for SSR
    current_occupancy = await get_current_occupancy(db, pool_uid)
    today_history = await get_today_history(db, pool_uid)
    today_predictions = await get_today_predictions(db, pool_uid)
    typical_patterns = await get_typical_patterns(db, pool_uid)  # pre-computed stats
    nearby_pools = await get_nearby_pools(db, pool_uid, limit=4)
    
    # Compute derived display values
    current_pct = round(current_occupancy.value * 100) if current_occupancy else None
    typical_busy = typical_patterns.busiest_slot if typical_patterns else None
    typical_quiet = typical_patterns.quietest_slot if typical_patterns else None
    
    return templates.TemplateResponse("pool.html", {
        "request": request,
        "pool": pool,
        "current_occupancy_pct": current_pct,
        "last_updated": current_occupancy.timestamp.strftime("%H:%M Uhr") if current_occupancy else None,
        "today_predictions_json": json.dumps([p.dict() for p in today_predictions]),
        "today_history_json": json.dumps([h.dict() for h in today_history]),
        "today_peak_hour": get_peak_hour(today_predictions),
        "typical_busy_day": typical_busy.day_label if typical_busy else "Samstag",
        "typical_busy_hour": typical_busy.hour if typical_busy else 14,
        "typical_quiet_desc": typical_quiet.description if typical_quiet else "Werktags am Morgen",
        "typical_busy_desc": f"{typical_busy.day_label} gegen {typical_busy.hour}:00 Uhr" if typical_busy else "samstags nachmittags",
        "opening_hours_summary": pool.opening_hours_summary,
        "nearby_pools": nearby_pools,
    })
```

---

### 7.6 Fix: Security Headers Middleware

```python
# In main.py

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Don't add to API responses unnecessarily
        if request.url.path.startswith("/api/") or request.url.path.startswith("/predict/"):
            return response
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

---

### 7.7 Fix: Improved Sitemap Generation

```python
# In main.py — improved sitemap route

from datetime import date

@app.get("/sitemap.xml", response_class=Response, media_type="application/xml")
async def sitemap():
    today = date.today().isoformat()
    pools = await get_all_pools()
    
    urls = [
        f"""  <url>
    <loc>https://badifrei.ch/</loc>
    <changefreq>always</changefreq>
    <priority>1.0</priority>
    <lastmod>{today}</lastmod>
  </url>"""
    ]
    
    for pool in pools:
        urls.append(f"""  <url>
    <loc>https://badifrei.ch/dashboard/pools/{pool.uid}</loc>
    <changefreq>hourly</changefreq>
    <priority>0.8</priority>
    <lastmod>{today}</lastmod>
  </url>""")
    
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""
    
    return Response(content=xml, media_type="application/xml")
```

---

### 7.8 Fix: Improved robots.txt

```python
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Allow: /
Disallow: /api/
Disallow: /predict/
Disallow: /health
Disallow: /pools

Sitemap: https://badifrei.ch/sitemap.xml
"""
```

---

## Appendix A: SEO Score Card

| Category | Current | After P0+P1 | After P2 |
|---|---|---|---|
| Technical SEO | 55/100 | 80/100 | 90/100 |
| Content (pool pages) | 10/100 | 60/100 | 80/100 |
| Content (homepage) | 45/100 | 65/100 | 75/100 |
| pSEO Execution | 15/100 | 55/100 | 80/100 |
| Internal Linking | 20/100 | 65/100 | 80/100 |
| Authority/Backlinks | 5/100 | 5/100 | 30/100 |
| **Overall** | **25/100** | **60/100** | **75/100** |

---

## Appendix B: Quick Reference — Pool Page SEO Checklist

For each pool page, verify:

- [ ] Unique `<title>` with pool name + city + "Auslastung"
- [ ] Unique `<meta description>` with pool name + live occupancy hook
- [ ] `og:url` matches canonical URL
- [ ] `og:description` is pool-specific
- [ ] `<h1>` = pool name (present in SSR, not just JS)
- [ ] `<h2>` headings for: Auslastung / Öffnungszeiten / Beste Zeiten / Weitere Bäder
- [ ] SSR text with current occupancy %
- [ ] SSR opening hours table
- [ ] SSR "Beste Besuchszeiten" paragraph
- [ ] SSR pool description (150 words)
- [ ] `SportsActivityLocation` schema with opening hours + capacity
- [ ] `BreadcrumbList` schema
- [ ] `FAQPage` schema
- [ ] Breadcrumb nav visible
- [ ] "Ähnliche Bäder" links
- [ ] Chart container has explicit height (no CLS)
- [ ] Initial chart data injected SSR (no API call for first render)

---

*Report generated March 2026. badifrei.ch has excellent potential — the data product is unique and the technical foundation is sound. The main work is surfacing that data as crawlable content, not rebuilding the site.*
