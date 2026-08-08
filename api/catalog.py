"""Pool catalog loaded from ml/pool_metadata.json."""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

ZURICH_TZ = ZoneInfo("Europe/Zurich")

POOL_METADATA_PATH = Path(__file__).parent.parent / "ml" / "pool_metadata.json"

_pools_cache: list | None = None


def get_pools() -> list[dict]:
    global _pools_cache
    if _pools_cache is None:
        _pools_cache = json.loads(POOL_METADATA_PATH.read_text(encoding="utf-8"))
    return _pools_cache
