"""Contract tests for mobile-first CSS breakpoints (ADR-004)."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
STYLE_CSS = ROOT / "api" / "static" / "style.css"
POOL_HTML = ROOT / "api" / "templates" / "pool.html"


def test_style_css_uses_tailwind_rem_min_width_breakpoints():
    css = STYLE_CSS.read_text(encoding="utf-8")
    assert "@media (min-width: 40rem)" in css
    assert "@media (min-width: 48rem)" in css
    # Desktop-first width queries must not creep back in for layout.
    assert not re.search(r"@media\s*\(\s*max-width:\s*\d+(\.\d+)?(px|rem)\s*\)", css)


def test_pool_card_uses_subgrid_for_cross_card_alignment():
    css = STYLE_CSS.read_text(encoding="utf-8")
    card_block = re.search(
        r"\.pool-card\s*\{([^}]+)\}",
        css,
        flags=re.DOTALL,
    )
    assert card_block is not None
    body = card_block.group(1)
    assert "grid-template-rows: subgrid" in body
    assert "grid-row: span 2" in body


def test_pool_info_grid_is_mobile_first_rem():
    html = POOL_HTML.read_text(encoding="utf-8")
    assert re.search(
        r"\.pool-info-grid\s*\{[^}]*grid-template-columns:\s*1fr",
        html,
        flags=re.DOTALL,
    )
    assert "@media (min-width: 40rem)" in html
    assert not re.search(r"@media\s*\(\s*max-width:\s*600px\s*\)", html)
