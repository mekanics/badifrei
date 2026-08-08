"""Agent-facing Markdown twins for badifrei.ch (llmstxt.org-style surfaces).

Pure renderers: callers load occupancy / predictions and pass structured inputs.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from fastapi.responses import Response

from api.city_display import CITY_DISPLAY

SITE_ORIGIN = "https://badifrei.ch"
POOL_MD_CACHE_MAX_AGE = 180
HOME_MD_CACHE_MAX_AGE = 3600
LLMS_TXT_CACHE_MAX_AGE = 3600


def adapt_faq_for_markdown(text: str | None, *, html_url: str) -> str | None:
    """Rewrite HTML-centric FAQ phrases for standalone Markdown twins."""
    if not text:
        return None
    return text.replace("auf dieser Seite", f"auf der HTML-Seite ({html_url})")


_STATUS_DE = {
    "ok": None,
    "no_model": "Prognosemodell derzeit nicht verfügbar.",
    "closed_all_day": "Heute geschlossen — keine Prognose für offene Stunden.",
    "off_season": "Ausserhalb der Saison — keine Prognose.",
}


def markdown_response(body: str, *, max_age: int) -> Response:
    """Shared Response factory for Markdown twins."""
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Cache-Control": f"public, max-age={max_age}",
            "X-Robots-Tag": "noindex",
        },
    )


def render_llms_txt(*, pools: list[dict]) -> str:
    """Curated llms.txt index: stable narrative + coverage derived from metadata.

    Full pool lists live on /index.md so counts and cities stay accurate without
    hand-maintaining capacity blurbs here.
    """
    n_pools = len(pools)
    city_keys = sorted(
        {p.get("city", "zurich") for p in pools},
        key=lambda c: (c != "zurich", c),
    )
    city_labels = [CITY_DISPLAY.get(c, str(c).title()) for c in city_keys]
    n_cities = len(city_keys)
    if len(city_labels) == 1:
        cities_phrase = city_labels[0]
    elif len(city_labels) == 2:
        cities_phrase = f"{city_labels[0]} and {city_labels[1]}"
    else:
        cities_phrase = ", ".join(city_labels[:-1]) + f", and {city_labels[-1]}"

    return "\n".join(
        [
            "# badifrei.ch",
            "",
            "> **badifrei.ch** zeigt die aktuelle Auslastung und KI-Prognosen "
            "für Schwimmbäder in der Schweiz.",
            "> The site tells you how crowded a swimming pool is right now, "
            "and forecasts occupancy for the next several hours.",
            ">",
            "> **Data source:** CrowdMonitor occupancy sensors by ASE "
            "(Switzerland), updated in near-real-time.",
            "> **ML model:** XGBoost, trained per pool, retrained weekly. "
            "Predicts percentage occupancy up to ~8 hours ahead.",
            f"> **Coverage:** {n_pools} pools across {n_cities} Swiss cities — "
            f"{cities_phrase}.",
            "> **Language:** German (de-CH). Operated independently; not "
            "affiliated with city authorities or pool operators.",
            "> **Limitations:** Coverage depends on sensor availability. "
            "Seasonal outdoor pools (Freibäder, Strandbäder) are closed in "
            "winter (approx. Oct–Apr). Sensor outages may cause temporary data "
            "gaps. Predictions are probabilistic and not a guarantee.",
            "> **AI-readable pages:** Prefer Markdown twins (`.md`) for live "
            "occupancy and today's forecast without HTML chrome. Start at "
            f"[index.md]({SITE_ORIGIN}/index.md).",
            "",
            "badifrei.ch is a public, free-to-use tool for residents and "
            "visitors in Switzerland. The homepage shows all monitored pools "
            "grouped by city, with live occupancy levels. Each pool detail "
            "page includes current occupancy, a day-ahead forecast, opening "
            "hours, and (where available) historical occupancy patterns.",
            "",
            "## Key Pages",
            "",
            f"- [index.md — full pool Markdown index]({SITE_ORIGIN}/index.md): "
            f"All {n_pools} pools as `.md` links, grouped by city "
            "(no live occupancy table).",
            f"- [Homepage HTML]({SITE_ORIGIN}/): Live occupancy overview.",
            f"- [Pool Markdown pattern]({SITE_ORIGIN}/bad/{{uid}}.md): "
            "Live occupancy + today's forecast for one pool "
            f"(example: [Freibad Allenmoos]({SITE_ORIGIN}/bad/fb006.md)).",
            "",
            "## Optional",
            "",
            f"- [Sitemap]({SITE_ORIGIN}/sitemap.xml): HTML URLs only "
            "(Markdown twins are noindex).",
            "",
            "## About the data",
            "",
            "Occupancy data is sourced from **CrowdMonitor** infrared/radar "
            "sensors (ASE AG). badifrei.ch trains per-pool XGBoost models on "
            "historical occupancy, time features, and weather. Models are "
            "retrained weekly. Forecast horizon is approximately 8 hours.",
            "",
            "badifrei.ch does **not** publish real-time admission prices, "
            "water temperature, or lane availability. For official pool "
            "details, visit the respective city sports department websites.",
            "",
        ]
    )


def render_home_markdown(*, pools: list[dict], as_of: datetime) -> str:
    """Curated index: site blurb + links to pool .md pages. No live occupancy."""
    by_city: dict[str, list[dict]] = defaultdict(list)
    for p in pools:
        by_city[p.get("city", "zurich")].append(p)
    city_keys = sorted(by_city.keys(), key=lambda c: (c != "zurich", c))

    lines: list[str] = [
        "# badifrei.ch",
        "",
        f"> Curated index — as_of {as_of.isoformat()}.",
        f"> HTML: {SITE_ORIGIN}/ · Markdown: {SITE_ORIGIN}/index.md",
        "",
        "badifrei.ch zeigt Live-Auslastung und KI-Prognosen für Schwimmbäder "
        "in der Schweiz. Datenquelle: CrowdMonitor (ASE). "
        "Jede Bad-Seite hat eine Markdown-Version für KI-Assistenten.",
        "",
        "## Hauptseiten",
        "",
        f"- [Startseite HTML]({SITE_ORIGIN}/): Übersicht aller Bäder",
        f"- [llms.txt]({SITE_ORIGIN}/llms.txt): Kuratierter Einstieg für KI",
        f"- [index.md]({SITE_ORIGIN}/index.md): Diese Seite",
        "",
    ]

    for key in city_keys:
        label = CITY_DISPLAY.get(key, key.title())
        lines.append(f"## {label}")
        lines.append("")
        for pool in sorted(by_city[key], key=lambda p: p.get("name", "")):
            uid = pool["uid"]
            name = pool.get("name", uid)
            lines.append(f"- [{name}]({SITE_ORIGIN}/bad/{uid}.md)")
        lines.append("")

    lines.extend(
        [
            "## Hinweis",
            "",
            "Live-Auslastung und Tagesprognosen stehen auf den einzelnen "
            f"Bad-Markdown-Seiten (`{SITE_ORIGIN}/bad/{{uid}}.md`), nicht hier.",
            "",
        ]
    )
    return "\n".join(lines)


def _format_sensor_time(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _forecast_rows(
    today_predictions: list[float],
    *,
    now_zurich: datetime,
    prediction_status: str,
) -> list[tuple[str, float]] | None:
    """Return (HH:00, pct) rows, or None if status should explain instead of a table."""
    if prediction_status in ("no_model", "off_season", "closed_all_day"):
        return None
    start = now_zurich.hour
    rows: list[tuple[str, float]] = []
    for h, pred in enumerate(today_predictions):
        if h < start:
            continue
        if pred is None or pred <= 0:
            continue
        rows.append((f"{h:02d}:00", float(pred)))
    return rows


def render_pool_markdown(
    *,
    pool: dict,
    occupancy: dict | None,
    today_predictions: list[float],
    prediction_status: dict,
    as_of: datetime,
    now_zurich: datetime,
    opening_hours_summary: str | None = None,
    opening_hours_detail: str | None = None,
) -> str:
    """Live occupancy + today's forecast, plus profile and opening hours."""
    uid = pool["uid"]
    name = pool.get("name", uid)
    status_key = prediction_status.get("prediction_status", "ok")
    html_url = f"{SITE_ORIGIN}/bad/{uid}"
    md_url = f"{SITE_ORIGIN}/bad/{uid}.md"
    city_key = pool.get("city", "zurich")
    city_label = CITY_DISPLAY.get(city_key, str(city_key).title())
    pool_type = pool.get("type") or ""
    description = (pool.get("description") or "").strip()

    lines: list[str] = [
        f"# {name}",
        "",
        f"> Cached snapshot — as_of {as_of.isoformat()}. "
        "Next refresh expected within ~180s.",
        f"> HTML: {html_url} · Markdown: {md_url}",
        "",
        "## Profil",
        "",
        f"- Name: {name}",
        f"- Stadt: {city_label}",
    ]
    if pool_type:
        lines.append(f"- Typ: {pool_type}")
    if pool.get("seasonal") is not None:
        lines.append(
            f"- Saisonal: {'ja' if pool.get('seasonal') else 'nein (ganzjährig)'}"
        )
    official = pool.get("official_url")
    if official:
        lines.append(f"- Offizielle Seite: {official}")
    if description:
        lines.extend(["", description, ""])
    else:
        lines.append("")

    lines.extend(["## Aktuelle Auslastung", ""])

    if occupancy is None or occupancy.get("occupancy_pct") is None:
        lines.append("- Occupancy: keine Daten")
        if occupancy is not None:
            if occupancy.get("is_open") is not None:
                open_label = "offen" if occupancy.get("is_open") else "geschlossen"
                lines.append(f"- Status: {open_label}")
            if occupancy.get("reason"):
                lines.append(f"- Reason: {occupancy['reason']}")
            if occupancy.get("state"):
                lines.append(f"- State: {occupancy['state']}")
    else:
        pct = occupancy["occupancy_pct"]
        # occupancy_pct from DB may be Decimal
        try:
            pct_i = int(round(float(pct)))
        except (TypeError, ValueError):
            pct_i = pct
        lines.append(f"- Occupancy: {pct_i}%")
        fill = occupancy.get("current_fill")
        max_space = occupancy.get("max_space")
        if fill is not None and max_space is not None:
            lines.append(f"- Guests: {fill} / {max_space}")
        open_label = "offen" if occupancy.get("is_open") else "geschlossen"
        lines.append(f"- Status: {open_label}")
        if occupancy.get("reason"):
            lines.append(f"- Reason: {occupancy['reason']}")
        if occupancy.get("state"):
            lines.append(f"- State: {occupancy['state']}")
        lines.append(f"- Sensor time: {_format_sensor_time(occupancy.get('time'))}")

    hours_detail_md = adapt_faq_for_markdown(opening_hours_detail, html_url=html_url)

    lines.extend(["", "## Öffnungszeiten", ""])
    if opening_hours_summary:
        lines.append(opening_hours_summary)
        lines.append("")
    if hours_detail_md and hours_detail_md != opening_hours_summary:
        lines.append(hours_detail_md)
        lines.append("")
    if not opening_hours_summary and not hours_detail_md:
        lines.append("Keine Öffnungszeiten hinterlegt.")
        lines.append("")

    lines.extend(["## Prognose heute", ""])

    status_note = _STATUS_DE.get(status_key)
    rows = _forecast_rows(
        today_predictions,
        now_zurich=now_zurich,
        prediction_status=status_key,
    )
    if status_note:
        lines.append(status_note)
        lines.append("")
    if rows:
        lines.append("| Stunde | Auslastung (Prognose %) |")
        lines.append("| --- | ---: |")
        for hour_label, pred in rows:
            lines.append(f"| {hour_label} | {pred:.0f} |")
        lines.append("")
    elif not status_note:
        lines.append("Keine Prognosewerte für die verbleibenden offenen Stunden.")
        lines.append("")

    lines.extend(
        [
            "## Datenquelle",
            "",
            "Live-Auslastung: CrowdMonitor-Sensoren (ASE). "
            "Prognose: XGBoost-Modell auf badifrei.ch, wöchentlich aktualisiert. "
            "Öffnungszeiten von den jeweiligen Betreibern / Gemeinden. "
            "Unabhängig betrieben — Angaben ohne Gewähr.",
            "",
            "## Related",
            "",
            f"- [HTML-Seite]({html_url})",
            f"- [Markdown]({md_url})",
            f"- [llms.txt]({SITE_ORIGIN}/llms.txt)",
            "",
        ]
    )
    return "\n".join(lines)
