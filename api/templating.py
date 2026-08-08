"""Jinja2 templates and presentation helpers for HTML routes."""

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

from api.config import get_settings

TEMPLATES_PATH = Path(__file__).parent / "templates"
STATIC_PATH = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_PATH))

_STATIC_VER_CACHE: dict[tuple[Path, str], str] = {}

_MONTHS_DE = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


def _static_ver(filename: str) -> str:
    """Return an 8-char content hash for a top-level static asset."""
    asset_name = Path(filename)
    if asset_name.name != filename:
        return "0"
    cache_key = (STATIC_PATH, filename)
    if cache_key in _STATIC_VER_CACHE:
        return _STATIC_VER_CACHE[cache_key]
    asset_path = STATIC_PATH / filename
    if not asset_path.is_file():
        return "0"
    version = hashlib.md5(asset_path.read_bytes()).hexdigest()[:8]
    _STATIC_VER_CACHE[cache_key] = version
    return version


def _fmt_date_de(value: str) -> str:
    """Format ISO date string (YYYY-MM-DD) as German date: '9. Mai 2026'."""
    try:
        parts = str(value).split("-")
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{d}. {_MONTHS_DE[m - 1]} {y}"
    except Exception:
        return str(value)


def configure_templates() -> None:
    """Bind globals/filters from current settings (call once at app startup)."""
    settings = get_settings()
    templates.env.globals["static_ver"] = _static_ver
    templates.env.globals["umami_script_url"] = settings.umami_script_url
    templates.env.globals["umami_website_id"] = settings.umami_website_id
    templates.env.filters["date_de"] = _fmt_date_de


configure_templates()
