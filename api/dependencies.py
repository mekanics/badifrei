"""Shared FastAPI Depends aliases. See ADR-002."""

from typing import Annotated

import asyncpg
from fastapi import Depends, Request


async def get_db_pool(request: Request) -> asyncpg.Pool | None:
    return getattr(request.app.state, "db_pool", None)


DbPool = Annotated[asyncpg.Pool | None, Depends(get_db_pool)]
