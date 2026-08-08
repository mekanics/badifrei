"""Health and crawler / AI discovery surfaces."""

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from api.catalog import get_pools
from api.markdown_surfaces import (
    LLMS_TXT_CACHE_MAX_AGE,
    markdown_response,
    render_llms_txt,
)

router = APIRouter()


@router.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": "0.1.0"}


@router.get("/llms.txt", include_in_schema=False)
async def llms_txt():
    """Curated AI index; coverage stats derived from pool metadata."""
    body = render_llms_txt(pools=get_pools())
    return markdown_response(body, max_age=LLMS_TXT_CACHE_MAX_AGE)


@router.get("/robots.txt", include_in_schema=False)
async def robots():
    content = """# Content Signals Policy (https://contentsignals.org)
# search:   building a search index and returning results
# ai-input: using content as live input for AI-generated answers (RAG, grounding)
# ai-train: training or fine-tuning AI models

User-agent: *
Content-Signal: search=yes,ai-input=yes,ai-train=no
Allow: /
Disallow: /dashboard/
Disallow: /api/
Disallow: /predict/
Disallow: /health
Disallow: /pools

# Block AI training crawlers (answer-generation bots are allowed above)
User-agent: Amazonbot
Disallow: /

User-agent: Applebot-Extended
Disallow: /

User-agent: Bytespider
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: ClaudeBot
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: GPTBot
Disallow: /

User-agent: meta-externalagent
Disallow: /

Sitemap: https://badifrei.ch/sitemap.xml
"""
    return PlainTextResponse(content)


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    pool_uids = [p["uid"] for p in get_pools()]
    today = datetime.now(timezone.utc).date().isoformat()

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += (
        "  <url>\n"
        "    <loc>https://badifrei.ch/</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
    )
    for uid in pool_uids:
        xml += (
            "  <url>\n"
            f"    <loc>https://badifrei.ch/bad/{uid}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            "    <changefreq>always</changefreq>\n"
            "    <priority>0.8</priority>\n"
            "  </url>\n"
        )
    xml += "</urlset>"

    return Response(content=xml, media_type="application/xml")
